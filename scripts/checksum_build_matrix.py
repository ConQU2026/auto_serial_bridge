import copy
import os
import shutil
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


def _protocol_path(repo_root: Path) -> Path:
    return repo_root / "config" / "protocol.yaml"


def _copy_source_tree(source_root: Path, dest_root: Path) -> None:
    # Keep matrix builds isolated from the developer's local workspace state.
    shutil.copytree(
        source_root,
        dest_root,
        ignore=shutil.ignore_patterns(
            ".git",
            "build",
            "install",
            "log",
            "__pycache__",
            ".pytest_cache",
        ),
    )


def _load_protocol_config(repo_root: Path) -> tuple[dict, str]:
    protocol_path = _protocol_path(repo_root)
    original_text = protocol_path.read_text()
    return yaml.safe_load(original_text), original_text


def _write_protocol_config(repo_root: Path, config: dict) -> None:
    _protocol_path(repo_root).write_text(
        yaml.safe_dump(config, sort_keys=False, allow_unicode=False)
    )


def _run(
    cmd: list[str],
    cwd: Path,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=True,
        text=True,
        env=env,
    )


def run_checksum_build_matrix() -> list[dict]:
    results: list[dict] = []

    with tempfile.TemporaryDirectory(prefix="checksum_matrix_") as temp_root_str:
        temp_root = Path(temp_root_str)
        matrix_source_root = temp_root / "src" / "auto_serial_bridge"
        _copy_source_tree(_repo_root(), matrix_source_root)
        base_config, _ = _load_protocol_config(matrix_source_root)
        if "config" not in base_config:
            raise ValueError("protocol.yaml is missing top-level 'config'")

        for algorithm in SUPPORTED_ALGOS:
            next_config = copy.deepcopy(base_config)
            next_config["config"]["checksum"] = algorithm
            _write_protocol_config(matrix_source_root, next_config)
            build_base = temp_root / algorithm / "build"
            install_base = temp_root / algorithm / "install"
            log_base = temp_root / algorithm / "log"

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
                cwd=temp_root / "src",
                extra_env={
                    "CMAKE_BUILD_PARALLEL_LEVEL": "1",
                    "MAKEFLAGS": "-j1",
                },
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

    return results


if __name__ == "__main__":
    matrix_results = run_checksum_build_matrix()
    for result in matrix_results:
        print(
            f"{result['algorithm']}: "
            f"build={result['build_returncode']} "
            f"ctest={result['ctest_returncode']}"
        )
