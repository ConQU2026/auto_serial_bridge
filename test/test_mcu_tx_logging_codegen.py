import subprocess
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CODEGEN_SCRIPT = REPO_ROOT / "scripts" / "codegen.py"
CONFIG = REPO_ROOT / "test" / "fixtures" / "protocol_test.yaml"


def _generate(tmpdir: str) -> Path:
    result = subprocess.run(
        ["python3", str(CODEGEN_SCRIPT), str(CONFIG), tmpdir],
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    return Path(tmpdir, "generated")


def test_generated_bindings_include_decoded_mcu_tx_logging():
    with tempfile.TemporaryDirectory() as tmpdir:
        bindings = (_generate(tmpdir) / "generated_bindings.hpp").read_text()

    assert "MCU TX DECODED" in bindings
    assert "describe_packet" in bindings
    assert "case PACKET_ID_FIXTURECOMMAND" in bindings
    assert "FixtureCommand:" in bindings


def test_generated_default_port_comes_from_protocol_yaml():
    with tempfile.TemporaryDirectory() as tmpdir:
        generated_config = (_generate(tmpdir) / "generated_config.hpp").read_text()

    assert 'DEFAULT_PORT = "/dev/ttyACM0"' in generated_config
