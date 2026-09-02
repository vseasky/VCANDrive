# VCAN USB Linux 内核驱动

本驱动为 HPMicro VCAN USB‑CAN(FD) 设备（VID:PID `1d50:6080`）提供 SocketCAN 接口。协议层与
`../../ref/firmware/vcan_0_0_2` 保持一致。

> 中文说明：
> [SocketCAN 使用手册](../../docs/SocketCAN使用手册.md)、
> [驱动特殊 API 说明](../../docs/驱动特殊API说明.md)

## 设备拓扑与命名模型

- 设备按 **USB interface** 分配通道：每个 interface 各有一组独立 IN/OUT Bulk 端点；
- 驱动按 interface 逐个 probe，动态注册 SocketCAN netdev；
- 一个带 N 路的硬件会注册 `can0 .. canN-1`（不会写死通道数量）；
- 每个 interface 独立 bring-up 与状态管理。

## 协议特征速记

- 帧为自描述结构：`{echo_id, opcode, flags}` + CAN 数据；
  `opcode = (channel << 12) | byte_size`。
- 固件不回显 TX；发送完成以 bulk‑OUT 回调为准，即 `TX` 阶段不会等待主机重入。
- 位时序为寄存器编码：固件对各段 +1、并忽略 `prop_seg`，所以主机下发时使用
  `brp-1`、`(prop_seg + phase_seg1)-1`、`phase_seg2-1`、`sjw-1`。
- 软件版本默认 80MHz，可在探测阶段读取并打印固件 `sw/hw/UID`。
- 终端电阻走 `VCAN_USB_BREQ_CAN_TERMINATION(37)`，与标准 SocketCAN `ip link set canX type can termination` 对应；
  总线负载上报通过 `VCAN_USB_BREQ_CAN_BUS_LOAD(36)`。
- 若固件上报了 `CAN_CTRLMODE_BERR_REPORTING` capability，`berr-reporting on` 会启用标准
  `CAN_ERR_PROT_*` 事件帧输出。

## 编译与加载

```bash
cd kernel/vcan_usb

make
sudo make install
sudo depmod -a
sudo modprobe vcan_usb
```

卸载：

```bash
sudo modprobe -r vcan_usb
```

### DKMS

```bash
sudo cp -r . /usr/src/vcan_usb-1.0.0
cd /usr/src/vcan_usb-1.0.0
sudo dkms add -m vcan_usb -v 1.0.0
sudo dkms build -m vcan_usb -v 1.0.0
sudo dkms install -m vcan_usb -v 1.0.0
```

## 运行示例

```bash
sudo ip link set can0 down
sudo ip link set can0 up type can bitrate 500000
# CAN FD
sudo ip link set can0 up type can bitrate 1000000 dbitrate 5000000 fd on
sudo ip link set can0 type can termination 120
candump can0
cansend can0 123#11223344
```

## 验收脚本

`../tests/vcan_usb/test_vcan_usb.sh` 会编译并重载模块，在有两路物理通道的设备上执行 6 阶段收发对照。
脚本要求两路 `can` 在同总线 CANH↔CANH、CANL↔CANL 连接。

```bash
sudo ../tests/vcan_usb/test_vcan_usb.sh
sudo env NFRAMES=100 BUILD=0 ../tests/vcan_usb/test_vcan_usb.sh
```

- `NFRAMES`：每个阶段发送数量；
- `BUILD=0`：不重编/重载，仅用当前加载的模块。

该脚本将每次发送与接收计数做严格比对，不是“能收多少算多少”。
