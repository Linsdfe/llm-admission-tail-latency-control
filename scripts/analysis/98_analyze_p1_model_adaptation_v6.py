#!/usr/bin/env python3
"""Fixed model artifact comparison; frozen v5 fact rules, instruction C only."""
import argparse
from collections import Counter
from datetime import datetime
import importlib.util
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('frozen97',ROOT/'scripts/analysis/97_analyze_p1_explicit_instructions_v5.py')
old=importlib.util.module_from_spec(spec); spec.loader.exec_module(old)
read,rows,sha,dump,text_sha=old.read,old.rows,old.sha,old.dump,old.text_sha
PAIR_KEYS=('both_correct','both_wrong','A_wrong_B_correct','A_correct_B_wrong')

def instruction_reference(ref):
    assert ref['arm'] in ('A','B') and ref['instruction_arm']=='C'
    return dict(ref,arm='C')

def pair_kind(a,b):
    return 'both_correct' if a and b else 'both_wrong' if not a and not b else 'A_wrong_B_correct' if b else 'A_correct_B_wrong'

def analyze(run,out):
    run=run.resolve(strict=True); out=out.resolve()
    assert not out.exists()
    status=read(run/'summary.json')
    assert status==read(run/'batch-status.json') and status['status']=='completed'
    assert status['measured_completions']==status['terminal_counts']['completed']==48
    assert sum(status['terminal_counts'].values())==48 and status['completion_requests']==56
    assert status['accounting']['events']==112 and status['server_exit_codes']==[0]*4
    assert status['final_test_generated_or_read'] is False
    plan,refs=rows(run/'plan.jsonl'),rows(run/'evaluation-plan.jsonl')
    planned={r['req_id']:r for r in plan}; reference={r['req_id']:r for r in refs}
    assert len(plan)==len(refs)==len(planned)==len(reference)==48 and planned.keys()==reference.keys()
    evidence={n:sha(run/n) for n in ('summary.json','batch-status.json','plan.jsonl','evaluation-plan.jsonl')}
    assessed=[]; fulltexts=[]
    for path in sorted((run/'blocks').glob('block*/cases/q*/replay/client.jsonl')):
        actual=rows(path); assert len(actual)==1
        row=actual[0]; ref=reference[row['req_id']]; execution=planned[row['req_id']]
        assert all(row[k]==v for k,v in execution.items())
        assert row['terminal']=='completed' and row['http_status']==200
        assert row['cached_prompt_tokens']==0 and row['cache_prompt'] is False
        assert row['input_tokens']==row['new_prompt_tokens']==row['native_final']['tokens_evaluated']
        assert row['output_tokens']==row['timings']['predicted_n']==row['native_final']['tokens_predicted']
        assert row['output_tokens_known'] is True and row['native_final']['truncated'] is False
        assert 0<row['output_tokens']<=row['max_tokens']
        assert not old.facts.FORBIDDEN_IN_REPLAY & row.keys()
        ins=instruction_reference(ref); old.prompt_facts_match(ins)
        expected=['|'.join(v) for v in old.facts.derive_expected(ins)]
        assert ref['expected_lines']==expected and ref['expected_answer']=='\n'.join(expected)
        settings=row['native_final']['generation_settings']
        assert settings['grammar']==execution['grammar']==old.expected_grammar(ins)
        assert settings['grammar_lazy'] is False and settings['temperature']==0 and settings['ignore_eos'] is False
        assert text_sha(settings['grammar'])==row['grammar_sha256']
        directory=path.parent.parent
        assert (directory/'output.txt').read_text(encoding='utf-8')==row['content']
        assert read(path.parent/'requests'/(row['req_id']+'.json'))==row
        result={k:ref[k] for k in ('req_id','pair_id','arm','instruction_arm','task_kind','category','kind_instance','condition_order','condition_position','input_token_bucket','input_tokens','max_tokens','block_id','group_id','source_req_id')}
        result.update(output_tokens=row['output_tokens'],stop_type=row['native_final']['stop_type'],content_sha256=text_sha(row['content']),prompt_sha256=text_sha(row['prompt']),grammar_sha256=text_sha(execution['grammar']),directory=directory.relative_to(run).as_posix(),**old.assess(ins,row['content']))
        assessed.append(result)
        fulltexts.append(dict(result,prompt=row['prompt'],content=row['content'],evaluation_facts={k:ref[k] for k in ('target_ids','source_records','left_records','right_records') if k in ref}))
        for item in (path,directory/'output.txt',path.parent/'requests'/(row['req_id']+'.json')): evidence[item.relative_to(run).as_posix()]=sha(item)
    assert len(assessed)==48
    pairs=[]
    for pid in dict.fromkeys(r['pair_id'] for r in refs):
        members={r['arm']:r for r in assessed if r['pair_id']==pid}
        raw=[r for r in refs if r['pair_id']==pid]
        assert set(members)=={'A','B'} and len(raw)==2
        for key in ('prompt','content_sha256','max_tokens','expected_lines','target_ids','source_req_id'):
            assert raw[0][key]==raw[1][key]
        a,b=members['A'],members['B']
        pairs.append({'pair_id':pid,'task_kind':a['task_kind'],'category':a['category'],'condition_order':a['condition_order'],'A_correct':a['business_correct'],'B_correct':b['business_correct'],'classification':pair_kind(a['business_correct'],b['business_correct'])})
    assert len(pairs)==24 and Counter(p['condition_order'] for p in pairs)=={'AB':12,'BA':12}
    arms={a:old.aggregate([r for r in assessed if r['arm']==a]) for a in ('A','B')}
    assert all(a['requests']==24 and a['target_fields_expected']==88 for a in arms.values())
    labels={}
    for a in ('A','B'):
        group=[r for r in assessed if r['arm']==a and r['task_kind']=='check']
        labels[a]={'expected_label_counts':dict(Counter(v for r in group for _,v in r['expected_fields'])),'observed_label_counts':dict(Counter(v for r in group for _,v in r['observed_fields'])),'correct_fields':sum(r['target_fields_correct'] for r in group),'planned_fields':28}
    counts=Counter(p['classification'] for p in pairs)
    result={'status':'completed_source_derived_model_adaptation_evaluation','run_id':run.name,'analyzed_at':datetime.now().astimezone().isoformat(),'analysis_script_sha256':sha(Path(__file__)),'frozen97_sha256':sha(Path(old.__file__)),'by_arm':arms,
      'by_arm_task':{a:{k:old.aggregate([r for r in assessed if r['arm']==a and r['task_kind']==k]) for k in old.facts.KINDS} for a in ('A','B')},
      'by_arm_category':{a:{c:old.aggregate([r for r in assessed if r['arm']==a and r['category']==c]) for c in ('interactive','background')} for a in ('A','B')},
      'by_arm_cell':{a:{f'{c}:{b}':old.aggregate([r for r in assessed if r['arm']==a and (r['category'],r['input_token_bucket'])==(c,b)]) for c in ('interactive','background') for b in range(3)} for a in ('A','B')},
      'by_block':{str(b):old.aggregate([r for r in assessed if r['block_id']==b]) for b in range(1,5)},
      'pair_counts':{k:counts[k] for k in PAIR_KEYS},'pair_results':pairs,'request_results':assessed,'comparison_label_diagnostics':labels,
      'entry_condition':{'required_B_correct':24,'observed_B_correct':arms['B']['business_correct'],'satisfied':arms['B']['business_correct']==24,'scope':'Separate registration of new calibration/small load only; not G1'},'G1_passed':False,'final_test_accessed':False,
      'limits':['All24 prompts already seen in development; no unseen-task reliability estimate.','One serial ABBA batch; no latency, throughput, model-size-only causal or scheduler conclusion.','Both models constrained by same public-task grammar; format validity is not semantic correctness.','Only complete model artifact packages compared; quantization errors and architecture can also differ.']}
    out.mkdir(parents=True,exist_ok=False)
    dump(out/'summary.json',result); dump(out/'fulltexts.json',fulltexts); dump(out/'evidence-sha256.json',evidence)
    (out/'analysis-source.py').write_bytes(Path(__file__).read_bytes())
    lines=['# 模型适配v6逐题结果','','A为旧1.5B，B为固定3B。相同24道已见开发题，不是新题测试。','','|题目|任务/类别|顺序|A正确|B正确|','|---|---|---|---|---|']
    lines += [f"|{p['pair_id']}|{p['task_kind']}/{p['category']}|{p['condition_order']}|{int(p['A_correct'])}|{int(p['B_correct'])}|" for p in pairs]
    lines += ['',f"A完整{arms['A']['business_correct']}/24，B完整{arms['B']['business_correct']}/24；字段{arms['A']['target_fields_correct']}/88与{arms['B']['target_fields_correct']}/88。",'','完整逐字段差异见summary，原始提示、资料和输出见fulltexts。G1未通过。','']
    (out/'metrics.md').write_text('\n'.join(lines),encoding='utf-8')
    dump(out/'report-sha256.json',{p.name:sha(p) for p in sorted(out.iterdir()) if p.is_file()})
    return {k:result[k] for k in ('status','by_arm','pair_counts','entry_condition')}

if __name__=='__main__':
    p=argparse.ArgumentParser(); p.add_argument('--run-dir',type=Path,required=True); p.add_argument('--out-dir',type=Path,required=True); a=p.parse_args()
    print(json.dumps(analyze(a.run_dir,a.out_dir),ensure_ascii=False))
