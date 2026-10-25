import importlib.util
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('v4_evaluation',ROOT/'scripts/analysis/97_analyze_p1_explicit_instructions_v5.py')
evaluation=importlib.util.module_from_spec(spec)
spec.loader.exec_module(evaluation)


def fixture(kind='extract'):
    row={'task_kind':kind,'target_ids':['R2','R1'],'source_records':[{'id':'R1','value':'41'},{'id':'R2','value':'42'}]}
    if kind=='check':
        row.pop('source_records')
        row.update(left_records=[{'id':'R1','value':'41'},{'id':'R2','value':'42'}],
            right_records=[{'id':'R1','value':'41'},{'id':'R2','value':'43'}])
    return row


class ConstraintEvaluationTests(unittest.TestCase):
    def test_schema_cannot_be_used_as_fact_correctness(self):
        for kind,content in [('extract','R2|43\nR1|41'),('check','R2|一致\nR1|一致')]:
            result=evaluation.assess(fixture(kind),content)
            self.assertTrue(result['schema_valid'])
            self.assertFalse(result['business_correct'])
            self.assertEqual((result['target_fields_correct'],result['target_fields_expected']),(1,2))

    def test_missing_duplicate_and_invalid_keep_fixed_target_denominator(self):
        for content in ('R2|42','R2|42\nR1|41\nR1|41','R2|42\nR1|41|extra'):
            result=evaluation.assess(fixture(),content)
            self.assertEqual(result['target_fields_expected'],2)
            self.assertEqual(result['target_fields_correct'],1)
            self.assertFalse(result['business_correct'])

    def test_auxiliary_fields_do_not_erase_extra_output(self):
        for content in ('R2|42\nR1|41\nR9|99','ID|值\nR2|42\nR1|41','R1|41\nR2|42'):
            result=evaluation.assess(fixture(),content)
            self.assertEqual(result['target_fields_correct'],2)
            self.assertFalse(result['schema_valid'])
            self.assertFalse(result['business_correct'])

    def test_primary_benign_whitespace_and_strict_value_domain(self):
        result=evaluation.assess(fixture(),'\r\n R2 | 42 \r\n R1|41 \r\n')
        self.assertTrue(result['business_correct'])
        self.assertTrue(result['schema_valid'])
        self.assertFalse(result['raw_text_exact'])
        for content in ('R2|042\nR1|41','R2|42件\nR1|41','R2|相同\nR1|41'):
            self.assertFalse(evaluation.assess(fixture(),content)['schema_valid'])

    def test_clear_prompt_facts_are_not_skipped(self):
        row={'task_kind':'check','arm':'C','target_ids':['R1'],
            'left_records':[{'id':'R1','value':'41'}],'right_records':[{'id':'R1','value':'42'}],
            'prompt':'<|im_start|>system\n只输出结果，无表头。<|im_end|>\n<|im_start|>user\nA\nR1|41\nB\nR1|42\n逐项比较A和B中R1的数值：相等写一致，否则写不一致。每行ID|结论。<|im_end|>\n<|im_start|>assistant\n'}
        self.assertTrue(evaluation.prompt_facts_match(row))
        changed=dict(row,prompt=row['prompt'].replace('R1|42','R1|41'))
        with self.assertRaises(ValueError): evaluation.prompt_facts_match(changed)
        changed=dict(row,right_records=[{'id':'R1','value':'41'}],expected_answer='R1|一致')
        with self.assertRaises(ValueError): evaluation.prompt_facts_match(changed)

    def test_two_prompts_have_one_answer_independent_grammar(self):
        row=fixture('check')
        row.update(arm='O')
        original=evaluation.expected_prompt(row)
        original_grammar=evaluation.expected_grammar(row)
        row['arm']='C'
        self.assertNotEqual(original,evaluation.expected_prompt(row))
        self.assertEqual(original_grammar,evaluation.expected_grammar(row))
        row['right_records'][0]['value']='99'
        self.assertEqual(original_grammar,evaluation.expected_grammar(row))

    def test_all_four_pair_outcomes_are_distinct(self):
        observed={evaluation.pair_kind(u,g) for u in (False,True) for g in (False,True)}
        self.assertEqual(observed,set(evaluation.PAIR_KEYS))


if __name__=='__main__': unittest.main()
