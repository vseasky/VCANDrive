from argparse import Namespace
import unittest
from unittest.mock import patch

import can

from tests.hardware_test import _prepare_round


class FakeLoopbackBus:
    def __init__(self, data_path_ready: bool):
        self.channel = 0
        self.data_path_ready = data_path_ready
        self.starts = 0
        self.stops = 0
        self.configurations = []
        self.sent = None

    def stop(self):
        self.stops += 1

    def configure(self, *, fd):
        self.configurations.append(fd)

    def get_usb_stats(self):
        return {"rx_completed": self.starts}

    def set_bus_load_reporting(self, enabled):
        self.bus_load_reporting = enabled

    def start(self):
        self.starts += 1

    def wait_for_usb_rx(self, previous, timeout):
        return True

    def send(self, message):
        self.sent = message

    def recv(self, timeout):
        if not self.data_path_ready:
            return None
        return can.Message(
            arbitration_id=self.sent.arbitration_id,
            is_extended_id=False,
            is_fd=self.sent.is_fd,
            bitrate_switch=self.sent.bitrate_switch,
            data=self.sent.data,
        )


class StartupProbeTest(unittest.TestCase):
    def args(self):
        return Namespace(
            channel_numbers=[0],
        )

    def test_accepts_complete_loopback_data_path(self):
        bus = FakeLoopbackBus(data_path_ready=True)

        _prepare_round(
            self.args(), [bus], fd=False, brs=False, loopback=True)

        self.assertEqual(bus.starts, 1)
        self.assertEqual(bus.configurations, [False])

    def test_accepts_fd_with_brs_off_and_on(self):
        for brs in (False, True):
            with self.subTest(brs=brs):
                bus = FakeLoopbackBus(data_path_ready=True)

                _prepare_round(
                    self.args(), [bus], fd=True, brs=brs, loopback=True)

                self.assertTrue(bus.sent.is_fd)
                self.assertEqual(bus.sent.bitrate_switch, brs)
                self.assertEqual(bus.configurations, [True])

    @patch("tests.hardware_test.STARTUP_TIMEOUT", 0.001)
    def test_does_not_retry_an_unarmed_usb_out_endpoint(self):
        bus = FakeLoopbackBus(data_path_ready=False)

        with self.assertRaises(can.CanInitializationError):
            _prepare_round(
                self.args(), [bus], fd=False, brs=False, loopback=True)

        self.assertEqual(bus.starts, 1)


if __name__ == "__main__":
    unittest.main()
