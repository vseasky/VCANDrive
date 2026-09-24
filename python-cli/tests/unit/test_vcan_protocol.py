import unittest

from vcan_usb import protocol as vcan_device


class Endpoint:
    def __init__(self, address):
        self.bEndpointAddress = address
        self.wMaxPacketSize = 512


class FakeAsyncDevice:
    bus = 1
    address = 2

    def __init__(self):
        self.ep_in = Endpoint(0x82)
        self.ep_out = Endpoint(0x02)
        self.writes = []

    def start_rx(self):
        pass

    def write(self, endpoint, packet, timeout):
        self.writes.append((endpoint, packet, timeout))
        return len(packet)


class VCanProtocolTest(unittest.TestCase):
    def test_async_device_does_not_reconfigure_or_reclaim_interface(self):
        transport = FakeAsyncDevice()
        device = vcan_device.CanDevice(device=transport, channel=1)

        self.assertIs(device.ep_in, transport.ep_in)
        self.assertIs(device.ep_out, transport.ep_out)
        self.assertFalse(device._claimed)

    def test_fd_send_uses_async_device_and_fixed_64_byte_payload_area(self):
        transport = FakeAsyncDevice()
        device = vcan_device.CanDevice(device=transport, channel=1)
        frame = vcan_device.CanFrame(can_id=0x123, data=b"123456789",
                                     channel=1, fd=True)

        device.send(frame)

        endpoint, packet, timeout = transport.writes[0]
        self.assertEqual(endpoint, 0x02)
        self.assertEqual(timeout, vcan_device.TIMEOUT_MS)
        self.assertEqual(len(packet), 24 + 64)
        self.assertEqual(packet[12], 9)  # DLC 9 represents 12 wire bytes.
        self.assertEqual(packet[24:33], b"123456789")
        self.assertEqual(packet[33:], bytes(55))

    def test_parse_bulk_preserves_a_frame_split_between_transfers(self):
        transport = FakeAsyncDevice()
        device = vcan_device.CanDevice(device=transport, channel=1)
        payload = bytes(range(8))
        size = 24 + 8
        packet = vcan_device.HEADER.pack(
            vcan_device.ECHO_RX, vcan_device.opcode(1, size), 0)
        packet += bytes.fromhex("23010000") + bytes((8, 0, 0, 0))
        packet += bytes(8)  # timestamp
        packet += payload

        self.assertEqual(list(device.parse_bulk(packet[:17])), [])
        frames = list(device.parse_bulk(packet[17:]))

        self.assertEqual(len(frames), 1)
        self.assertEqual(frames[0].can_id, 0x123)
        self.assertEqual(frames[0].data, payload)
        self.assertEqual(frames[0].channel, 1)

    def test_fd_receive_reassembles_all_splits_including_full_speed_packets(self):
        protocol = vcan_device
        for dlc, size in enumerate(protocol.FD_DLC_LENGTHS):
            packet = protocol.HEADER.pack(protocol.ECHO_RX, protocol.opcode(1, 88), protocol.FLAG_FD)
            packet += protocol.CAN_FRAME_FIELDS.pack(0x123, dlc, 1234) + bytes(range(64))
            for split in range(1, len(packet)):
                with self.subTest(dlc=dlc, split=split):
                    device = protocol.CanDevice(FakeAsyncDevice(), channel=1)
                    self.assertEqual(list(device.parse_bulk(packet[:split])), [])
                    frames = list(device.parse_bulk(packet[split:]))
                    self.assertEqual(len(frames), 1)
                    self.assertEqual(frames[0].data, bytes(range(size)))
                    self.assertEqual(frames[0].timestamp_us, 1234)
                    self.assertEqual(device._rx_tail, b'')

    def test_fd_padded_bundles_preserve_sequence_and_payload(self):
        # Firmware bundles end at a zero echo ID and are padded to FS MPS.
        from tests.hardware_test import _message
        protocol = vcan_device
        for bundle_size in (1, 2, 5):
            for split in (None, 64):
                device = protocol.CanDevice(FakeAsyncDevice(), channel=1)
                expected = [_message(1, n, True, False) for n in range(96)]
                received = []
                for first in range(0, len(expected), bundle_size):
                    packet = b''
                    for msg in expected[first:first + bundle_size]:
                        flags = protocol.FLAG_FD | (protocol.FLAG_EFF if msg.is_extended_id else 0)
                        packet += protocol.HEADER.pack(protocol.ECHO_RX, protocol.opcode(1, 88), flags)
                        dlc = protocol.FD_DLC_LENGTHS.index(len(msg.data))
                        packet += protocol.CAN_FRAME_FIELDS.pack(msg.arbitration_id, dlc, 0)
                        packet += bytes(msg.data).ljust(64, b'\x00')
                    packet += bytes(4)
                    packet += bytes((-len(packet)) % 64)
                    step = split or len(packet)
                    for offset in range(0, len(packet), step):
                        received.extend(device.parse_bulk(packet[offset:offset + step]))
                self.assertEqual(len(received), len(expected))
                for frame, msg in zip(received, expected):
                    self.assertEqual(frame.data, msg.data)
                    self.assertEqual(frame.extended, msg.is_extended_id)
                self.assertEqual(device._rx_tail, b'')


if __name__ == "__main__":
    unittest.main()
