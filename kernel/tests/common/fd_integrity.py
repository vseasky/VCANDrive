#!/usr/bin/env python3
"""Strict CAN-FD sequence and round-trip checks using only Linux SocketCAN."""

import argparse
import errno
import select
import socket
import struct
import sys
import time


CAN_EFF_FLAG = 0x80000000
CAN_RAW_FD_FRAMES = 5
CANFD_BRS = 1
CANFD_ESI = 2
CANFD_FDF = 4
SO_RXQ_OVFL = 40
FRAME = struct.Struct("=IBB2x64s")
LENGTHS = (12, 16, 20, 24, 32, 48, 64)
WINDOW = 50
STALL_TIMEOUT = 20
BACKPRESSURE_RETRY = 0.001


class IntegrityError(Exception):
    pass


def payload(sequence):
    length = LENGTHS[sequence % len(LENGTHS)]
    return sequence.to_bytes(4, "little") + bytes(
        (sequence + offset) & 0xFF for offset in range(length - 4)
    )


def make_frame(can_id, sequence):
    data = payload(sequence)
    return FRAME.pack(CAN_EFF_FLAG | can_id, len(data), CANFD_BRS, data)


def check_frame(raw, can_id, sequence):
    if len(raw) != FRAME.size:
        raise IntegrityError(f"expected 72-byte CAN-FD frame, received {len(raw)} bytes")
    actual_id, length, flags, data = FRAME.unpack(raw)
    expected_data = payload(sequence)
    # SocketCAN may set FDF on received FD frames; ESI reports the sender's
    # controller state. Neither changes the checked payload or BRS requirement.
    if (actual_id != CAN_EFF_FLAG | can_id or
            not flags & CANFD_BRS or
            flags & ~(CANFD_BRS | CANFD_ESI | CANFD_FDF) or
            length != len(expected_data)):
        raise IntegrityError(
            f"frame {sequence}: ID/flags/length mismatch "
            f"(id=0x{actual_id:x}, flags={flags}, length={length})"
        )
    actual_sequence = int.from_bytes(data[:4], "little")
    if actual_sequence != sequence:
        raise IntegrityError(f"sequence gap/reorder: expected {sequence}, got {actual_sequence}")
    if data[:length] != expected_data:
        raise IntegrityError(f"frame {sequence}: payload mismatch")
    return bool(flags & CANFD_ESI)


def open_socket(interface):
    sock = socket.socket(socket.AF_CAN, socket.SOCK_RAW, socket.CAN_RAW)
    sock.setsockopt(socket.SOL_CAN_RAW, CAN_RAW_FD_FRAMES, 1)
    sock.setsockopt(socket.SOL_SOCKET, SO_RXQ_OVFL, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 8 * 1024 * 1024)
    sock.bind((interface,))
    sock.setblocking(False)
    return sock


def receive(sock, can_id, sequence):
    raw, controls, flags, _ = sock.recvmsg(FRAME.size, 128)
    if flags & (socket.MSG_TRUNC | socket.MSG_CTRUNC):
        raise IntegrityError("truncated frame or socket overflow metadata")
    for level, kind, value in controls:
        if level == socket.SOL_SOCKET and kind == SO_RXQ_OVFL:
            dropped = struct.unpack("=I", value[:4])[0]
            if dropped:
                raise IntegrityError(f"socket receive queue overflow: {dropped} dropped")
    return check_frame(raw, can_id, sequence)


def try_send(sock, frame):
    """Retry queue pressure without consuming a sequence number."""
    try:
        written = sock.send(frame)
    except OSError as exc:
        if exc.errno in (errno.EAGAIN, errno.EWOULDBLOCK, errno.ENOBUFS):
            return False
        raise
    if written != len(frame):
        raise IntegrityError(f"short CAN-FD write: {written}/{len(frame)} bytes")
    return True


def run(mode, tx_iface, rx_iface, count):
    request_id = 0x1ABCDE0
    reply_id = request_id + 1
    with open_socket(tx_iface) as tx, open_socket(rx_iface) as rx:
        sent = received = replied = confirmed = esi_frames = 0
        last_progress = time.monotonic()
        retry_at = {tx: 0.0, rx: 0.0}
        while confirmed < count if mode == "roundtrip" else received < count:
            if time.monotonic() - last_progress > STALL_TIMEOUT:
                raise IntegrityError(
                    f"stalled: sent={sent}, received={received}, "
                    f"replied={replied}, confirmed={confirmed}"
                )
            readers = [rx]
            if mode == "roundtrip":
                readers.append(tx)
            writers = []
            if sent < count and sent - (confirmed if mode == "roundtrip" else received) < WINDOW:
                writers.append(tx)
            if mode == "roundtrip" and replied < received:
                writers.append(rx)
            now = time.monotonic()
            delayed = [retry_at[sock] - now for sock in writers if retry_at[sock] > now]
            writers = [sock for sock in writers if retry_at[sock] <= now]
            ready_r, ready_w, _ = select.select(readers, writers, [], min([1.0] + delayed))
            if rx in ready_r:
                esi_frames += receive(rx, request_id, received)
                received += 1
                last_progress = time.monotonic()
            if mode == "roundtrip" and tx in ready_r:
                esi_frames += receive(tx, reply_id, confirmed)
                confirmed += 1
                last_progress = time.monotonic()
            if rx in ready_w:
                if try_send(rx, make_frame(reply_id, replied)):
                    replied += 1
                    last_progress = time.monotonic()
                else:
                    retry_at[rx] = time.monotonic() + BACKPRESSURE_RETRY
            if tx in ready_w:
                if try_send(tx, make_frame(request_id, sent)):
                    sent += 1
                    last_progress = time.monotonic()
                else:
                    retry_at[tx] = time.monotonic() + BACKPRESSURE_RETRY
        if sent != count or received != count or (mode == "roundtrip" and replied != count):
            raise IntegrityError(
                f"incomplete transfer: sent={sent}, received={received}, replied={replied}"
            )
    print(f"{mode}: {count} ordered CAN-FD/BRS frames, no gap/reorder/socket overflow")
    if esi_frames:
        print(f"  ESI set on {esi_frames} received frame(s); check CAN controller error state")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("sequence", "roundtrip"))
    parser.add_argument("tx_interface")
    parser.add_argument("rx_interface")
    parser.add_argument("count", type=int)
    args = parser.parse_args()
    if args.count <= 0 or args.count > 0xFFFFFFFF:
        parser.error("count must be 1..4294967295")
    try:
        run(args.mode, args.tx_interface, args.rx_interface, args.count)
    except (IntegrityError, OSError) as exc:
        print(f"CAN-FD integrity failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
