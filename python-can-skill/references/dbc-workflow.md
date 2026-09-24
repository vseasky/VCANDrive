# DBC 信号编解码工作流

用户路线：先按[快速入门](../../udocs/快速入门.md)建立 Python USB 环境并收发第一帧，再按[验收测试路线](../../udocs/验证路线.md)做矩阵/压力验证，最后接入本页的应用代码。

DBC 描述 CAN 帧中的信号布局、字节序、比例、偏移、单位、枚举值、复用关系和发送节点。它不配置 USB 设备，也不替代 `python-can` 总线生命周期。

## 适用边界

- 有 `.dbc` 且需求以“车速、温度、状态位”等信号命名时，使用 DBC。
- 只有帧 ID 与原始 payload 时，先使用 raw CAN；不要编造信号定义。
- CANopen 设备优先使用 EDS/DCF 对象字典。DBC 可用于观察固定 PDO 布局，但不能描述 SDO、NMT、heartbeat 等完整 CANopen 行为。

安装：

```bash
python -m pip install cantools
```

## 加载并校验数据库

```python
from pathlib import Path
import cantools

dbc_path = Path("network.dbc")
db = cantools.database.load_file(dbc_path, strict=True)

for definition in db.messages:
    print(
        definition.name,
        hex(definition.frame_id),
        definition.length,
        definition.is_extended_frame,
        definition.is_fd,
    )
```

保持 `strict=True`，让重叠信号或越界布局尽早失败。DBC 默认编码常见为 cp1252；遇到来源明确的其他编码时显式传 `encoding=...`，不要以忽略错误的方式吞掉注释或符号名。

## 信号编码后发送

```python
import can
import cantools

db = cantools.database.load_file("network.dbc", strict=True)
definition = db.get_message_by_name("VehicleStatus")

values = {
    "VehicleSpeed": 12.5,
    "Gear": "Drive",
    "AliveCounter": 3,
}
payload = definition.encode(values, strict=True)

frame = can.Message(
    arbitration_id=definition.frame_id,
    is_extended_id=definition.is_extended_frame,
    is_fd=definition.is_fd,
    bitrate_switch=False,  # BRS 是链路策略，不能仅由 DBC 的 is_fd 推断
    data=payload,
)

with can.Bus(
    interface="vkgs_usb",
    channel=0,
    bitrate=500_000,
    fd=definition.is_fd,
    data_bitrate=2_000_000,
) as bus:
    bus.send(frame)
```

`encode()` 默认应用比例与偏移。范围外信号、缺失必需信号或多余字段应在业务边界报错，不要静默截断。校验 `len(payload) == definition.length`。

## 接收后解码

```python
from collections.abc import Mapping

def decode_frame(db, frame) -> tuple[str, Mapping[str, object]] | None:
    try:
        definition = db.get_message_by_frame_id(frame.arbitration_id)
    except KeyError:
        return None

    if definition.is_extended_frame != frame.is_extended_id:
        return None
    if len(frame.data) != definition.length:
        raise ValueError(
            f"{definition.name}: expected {definition.length} bytes, "
            f"got {len(frame.data)}"
        )

    values = definition.decode(
        frame.data,
        decode_choices=True,
        scaling=True,
        allow_truncated=False,
    )
    return definition.name, values
```

未知 ID 是常见总线事件，可计数或记录但不一定是异常。已知消息长度不符通常代表 DBC 版本不匹配、抓到了同 ID 的另一网络，或发送端协议错误。

## 字节序、符号和比例

- Intel/little-endian 与 Motorola/big-endian 描述的是信号位排列，不等于简单地把整个 payload 反转。
- signed 信号必须按定义进行符号扩展；不要先把原始值当无符号数再自行修补。
- 物理值通常遵循 `physical = raw * scale + offset`，由 cantools 统一处理。
- 枚举值可能解码为字符串；需要数值时设置 `decode_choices=False`。
- 浮点数、复用信号、容器消息和校验字段需要单独测试，不能只验证一条普通帧。

## 标准帧、扩展帧与 CAN FD

帧 ID 与帧格式共同标识消息。不要通过 `frame_id > 0x7ff` 猜测扩展帧；使用 DBC 的 `is_extended_frame` 和 python-can 的 `is_extended_id`。

DBC 中 `is_fd=True` 只说明消息可用 CAN FD 帧承载。仲裁/数据波特率、ISO/non-ISO 与 BRS 仍由总线配置和运行时发送策略决定。对于 12/16/20/24/32/48/64 字节 FD 消息，逐个验证长度与接收端一致。

## 复用与版本管理

cantools 会根据 multiplexer 信号选择活动分支。调用方仍应：

1. 为每个 mux 分支准备至少一组 encode/decode 往返测试；
2. 把 DBC 与应用版本一起发布，记录哈希或版本字段；
3. 启动时输出所加载的 DBC 路径和版本，避免运行时误用旧文件；
4. 不直接修改共享 `Database` 对象；确需修改后调用 `refresh()` 并重新执行一致性测试。

## 周期消息与计数器

`bus.send_periodic()` 可发送固定周期帧，但 alive counter、CRC 或时间相关字段通常需要每周期重新编码。此时使用显式调度循环或可修改的周期任务，并将以下职责分开：

- 调度器决定发送时刻；
- codec 根据最新信号、counter 和 CRC 生成 payload；
- transport 只发送 `can.Message`；
- 失败策略根据上层协议是否可去重来决定，不能盲目重发。

## 测试清单

- 每条发送消息至少一次 encode → decode 往返；
- 最小值、最大值、负值、枚举与范围外值；
- 每个 multiplex 分支；
- 标准/扩展 ID 同值冲突场景；
- classic/FD 长度边界；
- 截断 payload、未知 ID 和错误 DBC 版本；
- 需要 counter/CRC 时验证连续多帧，而非只测单帧。

命令行辅助：

```bash
candump can0 | python -m cantools decode network.dbc
python -m cantools list -a network.dbc
python -m cantools generate_c_source --database-name vehicle network.dbc
```

上游参考：[cantools 文档](https://cantools.readthedocs.io/en/stable/)。
