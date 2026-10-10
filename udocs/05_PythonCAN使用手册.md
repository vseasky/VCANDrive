# PythonCAN 使用手册

首次安装和一帧收发从[快速入门](01_快速入门.md)开始；编写第一个 `can.Bus` 程序按[API 验证](06_API验证.md)操作，双通道压力测试见[验收路线](10_验证路线.md)。本页集中说明后端配置、设备选择、生命周期和故障处理。仓库包含两套独立的 `python-can` USB 后端：

| 固件模式 | USB ID | 安装包 | `can.Bus` 接口名 |
|---|---|---|---|
| VCAN | `1d50:6080` | `vcan-usb` | `vcan_usb` |
| VKGS/gs_usb 扩展 | `1d50:606f` | `vkgs-usb` | `vkgs_usb` |

可同时安装两个包，但打开设备时必须选择当前固件模式对应的后端。查看设备身份或切换 VCAN、PCAN、GS_CAN 模式，请使用[设备管理器](03_设备管理器.md)。终端工具与测试脚本位于 `python-cli/tools/`
与 `python-cli/tests/`，它们不会随驱动包安装分发。

> 关键规则：同一 USB 接口不能同时被 `python-can` 后端和 SocketCAN 内核驱动占用。
> 需要 `candump` / `cansend` / `ip link` PF_CAN 场景时，请优先使用 SocketCAN 手册。

需要按 DBC signal 或 CANopen 对象字典开发时，先完成本文的 transport 收发，再阅读
《[CANopen 与 DBC 开发指南](11_CANopen与DBC开发指南.md)》或直接使用
[`python-can-skill`](../python-can-skill/SKILL.md)。

## 只查询 UID / UUID 和版本

安装对应后端后，在 `python-cli/` 执行当前设备模式对应的一条命令：

```bash
python3 canctl.py --interface vcan_usb --channel 0 info
python3 canctl.py --interface vkgs_usb --channel 0 info
```

CLI 默认输出多行格式化的精简 JSON，包含模式、通道、版本、UID、UUID 和 `identity_key`，可直接供程序解析；要读取原有完整 JSON，在命令末尾加 `--json`。JSON 包含 `uid_hex`、`uuid_hex`、`sw_version_full_text`、`hw_version_text` 和 `raw_hw_version`。
类方法 `vcan_usb_bus.read_device_info()` / `vkgs_usb_bus.read_device_info()` 只查询身份，不启动 CAN；已有 Bus 可用 `get_device_info()` 读取身份。
SocketCAN 已绑定时可从只读 sysfs 查询身份；Python USB 与内核驱动不能同时打开同一路。

## 1. 快速开始

统一入口位于 `python-cli/canctl.py`，所有命令在 `python-cli/` 运行。完整的新手接线、安装和免 sudo 流程见[快速入门](01_快速入门.md)。无需手工激活虚拟环境。

### 1.1 Linux

```bash
python3 canctl.py setup
python3 canctl.py permissions
sudo python3 canctl.py permissions --apply
```

重新插拔后，普通用户执行 `python3 canctl.py doctor`；按输出释放仍被 SocketCAN 绑定的目标板卡，再执行 `python3 canctl.py --interface vcan_usb list`。

### 1.2 Windows

每路 MI 子接口绑定 WinUSB 后：

```powershell
py canctl.py setup
py canctl.py doctor
py canctl.py --interface vcan_usb list
```

### 1.3 macOS

先安装 Python 3.10+ 与原生 libusb（Homebrew：`brew install libusb`），再执行：

```bash
python3 canctl.py setup
python3 canctl.py doctor
python3 canctl.py --interface vcan_usb list
```

三平台 setup 默认安装两种后端；只需要一种时加 `--interface vcan_usb`。设备实际为 GS_CAN 时，日常命令改为 `--interface vkgs_usb`。其余参数一致。

## 2. 平台准备

### 2.1 Linux：USB 与权限

权限和占用分开处理。`permissions --apply` 一次性写入限定 VID/PID 的 udev 规则，重新插拔后日常命令无需 sudo。`doctor` 显示节点权限和每路绑定；如果显示内核 CAN 驱动，关闭 SocketCAN 程序，复制输出中的 `release` 命令解绑选定板卡。

测试临时加载的 VCAN 模块可能只能用 `sudo rmmod vcan_usb` 卸载；GS_CAN 则用 `sudo rmmod vkgs_usb`，`modprobe -r` 报不存在不代表未加载。选择性 release 比卸载整个模块更适合多板卡环境；详见[常见问题](02_常见问题.md)。不要让 USB 后端与内核驱动同时占用同一路。

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

## 3. 通道占用

一个 `Bus` 打开一个 USB CAN 通道。两路通道可以分别使用，同一路通道不能同时被两个程序或 SocketCAN 驱动占用。使用完毕调用 `shutdown()`，或使用下文的 `with can.Bus(...)` 自动关闭。

## 4. Python 示例

### 4.1 经典 CAN

```python
import can

with can.Bus(
    interface="vcan_usb",
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
    interface="vcan_usb",
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
    interface="vcan_usb",
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
| `port_path` | USB 拓扑路径（如 `2.1.4`），用于选择连接位置；不是板卡唯一身份，必要时同时指定 `bus` |
| `bitrate` / `sample_point` | 仲裁段参数；采样点用百分数（如 `75.0`） |
| `fd` | 使能 CAN FD |
| `data_bitrate` / `data_sample_point` | FD 数据段参数 |
| `loopback` | 控制器内部回环 |
| `termination` | `True/False` 开关终端，`None` 保持现状 |
| `bus_load_reporting` | 负载事件开关 |
| `auto_start` | `False` 时在构造后等待调用 `start()` |
| `can_filters` | python-can 软件过滤（由解析层处理） |
| `timeout_ms` | USB 控制/OUT 超时（ms） |

完整字段、异常和扩展方法见 [Python API 参考](07_PythonAPI参考.md)。

## 6. 设备发现与多通道

**用完整 UID/UUID 识别板卡，用“UID/UUID + channel”识别唯一通道。** `channel` 是板内 USB interface 编号，通常为 0、1；与 Linux 的 `can0/can1` 名称无关。先通过终端工具发现当前连接位置：

```text
channel=0 bus=2 address=10 port_path=2.1.4
channel=1 bus=2 address=10 port_path=2.1.4
```

同一次枚举中，相同 `bus/address/port_path` 下的不同 `channel` 属于同一物理设备。`index` 是枚举序号，`address` 可能在重插后改变，`port_path` 标识 USB 连接位置：换口会变，同一个口换板则位置可能不变。因此这些参数用于本次打开设备，不能代替长期保存的 UID/UUID。

当前 API 没有直接按 `uid=` 或 `uuid=` 打开设备的参数。多板应用应先发现候选设备，用其当前位置和目标通道调用 `read_device_info()`，匹配已保存的完整身份，再用同一组选择参数打开 Bus。示例中的位置应替换为本次发现的值：

```python
from vcan_usb import vcan_usb_bus

selection = {"bus": 2, "address": 10, "port_path": "2.1.4", "channel": 1}
info = vcan_usb_bus.read_device_info(**selection)
print("板卡 UID:", info["uid_hex"])
print("板卡 UUID:", info["uuid_hex"])
print("唯一通道:", info["identity_key"])  # UUID:channel
# 与已保存的完整身份匹配后，用 can.Bus(interface="vcan_usb", **selection, ...) 打开。
```

返回值是 Python 字典，可直接读取 `info["interface"]`、`info["channel"]`、`info["uid_hex"]`、`info["uuid_hex"]` 和 `info["identity_key"]`。已有 Bus 调用 `bus.get_device_info()` 时字段相同。`identity_key` 由 UUID 与板内通道号组成，适合标识唯一通道。

查询不会启动 CAN，但需要 claim 对应 USB interface；SocketCAN 已绑定时改用其 sysfs 查询。未找到目标、查询失败或匹配到多块板卡时报告错误，不要回退到 `index=0`。查询后若发生插拔或模式切换，应重新发现和核对身份。需要在打开后再次核对时，可用 `auto_start=False` 创建 Bus，检查 `get_device_info()` 后再调用 `start()`。

同一板卡各通道的 UID/UUID 应一致；物理插座与软件通道的对应关系需按板卡说明确认并记录。SocketCAN 路线的完整对照表见[定位唯一板卡和通道](09_SocketCAN使用手册.md#定位唯一板卡和通道)。

```python
from vcan_usb import vcan_usb_bus

for item in vcan_usb_bus.discover_interfaces(index=0):
    print(item.number, item.bus, item.address, item.port_path)
```

GS_CAN 对应：`from vkgs_usb import vkgs_usb_bus`。

多通道示例（同一进程）：

```python
import can

buses = [
    can.Bus(
        interface="vcan_usb",
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
| `stop()` | 取消并等待 RX、清空接收会话状态、停止控制器，保留 USB interface |
| `configure(fd=...)` | 在 stopped 状态下重配仲裁/FD 参数 |
| `set_termination(enabled)` | 在 stopped 下设置内置终端 |
| `set_bus_load_reporting(enabled)` | 在 stopped 下切换总线负载上报 |
| `get_device_info()` | 返回板卡身份；信息缺失时可在通道停止后重查 |
| `get_capabilities()` | 固件时钟及 Classic/FD 独立范围 |
| `get_bit_timing()` | 主机选择的时序，不是硬件读回 |
| `get_usb_stats()` | 读取当前 interface 的累计 USB 计数 |
| `get_rx_trace()` | 获取最近的 USB 接收诊断记录，供排障时保存 |

| `state` | `can.BusState.ACTIVE/PASSIVE/ERROR`（被动事件更新） |
| `get_berr_counter()` | 返回 `{"rxerr", "txerr"}` |
| `identify(enabled=True)` | 仅 `vkgs_usb_bus` 的 LED 指示 |
| `shutdown()` | 关闭 USB 资源与后台线程，可重复调用 |

USB 收发异常时保存报错与 `get_usb_stats()`，关闭 Bus 后重新发现设备；发送结果不确定时不要自动重发。GS_CAN 特有的 `rx_zero_terminator` 格式选项只在明确知道固件格式时使用，普通用户保持默认值。

## 8. 排错

权限、接口占用、找不到设备与超时，按[常见问题](02_常见问题.md)中的实际错误处理。

## 9. 验证与测试

### 9.1 硬件验收

双通道互通要求 CANH 接 CANH、CANL 接 CANL：

```bash
python3 canctl.py test --interface vcan_usb
```

```powershell
py canctl.py test --interface vcan_usb
```

高负载长测：

```powershell
py canctl.py test --interface vcan_usb --frames 20000 --rounds 3
```

测试要求两路总线和 CAN FD，覆盖完整混合帧矩阵、双向收发、内部回环、定向序号与重复打开。每个发送端每阶段默认 10000 帧；缺项直接失败。

## 10. DBC 与 CANopen 接入

- DBC：使用 `cantools.database.load_file(..., strict=True)`，从消息定义读取 frame ID、
  标准/扩展格式和长度，再把 `encode()` 结果放进 `can.Message`。
- CANopen：`canopen.Network.connect()` 的参数会传给 python-can，可直接使用
  `interface="vcan_usb"` 或 `interface="vcan_usb"`；设备 EDS/DCF 决定对象字典和 PDO 映射。
- DBC 的 `is_fd` 不等于启用 BRS；CANopen CC 通常使用 classic CAN，FD 能力必须由
  全网节点和协议栈共同确认。

完整代码与错误分层见 [CANopen 与 DBC 开发指南](11_CANopen与DBC开发指南.md)。

## 11. 与 SocketCAN 切换

先关闭所有 Python `Bus`。Linux 返回 SocketCAN 的加载步骤见[内核驱动安装](08_Linux内核驱动安装.md)；从 SocketCAN 返回 Python 时先按 `canctl.py doctor` 查绑定，再按[快速入门](01_快速入门.md#3-linux-一次配置以后免-sudo)释放目标板卡。
