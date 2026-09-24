# CANopen 与 DBC 开发指南

VCANDrive 提供 CAN/CAN FD transport；CANopen 与 DBC 位于应用层。本页帮助项目选择正确的数据模型并快速连接两套 VCANDrive 主机端路径。

## 1. 先选择协议模型

| 需求 | 使用资料 | 运行时职责 |
|---|---|---|
| 按原始 ID/payload 收发 | 无或自定义协议说明 | 应用自行验证 ID、DLC、状态机 |
| 按 signal 名读写、缩放、枚举、multiplex | DBC | codec 负责 payload ↔ signal |
| 配置对象字典、NMT、PDO、SDO、EMCY、heartbeat | EDS/DCF + CANopen 栈 | 栈负责 CANopen 服务和状态 |

EDS/DCF 与 DBC 不是互换格式。CANopen 的固定 PDO 可额外映射到 DBC 做分析，但 NMT、SDO、heartbeat 和 EMCY 仍需 CANopen 栈处理。

## 2. 两种 transport

### 2.1 VCANDrive Python USB backend

先按[快速入门](快速入门.md)完成同位率双通道收发，再按[验收测试路线](验证路线.md)做功能验收。以下两个 transport 任选其一；示例中的模式、通道号和 `network.dbc`/`device.eds` 要换成你的实际设备及项目文件。

适用 Windows、Linux、macOS，直接打开 USB interface：

```python
import can

bus = can.Bus(
    interface="vkgs_usb",   # 1d50:606f；6080 改为 vcan_usb
    channel=0,              # USB bInterfaceNumber
    bitrate=500_000,
)
```

多台设备优先增加 `port_path="2.1.4"`。Linux 下使用前释放同一 interface 的 `vcan_usb`、`vkgs_usb` 或 `gs_usb` 内核模块。

### 2.2 Linux SocketCAN

先配置 `canX`：

```bash
sudo ip link set can0 down
sudo ip link set can0 type can bitrate 500000 restart-ms 100
sudo ip link set can0 up
```

Python 通过标准 backend 使用：

```python
bus = can.Bus(interface="socketcan", channel="can0")
```

这里 `can0` 是网络接口名；它与 Python USB backend 的 `channel=0` 含义不同。

## 3. DBC + python-can

安装：

```bash
python -m pip install python-can cantools
```

发送一条 DBC 消息：

```python
import can
import cantools

db = cantools.database.load_file("network.dbc", strict=True)
definition = db.get_message_by_name("VehicleCommand")
payload = definition.encode(
    {"Enable": 1, "TargetSpeed": 12.5},
    strict=True,
)

with can.Bus(
    interface="vkgs_usb",
    channel=0,
    bitrate=500_000,
    fd=definition.is_fd,
    data_bitrate=2_000_000,
) as bus:
    bus.send(can.Message(
        arbitration_id=definition.frame_id,
        is_extended_id=definition.is_extended_frame,
        is_fd=definition.is_fd,
        bitrate_switch=False,
        data=payload,
    ))
```

接收解码：

发送示例中的 `with` 结束后总线已经关闭。接收端请另开一个会话（实际应用也可以在同一个 `with` 内收发）：

```python
import can
import cantools

db = cantools.database.load_file("network.dbc", strict=True)
with can.Bus(interface="vkgs_usb", channel=1, bitrate=500_000, fd=True, data_bitrate=2_000_000) as bus:
    frame = bus.recv(timeout=1.0)
if frame is not None:
    try:
        definition = db.get_message_by_frame_id(frame.arbitration_id)
    except KeyError:
        print("unknown frame", hex(frame.arbitration_id))
    else:
        if definition.is_extended_frame != frame.is_extended_id:
            raise ValueError("standard/extended format mismatch")
        if len(frame.data) != definition.length:
            raise ValueError("payload length does not match DBC")
        values = definition.decode(frame.data, allow_truncated=False)
        print(definition.name, values)
```

### 3.1 DBC 易错点

- Intel/Motorola 表示信号位布局，不是把整个 payload 简单反转。
- 标准/扩展帧格式要和 ID 一起校验，不要只用 `ID > 0x7ff` 猜测。
- `is_fd=True` 不决定 BRS；BRS 是发送帧策略，位速率由总线配置决定。
- signal 的 scale/offset/signed/choices 交给 codec；保持 `strict=True`。
- 每个 multiplex 分支、最小/最大/越界值和 encode→decode round trip 都应测试。
- alive counter/CRC 需要每周期重新编码，不适合把第一帧 payload 永久交给固定周期发送器。

SocketCAN 抓包可直接辅助解码：

```bash
candump can0 | python -m cantools decode network.dbc
python -m cantools list -a network.dbc
```

C/C++ 项目可生成 codec：

```bash
python -m cantools generate_c_source --database-name vehicle network.dbc
```

生成文件负责 pack/unpack，SocketCAN 层仍负责 `can_frame`/`canfd_frame` 收发。记录生成命令、cantools 版本和 DBC 哈希。

## 4. CANopen + VCANDrive

安装 Python 栈：

```bash
python -m pip install canopen
```

### 4.1 Python USB backend

```python
import canopen

network = canopen.Network()
network.connect(
    interface="vkgs_usb",
    channel=0,
    port_path="2.1.4",
    bitrate=500_000,
    termination=None,
)
try:
    node = network.add_node(6, "device.eds")
    print(node.sdo[0x1008].raw)
finally:
    network.disconnect()
```

### 4.2 SocketCAN

```python
network = canopen.Network()
network.connect(interface="socketcan", channel="can0")
try:
    node = network.add_node(6, "device.eds")
finally:
    network.disconnect()
```

`can0` 已由 `ip link` 配好位速率。传统 CANopen CC 通常使用 classic CAN；不能因为 VCANDrive 支持 CAN FD 就假设设备和 CANopen 栈也支持同一种 CANopen FD 规范。

## 5. CANopen 关键知识

| 服务 | 作用 | 常见默认 COB-ID |
|---|---|---|
| NMT | 控制节点网络状态 | `0x000` |
| SYNC | 同步 PDO/设备行为 | `0x080` |
| EMCY | 设备紧急错误 | `0x080 + node_id` |
| TPDO1 / RPDO1 | 第一组过程数据 | `0x180 + node_id` / `0x200 + node_id` |
| SDO 响应 / 请求 | 对象字典访问 | `0x580 + node_id` / `0x600 + node_id` |
| heartbeat / boot-up | 存活与 NMT 状态 | `0x700 + node_id` |

节点 ID 通常是 1–127，`0` 用于 NMT 广播。COB-ID 与 PDO mapping 可通过对象字典重映射，设备手册和当前 EDS/DCF 才是最终依据。

典型启动顺序：

1. 等待 boot-up/heartbeat；
2. 进入 PRE-OPERATIONAL；
3. 通过只读 SDO 核对设备类型、版本和错误寄存器；
4. 必要时配置 PDO、heartbeat 与同步参数并读回确认；
5. 切到 OPERATIONAL；
6. 监控 heartbeat、EMCY 和 SocketCAN 总线状态。

SDO 适合有确认的配置与诊断，PDO 适合低开销过程数据。不要用高频 SDO 轮询代替正确的 PDO 设计。

## 6. 错误分层

| 层级 | 示例 | 处理方向 |
|---|---|---|
| DBC/应用 | 未知 ID、长度不符、信号越界 | 检查数据库版本和输入 |
| CANopen | SDO abort、heartbeat timeout、EMCY | 检查对象字典、节点状态和设备错误 |
| CAN 网络 | 无 ACK、error-passive、BUS-OFF | 检查接线、终端、位时序和负载 |
| 主机 transport | USB 断开、busy、permission denied | 检查 backend、占用、权限与设备重枚举 |

SocketCAN error frame 与 CANopen EMCY 不同；日志中应分别保留并关联时间。发送超时结果不确定，只有在业务协议具备幂等或去重机制时才能安全自动重发。

## 7. 测试建议

1. 用纯单元测试验证 DBC golden vector、范围、mux、counter/CRC。
2. 用 python-can virtual 或 Linux `vcan0` 验证路由、过滤、Notifier 与 CANopen 状态机。
3. 真实总线上先 classic CAN、只读 SDO 和 heartbeat，再测试写入/PDO。
4. 在隔离总线验证节点掉线、无 ACK、BUS-OFF 和应用恢复。
5. 始终保留原始 CAN 日志，使高层 codec/对象字典错误可被复核。

## 8. 进一步参考

- [`python-can-skill`](../python-can-skill/SKILL.md)：直接 USB backend、DBC 与 CANopen 的完整 Python 路由。
- [`socket-can-skill`](../socket-can-skill/SKILL.md)：Linux 驱动、PF_CAN、DBC 代码生成与 CANopen 路由。
- [cantools](https://cantools.readthedocs.io/en/stable/)
- [CAN in Automation：CANopen](https://www.can-cia.org/can-knowledge/canopen)
- [canopen for Python](https://canopen.readthedocs.io/en/stable/network.html)
