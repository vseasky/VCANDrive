# VCAN USB Linux Kernel Driver

Out-of-tree Linux SocketCAN driver for the VCAN USB-CAN(FD) mode.

> 中文文档：[SocketCAN 使用手册](../../udocs/SocketCAN使用手册.md)、
> [驱动特殊 API 说明](../../udocs/驱动特殊API说明.md)。

## Topology

The device exposes **one USB interface per CAN channel**, each with its own bulk
IN/OUT endpoint pair. The driver probes per interface and registers one
SocketCAN netdev per interface — it does **not** hard‑code the channel count, so
a device with N interfaces yields `can0 .. canN-1`. Each interface is fully
independent (own endpoints, own bring‑up).

## Protocol notes

- Self‑describing frames: every frame starts with `{ echo_id, opcode, flags }`,
  `opcode = (channel << 12) | byte_size`. Data payload starts at offset 24.
- The firmware does **not** echo transmitted frames; TX is completed on the
  bulk‑OUT URB completion.
- Bit timing is register‑encoded (firmware adds 1 to each segment and ignores
  `prop_seg`): the host sends `brp-1`, `(prop_seg+phase_seg1)-1`, `phase_seg2-1`,
  `sjw-1`.
- Termination (120 Ω) uses `VCAN_USB_BREQ_CAN_TERMINATION` (37) and is wired
  to the standard SocketCAN termination API. Bus-load reporting can be controlled
  explicitly with `VCAN_USB_BREQ_CAN_BUS_LOAD` (36).
- Device version/UID/UUID is read via the HAL-specific
  `VCAN_USB_BREQ_BSP_DEVICE_INFO` (33) request and logged at probe. Software
  and hardware versions use `vMAJOR.MINOR.PATCH` notation.
- `berr-reporting` is advertised as a `CAN_CTRLMODE_BERR_REPORTING` capability
  when the firmware reports the matching feature bit; enabling it
  (`ip link set canX type can berr-reporting on`) makes the device push
  protocol-violation error frames (stuff/form/ACK/bit0/bit1/CRC) in addition to
  the always-on bus-off/error-passive/error-warning state frames.
- HW timestamps are intentionally not enabled, keeping the receive path simple
  and avoiding another version-dependent kernel interface.

## Kernel compatibility

The supported baseline is upstream Linux **4.12 or newer**. The local
`usbcan_compat.h` isolates CAN DLC helper renames and the independently changed
echo-SKB signatures in 5.12 and 5.13. Termination control and CAN FD remain
available across the whole supported range. Vendor kernels with backported APIs
may differ from their advertised version and should be build-tested separately.

## Build

```bash
make
sudo make install
sudo depmod -a
sudo modprobe vcan_usb
```

DKMS: copy this directory to `/usr/src/vcan_usb-1.1.4/`, then
`dkms add/build/install -m vcan_usb -v 1.1.4`.

## Usage

```bash
sudo ip link set can0 up type can bitrate 500000
# CAN FD:
sudo ip link set can0 up type can bitrate 1000000 dbitrate 5000000 fd on
sudo ip link set can0 type can termination 120   # 120 Ohm on (0 = off)
candump can0
cansend can0 123#11223344
```

## Test

Only the hardware stress entry remains:

```bash
sudo ../tests/vcan_usb/test_vcan_usb.sh
sudo env BUILD=0 DURATION=20 REOPEN_LOOPS=10 ../tests/vcan_usb/test_vcan_usb.sh
```

Requires two connected, terminated physical CAN channels and can-utils. The
suite covers mixed traffic, ISO-TP/J1939, sequence integrity, load, CAN FD and
close/open regression. Unsupported optional FD tools are reported as skips;
`REQUIRE_ALL=1` makes missing coverage fail. The old `NFRAMES` setting is no
longer used. Default execution builds/reloads the driver and leaves interfaces
down during cleanup. Use `sudo bash <script>` if its executable bit is missing.
See [test strategy](../tests/CAN_TEST_STRATEGY.md) for full coverage details.
