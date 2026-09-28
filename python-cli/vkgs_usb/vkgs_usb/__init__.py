"""VKGS USB backend for python-can."""

__all__ = ["vkgs_usb_bus"]
__version__ = "0.2.0"


def __getattr__(name):
    if name == "vkgs_usb_bus":
        from .bus import vkgs_usb_bus

        return vkgs_usb_bus
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
