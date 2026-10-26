import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts/replay'))
import p1_live_control_v11 as worst
import p1_live_control_v12 as clipped
class RoleTests(unittest.TestCase):
 def setUp(self): self.row={'prediction':40,'grammar_ceiling':50,'max_tokens':384,'input_tokens':300,'input_cost_coefficient':1,'output_cost_coefficient':10}
 def test_same_maximum_does_not_force_soft_floor(self):
  self.assertEqual(worst.estimate(self.row,0)[0],50); self.assertEqual(clipped.estimate(self.row,0)[0],40); self.assertEqual(clipped.estimate(self.row,2)[0],42)
 def test_public_max_still_limits_soft_cost(self): self.assertEqual(clipped.estimate(self.row,20)[0],50)
 def test_hard_reservation_unchanged(self): self.assertEqual(worst.reserve(self.row),clipped.reserve(self.row)); self.assertEqual(clipped.reserve(self.row),685)
 def test_no_grammar_upper(self): self.row['grammar_ceiling']=None; self.assertEqual(clipped.estimate(self.row,2)[0],42)
if __name__=='__main__': unittest.main()
