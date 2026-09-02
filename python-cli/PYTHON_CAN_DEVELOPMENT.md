# Development notes

## USB startup and loopback test invariants

This section records the hardware findings behind the current Python driver
and test behavior. Preserve these invariants unless new firmware has been
validated on hardware and the evidence below is no longer applicable.

### Do not reconfigure the active composite USB device

The VKGS device exposes one USB interface per CAN channel. Normal operation is
interface-local: each `Bus` opens and claims only its selected interface and
must not issue a device-wide `SET_CONFIGURATION`.

Hardware testing showed that sending `SET_CONFIGURATION` to an already
configured device can desynchronize the firmware's bulk OUT state:

1. The first host bulk OUT transfer completes successfully.
2. The device continues producing periodic bus-load packets on bulk IN.
3. No loopback CAN frame is returned for the completed OUT transfer.
4. Later bulk OUT writes time out with `LIBUSB_ERROR_TIMEOUT`.
5. CAN `stop/configure/start` does not recover the endpoint because it only
   resets the CAN controller, not the firmware's pending USB OUT request.
6. A later process/device re-enumeration may work again, making the failure
   appear as alternating failed and successful runs.

For this reason:

- `UsbCanBus` must not call `set_configuration()` during normal acquisition.
- The hardware test must not offer or internally use `--usb-reconfigure`.
- A required hardware reset should use a controlled USB reset or physical
  reconnect, followed by rediscovery and reopening all handles.
- Releasing/reclaiming one interface or restarting CAN mode must not be
  presented as recovery for a device-level endpoint fault.

### Startup readiness has two levels

A bus-load event proves that the USB IN endpoint and firmware main loop are
alive, but it does not prove that the complete transmit path is ready.

In internal-loopback mode, the repository-only `tests/hardware_test.py` tool therefore
sends one sacrificial
probe frame before measured traffic and requires the same frame to return over
the complete path:

```text
USB bulk OUT -> firmware CAN TX -> internal loopback -> USB bulk IN
```

The probe uses a separate CAN identifier/payload and is not included in the RX
matrix. Shared-bus mode retains only the non-invasive bus-load readiness event;
it must not inject an implicit retry that could duplicate traffic on a real CAN
bus.

The default dual-channel test order intentionally mirrors both kernel test
scripts and sends 60 frames from each channel in every data phase:

1. enable 120-ohm termination on both channels;
2. shared-bus classic CAN at 1 Mbit/s;
3. shared-bus CAN FD at 1/5 Mbit/s with BRS off;
4. shared-bus CAN FD at 1/5 Mbit/s with BRS on;
5. reopen both interfaces in independent internal loopback;
6. repeat classic, FD/BRS-off and FD/BRS-on as three loopback phases.

Measured arbitration IDs use `0x100 + USB channel`, matching the SocketCAN
script. The post-bus loopback diagnostic uses newly opened Bus objects because
loopback mode is fixed when a Bus is constructed. It always diagnoses all
channels even when `--senders` restricts the shared-bus traffic phase. Shared-
bus termination is enabled only on the first and last selected interface;
isolated loopback diagnostics enable termination on every interface.

The SocketCAN scripts capture in candump log format and classify frames by the
full wire prefix: `ID#` for classic, `ID##0` for FD/BRS-off, and `ID##1` for
FD/BRS-on. Counting by CAN ID alone is insufficient because it would not prove
that the BRS flag survived the driver and firmware path. Every required matrix
cell must equal `NFRAMES` (60 by default), not merely be nonzero.

Python shared-bus traffic is interleaved by sequence (`ch0`, then `ch1`) so
both bulk OUT endpoints are exercised immediately. Do not change it back to
sending all 60 frames from one channel before touching the other: that delays
detection of an unarmed per-channel OUT endpoint and is less representative of
bidirectional bus traffic.

### Resolved Python bulk-OUT failure

On VKGS hardware, one dual-channel Python run produced the following bounded
failure in the first classic shared-bus sequence:

- ch0 endpoint `0x01`: `tx_completed=1`, `tx_errors=0`;
- ch1 endpoint `0x02`: `tx_completed=0`, `tx_errors=1` and
  `LIBUSB_ERROR_TIMEOUT`;
- both channels: three successful 64-byte IN completions with no RX errors.

This proved that interface 1 bulk IN was operational while Python's first bulk
OUT did not complete. It did not by itself prove a firmware fault.

The isolated ch1 test also failed on its first endpoint `0x02` write with no
other interface open, proving this is not a Python multi-handle issue. A later
SocketCAN run then passed 60 classic, 60 FD/BRS-off and 60 FD/BRS-on frames in
both directions, plus independent loopback on both channels. That establishes
that firmware endpoint `0x02` and both CAN channels are operational.

The decisive transport difference was Python's blocking `bulkWrite()` mixed
with an asynchronous bulk-IN event dispatcher, while the working kernel driver
submits both directions asynchronously. Python bulk OUT now uses a libusb
transfer completed by the same event dispatcher and waits on its callback; it
does not reconfigure the device or add interface/endpoint open-close cycles.
After this change, the full dual-channel Python test passed on hardware.

Implementation details that must be preserved:

- Linux/macOS bulk IN and OUT use asynchronous libusb transfers with one
  context, handle and event dispatcher per Bus;
- Windows uses native overlapped WinUSB I/O and opens only the selected
  `MI_xx` device-interface path, with one handle and receive dispatcher per Bus;
- OUT writes are serialized and wait for asynchronous completion;
- EP0 and bulk traffic use the same interface-owned handle;
- normal acquisition never sends device-wide `SET_CONFIGURATION`;
- timeout, STALL, removal and overflow failures remain visible to callers;
- CAN frames are never automatically retried.

### Windows per-interface ownership

`libusb_open()` is thread-safe and does not send a USB bus request. The relevant
Windows behavior occurs below that API: stock libusb 1.0.29 and 1.0.30 iterate
all WinUSB interface paths belonging to a composite device and call
`CreateFile()` for each path during `libusb_open()`. A minimal reproduction that
performed only enumeration and `device.open()` therefore failed on its second
process with `LIBUSB_ERROR_ACCESS`; no interface claim, control request, bulk
transfer or CAN initialization had occurred.

Direct WinUSB validation proved the hardware and driver binding support the
required ownership model: one process opened `MI_00` while another opened
`MI_01`, and both `WinUsb_Initialize()` calls succeeded. Opening the same
`MI_xx` twice still returned access denied, as expected.

The Windows backend now uses libusb only for descriptor discovery and the
existing `index`/`bus`/`address`/`port_path` selection contract. It then closes
the discovery context, maps the selected bus, physical port and interface number
to a Windows device-interface path through SetupAPI, and opens exactly that PDO
with `CreateFile()` plus `WinUsb_Initialize()`. Each Bus owns its own handle,
EP0 operations, bulk pipes, ordered overlapped receive pool and dispatcher.

NexuTrace was reviewed for selector and lifecycle style. Its application layer
also identifies devices by physical identity and interface. The provenance of
the locally installed binary versus changes in its libusb worktree was not
established, so this backend relies on behavior reproduced directly with stock
libusb and native WinUSB rather than assumptions about that binary.

Windows VKGS hardware validation (`1d50:606f`) passed classic CAN, CAN FD with
BRS off and on, and independent loopback with 60 frames per matrix cell. Two
separate Python processes were then tested in both directions on the same
physical adapter: ch0 -> ch1 and ch1 -> ch0 each delivered 60/60 frames, with
both processes exiting successfully.

The first async-OUT implementation briefly read `.error` from the raw
`usb1.USBContext`; that field belongs to the outer event-dispatcher `Context`.
Passing that owner explicitly fixed the resulting pre-submit `AttributeError`.

The VCAN backend had three additional integration bugs found during the same
review: it tried to run PyUSB configuration/claim calls on `AsyncDevice`, called
`write()` on an endpoint descriptor instead of the transport, and lacked the
`parse_bulk()` callback used by the shared Bus. VCAN now uses the same async
transport contract as VKGS and preserves partial frames between IN transfers.

Wire allocation is protocol-specific and must not be replaced by the logical
payload length: VKGS uses a 12-byte header plus 8 bytes in classic mode or 64
bytes in FD mode; VCAN uses its 24-byte header plus a fixed 8-byte classic or
64-byte FD payload area. CAN-FD DLC rounding is applied before zero padding.

### Do not automatically retry CAN sends

A successful host bulk write does not prove whether firmware transmitted the
CAN frame. Retrying after a missing acknowledgement can therefore duplicate a
real CAN message. Keep failures visible to the caller and to the hardware test.

The loopback startup probe also does not retry on the same handle after a
failure. Once the bulk OUT endpoint is unarmed, CAN stop/start cannot repair it.

### Confirmed stable test path

The normal path was run three consecutive times with 10 classic CAN loopback
frames and passed each time when device-wide reconfiguration was omitted:

```bash
sudo "$PWD/.venv-linux/bin/python" tests/hardware_test.py \
  --interface vkgs_usb \
  --channels 1 \
  --mode loopback \
  --frames 10 \
  --skip-fd
```

Expected result: the startup probe loops back, the RX matrix reports `10*`, and
the phase ends with `PHASE OK`.

### Regression coverage

`tests/unit/test_startup_probe.py` checks that:

- a complete loopback startup data path is accepted;
- an IN-only readiness condition followed by no loopback data raises
  `CanInitializationError`;
- the test does not perform a misleading CAN restart retry for an unarmed USB
  OUT endpoint.

`tests/unit/test_test_helpers.py` checks the SocketCAN-aligned workflow helpers:

- measured IDs are `0x100 + channel`;
- shared-bus termination is enabled only at the two physical ends;
- isolated loopback termination is enabled on every channel;
- Bus objects are shut down in reverse-open order and the live list is cleared
  before interfaces are reopened in another mode.

`tests/unit/test_usb_async.py` verifies async OUT completion and timeout mapping
for both independently packaged transports; `tests/unit/test_bus_api.py`
verifies independent Windows interface acquisition;
`tests/unit/test_winusb.py` verifies physical-port and `MI_xx` path selection;
`tests/unit/test_vcan_protocol.py` verifies VCAN async transport integration, fixed
8/64-byte payload areas and split bulk-IN reassembly.

Run the local checks with bytecode writes disabled because hardware tests may
have left root-owned `__pycache__` files after execution through `sudo`:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv-linux/bin/python -m unittest discover -s tests/unit -v
```

Before changing USB initialization or retry behavior, update these notes with
the new hardware evidence and add a regression test for the new behavior.
