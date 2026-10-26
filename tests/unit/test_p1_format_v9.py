import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts/replay'))
import p1_format_workload_v9 as w
import p1_format_estimator_v9 as e
class FormatTest(unittest.TestCase):
 def test_split_and_format(self):
  cfg=w.load_config(); groups={s:[r for seed in cfg[s]['seeds'] for r in w.build_descriptors(cfg,s,seed)] for s in cfg['allowed_splits']}
  self.assertEqual([len(x) for x in groups.values()],[48,48])
  self.assertFalse({r['template_family'] for r in groups['calibration']}&{r['template_family'] for r in groups['development']})
  for split,group in groups.items():
   for r in group:
    m=w.task_material(r); self.assertEqual(len(m['expected_lines']),r['target_count'])
    prompt=w.render_candidate(r,0,'O' if split=='calibration' else 'C'); self.assertIn(m['target_ids'][0],prompt)
 def test_materialization_each_instruction(self):
  cfg=w.load_config()
  for split,seed in [('calibration',29101),('development',29201)]:
   descriptors=w.build_descriptors(cfg,split,seed)
   for r in descriptors: r['input_token_range']=[1,1500]
   result=w.materialize_trace(descriptors,lambda p:list(range(len(p))),cfg)
   self.assertEqual(len(result),24)
   self.assertEqual({r['arm'] for r in result},{'O' if split=='calibration' else 'C'})
 def test_no_final(self):
  with self.assertRaises(ValueError): w.build_descriptors(w.load_config(),'final',39101)
 def test_no_development_fit(self):
  with self.assertRaises(AssertionError): e.fit([{'split':'development'}],e.CANDIDATES[0])
 def test_shift_feature(self):
  rows=[{'split':'calibration','seed':29101+i//24,'category':'interactive' if i%2 else 'background','task_kind':'extract','format_tokens':6+i//24,'max_tokens':64,'output_tokens':7+i//24} for i in range(48)]
  model=e.select(rows); self.assertEqual(model['candidate'],'format_residual')
  self.assertEqual(e.apply({'category':'interactive','task_kind':'extract','format_tokens':9,'max_tokens':64},model),{'prediction':10,'upper':10})
if __name__=='__main__': unittest.main()
