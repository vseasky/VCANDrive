import unittest

import can

from tools.canctl import _data, _format_message, _int, _parser


class CanCtlTest(unittest.TestCase):
    def test_numeric_and_payload_parsing(self):
        self.assertEqual(_int("0x123"), 0x123)
        self.assertEqual(_int("123"), 123)
        self.assertEqual(_data("11:22-33 44"), bytes.fromhex("11223344"))

    def test_global_options_precede_send_command(self):
        args = _parser().parse_args([
            "--interface", "vkgs_usb", "--channel", "1", "--fd",
            "send", "0x123", "0102", "--brs",
        ])
        self.assertEqual(args.channel, 1)
        self.assertTrue(args.fd)
        self.assertTrue(args.brs)

    def test_channel_has_no_implicit_default(self):
        args = _parser().parse_args(["send", "0x123"])
        self.assertIsNone(args.channel)

    def test_usb_mode_is_unambiguous(self):
        args = _parser().parse_args([
            "--interface", "vcan_usb", "--channel", "1",
            "usb_mode", "gs_usb", "--yes",
        ])
        self.assertEqual(args.command, "usb_mode")
        self.assertEqual(args.target, "gs_usb")
        self.assertTrue(args.yes)

    def test_message_format_contains_frame_type(self):
        message = can.Message(
            timestamp=1.25, arbitration_id=0x123, channel=0,
            is_extended_id=False, is_fd=True, bitrate_switch=True,
            data=b"\x01\x02",
        )
        rendered = _format_message(message)
        self.assertIn("123 FD+BRS", rendered)
        self.assertIn("01 02", rendered)


if __name__ == "__main__":
    unittest.main()
