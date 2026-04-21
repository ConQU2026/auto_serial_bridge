import unittest
import time

import pytest
import rclpy
import struct
from std_msgs.msg import Int32MultiArray

from pty_test_utils import (
    ID_HANDSHAKE,
    PROTOCOL_CONFIG,
    PROTOCOL_HASH,
    PYTESTMARK_SKIP_IF_NO_PTY,
    SerialControllerPtyTestCase,
    create_test_description,
    get_config_value,
    get_message_id,
)

pytestmark = PYTESTMARK_SKIP_IF_NO_PTY

@pytest.mark.launch_test
def generate_test_description():
    return create_test_description()

class TestSerialController(SerialControllerPtyTestCase):
    def test_00_mismatched_handshake_is_accepted_when_ignore_enabled(self):
        if not get_config_value("ignore_version_mismatch", True):
            self.skipTest("ignore_version_mismatch=false 时不应接受 mismatch 握手")

        heartbeat_id = get_message_id("Heartbeat")
        self.assertIsNotNone(heartbeat_id)

        wrong_hash = 0xA5A5A5A5
        if wrong_hash == PROTOCOL_HASH:
            wrong_hash = 0x5A5A5A5A

        replied_with_wrong_hash = False
        deadline = time.time() + 6.0
        while time.time() < deadline:
            packet_id, payload = self.read_next_packet(timeout_sec=0.2)
            if packet_id is None:
                continue
            if packet_id == ID_HANDSHAKE:
                self.assertEqual(len(payload), 4, "Handshake payload should be uint32 protocol hash")
                self.write_serial_packet(
                    self.pack_packet(ID_HANDSHAKE, struct.pack("<I", wrong_hash))
                )
                replied_with_wrong_hash = True
                continue
            if packet_id == heartbeat_id:
                self.assertTrue(
                    replied_with_wrong_hash,
                    "收到 Heartbeat 前应先完成一次 mismatch 握手回包",
                )
                self.assertEqual(len(payload), 4, "Heartbeat payload should be uint32.")
                self.write_serial_packet(self.pack_packet(packet_id, payload))
                return

        self.fail("ignore_version_mismatch=true 时，mismatch 握手后仍未进入 RUNNING")

    def test_communication(self):
        self.assertIsNotNone(
            PROTOCOL_CONFIG,
            "Protocol configuration is not loaded."
        )

        excluded_system_msgs = {'Ack', 'Heartbeat', 'Handshake'}

        # 1. 执行握手
        self.ensure_running_state()
        
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
        self.serial_port.reset_input_buffer()
        self._rx_buf = b''

        self.assertTrue(
            self.publish_message_until_seen(tx_msg, timeout_sec=4.0, service_heartbeat=True),
            f"未在串口上收到 {tx_msg['name']} 数据; seen packet ids={self._seen_packet_ids}"
        )

    def test_stale_heartbeat_ack_triggers_disconnect(self):
        first_count = self.ensure_running_state()
        heartbeat_timeout_ms = get_config_value("heartbeat_timeout_ms", 3000)

        deadline = time.time() + max((heartbeat_timeout_ms / 1000.0) + 4.0, 8.0)
        while time.time() < deadline:
            packet_id, payload = self.read_next_packet(timeout_sec=0.2)
            if packet_id is None:
                continue
            if packet_id == get_message_id('Heartbeat'):
                self.assertEqual(len(payload), 4)
                self.write_serial_packet(
                    self.pack_packet(packet_id, struct.pack('<I', first_count))
                )
                continue
            if packet_id == ID_HANDSHAKE:
                self.assertEqual(len(payload), 4, "Handshake payload should be uint32 protocol hash")
                return

        self.fail("Expected node to restart handshake after repeated stale heartbeat acknowledgements")

    def test_delayed_heartbeat_ack_one_period_later_keeps_connection(self):
        self.ensure_running_state()
        heartbeat_id = get_message_id('Heartbeat')
        self.assertIsNotNone(heartbeat_id)

        delayed_payload = None
        deadline = time.time() + 4.0
        while time.time() < deadline:
            packet_id, payload = self.read_next_packet(timeout_sec=0.2)
            if packet_id is None:
                continue
            if packet_id == heartbeat_id:
                self.assertEqual(len(payload), 4)
                delayed_payload = payload
                break
            if packet_id == ID_HANDSHAKE:
                self.fail("Unexpected handshake before delayed heartbeat acknowledgement")

        self.assertIsNotNone(delayed_payload, "Did not receive a heartbeat to delay")

        time.sleep(1.2)
        self.write_serial_packet(self.pack_packet(heartbeat_id, delayed_payload))

        observe_deadline = time.time() + 2.5
        while time.time() < observe_deadline:
            packet_id, payload = self.read_next_packet(timeout_sec=0.2)
            if packet_id is None:
                continue
            if packet_id == ID_HANDSHAKE:
                self.fail("Node restarted handshake after a one-period delayed ACK")
            if packet_id == heartbeat_id:
                self.assertEqual(len(payload), 4)
                self.write_serial_packet(self.pack_packet(packet_id, payload))

    def test_generic_status_round_trip_uses_int32_multi_array(self):
        self.ensure_running_state()
        heartbeat_id = get_message_id('Heartbeat')
        generic_status_tx_id = get_message_id('GenericStatusTx')
        generic_status_rx_id = get_message_id('GenericStatusRx')

        self.assertIsNotNone(generic_status_tx_id)
        self.assertIsNotNone(generic_status_rx_id)

        publisher = self.node.create_publisher(Int32MultiArray, '/task/generic_status_tx', 10)
        received_msgs = []
        self.node.create_subscription(
            Int32MultiArray,
            '/task/generic_status_rx',
            lambda msg: received_msgs.append(list(msg.data)),
            10,
        )

        tx_msg = Int32MultiArray()
        tx_msg.data = [7, 8, 9]

        tx_seen = False
        deadline = time.time() + 4.0
        while time.time() < deadline and not tx_seen:
            publisher.publish(tx_msg)
            rclpy.spin_once(self.node, timeout_sec=0.05)
            packet_id, payload = self.read_next_packet(timeout_sec=0.2)
            if packet_id is None:
                continue
            self._seen_packet_ids.append(packet_id)
            if packet_id == heartbeat_id:
                self.assertEqual(len(payload), 4)
                self.write_serial_packet(self.pack_packet(packet_id, payload))
                continue
            if packet_id == generic_status_tx_id:
                self.assertEqual(list(payload[:3]), [7, 8, 9])
                self.assertEqual(len(payload), 4)
                tx_seen = True

        self.assertTrue(tx_seen, "Did not observe GenericStatusTx payload on serial")

        self.write_serial_packet(
            self.pack_packet(generic_status_rx_id, bytes([10, 11, 12]))
        )

        rx_deadline = time.time() + 2.0
        while time.time() < rx_deadline and not received_msgs:
            rclpy.spin_once(self.node, timeout_sec=0.1)
            packet_id, payload = self.read_next_packet(timeout_sec=0.05)
            if packet_id == heartbeat_id:
                self.assertEqual(len(payload), 4)
                self.write_serial_packet(self.pack_packet(packet_id, payload))

        self.assertTrue(received_msgs, "Did not receive GenericStatusRx on ROS topic")
        self.assertEqual(received_msgs[0], [10, 11, 12])
