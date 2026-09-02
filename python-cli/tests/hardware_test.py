"""Hardware acceptance test using only the public python-can API."""
from __future__ import annotations

import argparse
from collections import Counter
import time

import can

# This script is both `python tests/hardware_test.py` (sys.path[0] == this
# directory, relative imports fail) and `python -m tests.hardware_test` /
# `from tests.hardware_test import ...` in unit tests (relative import is the
# only form that works there).
try:
    from ._helpers import _arbitration_id, _open_buses, _shutdown_buses
except ImportError:
    from _helpers import _arbitration_id, _open_buses, _shutdown_buses

STARTUP_TIMEOUT = 2.0
RECEIVE_TIMEOUT = 2.0
INTER_FRAME_GAP = 0.02


def _payload(sender: int, sequence: int, size: int) -> bytes:
    return bytes(sender & 0xFF if i % 2 == 0 else (sequence + i) & 0xFF
                 for i in range(size))


def _version(value: int) -> str:
    return f"v{value >> 16 & 0xff}.{value >> 8 & 0xff}.{value & 0xff}"


def _identity(words: list[int]) -> str:
    return "".join(f"{word:08x}" for word in words)


def _port_path(value: str) -> tuple[int, ...]:
    try:
        result = tuple(int(item) for item in value.replace(",", ".").split("."))
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"USB port path must be like 1.2.3, got {value!r}") from exc
    if not result or any(item < 0 or item > 255 for item in result):
        raise argparse.ArgumentTypeError(
            "USB port path entries must be 0..255")
    return result


def _drain(bus: can.BusABC) -> None:
    while bus.recv(0) is not None:
        pass


def _verify_termination(buses: list[can.BusABC]) -> bool:
    print("\n=== 120 ohm termination ===")
    passed = True
    for bus in buses:
        actual = bus.get_termination()
        ok = actual is True
        passed &= ok
        print(f"  ch{bus.channel}: "
              f"{'120 ohm enabled' if ok else 'readback mismatch'}")
    return passed


def _print_usb_stats(args: argparse.Namespace,
                     buses: list[can.BusABC]) -> None:
    print("  USB transfer diagnostics:")
    for position, bus in enumerate(buses):
        stats = bus.get_usb_stats()
        values = " ".join(f"{key}={value}" for key, value in stats.items())
        print(f"    ch{args.channel_numbers[position]}: {values}")


def _stop_round(buses: list[can.BusABC]) -> None:
    for bus in reversed(buses):
        bus.stop()


def _probe_loopback_path(args: argparse.Namespace, buses: list[can.BusABC],
                         fd: bool, brs: bool) -> bool:
    """Prove the firmware CAN path, not merely its USB event endpoint."""
    size = 64 if fd else 8
    for position, bus in enumerate(buses):
        channel = args.channel_numbers[position]
        can_id = 0x700 | (channel & 0x7f)
        payload = bytes((0xA5, 0x5A, channel & 0xff,
                         int(fd) | (int(brs) << 1)))
        payload = (payload * ((size + len(payload) - 1) // len(payload)))[:size]
        try:
            bus.send(can.Message(
                arbitration_id=can_id, is_extended_id=False,
                is_fd=fd, bitrate_switch=brs, data=payload))
        except can.CanError as exc:
            print(f"  startup data-path probe ch{channel} send failed: {exc}")
            return False
        deadline = time.monotonic() + STARTUP_TIMEOUT
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            msg = bus.recv(min(0.05, remaining))
            if msg is None:
                continue
            if (msg.arbitration_id == can_id and bytes(msg.data) == payload and
                    msg.is_fd == fd and
                    (not fd or msg.bitrate_switch == brs)):
                break
        else:
            print(f"  startup data-path probe ch{channel} received no loopback")
            return False
    return True


def _prepare_round(args: argparse.Namespace, buses: list[can.BusABC],
                   fd: bool, brs: bool, loopback: bool) -> None:
    _stop_round(buses)
    for bus in buses:
        bus.configure(fd=fd)
    # A completed EP0 request and submitted host transfer do not prove that
    # the firmware main loop and device IN endpoint are operational.  Use the
    # existing bus-load event as a per-interface readiness handshake.
    baselines = [bus.get_usb_stats()["rx_completed"] for bus in buses]
    for bus in buses:
        bus.set_bus_load_reporting(True)
    for bus in buses:
        bus.start()
    for position, bus in enumerate(buses):
        if not bus.wait_for_usb_rx(
                baselines[position], STARTUP_TIMEOUT):
            raise can.CanInitializationError(
                f"channel {args.channel_numbers[position]} produced no "
                "USB IN readiness event")

    # A load event only proves that EP IN and the firmware main loop are
    # alive.  In internal loopback mode safely prove the complete OUT -> CAN
    # -> IN path with a sacrificial frame before measured traffic starts.
    if loopback and not _probe_loopback_path(args, buses, fd, brs):
        raise can.CanInitializationError(
            "loopback startup data path failed; the USB OUT endpoint may be "
            "unarmed (do not use device-wide USB reconfiguration)")


def _phase(args: argparse.Namespace, buses: list[can.BusABC], fd: bool,
           brs: bool, loopback: bool, label: str) -> bool:
    kind = "CAN FD" if fd else "classic CAN"
    timing = (f"{args.bitrate} bit/s / {args.data_bitrate} bit/s"
              if fd else f"{args.bitrate} bit/s")
    brs_text = f", BRS {'on' if brs else 'off'}" if fd else ""
    suffix = "independent internal loopback" if loopback else "dual-CAN bus"
    print(f"\n=== {label}: {kind} @ {timing}{brs_text} ({suffix}) ===")

    size = 64 if fd else 8
    senders = args.sender_indices
    expected = {
        (sender, sequence): _payload(
            args.channel_numbers[sender], sequence, size)
        for sender in senders for sequence in range(args.frames)
    }
    sender_by_id = {
        _arbitration_id(args.channel_numbers[sender]): sender
        for sender in senders
    }
    sequence_by_payload = {
        (sender, payload): sequence
        for (sender, sequence), payload in expected.items()
    }
    counts = [Counter() for _ in range(args.channels)]
    invalid = [0 for _ in range(args.channels)]
    seen = [set() for _ in range(args.channels)]
    _prepare_round(args, buses, fd, brs, loopback)
    for bus in buses:
        _drain(bus)

    def collect(receiver: int, timeout: float) -> None:
        msg = buses[receiver].recv(timeout)
        if msg is None:
            return
        sender = sender_by_id.get(msg.arbitration_id)
        if sender is None:
            invalid[receiver] += 1
            return
        payload = bytes(msg.data)
        sequence = sequence_by_payload.get((sender, payload))
        valid = (sequence is not None and msg.is_fd == fd and
                 (not fd or msg.bitrate_switch == brs))
        key = (sender, sequence)
        if not valid or key in seen[receiver]:
            invalid[receiver] += 1
            return
        seen[receiver].add(key)
        counts[receiver][sender] += 1

    for sender in senders:
        can_id = _arbitration_id(args.channel_numbers[sender])
        print(f"  sender ch{args.channel_numbers[sender]}: CAN ID 0x{can_id:03x}")

    # Exercise both OUT endpoints immediately instead of completing every
    # frame on ch0 before touching ch1.  This mirrors bidirectional traffic and
    # exposes an unarmed per-channel endpoint at sequence zero.
    blocked_senders: set[int] = set()
    for sequence in range(args.frames):
        for sender in senders:
            if sender in blocked_senders:
                continue
            can_id = _arbitration_id(args.channel_numbers[sender])
            bus = buses[sender]
            payload = expected[(sender, sequence)]
            bus.send(can.Message(
                arbitration_id=can_id, is_extended_id=False,
                is_fd=fd, bitrate_switch=brs,
                data=payload,
            ))
            # Firmware does not return a usable TX-completion echo to this
            # backend.  Advance on actual receive events instead: this bounds
            # the device OUT queue without relying on arbitrary sleeps.
            targets = ([sender] if loopback else
                       [receiver for receiver in range(args.channels)
                        if receiver != sender])
            deadline = time.monotonic() + RECEIVE_TIMEOUT
            while (time.monotonic() < deadline and
                   any((sender, sequence) not in seen[receiver]
                       for receiver in targets)):
                for receiver in targets:
                    if (sender, sequence) in seen[receiver]:
                        continue
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    collect(receiver, min(0.05, remaining))
            # Do not submit another OUT packet while the preceding one has
            # produced no corresponding CAN/RX event.  The firmware rearms
            # its OUT endpoint only after consuming that packet; continuing
            # would merely turn the original processing failure into a less
            # useful bulk-OUT timeout on the following frame.
            missing = [receiver for receiver in targets
                       if (sender, sequence) not in seen[receiver]]
            if missing:
                names = ", ".join(
                    f"ch{args.channel_numbers[receiver]}"
                    for receiver in missing)
                print(f"  no RX acknowledgement for sender "
                      f"ch{args.channel_numbers[sender]} sequence "
                      f"{sequence}; pending receiver(s): {names}")
                blocked_senders.add(sender)
                continue
            time.sleep(INTER_FRAME_GAP)

    required = ([(i, i) for i in senders] if loopback else
                [(r, s) for r in range(args.channels)
                 for s in senders if r != s])

    # RX workers have been collecting throughout transmission.  Consume their
    # queues as one bounded batch instead of waiting once per sent frame.
    deadline = time.monotonic() + RECEIVE_TIMEOUT
    while time.monotonic() < deadline:
        if all(counts[receiver][sender] >= args.frames
               for receiver, sender in required):
            break
        for receiver, bus in enumerate(buses):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            collect(receiver, min(0.01, remaining))
    print("  stop")
    _stop_round(buses)

    print(f"\n  RX matrix (rows=receiver, cols=sender; sent {args.frames}/cell"
          f"{', *=self' if loopback else ''})")
    print(" " * 11 + "".join(
        f"{'ch' + str(channel):>8}" for channel in args.channel_numbers))
    passed = True
    for receiver in range(args.channels):
        print(f"  ch{args.channel_numbers[receiver]:<6}", end="")
        for sender in range(args.channels):
            mark = "*" if loopback and receiver == sender else ""
            print(f"{str(counts[receiver][sender]) + mark:>8}", end="")
        if invalid[receiver]:
            print(f"  invalid/duplicate={invalid[receiver]}", end="")
            passed = False
        print()

    for receiver, sender in required:
        if counts[receiver][sender] < args.frames:
            print(f"  MISSING: ch{args.channel_numbers[receiver]} received "
                  f"{counts[receiver][sender]}/{args.frames} from "
                  f"ch{args.channel_numbers[sender]}")
            passed = False
    if passed:
        text = ("every channel looped its own frames back" if loopback else
                "every channel received every other channel")
        print(f"  PHASE OK: {text}.")
    else:
        print("  PHASE FAIL: missing or invalid frames.")
        _print_usb_stats(args, buses)
    return passed


def main() -> int:
    parser = argparse.ArgumentParser(
        description="python-can USB backend hardware functional test")
    parser.add_argument("--interface", choices=("vcan_usb", "vkgs_usb"),
                        default="vkgs_usb")
    parser.add_argument("--device", type=int, default=0)
    parser.add_argument("--usb-bus", type=int)
    parser.add_argument("--usb-address", type=int)
    parser.add_argument("--usb-port-path", type=_port_path)
    parser.add_argument(
        "--channels", type=int, default=None,
        help="number of descriptor-discovered CAN interfaces (default: all)")
    parser.add_argument("--mode", choices=("auto", "bus", "loopback"),
                        default="auto")
    parser.add_argument("--frames", type=int, default=60)
    parser.add_argument("--bitrate", type=int, default=1_000_000)
    parser.add_argument("--sample-point", type=float, default=75.0)
    parser.add_argument("--data-bitrate", type=int, default=5_000_000)
    parser.add_argument("--data-sample-point", type=float, default=75.0)
    parser.add_argument("--timeout-ms", type=int, default=2_000)
    parser.add_argument("--senders", default=None,
                        help="comma-separated sender channels (default: all)")
    parser.add_argument("--skip-fd", action="store_true")
    parser.add_argument("--no-termination", action="store_true")
    args = parser.parse_args()
    # The repository tool must remain usable when only one independent backend
    # is installed.  Import only the protocol selected by the user.
    if args.interface == "vkgs_usb":
        from vkgs_usb import vkgs_usb_bus
        bus_class = vkgs_usb_bus
    else:
        from vcan_usb import vcan_usb_bus
        bus_class = vcan_usb_bus
    try:
        discovered = bus_class.discover_channels(
            index=args.device, bus=args.usb_bus, address=args.usb_address,
            port_path=args.usb_port_path)
    except Exception as exc:
        parser.error(f"USB interface discovery failed: {exc}")
    if not discovered:
        parser.error("device descriptors contain no bulk IN/OUT CAN interfaces")
    requested_channels = len(discovered) if args.channels is None else args.channels
    if requested_channels < 1 or requested_channels > len(discovered):
        parser.error(
            f"--channels must be 1..{len(discovered)} for this device; "
            f"descriptor interfaces are {discovered}")
    if args.frames < 1:
        parser.error("--frames must be >= 1")
    args.channel_numbers = discovered[:requested_channels]
    args.channels = len(args.channel_numbers)
    if args.senders is None:
        args.sender_indices = list(range(args.channels))
    else:
        try:
            sender_channels = [int(value) for value in args.senders.split(",")]
        except ValueError:
            parser.error("--senders must contain comma-separated channel numbers")
        if (not sender_channels or
                len(set(sender_channels)) != len(sender_channels) or
                any(value not in args.channel_numbers
                    for value in sender_channels)):
            parser.error("--senders contains an invalid or duplicate channel")
        args.sender_indices = [args.channel_numbers.index(value)
                               for value in sender_channels]
    loopback = args.mode == "loopback" or (args.mode == "auto" and args.channels == 1)
    if not loopback and args.channels != 2:
        parser.error("dual-CAN bus mode requires exactly two channels")
    if loopback and args.senders is not None:
        # A selected loopback sender is a complete single-interface test.  Do
        # not open, configure or start unrelated interfaces: this must exercise
        # exactly the same driver lifetime as an application that opens only
        # can1 in another terminal.  Multiple selected senders remain multiple
        # independent Bus objects, one per requested interface.
        selected_channels = [args.channel_numbers[index]
                             for index in args.sender_indices]
        args.channel_numbers = selected_channels
        args.channels = len(selected_channels)
        args.sender_indices = list(range(args.channels))

    buses: list[can.BusABC] = []
    passed = True
    try:
        buses = _open_buses(args, fd=False, loopback=loopback)
        info = buses[0].get_device_info()
        print(f"device: {args.interface} index={args.device}")
        print(f"channels: {', '.join(str(i) for i in args.channel_numbers)}")
        print(f"test mode: {'loopback' if loopback else 'bus'}")
        if info:
            print(f"info: sw={_version(info['sw_version'])} "
                  f"hw={_version(info['hw_version'])} "
                  f"uid={_identity(info['uid'])} "
                  f"uuid={_identity(info['uuid'])}")
        if not args.no_termination:
            passed = _verify_termination(buses) and passed
        passed = _phase(
            args, buses, False, False, loopback,
            "BUS 1" if not loopback else "LOOPBACK 1") and passed

        if not args.skip_fd:
            passed = _phase(
                args, buses, True, False, loopback,
                "BUS 2" if not loopback else "LOOPBACK 2") and passed
            passed = _phase(
                args, buses, True, True, loopback,
                "BUS 3" if not loopback else "LOOPBACK 3") and passed

        # Match the SocketCAN workflow: after proving the shared physical bus,
        # reopen every interface in internal loopback and isolate channel-local
        # TX/RX operation.  Loopback is a construction-time Bus setting, so do
        # not mutate or reuse the normal-mode handles.
        if not loopback:
            _shutdown_buses(buses)
            print("\nreopening all channels for internal-loopback diagnostic")
            buses = _open_buses(args, fd=False, loopback=True)
            # The shared-bus phase may deliberately restrict transmitters via
            # --senders.  The channel-local diagnostic must still cover every
            # discovered interface, as the SocketCAN script does.
            args.sender_indices = list(range(args.channels))
            passed = _phase(
                args, buses, False, False, True, "LOOPBACK 1") and passed
            if not args.skip_fd:
                passed = _phase(
                    args, buses, True, False, True, "LOOPBACK 2") and passed
                passed = _phase(
                    args, buses, True, True, True, "LOOPBACK 3") and passed
        return 0 if passed else 1
    except (can.CanError, OSError, RuntimeError) as exc:
        print(f"ERROR: {exc}")
        if buses:
            _print_usb_stats(args, buses)
        return 2
    finally:
        _shutdown_buses(buses)


if __name__ == "__main__":
    raise SystemExit(main())
