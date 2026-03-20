
import os
import sys
import hashlib
import time
import threading
import unittest
import pytest
import launch
import launch_ros.actions
import launch_testing.actions
from launch.actions import ExecuteProcess, TimerAction
from launch_ros.actions import Node as LaunchNode
from ament_index_python.packages import get_package_share_directory
import rclpy
from rclpy.node import Node
import serial
import struct
import yaml
from importlib import import_module

RUN_PTY_INTEGRATION = os.getenv("AUTO_SERIAL_BRIDGE_RUN_PTY_INTEGRATION") == "1"

pytestmark = pytest.mark.skipif(
    not RUN_PTY_INTEGRATION,
    reason=(
        "PTY launch/integration test is opt-in. "
        "Default CI runs deterministic build and unit-level regressions; "
        "set AUTO_SERIAL_BRIDGE_RUN_PTY_INTEGRATION=1 to exercise the serial end-to-end path."
    ),
)

# 动态加载协议配置
def load_protocol_config():
    try:
        package_name = 'auto_serial_bridge'
        from ament_index_python.packages import get_package_share_directory
        share_dir = get_package_share_directory(package_name)
        config_path = os.path.join(share_dir, 'config', 'protocol.yaml')

        with open(config_path, 'r') as f:
            content = f.read()
            config = yaml.safe_load(content)
            
        return config, content
    except Exception as e:
        print(f"Error loading protocol config: {e}")
        return None, None

if RUN_PTY_INTEGRATION:
    PROTOCOL_CONFIG, PROTOCOL_YAML_CONTENT = load_protocol_config()
else:
    PROTOCOL_CONFIG, PROTOCOL_YAML_CONTENT = {}, ""

if RUN_PTY_INTEGRATION and PROTOCOL_CONFIG is None:
    raise AssertionError(
        "Failed to load protocol config for tests. "
        "Ensure config/protocol.yaml exists and is valid YAML."
    )

def get_config_value(key, default):
    if PROTOCOL_CONFIG and 'config' in PROTOCOL_CONFIG:
        return PROTOCOL_CONFIG['config'].get(key, default)
    return default

def get_message_id(name):
    if PROTOCOL_CONFIG and 'messages' in PROTOCOL_CONFIG:
        for msg in PROTOCOL_CONFIG['messages']:
            if msg['name'] == name:
                return msg['id']
    return None

def get_ros_msg_class(ros_msg_type_str):
    parts = ros_msg_type_str.split('/')
    if len(parts) < 3:
        raise ValueError(f"Invalid ROS message type string: {ros_msg_type_str!r}")

    module_name = f"{parts[0]}.{parts[1]}"
    class_name = parts[2]
    try:
        module = import_module(module_name)
    except (ImportError, ModuleNotFoundError) as exc:
        raise ValueError(
            f"Could not import ROS message module {module_name!r} "
            f"for type {ros_msg_type_str!r}"
        ) from exc

    try:
        return getattr(module, class_name)
    except AttributeError as exc:
        raise ValueError(
            f"ROS message type {ros_msg_type_str!r} "
            f"not found in module {module_name!r}"
        ) from exc

def get_struct_format_for_type(type_name):
    formats = {
        'uint8': 'B', 'uint8_t': 'B', 'u8': 'B',
        'uint16': 'H', 'uint16_t': 'H', 'u16': 'H',
        'uint32': 'I', 'uint32_t': 'I', 'u32': 'I',
        'int8': 'b', 'int8_t': 'b', 'i8': 'b',
        'int16': 'h', 'int16_t': 'h', 'i16': 'h',
        'int32': 'i', 'int32_t': 'i', 'i32': 'i',
        'float': 'f', 'float32': 'f', 'f32': 'f',
        'double': 'd', 'float64': 'd', 'f64': 'd'
    }
    if type_name not in formats:
        raise ValueError(
            f"Unknown field type '{type_name}' in protocol configuration. "
            f"Supported types are: {', '.join(sorted(formats.keys()))}"
        )
    return formats[type_name]

def generate_dummy_payload(msg_config):
    fmt = '<'
    values = []
    fields = msg_config.get('fields', [])
    if not fields:
        return b''
    for field in fields:
        fmt += get_struct_format_for_type(field['type'])
        values.append(0)
    return struct.pack(fmt, *values)

# 协议常量 (动态获取)
HEAD1 = get_config_value('head_byte_1', 0x5A)
HEAD2 = get_config_value('head_byte_2', 0xA5)
ID_HANDSHAKE = get_message_id('Handshake')
if RUN_PTY_INTEGRATION and ID_HANDSHAKE is None:
    raise AssertionError(
        "Handshake message ID not found in protocol config. "
        "Ensure a 'Handshake' message is defined in protocol YAML."
    )

def get_protocol_hash():
    if PROTOCOL_YAML_CONTENT:
        # 必须匹配 codegen.py 的逻辑
        return int(hashlib.md5(PROTOCOL_YAML_CONTENT.encode('utf-8')).hexdigest()[:8], 16)
    return 0

PROTOCOL_HASH = get_protocol_hash()


# CRC8 生成逻辑 (避免硬编码表)
def generate_crc8_table(polynomial=0x31):
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

CRC8_TABLE = generate_crc8_table()
# CRC8_TABLE = [
#    0x00, 0x31, 0x62, ... (Dynamic now)
# ]


def calculate_checksum(data):
    crc = 0
    for byte in data:
        crc = CRC8_TABLE[crc ^ byte]
    return crc

@pytest.mark.launch_test
def generate_test_description():
    if not RUN_PTY_INTEGRATION:
        pytest.skip(
            "PTY launch/integration test is opt-in. "
            "Set AUTO_SERIAL_BRIDGE_RUN_PTY_INTEGRATION=1 to run it.",
            allow_module_level=True,
        )

    package_name = 'auto_serial_bridge'

    socat_cmd = ['socat', '-d', '-d', 'PTY,link=/tmp/vtty0,raw,echo=0', 'PTY,link=/tmp/vtty1,raw,echo=0']
    socat_process = ExecuteProcess(
        cmd=socat_cmd,
        output='screen'
    )

    serial_node = LaunchNode(
        package=package_name,
        executable='serial_node',
        name='serial_controller',
        output='screen',
        parameters=[{'port': '/tmp/vtty1', 'baudrate': 921600}],
    )

    return launch.LaunchDescription([
        socat_process,
        TimerAction(period=2.0, actions=[serial_node]),
        launch_testing.actions.ReadyToTest(),
    ])

@unittest.skipUnless(
    RUN_PTY_INTEGRATION,
    "PTY launch/integration test is opt-in; set AUTO_SERIAL_BRIDGE_RUN_PTY_INTEGRATION=1 to run it.",
)
class TestSerialController(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        rclpy.init()

    @classmethod
    def tearDownClass(cls):
        rclpy.shutdown()

    def setUp(self):
        self.node = rclpy.create_node('test_serial_controller_client')
        self._heartbeat_stop = threading.Event()
        self._heartbeat_thread = None
        self._serial_write_lock = threading.Lock()
        while(rclpy.ok()):
            try:
                self.serial_port = serial.Serial('/tmp/vtty0', baudrate=921600, timeout=1)
                break
            except serial.SerialException:
                time.sleep(0.1)

    def tearDown(self):
        self.stop_mcu_heartbeat()
        self.node.destroy_node()
        if hasattr(self, 'serial_port') and self.serial_port.is_open:
            self.serial_port.close()

    def pack_packet(self, packet_id, data_bytes):
        # Header(2) + ID(1) + Len(1) + Data(N) + CRC(1)
        length = len(data_bytes)
        
        packet = struct.pack('<BBBB', HEAD1, HEAD2, packet_id, length)
        packet += data_bytes
        
        # 校验和覆盖 ID, Len, Data
        payload_for_checksum = struct.pack('<BB', packet_id, length) + data_bytes
        checksum = calculate_checksum(payload_for_checksum)
        
        packet += struct.pack('<B', checksum)
        return packet

    def write_serial_packet(self, packet):
        with self._serial_write_lock:
            self.serial_port.write(packet)
            self.serial_port.flush()

    def wait_for_handshake(self):
        # 等待握手请求 (ID 使用配置里的 Handshake ID)
        start_time = time.time()
        buf = b''
        while time.time() - start_time < 5.0:
            if self.serial_port.in_waiting:
                buf += self.serial_port.read(self.serial_port.in_waiting)
            
            # 简单的握手解析器
            # [HEAD1][HEAD2][ID_HANDSHAKE][LEN=4][HASH 4 bytes][CRC]
            if len(buf) >= 9: # 2+1+1+4+1 = 9
                idx = buf.find(struct.pack('<BB', HEAD1, HEAD2))
                if idx != -1 and len(buf) >= idx + 9:
                    if buf[idx+2] == ID_HANDSHAKE:
                         # 发现握手请求
                         # 发送握手响应
                         hash_bytes = struct.pack('<I', PROTOCOL_HASH)
                         resp = self.pack_packet(ID_HANDSHAKE, hash_bytes)
                         self.write_serial_packet(resp)
                         return True
                    else:
                        buf = buf[idx+1:]
                else:
                    if idx == -1: buf = b''
            time.sleep(0.01)
        return False

    def start_mcu_heartbeat(self, period_sec=0.5):
        heartbeat_id = get_message_id('Heartbeat')
        self.assertIsNotNone(heartbeat_id, "Heartbeat message ID not found in protocol config.")

        def heartbeat_worker():
            count = 0
            while not self._heartbeat_stop.is_set():
                payload = struct.pack('<I', count)
                packet = self.pack_packet(heartbeat_id, payload)
                try:
                    self.write_serial_packet(packet)
                except serial.SerialException:
                    break
                count += 1
                if self._heartbeat_stop.wait(period_sec):
                    break

        self._heartbeat_stop.clear()
        self._heartbeat_thread = threading.Thread(target=heartbeat_worker, daemon=True)
        self._heartbeat_thread.start()

    def stop_mcu_heartbeat(self):
        self._heartbeat_stop.set()
        if self._heartbeat_thread is not None:
            self._heartbeat_thread.join(timeout=1.0)
            self._heartbeat_thread = None

    def test_communication(self):
        self.assertIsNotNone(
            PROTOCOL_CONFIG,
            "Protocol configuration is not loaded."
        )

        excluded_system_msgs = {'Heartbeat', 'Handshake'}

        # 1. 执行握手
        self.assertTrue(self.wait_for_handshake(), "Handshake failed")
        self.start_mcu_heartbeat()
        
        # 找到一个 tx_msg
        tx_msg = None
        for msg in PROTOCOL_CONFIG.get('messages', []):
            if (
                msg.get('name') not in excluded_system_msgs and
                msg.get('direction') in ['tx', 'both'] and
                not tx_msg
            ):
                tx_msg = msg

        self.assertIsNotNone(
            tx_msg,
            "No non-system message with direction 'tx' or 'both' found in protocol config."
        )

        # 2. 测试发送到串口 (ROS -> Serial)
        ros_msg_class = get_ros_msg_class(tx_msg['ros_msg'])
        pub = self.node.create_publisher(ros_msg_class, tx_msg['sub_topic'], 10)
        msg_instance = ros_msg_class()

        self.serial_port.reset_input_buffer()

        for _ in range(10):
            pub.publish(msg_instance)
            time.sleep(0.1)
            rclpy.spin_once(self.node, timeout_sec=0.1)

        start_time = time.time()
        found_packet = False
        buf = b''
        while time.time() - start_time < 2.0:
            if self.serial_port.in_waiting:
                buf += self.serial_port.read(self.serial_port.in_waiting)

            idx = buf.find(struct.pack('<BB', HEAD1, HEAD2))
            if idx != -1:
                # Need at least header(2) + id(1) + len(1)
                if len(buf) >= idx + 4:
                    msg_id = buf[idx + 2]
                    payload_len = buf[idx + 3]
                    frame_len = 2 + 1 + 1 + payload_len + 1
                    if len(buf) >= idx + frame_len:
                        if msg_id == tx_msg['id']:
                            found_packet = True
                            break
                        buf = buf[idx + 1:]
            else:
                buf = b''
            time.sleep(0.05)

        self.assertTrue(found_packet, f"未在串口上收到 {tx_msg['name']} 数据")
