# 文档导航

按实际操作顺序阅读即可：**接线与第一帧 → API 验证 → 完整验收 → 应用协议**。先确认设备当前是 VCAN、GS_CAN 还是 PCAN 模式，再选主机端路径。命令中的 `vcan_usb` 和 `vkgs_usb` 代表不同 USB 模式，不能混用。

| 阶段 | 要做什么 | 阅读 |
|---|---|---|
| 1. 第一次使用 | 接线、识别模式、安装、收发第一帧 | [快速入门](start/快速入门.md) |
| 2A. Python USB | 安装后端、用命令操作设备 | [Python CAN 使用手册](python/PythonCAN使用手册.md) · [终端工具](python/Python终端工具.md) |
| 2B. Linux SocketCAN | 安装匹配驱动、配置 `canX`、收发和诊断 | [驱动安装](linux/Linux内核驱动安装.md) · [SocketCAN 使用手册](linux/SocketCAN使用手册.md) |
| 3. 验证 API | 用实际双通道验证 `can.Bus`、`send()`、`recv()` 和 CAN FD | [Python API 验证](python/API验证.md) · [API 参考](python/PythonAPI参考.md) |
| 4. 完整验收 | 核对帧数、载荷、错误计数与高负载行为 | [验收测试路线](verify/验证路线.md) |
| 5. 应用开发 | DBC 信号或 CANopen 服务 | [CANopen 与 DBC](protocol/CANopen与DBC开发指南.md) |

## 设备与模式

- [Windows 设备管理器](start/设备管理器.md)：多台设备识别、版本/UID、每路终端电阻、模式切换。该程序单独交付，不在本仓库内。
- [项目说明](start/项目说明.md)：组件职责、USB 模式和能力边界。

## 深入参考

- [Python API 参考](python/PythonAPI参考.md)：参数、发现、生命周期和扩展方法；先完成 [API 验证](python/API验证.md) 再按需查阅。
- [驱动特殊 API](reference/驱动特殊API说明.md)：USB 控制请求与设备协议，适合编写自定义驱动或上位机。
- [PCAN 设备管理帧](reference/PCAN设备管理.md)：PCAN 模式的身份查询、终端电阻和切换命令；普通用户使用设备管理器即可。
- [`python-can-skill`](../python-can-skill/SKILL.md) 与 [`socket-can-skill`](../socket-can-skill/SKILL.md)：面向应用开发的可复用参考。

## 常见选择

| 你看到的情况 | 下一步 |
|---|---|
| Windows 上 `list` 没有通道 | 核对设备模式和每路 `MI_xx` 的 WinUSB；见[快速入门排查](start/快速入门.md#4-遇到问题先核对这些结果)。 |
| Linux 上没有 `canX` | 核对 USB ID、实际绑定驱动及 `gs_usb` 冲突；见[驱动安装](linux/Linux内核驱动安装.md)。 |
| `send()` 返回但接收端没帧 | 检查两路 CANH/CANL、位率、终端与 ACK；再按[API 验证](python/API验证.md)对比 ID/载荷。 |
| 第一帧成功，需要证明不丢包 | 运行[完整验收](verify/验证路线.md)，检查双向 TX/RX 和异常计数。 |

Python 的 `channel=0/1` 指 USB interface 号；Linux 的 `can0/can1` 是动态分配的网络接口名。DBC 负责信号解释，CANopen 栈负责协议服务，两者都不替代底层收发验收。
