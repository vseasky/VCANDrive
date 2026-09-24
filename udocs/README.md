# VCANDrive 文档导航

本目录面向驱动安装、API 开发、应用协议接入和故障定位。第一次使用时只选择一种主机端路径：Linux SocketCAN 或 Python USB backend。

## 按任务选择

| 目标 | 首先阅读 | 后续参考 |
|---|---|---|
| 理解仓库能力、边界和目录 | [项目说明](项目说明.md) | 根目录 [README](../README.md) |
| Linux 安装/更新驱动 | [Linux 内核驱动安装](Linux内核驱动安装.md) | [SocketCAN 使用手册](SocketCAN使用手册.md) |
| 使用 `candump`、`cansend` 或 C `PF_CAN` | [SocketCAN 使用手册](SocketCAN使用手册.md) | [`socket-can-skill`](../socket-can-skill/SKILL.md) |
| Windows/Linux/macOS Python 自动化 | [PythonCAN 使用手册](PythonCAN使用手册.md) | [Python API 参考](PythonAPI参考.md) |
| 查看设备身份并切换模式 | [设备管理器](设备管理器.md) | [PCAN 模式设备管理接口](PCAN设备管理.md) |
| 使用命令行验收设备 | [Python 终端工具](Python终端工具.md) | [PythonCAN 使用手册](PythonCAN使用手册.md) |
| 接入 CANopen 或 DBC | [CANopen 与 DBC 开发指南](CANopen与DBC开发指南.md) | 两个项目 Skills |
| 开发自定义 USB 上位机 | [驱动特殊 API 说明](驱动特殊API说明.md) | 固件协议实现 |

## 支持矩阵

| 模式 | USB ID | Linux 内核驱动 | Python 包 / backend |
|---|---|---|---|
| VCAN 原生 | `1d50:6080` | `vcan_usb` | `vcan-usb` / `vcan_usb` |
| GS_USB / VKGS | `1d50:606f` | `vkgs_usb` | `vkgs-usb` / `vkgs_usb` |
| PCAN 兼容 | 随设备配置而定 | 使用兼容驱动 | 使用[设备管理器](设备管理器.md)查看和切换模式 |

固件、内核模块和 Python backend 必须匹配同一 USB 模式。`1d50:606f` 还可能被 Linux 主线 `gs_usb` 抢先绑定。

## 术语映射

- `channel`：USB `bInterfaceNumber`，用于 Python 的 `channel=...` / CLI `--channel`。
- `canX`：Linux SocketCAN 网络接口名，由内核动态分配。
- `interface`（Python）：backend 选择码 `vcan_usb` 或 `vkgs_usb`。
- `bus/address/port_path`：USB 物理设备定位信息；优先按 `port_path` 固定多台同型号设备。
- `index`：同 VID/PID 下的枚举序号，只适合临时选择。
- `MI_xx`：Windows 的独立 USB interface 路径，与 channel 对应。

## 两种主机端路径不能并发占用

```text
VCAN 硬件 USB interface
├── Linux SocketCAN 模块 → canX → can-utils / PF_CAN / socketcan backend
└── Python USB backend   → vcan_usb 或 vkgs_usb → python-can API
```

同一 interface 只能选择其中一条。切换前先关闭应用、把 `canX` 设为 down，并释放前一种驱动/句柄。

## 应用层资料

- DBC 描述固定帧中的 signal，不配置 CAN 控制器，也不决定 BRS。
- CANopen 使用 EDS/DCF 对象字典，协议栈负责 NMT、SDO、PDO、EMCY、SYNC 与 heartbeat。
- SocketCAN error frame 与 CANopen EMCY 是两类不同事件；日志和处理策略应分开。

更细的可复用开发提示位于：

- [`python-can-skill/references`](../python-can-skill/references/)
- [`socket-can-skill/references`](../socket-can-skill/references/)

## 实现与参考来源

文档根据以下实现交叉核对：

- Linux 驱动：`../kernel/vcan_usb/`、`../kernel/vkgs_usb/`
- Python backend：`../python-cli/vcan_usb/`、`../python-cli/vkgs_usb/`
- CLI 与测试：`../python-cli/tools/`、`../python-cli/tests/`、`../kernel/tests/`

上述相对路径均以当前 `VCANDrive` 根目录为准。
