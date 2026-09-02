# 文档导航（VCANDrive / 04.VCANDrive）

## 你在找什么？

- 你使用的是 **Python 后端**：先看 [PythonCAN 使用手册](PythonCAN使用手册.md)
- 你使用的是 **Linux SocketCAN**：先看 [SocketCAN 使用手册](SocketCAN使用手册.md)
- 你要看 **USB 厂商控制请求 / 帧格式**：看 [驱动特殊 API 说明](驱动特殊API说明.md)
- 你想了解历史优化：看 [优化记录](优化记录.md)

## 支持矩阵

| 模式 | USB ID | Linux 内核驱动 | Python 后端 |
|---|---|---|---|
| VCAN | `1d50:6080` | `vcan_usb` | `vcan_usb` |
| VKGS/gs_usb 扩展 | `1d50:606f` | `vkgs_usb` | `vkgs_usb` |

> 固件、内核、Python 后端三者必须匹配同一 USB 模式。建议先确认 `lsusb` / `ip link` 或
> `canctl list` 输出中的 VID:PID 与你准备使用的接口名一致。

## Linux + SocketCAN 用户

- [SocketCAN 使用手册](SocketCAN使用手册.md)：内核模块编译加载、`candump`/`cansend`、
  `ip link` 参数、诊断命令、C API 片段。
- [驱动特殊 API 说明](驱动特殊API说明.md)：两种协议下可用的扩展能力、`CAN`
  用户态特性与厂商控制请求。

## Python 用户

- [PythonCAN 使用手册](PythonCAN使用手册.md)：安装、权限/绑定、并发、FD、
  多通道、常见错误、验收流程。
- [Python API 参考](PythonAPI参考.md)：`can.Bus` 参数、`switch_usb_mode`、
  生命周期、异常、状态与多通道约束。
- [Python 终端工具](Python终端工具.md)：命令行快速验收、统计、`identify`、
  USB 协议模式切换。

## 目录与代码位置

- Linux 内核驱动源码：`../kernel/vcan_usb/`、`../kernel/vkgs_usb/`
- Python 后端源码与测试：`../python-cli/vcan_usb/`、`../python-cli/vkgs_usb/`、
  `../python-cli/tests/`
- 固件参考与专有协议示例：`../ref/README.md`、`../ref/...`

文档的目标是帮助你快速启动与稳定运行；示例命令默认按 `vkgs_usb` 为主，
`vcan_usb` 只需替换 `--interface` 与安装包名。
