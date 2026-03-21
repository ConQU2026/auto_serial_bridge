import copy
import subprocess
import tempfile
from pathlib import Path

import yaml


SUPPORTED_ALGOS = ["NONE", "SUM8", "XOR8", "CRC8"]
GTEST_REGEX = (
    "^(test_checksum_algorithms|test_edge_condition|test_packet_handler|"
    "test_packet_handler_reset|test_protocol_structure)$"
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _protocol_path() -> Path:
    return _repo_root() / "config" / "protocol.yaml"


def _codegen_script() -> Path:
    return _repo_root() / "scripts" / "codegen.py"


def _load_protocol_config() -> tuple[dict, str]:
    protocol_path = _protocol_path()
    original_text = protocol_path.read_text()
    return yaml.safe_load(original_text), original_text


def _write_protocol_config(config: dict) -> None:
    _protocol_path().write_text(
        yaml.safe_dump(config, sort_keys=False, allow_unicode=False)
    )


def _run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=True,
        text=True,
    )


def _restore_original_state(original_text: str) -> None:
    protocol_path = _protocol_path()
    protocol_path.write_text(original_text)
    restore = _run(
        ["python3", str(_codegen_script()), str(protocol_path), str(_repo_root())],
        cwd=_repo_root(),
    )
    if restore.returncode != 0:
        raise RuntimeError(
            "failed to restore generated files for original protocol.yaml:\n"
            f"{restore.stdout}\n{restore.stderr}"
        )


def run_checksum_build_matrix() -> list[dict]:
    base_config, original_text = _load_protocol_config()
    if "config" not in base_config:
        raise ValueError("protocol.yaml is missing top-level 'config'")

    results: list[dict] = []

    with tempfile.TemporaryDirectory(prefix="checksum_matrix_") as temp_root_str:
        temp_root = Path(temp_root_str)
        build_base = temp_root / "build"
        install_base = temp_root / "install"
        log_base = temp_root / "log"

        try:
            for algorithm in SUPPORTED_ALGOS:
                next_config = copy.deepcopy(base_config)
                next_config["config"]["checksum"] = algorithm
                _write_protocol_config(next_config)

                build = _run(
                    [
                        "colcon",
                        "--log-base",
                        str(log_base),
                        "build",
                        "--packages-select",
                        "auto_serial_bridge",
                        "--build-base",
                        str(build_base),
                        "--install-base",
                        str(install_base),
                        "--event-handlers",
                        "console_direct+",
                        "--cmake-force-configure",
                        "--cmake-args",
                        "-DBUILD_TESTING=ON",
                    ],
                    cwd=_repo_root(),
                )

                package_build_dir = build_base / "auto_serial_bridge"
                ctest = _run(
                    ["ctest", "--output-on-failure", "-R", GTEST_REGEX],
                    cwd=package_build_dir,
                ) if build.returncode == 0 else subprocess.CompletedProcess(
                    args=["ctest", "--output-on-failure", "-R", GTEST_REGEX],
                    returncode=1,
                    stdout="",
                    stderr="skipped because build failed",
                )

                results.append(
                    {
                        "algorithm": algorithm,
                        "build_returncode": build.returncode,
                        "build_stdout": build.stdout,
                        "build_stderr": build.stderr,
                        "ctest_returncode": ctest.returncode,
                        "ctest_stdout": ctest.stdout,
                        "ctest_stderr": ctest.stderr,
                    }
                )

                if build.returncode != 0 or ctest.returncode != 0:
                    break
        finally:
            _restore_original_state(original_text)

    return results


if __name__ == "__main__":
    matrix_results = run_checksum_build_matrix()
    for result in matrix_results:
        print(
            f"{result['algorithm']}: "
            f"build={result['build_returncode']} "
            f"ctest={result['ctest_returncode']}"
        )
