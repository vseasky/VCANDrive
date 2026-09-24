"""functional and bounded-window stress tests of the public Python API."""
from __future__ import annotations

import argparse
import time
import math
import json
from pathlib import Path

import can

# Direct execution puts tests/ on sys.path before editable-install finders.
# Its vcan_usb/ and vkgs_usb/ wrapper directories then shadow the real packages
# as namespace packages. Use the repository root, matching module execution.
if __package__ in (None, ""):
    import sys
    from pathlib import Path

    sys.path[0] = str(Path(__file__).resolve().parents[1])
    from tests._helpers import _arbitration_id, _backend_class, _open_buses, _shutdown_buses
else:
    from ._helpers import _arbitration_id, _backend_class, _open_buses, _shutdown_buses

STARTUP_TIMEOUT = 2.0


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


def _payload(sender: int, sequence: int, size: int) -> bytes:
    """Unique through 2**32 frames, including the complete source channel."""
    return (sequence.to_bytes(4, 'little') + sender.to_bytes(4, 'little')
            + bytes((sequence + i) & 0xff for i in range(size - 8)))


def _message(channel: int, sequence: int, fd: bool, brs: bool) -> can.Message:
    size = (8, 12, 16, 20, 24, 32, 48, 64)[sequence % 8] if fd else 8
    return can.Message(arbitration_id=_arbitration_id(channel),
                       is_extended_id=bool(sequence % 2), is_fd=fd,
                       bitrate_switch=brs, data=_payload(channel, sequence, size))


class Integrity:
    """Bounded per-link accounting; no frame-count-sized payload/seen tables."""

    def __init__(self, channels, senders, loopback, factory=None, matrix_size=0):
        self.factory = factory or _message
        self.matrix_size = matrix_size
        self.channels = channels
        self.sender_by_id = {_arbitration_id(channels[s]): s for s in senders}
        self.counts = {(r, s): 0 for r in range(len(channels)) for s in senders
                       if (r == s if loopback else r != s)}
        if matrix_size:
            self.sender_by_id = {self.factory(channels[s], n, True, True).arbitration_id: s
                                 for s in senders for n in range(100)}
        self.observed = 0
        self.link_bytes = {key: 0 for key in self.counts}
        self.invalid = 0
        self.first_failure = None
        self.failure_samples = []
        self.rx_bytes = 0

    def _reject(self, receiver, sender, msg, fd, brs, sent):
        self.invalid += 1
        if len(self.failure_samples) >= 8:
            return
        expected_sequence = self.counts.get((receiver, sender))
        actual_sequence = (int.from_bytes(msg.data[:4], 'little')
                           if len(msg.data) >= 4 and not self.matrix_size else None)
        actual = self._snapshot(msg)
        expected = None
        reasons = []
        if expected_sequence is None:
            reasons.append("unexpected CAN ID or source/receiver link")
        else:
            expected = self._snapshot(self.factory(
                self.channels[sender], expected_sequence, fd, brs))
            reasons.extend(name for name in expected if expected[name] != actual[name])
            if actual_sequence is not None and actual_sequence >= sent[sender]:
                reasons.append("sequence not submitted")
        if len(msg.data) < 8 and not self.matrix_size:
            reasons.append("payload shorter than sequence/source header")
        own_sequence_matches = (
            sender is not None and actual_sequence is not None and
            actual == self._snapshot(self.factory(
                self.channels[sender], actual_sequence, fd, brs)))
        if expected_sequence is not None and actual_sequence != expected_sequence:
            reasons.insert(0, "sequence")
        failure = {
            "receiver": self.channels[receiver],
            "sender": self.channels[sender] if sender is not None else None,
            "expected_sequence": expected_sequence,
            "actual_sequence": actual_sequence,
            "submitted": sent.get(sender), "reasons": reasons,
            "expected": expected, "actual": actual,
            "matches_own_sequence": own_sequence_matches,
        }
        self.failure_samples.append(failure)
        if self.first_failure is None:
            self.first_failure = failure

    @staticmethod
    def _snapshot(msg):
        return {"id": f"0x{msg.arbitration_id:X}",
                "extended": msg.is_extended_id, "fd": msg.is_fd,
                "brs": msg.bitrate_switch, "esi": msg.error_state_indicator,
                "rtr": msg.is_remote_frame, "error": msg.is_error_frame,
                "dlc": msg.dlc, "data": bytes(msg.data).hex()}

    def accept(self, receiver, msg, fd, brs, sent):
        self.observed += 1
        sender = self.sender_by_id.get(msg.arbitration_id)
        key = (receiver, sender)
        if self.matrix_size and key in self.counts:
            sequence = self.counts[key]
            if (sequence >= sent[sender] or
                    self._snapshot(msg) != self._snapshot(self.factory(
                        self.channels[sender], sequence, fd, brs))):
                self._reject(receiver, sender, msg, fd, brs, sent)
                return
            self.counts[key] += 1
            self.link_bytes[key] += len(msg.data)
            self.rx_bytes += len(msg.data)
            return
        if key not in self.counts or len(msg.data) < 8:
            self._reject(receiver, sender, msg, fd, brs, sent)
            return
        sequence = int.from_bytes(msg.data[:4], 'little')
        size = (8, 12, 16, 20, 24, 32, 48, 64)[sequence % 8] if fd else 8
        if (sequence != self.counts[key] or sequence >= sent[sender]
                or msg.is_error_frame or msg.is_remote_frame
                or msg.is_fd != fd or msg.bitrate_switch != brs
                or msg.is_extended_id != bool(sequence % 2)
                or msg.error_state_indicator or msg.dlc != size
                or msg.data != _payload(self.channels[sender], sequence, size)):
            self._reject(receiver, sender, msg, fd, brs, sent)
            return
        self.counts[key] += 1
        self.rx_bytes += len(msg.data)
        self.link_bytes[key] += len(msg.data)


def _phase(args: argparse.Namespace, buses: list[can.BusABC], fd: bool,
           brs: bool, loopback: bool, label: str) -> bool:
    print(f"\n=== {label}: {'FD' if fd else 'Classic'} BRS={brs} "
          f"{'loopback' if loopback else 'bus'} window={args.window} ===")
    matrix_size = getattr(args, 'matrix_size', 0)
    if matrix_size:
        print(f'  requested frames per sender={args.frames}; matrix cases={matrix_size}; '
              f'cycles={args.frames // matrix_size} remainder={args.frames % matrix_size}')
        if args.frames < matrix_size:
            gap = f'{label}: only {args.frames}/{matrix_size} matrix cases requested'
            print('  partial coverage: ' + gap)
            if hasattr(args, 'coverage_gaps'):
                args.coverage_gaps.append(gap)
    _prepare_round(args, buses, fd, brs, loopback)
    for position, bus in enumerate(buses):
        timing = bus.get_bit_timing()
        if 'bitrate' in timing['nominal'] and not math.isclose(timing['nominal']['bitrate'], args.bitrate, rel_tol=1e-6):
            raise PhaseFailure('nominal bitrate does not match phase request')
        if fd and 'bitrate' in timing['data'] and not math.isclose(timing['data']['bitrate'], args.data_bitrate, rel_tol=1e-6):
            raise PhaseFailure('data bitrate does not match phase request')
        print(f"  ch{args.channel_numbers[position]} host-selected timing "
              f"(not register readback): nominal={timing['nominal']} "
              f"data={timing['data'] if fd else None}")
    for bus in buses:
        _drain(bus)
    baseline = [bus.get_usb_stats() for bus in buses]
    error_baseline = [bus.get_berr_counter() for bus in buses]
    factory = getattr(args, 'message_factory', _message)
    check = Integrity(args.channel_numbers, args.sender_indices, loopback,
                      factory, getattr(args, 'matrix_size', 0))
    sent = {s: 0 for s in args.sender_indices}
    tx_bytes = 0
    sent_bytes = {s: 0 for s in args.sender_indices}
    started = time.monotonic()

    def collect(timeout=0.0):
        for receiver, bus in enumerate(buses):
            msg = bus.recv(timeout)
            if msg is not None:
                check.accept(receiver, msg, fd, brs, sent)

    for first in range(0, args.frames, args.window):
        end = min(args.frames, first + args.window)
        for sequence in range(first, end):
            for sender in args.sender_indices:
                msg = factory(args.channel_numbers[sender], sequence, fd, brs)
                buses[sender].send(msg, timeout=args.timeout_ms / 1000)
                sent[sender] += 1
                tx_bytes += len(msg.data)
                sent_bytes[sender] += len(msg.data)
                collect()
                if check.invalid:
                    break
            if check.invalid:
                break
        deadline = time.monotonic() + args.receive_timeout
        while any(n < end for n in check.counts.values()):
            if check.invalid or time.monotonic() >= deadline:
                break
            collect(0.001)
        if check.invalid or any(n < end for n in check.counts.values()):
            break  # Never retry uncertain CAN transmissions.

    # Keep collecting after the last expected frame to catch trailing duplicates.
    deadline = time.monotonic() + args.drain_time
    while time.monotonic() < deadline:
        collect(0.001)
    elapsed = time.monotonic() - started
    final = [bus.get_usb_stats() for bus in buses]
    bad_stats = False
    usb_deltas = []
    controller_states = []
    for i, (before, after) in enumerate(zip(baseline, final)):
        delta = {k: after.get(k, 0) - before.get(k, 0) for k in
                 ('tx_completed', 'rx_completed', 'tx_bytes', 'rx_bytes', 'tx_errors', 'rx_errors',
                  'app_rx_dropped', 'app_rx_overflow')}
        usb_deltas.append({'channel': args.channel_numbers[i], **delta})
        print(f"  ch{args.channel_numbers[i]} USB transfers (NOT CAN frames): "
              f"OUT={delta['tx_completed']} IN={delta['rx_completed']} "
              f"wire_bytes OUT={delta['tx_bytes']} IN={delta['rx_bytes']}; "
              f"errors OUT={delta['tx_errors']} IN={delta['rx_errors']} "
              f"app_drop={delta['app_rx_dropped']} overflow={delta['app_rx_overflow']}")
        bad_stats |= any(delta[k] != 0 for k in
                         ('tx_errors', 'rx_errors', 'app_rx_dropped', 'app_rx_overflow'))
    for i, bus in enumerate(buses):
        counters = bus.get_berr_counter()
        controller_states.append({'channel': args.channel_numbers[i],
                                  'state': str(bus.state), 'bec': counters})
        print(f"  ch{args.channel_numbers[i]} state={bus.state} bec={counters}")
        bad_stats |= (bus.state != can.BusState.ACTIVE or any(
            counters.get(k, 0) > error_baseline[i].get(k, 0) for k in ('rxerr', 'txerr')))
    links = []
    for (receiver, sender), count in check.counts.items():
        equal = count == sent[sender] == args.frames and check.link_bytes[(receiver, sender)] == sent_bytes[sender]
        bad_stats |= not equal
        links.append({'sender': args.channel_numbers[sender], 'receiver': args.channel_numbers[receiver],
                      'tx_frames': sent[sender], 'rx_valid_frames': count,
                      'tx_payload_bytes': sent_bytes[sender],
                      'rx_payload_bytes': check.link_bytes[(receiver, sender)], 'equal': equal})
        print(f"  CAN ch{args.channel_numbers[sender]} -> ch{args.channel_numbers[receiver]}: "
              f"TX={sent[sender]}/{sent_bytes[sender]}B "
              f"RX={count}/{check.link_bytes[(receiver, sender)]}B "
              f"expected={args.frames} {'MATCH' if equal else 'MISMATCH'}")
        if count < sent[sender] and not check.invalid:
            print(f"  pending ch{args.channel_numbers[sender]} -> "
                  f"ch{args.channel_numbers[receiver]}: "
                  f"sequence {count}..{sent[sender] - 1} (no retransmission)")
    if check.first_failure is not None:
        failure = check.first_failure
        print(f"  first integrity failure: ch{failure['sender']} -> "
              f"ch{failure['receiver']} expected_sequence={failure['expected_sequence']} "
              f"actual_sequence={failure['actual_sequence']} "
              f"submitted={failure['submitted']}")
        print(f"    mismatches: {', '.join(failure['reasons'])}")
        print(f"    expected: {failure['expected']}")
        print(f"    actual:   {failure['actual']}")
        print(f"    matches own sequence: {failure['matches_own_sequence']}")
        print("    first anomaly samples (receiver, sender, sequence, self-consistent): "
              + str([(f['receiver'], f['sender'], f['actual_sequence'],
                      f['matches_own_sequence']) for f in check.failure_samples]))
        print("    RX counts include validated frames only; their deficit is not a loss count.")
    passed = (bool(check.counts) and not check.invalid and not bad_stats
              and all(n == args.frames for n in check.counts.values())
              and all(n == args.frames for n in sent.values()))
    print(f"  TX={sum(sent.values())}/{tx_bytes}B RX={sum(check.counts.values())}/"
          f"{check.rx_bytes}B invalid/duplicate/reorder={check.invalid} "
          f"elapsed={elapsed:.3f}s TX_fps={sum(sent.values()) / max(elapsed, 1e-9):.1f}")
    if hasattr(args, 'phase_results'):
        args.phase_results.append({'label': label, 'passed': passed,
            'bitrate': args.bitrate, 'data_bitrate': args.data_bitrate if fd else None,
            'fd': fd, 'brs': brs, 'loopback': loopback, 'links': links,
            'rx_observed_frames': check.observed, 'invalid_frames': check.invalid,
            'anomalies': check.failure_samples, 'usb_transfers': usb_deltas,
            'controllers': controller_states, 'elapsed_seconds': elapsed})
    _stop_round(buses)
    print('  PHASE OK' if passed else '  PHASE FAIL')
    return passed



def _matrix_message(channel, sequence, fd, brs):
    # Repeat the complete matrix to honor the requested frame count. Keep
    # identifiers bounded for standard frames, including zero-length and RTR.
    serial = sequence
    sequence %= 100 if fd else 36
    if sequence < 36:
        extended = bool(sequence // 18)
        remote = bool((sequence % 18) // 9)
        length = sequence % 9
        is_fd, use_brs = False, False
    else:
        index = sequence - 36
        extended, use_brs = bool(index // 32), bool((index % 32) // 16)
        length = (0, 1, 2, 3, 4, 5, 6, 7, 8, 12, 16, 20, 24, 32, 48, 64)[index % 16]
        remote, is_fd = False, True
    identifier = 0x100 + channel * 0x100 + sequence
    if extended:
        identifier |= 0x18000000
    data = b'' if remote else bytes((serial + channel + n) & 255 for n in range(length))
    return can.Message(arbitration_id=identifier, is_extended_id=extended,
        is_remote_frame=remote, is_fd=is_fd, bitrate_switch=use_brs,
        dlc=length, data=data)


def _socketcan_raw_phases(args, buses):
    saved = (args.frames, args.window, args.bitrate, args.data_bitrate, args.sender_indices[:])
    try:
        for rate in (250000, 1000000):
            args.bitrate, args.window = rate, 1
            _shutdown_buses(buses)
            buses.extend(_open_buses(args, fd=False, loopback=False))
            args.message_factory, args.matrix_size = _matrix_message, 36
            for sender in (0, 1):
                args.sender_indices = [sender]
                _run_phase(args, buses, False, False, False, f'MIXED CLASSIC {rate} ch{args.channel_numbers[sender]}')
        if not args.skip_fd:
            args.bitrate, args.data_bitrate = 1000000, 5000000
            _shutdown_buses(buses)
            buses.extend(_open_buses(args, fd=True, loopback=False))
            args.matrix_size = 100
            for sender in (0, 1):
                args.sender_indices = [sender]
                _run_phase(args, buses, True, True, False, f'MIXED CLASSIC/FD ch{args.channel_numbers[sender]}')
        del args.message_factory, args.matrix_size
        args.frames, args.window = saved[:2]
        args.bitrate, args.sender_indices = 500000, [0]
        _shutdown_buses(buses)
        buses.extend(_open_buses(args, fd=False, loopback=False))
        _run_phase(args, buses, False, False, False, 'SEQUENCE CLASSIC A->B 500k')
        if not args.skip_fd:
            args.bitrate, args.data_bitrate, args.sender_indices = 1000000, 5000000, [1]
            _shutdown_buses(buses)
            buses.extend(_open_buses(args, fd=True, loopback=False))
            _run_phase(args, buses, True, True, False, 'SEQUENCE FD B->A 1M/5M')
    finally:
        args.frames, args.window, args.bitrate, args.data_bitrate, args.sender_indices = saved
        for name in ('message_factory', 'matrix_size'):
            if hasattr(args, name):
                delattr(args, name)

    _shutdown_buses(buses)
    buses.extend(_open_buses(args, fd=False, loopback=False))


class PhaseFailure(RuntimeError):
    """A completed phase failed validation; do not start another phase."""


def _run_phase(*args, **kwargs):
    if not _phase(*args, **kwargs):
        raise PhaseFailure("phase validation failed; stopping suite")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(
        description="python-can USB integrity and windowed stress test")
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
    parser.add_argument("--profile", choices=("functional", "stress", "socketcan"), default="stress")
    parser.add_argument("--results-json", type=Path)
    parser.add_argument("--reopen-loops", type=int, default=4)
    parser.add_argument("--frames", type=int, default=None)
    parser.add_argument("--window", type=int, default=None)
    parser.add_argument("--rounds", type=int, default=1)
    parser.add_argument("--receive-timeout", type=float, default=2.0)
    parser.add_argument("--drain-time", type=float, default=0.2)
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
    args.phase_results = []
    args.coverage_gaps = (['ECU periodic scheduling', 'ISO-TP', 'J1939 TP',
        'canfdtest request/reply semantics', 'measured 90% load / saturation', 'physical ESI fault injection']
        if args.profile == 'socketcan' else [])
    if args.frames is None:
        args.frames = 1000 if args.profile != 'functional' else 60
    if args.window is None:
        args.window = 32 if args.profile != 'functional' else 1
    if not 1 <= args.frames <= 2**32:
        parser.error('--frames must be 1..2**32')
    if not 1 <= args.window <= 1024 or args.rounds < 1 or args.reopen_loops < 1:
        parser.error('--window must be 1..1024 and --rounds >= 1')
    if (not all(math.isfinite(v) for v in
                (args.receive_timeout, args.drain_time))
            or args.receive_timeout <= 0 or args.drain_time < 0
            or args.timeout_ms <= 0):
        parser.error('invalid timeout/drain-time')
    # The repository tool must remain usable when only one independent backend
    # is installed.  Import only the protocol selected by the user.
    bus_class = _backend_class(args.interface)
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
    if args.profile == 'socketcan' and (len(args.channel_numbers) != 2 or args.senders is not None or args.mode == 'loopback'):
        parser.error('socketcan profile requires two physical bus channels and all senders')
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
    suite_started = time.monotonic()
    try:
        selected_senders = args.sender_indices[:]
        for round_index in range(args.rounds):
            print(f"\n=== ROUND {round_index + 1}/{args.rounds} ({args.profile}) ===")
            args.sender_indices = selected_senders[:]
            buses = _open_buses(args, fd=False, loopback=loopback)
            for bus in buses:
                caps = bus.get_capabilities()
                print(f"ch{bus.channel}: clock={caps['clock_hz']}Hz FD={caps['fd']}")
                if not args.skip_fd and not caps['fd']:
                    raise PhaseFailure("device lacks CAN FD; use --skip-fd for explicit classic-only coverage")
            info = buses[0].get_device_info()
            print(f"device: {args.interface} index={args.device}")
            print(f"channels: {', '.join(str(i) for i in args.channel_numbers)}")
            print("TX: unpaced, bounded by USB completion and receive window")
            print(f"test mode: {'loopback' if loopback else 'bus'}")
            if info:
                print(f"info: sw={info['sw_version_text']} usb={info['usb_speed']}")
            if not args.no_termination:
                if not _verify_termination(buses):
                    raise PhaseFailure("termination readback failed")
            if args.profile == 'socketcan':
                _socketcan_raw_phases(args, buses)
            passed = _run_phase(
                args, buses, False, False, loopback,
                "BUS 1" if not loopback else "LOOPBACK 1") and passed

            if not args.skip_fd:
                passed = _run_phase(
                    args, buses, True, False, loopback,
                    "BUS 2" if not loopback else "LOOPBACK 2") and passed
                passed = _run_phase(
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
                passed = _run_phase(
                    args, buses, False, False, True, "LOOPBACK 1") and passed
                if not args.skip_fd:
                    passed = _run_phase(
                        args, buses, True, False, True, "LOOPBACK 2") and passed
                    passed = _run_phase(
                        args, buses, True, True, True, "LOOPBACK 3") and passed
            _shutdown_buses(buses)
            if args.profile == 'socketcan':
                for reopen_index in range(args.reopen_loops):
                    fd_reopen = bool(reopen_index % 2) and not args.skip_fd
                    buses = _open_buses(args, fd=fd_reopen, loopback=False)
                    _run_phase(args, buses, fd_reopen, fd_reopen, False, f'REOPEN {reopen_index + 1}')
                    _shutdown_buses(buses)
            if not passed:
                break
        if args.profile == 'socketcan':
            print('Coverage gaps versus kernel stress.sh: ' + ', '.join(args.coverage_gaps))
        print("\nTEST PASSED" if passed else "\nTEST FAILED")
        if args.skip_fd:
            print("partial coverage: FD stages skipped")
        return 0 if passed else 1
    except PhaseFailure as exc:
        print(f"TEST FAILED: {exc}")
        return 1
    except (can.CanError, OSError, RuntimeError) as exc:
        print(f"ERROR: {exc}")
        if buses:
            _print_usb_stats(args, buses)
        return 2
    finally:
        try:
            _shutdown_buses(buses)
        finally:
            if args.results_json:
                args.results_json.parent.mkdir(parents=True, exist_ok=True)
                all_links = [link for phase in args.phase_results for link in phase['links']]
                totals = {key: sum(link[key] for link in all_links) for key in
                    ('tx_frames', 'rx_valid_frames', 'tx_payload_bytes', 'rx_payload_bytes')}
                totals['all_completed_phases_passed'] = bool(args.phase_results) and all(p['passed'] for p in args.phase_results)
                args.results_json.write_text(json.dumps({'phases': args.phase_results, 'totals': totals,
                    'coverage_gaps': args.coverage_gaps,
                    'counter_units': {'links': 'CAN frames and payload bytes', 'usb_transfers': 'USB completion events and wire bytes'}}, indent=2), encoding='utf-8')
            print(f"suite elapsed: {time.monotonic() - suite_started:.3f}s")


if __name__ == "__main__":
    raise SystemExit(main())
