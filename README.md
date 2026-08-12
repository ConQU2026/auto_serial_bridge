# Auto Serial Bridge

![ROS2](https://img.shields.io/badge/ROS2-Humble-blue)
![Ubuntu](https://img.shields.io/badge/Ubuntu-22.04-orange)
![License](https://img.shields.io/badge/License-Apache%202.0-green)

用一份 YAML 定义 ROS 2 与 MCU 之间的通信协议，编译时自动生成两端代码：

- ROS 端：话题与串口帧的自动互转，无需手写序列化。
- MCU 端：可直接集成的 `protocol.h` / `protocol.c`，附带协议文档 `PROTOCOL_DOC.md`。
- 内置握手（协议哈希校验）、心跳检测、断线自动重连，按需启用 ACK + 超时重传。

## 快速开始

假设工作空间为 `~/ros2_ws`。

### 1. 克隆并安装依赖

```bash
mkdir -p ~/ros2_ws/src && cd ~/ros2_ws/src
git clone https://github.com/ConQU2026/auto_serial_bridge.git
cd ~/ros2_ws
source /opt/ros/humble/setup.bash
rosdep install --from-paths src/auto_serial_bridge --ignore-src -r -y
```

### 2. 定义协议

编辑 `config/protocol.yaml`，在 `messages` 里写业务消息（文件末尾有注释示例）：

```yaml
messages:
  - name: "MotorCommand"          # 合法 C 标识符，生成 Packet_MotorCommand
    id: 0x10                      # 0x00-0xFC 唯一 ID（0xFD-0xFF 为框架保留）
    direction: "tx"               # tx = ROS->MCU，rx = MCU->ROS，both = 双向
    reliable: true                # 启用 ACK + 超时重传（仅 tx 可用）
    sub_topic: "motor/command"    # tx/both 订阅的话题；rx/both 用 pub_topic
    ros_msg: "std_msgs/msg/UInt32"
    fields:                       # 按线协议顺序映射字段
      - { proto: "value", type: "u32", ros: "data" }
```

Ack、Heartbeat、Handshake 系统消息由框架内置，不需要也不允许写在 YAML 里。

也可以用 [Web 协议编辑器](https://conqu2026.github.io/auto_serial_bridge/)
可视化编辑并导出 YAML（离线可直接打开 `web/index.html`）。

### 3. 编译

```bash
cd ~/ros2_ws
colcon build --packages-select auto_serial_bridge
source install/setup.bash
```

修改 `protocol.yaml` 后必须重新编译，代码和协议哈希才会更新。

### 4. 集成 MCU

生成的下位机文件在：

```text
install/auto_serial_bridge/share/auto_serial_bridge/mcu_output/          # 交付目录
build/auto_serial_bridge/include/auto_serial_bridge/generated/           # 构建产物
```

集成步骤：

1. 把 `protocol.h` 和 `protocol.c` 复制到 MCU 工程。
2. 实现 `void serial_write(const uint8_t *data, uint16_t len)`，接到 UART 发送。
3. 把 UART 收到的每个字节传给 `protocol_fsm_feed(uint8_t byte)`。
4. 在 `on_receive_<Message>()` 回调中处理 ROS 发来的消息。
5. 用 `send_<Message>()` 向 ROS 发送 `rx` / `both` 方向的消息。

握手和心跳的 MCU 侧默认处理已自动生成，哈希匹配即自动回包。每次修改协议后
需重新同步 MCU 文件，两端 `PROTOCOL_HASH` 必须一致。

生成文件中的 `/* USER CODE BEGIN/END */` 区块在重新生成时会保留，但 `build`
目录可能被清理，长期维护的业务代码应放在 MCU 工程里。

### 5. 启动

```bash
ros2 launch auto_serial_bridge serial_bridge_by_node.launch.py
# 覆盖串口 / 波特率 / 日志级别
ros2 launch auto_serial_bridge serial_bridge_by_node.launch.py \
  port:=/dev/ttyUSB0 baudrate:=921600 log_level:=debug
```

也支持 component 方式（`serial_bridge_by_component.launch.py`）或直接
`ros2 run auto_serial_bridge serial_node`。

节点在 `auto_serial_bridge/ready` 话题发布 `std_msgs/msg/Bool`（transient
local）：串口打开且握手完成为 `true`，断连、心跳超时或重连中为 `false`。

## 配置参考

`protocol.yaml` 的四个顶层部分：

| 部分 | 作用 |
| :--- | :--- |
| `serial_controller` | ROS 节点运行参数：`port`、`baudrate`、`debug_raw_frame` |
| `config` | 线协议与传输行为，参与代码生成 |
| `type_mappings` | 字段类型别名到定长 C 类型的映射 |
| `messages` | 业务消息列表 |

`config` 常用项：

- `checksum`：`NONE` / `SUM8` / `XOR8` / `CRC8`。
- `require_handshake`：打开串口后先校验协议哈希，通过才收发业务消息。
- `enable_heartbeat` + `strict_heartbeat`：心跳超时（`heartbeat_timeout_ms`）
  断开串口并自动重连。
- `reliable_retry_interval_ms` / `reliable_max_retries`：可靠消息的重传参数。
- `buffer_size`：接收缓冲区，须能容纳最大完整帧。

字段类型支持 `uint8_t`、`uint16_t`、`uint32_t`、`int32_t`、`float`（32 位
IEEE-754，两端须为小端序）。payload 最大 255 字节，可靠消息会透明追加 1 字节
序列号。

`ros_msg` 引用的消息包会被 CMake 自动 `find_package()`，只需保证该包已安装或
在同一工作空间中；建议同时在 `package.xml` 里声明依赖以便 `rosdep` 安装。

### 协议哈希

哈希覆盖线协议契约：帧头、校验算法、握手/心跳开关、消息 ID、方向、reliable
标志和字段 C 类型（按 ID 排序，与 YAML 书写顺序无关）。串口路径、波特率、
话题名、QoS、日志和 notes 不参与哈希。

## 串口权限（可选）

用脚本生成固定的 udev 设备别名：

```bash
sudo ./scripts/auto_udev.sh
```

脚本按 VID/PID/序列号生成规则；设备无序列号且接了多个同型号设备时，需按 USB
端口手写更具体的规则。

## 测试

```bash
colcon build --packages-select auto_serial_bridge --cmake-args -DBUILD_TESTING=ON
colcon test --packages-select auto_serial_bridge --event-handlers console_direct+
colcon test-result --verbose
```

可选的重量级测试：

```bash
# 虚拟串口回归（需要 socat）
python3 -m pytest -q test/test_scocat.py
# ROS 节点 PTY 端到端（握手/心跳/重连）
AUTO_SERIAL_BRIDGE_RUN_PTY_INTEGRATION=1 python3 -m pytest -q test/test_main.py
```

## 常见问题

- **修改配置后行为没变**：重新 `colcon build` 并 `source install/setup.bash`。
- **握手一直失败**：两端必须使用同一次构建的产物，对比 `PROTOCOL_HASH`；
  不要混用新旧 `protocol.h` / `protocol.c`。
- **找不到自定义消息包**：先安装或构建提供 `ros_msg` 的包，再重新编译本包。
- **找不到 mcu_output**：它在 `install/.../share/auto_serial_bridge/` 下，
  不在源码目录；先确认编译成功。

## 许可证

Apache License 2.0。内置的 `js-yaml 4.1.0` 使用 MIT License，详见
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。更新日志见
[CHANGELOG.md](CHANGELOG.md)。
