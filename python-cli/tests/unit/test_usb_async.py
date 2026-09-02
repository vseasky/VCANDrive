import threading
import unittest

import usb.core
import usb1

from vcan_usb import transport as vcan_transport
from vcan_usb.transport import AsyncDevice as VCanAsyncDevice
from vkgs_usb import transport as vkgs_transport
from vkgs_usb.transport import AsyncDevice as VkGsAsyncDevice


ASYNC_DEVICE_CLASSES = (VCanAsyncDevice, VkGsAsyncDevice)
TRANSPORT_MODULES = (vcan_transport, vkgs_transport)


class FakeTransfer:
    def __init__(self, status=usb1.TRANSFER_COMPLETED, actual=None):
        self.status = status
        self.actual = actual
        self.callback = None
        self.payload = None
        self.endpoint = None
        self.timeout = None
        self.submitted = False

    def setBulk(self, endpoint, payload, callback, timeout):
        self.endpoint = endpoint
        self.payload = payload
        self.callback = callback
        self.timeout = timeout

    def submit(self):
        self.submitted = True
        if self.actual is None:
            self.actual = len(self.payload)
        self.submitted = False
        self.callback(self)

    def getStatus(self):
        return self.status

    def getActualLength(self):
        return self.actual

    def isSubmitted(self):
        return self.submitted


class FakeHandle:
    def __init__(self, transfer):
        self.transfer = transfer
        self.bulk_write_called = False

    def getTransfer(self):
        return self.transfer

    def bulkWrite(self, *args, **kwargs):
        self.bulk_write_called = True
        raise AssertionError("blocking bulkWrite must not be used")


class FakeUsbDevice:
    def __init__(self, vid, pid, bus, address, port_path):
        self.vid = vid
        self.pid = pid
        self.bus = bus
        self.address = address
        self.port_path = tuple(port_path)

    def getVendorID(self):
        return self.vid

    def getProductID(self):
        return self.pid

    def getBusNumber(self):
        return self.bus

    def getDeviceAddress(self):
        return self.address

    def getPortNumberList(self):
        return self.port_path


class FakeUsbContext:
    def __init__(self, devices):
        self.devices = devices

    def getDeviceList(self, skip_on_error=True):
        return self.devices


def device_with(device_class, transfer):
    device = device_class.__new__(device_class)
    device.context = object()
    device.event_context = type("Context", (), {"error": None})()
    device.handle = FakeHandle(transfer)
    device._tx_lock = threading.Lock()
    device._stats = {
        "rx_completed": 0, "rx_bytes": 0, "rx_errors": 0,
        "tx_completed": 0, "tx_bytes": 0, "tx_errors": 0,
    }
    return device


class AsyncOutTest(unittest.TestCase):
    def test_context_find_filters_usb_port_path(self):
        devices = [
            FakeUsbDevice(0x1D50, 0x606F, 1, 10, (1, 2)),
            FakeUsbDevice(0x1D50, 0x606F, 1, 11, (1, 3)),
        ]
        for module in TRANSPORT_MODULES:
            with self.subTest(module=module.__name__):
                context = module.Context.__new__(module.Context)
                context.usb = FakeUsbContext(devices)

                found = module.Context.find(
                    context, 0x1D50, 0x606F, port_path="1.3")

                self.assertIs(found, devices[1])
                self.assertEqual(module.device_port_path(found), (1, 3))

    def test_context_find_reports_selectors(self):
        devices = [FakeUsbDevice(0x1D50, 0x606F, 1, 10, (1, 2))]
        for module in TRANSPORT_MODULES:
            with self.subTest(module=module.__name__):
                context = module.Context.__new__(module.Context)
                context.usb = FakeUsbContext(devices)

                with self.assertRaisesRegex(RuntimeError, "port_path=9"):
                    module.Context.find(
                        context, 0x1D50, 0x606F, port_path="9")

    def test_write_uses_async_transfer_and_counts_completion(self):
        for device_class in ASYNC_DEVICE_CLASSES:
            with self.subTest(device_class=device_class):
                transfer = FakeTransfer()
                device = device_with(device_class, transfer)

                written = device.write(0x02, b"payload", timeout=2000)

                self.assertEqual(written, 7)
                self.assertEqual(transfer.endpoint, 0x02)
                self.assertEqual(transfer.payload, b"payload")
                self.assertEqual(transfer.timeout, 2000)
                self.assertFalse(device.handle.bulk_write_called)
                self.assertEqual(device._stats["tx_completed"], 1)
                self.assertEqual(device._stats["tx_bytes"], 7)
                self.assertEqual(device._stats["tx_errors"], 0)

    def test_write_maps_async_timeout_and_counts_error(self):
        for device_class in ASYNC_DEVICE_CLASSES:
            with self.subTest(device_class=device_class):
                device = device_with(
                    device_class,
                    FakeTransfer(status=usb1.TRANSFER_TIMED_OUT, actual=0),
                )

                with self.assertRaises(usb.core.USBTimeoutError):
                    device.write(0x02, b"payload", timeout=2000)

                self.assertEqual(device._stats["tx_completed"], 0)
                self.assertEqual(device._stats["tx_bytes"], 0)
                self.assertEqual(device._stats["tx_errors"], 1)


if __name__ == "__main__":
    unittest.main()
