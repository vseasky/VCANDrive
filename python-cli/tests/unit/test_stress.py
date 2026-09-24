from argparse import Namespace
from collections import deque
from contextlib import redirect_stdout
import io
import unittest
from unittest.mock import patch
import can
from vcan_usb import vcan_usb_bus
from vkgs_usb import vkgs_usb_bus
from tests.hardware_test import Integrity, _message, _payload, _phase


class FakeBus:
    state = can.BusState.ACTIVE
    channel = 0

    def get_bit_timing(self):
        return {"nominal": {}, "data": {}}

    def get_capabilities(self):
        return {"clock_hz": 80_000_000, "fd": True}

    def __init__(self):
        self.queue = deque()
        self.peer = self
        self.tx = 0
        self.drop = False
        self.duplicate = False
        self.overflow = False

    def recv(self, timeout):
        return self.queue.popleft() if self.queue else None

    def send(self, msg, timeout=None):
        self.tx += 1
        if not self.drop:
            self.peer.queue.append(msg)
            if self.duplicate:
                self.peer.queue.append(msg)

    def stop(self):
        pass

    def get_usb_stats(self):
        return {'tx_completed': self.tx, 'app_rx_dropped': int(self.overflow and self.tx > 0)}

    def get_berr_counter(self):
        return {'rxerr': 0, 'txerr': 0}


class StressV2Test(unittest.TestCase):
    def test_sequences_do_not_repeat_after_256(self):
        self.assertNotEqual(_payload(0, 0, 8), _payload(0, 256, 8))
        for fd in (False, True):
            check = Integrity([0], [0], True)
            for sequence in range(1025):
                check.accept(0, _message(0, sequence, fd, fd), fd, fd, {0: 1025})
            self.assertEqual(check.counts[(0, 0)], 1025)
            self.assertEqual(check.invalid, 0)

    def test_rejects_duplicate_reorder_corruption_flags_and_unsent(self):
        for defect in ('duplicate', 'reorder', 'payload', 'extended', 'brs', 'error', 'unsent'):
            with self.subTest(defect=defect):
                check = Integrity([0], [0], True)
                msg = _message(0, 0, True, True)
                sent = {0: 2}
                if defect == 'duplicate':
                    check.accept(0, msg, True, True, sent)
                if defect == 'reorder':
                    msg = _message(0, 1, True, True)
                if defect == 'payload':
                    msg.data[-1] ^= 1
                if defect == 'extended':
                    msg.is_extended_id = True
                if defect == 'brs':
                    msg.bitrate_switch = False
                if defect == 'error':
                    msg.is_error_frame = True
                if defect == 'unsent':
                    sent = {0: 0}
                check.accept(0, msg, True, True, sent)
                self.assertEqual(check.invalid, 1)

    def test_first_failure_preserves_exact_frame_and_expected_sequence(self):
        check = Integrity([0, 1], [0, 1], False)
        good = _message(1, 0, True, False)
        check.accept(0, good, True, False, {0: 3, 1: 3})
        bad = _message(1, 2, True, False)
        check.accept(0, bad, True, False, {0: 3, 1: 3})
        failure = check.first_failure
        self.assertEqual(failure['expected_sequence'], 1)
        self.assertEqual(failure['actual_sequence'], 2)
        self.assertEqual(failure['sender'], 1)
        self.assertEqual(failure['receiver'], 0)
        self.assertEqual(failure['actual']['data'], bytes(bad.data).hex())
        self.assertIn('extended', failure['reasons'])
        self.assertIn('sequence', failure['reasons'])
        self.assertTrue(failure['matches_own_sequence'])
        check.accept(0, good, True, False, {0: 3, 1: 3})
        self.assertIs(check.first_failure, failure)
        self.assertEqual(check.counts[(0, 1)], 1)
        self.assertEqual(check.invalid, 2)
        for _ in range(20):
            check.accept(0, bad, True, False, {0: 3, 1: 3})
        self.assertEqual(len(check.failure_samples), 8)
        self.assertIs(check.first_failure, failure)

    def test_unknown_id_and_short_payload_diagnostics(self):
        for unknown in (False, True):
            check = Integrity([0], [0], True)
            msg = _message(0, 0, True, False)
            if unknown:
                msg.arbitration_id = 0x777
            msg.data = bytearray(b"x")
            check.accept(0, msg, True, False, {0: 1})
            self.assertEqual(check.invalid, 1)
            self.assertIsNone(check.first_failure['actual_sequence'])
            self.assertIn('payload shorter than sequence/source header',
                          check.first_failure['reasons'])

    @patch('tests.hardware_test._prepare_round')
    def test_phase_window_success_and_failure(self, prepare):
        for failure in ('none', 'drop', 'duplicate', 'overflow'):
            for loopback in (False, True):
                with self.subTest(failure=failure, loopback=loopback):
                    buses = [FakeBus(), FakeBus()]
                    if not loopback:
                        buses[0].peer, buses[1].peer = buses[1], buses[0]
                    if failure != 'none':
                        setattr(buses[0], failure, True)
                    args = Namespace(channel_numbers=[0, 1], sender_indices=[0, 1],
                        frames=300, window=32, timeout_ms=100,
                        receive_timeout=0.002, drain_time=0.002)
                    with redirect_stdout(io.StringIO()):
                        passed = _phase(args, buses, True, True, loopback, 'fixture')
                    self.assertEqual(passed, failure == 'none')
                    self.assertLessEqual(buses[0].tx, args.frames)

    def test_rounds_reopen_and_restore_selected_senders(self):
        for bus_class in (vcan_usb_bus, vkgs_usb_bus):
            from tests.hardware_test import main
            bus = FakeBus()
            bus.get_device_info = lambda: {}
            opened = []
            phases = []

            def open_buses(args, fd, loopback):
                opened.append((loopback, args.sender_indices[:]))
                return [bus, bus]

            def shutdown(buses):
                buses.clear()

            def phase(args, buses, fd, brs, loopback, label):
                phases.append((loopback, args.sender_indices[:]))
                return True

            with patch('sys.argv', ['test', '--interface', bus_class.INTERFACE_NAME, '--rounds', '2', '--senders', '0',
                                   '--skip-fd', '--no-termination']), \
                    patch.object(bus_class, 'discover_channels', return_value=[0, 1]), \
                    patch('tests.hardware_test._open_buses', side_effect=open_buses), \
                    patch('tests.hardware_test._shutdown_buses', side_effect=shutdown), \
                    patch('tests.hardware_test._phase', side_effect=phase), \
                    redirect_stdout(io.StringIO()):
                self.assertEqual(main(), 0)
            self.assertEqual(len(opened), 4)
            self.assertEqual(phases, [(False, [0]), (True, [0, 1])] * 2)

    def test_failed_phase_stops_suite_and_releases_handles(self):
        for bus_class in (vcan_usb_bus, vkgs_usb_bus):
            from tests.hardware_test import main
            bus = FakeBus()
            bus.get_device_info = lambda: {}
            closed = []

            def shutdown(buses):
                closed.extend(buses)
                buses.clear()

            with patch('sys.argv', ['test', '--interface', bus_class.INTERFACE_NAME, '--rounds', '5', '--no-termination']), \
                    patch.object(bus_class, 'discover_channels', return_value=[0, 1]), \
                    patch('tests.hardware_test._open_buses', return_value=[bus, bus]) as opened, \
                    patch('tests.hardware_test._shutdown_buses', side_effect=shutdown), \
                    patch('tests.hardware_test._phase', return_value=False) as phase, \
                    redirect_stdout(io.StringIO()):
                self.assertEqual(main(), 1)
            self.assertEqual(opened.call_count, 1)
            self.assertEqual(phase.call_count, 1)
            self.assertEqual(len(closed), 2)

    def test_shutdown_error_does_not_leak_other_handles(self):
        from tests._helpers import _shutdown_buses
        from unittest.mock import Mock
        first, second = Mock(), Mock()
        second.shutdown.side_effect = RuntimeError('unplugged')
        buses = [first, second]
        with self.assertRaisesRegex(RuntimeError, 'unplugged'):
            _shutdown_buses(buses)
        first.shutdown.assert_called_once()
        second.shutdown.assert_called_once()
        self.assertEqual(buses, [])
