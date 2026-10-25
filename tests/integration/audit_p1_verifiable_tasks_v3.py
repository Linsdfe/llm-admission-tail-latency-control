#!/usr/bin/env python3
"""Independent v3 raw-data, offline-reference and native-accounting audit."""
import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def rows(path):
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def independent_answer(reference):
    assert reference['task_kind'] in ('extract','check','organize')
    targets = reference['target_ids']
    assert len(set(targets)) == len(targets) and targets
    assert all(isinstance(value,str) and re.fullmatch(r'R\d+',value) for value in targets)
    fields = ('left_records','right_records') if reference['task_kind'] == 'check' else ('source_records',)
    tables = []
    for field in fields:
        data = reference[field]
        table = {item['id']:item['value'] for item in data}
        assert data and len(table) == len(data) and set(targets) <= table.keys()
        assert all(isinstance(identity,str) and re.fullmatch(r'R\d+',identity) for identity in table)
        assert all(isinstance(value,str) and value and value==value.strip() and not any(mark in value for mark in '|\r\n') for value in table.values())
        tables.append(table)
    if len(tables) == 2:
        return [[identity,'一致' if tables[0][identity] == tables[1][identity] else '不一致'] for identity in targets]
    return [[identity,tables[0][identity]] for identity in targets]


def independent_prompt_matches(reference):
    """Check the complete registered prompt independently of collector and 95."""
    independent_answer(reference)
    targets = ','.join(reference['target_ids'])
    def table(field):
        return '\n'.join(record['id']+'|'+record['value'] for record in reference[field])
    kind = reference['task_kind']
    if kind == 'check':
        body = 'A\n'+table('left_records')+'\nB\n'+table('right_records')
        body += '\n核对'+targets+'：同写一致，异写不一致。每行ID|结论。'
    else:
        body = '资料\n'+table('source_records')+'\n'
        body += ('提取'+targets+'。' if kind == 'extract' else '按'+targets+'顺序整理。')+'每行ID|值。'
    return reference.get('prompt') == ('<|im_start|>system\n只输出结果，无表头。<|im_end|>\n'
        '<|im_start|>user\n'+body+'<|im_end|>\n<|im_start|>assistant\n')


def independent_match(content, expected):
    observed = []
    for line in content.replace('\r\n','\n').replace('\r','\n').split('\n'):
        if not line.strip():
            continue
        fields = line.split('|')
        if len(fields) != 2 or not all(field.strip() for field in fields):
            return False
        observed.append([field.strip() for field in fields])
    return observed == expected


def audit(root, run, analysis=None):
    checks, evidence = 0, {}
    def check(condition, label):
        nonlocal checks
        checks += 1
        if not condition:
            raise AssertionError(label)
    def tracked(path):
        evidence[path.relative_to(root).as_posix()] = sha(path)
    summary = read(run/'summary.json')
    check(summary == read(run/'batch-status.json') and summary['status']=='completed','complete summary')
    check(summary['measured_completions']==summary['terminal_counts']['completed']==sum(summary['terminal_counts'].values())==24,'24 complete')
    check(summary['completion_requests']==26 and summary['accounting']['events']==52,'26 requests 52 CAP')
    check(summary['server_exit_code']==0 and summary['final_test_generated_or_read'] is False,'exit and final isolation')
    plan, reference = rows(run/'plan.jsonl'), rows(run/'evaluation-plan.jsonl')
    planned = {row['req_id']:row for row in plan}
    reference = {row['req_id']:row for row in reference}
    check(len(plan)==len(planned)==len(reference)==24 and set(planned)==set(reference),'unique planned/reference IDs')
    forbidden = {'expected_answer','expected_lines','evaluation_only','oracle_evaluation_only',
        'source_records','left_records','right_records'}
    check(all(not forbidden & row.keys() for row in plan),'inference plan excludes offline facts/answers')
    check(Counter(row['task_kind'] for row in reference.values())==Counter({'extract':8,'check':8,'organize':8}),'three task kinds')
    cells = Counter((row['category'],row['input_token_bucket']) for row in reference.values())
    check(len(cells)==6 and set(cells.values())=={4},'six cells four each')
    tokenized = {}
    for record in rows(run/'tokenization.jsonl'):
        check(record['http_status']==200,'tokenizer HTTP')
        check(record['request']['add_special'] is True and record['request']['parse_special'] is True,'tokenizer flags')
        ids = json.loads(record['response_body'])['tokens']
        check(ids and all(type(value) is int for value in ids),'actual integer token IDs')
        tokenized[hashlib.sha256(record['request']['content'].encode()).hexdigest()] = ids
    observed, correct = {}, {}
    for client in sorted((run/'cases').glob('q*/replay/client.jsonl')):
        recorded = rows(client)
        check(len(recorded)==1,'one client record')
        row = recorded[0]
        req_id, directory = row['req_id'], client.parent.parent
        check(req_id not in observed,'unique measured ID')
        observed[req_id] = row
        check(all(row[key]==value for key,value in planned[req_id].items()),'plan preserved')
        check(not forbidden & row.keys(),'client excludes offline oracle fields')
        check(row['terminal']=='completed' and row['http_status']==200,'completed HTTP')
        check(read(client.parent/'requests'/(req_id+'.json'))==row,'JSON=JSONL')
        check(row['cache_prompt'] is False and row['cached_prompt_tokens']==0,'uncached actual request')
        check(row['input_tokens']==row['new_prompt_tokens']==row['native_final']['tokens_evaluated'],'native input')
        prompt_sha = hashlib.sha256(row['prompt'].encode()).hexdigest()
        ids = tokenized[prompt_sha]
        check(prompt_sha==row['content_sha256'] and len(ids)==row['input_tokens'],'prompt and input length')
        check(hashlib.sha256(json.dumps(ids,separators=(',',':')).encode()).hexdigest()==row['token_ids_sha256'],'token IDs SHA')
        check(row['output_tokens_known'] is True and row['output_tokens']==row['native_final']['tokens_predicted']==row['timings']['predicted_n'],'native known output')
        check(row['native_final']['truncated'] is False and 0<row['output_tokens']<=row['max_tokens'],'output/context')
        check(row['max_tokens']=={'interactive':64,'background':384}[row['category']],'fixed output budget')
        check(row['input_tokens']+row['max_tokens']+1<=2048,'hard capacity')
        check(row['input_token_range'][0]<=row['input_tokens']<=row['input_token_range'][1],'actual input bucket')
        check(row['system_deadline_offset_ms']==45000 and 0<=row['actual_send_offset_ms']<=row['end_offset_ms']<45000,'planned deadline')
        check(0<=row['send_lag_ms']<=50,'send-lag gate')
        check((directory/'output.txt').read_text(encoding='utf-8')==row['content'],'full output preserved')
        slots = read(directory/'after-request-slots.json')
        check(len(slots)==2 and not any(slot['is_processing'] for slot in slots),'per-request slots idle')
        ref = reference[req_id]
        check(ref['prompt']==row['prompt'] and ref['input_tokens']==row['input_tokens'],'offline and inference input equal')
        check(independent_prompt_matches(ref),'actual prompt includes exactly registered source facts, task and target order')
        expected = independent_answer(ref)
        check(ref['expected_lines']==['|'.join(item) for item in expected] and ref['expected_answer']=='\n'.join('|'.join(item) for item in expected),'independent source-derived expected answer')
        check(ref.get('oracle_evaluation_only') is True or ref.get('evaluation_only') is True,'offline marker')
        correct[req_id] = independent_match(row['content'],expected)
        payload_record = read(directory/'completion-request-payload.json')
        check(payload_record['evidence_kind']=='reconstructed_from_frozen_client_not_actual_wire_capture','payload evidence correctly labelled')
        payload = payload_record['payload']
        expected_payload = {'prompt':row['prompt'],'n_predict':row['max_tokens'],'temperature':0.0,
            'stream':True,'ignore_eos':False,'cache_prompt':False,'request_class':row['category'],'client_req_id':req_id}
        check(payload==expected_payload and len(payload)==8,'payload whitelist excludes offline answers')
        check(payload_record['client_source_sha256']==sha(root/'scripts/replay/p1_development_replay.py'),'payload frozen client SHA')
        for item in (client,directory/'output.txt',directory/'completion-request-payload.json'):
            tracked(item)
    check(set(observed)==set(planned),'all planned requests observed')
    check(all(row['split']=='development' and row['seed']==23001 for row in reference.values()),'fresh development seed only')
    warm = rows(run/'warmup/client.jsonl')
    check(len(warm)==2 and all(row['terminal']=='completed' and row['cache_prompt'] is False and row['cached_prompt_tokens']==0 for row in warm),'two uncached warmups')
    events = []
    for line in (run/'service/server.log').read_text(encoding='utf-8',errors='replace').splitlines():
        for kind in ('LAUNCH','RELEASE'):
            if 'SCHED_CAP_'+kind in line:
                events.append(dict(event=kind,**dict(item.split('=',1) for item in line.split() if '=' in item)))
    check(len(events)==52,'52 CAP events')
    expected_ids = set(observed)|{'warm0','warm1'}
    check({event['clientid'] for event in events}==expected_ids,'CAP ID coverage')
    active, count = {}, 0
    for event in events:
        check(int(event['cap'])==0 and int(event['running_background_before'])==count,'CAP before')
        if event['event']=='LAUNCH':
            check(event['slot'] not in active,'launch free slot')
            active[event['slot']] = event
            count += event['class']=='background'
        else:
            prior = active.pop(event['slot'])
            check(prior['clientid']==event['clientid'] and prior['id']==event['id'],'matching release')
            count -= event['class']=='background'
        check(count>=0 and int(event['running_background_after'])==count,'CAP after')
    check(not active and count==0,'CAP final zero')
    for req_id in expected_ids:
        check([event['event'] for event in events if event['clientid']==req_id]==['LAUNCH','RELEASE'],'one pair per ID')
    slots = read(run/'service/after-all-slots.json')
    check(len(slots)==2 and not any(slot['is_processing'] for slot in slots),'final slots idle')
    for metric in ('llamacpp:requests_processing','llamacpp:requests_deferred'):
        found = [float(line.split()[1]) for line in (run/'service/after-all-metrics.txt').read_text().splitlines() if line.startswith(metric+' ')]
        check(bool(found) and all(value==0 for value in found),'final metric zero')
    check(read(run/'service-stop.json')['server_exit_code']==0,'owned stop')
    for relative,digest in read(run/'source-sha256.json').items():
        check(sha(root/relative)==sha(run/'source'/relative)==digest,'project source '+relative)
    env = read(run/'environment.json')
    engine_root = Path(env['engine_binary']).parents[2]
    for relative,digest in read(run/'engine-source-sha256.json').items():
        check(sha(run/'engine-source'/relative)==sha(engine_root/relative)==digest,'actual engine source')
    before = read(run/'engine-runtime-sha256.json')
    check(before==read(run/'engine-runtime-sha256-after.json'),'runtime before after')
    actual = set()
    for entry in before.values():
        path = Path(entry['path'])
        check(str(path.resolve())==entry['realpath'],'runtime resolved target')
        if entry['realpath'] not in actual:
            check(sha(path)==entry['sha256'],'actual runtime bytes')
            actual.add(entry['realpath'])
    after = read(run/'model-sha256-after.json')
    check(env['model_sha256']==after['sha256']==sha(Path(env['model'])),'actual model bytes')
    if analysis is not None:
        reported = read(analysis/'summary.json')
        check(reported['counts']['business_correct']==sum(correct.values()),'95 independent answer count')
        check({row['req_id']:row['business_correct'] for row in reported['request_results']}==correct,'95 all individual answer results')
    for path in (run/'summary.json',run/'plan.jsonl',run/'evaluation-plan.jsonl',run/'source-sha256.json',run/'service/server.log'):
        tracked(path)
    return {'status':'passed','checked_at':datetime.now(timezone(timedelta(hours=8))).isoformat(),
        'audit_script_sha256':sha(Path(__file__)),'checks':checks,'measured_completions':24,'warmups':2,'cap_events':52,
        'frozen_project_sources':len(read(run/'source-sha256.json')),'engine_sources':len(read(run/'engine-source-sha256.json')),
        'runtime_entries':len(before),'runtime_actual_files':len(actual),'max_send_lag_ms':max(row['send_lag_ms'] for row in observed.values()),
        'independent_business_correct':sum(correct.values()),'individual_business_correct':correct,
        'analysis_compared':analysis is not None,'G1_passed':False,'evidence_sha256':evidence,
        'scope':'Independent raw evidence and source-derived business checks; no collector/generator/95 imports, no GPU execution or general quality guarantee'}


if __name__=='__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--run-dir',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--analysis-dir',type=Path)
    args = parser.parse_args()
    result = audit(args.root.resolve(),args.run_dir.resolve(),args.analysis_dir.resolve() if args.analysis_dir else None)
    args.out.parent.mkdir(parents=True,exist_ok=True)
    with args.out.open('x',encoding='utf-8') as output:
        json.dump(result,output,ensure_ascii=False,indent=2)
        output.write('\n')
    print(json.dumps({key:value for key,value in result.items() if key not in ('evidence_sha256','individual_business_correct')},ensure_ascii=False))
