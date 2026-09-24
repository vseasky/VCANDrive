"""Local PCAN management v1; requires matching firmware, not generic adapters."""
import argparse
import json
import os
from pathlib import Path
import secrets
import struct
import time
import zlib

REQUEST_ID, RESPONSE_ID = 0x1FFFFF00, 0x1FFFFF01

def packet(op, arg, seq, nonce, uid):
    raw = struct.pack('<4sBBHI16s', b'UCM1', op, arg, seq, nonce, uid)
    return raw + struct.pack('<I', zlib.crc32(raw))

def decode(raw):
    if len(raw) != 32 or raw[:4] != b'UCM1' or zlib.crc32(raw[:28]) != struct.unpack_from('<I', raw, 28)[0]:
        raise ValueError('Invalid management response')
    return struct.unpack('<4sBBHI16s', raw[:28])[1:]

def uid_bytes(text):
    if len(text) != 32:
        raise ValueError('Expected UID must contain 32 hex digits')
    return struct.pack('<4I', *(int(text[i:i+8], 16) for i in range(0,32,8)))

def open_bus(channel, passive=False):
    import can
    return can.Bus(interface='pcan', channel=channel, state=can.BusState.PASSIVE if passive else can.BusState.ACTIVE, fd=True, f_clock=80000000,
        nom_brp=5, nom_tseg1=11, nom_tseg2=4, nom_sjw=1,
        data_brp=2, data_tseg1=5, data_tseg2=2, data_sjw=1)

class Session:
    def __init__(self, bus, expected_uid=None, timeout=3.0):
        self.bus = bus
        self.uid = uid_bytes(expected_uid) if expected_uid else None
        self.timeout = timeout
        self.seq = secrets.randbelow(65000)
        self.nonce = secrets.randbits(32) or 1

    def exchange(self, op, arg=0, hello=False):
        import can
        if not hello and self.uid is None:
            raise RuntimeError('Handshake required')
        data = packet(op, arg, self.seq, self.nonce, bytes(16) if hello else self.uid)
        self.bus.send(can.Message(arbitration_id=REQUEST_ID, is_extended_id=True,
            is_fd=True, bitrate_switch=False, data=data), timeout=2)
        deadline=time.monotonic()+self.timeout
        while time.monotonic()<deadline:
            msg=self.bus.recv(timeout=0.1)
            if msg is None or not msg.is_extended_id or not msg.is_fd or msg.arbitration_id != RESPONSE_ID:
                continue
            raw = bytes(msg.data)
            info = None
            if len(raw) == 64 and raw[:4] == b'UCM1' and raw[4] == 0x85:
                if zlib.crc32(raw[:60]) != struct.unpack_from('<I', raw, 60)[0] or any(raw[52:60]):
                    raise ValueError('Invalid device information response')
                code, value, seq, nonce = struct.unpack_from('<BBHI', raw, 4)
                info = raw[12:52]
                uid = info[8:24]
            else:
                code, value, seq, nonce, uid=decode(raw)
            if seq != self.seq or nonce != self.nonce:
                continue
            if self.uid is not None and uid != self.uid:
                raise RuntimeError('UID mismatch; no further command sent')
            if code == 255:
                raise RuntimeError(f'Management command rejected: status {value}')
            if code != op | 0x80:
                raise RuntimeError('Unexpected response opcode')
            self.seq=(self.seq+1)&65535
            if hello:
                self.uid = uid
            return info if op == 5 else value
        raise TimeoutError('No matching management response; outcome uncertain, no automatic retry')

    def info(self):
        raw = self.exchange(5)
        if not isinstance(raw, bytes) or len(raw) != 40:
            raise ValueError('Expected 40-byte device information')
        return raw

    def hello(self):
        if self.exchange(1, hello=True) != 1:
            raise RuntimeError('Unsupported management protocol')

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--dll',type=Path)
    p.add_argument('--channel',default='PCAN_USBBUS1')
    p.add_argument('--expect-uid',required=True)
    p.add_argument('--operation',choices=['get-termination','set-termination','mode'],required=True)
    p.add_argument('--value',type=int)
    args=p.parse_args()
    uid_bytes(args.expect_uid)
    if args.operation=='set-termination' and args.value not in (0,1):p.error('termination value must be 0 or 1')
    if args.operation=='mode' and args.value not in (0,1,2):p.error('mode value must be 0, 1 or 2')
    if args.dll:
        if not args.dll.is_file():p.error('DLL does not exist')
        os.environ['PATH']=str(args.dll.resolve().parent)+os.pathsep+os.environ['PATH']
    with open_bus(args.channel) as bus:
        s=Session(bus,args.expect_uid);s.hello()
        if args.operation=='get-termination':value=s.exchange(3)
        elif args.operation=='set-termination':
            s.exchange(4,args.value);value=s.exchange(3)
            if value!=args.value:raise RuntimeError('Configuration readback mismatch')
        else:value=s.exchange(2,args.value)
    print(json.dumps({'operation':args.operation,'value':value,
        'completion':'acknowledged' if args.operation=='mode' else 'configuration_readback',
        'physical_verified':False,'uid':args.expect_uid}))

if __name__=='__main__':main()
