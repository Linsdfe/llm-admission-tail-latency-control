import sys
from pathlib import Path
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts/replay'))
import p1_copy_workload_v16 as w

class CopyTests(unittest.TestCase):
    def test_balance(self):
        ds=w.descriptors();self.assertEqual(len(ds),64);self.assertEqual(len({d['req_id'] for d in ds}),64)
    def test_independent_rule(self):
        for d in w.descriptors():
            r=w.render(d,20);self.assertEqual(w.public_rule(r['prompt']),r['expected_answer'])
    def test_grammar_has_no_values(self):
        for d in w.descriptors():
            r=w.render(d,4);g=w.grammar(r['prompt']);self.assertEqual(g.count('[1-9]'),d['target_count']);self.assertNotIn(r['expected_answer'],g)
    def test_public_no_gold(self):
        p=w.public_row(w.render(w.descriptors()[0],4));self.assertNotIn('expected_answer',p);self.assertNotIn('source_records_evaluation_only',p)
    def test_final_disabled(self):
        d=dict(w.descriptors()[0],seed=39101)
        with self.assertRaises(ValueError):w.render(d,4)
    def test_serial_gate(self):
        with self.assertRaises(ValueError):w.validate([],set())
    def test_strict_quality(self):
        ref=w.render(w.descriptors()[0],0);a=dict(terminal='completed',finish_reason='stop',content=ref['expected_answer'])
        self.assertTrue(w.quality(ref,a)['whole_correct']);a['content']+='\n';self.assertFalse(w.quality(ref,a)['whole_correct'])
    def test_plan_counts_public(self):
        for d in w.descriptors():self.assertEqual(len(w.public_targets(w.render(d,0)['prompt'])),d['target_count'])

if __name__=='__main__':unittest.main()
