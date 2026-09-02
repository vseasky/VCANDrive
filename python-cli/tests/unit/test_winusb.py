import sys
import unittest
from unittest.mock import patch


@unittest.skipUnless(sys.platform == "win32", "native WinUSB is Windows-only")
class WinUsbDiscoveryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from vcan_usb import winusb as vcan_winusb
        from vkgs_usb import winusb as vkgs_winusb

        cls.modules = (vcan_winusb, vkgs_winusb)

    def test_location_path_matches_libusb_port_path(self):
        location = (
            "PCIROOT(0)#PCI(1400)#USBROOT(0)#USB(2)#USB(1)#USB(4)#USBMI(1)"
        )
        for module in self.modules:
            with self.subTest(module=module.__name__):
                self.assertEqual(module._usb_port_path(location), (2, 1, 4))
                self.assertEqual(
                    module._root_location_path(location),
                    "PCIROOT(0)#PCI(1400)#USBROOT(0)",
                )

    def test_interface_selection_uses_bus_channel_and_physical_port(self):
        for module in self.modules:
            paths = [
                module.DeviceInterfacePath(
                    r"\\?\usb#vid_1d50&pid_606f&mi_00#first#{custom}",
                    (2, 1, 4),
                    bus=2,
                ),
                module.DeviceInterfacePath(
                    r"\\?\usb#vid_1d50&pid_606f&mi_01#other#"
                    r"{dee824ef-729b-4a0e-9c14-b7117d33a817}",
                    (2, 1, 4),
                    bus=3,
                ),
                module.DeviceInterfacePath(
                    r"\\?\usb#vid_1d50&pid_606f&mi_01#first#{custom}",
                    (2, 1, 4),
                    bus=2,
                ),
                module.DeviceInterfacePath(
                    r"\\?\usb#vid_1d50&pid_606f&mi_01#first#"
                    r"{dee824ef-729b-4a0e-9c14-b7117d33a817}",
                    (2, 1, 4),
                    bus=2,
                ),
            ]
            with self.subTest(module=module.__name__), patch.object(
                module, "enumerate_interface_paths", return_value=paths
            ):
                selected = module.find_interface_path(
                    0x1D50, 0x606F, 1, (2, 1, 4), bus=2
                )
                self.assertIn("dee824ef", selected.path)
                self.assertEqual(selected.bus, 2)
                self.assertEqual(selected.port_path, (2, 1, 4))

    def test_missing_interface_has_actionable_error(self):
        for module in self.modules:
            with self.subTest(module=module.__name__), patch.object(
                module, "enumerate_interface_paths", return_value=[]
            ):
                with self.assertRaisesRegex(RuntimeError, "MI_xx.*WinUSB"):
                    module.find_interface_path(0x1D50, 0x606F, 1, (2, 1, 4))


if __name__ == "__main__":
    unittest.main()
