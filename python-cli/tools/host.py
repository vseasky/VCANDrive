"""Read-only host diagnostics plus explicit Linux setup actions. No automatic detach."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import subprocess
import sys

USB_ROOT = Path('/sys/bus/usb/devices')
NET_ROOT = Path('/sys/class/net')
RULE_PATH = Path('/etc/udev/rules.d/70-vcandrive.rules')
PRODUCTS = {'6080': 'vcan_usb', '606f': 'vkgs_usb'}
DRIVERS = {'vcan_usb', 'vkgs_usb', 'gs_usb'}


def _read(path: Path) -> str:
    try:
        return path.read_text().strip()
    except OSError:
        return ''


def linux_boards(root: Path | None = None) -> list[dict]:
    root = USB_ROOT if root is None else root
    boards = []
    for device in sorted(root.glob('*')):
        product = _read(device / 'idProduct').lower()
        if _read(device / 'idVendor').lower() != '1d50' or product not in PRODUCTS:
            continue
        interfaces = []
        for interface in sorted(root.glob(device.name + ':*')):
            channel = _read(interface / 'bInterfaceNumber')
            if not channel:
                continue
            driver_link = interface / 'driver'
            driver = driver_link.resolve().name if driver_link.is_symlink() else None
            interfaces.append({'path': interface.name, 'channel': int(channel, 16),
                               'driver': driver, 'netdevs': sorted(p.name for p in (interface / 'net').glob('*'))})
        bus, address = _read(device / 'busnum'), _read(device / 'devnum')
        node = (Path(f'/dev/bus/usb/{int(bus):03d}/{int(address):03d}')
                if bus and address else None)
        boards.append({'path': device.name, 'backend': PRODUCTS[product], 'product': product,
                       'bus': bus, 'address': address, 'interfaces': interfaces,
                       'node': str(node) if node else None,
                       'accessible': bool(node and os.access(node, os.R_OK | os.W_OK))})
    return boards


def print_boards(boards: list[dict], *, file=None) -> None:
    file = sys.stdout if file is None else file
    if not boards:
        print('No VCAN/GS_CAN USB board visible. Check cable, firmware mode and VM USB passthrough.', file=file)
    for board in boards:
        print(f"USB {board['path']} mode={board['backend']} node={board['node']} "
              f"read/write={'yes' if board['accessible'] else 'no'}", file=file)
        for item in board['interfaces']:
            print(f"  channel={item['channel']} driver={item['driver'] or 'unbound (available for USB claim)'} "
                  f"netdevs={','.join(item['netdevs']) or '-'}", file=file)
            for netdev in item['netdevs']:
                uuid = _read(NET_ROOT / netdev / 'device_info/uuid')
                if uuid:
                    print(f'    UUID={uuid}; info: /sys/class/net/{netdev}/device_info/', file=file)
        if any(item['driver'] in DRIVERS for item in board['interfaces']):
            print('  To use Python USB, first stop SocketCAN applications, then run:', file=file)
            print(f"  sudo python3 canctl.py release --interface {board['backend']} --usb-path {board['path']}", file=file)


def _linux_only() -> None:
    if not sys.platform.startswith('linux'):
        raise RuntimeError('This command is Linux-only. Windows uses WinUSB; macOS uses native libusb.')


def permission_rules(group_id: int) -> str:
    if group_id < 0:
        raise ValueError('group ID must be non-negative')
    return '# VCANDrive USB access; limited to VCAN and GS_CAN devices.\n' + ''.join(
        f'SUBSYSTEM=="usb", ENV{{DEVTYPE}}=="usb_device", ATTR{{idVendor}}=="1d50", '
        f'ATTR{{idProduct}}=="{pid}", MODE="0660", GROUP="{group_id}"\n'
        for pid in ('6080', '606f'))


def permissions(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description='Preview Linux USB permissions; --apply installs the rules as root.')
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--user', help='target login user; defaults to SUDO_USER or the current user')
    args = parser.parse_args(argv)
    _linux_only()
    import pwd
    username = args.user or os.environ.get('SUDO_USER') or pwd.getpwuid(os.getuid()).pw_name
    account = pwd.getpwnam(username)
    if account.pw_uid == 0:
        raise RuntimeError('Specify the normal login user with --user; do not grant this rule to root.')
    rules = permission_rules(account.pw_gid)
    print(f'Target user: {username}; primary group ID: {account.pw_gid}\n{RULE_PATH}\n{rules}')
    if not args.apply:
        print(f'To install once: sudo python3 canctl.py permissions --apply --user {username}')
        print('Then reconnect the USB board and run doctor without sudo. No world-writable USB permissions are used.')
        return 0
    if os.geteuid() != 0:
        raise RuntimeError('Installing /etc/udev rules requires sudo once. Daily Python commands do not.')
    if RULE_PATH.is_symlink():
        raise RuntimeError(f'Refusing to overwrite a symbolic link: {RULE_PATH}')
    RULE_PATH.write_text(rules, encoding='utf-8')
    RULE_PATH.chmod(0o644)
    subprocess.run(['udevadm', 'control', '--reload-rules'], check=True)
    print('Rules installed. Unplug/reconnect the board, then run python3 canctl.py doctor WITHOUT sudo.')
    print('A kernel CAN driver may bind again after reconnect; release it before using Python USB.')
    return 0


def release(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description='Detach only one selected USB board from its CAN kernel driver. Requires sudo.')
    parser.add_argument('--interface', required=True, choices=('vcan_usb', 'vkgs_usb'))
    parser.add_argument('--usb-path', required=True, help='USB topology name from doctor, e.g. 1-1 or 1-2.3')
    args = parser.parse_args(argv)
    _linux_only()
    if not re.fullmatch(r'\d+-\d+(?:\.\d+)*', args.usb_path):
        raise ValueError('--usb-path must be a topology name printed by doctor')
    board = next((b for b in linux_boards() if b['path'] == args.usb_path and b['backend'] == args.interface), None)
    if board is None:
        raise RuntimeError('No matching board at that USB path; run doctor again.')
    if not board['interfaces']:
        raise RuntimeError('No CAN interfaces found on the selected board; reconnect and run doctor again.')
    # Validate every interface before changing any of them.
    if any(item['driver'] not in DRIVERS | {None} for item in board['interfaces']):
        raise RuntimeError('Unexpected driver on this board; refusing to detach it.')
    if os.geteuid() != 0:
        raise RuntimeError('Detaching a kernel driver requires sudo. This does not grant USB file permissions.')
    for item in board['interfaces']:
        if item['driver'] is None:
            continue
        interface = USB_ROOT / item['path']
        driver = interface / 'driver'
        if not driver.is_symlink() or driver.resolve().name != item['driver']:
            raise RuntimeError('USB binding changed; run doctor again before retrying.')
        for netdev in item['netdevs']:
            subprocess.run(['ip', 'link', 'set', 'dev', netdev, 'down'], check=True)
        (driver / 'unbind').write_text(item['path'], encoding='ascii')
        print(f"Released {item['path']} from {item['driver']}")
    print('Selected board released. Run info/recv/test as your normal user if udev permissions are configured.')
    print('To return to SocketCAN, close Python tools and reconnect the board, or run the kernel test without --no-build.')
    return 0


def error_hint(exc: BaseException, interface: str | None = None) -> str:
    text = str(exc)
    if sys.platform.startswith('linux'):
        if 'LIBUSB_ERROR_ACCESS' in text or 'access denied' in text:
            return ('Linux USB permissions are missing. Run python3 canctl.py permissions, then install the displayed rules once with sudo.\n'
                    'After reconnect, use ordinary-user commands. sudo cannot resolve kernel-driver ownership; check doctor separately.')
        if 'LIBUSB_ERROR_BUSY' in text or 'Resource busy' in text:
            return ('USB interface is busy: a SocketCAN driver or another program owns it.\n'
                    'Run python3 canctl.py doctor; use its release command for the selected board.\n'
                    'If manually unloading a module loaded by the kernel test, use sudo rmmod vkgs_usb (or vcan_usb), not modprobe -r.\n'
                    'Do not unload a shared module while other adapters are in use. For identity only, read /sys/class/net/canX/device_info/.')
    if sys.platform == 'win32' and ('ACCESS' in text or 'WinError 5' in text or 'denied' in text):
        return 'Close the application using this MI_xx channel and verify that this USB child interface uses WinUSB. Administrator mode is not a substitute for WinUSB.'
    if sys.platform == 'darwin' and ('ACCESS' in text or 'denied' in text):
        return 'Close other CAN applications, allow/reconnect the USB accessory if macOS asks, and check the device with doctor. Linux udev/rmmod commands do not apply on macOS.'
    if 'not found' in text or 'No module named' in text:
        return 'Run canctl.py doctor; check the selected backend, setup, USB cable, firmware mode and VM USB passthrough.'
    return ''
