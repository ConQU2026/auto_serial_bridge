import os
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path

import pytest


RUN_LONG_SOAK = os.getenv("AUTO_SERIAL_BRIDGE_RUN_LONG_SOAK") == "1"


def _have_socat() -> bool:
	return shutil.which("socat") is not None


def _read_exact(port, size: int, timeout_sec: float) -> bytes:
	deadline = time.monotonic() + timeout_sec
	data = bytearray()
	while len(data) < size and time.monotonic() < deadline:
		chunk = port.read(size - len(data))
		if chunk:
			data.extend(chunk)
			continue
		time.sleep(0.002)
	return bytes(data)


def _write_in_chunks(port, payload: bytes, chunk_sizes, inter_chunk_delay_sec: float = 0.0) -> None:
	index = 0
	for chunk_size in chunk_sizes:
		if index >= len(payload):
			break
		end = min(index + chunk_size, len(payload))
		port.write(payload[index:end])
		index = end
		if inter_chunk_delay_sec > 0:
			time.sleep(inter_chunk_delay_sec)

	if index < len(payload):
		port.write(payload[index:])

	port.flush()


def _transfer_exact(sender, receiver, payload: bytes, timeout_sec: float) -> bytes:
	write_errors = []

	def _writer():
		try:
			_write_in_chunks(
				sender,
				payload,
				chunk_sizes=[512, 256, 128, 64, 32],
				inter_chunk_delay_sec=0.0005,
			)
		except Exception as exc:
			write_errors.append(exc)

	writer = threading.Thread(target=_writer)
	writer.start()

	received = _read_exact(receiver, len(payload), timeout_sec=timeout_sec)

	writer.join(timeout=timeout_sec)
	assert not writer.is_alive(), "writer thread did not finish in time"
	if write_errors:
		raise write_errors[0]

	return received


@pytest.fixture(scope="function")
def socat_process():
	if not _have_socat():
		pytest.skip("socat not found; skipping virtual-serial tests")

	with tempfile.TemporaryDirectory(prefix="scocat_pty_") as temp_dir:
		pty0 = Path(temp_dir) / "vtty0"
		pty1 = Path(temp_dir) / "vtty1"
		cmd = [
			"socat",
			"-d",
			"-d",
			f"PTY,link={pty0},raw,echo=0",
			f"PTY,link={pty1},raw,echo=0",
		]

		process = subprocess.Popen(
			cmd,
			stdout=subprocess.DEVNULL,
			stderr=subprocess.DEVNULL,
		)

		for _ in range(40):
			if pty0.exists() and pty1.exists() and process.poll() is None:
				break
			time.sleep(0.05)
		else:
			process.terminate()
			pytest.skip("socat failed to create virtual PTYs")

		yield process, str(pty0), str(pty1)

		process.terminate()
		try:
			process.wait(timeout=1)
		except Exception:
			process.kill()


@pytest.fixture(scope="function")
def serial_ports(socat_process):
	try:
		import serial
	except Exception:
		pytest.skip("pyserial not installed; skipping serial tests")

	_, pty0, pty1 = socat_process
	s0 = serial.Serial(pty0, baudrate=115200, timeout=0.05, write_timeout=2)
	s1 = serial.Serial(pty1, baudrate=115200, timeout=0.05, write_timeout=2)

	try:
		s0.reset_input_buffer()
		s1.reset_input_buffer()
		s0.reset_output_buffer()
		s1.reset_output_buffer()
	except Exception:
		pass

	yield s0, s1

	s0.close()
	s1.close()


def test_loopback(serial_ports):
	s0, s1 = serial_ports
	msg = b"hello-serial-test\n"

	s0.write(msg)
	s0.flush()

	data = _read_exact(s1, len(msg), timeout_sec=1.0)
	assert data == msg


def test_large_payload_round_trip_integrity(serial_ports):
	s0, s1 = serial_ports
	payload0 = os.urandom(16384)
	payload1 = bytes((i * 17) % 256 for i in range(16384))

	received0 = _transfer_exact(s0, s1, payload0, timeout_sec=6.0)
	assert received0 == payload0

	received1 = _transfer_exact(s1, s0, payload1, timeout_sec=6.0)
	assert received1 == payload1


def test_fragmented_and_sticky_transfer_preserves_order(serial_ports):
	s0, s1 = serial_ports
	frames = [
		b"\x5A\xA5\xFE\x04\x01\x00\x00\x00\x00",
		b"\x5A\xA5\xFE\x04\x02\x00\x00\x00\x00",
		b"\x5A\xA5\x20\x03\x01\x02\x03\x00",
		b"\x5A\xA5\xFD\x02\x20\x03\x00",
	]
	stream = b"".join(frames)

	_write_in_chunks(
		s0,
		stream,
		chunk_sizes=[1, 2, 1, 3, 5, 8, 13, 21],
		inter_chunk_delay_sec=0.001,
	)

	received = _read_exact(s1, len(stream), timeout_sec=2.0)
	assert received == stream


def test_full_duplex_concurrent_transfer(serial_ports):
	s0, s1 = serial_ports
	payload_from_0 = os.urandom(12288)
	payload_from_1 = bytes((i * 7 + 3) % 256 for i in range(16384))

	received = {"to0": b"", "to1": b""}
	start_evt = threading.Event()

	def sender(port, payload):
		start_evt.wait()
		_write_in_chunks(port, payload, chunk_sizes=[97, 31, 5, 211, 17], inter_chunk_delay_sec=0.0005)

	def receiver(port, key, size):
		start_evt.wait()
		received[key] = _read_exact(port, size, timeout_sec=6.0)

	threads = [
		threading.Thread(target=sender, args=(s0, payload_from_0)),
		threading.Thread(target=sender, args=(s1, payload_from_1)),
		threading.Thread(target=receiver, args=(s0, "to0", len(payload_from_1))),
		threading.Thread(target=receiver, args=(s1, "to1", len(payload_from_0))),
	]

	for thread in threads:
		thread.start()

	start_evt.set()

	for thread in threads:
		thread.join(timeout=8.0)
		assert not thread.is_alive(), "thread did not finish in time"

	assert received["to0"] == payload_from_1
	assert received["to1"] == payload_from_0


def test_reopen_endpoint_mid_session_still_transfers(socat_process):
	try:
		import serial
	except Exception:
		pytest.skip("pyserial not installed; skipping serial tests")

	_, pty0, pty1 = socat_process
	s0 = serial.Serial(pty0, baudrate=115200, timeout=0.05, write_timeout=1)
	s1 = serial.Serial(pty1, baudrate=115200, timeout=0.05, write_timeout=1)

	try:
		before = b"before-reopen"
		s0.write(before)
		s0.flush()
		assert _read_exact(s1, len(before), timeout_sec=1.5) == before

		s0.close()
		time.sleep(0.05)
		s0 = serial.Serial(pty0, baudrate=115200, timeout=0.05, write_timeout=1)
		s0.reset_input_buffer()
		s1.reset_input_buffer()

		after = b"after-reopen"
		s1.write(after)
		s1.flush()
		assert _read_exact(s0, len(after), timeout_sec=1.5) == after
	finally:
		if s0.is_open:
			s0.close()
		if s1.is_open:
			s1.close()


def test_repeated_short_bursts_without_loss(serial_ports):
	s0, s1 = serial_ports

	for i in range(120):
		burst_len = (i % 48) + 1
		payload = bytes((i + j) % 256 for j in range(burst_len))

		if i % 2 == 0:
			s0.write(payload)
			s0.flush()
			got = _read_exact(s1, len(payload), timeout_sec=1.0)
		else:
			s1.write(payload)
			s1.flush()
			got = _read_exact(s0, len(payload), timeout_sec=1.0)

		assert got == payload


def test_control_bytes_passthrough_in_raw_mode(serial_ports):
	s0, s1 = serial_ports

	control_pattern = bytes([0x00, 0x11, 0x13, 0x7E, 0x7F, 0xFF])
	payload0 = (bytes(range(256)) + control_pattern) * 8
	payload1 = (control_pattern + bytes(reversed(range(256)))) * 6

	got0 = _transfer_exact(s0, s1, payload0, timeout_sec=6.0)
	assert got0 == payload0

	got1 = _transfer_exact(s1, s0, payload1, timeout_sec=6.0)
	assert got1 == payload1


def test_jitter_and_silence_windows_do_not_break_stream(serial_ports):
	s0, s1 = serial_ports
	payload = os.urandom(8192)

	write_error = []

	def sender():
		try:
			cursor = 0
			chunk_index = 0
			while cursor < len(payload):
				chunk_size = [37, 89, 211, 53, 127][chunk_index % 5]
				end = min(cursor + chunk_size, len(payload))
				s0.write(payload[cursor:end])
				cursor = end
				chunk_index += 1
				if chunk_index % 9 == 0:
					time.sleep(0.08)
				else:
					time.sleep(0.002)
			s0.flush()
		except Exception as exc:
			write_error.append(exc)

	thread = threading.Thread(target=sender)
	thread.start()

	got = _read_exact(s1, len(payload), timeout_sec=8.0)

	thread.join(timeout=8.0)
	assert not thread.is_alive(), "sender thread did not finish in time"
	if write_error:
		raise write_error[0]

	assert got == payload


@pytest.mark.skipif(
	not RUN_LONG_SOAK,
	reason=(
		"Long soak disconnect/recovery test is opt-in; "
		"set AUTO_SERIAL_BRIDGE_RUN_LONG_SOAK=1 to run it."
	),
)
def test_link_brief_disconnect_auto_recovery_and_data_consistency_long_run(socat_process):
	try:
		import serial
	except Exception:
		pytest.skip("pyserial not installed; skipping serial tests")

	_, pty0, pty1 = socat_process

	def open_port(path: str):
		deadline = time.monotonic() + 3.0
		last_exc = None
		while time.monotonic() < deadline:
			try:
				return serial.Serial(path, baudrate=115200, timeout=0.02, write_timeout=0.2)
			except Exception as exc:
				last_exc = exc
				time.sleep(0.05)
		if last_exc is not None:
			raise last_exc
		raise RuntimeError(f"failed to open serial port {path}")

	def ingest_ascii_seq_lines(buffer: bytearray, chunk: bytes, out_list, out_set):
		if chunk:
			buffer.extend(chunk)
		while True:
			newline_index = buffer.find(b"\n")
			if newline_index < 0:
				break
			line = bytes(buffer[:newline_index])
			del buffer[: newline_index + 1]
			if not line:
				continue
			assert len(line) == 8 and line.isdigit(), f"corrupted frame line: {line!r}"
			seq = int(line.decode("ascii"))
			assert seq not in out_set, f"duplicate seq detected: {seq}"
			if out_list:
				assert seq > out_list[-1], f"out-of-order seq: prev={out_list[-1]} curr={seq}"
			out_list.append(seq)
			out_set.add(seq)

	s0 = open_port(pty0)
	s1 = open_port(pty1)

	try:
		s0.reset_input_buffer()
		s0.reset_output_buffer()
		s1.reset_input_buffer()
		s1.reset_output_buffer()

		run_seconds = 65.0
		disconnect_interval_sec = 8.0
		disconnect_down_sec = 0.25

		start = time.monotonic()
		end = start + run_seconds
		next_disconnect = start + disconnect_interval_sec

		received_seq = []
		received_seq_set = set()
		sent_ok_seq = []
		disconnect_markers = []
		pending = bytearray()

		seq = 0
		while time.monotonic() < end:
			frame = f"{seq:08d}\n".encode("ascii")
			try:
				s0.write(frame)
				s0.flush()
				sent_ok_seq.append(seq)
			except (serial.SerialTimeoutException, serial.SerialException, OSError):
				pass
			seq += 1

			ingest_ascii_seq_lines(pending, s1.read(4096), received_seq, received_seq_set)

			now = time.monotonic()
			if now >= next_disconnect:
				disconnect_markers.append(seq)
				s1.close()
				time.sleep(disconnect_down_sec)
				s1 = open_port(pty1)
				s1.reset_input_buffer()
				pending.clear()
				next_disconnect += disconnect_interval_sec

			time.sleep(0.003)

		drain_until = time.monotonic() + 1.2
		while time.monotonic() < drain_until:
			ingest_ascii_seq_lines(pending, s1.read(4096), received_seq, received_seq_set)
			time.sleep(0.01)

		assert len(disconnect_markers) >= 5, "disconnect cycles were fewer than expected"
		assert len(received_seq) >= 500, f"insufficient received frames: {len(received_seq)}"

		sent_ok_set = set(sent_ok_seq)
		for value in received_seq:
			assert value in sent_ok_set, f"received unknown seq not confirmed sent: {value}"

		for marker in disconnect_markers:
			assert any(v > marker for v in received_seq), (
				f"no post-recovery frames observed after disconnect marker seq={marker}"
			)
	finally:
		if s0.is_open:
			s0.close()
		if s1.is_open:
			s1.close()
