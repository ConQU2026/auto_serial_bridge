
import os
import sys
import hashlib
import time
import unittest
import pytest
import launch
import launch_ros.actions
import launch_testing.actions
from launch.actions import ExecuteProcess, TimerAction
from launch_ros.actions import ComposableNodeContainer
from launch_ros.descriptions import ComposableNode
from ament_index_python.packages import get_package_share_directory
import rclpy
from rclpy.node import Node
import serial
import struct
import yaml
from importlib import import_module

# 动态加载协议配置
def load_protocol_config():
    try:
        package_name = 'auto_serial_bridge'
        from ament_index_python.packages import get_package_share_directory
        share_dir = get_package_share_directory(package_name)
        config_path = os.path.join(share_dir, 'config', 'protocol.yaml')
        if not os.path.exists(config_path):
            config_path = os.path.join(share_dir, 'config', 'protocol-sample.yaml')
            
        with open(config_path, 'r') as f:
            content = f.read()
            config = yaml.safe_load(content)
            
        return config, content
    except Exception as e:
        print(f"Error loading protocol config: {e}")
        return None, None

PROTOCOL_CONFIG, PROTOCOL_YAML_CONTENT = load_protocol_config()

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
    if len(parts) >= 3:
        module_name = f"{parts[0]}.{parts[1]}"
        class_name = parts[2]
        return getattr(import_module(module_name), class_name)
    return None

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
    return formats.get(type_name, 'B')

def generate_dummy_payload(msg_config):
    fmt = '<'
    values = []
    for field in msg_config.get('fields', []):
        fmt += get_struct_format_for_type(field['type'])
        values.append(0)
    return struct.pack(fmt, *values)

# 协议常量 (动态获取)
HEAD1 = get_config_value('head_byte_1', 0x5A)
HEAD2 = get_config_value('head_byte_2', 0xA5)
ID_HANDSHAKE = get_message_id('Handshake')

if ID_HANDSHAKE is None: ID_HANDSHAKE = 0x00

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
    package_name = 'auto_serial_bridge'
    
    my_pkg_share = get_package_share_directory(package_name)
    
    common_config = os.path.join(my_pkg_share, 'config', 'protocol.yaml') # Update path if needed, but node param usually separate.
    # 实际上之前使用了参数 'serial_data.yaml'
    # 新的 serial_controller 使用 `config/protocol.yaml` 进行代码生成，但运行时的参数 `baudrate` 等是标准参数。
    
    socat_cmd = ['socat', '-d', '-d', 'PTY,link=/tmp/vtty0,raw,echo=0', 'PTY,link=/tmp/vtty1,raw,echo=0']
    socat_process = ExecuteProcess(
        cmd=socat_cmd,
        output='screen'
    )

    serial_component = ComposableNode(
        package=package_name,
        plugin='auto_serial_bridge::SerialController',
        name='auto_serial_bridge_node',
        parameters=[{'port': '/tmp/vtty1', 'baudrate': 921600}],
        extra_arguments=[{'use_intra_process_comms': True}],
    )

    container = ComposableNodeContainer(
        name='my_container',
        namespace='',
        package='rclcpp_components',
        executable='component_container',
        arguments=["--ros-args", "--log-level", "info"],
        composable_node_descriptions=[serial_component],
        output='screen',
    )

    return launch.LaunchDescription([
        socat_process,
        TimerAction(period=2.0, actions=[container]),
        launch_testing.actions.ReadyToTest(),
    ])

class TestSerialController(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        rclpy.init()

    @classmethod
    def tearDownClass(cls):
        rclpy.shutdown()

    def setUp(self):
        self.node = rclpy.create_node('test_serial_controller_client')
        while(rclpy.ok()):
            try:
                self.serial_port = serial.Serial('/tmp/vtty0', baudrate=921600, timeout=1)
                break
            except serial.SerialException:
                time.sleep(0.1)

    def tearDown(self):
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

    def wait_for_handshake(self):
        # 等待握手请求 (ID 0)
        start_time = time.time()
        buf = b''
        while time.time() - start_time < 5.0:
            if self.serial_port.in_waiting:
                buf += self.serial_port.read(self.serial_port.in_waiting)
            
            # 简单的握手解析器
            # 5A A5 00 04 [HASH 4 bytes] [CRC]
            if len(buf) >= 9: # 2+1+1+4+1 = 9
                idx = buf.find(struct.pack('<BB', HEAD1, HEAD2))
                if idx != -1 and len(buf) >= idx + 9:
                    if buf[idx+2] == ID_HANDSHAKE:
                         # 发现握手请求
                         # 发送握手响应
                         hash_bytes = struct.pack('<I', PROTOCOL_HASH)
                         resp = self.pack_packet(ID_HANDSHAKE, hash_bytes)
                         self.serial_port.write(resp)
                         self.serial_port.flush()
                         return True
                    else:
                        buf = buf[idx+1:]
                else:
                    if idx == -1: buf = b''
            time.sleep(0.01)
        return False

    def test_communication(self):
        # 1. 执行握手
        self.assertTrue(self.wait_for_handshake(), "Handshake failed")
        
        # 找到一个 rx_msg 和 tx_msg
        rx_msg = None
        tx_msg = None
        for msg in PROTOCOL_CONFIG.get('messages', []):
            if msg.get('direction') in ['rx', 'both'] and not rx_msg:
                rx_msg = msg
            if msg.get('direction') in ['tx', 'both'] and not tx_msg:
                tx_msg = msg
        
        # 2. 测试从串口接收 (Serial -> ROS)
        if rx_msg:
            received_msgs = []
            ros_msg_class = get_ros_msg_class(rx_msg['ros_msg'])
            
            sub_hb = self.node.create_subscription(
                ros_msg_class,
                rx_msg['pub_topic'],
                lambda msg: received_msgs.append(msg),
                10
            )
            
            payload = generate_dummy_payload(rx_msg)
            packet = self.pack_packet(rx_msg['id'], payload)
            
            end_time = time.time() + 5.0
            found = False
            while time.time() < end_time:
                self.serial_port.write(packet)
                self.serial_port.flush()
                
                spin_end = time.time() + 0.5
                while time.time() < spin_end:
                     rclpy.spin_once(self.node, timeout_sec=0.1)
                     if len(received_msgs) > 0:
                         found = True
                         break
                if found: break
            
            self.assertTrue(found, f"Did not receive {rx_msg['name']} from ROS")

        # 3. 测试发送到串口 (ROS -> Serial)
        if tx_msg:
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
                
                if len(buf) >= 6:
                    idx = buf.find(struct.pack('<BB', HEAD1, HEAD2))
                    if idx != -1 and len(buf) >= idx + 4:
                        if buf[idx+2] == tx_msg['id']:
                            found_packet = True
                            break
                        else:
                            buf = buf[idx+1:]
                    else:
                        if idx == -1: buf = b''
                time.sleep(0.05)
                
            self.assertTrue(found_packet, f"未在串口上收到 {tx_msg['name']} 数据")
