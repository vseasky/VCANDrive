"""Repository-layout shim for the VKGS USB backend."""
from __future__ import annotations

from pathlib import Path

_inner = str(Path(__file__).with_name("vkgs_usb"))
if _inner not in __path__:
    __path__.insert(0, _inner)

from .bus import vkgs_usb_bus

__all__ = ["vkgs_usb_bus"]
__version__ = "0.2.0"
