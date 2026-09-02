from argparse import Namespace
import unittest
from unittest.mock import patch

from tests._helpers import (
    _arbitration_id,
    _open_buses,
    _shutdown_buses,
)


class FakeBus:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.shutdown_calls = 0

    def shutdown(self):
        self.shutdown_calls += 1


class TestHelpersTest(unittest.TestCase):
    def args(self):
        return Namespace(
            channel_numbers=[0, 1, 2], channels=3,
            no_termination=False, interface="vkgs_usb", device=0,
            usb_bus=None, usb_address=None, usb_port_path=None,
            bitrate=1_000_000, sample_point=75.0,
            data_bitrate=5_000_000, data_sample_point=75.0,
            timeout_ms=2_000,
        )

    def test_ids_match_socketcan_script(self):
        self.assertEqual(_arbitration_id(0), 0x100)
        self.assertEqual(_arbitration_id(1), 0x101)
        self.assertEqual(_arbitration_id(2), 0x102)

    @patch("tests._helpers.can.Bus", side_effect=FakeBus)
    def test_shared_bus_terminates_only_physical_ends(self, bus_factory):
        buses = _open_buses(self.args(), fd=False, loopback=False)

        self.assertEqual(
            [bus.kwargs["termination"] for bus in buses],
            [True, False, True],
        )

    @patch("tests._helpers.can.Bus", side_effect=FakeBus)
    def test_loopback_terminates_every_isolated_channel(self, bus_factory):
        buses = _open_buses(self.args(), fd=False, loopback=True)

        self.assertEqual(
            [bus.kwargs["termination"] for bus in buses],
            [True, True, True],
        )

    def test_shutdown_releases_in_reverse_order_and_clears_list(self):
        order = []

        class OrderedBus:
            def __init__(self, channel):
                self.channel = channel

            def shutdown(self):
                order.append(self.channel)

        buses = [OrderedBus(0), OrderedBus(1), OrderedBus(2)]
        _shutdown_buses(buses)

        self.assertEqual(order, [2, 1, 0])
        self.assertEqual(buses, [])

if __name__ == "__main__":
    unittest.main()
