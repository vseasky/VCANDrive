import struct
import unittest

from vcan_usb import protocol as vcan
from vkgs_usb import protocol as vkgs


class Endpoint:
    def __init__(self, address):
        self.bEndpointAddress = address
        self.wMaxPacketSize = 512


class FakeDevice:
    bus = 1
    address = 2

    def __init__(self):
        self.ep_in = Endpoint(0x82)
        self.ep_out = Endpoint(0x02)
        self.writes = []

    def write(self, endpoint, packet, timeout):
        self.writes.append((endpoint, packet, timeout))
        return len(packet)

    def start_rx(self):
        pass


class FakeInfoControl:
    def __init__(self, protocol, payload):
        self.protocol = protocol
        self.payload = payload

    def ctrl_transfer(
        self, request_type, request, value, index, size, timeout
    ):
        if self.protocol is vcan:
            total = vcan.HEADER.size + len(self.payload)
            return (
                vcan.HEADER.pack(
                    vcan.ECHO_SETUP,
                    vcan.opcode(index, total),
                    vcan.BREQ_BSP_DEVICE_INFO,
                )
                + self.payload
            )
        return self.payload


class ProtocolMappingTest(unittest.TestCase):
    def test_device_info_uses_v003_hardware_field_semantics(self):
        values = (
            0x00000300,
            0x01200102,
            1, 2, 3, 4,
            5, 6, 7, 8,
        )
        for protocol in (vcan, vkgs):
            with self.subTest(protocol=protocol.__name__):
                payload = protocol.DEVICE_INFO_FIELDS.pack(*values)
                device = protocol.CanDevice(
                    FakeDevice(),
                    channel=1,
                    control_device=FakeInfoControl(protocol, payload),
                )
                info = device.info()
                self.assertEqual(info["sw_version_text"], "v0.3.0")
                self.assertEqual(info["hw_version_text"], "v1.2")
                self.assertEqual(info["hw_version"], 0x0102)
                self.assertEqual(info["hw_flags"], 1)
                self.assertTrue(info["hw_isolated"])
                self.assertNotIn("hw_platform", info)
                self.assertEqual(info["usb_speed"], "HS")
                self.assertEqual(info["uid_hex"], "00000001000000020000000300000004")

    def test_vcan_maps_all_frame_flags_and_timeout(self):
        transport = FakeDevice()
        device = vcan.CanDevice(transport, channel=1)
        frame = vcan.CanFrame(
            can_id=0x18EBFF80,
            data=b"12345678",
            channel=1,
            fd=True,
            brs=True,
            esi=True,
            extended=True,
            error=True,
            overflow=True,
        )
        device.send(frame, timeout_ms=37)
        _endpoint, packet, timeout = transport.writes[0]
        _echo, _opcode, flags = vcan.HEADER.unpack_from(packet)
        self.assertEqual(timeout, 37)
        self.assertTrue(flags & vcan.FLAG_FD)
        self.assertTrue(flags & vcan.FLAG_BRS)
        self.assertTrue(flags & vcan.FLAG_ESI)
        self.assertTrue(flags & vcan.FLAG_EFF)
        self.assertTrue(flags & vcan.FLAG_ERR)

    def test_vcan_decodes_data_state_berr_and_load(self):
        device = vcan.CanDevice(FakeDevice(), channel=1)
        frame_flags = (
            vcan.FLAG_FD | vcan.FLAG_BRS | vcan.FLAG_ESI |
            vcan.FLAG_EFF | vcan.FLAG_ERR | vcan.FLAG_OVERFLOW
        )
        data_size = 24 + 64
        data = vcan.HEADER.pack(
            vcan.ECHO_RX, vcan.opcode(1, data_size), frame_flags)
        data += vcan.CAN_FRAME_FIELDS.pack(0x18EBFF80, 8, 1234)
        data += b"12345678" + bytes(56)

        state_size = vcan.HEADER.size + vcan.CAN_STATE_FIELDS.size
        state = vcan.HEADER.pack(
            vcan.ECHO_STATE, vcan.opcode(1, state_size),
            vcan.BREQ_CAN_STATE)
        state += vcan.CAN_STATE_FIELDS.pack(2000, 2, 5, 7)

        berr_size = vcan.HEADER.size + vcan.DEVICE_BERR_FIELDS.size
        berr = vcan.HEADER.pack(
            vcan.ECHO_STATE, vcan.opcode(1, berr_size), vcan.BREQ_BERR)
        berr += vcan.DEVICE_BERR_FIELDS.pack(1, 3, 6, 8, 9)

        load_size = vcan.HEADER.size + vcan.DEVICE_LOAD_FIELDS.size
        load = vcan.HEADER.pack(
            vcan.ECHO_LOAD, vcan.opcode(1, load_size), 0)
        load += vcan.DEVICE_LOAD_FIELDS.pack(3000, 0x4000, 0, 11, 12)

        frames = list(device.parse_bulk(data + state + berr + load))
        self.assertEqual(len(frames), 1)
        self.assertTrue(frames[0].fd and frames[0].brs and frames[0].esi)
        self.assertTrue(frames[0].extended and frames[0].error)
        self.assertTrue(frames[0].overflow)
        self.assertEqual(device.can_state, 2)
        self.assertEqual(device.bec, {"rxerr": 6, "txerr": 8})
        self.assertEqual(device.last_berr["error_code"], 3)
        self.assertEqual(device.last_bus_load["bus_load_q15"], 0x4000)

    def test_vkgs_decodes_data_state_berr_and_load(self):
        device = vkgs.CanDevice(FakeDevice(), channel=1)
        flags = (
            vkgs.FLAG_FD | vkgs.FLAG_BRS | vkgs.FLAG_ESI |
            vkgs.FLAG_OVERFLOW
        )
        raw_id = 0x18EBFF80 | vkgs.CAN_EFF | vkgs.CAN_ERR
        data = vkgs.HOST_HEADER.pack(
            vkgs.ECHO_RX, raw_id, 8, 1, flags, 0)
        data += b"12345678" + bytes(56)

        state = vkgs.EVENT_HEADER.pack(
            vkgs.ECHO_STATE, 1, 0, vkgs.BREQ_GET_STATE)
        state += vkgs.CAN_STATE_FIELDS.pack(2000, 2, 5, 7)

        berr = vkgs.EVENT_HEADER.pack(
            vkgs.ECHO_BERR, 1, 0, vkgs.BREQ_BERR)
        berr += vkgs.DEVICE_BERR_FIELDS.pack(1, 3, 6, 8, 9)

        load = vkgs.EVENT_HEADER.pack(
            vkgs.ECHO_LOAD, 1, 0, vkgs.BREQ_TIMESTAMP)
        load += vkgs.DEVICE_LOAD_FIELDS.pack(3000, 0x4000, 0, 11, 12)

        frames = list(device.parse_bulk(data + state + berr + load))
        self.assertEqual(len(frames), 1)
        self.assertTrue(frames[0].fd and frames[0].brs and frames[0].esi)
        self.assertTrue(frames[0].extended)
        self.assertFalse(frames[0].error)
        self.assertTrue(frames[0].overflow)
        self.assertEqual(device.can_state, 2)
        self.assertEqual(device.bec, {"rxerr": 6, "txerr": 8})
        self.assertEqual(device.last_berr["error_code"], 3)
        self.assertEqual(device.last_bus_load["bus_load_q15"], 0x4000)

    def test_vkgs_opcode_events_and_captured_device_packet(self):
        packet = bytes.fromhex(
            "3d5ec9a31c0006000d981801000000000000000000000000000000000000")
        device = vkgs.CanDevice(FakeDevice(), channel=0)
        self.assertEqual(list(device.parse_bulk(packet + bytes(36))), [])
        self.assertEqual(device.bec, {"rxerr": 0, "txerr": 0})
        for size in (28, 32):
            state = struct.pack("<IHH", vkgs.ECHO_STATE, 0x1000 | size, vkgs.BREQ_GET_STATE)
            state += vkgs.CAN_STATE_FIELDS.pack(2000, 2, 5, 7)
            if size == 32:
                state += bytes((1, 3, 9, 0))
            load = struct.pack("<IHH", vkgs.ECHO_LOAD, 0x101c, vkgs.BREQ_TIMESTAMP)
            load += vkgs.DEVICE_LOAD_FIELDS.pack(3000, 0x4000, 0, 11, 12)
            for split in range(1, len(state + load)):
                device = vkgs.CanDevice(FakeDevice(), channel=1)
                self.assertEqual(list(device.parse_bulk((state + load)[:split])), [])
                self.assertEqual(list(device.parse_bulk((state + load)[split:])), [])
                self.assertEqual(device.bec, {"rxerr": 5, "txerr": 7})
                self.assertEqual(device.last_bus_load["bus_load_q15"], 0x4000)
                if size == 32:
                    self.assertEqual(device.last_berr["error_code"], 3)
            wrong_channel = vkgs.CanDevice(FakeDevice(), channel=0)
            with self.assertRaises(vkgs.ProtocolError):
                list(wrong_channel.parse_bulk(state))
        device = vkgs.CanDevice(FakeDevice(), channel=1)
        with self.assertRaises(vkgs.ProtocolError):
            list(device.parse_bulk(struct.pack("<IHHI", vkgs.ECHO_STATE, 0x101d, 6, 0)))

    def test_both_protocols_reject_cross_channel_frames(self):
        vcan_device = vcan.CanDevice(FakeDevice(), channel=1)
        packet = vcan.HEADER.pack(
            vcan.ECHO_RX, vcan.opcode(0, 32), 0)
        packet += struct.pack("<IB3xQ", 0x123, 8, 0) + bytes(8)
        with self.assertRaises(vcan.ProtocolError):
            list(vcan_device.parse_bulk(packet))

        vkgs_device = vkgs.CanDevice(FakeDevice(), channel=1)
        packet = vkgs.HOST_HEADER.pack(
            vkgs.ECHO_RX, 0x123, 8, 0, 0, 0) + bytes(8)
        with self.assertRaises(vkgs.ProtocolError):
            list(vkgs_device.parse_bulk(packet))


if __name__ == "__main__":
    unittest.main()
