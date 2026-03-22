# Auto Serial Bridge

![ROS2](https://img.shields.io/badge/ROS2-Humble-blue)
![Ubuntu](https://img.shields.io/badge/Ubuntu-22.04-orange)
![License](https://img.shields.io/badge/License-Apache%202.0-green)

## 项目简介

**Auto Serial Bridge** 是一个致力于提升 ROS2 与嵌入式设备之间串口通信开发效率的工具。

鉴于 microROS 在某些场景下的配置繁琐与不稳定，本项目旨在提供一个**轻量级、自动化、可配置**的替代方案。只需在 `protocol.yaml` 配置文件中定义通信协议，即可自动生成 ROS2 端的 C++ 解析代码以及嵌入式端的 C 代码。
*   **高效开发**：无需花费太多时间在与电控的协议沟通上
*   **配置即代码**：通过 YAML 文件集中管理协议，修改协议只需改配置并重新编译
*   **无缝对接**：自动生成 ROS2 消息与嵌入式结构体，消除协议不一致带来的隐患。
*   **高性能**：基于 C++ 实现的高效串口通信与数据解包。


## 项目结构

```text
auto_serial_bridge/
├── config/              # 配置文件目录 
├── include/             # 头文件 (含自动生成的协议头文件)
├── launch/              # ROS2 Launch 启动文件
├── mcu_output/          # 自动生成的 MCU 端 C 代码 
├── scripts/             # 代码生成与辅助脚本
├── src/                 # 核心源代码
└── test/                # 单元测试
```

## 快速开始

### 1. 环境依赖

仅在`ROS2 Humble`进行过测试

```bash
sudo apt install ros-humble-serial-driver libasio-dev ros-humble-rclcpp-components ros-humble-io-context 
sudo apt install python3-serial socat
sudo apt install ros-humble-asio-cmake-module
```

### 2. 配置串口权限(可选)

使用本项目提供的脚本自动设置 udev 规则（只需运行一次）：

```bash
cd src/auto_serial_bridge/scripts
sudo ./auto_udev.sh
# 重新插拔串口设备后生效
```

### 2. 配置`protocol.yaml`文件

开源仓库不附带生产环境的 `config/protocol.yaml`。项目提供了一个名为 `config/protocol-sample.yaml` 的示例文件，但它不参与默认构建，也不是运行时 fallback。

`config/protocol-sample.yaml` 仅用于示例和公开自检；真正的本地/私有部署构建前，仍然需要你自行准备 `config/protocol.yaml`（该文件已被加入 `.gitignore`，不会被 Git 追踪，方便你在本地进行自定义配置）：

```bash
cp config/protocol-sample.yaml config/protocol.yaml
```

核心配置文件位于 `config/protocol.yaml`。可通过修改此文件来增删改数据协议，**修改后重新编译即可生效**。如果缺少该文件，真实 `colcon build` 会直接失败。修改 `config/protocol.yaml` 后必须重新 `colcon build`，否则 launch 读到的仍然是 install 目录里的旧值。

当前仓库的构建触发契约如下：

- 修改 `config/protocol.yaml` 后需要重新编译。
- 修改 `package.xml` 的 `<version>` 后，同样会触发重新构建流程。
- 两者都不变时，保留当前增量构建行为，不额外重跑代码生成。

#### 参数配置
```yaml
# ROS2 参数配置
serial_controller:
  ros__parameters:
    port: "/dev/stm32"
    baudrate: 115200
    timeout: 0.1

# 全局配置
config:
  baudrate: 115200
  buffer_size: 256
  head_byte_1: 0x5A      # 双帧头 1
  head_byte_2: 0xA5      # 双帧头 2
  checksum: "CRC8"       # 校验算法
  require_handshake: true
  enable_heartbeat: true # false = 不发送 Heartbeat, 也不做 ACK 断连检测
  qos_depth: 10
  heartbeat_timeout_ms: 3000 # 仅在 enable_heartbeat=true 时参与检测; 0 = 不检测
```

#### 消息定义
可以在 `messages` 列表中定义多个消息类型：

```yaml
messages:
  - name: "CmdVel"                 # 消息名称
    id: 0x04                       # 消息ID (唯一)
    direction: "tx"                # 传输方向: tx (ROS->MCU), rx (MCU->ROS), both(双向)
    sub_topic: "/cmd_vel"          # 订阅话题 (仅 tx/both 有效)
    ros_msg: "geometry_msgs/msg/Twist" # 对应的 ROS 消息类型
    fields:
      # proto: 协议字段名 | type: 数据类型 | ros: ROS消息字段映射路径
      - { proto: "linear_x",  type: "f32", ros: "linear.x" }
      - { proto: "angular_z", type: "f32", ros: "angular.z" }
```

### 3. MCU 端集成与协议文档

当你在 ROS2 端运行 `colcon build` 后，`mcu_output` 目录下会自动生成相关文件：

#### 协议文档 (PROTOCOL_DOC.md)
`mcu_output/PROTOCOL_DOC.md` 是**自动生成**的通信协议文档，它清晰地列出了：
*   所有消息的 ID、方向、数据结构定义。
*   每个字段的字节偏移量和 C 语言类型。
*   **这是提供给电控开发人员最直接的参考文档。**

#### 生成的 C 代码
*   `protocol.c`: 协议打包与解包实现。
*   `protocol.h`: 协议结构体与函数声明。

**电控开发人员集成步骤：**
1.  将 `mcu_output` 文件夹内容复制到单片机工程中。
2.  实现 `serial_write` 等底层发送接口。
3.  调用 `protocol.h` 中的 `pack_*` 和 `unpack_*` 接口进行数据收发。

> [!TIP]
> 代码生成器支持类似 CubeMX 的用户代码保护功能，在 `/* USER CODE BEGIN */` 和 `/* USER CODE END */` 之间的代码在重新生成时不会被覆盖。

> [!IMPORTANT]
> **握手要求**：当 `require_handshake=true` 时，生成的 MCU `protocol.c` 会在默认 `on_receive_Handshake()` 中对匹配 `PROTOCOL_HASH` 的握手包自动调用 `send_Handshake()` 回包；如需附加业务逻辑，可在对应 `USER CODE` 区块中追加。
>
> **心跳要求**：当 `enable_heartbeat=true` 时，生成的 MCU `protocol.c` 会在默认 `on_receive_Heartbeat()` 中自动调用 `send_Heartbeat(pkt)`，按原样回同一个 `count` 作为确认；电控不再需要自己实现固定 ACK 逻辑，也不应独立主动发送心跳。
>
> `enable_heartbeat=true` 且 `heartbeat_timeout_ms>0` 时，ROS 会发送心跳并在 ACK 超时后断开重连；
> `enable_heartbeat=true` 且 `heartbeat_timeout_ms=0` 时，ROS 仍发送心跳，但不会因 ACK 超时判定断连；
> `enable_heartbeat=false` 时，ROS 不发送心跳，也不做 ACK 断连检测。

### 4. 编译项目

在你的 ROS2 工作空间根目录下，且已经自行准备好私有 `config/protocol.yaml` 后：

```bash
colcon build --packages-select auto_serial_bridge
source install/setup.bash
```

## 5. 运行
在配置完yaml文件并且编译成功之后, 即可直接通过运行节点的方式启动, 也可以通过launch的方式启动(推荐)

我们提供了两种示例, 一种是使用节点的方式启动, 一种是使用组件化方式启动

## DEBUG说明
开启该节点的debug模式之后, 会打印当前接收到的十六进制数据包以及发送的十六进制数据包.

## 测试

本仓库区分两类验证：

1. 公开自检：用于保证示例协议和代码生成脚本没有损坏。
2. 私有构建/运行验证：依赖你自己的 `config/protocol.yaml`，只在本地或私有环境执行。

公开 CI 只校验 sample 和 codegen，不执行 `colcon build` 或 `colcon test`。

公开自检可直接运行：
```bash
tmpdir="$(mktemp -d)"
python3 scripts/codegen.py config/protocol-sample.yaml "$tmpdir"
python3 -m pytest -q test/test_codegen_checksum.py test/test_public_ci_contract.py
```

私有协议已经就绪时，仍可在本地执行完整包级测试：
```bash
colcon test --packages-select auto_serial_bridge --event-handlers console_direct+
```

## 更新日志

*   **v1.0**: 实现了基于 `protocol.yaml` 的全自动代码生成。
    *   **核心特性**：
        *   支持 `protocol.yaml` 配置变化检测，仅在配置更改时触发重新生成。
        *   自动生成面向电控的协议文档 `PROTOCOL_DOC.md`。
        *   支持 ROS 主动心跳确认（Heartbeat）与版本哈希握手（Handshake）。
        *   支持用户代码块保护，增量式开发更友好。
*   **Beta**: 初始验证版本。

## TODO
- 增加多个校验协议可选项
- 预设多个串口通信配置，用于适配不同场景（如：Easy 模式去掉握手和校验）
- 支持动态调参
