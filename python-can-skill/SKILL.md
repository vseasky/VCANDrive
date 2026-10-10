---
name: python-can-skill
description: 帮助用户在 Windows、Linux 或 macOS 使用 VCANDrive 的 python-can USB 后端，完成设备定位、CAN/CAN FD 收发、DBC 和 CANopen 应用；Linux canX 应用使用 socket-can-skill。
---

# VCANDrive Python CAN

先按[快速入门](../udocs/01_快速入门.md)安装、定位通道并收发第一帧。常见权限、占用和超时错误见[常见问题](../udocs/02_常见问题.md)。

## 选择路径

| 当前设备模式 | USB ID | `can.Bus` 的 `interface` |
|---|---|---|
| VCAN | `1d50:6080` | `vcan_usb` |
| GS_CAN | `1d50:606f` | `vkgs_usb` |

以下示例以 VCAN 为主；GS_CAN 用户将 `vcan_usb` 改为 `vkgs_usb`。PCAN 模式先看[设备管理器](../udocs/03_设备管理器.md)。`vcan_usb` 是本设备的 USB 后端，不是 Linux 的虚拟 `vcan0`。

- 原始 CAN/CAN FD 收发、设备发现、身份和扩展 API：看 [Python API 使用参考](references/vcandrive-python-api.md) 与 [用户 API 参考](../udocs/07_PythonAPI参考.md)。
- DBC 信号编解码：看 [DBC 工作流](references/dbc-workflow.md)。
- CANopen 对象字典、NMT、PDO、SDO：看 [CANopen 工作流](references/canopen-workflow.md)。
- Linux `canX`、`candump` 或 PF_CAN：改用 `socket-can-skill`。

## 使用时核对

- 一块硬件用完整 UID/UUID 识别，具体通道用 `uuid_hex:channel`；`can0`、USB 地址和发现序号可能变化。多板卡先用 `list`、`info` 核对，再选目标设备。
- 一个 `Bus` 对应一个通道；用 `with can.Bus(...)` 或 `shutdown()` 关闭。同一 USB 通道不能同时被 Python 和 SocketCAN 驱动占用。
- 发送 CAN FD 前在 Bus 上设置 `fd=True`，报文设置 `is_fd=True`；需要 BRS 时再设置 `bitrate_switch=True`。
- 发送超时后结果可能不确定；业务协议未提供去重机制时，不要自动重发。
- 切换 USB 模式会重启设备、使旧句柄失效；先关闭正在使用的通道，切换后重新查询 UID/UUID。

完成应用代码后，先按[API 验证](../udocs/06_API验证.md)核对收发，再按[验收路线](../udocs/10_验证路线.md)运行与当前主机路径对应的测试。
