# VCANDrive Linux 接入与运维参考

## 模式与驱动

| 设备模式 | USB ID | 模块 | 源码目录 | 备注 |
|---|---|---|---|---|
| VCAN 原生 | `1d50:6080` | `vcan_usb` | `kernel/vcan_usb` | VCAN 自有 USB 协议 |
| GS_USB / VKGS | `1d50:606f` | `vkgs_usb` | `kernel/vkgs_usb` | 可能被内核 `gs_usb` 先绑定 |

每个 USB interface 注册一个 SocketCAN netdevice。`can0/can1` 是 Linux 动态分配的名字，不保证与面板通道号一致。

## 安装前检查

```bash
uname -r
lsusb | grep -Ei '1d50:6080|1d50:606f'
ip -br link show type can
lsmod | grep -E '(^|_)vcan_usb|(^|_)vkgs_usb|(^|_)gs_usb'
```

Ubuntu/Debian 依赖：

```bash
sudo apt update
sudo apt install -y build-essential linux-headers-$(uname -r) \
    iproute2 can-utils dkms usbutils
test -e /lib/modules/$(uname -r)/build && echo "kernel headers OK"
```

必须使用与当前运行内核匹配的 headers。Secure Boot 可能拒绝未签名的 out-of-tree 模块，应按发行版流程签名或调整启动策略，不要把签名失败误判为 CAN 参数错误。

## 手动构建与加载

VCAN 原生：

```bash
cd kernel/vcan_usb
make
sudo make install
sudo depmod -a
sudo modprobe vcan_usb
```

VKGS：

```bash
sudo modprobe -r gs_usb
cd kernel/vkgs_usb
make
sudo make install
sudo depmod -a
sudo modprobe vkgs_usb
```

先临时卸载 `gs_usb` 验证 `vkgs_usb`，再决定是否持久屏蔽。永久屏蔽前确认系统没有其他必须使用主线 `gs_usb` 的设备；配置 `/etc/modprobe.d/blacklist-gs_usb.conf`：

```text
blacklist gs_usb
install gs_usb /bin/false
```

之后按发行版需要更新 initramfs 并重新插拔。恢复时删除该文件、更新 initramfs，再加载 `gs_usb`。

## 确认接口归属

```bash
for n in /sys/class/net/can*; do
    [ -e "$n" ] || continue
    printf '%s usb=%s driver=%s\n' \
        "$(basename "$n")" \
        "$(basename "$(readlink -f "$n/device")")" \
        "$(basename "$(readlink -f "$n/device/driver")")"
done
```

应用配置只保存 `canX` 时，需要另外提供稳定命名或启动时的 sysfs 映射检查，避免插拔后误连另一通道。

## 经典 CAN 配置

```bash
sudo ip link set can0 down
sudo ip link set can0 type can \
    bitrate 500000 sample-point 0.75 restart-ms 100
sudo ip link set can0 txqueuelen 1000
sudo ip link set can0 up
ip -details link show can0
```

## CAN FD 配置

```bash
sudo ip link set can0 down
sudo ip link set can0 type can \
    bitrate 1000000 sample-point 0.75 \
    dbitrate 5000000 dsample-point 0.75 \
    fd on fd-non-iso off restart-ms 100
sudo ip link set can0 txqueuelen 1000
sudo ip link set can0 up
ip -details link show can0
```

设备 CAN 时钟为 80 MHz。若速率和采样点无法组成合法位时序，`ip` 会拒绝配置；先去掉显式采样点或选择常见速率，再从 `ip -details` 读取实际 TQ/BRP/TSEG/SJW。

## 终端电阻与控制模式

只在物理总线两个末端启用 120 Ω，断电测量两个终端并联通常约 60 Ω：

```bash
sudo ip link set can0 down
sudo ip link set can0 type can termination 120
sudo ip link set can0 type can bitrate 500000 restart-ms 100
sudo ip link set can0 up
ip -details link show can0 | grep termination
```

可在接口 down 时组合设置：

```bash
sudo ip link set can0 type can bitrate 500000 listen-only on
sudo ip link set can0 type can bitrate 500000 loopback on
sudo ip link set can0 type can bitrate 500000 one-shot on
sudo ip link set can0 type can bitrate 500000 berr-reporting on
```

- `listen-only` 不发送 ACK，适合被动监听。
- `loopback` 是控制器内部回环，不是 socket 自收发回显。
- `one-shot` 禁止 CAN 控制器自动重传。
- `berr-reporting` 允许驱动上报协议错误帧；应用仍需设置 error filter 才能接收。

## can-utils 快速验证

```bash
candump -ta -e can1
cansend can0 123#11223344
cansend can0 18DAF110#01020304
cansend can0 123##00001020304050607
cansend can0 123##10001020304050607
```

CAN FD 使用 `##`；后面的 flags 低位控制 BRS。先启动接收端，再从另一物理节点发送，确保总线上存在 ACK 节点。

记录、过滤与回放：

```bash
candump -L can0 > capture.log
candump 'can0,123:7FF'
canplayer -I capture.log
cansniffer can0
canbusload can0@500000
```

高负载 `cangen` 会真实占用总线，不要在未知或生产网络上直接运行。

## 状态诊断

```bash
ip -details -statistics link show can0
candump -e can0
sudo dmesg --follow
```

- TX error 持续增长：优先检查 ACK 节点、位速率、接线和终端。
- RX error 持续增长：检查采样点、信号质量、H/L 和干扰。
- 经典 CAN 正常而 FD 失败：核对数据段速率、BRS 与 ISO/non-ISO。
- BUS-OFF：先停止发送并修复物理问题，再等待 `restart-ms` 或执行 `ip link set can0 type can restart`。
- `Device or resource busy`：检查 Python/libusb、另一模块或进程是否仍占用 interface。

## 仓库硬件验收

两通道接在同一总线且终端正确后：

```bash
sudo ./kernel/tests/vcan_usb/test_vcan_usb.sh
sudo bash ./kernel/tests/vkgs_usb/test_vkgs_usb.sh
```

可用 `sudo env BUILD=0 NFRAMES=20 ...` 调整。脚本会重配接口、启用终端并发送真实流量，只在隔离测试总线上执行。

## 与 Python USB 后端切换

同一 USB interface 不能同时由 SocketCAN 与 Python backend 使用。切到 Python 前先停接口并卸载占用模块；切回 SocketCAN 前确保所有 Python `Bus` 已 `shutdown()`，再加载与 VID:PID 匹配的模块。
