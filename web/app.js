/*
 * auto_serial_bridge 协议配置器。
 *
 * 只编辑业务消息；系统消息 Ack/Heartbeat/Handshake 由 scripts/codegen.py
 * 内置注入，不出现在 protocol.yaml 中。校验规则与 codegen.py 保持一致。
 */

(function (globalScope) {
  "use strict";

  const CHECKSUM_OPTIONS = ["NONE", "SUM8", "XOR8", "CRC8"];
  const DIRECTION_OPTIONS = ["tx", "rx", "both"];
  const DEBUG_LOG_MODE_OPTIONS = ["on", "off"];
  const CONFIG_NUMERIC_KEYS = [
    "buffer_size",
    "head_byte_1",
    "head_byte_2",
    "reliable_retry_interval_ms",
    "reliable_max_retries",
    "qos_depth",
    "heartbeat_interval_ms",
    "heartbeat_timeout_ms",
  ];
  const ROS_NUMERIC_KEYS = ["baudrate"];

  // 与 codegen.py 的 _C_TYPE_SIZES 一致
  const C_TYPE_SIZES = {
    uint8_t: 1,
    uint16_t: 2,
    uint32_t: 4,
    int32_t: 4,
    float: 4,
  };

  // 与 codegen.py 的 SYSTEM_MESSAGES 一致（仅用于校验保留 ID/名称与提示）
  const RESERVED_IDS = new Map([
    [0xfd, "Ack"],
    [0xfe, "Heartbeat"],
    [0xff, "Handshake"],
  ]);
  const RESERVED_NAMES = new Set(["Ack", "Heartbeat", "Handshake"]);
  const SYSTEM_MAX_WIRE_PAYLOAD = 4; // Heartbeat/Handshake payload = 4 字节

  const IDENTIFIER_RE = /^[A-Za-z_][A-Za-z0-9_]*$/;
  const ROS_MSG_RE = /^[a-z][a-z0-9_]*\/msg\/[A-Z][A-Za-z0-9_]*$/;
  const TOPIC_RE = /^(?!\/)(?!.*\/\/)[A-Za-z0-9_~][A-Za-z0-9_~/]*$/;
  const ROS_PATH_SEGMENT_RE = /^[A-Za-z_][A-Za-z0-9_]*(\[\d+\])?$/;

  const DEFAULT_PROTOCOL = Object.freeze({
    serial_controller: {
      ros__parameters: {
        port: "/dev/ttyACM0",
        baudrate: 115200,
        debug_raw_frame: false,
      },
    },
    config: {
      buffer_size: 1024,
      head_byte_1: 0x5a,
      head_byte_2: 0xa5,
      checksum: "CRC8",
      require_handshake: true,
      ignore_version_mismatch: false,
      reliable_retry_interval_ms: 100,
      reliable_max_retries: 3,
      strict_heartbeat: true,
      qos_depth: 10,
      heartbeat_interval_ms: 1000,
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

  function escapeHtml(value) {
    return normalizeString(value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function resolveCType(type, typeMappings) {
    const mapped = typeMappings && Object.hasOwn(typeMappings, type) ? typeMappings[type] : type;
    return mapped;
  }

  function fieldSize(field, typeMappings) {
    return C_TYPE_SIZES[resolveCType(field.type, typeMappings)] ?? 1;
  }

  function wirePayloadSize(message, typeMappings) {
    const base = message.fields.reduce((sum, field) => sum + fieldSize(field, typeMappings), 0);
    return message.reliable ? base + 1 : base;
  }

  function createDefaultField() {
    return {
      proto: "value",
      type: "u8",
      ros: "data",
    };
  }

  function createDefaultMessage(existingMessages = []) {
    const usedIds = new Set(
      existingMessages
        .map((message) => toInteger(message.id, -1))
        .filter((value) => value >= 0 && value <= 255),
    );
    for (const reservedId of RESERVED_IDS.keys()) {
      usedIds.add(reservedId);
    }

    let nextId = 1;
    while (usedIds.has(nextId) && nextId <= 255) {
      nextId += 1;
    }

    const usedNames = new Set(existingMessages.map((message) => normalizeString(message.name)));
    let nameIndex = existingMessages.length + 1;
    while (usedNames.has(`NewMessage${nameIndex}`)) {
      nameIndex += 1;
    }

    return {
      name: `NewMessage${nameIndex}`,
      id: nextId,
      direction: "tx",
      debug_log_mode: "on",
      reliable: false,
      sub_topic: `demo/new_message_${nameIndex}`,
      pub_topic: `demo/new_message_${nameIndex}_state`,
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

  function isLegacySystemMessage(message) {
    const id = toInteger(message?.id, -1);
    const reservedName = RESERVED_IDS.get(id);
    if (!reservedName) {
      return false;
    }
    return normalizeString(message?.name).trim().toLowerCase() === reservedName.toLowerCase();
  }

  function normalizeProtocol(rawData = {}) {
    const serialParameters = rawData?.serial_controller?.ros__parameters ?? {};
    const config = rawData?.config ?? {};
    const typeMappings = rawData?.type_mappings ?? {};
    const messages = Array.isArray(rawData?.messages) ? rawData.messages : [];

    const normalized = deepClone(DEFAULT_PROTOCOL);

    normalized.serial_controller.ros__parameters = {
      port: normalizeString(serialParameters.port || normalized.serial_controller.ros__parameters.port),
      baudrate: toInteger(serialParameters.baudrate, normalized.serial_controller.ros__parameters.baudrate),
      debug_raw_frame: normalizeBoolean(
        serialParameters.debug_raw_frame,
        normalized.serial_controller.ros__parameters.debug_raw_frame,
      ),
    };

    normalized.config = {
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
      // 旧版 protocol.yaml 可能包含 enable_heartbeat，导入时静默剔除（心跳始终开启）
      strict_heartbeat: normalizeBoolean(config.strict_heartbeat, normalized.config.strict_heartbeat),
      qos_depth: toInteger(config.qos_depth, normalized.config.qos_depth),
      heartbeat_interval_ms: toInteger(
        config.heartbeat_interval_ms,
        normalized.config.heartbeat_interval_ms,
      ),
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

    // 旧版 protocol.yaml 会显式包含系统消息，导入时静默剔除（现由 codegen 注入）。
    normalized.messages = messages
      .map(normalizeMessage)
      .filter((message) => !isLegacySystemMessage(message));

    return normalized;
  }

  function parseProtocolYaml(yamlText) {
    if (!yamlText || !yamlText.trim()) {
      throw new Error("YAML 内容为空。");
    }
    if (!globalScope.jsyaml?.load) {
      throw new Error("js-yaml 未正确加载。");
    }

    let parsed;
    try {
      parsed = globalScope.jsyaml.load(yamlText);
    } catch (error) {
      throw new Error(`YAML 解析失败: ${error.message}`);
    }

    if (!parsed || typeof parsed !== "object") {
      throw new Error("YAML 顶层必须是对象。");
    }

    return normalizeProtocol(parsed);
  }

  function validateRosPath(path) {
    return path.split(".").every((segment) => ROS_PATH_SEGMENT_RE.test(segment));
  }

  function validateProtocol(protocol) {
    const errors = [];
    const cfg = protocol.config ?? {};
    const typeMappings = protocol.type_mappings ?? {};
    const messages = Array.isArray(protocol.messages) ? protocol.messages : [];

    if (!CHECKSUM_OPTIONS.includes(cfg.checksum)) {
      errors.push(`不支持的校验算法 '${normalizeString(cfg.checksum)}'，可选：${CHECKSUM_OPTIONS.join(", ")}。`);
    }

    for (const key of ["head_byte_1", "head_byte_2"]) {
      if (!Number.isInteger(cfg[key]) || cfg[key] < 0 || cfg[key] > 255) {
        errors.push(`config.${key} 必须是 0x00-0xFF 范围内的整数。`);
      }
    }

    for (const key of [
      "buffer_size",
      "reliable_retry_interval_ms",
      "reliable_max_retries",
      "qos_depth",
      "heartbeat_interval_ms",
      "heartbeat_timeout_ms",
    ]) {
      if (!Number.isInteger(cfg[key]) || cfg[key] <= 0) {
        errors.push(`config.${key} 必须是正整数。`);
      }
    }

    if (
      Number.isInteger(cfg.heartbeat_interval_ms) &&
      Number.isInteger(cfg.heartbeat_timeout_ms) &&
      cfg.heartbeat_interval_ms > 0 &&
      cfg.heartbeat_timeout_ms > 0 &&
      cfg.heartbeat_timeout_ms < cfg.heartbeat_interval_ms
    ) {
      errors.push("config.heartbeat_timeout_ms 必须 >= heartbeat_interval_ms。");
    }

    if (!normalizeString(protocol.serial_controller?.ros__parameters?.port).trim()) {
      errors.push("serial_controller.ros__parameters.port 不能为空。");
    }
    const baudrate = protocol.serial_controller?.ros__parameters?.baudrate;
    if (!Number.isInteger(baudrate) || baudrate <= 0) {
      errors.push("serial_controller.ros__parameters.baudrate 必须是正整数。");
    }

    const seenIds = new Map();
    const seenNames = new Set();
    const seenSubTopics = new Map();
    const seenPubTopics = new Map();
    let largestWirePayload = SYSTEM_MAX_WIRE_PAYLOAD;

    messages.forEach((message, messageIndex) => {
      const label = message.name || `messages[${messageIndex}]`;

      if (!IDENTIFIER_RE.test(normalizeString(message.name))) {
        errors.push(`消息 '${label}' 的 name 必须是合法 C 标识符。`);
      } else if (RESERVED_NAMES.has(message.name)) {
        errors.push(`消息名 '${message.name}' 是框架内置系统消息，会由生成器自动注入，请改用其他名称。`);
      } else if (seenNames.has(message.name)) {
        errors.push(`消息名 '${message.name}' 重复。`);
      } else {
        seenNames.add(message.name);
      }

      if (!Number.isInteger(message.id) || message.id < 0 || message.id > 255) {
        errors.push(`消息 '${label}' 的 id 必须是 0x00-0xFF 范围内的整数。`);
      } else if (RESERVED_IDS.has(message.id)) {
        errors.push(`消息 '${label}' 使用了保留 ID ${hexByte(message.id)}（框架内置 ${RESERVED_IDS.get(message.id)}），请使用 0x00-0xFC。`);
      } else if (seenIds.has(message.id)) {
        errors.push(`消息 '${label}' 的 ID ${hexByte(message.id)} 与 '${seenIds.get(message.id)}' 冲突。`);
      } else {
        seenIds.set(message.id, label);
      }

      if (!DIRECTION_OPTIONS.includes(message.direction)) {
        errors.push(`消息 '${label}' 的 direction '${normalizeString(message.direction)}' 无效，可选：${DIRECTION_OPTIONS.join(", ")}。`);
      }

      if (!DEBUG_LOG_MODE_OPTIONS.includes(message.debug_log_mode)) {
        errors.push(`消息 '${label}' 的 debug_log_mode '${normalizeString(message.debug_log_mode)}' 无效，可选：on / off。`);
      }

      if (message.reliable && message.direction !== "tx") {
        errors.push(`消息 '${label}' 启用了 reliable，但 direction 是 '${message.direction}'。reliable 仅支持 tx (ROS → MCU)。`);
      }

      if (!ROS_MSG_RE.test(normalizeString(message.ros_msg))) {
        errors.push(`消息 '${label}' 的 ros_msg '${normalizeString(message.ros_msg)}' 无效，格式应为 package/msg/Type。`);
      }

      const needsSub = message.direction === "tx" || message.direction === "both";
      const needsPub = message.direction === "rx" || message.direction === "both";
      if (needsSub) {
        if (!TOPIC_RE.test(message.sub_topic)) {
          errors.push(`消息 '${label}' 的 sub_topic '${message.sub_topic}' 无效，请使用不带前导 / 的相对话题。`);
        } else if (seenSubTopics.has(message.sub_topic)) {
          errors.push(`消息 '${label}' 的 sub_topic '${message.sub_topic}' 与 '${seenSubTopics.get(message.sub_topic)}' 重复。`);
        } else {
          seenSubTopics.set(message.sub_topic, label);
        }
      }
      if (needsPub) {
        if (!TOPIC_RE.test(message.pub_topic)) {
          errors.push(`消息 '${label}' 的 pub_topic '${message.pub_topic}' 无效，请使用不带前导 / 的相对话题。`);
        } else if (seenPubTopics.has(message.pub_topic)) {
          errors.push(`消息 '${label}' 的 pub_topic '${message.pub_topic}' 与 '${seenPubTopics.get(message.pub_topic)}' 重复。`);
        } else {
          seenPubTopics.set(message.pub_topic, label);
        }
      }
      if (message.direction === "both" && message.sub_topic && message.sub_topic === message.pub_topic) {
        errors.push(`消息 '${label}' 的 sub_topic 与 pub_topic 相同，会把 MCU 数据原样回环发回 MCU，请使用两个不同话题。`);
      }

      if (!Array.isArray(message.fields) || message.fields.length === 0) {
        errors.push(`消息 '${label}' 至少需要一个 field。`);
        return;
      }

      const seenFieldNames = new Set();
      message.fields.forEach((field, fieldIndex) => {
        const fieldLabel = `消息 '${label}' 的 field[${fieldIndex}]`;
        if (!IDENTIFIER_RE.test(normalizeString(field.proto))) {
          errors.push(`${fieldLabel} 的 proto '${normalizeString(field.proto)}' 必须是合法 C 标识符。`);
        } else if (seenFieldNames.has(field.proto)) {
          errors.push(`消息 '${label}' 的字段名 '${field.proto}' 重复。`);
        } else {
          seenFieldNames.add(field.proto);
        }

        const cType = resolveCType(normalizeString(field.type), typeMappings);
        if (!Object.hasOwn(C_TYPE_SIZES, cType)) {
          errors.push(`${fieldLabel} 的 type '${normalizeString(field.type)}' 未知，支持：${Object.keys(C_TYPE_SIZES).join(", ")} 或 type_mappings 中的别名。`);
        }

        const rosPath = normalizeString(field.ros);
        if (!rosPath.trim()) {
          errors.push(`${fieldLabel} 的 ros 路径不能为空。`);
        } else if (!validateRosPath(rosPath)) {
          errors.push(`${fieldLabel} 的 ros 路径 '${rosPath}' 无效，支持形如 data、pose.x、data[0]。`);
        }
      });

      const wireSize = wirePayloadSize(message, typeMappings);
      if (wireSize > 255) {
        errors.push(`消息 '${label}' 的链路 payload 为 ${wireSize} 字节，超过 255 字节上限。`);
      }
      largestWirePayload = Math.max(largestWirePayload, wireSize);
    });

    if (Number.isInteger(cfg.buffer_size) && cfg.buffer_size > 0) {
      const largestFrame = largestWirePayload + 5;
      if (cfg.buffer_size < largestFrame) {
        errors.push(`config.buffer_size=${cfg.buffer_size} 无法容纳最大完整帧（${largestFrame} 字节）。`);
      }
    }

    return {
      valid: errors.length === 0,
      errors,
    };
  }

  function exportMessage(message) {
    const output = {
      name: message.name,
      id: message.id,
      direction: message.direction,
    };
    if (message.reliable) {
      output.reliable = true;
    }
    if (message.debug_log_mode === "off") {
      output.debug_log_mode = "off";
    }
    if (message.direction === "tx" || message.direction === "both") {
      output.sub_topic = message.sub_topic;
    }
    if (message.direction === "rx" || message.direction === "both") {
      output.pub_topic = message.pub_topic;
    }
    output.ros_msg = message.ros_msg;
    if (message.notes.trim()) {
      output.notes = message.notes;
    }
    output.fields = message.fields.map((field) => ({
      proto: field.proto,
      type: field.type,
      ros: field.ros,
    }));
    return output;
  }

  function serializeProtocol(protocol) {
    if (!globalScope.jsyaml?.dump) {
      throw new Error("js-yaml 未正确加载。");
    }
    const normalized = normalizeProtocol(protocol);
    const doc = {
      serial_controller: normalized.serial_controller,
      config: normalized.config,
      type_mappings: normalized.type_mappings,
      messages: normalized.messages.map(exportMessage),
    };
    let text = globalScope.jsyaml.dump(doc, {
      noRefs: true,
      lineWidth: -1,
      sortKeys: false,
    });
    // 帧头与消息 ID 以十六进制展示，提升可读性（YAML 语义等价）。
    text = text.replace(/^(\s*head_byte_[12]:) (\d+)$/gm, (_m, key, value) => `${key} ${hexByte(value)}`);
    text = text.replace(/^(\s{2,}id:) (\d+)$/gm, (_m, key, value) => `${key} ${hexByte(value)}`);
    return text;
  }

  function updateAtPath(target, path, rawValue) {
    const next = deepClone(target);
    const segments = path.split(".");
    let cursor = next;
    for (let index = 0; index < segments.length - 1; index += 1) {
      cursor = cursor[segments[index]];
    }
    cursor[segments[segments.length - 1]] = rawValue;
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
        { label: "串口设备", path: "serial_controller.ros__parameters.port", type: "text", value: rosParameters.port },
        { label: "串口波特率", path: "serial_controller.ros__parameters.baudrate", type: "number", value: rosParameters.baudrate },
        { label: "输出原始数据帧", path: "serial_controller.ros__parameters.debug_raw_frame", type: "checkbox", value: rosParameters.debug_raw_frame },
      ];

      const configFields = [
        { label: "缓冲区大小", path: "config.buffer_size", type: "number", value: config.buffer_size },
        { label: "帧头字节 1", path: "config.head_byte_1", type: "text", value: hexByte(config.head_byte_1) },
        { label: "帧头字节 2", path: "config.head_byte_2", type: "text", value: hexByte(config.head_byte_2) },
        { label: "校验方式", path: "config.checksum", type: "select", value: config.checksum, options: CHECKSUM_OPTIONS },
        { label: "QoS 深度", path: "config.qos_depth", type: "number", value: config.qos_depth },
        { label: "心跳发送间隔 (ms)", path: "config.heartbeat_interval_ms", type: "number", value: config.heartbeat_interval_ms },
        { label: "心跳超时 (ms)", path: "config.heartbeat_timeout_ms", type: "number", value: config.heartbeat_timeout_ms },
        { label: "启用握手", path: "config.require_handshake", type: "checkbox", value: config.require_handshake },
        { label: "忽略协议版本不匹配", path: "config.ignore_version_mismatch", type: "checkbox", value: config.ignore_version_mismatch },
        { label: "可靠传输重试间隔 (ms)", path: "config.reliable_retry_interval_ms", type: "number", value: config.reliable_retry_interval_ms },
        { label: "可靠传输最大重试次数", path: "config.reliable_max_retries", type: "number", value: config.reliable_max_retries },
        { label: "严格心跳检测", path: "config.strict_heartbeat", type: "checkbox", value: config.strict_heartbeat },
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
              value="${escapeHtml(String(field.value))}"
            />
          </label>
        `;
      };

      const rosFieldHtml = rosFields.map(renderFormField).join("");
      const configFieldHtml = configFields.map(renderFormField).join("");

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

      const numericPaths = new Set(
        CONFIG_NUMERIC_KEYS.map((key) => `config.${key}`).concat(
          ROS_NUMERIC_KEYS.map((key) => `serial_controller.ros__parameters.${key}`),
        ),
      );

      elements.globalForm.querySelectorAll("[data-path]").forEach((input) => {
        input.addEventListener("change", (event) => {
          const { path } = event.currentTarget.dataset;
          let nextValue;
          if (event.currentTarget.type === "checkbox") {
            nextValue = event.currentTarget.checked;
          } else if (numericPaths.has(path)) {
            nextValue = toInteger(event.currentTarget.value, 0);
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
            还没有业务消息，点击「添加消息」开始创建。<br />
            系统消息（Ack / Heartbeat / Handshake）由框架内置，无需配置。
          </div>
        `;
        return;
      }

      elements.messageList.innerHTML = state.messages
        .map((message, messageIndex) => {
          const needsSub = message.direction === "tx" || message.direction === "both";
          const needsPub = message.direction === "rx" || message.direction === "both";
          const fieldsHtml = message.fields
            .map(
              (field, fieldIndex) => `
                <div class="field-row">
                  <label>
                    <span>proto（协议字段名）</span>
                    <input data-field="${messageIndex}:${fieldIndex}:proto" type="text" value="${escapeHtml(field.proto)}" />
                  </label>
                  <label>
                    <span>type</span>
                    <input data-field="${messageIndex}:${fieldIndex}:type" type="text" value="${escapeHtml(field.type)}" list="type-options" />
                  </label>
                  <label>
                    <span>ros（ROS 字段路径）</span>
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
                    <span>ID（0x00-0xFC，支持十六进制）</span>
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
                  ${needsSub ? `
                  <label>
                    <span>订阅话题（ROS → MCU）</span>
                    <input data-message="${messageIndex}:sub_topic" type="text" value="${escapeHtml(message.sub_topic)}" />
                  </label>` : ""}
                  ${needsPub ? `
                  <label>
                    <span>发布话题（MCU → ROS）</span>
                    <input data-message="${messageIndex}:pub_topic" type="text" value="${escapeHtml(message.pub_topic)}" />
                  </label>` : ""}
                  <label>
                    <span>调试日志</span>
                    <select data-message="${messageIndex}:debug_log_mode">
                      ${DEBUG_LOG_MODE_OPTIONS.map(
                        (mode) =>
                          `<option value="${mode}" ${mode === message.debug_log_mode ? "selected" : ""}>${mode}</option>`,
                      ).join("")}
                    </select>
                  </label>
                </div>
                ${message.direction === "tx" ? `
                <div class="field-grid">
                  <label class="toggle-switch-container stack-inline">
                    <div class="toggle-switch">
                      <input type="checkbox" data-message="${messageIndex}:reliable" ${message.reliable ? "checked" : ""} />
                      <span class="slider"></span>
                    </div>
                    <span>启用可靠发送（等待 ACK 并按配置重试，仅 tx 支持）</span>
                  </label>
                </div>` : ""}
                <div class="field-grid two-col">
                  <label>
                    <span>ROS 消息类型（package/msg/Type）</span>
                    <input data-message="${messageIndex}:ros_msg" type="text" value="${escapeHtml(message.ros_msg)}" />
                  </label>
                  <label>
                    <span>备注</span>
                    <textarea data-message="${messageIndex}:notes">${escapeHtml(message.notes)}</textarea>
                  </label>
                </div>
                <div class="card">
                  <div class="field-list-header">
                    <h3>字段列表（按线协议字节顺序）</h3>
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
      elements.messageCountPill.textContent = `${state.messages.length} 条业务消息`;
      elements.sourcePill.textContent = currentSource;

      if (validation.valid) {
        elements.validationPill.textContent = "配置有效，可导出";
        elements.validationPill.className = "pill pill-ok";
        elements.exportButton.disabled = false;
        elements.errorSummary.innerHTML = `<div class="hint">校验通过，可导出。</div>`;
      } else {
        elements.validationPill.textContent = `存在 ${validation.errors.length} 个问题`;
        elements.validationPill.className = "pill pill-warning";
        elements.exportButton.disabled = true;
        elements.errorSummary.innerHTML = `
          <div class="error-box">
            <h3>配置校验失败</h3>
            <ul>${validation.errors.map((error) => `<li>${escapeHtml(error)}</li>`).join("")}</ul>
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

  const api = {
    normalizeProtocol,
    parseProtocolYaml,
    validateProtocol,
    serializeProtocol,
    createDefaultMessage,
  };

  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }
  globalScope.protocolEditor = api;

  if (typeof document !== "undefined" && document.querySelector("#global-form")) {
    initApp();
  }
})(typeof globalThis !== "undefined" ? globalThis : this);
