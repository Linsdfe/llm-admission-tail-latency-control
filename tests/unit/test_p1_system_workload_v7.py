import importlib.util
from pathlib import Path
import unittest
from copy import deepcopy
ROOT=Path(__file__).resolve().parents[2]
def load(name,path):
    spec=importlib.util.spec_from_file_location(name,ROOT/path); m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m
r=load('v7_runner','scripts/replay/79i_run_p1_system_workload_v7.py')
a=load('v7_analysis','scripts/analysis/99_analyze_p1_system_workload_v7.py')

def calibration():
    return [dict(x,output_tokens=6 if x['task_kind']=='extract' else 12 if x['task_kind']=='organize' else 5) for x in r.workload.build_descriptors(r.base.read(r.CONFIG),'calibration',27001)]

class SystemWorkloadTests(unittest.TestCase):
    def test_design_counts_and_final_refusal(self):
        cfg=r.base.read(r.CONFIG); self.assertEqual(r.model_free(cfg)['completion_requests'],332)
        for split,seed in [('final',29001),('development',27001),('calibration',27101)]:
            with self.assertRaises(ValueError): r.workload.build_descriptors(cfg,split,seed)
    def test_new_content_is_deterministic_and_disjoint(self):
        cfg=r.base.read(r.CONFIG); seen=set()
        for split in cfg['allowed_splits']:
            for seed in cfg[split]['seeds']:
                for row in r.workload.build_descriptors(cfg,split,seed):
                    # Zero-padding drafts can coincide across different intended
                    # input buckets. Compare nontrivial fact tables here; the
                    # collector separately rejects any final tokenized overlap.
                    p=r.workload.render_candidate(row,4,'C')
                    self.assertEqual(p,r.workload.render_candidate(row,4,'C'))
                    self.assertNotIn(p,seen); seen.add(p)
        self.assertEqual(len(seen),96)
    def test_no_oracle_in_actual_payload_and_grammar_does_not_encode_answer(self):
        d=r.workload.build_descriptors(r.base.read(r.CONFIG),'calibration',27001)[0]
        facts=r.workload.task_material(d,0); full=dict(d,**facts,prompt=r.workload.render_candidate(d,0,'C'),input_tokens=47,content_sha256='x',token_ids_sha256='y',arm='C')
        before=r.structured.completion_payload(r.replay_row(full))
        full.update(expected_answer='injected',source_records=[{'id':'R1','value':'99'}])
        self.assertEqual(before,r.structured.completion_payload(r.replay_row(full)))
        self.assertFalse(r.v6.FORBIDDEN & before.keys())
    def test_arrivals_are_open_fixed_and_no_client_policy_reordering(self):
        descriptors=r.workload.build_descriptors(r.base.read(r.CONFIG),'development',27101)
        plan=[dict(d,prompt='fixture',input_tokens=1) for d in descriptors]
        trace=r.make_trace(plan)
        self.assertEqual([x['arrival_offset_ms'] for x in trace if x['category']=='interactive'],list(range(0,12000,1000)))
        self.assertEqual([x['arrival_offset_ms'] for x in trace if x['category']=='background'],[250,300,350,3250,3300,3350,6250,6300,6350,9250,9300,9350])
        self.assertTrue(all('release_offset_ms' not in x for x in trace))
    def test_estimator_selects_finite_candidate_using_calibration_only(self):
        rows=calibration(); model=r.estimator.select(rows)
        self.assertEqual(model['candidate'],'category_task_median')
        self.assertEqual(model['cross_validation_mae']['category_task_median'],0)
        changed=deepcopy(rows); changed[0]['split']='development'
        with self.assertRaises(ValueError): r.estimator.select(changed)
        with self.assertRaises(ValueError): r.estimator.fit(changed,'category_median')
    def test_tie_prefers_simpler_model_and_upper_does_not_expand_budget(self):
        rows=calibration()
        for x in rows: x['output_tokens']=5
        model=r.estimator.select(rows); self.assertEqual(model['candidate'],'category_median')
        model['margin']=999
        row=rows[0]; got=r.estimator.apply(row,model)
        self.assertEqual(got['upper'],row['max_tokens'])
    def test_formatted_wrong_answer_remains_wrong(self):
        d=r.workload.build_descriptors(r.base.read(r.CONFIG),'development',27101)[0]
        ref=dict(d,**r.workload.task_material(d,0),prompt=r.workload.render_candidate(d,0,'C'),arm='C')
        correct=ref['expected_answer']; bad=correct.rsplit('|',1)[0]+'|'+('99' if not correct.endswith('99') else '98')
        self.assertFalse(a.quality(ref,{'terminal':'completed','content':bad})['completed_and_correct'])
        self.assertFalse(a.quality(ref,{'terminal':'timeout','content':correct})['completed_and_correct'])
    def test_percentiles_and_metrics_use_actual_values(self):
        self.assertAlmostEqual(a.percentile([10,20,30],95),29)
        self.assertEqual(a.dist([1,None,3])['n'],2)

if __name__=='__main__': unittest.main()
