import importlib.util
import sys
import types
import xml.etree.ElementTree as ET
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "ros2_test.yml"
README_PATH = REPO_ROOT / "README.md"
CMAKELISTS_PATH = REPO_ROOT / "CMakeLists.txt"
PACKAGE_XML_PATH = REPO_ROOT / "package.xml"
NODE_LAUNCH_PATH = REPO_ROOT / "launch" / "serial_bridge_by_node.launch.py"
COMPONENT_LAUNCH_PATH = REPO_ROOT / "launch" / "serial_bridge_by_component.launch.py"
PROTOCOL_SAMPLE_PATH = REPO_ROOT / "config" / "protocol-sample.yaml"
RUNTIME_PARAMS_PATH = REPO_ROOT / "config" / "runtime_params.yaml"


def _workflow_run_script() -> str:
    workflow = yaml.safe_load(WORKFLOW_PATH.read_text())
    jobs = workflow["jobs"]
    assert len(jobs) == 1, f"expected one public CI job, found: {list(jobs)}"
    steps = next(iter(jobs.values()))["steps"]
    run_blocks = [step.get("run", "") for step in steps if "run" in step]
    return "\n".join(run_blocks)


def _load_launch_module(path: Path, share_dir: Path):
    fake_ament_index = types.ModuleType("ament_index_python")
    fake_ament_index_packages = types.ModuleType("ament_index_python.packages")
    fake_ament_index_packages.get_package_share_directory = lambda _package: str(share_dir)
    fake_ament_index.packages = fake_ament_index_packages

    fake_launch = types.ModuleType("launch")

    class FakeLaunchDescription:
        def __init__(self):
            self.entities = []

        def add_action(self, action):
            self.entities.append(action)

    fake_launch.LaunchDescription = FakeLaunchDescription

    fake_launch_ros = types.ModuleType("launch_ros")
    fake_launch_ros_actions = types.ModuleType("launch_ros.actions")
    fake_launch_ros_descriptions = types.ModuleType("launch_ros.descriptions")

    class FakeNode:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class FakeComposableNodeContainer:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class FakeComposableNode:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    fake_launch_ros_actions.Node = FakeNode
    fake_launch_ros_actions.ComposableNodeContainer = FakeComposableNodeContainer
    fake_launch_ros_descriptions.ComposableNode = FakeComposableNode
    fake_launch_ros.actions = fake_launch_ros_actions
    fake_launch_ros.descriptions = fake_launch_ros_descriptions

    module_name = f"_test_launch_{path.stem.replace('.', '_')}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)

    original_modules = {
        name: sys.modules.get(name)
        for name in (
            "ament_index_python",
            "ament_index_python.packages",
            "launch",
            "launch_ros",
            "launch_ros.actions",
            "launch_ros.descriptions",
        )
    }
    try:
        sys.modules["ament_index_python"] = fake_ament_index
        sys.modules["ament_index_python.packages"] = fake_ament_index_packages
        sys.modules["launch"] = fake_launch
        sys.modules["launch_ros"] = fake_launch_ros
        sys.modules["launch_ros.actions"] = fake_launch_ros_actions
        sys.modules["launch_ros.descriptions"] = fake_launch_ros_descriptions
        assert spec.loader is not None
        spec.loader.exec_module(module)
        return module
    finally:
        for name, original in original_modules.items():
            if original is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = original


def _package_dependencies() -> set[str]:
    root = ET.fromstring(PACKAGE_XML_PATH.read_text())
    deps = set()
    for tag in ("depend", "exec_depend", "build_depend", "test_depend"):
        deps.update(elem.text for elem in root.findall(tag) if elem.text)
    return deps


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


def test_readme_documents_protocol_rebuild_requirement_for_launch():
    readme = README_PATH.read_text()

    assert "修改 `config/protocol.yaml` 后必须重新 `colcon build`" in readme
    assert "否则 launch 读到的仍然是 install 目录里的旧值" in readme


def test_launch_files_load_runtime_params_from_protocol_yaml(tmp_path):
    share_dir = tmp_path / "share"
    config_dir = share_dir / "config"
    config_dir.mkdir(parents=True)
    protocol_text = PROTOCOL_SAMPLE_PATH.read_text()
    (config_dir / "protocol.yaml").write_text(protocol_text)

    node_module = _load_launch_module(NODE_LAUNCH_PATH, share_dir)
    component_module = _load_launch_module(COMPONENT_LAUNCH_PATH, share_dir)

    expected_params = yaml.safe_load(protocol_text)["serial_controller"]["ros__parameters"]

    node_description = node_module.generate_launch_description()
    component_description = component_module.generate_launch_description()

    node_action = node_description.entities[0]
    component_action = component_description.entities[0]
    component_node = component_action.kwargs["composable_node_descriptions"][0]

    assert node_action.kwargs["parameters"] == [expected_params]
    assert component_node.kwargs["parameters"] == [expected_params]


def test_runtime_params_yaml_has_been_removed():
    assert not RUNTIME_PARAMS_PATH.exists()


def test_package_xml_declares_yaml_runtime_dependency_for_launch():
    deps = _package_dependencies()

    assert "python3-yaml" in deps


def test_protocol_sample_yaml_contains_runtime_ros_section_for_launch_only():
    config = yaml.safe_load(PROTOCOL_SAMPLE_PATH.read_text())
    params = config["serial_controller"]["ros__parameters"]

    assert list(params) == ["port", "baudrate", "timeout"]
    assert "&baudrate" not in PROTOCOL_SAMPLE_PATH.read_text()
    assert "*baudrate" not in PROTOCOL_SAMPLE_PATH.read_text()


def test_protocol_sample_yaml_is_protocol_only_without_ros_runtime_section():
    config = yaml.safe_load(PROTOCOL_SAMPLE_PATH.read_text())

    assert list(config) == ["serial_controller", "config", "type_mappings", "messages"]
    assert list(config["serial_controller"]["ros__parameters"]) == ["port", "baudrate", "timeout"]
    assert "&baudrate" not in PROTOCOL_SAMPLE_PATH.read_text()
    assert "*baudrate" not in PROTOCOL_SAMPLE_PATH.read_text()


def test_readme_protocol_example_keeps_runtime_ros_section_in_protocol_yaml():
    readme = README_PATH.read_text()

    assert "serial_controller:" in readme
    assert "ros__parameters:" in readme
    assert "&baudrate" not in readme
    assert "*baudrate" not in readme


def test_serial_controller_does_not_expose_heartbeat_runtime_overrides():
    serial_controller = (REPO_ROOT / "src" / "serial_controller.cpp").read_text()

    assert 'declare_parameter<bool>("enable_heartbeat"' not in serial_controller
    assert 'declare_parameter<int>("heartbeat_timeout_ms"' not in serial_controller
    assert 'get_parameter("enable_heartbeat"' not in serial_controller
    assert 'get_parameter("heartbeat_timeout_ms"' not in serial_controller


def test_checksum_build_matrix_is_marked_run_serial():
    cmakelists = CMAKELISTS_PATH.read_text()

    assert "set_tests_properties(checksum_build_matrix PROPERTIES RUN_SERIAL TRUE)" in cmakelists


def test_serial_controller_uses_shared_loopback_helper():
    serial_controller = (REPO_ROOT / "src" / "serial_controller.cpp").read_text()

    assert "should_skip_loopback_delivery(" in serial_controller
