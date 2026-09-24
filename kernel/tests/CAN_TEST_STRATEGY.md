# USB-CAN driver test strategy

No single traffic generator is sufficient to qualify a CAN adapter. Tests are
split into layers so a failure identifies the affected contract instead of only
reporting that a long stress run failed.

## Test layers

| Layer | Purpose | Default implementation |
|---|---|---|
| Build/API | Kernel API compatibility and compiler diagnostics | Build both modules with `W=1`; build against every supported kernel header in CI |
| Protocol format | SFF/EFF, RTR, zero-to-eight-byte Classic CAN, FD lengths 0/8/12/16/20/24/32/48/64, BRS | `cangen`, directed frame checks |
| Integrity | Detect loss, duplication and reordering; distinguish socket overflow from hardware loss | `cansequence` in Classic CAN and strict CAN-FD modes |
| Bidirectional queueing | Exercise simultaneous RX/TX and multiple in-flight echo contexts | `canfdtest` with 50 frames in flight |
| Vehicle protocols | Exercise kernel transport protocols over the driver | ISO-TP diagnostics; J1939 unicast TP.CM/TP.DT; ECU PGNs |
| Scheduling/arbitration | Mix high-priority control traffic with low-priority diagnostics and event bursts | Dual-ECU profile in `test_vcan_usb.sh` |
| Capacity | Validate sustained load and saturation without silent loss | `canbusload`, `cangen`, netdev counters and error-frame monitor |
| Recovery | Verify restart after bus-off, unplug/replug, suspend/resume and repeated up/down | Separate destructive/manual fixture test |
| Fault injection | Short/corrupt USB records, allocation failure and URB submit/completion errors | KUnit or USB emulation plus kernel fault injection; not safe on a production bus |

## Required test environments

1. A virtual SocketCAN CI job validates test utilities and ISO-TP/J1939 logic.
2. A two-port adapter wired to one bus validates the USB data path and
   arbitration. Both ends must be correctly terminated.
3. An independent reference CAN analyzer is required for conformance claims.
   Two channels of the device under test cannot independently prove bitrate,
   physical error signaling or timestamp accuracy because they share firmware
   and clock assumptions.
4. A programmable CAN fault-injection fixture is required for ACK loss,
   bit/stuff/CRC errors, bus-off and recovery tests.

## Pass criteria

- Payload, identifier, frame type and order match exactly.
- No unexplained sequence gap, duplicate or reorder is accepted.
- Socket overflow is reported separately from hardware/driver loss.
- Netdev `rx_errors`, `tx_errors`, `rx_dropped` and `tx_dropped` do not grow in
  nominal tests.
- No CAN error frame is emitted in nominal tests.
- Error-injection tests must emit the expected Linux CAN error class and update
  controller state/counters.
- Interface down/up, module reload and USB reconnect must leave no stuck queue,
  occupied echo slot, leaked URB or use-after-free report.

## Kernel diagnostics for qualification runs

Run destructive recovery tests on a debug kernel with KASAN, UBSAN, lockdep and
kmemleak when possible. Capture `dmesg --follow`, `/proc/net/can/stats`, netdev
statistics and USB traces. Build-time CI should include `W=1`, Sparse and
Coccinelle when those tools are installed.

`test_vcan_usb.sh` intentionally contains only repeatable nominal and load
tests. Electrical fault injection, hot-unplug loops and system suspend must be
opt-in because they modify hardware or system-wide state.

## 运行保护补充

两种协议共享 实现。阶段返回非零或设置 FAILED 后停止后续阶段，重开循环失败也停止。
错误监控和 DRAIN_TIME 不缩短；阶段内的生成器仍由原有 timeout/DURATION 限制。
打印阶段和总耗时。失败或中断时保留 `/tmp/<driver>.*` 诊断目录并打印路径，
成功时删除；接口仍在清理中 down。统计读取失败视为失败，而非零计数。
Python 对应入口、覆盖范围和相同失败边界见 [Python 测试说明](../../python-cli/tests/README.md)。
