"""Shared helpers for the hardware acceptance test and its unit tests.

Kept separate from hardware_test.py so unit tests import a plain library
module instead of reaching into a runnable script's private functions.
"""
from __future__ import annotations

import argparse

import can


def _backend_class(interface: str):
    """Resolve repository backends without installed entry-point metadata."""
    if interface == "vcan_usb":
        from vcan_usb import vcan_usb_bus
        return vcan_usb_bus
    if interface == "vkgs_usb":
        from vkgs_usb import vkgs_usb_bus
        return vkgs_usb_bus
    raise ValueError(f"unsupported interface: {interface}")


def _arbitration_id(sender: int) -> int:
    """Match the SocketCAN test's 0x100 + sender-channel identifier."""
    if sender < 0 or sender > 0x6FF:
        raise ValueError("sender channel does not fit a standard CAN ID")
    return 0x100 + sender


def _open_buses(args: argparse.Namespace, fd: bool,
                loopback: bool) -> list[can.BusABC]:
    buses: list[can.BusABC] = []
    backend = _backend_class(args.interface)
    try:
        for position, channel in enumerate(args.channel_numbers):
            termination = None
            if not args.no_termination:
                # Match SocketCAN: every isolated internal-loopback channel
                # gets termination; on a shared bus only the two ends do.
                termination = (loopback or
                               position in (0, args.channels - 1))
            buses.append(backend(
                channel=channel, index=args.device,
                bus=args.usb_bus, address=args.usb_address,
                port_path=args.usb_port_path,
                bitrate=args.bitrate, sample_point=args.sample_point,
                fd=fd, data_bitrate=args.data_bitrate,
                data_sample_point=args.data_sample_point,
                timeout_ms=args.timeout_ms, loopback=loopback,
                receive_own_messages=loopback,
                termination=termination, bus_load_reporting=False,
                auto_start=False,
            ))
        return buses
    except Exception:
        # Preserve the acquisition error while still releasing every handle.
        try:
            _shutdown_buses(buses)
        except Exception:
            pass
        raise


def _shutdown_buses(buses: list[can.BusABC]) -> None:
    closing = list(reversed(buses))
    buses.clear()
    first_error = None
    for bus in closing:
        try:
            bus.shutdown()
        except Exception as exc:
            if first_error is None:
                first_error = exc
    if first_error is not None:
        raise first_error
