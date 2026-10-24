"""Model-free gates for a paired repair pilot and capped-length interpretation."""
from copy import deepcopy
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('repair_runner', ROOT / 'scripts/replay/79d_run_p1_prompt_task_repair_v2.py')
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


class RepairRunnerTests(unittest.TestCase):
    def test_registration_and_old_evidence_are_valid(self):
        result = runner.model_free_checks(runner.base.read(runner.CONFIG))
        self.assertEqual((result['paired_completions'], result['fresh_development_completions'],
                          result['warmup_completions'], result['expected_cap_events']), (16, 24, 2, 84))
        self.assertFalse(result['cache_prompt'])

    def test_output_budget_changes_are_rejected(self):
        cfg = deepcopy(runner.base.read(runner.CONFIG))
        cfg['max_output_tokens']['interactive'] = 128
        with self.assertRaises(AssertionError):
            runner.model_free_checks(cfg)

    def test_repaired_sentinel_bucket_is_measured_not_inherited(self):
        cfg = runner.base.read(runner.CONFIG)
        self.assertEqual(runner.actual_bucket(79, 'interactive', cfg), (0, [32, 79]))
        self.assertEqual(runner.actual_bucket(80, 'interactive', cfg), (1, [80, 143]))
        self.assertEqual(runner.actual_bucket(257, 'interactive', cfg), (None, None))

    def test_eos_at_the_output_limit_is_not_automatic_quality_success(self):
        row = dict(req_id='x', repair_role='paired_sentinel', prompt_version='v2', pair_index=1,
            source_case='c05', source_req_id='old', category='interactive', input_tokens=151,
            input_token_bucket=2, max_tokens=64, output_tokens=64, terminal='completed',
            cache_prompt=False, new_prompt_tokens=151, cached_prompt_tokens=0, content='未完任务',
            native_final={'stop_type': 'eos', 'truncated': False})
        value = runner.diagnostics(row)
        self.assertEqual(value['stop_type'], 'eos')
        self.assertIn('pending', value['assistant_qualitative_review'])
        self.assertIsNone(value['repetition']['quality_verdict'])


if __name__ == '__main__':
    unittest.main()
