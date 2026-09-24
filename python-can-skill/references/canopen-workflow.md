# CANopen Python 开发工作流

用户路线：先按[快速入门](../../udocs/快速入门.md)建立 Python USB 环境并收发第一帧，再按[验收测试路线](../../udocs/验证路线.md)做矩阵/压力验证，最后接入本页的应用代码。

CANopen 是建立在 CAN 之上的应用层与通信配置体系。VCANDrive 负责可靠收发 CAN 帧；对象字典、NMT、PDO、SDO、EMCY、SYNC 和 heartbeat 由 CANopen 栈处理。

## 先准备对象字典

- EDS 描述设备能力与默认对象字典。
- DCF 通常是在 EDS 基础上加入具体网络配置后的设备配置文件。
- 设备手册和当前 EDS/DCF 是索引、子索引、数据类型、访问权限和 PDO 映射的权威来源。
- DBC 适合固定信号帧，不足以替代 CANopen 对象字典和服务协议。

安装：

```bash
python -m pip install canopen
python -m pip install -e ./python-cli/vkgs_usb
```

VCAN 原生模式将安装目录和接口名换成 `vcan_usb`。

## 建立网络连接

`canopen.Network.connect()` 会把参数传给 python-can Bus，因此可直接选择 VCANDrive 后端：

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
    print(node.sdo[0x1008].raw)  # Manufacturer device name
finally:
    network.disconnect()
```

每条物理 CAN 总线创建一个 `Network`。多通道时分别创建 Network，不要让多个 CANopen dispatcher 竞争同一个 Bus 的接收队列。

传统 CANopen CC 通常使用经典 CAN。除非设备、对象字典和所选库明确支持同一 CANopen FD 规范，不要仅因 VCANDrive 支持 CAN FD 就设置 `fd=True`。

## 常见默认 COB-ID

下表是预定义连接集中的常见默认值，节点配置可通过对象字典重映射，运行时不要硬编码成不可变规则。

| 服务 | 默认 COB-ID |
|---|---|
| NMT 控制 | `0x000` |
| SYNC | `0x080` |
| EMCY | `0x080 + node_id` |
| TPDO1 / RPDO1 | `0x180 + node_id` / `0x200 + node_id` |
| TPDO2 / RPDO2 | `0x280 + node_id` / `0x300 + node_id` |
| TPDO3 / RPDO3 | `0x380 + node_id` / `0x400 + node_id` |
| TPDO4 / RPDO4 | `0x480 + node_id` / `0x500 + node_id` |
| SDO server → client | `0x580 + node_id` |
| SDO client → server | `0x600 + node_id` |
| heartbeat / boot-up | `0x700 + node_id` |

节点 ID 通常为 1–127；`0` 用于 NMT 广播，不应作为普通从节点 ID。

## NMT 与上线流程

推荐明确建模状态变化：

```python
node.nmt.state = "PRE-OPERATIONAL"

# 完成 SDO 配置、PDO 映射或参数检查
device_type = node.sdo[0x1000].raw
error_register = node.sdo[0x1001].raw

node.nmt.state = "OPERATIONAL"
```

生产应用应等待 boot-up/heartbeat，并对 heartbeat timeout、EMCY 与节点重启分别记录。不要把“收到任意帧”当作节点已进入 OPERATIONAL。

## SDO 读写

```python
# 读取标量
status = node.sdo[0x2000][1].raw

# 写入前核对 EDS 数据类型、范围和访问权限
node.sdo[0x2001][0].raw = 100

# 大对象可使用 open() 让库选择分段/块传输能力
with node.sdo[0x1F50][1].open("rb") as stream:
    chunk = stream.read(256)
```

SDO 是有确认的配置/诊断通道，不适合替代高频实时 PDO。对写操作设置业务级超时，并区分 SDO abort code、CAN 接收超时与 USB transport 错误。

## PDO 配置与消费

先从设备读取实际 PDO 配置，再注册回调：

```python
node.tpdo.read()
node.rpdo.read()

def on_tpdo(message_map):
    for variable in message_map:
        print(variable.name, variable.raw, variable.phys)

node.tpdo[1].add_callback(on_tpdo)
node.tpdo[1].enabled = True
```

若需要修改 PDO 映射：

1. 进入 PRE-OPERATIONAL；
2. 依据设备手册禁用目标 PDO；
3. 修改通信参数和映射项；
4. 保存/重新启用并再次读取确认；
5. 进入 OPERATIONAL 后验证周期、event timer 和 inhibit time。

不要假设 EDS 默认 PDO 映射等于设备当前非易失配置。

## SYNC、heartbeat 与 EMCY

- SYNC producer 的周期必须与网络设计一致；不要在多个管理器中同时启动 SYNC。
- heartbeat consumer timeout 应大于 producer period 并留出调度抖动裕量。
- boot-up 帧 payload 为节点状态事件，不等同于普通 heartbeat 健康确认。
- EMCY 应保存 error code、error register、manufacturer-specific data 和接收时间，便于与总线错误区分。

## 关闭与错误传播

始终在 `finally` 中 `network.disconnect()`。后台接收线程异常时调用 `network.check()` 让异常回到主控制流。应用层可按如下类别处理：

- 节点协议错误：SDO abort、heartbeat timeout、EMCY；
- CAN 网络错误：BUS-OFF、error-passive、无 ACK；
- 主机 transport 错误：USB 断开、权限、接口被占用；
- 配置错误：后端/VID:PID 不匹配、错误 EDS、节点 ID 或位速率错误。

不要把这四类都实现成同一个无限重连循环。重新发送非幂等 SDO 写入前，先确认设备状态或用业务序列号防重复。

## 测试建议

- 用 mock/fake object dictionary 测试索引、类型和状态机；
- 用 python-can virtual bus 测试 COB-ID 路由和 timeout；
- 在真实设备上先验证 heartbeat 与只读 SDO，再执行写入；
- PDO 测试覆盖映射版本、周期、超时与节点重启；
- 记录原始 CAN 帧，确保出现问题时能绕过高层解释复核。

上游参考：

- [CAN in Automation：CANopen 概览](https://www.can-cia.org/can-knowledge/canopen)
- [canopen for Python：Network 与节点](https://canopen.readthedocs.io/en/stable/network.html)
