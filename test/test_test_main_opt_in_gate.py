import os
import subprocess
import sys
from pathlib import Path


def test_test_main_is_skipped_without_opt_in_env():
    repo_root = Path(__file__).resolve().parent.parent
    env = os.environ.copy()
    env.pop("AUTO_SERIAL_BRIDGE_RUN_PTY_INTEGRATION", None)

    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "test/test_main.py", "-rs"],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 skipped" in result.stdout, result.stdout + result.stderr
