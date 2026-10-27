"""One48-case profile of content-dependent output; no online scheduling."""
import argparse
import asyncio
from datetime import datetime
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import time
import traceback
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'scripts/replay'))
spec = importlib.util.spec_from_file_location('content_v8_helpers',ROOT/'scripts/replay/79j_run_p1_target_mix_v8.py')
v8 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(v8)
base = v8.base
import p1_content_workload_v14 as workload
from p1_profile_preflight_v14 import validate
import p1_controlled_replay_v13 as client

NEW = ['scripts/replay/79r_run_p1_content_profile_v14.py','scripts/replay/p1_content_workload_v14.py',
       'scripts/replay/p1_profile_preflight_v14.py','scripts/replay/p1_controlled_replay_v13.py',
       'tests/unit/test_p1_content_profile_v14.py','tests/integration/audit_p1_content_profile_v14.py',
       'configs/p1-content-selection-profile-v14.json','configs/p1-live-mechanism-v11.json',
       'docs/p1/content-selection-profile-protocol-v14.md']


def history(out):
    hashes, sources = set(), {}
    for p in (ROOT/'results/raw').rglob('*'):
        if p.name not in ('evaluation-plan.jsonl','trace.jsonl','plan.jsonl','warmup-plan.jsonl') or out in p.parents or 'source' in p.relative_to(ROOT).parts:
            continue
        sources[p.relative_to(ROOT).as_posix()] = base.prior.sha(p)
        for r in base.rows(p):
            if isinstance(r,dict) and isinstance(r.get('prompt'),str): hashes.add(workload.digest(r['prompt']))
    return hashes,sources


def output_features(ref,out):
    counts = {}
    with (out/'public-output-tokenization.jsonl').open('a',encoding='utf-8') as f:
        for text in workload.alternatives(ref['prompt']):
            body = {'content':text,'add_special':False,'parse_special':False}
            request = urllib.request.Request(base.prior.URL+'/tokenize',data=json.dumps(body).encode(),headers={'Content-Type':'application/json'})
            with urllib.request.urlopen(request,timeout=30) as response:
                raw = response.read().decode('utf-8'); assert response.status == 200
            tokens = json.loads(raw)['tokens']; assert tokens and all(type(t) is int for t in tokens)
            counts[text] = len(tokens)
            f.write(json.dumps({'req_id':ref['req_id'],'request':body,'response_body':raw,'token_count':len(tokens)},ensure_ascii=False)+'\n')
    rule = workload.parse_public(ref['prompt']); text = '\n'.join(rule) if rule else '无'
    return {'req_id':ref['req_id'],'rule_ids':rule,'rule_output_tokens':counts[text]+1,
            'grammar_min_output_tokens':min(counts.values())+1,'grammar_max_output_tokens':max(counts.values())+1,
            'allowed_outputs':len(counts),'read_actual_prompt_only':True,'model_output_accessed':False}


async def collect(out,engine):
    out = out.resolve(); out.mkdir(parents=True,exist_ok=False)
    server = log = tokenizer = None
    started = time.monotonic(); cfg = workload.config()
    try:
        prep = base.read(ROOT/'results/raw/maintenance/20261026-p1-content-profile-preparation-v14/final-gate.json')
        assert prep['status'] == 'passed' and prep['WSL_unit_tests_passed'] == 8
        assert all(base.prior.sha(ROOT/p) == h for p,h in prep['source_sha256'].items())
        hashes = v8.frozen_sources(); hashes.update({p:base.prior.sha(ROOT/p) for p in v8.NEW_SOURCES+NEW})
        for name,digest in hashes.items():
            assert base.prior.sha(ROOT/name) == digest
            target = out/'source'/name; target.parent.mkdir(parents=True,exist_ok=True); target.write_bytes((ROOT/name).read_bytes())
        base.dump(out/'source-sha256.json',hashes)
        old_cfg = base.read(ROOT/'configs/p1-live-mechanism-v11.json'); model = Path(old_cfg['model_path'])
        assert await asyncio.to_thread(base.prior.sha,model) == old_cfg['model_sha256']
        runtime = base.prior.binary_inventory(engine); assert runtime == base.read(v8.RUN7/'engine-runtime-sha256.json')
        engine_sources = base.read(v8.RUN7/'engine-source-sha256.json')
        assert all(base.prior.sha(engine.parents[2]/p) == h for p,h in engine_sources.items())
        assert shutil.disk_usage(out).free >= 10*1024**3
        history_hashes, history_sources = await asyncio.to_thread(history,out)
        base.dump(out/'history-source-sha256.json',history_sources)
        base.dump(out/'environment.json',{'started_at':datetime.now().astimezone().isoformat(),'model_sha256':old_cfg['model_sha256'],
                  'engine_runtime_sha256':runtime,'historical_unique_prompts':len(history_hashes),'purpose':cfg['purpose'],
                  'online_updates_enabled':False,'G1_passed':False,'final_test_accessed':False})
        server,log = await v8.start(out,engine,model,'original')
        tokenizer = base.Tokenizer(out)
        refs, attempts, seen = [], [], set(history_hashes)
        for d in workload.descriptors(cfg):
            for nonce in range(100):
                full = workload.materialize(dict(d,nonce=nonce))
                duplicate = full['content_sha256'] in seen
                attempts.append({'req_id':d['req_id'],'nonce':nonce,'content_sha256':full['content_sha256'],'duplicate':duplicate})
                if not duplicate: break
            else: raise RuntimeError('no unique prompt in registered nonce range')
            seen.add(full['content_sha256'])
            tokens = await asyncio.to_thread(tokenizer,full['prompt'])
            full.update(input_tokens=len(tokens),token_ids_sha256=workload.digest(json.dumps(tokens,separators=(',',':'))),grammar=workload.grammar(full['prompt']))
            refs.append(full)
        tokenizer.close(); tokenizer = None
        public = [workload.public_row(r) for r in refs]
        gate = validate(public,history_hashes,cfg['purpose'])
        base.write_rows(out/'evaluation-plan.jsonl',refs); base.write_rows(out/'public-plan.jsonl',public)
        base.write_rows(out/'materialization-attempts.jsonl',attempts); base.dump(out/'preflight.json',gate)
        for r in public:
            assert set(client.completion_payload(r)) == {'prompt','n_predict','temperature','stream','ignore_eos','cache_prompt','request_class','client_req_id','grammar'}
        features = []
        for i,r in enumerate(refs,1):
            features.append(await asyncio.to_thread(output_features,r,out))
            assert time.monotonic()-started < cfg['batch_timeout_seconds']
            if i%12 == 0: print(f'FEATURE {i}/48',flush=True)
        base.write_rows(out/'public-features-before-data.jsonl',features)
        feature_hash = base.prior.sha(out/'public-features-before-data.jsonl')
        base.dump(out/'pre-measurement-freeze.json',{'recorded_at':datetime.now().astimezone().isoformat(),
                  'public_features_sha256':feature_hash,'planned':48,'outputs_read':0,'G1_passed':False})
        warm = base.rows(ROOT/'results/raw/development/20261026-0845-p1-target-mix-v8-r2/warmup-plan.jsonl')
        await v8.warm(out,warm)
        for i,(ref,row) in enumerate(zip(refs,public),1):
            assert time.monotonic()-started < cfg['batch_timeout_seconds']
            assert all(base.prior.sha(ROOT/p) == h for p,h in hashes.items())
            folder = out/'cases'/f'q{i:03d}'; folder.mkdir(parents=True)
            await client.replay([row],folder/'replay',base.prior.URL,.01,30,30,50,60)
            audit = base.audit_replay(folder/'replay',[row]); base.dump(folder/'recording-audit.json',audit)
            assert not audit['flags'] and audit['terminal_counts']['completed'] == 1
            actual = base.rows(folder/'replay/client.jsonl')[0]
            assert actual['output_tokens_known'] and actual['finish_reason'] == 'stop' and actual['cached_prompt_tokens'] == 0
            quality = workload.quality(ref,actual); base.dump(folder/'quality.json',quality)
            await base.await_idle(folder,'after-request')
            base.dump(out/'batch-status.json',{'status':'profiling','completed':i,'planned':48,'last_correct':quality['whole_correct']})
            print(f'CASE {i}/48 {row["category"]} tokens={actual["output_tokens"]} correct={quality["whole_correct"]}',flush=True)
        accounting = await v8.finish(out,server,log,[r['req_id'] for r in public]+['warm0','warm1'],'original'); log = None
        assert base.prior.sha(out/'public-features-before-data.jsonl') == feature_hash
        assert all(base.prior.sha(ROOT/p) == h for p,h in hashes.items())
        assert base.prior.binary_inventory(engine) == runtime and await asyncio.to_thread(base.prior.sha,model) == old_cfg['model_sha256']
        assert all(base.prior.sha(engine.parents[2]/p) == h for p,h in engine_sources.items())
        base.dump(out/'summary.json',{'status':'completed','measured_requests':48,'warmups':2,'accounting':accounting,
                  'elapsed_seconds':time.monotonic()-started,'source_model_runtime_unchanged':True,'service_exit_zero':True,
                  'scheduler_comparison_started':False,'G1_passed':False,'final_test_accessed':False,
                  'finished_at':datetime.now().astimezone().isoformat()})
    except BaseException:
        (out/'error-traceback.txt').write_text(traceback.format_exc(),encoding='utf-8'); raise
    finally:
        if tokenizer is not None: tokenizer.close()
        if server is not None and log is not None: await base.prior.prior.engine.stop_server(server,log)


if __name__ == '__main__':
    p = argparse.ArgumentParser(); p.add_argument('--out-dir',type=Path,required=True); p.add_argument('--engine-bin',type=Path,required=True)
    a = p.parse_args(); asyncio.run(asyncio.wait_for(collect(a.out_dir,a.engine_bin.resolve(strict=True)),1500))
