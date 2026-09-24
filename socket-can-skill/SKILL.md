---
name: socket-can-skill
description: Build and troubleshoot Linux SocketCAN applications for VCANDrive canX interfaces, including CAN/CAN FD setup, C/C++ PF_CAN sockets, can-utils, DBC workflows, and CANopen integration. Use for Linux netdevice projects; use python-can-skill for direct cross-platform USB backends.
---

# VCANDrive SocketCAN

用户路线：先按[快速入门](../udocs/快速入门.md)安装驱动并收发第一帧，再按[验收测试路线](../udocs/验证路线.md)验收 Linux 内核路径，最后使用本页 API。

Build Linux applications on the stable SocketCAN network interface. Once the correct VCANDrive module has registered a `canX` netdevice, application code should use standard `PF_CAN` APIs and should not depend on USB endpoint details.

For a first-time user, start with [the quick-start route](../udocs/快速入门.md) and verify one frame before designing the application protocol.

## Route the request

1. Confirm the device mode and driver ownership:

   | USB personality | VID:PID | Preferred module | Userspace name |
   |---|---|---|---|
   | VCAN native | `1d50:6080` | `vcan_usb` | `canX` |
   | GS_USB / VKGS | `1d50:606f` | `vkgs_usb` | `canX` |

   Linux's in-tree `gs_usb` can claim `1d50:606f`. Confirm the actual bound driver through sysfs before diagnosing application code.

2. Load only the references needed for the request:

   - For module selection, interface setup, termination, diagnostics, or can-utils, read [references/vcandrive-linux.md](references/vcandrive-linux.md).
   - For C/C++ raw sockets, filters, CAN FD, polling, timestamps, and error frames, read [references/socketcan-api.md](references/socketcan-api.md).
   - For CANopen or DBC-backed applications, read [references/canopen-dbc.md](references/canopen-dbc.md) plus the transport reference relevant to the implementation language.
   - If the request must access the VCANDrive USB interface directly on Windows, macOS, or Linux, use `python-can-skill` instead.

3. When this repository is present, use `udocs/Linux内核驱动安装.md`, `udocs/SocketCAN使用手册.md`, `udocs/设备管理器.md`, and the matching `kernel/*/README.md` as project-specific references. Inspect driver source only when needed for diagnosis; do not change it unless the user explicitly asks.

## Implementation invariants

- `can0` and `can1` are dynamically assigned network-interface names, not guaranteed physical channel labels. Resolve mappings through sysfs or a deliberate persistent naming rule.
- Configure bit timing, controller mode, and termination while the interface is down; application sockets do not configure those values by themselves.
- Enable `CAN_RAW_FD_FRAMES` before sending or receiving CAN FD on a raw socket. On receive, distinguish `CAN_MTU` from `CANFD_MTU` before using FD-only flags.
- Keep identifier flags (`CAN_EFF_FLAG`, `CAN_RTR_FLAG`, `CAN_ERR_FLAG`) separate from the 11-bit or 29-bit application identifier.
- Socket filters are per-socket receive filters. They are not the device firmware's hardware filter table and do not affect other processes.
- Subscribe to error frames explicitly with `CAN_RAW_ERR_FILTER`; ordinary data-frame reception does not automatically expose every controller error.
- Local SocketCAN echo and controller `loopback on` are different mechanisms. Name the intended behavior in tests.
- Do not run traffic generators, change termination, unload modules, or reconfigure a production bus unless the user placed that bus in scope.

## Shape of a maintainable project

Separate host setup (`ip link`, udev/systemd/network management) from the unprivileged application. The application should open a named interface, install its own filters, handle timeouts and `EINTR`, validate exact frame sizes, and close descriptors on all paths.

Keep signal or device-profile knowledge outside the transport layer. A DBC codec turns raw payloads into named signals; a CANopen stack implements NMT, PDO, SDO, EMCY, and heartbeat over raw CAN frames. Neither belongs in the USB driver.

Validate protocol logic on a Linux `vcan` interface where possible, then run an explicitly identified hardware test on VCANDrive. Verify classic CAN first, then CAN FD/BRS, and finally bus-off recovery and error reporting if the application depends on them.
