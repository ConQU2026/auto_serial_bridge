from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "ros2_test.yml"
README_PATH = REPO_ROOT / "README.md"
CMAKELISTS_PATH = REPO_ROOT / "CMakeLists.txt"


def _workflow_run_script() -> str:
    workflow = yaml.safe_load(WORKFLOW_PATH.read_text())
    jobs = workflow["jobs"]
    assert len(jobs) == 1, f"expected one public CI job, found: {list(jobs)}"
    steps = next(iter(jobs.values()))["steps"]
    run_blocks = [step.get("run", "") for step in steps if "run" in step]
    return "\n".join(run_blocks)


def test_public_workflow_only_runs_sample_self_checks():
    script = _workflow_run_script()

    assert "protocol-sample.yaml" in script
    assert "scripts/codegen.py" in script
    assert "pytest" in script
    assert "test/test_codegen_checksum.py" in script

    assert "colcon build" not in script
    assert "colcon test" not in script
    assert "cp src/auto_serial_bridge/config/protocol-sample.yaml src/auto_serial_bridge/config/protocol.yaml" not in script


def test_readme_documents_private_protocol_boundary():
    readme = README_PATH.read_text()

    assert "开源仓库不附带生产环境的 `config/protocol.yaml`" in readme
    assert "`config/protocol-sample.yaml` 仅用于示例和公开自检" in readme
    assert "公开 CI 只校验 sample 和 codegen，不执行 `colcon build` 或 `colcon test`" in readme


def test_cmakelists_tracks_package_manifest_for_reconfigure_and_codegen():
    cmakelists = CMAKELISTS_PATH.read_text()

    assert "set(PACKAGE_MANIFEST" in cmakelists
    assert "CMAKE_CONFIGURE_DEPENDS" in cmakelists
    assert "${PACKAGE_MANIFEST}" in cmakelists
    assert "DEPENDS ${PROTOCOL_YAML} ${CODEGEN_SCRIPT} ${PACKAGE_MANIFEST}" in cmakelists


def test_readme_documents_package_version_rebuild_trigger():
    readme = README_PATH.read_text()

    assert "修改 `package.xml` 的 `<version>` 后，同样会触发重新构建流程。" in readme
