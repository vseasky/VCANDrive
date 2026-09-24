"""Read-only USB device discovery and information for the mode manager."""
from contextlib import contextmanager
import importlib
from pathlib import Path
import struct
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

def backend_components(interface):
    if interface not in ('vcan_usb', 'vkgs_usb'):
        raise ValueError('USB information is available in VCAN/GS_CAN modes only')
    package = importlib.import_module(interface)
    backend = getattr(package, interface + '_bus')
    module = importlib.import_module(backend.__module__)
    return backend, module, backend.DEVICE_MODULE


@contextmanager
def open_device(interface, selector, channel=0, timeout_ms=2000):
    _, module, protocol = backend_components(interface)
    session, transport = module._UsbSession.acquire(protocol.VID, protocol.PID,
        channel=channel, index=selector.get('index', 0), bus=selector.get('bus'),
        address=selector.get('address'), port_path=selector.get('port_path'))
    try:
        device = protocol.CanDevice(device=transport, channel=channel,
            timeout_ms=timeout_ms, control_device=session.control_device)
        yield device, protocol
    finally:
        session.release(transport)


def identity(device, protocol):
    # Read all hardware version bits; the public backend exposes only revision bits.
    raw = device.get_control(protocol.BREQ_BSP_DEVICE_INFO, 40)
    sw, hw, *words = struct.unpack('<II8I', raw)
    magic = (hw >> 16) & 255
    return {'device_type': magic,
            'sw_version': sw, 'sw_version_full': '.'.join(str((sw >> n) & 255) for n in (24, 16, 8, 0)),
            'raw_hw_version': hw, 'uid': words[:4], 'uuid': words[4:],
            'uid_hex': ''.join(f'{word:08x}' for word in words[:4]),
            'raw_info': raw.hex()}


def inspect(interface, selector, channel=0):
    backend, _, _ = backend_components(interface)
    interfaces = backend.discover_interfaces(index=selector.get('index', 0),
        bus=selector.get('bus'), address=selector.get('address'), port_path=selector.get('port_path'))
    selected = next((entry for entry in interfaces if entry.number == channel), None)
    if selected is None:
        raise RuntimeError('selected CAN interface not found')
    pinned = {'index': 0, 'bus': selected.bus, 'port_path': tuple(selected.port_path)}
    if not pinned['port_path']:
        raise RuntimeError('stable USB port path unavailable; cannot safely pin reconnect')
    with open_device(interface, pinned, channel) as (device, protocol):
        info = identity(device, protocol)
    info['selector'] = pinned
    info['channels'] = [entry.number for entry in interfaces]
    return info
