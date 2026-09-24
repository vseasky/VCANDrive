"""Check direct-file imports in a fresh interpreter, outside the project cwd."""
from pathlib import Path
import os
import subprocess
import sys
import tempfile
import unittest


class HardwareEntrypointTest(unittest.TestCase):
    def test_direct_script_does_not_import_wrapper_namespace_packages(self):
        script = Path(__file__).resolve().parents[1] / 'hardware_test.py'
        code = '''
import runpy
import sys
from pathlib import Path
script = Path(sys.argv[1])
# Reproduce python /absolute/path/tests/hardware_test.py startup.
sys.path[0] = str(script.parent)
runpy.run_path(str(script), run_name='entrypoint_import_test')
from vcan_usb import vcan_usb_bus
from vkgs_usb import vkgs_usb_bus
assert vcan_usb_bus.INTERFACE_NAME == 'vcan_usb'
assert vkgs_usb_bus.INTERFACE_NAME == 'vkgs_usb'
'''
        with tempfile.TemporaryDirectory() as cwd:
            result = subprocess.run([sys.executable, '-c', code, str(script)],
                cwd=cwd, env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1',
                              'PYTHONPATH': ''},
                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
