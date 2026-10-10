---
name: socket-can-skill
description: 帮助用户在 Linux 使用 VCANDrive SocketCAN canX 接口，完成驱动安装、通道定位、can-utils 或 PF_CAN 收发，以及 DBC 和 CANopen 应用；直接 USB 的跨平台 Python 应用使用 python-can-skill。
---

# VCANDrive SocketCAN

先按[Linux 驱动安装](../udocs/08_Linux内核驱动安装.md)确认设备绑定，再按[SocketCAN 使用手册](../udocs/09_SocketCAN使用手册.md)收发第一帧。

## 选择路径

| 当前设备模式 | USB ID | Linux 模块 |
|---|---|---|
| VCAN | `1d50:6080` | `vcan_usb` |
| GS_CAN | `1d50:606f` | `vkgs_usb` |

以下示例以 VCAN 为主；GS_CAN 用户替换为 `vkgs_usb`。PCAN 模式先看[设备管理器](../udocs/03_设备管理器.md)。GS_CAN 设备若被系统 `gs_usb` 抢先绑定，按安装文档检查实际驱动。

- 安装、接口配置、终端和 can-utils：看 [Linux 使用参考](references/vcandrive-linux.md)。
- C/C++ PF_CAN socket、过滤、CAN FD 和错误帧：看 [SocketCAN API](references/socketcan-api.md)。
- DBC 与 CANopen 应用：看 [CANopen/DBC 参考](references/canopen-dbc.md)。
- Windows、macOS 或 Linux 上直接打开 USB 的 Python 应用：改用 `python-can-skill`。

## 使用时核对

- `can0`、`can1` 是动态分配的网络接口名。用[身份查询](../udocs/09_SocketCAN使用手册.md#定位唯一板卡和通道)把 UUID 与板内通道号对应起来，再选应用所用的 `canX`。
- 先在接口关闭时配置位率、FD 模式和终端，再启用接口；应用打开 PF_CAN socket 不能代替这些设置。
- CAN FD raw socket 要启用 `CAN_RAW_FD_FRAMES`，接收时按返回长度区分经典帧和 FD 帧。
- Python USB 后端与内核驱动不能同时占用同一 USB 通道。切换路径时先关闭现有程序，再按[常见问题](../udocs/02_常见问题.md)处理绑定。
- 测试脚本会发送真实流量并重配接口，只在指定的测试总线上运行。先确认第一帧，再按[验收路线](../udocs/10_验证路线.md)做完整测试。
