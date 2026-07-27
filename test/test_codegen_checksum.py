"""
测试 codegen.py 在不同校验算法 / 握手配置下生成的代码是否正确。

不依赖 ROS 启动, 直接调用 codegen.py 脚本并检查输出文件内容。
"""

import os
import re
import sys
import tempfile
import shutil
import subprocess

import pytest
import yaml

SCRIPTS_DIR = os.path.join(os.path.dirname(__file__), '..', 'scripts')
CODEGEN_SCRIPT = os.path.abspath(os.path.join(SCRIPTS_DIR, 'codegen.py'))
SAMPLE_CONFIG = os.path.abspath(
    os.path.join(os.path.dirname(__file__), '..', 'config', 'protocol.yaml')
)

SUPPORTED_ALGOS = ["NONE", "SUM8", "XOR8", "CRC8"]
LOCAL_TIMESTAMP_RE = r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}"


def _load_sample_config() -> dict:
    with open(SAMPLE_CONFIG, 'r') as f:
        return yaml.safe_load(f.read())


def _run_codegen(config_dict: dict, output_dir: str) -> subprocess.CompletedProcess:
    config_path = os.path.join(output_dir, 'protocol.yaml')
    with open(config_path, 'w') as f:
        yaml.dump(config_dict, f, default_flow_style=False)

    return subprocess.run(
        [sys.executable, CODEGEN_SCRIPT, config_path, output_dir],
        capture_output=True, text=True,
    )


def _run_codegen_text(config_text: str, output_dir: str) -> subprocess.CompletedProcess:
    config_path = os.path.join(output_dir, 'protocol.yaml')
    with open(config_path, 'w') as f:
        f.write(config_text)

    return subprocess.run(
        [sys.executable, CODEGEN_SCRIPT, config_path, output_dir],
        capture_output=True, text=True,
    )


def _read_generated(output_dir: str, relpath: str) -> str:
    legacy_names = {
        'mcu_output/protocol.h': 'protocol.h',
        'mcu_output/protocol.c': 'protocol.c',
        'mcu_output/PROTOCOL_DOC.md': 'PROTOCOL_DOC.md',
        'include/auto_serial_bridge/generated_config.hpp': 'generated_config.hpp',
        'include/auto_serial_bridge/generated_bindings.hpp': 'generated_bindings.hpp',
    }
    relpath = os.path.join('generated', legacy_names.get(relpath, relpath))
    fpath = os.path.join(output_dir, relpath)
    with open(fpath, 'r') as f:
        return f.read()


def _extract_function_body(content: str, func_name: str) -> str:
    match = re.search(
        rf"void {re.escape(func_name)}\([^)]*\) \{{(?P<body>.*?)\n\}}",
        content,
        re.DOTALL,
    )
    assert match is not None, f"未找到函数 {func_name}"
    return match.group("body")


def _extract_timestamp(pattern: str, content: str, error_message: str) -> str:
    match = re.search(pattern, content, re.MULTILINE)
    assert match is not None, error_message
    return match.group("ts")


def _extract_protocol_hash(content: str) -> str:
    match = re.search(r"^#define PROTOCOL_HASH (?P<hash>0x[0-9A-F]+)$", content, re.MULTILINE)
    assert match is not None, "生成文件中应包含 PROTOCOL_HASH 宏"
    return match.group("hash")


# ============================================================================
# Fixture: 临时输出目录
# ============================================================================

@pytest.fixture()
def tmpdir():
    d = tempfile.mkdtemp(prefix='codegen_test_')
    yield d
    shutil.rmtree(d, ignore_errors=True)


# ============================================================================
# 1. 各算法 —— C++ generated_config.hpp 正确包含算法枚举
# ============================================================================

@pytest.mark.parametrize("algo", SUPPORTED_ALGOS)
def test_cpp_config_has_algo_enum(algo, tmpdir):
    cfg = _load_sample_config()
    cfg['config']['checksum'] = algo

    result = _run_codegen(cfg, tmpdir)
    assert result.returncode == 0, f"codegen failed:\n{result.stdout}\n{result.stderr}"

    content = _read_generated(tmpdir, 'include/auto_serial_bridge/generated_config.hpp')
    assert f"ChecksumAlgo::{algo}" in content, \
        f"generated_config.hpp 应包含 ChecksumAlgo::{algo}"


# ============================================================================
# 2. 各算法 —— MCU protocol.h 正确包含宏定义
# ============================================================================

@pytest.mark.parametrize("algo", SUPPORTED_ALGOS)
def test_mcu_header_has_algo_macro(algo, tmpdir):
    cfg = _load_sample_config()
    cfg['config']['checksum'] = algo

    result = _run_codegen(cfg, tmpdir)
    assert result.returncode == 0, f"codegen failed:\n{result.stdout}\n{result.stderr}"

    content = _read_generated(tmpdir, 'mcu_output/protocol.h')
    assert f"CHECKSUM_ALGO_{algo}" in content, \
        f"protocol.h 应包含 CHECKSUM_ALGO_{algo}"


# ============================================================================
# 3. CRC8_TABLE 仅在 CRC8 算法下生成
# ============================================================================

@pytest.mark.parametrize("algo", ["NONE", "SUM8", "XOR8"])
def test_no_crc_table_for_non_crc8(algo, tmpdir):
    cfg = _load_sample_config()
    cfg['config']['checksum'] = algo

    result = _run_codegen(cfg, tmpdir)
    assert result.returncode == 0

    content = _read_generated(tmpdir, 'mcu_output/protocol.h')
    assert "CRC8_TABLE" not in content, \
        f"选用 {algo} 时, protocol.h 不应包含 CRC8_TABLE"


def test_crc_table_present_for_crc8(tmpdir):
    cfg = _load_sample_config()
    cfg['config']['checksum'] = "CRC8"

    result = _run_codegen(cfg, tmpdir)
    assert result.returncode == 0

    content = _read_generated(tmpdir, 'mcu_output/protocol.h')
    assert "CRC8_TABLE" in content
    assert "0x00" in content
    assert "0x31" in content


# ============================================================================
# 4. MCU protocol.c —— 校验函数与算法一致
# ============================================================================

_ALGO_C_KEYWORDS = {
    "NONE": "无校验",
    "SUM8": "SUM8",
    "XOR8": "XOR8",
    "CRC8": "CRC8",
}

@pytest.mark.parametrize("algo", SUPPORTED_ALGOS)
def test_mcu_source_checksum_function(algo, tmpdir):
    cfg = _load_sample_config()
    cfg['config']['checksum'] = algo

    result = _run_codegen(cfg, tmpdir)
    assert result.returncode == 0

    content = _read_generated(tmpdir, 'mcu_output/protocol.c')
    assert "checksum_update" in content, \
        "protocol.c 应包含 checksum_update 函数"
    assert _ALGO_C_KEYWORDS[algo] in content, \
        f"protocol.c 应包含 {algo} 的注释标记"


# ============================================================================
# 5. require_handshake 配置传播
# ============================================================================

@pytest.mark.parametrize("require_hs", [True, False])
def test_handshake_cpp_config(require_hs, tmpdir):
    cfg = _load_sample_config()
    cfg['config']['require_handshake'] = require_hs

    result = _run_codegen(cfg, tmpdir)
    assert result.returncode == 0

    content = _read_generated(tmpdir, 'include/auto_serial_bridge/generated_config.hpp')
    expected_val = 'true' if require_hs else 'false'
    assert f"REQUIRE_HANDSHAKE = {expected_val}" in content


@pytest.mark.parametrize("require_hs", [True, False])
def test_handshake_mcu_macro(require_hs, tmpdir):
    cfg = _load_sample_config()
    cfg['config']['require_handshake'] = require_hs

    result = _run_codegen(cfg, tmpdir)
    assert result.returncode == 0

    content = _read_generated(tmpdir, 'mcu_output/protocol.h')
    expected_val = '1' if require_hs else '0'
    assert f"CFG_REQUIRE_HANDSHAKE {expected_val}" in content


@pytest.mark.parametrize("enable_heartbeat", [True, False])
def test_enable_heartbeat_cpp_config(enable_heartbeat, tmpdir):
    cfg = _load_sample_config()
    cfg['config']['enable_heartbeat'] = enable_heartbeat

    result = _run_codegen(cfg, tmpdir)
    assert result.returncode == 0

    content = _read_generated(tmpdir, 'include/auto_serial_bridge/generated_config.hpp')
    expected_val = 'true' if enable_heartbeat else 'false'
    assert f"ENABLE_HEARTBEAT = {expected_val}" in content


@pytest.mark.parametrize("enable_heartbeat", [True, False])
def test_enable_heartbeat_mcu_macro(enable_heartbeat, tmpdir):
    cfg = _load_sample_config()
    cfg['config']['enable_heartbeat'] = enable_heartbeat

    result = _run_codegen(cfg, tmpdir)
    assert result.returncode == 0

    content = _read_generated(tmpdir, 'mcu_output/protocol.h')
    expected_val = '1' if enable_heartbeat else '0'
    assert f"CFG_ENABLE_HEARTBEAT {expected_val}" in content


@pytest.mark.parametrize("strict_heartbeat", [True, False])
def test_strict_heartbeat_config_propagates_to_generated_headers(strict_heartbeat, tmpdir):
    cfg = _load_sample_config()
    cfg['config']['strict_heartbeat'] = strict_heartbeat

    result = _run_codegen(cfg, tmpdir)
    assert result.returncode == 0, f"codegen failed:\n{result.stdout}\n{result.stderr}"

    header = _read_generated(tmpdir, 'mcu_output/protocol.h')
    cpp_cfg = _read_generated(tmpdir, 'include/auto_serial_bridge/generated_config.hpp')
    expected_macro = '1' if strict_heartbeat else '0'
    expected_const = 'true' if strict_heartbeat else 'false'

    assert f"CFG_STRICT_HEARTBEAT {expected_macro}" in header
    assert f"constexpr bool STRICT_HEARTBEAT = {expected_const};" in cpp_cfg


@pytest.mark.parametrize("heartbeat_timeout_ms", [1, 3000, 10000])
def test_heartbeat_timeout_config_propagates_to_generated_headers(heartbeat_timeout_ms, tmpdir):
    cfg = _load_sample_config()
    cfg['config']['heartbeat_timeout_ms'] = heartbeat_timeout_ms

    result = _run_codegen(cfg, tmpdir)
    assert result.returncode == 0, f"codegen failed:\n{result.stdout}\n{result.stderr}"

    header = _read_generated(tmpdir, 'mcu_output/protocol.h')
    cpp_cfg = _read_generated(tmpdir, 'include/auto_serial_bridge/generated_config.hpp')

    assert f"CFG_HEARTBEAT_TIMEOUT_MS {heartbeat_timeout_ms}" in header
    assert f"constexpr int HEARTBEAT_TIMEOUT_MS = {heartbeat_timeout_ms};" in cpp_cfg


@pytest.mark.parametrize(
    ("retry_interval_ms", "max_retries"),
    [(50, 1), (100, 3), (250, 5)],
)
def test_reliable_retry_config_propagates_to_generated_headers(retry_interval_ms, max_retries, tmpdir):
    cfg = _load_sample_config()
    cfg['config']['reliable_retry_interval_ms'] = retry_interval_ms
    cfg['config']['reliable_max_retries'] = max_retries

    result = _run_codegen(cfg, tmpdir)
    assert result.returncode == 0, f"codegen failed:\n{result.stdout}\n{result.stderr}"

    header = _read_generated(tmpdir, 'mcu_output/protocol.h')
    cpp_cfg = _read_generated(tmpdir, 'include/auto_serial_bridge/generated_config.hpp')

    assert f"CFG_RELIABLE_RETRY_INTERVAL_MS {retry_interval_ms}" in header
    assert f"CFG_RELIABLE_MAX_RETRIES {max_retries}" in header
    assert f"constexpr int RELIABLE_RETRY_INTERVAL_MS = {retry_interval_ms};" in cpp_cfg
    assert f"constexpr int RELIABLE_MAX_RETRIES = {max_retries};" in cpp_cfg


# ============================================================================
# 6. 不支持的算法应报错
# ============================================================================

@pytest.mark.parametrize("bad_algo", ["MD5", "SHA256", "crc16", ""])
def test_unsupported_algo_rejected(bad_algo, tmpdir):
    cfg = _load_sample_config()
    cfg['config']['checksum'] = bad_algo

    result = _run_codegen(cfg, tmpdir)
    assert result.returncode != 0, \
        f"不支持的算法 '{bad_algo}' 应使 codegen 返回非零退出码"


# ============================================================================
# 7. 协议文档中校验算法描述正确
# ============================================================================

_DOC_ALGO_KEYWORDS = {
    "NONE": "无校验",
    "SUM8": "SUM8",
    "XOR8": "XOR8",
    "CRC8": "CRC8",
}

@pytest.mark.parametrize("algo", SUPPORTED_ALGOS)
def test_protocol_doc_checksum_desc(algo, tmpdir):
    cfg = _load_sample_config()
    cfg['config']['checksum'] = algo

    result = _run_codegen(cfg, tmpdir)
    assert result.returncode == 0

    content = _read_generated(tmpdir, 'mcu_output/PROTOCOL_DOC.md')
    assert _DOC_ALGO_KEYWORDS[algo] in content, \
        f"PROTOCOL_DOC.md 应包含 {algo} 的描述"


def test_generated_outputs_include_consistent_local_timestamps(tmpdir):
    cfg = _load_sample_config()

    result = _run_codegen(cfg, tmpdir)
    assert result.returncode == 0, f"codegen failed:\n{result.stdout}\n{result.stderr}"

    header_content = _read_generated(tmpdir, 'mcu_output/protocol.h')
    source_content = _read_generated(tmpdir, 'mcu_output/protocol.c')
    doc_content = _read_generated(tmpdir, 'mcu_output/PROTOCOL_DOC.md')

    header_ts = _extract_timestamp(
        rf"^// Generated at: (?P<ts>{LOCAL_TIMESTAMP_RE})$",
        header_content,
        "protocol.h 顶部应包含本地时间注释",
    )
    source_ts = _extract_timestamp(
        rf"^// Generated at: (?P<ts>{LOCAL_TIMESTAMP_RE})$",
        source_content,
        "protocol.c 顶部应包含本地时间注释",
    )
    doc_ts = _extract_timestamp(
        rf"^> 生成时间：(?P<ts>{LOCAL_TIMESTAMP_RE})$",
        doc_content,
        "PROTOCOL_DOC.md 顶部应包含可见的生成时间说明",
    )

    assert header_content.startswith(f"// Generated at: {header_ts}\n#pragma once\n")
    assert source_content.startswith(f"// Generated at: {source_ts}\n#include \"protocol.h\"\n")
    assert doc_content.startswith(f"> 生成时间：{doc_ts}\n# MCU ↔ ROS 串口通信协议文档\n")
    assert header_ts == source_ts == doc_ts, \
        "同一次 codegen 运行生成的时间注释应完全一致"


# ============================================================================
# 8. 所有算法下生成的文件完整性
# ============================================================================

_EXPECTED_FILES = [
    'generated/protocol.h',
    'generated/protocol.c',
    'generated/PROTOCOL_DOC.md',
    'generated/generated_config.hpp',
    'generated/generated_bindings.hpp',
]

@pytest.mark.parametrize("algo", SUPPORTED_ALGOS)
def test_all_files_generated(algo, tmpdir):
    cfg = _load_sample_config()
    cfg['config']['checksum'] = algo

    result = _run_codegen(cfg, tmpdir)
    assert result.returncode == 0

    for relpath in _EXPECTED_FILES:
        fpath = os.path.join(tmpdir, relpath)
        assert os.path.isfile(fpath), \
            f"算法 {algo}: 缺少生成文件 {relpath}"
        assert os.path.getsize(fpath) > 0, \
            f"算法 {algo}: 生成文件 {relpath} 为空"


# ============================================================================
# 9. 跨算法切换不残留旧算法痕迹
# ============================================================================

def test_algo_switch_no_residue(tmpdir):
    """先用 CRC8 生成, 再用 SUM8 重新生成到同一目录, 确认无 CRC8 残留."""
    cfg = _load_sample_config()

    cfg['config']['checksum'] = "CRC8"
    result = _run_codegen(cfg, tmpdir)
    assert result.returncode == 0

    cfg['config']['checksum'] = "SUM8"
    result = _run_codegen(cfg, tmpdir)
    assert result.returncode == 0

    header = _read_generated(tmpdir, 'mcu_output/protocol.h')
    assert "CRC8_TABLE" not in header, \
        "切换到 SUM8 后, protocol.h 不应残留 CRC8_TABLE"
    assert "CHECKSUM_ALGO_SUM8" in header

    cpp_cfg = _read_generated(tmpdir, 'include/auto_serial_bridge/generated_config.hpp')
    assert "ChecksumAlgo::SUM8" in cpp_cfg
    assert "ChecksumAlgo::CRC8" not in cpp_cfg.split("constexpr ChecksumAlgo CHECKSUM_ALGO")[1].split(";")[0], \
        "generated_config.hpp 的 CHECKSUM_ALGO 赋值不应是 CRC8"


def test_generated_bindings_guard_indexed_array_input_and_resize_output(tmpdir):
    cfg = _load_sample_config()
    cfg['messages'].append({
        'name': 'MirrorArray',
        'id': 0x42,
        'direction': 'both',
        'sub_topic': 'mirror_array',
        'pub_topic': 'mirror_array',
        'ros_msg': 'std_msgs/msg/Float32MultiArray',
        'fields': [
            {'proto': 'first', 'type': 'f32', 'ros': 'data[0]'},
            {'proto': 'third', 'type': 'f32', 'ros': 'data[2]'},
        ],
    })

    result = _run_codegen(cfg, tmpdir)
    assert result.returncode == 0, f"codegen failed:\n{result.stdout}\n{result.stderr}"

    content = _read_generated(tmpdir, 'include/auto_serial_bridge/generated_bindings.hpp')
    assert 'msg->data.size() < 3' in content, \
        '订阅端应先检查 Float32MultiArray.data 长度，避免越界读取'
    assert 'msg.data.resize(3);' in content, \
        '发布端应先 resize Float32MultiArray.data，避免越界写入'


def test_generated_bindings_add_loopback_guard_for_same_topic_both(tmpdir):
    cfg = _load_sample_config()
    cfg['messages'].append({
        'name': 'LoopbackStatus',
        'id': 0x43,
        'direction': 'both',
        'sub_topic': 'loopback_status',
        'pub_topic': 'loopback_status',
        'ros_msg': 'std_msgs/msg/UInt32',
        'fields': [
            {'proto': 'count', 'type': 'u32', 'ros': 'data'},
        ],
    })

    result = _run_codegen(cfg, tmpdir)
    assert result.returncode == 0, f"codegen failed:\n{result.stdout}\n{result.stderr}"

    content = _read_generated(tmpdir, 'include/auto_serial_bridge/generated_bindings.hpp')
    assert 'const rclcpp::MessageInfo& msg_info' in content, \
        '生成的订阅回调应接收 MessageInfo，用于识别自发回环'
    assert 'should_skip_loopback(PACKET_ID_LOOPBACKSTATUS, msg_info)' in content, \
        '同 topic 的双向消息应在订阅端显式跳过自身发布的回环消息'
    assert 'register_loopback_publisher(PACKET_ID_LOOPBACKSTATUS, pub_LoopbackStatus);' in content, \
        '生成的发布者应向节点登记，用于回环抑制'


def test_generated_bindings_guard_int32_multiarray_to_u8_range_and_cast_output(tmpdir):
    cfg = _load_sample_config()
    cfg['messages'].append({
        'name': 'ByteStatusMirror',
        'id': 0x44,
        'direction': 'both',
        'sub_topic': 'byte_status_mirror',
        'pub_topic': 'byte_status_mirror',
        'ros_msg': 'std_msgs/msg/Int32MultiArray',
        'fields': [
            {'proto': 'task_id', 'type': 'u8', 'ros': 'data[0]'},
            {'proto': 'status', 'type': 'u8', 'ros': 'data[1]'},
        ],
    })

    result = _run_codegen(cfg, tmpdir)
    assert result.returncode == 0, f"codegen failed:\n{result.stdout}\n{result.stderr}"

    content = _read_generated(tmpdir, 'include/auto_serial_bridge/generated_bindings.hpp')
    assert 'field task_id is out of uint8 range [0, 255]' in content
    assert 'field status is out of uint8 range [0, 255]' in content
    assert 'pkt.task_id = static_cast<uint8_t>(msg->data[0]);' in content
    assert 'pkt.status = static_cast<uint8_t>(msg->data[1]);' in content
    assert 'msg.data[0] = static_cast<int32_t>(pkt->task_id);' in content
    assert 'msg.data[1] = static_cast<int32_t>(pkt->status);' in content


def test_sample_config_heartbeat_is_both(tmpdir):
    cfg = _load_sample_config()

    result = _run_codegen(cfg, tmpdir)
    assert result.returncode == 0, f"codegen failed:\n{result.stdout}\n{result.stderr}"

    content = _read_generated(tmpdir, 'include/auto_serial_bridge/generated_bindings.hpp')
    protocol_doc = _read_generated(tmpdir, 'mcu_output/PROTOCOL_DOC.md')
    rx_section = protocol_doc.split('## 电控 → ROS（电控主动发送）', 1)[1].split('## ROS → 电控（电控被动接收）', 1)[0]

    assert 'pub_Heartbeat' in content
    assert 'case PACKET_ID_HEARTBEAT' in content
    assert 'register_loopback_publisher(PACKET_ID_HEARTBEAT' in content
    assert 'Heartbeat (ROS -> MCU)' in content
    assert '### `Heartbeat`' in rx_section


def test_message_debug_log_mode_off_suppresses_generated_rx_tx_debug(tmpdir):
    cfg = _load_sample_config()
    for msg in cfg['messages']:
        msg['debug_log_mode'] = 'off'

    result = _run_codegen(cfg, tmpdir)
    assert result.returncode == 0, f"codegen failed:\n{result.stdout}\n{result.stderr}"

    content = _read_generated(tmpdir, 'include/auto_serial_bridge/generated_bindings.hpp')
    assert 'RCLCPP_DEBUG(node->get_logger(), "TX ' not in content
    assert 'if constexpr (config::is_debug_log_enabled(' in content


def test_message_debug_log_mode_on_generates_tx_debug_without_change_gate(tmpdir):
    cfg = _load_sample_config()
    for msg in cfg['messages']:
        msg['debug_log_mode'] = 'off'

    target_msg = next(
        (msg for msg in cfg['messages'] if msg.get('direction') == 'tx'),
        None,
    )
    assert target_msg is not None, 'sample config should include at least one tx/both message'
    target_msg['debug_log_mode'] = 'on'

    result = _run_codegen(cfg, tmpdir)
    assert result.returncode == 0, f"codegen failed:\n{result.stdout}\n{result.stderr}"

    content = _read_generated(tmpdir, 'include/auto_serial_bridge/generated_bindings.hpp')
    message_name = target_msg['name']
    upper_name = message_name.upper()
    assert f'PACKET_ID_{upper_name}' in content
    assert 'has_previous_pkt_' not in content
    assert 'previous_pkt_' not in content
    assert 'should_log_' not in content
    assert 'std::memcmp' not in content


def test_message_debug_log_mode_on_generates_rx_debug_without_change_gate(tmpdir):
    cfg = _load_sample_config()
    for msg in cfg['messages']:
        msg['debug_log_mode'] = 'off'

    target_msg = next(
        (msg for msg in cfg['messages'] if msg.get('direction') in ('rx', 'both')),
        None,
    )
    assert target_msg is not None, 'sample config should include at least one rx/both message'
    target_msg['debug_log_mode'] = 'on'

    result = _run_codegen(cfg, tmpdir)
    assert result.returncode == 0, f"codegen failed:\n{result.stdout}\n{result.stderr}"

    content = _read_generated(tmpdir, 'include/auto_serial_bridge/generated_bindings.hpp')
    message_name = target_msg['name']
    assert f'RCLCPP_DEBUG(logger, "RX {message_name}:' in content
    assert 'has_previous_pkt_' not in content
    assert 'previous_pkt_' not in content
    assert 'should_log_' not in content
    assert 'std::memcmp' not in content


def test_message_debug_log_mode_missing_defaults_to_on(tmpdir):
    cfg = _load_sample_config()
    target_msg = next(msg for msg in cfg['messages'] if msg.get('direction') == 'tx')
    target_msg.pop('debug_log_mode', None)

    result = _run_codegen(cfg, tmpdir)
    assert result.returncode == 0, f"codegen failed:\n{result.stdout}\n{result.stderr}"

    content = _read_generated(tmpdir, 'include/auto_serial_bridge/generated_bindings.hpp')
    assert f'case PACKET_ID_{target_msg["name"].upper()}: return true;' in _read_generated(
        tmpdir, 'include/auto_serial_bridge/generated_config.hpp'
    )
    assert 'std::memcmp' not in content


def test_message_debug_log_mode_invalid_value_fails_codegen(tmpdir):
    cfg = _load_sample_config()
    cfg['messages'][0]['debug_log_mode'] = 'on_change'

    result = _run_codegen(cfg, tmpdir)
    assert result.returncode != 0
    combined_output = f"{result.stdout}\n{result.stderr}"
    assert 'invalid debug_log_mode' in combined_output


def test_codegen_uses_size_t_checksum_signature_and_correct_buffer_sizes(tmpdir):
    """验证 codegen 生成的校验函数签名和缓冲区大小。

    MCU 端 rx_buffer 基于实际 max payload 计算（非 ROS 端 buffer_size），
    ROS 端 BUFFER_SIZE 保持 yaml 中配置的值。
    """
    cfg = _load_sample_config()

    result = _run_codegen(cfg, tmpdir)
    assert result.returncode == 0, f"codegen failed:\n{result.stdout}\n{result.stderr}"

    header = _read_generated(tmpdir, 'mcu_output/protocol.h')
    source = _read_generated(tmpdir, 'mcu_output/protocol.c')
    cpp_cfg = _read_generated(tmpdir, 'include/auto_serial_bridge/generated_config.hpp')

    assert 'uint8_t calculate_checksum(const uint8_t* data, size_t len);' in header
    assert 'uint8_t calculate_checksum(const uint8_t* data, size_t len) {' in source
    assert 'for (size_t i = 0; i < len; i++) {' in source
    assert 'static uint8_t rx_buffer[' in source
    match = re.search(r'static uint8_t rx_buffer\[(\d+)\];', source)
    assert match is not None, "MCU 源码中应包含 rx_buffer 定义"
    mcu_buf_size = int(match.group(1))
    assert mcu_buf_size < 64, (
        f"MCU rx_buffer 应基于 max payload 计算，"
        f"不应为 ROS 端 buffer_size; got {mcu_buf_size}"
    )
    assert 'constexpr size_t BUFFER_SIZE = 1024;' in cpp_cfg


def test_protocol_hash_ignores_non_wire_configuration(tmpdir):
    config_a = _load_sample_config()
    config_b = _load_sample_config()
    config_b['serial_controller']['ros__parameters']['port'] = '/dev/ttyUSB9'
    config_b['serial_controller']['ros__parameters']['baudrate'] = 921600
    config_b['serial_controller']['ros__parameters']['debug_raw_frame'] = True
    config_b['config']['qos_depth'] = 42
    config_b['config']['heartbeat_timeout_ms'] = 9999
    config_b['config']['reliable_retry_interval_ms'] = 777
    config_b['messages'][3]['sub_topic'] = 'renamed/command'
    config_b['messages'][3]['ros_msg'] = 'std_msgs/msg/Int32'
    config_b['messages'][3]['notes'] = 'changed documentation only'

    result_a = _run_codegen(config_a, tmpdir)
    assert result_a.returncode == 0, f"codegen failed:\n{result_a.stdout}\n{result_a.stderr}"
    hash_a = _extract_protocol_hash(_read_generated(tmpdir, 'mcu_output/protocol.h'))

    alt_dir = tempfile.mkdtemp(prefix='codegen_norm_')
    try:
        result_b = _run_codegen(config_b, alt_dir)
        assert result_b.returncode == 0, f"codegen failed:\n{result_b.stdout}\n{result_b.stderr}"
        hash_b = _extract_protocol_hash(_read_generated(alt_dir, 'mcu_output/protocol.h'))
    finally:
        shutil.rmtree(alt_dir, ignore_errors=True)

    assert hash_a == hash_b, \
        "topic、port、baudrate、ros_msg、QoS、notes 和超时重试参数不应改变 PROTOCOL_HASH"


def test_protocol_hash_changes_when_message_order_changes(tmpdir):
    config_a = _load_sample_config()
    config_b = _load_sample_config()
    config_b['messages'][3], config_b['messages'][4] = config_b['messages'][4], config_b['messages'][3]

    result_a = _run_codegen(config_a, tmpdir)
    assert result_a.returncode == 0, f"codegen failed:\n{result_a.stdout}\n{result_a.stderr}"
    hash_a = _extract_protocol_hash(_read_generated(tmpdir, 'mcu_output/protocol.h'))

    alt_dir = tempfile.mkdtemp(prefix='codegen_order_')
    try:
        result_b = _run_codegen(config_b, alt_dir)
        assert result_b.returncode == 0, f"codegen failed:\n{result_b.stdout}\n{result_b.stderr}"
        hash_b = _extract_protocol_hash(_read_generated(alt_dir, 'mcu_output/protocol.h'))
    finally:
        shutil.rmtree(alt_dir, ignore_errors=True)

    assert hash_a != hash_b, \
        "messages 列表顺序变化应视为真实协议变化，PROTOCOL_HASH 不应保持一致"


@pytest.mark.parametrize("ignore_version_mismatch", [True, False])
def test_ignore_version_mismatch_config_propagates_to_generated_headers(ignore_version_mismatch, tmpdir):
    cfg = _load_sample_config()
    cfg['config']['ignore_version_mismatch'] = ignore_version_mismatch

    result = _run_codegen(cfg, tmpdir)
    assert result.returncode == 0, f"codegen failed:\n{result.stdout}\n{result.stderr}"

    header = _read_generated(tmpdir, 'mcu_output/protocol.h')
    cpp_cfg = _read_generated(tmpdir, 'include/auto_serial_bridge/generated_config.hpp')
    expected_macro = '1' if ignore_version_mismatch else '0'
    expected_const = 'true' if ignore_version_mismatch else 'false'

    assert f"CFG_IGNORE_VERSION_MISMATCH {expected_macro}" in header
    assert f"constexpr bool IGNORE_VERSION_MISMATCH = {expected_const};" in cpp_cfg


def test_mcu_source_auto_replies_handshake_on_mismatch_when_ignore_enabled(tmpdir):
    cfg = _load_sample_config()
    cfg['config']['require_handshake'] = True
    cfg['config']['ignore_version_mismatch'] = True

    result = _run_codegen(cfg, tmpdir)
    assert result.returncode == 0, f"codegen failed:\n{result.stdout}\n{result.stderr}"

    content = _read_generated(tmpdir, 'mcu_output/protocol.c')
    body = _extract_function_body(content, 'on_receive_Handshake')

    assert 'send_Handshake(pkt);' in body
    assert 'if (pkt->protocol_hash == PROTOCOL_HASH)' not in body


def test_mcu_source_requires_hash_match_when_ignore_disabled(tmpdir):
    cfg = _load_sample_config()
    cfg['config']['require_handshake'] = True
    cfg['config']['ignore_version_mismatch'] = False

    result = _run_codegen(cfg, tmpdir)
    assert result.returncode == 0, f"codegen failed:\n{result.stdout}\n{result.stderr}"

    content = _read_generated(tmpdir, 'mcu_output/protocol.c')
    body = _extract_function_body(content, 'on_receive_Handshake')

    assert 'if (pkt->protocol_hash == PROTOCOL_HASH)' in body
    assert 'send_Handshake(pkt);' in body


def test_mcu_source_does_not_auto_reply_handshake_when_disabled(tmpdir):
    cfg = _load_sample_config()
    cfg['config']['require_handshake'] = False

    result = _run_codegen(cfg, tmpdir)
    assert result.returncode == 0, f"codegen failed:\n{result.stdout}\n{result.stderr}"

    content = _read_generated(tmpdir, 'mcu_output/protocol.c')
    body = _extract_function_body(content, 'on_receive_Handshake')

    assert 'send_Handshake(pkt);' not in body
    assert 'PROTOCOL_HASH' not in body


def test_mcu_source_auto_acks_heartbeat_when_enabled(tmpdir):
    cfg = _load_sample_config()
    cfg['config']['enable_heartbeat'] = True

    result = _run_codegen(cfg, tmpdir)
    assert result.returncode == 0, f"codegen failed:\n{result.stdout}\n{result.stderr}"

    content = _read_generated(tmpdir, 'mcu_output/protocol.c')
    body = _extract_function_body(content, 'on_receive_Heartbeat')

    assert 'send_Heartbeat(pkt);' in body


def test_mcu_source_does_not_auto_ack_heartbeat_when_disabled(tmpdir):
    cfg = _load_sample_config()
    cfg['config']['enable_heartbeat'] = False

    result = _run_codegen(cfg, tmpdir)
    assert result.returncode == 0, f"codegen failed:\n{result.stdout}\n{result.stderr}"

    content = _read_generated(tmpdir, 'mcu_output/protocol.c')
    body = _extract_function_body(content, 'on_receive_Heartbeat')

    assert 'send_Heartbeat(pkt);' not in body
