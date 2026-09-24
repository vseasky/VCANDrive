"""Both independently packaged protocols must obey the same send contract."""
import unittest
from unittest.mock import patch
import usb.core
from vcan_usb import protocol as vcan
from vkgs_usb import protocol as vkgs
from test_protocol_mapping import FakeDevice


class TxPacingTest(unittest.TestCase):
    def test_send_has_no_sleep_and_preserves_packet_and_timeout(self):
        for protocol in (vcan, vkgs):
            with self.subTest(protocol=protocol.__name__):
                transport = FakeDevice()
                device = protocol.CanDevice(transport, channel=1)
                with patch('time.sleep') as sleep:
                    device.send(protocol.CanFrame(0x123, b'12345678'), timeout_ms=47)
                    sleep.assert_not_called()
                if protocol is vcan:
                    packet = (vcan.HEADER.pack(vcan.ECHO_TX, vcan.opcode(1, 32), 0)
                              + vcan.CAN_FRAME_FIELDS.pack(0x123, 8, 0))
                else:
                    packet = vkgs.HOST_HEADER.pack(0, 0x123, 8, 1, 0, 0)
                self.assertEqual(transport.writes, [(2, packet + b'12345678', 47)])

    def test_usb_timeout_is_not_retried(self):
        for protocol in (vcan, vkgs):
            with self.subTest(protocol=protocol.__name__):
                device = protocol.CanDevice(FakeDevice())
                with patch.object(device.dev, 'write', side_effect=usb.core.USBTimeoutError('timeout')) as write:
                    with self.assertRaises(protocol.ProtocolError):
                        device.send(protocol.CanFrame(0x123, b'12345678'))
                    self.assertEqual(write.call_count, 1)

    def test_short_write_is_not_retried(self):
        for protocol in (vcan, vkgs):
            with self.subTest(protocol=protocol.__name__):
                device = protocol.CanDevice(FakeDevice())
                with patch.object(device.dev, 'write', return_value=1) as write:
                    with self.assertRaises(protocol.ProtocolError):
                        device.send(protocol.CanFrame(0x123, b'12345678'))
                    self.assertEqual(write.call_count, 1)
