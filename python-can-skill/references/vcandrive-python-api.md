# VCANDrive Python API 开发参考

## 选择正确后端

| 当前设备模式 | USB ID | 安装目录 | `interface` | 特有能力 |
|---|---|---|---|---|
| VCAN 原生 | `1d50:6080` | `python-cli/vcan_usb` | `vcan_usb` | 自描述 VCAN 帧协议 |
| GS_USB / VKGS | `1d50:606f` | `python-cli/vkgs_usb` | `vkgs_usb` | `identify()` LED 识别 |

两套包要求 Python 3.10+，并依赖 `python-can>=4.3`、`pyusb>=1.2` 和 `libusb1>=3.4`。在仓库根目录安装开发版本：

```bash
python -m venv .venv
.venv/bin/python -m pip install -e ./python-cli/vkgs_usb
```

Windows 将解释器改为 `.venv\Scripts\python.exe`。只安装与设备当前 VID:PID 匹配的包。

## Bus 参数

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

要点：

- `channel` 是 USB `bInterfaceNumber`，不是 Linux 的 `can0` 序号。
- `sample_point` 使用百分数，如 `75.0`；SocketCAN 的 `ip link` 则写 `0.75`。
- `port_path` 可写成 `"2.1.4"` 或 `(2, 1, 4)`，适合固定多台同 VID/PID 设备。
- `termination=None` 表示保持当前终端电阻状态；`True/False` 会显式改动硬件。
- `auto_start=False` 完成 USB 打开和配置后保持 stopped，适合同步启动多通道。
- `timeout_ms` 是 USB 控制/OUT 超时；`recv(timeout=...)` 的单位是秒。

## 发现设备与固定通道

```python
from vkgs_usb import vkgs_usb_bus

for item in vkgs_usb_bus.discover_interfaces(index=0):
    print(
        item.number,
        item.bus,
        item.address,
        item.port_path,
        hex(item.ep_in.bEndpointAddress),
        hex(item.ep_out.bEndpointAddress),
    )
```

同一 `bus/address/port_path` 下的不同 `number` 通常属于同一物理设备的多个 CAN 通道。记录设备位置时优先保存 `port_path`，不要只保存 `index` 或会在重枚举后变化的 `address`。

## 经典 CAN 收发

```python
import can

with can.Bus(
    interface="vkgs_usb",       # VCAN 原生模式改为 vcan_usb
    channel=0,
    port_path="2.1.4",          # 单设备临时测试可省略
    bitrate=500_000,
    termination=True,
) as bus:
    tx = can.Message(
        arbitration_id=0x123,
        is_extended_id=False,
        data=bytes.fromhex("11 22 33 44"),
    )
    bus.send(tx, timeout=1.0)

    rx = bus.recv(timeout=1.0)
    if rx is None:
        raise TimeoutError("one second without a CAN frame")
    print(f"id={rx.arbitration_id:#x} data={rx.data.hex(' ')}")
```

扩展帧设置 `is_extended_id=True` 并确保 ID 不超过 29 位。RTR 使用 `is_remote_frame=True` 和期望 DLC；不要为 RTR 同时填普通数据载荷。

## CAN FD 与 BRS

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

`fd=True` 配置控制器；`is_fd=True` 选择报文格式；`bitrate_switch=True` 只对该帧启用 BRS。合法 FD 长度为 0–8、12、16、20、24、32、48、64 字节。应用应主动产生合法长度，不依赖后端填充来掩盖上层协议错误。

## 接收过滤与异步消费

```python
filters = [
    {"can_id": 0x180, "can_mask": 0x780, "extended": False},
]

with can.Bus(
    interface="vkgs_usb",
    channel=0,
    bitrate=500_000,
    can_filters=filters,
) as bus:
    reader = can.BufferedReader()
    notifier = can.Notifier(bus, [reader])
    try:
        message = reader.get_message(timeout=1.0)
    finally:
        notifier.stop()
```

这里的过滤器遵循 python-can 软件过滤语义，不会配置固件 `CAN_FILTERS(35)` 硬件表。多个消费者需要明确由一个 `Notifier` 分发，避免多个线程同时 `recv()` 后难以判断帧归属。

## 多通道生命周期

```python
import can

buses = [
    can.Bus(
        interface="vkgs_usb",
        channel=channel,
        port_path="2.1.4",
        bitrate=1_000_000,
        auto_start=False,
    )
    for channel in (0, 1)
]

try:
    for bus in buses:
        bus.start()
    # application work
finally:
    for bus in reversed(buses):
        bus.shutdown()
```

每个 `Bus` 只拥有一个 interface。Windows 可由不同进程分别打开同一设备的不同 `MI_xx`，但同一 `MI_xx` 不能被第二个进程再次独占打开。

## VCANDrive 扩展方法

| 方法/属性 | 说明 |
|---|---|
| `start()` / `stop()` | 启停 CAN 控制器，保留 USB 会话 |
| `configure(fd=...)` | stopped 状态下重新应用位时序和模式 |
| `set_termination(bool)` / `get_termination()` | 设置/读取 120 Ω 终端 |
| `set_bus_load_reporting(bool)` | stopped 状态切换负载事件 |
| `get_device_info()` | 获取缓存的设备信息；普通打开不会强制把诊断查询作为依赖 |
| `get_usb_stats()` | USB interface 局部传输计数，不是 CAN 帧统计 |
| `state` | 后台事件更新的 `can.BusState` |
| `get_berr_counter()` | 最近的 `rxerr` / `txerr` |
| `identify(bool)` | 仅 `vkgs_usb` 支持 |
| `wait_for_usb_rx(previous, timeout)` | 主要供硬件验收等待 bulk IN 进展 |

停止状态才能修改的配置应集中完成，再调用一次 `start()`。`stop()/start()` 只影响 CAN 控制器，不能修复设备级 USB 端点故障。

## 模式切换

```python
from vkgs_usb import vkgs_usb_bus

vkgs_usb_bus.switch_usb_mode(
    "vcan",
    channel=0,
    port_path="2.1.4",
)
```

目标可选 `vcan`、`peak`、`gs_usb`。调用前关闭同一物理设备的全部 Bus 和 SocketCAN 通道；调用后设备写 Flash、重启并以另一 USB ID 枚举，旧对象不可继续使用。业务程序不要在重连循环里隐式切换模式。

## 异常策略

- `can.CanInitializationError`：后端不匹配、通道不存在、权限/占用问题、位时序不可实现。
- `can.CanOperationError`：stopped/closed 状态发送、USB 超时、后台 I/O 或协议错误。
- `recv()` 超时返回 `None`，这不是总线故障。
- 发送超时表示结果不确定；除非业务协议具备去重/序列号且明确允许，否则不要自动补发。
- `LIBUSB_ERROR_BUSY` 或 `WinError 5` 先检查同一 USB interface 是否被内核驱动或其他进程占用。

## 测试分层

1. 纯函数：DBC 编解码、CANopen 状态转换、ID 与 DLC 验证。
2. 虚拟总线：使用 python-can `virtual` backend 验证收发、Notifier 与超时，不依赖 USB。
3. 仓库单元测试：`python -m unittest discover -s python-cli/tests/unit -v`。
4. 硬件验收：明确接线和终端后运行 `python-cli/can-test`，先经典 CAN，再 FD/BRS。

上游参考：

- [python-can Bus API](https://python-can.readthedocs.io/en/stable/bus.html)
- [python-can Notifier 与 Listener](https://python-can.readthedocs.io/en/stable/notifier.html)
