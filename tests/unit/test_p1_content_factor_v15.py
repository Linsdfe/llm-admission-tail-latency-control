from collections import Counter
import copy
import json
from pathlib import Path
import sys
import unittest

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'scripts/replay'))
import p1_content_factor_v15 as d


class Diagnostic(unittest.TestCase):
    def setUp(self):
        corpus=json.loads((ROOT/'docs/experiments/20261026-p1-content-profile-v14/corpus.json').read_text(encoding='utf-8'))
        lookup={r['req_id']:r for r in corpus}
        self.fixtures=[lookup[k] for k in d.config()['source_req_ids']]
        self.plan=d.plans(self.fixtures)
        for r in self.plan:r['input_tokens']=100

    def test_exact32_and16_prompts(self): self.assertEqual(d.validate(self.plan,self.fixtures)['distinct_prompts'],16)

    def test_time_view_is_exact_and_grammar_has_no_answer_filter(self):
        for r in self.plan:
            ref=self.fixtures[r['fixture_index']]
            if r['condition'][0]=='T': self.assertEqual(r['prompt'],ref['prompt'])
            if r['condition'][1]=='G': self.assertEqual(r['grammar'],ref['grammar'])
            else:self.assertNotIn('grammar',r)

    def test_current_state_uses_public_latest_phrase(self):
        for ref in self.fixtures:
            view=d.current_view(ref['prompt'])
            self.assertNotIn('09:10',view); self.assertNotIn('09:40',view)
            for f in ref['facts_evaluation_only']:
                self.assertIn(f['id']+'：当前状态='+max(f['events_in_written_order'])[1],view)

    def test_position_and_transition_balance(self):
        positions=Counter((r['condition'],r['condition_position']) for r in self.plan)
        self.assertEqual(set(positions.values()),{2})
        transitions=Counter()
        for order in d.config()['orders']:
            transitions.update(zip(order,order[1:]))
        self.assertEqual(len(transitions),12);self.assertEqual(set(transitions.values()),{1})

    def test_reject_truth_context_and_wrong_purpose(self):
        for key,value in [('expected_answer','无'),('input_tokens',2048),('purpose','online_calibration')]:
            plan=copy.deepcopy(self.plan);plan[0][key]=value
            with self.assertRaises(ValueError):d.validate(plan,self.fixtures)

    def test_whitespace_is_auxiliary_only(self):
        ref=self.fixtures[1];r={'terminal':'completed','finish_reason':'stop','content':ref['expected_answer']+'\n'}
        q=d.quality(ref,r);self.assertFalse(q['strict_correct']);self.assertTrue(q['outer_whitespace_field_correct'])

    def test_explanation_is_not_scored_as_answer(self):
        ref=self.fixtures[1];r={'terminal':'completed','finish_reason':'stop','content':'编号如下：\n'+ref['expected_answer']}
        q=d.quality(ref,r);self.assertFalse(q['format_valid']);self.assertFalse(q['outer_whitespace_field_correct'])

    def test_limit_output_does_not_pass(self):
        ref=self.fixtures[1];r={'terminal':'completed','finish_reason':'length','content':ref['expected_answer']}
        q=d.quality(ref,r);self.assertTrue(q['output_limit']);self.assertFalse(q['strict_correct'])


if __name__=='__main__':unittest.main()
