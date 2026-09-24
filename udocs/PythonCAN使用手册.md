# PythonCAN 使用手册

使用本页前先按[快速入门](快速入门.md)确认设备模式、安装匹配的驱动并完成第一帧收发；需要完整验收时按[验收测试路线](验证路线.md)逐步运行。

第一次使用请先按[快速入门](快速入门.md)确认模式、安装驱动并完成第一帧收发。本文提供后续配置和开发说明，仓库包含两套独立的 `python-can` USB 后端：

| 固件模式 | USB ID | 安装包 | `can.Bus` 接口名 |
|---|---|---|---|
| VCAN | `1d50:6080` | `vcan-usb` | `vcan_usb` |
| VKGS/gs_usb 扩展 | `1d50:606f` | `vkgs-usb` | `vkgs_usb` |

只安装当前固件模式对应的 Python 包。查看设备身份或切换 VCAN、PCAN、GS_CAN 模式，请使用[设备管理器](设备管理器.md)。终端工具与测试脚本位于 `python-cli/tools/`
与 `python-cli/tests/`，它们不会随驱动包安装分发。

> 关键规则：同一 USB 接口不能同时被 `python-can` 后端和 SocketCAN 内核驱动占用。
> 需要 `candump` / `cansend` / `ip link` PF_CAN 场景时，请优先使用 SocketCAN 手册。

需要按 DBC signal 或 CANopen 对象字典开发时，先完成本文的 transport 收发，再阅读
《[CANopen 与 DBC 开发指南](CANopen与DBC开发指南.md)》或直接使用
[`python-can-skill`](../python-can-skill/SKILL.md)。

## 1. 快速开始

以下命令均在 `VCANDrive/python-cli/` 目录执行。

### 1.1 Linux

```bash
cd /path/to/VCANDrive/python-cli
python3 -m venv .venv-linux
.venv-linux/bin/python -m pip install --upgrade pip setuptools wheel
.venv-linux/bin/python -m pip install -e ./vkgs_usb
```

VCAN 固件时将最后一行改为 `-e ./vcan_usb`。

```bash
sudo ./canctl --interface vkgs_usb list
```

配置好 udev 之后通常可免 `sudo`。

### 1.2 Windows

```powershell
py --list
py -3 -m venv .venv-win
.\.venv-win\Scripts\python.exe -m pip install --upgrade pip setuptools wheel
.\.venv-win\Scripts\python.exe -m pip install -e .\vkgs_usb
```

VCAN 固件时将最后一行改为 `-e .\vcan_usb`。

```powershell
.\.venv-win\Scripts\python.exe .\tools\canctl.py --interface vkgs_usb list
```

若 `py --list` 为空，先确认 Python 3 已安装并带 `py` 启动器。若 `py -3.10`
这类版本未实际存在，会创建失败。

## 2. 平台准备

### 2.1 Linux：USB 与权限

先确认关闭可能占用的 SocketCAN 接口：

```bash
sudo ip link set can0 down 2>/dev/null || true
sudo ip link set can1 down 2>/dev/null || true
sudo modprobe -r vcan_usb 2>/dev/null || true
sudo modprobe -r vkgs_usb 2>/dev/null || true
sudo modprobe -r gs_usb 2>/dev/null || true
```

长期部署建议通过 udev 规则固定访问权（示例）：

```udev
SUBSYSTEM=="usb", ATTR{idVendor}=="1d50", ATTR{idProduct}=="6080", MODE="0660", GROUP="plugdev"
SUBSYSTEM=="usb", ATTR{idVendor}=="1d50", ATTR{idProduct}=="606f", MODE="0660", GROUP="plugdev"
```

保存为 `/etc/udev/rules.d/99-usbcan.rules`，重载后重插设备。

```bash
sudo udevadm control --reload-rules
sudo udevadm trigger
```

### 2.2 Windows：WinUSB 绑定

每个 CAN 通道对应一个独立 USB `MI_xx` interface（如 `MI_00`、`MI_01`），
每个接口有独立 bulk IN/OUT。绑定 WinUSB 时要按 `MI_xx` 级别操作，不要只绑定整机父设备。

`list` 返回 `USB device index 0 not found` 时按顺序检查：

1. VID:PID 是否与目标固件一致；
2. 当前固件模式是否匹配 `--interface`；
3. 每个目标 `MI_xx` 是否已绑定到 WinUSB；
4. 是否已有其他进程占用。

`WinError 5` 常见于 `MI_xx` 仍被占用；同一物理设备不同 MI_xx 可被不同进程并发打开。

### 2.3 macOS

安装并配置 libusb，确认当前用户有 USB 访问权限。macOS 不使用 SocketCAN，且不包含 Linux kernel module。

## 3. USB 与并发模型

一个 `Bus` 对应**一个 USB interface（一个通道）**，不是 Linux 的 `can0/can1`：

- 端点和 interface 由 USB 描述符发现，不写死端口；
- 每个 Bus 有独立 CAN 状态、接收队列和异步传输池；
- Linux/macOS：每个 Bus 拥有独立 libusb context、handle 与事件线程；
- Windows：每个 Bus 仅打开目标 `MI_xx` 对应的 WinUSB path；
- `channel` 打开时只 claim 一个目标 interface，`shutdown` 时释放；
- 正常打开不发送设备级 `SET_CONFIGURATION`；
- 发送失败不自动重试，以避免重复报文上真实总线。

#### vkgs_usb 发送节奏说明（固定固件约束）

`vkgs_usb` 固件按固定节拍处理 USB FIFO，`send()` 周边会出现约 5 ms + 10 ms 的
发送窗口等待，属于固件行为而非可调参数。
建议按通道规划发送节奏：
- 稳态发送上限约 **每通道 66 帧/秒**；
- 每次 `start()/stop()` 额外有约 150 ms 的过渡等待；
- 这会导致完整 `stop → start` 周期比想象慢，属于协议约束。

### 3.1 Linux/macOS 行为

允许不同进程 claim 同一设备的不同 interface（取决于系统策略与权限）。

### 3.2 Windows 行为

`libusb` 仅用于描述符发现；随后通过 SetupAPI 将 `bus + port_path + MI_xx`
映射到单独的 WinUSB 路径并打开。每个 Bus 独立持有 EP0 操作、bulk 管道、
接收池与 dispatcher。

实测边界：

| 场景 | 结果 |
|---|---|
| 一个进程打开同一设备 ch0/ch1 | 支持 |
| 两个进程分别打开不同物理设备 | 支持 |
| 两个进程分别打开同一设备 ch0/ch1 | 支持 |
| 两个进程同时打开同一 ch0 | 失败（第二个进程返回 `WinError 5`） |

## 4. Python 示例

### 4.1 经典 CAN

```python
import can

with can.Bus(
    interface="vkgs_usb",
    channel=0,
    bitrate=1_000_000,
    sample_point=75.0,
    termination=True,
) as bus:
    bus.send(
        can.Message(
            arbitration_id=0x123,
            is_extended_id=False,
            data=bytes.fromhex("11 22 33 44"),
        )
    )

    msg = bus.recv(timeout=1.0)
    if msg is not None:
        print(f"id={msg.arbitration_id:#x} data={msg.data.hex()}")
```

### 4.2 CAN FD

```python
import can

with can.Bus(
    interface="vkgs_usb",
    channel=0,
    bitrate=1_000_000,
    fd=True,
    data_bitrate=5_000_000,
) as bus:
    bus.send(can.Message(
        arbitration_id=0x321,
        is_extended_id=False,
        is_fd=True,
        bitrate_switch=True,
        data=bytes(range(64)),
    ))
```

`fd=True` 只表示控制器允许 FD；发送 FD 报文仍需显式设置 `message.is_fd=True`。
FD 数据长度限制为 `0-8,12,16,20,24,32,48,64`，其余长度会自动按 DLC 规则规整。

## 5. Bus 参数速查

```python
can.Bus(
    interface="vkgs_usb",
    channel=0,
    index=0,
    bus=None,
    address=None,
    port_path=None,
    bitrate=1_000_000,
    sample_point=75.0,
    fd=False,
    data_bitrate=5_000_000,
    data_sample_point=75.0,
    receive_own_messages=False,
    loopback=False,
    termination=None,
    bus_load_reporting=None,
    auto_start=True,
    can_filters=None,
    timeout_ms=2_000,
)
```

| 参数 | 说明 |
|---|---|
| `interface` | 当前固件对应的接口名：`vcan_usb` 或 `vkgs_usb` |
| `channel` | USB interface 编号（非 Linux 的 canX） |
| `index` | 相同 VID/PID 设备列表中的序号 |
| `bus` / `address` | libusb 总线号 / 地址；插拔后可能变化 |
| `port_path` | USB 拓扑路径（如 `2.1.4`），多设备场景更稳定 |
| `bitrate` / `sample_point` | 仲裁段参数；采样点用百分数（如 `75.0`） |
| `fd` | 使能 CAN FD |
| `data_bitrate` / `data_sample_point` | FD 数据段参数 |
| `loopback` | 控制器内部回环 |
| `termination` | `True/False` 开关终端，`None` 保持现状 |
| `bus_load_reporting` | 负载事件开关 |
| `auto_start` | `False` 时在构造后等待调用 `start()` |
| `can_filters` | python-can 软件过滤（由解析层处理） |
| `timeout_ms` | USB 控制/OUT 超时（ms） |

完整字段、异常和扩展方法见 [Python API 参考](PythonAPI参考.md)。

## 6. 设备发现与多通道

建议先通过终端工具确认稳定身份信息，再在代码里固定选择：

```text
channel=0 bus=2 address=10 port_path=2.1.4 ep_in=0x81 ep_out=0x01 ...
channel=1 bus=2 address=10 port_path=2.1.4 ep_in=0x82 ep_out=0x02 ...
```

同一 `bus/address/port_path` 代表同一物理设备两个通道。推荐按 `port_path` 选设备。

```python
from vkgs_usb import vkgs_usb_bus

for item in vkgs_usb_bus.discover_interfaces(index=0):
    print(item.number, item.bus, item.address, item.port_path)
```

VCAN 对应：`from vcan_usb import vcan_usb_bus`。

多通道示例（同一进程）：

```python
import can

buses = [
    can.Bus(
        interface="vkgs_usb",
        channel=ch,
        port_path="2.1.4",
        bitrate=1_000_000,
        termination=True,
        auto_start=False,
    )
    for ch in (0, 1)
]

try:
    for b in buses:
        b.start()
    # 发送/接收
finally:
    for b in reversed(buses):
        b.shutdown()
```

## 7. 生命周期与扩展方法

| 方法 | 作用 |
|---|---|
| `start()` | 启动通道（重复调用不重复发起启动） |
| `stop()` | 停止控制器，但保留 USB interface |
| `configure(fd=...)` | 在 stopped 状态下重配仲裁/FD 参数 |
| `set_termination(enabled)` | 在 stopped 下设置内置终端 |
| `set_bus_load_reporting(enabled)` | 在 stopped 下切换总线负载上报 |
| `get_usb_stats()` | 读取当前 interface 的 USB 计数 |
| `wait_for_usb_rx(previous, timeout)` | 等待下一次 USB 收包用于验收 |
| `state` | `can.BusState.ACTIVE/PASSIVE`（被动事件更新） |
| `get_berr_counter()` | 返回 `{"rxerr", "txerr"}` |
| `identify(enabled=True)` | 仅 `vkgs_usb_bus` 的 LED 指示 |
| `shutdown()` | 关闭 USB 资源与后台线程，可重复调用 |

`stop()/start()` 仅重置 CAN 控制器，不会修复设备级 USB 端点挂接问题。

## 8. 常见错误对照

| 现象 | 处理建议 |
|---|---|
| `No suitable Python runtime found` | 先执行 `py --list`，使用实际存在版本 |
| 找不到 `.venv-win\\Scripts\\python.exe` | venv 未创建成功，先修复 Python 环境 |
| `USB device index 0 not found` | 检查 VID/PID、WinUSB/udev、设备连接 |
| `WinError 5` | 同一 MI_xx 被占用、或绑定不正确 |
| `LIBUSB_ERROR_ACCESS` | USB 访问权限问题（Linux udev / Windows绑定） |
| `LIBUSB_ERROR_BUSY` / claim 失败 | SocketCAN 或其他进程仍占用该 interface |
| `LIBUSB_ERROR_TIMEOUT` | 输出未返回超时，检查 OUT 通道与总线状态 |
| `LIBUSB_ERROR_PIPE` | 控制请求不被当前固件支持 |

发送超时后请勿盲目重发，host 无法可靠确认固件是否已把报文投递到总线。

## 9. 验证与测试

### 9.1 硬件验收

双通道互通要求 CANH-CANH、CANL-CANL 交叉连接：

```bash
sudo ./can-test --interface vkgs_usb --channels 2 --mode bus --frames 60
```

```powershell
.\.venv-win\Scripts\python.exe .\tests\hardware_test.py --interface vkgs_usb --channels 2 --mode bus --frames 60
```

单通道内部回环：

```powershell
.\.venv-win\Scripts\python.exe .\tests\hardware_test.py --interface vkgs_usb --channels 1 --mode loopback --frames 60
```

内置顺序默认覆盖经典 CAN、FD/BRS-off、FD/BRS-on；若不测 FD 可加
`--skip-fd`。

## 10. DBC 与 CANopen 接入

- DBC：使用 `cantools.database.load_file(..., strict=True)`，从消息定义读取 frame ID、
  标准/扩展格式和长度，再把 `encode()` 结果放进 `can.Message`。
- CANopen：`canopen.Network.connect()` 的参数会传给 python-can，可直接使用
  `interface="vcan_usb"` 或 `interface="vkgs_usb"`；设备 EDS/DCF 决定对象字典和 PDO 映射。
- DBC 的 `is_fd` 不等于启用 BRS；CANopen CC 通常使用 classic CAN，FD 能力必须由
  全网节点和协议栈共同确认。

完整代码与错误分层见 [CANopen 与 DBC 开发指南](CANopen与DBC开发指南.md)。

## 11. 与 SocketCAN 切换

切回 SocketCAN 前确保所有 Bus 已 `shutdown()`，再按目标模式加载内核模块：

```bash
sudo modprobe vcan_usb
# 或
sudo modprobe -r gs_usb
sudo modprobe vkgs_usb
```

切回 Python 前也请先把相关 `canX` down，再卸载内核模块，避免双栈并发。

