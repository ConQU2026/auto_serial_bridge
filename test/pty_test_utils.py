import os
import re
import struct
import subprocess
import threading
import time
import unittest

import launch
import launch_testing.actions
import pytest
import rclpy
import serial
import yaml
from launch.actions import ExecuteProcess, TimerAction
from launch_ros.actions import Node as LaunchNode
from ament_index_python.packages import get_package_share_directory


RUN_PTY_INTEGRATION = os.getenv("AUTO_SERIAL_BRIDGE_RUN_PTY_INTEGRATION") == "1"
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

PYTESTMARK_SKIP_IF_NO_PTY = pytest.mark.skipif(
    not RUN_PTY_INTEGRATION,
    reason=(
        "PTY launch/integration test is opt-in. "
        "Default CI runs deterministic build and unit-level regressions; "
        "set AUTO_SERIAL_BRIDGE_RUN_PTY_INTEGRATION=1 to exercise the serial end-to-end path."
    ),
)


def load_protocol_config():
    local_config_path = os.path.join(REPO_ROOT, "config", "protocol.yaml")
    try:
        if os.path.exists(local_config_path):
            config_path = local_config_path
        else:
            share_dir = get_package_share_directory("auto_serial_bridge")
            config_path = os.path.join(share_dir, "config", "protocol.yaml")
        with open(config_path, "r", encoding="utf-8") as file_obj:
            content = file_obj.read()
        return yaml.safe_load(content), content
    except Exception as exc:
        print(f"Error loading protocol config: {exc}")
        return None, None


def load_generated_protocol_macros():
    try:
        share_dir = get_package_share_directory("auto_serial_bridge")
        protocol_header_path = os.path.join(share_dir, "mcu_output", "protocol.h")
    except Exception:
        protocol_header_path = ""
    macros = {}
    if not os.path.exists(protocol_header_path):
        return macros

    define_pattern = re.compile(r"^#define\s+(\w+)\s+(.+?)\s*$")
    with open(protocol_header_path, "r", encoding="utf-8") as file_obj:
        for line in file_obj:
            match = define_pattern.match(line.strip())
            if match:
                macros[match.group(1)] = match.group(2)
    return macros


if RUN_PTY_INTEGRATION:
    PROTOCOL_CONFIG, PROTOCOL_YAML_CONTENT = load_protocol_config()
else:
    PROTOCOL_CONFIG, PROTOCOL_YAML_CONTENT = {}, ""
GENERATED_PROTOCOL_MACROS = load_generated_protocol_macros()

if RUN_PTY_INTEGRATION and PROTOCOL_CONFIG is None:
    raise AssertionError(
        "Failed to load protocol config for tests. "
        "Ensure config/protocol.yaml exists and is valid YAML."
    )


def get_config_value(key, default):
    if PROTOCOL_CONFIG and "config" in PROTOCOL_CONFIG:
        return PROTOCOL_CONFIG["config"].get(key, default)
    return default


def get_runtime_protocol_value(key, default, env_name=None):
    if env_name:
        env_value = os.getenv(env_name)
        if env_value is not None:
            return env_value
    return get_config_value(key, default)


def get_message_id(name):
    if PROTOCOL_CONFIG and "messages" in PROTOCOL_CONFIG:
        for message in PROTOCOL_CONFIG["messages"]:
            if message["name"] == name:
                return message["id"]
    return None


HEAD1 = int(get_runtime_protocol_value("head_byte_1", 0x5A, "AUTO_SERIAL_BRIDGE_HEAD1"))
HEAD2 = int(get_runtime_protocol_value("head_byte_2", 0xA5, "AUTO_SERIAL_BRIDGE_HEAD2"))
ID_HANDSHAKE = get_message_id("Handshake")
if RUN_PTY_INTEGRATION and ID_HANDSHAKE is None:
    raise AssertionError(
        "Handshake message ID not found in protocol config. "
        "Ensure a 'Handshake' message is defined in protocol YAML."
    )


def get_protocol_hash():
    if "PROTOCOL_HASH" in GENERATED_PROTOCOL_MACROS:
        return int(GENERATED_PROTOCOL_MACROS["PROTOCOL_HASH"], 0)
    if PROTOCOL_YAML_CONTENT:
        result = subprocess.run(
            ["python3", os.path.join(REPO_ROOT, "scripts", "codegen.py"),
             os.path.join(REPO_ROOT, "config", "protocol.yaml"),
             "/tmp/auto_serial_bridge_pty_codegen"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            header_path = "/tmp/auto_serial_bridge_pty_codegen/generated/protocol.h"
            with open(header_path, "r", encoding="utf-8") as file_obj:
                for line in file_obj:
                    if line.startswith("#define PROTOCOL_HASH "):
                        return int(line.split()[-1], 0)
    return 0


PROTOCOL_HASH = get_protocol_hash()


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


def calculate_checksum(data):
    algo = str(get_runtime_protocol_value("checksum", "CRC8", "AUTO_SERIAL_BRIDGE_CHECKSUM_ALGO")).upper()
    if algo == "NONE":
        return 0
    if algo == "SUM8":
        checksum = 0
        for byte in data:
            checksum = (checksum + byte) & 0xFF
        return checksum
    if algo == "XOR8":
        checksum = 0
        for byte in data:
            checksum ^= byte
        return checksum
    if algo == "CRC8":
        crc = 0
        for byte in data:
            crc = CRC8_TABLE[crc ^ byte]
        return crc
    raise ValueError(f"Unsupported checksum algorithm in protocol config: {algo}")


def create_test_description(node_parameters=None):
    if not RUN_PTY_INTEGRATION:
        pytest.skip(
            "PTY launch/integration test is opt-in. "
            "Set AUTO_SERIAL_BRIDGE_RUN_PTY_INTEGRATION=1 to run it.",
            allow_module_level=True,
        )

    if node_parameters is None:
        node_parameters = {}

    for link_path in ("/tmp/vtty0", "/tmp/vtty1"):
        try:
            os.remove(link_path)
        except FileNotFoundError:
            pass

    socat_process = ExecuteProcess(
        cmd=[
            "socat",
            "-d",
            "-d",
            "PTY,link=/tmp/vtty0,raw,echo=0",
            "PTY,link=/tmp/vtty1,raw,echo=0",
        ],
        output="screen",
    )
    serial_node = LaunchNode(
        package="auto_serial_bridge",
        executable="serial_node",
        name="serial_controller",
        output="screen",
        parameters=[{"port": "/tmp/vtty1", "baudrate": 921600, **node_parameters}],
    )
    return launch.LaunchDescription(
        [
            socat_process,
            TimerAction(period=2.0, actions=[serial_node]),
            launch_testing.actions.ReadyToTest(),
        ]
    )


@unittest.skipUnless(
    RUN_PTY_INTEGRATION,
    "PTY launch/integration test is opt-in; set AUTO_SERIAL_BRIDGE_RUN_PTY_INTEGRATION=1 to run it.",
)
class SerialControllerPtyTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        rclpy.init()
        cls._handshake_completed = False

    @classmethod
    def tearDownClass(cls):
        rclpy.shutdown()

    def setUp(self):
        self._serial_write_lock = threading.Lock()
        self._rx_buf = b""
        self._seen_packet_ids = []
        self.node = rclpy.create_node("test_serial_controller_client")
        while rclpy.ok():
            try:
                self.serial_port = serial.Serial("/tmp/vtty0", baudrate=921600, timeout=1)
                self.serial_port.reset_input_buffer()
                self.serial_port.reset_output_buffer()
                break
            except serial.SerialException:
                time.sleep(0.1)

    def tearDown(self):
        self.node.destroy_node()
        if hasattr(self, "serial_port") and self.serial_port.is_open:
            self.serial_port.close()

    def pack_packet(self, packet_id, data_bytes):
        length = len(data_bytes)
        packet = struct.pack("<BBBB", HEAD1, HEAD2, packet_id, length)
        packet += data_bytes
        payload_for_checksum = struct.pack("<BB", packet_id, length) + data_bytes
        checksum = calculate_checksum(payload_for_checksum)
        packet += struct.pack("<B", checksum)
        return packet

    def write_serial_packet(self, packet):
        with self._serial_write_lock:
            self.serial_port.write(packet)
            self.serial_port.flush()

    def read_next_packet(self, timeout_sec=2.0):
        deadline = time.time() + timeout_sec
        while time.time() < deadline:
            if self.serial_port.in_waiting:
                self._rx_buf += self.serial_port.read(self.serial_port.in_waiting)

            idx = self._rx_buf.find(struct.pack("<BB", HEAD1, HEAD2))
            if idx == -1:
                if len(self._rx_buf) > 1:
                    self._rx_buf = self._rx_buf[-1:]
                time.sleep(0.01)
                continue

            if len(self._rx_buf) < idx + 4:
                time.sleep(0.01)
                continue

            packet_id = self._rx_buf[idx + 2]
            payload_len = self._rx_buf[idx + 3]
            frame_len = 2 + 1 + 1 + payload_len + 1
            if len(self._rx_buf) < idx + frame_len:
                time.sleep(0.01)
                continue

            payload = self._rx_buf[idx + 4 : idx + 4 + payload_len]
            self._rx_buf = self._rx_buf[idx + frame_len :]
            return packet_id, payload

        return None, None

    def ensure_running_state(self, timeout_sec=5.0):
        heartbeat_id = get_message_id("Heartbeat")
        self.assertIsNotNone(heartbeat_id, "Heartbeat message ID not found in protocol config.")

        start_time = time.time()
        while time.time() - start_time < timeout_sec:
            packet_id, payload = self.read_next_packet(timeout_sec=0.1)
            if packet_id is None:
                continue
            if packet_id == ID_HANDSHAKE:
                self.assertEqual(len(payload), 4, "Handshake payload should be uint32 protocol hash")
                self.write_serial_packet(self.pack_packet(ID_HANDSHAKE, payload))
                continue
            if packet_id == heartbeat_id:
                self.assertEqual(len(payload), 4, "Heartbeat payload should be uint32.")
                self.write_serial_packet(self.pack_packet(packet_id, payload))
                return struct.unpack("<I", payload)[0]

        self.fail("Did not reach RUNNING state with a confirmed heartbeat in time")

    def complete_handshake_only(self, timeout_sec=6.0, quiet_after_ack_sec=1.2):
        if self.__class__._handshake_completed:
            return True

        start_time = time.time()
        saw_handshake = False
        last_ack_at = None

        while time.time() - start_time < timeout_sec:
            packet_id, payload = self.read_next_packet(timeout_sec=0.1)
            now = time.time()

            if packet_id is None:
                if saw_handshake and last_ack_at is not None and now - last_ack_at >= quiet_after_ack_sec:
                    self.__class__._handshake_completed = True
                    return True
                continue

            self._seen_packet_ids.append(packet_id)

            if packet_id != ID_HANDSHAKE:
                if saw_handshake:
                    self.__class__._handshake_completed = True
                    return True
                continue

            self.assertEqual(len(payload), 4, "Handshake payload should be uint32 protocol hash")
            self.write_serial_packet(self.pack_packet(ID_HANDSHAKE, payload))
            saw_handshake = True
            last_ack_at = time.time()

        return False

    def assert_no_packet_id(self, forbidden_packet_id, timeout_sec):
        deadline = time.time() + timeout_sec
        while time.time() < deadline:
            packet_id, _payload = self.read_next_packet(timeout_sec=0.1)
            if packet_id is None:
                continue
            self._seen_packet_ids.append(packet_id)
            self.assertNotEqual(
                packet_id,
                forbidden_packet_id,
                f"Unexpected packet id {forbidden_packet_id} seen while expecting silence; "
                f"seen packet ids={self._seen_packet_ids}",
            )
