#!/usr/bin/env python3
"""Paired prompt repair plus a fresh development quality pilot; no scheduler test."""
import argparse
import asyncio
from collections import Counter
from datetime import datetime
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts/replay'))
spec = importlib.util.spec_from_file_location('p1_frozen_budget_helpers', ROOT / 'scripts/replay/79c_run_p1_output_budget_probe.py')
budget = importlib.util.module_from_spec(spec)
spec.loader.exec_module(budget)
base = budget.base
from p1_workload import build_candidates
import p1_workload_v2 as workload

CONFIG = ROOT / 'configs/p1-prompt-task-repair-v2.json'
PROTOCOL = 'docs/p1/prompt-task-repair-protocol-v2.md'
NEW_SOURCES = ['scripts/replay/79c_run_p1_output_budget_probe.py',
    'scripts/replay/79d_run_p1_prompt_task_repair_v2.py',
    'scripts/replay/p1_workload_v2.py', 'configs/p1-prompt-task-repair-v2.json',
    'tests/unit/test_p1_workload_v2.py', 'tests/unit/test_p1_prompt_task_repair_runner.py', PROTOCOL,
    'docs/p1/prompt-task-repair-quality-rubric-v2.md']
TIMEOUT_SECONDS = 1800
SCOPE = ('Development prompt/task repair pilot: eight post-hoc sentinels paired v1/v2, '
    'then 24 new development v2 prompts. Not final test, calibration fit, scheduler performance, '
    'or automatic semantic-quality acceptance. cache_prompt=false for every completion.')


def fresh_descriptors(cfg):
    return build_candidates('development', 22001, config=cfg)


def model_free_checks(cfg):
    frozen = base.read(budget.OLD / 'source-sha256.json')
    assert len(frozen) == 38 and all(base.prior.sha(ROOT / p) == h for p, h in frozen.items())
    old_cfg = base.read(ROOT / 'configs/p1-development-workload-v1.json')
    for key in ('max_output_tokens', 'sampling', 'input_token_buckets', 'slot_context_tokens',
                'input_plus_output_safety_tokens', 'tokenizer_request', 'prompt_format'):
        assert cfg[key] == old_cfg[key], 'Changed registered execution/workload field: ' + key
    assert cfg['development']['seeds'] == [22001]
    assert cfg['development']['interactive_count'] == cfg['development']['background_count'] == 12
    selected, input_hashes = budget.selected_inputs()
    assert len(selected) == 8
    for _, row in selected:
        revised = workload.render_repaired_prompt(row)
        assert isinstance(revised, str) and revised and revised != row['prompt']
    fresh = fresh_descriptors(cfg)
    counts = Counter((r['category'], r['input_token_bucket']) for r in fresh)
    assert len(fresh) == 24 and len(counts) == 6 and set(counts.values()) == {4}
    assert all(r['split'] == 'development' and r['seed'] == 22001 and r['ignore_eos'] is False for r in fresh)
    assert not ({r['template_family'] for r in fresh} & {r['template_family'] for _, r in selected})
    try:
        build_candidates('final', 31001, config=cfg)
    except ValueError:
        pass
    else:
        raise AssertionError('Final generation must stay disabled')
    return {'status': 'passed', 'model_started': False, 'sentinel_pairs': 8,
        'paired_completions': 16, 'fresh_development_completions': 24,
        'fresh_per_category_input_bucket': 4, 'measured_completions': 40,
        'warmup_completions': 2, 'expected_cap_events': 84,
        'cache_prompt': False, 'old_frozen_sources_unchanged': 38,
        'input_source_sha256': input_hashes, 'scope': SCOPE}


def actual_bucket(length, category, cfg):
    for bucket, limits in enumerate(cfg['input_token_buckets'][category]):
        if limits[0] <= length <= limits[1]:
            return bucket, limits
    return None, None


def measured_plan(selected, fresh, tokenizer, cfg):
    paired = []
    for index, (case, original) in enumerate(selected, 1):
        old_tokens = tokenizer(original['prompt'])
        assert len(old_tokens) == original['input_tokens']
        assert budget.text_sha(json.dumps(old_tokens, separators=(',', ':'))) == original['token_ids_sha256']
        versions = ('v1', 'v2') if index % 2 else ('v2', 'v1')
        revised_prompt = workload.render_repaired_prompt(original)
        revised_tokens = tokenizer(revised_prompt)
        for version in versions:
            prompt, tokens = (original['prompt'], old_tokens) if version == 'v1' else (revised_prompt, revised_tokens)
            bucket_index, limits = actual_bucket(len(tokens), original['category'], cfg)
            row = dict(original, req_id=f'repair-{index:02d}-{version}', arrival_offset_ms=0,
                prompt=prompt, input_tokens=len(tokens), cache_prompt=False,
                input_token_bucket=bucket_index, input_token_range=limits,
                content_sha256=budget.text_sha(prompt),
                token_ids_sha256=budget.text_sha(json.dumps(tokens, separators=(',', ':'))),
                repair_role='paired_sentinel', prompt_version=version, pair_index=index,
                source_case=case, source_req_id=original['req_id'],
                source_input_tokens=original['input_tokens'],
                source_input_token_bucket=original['input_token_bucket'],
                source_content_sha256=original['content_sha256'], posthoc_sentinel=True,
                template_version=original['template_version'] if version == 'v1' else 'p1_prompt_task_repair_v2')
            row.update(workload.repair_metadata(original))
            row['prompt_repair_variant'] = workload.VARIANT if version == 'v2' else 'unmodified_v1_reference'
            assert row['input_tokens'] + row['max_tokens'] + 1 <= 2048
            paired.append(row)
    for row in fresh:
        row.update(arrival_offset_ms=0, cache_prompt=False,
            repair_role='fresh_development_pilot', prompt_version='v2', pair_index=None,
            source_case=None, source_req_id=None, posthoc_sentinel=False)
        limits = cfg['input_token_buckets'][row['category']][row['input_token_bucket']]
        assert limits[0] <= row['input_tokens'] <= limits[1]
        assert row['input_tokens'] + row['max_tokens'] + 1 <= 2048
    plan = paired + fresh
    assert len(plan) == len({r['req_id'] for r in plan}) == 40
    assert [(r['pair_index'], r['prompt_version']) for r in paired] == [
        (i, version) for i in range(1, 9) for version in (('v1', 'v2') if i % 2 else ('v2', 'v1'))]
    budget.validate(plan)
    return plan


def diagnostics(row):
    final = row['native_final']
    return {key: row[key] for key in ('req_id', 'repair_role', 'prompt_version', 'pair_index',
        'source_case', 'source_req_id', 'category', 'input_tokens', 'input_token_bucket',
        'max_tokens', 'output_tokens', 'terminal', 'cache_prompt', 'new_prompt_tokens', 'cached_prompt_tokens')} | {
        'stop_type': final.get('stop_type'), 'context_truncated': final.get('truncated'),
        'content': row['content'], 'content_sha256': budget.text_sha(row['content']),
        'repetition': budget.ngram_diagnostics(row['content']),
        'assistant_qualitative_review': 'pending_exploratory_subjective_review_no_automatic_pass'}


async def collect(args, cfg):
    out = args.out_dir.resolve()
    out.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    completed = []
    server = log = sampler = sample_log = tokenizer = None
    base.dump(out / 'batch-status.json', {'status': 'preparing', 'started_at': datetime.now().astimezone().isoformat()})
    try:
        preflight = model_free_checks(cfg)
        base.dump(out / 'model-free-preflight.json', preflight)
        assert shutil.disk_usage(out).free >= 10 * 1024**3
        base.free_port()
        engine = args.engine_bin.resolve(strict=True)
        model = args.model.resolve(strict=True)
        engine_root = engine.parents[2]
        fixed = base.read(ROOT / 'configs/p1-development-validation-v1.json')
        runtime = base.prior.binary_inventory(engine)
        assert runtime == base.read(budget.OLD / 'engine-runtime-sha256.json')
        model_sha = await asyncio.to_thread(base.prior.sha, model)
        assert model_sha == fixed['model_sha256']
        commit = base.prior.git_output(engine_root, 'rev-parse', 'HEAD').strip()
        assert commit == fixed['engine_commit']
        gate_dir = args.client_gate_dir.resolve(strict=True)
        gate = base.read(gate_dir / 'summary.json')
        assert gate['status'] == 'passed'
        assert all(base.prior.sha(ROOT / p) == h for p, h in gate['source_sha256'].items())
        shutil.copytree(gate_dir, out / 'client-gate')
        old_hashes = base.read(budget.OLD / 'source-sha256.json')
        hashes = {}
        for relative in list(old_hashes) + NEW_SOURCES:
            source = ROOT / relative
            target = out / 'source' / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read_bytes())
            hashes[relative] = base.prior.sha(source)
        assert all(hashes[p] == h for p, h in old_hashes.items())
        for relative, digest in preflight['input_source_sha256'].items():
            assert base.prior.sha(ROOT / relative) == digest
            target = out / 'input-source' / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((ROOT / relative).read_bytes())
        engine_hashes = base.read(budget.OLD / 'engine-source-sha256.json')
        for relative, digest in engine_hashes.items():
            source = engine_root / relative
            assert base.prior.sha(source) == digest
            target = out / 'engine-source' / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read_bytes())
        for name, value in (('source-sha256.json', hashes), ('input-source-sha256.json', preflight['input_source_sha256']),
                            ('engine-source-sha256.json', engine_hashes), ('engine-runtime-sha256.json', runtime)):
            base.dump(out / name, value)
        base.dump(out / 'environment.json', {'recorded_at': datetime.now().astimezone().isoformat(),
            'python': sys.version, 'kernel': Path('/proc/version').read_text(), 'engine_commit': commit,
            'engine_binary': str(engine), 'model': str(model), 'model_sha256': model_sha,
            'project_commit': base.prior.git_output(ROOT, 'rev-parse', 'HEAD').strip(),
            'project_working_tree': base.prior.git_output(ROOT, 'status', '--short'),
            'scope': SCOPE, 'protocol': PROTOCOL, 'cache_prompt': False,
            'batch_timeout_seconds': TIMEOUT_SECONDS})
        base.capture_gpu(out / 'gpu-before.txt')
        sample_log = (out / 'gpu-samples.csv').open('xb')
        sampler = await asyncio.create_subprocess_exec('nvidia-smi', '--query-gpu=timestamp,memory.used,utilization.gpu,temperature.gpu,power.draw',
            '--format=csv', '--loop-ms=1000', stdout=sample_log, stderr=asyncio.subprocess.STDOUT)
        service = out / 'service'
        service.mkdir()
        server, log = await base.prior.start_server(service, engine, model, 'original', 5000)
        props = base.read(service / 'props.json')
        assert props['total_slots'] == 2 and props['default_generation_settings']['n_ctx'] == 2048
        base.dump(out / 'service-ownership.json', {'pid': server.pid, 'owner': 'this_script_created_process', 'port_was_free_before_start': True})
        tokenizer = base.Tokenizer(out)
        try:
            selected = budget.selected_inputs()[0]
            fresh = await asyncio.to_thread(workload.materialize_v2, fresh_descriptors(cfg), tokenizer, cfg)
            plan = await asyncio.to_thread(measured_plan, selected, fresh, tokenizer, cfg)
            warm_prompt = '<|im_start|>system\n你是简洁的助手。<|im_end|>\n<|im_start|>user\n请说准备好了。<|im_end|>\n<|im_start|>assistant\n'
            warm_tokens = await asyncio.to_thread(tokenizer, warm_prompt)
            warm = [{'req_id': f'warm{i}', 'category': 'interactive', 'arrival_offset_ms': i*500,
                'prompt': warm_prompt, 'input_tokens': len(warm_tokens), 'max_tokens': 8,
                'temperature': 0, 'ignore_eos': False, 'cache_prompt': False} for i in range(2)]
            base.write_rows(out / 'plan.jsonl', plan)
            base.write_rows(out / 'paired-sentinel-plan.jsonl', plan[:16])
            base.write_rows(out / 'fresh-development-plan.jsonl', plan[16:])
            base.write_rows(out / 'warmup-plan.jsonl', warm)
            base.dump(out / 'tokenization-summary.json', {'calls': tokenizer.count, 'warmup_prompts': 1,
                'sentinel_original_prompts': 8, 'sentinel_repaired_prompts': 8, 'fresh_development_prompts': 24,
                'final_test_generated_or_read': False})
        finally:
            tokenizer.close()
            tokenizer = None
        await asyncio.wait_for(base.replay(warm, out / 'warmup', base.prior.URL, 1, 15, 10, 50, 360), timeout=60)
        warm_audit = base.audit_replay(out / 'warmup', warm)
        assert not warm_audit['flags'] and warm_audit['terminal_counts']['completed'] == 2
        assert all(r['cached_prompt_tokens'] == 0 and r['cache_prompt'] is False for r in base.rows(out / 'warmup/client.jsonl'))
        await base.await_idle(service, 'after-warmup')
        base.dump(out / 'warmup-audit.json', warm_audit | {'cache_zero_checked': True,
            'cap_pairs': budget.cap_pairs(service / 'server.log', ['warm0', 'warm1'])})
        for index, row in enumerate(plan, 1):
            assert time.monotonic() - started < TIMEOUT_SECONDS
            assert shutil.disk_usage(out).free >= 4 * 1024**3
            assert sum(p.stat().st_size for p in out.rglob('*') if p.is_file()) < 512 * 1024**2
            assert all(base.prior.sha(ROOT / p) == h for p, h in hashes.items())
            directory = out / 'cases' / f'q{index:02d}-{row["req_id"]}'
            directory.mkdir(parents=True)
            manifest = {'status': 'running', 'sequence_index': index,
                **{key: row[key] for key in ('req_id', 'repair_role', 'prompt_version', 'pair_index', 'source_case', 'source_req_id')}}
            base.dump(directory / 'manifest.json', manifest)
            await base.replay([row], directory / 'replay', base.prior.URL, .01, 45, 45, 50, 360)
            audit = base.audit_replay(directory / 'replay', [row])
            base.dump(directory / 'audit.json', audit)
            assert not audit['flags'] and audit['terminal_counts']['completed'] == 1, 'Retain failing request and stop later requests'
            observed = base.rows(directory / 'replay/client.jsonl')[0]
            assert observed['cache_prompt'] is False and observed['cached_prompt_tokens'] == 0, 'cache_prompt=false did not produce zero cached input'
            await base.await_idle(directory, 'after-request')
            audit.update(idle_verified=True, cache_zero_checked=True, cap_pairs=budget.cap_pairs(service / 'server.log', [row['req_id']]))
            base.dump(directory / 'audit.json', audit)
            result = diagnostics(observed)
            (directory / 'output.txt').write_text(observed['content'], encoding='utf-8')
            base.dump(directory / 'diagnostic.json', result)
            manifest.update(status='completed', finished_at=datetime.now().astimezone().isoformat(), assistant_qualitative_review='pending', audit=audit)
            base.dump(directory / 'manifest.json', manifest)
            completed.append({'directory': directory.relative_to(out).as_posix(), **result})
            base.dump(out / 'diagnostics.json', completed)
            base.dump(out / 'batch-status.json', {'status': 'running', 'requests_completed': len(completed),
                'paired_completed': sum(r['repair_role'] == 'paired_sentinel' for r in completed),
                'fresh_completed': sum(r['repair_role'] == 'fresh_development_pilot' for r in completed),
                'elapsed_seconds': time.monotonic()-started})
            print(f'DONE {index}/40 {row["req_id"]} output={result["output_tokens"]}/{row["max_tokens"]} stop={result["stop_type"]}', flush=True)
        await base.await_idle(service, 'after-all')
        await base.prior.prior.engine.stop_server(server, log)
        log = None
        base.dump(out / 'service-stop.json', {'pid': server.pid, 'server_exit_code': server.returncode, 'stopped_owned_process_only': True})
        assert server.returncode == 0
        accounting = base.gate.verify_cap_events(service / 'server.log', 0)
        assert accounting['events'] == 84
        expected_ids = {r['req_id'] for r in plan} | {'warm0', 'warm1'}
        assert {e['clientid'] for e in base.gate.cap_events(service / 'server.log')} == expected_ids
        base.dump(out / 'cap-pairs.json', budget.cap_pairs(service / 'server.log', sorted(expected_ids)))
        assert base.prior.binary_inventory(engine) == runtime
        assert all(base.prior.sha(ROOT / p) == h for p, h in hashes.items())
        assert all(base.prior.sha(ROOT / p) == h for p, h in preflight['input_source_sha256'].items())
        assert all(base.prior.sha(engine_root / p) == h for p, h in engine_hashes.items())
        assert await asyncio.to_thread(base.prior.sha, model) == model_sha
        base.dump(out / 'engine-runtime-sha256-after.json', base.prior.binary_inventory(engine))
        base.dump(out / 'model-sha256-after.json', {'path': str(model), 'sha256': model_sha})
        summary = {'status': 'completed', 'study': 'p1-prompt-task-repair-v2', 'scope': SCOPE,
            'sentinel_pairs': 8, 'paired_completions': 16, 'fresh_development_completions': 24,
            'measured_completions': 40, 'warmup_completions': 2, 'completion_requests': 42,
            'terminal_counts': {s: sum(r['terminal'] == s for r in completed) for s in base.STATES},
            'accounting': accounting, 'server_exit_code': server.returncode, 'cache_prompt': False,
            'all_cached_prompt_tokens_zero': True, 'source_runtime_model_inputs_unchanged': True,
            'final_test_generated_or_read': False, 'assistant_qualitative_review': 'pending_exploratory_subjective_review_no_automatic_pass',
            'finished_at': datetime.now().astimezone().isoformat(), 'elapsed_seconds': time.monotonic()-started}
        base.dump(out / 'summary.json', summary)
        base.dump(out / 'batch-status.json', summary)
        print(json.dumps(summary, ensure_ascii=False), flush=True)
    except BaseException as exc:
        (out / 'error-traceback.txt').write_text(traceback.format_exc(), encoding='utf-8')
        base.dump(out / 'batch-status.json', {'status': 'failed', 'requests_completed': len(completed),
            'error': repr(exc), 'finished_at': datetime.now().astimezone().isoformat(), 'elapsed_seconds': time.monotonic()-started})
        raise
    finally:
        if tokenizer is not None:
            tokenizer.close()
        if server is not None and log is not None:
            await base.prior.prior.engine.stop_server(server, log)
            base.dump(out / 'service-stop.json', {'pid': server.pid, 'server_exit_code': server.returncode,
                'stopped_owned_process_only': True, 'cleanup_after_interrupted_or_failed_batch': True})
        if sampler is not None and sampler.returncode is None:
            sampler.terminate()
            await sampler.wait()
        if sample_log is not None:
            sample_log.close()
            base.capture_gpu(out / 'gpu-after.txt')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out-dir', type=Path)
    parser.add_argument('--config', type=Path, default=CONFIG)
    parser.add_argument('--model-free-check', action='store_true')
    parser.add_argument('--engine-bin', type=Path)
    parser.add_argument('--model', type=Path, default=base.prior.DEFAULT_MODEL)
    parser.add_argument('--client-gate-dir', type=Path, default=ROOT / 'results/raw/client-verification/20261024-p1-development-client-v2')
    args = parser.parse_args()
    cfg = base.read(args.config)
    if args.config.resolve() != CONFIG.resolve():
        parser.error('This registered runner only accepts its fixed v2 configuration')
    if args.model_free_check:
        result = model_free_checks(cfg)
        result['source_sha256'] = {p: base.prior.sha(ROOT / p) for p in NEW_SOURCES}
        if args.out_dir is not None:
            args.out_dir.resolve().mkdir(parents=True, exist_ok=False)
            base.dump(args.out_dir.resolve() / 'summary.json', result)
        print(json.dumps(result, ensure_ascii=False))
        return
    if args.engine_bin is None or args.out_dir is None:
        parser.error('--engine-bin and --out-dir are required for real-model collection')
    asyncio.run(asyncio.wait_for(collect(args, cfg), timeout=TIMEOUT_SECONDS))


if __name__ == '__main__':
    main()
