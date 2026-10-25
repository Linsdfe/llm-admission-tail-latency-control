"""Counterexamples for the new-seed paired output-grammar collection plan."""
from collections import Counter
from copy import deepcopy
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('v4_pair_runner',ROOT/'scripts/replay/79f_run_p1_output_constraints_v4.py')
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def fixture_unique():
    cfg = runner.base.read(runner.CONFIG)
    result=[]
    for row in runner.workload.build_descriptors(cfg,seed=24001):
        material=runner.workload.task_material(row,0)
        prompt=runner.workload.render_candidate(row,0)
        result.append(dict(row,**material,prompt=prompt,input_tokens=1,
            content_sha256=runner.budget.text_sha(prompt),token_ids_sha256='unit_not_actual_tokenizer'))
    return cfg,result


class OutputConstraintRunnerTests(unittest.TestCase):
    def test_fixed_new_registration_counts_and_output_caps(self):
        cfg,_=fixture_unique()
        result=runner.model_free_checks(cfg)
        self.assertEqual((result['unique_tasks'],result['measured_completions'],
            result['completion_requests'],result['expected_cap_events']),(24,48,50,100))
        self.assertEqual(result['conditions'],{'U':24,'G':24})
        self.assertEqual(result['old_frozen_sources_unchanged'],49)
        for field,value in [('max_output_tokens',{'interactive':64,'background':768}),
            ('development',dict(cfg['development'],seeds=[23001])),('cache_prompt',True),
            ('comparison',dict(cfg['comparison'],pair_order='all UG'))]:
            changed=deepcopy(cfg)
            changed[field]=value
            with self.assertRaises((AssertionError,ValueError)):
                runner.model_free_checks(changed)

    def test_every_fresh_task_has_both_arms_and_balanced_adjacent_order(self):
        _,unique=fixture_unique()
        full=runner.paired_evaluation_rows(unique)
        firsts=runner.check_pair_design(full)
        self.assertEqual(len(full),48)
        self.assertEqual(Counter(row['condition_order'] for row in firsts),{'UG':12,'GU':12})
        for row in firsts:
            self.assertTrue(row['pair_id'].startswith('v4-24001-'))
            self.assertEqual(row['pair_id'],row['content_id'])
        balanced=Counter((row['category'],row['task_kind'],row['condition_order']) for row in firsts)
        self.assertEqual(set(balanced.values()),{2})
        self.assertEqual(len(balanced),12)

    def test_pair_prompt_change_or_missing_reordered_duplicate_arm_is_rejected(self):
        _,unique=fixture_unique()
        full=runner.paired_evaluation_rows(unique)
        mutations=[]
        changed=deepcopy(full); changed[1]['prompt']+='extra'; mutations.append(changed)
        changed=deepcopy(full); changed[1]['arm']=changed[0]['arm']; mutations.append(changed)
        changed=deepcopy(full); changed[1]['req_id']=changed[0]['req_id']; mutations.append(changed)
        changed=deepcopy(full); changed[1],changed[2]=changed[2],changed[1]; mutations.append(changed)
        mutations.append(full[:-1])
        for changed in mutations:
            with self.assertRaises(AssertionError): runner.check_pair_design(changed)

    def test_projection_uses_public_instruction_and_never_offline_answer(self):
        _,unique=fixture_unique()
        full=runner.paired_evaluation_rows(unique)
        for reference in full:
            projected=runner.replay_row(reference)
            self.assertFalse(runner.FORBIDDEN & projected.keys())
            self.assertEqual(projected['prompt'],reference['prompt'])
            expected_keys=runner.PAYLOAD_KEYS | ({'grammar'} if reference['arm']=='G' else set())
            self.assertEqual(set(runner.completion_payload(projected)),expected_keys)
            self.assertEqual('grammar' in projected,reference['arm']=='G')
            changed=deepcopy(reference)
            changed.update(expected_answer='SECRET_OFFLINE_ONLY',expected_lines=['SECRET_OFFLINE_ONLY'],
                source_records=[{'id':'secret','value':'SECRET_OFFLINE_ONLY'}],
                left_records=[{'id':'secret','value':'SECRET_OFFLINE_ONLY'}],
                right_records=[{'id':'secret','value':'SECRET_OFFLINE_ONLY'}],
                target_ids=['secret'],unregistered_extra='SECRET_OFFLINE_ONLY')
            self.assertEqual(runner.replay_row(changed),projected)
            self.assertNotIn('SECRET_OFFLINE_ONLY',str(runner.completion_payload(projected)))

    def test_wrong_split_seed_and_duplicate_unique_task_are_rejected(self):
        cfg,unique=fixture_unique()
        for split,seed in [('final',24001),('calibration',24001),('development',23001),('development',24002)]:
            with self.assertRaises(ValueError):
                runner.workload.build_descriptors(cfg,split=split,seed=seed)
        for descriptor in unique:
            changed=dict(descriptor,seed=23001)
            with self.assertRaises(ValueError): runner.workload.task_material(changed,0)
        unique[1]=deepcopy(unique[0])
        with self.assertRaises(AssertionError): runner.paired_evaluation_rows(unique)


if __name__=='__main__':
    unittest.main()
