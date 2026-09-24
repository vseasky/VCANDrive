"""Offline reproduction using real encoders/decoders; only USB is mocked."""
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import can
from vcan_usb import protocol as vcan, vcan_usb_bus
from vkgs_usb import protocol as vkgs, vkgs_usb_bus
from test_protocol_mapping import FakeDevice


class SocketCanParityTest(unittest.TestCase):
    def test_vkgs_fd_matrix_and_every_transfer_split(self):
        # Same 128 combinations as the kernel RX fixture, at all 75 splits.
        for extended in (False, True):
            for esi in (False, True):
                for brs in (False, True):
                    for dlc, length in enumerate(vkgs.FD_DLC_LENGTHS):
                        device = vkgs.CanDevice(FakeDevice(), channel=1)
                        raw_id = 0x18C15959 if extended else 0x415
                        flags = vkgs.FLAG_FD | (vkgs.FLAG_ESI if esi else 0)
                        flags |= vkgs.FLAG_BRS if brs else 0
                        packet = vkgs.HOST_HEADER.pack(vkgs.ECHO_RX,
                            raw_id | (vkgs.CAN_EFF if extended else 0)
                            | (vkgs.CAN_ERR if esi else 0), dlc, 1, flags, 0)
                        packet += bytes(range(64))
                        for split in range(1, len(packet)):
                            with self.subTest(extended=extended, esi=esi,
                                              brs=brs, dlc=dlc, split=split):
                                self.assertEqual(list(device.parse_bulk(packet[:split])), [])
                                frame, = device.parse_bulk(packet[split:])
                                self.assertEqual(frame.can_id, raw_id)
                                self.assertEqual(frame.data, bytes(range(length)))
                                self.assertEqual((frame.extended, frame.esi, frame.brs),
                                                 (extended, esi, brs))
                                self.assertFalse(frame.error)

    def test_classic_error_flag_is_preserved(self):
        device = vkgs.CanDevice(FakeDevice(), channel=1)
        packet = vkgs.HOST_HEADER.pack(
            vkgs.ECHO_RX, vkgs.CAN_ERR | 0x40, 8, 1, 0, 0) + bytes(8)
        frame, = device.parse_bulk(packet)
        self.assertTrue(frame.error)

    def test_fd_remote_frames_are_rejected(self):
        packets = (
            (vcan, vcan.HEADER.pack(vcan.ECHO_RX, vcan.opcode(1, 88),
                                   vcan.FLAG_FD | vcan.FLAG_RTR)
             + vcan.CAN_FRAME_FIELDS.pack(0x123, 0, 0) + bytes(64)),
            (vkgs, vkgs.HOST_HEADER.pack(vkgs.ECHO_RX, vkgs.CAN_RTR | 0x123,
                                        0, 1, vkgs.FLAG_FD, 0) + bytes(64)),
        )
        for protocol, packet in packets:
            device = protocol.CanDevice(FakeDevice(), channel=1)
            with self.assertRaises(protocol.ProtocolError):
                list(device.parse_bulk(packet))
            with self.assertRaises(ValueError):
                device.send(protocol.CanFrame(0x123, b'', fd=True, rtr=True))

    def test_remote_dlc_survives_bus_send_to_wire(self):
        for bus_class in (vcan_usb_bus, vkgs_usb_bus):
            protocol = bus_class.DEVICE_MODULE
            transport = FakeDevice()
            device = protocol.CanDevice(transport, channel=1)
            bus = SimpleNamespace(_is_shutdown=False, _started=True,
                _usb_session=SimpleNamespace(context=SimpleNamespace(error=None)),
                _can_protocol=can.CanProtocol.CAN_20, DEVICE_MODULE=protocol,
                channel=1, _io_lock=threading.Lock(), _protocol_device=device)
            for dlc in range(9):
                with self.subTest(protocol=protocol.__name__, dlc=dlc), \
                        patch('time.sleep'):
                    bus_class.send(bus, can.Message(arbitration_id=0x123,
                        is_extended_id=False, is_remote_frame=True, dlc=dlc), timeout=1)
                    packet = transport.writes[-1][1]
                    self.assertEqual(packet[12 if protocol is vcan else 8], dlc)
                    self.assertEqual(packet[24 if protocol is vcan else 12:], bytes(8))

    def test_bus_off_is_error_not_passive(self):
        for bus_class in (vcan_usb_bus, vkgs_usb_bus):
            for firmware_state, expected in ((0, can.BusState.ACTIVE),
                                             (1, can.BusState.ACTIVE),
                                             (2, can.BusState.PASSIVE),
                                             (3, can.BusState.ERROR)):
                bus = SimpleNamespace(DEVICE_MODULE=bus_class.DEVICE_MODULE,
                    _protocol_device=SimpleNamespace(can_state=firmware_state))
                self.assertEqual(bus_class.state.fget(bus), expected)

    def test_zero_sentinel_discards_stale_padding(self):
        for protocol in (vcan, vkgs):
            device = protocol.CanDevice(FakeDevice(), channel=1)
            if protocol is vcan:
                packet = vcan.HEADER.pack(vcan.ECHO_RX, vcan.opcode(1, 32), 0)
                packet += vcan.CAN_FRAME_FIELDS.pack(0x123, 8, 0) + bytes(8)
            else:
                packet = vkgs.HOST_HEADER.pack(vkgs.ECHO_RX, 0x123, 8, 1, 0, 0) + bytes(8)
            self.assertEqual(len(list(device.parse_bulk(packet + bytes(4) + packet))), 1)
            self.assertEqual(len(list(device.parse_bulk(packet))), 1)
