# VKGS USB Linux 内核驱动

本驱动为 HPMicro VKGS/gs_usb 扩展（VID:PID `1d50:606f`）提供 SocketCAN 接口，兼容
candleLight/gs_usb 的基础帧结构并补充厂商扩展能力。
协议逻辑与 `../../ref/firmware/vcan_0_0_2` 对齐。

> 中文说明：
> [SocketCAN 使用手册](../../docs/SocketCAN使用手册.md)、
> [驱动特殊 API 说明](../../docs/驱动特殊API说明.md)

## 设备拓扑与控制路由

- 一路 CAN 对应一个 USB interface，每个 interface 有独立 IN/OUT Bulk 端点；
- `wIndex` 路由到接口，因此每个接口可独立 probe 与注册为独立 `canX`；
- 不固定通道数，依设备 interface 数量动态生成。

## 协议与能力速记

- 使用标准 gs_usb 帧布局（`echo_id/can_id/can_dlc/channel/flags/data`）；
  事件帧与普通帧共用 IN 通道，由 `echo_id` 区分。
- 固件不回显 TX，发送完成以 bulk‑OUT 回调为准；
- 位时序同样是寄存器编码：主机下发 `brp-1`、`(prop_seg + phase_seg1)-1`、`phase_seg2-1`、`sjw-1`；
- 终端电阻优先使用 `VKGS_USB_BREQ_CAN_TERMINATION(37)`，兼容保留 `SET/GET_TERMINATION(12/13)`；
- 总线负载上报通过 `VKGS_USB_BREQ_CAN_BUS_LOAD(36)`，默认不强制开启；
- `berr-reporting` 支持在 capability 生效后可按 `ip link set canX type can berr-reporting on/off` 切换。

`VKGS` 还支持 `identify` 能力：

```bash
sudo ip link set can0 down   # 示例
sudo ip link set can0 type can termination 120
ethtool -p can0 5
```

## 与内核原生 gs_usb 冲突处理

设备使用 `1d50:606f`，可能与内核主线 `gs_usb` 冲突，建议长期使用前先禁用主线驱动。

```bash
echo "blacklist gs_usb" | sudo tee /etc/modprobe.d/blacklist-gs_usb.conf
sudo modprobe -r gs_usb
sudo modprobe vkgs_usb
```

若不再需要本驱动，删除 blacklist 并重载即可恢复主线优先策略。

## 编译与加载

```bash
cd kernel/vkgs_usb

make
sudo make install
sudo depmod -a
sudo modprobe vkgs_usb
```

卸载：

```bash
sudo modprobe -r vkgs_usb
```

### DKMS

```bash
sudo cp -r . /usr/src/vkgs_usb-1.0.0
cd /usr/src/vkgs_usb-1.0.0
sudo dkms add -m vkgs_usb -v 1.0.0
sudo dkms build -m vkgs_usb -v 1.0.0
sudo dkms install -m vkgs_usb -v 1.0.0
```

## 运行示例

```bash
sudo ip link set can0 down
sudo ip link set can0 up type can \
    bitrate 1000000 sample-point 0.75 dbitrate 5000000 dsample-point 0.75 fd on
sudo ip link set can0 type can termination 120
candump can0
cansend can0 123#11223344
```

## 验收脚本

`../tests/vkgs_usb/test_vkgs_usb.sh` 会卸载 `gs_usb` 后加载本驱动，并执行六段测试矩阵。
同样要求两路 CAN 接线后再运行。

```bash
sudo ../tests/vkgs_usb/test_vkgs_usb.sh
sudo env NFRAMES=100 BUILD=0 ../tests/vkgs_usb/test_vkgs_usb.sh
```

默认模式会对测试脚本执行的 phase 统计进行严格比对：
`发送数 = 接收数`。

如脚本报 `command not found`（无执行位）可临时用 bash 直跑：

```bash
sudo bash ../tests/vkgs_usb/test_vkgs_usb.sh
sudo env NFRAMES=5 bash ../tests/vkgs_usb/test_vkgs_usb.sh
```

