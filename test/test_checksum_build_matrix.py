from pathlib import Path
import subprocess
import sys


SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from checksum_build_matrix import SUPPORTED_ALGOS, run_checksum_build_matrix


def test_does_not_write_real_protocol_yaml(monkeypatch):
    import checksum_build_matrix

    protocol_path = checksum_build_matrix._protocol_path(
        checksum_build_matrix._repo_root()
    )
    original_write_text = Path.write_text
    writes_to_real_protocol = []

    def tracking_write_text(self, data, *args, **kwargs):
        if self == protocol_path:
            writes_to_real_protocol.append(data)
            return len(data)
        return original_write_text(self, data, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", tracking_write_text)
    monkeypatch.setattr(
        checksum_build_matrix,
        "_run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=args[0],
            returncode=0,
            stdout="",
            stderr="",
        ),
    )

    results = run_checksum_build_matrix()

    assert [result["algorithm"] for result in results] == SUPPORTED_ALGOS
    assert writes_to_real_protocol == []


def test_runs_all_supported_algorithms():
    results = run_checksum_build_matrix()
    summaries = [
        (
            result["algorithm"],
            result["build_returncode"],
            result["ctest_returncode"],
        )
        for result in results
    ]

    assert [result["algorithm"] for result in results] == SUPPORTED_ALGOS, summaries
    assert all(result["build_returncode"] == 0 for result in results), summaries
    assert all(result["ctest_returncode"] == 0 for result in results), summaries
