#!/usr/bin/env python3
"""Plan multi-packet PDUs whose data-frame counts sum to the requested budget."""
import argparse

ISOTP_FIRST_PAYLOAD = 6
ISOTP_CONSECUTIVE_PAYLOAD = 7
ISOTP_MAX_PAYLOAD = 4095
J1939_PACKET_PAYLOAD = 7
J1939_MAX_PAYLOAD = 1785
MIN_MULTI_FRAME_COUNT = 2


def data_frames(protocol, payload_size):
    if protocol == 'isotp':
        if not 8 <= payload_size <= ISOTP_MAX_PAYLOAD:
            raise ValueError('ISO-TP payload must be 8..4095 bytes')
        return 1 + (payload_size - ISOTP_FIRST_PAYLOAD + ISOTP_CONSECUTIVE_PAYLOAD - 1) // ISOTP_CONSECUTIVE_PAYLOAD
    if protocol == 'j1939':
        if not 9 <= payload_size <= J1939_MAX_PAYLOAD:
            raise ValueError('J1939 TP payload must be 9..1785 bytes')
        return (payload_size + J1939_PACKET_PAYLOAD - 1) // J1939_PACKET_PAYLOAD
    raise ValueError(f'unknown protocol: {protocol}')


def plan(protocol, count, max_payload):
    """Return (payload bytes, data frames) without creating a single-frame PDU."""
    capacity = data_frames(protocol, max_payload)
    if count < MIN_MULTI_FRAME_COUNT:
        raise ValueError('multi-packet budget must be at least two data frames')
    pdu_count = (count + capacity - 1) // capacity
    if pdu_count * MIN_MULTI_FRAME_COUNT > count:
        raise ValueError('frame budget cannot be split into multi-packet PDUs at this payload limit')
    # Balanced allocation avoids a one-frame remainder (e.g. 256 / 255).
    per_pdu, extra = divmod(count, pdu_count)
    for index in range(pdu_count):
        frames = per_pdu + (index < extra)
        size = (ISOTP_FIRST_PAYLOAD + (frames - 1) * ISOTP_CONSECUTIVE_PAYLOAD
                if protocol == 'isotp' else frames * J1939_PACKET_PAYLOAD)
        yield min(size, max_payload), frames


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('protocol', choices=('isotp', 'j1939'))
    parser.add_argument('count', type=int)
    parser.add_argument('max_payload', type=int)
    args = parser.parse_args()
    try:
        for size, frames in plan(args.protocol, args.count, args.max_payload):
            print(size, frames)
    except ValueError as exc:
        parser.error(str(exc))


if __name__ == '__main__':
    main()
