"""Lightweight device discovery, identity and USB mode switching (Windows)."""
import argparse
import json
import os
from pathlib import Path
import struct
import sys
import time

if __package__:
    from . import usb_device_info as usb_tools, pcan_manage as pcan
else:
    import usb_device_info as usb_tools
    import pcan_manage as pcan

MODES = {'vcan_usb': 0, 'pcan': 1, 'vkgs_usb': 2}

def version(value):
    return '.'.join(str((value >> shift) & 255) for shift in (24,16,8,0))

def identity(raw):
    if len(raw) != 40:
        raise ValueError('Expected 40-byte device information')
    sw, hw, *words = struct.unpack('<II8I', raw)
    uid = ''.join(f'{n:08x}' for n in words[:4])
    if uid in ('0'*32, 'f'*32):
        raise ValueError('Invalid device UID')
    return {'sw_version': sw, 'sw_version_full': version(sw),
            'raw_hw_version': hw, 'hw_version_hex': f'0x{hw:08x}',
            'device_type': (hw >> 16) & 255, 'uid': words[:4], 'uuid': words[4:],
            'uid_hex': uid, 'raw_info': raw.hex()}

def discover():
    devices, notices = [], []
    # Enumeration is descriptor-only; use existing backends for control reads.
    try:
        import usb.core
        import libusb_package
        backend = libusb_package.get_libusb1_backend()
        candidates = list(usb.core.find(find_all=True, backend=backend))
        for d in candidates:
            mode = {(0x1d50,0x6080):'vcan_usb',(0x1d50,0x606f):'vkgs_usb'}.get((d.idVendor,d.idProduct))
            if not mode:
                continue
            selector={'bus':d.bus,'port_path':tuple(d.port_numbers or ())}
            try:
                # Avoid index fallback if a physical path is unavailable.
                if not selector['port_path']:
                    raise RuntimeError('USB topology unavailable')
                details = usb_tools.inspect(mode,selector)
                devices.append(dict(identity(bytes.fromhex(details['raw_info'])),
                    mode=mode, selector=details['selector'], channels=details['channels']))
            except Exception as e:
                notices.append(f'{mode} {selector}: {e}')
    except Exception as e:
        notices.append(f'USB discovery: {e}')
    try:
        from can.interfaces.pcan import basic
        api=basic.PCANBasic()
        status, channels=api.GetValue(basic.PCAN_NONEBUS,basic.PCAN_ATTACHED_CHANNELS)
        if status != 0:
            raise RuntimeError(f'channel enumeration status {status:#x}')
        for ch in channels:
            if ch.device_type != basic.PCAN_USB.value:
                continue
            handle=int(ch.channel_handle)
            if ch.channel_condition != basic.PCAN_CHANNEL_AVAILABLE:
                notices.append(f'PCAN {handle:#x}: occupied/unavailable; skipped')
                continue
            try:
                # Listen-only: discovery must not put management frames on CAN.
                with pcan.open_bus(handle, passive=True) as bus:
                    session=pcan.Session(bus,timeout=0.5)
                    session.hello()
                    details=identity(session.info())
                found=next((d for d in devices if d['mode']=='pcan' and
                            d['uid_hex']==details['uid_hex']),None)
                if found:
                    if found['raw_info']!=details['raw_info']:
                        raise RuntimeError('Conflicting identity on channels sharing UID')
                    found['channels'].append(handle)
                else:
                    devices.append(dict(details,mode='pcan',channels=[handle],
                                        selector={'channel':handle}))
            except Exception as e:
                notices.append(f'PCAN {handle:#x}: not identified by management protocol ({e})')
    except Exception as e:
        notices.append(f'PCAN discovery: {e}')
    return sorted(devices, key=lambda d:(d['uid_hex'],d['mode'])), notices

def select(devices, uid=None):
    matches=[d for d in devices if uid is None or d['uid_hex']==uid.lower()]
    if len(matches)!=1:
        raise ValueError('No unique device; use list and --uid when multiple devices are connected')
    return matches[0]

def request_switch(device, target):
    expected=device['raw_info']
    if device['mode']=='pcan':
        with pcan.open_bus(device['selector']['channel'],passive=True) as bus:
            session=pcan.Session(bus,device['uid_hex'])
            session.hello()
            if session.info().hex()!=expected:
                raise RuntimeError('Device identity changed; switch cancelled')
            session.exchange(2,MODES[target])
    else:
        channel=min(device['channels'])
        # Identity check and mutation use the same open USB handle.
        with usb_tools.open_device(device['mode'],device['selector'],channel) as (handle, _):
            if usb_tools.identity(handle, _)['raw_info']!=expected:
                raise RuntimeError('Device identity changed; switch cancelled')
            handle.usb_mode(MODES[target])

def switch(device, target, timeout=30):
    if device['mode']==target:
        return {'completion':'already_selected','device':device}
    request_switch(device,target)
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        time.sleep(0.3)
        devices,_=discover()
        matches=[d for d in devices if d['uid_hex']==device['uid_hex'] and d['mode']==target]
        if len(matches)>1:
            raise RuntimeError('Ambiguous identity after switch')
        if matches:
            if matches[0]['raw_info']!=device['raw_info']:
                raise RuntimeError('Identity/version changed after switch')
            return {'completion':'verified','device':matches[0]}
    raise TimeoutError('Switch not verified; no automatic write retry')

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',nargs='?',choices=['list','info','switch'],default='list')
    parser.add_argument('target',nargs='?',choices=list(MODES))
    parser.add_argument('--uid',help='only required when multiple devices are detected')
    parser.add_argument('--dll',type=Path,help='optional PCANBasic.dll path')
    parser.add_argument('--json',action='store_true')
    parser.add_argument('--timeout',type=float,default=30)
    args=parser.parse_args()
    if args.command=='switch' and args.target is None:parser.error('switch requires a target mode')
    if args.command!='switch' and args.target is not None:parser.error('target only applies to switch')
    if args.timeout<=0:parser.error('timeout must be positive')
    if args.dll:
        if not args.dll.is_file():parser.error('DLL file not found')
        os.environ['PATH']=str(args.dll.resolve().parent)+os.pathsep+os.environ['PATH']
    try:
        devices, notices=discover()
        if args.command=='list':result={'devices':devices,'notices':notices}
        else:
            chosen_uid=args.uid
            if len(devices)>1 and chosen_uid is None and not args.json and sys.stdin.isatty():
                for n,d in enumerate(devices,1):
                    print(f"{n}. {d['mode']}  FW {d['sw_version_full']}  HW {d['hw_version_hex']}  UID {d['uid_hex']}")
                number=int(input('Select device number (0 cancels): '))
                if number==0:return 0
                if not 1<=number<=len(devices):raise ValueError('Invalid device number')
                chosen_uid=devices[number-1]['uid_hex']
            device=select(devices,chosen_uid)
            result=({'device':device} if args.command=='info' else switch(device,args.target,args.timeout))
        if args.json:print(json.dumps(result,ensure_ascii=False,indent=2))
        else:
            for n,d in enumerate(result.get('devices',[result['device']] if 'device' in result else []),1):
                print(f"{n}. {d['mode']:10}  FW {d['sw_version_full']:12}  HW {d['hw_version_hex']}  UID {d['uid_hex']}")
            if 'completion' in result:print(result['completion'])
            if args.command=='list' and not devices:print('No identified device')
            for notice in result.get('notices',[]):print(notice,file=sys.stderr)
        return 0
    except Exception as e:
        if args.json:print(json.dumps({'error':str(e)},ensure_ascii=False))
        else:print(str(e),file=sys.stderr)
        return 1

if __name__=='__main__':sys.exit(main())
