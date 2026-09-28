# SocketCAN 使用手册

先按[快速入门](../start/快速入门.md)确认模式和接线，安装步骤见[Linux 驱动安装](Linux内核驱动安装.md)。本页按 `canX` 的配置、收发、诊断和故障排查顺序说明 Linux SocketCAN；完整压力测试见[验收路线](../verify/验证路线.md)。

支持模式：

| 固件模式 | USB ID | 绑定内核模块 | 冲突模块 |
|---|---|---|---|
| VCAN | `1d50:6080` | `vcan_usb` | 无 |
| VKGS/gs_usb 扩展 | `1d50:606f` | `vkgs_usb` | `gs_usb` |

厂商控制请求见[驱动特殊 API](../reference/驱动特殊API说明.md)；C/C++、CANopen 或 DBC 项目可查阅[`socket-can-skill`](../../socket-can-skill/SKILL.md)。

## 1. 设备拓扑与所有权

- 一个 **USB interface** 对应一个 SocketCAN netdev，通常也是一个独立 CAN 通道（各自有独立 IN/OUT）；
- `channel` = USB interface 的软件索引（`USB bInterfaceNumber`）；
- `canX` = Linux 分配的网卡名（如 `can0`、`can1`）；
- 驱动按 `interface` 逐个 probe，每个接口注册一个 `canX`，**不会写死通道数**；
- `canX` 与 USB `channel` 命名不等价（`can0/can1` 由 Linux 分配）；
- SocketCAN 与 Python 后端不能并发 claim 同一 interface；
- 正常开停 `canX` 不应做设备级重配置。

可快速查看接口归属：

```bash
ip -br link show type can
readlink -f /sys/class/net/can0/device
basename "$(readlink -f /sys/class/net/can0/device/driver)"
```

## 2. 编译与加载

Ubuntu/Debian 环境：

```bash
sudo apt update
sudo apt install build-essential linux-headers-$(uname -r) can-utils
```

VCAN：

```bash
cd /path/to/VCANDrive/kernel/vcan_usb
make
sudo make install
sudo depmod -a
sudo modprobe vcan_usb
```

VKGS：

```bash
cd /path/to/VCANDrive/kernel/vkgs_usb
make
sudo make install
sudo depmod -a
sudo modprobe -r gs_usb
sudo modprobe vkgs_usb
```

### 2.1 处理 gs_usb 冲突

`1d50:606f` 会被主线 `gs_usb` 先匹配。先临时卸载并确认 `vkgs_usb` 工作正常；只有在
系统没有其他必须使用主线 `gs_usb` 的设备时，才创建
`/etc/modprobe.d/blacklist-gs_usb.conf`：

```text
blacklist gs_usb
install gs_usb /bin/false
```

Ubuntu/Debian 再执行 `sudo update-initramfs -u`，卸载 `gs_usb`、加载 `vkgs_usb` 并
重新插拔。取消时删除该文件并再次更新 initramfs。完整流程见
[Linux 内核驱动安装](Linux内核驱动安装.md)。

## 3. CAN 配置

所有配置应在接口 down 后进行。

### 3.1 经典 CAN

```bash
sudo ip link set can0 down
sudo ip link set can0 type can bitrate 500000
sudo ip link set can0 up
```

### 3.2 CAN FD

```bash
sudo ip link set can0 down
sudo ip link set can0 type can \
    bitrate 1000000 sample-point 0.750 \
    dbitrate 5000000 dsample-point 0.750 \
    fd on
sudo ip link set can0 up
```

### 3.3 常用控制开关

```bash
sudo ip link set can0 type can termination 120
sudo ip link set can0 type can termination 0
sudo ip link set can0 type can restart-ms 100
sudo ip link set can0 type can loopback on
sudo ip link set can0 type can listen-only on
sudo ip link set can0 type can one-shot on
sudo ip link set can0 type can berr-reporting on
```

`ip -details link show can0` 查看 `supported`、`berr-counter` 与状态。

## 4. can-utils 实操

### 4.1 接收

```bash
candump can0
candump -ta -e can0
candump can0 can1
candump any
candump -L can0 > capture.log
candump 'can0,123:7FF'
```

### 4.2 发送

```bash
cansend can0 123#1122334455667788
cansend can0 18DAF110#01020304
cansend can0 123#R8
```

FD 示例：

```bash
cansend can0 123##00102030405060708    # FD, BRS off
cansend can0 123##10102030405060708    # FD, BRS on
```

### 4.3 诊断与回放

```bash
cangen can0 -g 10 -I 123 -L 8 -D i
cangen can0 -g 1 -f -b -I 123 -L 64
canplayer -I capture.log
canbusload can0@1000000
cansniffer can0
```

## 5. 自动验收脚本

先按[快速入门](../start/快速入门.md)完成两路接线和 `candump`/`cansend` 第一帧。在隔离测试总线上，从仓库根目录**只运行当前模式对应的一个脚本**。脚本会编译/重载模块、配置两路接口和终端，并发送真实流量：

```bash
sudo bash ./kernel/tests/vcan_usb/test_vcan_usb.sh
sudo bash ./kernel/tests/vkgs_usb/test_vkgs_usb.sh
```

脚本依次覆盖 250 kbit/s 混合帧、ECU 周期/事件/诊断流量、ISO-TP、J1939 TP、序号完整性、FD 往返、实测总线负载、1 Mbit/s 混合帧、FD 压力与重复打开。默认每个发送端、每个流量阶段 10000 帧，重复打开 5 次；普通压力流量按总线背压持续发送，ECU 周期场景保留周期。需要保持已加载驱动并延长高负载测试时，可在确认当前模块归属后执行：

```bash
sudo bash ./kernel/tests/vkgs_usb/test_vkgs_usb.sh --no-build --frames 20000
```

`--frames` 作用于所有按帧数运行的流量项目，默认 10000；ISO-TP/J1939 TP 按协议载荷大小验证。FD 往返固定最多 50 帧在途，这是发送窗口，并非总帧数。SocketCAN 队列满时等待可写，不忽略写入失败；故障保护超时只用于防止断线时无限等待。缺少 FD 或必需工具会失败；仅全部完成时输出 `COMPLETE SOCKETCAN STRESS TEST PASSED`。环境要求、Python USB 测试与本脚本的覆盖差异见[验收测试路线](../verify/验证路线.md)。

## 6. 常见问题与定位

```bash
ip -details link show can0
ip -details -statistics link show can0
candump -e can0
sudo dmesg --follow
```

重点字段：

- `ERROR-ACTIVE / ERROR-WARNING / ERROR-PASSIVE / BUS-OFF`
- `berr-counter tx/rx`
- `RX/TX dropped`
- `termination`

典型现象与对应判断：

- TX 长期 error 且无 RX：检查接线、波特率一致性、ACK 节点数量；
- FD 都显示为 0：确认接口已 `fd on` 且报文格式是 `##`；
- BRS 失败：确认所有节点一致的数据位参数；
- BUS-OFF：先修复物理问题，再考虑重启/重试。

`Device or resource busy` 往往是 Python/libusb 或其他驱动仍占用 interface，
建议统一切换到 Python 后端时先 `ip link set canX down` 并释放驱动。

## 7. C SocketCAN API（简明）

文档保留最小示例，完整语义请结合内核 `man 7 can`, `man 8 ip-link`。

```c
#include <linux/can.h>
#include <linux/can/raw.h>
#include <sys/socket.h>
#include <sys/ioctl.h>
#include <net/if.h>
#include <unistd.h>

int fd = socket(PF_CAN, SOCK_RAW, CAN_RAW);
struct ifreq ifr = {0};
strncpy(ifr.ifr_name, "can0", IFNAMSIZ - 1);
ioctl(fd, SIOCGIFINDEX, &ifr);

struct sockaddr_can addr = { .can_family = AF_CAN, .can_ifindex = ifr.ifr_ifindex };
bind(fd, (struct sockaddr *)&addr, sizeof(addr));

struct can_frame tx = {
    .can_id = 0x123,
    .can_dlc = 8,
    .data = {0x11, 0x22},
};
write(fd, &tx, sizeof(tx));

struct can_frame rx;
read(fd, &rx, sizeof(rx));
close(fd);
```

FD 发送：

```c
int enable = 1;
setsockopt(fd, SOL_CAN_RAW, CAN_RAW_FD_FRAMES, &enable, sizeof(enable));

struct canfd_frame txfd = {
    .can_id = 0x123,
    .len = 64,
    .flags = CANFD_BRS,
};
write(fd, &txfd, sizeof(txfd));
```

## 8. DBC 与 CANopen

SocketCAN 只传输帧；DBC signal codec 与 CANopen 协议栈都运行在用户态：

```bash
candump can0 | python3 -m cantools decode network.dbc
python3 -m cantools generate_c_source --database-name vehicle network.dbc
```

Python CANopen 栈可直接连接已配置好的接口：

```python
import canopen

network = canopen.Network()
network.connect(interface="socketcan", channel="can0")
try:
    node = network.add_node(6, "device.eds")
finally:
    network.disconnect()
```

DBC 不能替代 CANopen EDS/DCF；SocketCAN error frame 也不同于 CANopen EMCY。完整的
COB-ID、PDO/SDO/NMT、DBC 字节序与测试说明见
[CANopen 与 DBC 开发指南](../protocol/CANopen与DBC开发指南.md)。

## 9. 与 Python 后端切换

回到 SocketCAN：

```bash
sudo ip link set can0 down
sudo ip link set can1 down
sudo modprobe -r vcan_usb
sudo modprobe -r vkgs_usb
sudo modprobe -r gs_usb
```

```bash
sudo modprobe vcan_usb
# 或
sudo modprobe -r gs_usb
sudo modprobe vkgs_usb
```

切回 Python 前确保 `canX` 全部 down 并关闭所有 Python Bus。
