# GS_CAN USB Linux Kernel Driver

Out-of-tree Linux SocketCAN driver for the GS_CAN USB-CAN(FD) mode.

> 新用户先看[快速入门](../../udocs/快速入门.md)。
> 中文文档：[SocketCAN 使用手册](../../udocs/SocketCAN使用手册.md)、
> [驱动特殊 API 说明](../../udocs/驱动特殊API说明.md)。

Start with the [quickstart](../../udocs/快速入门.md): identify the USB mode, connect two CAN channels with correct termination, build the matching module, check both `canX` interfaces, then exchange one frame. Run the [acceptance route](../../udocs/验证路线.md) after that first frame succeeds.

## Topology

The device exposes **one USB interface per CAN channel**, each with its own bulk
IN/OUT endpoint pair. The driver probes per interface and registers one
SocketCAN netdev per interface — it does **not** hard‑code the channel count.
Each interface is independent; control requests are routed by `wIndex` = channel.

## Protocol notes

- Standard gs_usb host‑frame layout (`echo_id, can_id, can_dlc, channel, flags`
  + data); event frames (state/berr/load) arrive on the IN endpoint and are
  identified by `echo_id`.
- The firmware does **not** echo transmitted frames; TX is completed on the
  bulk‑OUT URB completion (not on a device echo as mainline gs_usb expects).
- Bit timing is register‑encoded (firmware adds 1 to each segment and ignores
  `prop_seg`): the host sends `brp-1`, `(prop_seg+phase_seg1)-1`, `phase_seg2-1`,
  `sjw-1`.
- Termination (120 Ω) prefers `VKGS_USB_BREQ_CAN_TERMINATION` (37), with standard
  gs_usb `SET/GET_TERMINATION` (12/13) retained as a compatibility fallback.
  Bus-load reporting can be controlled explicitly through
  `VKGS_USB_BREQ_CAN_BUS_LOAD` (36); normal interface startup does not force it on.
- Device version/UID/UUID is read via `VKGS_USB_BREQ_BSP_DEVICE_INFO` (33,
  `wValue=0` so it does
  not trigger the OTA path) and logged at probe. Software and hardware versions
  use `vMAJOR.MINOR.PATCH` notation.
- `berr-reporting` is advertised as a `CAN_CTRLMODE_BERR_REPORTING` capability
  when the firmware reports the matching feature bit; enabling it
  (`ip link set canX type can berr-reporting on`) makes the device push
  protocol-violation error frames (stuff/form/ACK/bit0/bit1/CRC) in addition to
  the always-on bus-off/error-passive/error-warning state frames.
- Identify/blink is wired to `ethtool -p canX <seconds>` via
  `VKGS_USB_BREQ_IDENTIFY` (7); the firmware blinks the device LED on its own
  once started, the driver only sends the start/stop request.
- HW timestamps are intentionally not enabled.

## Kernel compatibility

The supported baseline is upstream Linux **4.12 or newer**. The local
`usbcan_compat.h` isolates CAN DLC helper renames and the independently changed
echo-SKB signatures in 5.12 and 5.13. Termination control and CAN FD remain
available across the whole supported range. Vendor kernels with backported APIs
may differ from their advertised version and should be build-tested separately.

## PID collision with in-tree `gs_usb`

The firmware reuses the candleLight PID `1d50:606f`, which the mainline `gs_usb`
driver also matches. If `gs_usb` already owns this device, unbind or temporarily unload it before loading this module. A persistent blacklist affects **all** devices using `gs_usb`; choose it only if that is intended. For a temporary check:

```bash
echo blacklist gs_usb | sudo tee /etc/modprobe.d/blacklist-gs_usb.conf
sudo modprobe -r gs_usb
sudo modprobe vkgs_usb
```

## Build

```bash
make
sudo make install
sudo depmod -a
sudo modprobe vkgs_usb
```

DKMS: copy this directory to `/usr/src/vkgs_usb-1.1.4/`, then
`dkms add/build/install -m vkgs_usb -v 1.1.4`.

## Usage

Connect CAN0-H to CAN1-H and CAN0-L to CAN1-L on an isolated test bus. Use one
120 Ω termination at each end. If external resistors are already installed,
change both `termination 120` commands below to `termination 0`.

For classic CAN, configure both interfaces **while down**, then bring them up:

```bash
sudo ip link set can0 down
sudo ip link set can1 down
sudo ip link set can0 type can bitrate 500000 sample-point 0.75
sudo ip link set can1 type can bitrate 500000 sample-point 0.75
sudo ip link set can0 type can termination 120
sudo ip link set can1 type can termination 120
sudo ip link set can0 up
sudo ip link set can1 up
```

Run `candump can1` in terminal A and `cansend can0 123#11223344` in terminal B.
Expected: terminal A prints ID `123` and bytes `11 22 33 44`.

To test CAN FD instead, stop both interfaces and select matching arbitration and
data bitrates before starting them again:

```bash
sudo ip link set can0 down
sudo ip link set can1 down
sudo ip link set can0 type can bitrate 1000000 sample-point 0.75 dbitrate 5000000 dsample-point 0.75 fd on
sudo ip link set can1 type can bitrate 1000000 sample-point 0.75 dbitrate 5000000 dsample-point 0.75 fd on
sudo ip link set can0 up
sudo ip link set can1 up
```

See the [SocketCAN manual](../../udocs/SocketCAN使用手册.md) for FD sending and
the [acceptance route](../../udocs/验证路线.md) for automated coverage.

## Test

Only the hardware stress entry remains:

```bash
sudo ../tests/vkgs_usb/test_vkgs_usb.sh
sudo env BUILD=0 DURATION=20 REOPEN_LOOPS=10 ../tests/vkgs_usb/test_vkgs_usb.sh
```

Requires two connected, terminated physical CAN channels and can-utils. The
suite covers mixed traffic, ISO-TP/J1939, sequence integrity, load, CAN FD and
close/open regression. Unsupported optional FD tools are reported as skips;
`REQUIRE_ALL=1` makes missing coverage fail. The old `NFRAMES` setting is no
longer used. Default execution builds/reloads the driver and leaves interfaces
down during cleanup. The acceptance script reports passes, failures and skipped optional coverage.
