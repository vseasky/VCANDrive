# VCANDrive

VCANDrive 提供 VCAN 与 GS_CAN 两种 USB-CAN 模式的 Linux SocketCAN 驱动、Python CAN 后端和设备管理工具。PCAN 兼容模式可使用设备管理器查看身份并切换模式。

## 从这里开始

- **第一次使用**：[快速入门](udocs/快速入门.md)（接线、识别、安装、第一帧）
- **进阶验收**：[验证路线](udocs/验证路线.md)（功能、矩阵、压力、内核专项）
- [文档导航](udocs/README.md)
- [Linux 驱动安装](udocs/Linux内核驱动安装.md)与 [SocketCAN 使用](udocs/SocketCAN使用手册.md)
- [Python 安装与使用](udocs/PythonCAN使用手册.md)及 [API 参考](udocs/PythonAPI参考.md)
- [设备管理器](udocs/设备管理器.md)：查看模式、版本、UID 并切换模式
- [CANopen 与 DBC 开发指南](udocs/CANopen与DBC开发指南.md)

## 目录

| 目录 | 内容 |
|---|---|
| `kernel/` | Linux SocketCAN 驱动及测试 |
| `python-cli/` | Python 后端、命令行与测试 |
| `python-can-skill/` | Python CAN 应用开发参考 |
| `socket-can-skill/` | SocketCAN 应用开发参考 |
| `udocs/` | 面向用户的文档 |

仓库不包含设备固件、升级包、自动构建脚本或原始抓包。选择与设备当前 USB 模式对应的驱动或 Python 后端；同一 USB interface 不能同时由内核驱动和 Python 后端占用。
