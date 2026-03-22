import pytest

from pty_test_utils import (
    PROTOCOL_CONFIG,
    PYTESTMARK_SKIP_IF_NO_PTY,
    SerialControllerPtyTestCase,
    create_test_description,
    get_message_id,
)


pytestmark = PYTESTMARK_SKIP_IF_NO_PTY
if PROTOCOL_CONFIG.get("config", {}).get("enable_heartbeat", True):
    pytestmark = [
        PYTESTMARK_SKIP_IF_NO_PTY,
        pytest.mark.skip(reason="Heartbeat-disabled PTY coverage now requires a protocol.yaml built with enable_heartbeat=false."),
    ]


@pytest.mark.launch_test
def generate_test_description():
    return create_test_description()


class TestSerialControllerHeartbeatDisabled(SerialControllerPtyTestCase):
    def test_no_heartbeat_packets_when_disabled(self):
        self.assertTrue(
            self.complete_handshake_only(),
            f"未能完成握手进入 RUNNING; seen packet ids={self._seen_packet_ids}",
        )
        self.assert_no_packet_id(get_message_id("Heartbeat"), timeout_sec=2.2)

    def test_business_packets_still_flow_when_heartbeat_disabled(self):
        self.assertTrue(
            self.complete_handshake_only(),
            f"未能完成握手进入 RUNNING; seen packet ids={self._seen_packet_ids}",
        )

        excluded_system_msgs = {"Heartbeat", "Handshake"}
        tx_msg = None
        for msg in PROTOCOL_CONFIG.get("messages", []):
            if msg.get("name") not in excluded_system_msgs and msg.get("direction") in ["tx", "both"]:
                tx_msg = msg
                break

        self.assertIsNotNone(
            tx_msg,
            "No non-system message with direction 'tx' or 'both' found in protocol config.",
        )
        self.assertTrue(
            self.publish_message_until_seen(tx_msg, timeout_sec=4.0, service_heartbeat=False),
            f"未在关闭心跳时收到 {tx_msg['name']} 数据; seen packet ids={self._seen_packet_ids}",
        )

    def test_no_disconnect_when_heartbeat_disabled(self):
        self.assertTrue(
            self.complete_handshake_only(),
            f"未能完成握手进入 RUNNING; seen packet ids={self._seen_packet_ids}",
        )
        self.assert_no_packet_id(get_message_id("Handshake"), timeout_sec=4.2)
