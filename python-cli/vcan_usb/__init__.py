"""Repository-layout shim for the VCAN USB backend."""
from __future__ import annotations

from pathlib import Path

_inner = str(Path(__file__).with_name("vcan_usb"))
if _inner not in __path__:
    __path__.insert(0, _inner)

from .bus import vcan_usb_bus

__all__ = ["vcan_usb_bus"]
__version__ = "0.2.0"
