"""type 0 VCAN private wire protocol implementation (firmware V_0_0_3)."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
import struct
from typing import Any

import usb.core
import usb.util


# USB identity and transfer defaults.
VID = 0x1D50
PID = 0x6080
TIMEOUT_MS = 1_000

# Private protocol message identifiers.
ECHO_TX = 0xA1C95E3D
ECHO_RX = 0xA2C95E3D
ECHO_LOAD = 0xA3C95E3D
ECHO_STATE = 0xA4C95E3D
ECHO_SETUP = 0xA5C95E3D

# Vendor request identifiers.
BREQ_HOST_FORMAT = 0
BREQ_MODE = 1
BREQ_BERR = 2
BREQ_CAN_STATE = 3
BREQ_BT_CONST = 16
BREQ_BT_CONST_EXT = 17
BREQ_BITTIMING = 24
BREQ_DATA_BITTIMING = 25
BREQ_BSP_DEVICE_INFO = 33
BREQ_USB_MODE = 34
BREQ_CAN_FILTERS = 35
BREQ_CAN_BUS_LOAD = 36
BREQ_CAN_TERMINATION = 37

# vcan_usb_device_state.state / vcan_usb_can_state (see vcan_usb.h).
CAN_STATE_ERROR_ACTIVE = 0
CAN_STATE_ERROR_WARNING = 1
CAN_STATE_ERROR_PASSIVE = 2
CAN_STATE_BUS_OFF = 3
CAN_STATE_STOPPED = 4
CAN_STATE_SLEEPING = 5

# vcan_usb_device_berr.error_code (protocol-violation reason).
ERROR_CODE_NONE = 0
ERROR_CODE_STUFF = 1
ERROR_CODE_FORM = 2
ERROR_CODE_ACK = 3
ERROR_CODE_BIT1 = 4
ERROR_CODE_BIT0 = 5
ERROR_CODE_CRC = 6
ERROR_CODE_NO_CHANGE = 7
ERROR_CODE_UNKNOWN = 127

# Device and USB personality modes.
MODE_RESET = 0
MODE_START = 1
USB_MODE_VCAN = 0
USB_MODE_PEAK_CAN = 1
USB_MODE_GS_USB = 2

# CAN controller mode and frame flags.
MODE_LOOPBACK = 1 << 1
MODE_FD = 1 << 8
MODE_FD_NON_ISO = 1 << 9
FLAG_OVERFLOW = 1 << 0
FLAG_FD = 1 << 1
FLAG_BRS = 1 << 2
FLAG_ESI = 1 << 3
FLAG_EFF = 1 << 4
FLAG_RTR = 1 << 5
FLAG_ERR = 1 << 6

# On-wire structures.
HEADER = struct.Struct("<IHH")
CAN_FRAME_FIELDS = struct.Struct("<IB3xQ")
DEVICE_INFO_FIELDS = struct.Struct("<II4I4I")
# vcan_usb_device_state fields following HEADER: timestamp_us, state, rxerr, txerr.
CAN_STATE_FIELDS = struct.Struct("<QIII")
# vcan_usb_device_berr fields following HEADER: error_flag, error_code,
# rx_error_count, tx_error_count, error_logging_count, 3 reserved bytes.
DEVICE_BERR_FIELDS = struct.Struct("<BBBBB3x")
DEVICE_LOAD_FIELDS = struct.Struct("<QHHII")
FD_DLC_LENGTHS = (0, 1, 2, 3, 4, 5, 6, 7, 8, 12, 16, 20, 24, 32, 48, 64)

_CONTROL_REQUEST_OUT = 0x41
_CONTROL_REQUEST_IN = 0xC1
_CONTROL_RESPONSE_FLAG = 0x80
_OPCODE_SIZE_MASK = 0x0FFF
_OPCODE_CHANNEL_SHIFT = 12
_BULK_READ_SIZE = 512
_CLASSIC_FRAME_WIDTH = 8
_FD_FRAME_WIDTH = 64
# HEADER + CAN_FRAME_FIELDS: fixed prefix of every RX/TX data frame, before
# the variable-width payload.
_HEADER_AND_FRAME_FIELDS_SIZE = HEADER.size + CAN_FRAME_FIELDS.size


class ProtocolError(RuntimeError):
    """Raised when a USB transfer violates the VCAN wire protocol."""


def opcode(channel: int, size: int) -> int:
    """Pack the channel and transfer size into a VCAN opcode."""
    return ((channel & 0x0F) << _OPCODE_CHANNEL_SHIFT) | (
        size & _OPCODE_SIZE_MASK
    )


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
    esi: bool = False
    extended: bool = False
    rtr: bool = False
    error: bool = False
    overflow: bool = False
    timestamp_us: int = 0
    # Remote frames carry a requested DLC but no data.
    dlc: int | None = None


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
    """Low-level VCAN channel over a claimed USB interface.

    ``device`` may be a regular PyUSB device or the asynchronous adapter from
    :mod:`vcan_usb.transport`. In the latter case, the transport owns the USB
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

        self.channel = channel
        self.interface = channel
        self.timeout = timeout_ms
        self.control_dev = control_device or self.dev
        self._rx_tail = b""
        # Kept current by STATE/BERR event frames (see parse_bulk()); lets
        # callers read bus/error state without an extra USB round trip.
        self.can_state = CAN_STATE_STOPPED
        self.bec = {"rxerr": 0, "txerr": 0}
        self.last_berr: dict[str, int] | None = None
        self.last_bus_load: dict[str, int] | None = None

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

    def _setup_packet(self, request: int, payload: bytes = b"") -> bytes:
        packet_size = HEADER.size + len(payload)
        header = HEADER.pack(
            ECHO_SETUP,
            opcode(self.channel, packet_size),
            request,
        )
        return header + payload

    def set_control(self, request: int, payload: bytes) -> None:
        """Send one channel-scoped vendor control request."""
        packet = self._setup_packet(request, payload)
        try:
            bytes_written = self.control_dev.ctrl_transfer(
                _CONTROL_REQUEST_OUT,
                request,
                0,
                self.channel,
                packet,
                timeout=self.timeout,
            )
        except usb.core.USBTimeoutError as exc:
            raise ProtocolError(
                f"control OUT timeout: channel={self.channel}, "
                f"request={request}"
            ) from exc
        if bytes_written != len(packet):
            raise ProtocolError(
                f"short control write: {bytes_written}/{len(packet)}"
            )

    def get_control(self, request: int, size: int, value: int = 0) -> bytes:
        """Read and validate one channel-scoped vendor response."""
        expected_size = HEADER.size + size
        try:
            response = bytes(
                self.control_dev.ctrl_transfer(
                    _CONTROL_REQUEST_IN,
                    request | _CONTROL_RESPONSE_FLAG,
                    value,
                    self.channel,
                    expected_size,
                    timeout=self.timeout,
                )
            )
        except usb.core.USBTimeoutError as exc:
            raise ProtocolError(
                f"control IN timeout: channel={self.channel}, "
                f"request={request}"
            ) from exc

        if len(response) != expected_size:
            raise ProtocolError(
                f"short control response: {len(response)}/{expected_size}"
            )
        echo_id, response_opcode, response_flags = HEADER.unpack_from(response)
        if (
            echo_id != ECHO_SETUP
            or (response_flags & 0x7F) != request
            or (response_opcode & _OPCODE_SIZE_MASK) != len(response)
        ):
            raise ProtocolError("invalid setup response")
        return response[HEADER.size:]

    def capabilities(self) -> dict[str, Any]:
        """Read channel capabilities rather than identifying an MCU by version."""
        values = struct.unpack("<10I", self.get_control(BREQ_BT_CONST, 40))
        feature, clock_hz = values[:2]
        nominal = values[2:]
        data = None
        if feature & (1 << 8):
            if not feature & (1 << 10):
                raise ProtocolError("FD advertised without extended bit-timing limits")
            extended = struct.unpack("<18I", self.get_control(BREQ_BT_CONST_EXT, 72))
            if extended[1] != clock_hz:
                raise ProtocolError("inconsistent nominal/data clock")
            data = extended[10:]
        return {"feature": feature, "clock_hz": clock_hz,
                "fd": bool(feature & (1 << 8)), "nominal": nominal, "data": data}

    def info(self) -> dict[str, Any]:
        """Read firmware, hardware, and device identity information."""
        response = self.get_control(BREQ_BSP_DEVICE_INFO, 40)
        values = DEVICE_INFO_FIELDS.unpack(response)
        sw_version, raw_hw_version = values[:2]
        hw_flags = raw_hw_version >> 24 & 0x01
        hw_version = raw_hw_version & 0xFFFF
        max_packet_size = int(getattr(self.ep_out, "wMaxPacketSize", 0))
        usb_speed = (
            "unknown" if not max_packet_size else
            "SS" if max_packet_size > 512 else
            "HS" if max_packet_size > 64 else "FS"
        )
        result = {
            "sw_version": sw_version,
            "sw_version_text": (
                f"v{sw_version >> 16 & 0xff}."
                f"{sw_version >> 8 & 0xff}.{sw_version & 0xff}"
            ),
            "raw_hw_version": raw_hw_version,
            "ota_magic": (raw_hw_version >> 16) & 0xFF,
            "sw_version_full_text": "v" + ".".join(
                str((sw_version >> shift) & 0xFF) for shift in (24, 16, 8, 0)),
            "hw_version": hw_version,
            "hw_version_text": f"v{hw_version >> 8 & 0xff}.{hw_version & 0xff}",
            "hw_flags": hw_flags,
            "hw_isolated": bool(hw_flags & 0x01),
            "hw_revision_major": hw_version >> 8 & 0xFF,
            "hw_revision_minor": hw_version & 0xFF,
            "usb_speed": usb_speed,
            "uid": list(values[2:6]),
            "uuid": list(values[6:10]),
        }
        result["uid_hex"] = "".join(f"{word:08x}" for word in result["uid"])
        result["uuid_hex"] = "".join(f"{word:08x}" for word in result["uuid"])
        return result

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
        self.set_control(BREQ_USB_MODE, struct.pack("<B3x", mode))

    def set_termination(self, enabled: bool) -> None:
        self.set_control(BREQ_CAN_TERMINATION, struct.pack("<I", int(enabled)))

    def get_termination(self) -> bool:
        response = self.get_control(BREQ_CAN_TERMINATION, 4)
        return bool(struct.unpack("<I", response)[0])

    def set_bus_load(self, enabled: bool) -> None:
        self.set_control(BREQ_CAN_BUS_LOAD, struct.pack("<I", int(enabled)))

    def start(self, flags: int = 0) -> None:
        self.set_control(BREQ_MODE, struct.pack("<II", MODE_START, flags))

    def stop(self) -> None:
        self.set_control(BREQ_MODE, struct.pack("<II", MODE_RESET, 0))

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
        request = BREQ_DATA_BITTIMING if data else BREQ_BITTIMING
        payload = struct.pack(
            "<IIIII",
            0,
            tseg1 - 1,
            tseg2 - 1,
            sjw - 1,
            brp - 1,
        )
        self.set_control(request, payload)

    def send(self, frame: CanFrame, timeout_ms: int | None = None) -> None:
        """Encode and write one CAN frame."""
        flags = FLAG_FD if frame.fd else 0
        flags |= FLAG_BRS if frame.brs else 0
        flags |= FLAG_ESI if frame.esi else 0
        flags |= FLAG_EFF if frame.extended else 0
        flags |= FLAG_RTR if frame.rtr else 0
        flags |= FLAG_ERR if frame.error else 0

        frame_width = _FD_FRAME_WIDTH if frame.fd else _CLASSIC_FRAME_WIDTH
        if frame.fd and frame.rtr:
            raise ValueError("CAN FD does not support remote frames")
        dlc = length_to_dlc(
            frame.dlc if frame.rtr and frame.dlc is not None else len(frame.data),
            frame.fd,
        )
        padded_length = dlc_to_length(dlc, frame.fd)
        packet_size = _HEADER_AND_FRAME_FIELDS_SIZE + frame_width
        packet = HEADER.pack(
            ECHO_TX,
            opcode(self.channel, packet_size),
            flags,
        )
        packet += CAN_FRAME_FIELDS.pack(frame.can_id, dlc, 0)
        packet += frame.data.ljust(padded_length, b"\0").ljust(
            frame_width, b"\0"
        )

        try:
            bytes_written = self.dev.write(
                self.ep_out.bEndpointAddress,
                packet,
                timeout=self.timeout if timeout_ms is None else timeout_ms,
            )
        except usb.core.USBTimeoutError as exc:
            raise ProtocolError(
                f"bulk OUT timeout: channel={self.channel}, "
                f"endpoint=0x{self.ep_out.bEndpointAddress:02x}"
            ) from exc
        if bytes_written != len(packet):
            raise ProtocolError(
                f"short bulk OUT write: {bytes_written}/{len(packet)}"
            )

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

    def _handle_state_event(
        self, flags: int, buffer: bytes, offset: int, frame_size: int
    ) -> None:
        """Decode an ECHO_STATE frame, distinguished by ``flags`` (the
        original request id: BREQ_CAN_STATE or BREQ_BERR) into cached
        controller/error state.
        """
        payload_offset = offset + HEADER.size
        if (
            flags == BREQ_CAN_STATE
            and frame_size >= HEADER.size + CAN_STATE_FIELDS.size
        ):
            _timestamp_us, state, rxerr, txerr = CAN_STATE_FIELDS.unpack_from(
                buffer, payload_offset
            )
            self.can_state = state
            self.bec = {"rxerr": rxerr, "txerr": txerr}
        elif (
            flags == BREQ_BERR
            and frame_size >= HEADER.size + DEVICE_BERR_FIELDS.size
        ):
            error_flag, error_code, rxerr, txerr, log_count = (
                DEVICE_BERR_FIELDS.unpack_from(buffer, payload_offset)
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

        while offset + HEADER.size <= len(buffer):
            echo_id, frame_opcode, flags = HEADER.unpack_from(buffer, offset)
            if echo_id == 0:
                break

            frame_size = frame_opcode & _OPCODE_SIZE_MASK
            channel = (frame_opcode >> _OPCODE_CHANNEL_SHIFT) & 0x0F
            if frame_size < HEADER.size:
                raise ProtocolError("invalid bulk frame length")
            if offset + frame_size > len(buffer):
                self._rx_tail = buffer[offset:]
                break
            if channel != self.channel:
                raise ProtocolError(
                    f"bulk frame channel {channel} != interface {self.channel}"
                )

            if echo_id == ECHO_RX and frame_size >= _HEADER_AND_FRAME_FIELDS_SIZE:
                can_id, dlc, timestamp_us = CAN_FRAME_FIELDS.unpack_from(
                    buffer, offset + HEADER.size
                )
                is_fd = bool(flags & FLAG_FD)
                if is_fd and flags & FLAG_RTR:
                    raise ProtocolError("CAN FD remote frame is invalid")
                data_length = dlc_to_length(dlc, is_fd)
                data_offset = offset + _HEADER_AND_FRAME_FIELDS_SIZE
                expected_size = data_offset - offset + (
                    _FD_FRAME_WIDTH if is_fd else _CLASSIC_FRAME_WIDTH
                )
                if frame_size < expected_size:
                    raise ProtocolError("truncated CAN frame payload")
                yield CanFrame(
                    can_id=can_id,
                    data=buffer[data_offset:data_offset + data_length],
                    channel=channel,
                    fd=is_fd,
                    brs=bool(flags & FLAG_BRS),
                    esi=bool(flags & FLAG_ESI),
                    extended=bool(flags & FLAG_EFF),
                    rtr=bool(flags & FLAG_RTR),
                    error=bool(flags & FLAG_ERR),
                    overflow=bool(flags & FLAG_OVERFLOW),
                    timestamp_us=timestamp_us,
                )
            elif echo_id == ECHO_STATE:
                self._handle_state_event(flags, buffer, offset, frame_size)
            elif (
                echo_id == ECHO_LOAD
                and frame_size >= HEADER.size + DEVICE_LOAD_FIELDS.size
            ):
                timestamp_us, bus_load, _reserved, tx_ns, rx_ns = (
                    DEVICE_LOAD_FIELDS.unpack_from(buffer, offset + HEADER.size)
                )
                self.last_bus_load = {
                    "timestamp_us": timestamp_us,
                    "bus_load_q15": bus_load,
                    "tx_time_ns": tx_ns,
                    "rx_time_ns": rx_ns,
                }
            offset += frame_size

        trailing_data = buffer[offset:]
        if 0 < len(trailing_data) < HEADER.size and any(trailing_data):
            self._rx_tail = trailing_data
