"""Small two-channel python-can API check for a wired test bus.

Run from python-cli after installing the backend for the current USB mode.
The example keeps the existing termination state. Use the full hardware test
for load, loss, timing and repeated-open validation.
"""

from __future__ import annotations

import argparse
from contextlib import ExitStack
import sys

import can


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--interface", choices=("vcan_usb", "vkgs_usb"), required=True)
    parser.add_argument("--channel-a", type=int, default=0)
    parser.add_argument("--channel-b", type=int, default=1)
    parser.add_argument("--port-path", help="USB port path from canctl list")
    parser.add_argument("--frames", type=int, default=1,
                        help="frames in each direction (default: 1)")
    parser.add_argument("--fd", action="store_true",
                        help="check CAN FD with BRS at 1/5 Mbit/s")
    args = parser.parse_args()
    if args.channel_a == args.channel_b:
        parser.error("choose two different channels")
    if args.frames < 1:
        parser.error("--frames must be >= 1")
    return args


def check_direction(sender: can.BusABC, receiver: can.BusABC,
                    can_id: int, count: int, fd: bool) -> bool:
    for sequence in range(count):
        payload = sequence.to_bytes(4, "little", signed=False) + b"\xA5\x5A\x00\xFF"
        if fd:
            payload += bytes(range(8))
        sent = can.Message(arbitration_id=can_id, is_extended_id=False,
                           is_fd=fd, bitrate_switch=fd, data=payload)
        sender.send(sent, timeout=2.0)
        received = receiver.recv(timeout=5.0)
        if received is None:
            print(f"FAIL id=0x{can_id:X} seq={sequence}: receive timeout", file=sys.stderr)
            return False
        if (received.arbitration_id != sent.arbitration_id or
                received.is_extended_id != sent.is_extended_id or
                received.is_fd != sent.is_fd or
                received.bitrate_switch != sent.bitrate_switch or
                bytes(received.data) != bytes(sent.data)):
            print(f"FAIL id=0x{can_id:X} seq={sequence}:\n"
                  f"  sent={sent}\n  received={received}", file=sys.stderr)
            return False
    print(f"PASS id=0x{can_id:X}: TX={count} RX={count}, ID/flags/payload match")
    return True


def main() -> int:
    args = parse_args()
    common = {"interface": args.interface, "fd": args.fd,
              "bitrate": 1_000_000 if args.fd else 500_000}
    if args.fd:
        common["data_bitrate"] = 5_000_000
    if args.port_path:
        common["port_path"] = args.port_path

    with ExitStack() as stack:
        a = stack.enter_context(can.Bus(channel=args.channel_a, **common))
        b = stack.enter_context(can.Bus(channel=args.channel_b, **common))
        print(f"A: {a.channel_info}")
        print(f"B: {b.channel_info}")
        if not check_direction(a, b, 0x321, args.frames, args.fd):
            return 1
        if not check_direction(b, a, 0x322, args.frames, args.fd):
            return 1
    print("COMPLETE API CHECK PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
