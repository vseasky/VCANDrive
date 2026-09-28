"""USB descriptors and transfer sizing, independent of CAN and USB backends.

Kept inside each distributable driver so either package installs on its own.
"""
from __future__ import annotations

from dataclasses import dataclass

USB_FS_MAX_PACKET_SIZE = 64
USB_HS_MAX_PACKET_SIZE = 512
USB_SS_MAX_PACKET_SIZE = 1024
# A one-packet read does not wait for legacy firmware to append a ZLP.
DEFAULT_RX_TRANSFER_SIZE = 1
RX_TRANSFER_COUNT = 30
_VALID_BULK_PACKET_SIZES = frozenset((
    8, 16, 32, USB_FS_MAX_PACKET_SIZE, USB_HS_MAX_PACKET_SIZE, USB_SS_MAX_PACKET_SIZE,
))
_ENDPOINT_SPEED_NAMES = {USB_HS_MAX_PACKET_SIZE: "HS", USB_SS_MAX_PACKET_SIZE: "SS"}
_USB_SPEED_NAMES = {1: "LS", 2: "FS", 3: "HS", 4: "SS", 5: "SS+"}
UsbPortPath = tuple[int, ...]


def format_port_path(port_path: UsbPortPath) -> str:
    """Format a physical USB path without requiring an open device."""
    return ".".join(str(number) for number in port_path) if port_path else "-"


def usb_speed_info(speed: int | None, packet_size: int) -> tuple[str, str]:
    """Return speed and its evidence; a 64-byte endpoint cannot prove FS."""
    if speed in _USB_SPEED_NAMES:
        return _USB_SPEED_NAMES[speed], "negotiated"
    if packet_size in _ENDPOINT_SPEED_NAMES:
        return _ENDPOINT_SPEED_NAMES[packet_size], "endpoint"
    return "unknown", "unknown"


def rx_transfer_size(packet_size: int, minimum: int = DEFAULT_RX_TRANSFER_SIZE) -> int:
    """Round host buffers to whole USB packets, independently of CAN frame size."""
    if packet_size not in _VALID_BULK_PACKET_SIZES:
        raise ValueError(f"invalid bulk endpoint packet size: {packet_size}")
    if minimum < 1:
        raise ValueError("bulk-IN buffer size must be positive")
    return ((minimum + packet_size - 1) // packet_size) * packet_size


@dataclass(frozen=True)
class Endpoint:
    bEndpointAddress: int
    wMaxPacketSize: int


@dataclass(frozen=True)
class InterfaceInfo:
    number: int
    ep_in: Endpoint
    ep_out: Endpoint
    bus: int
    address: int
    port_path: UsbPortPath
    speed: int | None = None
