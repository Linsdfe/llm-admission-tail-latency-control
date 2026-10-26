import importlib.util,unittest
from pathlib import Path
p=Path(__file__).resolve().parents[2]/'scripts/analysis/103_audit_p1_margin_decision_space.py'; s=importlib.util.spec_from_file_location('audit',p); a=importlib.util.module_from_spec(s); s.loader.exec_module(a)
class MarginTests(unittest.TestCase):
 def test_floor_absorbs_small_margins(self):
  self.assertEqual(a.length(40,0,384,48),a.length(40,4,384,48)); self.assertEqual(a.length(40,16,384,48),56)
 def test_fallback_and_quantile(self):
  self.assertEqual(a.margin([0]*31,32,.95,1),1); self.assertEqual(a.margin([0]*32,32,.95,1),0)
 def test_real_decision_difference_at_common_state(self):
  w=a.witness(12,10,20); self.assertFalse(w['M_admit']); self.assertTrue(w['B3_admit']); self.assertIsNone(a.witness(10,10,20)); self.assertIsNone(a.witness(12,10,5))
 def test_common_bypass_and_hard_guard(self):
  self.assertTrue(a.admit(50,10,100,active=0)); self.assertTrue(a.admit(50,10,100,protected=True)); self.assertFalse(a.admit(1,10,0,active=0,hard=False))
if __name__=='__main__': unittest.main()
