# SocketCAN 使用手册

本文档面向 Linux 下的 SocketCAN 使用流程：驱动加载、CAN 配置、统计诊断、验证脚本与
故障排查。

支持模式：

| 固件模式 | USB ID | 绑定内核模块 | 冲突模块 |
|---|---|---|---|
| VCAN | `1d50:6080` | `vcan_usb` | 无 |
| VKGS/gs_usb 扩展 | `1d50:606f` | `vkgs_usb` | `gs_usb` |

厂商控制请求与协议细节见《[驱动特殊 API 说明](驱动特殊API说明.md)》，Python 方案见
《[PythonCAN 使用手册](PythonCAN使用手册.md)》。首次安装或内核升级请先看
《[Linux 内核驱动安装](Linux内核驱动安装.md)》；开发 C/C++、CANopen 或 DBC 项目时可查阅
[`socket-can-skill`](../socket-can-skill/SKILL.md)。

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
cd /path/to/04.VCANDrive/kernel/vcan_usb
make
sudo make install
sudo depmod -a
sudo modprobe vcan_usb
```

VKGS：

```bash
cd /path/to/04.VCANDrive/kernel/vkgs_usb
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

脚本会自动编译并加载对应驱动、发现映射到该驱动的 `canX`，并执行六段矩阵：

1. 共享总线经典 CAN
2. 共享总线 FD/BRS-off
3. 共享总线 FD/BRS-on
4. 独立经典 CAN 内部回环
5. 独立 FD/BRS-off
6. 独立 FD/BRS-on

默认每阶段发送 60 帧。

```bash
sudo /path/to/04.VCANDrive/kernel/tests/vcan_usb/test_vcan_usb.sh
sudo /path/to/04.VCANDrive/kernel/tests/vkgs_usb/test_vkgs_usb.sh
```

可通过环境变量控制测试规模：

```bash
sudo env BUILD=0 NFRAMES=20 /path/to/04.VCANDrive/kernel/tests/vkgs_usb/test_vkgs_usb.sh
```

- `BUILD=0`：不重编不重载，适用于确认当前已加载模块正确时；
- `NFRAMES`：每阶段发送帧数。

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
[CANopen 与 DBC 开发指南](CANopen与DBC开发指南.md)。

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
