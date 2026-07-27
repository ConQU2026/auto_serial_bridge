# auto_serial_bridge

`auto_serial_bridge` 是面向 ROS 2 Humble 的配置驱动串口桥。仓库自带可运行的中性协议；用户只需修改 `config/protocol.yaml`，重新编译后即可同步生成 ROS 绑定、MCU C 代码和协议文档。

## 能力与限制

- 双帧头、1 字节消息 ID、1 字节 payload 长度和可选 `NONE/SUM8/XOR8/CRC8` 校验。
- 支持握手、协议哈希、严格心跳、可靠发送、ACK/重传、串口热插拔和自动重连。
- YAML 可引用当前 workspace 或 underlay 中已安装的任意 ROS 消息包；CMake 会自动提取包名并执行 `find_package()`。
- 仅支持代码生成器明确列出的定长 C 类型：`uint8_t`、`uint16_t`、`uint32_t`、`int32_t`、`float`。
- payload 长度字段为 1 字节；可靠消息附加 seq 后也不得超过 255 字节。
- 线协议直接使用 packed C 结构体内存布局，要求双方为小端序，定长整数宽度符合 `<stdint.h>`，`float` 为 32 位 IEEE-754。

## 安装与构建

安装 ROS 2 Humble 后，先安装基础依赖：

```bash
sudo apt update
sudo apt install python3-colcon-common-extensions python3-yaml \
  ros-humble-ament-cmake ros-humble-rclcpp ros-humble-rclcpp-components \
  ros-humble-std-msgs ros-humble-io-context
```

在 ROS 2 workspace 的 `src` 目录克隆并直接构建：

```bash
cd ~/ros2_ws/src
git clone <repository-url> auto_serial_bridge
cd ..
source /opt/ros/humble/setup.bash
colcon build --packages-select auto_serial_bridge
source install/setup.bash
```

仓库已经跟踪 `config/protocol.yaml`，全新 clone 不需要复制配置。修改 `config/protocol.yaml` 后必须重新执行 `colcon build --packages-select auto_serial_bridge`；launch 读取的是安装目录中的配置，未重新编译时仍会使用旧版本。

## YAML 结构

`serial_controller.ros__parameters` 是节点运行参数：

```yaml
serial_controller:
  ros__parameters:
    port: "/dev/ttyACM0"
    baudrate: 115200
    debug_raw_frame: false
```

波特率只在这里定义。`port` 和 `baudrate` 不参与协议哈希；`debug_raw_frame` 只控制日志。

`config` 定义协议与传输行为：

- `buffer_size`：ROS 端环形缓冲区容量，必须容纳最大完整帧。
- `head_byte_1`、`head_byte_2`：0 至 255 的双帧头字节。
- `checksum`：`NONE`、`SUM8`、`XOR8` 或 `CRC8`。
- `require_handshake`：进入 RUNNING 前是否要求握手。
- `ignore_version_mismatch`：是否允许协议哈希不一致；公共默认值为 `false`。
- `enable_heartbeat`、`strict_heartbeat`、`heartbeat_timeout_ms`：心跳与严格超时策略。
- `reliable_retry_interval_ms`、`reliable_max_retries`：可靠消息重传参数。
- `qos_depth`：自动生成的普通 ROS publisher/subscription 深度。

`type_mappings` 把 YAML 类型别名映射为受支持的定长 C 类型。`messages` 中每项包含：

- `name`：合法 C 标识符，生成 `Packet_<name>`。
- `id`：0 至 255；`0xFD`、`0xFE`、`0xFF` 固定保留给 Ack、Heartbeat、Handshake。
- `direction`：`tx` 表示 ROS 到 MCU，`rx` 表示 MCU 到 ROS，`both` 表示双向。
- `sub_topic` / `pub_topic`：按方向必填，建议使用相对话题以支持 namespace 和 remap。
- `ros_msg`：`package/msg/Type`，例如 `sensor_msgs/msg/Imu`。
- `reliable`：仅允许用于 `tx` 或 `both`；启用后框架追加 seq 并等待 ACK。
- `debug_log_mode`：`on` 或 `off`。
- `fields`：按线协议顺序列出 `proto`、`type` 和 ROS 字段路径 `ros`。
- `notes`：仅用于文档，不参与协议哈希。

Ack、Heartbeat、Handshake 的名称、ID、方向、字段类型和顺序是框架契约，代码生成阶段会拒绝删除或修改。默认中性示例还提供 `DemoCommand`、`DemoReliableCommand` 和 `DemoTelemetry`。

自定义 `ros_msg` 对应的消息包必须先能被当前 workspace 或 underlay 的 CMake 找到。例如使用 `sensor_msgs/msg/Imu` 前先安装 `ros-humble-sensor-msgs`，或先在同一 workspace 构建自定义消息包；无需手动修改本包的 `CMakeLists.txt`。

## 启动

普通节点：

```bash
ros2 launch auto_serial_bridge serial_bridge_by_node.launch.py
ros2 launch auto_serial_bridge serial_bridge_by_node.launch.py port:=/dev/ttyUSB0 log_level:=debug
```

组件容器：

```bash
ros2 launch auto_serial_bridge serial_bridge_by_component.launch.py
ros2 launch auto_serial_bridge serial_bridge_by_component.launch.py port:=/dev/ttyUSB0
```

两个入口使用同一个 `SerialController`，因此握手、重连和 ready 行为一致。namespace 和 remap 由标准 ROS 2 启动参数处理。

## Ready 状态

`SerialController` 在相对话题 `auto_serial_bridge/ready` 发布 `std_msgs/msg/Bool`，QoS 为 depth 1、reliable、transient local。无 namespace 时解析为 `/auto_serial_bridge/ready`。

- 节点启动立即发布 `false`。
- 串口打开并完成握手后发布 `true`。
- 禁用握手时，串口打开并进入 RUNNING 后发布 `true`。
- 读取错误、端口关闭、严格心跳超时、连接重置和析构时发布 `false`。
- 相同值不会重复发布。

## 生成与安装产物

构建目录 `build/auto_serial_bridge/generated/` 包含：

```text
protocol.h
protocol.c
generated_config.hpp
generated_bindings.hpp
PROTOCOL_DOC.md
```

安装后公共头位于 `include/auto_serial_bridge/generated/`。MCU 使用的 `protocol.h`、`protocol.c` 和 `PROTOCOL_DOC.md` 位于 `share/auto_serial_bridge/mcu_output/`。源码目录不会产生生成文件。

协议哈希只包含帧头、校验算法、握手/心跳开关、消息 ID、方向、reliable 标志和按顺序解析后的字段 C 类型。串口路径、波特率、ROS topic、ROS 消息路径、QoS、日志、notes、心跳超时、重传参数以及 YAML 格式均不参与哈希。

## 串口权限

可执行 `sudo ./scripts/auto_udev.sh` 生成 `MODE="0660"`、`GROUP="dialout"` 的规则。脚本会在可用时加入 `uaccess`，并在设备没有序列号时提示同 VID/PID 多设备冲突风险。

## Web 编辑器

直接打开 `web/index.html` 可编辑并导出 YAML。系统消息固定，波特率与 `serial_controller.ros__parameters.baudrate` 保持一致。导出的文件应替换仓库内 `config/protocol.yaml`，随后重新编译。

## 许可证

项目采用 Apache License 2.0。内置 `js-yaml 4.1.0` 使用 MIT License，详见 `THIRD_PARTY_NOTICES.md`。
