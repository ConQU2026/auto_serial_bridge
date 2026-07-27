import yaml
import os
import sys
import hashlib
import argparse
import re
from datetime import datetime

def generate_crc8_table():
    """生成CRC8查找表。

    使用多项式0x31计算256个可能值的CRC8校验码。

    Returns:
        包含256个uint8_t值的列表。
    """
    polynomial = 0x31
    table = []
    for i in range(256):
        crc = i
        for _ in range(8):
            if crc & 0x80:
                crc = (crc << 1) ^ polynomial
            else:
                crc = crc << 1
        table.append(crc & 0xFF)
    return table

def canonicalize_protocol_hash_input(protocol_source):
    """将协议配置规范化为稳定文本，用于计算协议哈希。

    忽略空白、注释和 mapping 键顺序差异，但保留列表顺序，
    以便把真正会影响协议布局的变化保留下来。

    Args:
        protocol_source: YAML 原始文本或已解析的配置字典。

    Returns:
        规范化后的 YAML 文本。
    """
    if isinstance(protocol_source, str):
        config_data = yaml.safe_load(protocol_source)
    else:
        config_data = protocol_source
    config_data = _protocol_hash_input(config_data)

    return yaml.safe_dump(
        config_data,
        sort_keys=True,
        default_flow_style=False,
        allow_unicode=True,
    )


def _protocol_hash_input(config_data):
    """Return only fields that affect the wire protocol or peer contract."""
    cfg = config_data.get('config', {})
    type_mappings = config_data.get('type_mappings', {})
    messages = []
    for message in config_data.get('messages', []):
        messages.append({
            'id': message['id'],
            'direction': message['direction'],
            'reliable': bool(message.get('reliable', False)),
            'field_types': [
                get_c_type(field['type'], type_mappings)
                for field in message.get('fields', [])
            ],
        })

    return {
        'head_byte_1': cfg['head_byte_1'],
        'head_byte_2': cfg['head_byte_2'],
        'checksum': cfg.get('checksum', 'CRC8').upper(),
        'require_handshake': bool(cfg.get('require_handshake', True)),
        'enable_heartbeat': bool(cfg.get('enable_heartbeat', True)),
        'messages': messages,
    }


def calculate_protocol_hash(protocol_source):
    """计算协议哈希值。

    基于协议的规范化结构计算 MD5 哈希，用于校验 MCU 和 ROS 端的协议一致性。

    Args:
        protocol_source: YAML 原始文本或已解析的配置字典。

    Returns:
        32位整数哈希值。
    """
    canonical_content = canonicalize_protocol_hash_input(protocol_source)
    return int(hashlib.md5(canonical_content.encode('utf-8')).hexdigest()[:8], 16)

def get_c_type(yaml_type, type_mappings):
    """获取对应的C语言类型。

    Args:
        yaml_type: YAML中定义的类型名称。
        type_mappings: 类型映射字典。

    Returns:
        对应的C语言类型字符串，如果没有映射则返回原值。
    """
    if yaml_type in type_mappings:
        return type_mappings[yaml_type]
    return yaml_type  # 兜底返回


def ros_message_packages(config_data):
    """Return sorted unique package names referenced by ros_msg entries."""
    return sorted({
        message['ros_msg'].split('/', 1)[0]
        for message in config_data.get('messages', [])
    })


def message_payload_size(message, type_mappings):
    """计算协议结构体 payload 大小，不含 reliable seq。"""
    return sum(
        _C_TYPE_SIZES.get(get_c_type(field['type'], type_mappings), 1)
        for field in message['fields']
    )


def message_wire_payload_size(message, type_mappings):
    """计算链路上传输的 payload 大小，reliable 消息额外包含 1 字节 seq。"""
    base_size = message_payload_size(message, type_mappings)
    if message.get('reliable', False):
        return base_size + 1
    return base_size

def extract_user_code(file_path):
    """Extract user code blocks from an existing file.
    
    Args:
        file_path: Path to the existing file.

    Returns:
        A dictionary mapping block keys to their content (list of lines).
    """
    blocks = {}
    if not os.path.exists(file_path):
        return blocks

    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()

    pattern = re.compile(r'/\* USER CODE BEGIN (\w+) \*/(.*?)/\* USER CODE END \1 \*/', re.DOTALL)
    matches = pattern.findall(content)
    
    for key, code in matches:
        blocks[key] = code

    return blocks

def render_block(blocks, key):
    """Render a user code block.
    
    Args:
        blocks: Dictionary of extracted blocks.
        key: The key for the block.

    Returns:
        Formatted string containing the user code block.
    """
    content = blocks.get(key, "\n")
    return f"/* USER CODE BEGIN {key} */{content}/* USER CODE END {key} */\n"


def generate_timestamp() -> str:
    """生成本地构建时间字符串。"""
    return datetime.now().astimezone().isoformat(timespec='seconds')


def generate_mcu_header(config, messages, type_mappings, protocol_hash, output_path, user_blocks, generated_at):
    """生成MCU端使用的C语言头文件。

    Args:
        config: 配置字典。
        messages: 消息定义列表。
        type_mappings: 类型映射字典。
        protocol_hash: 协议哈希值。
        output_path: 输出文件路径。
        user_blocks: Extracted user code blocks.
    """
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    with open(output_path, 'w') as f:
        f.write(f"// Generated at: {generated_at}\n")
        f.write("#pragma once\n")
        f.write("#include <stdint.h>\n")
        f.write("#include <stddef.h>\n")
        f.write("\n")
        f.write(render_block(user_blocks, "Includes"))
        f.write("\n")

        checksum_algo = config.get('checksum', 'CRC8').upper()
        require_handshake = config.get('require_handshake', True)
        ignore_version_mismatch = config.get('ignore_version_mismatch', True)
        enable_heartbeat = config.get('enable_heartbeat', True)

        f.write("// 协议哈希校验码\n")
        f.write(f"#define PROTOCOL_HASH 0x{protocol_hash:08X}\n")
        f.write("\n")

        f.write(f"// 校验算法: {checksum_algo}\n")
        f.write(f"#define CHECKSUM_ALGO_{checksum_algo} 1\n")
        f.write("\n")

        f.write(f"// 握手配置\n")
        f.write(f"#define CFG_REQUIRE_HANDSHAKE {1 if require_handshake else 0}\n")
        f.write(f"#define CFG_IGNORE_VERSION_MISMATCH {1 if ignore_version_mismatch else 0}\n")
        f.write("\n")

        strict_heartbeat = config.get('strict_heartbeat', True)
        heartbeat_timeout_ms = config.get('heartbeat_timeout_ms', 3000)
        f.write(f"// 心跳配置\n")
        f.write(f"#define CFG_ENABLE_HEARTBEAT {1 if enable_heartbeat else 0}\n")
        f.write(f"#define CFG_STRICT_HEARTBEAT {1 if strict_heartbeat else 0}\n")
        f.write(f"#define CFG_HEARTBEAT_TIMEOUT_MS {heartbeat_timeout_ms}\n")
        f.write("\n")

        reliable_retry_interval_ms = config.get('reliable_retry_interval_ms', 100)
        reliable_max_retries = config.get('reliable_max_retries', 3)
        f.write(f"// 可靠传输配置\n")
        f.write(f"#define CFG_RELIABLE_RETRY_INTERVAL_MS {reliable_retry_interval_ms}\n")
        f.write(f"#define CFG_RELIABLE_MAX_RETRIES {reliable_max_retries}\n")
        f.write("\n")

        f.write(render_block(user_blocks, "Private_Defines"))
        f.write("\n")

        f.write("// 帧头定义\n")
        f.write(f"#define FRAME_HEADER1 {config['head_byte_1']}\n")
        f.write(f"#define FRAME_HEADER2 {config['head_byte_2']}\n")
        f.write("\n")

        f.write("// 数据包ID定义\n")
        f.write("typedef enum {\n")
        for msg in messages:
            f.write(f"    PACKET_ID_{msg['name'].upper()} = {msg['id']},\n")
        f.write("} PacketID;\n")
        f.write("\n")

        f.write("#pragma pack(1)\n")
        for msg in messages:
            f.write(f"typedef struct {{\n")
            for field in msg['fields']:
                 c_type = get_c_type(field['type'], type_mappings)
                 f.write(f"    {c_type} {field['proto']};\n")
            f.write(f"}} Packet_{msg['name']};\n")
            f.write("\n")
        f.write("#pragma pack()\n")
        f.write("\n")

        f.write("// 协议辅助函数声明\n")
        f.write("uint8_t calculate_checksum(const uint8_t* data, size_t len);\n")
        f.write("void protocol_fsm_feed(uint8_t byte);\n")
        f.write("\n")
        f.write("// 用户可覆盖的接收回调与自动生成的发送函数声明\n")
        for msg in messages:
            f.write(f"void on_receive_{msg['name']}(const Packet_{msg['name']}* pkt);\n")
            f.write(f"void send_{msg['name']}(const Packet_{msg['name']}* pkt);\n")
        f.write("\n")
        
        f.write(render_block(user_blocks, "User_Types"))
        f.write("\n")
        
        if checksum_algo == "CRC8":
            table = generate_crc8_table()
            f.write("// CRC8查找表 (多项式 0x31)\n")
            f.write("static const uint8_t CRC8_TABLE[256] = {\n")
            for i in range(0, 256, 16):
                line = ", ".join(f"0x{x:02X}" for x in table[i:i+16])
                f.write(f"    {line},\n")
            f.write("};\n")


def generate_mcu_source(config, messages, type_mappings, output_path, user_blocks, generated_at):
    """生成MCU端使用的C语言源文件。

    Args:
        config: 全局配置字典。
        messages: 消息定义列表。
        type_mappings: 类型映射字典，用于计算字段字节大小。
        output_path: 输出文件路径。
        user_blocks: 已提取的用户代码块。
        generated_at: 生成时间戳字符串。
    """
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    checksum_algo = config.get('checksum', 'CRC8').upper()
    require_handshake = config.get('require_handshake', True)
    ignore_version_mismatch = config.get('ignore_version_mismatch', True)
    enable_heartbeat = config.get('enable_heartbeat', True)

    with open(output_path, 'w') as f:
        f.write(f"// Generated at: {generated_at}\n")
        f.write("#include \"protocol.h\"\n")
        f.write("#include <string.h>\n\n")
        f.write(render_block(user_blocks, "Includes"))
        f.write("\n")
        
        # --- 1. 计算 MCU 端解析缓冲区大小 ---
        # rx_buffer 仅用于暂存当前正在解析的单个包的 payload，
        # 无需使用 ROS 端的环形缓冲区大小。reliable 包会追加 1 字节 seq。
        max_struct_payload = max(message_payload_size(msg, type_mappings) for msg in messages)
        max_wire_payload = max(message_wire_payload_size(msg, type_mappings) for msg in messages)
        mcu_rx_buf_size = max_wire_payload

        # --- 2. 定义解析器状态机 + 校验函数 ---
        f.write(f"""
// 解析器状态定义
typedef enum {{
    STATE_WAIT_HEADER1,
    STATE_WAIT_HEADER2,
    STATE_WAIT_ID,
    STATE_WAIT_LEN,
    STATE_WAIT_DATA,
    STATE_WAIT_CRC
}} State;

// 单包 payload 解析缓冲区 (最大结构体 payload={max_struct_payload}, 最大 wire payload={max_wire_payload})
static State rx_state = STATE_WAIT_HEADER1;
static uint8_t rx_buffer[{mcu_rx_buf_size}];
static uint16_t rx_cnt = 0;
static uint8_t rx_data_len = 0;
static uint8_t rx_id = 0;
static uint8_t rx_checksum = 0;

""")
        # 根据算法生成校验函数
        if checksum_algo == "CRC8":
            f.write("""// CRC8 校验函数 (查表法, 多项式 0x31)
static uint8_t checksum_update(uint8_t current, uint8_t byte) {
    return CRC8_TABLE[current ^ byte];
}
""")
        elif checksum_algo == "SUM8":
            f.write("""// SUM8 校验函数 (累加和)
static uint8_t checksum_update(uint8_t current, uint8_t byte) {
    return (uint8_t)(current + byte);
}
""")
        elif checksum_algo == "XOR8":
            f.write("""// XOR8 校验函数 (异或)
static uint8_t checksum_update(uint8_t current, uint8_t byte) {
    return current ^ byte;
}
""")
        else:  # NONE
            f.write("""// 无校验
static uint8_t checksum_update(uint8_t current, uint8_t byte) {
    (void)current; (void)byte;
    return 0;
}
""")

        f.write("""
uint8_t calculate_checksum(const uint8_t* data, size_t len) {
    uint8_t cs = 0;
    for (size_t i = 0; i < len; i++) {
        cs = checksum_update(cs, data[i]);
    }
    return cs;
}
""")

        # 可靠传输序列号全局变量与辅助函数
        has_reliable = any(m.get('reliable', False) for m in messages)
        if has_reliable:
            f.write("// 可靠传输序列号：ROS 端在 reliable 包 payload 末尾追加 1 字节 seq，\n")
            f.write("// MCU 收到后存入此变量，ACK 回传时携带该 seq 以区分同 ID 的不同版本。\n")
            f.write("static uint8_t g_last_reliable_seq = 0;\n\n")
            f.write("static inline void send_reliable_ack(PacketID id) {\n")
            f.write("    Packet_Ack ack;\n")
            f.write("    ack.acked_id = (uint8_t)id;\n")
            f.write("    ack.ack_seq = g_last_reliable_seq;\n")
            f.write("    send_Ack(&ack);\n")
            f.write("}\n\n")

        f.write("\n")
        f.write(render_block(user_blocks, "Private_Variables"))
        f.write("\n")

        # --- 2. 生成回调函数原型 ---
        f.write("\n// 用户需要实现的回调函数\n")
        for msg in messages:
            # Generate callback for ALL messages to support loopback/debugging/flexible config
            func_name = f"on_receive_{msg['name']}"
            f.write(f"__attribute__((weak)) void {func_name}(const Packet_{msg['name']}* pkt) {{\n")
            if msg['name'] == 'Handshake' and require_handshake:
                if ignore_version_mismatch:
                    f.write("    // Default system behavior: keep handshake flow even when protocol hash differs.\n")
                    f.write("    send_Handshake(pkt);\n")
                else:
                    f.write("    // Default system behavior: ack matching protocol hash automatically.\n")
                    f.write("    if (pkt->protocol_hash == PROTOCOL_HASH) {\n")
                    f.write("        send_Handshake(pkt);\n")
                    f.write("    }\n")
            if msg['name'] == 'Heartbeat' and enable_heartbeat:
                f.write("    // Default system behavior: ack the latest heartbeat with the same count.\n")
                f.write("    send_Heartbeat(pkt);\n")
            # ACK 已由协议层 FSM 自动发送，回调中无需处理
            f.write(render_block(user_blocks, func_name))
            f.write("}\n")

        
        f.write("\n")
        f.write(render_block(user_blocks, "Code_0"))
        f.write("\n")
        
        # --- 3. 核心状态机函数 ---
        # NONE 算法时跳过校验比较
        if checksum_algo == "NONE":
            crc_check_expr = "1"
        else:
            crc_check_expr = "byte == rx_checksum"

        f.write(f"""
/**
 * @brief 协议解析状态机，在串口中断或轮询中调用此函数处理每个接收到的字节
 * @param byte 接收到的单个字节
 */
void protocol_fsm_feed(uint8_t byte) {{
    switch (rx_state) {{
        case STATE_WAIT_HEADER1:
            if (byte == FRAME_HEADER1) {{
                rx_state = STATE_WAIT_HEADER2;
                rx_checksum = 0; // 校验重置，不包含 Frame Header
            }}
            break;
            
        case STATE_WAIT_HEADER2:
            if (byte == FRAME_HEADER2) {{
                rx_state = STATE_WAIT_ID;
            }} else {{
                rx_state = STATE_WAIT_HEADER1;
            }}
            break;
            
        case STATE_WAIT_ID:
            rx_id = byte;
            rx_checksum = checksum_update(0, rx_id); // 校验包含 ID
            rx_state = STATE_WAIT_LEN;
            break;
            
        case STATE_WAIT_LEN:
            rx_data_len = byte;
            rx_checksum = checksum_update(rx_checksum, rx_data_len); // 校验包含 Len
            rx_cnt = 0;
            if (rx_data_len > 0) {{
                rx_state = STATE_WAIT_DATA;
            }} else {{
                rx_state = STATE_WAIT_CRC;
            }}
            break;
            
        case STATE_WAIT_DATA:
            if (rx_cnt < sizeof(rx_buffer)) {{
                rx_buffer[rx_cnt++] = byte;
                rx_checksum = checksum_update(rx_checksum, byte);
                if (rx_cnt >= rx_data_len) {{
                    rx_state = STATE_WAIT_CRC;
                }}
            }} else {{
                rx_state = STATE_WAIT_HEADER1;
            }}
            break;
            
        case STATE_WAIT_CRC:
            if ({crc_check_expr}) {{
                // 校验通过，分发数据
                switch (rx_id) {{
""")
        # --- 4. 自动生成分发逻辑 ---
        for msg in messages:
            # Generate parsing logic for ALL messages
            f.write(f"                    case PACKET_ID_{msg['name'].upper()}:\n")
            if msg.get('reliable', False):
                # reliable 包: ROS 端会在 payload 末尾追加 1 字节 seq
                # ACK 在协议层自动发送，用户回调无需关心
                f.write(f"                        if (rx_data_len == sizeof(Packet_{msg['name']}) + 1) {{\n")
                f.write(f"                            g_last_reliable_seq = rx_buffer[sizeof(Packet_{msg['name']})];//提取seq\n")
                f.write(f"                            send_reliable_ack(PACKET_ID_{msg['name'].upper()});\n")
                f.write(f"                            on_receive_{msg['name']}((Packet_{msg['name']}*)rx_buffer);\n")
                f.write(f"                        }}\n")
            else:
                f.write(f"                        if (rx_data_len == sizeof(Packet_{msg['name']})) {{\n")
                f.write(f"                            on_receive_{msg['name']}((Packet_{msg['name']}*)rx_buffer);\n")
                f.write(f"                        }}\n")
            f.write(f"                        break;\n")


        f.write("""
                    default:
                        break;
                }
            }
            rx_state = STATE_WAIT_HEADER1;
            break;
            
        default:
            rx_state = STATE_WAIT_HEADER1;
            break;
    }
}
""")

        # --- 5. 生成发送辅助函数 ---
        f.write("\n// --- 发送函数 ---\n")
        f.write("// 外部依赖：用户必须实现 void serial_write(const uint8_t* data, uint16_t len);\n")
        f.write("extern void serial_write(const uint8_t* data, uint16_t len);\n\n")
        
        for msg in messages:
                f.write(f"void send_{msg['name']}(const Packet_{msg['name']}* pkt) {{\n")
                f.write(f"    uint8_t buffer[4 + sizeof(Packet_{msg['name']}) + 1];\n")
                f.write(f"    uint16_t idx = 0;\n")
                f.write(f"    \n")
                f.write(f"    buffer[idx++] = FRAME_HEADER1;\n")
                f.write(f"    buffer[idx++] = FRAME_HEADER2;\n")
                f.write(f"    buffer[idx++] = PACKET_ID_{msg['name'].upper()};\n")
                f.write(f"    buffer[idx++] = sizeof(Packet_{msg['name']});\n")
                f.write(f"    \n")
                f.write(f"    memcpy(&buffer[idx], pkt, sizeof(Packet_{msg['name']}));\n")
                f.write(f"    idx += sizeof(Packet_{msg['name']});\n")
                f.write(f"    \n")
                f.write(f"    buffer[idx] = calculate_checksum(&buffer[2], idx - 2);\n")
                f.write(f"    idx++;\n")
                f.write(f"    \n")
                f.write(f"    serial_write(buffer, idx);\n")
                f.write(f"}}\n")
                
        f.write("\n")
        f.write(render_block(user_blocks, "Code_1"))
        
        # --- 6. 生成建议的消息发送模板 (如 Heartbeat) ---
        f.write("\n/*\n// --- 建议的消息发送模板 (以 Heartbeat 为例) ---\n")
        f.write("// 建议在定时器回调或主循环中以固定频率调用\n\n")
        f.write("void heartbeat_timer_callback(void) {\n")
        f.write("    static uint32_t hb_count = 0;\n")
        f.write("    Packet_Heartbeat pkt;\n")
        f.write("    pkt.count = hb_count++;\n")
        f.write("    send_Heartbeat(&pkt);\n")
        f.write("}\n*/\n")
        f.write("\n")

_ROS_FIELD_SEGMENT_RE = re.compile(r'([A-Za-z_][A-Za-z0-9_]*)(?:\[(\d+)\])?$')


def _merge_vector_requirements(dest, updates):
    for expr, required_size in updates.items():
        dest[expr] = max(dest.get(expr, 0), required_size)


def _vector_label(expr: str) -> str:
    if expr.startswith('msg->'):
        return expr[5:]
    if expr.startswith('msg.'):
        return expr[4:]
    return expr


def _analyze_ros_path(path: str, root_var: str, pointer: bool):
    current_expr = root_var
    access_expr = root_var
    vector_requirements = {}

    for index, segment in enumerate(path.split('.')):
        match = _ROS_FIELD_SEGMENT_RE.fullmatch(segment)
        if not match:
            raise ValueError(f"Unsupported ROS field path segment: {segment!r} in {path!r}")

        name, array_index = match.groups()
        separator = '->' if pointer and index == 0 else '.'
        base_expr = f"{current_expr}{separator}{name}"
        if array_index is not None:
            access_expr = f"{base_expr}[{array_index}]"
            vector_requirements[base_expr] = max(
                vector_requirements.get(base_expr, 0),
                int(array_index) + 1,
            )
        else:
            access_expr = base_expr

        current_expr = access_expr

    return access_expr, vector_requirements


def _needs_int32_multiarray_u8_range_guard(ros_msg: str, field: dict, type_mappings: dict) -> bool:
    return (
        ros_msg == "std_msgs/msg/Int32MultiArray" and
        get_c_type(field['type'], type_mappings) == "uint8_t"
    )


def _write_u8_range_guard(f, msg_name: str, topic: str, field_name: str, read_expr: str) -> None:
    f.write(f"            if ({read_expr} < 0 || {read_expr} > 255) {{\n")
    f.write("                RCLCPP_ERROR_THROTTLE(\n")
    f.write("                    node->get_logger(), *node->get_clock(), 2000,\n")
    f.write(
        f"                    \"Message for {msg_name} on {topic} field {field_name} is out of uint8 range [0, 255]: %d\",\n"
    )
    f.write(f"                    static_cast<int>({read_expr}));\n")
    f.write("                return;\n")
    f.write("            }\n")


def _normalize_debug_log_mode(value) -> str:
    if value is None:
        return "on"
    return str(value).strip().lower()


def _write_guarded_rx_debug_log(f, msg_name: str, fmt_parts, arg_parts) -> None:
    packet_id = f"PACKET_ID_{msg_name.upper()}"
    f.write(f"            if constexpr (config::is_debug_log_enabled({packet_id})) {{\n")
    if fmt_parts:
        fmt_str = ", ".join(fmt_parts)
        args_str = ", ".join(arg_parts)
        f.write(
            f"                RCLCPP_DEBUG(logger, \"RX {msg_name}: {fmt_str}\", {args_str});\n"
        )
    else:
        f.write(
            f"                RCLCPP_DEBUG(logger, \"RX {msg_name}\");\n"
        )
    f.write("            }\n")


def generate_ros_bindings(messages, type_mappings, config, output_path):
    """生成ROS端使用的C++绑定代码。

    包含自动订阅和发布逻辑。

    Args:
        messages: 消息定义列表。
        type_mappings: 类型映射字典。
        config: 全局配置字典。
        output_path: 输出文件路径。
    """
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    qos_depth = config.get('qos_depth', 10)
    
    includes = set()
    for msg in messages:
        includes.add(msg['ros_msg'])

    with open(output_path, 'w') as f:
        f.write("#pragma once\n")
        f.write("#include <cstdint>\n")
        f.write("#include <functional>\n")
        f.write("#include <sstream>\n")
        f.write("#include <string>\n")
        f.write("#include \"auto_serial_bridge/generated/generated_config.hpp\"\n")
        f.write("#include \"auto_serial_bridge/serial_controller.hpp\"\n")
        for inc in includes:
             parts = inc.split('/')
             pkg, sub, typ = parts[0], parts[1], parts[2]
             s1 = re.sub('(.)([A-Z][a-z]+)', r'\1_\2', typ)
             snake_typ = re.sub('([a-z0-9])([A-Z])', r'\1_\2', s1).lower()
             f.write(f"#include <{pkg}/{sub}/{snake_typ}.hpp>\n")
        
        f.write("#include \"auto_serial_bridge/generated/protocol.h\"\n")
        f.write("\n")
        
        f.write("namespace auto_serial_bridge {\n")
        f.write("namespace generated {\n")
        f.write("\n")
        
        f.write("template <typename T> void register_subscriber(SerialController* node, const std::string& topic, PacketID id);\n")
        f.write("\n")
        
        f.write("// MCU TX DECODED helper.\n")
        f.write("inline std::string describe_packet(PacketID id, const std::vector<uint8_t>& payload) {\n")
        f.write("    std::ostringstream out;\n")
        f.write("    switch(id) {\n")
        for msg in messages:
            if msg['direction'] == 'tx' or msg['direction'] == 'both':
                f.write(f"        case PACKET_ID_{msg['name'].upper()}: {{\n")
                f.write(f"            if (payload.size() < sizeof(Packet_{msg['name']})) {{\n")
                f.write(f"                out << \"{msg['name']}: payload_size=\" << payload.size() << \" expected>=\" << sizeof(Packet_{msg['name']});\n")
                f.write("                return out.str();\n")
                f.write("            }\n")
                f.write(f"            const Packet_{msg['name']}* pkt = reinterpret_cast<const Packet_{msg['name']}*>(payload.data());\n")
                field_parts = []
                for field in msg['fields']:
                    field_name = field['proto']
                    field_type = str(field.get('type', '')).lower()
                    if field_type.startswith('f'):
                        field_parts.append(f' << "{field_name}=" << static_cast<double>(pkt->{field_name})')
                    elif field_type.startswith('u'):
                        field_parts.append(f' << "{field_name}=" << static_cast<unsigned int>(pkt->{field_name})')
                    elif field_type.startswith('i'):
                        field_parts.append(f' << "{field_name}=" << static_cast<int>(pkt->{field_name})')
                    else:
                        field_parts.append(f' << "{field_name}=" << static_cast<int>(pkt->{field_name})')
                if field_parts:
                    expr = f'out << "{msg["name"]}: "' + ' << ", "'.join([''] * 0)
                    joined = ''
                    for i, part in enumerate(field_parts):
                        if i > 0:
                            joined += ' << ", "'
                        joined += part
                    f.write(f"            out << \"{msg['name']}: \"{joined};\n")
                else:
                    f.write(f"            out << \"{msg['name']}\";\n")
                if msg.get('reliable', False):
                    f.write(f"            if (payload.size() > sizeof(Packet_{msg['name']})) {{\n")
                    f.write(f"                out << \", ack_seq=\" << static_cast<unsigned int>(payload[sizeof(Packet_{msg['name']})]);\n")
                    f.write("            }\n")
                f.write("            return out.str();\n")
                f.write("        }\n")
        f.write("        default:\n")
        f.write("            out << \"packet_id=0x\" << std::hex << static_cast<unsigned int>(static_cast<uint8_t>(id)) << std::dec << \", payload_size=\" << payload.size();\n")
        f.write("            return out.str();\n")
        f.write("    }\n")
        f.write("}\n\n")

        f.write("inline void register_all(SerialController* node) {\n")
        
        for msg in messages:
            if msg['direction'] == 'tx' or msg['direction'] == 'both':
                
                topic = msg.get('sub_topic')
                if not topic:
                    raise ValueError(f"Message {msg['name']} missing 'sub_topic'")
                
                parts = msg['ros_msg'].split('/')
                ros_type_cpp = f"{parts[0]}::{parts[1]}::{parts[2]}" 
                mirrored_topic = msg['direction'] == 'both' and msg.get('pub_topic') == topic
                
                f.write(f"    // {msg['name']} (ROS -> MCU)\n")
                f.write(f"    node->add_subscription(node->create_subscription<{ros_type_cpp}>(\n")
                f.write(f"        \"{topic}\", {qos_depth},\n")
                if mirrored_topic:
                    f.write(f"        [node](const {ros_type_cpp}::SharedPtr msg, const rclcpp::MessageInfo& msg_info) {{\n")
                    f.write(f"            if (node->should_skip_loopback(PACKET_ID_{msg['name'].upper()}, msg_info)) {{\n")
                    f.write("                return;\n")
                    f.write("            }\n")
                else:
                    f.write(f"        [node](const {ros_type_cpp}::SharedPtr msg) {{\n")

                vector_requirements = {}
                field_reads = []
                for field in msg['fields']:
                    read_expr, requirements = _analyze_ros_path(field['ros'], 'msg', True)
                    _merge_vector_requirements(vector_requirements, requirements)
                    field_reads.append((field, read_expr))

                for expr, required_size in sorted(vector_requirements.items()):
                    label = _vector_label(expr)
                    f.write(f"            if ({expr}.size() < {required_size}) {{\n")
                    f.write("                RCLCPP_ERROR_THROTTLE(\n")
                    f.write("                    node->get_logger(), *node->get_clock(), 2000,\n")
                    f.write(f"                    \"Message for {msg['name']} on {topic} requires at least {required_size} entries in {label}, got %zu\",\n")
                    f.write(f"                    {expr}.size());\n")
                    f.write("                return;\n")
                    f.write("            }\n")

                f.write(f"            Packet_{msg['name']} pkt;\n")
                for field, read_expr in field_reads:
                    c_type = get_c_type(field['type'], type_mappings)
                    if _needs_int32_multiarray_u8_range_guard(msg['ros_msg'], field, type_mappings):
                        _write_u8_range_guard(f, msg['name'], topic, field['proto'], read_expr)
                        f.write(f"            pkt.{field['proto']} = static_cast<{c_type}>({read_expr});\n")
                    else:
                        f.write(f"            pkt.{field['proto']} = {read_expr};\n")
                
                send_api = "reliable_send" if msg.get('reliable', False) else "send_packet"
                f.write(f"            node->{send_api}(PACKET_ID_{msg['name'].upper()}, pkt);\n")
                f.write(f"        }}));\n")
                f.write("\n")

        for msg in messages:
             if msg['direction'] == 'rx' or msg['direction'] == 'both':
                # 注册发布者逻辑 (MCU -> ROS)
                # 具体的发布逻辑在 dispatch_packet 中处理，这里暂不需要预注册除了 map 之外的内容
                pass
        
        f.write("}\n\n")

        # 定义包含所有发布者的结构体
        f.write("struct ProtocolPublishers {\n")
        for msg in messages:
            if msg['direction'] == 'rx' or msg['direction'] == 'both':
                parts = msg['ros_msg'].split('/')
                ros_type_cpp = f"{parts[0]}::{parts[1]}::{parts[2]}"
                f.write(f"    rclcpp::Publisher<{ros_type_cpp}>::SharedPtr pub_{msg['name']};\n")
        
        f.write("\n    void init(SerialController* node) {\n")
        for msg in messages:
            if msg['direction'] == 'rx' or msg['direction'] == 'both':
                parts = msg['ros_msg'].split('/')
                ros_type_cpp = f"{parts[0]}::{parts[1]}::{parts[2]}"
                topic = msg.get('pub_topic')
                if not topic:
                    raise ValueError(f"Message {msg['name']} missing 'pub_topic'")
                f.write(f"        pub_{msg['name']} = node->create_publisher<{ros_type_cpp}>(\"{topic}\", {qos_depth});\n")
                if msg['direction'] == 'both' and msg.get('sub_topic') == topic:
                    f.write(f"        node->register_loopback_publisher(PACKET_ID_{msg['name'].upper()}, pub_{msg['name']});\n")
        f.write("    }\n")
        f.write("};\n\n")

        # 消息分发函数
        f.write("inline void dispatch_packet(ProtocolPublishers& pubs, uint8_t id, const std::vector<uint8_t>& data, const rclcpp::Logger& logger) {\n")
        f.write("    switch(id) {\n")
        for msg in messages:
            if msg['direction'] == 'rx' or msg['direction'] == 'both':
                f.write(f"        case PACKET_ID_{msg['name'].upper()}: {{\n")
                f.write(f"            if (data.size() != sizeof(Packet_{msg['name']})) break;\n")
                f.write(f"            const Packet_{msg['name']}* pkt = reinterpret_cast<const Packet_{msg['name']}*>(data.data());\n")
                
                parts = msg['ros_msg'].split('/')
                ros_type_cpp = f"{parts[0]}::{parts[1]}::{parts[2]}"
                f.write(f"            auto msg = {ros_type_cpp}();\n")
                vector_requirements = {}
                field_writes = []
                for field in msg['fields']:
                     write_expr, requirements = _analyze_ros_path(field['ros'], 'msg', False)
                     _merge_vector_requirements(vector_requirements, requirements)
                     field_writes.append((field, write_expr))
                for expr, required_size in sorted(vector_requirements.items()):
                     f.write(f"            {expr}.resize({required_size});\n")
                for field, write_expr in field_writes:
                     c_type = get_c_type(field['type'], type_mappings)
                     if msg['ros_msg'] == "std_msgs/msg/Int32MultiArray" and c_type == "uint8_t":
                         f.write(f"            {write_expr} = static_cast<int32_t>(pkt->{field['proto']});\n")
                     else:
                         f.write(f"            {write_expr} = pkt->{field['proto']};\n")

                fmt_parts = []
                arg_parts = []
                for field in msg['fields']:
                    field_name = field['proto']
                    field_type = str(field.get('type', '')).lower()
                    if field_type.startswith('f'):
                        fmt_parts.append(f"{field_name}=%.3f")
                        arg_parts.append(f"static_cast<double>(pkt->{field_name})")
                    elif field_type.startswith('u'):
                        fmt_parts.append(f"{field_name}=%u")
                        arg_parts.append(f"static_cast<unsigned int>(pkt->{field_name})")
                    elif field_type.startswith('i'):
                        fmt_parts.append(f"{field_name}=%d")
                        arg_parts.append(f"static_cast<int>(pkt->{field_name})")
                    else:
                        fmt_parts.append(f"{field_name}=%d")
                        arg_parts.append(f"static_cast<int>(pkt->{field_name})")

                _write_guarded_rx_debug_log(f, msg['name'], fmt_parts, arg_parts)
                
                f.write(f"            if (pubs.pub_{msg['name']}) {{\n")
                f.write(f"                pubs.pub_{msg['name']}->publish(msg);\n")
                f.write(f"            }}\n")
                f.write(f"            break;\n")
                f.write(f"        }}\n")
        f.write("    }\n")
        f.write("}\n")

        f.write("}\n") # namespace
        f.write("}\n") # namespace

def generate_cpp_config(config, messages, type_mappings, output_path, serial_params=None):
    """生成C++公共配置头文件。"""
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    checksum_algo = config.get('checksum', 'CRC8').upper()
    require_handshake = config.get('require_handshake', True)
    ignore_version_mismatch = config.get('ignore_version_mismatch', True)
    qos_depth = config.get('qos_depth', 10)
    heartbeat_timeout_ms = config.get('heartbeat_timeout_ms', 3000)
    enable_heartbeat = config.get('enable_heartbeat', True)
    strict_heartbeat = config.get('strict_heartbeat', True)
    reliable_retry_interval_ms = config.get('reliable_retry_interval_ms', 100)
    reliable_max_retries = config.get('reliable_max_retries', 3)
    serial_params = serial_params or {}
    default_port = serial_params.get('port', '/dev/ttyACM0')
    default_baudrate = serial_params.get('baudrate', 115200)

    with open(output_path, 'w') as f:
        f.write("#pragma once\n")
        f.write("#include <cstdint>\n")
        f.write("#include <cstddef>\n\n")
        f.write("#include \"auto_serial_bridge/generated/protocol.h\"\n\n")
        f.write("namespace auto_serial_bridge {\n")
        f.write("namespace config {\n\n")

        f.write(f"    constexpr const char * DEFAULT_PORT = \"{default_port}\";\n")
        f.write(f"    constexpr uint32_t DEFAULT_BAUDRATE = {default_baudrate};\n")
        f.write(f"    constexpr size_t BUFFER_SIZE = {config['buffer_size']};\n")
        f.write(f"    constexpr uint8_t CFG_FRAME_HEADER1 = {config['head_byte_1']};\n")
        f.write(f"    constexpr uint8_t CFG_FRAME_HEADER2 = {config['head_byte_2']};\n\n")

        # TODO: 扩展支持 CRC16/CRC32 (需修改帧格式，校验字段从 1 字节增加到 2/4 字节)
        f.write("    enum class ChecksumAlgo { NONE, SUM8, XOR8, CRC8 };\n")
        f.write(f"    constexpr ChecksumAlgo CHECKSUM_ALGO = ChecksumAlgo::{checksum_algo};\n\n")

        f.write(f"    constexpr bool REQUIRE_HANDSHAKE = {'true' if require_handshake else 'false'};\n")
        f.write(f"    constexpr bool IGNORE_VERSION_MISMATCH = {'true' if ignore_version_mismatch else 'false'};\n")
        f.write(f"    constexpr bool ENABLE_HEARTBEAT = {'true' if enable_heartbeat else 'false'};\n")
        f.write(f"    constexpr bool STRICT_HEARTBEAT = {'true' if strict_heartbeat else 'false'};\n")
        f.write(f"    constexpr size_t QOS_DEPTH = {qos_depth};\n")
        f.write(f"    constexpr int HEARTBEAT_TIMEOUT_MS = {heartbeat_timeout_ms};\n")
        f.write(f"    constexpr int RELIABLE_RETRY_INTERVAL_MS = {reliable_retry_interval_ms};\n")
        f.write(f"    constexpr int RELIABLE_MAX_RETRIES = {reliable_max_retries};\n")
        max_payload_size = max(message_wire_payload_size(msg, type_mappings) for msg in messages)
        f.write(f"    constexpr size_t MAX_PACKET_PAYLOAD_SIZE = {max_payload_size};\n\n")
        f.write("    inline constexpr size_t expected_payload_size(PacketID id) {\n")
        f.write("        switch (id) {\n")
        for msg in messages:
            wire_size = message_wire_payload_size(msg, type_mappings)
            if msg.get('reliable', False):
                f.write(
                    f"            case PACKET_ID_{msg['name'].upper()}: return sizeof(Packet_{msg['name']}) + 1;\n"
                )
            else:
                f.write(f"            case PACKET_ID_{msg['name'].upper()}: return sizeof(Packet_{msg['name']});\n")
        f.write("            default: return 0;\n")
        f.write("        }\n")
        f.write("    }\n")
        f.write("\n")
        f.write("    inline constexpr bool is_reliable_packet(PacketID id) {\n")
        f.write("        switch (id) {\n")
        for msg in messages:
            reliable = 'true' if msg.get('reliable', False) else 'false'
            f.write(f"            case PACKET_ID_{msg['name'].upper()}: return {reliable};\n")
        f.write("            default: return false;\n")
        f.write("        }\n")
        f.write("    }\n")

        # 生成 debug_log_mode 查询函数，供手写代码判断是否输出 debug 日志
        f.write("\n")
        f.write("    inline constexpr bool is_debug_log_enabled(PacketID id) {\n")
        f.write("        switch (id) {\n")
        for msg in messages:
            mode = msg.get('debug_log_mode', 'on')
            enabled = 'false' if mode == 'off' else 'true'
            f.write(f"            case PACKET_ID_{msg['name'].upper()}: return {enabled};\n")
        f.write("            default: return true;\n")
        f.write("        }\n")
        f.write("    }\n")

        f.write("\n}\n")
        f.write("}\n")


# C 类型字节长度映射
_C_TYPE_SIZES = {
    "uint8_t":  1,
    "uint16_t": 2,
    "uint32_t": 4,
    "int32_t":  4,
    "float":    4,
}


def _field_table(
    fields: list,
    type_mappings: dict,
    checksum_algo: str = "CRC8",
    include_reliable_seq: bool = False,
) -> str:
    """渲染字段信息为 Markdown 表格字符串。

    Args:
        fields: 协议字段定义列表。
        type_mappings: YAML 类型到 C 类型的映射。
        checksum_algo: 校验算法名称。
        include_reliable_seq: 是否在数据段末尾展示 reliable 框架附加序列号。

    Returns:
        Markdown 表格字符串，包含字节偏移、字段名、类型、字节数列。
    """
    _CHECKSUM_LABELS = {
        "NONE": "无校验 (占位)",
        "SUM8": "SUM8",
        "XOR8": "XOR8",
        "CRC8": "CRC8",
    }
    lines = [
        "| 字节偏移 | 字段名 | C 类型 | 字节数 |",
        "| :------: | :----- | :----- | :----: |",
    ]
    offset = 0
    for field in fields:
        c_type = type_mappings.get(field['type'], field['type'])
        size = _C_TYPE_SIZES.get(c_type, 1)
        lines.append(f"| {offset} | `{field['proto']}` | `{c_type}` | {size} |")
        offset += size
    if include_reliable_seq:
        lines.append(f"| {offset} | *(reliable seq，框架附加，非业务结构体字段)* | `uint8_t` | 1 |")
        offset += 1
    label = _CHECKSUM_LABELS.get(checksum_algo, checksum_algo)
    lines.append(f"| **{offset}** | *({label})* | `uint8_t` | 1 |")
    return "\n".join(lines)


def generate_mcu_doc(config: dict, messages: list, type_mappings: dict,
                    protocol_hash: int, output_path: str, generated_at: str,
                    serial_params=None) -> None:
    """生成面向电控的 Markdown 通信协议文档。

    文档包含帧格式说明、电控发送/接收的消息列表及字段表格。

    Args:
        config: 全局配置字典（头字节、波特率等）。
        messages: 消息定义列表。
        type_mappings: YAML 类型到 C 类型的映射。
        protocol_hash: 协议哈希，用于握手校验。
        output_path: 输出 Markdown 文件路径。
    """
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    head1 = config['head_byte_1']
    head2 = config['head_byte_2']
    serial_params = serial_params or {}
    baudrate = serial_params.get('baudrate', 115200)
    checksum = config.get('checksum', 'CRC8').upper()
    require_handshake = config.get('require_handshake', True)
    enable_heartbeat = config.get('enable_heartbeat', True)

    _CHECKSUM_DESC = {
        "NONE": "无校验（占位字节 `0x00`）",
        "SUM8": "SUM8 累加和，覆盖 ID + Len + Data",
        "XOR8": "XOR8 异或，覆盖 ID + Len + Data",
        "CRC8": "CRC8，覆盖 ID + Len + Data，多项式 `0x31`",
    }

    # 按方向分组
    rx_msgs  = [m for m in messages if m['direction'] in ('rx', 'both')]   # MCU → ROS
    tx_msgs  = [m for m in messages if m['direction'] in ('tx', 'both')]   # ROS → MCU

    with open(output_path, 'w', encoding='utf-8') as f:
        # ── 文件头 ──
        f.write(f"> 生成时间：{generated_at}\n")
        f.write("# MCU ↔ ROS 串口通信协议文档\n\n")
        f.write("> **Auto-generated** — 由 `scripts/codegen.py` 根据 `config/protocol.yaml` 生成，请勿手动修改。\n\n")
        f.write("---\n\n")

        strict_heartbeat = config.get('strict_heartbeat', True)
        heartbeat_timeout_ms = config.get('heartbeat_timeout_ms', 3000)
        reliable_retry_interval_ms = config.get('reliable_retry_interval_ms', 100)
        reliable_max_retries = config.get('reliable_max_retries', 3)

        # ── 全局参数 ──
        f.write("## 全局参数\n\n")
        f.write(f"| 参数 | 值 |\n")
        f.write(f"| :--- | :--- |\n")
        f.write(f"| 波特率 | `{baudrate}` |\n")
        f.write(f"| 帧头字节 1 | `{head1:#04x}` |\n")
        f.write(f"| 帧头字节 2 | `{head2:#04x}` |\n")
        f.write(f"| 校验算法 | `{checksum}` |\n")
        f.write(f"| 强制握手 | `{'是' if require_handshake else '否'}` |\n")
        f.write(f"| 协议哈希（握手用）| `0x{protocol_hash:08X}` |\n")
        f.write(f"| 严格心跳模式 | `{'是' if strict_heartbeat else '否'}` |\n")
        f.write(f"| 心跳超时时间 | `{heartbeat_timeout_ms} ms` |\n")
        f.write(f"| 可靠传输重试间隔 | `{reliable_retry_interval_ms} ms` |\n")
        f.write(f"| 可靠传输最大重试 | `{reliable_max_retries} 次` |\n")
        f.write("\n---\n\n")

        # ── 帧格式 ──
        f.write("## 帧格式\n\n")
        f.write("每帧结构如下（小端序）：\n\n")
        f.write("| 字节位置 | 字段 | 说明 |\n")
        f.write("| :------: | :--- | :--- |\n")
        f.write(f"| 0 | Header1 | 固定 `{head1:#04x}` |\n")
        f.write(f"| 1 | Header2 | 固定 `{head2:#04x}` |\n")
        f.write("| 2 | ID | 消息 ID，见下表 |\n")
        f.write("| 3 | Len | 数据段字节数 |\n")
        f.write("| 4 … 4+Len-1 | Data | 各字段按结构体内存布局排列 |\n")
        checksum_desc = _CHECKSUM_DESC.get(checksum, checksum)
        f.write(f"| 4+Len | Checksum | {checksum_desc} |\n")
        f.write("\n---\n\n")

        # ── 电控需要发送给 ROS（MCU → ROS） ──
        f.write("## 电控 → ROS（电控主动发送）\n\n")
        if rx_msgs:
            for msg in rx_msgs:
                total_bytes = sum(
                    _C_TYPE_SIZES.get(type_mappings.get(f['type'], f['type']), 1)
                    for f in msg['fields']
                )
                f.write(f"### `{msg['name']}` — ID `{msg['id']:#04x}`\n\n")
                f.write(f"- **ROS 话题**：`{msg.get('pub_topic', 'N/A')}`\n")
                f.write(f"- **ROS 消息类型**：`{msg['ros_msg']}`\n")
                f.write(f"- **数据段字节数（Len）**：`{total_bytes}`\n")
                if msg.get('notes'):
                    f.write(f"- **注意事项**：{msg['notes']}\n")
                if msg['name'] == 'Handshake' and require_handshake:
                    f.write("- **默认生成行为**：`on_receive_Handshake()` 在收到匹配 `PROTOCOL_HASH` 的握手包后会自动调用 `send_Handshake(pkt)` 回包。\n")
                f.write("\n")
                f.write(_field_table(msg['fields'], type_mappings, checksum))
                f.write("\n\n")
        else:
            f.write("_无_\n\n")

        f.write("---\n\n")

        # ── ROS 发送给电控（ROS → MCU） ──
        f.write("## ROS → 电控（电控被动接收）\n\n")
        if tx_msgs:
            for msg in tx_msgs:
                base_bytes = sum(
                    _C_TYPE_SIZES.get(type_mappings.get(f['type'], f['type']), 1)
                    for f in msg['fields']
                )
                reliable = msg.get('reliable', False)
                total_bytes = base_bytes + (1 if reliable else 0)
                f.write(f"### `{msg['name']}` — ID `{msg['id']:#04x}`\n\n")
                f.write(f"- **ROS 话题**：`{msg.get('sub_topic', 'N/A')}`\n")
                f.write(f"- **ROS 消息类型**：`{msg['ros_msg']}`\n")
                f.write(f"- **数据段字节数（Len）**：`{total_bytes}`\n")
                if msg.get('notes'):
                    f.write(f"- **注意事项**：{msg['notes']}\n")
                if msg['name'] == 'Handshake' and require_handshake:
                    f.write("- **默认生成行为**：`on_receive_Handshake()` 在收到匹配 `PROTOCOL_HASH` 的握手包后会自动调用 `send_Handshake(pkt)` 回包。\n")
                if msg['name'] == 'Heartbeat' and enable_heartbeat:
                    f.write("- **默认生成行为**：`on_receive_Heartbeat()` 会自动调用 `send_Heartbeat(pkt)`，按原样回同一个 `count` 作为 ACK。\n")
                if reliable:
                    f.write("- **可靠投递**：该消息启用 ACK/重传。ROS 端会在业务结构体 payload 后透明追加 1 字节 reliable seq；该字节参与 `Len` 和校验，但不属于 `Packet_xxx` 业务结构体字段。生成的分发逻辑会先自动 `send_Ack()`，再调用 `on_receive_xxx()`。\n")
                f.write("\n")
                f.write(_field_table(
                    msg['fields'],
                    type_mappings,
                    checksum,
                    include_reliable_seq=reliable,
                ))
                f.write("\n\n")
        else:
            f.write("_无_\n\n")

        f.write("---\n\n")
        f.write("*文档由构建系统自动生成，版本以协议哈希为准。*\n")

def validate_protocol(config_data):
    """验证协议配置的完整性和合法性。"""
    if not isinstance(config_data, dict):
        raise ValueError("Protocol YAML root must be a mapping.")

    cfg = config_data.get('config', {})
    messages = config_data.get('messages', [])
    type_mappings = config_data.get('type_mappings', {})
    serial_params = config_data.get('serial_controller', {}).get('ros__parameters', {})
    errors = []

    if not isinstance(cfg, dict):
        errors.append("Top-level 'config' must be a mapping.")
        cfg = {}
    if not isinstance(messages, list) or not messages:
        errors.append("Top-level 'messages' must be a non-empty list.")
        messages = []
    if not isinstance(type_mappings, dict) or not type_mappings:
        errors.append("Top-level 'type_mappings' must be a non-empty mapping.")
        type_mappings = {}
    if not isinstance(serial_params, dict):
        errors.append("serial_controller.ros__parameters must be a mapping.")
        serial_params = {}

    if 'baudrate' in cfg:
        errors.append("config.baudrate is not supported; use serial_controller.ros__parameters.baudrate only.")
    baudrate = serial_params.get('baudrate')
    if not isinstance(baudrate, int) or isinstance(baudrate, bool) or baudrate <= 0:
        errors.append("serial_controller.ros__parameters.baudrate must be a positive integer.")
    port = serial_params.get('port')
    if not isinstance(port, str) or not port:
        errors.append("serial_controller.ros__parameters.port must be a non-empty string.")

    identifier_re = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')
    ros_msg_re = re.compile(r'^[a-z][a-z0-9_]*/msg/[A-Z][A-Za-z0-9_]*$')
    topic_re = re.compile(r'^(?!/)(?!.*//)[A-Za-z0-9_~][A-Za-z0-9_~/]*$')
    supported_c_types = set(_C_TYPE_SIZES)

    SUPPORTED_CHECKSUMS = {"NONE", "SUM8", "XOR8", "CRC8"}
    checksum_algo = cfg.get('checksum', 'CRC8').upper()
    if checksum_algo not in SUPPORTED_CHECKSUMS:
        errors.append(f"Unsupported checksum '{cfg.get('checksum')}'. Supported: {', '.join(sorted(SUPPORTED_CHECKSUMS))}")
    cfg['checksum'] = checksum_algo

    for key in ('head_byte_1', 'head_byte_2'):
        value = cfg.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 0xFF:
            errors.append(f"config.{key} must be an integer in [0, 255].")

    buffer_size = cfg.get('buffer_size')
    if not isinstance(buffer_size, int) or isinstance(buffer_size, bool) or buffer_size <= 0:
        errors.append("config.buffer_size must be a positive integer.")

    for key in ('require_handshake', 'ignore_version_mismatch', 'enable_heartbeat', 'strict_heartbeat'):
        if not isinstance(cfg.get(key, True), bool):
            errors.append(f"config.{key} must be boolean.")

    ignore_version_mismatch = cfg.get('ignore_version_mismatch', True)
    if not isinstance(ignore_version_mismatch, bool):
        errors.append("config.ignore_version_mismatch must be boolean.")

    reliable_retry_interval_ms = cfg.get('reliable_retry_interval_ms', 100)
    if not isinstance(reliable_retry_interval_ms, int) or reliable_retry_interval_ms <= 0:
        errors.append("config.reliable_retry_interval_ms must be a positive integer.")

    reliable_max_retries = cfg.get('reliable_max_retries', 3)
    if not isinstance(reliable_max_retries, int) or reliable_max_retries <= 0:
        errors.append("config.reliable_max_retries must be a positive integer.")

    VALID_DIRECTIONS = {"tx", "rx", "both"}
    VALID_DEBUG_LOG_MODES = {"on", "off"}
    seen_ids = {}
    seen_names = set()

    for i, msg in enumerate(messages):
        if not isinstance(msg, dict):
            errors.append(f"messages[{i}] must be a mapping.")
            continue
        label = msg.get('name', f'messages[{i}]')

        for required in ('name', 'id', 'direction', 'ros_msg', 'fields'):
            if required not in msg:
                errors.append(f"Message '{label}' missing required field '{required}'.")

        if msg.get('direction') not in VALID_DIRECTIONS:
            errors.append(f"Message '{label}' has invalid direction '{msg.get('direction')}'. Must be one of: {', '.join(sorted(VALID_DIRECTIONS))}")

        debug_log_mode = _normalize_debug_log_mode(msg.get('debug_log_mode', 'on'))
        if debug_log_mode not in VALID_DEBUG_LOG_MODES:
            errors.append(
                f"Message '{label}' has invalid debug_log_mode '{msg.get('debug_log_mode')}'. Must be one of: {', '.join(sorted(VALID_DEBUG_LOG_MODES))}."
            )
        else:
            msg['debug_log_mode'] = debug_log_mode

        if 'reliable' in msg and not isinstance(msg.get('reliable'), bool):
            errors.append(f"Message '{label}' has invalid reliable='{msg.get('reliable')}'. Must be boolean.")

        if msg.get('reliable', False) and msg.get('direction') not in ('tx', 'both'):
            errors.append(f"Message '{label}' sets reliable=true but direction is '{msg.get('direction')}'. reliable is only allowed for tx/both.")

        mid = msg.get('id')
        if mid is not None:
            if not isinstance(mid, int) or isinstance(mid, bool) or not 0 <= mid <= 0xFF:
                errors.append(f"Message '{label}' id must be an integer in [0, 255].")
            elif mid in seen_ids:
                errors.append(f"Message '{label}' has duplicate ID {mid:#04x} (conflicts with '{seen_ids[mid]}').")
            else:
                seen_ids[mid] = label

        name = msg.get('name')
        if name:
            if not isinstance(name, str) or not identifier_re.fullmatch(name):
                errors.append(f"Message name '{name}' must be a valid C identifier.")
            if name in seen_names:
                errors.append(f"Duplicate message name '{name}'.")
            seen_names.add(name)

        ros_msg = msg.get('ros_msg')
        if not isinstance(ros_msg, str) or not ros_msg_re.fullmatch(ros_msg):
            errors.append(f"Message '{label}' has invalid ros_msg '{ros_msg}'. Expected package/msg/Type.")

        direction = msg.get('direction', '')
        if direction in ('tx', 'both') and 'sub_topic' not in msg:
            errors.append(f"Message '{label}' (direction={direction}) missing 'sub_topic'.")
        if direction in ('rx', 'both') and 'pub_topic' not in msg:
            errors.append(f"Message '{label}' (direction={direction}) missing 'pub_topic'.")

        for topic_key in ('sub_topic', 'pub_topic'):
            if topic_key not in msg:
                continue
            topic = msg.get(topic_key)
            if not isinstance(topic, str) or not topic_re.fullmatch(topic):
                errors.append(
                    f"Message '{label}' has invalid {topic_key} '{topic}'. Use a non-empty relative ROS topic."
                )

        fields = msg.get('fields', [])
        if not isinstance(fields, list):
            errors.append(f"Message '{label}' fields must be a list.")
            fields = []
        seen_fields = set()
        for field in fields:
            if not isinstance(field, dict):
                errors.append(f"Message '{label}' contains a field that is not a mapping.")
                continue
            field_name = field.get('proto')
            if not isinstance(field_name, str) or not identifier_re.fullmatch(field_name):
                errors.append(f"Message '{label}' field name '{field_name}' must be a valid C identifier.")
            elif field_name in seen_fields:
                errors.append(f"Message '{label}' has duplicate field name '{field_name}'.")
            else:
                seen_fields.add(field_name)
            ftype = field.get('type', '')
            c_type = get_c_type(ftype, type_mappings)
            if c_type not in supported_c_types:
                errors.append(f"Message '{label}', field '{field.get('proto', '?')}': unknown type '{ftype}'.")
            if not isinstance(field.get('ros'), str) or not field.get('ros'):
                errors.append(f"Message '{label}', field '{field.get('proto', '?')}' missing non-empty ros path.")

        if fields:
            wire_size = message_wire_payload_size(msg, type_mappings)
            if wire_size > 0xFF:
                errors.append(f"Message '{label}' wire payload is {wire_size} bytes; maximum is 255.")

    expected_system_messages = {
        'Ack': {
            'id': 0xFD,
            'direction': 'both',
            'fields': [('acked_id', 'uint8_t'), ('ack_seq', 'uint8_t')],
        },
        'Heartbeat': {
            'id': 0xFE,
            'direction': 'both',
            'fields': [('count', 'uint32_t')],
        },
        'Handshake': {
            'id': 0xFF,
            'direction': 'both',
            'fields': [('protocol_hash', 'uint32_t')],
        },
    }
    messages_by_name = {
        message.get('name'): message
        for message in messages
        if isinstance(message, dict) and isinstance(message.get('name'), str)
    }
    for name, expected in expected_system_messages.items():
        message = messages_by_name.get(name)
        if message is None:
            errors.append(f"Required system message '{name}' is missing.")
            continue
        if message.get('id') != expected['id']:
            errors.append(f"System message '{name}' must use ID 0x{expected['id']:02X}.")
        if message.get('direction') != expected['direction']:
            errors.append(f"System message '{name}' must use direction 'both'.")
        actual_fields = [
            (field.get('proto'), get_c_type(field.get('type'), type_mappings))
            for field in message.get('fields', [])
            if isinstance(field, dict)
        ]
        if actual_fields != expected['fields']:
            errors.append(
                f"System message '{name}' fields must be exactly {expected['fields']} in this order."
            )

    for reserved_id, system_name in ((0xFD, 'Ack'), (0xFE, 'Heartbeat'), (0xFF, 'Handshake')):
        owner = seen_ids.get(reserved_id)
        if owner is not None and owner != system_name:
            errors.append(f"ID 0x{reserved_id:02X} is reserved for system message '{system_name}'.")

    if isinstance(buffer_size, int) and buffer_size > 0 and messages:
        largest_frame = max(message_wire_payload_size(msg, type_mappings) + 5 for msg in messages)
        if buffer_size < largest_frame:
            errors.append(
                f"config.buffer_size={buffer_size} cannot hold the largest complete frame ({largest_frame} bytes)."
            )

    if errors:
        print("Protocol validation failed:")
        for e in errors:
            print(f"  - {e}")
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="Generate Auto Serial Bridge protocol sources.")
    parser.add_argument('protocol_yaml')
    parser.add_argument('output_dir', nargs='?')
    parser.add_argument(
        '--print-ros-packages',
        action='store_true',
        help='validate YAML and print referenced ROS message packages, one per line',
    )
    args = parser.parse_args()
    if not args.print_ros_packages and not args.output_dir:
        parser.error('output_dir is required unless --print-ros-packages is used')
    yaml_file = args.protocol_yaml
    output_dir = args.output_dir
    
    with open(yaml_file, 'r') as f:
        content = f.read()
        config_data = yaml.safe_load(content)

    validate_protocol(config_data)

    if args.print_ros_packages:
        print('\n'.join(ros_message_packages(config_data)))
        return
        
    phash = calculate_protocol_hash(config_data)
    generated_at = generate_timestamp()
    
    generated_dir = os.path.join(output_dir, 'generated')
    mcu_header_path = os.path.join(generated_dir, 'protocol.h')
    header_user_blocks = extract_user_code(mcu_header_path)
    
    generate_mcu_header(config_data['config'], config_data['messages'], config_data['type_mappings'], phash, 
                        mcu_header_path, header_user_blocks, generated_at)
    
    mcu_source_path = os.path.join(generated_dir, 'protocol.c')
    source_user_blocks = extract_user_code(mcu_source_path)
    
    generate_mcu_source(config_data['config'], config_data['messages'], config_data['type_mappings'],
                        mcu_source_path, source_user_blocks, generated_at)
                        
    generate_cpp_config(config_data['config'], config_data['messages'], config_data['type_mappings'],
                        os.path.join(generated_dir, 'generated_config.hpp'),
                        config_data.get('serial_controller', {}).get('ros__parameters', {}))
                        
    generate_ros_bindings(config_data['messages'], config_data['type_mappings'],
                          config_data['config'],
                          os.path.join(generated_dir, 'generated_bindings.hpp'))

    generate_mcu_doc(config_data['config'], config_data['messages'], config_data['type_mappings'],
                     phash, os.path.join(generated_dir, 'PROTOCOL_DOC.md'), generated_at,
                     config_data.get('serial_controller', {}).get('ros__parameters', {}))

if __name__ == "__main__":
    main()
