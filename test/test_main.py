import unittest
import time

import pytest
import struct

from pty_test_utils import (
    ID_HANDSHAKE,
    PROTOCOL_CONFIG,
    PYTESTMARK_SKIP_IF_NO_PTY,
    SerialControllerPtyTestCase,
    create_test_description,
    get_message_id,
)

pytestmark = PYTESTMARK_SKIP_IF_NO_PTY

@pytest.mark.launch_test
def generate_test_description():
    return create_test_description()

class TestSerialController(SerialControllerPtyTestCase):
    def test_communication(self):
        self.assertIsNotNone(
            PROTOCOL_CONFIG,
            "Protocol configuration is not loaded."
        )

        excluded_system_msgs = {'Heartbeat', 'Handshake'}

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

        deadline = time.time() + 8.0
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
