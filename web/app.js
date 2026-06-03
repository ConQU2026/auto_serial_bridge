const CHECKSUM_OPTIONS = ["NONE", "SUM8", "XOR8", "CRC8"];
const DIRECTION_OPTIONS = ["tx", "rx", "both"];
const DEBUG_LOG_MODE_OPTIONS = ["on", "off"];
const CONFIG_NUMERIC_KEYS = [
  "baudrate",
  "buffer_size",
  "head_byte_1",
  "head_byte_2",
  "reliable_retry_interval_ms",
  "reliable_max_retries",
  "qos_depth",
  "heartbeat_timeout_ms",
];
const ROS_NUMERIC_KEYS = ["baudrate", "timeout"];
const DEFAULT_PROTOCOL = Object.freeze({
  serial_controller: {
    ros__parameters: {
      port: "/dev/ttyACM0",
      baudrate: 115200,
      timeout: 0.1,
      debug_raw_frame: false,
    },
  },
  config: {
    baudrate: 115200,
    buffer_size: 256,
    head_byte_1: 0x5a,
    head_byte_2: 0xa5,
    checksum: "CRC8",
    require_handshake: true,
    ignore_version_mismatch: true,
    reliable_retry_interval_ms: 100,
    reliable_max_retries: 3,
    enable_heartbeat: true,
    strict_heartbeat: true,
    qos_depth: 10,
    heartbeat_timeout_ms: 3000,
  },
  type_mappings: {
    f32: "float",
    i32: "int32_t",
    u8: "uint8_t",
    u16: "uint16_t",
    u32: "uint32_t",
  },
  messages: [],
});

const SYSTEM_MESSAGES = Object.freeze([
  {
    name: "Ack",
    id: 0xfd,
    direction: "both",
    debug_log_mode: "on",
    sub_topic: "/task/ack",
    pub_topic: "/task/ack",
    ros_msg: "std_msgs/msg/Int32MultiArray",
    notes:
      "通用 ACK。acked_id 为被确认包的原始 Packet ID，ack_seq 为发送方附加的序列号，用于区分同 ID 的不同版本。",
    fields: [
      { proto: "acked_id", type: "u8", ros: "data[0]" },
      { proto: "ack_seq", type: "u8", ros: "data[1]" },
    ],
  },
  {
    name: "Heartbeat",
    id: 0xfe,
    direction: "both",
    debug_log_mode: "on",
    sub_topic: "/task/heartbeat",
    pub_topic: "/task/heartbeat",
    ros_msg: "std_msgs/msg/UInt32",
    notes:
      "握手完成后，ROS 侧以固定周期下发心跳。电控收到后必须尽快原样回同一个 count 作为确认，但不再独立主动发送心跳。只有与 ROS 最近一次发送值一致的回包才算有效确认。",
    fields: [{ proto: "count", type: "u32", ros: "data" }],
  },
  {
    name: "Handshake",
    id: 0xff,
    direction: "both",
    debug_log_mode: "on",
    sub_topic: "/task/handshake",
    pub_topic: "/task/handshake",
    ros_msg: "std_msgs/msg/UInt32",
    notes:
      "上电后 ROS 主动发起握手，电控收到后用相同的 protocol_hash 原样回复。握手通过后方可发送其他数据帧，且整个连接周期内只需执行一次。protocol_hash 值见【全局参数】。",
    fields: [{ proto: "protocol_hash", type: "u32", ros: "data" }],
  },
]);

const SYSTEM_MESSAGE_BY_ID = new Map(SYSTEM_MESSAGES.map((message) => [message.id, message]));
const SYSTEM_MESSAGE_RESERVED_IDS = new Set(SYSTEM_MESSAGES.map((message) => message.id));

function deepClone(value) {
  return JSON.parse(JSON.stringify(value));
}

function toNumber(value) {
  if (typeof value === "number" && Number.isFinite(value)) {
    return value;
  }
  if (typeof value === "string") {
    const trimmed = value.trim();
    if (!trimmed) {
      return Number.NaN;
    }
    if (/^0x[0-9a-f]+$/i.test(trimmed)) {
      return Number.parseInt(trimmed, 16);
    }
    return Number(trimmed);
  }
  return Number.NaN;
}

function toInteger(value, fallback = 0) {
  const parsed = toNumber(value);
  return Number.isFinite(parsed) ? Math.trunc(parsed) : fallback;
}

function toFiniteNumber(value, fallback = 0) {
  const parsed = toNumber(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function normalizeString(value) {
  return typeof value === "string" ? value : value == null ? "" : String(value);
}

function normalizeBoolean(value, fallback = false) {
  if (typeof value === "boolean") {
    return value;
  }
  if (typeof value === "string") {
    const normalized = value.trim().toLowerCase();
    if (normalized === "true") {
      return true;
    }
    if (normalized === "false") {
      return false;
    }
  }
  return fallback;
}

function hexByte(value) {
  const numeric = Math.max(0, Math.min(255, toInteger(value, 0)));
  return `0x${numeric.toString(16).toUpperCase().padStart(2, "0")}`;
}

function yamlSingleQuote(value) {
  return `'${normalizeString(value).replace(/'/g, "''")}'`;
}

function escapeHtml(value) {
  return normalizeString(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function createDefaultField() {
  return {
    proto: "field_name",
    type: "u8",
    ros: "data",
  };
}

export function createDefaultMessage(existingMessages = []) {
  const usedIds = new Set(
    existingMessages
      .map((message) => toInteger(message.id, -1))
      .filter((value) => value >= 0 && value <= 255),
  );
  for (const reservedId of SYSTEM_MESSAGE_RESERVED_IDS) {
    usedIds.add(reservedId);
  }

  let nextId = 0;
  while (usedIds.has(nextId) && nextId <= 255) {
    nextId += 1;
  }
  if (nextId > 255) {
    nextId = 0;
  }

  const messageIndex = existingMessages.length + 1;
  return {
    name: `NewMessage${messageIndex}`,
    id: nextId,
    direction: "tx",
    debug_log_mode: "on",
    reliable: false,
    sub_topic: `/task/new_message_${messageIndex}`,
    pub_topic: `/task/new_message_${messageIndex}`,
    ros_msg: "std_msgs/msg/UInt32",
    notes: "",
    fields: [createDefaultField()],
  };
}

function normalizeField(field) {
  return {
    proto: normalizeString(field?.proto),
    type: normalizeString(field?.type),
    ros: normalizeString(field?.ros),
  };
}

function normalizeMessage(message, index) {
  const direction = normalizeString(message?.direction || "tx").toLowerCase();
  const debugLogMode = normalizeString(message?.debug_log_mode || "on").toLowerCase();
  const fields = Array.isArray(message?.fields) ? message.fields.map(normalizeField) : [];
  return {
    name: normalizeString(message?.name || `Message${index + 1}`),
    id: toInteger(message?.id, index),
    direction: DIRECTION_OPTIONS.includes(direction) ? direction : direction || "tx",
    debug_log_mode: DEBUG_LOG_MODE_OPTIONS.includes(debugLogMode) ? debugLogMode : "on",
    reliable: normalizeBoolean(message?.reliable, false),
    sub_topic: normalizeString(message?.sub_topic),
    pub_topic: normalizeString(message?.pub_topic),
    ros_msg: normalizeString(message?.ros_msg),
    notes: normalizeString(message?.notes),
    fields: fields.length > 0 ? fields : [createDefaultField()],
  };
}

function isSystemMessage(message) {
  const id = toInteger(message?.id, -1);
  const systemTemplate = SYSTEM_MESSAGE_BY_ID.get(id);
  if (!systemTemplate) {
    return false;
  }
  return normalizeString(message?.name).trim().toLowerCase() === systemTemplate.name.toLowerCase();
}

function getSystemMessages() {
  return SYSTEM_MESSAGES.map((message) => deepClone(message));
}

function getAllMessages(protocol) {
  const editableMessages = Array.isArray(protocol?.messages)
    ? protocol.messages.map(normalizeMessage)
    : [];
  return [...getSystemMessages(), ...editableMessages];
}

export function normalizeProtocol(rawData = {}) {
  const serialParameters = rawData?.serial_controller?.ros__parameters ?? {};
  const config = rawData?.config ?? {};
  const typeMappings = rawData?.type_mappings ?? {};
  const messages = Array.isArray(rawData?.messages) ? rawData.messages : [];

  const normalized = deepClone(DEFAULT_PROTOCOL);

  normalized.serial_controller.ros__parameters = {
    port: normalizeString(serialParameters.port || normalized.serial_controller.ros__parameters.port),
    baudrate: toFiniteNumber(serialParameters.baudrate, normalized.serial_controller.ros__parameters.baudrate),
    timeout: toFiniteNumber(serialParameters.timeout, normalized.serial_controller.ros__parameters.timeout),
    debug_raw_frame: normalizeBoolean(
      serialParameters.debug_raw_frame,
      normalized.serial_controller.ros__parameters.debug_raw_frame,
    ),
  };

  normalized.config = {
    baudrate: toInteger(config.baudrate, normalized.config.baudrate),
    buffer_size: toInteger(config.buffer_size, normalized.config.buffer_size),
    head_byte_1: toInteger(config.head_byte_1, normalized.config.head_byte_1),
    head_byte_2: toInteger(config.head_byte_2, normalized.config.head_byte_2),
    checksum: normalizeString(config.checksum || normalized.config.checksum).toUpperCase(),
    require_handshake: normalizeBoolean(config.require_handshake, normalized.config.require_handshake),
    ignore_version_mismatch: normalizeBoolean(
      config.ignore_version_mismatch,
      normalized.config.ignore_version_mismatch,
    ),
    reliable_retry_interval_ms: toInteger(
      config.reliable_retry_interval_ms,
      normalized.config.reliable_retry_interval_ms,
    ),
    reliable_max_retries: toInteger(
      config.reliable_max_retries,
      normalized.config.reliable_max_retries,
    ),
    enable_heartbeat: normalizeBoolean(config.enable_heartbeat, normalized.config.enable_heartbeat),
    strict_heartbeat: normalizeBoolean(config.strict_heartbeat, normalized.config.strict_heartbeat),
    qos_depth: toInteger(config.qos_depth, normalized.config.qos_depth),
    heartbeat_timeout_ms: toInteger(
      config.heartbeat_timeout_ms,
      normalized.config.heartbeat_timeout_ms,
    ),
  };

  normalized.type_mappings = Object.fromEntries(
    Object.entries({ ...normalized.type_mappings, ...typeMappings }).map(([key, value]) => [
      normalizeString(key),
      normalizeString(value),
    ]),
  );

  const editableMessages = messages.map(normalizeMessage).filter((message) => !isSystemMessage(message));
  normalized.messages = editableMessages.length > 0 ? editableMessages : [createDefaultMessage([])];

  return normalized;
}

export function parseProtocolYaml(yamlText) {
  if (!yamlText || !yamlText.trim()) {
    throw new Error("YAML 内容为空。");
  }
  if (!globalThis.jsyaml?.load) {
    throw new Error("js-yaml 未正确加载。");
  }

  let parsed;
  try {
    parsed = globalThis.jsyaml.load(yamlText);
  } catch (error) {
    throw new Error(`YAML 解析失败: ${error.message}`);
  }

  if (!parsed || typeof parsed !== "object") {
    throw new Error("YAML 顶层必须是对象。");
  }

  return normalizeProtocol(parsed);
}

function validateRequiredString(errors, label, value) {
  if (!normalizeString(value).trim()) {
    errors.push(`${label} 不能为空。`);
  }
}

export function validateProtocol(protocol) {
  const errors = [];
  const cfg = protocol.config ?? {};
  const typeMappings = protocol.type_mappings ?? {};
  const messages = getAllMessages(protocol);

  if (!CHECKSUM_OPTIONS.includes(cfg.checksum)) {
    errors.push(
      `Unsupported checksum '${normalizeString(cfg.checksum)}'. Supported: ${CHECKSUM_OPTIONS.join(", ")}`,
    );
  }

  for (const key of ["head_byte_1", "head_byte_2"]) {
    if (!Number.isInteger(cfg[key]) || cfg[key] < 0 || cfg[key] > 255) {
      errors.push(`config.${key} 必须是 0x00-0xFF 范围内的整数。`);
    }
  }

  const seenIds = new Map();
  const seenNames = new Set();

  messages.forEach((message, messageIndex) => {
    const label = message.name || `messages[${messageIndex}]`;

    for (const fieldName of ["name", "id", "direction", "ros_msg", "fields"]) {
      if (message[fieldName] === undefined) {
        errors.push(`Message '${label}' missing required field '${fieldName}'.`);
      }
    }

    validateRequiredString(errors, `Message '${label}' 的 name`, message.name);
    validateRequiredString(errors, `Message '${label}' 的 ros_msg`, message.ros_msg);

    if (!DIRECTION_OPTIONS.includes(message.direction)) {
      errors.push(
        `Message '${label}' has invalid direction '${normalizeString(message.direction)}'. Must be one of: ${DIRECTION_OPTIONS.join(", ")}`,
      );
    }

    if (!DEBUG_LOG_MODE_OPTIONS.includes(message.debug_log_mode)) {
      errors.push(
        `Message '${label}' has invalid debug_log_mode '${normalizeString(message.debug_log_mode)}'. Must be one of: ${DEBUG_LOG_MODE_OPTIONS.join(", ")}`,
      );
    }

    if (!Number.isInteger(message.id) || message.id < 0 || message.id > 255) {
      errors.push(`Message '${label}' 的 id 必须是 0x00-0xFF 范围内的整数。`);
    } else if (seenIds.has(message.id)) {
      errors.push(
        `Message '${label}' has duplicate ID ${hexByte(message.id)} (conflicts with '${seenIds.get(message.id)}').`,
      );
    } else {
      seenIds.set(message.id, label);
    }

    if (seenNames.has(message.name)) {
      errors.push(`Duplicate message name '${message.name}'.`);
    } else {
      seenNames.add(message.name);
    }

    if ((message.direction === "tx" || message.direction === "both") && !message.sub_topic.trim()) {
      errors.push(`Message '${label}' (direction=${message.direction}) missing 'sub_topic'.`);
    }
    if ((message.direction === "rx" || message.direction === "both") && !message.pub_topic.trim()) {
      errors.push(`Message '${label}' (direction=${message.direction}) missing 'pub_topic'.`);
    }

    if (!Array.isArray(message.fields) || message.fields.length === 0) {
      errors.push(`Message '${label}' 至少需要一个 field。`);
      return;
    }

    message.fields.forEach((field, fieldIndex) => {
      const fieldLabel = `Message '${label}', field[${fieldIndex}]`;
      validateRequiredString(errors, `${fieldLabel} 的 proto`, field.proto);
      validateRequiredString(errors, `${fieldLabel} 的 type`, field.type);
      validateRequiredString(errors, `${fieldLabel} 的 ros`, field.ros);
      if (field.type && !(field.type in typeMappings) && !Object.values(typeMappings).includes(field.type)) {
        errors.push(
          `Message '${label}', field '${normalizeString(field.proto) || "?"}': unknown type '${normalizeString(field.type)}'.`,
        );
      }
    });
  });

  validateRequiredString(
    errors,
    "serial_controller.ros__parameters.port",
    protocol.serial_controller?.ros__parameters?.port,
  );

  return {
    valid: errors.length === 0,
    errors,
  };
}

function orderObjectEntries(objectValue, preferredKeys) {
  const remainingEntries = Object.entries(objectValue).filter(([key]) => !preferredKeys.includes(key));
  const orderedEntries = preferredKeys
    .filter((key) => Object.hasOwn(objectValue, key))
    .map((key) => [key, objectValue[key]]);
  return [...orderedEntries, ...remainingEntries];
}

function serializeInlineField(field) {
  return `{ proto: ${yamlSingleQuote(field.proto)}, type: ${yamlSingleQuote(field.type)}, ros: ${yamlSingleQuote(field.ros)} }`;
}

function appendSection(lines, key, objectValue, preferredKeys = [], transformValue) {
  lines.push(`${key}:`);
  for (const [entryKey, entryValue] of orderObjectEntries(objectValue, preferredKeys)) {
    const renderedValue = transformValue ? transformValue(entryKey, entryValue) : entryValue;
    lines.push(`  ${entryKey}: ${renderedValue}`);
  }
}

export function serializeProtocol(protocol) {
  const normalized = normalizeProtocol(protocol);
  const lines = [];
  const messages = getAllMessages(normalized);

  lines.push("serial_controller:");
  lines.push("  ros__parameters:");
  for (const [key, value] of orderObjectEntries(
    normalized.serial_controller.ros__parameters,
    ["port", "baudrate", "timeout", "debug_raw_frame"],
  )) {
    const renderedValue = typeof value === "number" || typeof value === "boolean" ? value : yamlSingleQuote(value);
    lines.push(`    ${key}: ${renderedValue}`);
  }
  lines.push("");

  appendSection(
    lines,
    "config",
    normalized.config,
    [
      "baudrate",
      "buffer_size",
      "head_byte_1",
      "head_byte_2",
      "checksum",
      "require_handshake",
      "ignore_version_mismatch",
      "reliable_retry_interval_ms",
      "reliable_max_retries",
      "enable_heartbeat",
      "strict_heartbeat",
      "qos_depth",
      "heartbeat_timeout_ms",
    ],
    (key, value) => {
      if (key === "head_byte_1" || key === "head_byte_2") {
        return hexByte(value);
      }
      if (typeof value === "string") {
        return yamlSingleQuote(value);
      }
      return value;
    },
  );
  lines.push("");

  appendSection(lines, "type_mappings", normalized.type_mappings, ["f32", "i32", "u8", "u16", "u32"], (_key, value) =>
    yamlSingleQuote(value),
  );
  lines.push("");

  lines.push("messages:");
  messages.forEach((message) => {
    lines.push(`  - name: ${yamlSingleQuote(message.name)}`);
    lines.push(`    id: ${hexByte(message.id)}`);
    lines.push(`    direction: ${yamlSingleQuote(message.direction)}`);
    lines.push(`    debug_log_mode: ${yamlSingleQuote(message.debug_log_mode)}`);
    if (message.reliable) {
      lines.push("    reliable: true");
    }
    if (message.sub_topic.trim()) {
      lines.push(`    sub_topic: ${yamlSingleQuote(message.sub_topic)}`);
    }
    if (message.pub_topic.trim()) {
      lines.push(`    pub_topic: ${yamlSingleQuote(message.pub_topic)}`);
    }
    lines.push(`    ros_msg: ${yamlSingleQuote(message.ros_msg)}`);
    if (message.notes.trim()) {
      lines.push(`    notes: ${yamlSingleQuote(message.notes)}`);
    }
    lines.push("    fields:");
    message.fields.forEach((field) => {
      lines.push(`      - ${serializeInlineField(field)}`);
    });
  });

  return `${lines.join("\n")}\n`;
}

function updateAtPath(target, path, rawValue) {
  const next = deepClone(target);
  const segments = path.split(".");
  let cursor = next;
  for (let index = 0; index < segments.length - 1; index += 1) {
    cursor = cursor[segments[index]];
  }
  const key = segments[segments.length - 1];
  cursor[key] = rawValue;
  return normalizeProtocol(next);
}

function removeTypeMapping(protocol, mappingKey) {
  const next = deepClone(protocol);
  delete next.type_mappings[mappingKey];
  return normalizeProtocol(next);
}

function addTypeMapping(protocol) {
  const next = deepClone(protocol);
  let index = 1;
  let key = `type_${index}`;
  while (Object.hasOwn(next.type_mappings, key)) {
    index += 1;
    key = `type_${index}`;
  }
  next.type_mappings[key] = "uint8_t";
  return normalizeProtocol(next);
}

function renameTypeMapping(protocol, oldKey, newKey) {
  const next = deepClone(protocol);
  const trimmed = normalizeString(newKey).trim();
  if (!trimmed || trimmed === oldKey || Object.hasOwn(next.type_mappings, trimmed)) {
    return normalizeProtocol(next);
  }
  const value = next.type_mappings[oldKey];
  delete next.type_mappings[oldKey];
  next.type_mappings[trimmed] = value;
  next.messages = next.messages.map((message) => ({
    ...message,
    fields: message.fields.map((field) =>
      field.type === oldKey
        ? {
            ...field,
            type: trimmed,
          }
        : field),
  }));
  return normalizeProtocol(next);
}

function updateMessage(protocol, messageIndex, updater) {
  const next = deepClone(protocol);
  next.messages[messageIndex] = updater(next.messages[messageIndex]);
  return normalizeProtocol(next);
}

function addMessage(protocol) {
  const next = deepClone(protocol);
  next.messages.push(createDefaultMessage(next.messages));
  return normalizeProtocol(next);
}

function removeMessage(protocol, messageIndex) {
  const next = deepClone(protocol);
  next.messages.splice(messageIndex, 1);
  if (next.messages.length === 0) {
    next.messages.push(createDefaultMessage([]));
  }
  return normalizeProtocol(next);
}

function addField(protocol, messageIndex) {
  return updateMessage(protocol, messageIndex, (message) => ({
    ...message,
    fields: [...message.fields, createDefaultField()],
  }));
}

function removeField(protocol, messageIndex, fieldIndex) {
  return updateMessage(protocol, messageIndex, (message) => {
    const nextFields = message.fields.filter((_, index) => index !== fieldIndex);
    return {
      ...message,
      fields: nextFields.length > 0 ? nextFields : [createDefaultField()],
    };
  });
}

function updateField(protocol, messageIndex, fieldIndex, key, value) {
  return updateMessage(protocol, messageIndex, (message) => ({
    ...message,
    fields: message.fields.map((field, index) =>
      index === fieldIndex
        ? {
            ...field,
            [key]: value,
          }
        : field),
  }));
}

function safeDownload(filename, text) {
  const blob = new Blob([text], { type: "text/yaml;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  document.body.removeChild(anchor);
  URL.revokeObjectURL(url);
}

function initApp() {
  if (typeof document === "undefined") {
    return;
  }

  const elements = {
    globalForm: document.querySelector("#global-form"),
    errorSummary: document.querySelector("#error-summary"),
    messageList: document.querySelector("#message-list"),
    yamlPreview: document.querySelector("#yaml-preview"),
    validationPill: document.querySelector("#validation-pill"),
    messageCountPill: document.querySelector("#message-count-pill"),
    sourcePill: document.querySelector("#source-pill"),
    importInput: document.querySelector("#import-input"),
    importButton: document.querySelector("#import-button"),
    exportButton: document.querySelector("#export-button"),
    resetButton: document.querySelector("#reset-button"),
    addMessageButton: document.querySelector("#add-message-button"),
    floatingAddButton: document.querySelector("#floating-add-button"),
  };

  let state = normalizeProtocol(DEFAULT_PROTOCOL);
  let baselineState = deepClone(state);
  let currentSource = "默认模板";

  function commit(nextState, options = {}) {
    state = normalizeProtocol(nextState);
    if (options.baseline) {
      baselineState = deepClone(state);
      currentSource = options.sourceLabel || currentSource;
    }
    render();
  }

  function renderGlobalForm() {
    const config = state.config;
    const rosParameters = state.serial_controller.ros__parameters;

    const rosFields = [
      {
        label: "串口设备",
        path: "serial_controller.ros__parameters.port",
        type: "text",
        value: rosParameters.port,
      },
      {
        label: "串口波特率",
        path: "serial_controller.ros__parameters.baudrate",
        type: "number",
        value: rosParameters.baudrate,
      },
      {
        label: "串口超时 (s)",
        path: "serial_controller.ros__parameters.timeout",
        type: "number",
        step: "0.01",
        value: rosParameters.timeout,
      },
      {
        label: "输出原始数据帧",
        path: "serial_controller.ros__parameters.debug_raw_frame",
        type: "checkbox",
        value: rosParameters.debug_raw_frame,
      },
    ];

    const configFields = [
      {
        label: "协议波特率",
        path: "config.baudrate",
        type: "number",
        value: config.baudrate,
      },
      {
        label: "缓冲区大小",
        path: "config.buffer_size",
        type: "number",
        value: config.buffer_size,
      },
      {
        label: "帧头字节 1",
        path: "config.head_byte_1",
        type: "text",
        value: hexByte(config.head_byte_1),
      },
      {
        label: "帧头字节 2",
        path: "config.head_byte_2",
        type: "text",
        value: hexByte(config.head_byte_2),
      },
      {
        label: "校验方式",
        path: "config.checksum",
        type: "select",
        value: config.checksum,
        options: CHECKSUM_OPTIONS,
      },
      {
        label: "QoS 深度",
        path: "config.qos_depth",
        type: "number",
        value: config.qos_depth,
      },
      {
        label: "心跳超时 (ms)",
        path: "config.heartbeat_timeout_ms",
        type: "number",
        value: config.heartbeat_timeout_ms,
      },
      {
        label: "启用握手",
        path: "config.require_handshake",
        type: "checkbox",
        value: config.require_handshake,
      },
      {
        label: "忽略协议版本不匹配",
        path: "config.ignore_version_mismatch",
        type: "checkbox",
        value: config.ignore_version_mismatch,
      },
      {
        label: "可靠传输重试间隔 (ms)",
        path: "config.reliable_retry_interval_ms",
        type: "number",
        value: config.reliable_retry_interval_ms,
      },
      {
        label: "可靠传输最大重试次数",
        path: "config.reliable_max_retries",
        type: "number",
        value: config.reliable_max_retries,
      },
      {
        label: "启用心跳",
        path: "config.enable_heartbeat",
        type: "checkbox",
        value: config.enable_heartbeat,
      },
      {
        label: "严格心跳检测",
        path: "config.strict_heartbeat",
        type: "checkbox",
        value: config.strict_heartbeat,
      },
    ];

    const renderFormField = (field) => {
      if (field.type === "select") {
        return `
          <label>
            <span>${escapeHtml(field.label)}</span>
            <select data-path="${escapeHtml(field.path)}">
              ${field.options
                .map(
                  (option) =>
                    `<option value="${escapeHtml(option)}" ${option === field.value ? "selected" : ""}>${escapeHtml(option)}</option>`,
                )
                .join("")}
            </select>
          </label>
        `;
      }
      if (field.type === "checkbox") {
        return `
          <label class="toggle-switch-container stack-inline">
            <div class="toggle-switch">
              <input type="checkbox" data-path="${escapeHtml(field.path)}" ${field.value ? "checked" : ""} />
              <span class="slider"></span>
            </div>
            <span>${escapeHtml(field.label)}</span>
          </label>
        `;
      }
      return `
        <label>
          <span>${escapeHtml(field.label)}</span>
          <input
            type="${escapeHtml(field.type)}"
            data-path="${escapeHtml(field.path)}"
            ${field.step ? `step="${escapeHtml(field.step)}"` : ""}
            value="${escapeHtml(String(field.value))}"
          />
        </label>
      `;
    };

    const rosFieldHtml = rosFields
      .map((field) => {
        return renderFormField(field);
      })
      .join("");

    const configFieldHtml = configFields.map((field) => renderFormField(field)).join("");

    elements.globalForm.innerHTML = `
      <div class="section-stack">
        <div class="card">
          <h3>串口参数</h3>
          <div class="field-grid two-col">${rosFieldHtml}</div>
        </div>
        <div class="card">
          <h3>协议参数</h3>
          <div class="field-grid two-col">${configFieldHtml}</div>
        </div>
      </div>
    `;

    const globalFormRoot = elements.globalForm;
    const numericPaths = new Set(
      CONFIG_NUMERIC_KEYS.map((key) => `config.${key}`).concat(
        ROS_NUMERIC_KEYS.map((key) => `serial_controller.ros__parameters.${key}`),
      ),
    );

    globalFormRoot.querySelectorAll("[data-path]").forEach((input) => {
      input.addEventListener("change", (event) => {
        const { path } = event.currentTarget.dataset;
        let nextValue;
        if (event.currentTarget.type === "checkbox") {
          nextValue = event.currentTarget.checked;
        } else if (path === "config.head_byte_1" || path === "config.head_byte_2") {
          nextValue = toInteger(event.currentTarget.value, 0);
        } else if (numericPaths.has(path)) {
          nextValue = path.endsWith(".timeout")
            ? toFiniteNumber(event.currentTarget.value, 0)
            : toInteger(event.currentTarget.value, 0);
        } else {
          nextValue = event.currentTarget.value;
        }
        commit(updateAtPath(state, path, nextValue));
      });
    });


  }

  function renderMessageList() {
    if (state.messages.length === 0) {
      elements.messageList.innerHTML = `
        <div class="empty-state">
          还没有消息定义，点击「添加消息」开始创建。
        </div>
      `;
      return;
    }

    elements.messageList.innerHTML = state.messages
      .map((message, messageIndex) => {
        const fieldsHtml = message.fields
          .map(
            (field, fieldIndex) => `
              <div class="field-row">
                <label>
                  <span>proto</span>
                  <input data-field="${messageIndex}:${fieldIndex}:proto" type="text" value="${escapeHtml(field.proto)}" />
                </label>
                <label>
                  <span>type</span>
                  <input data-field="${messageIndex}:${fieldIndex}:type" type="text" value="${escapeHtml(field.type)}" />
                </label>
                <label>
                  <span>ros</span>
                  <input data-field="${messageIndex}:${fieldIndex}:ros" type="text" value="${escapeHtml(field.ros)}" />
                </label>
                <button class="button button-danger" data-remove-field="${messageIndex}:${fieldIndex}">删除</button>
              </div>
            `,
          )
          .join("");

        return `
          <article class="message-card">
            <div class="message-card-header">
              <div class="message-title">
                <strong>${escapeHtml(message.name || `Message ${messageIndex + 1}`)}</strong>
                <span>${escapeHtml(hexByte(message.id))} · ${escapeHtml(message.direction.toUpperCase())} · ${escapeHtml(message.ros_msg || "未设置 ROS 消息类型")}</span>
              </div>
              <button class="button button-danger" data-remove-message="${messageIndex}">删除消息</button>
            </div>
            <div class="message-card-body">
              <div class="field-grid three-col">
                <label>
                  <span>名称</span>
                  <input data-message="${messageIndex}:name" type="text" value="${escapeHtml(message.name)}" />
                </label>
                <label>
                  <span>ID (十六进制或十进制)</span>
                  <input data-message="${messageIndex}:id" type="text" value="${hexByte(message.id)}" />
                </label>
                <label>
                  <span>方向</span>
                  <select data-message="${messageIndex}:direction">
                    ${DIRECTION_OPTIONS.map(
                      (direction) =>
                        `<option value="${direction}" ${direction === message.direction ? "selected" : ""}>${direction}</option>`,
                    ).join("")}
                  </select>
                </label>
              </div>
              <div class="field-grid three-col">
                <label>
                  <span>订阅话题</span>
                  <input data-message="${messageIndex}:sub_topic" type="text" value="${escapeHtml(message.sub_topic)}" />
                </label>
                <label>
                  <span>发布话题</span>
                  <input data-message="${messageIndex}:pub_topic" type="text" value="${escapeHtml(message.pub_topic)}" />
                </label>
                <label>
                  <span>调试输出模式</span>
                  <select data-message="${messageIndex}:debug_log_mode">
                    ${DEBUG_LOG_MODE_OPTIONS.map(
                      (mode) =>
                        `<option value="${mode}" ${mode === message.debug_log_mode ? "selected" : ""}>${mode}</option>`,
                    ).join("")}
                  </select>
                </label>
              </div>
              <div class="field-grid">
                <label class="toggle-switch-container stack-inline">
                  <div class="toggle-switch">
                    <input type="checkbox" data-message="${messageIndex}:reliable" ${message.reliable ? "checked" : ""} />
                    <span class="slider"></span>
                  </div>
                  <span>启用可靠发送（等待 ACK 并按配置重试）</span>
                </label>
              </div>
              <div class="field-grid two-col">
                <label>
                  <span>ROS 消息类型</span>
                  <input data-message="${messageIndex}:ros_msg" type="text" value="${escapeHtml(message.ros_msg)}" />
                </label>
                <label>
                  <span>备注</span>
                  <textarea data-message="${messageIndex}:notes">${escapeHtml(message.notes)}</textarea>
                </label>
              </div>
              <div class="card">
                <div class="field-list-header">
                  <h3>字段列表</h3>
                  <button class="button" data-add-field="${messageIndex}">添加字段</button>
                </div>
                <div class="field-list">${fieldsHtml}</div>
              </div>
            </div>
          </article>
        `;
      })
      .join("");

    elements.messageList.querySelectorAll("[data-message]").forEach((input) => {
      input.addEventListener("change", (event) => {
        const [messageIndexText, key] = event.currentTarget.dataset.message.split(":");
        const messageIndex = Number(messageIndexText);
        let nextValue;
        if (event.currentTarget.type === "checkbox") {
          nextValue = event.currentTarget.checked;
        } else if (key === "id") {
          nextValue = toInteger(event.currentTarget.value, 0);
        } else {
          nextValue = event.currentTarget.value;
        }
        commit(
          updateMessage(state, messageIndex, (message) => ({
            ...message,
            [key]: nextValue,
          })),
        );
      });
    });

    elements.messageList.querySelectorAll("[data-add-field]").forEach((button) => {
      button.addEventListener("click", () => {
        commit(addField(state, Number(button.dataset.addField)));
      });
    });

    elements.messageList.querySelectorAll("[data-remove-message]").forEach((button) => {
      button.addEventListener("click", () => {
        commit(removeMessage(state, Number(button.dataset.removeMessage)));
      });
    });

    elements.messageList.querySelectorAll("[data-field]").forEach((input) => {
      input.addEventListener("change", (event) => {
        const [messageIndexText, fieldIndexText, key] = event.currentTarget.dataset.field.split(":");
        commit(
          updateField(
            state,
            Number(messageIndexText),
            Number(fieldIndexText),
            key,
            event.currentTarget.value,
          ),
        );
      });
    });

    elements.messageList.querySelectorAll("[data-remove-field]").forEach((button) => {
      button.addEventListener("click", () => {
        const [messageIndexText, fieldIndexText] = button.dataset.removeField.split(":");
        commit(removeField(state, Number(messageIndexText), Number(fieldIndexText)));
      });
    });
  }

  function renderValidation(validation, yamlText) {
    elements.yamlPreview.textContent = yamlText;
    elements.messageCountPill.textContent = `${state.messages.length} 条可编辑消息 + ${SYSTEM_MESSAGES.length} 条系统消息`;
    elements.sourcePill.textContent = currentSource;

    if (validation.valid) {
      elements.validationPill.textContent = "配置有效，可导出";
      elements.validationPill.className = "pill pill-ok";
      elements.exportButton.disabled = false;
      elements.errorSummary.innerHTML = `
        <div class="hint">
          校验通过，可导出。
        </div>
      `;
    } else {
      elements.validationPill.textContent = `存在 ${validation.errors.length} 个问题`;
      elements.validationPill.className = "pill pill-warning";
      elements.exportButton.disabled = true;
      elements.errorSummary.innerHTML = `
        <div class="error-box">
          <h3>配置校验失败</h3>
          <ul>${validation.errors.map((error) => `<li>${error}</li>`).join("")}</ul>
        </div>
      `;
    }
  }

  function render() {
    renderGlobalForm();
    renderMessageList();
    const validation = validateProtocol(state);
    const yamlText = serializeProtocol(state);
    renderValidation(validation, yamlText);
  }

  elements.importButton.addEventListener("click", () => {
    elements.importInput.click();
  });

  elements.importInput.addEventListener("change", async (event) => {
    const [file] = event.currentTarget.files ?? [];
    if (!file) {
      return;
    }

    try {
      const text = await file.text();
      const parsed = parseProtocolYaml(text);
      commit(parsed, {
        baseline: true,
        sourceLabel: `已导入: ${file.name}`,
      });
    } catch (error) {
      window.alert(error.message);
    } finally {
      event.currentTarget.value = "";
    }
  });

  elements.exportButton.addEventListener("click", () => {
    const validation = validateProtocol(state);
    if (!validation.valid) {
      window.alert("当前配置存在校验错误，无法导出。");
      return;
    }
    safeDownload("protocol.yaml", serializeProtocol(state));
  });

  elements.resetButton.addEventListener("click", () => {
    commit(deepClone(baselineState), {
      sourceLabel: currentSource,
    });
  });

  elements.addMessageButton.addEventListener("click", () => {
    commit(addMessage(state));
  });

  elements.floatingAddButton.addEventListener("click", () => {
    commit(addMessage(state));
  });

  commit(state, {
    baseline: true,
    sourceLabel: currentSource,
  });
}

if (typeof window !== "undefined") {
  initApp();
}
