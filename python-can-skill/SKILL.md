---
name: python-can-skill
description: Build and troubleshoot Python applications using VCANDrive vcan_usb or vkgs_usb python-can backends, including CAN/CAN FD, multi-device access, DBC signal coding, and CANopen integration. Use for Python API projects and tests; use socket-can-skill for Linux PF_CAN or canX applications.
---

# VCANDrive Python CAN

用户路线：先按[快速入门](../udocs/快速入门.md)建立 Python USB 环境并收发第一帧，再按[验收测试路线](../udocs/验证路线.md)做矩阵/压力验证，最后接入本页的应用代码。

Build applications against the repository's public `python-can` backends. Keep the transport choice, CAN application protocol, and hardware validation separate so code can be tested without a live bus.

For a first-time user, start with [the quick-start route](../udocs/快速入门.md) and prove one frame on the chosen transport before adding DBC or CANopen.

## Route the request

1. Identify the device's current USB personality before selecting a backend:

   | USB personality | VID:PID | Package | `can.Bus` interface |
   |---|---|---|---|
   | VCAN native | `1d50:6080` | `vcan-usb` | `vcan_usb` |
   | GS_USB / VKGS | `1d50:606f` | `vkgs-usb` | `vkgs_usb` |

   Do not use `vcan_usb` as a synonym for Linux's virtual `vcan` network interface.

2. Select the application layer:

   - For raw CAN/CAN FD frames, device discovery, lifecycle, or backend extensions, read [references/vcandrive-python-api.md](references/vcandrive-python-api.md).
   - For signal-oriented applications backed by a `.dbc`, read [references/dbc-workflow.md](references/dbc-workflow.md) and the API reference.
   - For CANopen networks backed by an EDS/DCF object dictionary, read [references/canopen-workflow.md](references/canopen-workflow.md) and the API reference.
   - If the requested transport is Linux `socketcan` with a `canX` interface, use `socket-can-skill` instead.

3. When this repository is present, treat these files as the implementation source of truth:

   - `python-cli/vcan_usb/vcan_usb/bus.py`
   - `python-cli/vkgs_usb/vkgs_usb/bus.py`
   - `python-cli/tools/canctl.py`
   - `udocs/PythonAPI参考.md`
   - `udocs/设备管理器.md` for identity, version display, and USB personality changes

   Check the implementation before documenting a new extension method or backend-specific behavior.

## Implementation invariants

- One `Bus` owns one USB interface. Use one `Bus` per channel and always close it with a context manager or `shutdown()` in `finally`.
- Prefer `port_path` for stable selection among identical devices. `index` and USB `address` can change after re-enumeration.
- A Python USB backend and a Linux SocketCAN driver cannot own the same USB interface at the same time.
- Configure the controller with `fd=True` before sending a message with `is_fd=True`; BRS is a per-message choice through `bitrate_switch=True`.
- Keep python-can `can_filters` semantics as software filtering. Do not silently replace them with VCANDrive's separate firmware filter request.
- Do not automatically retry a send after an ambiguous USB timeout: the frame may already have reached the CAN bus.
- Treat `switch_usb_mode()` as a disruptive device-management operation. It persists configuration, reboots the device, changes its USB identity, and invalidates open handles.
- Do not modify the VCANDrive driver or firmware sources unless the user explicitly asks for implementation changes.

## Shape of a maintainable project

Keep three boundaries visible:

- transport configuration: backend name, channel, bit timing, device selector;
- protocol codec: raw frames, DBC signals, or CANopen object dictionary operations;
- application behavior: timeouts, state machine, persistence, logging, and retries.

Inject a `can.BusABC`-compatible bus into application code when practical. Unit-test codecs and state transitions with python-can's virtual bus or fake messages; keep USB discovery and physical bus tests in a separately marked hardware test.

Before handing off code, verify cleanup on success and exceptions, timeout handling, standard versus extended identifiers, classical versus FD frame lengths, and at least one encode/decode round trip for every DBC message used.
