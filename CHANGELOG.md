# Changelog

## 2.0.0 - 2026-08-13

破坏性变更（需两端同步升级，协议哈希会变化）：

- 系统消息 Ack/Heartbeat/Handshake 由 codegen 内置注入，`protocol.yaml` 只写
  业务消息；YAML 中显式定义系统消息或占用 0xFD-0xFF 会被拒绝。
- 协议哈希改为按消息 ID 排序后计算，与 YAML 书写顺序无关。
- `reliable: true` 仅允许 `direction: tx`；`both` 方向不再支持同名收发话题，
  相应移除了 ROS 端 loopback 过滤机制。

修复：

- MCU 状态机 `WAIT_HEADER2` 重同步不再丢帧（如 `5A 5A A5` 序列），超长帧
  直接拒收并重新同步。
- 生成的 ROS 绑定支持 `std::array` 等定长容器字段，不再无条件调用 `resize()`。
- codegen 全部文件 I/O 显式 UTF-8；内容不变时不重写文件，避免无谓重编译。
- Web 编辑器：支持 `file://` 直接打开、多行 notes 正确序列化（改用 js-yaml
  dump）、校验规则与 codegen 对齐、不再静默修改系统消息属性。
- CMake 生成头文件路径不再依赖 colcon 构建目录命名；launch 增加 `baudrate`
  参数；`auto_udev.sh` 改用 `udevadm info -q property` 稳健解析设备信息。

改进：

- 接收环形缓冲容量取 2 的幂，发送路径消除双拷贝，RX 缓冲复用成员变量。
- 校验和实现合并为单个 `if constexpr` 函数，CRC8 表移入生成的 `.c` 文件。
- 校验错误清单化输出、信息更友好；新增保留 ID/名称、话题唯一性、payload
  与缓冲区大小等检查。
- Web 编辑器通过 GitHub Actions 自动发布到 GitHub Pages。
- 删除死板的字符串匹配测试，保留行为测试；README 重写精简。

## 1.0.0 - 2026-07-27

- 首个稳定公共版本。
- 提供可直接构建的中性 `config/protocol.yaml`。
- 构建时自动识别 YAML 引用的 ROS 消息包依赖。
- 生成文件迁移到构建目录，并安装公共头、MCU 源码和协议文档。
- 提供握手、协议哈希、严格心跳、可靠传输、热插拔重连和统一 ready 状态。
- 加强系统消息、标识符、类型、话题、长度和缓冲区验证。
- 协议哈希仅覆盖线协议及对端行为契约。
