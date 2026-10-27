import copy
import sys
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts/replay'))
import p1_content_workload_v14 as tasks
from p1_profile_preflight_v14 import validate


class Profile(unittest.TestCase):
    def setUp(self):
        self.full = []
        for d in tasks.descriptors():
            r = tasks.materialize(d)
            r.update(input_tokens=300, token_ids_sha256='fixture', grammar=tasks.grammar(r['prompt']))
            self.full.append(r)
        self.public = [tasks.public_row(r) for r in self.full]

    def test_independent_oracle_and_public_parser_all_states(self):
        self.assertEqual(len(self.full), 48)
        self.assertEqual(len({r['content_sha256'] for r in self.full}), 48)
        for r in self.full:
            self.assertEqual(tasks.parse_public(r['prompt']), r['expected_ids'])
            for fact in r['facts_evaluation_only']:
                latest = max(fact['events_in_written_order'])[1]
                self.assertEqual(latest in tasks.DOWN, fact['current_down'])

    def test_grammar_includes_every_public_subset(self):
        for r in self.full:
            choices = tasks.alternatives(r['prompt'])
            self.assertEqual(len(choices), 2 ** r['record_count'])
            self.assertEqual(len(choices), len(set(choices)))
            self.assertIn(r['expected_answer'], choices)
            self.assertIn('无', choices)
            self.assertIn('\n'.join(tasks.public_ids(r['prompt'])), choices)
            self.assertGreater(len({0 if s == '无' else len(s.splitlines()) for s in choices}), 3)

    def test_no_truth_in_payload(self):
        for r in self.public:
            self.assertFalse({'expected_answer','expected_ids','target_count_evaluation_only','facts_evaluation_only'} & r.keys())
        self.assertEqual(validate(self.public, set(), 'serial_task_profile')['unique_prompts'], 48)

    def test_profile_gate_cannot_authorize_online_updates(self):
        with self.assertRaises(ValueError): validate(self.public, set(), 'online_calibration')

    def test_duplicate_and_leak_fail(self):
        with self.assertRaises(ValueError): validate(self.public, {self.public[0]['content_sha256']}, 'serial_task_profile')
        changed = copy.deepcopy(self.public)
        changed[0]['expected_answer'] = '无'
        with self.assertRaises(ValueError): validate(changed, set(), 'serial_task_profile')

    def test_input_context_final_fail(self):
        for key, value in [('input_tokens', 1800), ('split','final'), ('seed',39101)]:
            changed = copy.deepcopy(self.public)
            changed[0][key] = value
            with self.assertRaises(ValueError): validate(changed, set(), 'serial_task_profile')

    def test_parser_rejects_unknown_and_missing_state(self):
        for text in ['R4100：', 'R4100：09:10 还不知道；09:40 待定', 'R4100：09:10 服务可用；09:10 服务不可用']:
            with self.assertRaises(ValueError): tasks.parse_public(text)

    def test_wrong_and_empty_answers_scored(self):
        r = next(r for r in self.full if r['expected_ids'])
        self.assertFalse(tasks.quality(r, {'content':'无','terminal':'completed'})['whole_correct'])
        self.assertTrue(tasks.quality(r, {'content':r['expected_answer'],'terminal':'completed'})['whole_correct'])
        self.assertFalse(tasks.quality(r, {'content':r['expected_answer'],'terminal':'timeout'})['whole_correct'])


if __name__ == '__main__':
    unittest.main()
