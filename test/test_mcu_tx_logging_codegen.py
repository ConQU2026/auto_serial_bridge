import subprocess
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CODEGEN_SCRIPT = REPO_ROOT / "scripts" / "codegen.py"
CONFIG = REPO_ROOT / "config" / "protocol.yaml"


def test_generated_bindings_include_decoded_mcu_tx_logging():
    with tempfile.TemporaryDirectory() as tmpdir:
        result = subprocess.run(
            ["python3", str(CODEGEN_SCRIPT), str(CONFIG), tmpdir],
            text=True,
            capture_output=True,
        )
        assert result.returncode == 0, result.stderr

        bindings = Path(tmpdir, "generated/generated_bindings.hpp").read_text()

    assert "MCU TX DECODED" in bindings
    assert "describe_packet" in bindings
    assert "case PACKET_ID_DEMOCOMMAND" in bindings
    assert "DemoCommand:" in bindings


def test_serial_controller_sources_include_raw_mcu_tx_logging():
    header = (REPO_ROOT / "include/auto_serial_bridge/serial_controller.hpp").read_text()
    source = (REPO_ROOT / "src/serial_controller.cpp").read_text()

    assert "log_mcu_tx" in header
    assert "MCU TX RAW" in source
    assert "describe_protocol_packet" in source
    assert "should_log_mcu_tx_raw" in source
    assert "RCLCPP_DEBUG" in source


def test_serial_controller_decoded_mcu_tx_logging_is_debug_only_and_respects_debug_log_mode():
    source = (REPO_ROOT / "src/serial_controller.cpp").read_text()

    start = source.index("void SerialController::log_mcu_tx")
    end = source.index("bool SerialController::async_send_impl", start)
    log_fn = source[start:end]

    assert "const bool debug_log_enabled = should_log_protocol_debug(id);" in log_fn
    assert "if (!debug_log_enabled)" in log_fn
    assert "[MCU TX DECODED] id=0x%02X" in log_fn

    decoded_line = log_fn.index("[MCU TX DECODED] id=0x%02X")
    before_decoded = log_fn[:decoded_line]
    assert "RCLCPP_DEBUG" in before_decoded[before_decoded.rfind("RCLCPP_"):]
    assert "RCLCPP_INFO" not in log_fn


def test_generated_default_port_comes_from_protocol_yaml():
    with tempfile.TemporaryDirectory() as tmpdir:
        result = subprocess.run(
            ["python3", str(CODEGEN_SCRIPT), str(CONFIG), tmpdir],
            text=True,
            capture_output=True,
        )
        assert result.returncode == 0, result.stderr

        generated_config = Path(tmpdir, "generated/generated_config.hpp").read_text()

    controller_source = (REPO_ROOT / "src/serial_controller.cpp").read_text()
    assert 'DEFAULT_PORT = "/dev/ttyACM0"' in generated_config
    assert 'declare_parameter<std::string>("port", auto_serial_bridge::config::DEFAULT_PORT)' in controller_source


def test_receive_callback_posts_to_serial_strand_without_strand_wrap():
    source = (REPO_ROOT / "src/serial_controller.cpp").read_text()

    start = source.index("void SerialController::start_receive()")
    end = source.index("size_t SerialController::ingest_received_bytes", start)
    start_receive = source[start:end]

    assert "port->async_read_some(" in start_receive
    assert "serial_strand_->wrap" in start_receive
