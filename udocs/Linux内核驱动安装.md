# Linux 内核驱动安装

第一次接线和收发请先按[快速入门](快速入门.md)完成。本文说明如何按设备当前 USB 模式安装 VCANDrive 的 SocketCAN 模块；完成安装后再阅读[SocketCAN 使用手册](SocketCAN使用手册.md)。

## 1. 确认设备模式

```bash
lsusb | grep -Ei '1d50:6080|1d50:606f'
dmesg | tail -80
```

| 检测结果 | 安装模块 | 目录 |
|---|---|---|
| `1d50:6080` | `vcan_usb` | `kernel/vcan_usb` |
| `1d50:606f` | `vkgs_usb` | `kernel/vkgs_usb` |

看不到设备时先检查数据线、供电、USB 归属和设备是否正在模式切换重启。虚拟机环境还需把重新枚举后的设备再次分配给 Linux 客体。

## 2. 安装构建依赖

Ubuntu/Debian：

```bash
sudo apt update
sudo apt install -y build-essential linux-headers-$(uname -r) \
    iproute2 can-utils dkms usbutils
test -e /lib/modules/$(uname -r)/build && echo "kernel headers OK"
```

必须安装与 `uname -r` 完全匹配的 headers。其他发行版请安装等价的 compiler、make、当前内核开发包、iproute2 和 can-utils。

## 3. 安装 VCAN 原生模块

```bash
cd /path/to/VCANDrive/kernel/vcan_usb
make
sudo make install
sudo depmod -a
sudo modprobe vcan_usb
```

验证：

```bash
lsmod | grep '^vcan_usb'
modinfo vcan_usb
ip -br link show type can
```

## 4. 安装 VKGS 模块

`1d50:606f` 可能已被 Linux 主线 `gs_usb` 绑定。先关闭其创建的接口，再临时释放：

```bash
sudo ip link set can0 down 2>/dev/null || true
sudo ip link set can1 down 2>/dev/null || true
sudo modprobe -r gs_usb
```

编译并加载本项目驱动：

```bash
cd /path/to/VCANDrive/kernel/vkgs_usb
make
sudo make install
sudo depmod -a
sudo modprobe vkgs_usb
```

重新插拔后验证：

```bash
lsmod | grep -E '^gs_usb|^vkgs_usb'
ip -br link show type can
```

### 4.1 持久避免 gs_usb 抢绑定

先确认系统没有其他必须使用主线 `gs_usb` 的 USB-CAN 设备，再创建 `/etc/modprobe.d/blacklist-gs_usb.conf`：

```text
blacklist gs_usb
install gs_usb /bin/false
```

Ubuntu/Debian 更新 initramfs：

```bash
sudo update-initramfs -u
sudo modprobe -r gs_usb 2>/dev/null || true
sudo modprobe vkgs_usb
```

恢复主线驱动时删除该配置、再次更新 initramfs，并加载 `gs_usb`。不要仅执行 `depmod -a` 后假设 initramfs 中的黑名单已恢复。

## 5. 确认每个 canX 的实际驱动

```bash
for n in /sys/class/net/can*; do
    [ -e "$n" ] || continue
    printf '%s -> %s\n' \
        "$(basename "$n")" \
        "$(basename "$(readlink -f "$n/device/driver")")"
done
```

预期显示 `vcan_usb` 或 `vkgs_usb`。`can0/can1` 由 Linux 动态命名，不能仅凭编号推断物理 CAN1/CAN2；多设备部署应通过 sysfs USB 拓扑或持久化命名规则固定映射。

## 6. 首次配置与收发

```bash
sudo ip link set can0 down
sudo ip link set can0 type can bitrate 500000 restart-ms 100
sudo ip link set can0 up
ip -details -statistics link show can0
```

在另一终端先接收：

```bash
candump -ta -e can0
```

从同一总线的另一节点发送：

```bash
cansend can1 123#11223344
```

确保 CAN_H/CAN_L 对应连接、所有节点位速率一致，并且物理总线两个末端各有 120 Ω 终端。只有一个节点时发送端通常得不到 ACK，错误计数会增长。

## 7. CAN FD

```bash
sudo ip link set can0 down
sudo ip link set can0 type can \
    bitrate 1000000 sample-point 0.75 \
    dbitrate 5000000 dsample-point 0.75 \
    fd on fd-non-iso off restart-ms 100
sudo ip link set can0 up

cansend can0 123##00001020304050607   # FD, BRS off
cansend can0 123##10001020304050607   # FD, BRS on
```

两端必须同时匹配仲裁段、数据段、ISO/non-ISO 和每帧 BRS 设置。

## 8. Secure Boot 与模块签名

`modprobe` 报 `Key was rejected by service` 或 `Operation not permitted` 时，通常是 Secure Boot 拒绝未签名模块。按发行版流程使用 Machine Owner Key 签名，或在符合组织安全策略的情况下调整 Secure Boot。不要通过修改 CAN 参数解决模块签名问题。

## 9. DKMS

两个驱动目录都包含 `dkms.conf`。使用 DKMS 前先打开该文件核对包名/版本与当前目录；将源码安装到发行版约定的 `/usr/src/<module>-<version>/`，再执行对应的 `dkms add/build/install`。内核升级后检查：

```bash
dkms status
modinfo vcan_usb   # 或 vkgs_usb
```

不同发行版对模块签名、`/usr/src` 和 initramfs 的策略不同，部署脚本应显式记录这些步骤，不要只判断 `make` 成功。

## 10. 自动硬件验收

把两个通道接入同一隔离 CAN 总线并正确设置终端，然后从仓库根目录运行：

```bash
sudo ./kernel/tests/vcan_usb/test_vcan_usb.sh
sudo bash ./kernel/tests/vkgs_usb/test_vkgs_usb.sh
```

脚本依次验证共享总线 classic CAN、FD/BRS-off、FD/BRS-on，以及两路独立内部回环。可用：

```bash
sudo env BUILD=0 NFRAMES=20 ./kernel/tests/vkgs_usb/test_vkgs_usb.sh
```

脚本会重配接口、启用终端并发送真实流量，不要在生产总线执行。

## 11. 卸载与切换 Python backend

```bash
sudo ip link set can0 down 2>/dev/null || true
sudo ip link set can1 down 2>/dev/null || true
sudo modprobe -r vcan_usb 2>/dev/null || true
sudo modprobe -r vkgs_usb 2>/dev/null || true
```

确认模块释放后，Python USB backend 才能独占相应 interface。切回 SocketCAN 前先关闭所有 Python `Bus`（`shutdown()` 或退出上下文），再加载匹配模块。
