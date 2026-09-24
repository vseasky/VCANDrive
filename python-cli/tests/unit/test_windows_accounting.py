import io
import unittest
from argparse import Namespace
from contextlib import redirect_stdout
from unittest.mock import patch
from tests.hardware_test import Integrity, _matrix_message, _phase, _socketcan_raw_phases
from test_stress import FakeBus


class AccountingTest(unittest.TestCase):
    def test_matrix_all_lengths_flags_and_trailing_duplicate(self):
        for count in (36, 100):
            check = Integrity([0, 1], [0, 1], False, _matrix_message, count)
            for sender in (0, 1):
                for sequence in range(count):
                    msg = _matrix_message(sender, sequence, True, True)
                    check.accept(1-sender, msg, True, True, {0: count, 1: count})
            self.assertEqual(check.invalid, 0)
            self.assertEqual(check.observed, count*2)
            self.assertEqual(check.counts, {(0, 1): count, (1, 0): count})
            self.assertEqual(check.link_bytes[(0, 1)], sum(len(_matrix_message(1,n,True,True).data) for n in range(count)))
            check.accept(0, _matrix_message(1, count-1, True, True), True, True, {0:count,1:count})
            self.assertEqual(check.invalid, 1)

    def test_zero_length_and_rtr_wrong_dlc_or_id_fail(self):
        for seq in (0, 9, 36):
            for defect in ('dlc', 'id', 'rtr', 'payload'):
                check = Integrity([0,1], [0], False, _matrix_message, 100)
                for n in range(seq): check.accept(1, _matrix_message(0,n,True,True),True,True,{0:100})
                msg = _matrix_message(0,seq,True,True)
                if defect == 'dlc': msg.dlc += 1
                elif defect == 'id': msg.arbitration_id ^= 0x100000
                elif defect == 'rtr': msg.is_remote_frame = not msg.is_remote_frame
                else: msg.data = bytearray(b'x')
                check.accept(1,msg,True,True,{0:100})
                self.assertEqual(check.invalid,1)

    @patch('tests.hardware_test._prepare_round')
    def test_phase_records_exact_bytes_and_counter_units(self, prepare):
        buses=[FakeBus(),FakeBus()];buses[0].peer,buses[1].peer=buses[1],buses[0]
        args=Namespace(channel_numbers=[0,1],sender_indices=[0,1],frames=100,
            window=1,timeout_ms=100,receive_timeout=.002,drain_time=.002,
            bitrate=1000000,data_bitrate=5000000,phase_results=[],
            message_factory=_matrix_message,matrix_size=100)
        output=io.StringIO()
        with redirect_stdout(output): self.assertTrue(_phase(args,buses,True,True,False,'matrix'))
        self.assertIn('USB transfers (NOT CAN frames)',output.getvalue())
        for link in args.phase_results[0]['links']:
            self.assertEqual(link['tx_frames'], link['rx_valid_frames'])
            self.assertEqual(link['tx_payload_bytes'], link['rx_payload_bytes'])
            self.assertTrue(link['equal'])

    def test_socketcan_rates_directions_and_restore(self):
        args=Namespace(frames=24,window=32,bitrate=1000000,data_bitrate=5000000,
                       sender_indices=[0,1],channel_numbers=[0,1],skip_fd=False)
        calls=[]
        def phase(args,buses,fd,brs,loopback,label):
            calls.append((args.bitrate,args.frames,args.sender_indices[:],fd))
        with patch('tests.hardware_test._run_phase',side_effect=phase), patch('tests.hardware_test._open_buses',return_value=[]): _socketcan_raw_phases(args,[])
        self.assertEqual(len(calls),8)
        self.assertTrue(all(call[1] == 24 for call in calls))
        self.assertEqual(calls[0],(250000,24,[0],False))
        self.assertEqual(calls[-2],(500000,24,[0],False))
        self.assertEqual(calls[-1],(1000000,24,[1],True))
        self.assertEqual(args.sender_indices,[0,1])
        self.assertEqual(args.frames,24)
        self.assertFalse(hasattr(args,'message_factory'))

    @patch('tests.hardware_test._prepare_round')
    def test_wrong_selected_bitrate_fails_before_sending(self, prepare):
        from tests.hardware_test import PhaseFailure
        buses=[FakeBus()]
        buses[0].get_bit_timing=lambda: {'nominal':{'bitrate':1000000},'data':{}}
        args=Namespace(channel_numbers=[0],sender_indices=[0],frames=1,window=1,bitrate=250000)
        with redirect_stdout(io.StringIO()), self.assertRaises(PhaseFailure):
            _phase(args,buses,False,False,True,'rate mismatch')
        self.assertEqual(buses[0].tx,0)

    @patch('tests.hardware_test._prepare_round')
    def test_matrix_requested_counts_small_and_repeated(self, prepare):
        for fd, width in ((False,36),(True,100)):
            for frames in (1,24,37,101,257,1000):
                buses=[FakeBus(),FakeBus()];buses[0].peer,buses[1].peer=buses[1],buses[0]
                args=Namespace(channel_numbers=[0,1],sender_indices=[0,1],frames=frames,
                    window=1,timeout_ms=100,receive_timeout=.002,drain_time=.002,
                    bitrate=1000000,data_bitrate=5000000,phase_results=[],coverage_gaps=[],
                    message_factory=_matrix_message,matrix_size=width)
                with redirect_stdout(io.StringIO()):
                    self.assertTrue(_phase(args,buses,fd,fd,False,'matrix'))
                for link in args.phase_results[0]['links']:
                    self.assertEqual(link['tx_frames'],frames)
                    self.assertEqual(link['rx_valid_frames'],frames)
                    self.assertTrue(link['equal'])
                self.assertEqual(bool(args.coverage_gaps),frames<width)
                self.assertTrue(all(not _matrix_message(0,n,fd,fd).is_fd for n in range(frames)) if not fd else True)
