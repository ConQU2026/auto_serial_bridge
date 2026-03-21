import os
import time
import subprocess
import shutil
import tempfile
from pathlib import Path
import pytest
import rclpy

def _have_socat():
	return shutil.which("socat") is not None


@pytest.fixture(scope="module")
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

		p = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
												 stderr=subprocess.DEVNULL)

		for _ in range(40):
			if pty0.exists() and pty1.exists():
				break
			time.sleep(0.05)
		else:
			p.terminate()
			pytest.skip("socat failed to create virtual PTYs")

		yield p, str(pty0), str(pty1)

		p.terminate()
		try:
			p.wait(timeout=1)
		except Exception:
			p.kill()


@pytest.fixture(scope="function")
def serial_ports(socat_process):
	try:
		import serial
	except Exception:
		pytest.skip("pyserial not installed; skipping serial tests")

	_, pty0, pty1 = socat_process
	s0 = serial.Serial(pty0, baudrate=115200, timeout=1)
	s1 = serial.Serial(pty1, baudrate=115200, timeout=1)

	# 清空任何初始数据
	try:
		s0.reset_input_buffer()
		s1.reset_input_buffer()
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

	# give a small moment for transfer
	time.sleep(0.05)

	data = s1.read(len(msg))
	assert data == msg
