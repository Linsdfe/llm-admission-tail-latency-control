import sys,unittest,copy,errno
from unittest.mock import Mock
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts/replay'))
import p1_preflight_v11 as gate
import p1_live_workload_v11 as workload
from p1_live_control_v11 import Controller,estimate,reserve
def fixture():
 rows=[]
 for category in ('interactive','background'):
  for kind in ('extract','check','organize'):
   for i in range(24):
    prompt=f'{category}-{kind}-{i}'; rows.append({'prompt':prompt,'content_sha256':gate.digest(prompt),'category':category,'task_kind':kind,'split':'calibration','input_token_range':[32,79],'input_tokens':50,'max_tokens':64})
 prior=[dict(r,prompt=r['prompt']+'-prior') for r in rows if int(r['prompt'].split('-')[-1])<8]
 return rows,prior
class PreflightTests(unittest.TestCase):
 def test_full_gate(self):
  rows,prior=fixture(); result=gate.validate(rows,set(),prior); self.assertEqual(set(result['history_buckets'].values()),{32})
 def test_duplicate_history(self):
  rows,prior=fixture()
  with self.assertRaisesRegex(ValueError,'historical_duplicate'): gate.validate(rows,{rows[0]['content_sha256']},prior)
 def test_duplicate_batch(self):
  rows,prior=fixture()
  with self.assertRaisesRegex(ValueError,'within_batch_duplicate'): gate.validate(rows+[rows[0]],set(),prior)
 def test_impossible_bucket(self):
  rows,prior=fixture(); rows[0]['input_tokens']=80
  with self.assertRaisesRegex(ValueError,'input_bucket_infeasible'): gate.validate(rows,set(),prior)
 def test_bad_context(self):
  rows,prior=fixture(); rows[0].update(input_tokens=2000,input_token_range=[1,2048],max_tokens=64)
  with self.assertRaisesRegex(ValueError,'context_budget'): gate.validate(rows,set(),prior)
 def test_missing_feedback(self):
  rows,prior=fixture()
  with self.assertRaisesRegex(ValueError,'warm_history_insufficient'): gate.validate(rows,set(),prior[:-1])
 def test_final_rejected(self):
  rows,prior=fixture(); rows[0]['split']='final'
  with self.assertRaisesRegex(ValueError,'forbidden_split'): gate.validate(rows,set(),prior)
 def test_generator_both_instructions(self):
  cfg=workload.load_config()
  for split in cfg['allowed_splits']:
   for seed in cfg[split]['seeds']:
    descriptors=workload.build_descriptors(cfg,split,seed)
    for r in descriptors: r['input_token_range']=[1,1500]
    result=workload.materialize_trace(descriptors,lambda p:list(range(len(p))),cfg)
    self.assertEqual(len(result),24); self.assertEqual({r['arm'] for r in result},{'O' if split=='calibration' else 'C'})
  with self.assertRaises(ValueError): workload.build_descriptors(cfg,'final',39101)
 def test_public_floor_and_hard_reservation(self):
  row={'prediction':20,'grammar_ceiling':30,'max_tokens':64,'input_tokens':50,'input_cost_coefficient':1,'output_cost_coefficient':10}
  self.assertEqual(estimate(row,0),estimate(row,4)); self.assertEqual(reserve(row),115)
 def test_transient_empty_log_read(self):
  history={(c,k):[0]*32 for c in ('interactive','background') for k in ('extract','check','organize')}
  ctl=Controller('M',100,history,Path('not-opened')); ctl.capfile=Mock(); ctl.buffer=b''; ctl.emit=Mock()
  ctl.capfile.read.side_effect=[OSError(errno.ENODATA,'No data available'),b'SCHED_CAP_RELEASE clientid=A event_ms=0\n']
  ctl.consume_native(); self.assertFalse(ctl.released); ctl.consume_native(); self.assertIn('A',ctl.released)
 def test_error_does_not_skip_file_bytes(self):
  history={(c,k):[0]*32 for c in ('interactive','background') for k in ('extract','check','organize')}
  ctl=Controller('M',100,history,Path('not-opened')); ctl.capfile=Mock(); ctl.buffer=b''; ctl.emit=Mock()
  line=b'SCHED_CAP_RELEASE clientid=B event_ms=0\n'; ctl.capfile.read.side_effect=[OSError(errno.ENODATA,'partial underlying read'),line]
  ctl.consume_native(); self.assertEqual(ctl.read_offset,0); ctl.consume_native(); self.assertIn('B',ctl.released); self.assertEqual(ctl.read_offset,len(line)); self.assertEqual([c.args for c in ctl.capfile.seek.call_args_list],[(0,),(0,)])
 def test_nonzero_history_is_used(self):
  history={(c,k):[0]*24+[4]*8 for c in ('interactive','background') for k in ('extract','check','organize')}
  ctl=Controller('M',100,history,Path('not-opened'))
  self.assertEqual(ctl.m_margin({'category':'background','task_kind':'check'}),4)
if __name__=='__main__': unittest.main()
