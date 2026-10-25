import importlib.util
from pathlib import Path
import unittest
from copy import deepcopy

ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('adaptation_v6',ROOT/'scripts/replay/79h_run_p1_model_adaptation_v6.py')
r=importlib.util.module_from_spec(spec); spec.loader.exec_module(r)

class AdaptationDesignTests(unittest.TestCase):
    def setUp(self): self.cfg=r.base.read(r.CONFIG)
    def test_registered_balance_and_all_failures_retained(self):
        self.assertEqual(r.model_free_checks(self.cfg)['expected_cap_events'],112)
        source=r.source_rows(self.cfg)
        full=[v for bid in range(1,5) for v in r.block_rows(self.cfg,bid)]
        self.assertEqual({v['source_req_id'] for v in full},{v['req_id'] for v in source})
        self.assertEqual(len(full),48)
    def test_source_changes_and_missing_cases_rejected(self):
        cfg=deepcopy(self.cfg); cfg['source_evaluation_plan_sha256']='0'*64
        with self.assertRaises(AssertionError): r.source_rows(cfg)
        full=[v for bid in range(1,5) for v in r.block_rows(self.cfg,bid)]
        with self.assertRaises(AssertionError): r.check_design(full[:-1])
        full[0]['prompt']+='changed'
        with self.assertRaises(AssertionError): r.check_design(full)
    def test_oracle_not_in_actual_payload(self):
        for row in r.block_rows(self.cfg,1):
            plan=r.replay_row(row); payload=r.completion_payload(plan)
            self.assertFalse(r.FORBIDDEN & payload.keys())
            self.assertEqual(set(payload),r.PAYLOAD_KEYS|{'grammar'})
            changed=deepcopy(row); changed['expected_answer']='malicious'; changed['source_records']=[]
            self.assertEqual(payload,r.completion_payload(r.replay_row(changed)))
    def test_model_tokenization_is_used_and_out_of_bucket_stops(self):
        full=r.block_rows(self.cfg,1,lambda prompt:[7]*3000)
        self.assertTrue(all(v['input_tokens']==3000 for v in full))
        with self.assertRaises(AssertionError): r.validate_plan(full,self.cfg)
    def test_model_arm_is_distinct_from_instruction_arm(self):
        for bid in range(1,5):
            for row in r.block_rows(self.cfg,bid):
                self.assertEqual(row['instruction_arm'],'C')
                self.assertIn(row['arm'],('A','B'))
                self.assertIn(row['condition_order'],('AB','BA'))

if __name__=='__main__': unittest.main()
