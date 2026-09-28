"""Repository-layout shim for the VCAN USB backend."""
from __future__ import annotations

from pathlib import Path

# Let source-tree users import the same modules as an installed package.
_inner = str(Path(__file__).with_name("vcan_usb"))
if _inner not in __path__:
    __path__.insert(0, _inner)

__all__ = ["vcan_usb_bus"]
__version__ = "0.2.0"


def __getattr__(name):
    if name == "vcan_usb_bus":
        from .bus import vcan_usb_bus

        return vcan_usb_bus
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
