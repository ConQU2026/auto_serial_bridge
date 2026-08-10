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
    if PROTOCOL_CONFIG.get("config", {}).get("enable_heartbeat", True):
        pytest.skip(
            "Heartbeat-disabled PTY coverage requires protocol.yaml with enable_heartbeat=false.",
            allow_module_level=True,
        )

    return create_test_description()


class TestSerialControllerHeartbeatDisabled(SerialControllerPtyTestCase):
    def test_no_heartbeat_packets_when_disabled(self):
        self.assertTrue(
            self.complete_handshake_only(),
            f"未能完成握手进入 RUNNING; seen packet ids={self._seen_packet_ids}",
        )
        self.assert_no_packet_id(get_message_id("Heartbeat"), timeout_sec=2.2)

    def test_no_disconnect_when_heartbeat_disabled(self):
        self.assertTrue(
            self.complete_handshake_only(),
            f"未能完成握手进入 RUNNING; seen packet ids={self._seen_packet_ids}",
        )
        self.assert_no_packet_id(get_message_id("Handshake"), timeout_sec=4.2)
