"""VCAN USB backend for python-can."""

__all__ = ["vcan_usb_bus"]
__version__ = "0.2.0"


def __getattr__(name):
    if name == "vcan_usb_bus":
        from .bus import vcan_usb_bus

        return vcan_usb_bus
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
