import copy
import struct
import unittest
from unittest.mock import patch
from tools import device_manager as dm
from tools import pcan_manage as pm

RAW=struct.pack('<II8I',48,0x00200102,1,2,3,4,5,6,7,8)
def device(mode='pcan'):
    return dict(dm.identity(RAW),mode=mode,selector={'channel':81},channels=[81,82])

class DeviceManagerTests(unittest.TestCase):
    def test_exact_identity(self):
        info=dm.identity(RAW)
        self.assertEqual(info['raw_info'],RAW.hex())
        self.assertEqual(info['sw_version_full'],'0.0.0.48')
        self.assertEqual(info['raw_hw_version'],0x00200102)
        self.assertEqual(info['uuid'],[5,6,7,8])
        self.assertEqual(pm.uid_bytes(info['uid_hex']),RAW[8:24])

    def test_identity_rejects_bad_length_or_uid(self):
        for raw in (RAW[:-1], RAW[:8]+bytes(16)+RAW[24:]):
            with self.assertRaises(ValueError):dm.identity(raw)

    def test_multiple_selection(self):
        a=device();b=copy.deepcopy(a);b['uid_hex']='a'*32
        with self.assertRaises(ValueError):dm.select([a,b])
        self.assertIs(dm.select([a,b],'A'*32),b)
        with self.assertRaises(ValueError):dm.select([])
        with self.assertRaises(ValueError):dm.select([a,a],a['uid_hex'])

    def test_already_selected_never_writes(self):
        with patch.object(dm,'request_switch') as write:
            self.assertEqual(dm.switch(device(),'pcan')['completion'],'already_selected')
            write.assert_not_called()

    def test_switch_waits_for_matching_mode_and_uid(self):
        wrong=device('vkgs_usb');right=device('vcan_usb')
        with patch.object(dm,'request_switch') as write,patch.object(dm.time,'sleep'),patch.object(dm,'discover',side_effect=[([wrong],[]),([right],[])]):
            self.assertEqual(dm.switch(device(),'vcan_usb')['completion'],'verified')
            write.assert_called_once()

    def test_switch_rejects_changed_version(self):
        changed=device('vcan_usb');changed['raw_info']='ff'+changed['raw_info'][2:]
        with patch.object(dm,'request_switch'),patch.object(dm.time,'sleep'),patch.object(dm,'discover',return_value=([changed],[])):
            with self.assertRaises(RuntimeError):dm.switch(device(),'vcan_usb')

    def test_query_response(self):
        import can,zlib
        class Bus:
            def send(self,msg,timeout):
                raw=bytearray(64);raw[:12]=msg.data[:12];raw[4]=0x85;raw[12:52]=RAW
                struct.pack_into('<I',raw,60,zlib.crc32(raw[:60]))
                self.msg=can.Message(arbitration_id=pm.RESPONSE_ID,is_extended_id=True,is_fd=True,data=raw)
            def recv(self,timeout):return self.msg
        s=pm.Session(Bus(),dm.identity(RAW)['uid_hex']);self.assertEqual(s.info(),RAW)

if __name__=='__main__':unittest.main()
