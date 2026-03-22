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

def calculate_protocol_hash(yaml_content):
    """计算协议哈希值。

    基于YAML内容计算MD5哈希，用于校验MCU和ROS端的协议一致性。

    Args:
        yaml_content: 协议文件的原始内容字符串。

    Returns:
        32位整数哈希值。
    """
    return int(hashlib.md5(yaml_content.encode('utf-8')).hexdigest()[:8], 16)

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
        f.write("\n")
        f.write(render_block(user_blocks, "Includes"))
        f.write("\n")

        checksum_algo = config.get('checksum', 'CRC8').upper()
        require_handshake = config.get('require_handshake', True)
        enable_heartbeat = config.get('enable_heartbeat', True)

        f.write("// 协议哈希校验码\n")
        f.write(f"#define PROTOCOL_HASH 0x{protocol_hash:08X}\n")
        f.write("\n")

        f.write(f"// 校验算法: {checksum_algo}\n")
        f.write(f"#define CHECKSUM_ALGO_{checksum_algo} 1\n")
        f.write("\n")

        f.write(f"// 握手配置\n")
        f.write(f"#define CFG_REQUIRE_HANDSHAKE {1 if require_handshake else 0}\n")
        f.write("\n")

        f.write(f"// 心跳配置\n")
        f.write(f"#define CFG_ENABLE_HEARTBEAT {1 if enable_heartbeat else 0}\n")
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
        f.write("uint8_t calculate_checksum(const uint8_t* data, uint8_t len);\n")
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


def generate_mcu_source(config, messages, output_path, user_blocks, generated_at):
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    buffer_size = config.get('buffer_size', 256)
    checksum_algo = config.get('checksum', 'CRC8').upper()
    require_handshake = config.get('require_handshake', True)
    enable_heartbeat = config.get('enable_heartbeat', True)

    with open(output_path, 'w') as f:
        f.write(f"// Generated at: {generated_at}\n")
        f.write("#include \"protocol.h\"\n")
        f.write("#include <string.h>\n\n")
        f.write(render_block(user_blocks, "Includes"))
        f.write("\n")
        
        # --- 1. 定义解析器状态机 + 校验函数 ---
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

static State rx_state = STATE_WAIT_HEADER1;
static uint8_t rx_buffer[{buffer_size}]; // 定义的最大包长
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
uint8_t calculate_checksum(const uint8_t* data, uint8_t len) {
    uint8_t cs = 0;
    for (uint8_t i = 0; i < len; i++) {
        cs = checksum_update(cs, data[i]);
    }
    return cs;
}
""")
        
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
                f.write("    // Default system behavior: ack matching protocol hash automatically.\n")
                f.write("    if (pkt->protocol_hash == PROTOCOL_HASH) {\n")
                f.write("        send_Handshake(pkt);\n")
                f.write("    }\n")
            if msg['name'] == 'Heartbeat' and enable_heartbeat:
                f.write("    // Default system behavior: ack the latest heartbeat with the same count.\n")
                f.write("    send_Heartbeat(pkt);\n")
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
        f.write("#include <functional>\n")
        f.write("#include \"auto_serial_bridge/serial_controller.hpp\"\n")
        for inc in includes:
             parts = inc.split('/')
             pkg, sub, typ = parts[0], parts[1], parts[2]
             s1 = re.sub('(.)([A-Z][a-z]+)', r'\1_\2', typ)
             snake_typ = re.sub('([a-z0-9])([A-Z])', r'\1_\2', s1).lower()
             f.write(f"#include <{pkg}/{sub}/{snake_typ}.hpp>\n")
        
        f.write("#include \"protocol.h\"\n")
        f.write("\n")
        
        f.write("namespace auto_serial_bridge {\n")
        f.write("namespace generated {\n")
        f.write("\n")
        
        f.write("template <typename T> void register_subscriber(SerialController* node, const std::string& topic, PacketID id);\n")
        f.write("\n")
        
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
                    f.write(f"            pkt.{field['proto']} = {read_expr};\n")
                
                f.write(f"            node->send_packet(PACKET_ID_{msg['name'].upper()}, pkt);\n")
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
        f.write("inline void dispatch_packet(ProtocolPublishers& pubs, uint8_t id, const std::vector<uint8_t>& data) {\n")
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
                     f.write(f"            {write_expr} = pkt->{field['proto']};\n")
                
                f.write(f"            if (pubs.pub_{msg['name']}) {{\n")
                f.write(f"                pubs.pub_{msg['name']}->publish(msg);\n")
                f.write(f"            }}\n")
                f.write(f"            break;\n")
                f.write(f"        }}\n")
        f.write("    }\n")
        f.write("}\n")

        f.write("}\n") # namespace
        f.write("}\n") # namespace

def generate_cpp_config(config, messages, type_mappings, output_path):
    """生成C++公共配置头文件。"""
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    checksum_algo = config.get('checksum', 'CRC8').upper()
    require_handshake = config.get('require_handshake', True)
    qos_depth = config.get('qos_depth', 10)
    heartbeat_timeout_ms = config.get('heartbeat_timeout_ms', 3000)
    enable_heartbeat = config.get('enable_heartbeat', True)

    with open(output_path, 'w') as f:
        f.write("#pragma once\n")
        f.write("#include <cstdint>\n")
        f.write("#include <cstddef>\n\n")
        f.write("#include \"protocol.h\"\n\n")
        f.write("namespace auto_serial_bridge {\n")
        f.write("namespace config {\n\n")

        f.write(f"    constexpr uint32_t DEFAULT_BAUDRATE = {config['baudrate']};\n")
        f.write(f"    constexpr size_t BUFFER_SIZE = {config['buffer_size']};\n")
        f.write(f"    constexpr uint8_t CFG_FRAME_HEADER1 = {config['head_byte_1']};\n")
        f.write(f"    constexpr uint8_t CFG_FRAME_HEADER2 = {config['head_byte_2']};\n\n")

        # TODO: 扩展支持 CRC16/CRC32 (需修改帧格式，校验字段从 1 字节增加到 2/4 字节)
        f.write("    enum class ChecksumAlgo { NONE, SUM8, XOR8, CRC8 };\n")
        f.write(f"    constexpr ChecksumAlgo CHECKSUM_ALGO = ChecksumAlgo::{checksum_algo};\n\n")

        f.write(f"    constexpr bool REQUIRE_HANDSHAKE = {'true' if require_handshake else 'false'};\n")
        f.write(f"    constexpr bool ENABLE_HEARTBEAT = {'true' if enable_heartbeat else 'false'};\n")
        f.write(f"    constexpr size_t QOS_DEPTH = {qos_depth};\n")
        f.write(f"    constexpr int HEARTBEAT_TIMEOUT_MS = {heartbeat_timeout_ms};\n")
        max_payload_size = max(
            sum(_C_TYPE_SIZES.get(get_c_type(field['type'], type_mappings), 1) for field in msg['fields'])
            for msg in messages
        )
        f.write(f"    constexpr size_t MAX_PACKET_PAYLOAD_SIZE = {max_payload_size};\n\n")
        f.write("    inline constexpr size_t expected_payload_size(PacketID id) {\n")
        f.write("        switch (id) {\n")
        for msg in messages:
            f.write(f"            case PACKET_ID_{msg['name'].upper()}: return sizeof(Packet_{msg['name']});\n")
        f.write("            default: return 0;\n")
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


def _field_table(fields: list, type_mappings: dict, checksum_algo: str = "CRC8") -> str:
    """渲染字段信息为 Markdown 表格字符串。

    Args:
        fields: 协议字段定义列表。
        type_mappings: YAML 类型到 C 类型的映射。
        checksum_algo: 校验算法名称。

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
    label = _CHECKSUM_LABELS.get(checksum_algo, checksum_algo)
    lines.append(f"| **{offset}** | *({label})* | `uint8_t` | 1 |")
    return "\n".join(lines)


def generate_mcu_doc(config: dict, messages: list, type_mappings: dict,
                    protocol_hash: int, output_path: str, generated_at: str) -> None:
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
    baudrate = config.get('baudrate', 115200)
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
                total_bytes = sum(
                    _C_TYPE_SIZES.get(type_mappings.get(f['type'], f['type']), 1)
                    for f in msg['fields']
                )
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
                f.write("\n")
                f.write(_field_table(msg['fields'], type_mappings, checksum))
                f.write("\n\n")
        else:
            f.write("_无_\n\n")

        f.write("---\n\n")
        f.write("*文档由构建系统自动生成，版本以协议哈希为准。*\n")

def validate_protocol(config_data):
    """验证协议配置的完整性和合法性。"""
    cfg = config_data.get('config', {})
    messages = config_data.get('messages', [])
    type_mappings = config_data.get('type_mappings', {})
    errors = []

    SUPPORTED_CHECKSUMS = {"NONE", "SUM8", "XOR8", "CRC8"}
    checksum_algo = cfg.get('checksum', 'CRC8').upper()
    if checksum_algo not in SUPPORTED_CHECKSUMS:
        errors.append(f"Unsupported checksum '{cfg.get('checksum')}'. Supported: {', '.join(sorted(SUPPORTED_CHECKSUMS))}")
    cfg['checksum'] = checksum_algo

    if 'head_byte_1' not in cfg or 'head_byte_2' not in cfg:
        errors.append("Protocol must define 'head_byte_1' and 'head_byte_2'.")

    VALID_DIRECTIONS = {"tx", "rx", "both"}
    seen_ids = {}
    seen_names = set()

    for i, msg in enumerate(messages):
        label = msg.get('name', f'messages[{i}]')

        for required in ('name', 'id', 'direction', 'ros_msg', 'fields'):
            if required not in msg:
                errors.append(f"Message '{label}' missing required field '{required}'.")

        if msg.get('direction') not in VALID_DIRECTIONS:
            errors.append(f"Message '{label}' has invalid direction '{msg.get('direction')}'. Must be one of: {', '.join(sorted(VALID_DIRECTIONS))}")

        mid = msg.get('id')
        if mid is not None:
            if mid in seen_ids:
                errors.append(f"Message '{label}' has duplicate ID {mid:#04x} (conflicts with '{seen_ids[mid]}').")
            else:
                seen_ids[mid] = label

        name = msg.get('name')
        if name:
            if name in seen_names:
                errors.append(f"Duplicate message name '{name}'.")
            seen_names.add(name)

        direction = msg.get('direction', '')
        if direction in ('tx', 'both') and 'sub_topic' not in msg:
            errors.append(f"Message '{label}' (direction={direction}) missing 'sub_topic'.")
        if direction in ('rx', 'both') and 'pub_topic' not in msg:
            errors.append(f"Message '{label}' (direction={direction}) missing 'pub_topic'.")

        for field in msg.get('fields', []):
            ftype = field.get('type', '')
            if ftype not in type_mappings and ftype not in type_mappings.values():
                errors.append(f"Message '{label}', field '{field.get('proto', '?')}': unknown type '{ftype}'.")

    if errors:
        print("Protocol validation failed:")
        for e in errors:
            print(f"  - {e}")
        sys.exit(1)


def main():
    if len(sys.argv) < 3:
        print("Usage: codegen.py <protocol_yaml> <output_dir>")
        sys.exit(1)
        
    yaml_file = sys.argv[1]
    output_dir = sys.argv[2] 
    
    with open(yaml_file, 'r') as f:
        content = f.read()
        config_data = yaml.safe_load(content)

    validate_protocol(config_data)
        
    phash = calculate_protocol_hash(content)
    generated_at = generate_timestamp()
    
    mcu_header_path = os.path.join(output_dir, 'mcu_output', 'protocol.h')
    header_user_blocks = extract_user_code(mcu_header_path)
    
    generate_mcu_header(config_data['config'], config_data['messages'], config_data['type_mappings'], phash, 
                        mcu_header_path, header_user_blocks, generated_at)
    
    mcu_source_path = os.path.join(output_dir, 'mcu_output', 'protocol.c')
    source_user_blocks = extract_user_code(mcu_source_path)
    
    generate_mcu_source(config_data['config'], config_data['messages'], 
                        mcu_source_path, source_user_blocks, generated_at)
                        
    generate_cpp_config(config_data['config'], config_data['messages'], config_data['type_mappings'],
                        os.path.join(output_dir, 'include', 'auto_serial_bridge', 'generated_config.hpp'))
                        
    generate_ros_bindings(config_data['messages'], config_data['type_mappings'],
                          config_data['config'],
                          os.path.join(output_dir, 'include', 'auto_serial_bridge', 'generated_bindings.hpp'))

    generate_mcu_doc(config_data['config'], config_data['messages'], config_data['type_mappings'],
                     phash, os.path.join(output_dir, 'mcu_output', 'PROTOCOL_DOC.md'), generated_at)

if __name__ == "__main__":
    main()
