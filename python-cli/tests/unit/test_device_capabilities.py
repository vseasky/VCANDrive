import importlib
import struct
import unittest
from unittest.mock import MagicMock, patch
import can
from vcan_usb import protocol as vcan, vcan_usb_bus
from vkgs_usb import protocol as vkgs, vkgs_usb_bus
from test_protocol_mapping import FakeDevice

NOMINAL = (1, 128, 1, 128, 64, 1, 128, 1)
DATA = (1, 32, 1, 16, 16, 1, 32, 1)


class DeviceCapabilitiesTest(unittest.TestCase):
    def test_wire_capability_responses_for_both_protocols(self):
        for protocol in (vcan, vkgs):
            for fd in (False, True):
                feature = (1 << 8 | 1 << 10) if fd else 0
                device = protocol.CanDevice(FakeDevice())
                raw = struct.pack('<10I', feature, 80_000_000, *NOMINAL)
                extended = struct.pack('<18I', feature, 80_000_000, *NOMINAL, *DATA)
                with patch.object(device, 'get_control', side_effect=[raw, extended]) as read:
                    caps = device.capabilities()
                self.assertEqual(caps['nominal'], NOMINAL)
                self.assertEqual(caps['data'], DATA if fd else None)
                self.assertEqual(caps['fd'], fd)
                self.assertEqual(read.call_count, 2 if fd else 1)

    def test_device_clock_and_fd_feature_are_used_by_bus(self):
        for cls in (vcan_usb_bus, vkgs_usb_bus):
            module = importlib.import_module(cls.__module__)
            device = MagicMock()
            device.capabilities.return_value = dict(clock_hz=48_000_000,
                fd=False, feature=0, nominal=(1, 16, 1, 8, 4, 1, 64, 1), data=None)
            usb = MagicMock(bus=1, address=2, port_path=(1,))
            device._claimed = False
            with patch.object(module._UsbSession, 'acquire', return_value=(MagicMock(), usb)), \
                    patch.object(cls.DEVICE_MODULE, 'CanDevice', return_value=device):
                bus = cls(bitrate=1_000_000, auto_start=False)
                try:
                    brp, t1, t2, sjw = device.bittiming.call_args.args
                    self.assertEqual(48_000_000 // (brp * (1 + t1 + t2)), 1_000_000)
                    self.assertEqual(bus.get_capabilities()['clock_hz'], 48_000_000)
                    with self.assertRaisesRegex(can.CanOperationError, 'FD support'):
                        bus.configure(fd=True)
                finally:
                    bus.shutdown()

    def test_timing_respects_increment_and_rejects_unrepresentable_rates(self):
        for cls in (vcan_usb_bus, vkgs_usb_bus):
            calculate = importlib.import_module(cls.__module__)._calculate_timing
            limits = (1, 16, 1, 8, 4, 2, 8, 2)
            brp, t1, t2, sjw = calculate(80_000_000, 500_000, 75, limits)
            self.assertEqual((brp - 2) % 2, 0)
            self.assertEqual(80_000_000 // (brp * (1 + t1 + t2)), 500_000)
            with self.assertRaises(can.CanInitializationError):
                calculate(80_000_000, 5_000_000, 75, (1, 2, 1, 2, 1, 128, 128, 1))

    def test_host_timing_diagnostics_do_not_claim_hardware_readback(self):
        for cls in (vcan_usb_bus, vkgs_usb_bus):
            calculate = importlib.import_module(cls.__module__)._calculate_timing
            bus = object.__new__(cls)
            bus._is_shutdown = True
            bus._capabilities = {"clock_hz": 80_000_000}
            bus._nominal_timing = calculate(80_000_000, 1_000_000, 75, NOMINAL)
            bus._data_timing = calculate(80_000_000, 5_000_000, 75, DATA)
            timing = bus.get_bit_timing()
            self.assertEqual(timing['nominal']['brp'], 5)
            self.assertEqual(timing['data']['brp'], 2)
            self.assertEqual(timing['data']['bitrate'], 5_000_000)
            self.assertEqual(timing['data']['sample_point'], 75)
            timing['data']['brp'] = 99
            self.assertEqual(bus.get_bit_timing()['data']['brp'], 2)
