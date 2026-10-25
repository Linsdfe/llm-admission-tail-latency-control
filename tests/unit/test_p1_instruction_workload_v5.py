"""Pair truth, public-only grammar and dual-token-bucket counterexamples."""
from collections import Counter
from copy import deepcopy
from pathlib import Path
import re
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'scripts/replay'))
import p1_verifiable_workload_v5 as workload
import p1_instruction_constraints_v5 as constraints
import p1_output_constraints_v4 as old_constraints


def config():
    return workload.load_config()


class InstructionWorkloadTests(unittest.TestCase):
    def test_registered_matrix_and_split_seed_rejections(self):
        cfg = config()
        desc = workload.build_descriptors(cfg)
        self.assertEqual(len(desc),24)
        self.assertEqual(Counter(row['task_kind'] for row in desc),dict(extract=8,check=8,organize=8))
        self.assertEqual(set(Counter((r['category'],r['input_token_bucket']) for r in desc).values()),{4})
        for split in ('calibration','final'):
            with self.assertRaises(ValueError): workload.build_descriptors(cfg,split=split)
        for seed in (24001,25002,True):
            with self.assertRaises(ValueError): workload.build_descriptors(cfg,seed=seed)

    def test_same_truth_and_source_order_for_both_arm_transport_ids(self):
        for descriptor in workload.build_descriptors(config()):
            for extra in (0,7,128):
                material = workload.task_material(descriptor,extra)
                if descriptor['task_kind'] == 'check':
                    left = {record['id']:record['value'] for record in material['left_records']}
                    right = {record['id']:record['value'] for record in material['right_records']}
                    expected = [target+'|'+('一致' if left[target] == right[target] else '不一致') for target in material['target_ids']]
                else:
                    source = {record['id']:record['value'] for record in material['source_records']}
                    expected = [target+'|'+source[target] for target in material['target_ids']]
                self.assertEqual(material['expected_lines'],expected)
                for arm in ('O','C'):
                    paired = dict(descriptor,req_id=descriptor['req_id']+'-'+arm.lower(),arm=arm)
                    self.assertEqual(workload.task_material(paired,extra),material)
                    prompt = workload.render_candidate(paired,extra,arm)
                    self.assertNotIn('expected_answer',prompt)
                    self.assertNotIn('oracle_evaluation_only',prompt)
                original = workload.render_candidate(descriptor,extra,'O')
                clear = workload.render_candidate(descriptor,extra,'C')
                self.assertEqual(original.rsplit('\n',3)[0],clear.rsplit('\n',3)[0])

    def test_public_grammar_equal_between_arms_and_v4_bytes(self):
        for descriptor in workload.build_descriptors(config()):
            original = workload.render_candidate(descriptor,3,'O')
            clear = workload.render_candidate(descriptor,3,'C')
            grammar = constraints.grammar_from_prompt(clear,descriptor['category'])
            self.assertEqual(grammar,constraints.grammar_from_prompt(original,descriptor['category']))
            self.assertEqual(grammar,old_constraints.grammar_from_prompt(original,descriptor['category']))
            changed = re.sub(r'R(\d+)\|\d+',r'R\1|99',clear)
            self.assertEqual(grammar,constraints.grammar_from_prompt(changed,descriptor['category']))
            self.assertIn('"一致" | "不一致"' if descriptor['task_kind']=='check' else '[1-9] [0-9]',grammar)

    def test_only_exact_registered_commands_and_counts_are_allowed(self):
        for descriptor in workload.build_descriptors(config()):
            prompt = workload.render_candidate(descriptor,0,'C')
            self.assertEqual(constraints.parse_public_task(prompt,descriptor['category'])['target_ids'],workload.task_material(descriptor)['target_ids'])
            for changed in (prompt.replace('每行ID','任意ID'),prompt+'\n解释',prompt.replace('R1,','R1,R1,',1)):
                if changed == prompt: continue
                with self.assertRaises(ValueError): constraints.grammar_from_prompt(changed,descriptor['category'])
        with self.assertRaises(ValueError): workload.render_candidate(workload.build_descriptors(config())[0],0,'X')

    def test_materialize_returns_48_paired_rows_and_preserves_88_fields(self):
        cfg = config()
        desc = workload.build_descriptors(cfg)
        # Model-free deterministic lengths only: each source line adds units.
        def fake_tokenize(prompt):
            extra = len(re.findall(r'^R\d+\|',prompt,flags=re.MULTILINE))*7
            return list(range(30+extra+(8 if '对应的数值' in prompt or '逐项比较' in prompt or '只按' in prompt else 0)))
        rows = workload.materialize_trace(desc,fake_tokenize,cfg)
        self.assertEqual(len(rows),48)
        self.assertEqual(Counter(row['arm'] for row in rows),{'O':24,'C':24})
        self.assertEqual(Counter(row['condition_order'] for row in rows[::2]),{'OC':12,'CO':12})
        for arm in ('O','C'):
            self.assertEqual(sum(len(row['target_ids']) for row in rows if row['arm']==arm),88)
        for index in range(0,48,2):
            first,second = rows[index:index+2]
            self.assertEqual((first['condition_position'],second['condition_position']),(1,2))
            for key in ('content_id','pair_id','target_ids','expected_lines','extra_records'):
                self.assertEqual(first[key],second[key])
            for key in ('source_records','left_records','right_records'):
                self.assertEqual(first.get(key),second.get(key))
            for row in (first,second):
                self.assertEqual(row['input_tokens'],len(fake_tokenize(row['prompt'])))
                self.assertTrue(row['input_token_range'][0]<=row['input_tokens']<=row['input_token_range'][1])
                self.assertFalse(row['cache_prompt'])

    def test_both_arms_must_fit_same_bucket_without_relaxation(self):
        cfg = config()
        descriptor = workload.build_descriptors(cfg)[0]
        def one_arm_oversize(prompt):
            return list(range(60 if '对应的数值' not in prompt else 100))
        with self.assertRaisesRegex(ValueError,'Shared input bucket infeasible'):
            workload.materialize_trace([descriptor],one_arm_oversize,cfg)
        with self.assertRaises(ValueError): workload.materialize_trace([descriptor],lambda prompt:[True],cfg)
        rows = workload.materialize_trace([descriptor],lambda prompt:list(range(60 if '对应的数值' not in prompt else 70)),cfg)
        self.assertEqual([row['input_tokens'] for row in rows],[60,70])
        self.assertEqual(rows[0]['expected_lines'],rows[1]['expected_lines'])


if __name__=='__main__': unittest.main()
