# SocketCAN 上的 CANopen 与 DBC

用户路线：先按[快速入门](../../udocs/快速入门.md)安装驱动并收发第一帧，再按[验收测试路线](../../udocs/验证路线.md)验收 Linux 内核路径，最后使用本页 API。

SocketCAN 只负责传输 CAN/CAN FD 帧。DBC 与 CANopen 位于应用层：

- DBC：把固定帧 payload 映射为命名信号，适合信号采集、控制和代码生成。
- CANopen：基于对象字典实现网络管理和通信服务，通常由 EDS/DCF 描述设备。

不要把 DBC 当作 CANopen 对象字典，也不要把 CANopen SDO/NMT/heartbeat 当作 USB 驱动功能。

## DBC：命令行观察

安装 cantools 后可直接解码 `candump`：

```bash
python3 -m pip install cantools
candump can0 | python3 -m cantools decode network.dbc
python3 -m cantools list -a network.dbc
```

先用原始 `candump -ta -e can0` 保存证据，再叠加 DBC 解码。未知 ID、长度不符或数据库版本错误不能通过忽略异常来掩盖。

## DBC：Python 复用 SocketCAN

即使项目主要运行在 Linux，也可用 python-can 的标准 `socketcan` 后端复用同一 DBC codec：

```python
import can
import cantools

db = cantools.database.load_file("network.dbc", strict=True)
definition = db.get_message_by_name("ControlCommand")
payload = definition.encode({"Enable": 1, "Target": 12.5}, strict=True)

with can.Bus(interface="socketcan", channel="can0") as bus:
    bus.send(can.Message(
        arbitration_id=definition.frame_id,
        is_extended_id=definition.is_extended_frame,
        is_fd=definition.is_fd,
        bitrate_switch=False,
        data=payload,
    ))
```

`ip link` 已负责配置 bitrate/FD；这里不要再把 `channel="can0"` 与 VCANDrive Python USB backend 的 `channel=0` 混淆。

## DBC：生成 C 源码

```bash
python3 -m cantools generate_c_source \
    --database-name vehicle network.dbc
```

生成的 `.c/.h` 通常包含消息结构、pack/unpack、信号缩放和 frame ID/length 宏。集成原则：

1. 把生成文件视为构建产物或可追踪生成物，记录生成命令和 cantools 版本。
2. SocketCAN 层只负责 `read/write` `can_frame`/`canfd_frame`。
3. pack 后检查 payload 长度等于数据库消息长度。
4. unpack 前同时检查帧 ID、标准/扩展格式和长度。
5. 每次 DBC 更新后重新生成并运行 encode/decode golden-vector 测试。

Motorola/big-endian 信号位布局不是简单反转字节；依赖生成 codec，不要手工拼位。BRS、仲裁段/数据段速率和 ISO/non-ISO 也不应从 `is_fd` 单独推断。

## CANopen：SocketCAN 连接

Python CANopen 栈示例：

```python
import canopen

network = canopen.Network()
network.connect(interface="socketcan", channel="can0")
try:
    node = network.add_node(6, "device.eds")

    node.nmt.state = "PRE-OPERATIONAL"
    print(node.sdo[0x1000].raw)
    node.tpdo.read()
    node.nmt.state = "OPERATIONAL"
finally:
    network.disconnect()
```

`can0` 必须已通过 `ip link` 配置正确速率并处于 UP。传统 CANopen CC 通常使用 classic CAN；只有所有节点和栈明确采用一致的 CANopen FD 规范时才启用 FD。

C/C++ 项目应选择成熟 CANopen 栈并启用其 SocketCAN adapter。不要在业务代码中临时实现不完整的 SDO 分段、heartbeat 或 NMT 状态机。

## CANopen 常用对象与服务

| 对象/服务 | 作用 |
|---|---|
| NMT | 控制 INITIALISING、PRE-OPERATIONAL、OPERATIONAL、STOPPED 状态 |
| SDO | 有确认地访问对象字典，适合配置与诊断 |
| PDO | 低开销过程数据，映射与传输类型由对象字典配置 |
| SYNC | 同步 PDO/设备行为；一个网络应明确唯一 producer |
| EMCY | 上报设备紧急错误，与 SocketCAN error frame 不同 |
| heartbeat | 节点存活和 NMT 状态监控 |

常见预定义 COB-ID（可通过对象字典重映射）：

| 服务 | 默认 COB-ID |
|---|---|
| NMT | `0x000` |
| SYNC | `0x080` |
| EMCY | `0x080 + node_id` |
| TPDO1 / RPDO1 | `0x180 + node_id` / `0x200 + node_id` |
| SDO 响应 / 请求 | `0x580 + node_id` / `0x600 + node_id` |
| heartbeat / boot-up | `0x700 + node_id` |

节点 ID 通常为 1–127；`0` 保留给 NMT 广播。设备手册与实际 EDS/DCF 决定索引、子索引、数据类型、权限和 PDO 映射。

## Socket filter 规划

CANopen 栈可按所需 COB-ID 安装 socket filters，例如一个管理器可能关心 heartbeat、EMCY、SDO 响应和映射后的 TPDO。注意：

- filter mask 要覆盖 `CAN_EFF_FLAG`，避免标准/扩展格式混淆；
- CANopen CC 的预定义连接集一般使用 11-bit 标准 ID；
- 多个 filter 默认 OR；
- error frames 必须另设 `CAN_RAW_ERR_FILTER`；
- socket filter 不改变其他进程，也不写固件硬件 filter。

## DBC 与 CANopen 一起使用

某些系统用 DBC 观察固定 PDO payload。这只在 PDO 映射已冻结且版本受控时安全：

1. 从设备读取/确认实际 PDO 映射；
2. 让 DBC frame ID、长度、位布局与该映射一致；
3. 单独由 CANopen 栈处理 NMT、SDO、heartbeat、EMCY；
4. 映射变更时同时升级 DCF/EDS、DBC 与应用；
5. 日志始终保留原始帧，便于发现高层数据库解释错误。

不要用 DBC 解码 SocketCAN error frame；不要把 SDO 分段帧误当作固定 8-byte 业务信号。

## 测试路径

1. 在 `vcan0` 上验证 socket filters、COB-ID 路由和 DBC golden vectors。
2. 用 CANopen 模拟节点验证 boot-up、heartbeat timeout、SDO abort 和 PDO 映射。
3. 在隔离硬件总线上先读只读对象，再测试写入和 NMT 控制。
4. 记录 `candump -L`，同时检查 SocketCAN error frame 与 CANopen EMCY。
5. 验证节点断电、总线 BUS-OFF、接口重启和应用退出时的资源清理。

上游参考：

- [Linux Kernel SocketCAN 文档](https://docs.kernel.org/networking/can.html)
- [cantools 文档](https://cantools.readthedocs.io/en/stable/)
- [CAN in Automation：CANopen](https://www.can-cia.org/can-knowledge/canopen)
- [canopen for Python](https://canopen.readthedocs.io/en/stable/network.html)
