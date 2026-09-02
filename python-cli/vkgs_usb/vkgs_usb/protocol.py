"""VKGS/gs_usb wire protocol implementation (firmware V_0_0_2)."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
import struct
import time
from typing import Any

import usb.core
import usb.util


# USB identity and transfer defaults.
VID = 0x1D50
PID = 0x606F
TIMEOUT_MS = 2_000

# Firmware scheduling delays validated against V_0_0_2. These are firmware
# constraints, not tunables: the device's main loop only drains its USB
# staging FIFO on a fixed schedule, so a shorter delay here does not speed
# anything up, it just risks racing a request the firmware has not applied
# yet. BULK_OUT_READY_S + CAN_TX_SETTLE_S together cap steady-state TX
# throughput at roughly 1 / (BULK_OUT_READY_S + CAN_TX_SETTLE_S) ~= 66
# frames/s per channel, and MODE_TRANSITION_SETTLE_S adds ~2x its value
# (300 ms) to every start()/stop() cycle; see docs/PythonCAN使用手册.md.
CONTROL_SETTLE_S = 0.005
BULK_OUT_READY_S = 0.005
CAN_TX_SETTLE_S = 0.01
MODE_TRANSITION_SETTLE_S = 0.15

# Standard gs_usb and firmware-extension request identifiers.
BREQ_HOST_FORMAT = 0
BREQ_BITTIMING = 1
BREQ_MODE = 2
BREQ_DATA_BITTIMING = 10
BREQ_SET_TERMINATION = 12
BREQ_GET_TERMINATION = 13
BREQ_GET_STATE = 14
BREQ_IDENTIFY = 7
BREQ_BSP_DEVICE_INFO = 33
BREQ_USB_MODE = 34
BREQ_CAN_BUS_LOAD = 36
BREQ_CAN_TERMINATION = 37

# vkgs_usb_state_ext.state / vkgs_usb_can_state (see vkgs_usb.h); numerically
# identical to the vcan_usb protocol's state enum (shared firmware core).
CAN_STATE_ERROR_ACTIVE = 0
CAN_STATE_ERROR_WARNING = 1
CAN_STATE_ERROR_PASSIVE = 2
CAN_STATE_BUS_OFF = 3
CAN_STATE_STOPPED = 4
CAN_STATE_SLEEPING = 5

# vkgs_usb_berr_ext.error_code (protocol-violation reason).
ERROR_CODE_STUFF = 1
ERROR_CODE_FORM = 2
ERROR_CODE_ACK = 3
ERROR_CODE_BIT1 = 4
ERROR_CODE_BIT0 = 5
ERROR_CODE_CRC = 6

# Device and USB personality modes.
MODE_RESET = 0
MODE_START = 1
USB_MODE_VCAN = 0
USB_MODE_PEAK_CAN = 1
USB_MODE_GS_USB = 2

# CAN controller mode and frame flags.
MODE_LOOPBACK = 1 << 1
MODE_HW_TIMESTAMP = 1 << 4
MODE_FD = 1 << 8
MODE_FD_NON_ISO = 1 << 9
FLAG_FD = 1 << 1
FLAG_BRS = 1 << 2
FLAG_ESI = 1 << 3
CAN_EFF = 1 << 31
CAN_RTR = 1 << 30
CAN_ERR = 1 << 29
CAN_ID_MASK = 0x1FFFFFFF

# Device-to-host frame identifiers.
ECHO_RX = 0xFFFFFFFF
ECHO_LOAD = 0xA3C95E3D
ECHO_STATE = 0xA4C95E3D
ECHO_BERR = 0xA6C95E3D

# On-wire structures.
HOST_HEADER = struct.Struct("<IIBBBB")
DEVICE_INFO_FIELDS = struct.Struct("<II4I4I")
# vkgs_usb_state_ext / vkgs_usb_berr_ext / vkgs_usb_load share this 8-byte
# prefix: echo_id, channel, reserved, flags. NOTE this differs from
# HOST_HEADER's layout (echo_id, can_id, dlc, channel, flags, reserved),
# which only applies to CAN data/TX-echo frames.
EVENT_HEADER = struct.Struct("<IBBH")
# vkgs_usb_state_ext fields following EVENT_HEADER: timestamp_us, state,
# rxerr, txerr.
CAN_STATE_FIELDS = struct.Struct("<QIII")
# vkgs_usb_berr_ext fields following EVENT_HEADER: error_flag, error_code,
# rx_error_count, tx_error_count, error_logging_count, 3 reserved bytes.
DEVICE_BERR_FIELDS = struct.Struct("<BBBBB3x")
FD_DLC_LENGTHS = (0, 1, 2, 3, 4, 5, 6, 7, 8, 12, 16, 20, 24, 32, 48, 64)

_CONTROL_REQUEST_OUT = 0x41
_CONTROL_REQUEST_IN = 0xC1
_HOST_BYTE_ORDER = 0x0000BEEF
_BULK_READ_SIZE = 512
_CLASSIC_FRAME_WIDTH = 8
_FD_FRAME_WIDTH = 64
_STATE_FRAME_SIZE = 28
_BUS_ERROR_FRAME_SIZE = 16
_BUS_LOAD_FRAME_SIZE = 28
_TIMESTAMP_SIZE = 8


class ProtocolError(RuntimeError):
    """Raised when a USB transfer violates the gs_usb wire protocol."""


def length_to_dlc(length: int, fd: bool) -> int:
    """Convert a payload length to a classic CAN or CAN FD DLC."""
    maximum_length = _FD_FRAME_WIDTH if fd else _CLASSIC_FRAME_WIDTH
    if length < 0 or length > maximum_length:
        raise ValueError(f"payload exceeds {maximum_length} bytes")
    if not fd:
        return length
    return next(
        dlc for dlc, payload_length in enumerate(FD_DLC_LENGTHS)
        if payload_length >= length
    )


def dlc_to_length(dlc: int, fd: bool) -> int:
    """Convert the low four DLC bits to a payload length."""
    normalized_dlc = dlc & 0x0F
    if fd:
        return FD_DLC_LENGTHS[normalized_dlc]
    return min(normalized_dlc, _CLASSIC_FRAME_WIDTH)


@dataclass
class CanFrame:
    """Protocol-neutral CAN frame exchanged with the python-can adapter."""

    can_id: int
    data: bytes
    channel: int = 0
    fd: bool = False
    brs: bool = False
    extended: bool = False
    rtr: bool = False
    timestamp_us: int = 0


def list_devices(vid: int = VID, pid: int = PID) -> list[Any]:
    """Return all PyUSB devices matching the requested USB identity."""
    devices = usb.core.find(find_all=True, idVendor=vid, idProduct=pid)
    return list(devices or [])


def _bulk_endpoints(interface_descriptor: Any) -> tuple[Any, Any]:
    """Return the bulk IN and OUT endpoints from one USB interface."""
    bulk_endpoints = [
        endpoint
        for endpoint in interface_descriptor
        if usb.util.endpoint_type(endpoint.bmAttributes)
        == usb.util.ENDPOINT_TYPE_BULK
    ]
    endpoint_in = next(
        endpoint
        for endpoint in bulk_endpoints
        if usb.util.endpoint_direction(endpoint.bEndpointAddress)
        == usb.util.ENDPOINT_IN
    )
    endpoint_out = next(
        endpoint
        for endpoint in bulk_endpoints
        if usb.util.endpoint_direction(endpoint.bEndpointAddress)
        == usb.util.ENDPOINT_OUT
    )
    return endpoint_in, endpoint_out


class CanDevice:
    """Low-level gs_usb channel over a claimed USB interface.

    ``device`` may be a regular PyUSB device or the asynchronous adapter from
    :mod:`vkgs_usb.transport`. In the latter case, the transport owns the USB
    interface claim and this class only implements protocol framing.
    """

    def __init__(
        self,
        device: Any = None,
        channel: int = 0,
        timeout_ms: int = TIMEOUT_MS,
        control_device: Any = None,
    ) -> None:
        self.dev = device or usb.core.find(idVendor=VID, idProduct=PID)
        if self.dev is None:
            raise ProtocolError(f"device {VID:04x}:{PID:04x} not found")

        self.control_dev = control_device or self.dev
        self.channel = channel
        self.interface = channel
        self.timeout = timeout_ms
        self.timestamps_enabled = False
        self.fd_mode_enabled = False
        self._rx_tail = b""
        # Kept current by STATE/BERR event frames (see parse_bulk()); lets
        # callers read bus/error state without an extra USB round trip.
        self.can_state = CAN_STATE_STOPPED
        self.bec = {"rxerr": 0, "txerr": 0}
        self.last_berr: dict[str, int] | None = None

        # A composite device is configured once, not once per CAN interface.
        # Reissuing SET_CONFIGURATION after another interface has been claimed
        # resets its endpoints and can strand a pending firmware OUT request.
        if hasattr(self.dev, "start_rx"):
            self.ep_in = self.dev.ep_in
            self.ep_out = self.dev.ep_out
            self._claimed = False  # The async transport owns the claim.
            return

        try:
            configuration = self.dev.get_active_configuration()
        except usb.core.USBError:
            self.dev.set_configuration()
            configuration = self.dev.get_active_configuration()

        interface_descriptor = configuration[(self.interface, 0)]
        usb.util.claim_interface(self.dev, self.interface)
        self._claimed = True
        self.ep_in, self.ep_out = _bulk_endpoints(interface_descriptor)

    def close(self) -> None:
        """Release resources owned by a direct PyUSB session."""
        if self._claimed:
            usb.util.release_interface(self.dev, self.interface)
            self._claimed = False
        if not hasattr(self.dev, "start_rx"):
            usb.util.dispose_resources(self.dev)

    def __enter__(self) -> "CanDevice":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()

    def set_control(self, request: int, payload: bytes) -> None:
        """Send one channel-scoped vendor control request."""
        try:
            bytes_written = self.control_dev.ctrl_transfer(
                _CONTROL_REQUEST_OUT,
                request,
                self.channel,
                self.channel,
                payload,
                timeout=self.timeout,
            )
        except usb.core.USBTimeoutError as exc:
            raise ProtocolError(
                f"control OUT timeout: channel={self.channel}, "
                f"request={request}"
            ) from exc
        if bytes_written != len(payload):
            raise ProtocolError(
                f"short control write: {bytes_written}/{len(payload)}"
            )

        # Firmware applies these requests from its main loop. The delay is a
        # protocol requirement; logging must not accidentally provide it.
        time.sleep(CONTROL_SETTLE_S)

    def get_control(self, request: int, size: int, value: int = 0) -> bytes:
        """Read one channel-scoped vendor response."""
        try:
            response = bytes(
                self.control_dev.ctrl_transfer(
                    _CONTROL_REQUEST_IN,
                    request,
                    value,
                    self.channel,
                    size,
                    timeout=self.timeout,
                )
            )
        except usb.core.USBTimeoutError as exc:
            raise ProtocolError(
                f"control IN timeout: channel={self.channel}, "
                f"request={request}"
            ) from exc
        if len(response) != size:
            raise ProtocolError(
                f"short control response: {len(response)}/{size}"
            )
        return response

    def info(self) -> dict[str, Any]:
        """Read firmware, hardware, and device identity information."""
        response = self.get_control(BREQ_BSP_DEVICE_INFO, 40)
        values = DEVICE_INFO_FIELDS.unpack(response)
        return {
            "sw_version": values[0],
            "hw_version": values[1],
            "uid": list(values[2:6]),
            "uuid": list(values[6:10]),
        }

    def host_format(self) -> None:
        """Restore the gs_usb host byte-order handshake."""
        self.set_control(
            BREQ_HOST_FORMAT,
            struct.pack("<I", _HOST_BYTE_ORDER),
        )

    def version(self) -> dict[str, int]:
        device_info = self.info()
        return {
            "sw_version": device_info["sw_version"],
            "hw_version": device_info["hw_version"],
        }

    def usb_mode(self, mode: int) -> None:
        """Persist a new USB personality in firmware."""
        valid_modes = (USB_MODE_VCAN, USB_MODE_PEAK_CAN, USB_MODE_GS_USB)
        if mode not in valid_modes:
            raise ValueError(
                "USB mode must be 0 (vcan), 1 (peak_can), or 2 (gs_usb)"
            )
        self.set_control(BREQ_USB_MODE, struct.pack("<B", mode))

    def identify(self, enabled: bool) -> None:
        """Start or stop the device's own LED-blink identify pattern."""
        self.set_control(BREQ_IDENTIFY, struct.pack("<I", int(enabled)))

    def set_termination(self, enabled: bool) -> None:
        payload = struct.pack("<I", int(enabled))
        try:
            self.set_control(BREQ_CAN_TERMINATION, payload)
        except usb.core.USBError:
            # Older gs_usb firmware exposes the standard request pair only.
            self.set_control(BREQ_SET_TERMINATION, payload)

    def get_termination(self) -> bool:
        try:
            response = self.get_control(BREQ_CAN_TERMINATION, 4)
        except usb.core.USBError:
            response = self.get_control(BREQ_GET_TERMINATION, 4)
        return bool(struct.unpack("<I", response)[0])

    def set_bus_load(self, enabled: bool) -> None:
        self.set_control(BREQ_CAN_BUS_LOAD, struct.pack("<I", int(enabled)))

    def start(self, flags: int = 0) -> None:
        self.set_control(BREQ_MODE, struct.pack("<II", MODE_START, flags))
        self.timestamps_enabled = bool(flags & MODE_HW_TIMESTAMP)
        self.fd_mode_enabled = bool(flags & MODE_FD)

        # EP0 completion updates bus_active first. The firmware opens the CAN
        # controller from its main loop, so data traffic must wait past the
        # 100 ms state period.
        time.sleep(MODE_TRANSITION_SETTLE_S)

    def stop(self) -> None:
        self.set_control(BREQ_MODE, struct.pack("<II", MODE_RESET, 0))
        self.timestamps_enabled = False
        self.fd_mode_enabled = False
        self._rx_tail = b""

        # hal_can_bus_close() clears the CAN rings and USB TX staging FIFO
        # after the control request. A following START must not race it.
        time.sleep(MODE_TRANSITION_SETTLE_S)

    def bittiming(
        self,
        brp: int,
        tseg1: int,
        tseg2: int,
        sjw: int,
        data: bool = False,
    ) -> None:
        """Configure nominal or CAN FD data-phase bit timing."""
        if min(brp, tseg1, tseg2, sjw) < 1:
            raise ValueError("timing values must be >= 1")
        payload = struct.pack(
            "<IIIII",
            0,
            tseg1 - 1,
            tseg2 - 1,
            sjw - 1,
            brp - 1,
        )
        request = BREQ_DATA_BITTIMING if data else BREQ_BITTIMING
        self.set_control(request, payload)

    def send(self, frame: CanFrame, echo_id: int = 0) -> None:
        """Encode and write one CAN frame."""
        # Match vkgs_usb.c exactly: classic-only mode allocates 8 data bytes.
        # Once FD mode is active, every host-frame allocation remains 64 bytes,
        # including allocations carrying an individual classic frame.
        frame_width = (
            _FD_FRAME_WIDTH
            if self.fd_mode_enabled or frame.fd
            else _CLASSIC_FRAME_WIDTH
        )
        dlc = length_to_dlc(len(frame.data), frame.fd)
        padded_length = dlc_to_length(dlc, frame.fd)

        can_id = frame.can_id
        can_id |= CAN_EFF if frame.extended else 0
        can_id |= CAN_RTR if frame.rtr else 0
        flags = FLAG_FD if frame.fd else 0
        flags |= FLAG_BRS if frame.brs else 0

        packet = HOST_HEADER.pack(
            echo_id,
            can_id,
            dlc,
            self.channel,
            flags,
            0,
        )
        packet += frame.data.ljust(padded_length, b"\0").ljust(
            frame_width, b"\0"
        )

        # The device rearms OUT from its main-loop flush path, independently
        # of successful control-transfer completion.
        time.sleep(BULK_OUT_READY_S)
        try:
            bytes_written = self.dev.write(
                self.ep_out.bEndpointAddress,
                packet,
                timeout=self.timeout,
            )
        except usb.core.USBError as exc:
            if not isinstance(exc, usb.core.USBTimeoutError):
                raise
            raise ProtocolError(
                f"bulk OUT timeout: channel={self.channel}, "
                f"endpoint=0x{self.ep_out.bEndpointAddress:02x}"
            ) from exc
        if bytes_written != len(packet):
            raise ProtocolError(
                f"short bulk OUT write: {bytes_written}/{len(packet)}"
            )
        time.sleep(CAN_TX_SETTLE_S)

    def recv(self, timeout_ms: int | None = None) -> Iterator[CanFrame]:
        """Read one USB transfer and yield every complete CAN frame in it."""
        read_timeout = self.timeout if timeout_ms is None else timeout_ms
        transfer = bytes(
            self.dev.read(
                self.ep_in.bEndpointAddress,
                _BULK_READ_SIZE,
                timeout=read_timeout,
            )
        )
        yield from self.parse_bulk(transfer)

    def _handle_state_event(self, buffer: bytes, offset: int) -> None:
        """Decode a vkgs_usb_state_ext frame into cached controller state."""
        _echo_id, _channel, _reserved, _flags = EVENT_HEADER.unpack_from(
            buffer, offset
        )
        _timestamp_us, state, rxerr, txerr = CAN_STATE_FIELDS.unpack_from(
            buffer, offset + EVENT_HEADER.size
        )
        self.can_state = state
        self.bec = {"rxerr": rxerr, "txerr": txerr}

    def _handle_berr_event(self, buffer: bytes, offset: int) -> None:
        """Decode a vkgs_usb_berr_ext frame into cached error state."""
        error_flag, error_code, rxerr, txerr, log_count = (
            DEVICE_BERR_FIELDS.unpack_from(buffer, offset + EVENT_HEADER.size)
        )
        self.bec = {"rxerr": rxerr, "txerr": txerr}
        self.last_berr = {
            "error_flag": error_flag,
            "error_code": error_code,
            "error_logging_count": log_count,
        }

    def parse_bulk(self, transfer: bytes) -> Iterator[CanFrame]:
        """Decode a USB transfer, retaining an incomplete trailing frame."""
        buffer = self._rx_tail + transfer
        self._rx_tail = b""
        offset = 0

        while offset < len(buffer):
            remaining = len(buffer) - offset
            if remaining < 4:
                if any(buffer[offset:]):
                    self._rx_tail = buffer[offset:]
                break

            echo_id = struct.unpack_from("<I", buffer, offset)[0]
            # Four zero bytes terminate a bundle. Remaining bytes are alignment
            # padding or stale FIFO data and must be ignored.
            if echo_id == 0:
                break
            if remaining < HOST_HEADER.size:
                self._rx_tail = buffer[offset:]
                break

            # STATE/BERR/LOAD event frames use a different field layout than
            # CAN data/TX-echo frames (EVENT_HEADER vs HOST_HEADER), so only
            # unpack with the struct matching this echo_id.
            if echo_id == ECHO_STATE:
                frame_size = _STATE_FRAME_SIZE
            elif echo_id == ECHO_BERR:
                frame_size = _BUS_ERROR_FRAME_SIZE
            elif echo_id == ECHO_LOAD:
                frame_size = _BUS_LOAD_FRAME_SIZE
            elif echo_id == ECHO_RX:
                _echo_id, raw_can_id, dlc, channel, flags, _reserved = (
                    HOST_HEADER.unpack_from(buffer, offset)
                )
                payload_width = (
                    _FD_FRAME_WIDTH if flags & FLAG_FD else _CLASSIC_FRAME_WIDTH
                )
                frame_size = HOST_HEADER.size + payload_width
                if self.timestamps_enabled:
                    frame_size += _TIMESTAMP_SIZE
            else:
                break

            if offset + frame_size > len(buffer):
                self._rx_tail = buffer[offset:]
                break

            if echo_id == ECHO_RX:
                is_fd = bool(flags & FLAG_FD)
                data_length = dlc_to_length(dlc, is_fd)
                data_offset = offset + HOST_HEADER.size
                timestamp_us = 0
                if self.timestamps_enabled:
                    timestamp_us = struct.unpack_from(
                        "<Q", buffer, offset + frame_size - _TIMESTAMP_SIZE
                    )[0]
                yield CanFrame(
                    can_id=raw_can_id & CAN_ID_MASK,
                    data=buffer[data_offset:data_offset + data_length],
                    channel=channel,
                    fd=is_fd,
                    brs=bool(flags & FLAG_BRS),
                    extended=bool(raw_can_id & CAN_EFF),
                    rtr=bool(raw_can_id & CAN_RTR),
                    timestamp_us=timestamp_us,
                )
            elif echo_id == ECHO_STATE:
                self._handle_state_event(buffer, offset)
            elif echo_id == ECHO_BERR:
                self._handle_berr_event(buffer, offset)
            offset += frame_size
