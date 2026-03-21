from pathlib import Path
import sys


SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from checksum_build_matrix import SUPPORTED_ALGOS, run_checksum_build_matrix


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
