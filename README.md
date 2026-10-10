# VCANDrive

VCANDrive 提供 VCAN 与 GS_CAN 模式的 Python USB 后端、Linux SocketCAN 驱动和设备管理资料。PCAN 兼容模式可用单独交付的 Windows 设备管理器查看设备信息、设置终端电阻和切换模式。

## 第一次使用

1. 按[快速入门](udocs/01_快速入门.md)接线、确认设备模式，选择 **跨平台 Python USB** 或 **Linux SocketCAN** 路线。
2. 用两个 CAN 通道完成一帧收发；发送成功还需在接收端看到相同 ID 和数据。
3. 按[API 验证](udocs/06_API验证.md)运行最小 Python 程序，或按[SocketCAN 手册](udocs/09_SocketCAN使用手册.md)使用标准 `canX` 接口。
4. 按[完整验收](udocs/10_验证路线.md)运行双通道、CAN FD 和压力测试。

设备处于 PCAN 模式时，先看[设备管理器](udocs/03_设备管理器.md)。完整的文档入口在[文档导航](udocs/00_文档目录.md)。

## 选用哪条主机端路径

| 目标 | 入口 | 设备模式 |
|---|---|---|
| Windows/Linux/macOS Python 程序直接操作 USB | [Python CAN 使用手册](udocs/05_PythonCAN使用手册.md) | VCAN 或 GS_CAN |
| Linux `candump`、`cansend`、`PF_CAN` 或 SocketCAN 应用 | [Linux 驱动安装](udocs/08_Linux内核驱动安装.md) | VCAN 或 GS_CAN |
| 查看信息、终端电阻、切换模式 | [设备管理器](udocs/03_设备管理器.md) | VCAN、GS_CAN 或 PCAN |

同一个 USB interface 不能同时被 Python 后端和 Linux 内核驱动占用。固件、升级包、设备管理器构建脚本和原始抓包不在本仓库中。

## 目录

| 目录 | 内容 |
|---|---|
| `python-cli/` | Python 后端、日常命令和硬件验收脚本 |
| `kernel/` | Linux SocketCAN 驱动和硬件验收脚本 |
| `udocs/` | 平铺的用户文档：入门、Python、Linux、验收和应用开发 |
| `docs/` | 内部开发资料：协议、实现、验证记录和历史归档 |
| `python-can-skill/`、`socket-can-skill/` | 应用开发参考 |
