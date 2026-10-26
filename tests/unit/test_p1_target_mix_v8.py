import importlib.util
from fractions import Fraction
from pathlib import Path
import unittest
from copy import deepcopy
ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('v8_runner',ROOT/'scripts/replay/79j_run_p1_target_mix_v8.py')
r=importlib.util.module_from_spec(spec); spec.loader.exec_module(r)

class TargetMixTests(unittest.TestCase):
    def setUp(self): self.cfg=r.base.read(r.CONFIG)
    def test_registered_counts_and_forbidden_final(self):
        self.assertEqual(r.model_free(self.cfg)['completion_requests'],2114)
        for split in ('final','calibration'):
            with self.assertRaises(ValueError): r.workload.build_descriptors(self.cfg,split,28101)
    def test_weighted_method_not_arbitrary_threshold_tuning(self):
        a=r.target_mix.reference_artifact(ROOT)
        self.assertEqual((a['weighted_cutoff'],a['ordinary_cutoff'],a['weight_sum']),(12,36,'230'))
        self.assertEqual(r.target_mix.weighted_quantile([5,12,36],[Fraction(100),Fraction(100),Fraction(30)]),12)
        self.assertEqual(r.target_mix.weighted_quantile([12,36],[Fraction(1),Fraction(1)]),36)
    def test_target_arrivals_and_occurrences_are_explicit(self):
        ds=r.workload.build_descriptors(self.cfg,'development',28101)
        corpus=[dict(d,prompt='fixture',input_tokens=1) for d in ds]
        trace=r.make_trace(corpus)
        self.assertEqual(len(trace),230); self.assertEqual(len({x['source_req_id'] for x in trace}),24)
        self.assertEqual([x['arrival_offset_ms'] for x in trace if x['category']=='interactive'],list(range(0,100000,500)))
        bg=[x['arrival_offset_ms'] for x in trace if x['category']=='background']
        self.assertEqual(bg,[b*10000+250+j*50 for b in range(10) for j in range(3)])
        self.assertTrue(all('release_offset_ms' not in x for x in trace))
    def test_new_ids_and_content_namespace_are_fixed_before_answers(self):
        seen=set()
        for seed in self.cfg['development']['seeds']:
            for d in r.workload.build_descriptors(self.cfg,'development',seed):
                facts=r.workload.task_material(d,4); prompt=r.workload.render_candidate(d,4,'C')
                self.assertTrue(all(int(i[1:])>=10 for i in facts['target_ids']))
                self.assertNotIn(prompt,seen); seen.add(prompt)
                self.assertEqual(prompt,r.workload.render_candidate(d,4,'C'))
        self.assertEqual(len(seen),72)
    def test_classifier_ignores_future_output_and_matches_class_here(self):
        artifact=r.target_mix.reference_artifact(ROOT)
        for d in r.workload.build_descriptors(self.cfg,'development',28101):
            x=r.target_mix.classify(d,artifact)
            self.assertEqual(x['predicted_class'],'short' if d['category']=='interactive' else 'long')
            changed=dict(d,output_tokens=999,expected_answer='R10|99')
            self.assertEqual(x,r.target_mix.classify(changed,artifact))
    def test_existing_simple_rule_includes_aging(self):
        got=r.target_mix.ordering_witnesses()
        self.assertEqual([x['different'] for x in got],[False,True,False,True])
        self.assertEqual(got[1]['simple_order'][0],'B')
        self.assertEqual(got[1]['adaptation_order'][0],'I')
        self.assertEqual(got[3]['theta'],100)
    def test_no_answer_leak_in_actual_payload(self):
        d=r.workload.build_descriptors(self.cfg,'development',28101)[0]
        full=dict(d,**r.workload.task_material(d,4),prompt=r.workload.render_candidate(d,4,'C'),input_tokens=60,token_ids_sha256='fixture',content_sha256='fixture',arm='C')
        before=r.structured.completion_payload(r.replay_row(full)); full['expected_answer']='injected'
        self.assertEqual(before,r.structured.completion_payload(r.replay_row(full)))
        self.assertFalse(r.v6.FORBIDDEN & before.keys())

if __name__=='__main__': unittest.main()
