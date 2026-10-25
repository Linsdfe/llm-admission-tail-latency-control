import importlib.util
from pathlib import Path
import unittest
ROOT=Path(__file__).resolve().parents[2]
def load(name,path):
    spec=importlib.util.spec_from_file_location(name,ROOT/path); m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m
e=load('eval98','scripts/analysis/98_analyze_p1_model_adaptation_v6.py')
a=load('audit_v6','tests/integration/audit_p1_model_adaptation_v6.py')

class EvaluationTests(unittest.TestCase):
    def test_pair_cells(self):
        self.assertEqual({e.pair_kind(x,y) for x in (False,True) for y in (False,True)},set(e.PAIR_KEYS))
    def test_instruction_model_separation(self):
        self.assertEqual(e.instruction_reference({'arm':'B','instruction_arm':'C'})['arm'],'C')
        for ref in ({'arm':'C','instruction_arm':'C'},{'arm':'A','instruction_arm':'O'}):
            with self.assertRaises(AssertionError): e.instruction_reference(ref)
    def test_independent_scoring_counterexamples(self):
        ref={'task_kind':'extract','target_ids':['R2','R1'],'source_records':[{'id':'R1','value':'41'},{'id':'R2','value':'42'}]}
        expected=[['R2','42'],['R1','41']]
        for content in ('R2|42\nR1|41','R2|43\nR1|41','R2|42','R2|42\nR1|41\nR1|41','R2|42\nR1|41\nR9|99','R1|41\nR2|42','ID|值\nR2|42\nR1|41','R2|042\nR1|41','R2|42\nR1|41|x','\r\n R2|42 \r\n R1|41 \n'):
            x=e.old.assess(ref,content); y=a.old.independent_fields(content,expected,'extract')
            self.assertEqual(x['business_correct'],y['business_correct'])
            self.assertEqual(x['schema_valid'],y['syntax_valid'])
            self.assertEqual(x['target_fields_correct'],y['correct_fields'])
            self.assertEqual(x['target_fields_expected'],y['planned_fields'])
    def test_all_same_comparison_is_wrong_on_different_fact(self):
        ref={'task_kind':'check','target_ids':['R1'],'left_records':[{'id':'R1','value':'41'}],'right_records':[{'id':'R1','value':'42'}]}
        expected=a.old.independent_answer(dict(ref,category='interactive'))
        self.assertEqual(expected,[['R1','不一致']])
        self.assertFalse(e.old.assess(ref,'R1|一致')['business_correct'])
        self.assertFalse(a.old.independent_fields('R1|一致',expected,'check')['business_correct'])

if __name__=='__main__': unittest.main()
