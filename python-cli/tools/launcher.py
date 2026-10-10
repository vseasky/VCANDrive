"""Dependency-free cross-platform environment and CLI launcher."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import platform
import subprocess
import sys
import venv

ROOT = Path(__file__).resolve().parents[1]
HELP = """VCANDrive — the same commands on Windows, Linux and macOS

Windows:       py canctl.py <arguments>
Linux/macOS:   python3 canctl.py <arguments>

  setup [--interface vkgs_usb|vcan_usb|all]   install/update the user environment
  doctor                                   inspect environment and USB ownership
  run script.py [arguments]                run your Python program in this environment
  permissions [--apply]                    Linux: preview/install USB udev rules
  release --interface NAME --usb-path PATH  Linux: detach one board from SocketCAN
  test --interface NAME [--frames 2000]     complete wired two-channel USB test
  --interface NAME list                    discover USB channels (not a claim test)
  --interface NAME --channel 0 info        read board UID/UUID
  --interface NAME --channel 1 recv ...    receive; another node must send
  --interface NAME --channel 0 send ...    send CAN frames

NAME is vkgs_usb (GS_CAN) or vcan_usb (VCAN).
Use 'test --help', 'setup --help', or '--interface vkgs_usb --help' for details.
Normal commands do not install software, detach kernel drivers or change permissions.
See ../udocs/01_快速入门.md and ../udocs/02_常见问题.md.
"""


def user_home() -> Path:
    if sys.platform != 'win32' and os.geteuid() == 0 and os.environ.get('SUDO_USER'):
        import pwd
        return Path(pwd.getpwnam(os.environ['SUDO_USER']).pw_dir)
    return Path.home()


def default_venv() -> Path:
    if sys.platform == 'win32':
        return Path(os.environ.get('LOCALAPPDATA', user_home() / 'AppData/Local')) / 'VCANDrive/venv'
    if sys.platform == 'darwin':
        return user_home() / 'Library/Application Support/VCANDrive/venv'
    return user_home() / '.local/share/vcandrive/venv-linux'


def venv_python(directory: Path) -> Path:
    return directory / ('Scripts/python.exe' if sys.platform == 'win32' else 'bin/python')


def selected_python() -> Path:
    override = os.environ.get('VCANDRIVE_PYTHON')
    if override:
        return Path(override).expanduser()
    # Keep existing installations usable; never build a venv on a shared drive.
    suffix = 'win' if sys.platform == 'win32' else ('mac' if sys.platform == 'darwin' else 'linux')
    project_python = venv_python(ROOT / ('.venv-' + suffix))
    if project_python.is_file() and os.access(project_python, os.X_OK):
        return project_python
    return venv_python(default_venv())


def command_prefix() -> str:
    return 'py canctl.py' if sys.platform == 'win32' else 'python3 canctl.py'


def setup(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description='Create/update the user-local Python USB environment; run without sudo.')
    parser.add_argument('--interface', choices=('vkgs_usb', 'vcan_usb', 'all'), default='all')
    args = parser.parse_args(argv)
    if sys.platform != 'win32' and os.geteuid() == 0:
        raise RuntimeError('Run setup as your normal user, without sudo.')
    target = default_venv()
    interpreter = venv_python(target)
    print(f'Environment: {target}', flush=True)
    if not interpreter.is_file():
        venv.EnvBuilder(with_pip=True).create(target)
    packages = ('vcan_usb', 'vkgs_usb') if args.interface == 'all' else (args.interface,)
    command = [str(interpreter), '-m', 'pip', 'install']
    for package in packages:
        command += ['-e', str(ROOT / package)]
    subprocess.run(command, check=True)
    print('Setup complete. Command and test launchers share the same interpreter selection.')
    if selected_python() != interpreter:
        print(f'Note: your explicit/project interpreter takes priority: {selected_python()}')
        print(f'To use this installation, set VCANDRIVE_PYTHON to {interpreter}')
    print(f'Next: {command_prefix()} doctor')
    return 0


def doctor(argv: list[str]) -> int:
    argparse.ArgumentParser(description='Read-only environment/USB checks; does not claim channels.').parse_args(argv)
    print(f'Platform: {platform.system()} {platform.release()}')
    interpreter = selected_python()
    print(f'Python environment: {interpreter}', flush=True)
    ok = interpreter.is_file() and os.access(interpreter, os.X_OK)
    if ok:
        # Check packages and native libusb using the selected interpreter.
        code = '''import importlib.util
installed = {}
for name in ('can', 'usb', 'usb1', 'vcan_usb', 'vkgs_usb'):
 installed[name] = importlib.util.find_spec(name) is not None
 print(name + ': ' + ('installed' if installed[name] else 'missing'))
if not all(installed[n] for n in ('can', 'usb', 'usb1')) or not any(installed[n] for n in ('vcan_usb', 'vkgs_usb')):
 print('Required packages missing. Run canctl.py setup.')
 raise SystemExit(1)
try:
 import usb1
 with usb1.USBContext() as ctx:
  devices = ctx.getDeviceList(skip_on_error=True)
  found = [d for d in devices if d.getVendorID()==0x1d50 and d.getProductID() in (0x606f, 0x6080)]
  print('libusb runtime: available; matching USB boards:', len(found))
except Exception as exc:
 print('libusb runtime/discovery failed:', exc)
 raise SystemExit(1)
'''
        ok = subprocess.run([str(interpreter), '-c', code]).returncode == 0
    else:
        print(f'Environment missing. Run: {command_prefix()} setup')
    if sys.platform.startswith('linux'):
        from .host import linux_boards, print_boards
        print_boards(linux_boards())
        print('For non-root USB access: python3 canctl.py permissions')
    elif sys.platform == 'win32':
        print('Each CAN MI_00/MI_01 interface needs WinUSB. Close apps using the selected channel.')
    elif sys.platform == 'darwin':
        print('Native libusb is required (brew install libusb). Reconnect/allow the USB accessory if macOS requests it.')
    print('Discovery does not prove USB permissions, successful interface claim, CAN wiring or bus ACK.')
    return 0 if ok else 2


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ('-h', '--help'):
        print(HELP)
        return 0
    try:
        if argv[0] == 'setup':
            return setup(argv[1:])
        if argv[0] == 'doctor':
            return doctor(argv[1:])
        if argv[0] in ('permissions', 'release'):
            from .host import permissions, release
            return {'permissions': permissions, 'release': release}[argv[0]](argv[1:])
        interpreter = selected_python()
        if not interpreter.is_file() or not os.access(interpreter, os.X_OK):
            raise RuntimeError(f'Python environment not found: {interpreter}\nRun without sudo: {command_prefix()} setup')
        if argv[0] == 'run':
            if len(argv) < 2:
                raise RuntimeError('Usage: canctl.py run your_script.py [arguments]')
            return subprocess.run([str(interpreter), *argv[1:]]).returncode
        target = ROOT / ('tests/hardware_test.py' if argv[0] == 'test' else 'tools/canctl.py')
        arguments = argv[1:] if argv[0] == 'test' else argv
        return subprocess.run([str(interpreter), str(target), *arguments]).returncode
    except KeyboardInterrupt:
        return 130
    except (OSError, RuntimeError, ValueError, KeyError, subprocess.CalledProcessError) as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        return 2
