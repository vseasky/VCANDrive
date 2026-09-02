"""Native WinUSB transport with one Windows handle per USB interface."""

from __future__ import annotations

import atexit
from collections import deque
import ctypes
from ctypes import wintypes
from dataclasses import dataclass
import re
import threading
import time
import uuid
from typing import Any, Callable

import usb.core

from .transport import InterfaceInfo, UsbPortPath, format_port_path


# WinUSB's standard device-interface class plus the GUID used by the packaged
# driver.  Enumerating both keeps the backend compatible with WCID and INF
# installations; duplicate paths are removed before device selection.
_INTERFACE_GUIDS = (
    "dee824ef-729b-4a0e-9c14-b7117d33a817",
    "c15b4308-04d3-11e6-b3ea-6057189e6443",
)
_USB_HUB_INTERFACE_GUID = "f18a0e88-c30c-11d0-8815-00a0c906bed8"

_DIGCF_PRESENT = 0x00000002
_DIGCF_DEVICEINTERFACE = 0x00000010
_ERROR_ACCESS_DENIED = 5
_ERROR_INSUFFICIENT_BUFFER = 122
_ERROR_IO_PENDING = 997
_ERROR_OPERATION_ABORTED = 995
_ERROR_NO_MORE_ITEMS = 259
_ERROR_NOT_FOUND = 1168
_ERROR_SEM_TIMEOUT = 121
_GENERIC_READ = 0x80000000
_GENERIC_WRITE = 0x40000000
_FILE_SHARE_READ = 0x00000001
_FILE_SHARE_WRITE = 0x00000002
_OPEN_EXISTING = 3
_FILE_FLAG_OVERLAPPED = 0x40000000
_WAIT_OBJECT_0 = 0
_WAIT_TIMEOUT = 258
_WAIT_FAILED = 0xFFFFFFFF
_INFINITE = 0xFFFFFFFF
_RX_WAIT_INTERVAL_MS = 100
_RX_STOP_TIMEOUT_S = 2.0

_INVALID_HANDLE_VALUE = wintypes.HANDLE(-1).value
_ULONG_PTR = ctypes.c_size_t
_UCHAR = ctypes.c_ubyte


class _GUID(ctypes.Structure):
    _fields_ = (
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", _UCHAR * 8),
    )

    @classmethod
    def parse(cls, value: str) -> "_GUID":
        return cls.from_buffer_copy(uuid.UUID(value).bytes_le)


class _SP_DEVICE_INTERFACE_DATA(ctypes.Structure):
    _fields_ = (
        ("cbSize", wintypes.DWORD),
        ("InterfaceClassGuid", _GUID),
        ("Flags", wintypes.DWORD),
        ("Reserved", _ULONG_PTR),
    )


class _SP_DEVINFO_DATA(ctypes.Structure):
    _fields_ = (
        ("cbSize", wintypes.DWORD),
        ("ClassGuid", _GUID),
        ("DevInst", wintypes.DWORD),
        ("Reserved", _ULONG_PTR),
    )


class _DEVPROPKEY(ctypes.Structure):
    _fields_ = (("fmtid", _GUID), ("pid", wintypes.DWORD))


class _OVERLAPPED(ctypes.Structure):
    _fields_ = (
        ("Internal", _ULONG_PTR),
        ("InternalHigh", _ULONG_PTR),
        ("Offset", wintypes.DWORD),
        ("OffsetHigh", wintypes.DWORD),
        ("hEvent", wintypes.HANDLE),
    )


class _WINUSB_SETUP_PACKET(ctypes.Structure):
    _pack_ = 1
    _fields_ = (
        ("RequestType", _UCHAR),
        ("Request", _UCHAR),
        ("Value", wintypes.WORD),
        ("Index", wintypes.WORD),
        ("Length", wintypes.WORD),
    )


_LOCATION_PATHS_KEY = _DEVPROPKEY(
    _GUID.parse("a45c254e-df1c-4efd-8020-67d146a850e0"),
    37,
)

_setupapi = ctypes.WinDLL("setupapi", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_winusb = ctypes.WinDLL("winusb", use_last_error=True)

_setupapi.SetupDiGetClassDevsW.argtypes = (
    ctypes.POINTER(_GUID),
    wintypes.LPCWSTR,
    wintypes.HWND,
    wintypes.DWORD,
)
_setupapi.SetupDiGetClassDevsW.restype = wintypes.HANDLE
_setupapi.SetupDiEnumDeviceInterfaces.argtypes = (
    wintypes.HANDLE,
    ctypes.POINTER(_SP_DEVINFO_DATA),
    ctypes.POINTER(_GUID),
    wintypes.DWORD,
    ctypes.POINTER(_SP_DEVICE_INTERFACE_DATA),
)
_setupapi.SetupDiEnumDeviceInterfaces.restype = wintypes.BOOL
_setupapi.SetupDiGetDeviceInterfaceDetailW.argtypes = (
    wintypes.HANDLE,
    ctypes.POINTER(_SP_DEVICE_INTERFACE_DATA),
    wintypes.LPVOID,
    wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD),
    ctypes.POINTER(_SP_DEVINFO_DATA),
)
_setupapi.SetupDiGetDeviceInterfaceDetailW.restype = wintypes.BOOL
_setupapi.SetupDiGetDevicePropertyW.argtypes = (
    wintypes.HANDLE,
    ctypes.POINTER(_SP_DEVINFO_DATA),
    ctypes.POINTER(_DEVPROPKEY),
    ctypes.POINTER(wintypes.DWORD),
    ctypes.POINTER(_UCHAR),
    wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD),
    wintypes.DWORD,
)
_setupapi.SetupDiGetDevicePropertyW.restype = wintypes.BOOL
_setupapi.SetupDiDestroyDeviceInfoList.argtypes = (wintypes.HANDLE,)
_setupapi.SetupDiDestroyDeviceInfoList.restype = wintypes.BOOL

_kernel32.CreateFileW.argtypes = (
    wintypes.LPCWSTR,
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.LPVOID,
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.HANDLE,
)
_kernel32.CreateFileW.restype = wintypes.HANDLE
_kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
_kernel32.CloseHandle.restype = wintypes.BOOL
_kernel32.CreateEventW.argtypes = (
    wintypes.LPVOID,
    wintypes.BOOL,
    wintypes.BOOL,
    wintypes.LPCWSTR,
)
_kernel32.CreateEventW.restype = wintypes.HANDLE
_kernel32.ResetEvent.argtypes = (wintypes.HANDLE,)
_kernel32.ResetEvent.restype = wintypes.BOOL
_kernel32.SetEvent.argtypes = (wintypes.HANDLE,)
_kernel32.SetEvent.restype = wintypes.BOOL
_kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
_kernel32.WaitForSingleObject.restype = wintypes.DWORD
_kernel32.CancelIoEx.argtypes = (
    wintypes.HANDLE,
    ctypes.POINTER(_OVERLAPPED),
)
_kernel32.CancelIoEx.restype = wintypes.BOOL

_winusb.WinUsb_Initialize.argtypes = (
    wintypes.HANDLE,
    ctypes.POINTER(wintypes.HANDLE),
)
_winusb.WinUsb_Initialize.restype = wintypes.BOOL
_winusb.WinUsb_Free.argtypes = (wintypes.HANDLE,)
_winusb.WinUsb_Free.restype = wintypes.BOOL
_winusb.WinUsb_ControlTransfer.argtypes = (
    wintypes.HANDLE,
    _WINUSB_SETUP_PACKET,
    ctypes.POINTER(_UCHAR),
    wintypes.ULONG,
    ctypes.POINTER(wintypes.ULONG),
    ctypes.POINTER(_OVERLAPPED),
)
_winusb.WinUsb_ControlTransfer.restype = wintypes.BOOL
_winusb.WinUsb_ReadPipe.argtypes = (
    wintypes.HANDLE,
    _UCHAR,
    ctypes.POINTER(_UCHAR),
    wintypes.ULONG,
    ctypes.POINTER(wintypes.ULONG),
    ctypes.POINTER(_OVERLAPPED),
)
_winusb.WinUsb_ReadPipe.restype = wintypes.BOOL
_winusb.WinUsb_WritePipe.argtypes = _winusb.WinUsb_ReadPipe.argtypes
_winusb.WinUsb_WritePipe.restype = wintypes.BOOL
_winusb.WinUsb_GetOverlappedResult.argtypes = (
    wintypes.HANDLE,
    ctypes.POINTER(_OVERLAPPED),
    ctypes.POINTER(wintypes.ULONG),
    wintypes.BOOL,
)
_winusb.WinUsb_GetOverlappedResult.restype = wintypes.BOOL


@dataclass(frozen=True)
class DeviceInterfacePath:
    """One present Windows device-interface path and its USB topology."""

    path: str
    port_path: UsbPortPath
    location_path: str = ""
    bus: int | None = None


@dataclass
class _RxTransfer:
    event: int
    overlapped: _OVERLAPPED
    buffer: Any
    immediate_length: int | None = None
    submitted: bool = False


def _usb_error(operation: str, error_code: int | None = None) -> usb.core.USBError:
    code = ctypes.get_last_error() if error_code is None else error_code
    detail = ctypes.FormatError(code).strip() if code else "unknown error"
    message = f"{operation} failed: WinError {code}: {detail}"
    if code in {_ERROR_SEM_TIMEOUT, _WAIT_TIMEOUT}:
        return usb.core.USBTimeoutError(message, code, 110)
    return usb.core.USBError(message, code, None)


def _location_paths(
    device_info_set: int,
    device_info: _SP_DEVINFO_DATA,
) -> list[str]:
    property_type = wintypes.DWORD()
    required_size = wintypes.DWORD()
    ctypes.set_last_error(0)
    _setupapi.SetupDiGetDevicePropertyW(
        device_info_set,
        ctypes.byref(device_info),
        ctypes.byref(_LOCATION_PATHS_KEY),
        ctypes.byref(property_type),
        None,
        0,
        ctypes.byref(required_size),
        0,
    )
    if ctypes.get_last_error() != _ERROR_INSUFFICIENT_BUFFER:
        return []
    buffer = (_UCHAR * required_size.value)()
    if not _setupapi.SetupDiGetDevicePropertyW(
        device_info_set,
        ctypes.byref(device_info),
        ctypes.byref(_LOCATION_PATHS_KEY),
        ctypes.byref(property_type),
        buffer,
        required_size.value,
        ctypes.byref(required_size),
        0,
    ):
        return []
    text = ctypes.wstring_at(
        ctypes.addressof(buffer), required_size.value // ctypes.sizeof(ctypes.c_wchar)
    )
    return [item for item in text.split("\0") if item]


def _usb_port_path(location_path: str) -> UsbPortPath:
    """Extract libusb-compatible hub port numbers from a Windows location."""
    segments = location_path.upper().split("#")
    try:
        root_index = next(
            index for index, segment in enumerate(segments)
            if segment.startswith("USBROOT(")
        )
    except StopIteration:
        return ()
    ports: list[int] = []
    for segment in segments[root_index + 1:]:
        if segment.startswith("USBMI("):
            break
        match = re.fullmatch(r"USB\((\d+)\)", segment)
        if match:
            ports.append(int(match.group(1)))
    return tuple(ports)


def _root_location_path(location_path: str) -> str:
    """Return the root-hub portion shared by one Windows USB topology."""
    segments = location_path.split("#")
    try:
        root_index = next(
            index for index, segment in enumerate(segments)
            if segment.upper().startswith("USBROOT(")
        )
    except StopIteration:
        return ""
    return "#".join(segments[:root_index + 1])


def _enumerate_guid(guid_text: str) -> list[DeviceInterfacePath]:
    interface_guid = _GUID.parse(guid_text)
    device_info_set = _setupapi.SetupDiGetClassDevsW(
        ctypes.byref(interface_guid),
        None,
        None,
        _DIGCF_PRESENT | _DIGCF_DEVICEINTERFACE,
    )
    if device_info_set == _INVALID_HANDLE_VALUE:
        raise _usb_error("SetupDiGetClassDevsW")
    interfaces: list[DeviceInterfacePath] = []
    try:
        member_index = 0
        while True:
            interface_data = _SP_DEVICE_INTERFACE_DATA()
            interface_data.cbSize = ctypes.sizeof(interface_data)
            if not _setupapi.SetupDiEnumDeviceInterfaces(
                device_info_set,
                None,
                ctypes.byref(interface_guid),
                member_index,
                ctypes.byref(interface_data),
            ):
                error_code = ctypes.get_last_error()
                if error_code == _ERROR_NO_MORE_ITEMS:
                    break
                raise _usb_error("SetupDiEnumDeviceInterfaces", error_code)
            member_index += 1

            required_size = wintypes.DWORD()
            device_info = _SP_DEVINFO_DATA()
            device_info.cbSize = ctypes.sizeof(device_info)
            _setupapi.SetupDiGetDeviceInterfaceDetailW(
                device_info_set,
                ctypes.byref(interface_data),
                None,
                0,
                ctypes.byref(required_size),
                ctypes.byref(device_info),
            )
            if ctypes.get_last_error() != _ERROR_INSUFFICIENT_BUFFER:
                raise _usb_error("SetupDiGetDeviceInterfaceDetailW")
            detail_buffer = ctypes.create_string_buffer(required_size.value)
            detail_cb_size = 8 if ctypes.sizeof(ctypes.c_void_p) == 8 else 6
            ctypes.cast(
                detail_buffer, ctypes.POINTER(wintypes.DWORD)
            ).contents.value = detail_cb_size
            if not _setupapi.SetupDiGetDeviceInterfaceDetailW(
                device_info_set,
                ctypes.byref(interface_data),
                detail_buffer,
                required_size.value,
                ctypes.byref(required_size),
                ctypes.byref(device_info),
            ):
                raise _usb_error("SetupDiGetDeviceInterfaceDetailW")
            path = ctypes.wstring_at(
                ctypes.addressof(detail_buffer) + ctypes.sizeof(wintypes.DWORD)
            )
            location_paths = _location_paths(device_info_set, device_info)
            location_path = location_paths[0] if location_paths else ""
            port_path = next(
                (parsed for parsed in map(_usb_port_path, location_paths) if parsed),
                (),
            )
            interfaces.append(DeviceInterfacePath(
                path=path,
                port_path=port_path,
                location_path=location_path,
            ))
    finally:
        _setupapi.SetupDiDestroyDeviceInfoList(device_info_set)
    return interfaces


def enumerate_interface_paths() -> list[DeviceInterfacePath]:
    """Return present WinUSB paths, deduplicated across interface GUIDs."""
    root_bus_numbers: dict[str, int] = {}
    for hub in _enumerate_guid(_USB_HUB_INTERFACE_GUID):
        root_path = _root_location_path(hub.location_path)
        if (
            root_path
            and root_path.casefold() == hub.location_path.casefold()
            and root_path.casefold() not in root_bus_numbers
        ):
            root_bus_numbers[root_path.casefold()] = len(root_bus_numbers) + 1

    paths: dict[str, DeviceInterfacePath] = {}
    for guid_text in _INTERFACE_GUIDS:
        for item in _enumerate_guid(guid_text):
            # Different interface GUIDs produce different suffixes for the same
            # PDO.  Keep both because either is a valid independently openable
            # path, while exact duplicates add no selection value.
            root_path = _root_location_path(item.location_path).casefold()
            mapped_item = DeviceInterfacePath(
                path=item.path,
                port_path=item.port_path,
                location_path=item.location_path,
                bus=root_bus_numbers.get(root_path),
            )
            paths.setdefault(item.path.casefold(), mapped_item)
    return list(paths.values())


def find_interface_path(
    vid: int,
    pid: int,
    interface: int,
    port_path: UsbPortPath,
    bus: int | None = None,
) -> DeviceInterfacePath:
    """Resolve one selected libusb device/interface to its Windows PDO path."""
    identity = re.compile(
        rf"vid_{vid:04x}&pid_{pid:04x}&mi_{interface:02x}", re.IGNORECASE
    )
    candidates = [
        item for item in enumerate_interface_paths()
        if identity.search(item.path)
    ]
    if port_path:
        candidates = [item for item in candidates if item.port_path == port_path]
    if bus is not None:
        bus_candidates = [item for item in candidates if item.bus == bus]
        if bus_candidates:
            candidates = bus_candidates
    if not candidates:
        selected_port = format_port_path(port_path)
        raise RuntimeError(
            f"WinUSB interface path not found for vid=0x{vid:04x} "
            f"pid=0x{pid:04x} interface={interface} bus={bus} "
            f"port_path={selected_port}; "
            "confirm that this MI_xx interface is bound to WinUSB"
        )
    physical_devices = {
        item.location_path.casefold()
        or item.path.casefold().rpartition("#{")[0]
        for item in candidates
    }
    if len(physical_devices) != 1:
        raise RuntimeError(
            f"WinUSB interface selection is ambiguous for bus={bus} "
            f"port_path={format_port_path(port_path)} interface={interface}"
        )
    # Prefer the system WinUSB class path when the INF also registers a custom
    # GUID.  Both point to the same PDO and have identical ownership semantics.
    candidates.sort(
        key=lambda item: (
            "dee824ef-729b-4a0e-9c14-b7117d33a817" not in item.path.casefold(),
            item.path.casefold(),
        )
    )
    return candidates[0]


class WinUsbDevice:
    """One independently opened WinUSB function (one physical CAN channel)."""

    def __init__(
        self,
        vid: int,
        pid: int,
        interface_info: InterfaceInfo,
        interface_count: int,
    ) -> None:
        selected_path = find_interface_path(
            vid,
            pid,
            interface_info.number,
            interface_info.port_path,
            bus=interface_info.bus,
        )
        self.bus = interface_info.bus
        self.address = interface_info.address
        self.port_path = interface_info.port_path
        self.idVendor = vid
        self.idProduct = pid
        self.interface = interface_info.number
        self.interface_count = interface_count
        self.ep_in = interface_info.ep_in
        self.ep_out = interface_info.ep_out
        self.device_path = selected_path.path
        self.context_id = id(self)

        self._device_handle = _kernel32.CreateFileW(
            self.device_path,
            _GENERIC_READ | _GENERIC_WRITE,
            _FILE_SHARE_READ | _FILE_SHARE_WRITE,
            None,
            _OPEN_EXISTING,
            _FILE_FLAG_OVERLAPPED,
            None,
        )
        if self._device_handle == _INVALID_HANDLE_VALUE:
            raise _usb_error("CreateFileW")
        self.handle_id = int(self._device_handle)
        self._interface_handle = wintypes.HANDLE()
        if not _winusb.WinUsb_Initialize(
            self._device_handle, ctypes.byref(self._interface_handle)
        ):
            error = _usb_error("WinUsb_Initialize")
            _kernel32.CloseHandle(self._device_handle)
            self._device_handle = None
            raise error

        self._closed = False
        self._active = False
        self._error: BaseException | None = None
        self._io_lock = threading.Lock()
        self._rx_condition = threading.Condition()
        self._rx_transfers: list[_RxTransfer] = []
        self._rx_submitted = 0
        self._rx_thread: threading.Thread | None = None
        self._stats = {
            "rx_completed": 0,
            "rx_bytes": 0,
            "rx_errors": 0,
            "tx_completed": 0,
            "tx_bytes": 0,
            "tx_errors": 0,
        }
        atexit.register(self.close)

    @property
    def error(self) -> BaseException | None:
        """Return the fatal exception that stopped receive dispatch."""
        return self._error

    def stats(self) -> dict[str, int]:
        """Return a snapshot of interface-local USB transfer counters."""
        with self._rx_condition:
            snapshot = dict(self._stats)
            snapshot["rx_submitted"] = self._rx_submitted
            snapshot["rx_pool"] = len(self._rx_transfers)
        return snapshot

    def _create_event(self) -> int:
        event = _kernel32.CreateEventW(None, True, False, None)
        if not event:
            raise _usb_error("CreateEventW")
        return event

    def _cancel(self, overlapped: _OVERLAPPED) -> None:
        if _kernel32.CancelIoEx(self._device_handle, ctypes.byref(overlapped)):
            return
        error_code = ctypes.get_last_error()
        if error_code != _ERROR_NOT_FOUND:
            raise _usb_error("CancelIoEx", error_code)

    def _run_overlapped(
        self,
        operation: str,
        timeout_ms: int,
        submit: Callable[[ctypes.POINTER(wintypes.ULONG), ctypes.POINTER(_OVERLAPPED)], int],
    ) -> int:
        event = self._create_event()
        overlapped = _OVERLAPPED(hEvent=event)
        transferred = wintypes.ULONG()
        try:
            if submit(ctypes.byref(transferred), ctypes.byref(overlapped)):
                return int(transferred.value)
            error_code = ctypes.get_last_error()
            if error_code != _ERROR_IO_PENDING:
                raise _usb_error(operation, error_code)
            wait_timeout = _INFINITE if timeout_ms == 0 else timeout_ms
            wait_result = _kernel32.WaitForSingleObject(event, wait_timeout)
            if wait_result == _WAIT_TIMEOUT:
                self._cancel(overlapped)
                _kernel32.WaitForSingleObject(event, _INFINITE)
                # Observe the cancelled completion before its OVERLAPPED and
                # buffer leave scope.  ERROR_OPERATION_ABORTED is expected.
                _winusb.WinUsb_GetOverlappedResult(
                    self._interface_handle,
                    ctypes.byref(overlapped),
                    ctypes.byref(transferred),
                    False,
                )
                raise usb.core.USBTimeoutError(
                    f"{operation} timed out after {timeout_ms} ms",
                    _WAIT_TIMEOUT,
                    110,
                )
            if wait_result == _WAIT_FAILED:
                raise _usb_error("WaitForSingleObject")
            if wait_result != _WAIT_OBJECT_0:
                raise usb.core.USBError(
                    f"{operation} returned unexpected wait status {wait_result}"
                )
            if not _winusb.WinUsb_GetOverlappedResult(
                self._interface_handle,
                ctypes.byref(overlapped),
                ctypes.byref(transferred),
                False,
            ):
                raise _usb_error(operation)
            return int(transferred.value)
        finally:
            _kernel32.CloseHandle(event)

    def ctrl_transfer(
        self,
        request_type: int,
        request: int,
        value: int,
        index: int,
        data_or_length: Any,
        timeout: int = 0,
    ) -> bytes | int:
        """Provide the PyUSB control-transfer API used by the protocol layer."""
        is_read = bool(request_type & 0x80)
        if is_read:
            length = int(data_or_length)
            buffer = (_UCHAR * length)()
        else:
            payload = bytes(data_or_length)
            length = len(payload)
            buffer = (_UCHAR * length).from_buffer_copy(payload)
        if length > 0xFFFF:
            raise ValueError("USB control transfer exceeds 65535 bytes")
        setup_packet = _WINUSB_SETUP_PACKET(
            request_type, request, value, index, length
        )
        buffer_pointer = ctypes.cast(buffer, ctypes.POINTER(_UCHAR))
        with self._io_lock:
            transferred = self._run_overlapped(
                "WinUsb_ControlTransfer",
                timeout,
                lambda count, overlapped: _winusb.WinUsb_ControlTransfer(
                    self._interface_handle,
                    setup_packet,
                    buffer_pointer,
                    length,
                    count,
                    overlapped,
                ),
            )
        if is_read:
            return bytes(buffer[:transferred])
        return transferred

    def write(self, endpoint: int, data: bytes, timeout: int = 0) -> int:
        """Write one bulk transfer through this interface's OUT pipe."""
        payload = bytes(data)
        buffer = (_UCHAR * len(payload)).from_buffer_copy(payload)
        buffer_pointer = ctypes.cast(buffer, ctypes.POINTER(_UCHAR))
        with self._io_lock:
            try:
                transferred = self._run_overlapped(
                    "WinUsb_WritePipe",
                    timeout,
                    lambda count, overlapped: _winusb.WinUsb_WritePipe(
                        self._interface_handle,
                        endpoint,
                        buffer_pointer,
                        len(payload),
                        count,
                        overlapped,
                    ),
                )
            except usb.core.USBError:
                self._stats["tx_errors"] += 1
                raise
        self._stats["tx_completed"] += 1
        self._stats["tx_bytes"] += transferred
        return transferred

    def _submit_rx(self, transfer: _RxTransfer) -> None:
        _kernel32.ResetEvent(transfer.event)
        transfer.overlapped = _OVERLAPPED(hEvent=transfer.event)
        transfer.immediate_length = None
        transferred = wintypes.ULONG()
        completed = _winusb.WinUsb_ReadPipe(
            self._interface_handle,
            self.ep_in.bEndpointAddress,
            ctypes.cast(transfer.buffer, ctypes.POINTER(_UCHAR)),
            len(transfer.buffer),
            ctypes.byref(transferred),
            ctypes.byref(transfer.overlapped),
        )
        if completed:
            transfer.immediate_length = int(transferred.value)
            _kernel32.SetEvent(transfer.event)
        else:
            error_code = ctypes.get_last_error()
            if error_code != _ERROR_IO_PENDING:
                raise _usb_error("WinUsb_ReadPipe", error_code)
        with self._rx_condition:
            transfer.submitted = True
            self._rx_submitted += 1
            self._rx_condition.notify_all()

    def _cancel_rx_transfers(self) -> None:
        for transfer in self._rx_transfers:
            if not transfer.submitted:
                continue
            try:
                self._cancel(transfer.overlapped)
            except usb.core.USBError:
                pass

    def _drain_cancelled_rx(self) -> None:
        """Drain reads cancelled before the dispatcher thread was started."""
        deadline = time.monotonic() + _RX_STOP_TIMEOUT_S
        for transfer in self._rx_transfers:
            if not transfer.submitted:
                continue
            remaining_ms = max(0, int((deadline - time.monotonic()) * 1000))
            wait_result = _kernel32.WaitForSingleObject(
                transfer.event, remaining_ms
            )
            if wait_result != _WAIT_OBJECT_0:
                raise RuntimeError("WinUSB receive cancellation did not complete")
            try:
                self._finish_rx(transfer)
            except usb.core.USBError:
                pass
            transfer.submitted = False
        with self._rx_condition:
            self._rx_submitted = 0
            self._rx_condition.notify_all()

    def _finish_rx(self, transfer: _RxTransfer) -> int:
        if transfer.immediate_length is not None:
            return transfer.immediate_length
        transferred = wintypes.ULONG()
        if not _winusb.WinUsb_GetOverlappedResult(
            self._interface_handle,
            ctypes.byref(transfer.overlapped),
            ctypes.byref(transferred),
            False,
        ):
            raise _usb_error("WinUsb_ReadPipe")
        return int(transferred.value)

    def _rx_worker(self, callback: Callable[[bytes], None]) -> None:
        pending = deque(self._rx_transfers)
        while pending:
            transfer = pending[0]
            wait_result = _kernel32.WaitForSingleObject(
                transfer.event, _RX_WAIT_INTERVAL_MS
            )
            if wait_result == _WAIT_TIMEOUT:
                continue
            pending.popleft()
            try:
                if wait_result == _WAIT_FAILED:
                    raise _usb_error("WaitForSingleObject")
                actual_length = self._finish_rx(transfer)
            except usb.core.USBError as exc:
                error_code = getattr(exc, "backend_error_code", None)
                with self._rx_condition:
                    transfer.submitted = False
                    self._rx_submitted = max(0, self._rx_submitted - 1)
                    if self._active or error_code != _ERROR_OPERATION_ABORTED:
                        self._stats["rx_errors"] += 1
                    self._rx_condition.notify_all()
                if self._active and error_code != _ERROR_OPERATION_ABORTED:
                    self._error = exc
                    self._active = False
                    self._cancel_rx_transfers()
                continue

            with self._rx_condition:
                transfer.submitted = False
                self._rx_submitted = max(0, self._rx_submitted - 1)
                self._stats["rx_completed"] += 1
                self._stats["rx_bytes"] += actual_length
                self._rx_condition.notify_all()
            try:
                callback(bytes(transfer.buffer[:actual_length]))
            except BaseException as exc:
                self._error = exc
                self._active = False
                self._cancel_rx_transfers()
            if self._active:
                try:
                    self._submit_rx(transfer)
                    pending.append(transfer)
                except usb.core.USBError as exc:
                    self._stats["rx_errors"] += 1
                    self._error = exc
                    self._active = False
                    self._cancel_rx_transfers()
        with self._rx_condition:
            self._rx_submitted = 0
            self._rx_condition.notify_all()

    def start_rx(
        self,
        callback: Callable[[bytes], None],
        size: int = 512,
        count: int = 30,
    ) -> None:
        """Submit and maintain an ordered pool of overlapped bulk-IN reads."""
        if count < 1:
            raise ValueError("bulk-IN transfer count must be positive")
        if self._active or self._rx_transfers:
            raise RuntimeError("bulk-IN transfer pool is already active")
        self._active = True
        try:
            for _ in range(count):
                event = self._create_event()
                transfer = _RxTransfer(
                    event=event,
                    overlapped=_OVERLAPPED(hEvent=event),
                    buffer=(_UCHAR * size)(),
                )
                self._rx_transfers.append(transfer)
                self._submit_rx(transfer)
            self._rx_thread = threading.Thread(
                target=self._rx_worker,
                args=(callback,),
                daemon=True,
                name=f"winusb-mi{self.interface}-rx",
            )
            self._rx_thread.start()
        except Exception:
            self.stop_rx()
            raise

    def ensure_rx_submitted(self, expected: int, timeout: float = 0.05) -> None:
        """Fail early when WinUSB did not retain the complete IN pool."""
        deadline = time.monotonic() + timeout
        with self._rx_condition:
            while self._active and self._rx_submitted != expected:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._rx_condition.wait(remaining)
            if not self._active or len(self._rx_transfers) != expected:
                raise RuntimeError(
                    f"bulk-IN pool is incomplete: {len(self._rx_transfers)}/{expected}"
                )
            if self._rx_submitted != expected:
                raise RuntimeError(
                    f"bulk-IN transfers submitted: {self._rx_submitted}/{expected}"
                )

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

    @property
    def rx_transfer_count(self) -> int:
        return len(self._rx_transfers)

    def stop_rx(self) -> None:
        """Cancel pending reads, join the dispatcher, and release its events."""
        self._active = False
        self._cancel_rx_transfers()
        if self._rx_thread is not None:
            self._rx_thread.join(timeout=_RX_STOP_TIMEOUT_S)
            if self._rx_thread.is_alive():
                error = RuntimeError("WinUSB receive dispatcher did not stop")
                if self._error is None:
                    self._error = error
                raise error
            self._rx_thread = None
        else:
            self._drain_cancelled_rx()
        for transfer in self._rx_transfers:
            _kernel32.CloseHandle(transfer.event)
        self._rx_transfers.clear()
        with self._rx_condition:
            self._rx_submitted = 0
            self._rx_condition.notify_all()

    def close(self) -> None:
        """Stop transfers and close this interface's independent handles."""
        if self._closed:
            return
        self.stop_rx()
        self._closed = True
        if self._interface_handle.value:
            _winusb.WinUsb_Free(self._interface_handle)
            self._interface_handle = wintypes.HANDLE()
        if self._device_handle not in (None, _INVALID_HANDLE_VALUE):
            _kernel32.CloseHandle(self._device_handle)
            self._device_handle = None
        atexit.unregister(self.close)
