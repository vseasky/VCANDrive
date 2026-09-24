"""python-can Bus lifecycle for the VCAN USB backend."""
from __future__ import annotations

from collections import deque
import math
import sys
import threading
import time
from typing import Any, ClassVar

import can
import usb.core
import usb.util

from . import protocol
from . import transport as usb_transport


BitTiming = tuple[int, int, int, int]


def _calculate_timing(
    clock_hz: int,
    bitrate: int,
    sample_point: float,
    limits: tuple[int, ...] = (1, 16, 1, 8, 1, 1, 1024, 1),
) -> BitTiming:
    """Use device-advertised limits; round prescaler candidates per TQ count."""
    t1min, t1max, t2min, t2max, sjwmax, brpmin, brpmax, inc = limits
    if (clock_hz <= 0 or bitrate <= 0 or not 0 < sample_point < 100
            or min(t1min, t2min, sjwmax, brpmin, inc) < 1
            or t1max < t1min or t2max < t2min or brpmax < brpmin
            or t1max > 1024 or t2max > 1024 or brpmax > 65536):
        raise can.CanInitializationError("invalid device bit-timing limits")
    best = None
    preferred = 16 if bitrate <= 1_000_000 else 8
    # Avoid iterating every prescaler * segment pair for larger MCU ranges.
    for tseg1 in range(t1min, t1max + 1):
        for tseg2 in range(t2min, t2max + 1):
            tq = 1 + tseg1 + tseg2
            ideal = clock_hz / (bitrate * tq)
            index = math.floor((ideal - brpmin) / inc)
            for i in (index, index + 1):
                brp = brpmin + i * inc
                if not brpmin <= brp <= brpmax:
                    continue
                error = abs(clock_hz / (brp * tq) - bitrate) / bitrate
                if error > 0.001:
                    continue
                score = error * 1000 + abs(100 * (1 + tseg1) / tq - sample_point)
                candidate = (score, abs(tq - preferred), brp, tseg1, tseg2)
                if best is None or candidate < best:
                    best = candidate
    if best is None:
        raise can.CanInitializationError(
            f"cannot calculate {bitrate} bit/s timing at {clock_hz} Hz within device limits")
    _, _, brp, tseg1, tseg2 = best
    return brp, tseg1, tseg2, 1


def _format_usb_selector(
    index: int,
    bus: int | None,
    address: int | None,
    port_path: object = None,
) -> str:
    """Format all active physical-device selectors for an error message."""
    parts = [f"index={index}"]
    if bus is not None:
        parts.append(f"bus={bus}")
    if address is not None:
        parts.append(f"address={address}")
    try:
        normalized_path = usb_transport.normalize_port_path(port_path)
    except ValueError:
        normalized_path = None
        parts.append(f"port_path={port_path!r}")
    if normalized_path is not None:
        parts.append(
            f"port_path={usb_transport.format_port_path(normalized_path)}")
    return " ".join(parts)


def _is_usb_access_error(exc: BaseException) -> bool:
    """Recognize access-denied errors from libusb and native WinUSB."""
    return (
        exc.__class__.__name__ == "USBErrorAccess" or
        getattr(exc, "value", None) == -3 or
        getattr(exc, "backend_error_code", None) in {-3, 5} or
        "LIBUSB_ERROR_ACCESS" in str(exc) or
        "WinError 5" in str(exc)
    )


def _format_usb_open_error(
    vid: int,
    pid: int,
    channel: int,
    index: int,
    bus: int | None,
    address: int | None,
    port_path: object,
    exc: BaseException,
) -> str:
    """Build an actionable USB-open error for the current platform."""
    message = (
        f"failed to open USB CAN interface {channel} on "
        f"vid=0x{vid:04x} pid=0x{pid:04x} "
        f"({_format_usb_selector(index, bus, address, port_path)}): {exc}"
    )
    if sys.platform == "win32" and _is_usb_access_error(exc):
        return (
            message +
            "; Windows denied access to this MI_xx interface. Close the "
            "process using this channel and confirm the selected interface is "
            "bound to WinUSB. Other interfaces on the adapter may remain open."
        )
    if _is_usb_access_error(exc):
        return (
            message +
            "; access denied. Check udev permissions or close the process that "
            "already owns this USB interface."
        )
    return message


def _open_windows_interface(
    vid: int,
    pid: int,
    interface_info: usb_transport.InterfaceInfo,
    interface_count: int,
) -> Any:
    """Load the Windows-only transport without affecting other platforms."""
    from .winusb import WinUsbDevice

    return WinUsbDevice(vid, pid, interface_info, interface_count)


class _UsbSession:
    """Own the platform USB resources used by one python-can Bus instance."""

    def __init__(
        self,
        context: Any,
        control_device: Any,
    ) -> None:
        # EP0 belongs to the same independently opened handle as this Bus's
        # bulk endpoints.  wValue/wIndex select the firmware channel.
        self.context = context
        self.control_device = control_device

    @classmethod
    def discover(cls, vid: int, pid: int, index: int = 0,
                 bus: int | None = None,
                 address: int | None = None,
                 port_path: object = None
                 ) -> list[usb_transport.InterfaceInfo]:
        """Discover interfaces without retaining a USB handle or claim."""
        # Enumeration is short-lived and owns no claimed interface.  Runtime
        # Bus objects deliberately do not share this context.
        context = usb_transport.Context(start_event_thread=False)
        try:
            return context.discover_interfaces(
                vid, pid, index=index, bus=bus, address=address,
                port_path=port_path)
        finally:
            context.close()

    @classmethod
    def acquire(cls, vid: int, pid: int, channel: int, index: int = 0,
                bus: int | None = None, address: int | None = None,
                port_path: object = None
                ) -> tuple[_UsbSession, Any]:
        """Open one interface using the platform-appropriate handle policy."""
        if sys.platform == "win32":
            try:
                return cls._acquire_windows(
                    vid, pid, channel, index, bus, address, port_path)
            except can.CanInitializationError:
                raise
            except Exception as exc:
                raise can.CanInitializationError(
                    _format_usb_open_error(
                        vid, pid, channel, index, bus, address, port_path, exc)
                ) from exc
        # Prefer independent runtime handles on platforms where libusb allows
        # them.  This preserves the validated Linux multi-process behavior:
        # one process may operate channel 0 while another owns channel 1.
        context = usb_transport.Context()
        try:
            raw_device = context.find(
                vid, pid, index, bus, address, port_path)
            # Never call SET_CONFIGURATION here: another process may already
            # own a different interface of this composite device.
            interface_device = usb_transport.AsyncDevice(
                context.usb,
                raw_device,
                channel,
                event_context=context,
            )
        except Exception as exc:
            context.close()
            raise can.CanInitializationError(
                _format_usb_open_error(
                    vid, pid, channel, index, bus, address, port_path, exc)
            ) from exc
        return cls(context, interface_device), interface_device

    @classmethod
    def _acquire_windows(
        cls,
        vid: int,
        pid: int,
        channel: int,
        index: int = 0,
        bus: int | None = None,
        address: int | None = None,
        port_path: object = None,
    ) -> tuple[_UsbSession, Any]:
        # libusb remains useful for descriptor discovery, but its Windows
        # backend opens every interface of a composite device at libusb_open().
        # Close that discovery context before opening only the selected MI_xx
        # PDO through native WinUSB.
        discovery_context = usb_transport.Context(start_event_thread=False)
        try:
            raw_device = discovery_context.find(
                vid, pid, index, bus, address, port_path)
            available_interfaces = usb_transport.interface_info(raw_device)
            try:
                selected_interface = next(
                    item for item in available_interfaces
                    if item.number == channel
                )
            except StopIteration as exc:
                available_numbers = ", ".join(
                    str(item.number) for item in available_interfaces
                )
                raise ValueError(
                    f"USB interface {channel} has no bulk IN/OUT pair; "
                    f"available CAN interfaces: {available_numbers or 'none'}"
                ) from exc
        finally:
            discovery_context.close()

        interface_device = _open_windows_interface(
            vid,
            pid,
            selected_interface,
            len(available_interfaces),
        )
        return cls(interface_device, interface_device), interface_device

    def release(self, interface_device: Any) -> None:
        """Release this Bus interface and its independently owned resources."""
        try:
            interface_device.close()
        finally:
            if self.context is not interface_device:
                self.context.close()


class UsbCanBus(can.BusABC):
    """Common python-can adapter for one USB CAN interface."""

    DEVICE_MODULE: ClassVar[Any]
    INTERFACE_NAME: ClassVar[str]

    @classmethod
    def switch_usb_mode(
        cls,
        mode: str,
        *,
        channel: int,
        index: int = 0,
        bus: int | None = None,
        address: int | None = None,
        port_path: object = None,
        timeout_ms: int = 2_000,
    ) -> None:
        """Persist a USB protocol mode and reboot the device.

        This is deliberately a class method: switching the USB personality is
        a device-management operation, not a CAN bus mode change.  It claims
        only the selected interface and does not start bulk RX, configure MCAN,
        or construct a normal :class:`can.BusABC` session.
        """
        modes = {
            "vcan": cls.DEVICE_MODULE.USB_MODE_VCAN,
            "peak": cls.DEVICE_MODULE.USB_MODE_PEAK_CAN,
            "gs_usb": cls.DEVICE_MODULE.USB_MODE_GS_USB,
        }
        if mode not in modes:
            raise ValueError("USB mode must be vcan, peak, or gs_usb")
        if channel < 0:
            raise ValueError("channel must be >= 0")
        if timeout_ms <= 0:
            raise ValueError("timeout_ms must be > 0")
        try:
            port_path = usb_transport.normalize_port_path(port_path)
        except ValueError as exc:
            raise can.CanInitializationError(str(exc)) from exc

        usb_session, interface_device = _UsbSession.acquire(
            cls.DEVICE_MODULE.VID,
            cls.DEVICE_MODULE.PID,
            channel=channel,
            index=index,
            bus=bus,
            address=address,
            port_path=port_path,
        )
        request_sent = False
        try:
            device = cls.DEVICE_MODULE.CanDevice(
                device=interface_device,
                channel=channel,
                timeout_ms=timeout_ms,
                control_device=usb_session.control_device,
            )
            device.usb_mode(modes[mode])
            request_sent = True
        finally:
            try:
                usb_session.release(interface_device)
            except Exception:
                # A successful request intentionally re-enumerates the USB
                # device, so releasing the now-disconnected handle may fail.
                if not request_sent:
                    raise

    @classmethod
    def discover_interfaces(cls, *, index: int = 0,
                            bus: int | None = None,
                            address: int | None = None,
                            port_path: object = None
                            ) -> list[usb_transport.InterfaceInfo]:
        """Return bulk endpoint maps discovered from USB descriptors."""
        return _UsbSession.discover(
            cls.DEVICE_MODULE.VID, cls.DEVICE_MODULE.PID,
            index=index, bus=bus, address=address, port_path=port_path)

    @classmethod
    def discover_channels(cls, *, index: int = 0,
                          bus: int | None = None,
                          address: int | None = None,
                          port_path: object = None) -> list[int]:
        """Return CAN interface numbers discovered from USB descriptors."""
        return [item.number for item in cls.discover_interfaces(
            index=index, bus=bus, address=address, port_path=port_path)]

    @classmethod
    def _detect_available_configs(
            cls) -> list[can.typechecking.AutoDetectedConfig]:
        configs: list[can.typechecking.AutoDetectedConfig] = []
        index = 0
        while True:
            try:
                channels = cls.discover_channels(index=index)
            except RuntimeError:
                break
            configs.extend({
                "interface": cls.INTERFACE_NAME,
                "channel": channel,
                "index": index,
            } for channel in channels)
            index += 1
        return configs

    def __init__(
        self,
        channel: int | str = 0,
        bitrate: int = 1_000_000,
        *,
        index: int = 0,
        bus: int | None = None,
        address: int | None = None,
        port_path: object = None,
        sample_point: float = 75.0,
        fd: bool = False,
        data_bitrate: int = 5_000_000,
        data_sample_point: float = 75.0,
        receive_own_messages: bool = False,
        loopback: bool = False,
        termination: bool | None = None,
        bus_load_reporting: bool | None = None,
        auto_start: bool = True,
        can_filters: can.typechecking.CanFilters | None = None,
        timeout_ms: int = 2_000,
        rx_queue_size: int = 4096,
        **kwargs: Any,
    ) -> None:
        try:
            channel_number = int(channel)
        except (TypeError, ValueError) as exc:
            raise can.CanInitializationError(
                f"channel must be a USB interface number, got {channel!r}") from exc
        if channel_number < 0:
            raise can.CanInitializationError("channel must be >= 0")
        if bitrate <= 0 or data_bitrate <= 0:
            raise can.CanInitializationError("bitrate values must be > 0")
        if not 0.0 < sample_point < 100.0:
            raise can.CanInitializationError("sample_point must be between 0 and 100")
        if not 0.0 < data_sample_point < 100.0:
            raise can.CanInitializationError(
                "data_sample_point must be between 0 and 100")
        if timeout_ms <= 0:
            raise can.CanInitializationError("timeout_ms must be > 0")
        if rx_queue_size <= 0:
            raise can.CanInitializationError("rx_queue_size must be > 0")
        try:
            port_path = usb_transport.normalize_port_path(port_path)
        except ValueError as exc:
            raise can.CanInitializationError(str(exc)) from exc
        self.channel = channel_number
        self.receive_own_messages = receive_own_messages
        # Every Bus owns exactly one interface and one runtime handle.  Windows
        # opens the selected MI_xx PDO; Linux/macOS claim only that interface.
        self._usb_session, self._usb_interface = _UsbSession.acquire(
            self.DEVICE_MODULE.VID, self.DEVICE_MODULE.PID,
            channel=channel_number, index=index, bus=bus, address=address,
            port_path=port_path)
        self._protocol_device: Any | None = None
        self._rx_queue: deque[Any] = deque()
        self._rx_queue_size = rx_queue_size
        self._rx_dropped = 0
        self._rx_overflow = 0
        self._rx_condition = threading.Condition()
        self._io_lock = threading.RLock()
        self._rx_error: BaseException | None = None
        self._device_info: dict[str, Any] | None = None
        self._started = False
        self._start_flags = 0
        self._loopback = loopback
        self._termination_setting = termination
        self._bus_load_setting = bus_load_reporting
        self._is_shutdown = False
        try:
            self._protocol_device = self.DEVICE_MODULE.CanDevice(
                device=self._usb_interface, channel=channel_number,
                timeout_ms=timeout_ms,
                control_device=self._usb_session.control_device)
            if hasattr(self._protocol_device, "host_format"):
                self._protocol_device.host_format()
            self._capabilities = self._protocol_device.capabilities()
            self._nominal_timing = _calculate_timing(
                self._capabilities["clock_hz"], bitrate, sample_point,
                self._capabilities["nominal"])
            self._data_timing = None
            self._data_bitrate = data_bitrate
            self._data_sample_point = data_sample_point
            # BSP_INFO is diagnostic-only and is deliberately not part of the
            # normal initialization transaction.
            # Claim/open has completed.  Submit this interface's independent
            # IN pool before the first firmware configuration command so no
            # startup response/event can race receiver registration.
            self._usb_interface.start_rx(self._accept_bulk)
            self._usb_interface.ensure_rx_submitted(
                usb_transport.RX_TRANSFER_COUNT)
            self.configure(fd=fd)
            try:
                self._device_info = self._protocol_device.info()
            except (usb.core.USBError, self.DEVICE_MODULE.ProtocolError):
                # BSP_INFO is optional on older firmware.  CAN operation must
                # remain available when this diagnostic request is absent.
                self._device_info = None
            if auto_start:
                self.start()
        except Exception:
            if (
                self._protocol_device is not None
                and getattr(self._protocol_device, "_claimed", False)
            ):
                try:
                    usb.util.release_interface(
                        self._usb_interface,
                        self._protocol_device.interface,
                    )
                except usb.core.USBError:
                    pass
                self._protocol_device._claimed = False
            self._usb_session.release(self._usb_interface)
            raise
        formatted_port_path = usb_transport.format_port_path(
            self._usb_interface.port_path)
        self.channel_info = (
            f"{self.INTERFACE_NAME} bus={self._usb_interface.bus:03d} "
            f"address={self._usb_interface.address:03d} "
            f"port_path={formatted_port_path} "
            f"interface={channel_number}")
        super().__init__(channel=channel_number, can_filters=can_filters,
                         **kwargs)

    def _accept_bulk(self, transfer: bytes) -> None:
        """Decode one USB transfer without calling user code."""
        try:
            received_frames = list(
                self._protocol_device.parse_bulk(transfer))
        except BaseException as exc:
            with self._rx_condition:
                self._rx_error = exc
                self._rx_condition.notify_all()
            return
        if received_frames:
            with self._rx_condition:
                self._rx_overflow += sum(
                    int(getattr(frame, "overflow", False))
                    for frame in received_frames
                )
                available = self._rx_queue_size - len(self._rx_queue)
                accepted = received_frames[:available]
                self._rx_queue.extend(accepted)
                self._rx_dropped += len(received_frames) - len(accepted)
                if accepted:
                    self._rx_condition.notify_all()

    def send(self, msg: can.Message, timeout: float | None = None) -> None:
        if self._is_shutdown:
            raise can.CanOperationError("bus is shut down")
        if self._usb_session.context.error is not None:
            raise can.CanOperationError(
                "USB receive dispatcher stopped"
            ) from self._usb_session.context.error
        if not self._started:
            raise can.CanOperationError("channel is not started")
        if msg.is_fd and self._can_protocol is not can.CanProtocol.CAN_FD:
            raise can.CanOperationError(
                "cannot send a CAN FD message on a classic CAN bus")
        if timeout is not None and timeout < 0:
            raise ValueError("timeout must be >= 0")
        if msg.is_fd and msg.is_remote_frame:
            raise ValueError("CAN FD does not support remote frames")
        protocol_frame = self.DEVICE_MODULE.CanFrame(
            can_id=msg.arbitration_id,
            data=bytes(msg.data),
            channel=self.channel,
            fd=msg.is_fd,
            brs=msg.bitrate_switch,
            esi=msg.error_state_indicator,
            extended=msg.is_extended_id,
            rtr=msg.is_remote_frame,
            error=msg.is_error_frame,
            dlc=msg.dlc,
        )
        deadline = None if timeout is None else time.monotonic() + timeout
        acquired = (
            self._io_lock.acquire()
            if timeout is None
            else self._io_lock.acquire(timeout=timeout)
        )
        if not acquired:
            raise can.CanOperationError("send queue timeout")
        try:
            remaining = (
                None if deadline is None
                else max(0.0, deadline - time.monotonic())
            )
            tx_timeout_ms = (
                None if remaining is None
                else max(1, math.ceil(remaining * 1000.0))
            )
            self._protocol_device.send(
                protocol_frame, timeout_ms=tx_timeout_ms)
        except (usb.core.USBError, self.DEVICE_MODULE.ProtocolError) as exc:
            raise can.CanOperationError(str(exc)) from exc
        finally:
            self._io_lock.release()

    def _recv_internal(
        self, timeout: float | None
    ) -> tuple[can.Message | None, bool]:
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._rx_condition:
            while not self._rx_queue:
                if self._usb_session.context.error is not None:
                    raise can.CanOperationError(
                        "USB receive dispatcher stopped"
                    ) from self._usb_session.context.error
                if self._rx_error is not None:
                    raise can.CanOperationError(str(self._rx_error)) from self._rx_error
                if self._is_shutdown:
                    return None, False
                remaining = (None if deadline is None else
                             deadline - time.monotonic())
                if remaining is not None and remaining <= 0:
                    return None, False
                # Wake periodically even for recv(None), so a fatal error in
                # the separate USB receive thread cannot strand this caller.
                self._rx_condition.wait(
                    0.1 if remaining is None else min(remaining, 0.1))
            protocol_frame = self._rx_queue.popleft()
        timestamp_us = getattr(protocol_frame, "timestamp_us", 0)
        payload = b"" if protocol_frame.rtr else protocol_frame.data
        message = can.Message(
            timestamp=timestamp_us / 1_000_000 if timestamp_us else 0.0,
            arbitration_id=protocol_frame.can_id,
            is_extended_id=protocol_frame.extended,
            is_remote_frame=protocol_frame.rtr,
            is_fd=protocol_frame.fd,
            bitrate_switch=protocol_frame.brs,
            error_state_indicator=protocol_frame.esi,
            is_error_frame=protocol_frame.error,
            channel=protocol_frame.channel,
            dlc=len(protocol_frame.data),
            data=payload,
            is_rx=True,
        )
        return message, False

    def shutdown(self) -> None:
        already_shutdown = self._is_shutdown
        super().shutdown()
        if already_shutdown:
            return
        with self._rx_condition:
            self._rx_condition.notify_all()
        try:
            if self._protocol_device is not None and self._started:
                with self._io_lock:
                    self._protocol_device.stop()
                    if hasattr(self._protocol_device, "host_format"):
                        self._protocol_device.host_format()
                self._started = False
        finally:
            if (
                self._protocol_device is not None
                and getattr(self._protocol_device, "_claimed", False)
            ):
                try:
                    usb.util.release_interface(
                        self._usb_interface,
                        self._protocol_device.interface,
                    )
                except usb.core.USBError:
                    pass
                self._protocol_device._claimed = False
            self._usb_session.release(self._usb_interface)

    def start(self) -> None:
        """Start a configured channel; useful for synchronized multi-channel open."""
        if self._is_shutdown:
            raise can.CanOperationError("bus is shut down")
        if self._started:
            return
        with self._io_lock:
            self._protocol_device.start(self._start_flags)
        self._started = True

    def stop(self) -> None:
        """Stop and return the interface to its host-format baseline."""
        if self._is_shutdown or not self._started:
            return
        with self._io_lock:
            self._protocol_device.stop()
            if hasattr(self._protocol_device, "host_format"):
                self._protocol_device.host_format()
        self._started = False

    def configure(self, *, fd: bool | None = None) -> None:
        """Close any persistent device session, then configure from baseline."""
        if self._is_shutdown:
            raise can.CanOperationError("bus is shut down")
        if self._started:
            raise can.CanOperationError("stop channel before configuring it")
        if fd is None:
            fd = self._can_protocol is can.CanProtocol.CAN_FD
        if fd:
            if not self._capabilities["fd"]:
                raise can.CanOperationError("device does not advertise CAN FD support")
            if self._data_timing is None:
                self._data_timing = _calculate_timing(
                    self._capabilities["clock_hz"], self._data_bitrate,
                    self._data_sample_point, self._capabilities["data"])
        with self._io_lock:
            # A newly opened host handle does not imply that MCAN was closed:
            # USB configuration and CAN controller state have independent
            # lifetimes.  Force the firmware to observe RESET before applying
            # the next session's timing/mode, even when this Bus has not itself
            # called start() yet.
            self._protocol_device.stop()
            if hasattr(self._protocol_device, "host_format"):
                self._protocol_device.host_format()
            self._protocol_device.bittiming(*self._nominal_timing)
            flags = self.DEVICE_MODULE.MODE_LOOPBACK if self._loopback else 0
            if fd:
                self._protocol_device.bittiming(*self._data_timing, data=True)
                flags |= self.DEVICE_MODULE.MODE_FD
                self._can_protocol = can.CanProtocol.CAN_FD
            else:
                self._can_protocol = can.CanProtocol.CAN_20
            if self._bus_load_setting is not None:
                self._protocol_device.set_bus_load(self._bus_load_setting)
            if self._termination_setting is not None:
                self._protocol_device.set_termination(
                    self._termination_setting)
        self._start_flags = flags

    def get_bit_timing(self) -> dict[str, Any]:
        """Return host-selected timing, not hardware register readback."""
        clock = self._capabilities["clock_hz"]
        def describe(timing):
            if timing is None:
                return None
            brp, tseg1, tseg2, sjw = timing
            tq = 1 + tseg1 + tseg2
            return dict(brp=brp, tseg1=tseg1, tseg2=tseg2, sjw=sjw,
                        bitrate=clock / (brp * tq),
                        sample_point=100 * (1 + tseg1) / tq)
        return {"nominal": describe(self._nominal_timing),
                "data": describe(self._data_timing)}

    def get_capabilities(self) -> dict[str, Any]:
        """Return cached clock, FD feature and nominal/data timing limits."""
        return dict(self._capabilities)

    def get_device_info(self) -> dict[str, Any]:
        if self._device_info is None:
            if self._started:
                raise can.CanOperationError(
                    "stop channel before querying device information")
            with self._io_lock:
                self._device_info = self._protocol_device.info()
        return dict(self._device_info)

    def set_termination(self, enabled: bool) -> None:
        if self._started:
            raise can.CanOperationError(
                "stop channel before changing termination")
        with self._io_lock:
            self._protocol_device.set_termination(enabled)

    def get_termination(self) -> bool:
        with self._io_lock:
            return self._protocol_device.get_termination()

    def set_bus_load_reporting(self, enabled: bool) -> None:
        if self._started:
            raise can.CanOperationError(
                "stop channel before changing bus-load reporting")
        with self._io_lock:
            self._protocol_device.set_bus_load(enabled)

    @property
    def state(self) -> can.BusState:
        """Map the firmware controller state to python-can's coarse model.

        Error-warning maps to ACTIVE, error-passive to PASSIVE and bus-off
        to ERROR. The value is cached telemetry, not a synchronous state query.
        Use :meth:`get_berr_counter` for cached RX/TX error counters.
        """
        firmware_state = getattr(self._protocol_device, "can_state", None)
        if firmware_state == self.DEVICE_MODULE.CAN_STATE_BUS_OFF:
            return can.BusState.ERROR
        if firmware_state == self.DEVICE_MODULE.CAN_STATE_ERROR_PASSIVE:
            return can.BusState.PASSIVE
        return can.BusState.ACTIVE

    def get_berr_counter(self) -> dict[str, int]:
        """Return the most recently reported RX/TX error counters.

        Updated as a side effect of :meth:`recv`/the background RX thread
        parsing STATE/BERR event frames; does not issue USB traffic itself.
        """
        return dict(getattr(self._protocol_device, "bec", {"rxerr": 0, "txerr": 0}))

    def get_bus_load(self) -> dict[str, int] | None:
        """Return the most recent firmware LOAD event, if reporting is on."""
        value = getattr(self._protocol_device, "last_bus_load", None)
        return None if value is None else dict(value)

    def get_usb_stats(self) -> dict[str, int]:
        """Return interface-local USB transfer counters for diagnostics."""
        stats = self._usb_interface.stats()
        with self._rx_condition:
            stats["app_rx_queued"] = len(self._rx_queue)
            stats["app_rx_queue_size"] = self._rx_queue_size
            stats["app_rx_dropped"] = self._rx_dropped
            stats["app_rx_overflow"] = self._rx_overflow
        return stats

    def wait_for_usb_rx(self, previous: int, timeout: float) -> bool:
        """Wait for one new bulk-IN completion on this interface."""
        return self._usb_interface.wait_rx_completed(previous, timeout)


class vcan_usb_bus(UsbCanBus):
    """python-can backend for the VCAN USB protocol."""

    DEVICE_MODULE = protocol
    INTERFACE_NAME = "vcan_usb"
