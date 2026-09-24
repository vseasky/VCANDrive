"""Repository-layout shim for the VCAN USB backend."""
from __future__ import annotations

from pathlib import Path
import sys as _sys

_inner = str(Path(__file__).with_name("vcan_usb"))
if _inner not in __path__:
    __path__.insert(0, _inner)

from .vcan_usb import bus as bus
from .vcan_usb import protocol as protocol
from .vcan_usb import transport as transport
from .vcan_usb.bus import vcan_usb_bus

for _name, _module in (("bus", bus), ("protocol", protocol),
                       ("transport", transport)):
    _sys.modules.setdefault(f"{__name__}.{_name}", _module)

__all__ = ["vcan_usb_bus"]
__version__ = "0.2.0"
