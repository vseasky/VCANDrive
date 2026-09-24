"""Repository-layout shim for the VKGS USB backend."""
from __future__ import annotations

from pathlib import Path
import sys as _sys

_inner = str(Path(__file__).with_name("vkgs_usb"))
if _inner not in __path__:
    __path__.insert(0, _inner)

from .vkgs_usb import bus as bus
from .vkgs_usb import protocol as protocol
from .vkgs_usb import transport as transport
from .vkgs_usb.bus import vkgs_usb_bus

for _name, _module in (("bus", bus), ("protocol", protocol),
                       ("transport", transport)):
    _sys.modules.setdefault(f"{__name__}.{_name}", _module)

__all__ = ["vkgs_usb_bus"]
__version__ = "0.2.0"
