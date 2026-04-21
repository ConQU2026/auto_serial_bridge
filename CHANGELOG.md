# CHANGELOG

本文件记录了 `auto_serial_bridge` 项目的所有显著更新及修改。

## 2026-04

* **2026-04-07** (`d042a67`): 将 heartbeat 默认改为 both，方便调试
* **2026-04-07** (`90c807b`): 调整心跳超时设置，增加日志级别参数，优化数据包调试输出

## 2026-03

* **2026-03-22** (`7c18677`): Merge pull request #5 from ConQU2026/dev (合并开发分支)
* **2026-03-22** (`7a1a354`): harden checksum matrix and launch config contracts (强化校验矩阵和 launch 配置约束)
* **2026-03-22** (`8a4b9de`): refactor: split public sample checks and heartbeat ack flow (重构: 分离公共示例检查和心跳 ACK 流程)
* **2026-03-21** (`e2a6894`): Add configurable checksum and harden serial bridge tests (添加可配置的校验和，并强化串口桥接测试)
* **2026-03-04** (`b8c8507`): Merge pull request #3 from ConQU2026/dev (合并开发分支)
* **2026-03-04** (`c129f38`): harden tests and gate packet hex logs behind debug (强化测试，并将数据包十六进制日志隐藏在 debug 之后)
* **2026-03-04** (`1a3aed5`): refactor(test): generalize bridge testing logic and update protocol definitions (重构: 泛化桥接测试逻辑并更新协议定义)
* **2026-03-04** (`451ab71`): feat: 部署 GitHub Actions 自动测试流并重组分支结构
* **2026-03-04** (`78ae08b`): fix(protocol): remove duplicate Handshake and Heartbeat definitions (修复: 移除重复的 Handshake 和 Heartbeat 定义)

## 2026-01

* **2026-01-26** (`b8a5946`): 优化: 检测到 config 变化才重新生成串口代码

## 2025-12

* **2025-12-26** (`b13d357`): 修复自动生成的电控代码逻辑, 修复已知bug, 完善 README 使用文档
* **2025-12-25** (`2a72a78`): 完善 README
* **2025-12-25** (`be3d21f`): 修复 test 部分 bug
* **2025-12-25** (`8a16fb8`): README 初始化
* **2025-12-25** (`e98b1e4`): 重构版本初次提交
* **2025-12-24** (`8e55301`): 备份
* **2025-12-23** (`ac1d459`): 优化数据包处理，减少不必要的拷贝并提升性能
* **2025-12-23** (`9ad0e37`): 完善代码注释
* **2025-12-21** (`665895b`): 增加热插拔支持
* **2025-12-21** (`3b9bc9e`): 优化性能, 增加调试信息显示 [DEBUG]
* **2025-12-20** (`2c5dbca`): 重命名包名为 auto_serial_bridge，更新相关文件以反映新命名
* **2025-12-20** (`899e1df`): 更新 README
* **2025-12-20** (`bcf9c68`): 新增自动设置 udev 脚本, 修正 README
* **2025-12-20** (`e5f750f`): 修复 launch 部分 bug
* **2025-12-20** (`f5001db`): 修复 iocontext 版本问题 bug, 新增 test_main.py & test_transmit_verify.cpp 两个关键测试代码
* **2025-12-19** (`b6c3c9f`): 串口 rx&tx 改为异步, 修复参数不统一问题, 完善 README 文档, TODO: 完善测试代码
* **2025-12-19** (`9cfc5b3`): 初次提交, 完成基础框架与功能, 等待测试和完善相关使用文档
