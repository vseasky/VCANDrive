#!/usr/bin/env python3
"""Small command-line client for the repository's python-can USB backends."""
from __future__ import annotations

import argparse
import sys
import time

import can


def _int(value: str) -> int:
    try:
        return int(value, 0)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"expected an integer such as 123 or 0x123, got {value!r}") from exc


def _data(value: str) -> bytes:
    compact = value.replace(" ", "").replace(":", "").replace("-", "")
    if compact.lower() in {"", "none", "-"}:
        return b""
    try:
        return bytes.fromhex(compact)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "data must contain an even number of hexadecimal digits") from exc


def _port_path(value: str) -> tuple[int, ...]:
    text = value.strip()
    if text in {"", "-"}:
        raise argparse.ArgumentTypeError(
            "USB port path must be like 1.2.3")
    try:
        result = tuple(int(item) for item in text.replace(",", ".").split("."))
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"USB port path must be like 1.2.3, got {value!r}") from exc
    if not result or any(item < 0 or item > 255 for item in result):
        raise argparse.ArgumentTypeError(
            "USB port path entries must be 0..255")
    return result


def _format_port_path(port_path: tuple[int, ...]) -> str:
    return ".".join(str(item) for item in port_path) if port_path else "-"


def _backend(interface: str):
    if interface == "vcan_usb":
        from vcan_usb import vcan_usb_bus
        return vcan_usb_bus
    from vkgs_usb import vkgs_usb_bus
    return vkgs_usb_bus


def _bus_kwargs(args: argparse.Namespace, *, auto_start: bool = True) -> dict:
    termination = {"keep": None, "on": True, "off": False}[args.termination]
    return {
        "interface": args.interface,
        "channel": args.channel,
        "index": args.device,
        "bus": args.usb_bus,
        "address": args.usb_address,
        "port_path": args.usb_port_path,
        "bitrate": args.bitrate,
        "sample_point": args.sample_point,
        "fd": args.fd,
        "data_bitrate": args.data_bitrate,
        "data_sample_point": args.data_sample_point,
        "loopback": args.loopback,
        "termination": termination,
        "bus_load_reporting": False,
        "timeout_ms": args.usb_timeout_ms,
        "auto_start": auto_start,
    }


def _format_message(message: can.Message) -> str:
    width = 8 if message.is_extended_id else 3
    frame_type = "FD+BRS" if message.bitrate_switch else (
        "FD" if message.is_fd else ("RTR" if message.is_remote_frame else "CAN"))
    data = message.data.hex(" ").upper()
    return (f"{message.timestamp:14.6f} ch{message.channel} "
            f"{message.arbitration_id:0{width}X} {frame_type:<6} "
            f"[{message.dlc:>2}] {data}")


def _list(args: argparse.Namespace) -> int:
    backend = _backend(args.interface)
    interfaces = backend.discover_interfaces(
        index=args.device, bus=args.usb_bus, address=args.usb_address,
        port_path=args.usb_port_path)
    if not interfaces:
        print("no CAN interfaces found", file=sys.stderr)
        return 1
    for item in interfaces:
        print(
            f"channel={item.number} "
            f"bus={item.bus} "
            f"address={item.address} "
            f"port_path={_format_port_path(item.port_path)} "
            f"ep_in=0x{item.ep_in.bEndpointAddress:02x} "
            f"ep_out=0x{item.ep_out.bEndpointAddress:02x} "
            f"in_mps={item.ep_in.wMaxPacketSize} "
            f"out_mps={item.ep_out.wMaxPacketSize}"
        )
    return 0


def _send(args: argparse.Namespace) -> int:
    payload = args.data
    if args.rtr and payload:
        raise ValueError("RTR frame cannot contain data")
    if not args.fd and len(payload) > 8:
        raise ValueError("classic CAN payload cannot exceed 8 bytes; add --fd")
    if args.fd and len(payload) > 64:
        raise ValueError("CAN FD payload cannot exceed 64 bytes")
    message = can.Message(
        arbitration_id=args.can_id,
        is_extended_id=args.extended or args.can_id > 0x7FF,
        is_remote_frame=args.rtr,
        is_fd=args.fd,
        bitrate_switch=args.brs,
        dlc=args.dlc if args.rtr else len(payload),
        data=b"" if args.rtr else payload,
        check=True,
    )
    with can.Bus(**_bus_kwargs(args)) as bus:
        for sequence in range(args.count):
            bus.send(message, timeout=args.timeout)
            print(f"TX {sequence + 1}/{args.count}: {_format_message(message)}")
            if sequence + 1 < args.count and args.gap:
                time.sleep(args.gap)
    return 0


def _receive(args: argparse.Namespace) -> int:
    with can.Bus(**_bus_kwargs(args)) as bus:
        received = 0
        while args.count == 0 or received < args.count:
            message = bus.recv(args.timeout)
            if message is None:
                if args.count == 0:
                    continue
                print(f"receive timeout ({received}/{args.count})", file=sys.stderr)
                return 1
            received += 1
            print(_format_message(message), flush=True)
    return 0


def _termination(args: argparse.Namespace) -> int:
    # Open stopped so termination is never changed while MCAN is running.
    with can.Bus(**_bus_kwargs(args, auto_start=False)) as bus:
        if args.state != "show":
            bus.set_termination(args.state == "on")
        enabled = bus.get_termination()
        print(f"ch{args.channel}: termination {'120 ohm' if enabled else 'off'}")
    return 0


def _stats(args: argparse.Namespace) -> int:
    with can.Bus(**_bus_kwargs(args)) as bus:
        if args.duration:
            deadline = time.monotonic() + args.duration
            while time.monotonic() < deadline:
                bus.recv(min(0.1, deadline - time.monotonic()))
        for key, value in bus.get_usb_stats().items():
            print(f"{key}={value}")
    return 0


def _state(args: argparse.Namespace) -> int:
    with can.Bus(**_bus_kwargs(args)) as bus:
        if args.duration:
            deadline = time.monotonic() + args.duration
            while time.monotonic() < deadline:
                bus.recv(min(0.1, deadline - time.monotonic()))
        counters = bus.get_berr_counter()
        print(f"ch{args.channel}: state={bus.state.name} "
              f"rxerr={counters['rxerr']} txerr={counters['txerr']}")
    return 0


def _identify(args: argparse.Namespace) -> int:
    with can.Bus(**_bus_kwargs(args, auto_start=False)) as bus:
        identify = getattr(bus, "identify", None)
        if identify is None:
            raise RuntimeError(
                f"{args.interface} does not support identify/blink "
                "(only vkgs_usb does; the vcan_usb firmware does not "
                "implement this request)")
        identify(args.state == "on")
        print(f"ch{args.channel}: identify {args.state}")
    return 0


def _usb_mode(args: argparse.Namespace) -> int:
    if not args.yes:
        answer = input(
            f"Switch device {args.device} channel {args.channel} from "
            f"{args.interface} to USB mode {args.target!r} and reboot it? "
            "[y/N] "
        )
        if answer.strip().lower() not in {"y", "yes"}:
            print("USB mode switch cancelled")
            return 1
    backend = _backend(args.interface)
    backend.switch_usb_mode(
        args.target,
        channel=args.channel,
        index=args.device,
        bus=args.usb_bus,
        address=args.usb_address,
        port_path=args.usb_port_path,
        timeout_ms=args.usb_timeout_ms,
    )
    print(
        f"USB mode switch requested: {args.interface} -> {args.target}; "
        "the device is rebooting and will re-enumerate"
    )
    if args.target == "peak":
        print("note: peak mode is operated by a PEAK-compatible driver, not this Python backend")
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Terminal client for vcan_usb and vkgs_usb python-can backends")
    parser.add_argument("--interface", choices=("vcan_usb", "vkgs_usb"),
                        default="vkgs_usb")
    parser.add_argument("--channel", type=int,
                        help="USB CAN interface number (required except for list)")
    parser.add_argument("--device", type=int, default=0,
                        help="same-VID/PID device enumeration index")
    parser.add_argument("--usb-bus", type=int)
    parser.add_argument("--usb-address", type=int)
    parser.add_argument("--usb-port-path", type=_port_path,
                        help="USB topology path from list, for example 1.2.3")
    parser.add_argument("--bitrate", type=int, default=1_000_000)
    parser.add_argument("--sample-point", type=float, default=75.0)
    parser.add_argument("--fd", action="store_true",
                        help="configure CAN FD; send also creates an FD frame")
    parser.add_argument("--data-bitrate", type=int, default=5_000_000)
    parser.add_argument("--data-sample-point", type=float, default=75.0)
    parser.add_argument("--loopback", action="store_true")
    parser.add_argument("--termination", choices=("keep", "on", "off"),
                        default="keep")
    parser.add_argument("--usb-timeout-ms", type=int, default=2_000)

    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="list descriptor-discovered CAN interfaces")

    send = commands.add_parser("send", help="send one or more CAN frames")
    send.add_argument("can_id", type=_int)
    send.add_argument("data", nargs="?", type=_data, default=b"")
    send.add_argument("--extended", action="store_true")
    send.add_argument("--rtr", action="store_true")
    send.add_argument("--dlc", type=int, default=0, help="RTR requested length")
    send.add_argument("--brs", action="store_true")
    send.add_argument("--count", type=int, default=1)
    send.add_argument("--gap", type=float, default=0.0)
    send.add_argument("--timeout", type=float, default=1.0)

    receive = commands.add_parser("recv", help="receive frames; count=0 runs forever")
    receive.add_argument("--count", type=int, default=1)
    receive.add_argument("--timeout", type=float, default=1.0)

    termination = commands.add_parser("termination", help="read or change 120 ohm")
    termination.add_argument("state", choices=("show", "on", "off"))

    stats = commands.add_parser("stats", help="show interface-local USB counters")
    stats.add_argument("--duration", type=float, default=0.0,
                       help="collect received transfers for this many seconds")

    state = commands.add_parser(
        "state", help="show CAN bus state and error counters")
    state.add_argument("--duration", type=float, default=0.0,
                       help="collect received transfers for this many seconds")

    identify = commands.add_parser(
        "identify", help="start/stop the device LED-blink identify pattern "
                          "(vkgs_usb only)")
    identify.add_argument("state", choices=("on", "off"))

    usb_mode = commands.add_parser(
        "usb_mode", help="persistently switch USB protocol mode and reboot")
    usb_mode.add_argument("target", choices=("vcan", "peak", "gs_usb"))
    usb_mode.add_argument("--yes", action="store_true",
                          help="skip the reboot confirmation prompt")
    return parser


def main() -> int:
    parser = _parser()
    args = parser.parse_args()
    if args.command != "list" and args.channel is None:
        parser.error(f"{args.command} requires --channel")
    if args.channel is not None and args.channel < 0:
        parser.error("--channel must be >= 0")
    if getattr(args, "brs", False) and not args.fd:
        parser.error("--brs requires the global --fd option")
    if getattr(args, "count", 1) < 0:
        parser.error("--count must be >= 0")
    try:
        return {
            "list": _list,
            "send": _send,
            "recv": _receive,
            "termination": _termination,
            "stats": _stats,
            "state": _state,
            "identify": _identify,
            "usb_mode": _usb_mode,
        }[args.command](args)
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130
    except (ImportError, can.CanError, OSError, RuntimeError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
