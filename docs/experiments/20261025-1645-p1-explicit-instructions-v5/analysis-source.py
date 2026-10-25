#!/usr/bin/env python3
"""Predeclared O/C paired checks; use frozen independent v3 fact oracle."""
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
PAIR_KEYS = ('both_correct','both_wrong','O_wrong_C_correct','O_correct_C_wrong')


def expected_prompt(reference):
    # Rebuild from facts and fixed registered command, never generator imports.
    facts.derive_expected(reference)
    arm,kind=reference['arm'],reference['task_kind']
    if arm not in ('O','C'): raise ValueError('Unregistered instruction arm')
    targets=','.join(reference['target_ids'])
    table=lambda records:'\n'.join(item['id']+'|'+item['value'] for item in records)
    if kind=='check':
        body='A\n'+table(reference['left_records'])+'\nB\n'+table(reference['right_records'])
        command=(f'核对{targets}：同写一致，异写不一致。每行ID|结论。' if arm=='O'
            else f'逐项比较A和B中{targets}的数值：相等写一致，否则写不一致。每行ID|结论。')
    else:
        body='资料\n'+table(reference['source_records'])
        if kind=='extract':
            command=f'提取{targets}。每行ID|值。' if arm=='O' else f'提取{targets}对应的数值，不要复制编号作值。每行ID|值。'
        else:
            command=f'按{targets}顺序整理。每行ID|值。' if arm=='O' else f'只按{targets}顺序抄对应数值，不要抄其他行。每行ID|值。'
    return ('<|im_start|>system\n只输出结果，无表头。<|im_end|>\n'
        '<|im_start|>user\n'+body+'\n'+command+'<|im_end|>\n<|im_start|>assistant\n')


def prompt_facts_match(reference):
    if reference['prompt']!=expected_prompt(reference):
        raise ValueError('Actual O/C prompt differs from registered facts, order or command')
    return True


def expected_grammar(reference):
    # Targets are public; prompt equality has independently validated them.
    sequence=[]
    for index,identity in enumerate(reference['target_ids']):
        if index: sequence.append(json.dumps('\n',ensure_ascii=False))
        sequence.extend((json.dumps(identity+'|',ensure_ascii=False),'value'))
    rule='"一致" | "不一致"' if reference['task_kind']=='check' else '[1-9] [0-9]'
    return 'root ::= '+' '.join(sequence)+'\nvalue ::= '+rule+'\n'


def label_diagnostics(records):
    result={}
    for arm in ('O','C'):
        group=[r for r in records if r['arm']==arm and r['task_kind']=='check']
        expected=Counter(value for r in group for _,value in r['expected_fields'])
        observed=Counter(value for r in group for _,value in r['observed_fields'])
        result[arm]={'requests':len(group),'planned_fields':sum(r['target_fields_expected'] for r in group),
            'expected_label_counts':dict(expected),'observed_label_counts':dict(observed),
            'correct_fields':sum(r['target_fields_correct'] for r in group)}
    return result


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
    return 'O_correct_C_wrong' if u else 'O_wrong_C_correct'


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
        prompt_facts_match(ref)
        expected = ['|'.join(item) for item in facts.derive_expected(ref)]
        assert ref['expected_lines']==expected and ref['expected_answer']=='\n'.join(expected)
        actual_grammar = row['native_final']['generation_settings']['grammar']
        assert actual_grammar==execution.get('grammar','')
        assert row['native_final']['generation_settings']['grammar_lazy'] is False
        assert row['arm'] in ('O','C') and actual_grammar==expected_grammar(ref)
        assert text_sha(actual_grammar)==row['grammar_sha256']
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
    assert Counter(r['arm'] for r in assessed)=={'O':24,'C':24}
    assert Counter(r['task_kind'] for r in assessed)=={kind:16 for kind in facts.KINDS}
    results = []
    for pair_id in dict.fromkeys(r['pair_id'] for r in reference):
        pair = [r for r in reference if r['pair_id']==pair_id]
        assert len(pair)==2 and {r['arm'] for r in pair}=={'O','C'}
        u,g = next(r for r in pair if r['arm']=='O'),next(r for r in pair if r['arm']=='C')
        for key in ('content_id','max_tokens','target_ids','expected_lines','source_records','left_records','right_records','extra_records'):
            assert u.get(key)==g.get(key)
        for key in ('prompt','content_sha256','token_ids_sha256'):
            assert u[key]!=g[key]
        assert planned[u['req_id']]['grammar']==planned[g['req_id']]['grammar']
        observed = {r['arm']:r for r in assessed if r['pair_id']==pair_id}
        order = ''.join(r['arm'] for r in assessed if r['pair_id']==pair_id)
        assert order==u['condition_order']==g['condition_order']==('OC' if u['kind_instance']%2==0 else 'CO')
        results.append({'pair_id':pair_id,'task_kind':u['task_kind'],'category':u['category'],
            'input_token_bucket':u['input_token_bucket'],'condition_order':order,
            'O_correct':observed['O']['business_correct'],'C_correct':observed['C']['business_correct'],
            'classification':pair_kind(observed['O']['business_correct'],observed['C']['business_correct'])})
    assert len(results)==24 and Counter(r['condition_order'] for r in results)=={'OC':12,'CO':12}
    for category in ('interactive','background'):
        for bucket in range(3):
            assert Counter(r['condition_order'] for r in results if (r['category'],r['input_token_bucket'])==(category,bucket))=={'OC':2,'CO':2}
    arms = {arm:aggregate([r for r in assessed if r['arm']==arm]) for arm in ('O','C')}
    assert all(value['target_fields_expected']==88 for value in arms.values())
    counts = Counter(r['classification'] for r in results)
    result = {'status':'completed_independent_source_derived_paired_evaluation','run_id':run.name,
        'analyzed_at':datetime.now().astimezone().isoformat(),'analysis_script_sha256':sha(Path(__file__)),
        'frozen_fact_oracle_sha256':sha(Path(facts.__file__)), 'by_arm':arms,
        'by_arm_task':{arm:{kind:aggregate([r for r in assessed if r['arm']==arm and r['task_kind']==kind]) for kind in facts.KINDS} for arm in ('O','C')},
        'by_arm_category':{arm:{cat:aggregate([r for r in assessed if r['arm']==arm and r['category']==cat]) for cat in ('interactive','background')} for arm in ('O','C')},
        'by_arm_cell':{arm:{f'{cat}:{bucket}':aggregate([r for r in assessed if r['arm']==arm and (r['category'],r['input_token_bucket'])==(cat,bucket)]) for cat in ('interactive','background') for bucket in range(3)} for arm in ('O','C')},
        'pair_counts':{key:counts[key] for key in PAIR_KEYS},'pair_results':results,'request_results':assessed,
        'entry_condition':{'required_C_correct':24,'observed_C_correct':arms['C']['business_correct'],
            'satisfied':arms['C']['business_correct']==24,'scope':'May separately register new calibration and small development load only; not G1'},
        'comparison_label_diagnostics':label_diagnostics(assessed),
        'G1_passed':False,'final_test_accessed':False,
        'limits':['One serial synthetic development batch; not population reliability or a scheduler comparison.',
            'Grammar forces public IDs/order/domain; schema validity is mechanism verification, not autonomous instruction following.',
            'Auxiliary target fields allow irrelevant output; complete business correctness still rejects extra or invalid lines.',
            'Historical v3/v4 results are background only; O/C uses fresh shared facts, with different instruction lengths.']}
    out.mkdir(parents=True,exist_ok=False)
    dump(out/'summary.json',result)
    dump(out/'fulltexts.json',fulltexts)
    dump(out/'evidence-sha256.json',evidence)
    (out/'analysis-source.py').write_bytes(Path(__file__).read_bytes())
    lines = ['# 明确指令v5：逐条配对结果','',f'运行`{run.name}`，24道新题、每题O/C各一次。O旧指令，C明确指令；两臂语法相同。','',
        '|题目|任务/类别|顺序|O完整正确|C完整正确|O/C目标字段正确|O/C格式合法|','|---|---|---|---|---|---|---|']
    for pair in results:
        members={r['arm']:r for r in assessed if r['pair_id']==pair['pair_id']}
        u,g=members['O'],members['C']
        lines.append(f"|{pair['pair_id']}|{pair['task_kind']} / {pair['category']}|{pair['condition_order']}|{int(u['business_correct'])}|{int(g['business_correct'])}|{u['target_fields_correct']}/{u['target_fields_expected']} ; {g['target_fields_correct']}/{g['target_fields_expected']}|{int(u['schema_valid'])}/{int(g['schema_valid'])}|")
    lines += ['',f"O完整正确{arms['O']['business_correct']}/24、C完整正确{arms['C']['business_correct']}/24；目标字段分别{arms['O']['target_fields_correct']}/88、{arms['C']['target_fields_correct']}/88。",'',
        '两臂的ID、行数、顺序和字段域由解码器限制；格式通过不代表事实正确。全部题目和原文见fulltexts，逐字段差异见summary。C24/24且技术检查通过才允许另登记校准/小负载，不自动通过G1。','']
    (out/'metrics.md').write_text('\n'.join(lines),encoding='utf-8')
    dump(out/'report-sha256.json',{p.name:sha(p) for p in sorted(out.iterdir()) if p.is_file()})
    return {key:result[key] for key in ('status','run_id','by_arm','pair_counts','entry_condition')}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir',type=Path,required=True)
    parser.add_argument('--out-dir',type=Path,required=True)
    args=parser.parse_args()
    print(json.dumps(analyze(args.run_dir,args.out_dir),ensure_ascii=False))
