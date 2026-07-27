import struct
import subprocess
import sys
import time
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parent.parent
CODEGEN_SCRIPT = REPO_ROOT / "scripts" / "codegen.py"
SAMPLE_CONFIG = REPO_ROOT / "config" / "protocol.yaml"
HARNESS_SOURCE = REPO_ROOT / "test" / "test_mcu_main.c"


def _load_sample_config() -> dict:
    return yaml.safe_load(SAMPLE_CONFIG.read_text(encoding="utf-8"))


def _checksum_crc8(data: bytes) -> int:
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x80:
                crc = ((crc << 1) ^ 0x31) & 0xFF
            else:
                crc = (crc << 1) & 0xFF
    return crc


def _calculate_checksum(data: bytes, algorithm: str) -> int:
    algorithm = algorithm.upper()
    if algorithm == "NONE":
        return 0
    if algorithm == "SUM8":
        return sum(data) & 0xFF
    if algorithm == "XOR8":
        value = 0
        for byte in data:
            value ^= byte
        return value
    if algorithm == "CRC8":
        return _checksum_crc8(data)
    raise ValueError(f"Unsupported checksum algorithm: {algorithm}")


def _message_ids(config: dict) -> dict[str, int]:
    return {message["name"]: int(message["id"]) for message in config["messages"]}


def _build_frame(config: dict, packet_id: int, payload: bytes) -> bytes:
    head1 = int(config["config"]["head_byte_1"])
    head2 = int(config["config"]["head_byte_2"])
    body = bytes([packet_id, len(payload)]) + payload
    checksum = _calculate_checksum(body, config["config"]["checksum"])
    return bytes([head1, head2]) + body + bytes([checksum])


def _parse_frame(config: dict, frame: bytes) -> tuple[int, bytes]:
    head1 = int(config["config"]["head_byte_1"])
    head2 = int(config["config"]["head_byte_2"])
    assert frame[:2] == bytes([head1, head2])

    packet_id = frame[2]
    payload_len = frame[3]
    expected_size = 2 + 1 + 1 + payload_len + 1
    assert len(frame) == expected_size

    payload = frame[4:-1]
    checksum = frame[-1]
    expected_checksum = _calculate_checksum(frame[2:-1], config["config"]["checksum"])
    assert checksum == expected_checksum
    return packet_id, payload


def _extract_tx_frames(stdout: str) -> list[bytes]:
    frames = []
    for line in stdout.splitlines():
        if not line.startswith("TX_FRAME"):
            continue
        hex_bytes = line.split()[1:]
        frames.append(bytes(int(value, 16) for value in hex_bytes))
    return frames


def _generate_and_compile(tmp_path: Path) -> tuple[dict, Path]:
    config = _load_sample_config()
    generated_config = tmp_path / "protocol.yaml"
    generated_config.write_text(SAMPLE_CONFIG.read_text(encoding="utf-8"), encoding="utf-8")

    codegen = subprocess.run(
        [sys.executable, str(CODEGEN_SCRIPT), str(generated_config), str(tmp_path)],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    assert codegen.returncode == 0, (
        f"codegen failed:\nstdout:\n{codegen.stdout}\nstderr:\n{codegen.stderr}"
    )

    executable = tmp_path / "mcu_protocol_test"
    compile_result = subprocess.run(
        [
            "gcc",
            "-std=c11",
            "-Wall",
            "-Wextra",
            "-I",
            str(tmp_path / "generated"),
            str(tmp_path / "generated" / "protocol.c"),
            str(HARNESS_SOURCE),
            "-o",
            str(executable),
        ],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    assert compile_result.returncode == 0, (
        f"gcc failed:\nstdout:\n{compile_result.stdout}\nstderr:\n{compile_result.stderr}"
    )
    assert executable.exists()
    return config, executable


def _run_harness(executable: Path, chunks: list[bytes]) -> subprocess.CompletedProcess:
    proc = subprocess.Popen(
        [str(executable)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert proc.stdin is not None
    assert proc.stdout is not None
    assert proc.stderr is not None

    for chunk in chunks:
        if not chunk:
            continue
        proc.stdin.write(chunk)
        proc.stdin.flush()
        time.sleep(0.01)

    proc.stdin.close()
    stdout = proc.stdout.read()
    stderr = proc.stderr.read()
    returncode = proc.wait(timeout=5)
    return subprocess.CompletedProcess(
        args=[str(executable)],
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


def test_compile_mcu_code(tmp_path):
    _config, executable = _generate_and_compile(tmp_path)
    result = subprocess.run([str(executable)], capture_output=True, text=True)
    assert result.returncode == 0


def test_handshake_roundtrip(tmp_path):
    config, executable = _generate_and_compile(tmp_path)
    ids = _message_ids(config)
    payload = struct.pack("<I", 0x12345678)
    frame = _build_frame(config, ids["Handshake"], payload)

    result = _run_harness(executable, [frame])
    stdout = result.stdout.decode("utf-8")

    assert result.returncode == 0
    assert "RX Handshake hash=0x12345678" in stdout

    tx_frames = _extract_tx_frames(stdout)
    assert len(tx_frames) == 1
    packet_id, echoed_payload = _parse_frame(config, tx_frames[0])
    assert packet_id == ids["Handshake"]
    assert echoed_payload == payload


def test_heartbeat_echo(tmp_path):
    config, executable = _generate_and_compile(tmp_path)
    ids = _message_ids(config)
    payload = struct.pack("<I", 0xFFFFFFFF)
    frame = _build_frame(config, ids["Heartbeat"], payload)

    result = _run_harness(executable, [frame])
    stdout = result.stdout.decode("utf-8")

    assert result.returncode == 0
    assert "RX Heartbeat count=4294967295" in stdout

    tx_frames = _extract_tx_frames(stdout)
    assert len(tx_frames) == 1
    packet_id, echoed_payload = _parse_frame(config, tx_frames[0])
    assert packet_id == ids["Heartbeat"]
    assert echoed_payload == payload


def test_checksum_mismatch_rejected(tmp_path):
    config, executable = _generate_and_compile(tmp_path)
    ids = _message_ids(config)
    payload = struct.pack("<I", 99)
    frame = bytearray(_build_frame(config, ids["Heartbeat"], payload))
    frame[-1] ^= 0xFF

    result = _run_harness(executable, [bytes(frame)])
    assert result.returncode == 0
    assert result.stdout.decode("utf-8").strip() == ""


def test_fragmented_input(tmp_path):
    config, executable = _generate_and_compile(tmp_path)
    ids = _message_ids(config)
    payload = struct.pack("<I", 7)
    frame = _build_frame(config, ids["Handshake"], payload)

    chunks = [frame[:1], frame[1:3], frame[3:5], frame[5:8], frame[8:]]
    result = _run_harness(executable, chunks)
    stdout = result.stdout.decode("utf-8")

    assert result.returncode == 0
    assert "RX Handshake hash=0x00000007" in stdout

    tx_frames = _extract_tx_frames(stdout)
    assert len(tx_frames) == 1
    packet_id, echoed_payload = _parse_frame(config, tx_frames[0])
    assert packet_id == ids["Handshake"]
    assert echoed_payload == payload


def test_noise_before_valid_frame(tmp_path):
    config, executable = _generate_and_compile(tmp_path)
    ids = _message_ids(config)
    payload = struct.pack("<I", 88)
    frame = _build_frame(config, ids["Heartbeat"], payload)
    noise = b"\x00\x11\x5A\x00\xFF"

    result = _run_harness(executable, [noise + frame])
    stdout = result.stdout.decode("utf-8")

    assert result.returncode == 0
    assert "RX Heartbeat count=88" in stdout

    tx_frames = _extract_tx_frames(stdout)
    assert len(tx_frames) == 1
    packet_id, echoed_payload = _parse_frame(config, tx_frames[0])
    assert packet_id == ids["Heartbeat"]
    assert echoed_payload == payload
