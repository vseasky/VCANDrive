# Python API 参考

本文档是 `vcan-usb` 与 `vkgs-usb` 的统一接口说明。首次安装请看[快速入门](01_快速入门.md)，复制运行实际 API 收发示例请看[API 验证](06_API验证.md)；完整配置与故障处理再看
《[PythonCAN 使用手册](05_PythonCAN使用手册.md)》。两者 API 保持同构。面向项目生成、
DBC 与 CANopen 集成时，可同时查阅 [`python-can-skill`](../python-can-skill/SKILL.md)
和《[CANopen 与 DBC 开发指南](11_CANopen与DBC开发指南.md)》。

## 1. 公共入口

通过 `python-can` 创建总线：

```python
import can

backend = "vcan_usb"  # GS_CAN 模式改为 "vkgs_usb"；只选择一种
with can.Bus(interface=backend, channel=0, bitrate=500_000) as bus:
    print(bus.channel_info)
```

需要静态发现设备或访问管理 API：

```python
# VCAN 模式：只安装并导入 vcan_usb；GS_CAN 模式改为 vkgs_usb
from vcan_usb import vcan_usb_bus

print(vcan_usb_bus.discover_channels())
print(vcan_usb_bus.discover_interfaces(index=0))
```

两套后端都从 `can.BusABC` 继承，支持 `send()`、`recv()`、`set_filters()`、
`shutdown()`、上下文管理器和 `Notifier`。

## 2. 构造函数与设备选择

```python
Bus(
    channel: int | str = 0,
    bitrate: int = 1_000_000,
    *,
    index: int = 0,
    bus: int | None = None,
    address: int | None = None,
    port_path: object = None,
    sample_point: float = 75.0,
    fd: bool = False,
    fd_non_iso: bool = False,
    data_bitrate: int = 5_000_000,
    data_sample_point: float = 75.0,
    receive_own_messages: bool = False,
    loopback: bool = False,
    termination: bool | None = None,
    bus_load_reporting: bool | None = None,
    auto_start: bool = True,
    can_filters: list[dict] | None = None,
    timeout_ms: int = 2_000,
    rx_queue_size: int = 4096,
    **kwargs,
)
```

参数要点：

- `channel`：USB `bInterfaceNumber`，字符串会尝试转 int；
- `index`：相同 VID/PID 下的枚举序号；
- `bus` / `address`：USB 总线/地址，适合多设备排查；
- `port_path`：例如 `"1.2.3"` 或 `(1,2,3)`，比 `index` 更稳定；
- `channel_info` 构造成功后会包含协议、USB `bus/address`、`port_path`。

### 位时序与初始化

- `bitrate` / `sample_point`：仲裁段。
- `fd=True`：使能 CAN FD；
- `data_bitrate` / `data_sample_point`：FD 数据段。
- 采样点单位为百分数（如 `75.0`），与 `ip link ... sample-point 0.750` 的表达不同；
- 主机根据固件报告的 `clock_hz` 和时序范围计算 `BRP/TSEG1/TSEG2/SJW`，USB 下发使用寄存器编码（减 1）；
- Classic 仲裁范围来自 `BT_CONST`，FD 仲裁及数据范围分别来自 `BT_CONST_EXT.btc/dbtc`，切换模式时重新选择仲裁时序；
- `fd_non_iso=True` 要求同时设置 `fd=True`；缺失 FD 扩展能力时拒绝 FD 配置；
- 参数不可满足时抛出 `can.CanInitializationError`。

`auto_start=False` 时仅完成 USB 打开与配置，等待显式 `start()`。

`termination=None`/`bus_load_reporting=None` 表示保持当前状态。

## 3. 设备发现

```python
from vcan_usb import vcan_usb_bus  # 当前是 VCAN 模式

channels = vcan_usb_bus.discover_channels(index=0)
interfaces = vcan_usb_bus.discover_interfaces(index=0)
```

GS_CAN 模式改为 `from vkgs_usb import vkgs_usb_bus`，并替换下面两个调用的类名。发现 API 不代替总线收发；按[API 验证](06_API验证.md)运行双向 `send()`/`recv()` 后再检查其它方法。

`discover_channels()` 返回可用 bulk IN/OUT 接口；
`discover_interfaces()` 返回 `InterfaceInfo` 列表，常见字段：

- `number`：USB interface 编号；
- `bus` / `address`：USB 总线号 / 地址；
- `port_path`：拓扑路径；
- `ep_in.bEndpointAddress` / `ep_out.bEndpointAddress`：IN/OUT 端点；
- `ep_in.wMaxPacketSize` / `ep_out.wMaxPacketSize`：端点包长。

发现过程只查询可用接口，不占用通道；收发前仍需创建 Bus。

### 3.1 板卡身份查询

```python
from vcan_usb import vcan_usb_bus  # GS_USB 改用 vkgs_usb_bus
info = vcan_usb_bus.read_device_info(channel=0, index=0, timeout_ms=2000)
print(info["uid_hex"], info["uuid_hex"])
print(info["channel"], info["identity_key"])
print(info["sw_version_full_text"], info["hw_version_text"])
```

`read_device_info(*, channel=0, index=0, bus=None, address=None, port_path=None, timeout_ms=2000)` 是类方法；只查询所选通道身份，不创建 Bus 或启动 CAN。设备选择与发现 API 一致。SocketCAN 已绑定时应读 sysfs，避免同时占用 USB 接口。

| 字段 | 含义 |
|---|---|
| `interface` / `channel` | 后端名称 / 已选择的板内 USB interface 编号 |
| `identity_key` | `uuid_hex:channel`，唯一通道标识 |
| `uid` / `uuid` | 各四个已转换为主机整数的小端 u32 |
| `uid_hex` / `uuid_hex` | 四个 word 依次格式化，每个 8 位十六进制；不是 RFC UUID 字节交换格式 |
| `sw_version` / `sw_version_full_text` | 原始 u32 / 完整四段固件版本；旧 `sw_version_text` 仍为低三段兼容字段 |
| `hw_version_text` | 低 16 位硬件修订，格式 `v主.次` |
| `raw_hw_version` | 完整硬件字段，包含平台标识、隔离标志、修订 |
| `usb_speed` / `usb_speed_source` | 连接速率及来源；无法确认时为 `unknown` |

查询失败会抛出异常；返回的 UID/UUID 来自板卡，不使用 USB 序列号代替。

已有 Bus 的 `get_device_info()` 同样返回 `interface`、`channel` 和 `identity_key`。身份信息不可用时，可在通道停止后重试；运行中重查会报 `CanOperationError`。

## 4. USB 协议模式切换（设备管理 API）

以下是三种**独立示例**，只执行与当前模式和目标模式相符的一行；切换后设备会重启，不要连续运行：

```python
vcan_usb_bus.switch_usb_mode("gs_usb", channel=0)  # 当前 VCAN
vcan_usb_bus.switch_usb_mode("peak", channel=0)  # 当前 VCAN
vkgs_usb_bus.switch_usb_mode("vcan", channel=0)  # 当前 GS_CAN
```

可选目标：`vcan`、`peak`、`gs_usb`。`channel` 与 `index`/`bus`/`address`/`port_path` 需同时校验才可稳定定位目标设备。

`usb_mode` 会向设备写 Flash 并重启，当前对象立刻失效：

- 不创建普通 `Bus`；
- 不启动 bulk RX，不改 CAN 位时序；
- 切换前必须关闭同一设备上全部 Bus 与 SocketCAN 通道。

## 5. send()

```python
bus.send(message: can.Message, timeout: float | None = None) -> None
```

支持：标准 CAN、扩展 CAN、RTR、FD、FD+BRS；
约束：

- `shutdown` 或 stopped 状态下不能发送；
- `message.is_fd=True` 在经典模式会拒绝；
- 超时或 USB/协议错误转为 `can.CanOperationError`；
- 底层传输超时由 `timeout_ms` 控制，`timeout` 为 python-can API 语义；
- 发送失败不自动重试。

## 6. recv()

```python
bus.recv(timeout: float | None = None) -> can.Message | None
```

- `None`：持续阻塞；
- `0`：非阻塞检查队列；
- 正数：最多等待秒数；超时返回 `None`。

接收的 `can.Message` 包含通道、ID、RTR、FD、BRS、DLC 和数据。
固件时间戳转成秒；无时间戳时为 `0.0`。

`bulk-in` 回调只解析入队，不直接调用用户 listener；用户侧由 `Notifier` 执行。

## 7. 生命周期

### start / stop / configure / shutdown

- `start()`：启动已配置通道，重复调用幂等；
- `stop()`：取消并等待 RX，清空半帧、接收队列和缓存错误，停止控制器并保留 interface；
- `configure(fd=...)`：仅 stopped 状态可重配时序、模式、终端/负载开关；
- `shutdown()`：关闭 USB 资源，释放 handle / context；
  Linux/macOS 释放 interface 和 dispatcher，Windows 释放该 Bus 专属 WinUSB 句柄。

上下文会自动调用 `shutdown()`。

## 8. 扩展方法

```python
bus.set_termination(enabled: bool) -> None
bus.get_termination() -> bool
bus.set_bus_load_reporting(enabled: bool) -> None
bus.get_usb_stats() -> dict[str, int]
bus.wait_for_usb_rx(previous: int, timeout: float) -> bool
bus.get_device_info() -> dict
bus.get_capabilities() -> dict
bus.get_bit_timing() -> dict
bus.state -> can.BusState
bus.get_berr_counter() -> dict[str, int]
bus.identify(enabled: bool = True) -> None   # 仅 vkgs_usb
```

`get_usb_stats()` 返回收发次数、字节数、错误数，以及应用接收队列的长度和丢弃数。键名包括 `rx_completed`、`tx_completed`、`rx_errors`、`tx_errors`、`app_rx_queue_size`、`app_rx_dropped`。累计量可用两次查询的差值比较；USB 传输次数不等同于 CAN 帧数。

`get_capabilities()` 返回 `clock_hz`、`feature`、`fd`、`nominal`、`fd_nominal`、`data`。
各时序范围依次为 `(tseg1_min, tseg1_max, tseg2_min, tseg2_max, sjw_max, brp_min, brp_max, brp_inc)`；FD 不可用时扩展范围为 `None`。HC32 兼容交集可能另带 `reported_nominal/reported_data`。
`get_bit_timing()` 返回主机选定的 BRP、TSEG、SJW、位率和采样点，不是硬件寄存器读回。

`bus.state` 是只读标准枚举（`ACTIVE/PASSIVE/ERROR`），由后台 STATE/BERR 事件被动更新。
`get_berr_counter()` 返回最近状态帧里的 `rxerr`/`txerr`。

`identify()` 仅 `vkgs_usb_bus` 支持 LED 指示（`vcan_usb` 固件当前不支持）。

## 9. 线程与多通道约束

- `send()` 内部串行化；
- `recv` 入队受队列保护；
- 多通道应用应按通道创建独立 Bus；
- Windows 两进程可各自打开同一设备不同 `MI_xx`；同一 `MI_xx` 不可共享。
- 每个 Bus 用完后调用 `shutdown()`。

## 10. 异常与稳定性约束

| 异常 | 常见触发 |
|---|---|
| `can.CanInitializationError` | 通道不存在、时序不可实现、控制请求失败 |
| `can.CanOperationError` | 停止/已关通道发起发送、USB 超时、协议/事件线程异常 |
| `usb.core.USBError` | libusb/WinUSB 发现或控制传输失败（多数会被包装为 CAN 异常） |

应用退出时请显式 `shutdown()`；发送状态不确定不要重试。

`get_rx_trace()` 可在排障时保存最近的 USB 接收诊断记录；`get_usb_stats()` 是累计计数，比较两个时间点时应取差值。使用这些诊断方法无需理解 USB 记录格式。

## 11. 应用层集成边界

- DBC codec 接收/输出 `can.Message`，负责 signal 的字节序、signed、scale/offset、
  choices 与 multiplex；它不负责 USB 发现或控制器位时序。
- CANopen 栈通过 `python-can` backend 使用本项目，负责 EDS/DCF 对象字典、NMT、
  PDO、SDO、EMCY、SYNC 与 heartbeat；传统 CANopen 不应仅因硬件支持 FD 就自动启用 FD。
- SocketCAN 路径应使用 `interface="socketcan", channel="can0"`；直接 USB 路径使用
  `interface="vcan_usb"/"vkgs_usb", channel=0`，两种 `channel` 语义不同。
- 应用协议测试应优先使用 virtual bus/`vcan0`，硬件测试再验证位时序、终端、ACK、
  BUS-OFF 和 USB 重枚举。
