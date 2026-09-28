#!/usr/bin/env python3
"""Measure a finite Classical CAN burst from candump kernel receive timestamps.

CRC-15 and stuffing cover SOF through the CRC sequence; the fixed trailer
includes CRC delimiter, ACK, EOF and intermission. This is the same wire-bit
accounting used by linux-can/can-utils canframelen.c (CFL_EXACT).
Receive timing measures host-observed throughput, not hardware bus occupancy.
"""
import argparse
from decimal import Decimal
from pathlib import Path
import re

CAN_CRC15_POLYNOMIAL = 0x4599
CAN_CRC15_MASK = 0x7FFF
CAN_FIXED_TRAILER_BITS = 13
LOAD_CAN_ID = 0x321
LOAD_PAYLOAD_BYTES = 8
LOG_FRAME = re.compile(r'^\((\d+\.\d+)\)\s+\S+\s+([0-9A-Fa-f]{3})#([0-9A-Fa-f]{16})$')


def bits(value, width):
    return [(value >> shift) & 1 for shift in range(width - 1, -1, -1)]


def wire_bits(can_id, payload):
    if not 0 <= can_id <= 0x7FF or len(payload) > 8:
        raise ValueError('only standard Classical data frames are supported')
    prefix = [0] + bits(can_id, 11) + [0, 0, 0] + bits(len(payload), 4)
    for value in payload:
        prefix.extend(bits(value, 8))
    crc = 0
    for bit in prefix:
        feedback = ((crc >> 14) & 1) ^ bit
        crc = (crc << 1) & CAN_CRC15_MASK
        if feedback:
            crc ^= CAN_CRC15_POLYNOMIAL
    encoded = prefix + bits(crc, 15)
    stuffed = run = 0
    previous = None
    for bit in encoded:
        run = run + 1 if bit == previous else 1
        previous = bit
        if run == 5:
            stuffed += 1
            previous = 1 - bit
            run = 1
    return len(encoded) + stuffed + CAN_FIXED_TRAILER_BITS


def measure(lines, count, bitrate):
    if count < 2 or bitrate <= 0:
        raise ValueError('at least two frames and a positive bitrate are required')
    first = previous = None
    interval_bits = received = 0
    equal_timestamps = 0
    backwards = False
    for line in lines:
        match = LOG_FRAME.fullmatch(line.strip())
        if not match:
            raise ValueError(f'unexpected capture output: {line.strip()!r}')
        timestamp, identifier, data = match.groups()
        timestamp = Decimal(timestamp)
        if int(identifier, 16) != LOAD_CAN_ID:
            raise ValueError('unexpected CAN identifier in load capture')
        length = wire_bits(LOAD_CAN_ID, bytes.fromhex(data))
        if previous is not None:
            if timestamp < previous:
                backwards = True
            equal_timestamps += timestamp == previous
            # End-of-frame timestamps bound frames 2..N, excluding frame 1.
            interval_bits += length
        else:
            first = timestamp
        previous = timestamp
        received += 1
    if received != count:
        raise ValueError(f'capture contains {received} frames, expected {count}')
    elapsed = previous - first
    if elapsed <= 0 or backwards:
        return dict(frames=received, seconds=elapsed, fps=None, load=None,
                    equal_timestamps=equal_timestamps)
    load = Decimal(interval_bits * 100) / (elapsed * bitrate)
    return dict(frames=received, seconds=elapsed, fps=Decimal(received - 1) / elapsed,
                load=load, equal_timestamps=equal_timestamps)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('capture', type=Path)
    parser.add_argument('count', type=int)
    parser.add_argument('--bitrate', type=int, default=250000)
    args = parser.parse_args()
    try:
        with args.capture.open() as stream:
            result = measure(stream, args.count, args.bitrate)
        if result['fps'] is None:
            print(f"  burst frames={result['frames']}; throughput unavailable (receive timestamps)")
        else:
            print(f"  burst frames={result['frames']} receive_span={result['seconds']:.6f}s "
                  f"rate={result['fps']:.1f} frames/s")
            print(f"  host-observed load={result['load']:.2f}% (diagnostic only; "
                  f"USB batching can distort timing), equal_timestamps={result['equal_timestamps']}")
    except (OSError, ValueError) as exc:
        print(f'FAIL: {exc}')
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
