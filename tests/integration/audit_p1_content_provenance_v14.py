"""Supplementary provenance audit; does not change the registered task gates."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def read(p): return json.loads(p.read_text(encoding='utf-8'))
def rows(p): return [json.loads(s) for s in p.read_text(encoding='utf-8').splitlines() if s]
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()


def audit(run):
    checks = 0
    def check(value, label):
        nonlocal checks
        assert value, label
        checks += 1
    freeze = read(run/'pre-measurement-freeze.json')
    check(sha(run/'public-features-before-data.jsonl') == freeze['public_features_sha256'], 'pre-measurement features unchanged')
    check(freeze['outputs_read'] == 0 and freeze['planned'] == 48, 'features before measured output')
    check(read(run/'preflight.json')['online_updates_enabled'] is False, 'profile not online calibration')
    refs = rows(run/'evaluation-plan.jsonl')
    tokenization = {r['request']['content']:r for r in rows(run/'tokenization.jsonl')}
    check(len(refs) == len(tokenization) == 48, 'all input tokenizations')
    seen = set()
    history = set()
    for name,digest in read(run/'history-source-sha256.json').items():
        p = ROOT/name
        check(sha(p) == digest, 'historical plan unchanged')
        for r in rows(p):
            if isinstance(r,dict) and isinstance(r.get('prompt'),str): history.add(hashlib.sha256(r['prompt'].encode('utf-8')).hexdigest())
    sent_ids = []
    native = []
    for line in (run/'server.log').read_text(encoding='utf-8',errors='replace').splitlines():
        if 'SCHED_CAP_LAUNCH' in line or 'SCHED_CAP_RELEASE' in line:
            d = dict(x.split('=',1) for x in line.split() if '=' in x)
            native.append(('release' if 'SCHED_CAP_RELEASE' in line else 'launch',d['clientid']))
    for i,ref in enumerate(refs,1):
        token = tokenization[ref['prompt']]
        ids = json.loads(token['response_body'])['tokens']
        check(token['request']['add_special'] is True and token['request']['parse_special'] is True, 'prompt special token flags')
        check(len(ids) == ref['input_tokens'] and hashlib.sha256(json.dumps(ids,separators=(',',':')).encode()).hexdigest() == ref['token_ids_sha256'], 'actual input token IDs')
        h = hashlib.sha256(ref['prompt'].encode('utf-8')).hexdigest()
        check(h not in seen and h not in history, 'batch and entire recorded-plan history deduplication')
        seen.add(h)
        r = rows(run/'cases'/f'q{i:03d}'/'replay/client.jsonl')[0]
        check(r['native_final']['prompt'] == ref['prompt'] and r['timings']['prompt_n'] == ref['input_tokens'], 'native input agreement')
        check(r['native_final']['generation_settings']['grammar'] == ref['grammar'], 'actual public grammar')
        check(r['native_final']['generation_settings']['ignore_eos'] is False, 'natural EOS enabled')
        check(r['actual_send_offset_ms'] is not None and r['output_tokens_known'], 'actually measured')
        sent_ids.append(ref['req_id'])
    launch = Counter(req for kind,req in native if kind == 'launch')
    release = Counter(req for kind,req in native if kind == 'release')
    check(launch == release and set(launch) == set(sent_ids)|{'warm0','warm1'}, 'all50 native pairs')
    check(all(n == 1 for n in launch.values()), 'one native launch each')
    check(not [p for p in (ROOT/'data/test').rglob('*') if p.is_file() and p.name != '.gitkeep'], 'final remains untouched')
    return {'status':'passed','checks':checks,'unique_new_prompts':48,'historical_unique_prompts':len(history),
            'historical_plan_files':len(read(run/'history-source-sha256.json')),'native_launches':50,'native_releases':50,
            'validation_only_does_not_change_acceptance_criteria':True,'G1_passed':False}


if __name__ == '__main__':
    p = argparse.ArgumentParser(); p.add_argument('--run-dir',type=Path,required=True); p.add_argument('--out',type=Path,required=True)
    a = p.parse_args(); result = audit(a.run_dir)
    with a.out.open('x',encoding='utf-8') as f: json.dump(result,f,ensure_ascii=False,indent=2)
    print(json.dumps(result,ensure_ascii=False))
