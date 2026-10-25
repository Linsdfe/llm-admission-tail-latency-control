"""O/C instruction contrast, shared truth/grammar, and offline-reference isolation."""
from collections import Counter
from copy import deepcopy
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('v5_instruction_runner',ROOT/'scripts/replay/79g_run_p1_explicit_instructions_v5.py')
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def fixture_rows():
    cfg = runner.base.read(runner.CONFIG)
    descriptors = runner.workload.build_descriptors(cfg,seed=25001)
    return cfg,runner.model_free_rows(descriptors)


class ExplicitInstructionRunnerTests(unittest.TestCase):
    def test_fixed_registration_and_no_budget_seed_or_design_drift(self):
        cfg,_ = fixture_rows()
        result = runner.model_free_checks(cfg)
        self.assertEqual((result['unique_tasks'],result['measured_completions'],
            result['completion_requests'],result['expected_cap_events']),(24,48,50,100))
        self.assertEqual(result['conditions'],{'O':24,'C':24})
        self.assertEqual(result['old_frozen_sources_unchanged'],61)
        self.assertTrue(result['same_pair_grammar'])
        self.assertFalse(result['input_tokens_matched'])
        for field,value in [('max_output_tokens',{'interactive':64,'background':768}),
            ('development',dict(cfg['development'],seeds=[24001])),('cache_prompt',True),
            ('comparison',dict(cfg['comparison'],pair_order='all OC'))]:
            changed = deepcopy(cfg)
            changed[field] = value
            with self.assertRaises((AssertionError,ValueError)):
                runner.model_free_checks(changed)

    def test_every_new_task_has_adjacent_balanced_arms_and_88_fields(self):
        _,full = fixture_rows()
        firsts = runner.check_pair_design(full)
        self.assertEqual(Counter(row['condition_order'] for row in firsts),{'OC':12,'CO':12})
        for row in firsts:
            self.assertTrue(row['pair_id'].startswith('v5-25001-'))
            self.assertEqual(row['pair_id'],row['content_id'])
        for field in ('task_kind','input_token_bucket'):
            balanced = Counter((row['category'],row[field],row['condition_order']) for row in firsts)
            self.assertEqual(len(balanced),12)
            self.assertEqual(set(balanced.values()),{2})
        for arm in ('O','C'):
            self.assertEqual(sum(len(row['target_ids']) for row in full if row['arm']==arm),88)
        for left,right in zip(full[::2],full[1::2]):
            self.assertNotEqual(left['prompt'],right['prompt'])
            self.assertEqual(runner.replay_row(left)['grammar'],runner.replay_row(right)['grammar'])

    def test_changed_truth_target_order_or_missing_duplicate_arm_is_rejected(self):
        _,full = fixture_rows()
        mutations = []
        changed = deepcopy(full); changed[1]['source_records'][0]['value']='99'; mutations.append(changed)
        changed = deepcopy(full); changed[1]['expected_answer']='R1|99'; mutations.append(changed)
        changed = deepcopy(full); changed[1]['target_ids']=['R999']; mutations.append(changed)
        changed = deepcopy(full); changed[1]['arm']=changed[0]['arm']; mutations.append(changed)
        changed = deepcopy(full); changed[1]['req_id']=changed[0]['req_id']; mutations.append(changed)
        changed = deepcopy(full); changed[1],changed[2]=changed[2],changed[1]; mutations.append(changed)
        changed = deepcopy(full); changed[1]['prompt']=changed[0]['prompt']; mutations.append(changed)
        mutations.append(full[:-1])
        for changed in mutations:
            with self.assertRaises((AssertionError,ValueError)):
                runner.check_pair_design(changed)

    def test_both_payloads_use_same_public_grammar_and_no_offline_metadata(self):
        _,full = fixture_rows()
        for reference in full:
            projected = runner.replay_row(reference)
            self.assertFalse(runner.FORBIDDEN & projected.keys())
            self.assertEqual(set(runner.completion_payload(projected)),runner.PAYLOAD_KEYS|{'grammar'})
            self.assertTrue(projected['grammar'])
            self.assertEqual(projected['grammar_sha256'],runner.budget.text_sha(projected['grammar']))
            changed = deepcopy(reference)
            changed.update(expected_answer='SECRET_OFFLINE_ONLY',expected_lines=['SECRET_OFFLINE_ONLY'],
                source_records=[{'id':'secret','value':'SECRET_OFFLINE_ONLY'}],
                left_records=[{'id':'secret','value':'SECRET_OFFLINE_ONLY'}],
                right_records=[{'id':'secret','value':'SECRET_OFFLINE_ONLY'}],
                target_ids=['secret'],unregistered_extra='SECRET_OFFLINE_ONLY')
            self.assertEqual(runner.replay_row(changed),projected)
            self.assertNotIn('SECRET_OFFLINE_ONLY',str(runner.completion_payload(projected)))

    def test_actual_plan_accepts_different_lengths_in_same_bucket_and_rejects_escape(self):
        cfg,full = fixture_rows()
        # These are explicit unmeasured token-count fixtures, not tokenizer evidence.
        for row in full:
            low,_ = cfg['input_token_buckets'][row['category']][row['input_token_bucket']]
            row.update(input_tokens=low+(1 if row['arm']=='C' else 0),
                token_ids_sha256='unit_not_actual_tokenizer_'+row['arm'])
        plan = runner.validate_plan(full,cfg)
        self.assertEqual(len(plan),48)
        for left,right in zip(plan[::2],plan[1::2]):
            self.assertNotEqual(left['input_tokens'],right['input_tokens'])
            self.assertEqual(left['grammar'],right['grammar'])
        changed = deepcopy(full)
        changed[1]['input_tokens']=changed[1]['input_token_range'][1]+1
        with self.assertRaises(AssertionError): runner.validate_plan(changed,cfg)
        changed = deepcopy(full)
        changed[1]['max_tokens']=2048
        with self.assertRaises(AssertionError): runner.validate_plan(changed,cfg)

    def test_final_calibration_and_unregistered_seed_are_rejected(self):
        cfg,_ = fixture_rows()
        for split,seed in [('final',25001),('calibration',25001),('development',24001),('development',25002)]:
            with self.assertRaises(ValueError): runner.workload.build_descriptors(cfg,split=split,seed=seed)
        descriptors = runner.workload.build_descriptors(cfg,seed=25001)
        for descriptor in descriptors:
            with self.assertRaises(ValueError): runner.workload.task_material(dict(descriptor,seed=24001),0)
        descriptors[1] = deepcopy(descriptors[0])
        with self.assertRaises(AssertionError): runner.model_free_rows(descriptors)


if __name__=='__main__': unittest.main()
