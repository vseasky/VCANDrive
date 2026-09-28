"""Repository-layout shim for the VKGS USB backend."""
from __future__ import annotations

from pathlib import Path

# Let source-tree users import the same modules as an installed package.
_inner = str(Path(__file__).with_name("vkgs_usb"))
if _inner not in __path__:
    __path__.insert(0, _inner)

__all__ = ["vkgs_usb_bus"]
__version__ = "0.2.0"


def __getattr__(name):
    if name == "vkgs_usb_bus":
        from .bus import vkgs_usb_bus

        return vkgs_usb_bus
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
