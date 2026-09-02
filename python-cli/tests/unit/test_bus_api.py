import unittest
from unittest.mock import MagicMock, patch

import can

from vcan_usb import vcan_usb_bus
from vkgs_usb import vkgs_usb_bus


BUS_CLASSES = (vcan_usb_bus, vkgs_usb_bus)


class BusApiTest(unittest.TestCase):
    def test_public_bus_names_and_interfaces(self):
        self.assertEqual(vcan_usb_bus.__name__, "vcan_usb_bus")
        self.assertEqual(vkgs_usb_bus.__name__, "vkgs_usb_bus")
        self.assertEqual(vcan_usb_bus.INTERFACE_NAME, "vcan_usb")
        self.assertEqual(vkgs_usb_bus.INTERFACE_NAME, "vkgs_usb")

    def test_invalid_parameters_fail_before_usb_access(self):
        cases = (
            {"channel": -1},
            {"bitrate": 0},
            {"sample_point": 0.0},
            {"sample_point": 100.0},
            {"data_bitrate": 0},
            {"data_sample_point": 100.0},
            {"timeout_ms": 0},
            {"port_path": "bad.path"},
        )
        for bus_class in BUS_CLASSES:
            for kwargs in cases:
                with self.subTest(bus_class=bus_class, kwargs=kwargs):
                    with self.assertRaises(can.CanInitializationError):
                        bus_class(**kwargs)

    def test_usb_mode_uses_control_only_session(self):
        for bus_class in BUS_CLASSES:
            module = __import__(bus_class.__module__, fromlist=["_UsbSession"])
            session = MagicMock()
            usb_device = MagicMock()
            protocol_device = MagicMock()
            with self.subTest(bus_class=bus_class), \
                    patch.object(module._UsbSession, "acquire",
                                 return_value=(session, usb_device)) as acquire, \
                    patch.object(bus_class.DEVICE_MODULE, "CanDevice",
                                 return_value=protocol_device) as constructor:
                bus_class.switch_usb_mode("gs_usb", channel=1)
                acquire.assert_called_once_with(
                    bus_class.DEVICE_MODULE.VID,
                    bus_class.DEVICE_MODULE.PID,
                    channel=1,
                    index=0,
                    bus=None,
                    address=None,
                    port_path=None,
                )
                constructor.assert_called_once_with(
                    device=usb_device,
                    channel=1,
                    timeout_ms=2_000,
                    control_device=session.control_device,
                )
                protocol_device.usb_mode.assert_called_once_with(
                    bus_class.DEVICE_MODULE.USB_MODE_GS_USB)
                usb_device.start_rx.assert_not_called()
                session.release.assert_called_once_with(usb_device)

    def test_windows_acquire_opens_independent_interface_handles(self):
        class FakeContext:
            instances = []

            def __init__(self, **kwargs):
                self.raw = object()
                self.close_count = 0
                self.instances.append(self)

            def find(self, *args, **kwargs):
                return self.raw

            def close(self):
                self.close_count += 1

        class FakeNativeDevice:
            def __init__(self, channel):
                self.channel = channel
                self.error = None
                self.close_count = 0

            def close(self):
                self.close_count += 1

        descriptors = [
            type("Interface", (), {"number": 0})(),
            type("Interface", (), {"number": 1})(),
        ]

        for bus_class in BUS_CLASSES:
            module = __import__(bus_class.__module__, fromlist=["_UsbSession"])
            FakeContext.instances = []
            opened_devices = []

            def open_interface(vid, pid, interface_info, interface_count):
                self.assertEqual((vid, pid), (0x1D50, 0x6080))
                self.assertEqual(interface_count, 2)
                device = FakeNativeDevice(interface_info.number)
                opened_devices.append(device)
                return device

            with self.subTest(bus_class=bus_class), \
                    patch.object(module.sys, "platform", "win32"), \
                    patch.object(module.usb_transport, "Context", FakeContext), \
                    patch.object(module.usb_transport, "interface_info",
                                 return_value=descriptors), \
                    patch.object(module, "_open_windows_interface",
                                 side_effect=open_interface):
                session0, device0 = module._UsbSession.acquire(
                    0x1D50, 0x6080, channel=0)
                session1, device1 = module._UsbSession.acquire(
                    0x1D50, 0x6080, channel=1)

                self.assertIsNot(device0, device1)
                self.assertEqual([item.channel for item in opened_devices], [0, 1])
                self.assertTrue(all(
                    context.close_count == 1
                    for context in FakeContext.instances
                ))

                session1.release(device1)
                self.assertEqual((device0.close_count, device1.close_count), (0, 1))
                session0.release(device0)
                self.assertEqual((device0.close_count, device1.close_count), (1, 1))


if __name__ == "__main__":
    unittest.main()
