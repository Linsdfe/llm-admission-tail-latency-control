"""Meaningful pilot checks: split isolation, source truth and input feasibility."""
from collections import Counter
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts/replay'))
import p1_verifiable_workload_v3 as v3


class VerifiableWorkloadTests(unittest.TestCase):
    def test_fixed_crossed_matrix_and_forbidden_splits(self):
        rows = v3.build_descriptors()
        self.assertEqual(len(rows), 24)
        self.assertEqual(set(Counter((r['category'], r['input_token_bucket']) for r in rows).values()), {4})
        self.assertEqual(set(Counter((r['category'], r['task_kind']) for r in rows).values()), {4})
        for category in v3.CATEGORIES:
            for kind in v3.KINDS:
                self.assertEqual({r['input_token_bucket'] for r in rows if r['category'] == category and r['task_kind'] == kind}, {0,1,2})
        for split in ('calibration', 'final'):
            with self.assertRaises(ValueError):
                v3.build_descriptors(split=split)
        with self.assertRaises(ValueError):
            v3.build_descriptors(seed=22001)

    def test_expected_lines_derive_from_only_present_source_records(self):
        for descriptor in v3.build_descriptors():
            for extra in (0, 5, 128):
                data = v3.task_material(descriptor, extra)
                self.assertTrue(data['oracle_evaluation_only'])
                self.assertEqual(len(data['target_ids']), descriptor['target_count'])
                if descriptor['task_kind'] == 'check':
                    left = {r['id']:r['value'] for r in data['left_records']}
                    right = {r['id']:r['value'] for r in data['right_records']}
                    expected = [key+'|'+('一致' if left[key] == right[key] else '不一致') for key in data['target_ids']]
                else:
                    source = {r['id']:r['value'] for r in data['source_records']}
                    expected = [key+'|'+source[key] for key in data['target_ids']]
                    if descriptor['task_kind'] == 'organize':
                        original_order = [r['id'] for r in data['source_records'] if r['id'] in set(data['target_ids'])]
                        self.assertEqual(data['target_ids'], list(reversed(original_order)))
                self.assertEqual(data['expected_lines'], expected)
                self.assertEqual(data['expected_answer'], '\n'.join(expected))

    def test_additional_records_do_not_change_target_truth(self):
        for descriptor in v3.build_descriptors():
            initial = v3.task_material(descriptor, 0)
            extended = v3.task_material(descriptor, 40)
            self.assertEqual(initial['target_ids'], extended['target_ids'])
            self.assertEqual(initial['expected_lines'], extended['expected_lines'])
            prompt = v3.render_candidate(descriptor, 40)
            self.assertNotIn('expected_answer', prompt)
            self.assertNotIn('oracle', prompt)
            self.assertTrue(prompt.endswith('<|im_start|>assistant\n'))

    def test_actual_tokenizer_callback_and_no_bucket_relaxation(self):
        descriptor = v3.build_descriptors()[0]
        called = []
        def tokenizer(prompt):
            called.append(prompt)
            return list(range(60))
        row = v3.materialize_trace([descriptor], tokenizer)[0]
        self.assertEqual(row['input_tokens'], 60)
        self.assertEqual(called[0], row['prompt'])
        self.assertFalse(row['cache_prompt'])
        self.assertFalse(row['ignore_eos'])
        self.assertIn('expected_lines', row)
        with self.assertRaises(ValueError):
            v3.materialize_trace([descriptor], lambda prompt:list(range(100)))
        with self.assertRaises(ValueError):
            v3.materialize_trace([descriptor], lambda prompt:[True])


if __name__ == '__main__':
    unittest.main()
