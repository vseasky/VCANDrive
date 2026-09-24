# 驱动特殊 API 说明（VCAN / PEAK / GS_USB 模式）

使用本页前先按[快速入门](快速入门.md)确认设备模式、安装匹配的驱动并完成第一帧收发；需要完整验收时按[验收测试路线](验证路线.md)逐步运行。

本文件说明设备的三种 USB 协议模式，以及 VCAN 与 GS_CAN 驱动暴露的特殊能力，包括：

1. 设备三种 USB 协议模式与切换；
2. 两个驱动对 SocketCAN 用户态暴露的能力（哪些 `ip link` 选项/特性可用）；
3. 协议层的厂商控制请求（USB control transfer）——供编写上位机、调试工具或固件
   联调时参考（这些是 **USB 协议层**接口，不是 SocketCAN 网络层接口）。

通用 SocketCAN 命令见《[SocketCAN使用手册](SocketCAN使用手册.md)》。

---

## 0. 设备 USB 协议模式概述

同一硬件可运行三种 USB-CAN 协议模式。仓库为 VCAN 和 GS_USB 提供自有驱动；
PEAK 模式使用 PEAK 兼容驱动：

| 模式 | USB VID:PID | 对应驱动 | 协议 |
|---|---|---|---|
| VCAN | `1d50:6080` | `vcan_usb` | 自研 VCAN（每帧带 opcode 长度前缀） |
| PEAK | `0c72:0011/0012/000d/0014`（依硬件型号） | PEAK 兼容驱动 | PCAN USB |
| gs_usb | `1d50:606f` | `vkgs_usb` | candleLight / gs_usb 兼容 + 扩展 |

设备内部模式枚举：`0 = VCAN`、`1 = PEAK_CAN`、`2 = GS_USB`。切换通过 `USB_MODE`
厂商请求写入并**持久化到 Flash、随后重启**生效（见 §3）。

### 两种模式的共性（两个驱动相同的设计）

- **每路 CAN = 一个独立 USB 接口**，各有独立 bulk 端点；驱动按接口逐个 probe，
  端点由 USB 描述符识别，不限制通道数量。
- **设备不回显 TX**：固件发送完成只更新统计，不把已发帧回送主机。因此驱动在
  **bulk‑OUT URB 完成时**就地完成本帧回显（`can_get_echo_skb` + tx 统计），
  不等待设备 echo。
- **位时序为寄存器编码**：固件对每个段 +1 且**忽略 prop_seg**。驱动据此下发
  `brp-1`、`(prop_seg+phase_seg1)-1`、`phase_seg2-1`、`sjw-1`，`prop_seg` 填 0。
- **80 MHz** CAN 时钟；终端电阻 120 Ω 可切换；探测时读取并打印固件版本/UID。
- bulk‑IN 可能把**多帧合并**在一次传输中，驱动 RX 解析按帧循环（见各自“帧格式”）。

---

## 1. SocketCAN 层暴露的能力（用户可直接用 `ip` 操作）

两个驱动对用户态暴露的能力一致，均按设备 `feature` 位动态启用：

| 能力 | SocketCAN 用法 | 说明 |
|---|---|---|
| 经典 / FD 收发 | `bitrate` / `dbitrate ... fd on` | FD 机型 `max_mtu=72` |
| 终端电阻 | `ip link set canX type can termination 120/0` | 见 §2/§3 的 `CAN_TERMINATION` |
| listen-only | `listen-only on` | 只听 |
| loopback | `loopback on` | 内部回环自测 |
| one-shot | `one-shot on` | 单次发送不重传 |
| berr-reporting | `ip link set canX type can berr-reporting on/off` | 打开后 BERR 事件帧会被解码成标准 `CAN_ERR_PROT_*` 错误帧；关闭时仅更新计数缓存，不产生错误帧。仅当固件通告 `FEATURE_BERR_REPORTING` 时可用（两种协议均通告） |
| 错误计数 | `ip -details link show` 的 `berr-counter` | `do_get_berr_counter` 直接返回被 STATE/BERR 事件帧持续更新的缓存，不再每次发同步控制请求 |
| 总线状态 | error frames + `state` 字段 | 由设备异步状态/错误事件驱动；bus-off/error-passive/error-warning 的上报与 berr-reporting 开关无关，始终开启 |
| identify/blink LED | `ethtool -p canX <秒数>` | 仅 `vkgs_usb` 支持（走 `IDENTIFY(7)`）；`vcan_usb` 固件虽通告 `FEATURE_IDENTIFY` 位但未实现对应请求处理，驱动未接 |

> 说明：硬件 ID 过滤（`CAN_FILTERS`）虽在协议层存在，但 SocketCAN 标准接口走
> 内核软件过滤（`CAN_RAW_FILTER`），因此驱动**未**把硬件过滤接到 `ip` 命令。
> 如需硬件过滤，走 §2/§3 的厂商请求自行下发。

---

## 2. VCAN 模式特殊 API（vcan_usb）

### 2.1 帧格式（规范、自带长度前缀）

每一帧（数据帧与控制响应）都以**公共头**开始：

```
偏移  长度  字段
0     4     echo_id   方向/类型标记
4     2     opcode    = (channel << 12) | 帧字节数(低12位)
6     2     flags     数据帧:CAN标志位; 控制帧:请求码
```

数据/接收帧（`vcan_usb_host_frame`）：

```
0   echo_id   8 字节头(echo_id+opcode+flags)
8   can_id    4   原始 ID（标志位在 flags 里，不在 can_id 里）
12  dlc       1
13  reserved  3
16  timestamp_us 8  设备微秒时间戳（驱动当前不用于硬件时间戳）
24  data[]    8(经典) / 64(FD)
```

`echo_id` 方向标记：

| 名称 | 值 | 含义 |
|---|---|---|
| TX | `0xA1C95E3D` | 主机→设备 发送帧 |
| RX | `0xA2C95E3D` | 设备→主机 接收帧 |
| LOAD | `0xA3C95E3D` | 总线负载统计帧 |
| STATE | `0xA4C95E3D` | 状态/错误事件帧 |
| SETUP | `0xA5C95E3D` | 控制请求载荷头 |

`flags`（数据帧标志位）：`OVERFLOW(b0) FD(b1) BRS(b2) ESI(b3) EFF(b4) RTR(b5) ERR(b6)`。

> **RX 健壮性**：因为每帧 opcode 自带长度，`vcan_usb` 的接收解析对多帧合并最可靠：
> 逐帧按 opcode 步进，并校验 `opcode` 中的通道号必须等于本接口通道；长度越界则停止
> 该次传输（`rx_length_errors++`），未知 `echo_id` 则跳过该帧并继续（`rx_frame_errors++`）。
> 统计可用 `ip -s -d link show canX` 查看。

### 2.2 控制请求（USB vendor，RECIP_INTERFACE）

- `bmRequestType`：写=`0x41`（OUT|VENDOR|INTERFACE），读=`0xC1`（IN|...）。
- `bRequest`：请求码；**bit7=1 表示读方向**。
- `wValue`：一般为 0（`BSP_DEVICE_INFO` 必须为 0，否则触发 OTA）。
- `wIndex`：**通道号**（=接口号）。
- 数据：**完整结构体，且必须填好 SETUP 公共头**（`echo_id=SETUP`，
  `opcode=(channel<<12)|sizeof`，`flags=请求码`）。设备据载荷头分发与校验。

| bRequest | 名称 | 方向 | 载荷结构（字节） |
|---|---|---|---|
| 0  | HOST_FORMAT | 写 | `vcan_usb_host_config`(12)：byte_order |
| 1  | MODE | 写 | `vcan_usb_device_mode`(16)：mode + mode_flags |
| 2  | BERR | 读 | `vcan_usb_device_berr`(16) |
| 3  | CAN_STATE | 读 | `vcan_usb_device_state`(28)：state/rxerr/txerr |
| 16 | BT_CONST | 读 | `vcan_usb_bt_const`(48)：feature/fclk/仲裁段范围 |
| 17 | BT_CONST_EXT | 读 | `vcan_usb_bt_const_ext`(80)：含数据段范围 |
| 24 | BITTIMING | 写 | `vcan_usb_device_bittiming`(28)：仲裁段时序 |
| 25 | DATA_BITTIMING | 写 | 同上(28)：FD 数据段时序 |
| 33 | BSP_DEVICE_INFO | 读 | `vcan_usb_bsp_device_info`(48)：sw/hw/uid[4]/uuid[4] |
| 34 | USB_MODE | 写 | `vcan_usb_device_usb_mode`：mode(u8) 持久化+重启 |
| 35 | CAN_FILTERS | 读/写 | `vcan_usb_can_filter`：硬件 ID 滤波表 |
| 36 | CAN_BUS_LOAD | 读/写 | `vcan_usb_can_config`(12)：负载上报使能 |
| 37 | CAN_TERMINATION | 读/写 | `vcan_usb_can_config`(12)：终端电阻使能 |

`mode_flags` / `feature` 位定义：`LISTEN_ONLY(b0) LOOP_BACK(b1) TRIPLE_SAMPLE(b2)
ONE_SHOT(b3) HW_TIMESTAMP(b4) PAD(b7) FD(b8) FD_NON_ISO(b9) BERR_REPORTING(b12)`
（feature 另有 `IDENTIFY(b5) BT_CONST_EXT(b10) TERMINATION(b11) GET_STATE(b13)`）。

### 2.3 终端电阻 / 负载上报（请求 37 / 36）

```c
struct vcan_usb_can_config {            // 12 字节（含 8 字节 SETUP 头）
    struct vcan_usb_hdr hdr;            // flags=36(负载) 或 37(终端电阻)
    __le32 state;                       // 0=关, 非0=开
};
```

驱动把它接到了标准 SocketCAN 终端 API：`ip link set canX type can termination 120/0`
即下发 `CAN_TERMINATION(37)`。负载上报独立由 `CAN_BUS_LOAD(36)` 控制。

---

## 3. gs_usb 模式特殊 API（vkgs_usb）

### 3.1 帧格式（标准 gs_usb host_frame）

```
0   echo_id   4   TX时填回显槽位; RX=0xFFFFFFFF; 事件帧见下
4   can_id    4   标志位在 can_id 高位：EFF=0x80000000 RTR=0x40000000 ERR=0x20000000
8   can_dlc   1
9   channel   1
10  flags     1   OVERFLOW(b0) FD(b1) BRS(b2) ESI(b3)
11  reserved  1
12  data[]    8(经典)/64(FD)            // 本驱动不开硬件时间戳，故无尾部时间戳
```

bulk‑IN 上的异步事件帧（按 `echo_id` 区分，与数据帧混在同一端点）：

| echo_id | 事件 | 结构（字节） |
|---|---|---|
| `0xFFFFFFFF` | 普通接收帧 | host_frame |
| `0xA4C95E3D` | 状态变化 | `gs_state_ext`(28)：state/rxerr/txerr |
| `0xA6C95E3D` | 总线错误 | `gs_berr_ext`(16) |
| `0xA3C95E3D` | 总线负载 | `gs_load`(28) |

> 注意：`vkgs_usb` 的帧**没有长度字段**，接收侧只能按 `echo_id`（事件帧定长）和
> FD 标志（数据帧 8/64 固定）推断长度后步进。这对本固件是正确的，但不如 VCAN 模式
> 自带 opcode 长度前缀那样“自描述”。

### 3.2 控制请求（USB vendor，RECIP_INTERFACE）

- `bmRequestType`：写=`0x41`，读=`0xC1`。
- `bRequest`：请求码（标准 gs_usb 0..14，扩展 33..37）。
- `wValue`：通道号（设备实际按接口路由，可填通道号）。
- `wIndex`：**通道号 = 接口号**（设备据此路由到对应接口的 handler）。
- 数据：**纯结构体，不带额外协议头**（与 VCAN 模式不同）。

标准 gs_usb 请求：`HOST_FORMAT(0) BITTIMING(1) MODE(2) BERR(3) BT_CONST(4)
DEVICE_CONFIG(5) TIMESTAMP(6) IDENTIFY(7) DATA_BITTIMING(10) BT_CONST_EXT(11)
SET_TERMINATION(12) GET_TERMINATION(13) GET_STATE(14)`。

> 差异：本固件 `GET_STATE(14)` 返回的是**扩展结构** `gs_state_ext`(28 字节)，
> 不是 mainline 的 12 字节 `gs_device_state`；且**不**通告
> `GS_CAN_FEATURE_TERMINATION`，因此终端电阻走扩展请求 `CAN_TERMINATION(37)`。

扩展请求（本文件重点）：

| bRequest | 名称 | 方向 | 载荷（字节） | 用途 |
|---|---|---|---|---|
| 33 | BSP_DEVICE_INFO | 读 | `gs_bsp_device_info`(40)：sw/hw/uid[4]/uuid[4] | 读版本/UID/UUID（`wValue=0`，否则进 OTA） |
| 34 | USB_MODE | 写 | 1 字节：模式值 | 切换协议模式，持久化+重启 |
| 35 | CAN_FILTERS | 读/写 | `gs_usb_breq_can_filter`(16) | 硬件 ID 滤波表 |
| 36 | CAN_BUS_LOAD | 读/写 | `gs_device_can_config`(4) | 负载上报使能 |
| 37 | CAN_TERMINATION | 读/写 | `gs_device_can_config`(4) | 终端电阻使能 |

### 3.3 终端电阻 / 负载上报（请求 37 / 36）

```c
struct gs_device_can_config {           // 4 字节
    __le32 state;                       // 0=关, 1=开
};
```

驱动同样接到了 `ip link set canX type can termination 120/0`，下发 `CAN_TERMINATION(37)`。

---

## 4. USB 模式切换（VCAN / PEAK / GS_USB）

切换设备协议模式需向 `USB_MODE` 请求写入目标模式值，固件会**写 Flash 并重启**，
重启后以新协议重新枚举（USB ID 也随之变为 `6080` 或 `606f`）。该操作**不经 SocketCAN**，
可用仓库的 `canctl usb_mode` 或 Python 类方法完成。

模式值：`0=VCAN`、`1=PEAK_CAN`、`2=GS_USB`。

```bash
# --interface 是当前协议；vcan 是目标协议；通道不可省略
sudo ./canctl --interface vkgs_usb --channel 0 usb_mode vcan
```

正常 Bus 初始化不会发送 USB_MODE。切换实现使用短生命周期控制会话，不启动 bulk
接收池、不配置 MCAN，也不为了切换模式反复打开/关闭数据端点。

> 重要差异：
> - **VCAN 模式**（请求 34）：载荷为带 SETUP 头的 `vcan_usb_device_usb_mode`
>   结构，`mode` 字段为 1 字节模式值，`wIndex=通道号`。
> - **gs_usb 模式**（请求 34）：载荷为**裸 1 字节**模式值，`wIndex=通道号`。

### 4.1 用 pyusb 切换（示例）

```python
# pip install pyusb ；需 root 或 udev 规则
import usb.core, usb.util, struct

# --- 从 gs_usb(606f) 切到 VCAN(=0)：gs 模式载荷是裸 1 字节 ---
dev = usb.core.find(idVendor=0x1d50, idProduct=0x606f)
dev.ctrl_transfer(0x41, 34, 0x0000, 0x0000, bytes([0]))   # bRequest=34, wIndex=ch0, data=[0]
# 设备将持久化并重启，之后以 1d50:6080 重新枚举

# --- 从 VCAN(6080) 切到 gs_usb(=2)：VCAN 模式载荷需带 SETUP 头 ---
dev = usb.core.find(idVendor=0x1d50, idProduct=0x6080)
SETUP = 0xA5C95E3D
ch = 0
size = 12                                  # sizeof(vcan_usb_device_usb_mode)
opcode = ((ch << 12) & 0xF000) | (size & 0x0FFF)
REQ = 34
payload = struct.pack('<IHH B 3x', SETUP, opcode, REQ, 2)   # echo_id,opcode,flags,mode=2,rsv[3]
dev.ctrl_transfer(0x41, REQ, 0x0000, ch, payload)
# 设备持久化并重启，之后以 1d50:606f 重新枚举
```

切换后按《SocketCAN使用手册》§1 加载对应驱动（切到 gs_usb 记得先屏蔽内核 `gs_usb`）。

### 4.2 读取版本/UID（不切换、安全示例）

```python
import usb.core, struct
dev = usb.core.find(idVendor=0x1d50, idProduct=0x606f)        # gs_usb 模式
data = dev.ctrl_transfer(0xC1, 33, 0x0000, 0x0000, 40)        # GET_BSP_DEVICE_INFO, wValue=0
sw, hw = struct.unpack_from('<II', data, 0)
uid = struct.unpack_from('<4I', data, 8)
print(f"sw=0x{sw:08x} hw=0x{hw:08x} uid={[hex(x) for x in uid]}")
```

VCAN 模式读版本：`bRequest=0x80|33=0xA1`，`wValue=0`，返回 48 字节
`vcan_usb_bsp_device_info`（前 8 字节为 SETUP 头，之后 sw/hw/uid/uuid）。

---

## 5. 两种模式速查对比

| 项目 | VCAN（vcan_usb） | gs_usb（vkgs_usb） |
|---|---|---|
| USB ID | `1d50:6080` | `1d50:606f`（需屏蔽内核 gs_usb） |
| 帧长度 | 每帧 opcode 自带长度（自描述） | 无长度字段，按 echo_id/FD 推断 |
| ID 标志位位置 | 帧 `flags` 字段 | `can_id` 高位 |
| 控制载荷 | 需带 SETUP 公共头 | 裸结构体 |
| 终端电阻请求 | `CAN_TERMINATION(37)`（带头，12B） | `CAN_TERMINATION(37)`（裸，4B） |
| 版本请求 | `0xA1`(=0x80\|33)，48B（带头） | `33`，40B（裸） |
| GET_STATE | `CAN_STATE(3)` 读，28B | `GET_STATE(14)` 读，扩展 28B |
| RX 合并解析 | 按 opcode 步进 + 通道校验 + 跳坏帧 | 按 echo_id/FD 推断步进 |
| 兼容 mainline gs_usb | 否（自研协议） | 协议兼容，但需本驱动处理无 TX echo/寄存器位时序/扩展请求 |

> 共同点（两个驱动都已实现并经实测验证）：接口独立、端点描述符识别、不写死端点、
> TX 于 URB 完成回显、位时序寄存器编码（减 1 并折叠 prop_seg）、bulk 多帧合并解析。

---

## 6. 固件能力与后续 Python API 取舍

根据固件请求处理代码，除当前收发、位时序、终端电阻、负载上报和 `usb_mode` 外，
还存在以下候选能力：

| 能力 | 固件状态 | Python API 建议 |
|---|---|---|
| BSP 版本、UID、UUID | 两种协议均实现 | 可增加独立的 `info` 管理命令；不放进正常 Bus 初始化 |
| 硬件 ID filter | 两种协议均实现，标准/扩展各自索引 | 可增加显式硬件过滤 API；不能冒充 python-can 软件 `can_filters` |
| bus-load 当前开关读取 | 两种协议均支持 GET | 可补 `get_bus_load_reporting()`，优先级较低（`set_bus_load()` 已有，读回暂缺） |
| identify LED | GS_USB 实现请求 7；VCAN 仅通告 feature、未见对应处理请求 | **已实现**：内核驱动 `vkgs_usb` 接了 `ethtool -p`，Python `vkgs_usb` 侧新增 `bus.identify()`；`vcan_usb` 两侧均未接（固件未实现） |
| timing constants/device config/timestamp | 固件已实现协议查询 | 适合诊断/能力发现，不应成为每次打开必需请求 |
| GET_STATE/BERR | 固件存在但两协议结构不同 | **已实现**：两个 Python 包的 `parse_bulk()` 现在会解码 STATE/BERR 事件帧，`bus.state`（python-can 标准 ACTIVE/PASSIVE 两档）和 `bus.get_berr_counter()`（细粒度 rxerr/txerr）都由异步事件被动更新，不额外发起 USB 请求 |
| USER_ID | GS_USB 枚举中明确标记 not implemented | 不应暴露 |
| BSP_INFO 的 OTA 路径 | 非零 `wValue` 可进入升级流程 | 高风险，必须与普通 info API 隔离，当前不暴露 |

硬件过滤最值得后续实现，但需要先确定“未配置槽位、拒绝规则、FIFO 路由、标准/扩展
索引上限”的稳定公共数据模型。直接把 python-can 的 `can_filters` 写进硬件会改变其软件
过滤语义和动态更新行为，因此当前继续由 python-can 软件层处理更安全。

## PCAN 模式管理

PCAN 兼容模式的只读身份查询、终端电阻和模式切换使用独立管理帧接口；格式、保留 ID 与身份核对流程见 [PCAN 模式设备管理接口](PCAN设备管理.md)。
