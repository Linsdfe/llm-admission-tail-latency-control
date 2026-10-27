"""Bounded2x2 fault diagnosis on eight seen sources, not a task revalidation."""
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

ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'scripts/replay'))
s=importlib.util.spec_from_file_location('factor_v8_helpers',ROOT/'scripts/replay/79j_run_p1_target_mix_v8.py')
v8=importlib.util.module_from_spec(s);s.loader.exec_module(v8);base=v8.base
import p1_content_factor_v15 as diagnostic
import p1_controlled_replay_v13 as client

NEW=['scripts/replay/79s_run_p1_content_factor_v15.py','scripts/replay/p1_content_factor_v15.py',
     'scripts/replay/p1_controlled_replay_v13.py','tests/unit/test_p1_content_factor_v15.py',
     'tests/integration/audit_p1_content_factor_v15.py','configs/p1-content-factor-diagnostic-v15.json',
     'configs/p1-live-mechanism-v11.json','docs/p1/content-factor-diagnostic-protocol-v15.md']
PARENT=ROOT/'results/raw/development/20261026-1730-p1-content-profile-v14'


async def collect(out,engine):
    out=out.resolve();out.mkdir(parents=True,exist_ok=False);started=time.monotonic()
    server=log=tokenizer=None
    try:
        prep=base.read(ROOT/'results/raw/maintenance/20261027-p1-content-factor-preparation-v15/final-gate.json')
        assert prep['status']=='passed' and prep['WSL_unit_tests_passed']==8
        assert all(base.prior.sha(ROOT/p)==h for p,h in prep['source_sha256'].items())
        hashes=v8.frozen_sources();hashes.update({p:base.prior.sha(ROOT/p) for p in v8.NEW_SOURCES+NEW})
        for name,digest in hashes.items():
            assert base.prior.sha(ROOT/name)==digest
            p=out/'source'/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes((ROOT/name).read_bytes())
        base.dump(out/'source-sha256.json',hashes)
        cfg=diagnostic.config();old_cfg=base.read(ROOT/'configs/p1-live-mechanism-v11.json');model=Path(old_cfg['model_path'])
        assert await asyncio.to_thread(base.prior.sha,model)==old_cfg['model_sha256']
        runtime=base.prior.binary_inventory(engine);assert runtime==base.read(v8.RUN7/'engine-runtime-sha256.json')
        esources=base.read(v8.RUN7/'engine-source-sha256.json');assert all(base.prior.sha(engine.parents[2]/p)==h for p,h in esources.items())
        assert shutil.disk_usage(out).free>=10*1024**3
        refs=base.rows(PARENT/'evaluation-plan.jsonl');lookup={r['req_id']:r for r in refs}
        fixtures=[lookup[req] for req in cfg['source_req_ids']]
        base.dump(out/'fixtures-evaluation-only.json',fixtures)
        old_outputs=[];reused={}
        for ref in fixtures:
            index=next(i for i,r in enumerate(refs,1) if r['req_id']==ref['req_id'])
            p=PARENT/'cases'/f'q{index:03d}'/'replay/client.jsonl';actual=base.rows(p)[0]
            old_outputs.append({'source_req_id':ref['req_id'],'content':actual['content'],'source_path':p.relative_to(ROOT).as_posix(),'sha256':base.prior.sha(p)})
            reused[p.relative_to(ROOT).as_posix()]=base.prior.sha(p)
        reused[(PARENT/'evaluation-plan.jsonl').relative_to(ROOT).as_posix()]=base.prior.sha(PARENT/'evaluation-plan.jsonl')
        base.write_rows(out/'prior-TG-output.jsonl',old_outputs);base.dump(out/'reused-source-sha256.json',reused)
        base.dump(out/'environment.json',{'started_at':datetime.now().astimezone().isoformat(),'model_sha256':old_cfg['model_sha256'],
                  'engine_runtime_sha256':runtime,'purpose':cfg['purpose'],'new_source_facts':0,'G1_passed':False,'final_test_accessed':False})
        server,log=await v8.start(out,engine,model,'original');tokenizer=base.Tokenizer(out)
        plan=diagnostic.plans(fixtures);cache={}
        for row in plan:
            if row['prompt'] not in cache:cache[row['prompt']]=await asyncio.to_thread(tokenizer,row['prompt'])
            tokens=cache[row['prompt']];row.update(input_tokens=len(tokens),token_ids_sha256=diagnostic.digest(json.dumps(tokens,separators=(',',':'))))
            assert set(client.completion_payload(row))=={'prompt','n_predict','temperature','stream','ignore_eos','cache_prompt','request_class','client_req_id'}|({'grammar'} if 'grammar' in row else set())
        tokenizer.close();tokenizer=None
        gate=diagnostic.validate(plan,fixtures);base.dump(out/'preflight.json',gate);base.write_rows(out/'public-plan.jsonl',plan)
        base.dump(out/'pre-measurement-freeze.json',{'recorded_at':datetime.now().astimezone().isoformat(),
                  'plan_sha256':base.prior.sha(out/'public-plan.jsonl'),'planned':32,'outputs_read':0,'G1_passed':False})
        warm=base.rows(ROOT/'results/raw/development/20261026-0845-p1-target-mix-v8-r2/warmup-plan.jsonl');await v8.warm(out,warm)
        for i,row in enumerate(plan,1):
            assert time.monotonic()-started<cfg['batch_timeout_seconds']
            assert all(base.prior.sha(ROOT/p)==h for p,h in hashes.items())
            directory=out/'cases'/f'q{i:03d}';directory.mkdir(parents=True)
            await client.replay([row],directory/'replay',base.prior.URL,.01,30,30,50,60)
            audit=base.audit_replay(directory/'replay',[row]);base.dump(directory/'recording-audit.json',audit)
            assert not audit['flags'] and audit['terminal_counts']['completed']==1
            actual=base.rows(directory/'replay/client.jsonl')[0];assert actual['cached_prompt_tokens']==0 and actual['output_tokens_known']
            q=diagnostic.quality(lookup[row['source_req_id']],actual);base.dump(directory/'quality.json',q)
            await base.await_idle(directory,'after-request')
            base.dump(out/'batch-status.json',{'status':'diagnosing','completed':i,'planned':32,'condition':row['condition'],'last_strict_correct':q['strict_correct']})
            print(f'CASE {i}/32 {row["condition"]} strict={q["strict_correct"]} fields={q["outer_whitespace_field_correct"]} limit={q["output_limit"]}',flush=True)
        acc=await v8.finish(out,server,log,[r['req_id'] for r in plan]+['warm0','warm1'],'original');log=None
        assert all(base.prior.sha(ROOT/p)==h for p,h in hashes.items())
        assert all(base.prior.sha(ROOT/p)==h for p,h in reused.items())
        assert base.prior.binary_inventory(engine)==runtime and await asyncio.to_thread(base.prior.sha,model)==old_cfg['model_sha256']
        assert all(base.prior.sha(engine.parents[2]/p)==h for p,h in esources.items())
        base.dump(out/'summary.json',{'status':'completed','measured_requests':32,'warmups':2,'seen_fixtures':8,'distinct_prompts':16,
                  'accounting':acc,'elapsed_seconds':time.monotonic()-started,'source_model_runtime_unchanged':True,'service_exit_zero':True,
                  'new_task_acceptance_evaluated':False,'scheduler_comparison_started':False,'G1_passed':False,'final_test_accessed':False,
                  'finished_at':datetime.now().astimezone().isoformat()})
    except BaseException:
        (out/'error-traceback.txt').write_text(traceback.format_exc(),encoding='utf-8');raise
    finally:
        if tokenizer is not None:tokenizer.close()
        if server is not None and log is not None:await base.prior.prior.engine.stop_server(server,log)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--out-dir',type=Path,required=True);p.add_argument('--engine-bin',type=Path,required=True)
    a=p.parse_args();asyncio.run(asyncio.wait_for(collect(a.out_dir,a.engine_bin.resolve(strict=True)),1000))
