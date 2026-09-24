# Python API 参考

本文档是 `vcan-usb` 与 `vkgs-usb` 的统一接口说明。首次安装与收发验证请先看[快速入门](快速入门.md)；完整配置与故障处理再看
《[PythonCAN 使用手册](PythonCAN使用手册.md)》。两者 API 保持同构。面向项目生成、
DBC 与 CANopen 集成时，可同时查阅 [`python-can-skill`](../python-can-skill/SKILL.md)
和《[CANopen 与 DBC 开发指南](CANopen与DBC开发指南.md)》。

## 1. 公共入口

通过 `python-can` 创建总线：

```python
import can

bus = can.Bus(interface="vcan_usb", channel=0)
bus = can.Bus(interface="vkgs_usb", channel=0)
```

需要静态发现设备或访问管理 API：

```python
from vcan_usb import vcan_usb_bus
from vkgs_usb import vkgs_usb_bus

vcan_usb_bus.discover_channels()
vkgs_usb_bus.discover_interfaces(index=0)
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
    data_bitrate: int = 5_000_000,
    data_sample_point: float = 75.0,
    receive_own_messages: bool = False,
    loopback: bool = False,
    termination: bool | None = None,
    bus_load_reporting: bool | None = None,
    auto_start: bool = True,
    can_filters: list[dict] | None = None,
    timeout_ms: int = 2_000,
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
- 固件基于 80 MHz 时钟回填 `BRP/TSEG1/TSEG2/SJW`，内部有 1 的偏移约定；
- 参数不可满足时抛出 `can.CanInitializationError`。

`auto_start=False` 时仅完成 USB 打开与配置，等待显式 `start()`。

`termination=None`/`bus_load_reporting=None` 表示保持当前状态。

## 3. 设备发现

```python
channels = vkgs_usb_bus.discover_channels(index=0)
interfaces = vkgs_usb_bus.discover_interfaces(index=0)
```

`discover_channels()` 返回可用 bulk IN/OUT 接口；
`discover_interfaces()` 返回 `InterfaceInfo` 列表，常见字段：

- `number`：USB interface 编号；
- `bus` / `address`：USB 总线号 / 地址；
- `port_path`：拓扑路径；
- `ep_in.bEndpointAddress` / `ep_out.bEndpointAddress`：IN/OUT 端点；
- `ep_in.wMaxPacketSize` / `ep_out.wMaxPacketSize`：端点包长。

发现过程短生命周期，不会长期 claim 接口，也不发送 `SET_CONFIGURATION`。

## 4. USB 协议模式切换（设备管理 API）

```python
vkgs_usb_bus.switch_usb_mode("vcan", channel=0)
vcan_usb_bus.switch_usb_mode("gs_usb", channel=0)
vcan_usb_bus.switch_usb_mode("peak", channel=0)
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
- `stop()`：停止控制器并保留 interface；
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
bus.state -> can.BusState
bus.get_berr_counter() -> dict[str, int]
bus.identify(enabled: bool = True) -> None   # 仅 vkgs_usb
```

`get_usb_stats()` 返回：`rx_completed/rx_bytes/rx_errors`, `tx_completed/tx_bytes/tx_errors`,
`rx_submitted/rx_pool`，用于 USB 通道级排障，不等同于 CAN 报文计数。

`bus.state` 是只读标准枚举（`ACTIVE/PASSIVE`），由后台 STATE/BERR 事件被动更新。
`get_berr_counter()` 返回最近状态帧里的 `rxerr`/`txerr`。

`identify()` 仅 `vkgs_usb_bus` 支持 LED 指示（`vcan_usb` 固件当前不支持）。

## 9. 线程与多通道约束

- `send()` 内部串行化；
- `recv` 入队受队列保护；
- 每个 Bus 独立维护 USB 句柄、传输池、状态；
- Windows 两进程可各自打开同一设备不同 `MI_xx`；同一 `MI_xx` 不可共享。
- 多通道应用应按通道创建独立 Bus；
- 建议关闭顺序按逆序，避免依赖顺序释放。

## 10. 异常与稳定性约束

| 异常 | 常见触发 |
|---|---|
| `can.CanInitializationError` | 通道不存在、时序不可实现、控制请求失败 |
| `can.CanOperationError` | 停止/已关通道发起发送、USB 超时、协议/事件线程异常 |
| `usb.core.USBError` | libusb/WinUSB 发现或控制传输失败（多数会被包装为 CAN 异常） |

应用退出时请显式 `shutdown()`；发送状态不确定不要重试。

### 已确认的硬件约束

1. 一个 Bus 生命周期内只持有一个目标 interface；
2. 不在正常业务路径里发送设备级 `SET_CONFIGURATION`；
3. Linux/macOS、Windows 一体化使用异步 I/O；
4. EP0 与 bulk 共用同一 handle；
5. 不靠频繁开关端点恢复异常；
6. `stop/start` 不能复原设备级 USB OUT 挂接异常；
7. VCAN 与 VKGS 的经典/FD 线长参数必须分别配置。

## 11. 应用层集成边界

- DBC codec 接收/输出 `can.Message`，负责 signal 的字节序、signed、scale/offset、
  choices 与 multiplex；它不负责 USB 发现或控制器位时序。
- CANopen 栈通过 `python-can` backend 使用本项目，负责 EDS/DCF 对象字典、NMT、
  PDO、SDO、EMCY、SYNC 与 heartbeat；传统 CANopen 不应仅因硬件支持 FD 就自动启用 FD。
- SocketCAN 路径应使用 `interface="socketcan", channel="can0"`；直接 USB 路径使用
  `interface="vcan_usb"/"vkgs_usb", channel=0`，两种 `channel` 语义不同。
- 应用协议测试应优先使用 virtual bus/`vcan0`，硬件测试再验证位时序、终端、ACK、
  BUS-OFF 和 USB 重枚举。

