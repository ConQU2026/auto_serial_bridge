# Auto Serial Bridge

![ROS2](https://img.shields.io/badge/ROS2-Humble-blue)
![Ubuntu](https://img.shields.io/badge/Ubuntu-22.04-orange)
![License](https://img.shields.io/badge/License-Apache%202.0-green)

## 项目简介

**Auto Serial Bridge** 是一个用于连接 ROS 2 与嵌入式下位机的配置驱动串口桥。

相比在 ROS 端和 MCU 端分别手写一套协议，本项目只需要维护
`config/protocol.yaml`：重新编译后，构建系统会同步生成 ROS 2 消息绑定、MCU C
代码和通信协议文档。

- **配置即协议**：在 YAML 中定义消息 ID、方向、字段和 ROS 话题。
- **自动生成两端代码**：ROS 端无需手写每种消息的序列化与反序列化代码，MCU
  端可直接集成生成的 `protocol.h` 和 `protocol.c`。
- **连接状态可控**：支持协议哈希握手、心跳检测、串口热插拔和自动重连。
- **按需可靠传输**：指定消息可启用 ACK、序列号和超时重传。
- **协议文档同步生成**：下位机开发人员可直接查看生成的
  `PROTOCOL_DOC.md`，确认帧格式、消息 ID 和字段偏移。

仓库的默认配置只启用 Ack、Heartbeat 和 Handshake 三条框架系统消息。文件末尾还
保留了三条已注释的 Demo 业务消息，方便学习配置格式；正式使用时可以直接删除这些
注释并加入自己的消息。

## 快速开始

下面的命令假设工作空间为 `~/ros2_ws`。如果你的工作空间路径不同，请替换为实际
路径。

### 1. 安装依赖

本项目面向 ROS 2 Humble，并在 Ubuntu 22.04 上开发和测试。

```bash
sudo apt update
sudo apt install python3-colcon-common-extensions python3-rosdep
```

如果系统尚未初始化 `rosdep`，先执行一次：

```bash
sudo rosdep init
```

把仓库克隆到工作空间后安装依赖：

```bash
mkdir -p ~/ros2_ws/src
cd ~/ros2_ws/src
git clone <repository-url> auto_serial_bridge
cd ~/ros2_ws
source /opt/ros/humble/setup.bash
rosdep update
rosdep install --from-paths src/auto_serial_bridge --ignore-src -r -y
```

### 2. 配置串口权限（可选）

可以使用项目提供的脚本，根据当前串口设备生成固定的 udev 别名：

```bash
cd ~/ros2_ws/src/auto_serial_bridge/scripts
sudo ./auto_udev.sh
```

脚本会询问当前设备路径和期望的设备别名，并生成
`MODE="0660"`、`GROUP="dialout"` 的 udev 规则。规则生成后重新插拔设备。

如果设备没有硬件序列号，脚本只能使用 VID/PID 匹配。连接多个相同设备时可能发生
别名冲突，此时应根据实际 USB 端口编写更具体的 udev 规则。

### 3. 修改通信协议

核心配置文件是：

```text
src/auto_serial_bridge/config/protocol.yaml
```

仓库已经跟踪 `config/protocol.yaml`，全新 clone 不需要复制配置。默认文件可以直接
构建；由于业务示例已被注释，默认生成物只包含三条系统消息。

正式使用前至少确认以下内容与下位机设计一致：

- `port` 和 `baudrate`：ROS 端串口路径与波特率。
- `head_byte_1`、`head_byte_2`：帧头字节。
- `checksum`：校验算法。
- `require_handshake`：是否必须完成协议哈希握手。
- `messages`：消息 ID、方向、ROS 话题、消息类型和字段映射。

修改 `config/protocol.yaml` 后必须重新执行 `colcon build --packages-select
auto_serial_bridge`。launch 读取的是安装目录中的配置，未重新编译时仍会使用旧版本；
ROS 绑定、MCU C 代码和协议哈希也不会自动更新。

### 4. 编译项目

在工作空间根目录执行：

```bash
cd ~/ros2_ws
source /opt/ros/humble/setup.bash
colcon build --packages-select auto_serial_bridge
source install/setup.bash
```

只运行节点、不编译测试时，可以使用：

```bash
colcon build --packages-select auto_serial_bridge --cmake-args -DBUILD_TESTING=OFF
```

如果之前使用 `-DBUILD_TESTING=OFF` 构建，运行测试前需要显式重新启用：

```bash
colcon build --packages-select auto_serial_bridge --cmake-args -DBUILD_TESTING=ON
```

### 5. 取得下位机文件

编译成功后，下位机需要的真实文件位于：

```text
build/auto_serial_bridge/generated/
├── protocol.h
├── protocol.c
└── PROTOCOL_DOC.md
```

把这三个文件复制到下位机工程：

```bash
cp build/auto_serial_bridge/generated/protocol.h /path/to/mcu-project/
cp build/auto_serial_bridge/generated/protocol.c /path/to/mcu-project/
cp build/auto_serial_bridge/generated/PROTOCOL_DOC.md /path/to/mcu-project/
```

安装目录中也提供同样的 MCU 产物：

```text
install/auto_serial_bridge/share/auto_serial_bridge/mcu_output/
```

使用 `colcon build --symlink-install` 时，安装目录中的文件可能是指向 `build` 目录的
符号链接。需要复制整个 `mcu_output` 目录时，应跟随链接：

```bash
cp -Lr install/auto_serial_bridge/share/auto_serial_bridge/mcu_output \
  /path/to/mcu-project/
```

> [!IMPORTANT]
> 当前版本不会在 `src/auto_serial_bridge/mcu_output/` 中生成文件。源码目录只保存
> 人工维护的输入，避免构建过程修改源码或误提交生成物。真实生成文件位于 `build`，
> 可交付副本同时安装到 `install`。

### 6. 启动串口桥

推荐通过 launch 启动普通节点：

```bash
source ~/ros2_ws/install/setup.bash
ros2 launch auto_serial_bridge serial_bridge_by_node.launch.py
```

临时覆盖串口路径并打开 debug 日志：

```bash
ros2 launch auto_serial_bridge serial_bridge_by_node.launch.py \
  port:=/dev/ttyUSB0 log_level:=debug
```

也可以将串口桥作为 ROS 2 component 启动：

```bash
ros2 launch auto_serial_bridge serial_bridge_by_component.launch.py
ros2 launch auto_serial_bridge serial_bridge_by_component.launch.py port:=/dev/ttyUSB0
```

或者直接运行节点：

```bash
ros2 run auto_serial_bridge serial_node --ros-args -p port:=/dev/ttyUSB0
```

## 配置 protocol.yaml

`protocol.yaml` 包含四个顶层部分，均需保留：

```text
serial_controller   ROS 2 节点运行参数
config              线协议和传输行为
type_mappings       YAML 类型到定长 C 类型的映射
messages            系统消息和业务消息
```

### 串口运行参数

```yaml
serial_controller:
  ros__parameters:
    port: "/dev/ttyACM0"
    baudrate: 115200
    debug_raw_frame: false
```

- `port`：默认串口设备，可通过 launch 的 `port:=...` 临时覆盖。
- `baudrate`：串口波特率，也是当前配置中唯一允许定义波特率的位置。
- `debug_raw_frame`：配合 debug 日志输出原始收发帧。

### 全局协议配置

```yaml
config:
  buffer_size: 1024
  head_byte_1: 0x5A
  head_byte_2: 0xA5
  checksum: "CRC8"
  require_handshake: true
  ignore_version_mismatch: false
  enable_heartbeat: true
  strict_heartbeat: true
  heartbeat_timeout_ms: 3000
  reliable_retry_interval_ms: 100
  reliable_max_retries: 3
  qos_depth: 10
```

- `checksum` 支持 `NONE`、`SUM8`、`XOR8` 和 `CRC8`。
- `buffer_size` 必须能够容纳最大完整帧。
- `ignore_version_mismatch=false` 时，只有协议哈希一致才能完成握手。
- `strict_heartbeat=true` 时，心跳响应超时会断开串口并进入重连流程。
- `reliable_retry_interval_ms` 和 `reliable_max_retries` 只作用于
  `reliable: true` 的消息。

这些配置会参与代码生成，其中多数不能通过运行时 ROS 参数覆盖。修改后必须重新
编译，并把本次构建产生的 MCU 文件同步给下位机。

### 业务消息

默认配置末尾的 `DemoCommand`、`DemoReliableCommand` 和 `DemoTelemetry` 整块都已
注释，因此它们不参与代码生成、编译或协议哈希。要试用示例，需要取消所选消息块
每一行开头的 `#`；正式项目可以删除全部 Demo 注释并加入自己的消息，例如：

```yaml
messages:
  # 上方 Ack、Heartbeat、Handshake 保持不变
  - name: "MotorCommand"
    id: 0x10
    direction: "tx"
    reliable: true
    debug_log_mode: "off"
    sub_topic: "motor/command"
    ros_msg: "std_msgs/msg/UInt32"
    fields:
      - { proto: "value", type: "u32", ros: "data" }
```

- `name`：合法的 C 标识符，生成 `Packet_<name>`。
- `id`：0 至 255 的唯一消息 ID。
- `direction`：`tx` 表示 ROS -> MCU，`rx` 表示 MCU -> ROS，`both` 表示双向。
- `sub_topic`：`tx` 或 `both` 消息订阅的 ROS 话题。
- `pub_topic`：`rx` 或 `both` 消息发布的 ROS 话题。
- `ros_msg`：ROS 消息类型，格式为 `package/msg/Type`。
- `fields`：按照线协议顺序定义协议字段、C 类型别名和 ROS 字段路径。
- `reliable`：为 `true` 时启用 ACK 和超时重传，只允许用于 `tx` 或 `both`。
- `debug_log_mode`：控制该消息是否生成逐消息 debug 日志，可选 `on` 或 `off`。
- `notes`：写入生成的协议文档，不参与协议哈希。

代码生成器当前支持的定长 C 类型为 `uint8_t`、`uint16_t`、`uint32_t`、
`int32_t` 和 `float`。payload 长度字段为 1 字节；可靠消息追加序列号后也不能超过
255 字节。

Ack、Heartbeat 和 Handshake 是框架系统消息，必须保留其名称、ID、方向、字段类型
和字段顺序：

| 系统消息 | 固定 ID | 用途 |
| :--- | :---: | :--- |
| Ack | `0xFD` | 确认可可靠消息及序列号 |
| Heartbeat | `0xFE` | 检测串口连接是否仍然可用 |
| Handshake | `0xFF` | 交换并校验协议哈希 |

### 使用其他 ROS 消息包

`ros_msg` 所属的 ROS 包必须能被当前 workspace 或 underlay 找到。代码生成器会提取
包名，CMake 会自动执行对应的 `find_package()`，无需手动修改本包的
`CMakeLists.txt`。

如果使用 `sensor_msgs/msg/Imu`，应先安装 `ros-humble-sensor-msgs`，或在同一
workspace 中构建提供该消息的包。为了让 `rosdep` 和其他用户也能安装依赖，还应在
本包的 `package.xml` 中加入实际使用的依赖：

```xml
<depend>sensor_msgs</depend>
```

## MCU 端集成

下位机工程至少需要集成：

```text
protocol.h    协议结构体、常量和函数声明
protocol.c    打包、解包、分发和系统消息默认处理
```

建议同时把 `PROTOCOL_DOC.md` 交给下位机开发人员，用于核对当前协议哈希、帧格式、
消息方向和字段偏移。

基本集成步骤：

1. 将本次构建生成的 `protocol.h` 和 `protocol.c` 复制到 MCU 工程。
2. 实现 `void serial_write(const uint8_t *data, uint16_t len)`，并连接到 MCU 的
   UART 驱动。
3. 将 UART 收到的每个字节依次传给 `protocol_fsm_feed(uint8_t byte)`。
4. 在对应的 `on_receive_<Message>()` 回调中处理 ROS 发来的业务消息。
5. 使用生成的 `send_<Message>()` 接口向 ROS 发送配置为 `rx` 或 `both` 的消息。
6. 每次修改 `protocol.yaml` 后重新编译，并重新同步 MCU 文件；不要混用不同协议
   哈希的 ROS 和 MCU 代码。

生成代码包含 `/* USER CODE BEGIN */` 和 `/* USER CODE END */` 区块。在同一个构建
目录中重新生成时，生成器会保留这些区块，但 `build` 目录本身可以被清理。长期维护
的 MCU 业务代码应放在下位机工程中，不应只保存在 ROS 工作空间的 `build` 目录。

### 握手与心跳

公共默认配置要求握手，并且不允许忽略协议版本不一致：

- ROS 打开串口后先进入等待握手状态。
- MCU 返回相同的 `PROTOCOL_HASH` 后，ROS 才进入正常收发状态。
- 生成的 MCU `on_receive_Handshake()` 会在哈希匹配时自动回传握手包。
- 如果两端哈希不一致，应重新同步同一次构建产生的 MCU 文件。

默认配置还启用严格心跳：

- ROS 周期发送 Heartbeat。
- 生成的 MCU `on_receive_Heartbeat()` 会自动回传相同的 `count`。
- 超过 `heartbeat_timeout_ms` 没有收到正确响应时，ROS 会断开串口并自动重连。

### 可靠消息

当业务消息设置 `reliable: true` 时，ROS 会在业务 payload 末尾透明追加 1 字节
序列号。该字节参与 `Len` 和校验，但不属于 YAML 中声明的业务结构体字段。生成的
MCU 分发逻辑会自动发送 Ack，ROS 在超时后按配置重传。

## 运行状态与日志

节点在相对话题 `auto_serial_bridge/ready` 发布 `std_msgs/msg/Bool`。无 namespace
时，完整话题为 `/auto_serial_bridge/ready`。QoS 为 depth 1、reliable、transient
local，后启动的订阅者也能立即取得最近状态。

- 节点启动、端口关闭或连接重置时发布 `false`。
- 串口打开并完成握手后发布 `true`。
- 禁用握手时，串口打开并进入 RUNNING 后发布 `true`。
- 严格心跳超时或串口读取错误时重新发布 `false`。
- 状态不变时不会重复发布。

普通节点可以通过 launch 参数开启 debug 日志：

```bash
ros2 launch auto_serial_bridge serial_bridge_by_node.launch.py log_level:=debug
```

debug 日志会显示握手模式、心跳模式、协议哈希对比和被过滤的数据包。
`debug_raw_frame=true` 时还会输出允许记录的原始收发帧；每种消息可通过
`debug_log_mode` 控制是否生成逐消息日志。

## Web 协议编辑器

直接用浏览器打开源码目录中的：

```text
src/auto_serial_bridge/web/index.html
```

编辑器可修改协议并导出 YAML。Ack、Heartbeat 和 Handshake 系统消息固定，波特率
与 `serial_controller.ros__parameters.baudrate` 保持一致。导出的文件应替换
`config/protocol.yaml`，随后重新编译并同步 MCU 文件。

## 测试

### 包级测试

```bash
cd ~/ros2_ws
source /opt/ros/humble/setup.bash
colcon build --packages-select auto_serial_bridge --cmake-args -DBUILD_TESTING=ON
source install/setup.bash
colcon test --packages-select auto_serial_bridge --event-handlers console_direct+
colcon test-result --verbose
```

测试使用独立协议 fixture 覆盖普通 tx、rx 和可靠消息。删除默认 Demo 注释，或把业务
消息替换为自己的有效协议，不会因为测试仍引用示例名称而失败。

### 虚拟串口回归

`test/test_scocat.py` 使用 `socat` 创建虚拟串口，覆盖分片、粘包、全双工并发、大包、
端口重开、短突发和控制字节透传：

```bash
cd ~/ros2_ws/src/auto_serial_bridge
python3 -m pytest -q test/test_scocat.py
```

长时间链路恢复用例默认关闭，需要时显式启用：

```bash
AUTO_SERIAL_BRIDGE_RUN_LONG_SOAK=1 \
  python3 -m pytest -q test/test_scocat.py -k long_run
```

### ROS 节点 PTY 端到端测试

PTY launch 测试默认关闭，避免日常测试启动额外 ROS 进程。需要验证系统握手、心跳
和重连时执行：

```bash
cd ~/ros2_ws/src/auto_serial_bridge
AUTO_SERIAL_BRIDGE_RUN_PTY_INTEGRATION=1 \
  python3 -m pytest -q test/test_main.py
```

PTY 测试只依赖框架系统消息。业务消息的端到端行为应由使用该业务配置的上层项目
测试。

## 常见问题

### 修改配置后节点仍使用旧协议

重新构建包并重新 source 安装空间：

```bash
colcon build --packages-select auto_serial_bridge
source install/setup.bash
```

必要时确认当前 shell 中 `ros2 pkg prefix auto_serial_bridge` 指向期望的工作空间。

### MCU 找不到 mcu_output 文件夹

`mcu_output` 位于安装空间，不在源码目录。先确认本次编译成功，再检查：

```bash
ls build/auto_serial_bridge/generated/
ls install/auto_serial_bridge/share/auto_serial_bridge/mcu_output/
```

### 握手一直失败

确认 ROS 与 MCU 使用同一次构建生成的文件，并对比双方 `PROTOCOL_HASH`。不要把旧的
`protocol.c` 与新的 `protocol.h` 混用。

### 自定义消息包无法找到

先构建或安装提供该消息的 ROS 包，再重新 source 对应工作空间并构建本包。若希望
`rosdep` 自动安装，还需在 `package.xml` 中声明依赖。

## 项目结构

```text
auto_serial_bridge/
├── config/
│   └── protocol.yaml         # 串口、协议和 ROS 消息映射的唯一配置入口
├── include/auto_serial_bridge/
│   └── ...                   # 串口桥公共 C++ 头文件
├── launch/
│   ├── serial_bridge_by_node.launch.py
│   └── serial_bridge_by_component.launch.py
├── scripts/
│   ├── auto_udev.sh          # 串口 udev 别名生成工具
│   └── codegen.py            # ROS/MCU 协议代码生成器
├── src/                      # ROS 2 串口控制器实现
├── test/                     # C++、代码生成、PTY 和虚拟串口测试
└── web/                      # 离线协议编辑器

工作空间构建后：
build/auto_serial_bridge/generated/                    # 真实生成文件
install/auto_serial_bridge/share/auto_serial_bridge/
└── mcu_output/                                        # 安装后的 MCU 交付目录
```

## 协议约束

- 帧格式为双帧头、1 字节消息 ID、1 字节 payload 长度、payload 和 1 字节校验位。
- 线协议直接使用 packed C 结构体内存布局，要求两端均使用小端序、标准定长整数，
  并且 `float` 为 32 位 IEEE-754。
- 协议哈希包含帧头、校验算法、握手/心跳开关、消息 ID、方向、reliable 标志和按
  顺序解析后的字段 C 类型。
- 串口路径、波特率、ROS 话题、ROS 字段路径、QoS、日志、notes、超时时间和重传
  参数不参与协议哈希。
- 修改不参与哈希的参数后仍应重新编译，因为它们可能进入 ROS 运行配置、MCU 宏或
  生成的协议文档。

## 更新日志

请查看 [CHANGELOG.md](CHANGELOG.md)。

## 许可证

项目采用 Apache License 2.0。内置 `js-yaml 4.1.0` 使用 MIT License，详见
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
