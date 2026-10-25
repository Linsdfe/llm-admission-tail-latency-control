#!/usr/bin/env python3
"""Predeclared U/G paired checks; use frozen independent v3 fact oracle."""
import argparse
from collections import Counter
from datetime import datetime
import importlib.util
import json
from pathlib import Path
import re
import statistics

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('frozen_v3_answer_rules',ROOT/'scripts/analysis/95_analyze_p1_verifiable_tasks_v3.py')
facts = importlib.util.module_from_spec(spec)
spec.loader.exec_module(facts)
read, rows, sha, text_sha, dump = facts.read, facts.rows, facts.sha, facts.text_sha, facts.dump
PAIR_KEYS = ('both_correct','both_wrong','U_wrong_G_correct','U_correct_G_wrong')


def assess(reference, content):
    result = facts.evaluate_answer(reference,content)
    parsed, invalid, _ = facts.normalize_fields(content)
    targets = reference['target_ids']
    allowed = lambda value: value in ('一致','不一致') if reference['task_kind']=='check' else re.fullmatch(r'[1-9][0-9]',value) is not None
    result['schema_valid'] = (not invalid and [item[0] for item in parsed]==targets
        and all(allowed(value) for _,value in parsed))
    fields = []
    for identity, expected in result['expected_fields']:
        observed = [value for key,value in parsed if key==identity]
        status = ('missing' if not observed else 'duplicate' if len(observed)>1
            else 'correct' if observed[0]==expected else 'wrong_value')
        fields.append({'id':identity,'expected':expected,'observed_values':observed,'status':status})
    result.update(target_field_results=fields,target_fields_correct=sum(item['status']=='correct' for item in fields),
        target_fields_expected=len(fields))
    return result


def pair_kind(u, g):
    if u and g: return 'both_correct'
    if not u and not g: return 'both_wrong'
    return 'U_correct_G_wrong' if u else 'U_wrong_G_correct'


def aggregate(group):
    return {'requests':len(group),'business_correct':sum(r['business_correct'] for r in group),
        'raw_text_exact':sum(r['raw_text_exact'] for r in group),'schema_valid':sum(r['schema_valid'] for r in group),
        'target_fields_correct':sum(r['target_fields_correct'] for r in group),
        'target_fields_expected':sum(r['target_fields_expected'] for r in group),
        'cases_with_value_mismatches':sum(bool(r['value_mismatches']) for r in group),
        'stop_types':dict(Counter(r['stop_type'] for r in group)),
        'input_tokens':facts.distribution([r['input_tokens'] for r in group]),
        'output_tokens':facts.distribution([r['output_tokens'] for r in group]),
        'total_output_tokens':sum(r['output_tokens'] for r in group)}


def analyze(run, out):
    run, out = run.resolve(strict=True),out.resolve()
    if out.exists(): raise FileExistsError('Use a new analysis directory')
    status = read(run/'summary.json')
    assert status==read(run/'batch-status.json') and status['status']=='completed'
    assert status['measured_completions']==status['terminal_counts']['completed']==sum(status['terminal_counts'].values())==48
    assert status['completion_requests']==50 and status['accounting']['events']==100 and status['server_exit_code']==0
    assert status['final_test_generated_or_read'] is False
    plan, reference = rows(run/'plan.jsonl'),rows(run/'evaluation-plan.jsonl')
    assert len(plan)==len(reference)==48 and not any(facts.FORBIDDEN_IN_REPLAY & r.keys() for r in plan)
    planned, by_id = {r['req_id']:r for r in plan},{r['req_id']:r for r in reference}
    assert len(planned)==len(by_id)==48 and set(planned)==set(by_id)
    evidence = {name:sha(run/name) for name in ('summary.json','batch-status.json','plan.jsonl','evaluation-plan.jsonl')}
    assessed, fulltexts = [],[]
    for path in sorted((run/'cases').glob('q*/replay/client.jsonl')):
        one = rows(path)
        assert len(one)==1
        row = one[0]
        ref, execution = by_id[row['req_id']],planned[row['req_id']]
        assert all(row[k]==v for k,v in execution.items())
        assert row['terminal']=='completed' and row['http_status']==200
        assert row['cached_prompt_tokens']==0 and row['cache_prompt'] is False
        assert row['input_tokens']==row['new_prompt_tokens']==row['native_final']['tokens_evaluated']
        assert row['output_tokens']==row['timings']['predicted_n']==row['native_final']['tokens_predicted']
        assert row['output_tokens_known'] is True and row['native_final']['truncated'] is False
        assert 0<row['output_tokens']<=row['max_tokens']
        assert not facts.FORBIDDEN_IN_REPLAY & row.keys()
        assert row['prompt']==ref['prompt'] and row['input_tokens']==ref['input_tokens']
        facts.prompt_facts_match(ref)
        expected = ['|'.join(item) for item in facts.derive_expected(ref)]
        assert ref['expected_lines']==expected and ref['expected_answer']=='\n'.join(expected)
        actual_grammar = row['native_final']['generation_settings']['grammar']
        assert actual_grammar==execution.get('grammar','')
        assert row['native_final']['generation_settings']['grammar_lazy'] is False
        if row['arm']=='G': assert actual_grammar and text_sha(actual_grammar)==row['grammar_sha256']
        else: assert row['arm']=='U' and actual_grammar=='' and 'grammar' not in execution
        directory = path.parent.parent
        assert (directory/'output.txt').read_text(encoding='utf-8')==row['content']
        assert read(path.parent/'requests'/(row['req_id']+'.json'))==row
        result = {key:ref[key] for key in ('req_id','pair_id','arm','task_kind','category','kind_instance',
            'condition_order','condition_position','input_token_bucket','input_tokens','max_tokens')}
        result.update(output_tokens=row['output_tokens'],stop_type=row['native_final']['stop_type'],
            content_sha256=text_sha(row['content']),prompt_sha256=text_sha(row['prompt']),
            grammar_sha256=text_sha(actual_grammar) if actual_grammar else None,
            directory=directory.relative_to(run).as_posix(),**assess(ref,row['content']))
        assessed.append(result)
        fulltexts.append({**result,'prompt':row['prompt'],'content':row['content'],
            'evaluation_facts':{key:ref[key] for key in ('target_ids','source_records','left_records','right_records') if key in ref}})
        for item in (path,directory/'output.txt',path.parent/'requests'/(row['req_id']+'.json')):
            evidence[item.relative_to(run).as_posix()]=sha(item)
    assert len(assessed)==len({r['req_id'] for r in assessed})==48
    assert Counter(r['arm'] for r in assessed)=={'U':24,'G':24}
    assert Counter(r['task_kind'] for r in assessed)=={kind:16 for kind in facts.KINDS}
    results = []
    for pair_id in dict.fromkeys(r['pair_id'] for r in reference):
        pair = [r for r in reference if r['pair_id']==pair_id]
        assert len(pair)==2 and {r['arm'] for r in pair}=={'U','G'}
        u,g = next(r for r in pair if r['arm']=='U'),next(r for r in pair if r['arm']=='G')
        for key in ('prompt','input_tokens','token_ids_sha256','max_tokens','target_ids','expected_lines'):
            assert u[key]==g[key]
        observed = {r['arm']:r for r in assessed if r['pair_id']==pair_id}
        order = ''.join(r['arm'] for r in assessed if r['pair_id']==pair_id)
        assert order==u['condition_order']==g['condition_order']==('UG' if u['kind_instance']%2==0 else 'GU')
        results.append({'pair_id':pair_id,'task_kind':u['task_kind'],'category':u['category'],
            'input_token_bucket':u['input_token_bucket'],'condition_order':order,
            'U_correct':observed['U']['business_correct'],'G_correct':observed['G']['business_correct'],
            'classification':pair_kind(observed['U']['business_correct'],observed['G']['business_correct'])})
    assert len(results)==24 and Counter(r['condition_order'] for r in results)=={'UG':12,'GU':12}
    for category in ('interactive','background'):
        for bucket in range(3):
            assert Counter(r['condition_order'] for r in results if (r['category'],r['input_token_bucket'])==(category,bucket))=={'UG':2,'GU':2}
    arms = {arm:aggregate([r for r in assessed if r['arm']==arm]) for arm in ('U','G')}
    assert all(value['target_fields_expected']==88 for value in arms.values())
    counts = Counter(r['classification'] for r in results)
    result = {'status':'completed_independent_source_derived_paired_evaluation','run_id':run.name,
        'analyzed_at':datetime.now().astimezone().isoformat(),'analysis_script_sha256':sha(Path(__file__)),
        'frozen_fact_oracle_sha256':sha(Path(facts.__file__)), 'by_arm':arms,
        'by_arm_task':{arm:{kind:aggregate([r for r in assessed if r['arm']==arm and r['task_kind']==kind]) for kind in facts.KINDS} for arm in ('U','G')},
        'by_arm_category':{arm:{cat:aggregate([r for r in assessed if r['arm']==arm and r['category']==cat]) for cat in ('interactive','background')} for arm in ('U','G')},
        'by_arm_cell':{arm:{f'{cat}:{bucket}':aggregate([r for r in assessed if r['arm']==arm and (r['category'],r['input_token_bucket'])==(cat,bucket)]) for cat in ('interactive','background') for bucket in range(3)} for arm in ('U','G')},
        'pair_counts':{key:counts[key] for key in PAIR_KEYS},'pair_results':results,'request_results':assessed,
        'entry_condition':{'required_G_correct':24,'observed_G_correct':arms['G']['business_correct'],
            'satisfied':arms['G']['business_correct']==24,'scope':'May separately register new calibration and small development load only; not G1'},
        'G1_passed':False,'final_test_accessed':False,
        'limits':['One serial synthetic development batch; not population reliability or a scheduler comparison.',
            'Grammar forces public IDs/order/domain; schema validity is mechanism verification, not autonomous instruction following.',
            'Auxiliary target fields allow irrelevant output; complete business correctness still rejects extra or invalid lines.',
            'Historical v3 9/24 is background only; all U/G comparisons use the same fresh 24 tasks.']}
    out.mkdir(parents=True,exist_ok=False)
    dump(out/'summary.json',result)
    dump(out/'fulltexts.json',fulltexts)
    dump(out/'evidence-sha256.json',evidence)
    (out/'analysis-source.py').write_bytes(Path(__file__).read_bytes())
    lines = ['# 输出约束v4：逐条配对结果','',f'运行`{run.name}`，24道新题、每题U/G各一次。U无语法约束，G有语法约束。','',
        '|题目|任务/类别|顺序|U完整正确|G完整正确|U/G目标字段正确|U/G格式合法|','|---|---|---|---|---|---|---|']
    for pair in results:
        members={r['arm']:r for r in assessed if r['pair_id']==pair['pair_id']}
        u,g=members['U'],members['G']
        lines.append(f"|{pair['pair_id']}|{pair['task_kind']} / {pair['category']}|{pair['condition_order']}|{int(u['business_correct'])}|{int(g['business_correct'])}|{u['target_fields_correct']}/{u['target_fields_expected']} ; {g['target_fields_correct']}/{g['target_fields_expected']}|{int(u['schema_valid'])}/{int(g['schema_valid'])}|")
    lines += ['',f"U完整正确{arms['U']['business_correct']}/24、G完整正确{arms['G']['business_correct']}/24；目标字段分别{arms['U']['target_fields_correct']}/88、{arms['G']['target_fields_correct']}/88。",'',
        'G的ID、行数、顺序和字段域由解码器限制；格式通过不代表事实正确。全部题目和原文见fulltexts，逐字段差异见summary。G24/24且技术检查通过才允许另登记校准/小负载，不自动通过G1。','']
    (out/'metrics.md').write_text('\n'.join(lines),encoding='utf-8')
    dump(out/'report-sha256.json',{p.name:sha(p) for p in sorted(out.iterdir()) if p.is_file()})
    return {key:result[key] for key in ('status','run_id','by_arm','pair_counts','entry_condition')}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir',type=Path,required=True)
    parser.add_argument('--out-dir',type=Path,required=True)
    args=parser.parse_args()
    print(json.dumps(analyze(args.run_dir,args.out_dir),ensure_ascii=False))
