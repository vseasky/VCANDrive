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


if __name__ == "__main__":
    unittest.main()
