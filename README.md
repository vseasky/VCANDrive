# VCANDrive

VCANDrive 是 HPMicro 双通道 USB-CAN FD 设备的开源主机端开发仓库，提供 Linux SocketCAN 内核驱动、跨平台 `python-can` USB 后端、命令行工具、自动验收脚本，以及面向 API 项目开发的可复用技能文档。

- Linux：把设备注册为标准 `canX` 网络接口，兼容 `iproute2`、`can-utils` 和 `PF_CAN`。
- Windows / Linux / macOS：通过 `vcan_usb` 或 `vkgs_usb` 后端接入 `python-can`。
- 应用层：提供 CAN/CAN FD、CANopen、DBC 编解码与工程化测试指引。
- 设备能力：双通道、经典 CAN、CAN FD/BRS、可切换 120 Ω 终端、电气/总线状态诊断。

本仓库不包含固件镜像。协议、USB 描述符或硬件行为需要交叉核对时，以同级 `../03.FirmWare/Application/vcan/` 的当前固件实现为准。

## 支持矩阵

同一硬件可运行不同 USB personality；当前 USB ID 必须与驱动、Python 包和接口名匹配。

| 设备模式 | USB VID:PID | Linux 模块 | Python 包 / interface | 本仓库支持 |
|---|---|---|---|---|
| VCAN 原生 | `1d50:6080` | `vcan_usb` | `vcan-usb` / `vcan_usb` | 是 |
| GS_USB / VKGS | `1d50:606f` | `vkgs_usb` | `vkgs-usb` / `vkgs_usb` | 是 |
| PEAK 兼容 | 随硬件型号而定 | 使用 PEAK 兼容驱动 | 未提供 | 仅支持从工具切换设备模式 |

Linux 内核可能用主线 `gs_usb` 抢先绑定 `1d50:606f`。使用本仓库的 `vkgs_usb` 前，应先确认实际绑定关系；不要在没有检查其他 gs_usb 设备的情况下直接永久屏蔽模块。

## 从这里开始

### Linux SocketCAN

```bash
sudo apt install build-essential linux-headers-$(uname -r) can-utils
cd kernel/vcan_usb                 # 1d50:6080；606f 改为 kernel/vkgs_usb
make
sudo make install
sudo depmod -a
sudo modprobe vcan_usb

sudo ip link set can0 down
sudo ip link set can0 type can bitrate 500000 restart-ms 100
sudo ip link set can0 up
candump can0
```

完整流程见 [Linux 内核驱动安装](docs/Linux内核驱动安装.md) 和 [SocketCAN 使用手册](docs/SocketCAN使用手册.md)。

### Python API

```bash
cd python-cli
python -m venv .venv
.venv/bin/python -m pip install -e ./vkgs_usb   # 606f
# 6080 使用：.venv/bin/python -m pip install -e ./vcan_usb
sudo ./canctl --interface vkgs_usb list
```

```python
import can

with can.Bus(
    interface="vkgs_usb",
    channel=0,
    bitrate=500_000,
) as bus:
    bus.send(can.Message(
        arbitration_id=0x123,
        is_extended_id=False,
        data=b"\x11\x22\x33\x44",
    ))
```

Windows 将虚拟环境解释器改为 `.venv\Scripts\python.exe`，并按 `MI_xx` 把目标 USB interface 绑定到 WinUSB。完整流程见 [PythonCAN 使用手册](docs/PythonCAN使用手册.md) 和 [Python API 参考](docs/PythonAPI参考.md)。

### CANopen 与 DBC

VCANDrive 负责 CAN 帧传输；CANopen/DBC 属于应用层：

- CANopen 使用 EDS/DCF 对象字典和成熟协议栈处理 NMT、PDO、SDO、EMCY、SYNC、heartbeat。
- DBC 使用 `cantools` 或生成的 C codec 处理信号字节序、缩放、枚举和 multiplex。

快速示例、边界和测试方法见 [CANopen 与 DBC 开发指南](docs/CANopen与DBC开发指南.md)。

## 面向项目开发的 Skills

仓库根目录提供两个独立技能包，可直接查阅，也可加入支持 Skills 的开发环境：

| Skill | 适用场景 | 主要参考 |
|---|---|---|
| [`python-can-skill`](python-can-skill/SKILL.md) | VCANDrive Python USB API、CAN/CAN FD、DBC、CANopen | Bus 生命周期、设备定位、异常、编解码与测试 |
| [`socket-can-skill`](socket-can-skill/SKILL.md) | Linux `canX`、C/C++ `PF_CAN`、can-utils、DBC、CANopen | 驱动接入、raw socket、过滤、错误帧与协议集成 |

两个技能按需加载各自 `references/`，避免把 USB backend 的 `channel=0` 与 SocketCAN 的 `can0` 混用。

## 关键术语

- `channel`：Python backend 使用的 USB `bInterfaceNumber`，不是 Linux 的 `can0/can1`。
- `canX`：Linux 动态分配的 SocketCAN 网络接口名，不保证等于面板通道号。
- `interface`（Python）：`vcan_usb` 或 `vkgs_usb`，用于选择协议 backend。
- `bus/address/port_path`：USB 设备定位信息；多设备场景优先使用较稳定的 `port_path`。
- `index`：相同 VID/PID 设备的临时枚举序号，设备重枚举后可能变化。
- `MI_xx`：Windows 复合 USB 设备中的独立 WinUSB interface，与 channel 一一对应。

## 使用边界

- 同一 USB interface 同时只能由一种后端占用：SocketCAN 内核模块或 Python USB backend 二选一。
- USB 模式切换会写 Flash、重启设备、改变 USB ID 并使旧句柄失效。
- CAN 发送超时后的结果可能不确定；除非上层协议有幂等/去重机制，否则不要盲目自动重发。
- CAN 总线必须有正确接线、统一位时序和两个物理末端终端；只有一个活动节点时通常收不到 ACK。
- `cangen`、终端切换和硬件验收脚本会改变真实总线状态，只在明确隔离的测试网络执行。

## 文档导航

- [文档总览](docs/README.md)
- [项目说明](docs/项目说明.md)
- [Linux 内核驱动安装](docs/Linux内核驱动安装.md)
- [SocketCAN 使用手册](docs/SocketCAN使用手册.md)
- [PythonCAN 使用手册](docs/PythonCAN使用手册.md)
- [Python API 参考](docs/PythonAPI参考.md)
- [Python 终端工具](docs/Python终端工具.md)
- [CANopen 与 DBC 开发指南](docs/CANopen与DBC开发指南.md)
- [驱动特殊 API 说明](docs/驱动特殊API说明.md)
- [优化记录](docs/优化记录.md)

## 仓库结构

```text
VCANDrive/
├── kernel/                 Linux vcan_usb/vkgs_usb 驱动与硬件验收脚本
├── python-cli/             python-can backends、CLI 与测试
├── docs/                   安装、API、协议与排障文档
├── python-can-skill/       Python CAN 项目开发技能
├── socket-can-skill/       Linux SocketCAN 项目开发技能
├── LICENSE                 GNU GPL v2 全文
└── README.md
```

## 许可证与镜像

本项目采用 [GNU General Public License v2.0 only](LICENSE)，与 Linux 驱动文件中的 `SPDX-License-Identifier: GPL-2.0` 一致。第三方依赖继续遵循各自许可证。

- GitHub：<https://github.com/vseasky/VCANDrive>
- Gitee：<https://gitee.com/vseasky/VCANDrive>
