"""Asynchronous libusb transport and USB discovery for VCAN devices."""

from __future__ import annotations

import atexit
import threading
import time
from typing import Callable

import usb1
import usb.core

from .usb_info import (DEFAULT_RX_TRANSFER_SIZE, RX_TRANSFER_COUNT, Endpoint,
                       InterfaceInfo, UsbPortPath, format_port_path, rx_transfer_size)

_LIBUSB_EVENT_TIMEOUT_S = 0.1
_EVENT_THREAD_JOIN_TIMEOUT_S = 0.3
_RX_POOL_SETTLE_INTERVAL_S = 0.001
_RX_CANCEL_TIMEOUT_S = 1.0
_RX_CANCEL_POLL_INTERVAL_S = 0.005
_BULK_TRANSFER_TYPE = 0x02
_ENDPOINT_DIRECTION_IN = 0x80



def normalize_port_path(port_path: object = None) -> UsbPortPath | None:
    """Normalize a USB topology selector to a tuple of port numbers.

    String selectors accept either ``"1.2.3"`` or ``"1,2,3"``. Empty strings
    and ``"-"`` mean that no port-path filter is requested.
    """
    if port_path is None:
        return None
    if isinstance(port_path, str):
        normalized_text = port_path.strip()
        if normalized_text in {"", "-"}:
            return None
        components = normalized_text.replace(",", ".").split(".")
        if any(component == "" for component in components):
            raise ValueError(
                f"USB port_path must be like '1.2.3', got {port_path!r}")
    else:
        try:
            components = list(port_path)  # type: ignore[arg-type]
        except TypeError:
            components = [port_path]
    try:
        normalized_path = tuple(int(component) for component in components)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"USB port_path must be like '1.2.3', got {port_path!r}") from exc
    if not normalized_path:
        return None
    if any(component < 0 or component > 255 for component in normalized_path):
        raise ValueError("USB port_path entries must be 0..255")
    return normalized_path


def device_port_path(device: usb1.USBDevice) -> UsbPortPath:
    """Read a device's stable physical hub path when libusb exposes it."""
    try:
        return tuple(int(number) for number in device.getPortNumberList())
    except (AttributeError, TypeError, usb1.USBError):
        try:
            port_number = device.getPortNumber()
        except (AttributeError, TypeError, usb1.USBError):
            return ()
        return () if port_number in (None, 0) else (int(port_number),)


def _format_device(device: usb1.USBDevice, index: int) -> str:
    return (
        f"index={index} bus={device.getBusNumber()} "
        f"address={device.getDeviceAddress()} "
        f"port_path={format_port_path(device_port_path(device))}"
    )


class Context:
    """Own one libusb context and, when needed, its event-dispatch thread."""

    def __init__(self, *, start_event_thread: bool = True) -> None:
        self.usb = usb1.USBContext()
        self.usb.open()
        self._stop_event = threading.Event()
        self._event_error: BaseException | None = None
        self._closed = False
        self._devices = set()
        self._event_thread: threading.Thread | None = None
        if start_event_thread:
            self._event_thread = threading.Thread(
                target=self._handle_events,
                daemon=True,
                name="vcan-usb-events",
            )
            self._event_thread.start()
        atexit.register(self.close)

    @property
    def error(self) -> BaseException | None:
        """Return the fatal exception that stopped event dispatch, if any."""
        return self._event_error

    def _handle_events(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.usb.handleEventsTimeout(_LIBUSB_EVENT_TIMEOUT_S)
            except usb1.USBErrorInterrupted:
                continue
            except BaseException as exc:
                # Do not leave callers believing that their transfer pool is
                # alive after the sole libusb event dispatcher has died.
                self._event_error = exc
                self._stop_event.set()
                return

    def find(self, vid: int, pid: int, index: int = 0,
             bus: int | None = None, address: int | None = None,
             port_path: object = None) -> usb1.USBDevice:
        target_port_path = normalize_port_path(port_path)
        matching_devices = [
            device
            for device in self.usb.getDeviceList(skip_on_error=True)
            if device.getVendorID() == vid and device.getProductID() == pid
        ]
        selected_devices = matching_devices
        if bus is not None:
            selected_devices = [
                device for device in selected_devices
                if device.getBusNumber() == bus
            ]
        if address is not None:
            selected_devices = [
                device for device in selected_devices
                if device.getDeviceAddress() == address
            ]
        if target_port_path is not None:
            selected_devices = [
                device for device in selected_devices
                if device_port_path(device) == target_port_path
            ]
        if index < 0 or index >= len(selected_devices):
            filters = []
            if bus is not None:
                filters.append(f"bus={bus}")
            if address is not None:
                filters.append(f"address={address}")
            if target_port_path is not None:
                filters.append(
                    f"port_path={format_port_path(target_port_path)}")
            selected = ", ".join(filters) if filters else "none"
            filtered = "; ".join(
                _format_device(device, device_index)
                for device_index, device in enumerate(selected_devices)
            )
            available = "; ".join(
                _format_device(device, device_index)
                for device_index, device in enumerate(matching_devices)
            )
            raise RuntimeError(
                f"USB device index {index} not found for "
                f"vid=0x{vid:04x} pid=0x{pid:04x} filters={selected}; "
                f"filtered devices: {filtered or 'none'}; "
                f"all matching devices: {available or 'none'}")
        return selected_devices[index]

    def discover_interfaces(self, vid: int, pid: int, index: int = 0,
                            bus: int | None = None,
                            address: int | None = None,
                            port_path: object = None) -> list[InterfaceInfo]:
        return interface_info(
            self.find(vid, pid, index, bus, address, port_path))

    def close(self) -> None:
        """Stop event dispatch and release the underlying libusb context."""
        if self._closed:
            return
        # Keep the event dispatcher/context alive until every cancellation
        # has completed. A failed drain intentionally retains ownership.
        for device in tuple(self._devices):
            device.close()
        self._stop_event.set()
        if self._event_thread is not None:
            self._event_thread.join(timeout=_EVENT_THREAD_JOIN_TIMEOUT_S)
            if self._event_thread.is_alive():
                raise RuntimeError("libusb event dispatcher did not stop")
        self.usb.close()
        self._closed = True
        atexit.unregister(self.close)


def interface_info(device: usb1.USBDevice) -> list[InterfaceInfo]:
    """Discover alt-0 interfaces containing a bulk IN/OUT pair."""
    interfaces: list[InterfaceInfo] = []
    bus = device.getBusNumber()
    address = device.getDeviceAddress()
    port_path = device_port_path(device)
    try:
        speed = device.getDeviceSpeed()
    except (AttributeError, NotImplementedError, usb1.USBError):
        speed = None
    for setting in device.iterSettings():
        if setting.getAlternateSetting() != 0:
            continue
        bulk_endpoints = [
            endpoint for endpoint in setting.iterEndpoints()
            if endpoint.getAttributes() & 0x03 == _BULK_TRANSFER_TYPE
        ]
        endpoint_in = next(
            (endpoint for endpoint in bulk_endpoints
             if endpoint.getAddress() & _ENDPOINT_DIRECTION_IN),
            None,
        )
        endpoint_out = next(
            (endpoint for endpoint in bulk_endpoints
             if not endpoint.getAddress() & _ENDPOINT_DIRECTION_IN),
            None,
        )
        if endpoint_in is None or endpoint_out is None:
            continue
        interfaces.append(InterfaceInfo(
            number=setting.getNumber(),
            ep_in=Endpoint(
                endpoint_in.getAddress(), endpoint_in.getMaxPacketSize()),
            ep_out=Endpoint(
                endpoint_out.getAddress(), endpoint_out.getMaxPacketSize()),
            bus=bus,
            address=address,
            port_path=port_path,
            speed=speed,
        ))
    return sorted(interfaces, key=lambda item: item.number)


def _pyusb_error(exc: usb1.USBError) -> usb.core.USBError:
    if isinstance(exc, usb1.USBErrorTimeout):
        return usb.core.USBTimeoutError(str(exc), exc.value, 110)
    return usb.core.USBError(str(exc), exc.value, None)


class AsyncDevice:
    """One claimed USB interface with asynchronous bulk IN and OUT transfers."""

    def __init__(self, context: usb1.USBContext, device: usb1.USBDevice,
                 interface: int, event_context: Context | None = None) -> None:
        self.context = context
        self.event_context = event_context
        self.context_id = id(context)
        self.bus = device.getBusNumber()
        self.address = device.getDeviceAddress()
        self.port_path = device_port_path(device)
        self.idVendor = device.getVendorID()
        self.idProduct = device.getProductID()
        available_interfaces = interface_info(device)
        self.interface_count = len(available_interfaces)
        try:
            interface_descriptor = next(
                item for item in available_interfaces
                if item.number == interface
            )
        except StopIteration as exc:
            available_numbers = ", ".join(
                str(item.number) for item in available_interfaces
            )
            raise ValueError(
                f"USB interface {interface} has no bulk IN/OUT pair; "
                f"available CAN interfaces: {available_numbers or 'none'}"
            ) from exc
        self.speed = interface_descriptor.speed
        self.ep_in = interface_descriptor.ep_in
        self.ep_out = interface_descriptor.ep_out
        self.interface = interface
        self.handle = device.open()
        self.handle_id = id(self.handle)
        try:
            self.handle.claimInterface(interface)
        except Exception:
            self.handle.close()
            raise
        self._transfers: list[usb1.USBTransfer] = []
        self._active = False
        self._closed = False
        self._closing = False
        self._rx_generation = 0
        self._rx_pending = set()
        self._tx_pending = None
        if self.event_context is not None:
            self.event_context._devices.add(self)
        self._rx_condition = threading.Condition()
        # libusb's blocking bulkWrite() drives its own event handling.  Mixing
        # that path with this object's dedicated async IN event dispatcher can
        # leave a multi-endpoint device's OUT request pending indefinitely.
        # Submit OUT through the same dispatcher and serialize writes per
        # interface, matching the kernel driver's asynchronous URB model.
        self._tx_lock = threading.Lock()
        self._stats = {
            "rx_completed": 0,
            "rx_bytes": 0,
            "rx_errors": 0,
            "tx_completed": 0,
            "tx_bytes": 0,
            "tx_errors": 0,
        }

    def stats(self) -> dict[str, int]:
        """Return a snapshot of interface-local USB transfer counters."""
        snapshot = dict(self._stats)
        snapshot["rx_submitted"] = sum(
            int(transfer.isSubmitted()) for transfer in self._transfers)
        snapshot["rx_pool"] = len(self._transfers)
        return snapshot

    def ensure_rx_submitted(self, expected: int, timeout: float = 0.05) -> None:
        """Fail early when libusb did not retain the complete IN pool."""
        if not self._active or len(self._transfers) != expected:
            raise RuntimeError(
                f"bulk-IN pool is incomplete: {len(self._transfers)}/{expected}")
        deadline = time.monotonic() + timeout
        while True:
            submitted_count = sum(
                int(transfer.isSubmitted()) for transfer in self._transfers
            )
            if submitted_count == expected:
                return
            if time.monotonic() >= deadline:
                raise RuntimeError(
                    f"bulk-IN transfers submitted: {submitted_count}/{expected}")
            # A transfer is briefly not submitted while its completion
            # callback runs, so tolerate that normal event-thread window.
            time.sleep(_RX_POOL_SETTLE_INTERVAL_S)

    def wait_rx_completed(self, previous: int, timeout: float) -> bool:
        """Wait until this interface completes a new bulk-IN transfer."""
        deadline = time.monotonic() + timeout
        with self._rx_condition:
            while self._stats["rx_completed"] <= previous:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._rx_condition.wait(remaining)
            return True

    def ctrl_transfer(self, request_type: int, request: int, value: int,
                      index: int, data_or_length, timeout: int = 0):
        """Provide the PyUSB control-transfer API expected by protocol code."""
        try:
            if request_type & 0x80:
                return self.handle.controlRead(request_type, request, value,
                                               index, data_or_length, timeout)
            return self.handle.controlWrite(request_type, request, value,
                                            index, bytes(data_or_length), timeout)
        except usb1.USBError as exc:
            raise _pyusb_error(exc) from exc

    def write(self, endpoint: int, data: bytes, timeout: int = 0) -> int:
        """Submit one async OUT transfer and wait for its event callback."""
        payload = bytes(data)
        completion_event = threading.Event()
        completion: dict[str, int] = {}

        def on_complete(transfer: usb1.USBTransfer) -> None:
            completion["status"] = transfer.getStatus()
            completion["actual_length"] = transfer.getActualLength()
            completion_event.set()

        with self._tx_lock:
            if self._closed or self._closing:
                raise usb.core.USBError("USB interface is closed")
            if self._tx_pending is not None:
                old_transfer, old_done, _payload = self._tx_pending
                if not old_done.is_set():
                    raise usb.core.USBError("previous bulk OUT cancellation is still pending")
                self._tx_pending = None
            event_error = (None if self.event_context is None else
                           self.event_context.error)
            if event_error is not None:
                raise usb.core.USBError(
                    "libusb event dispatcher stopped") from event_error
            transfer = self.handle.getTransfer()
            transfer.setBulk(endpoint, payload, on_complete, timeout=timeout)
            self._tx_pending = (transfer, completion_event, payload)
            try:
                transfer.submit()
            except usb1.USBError as exc:
                self._tx_pending = None
                self._stats["tx_errors"] += 1
                raise _pyusb_error(exc) from exc

            # A finite libusb timeout completes through the callback.  Keep a
            # small host-side guard so a failed event dispatcher cannot strand
            # the caller forever; timeout=0 intentionally remains unlimited.
            host_timeout = None if timeout == 0 else timeout / 1000.0 + 1.0
            deadline = None if host_timeout is None else time.monotonic() + host_timeout
            while not completion_event.wait(0.05):
                if self._closing:
                    break
                if (self.event_context is not None and self.event_context.error is not None):
                    break
                if deadline is not None and time.monotonic() >= deadline:
                    break
            if not completion_event.is_set():
                if transfer.isSubmitted():
                    try:
                        transfer.cancel()
                    except usb1.USBErrorNotFound:
                        pass
                # cancel() only requests cancellation. Retain transfer and
                # immutable payload, and block another OUT until callback.
                if completion_event.wait(_RX_CANCEL_TIMEOUT_S):
                    self._tx_pending = None
                self._stats["tx_errors"] += 1
                event_error = (None if self.event_context is None else
                               self.event_context.error)
                if event_error is not None:
                    raise usb.core.USBError(
                        "libusb event dispatcher stopped") from event_error
                raise usb.core.USBTimeoutError(
                    "asynchronous bulk OUT completion timed out", -7, 110)

            self._tx_pending = None
            status = completion["status"]
            if status != usb1.TRANSFER_COMPLETED:
                self._stats["tx_errors"] += 1
                error_type = {
                    usb1.TRANSFER_TIMED_OUT: usb1.USBErrorTimeout,
                    usb1.TRANSFER_STALL: usb1.USBErrorPipe,
                    usb1.TRANSFER_NO_DEVICE: usb1.USBErrorNoDevice,
                    usb1.TRANSFER_OVERFLOW: usb1.USBErrorOverflow,
                }.get(status, usb1.USBErrorIO)
                exc = error_type()
                raise _pyusb_error(exc) from exc

            bytes_written = completion["actual_length"]
            self._stats["tx_completed"] += 1
            self._stats["tx_bytes"] += bytes_written
            return bytes_written

    def start_rx(self, callback: Callable[[bytes], None], size: int = DEFAULT_RX_TRANSFER_SIZE,
                 count: int = RX_TRANSFER_COUNT,
                 on_error: Callable[[BaseException], None] | None = None) -> None:
        """Submit and maintain a fixed-size pool of bulk-IN transfers."""
        if count != 1:
            raise ValueError("exactly one active bulk-IN transfer is required")
        if self._active or self._transfers:
            raise RuntimeError("bulk-IN transfer pool is already active")
        size = rx_transfer_size(self.ep_in.wMaxPacketSize, size)
        if size != self.ep_in.wMaxPacketSize:
            raise ValueError("vendor FIFO receive requires one USB packet per read")
        if self._closed or self._closing:
            raise RuntimeError("USB interface is closed")
        if self.event_context is not None and self.event_context.error is not None:
            raise RuntimeError("libusb event dispatcher stopped") from self.event_context.error
        self._active = True
        self._rx_generation += 1
        generation = self._rx_generation

        def on_complete(transfer: usb1.USBTransfer) -> None:
            # The condition covers delivery and resubmission: stop_rx cannot
            # return while a callback still owns the parser or resubmit late.
            with self._rx_condition:
                try:
                    if not self._active or generation != self._rx_generation:
                        return
                    status = transfer.getStatus()
                    if status != usb1.TRANSFER_COMPLETED:
                        raise usb.core.USBError(f"bulk IN failed: status={status}")
                    actual_length = transfer.getActualLength()
                    self._stats["rx_completed"] += 1
                    self._stats["rx_bytes"] += actual_length
                    callback(bytes(transfer.getBuffer()[:actual_length]))
                    if self._active and generation == self._rx_generation:
                        transfer.submit()
                        return
                except BaseException as exc:
                    self._stats["rx_errors"] += 1
                    self._active = False
                    if on_error is not None:
                        on_error(exc)
                    elif self.event_context is not None:
                        self.event_context._event_error = exc
                finally:
                    if not transfer.isSubmitted():
                        self._rx_pending.discard(transfer)
                    self._rx_condition.notify_all()

        self._transfers = []
        try:
            for _ in range(count):
                transfer = self.handle.getTransfer()
                transfer.setBulk(self.ep_in.bEndpointAddress, size, on_complete,
                                 timeout=0)
                self._transfers.append(transfer)
                with self._rx_condition:
                    self._rx_pending.add(transfer)
                    try:
                        transfer.submit()
                    except Exception:
                        self._rx_pending.discard(transfer)
                        raise
        except Exception:
            self.stop_rx()
            raise

    @property
    def rx_transfer_count(self) -> int:
        return len(self._transfers)

    def stop_rx(self) -> None:
        """Cancel reads; retain all resources if callbacks fail to drain."""
        with self._rx_condition:
            self._active = False
            self._rx_generation += 1
            for transfer in self._transfers:
                if transfer.isSubmitted():
                    try:
                        transfer.cancel()
                    except usb1.USBErrorNotFound:
                        pass
            deadline = time.monotonic() + _RX_CANCEL_TIMEOUT_S
            while self._rx_pending:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RuntimeError("bulk IN cancellation did not complete; resources retained")
                self._rx_condition.wait(remaining)
            self._transfers.clear()

    def close(self) -> None:
        """Close only after IN and OUT completions no longer own buffers."""
        if self._closed:
            return
        self._closing = True
        self.stop_rx()
        with self._tx_lock:
            if self._tx_pending is not None:
                transfer, done, _payload = self._tx_pending
                if transfer.isSubmitted():
                    try:
                        transfer.cancel()
                    except usb1.USBErrorNotFound:
                        pass
                if not done.wait(_RX_CANCEL_TIMEOUT_S):
                    raise RuntimeError("bulk OUT cancellation did not complete; resources retained")
                self._tx_pending = None
            try:
                self.handle.releaseInterface(self.interface)
            finally:
                self.handle.close()
            self._closed = True
            if self.event_context is not None:
                self.event_context._devices.discard(self)
