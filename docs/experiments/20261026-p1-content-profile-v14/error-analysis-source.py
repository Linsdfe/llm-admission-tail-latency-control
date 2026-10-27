"""Post-collection error accounting; no new generation or acceptance changes."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def summarize(folder, run):
    audit_path = folder / 'independent-audit.json'
    audit = json.loads(audit_path.read_text(encoding='utf-8'))
    refs = [json.loads(s) for s in (run / 'evaluation-plan.jsonl').read_text(encoding='utf-8').splitlines() if s]
    refmap = {r['req_id']: r for r in refs}
    counts = Counter()
    for r in audit['request_results']:
        ref = refmap[r['req_id']]
        counts['total'] += 1
        counts['whole_correct'] += r['whole_correct']
        counts['has_missing_items'] += bool(r['missing'])
        counts['has_extra_items'] += bool(r['extra'])
        counts['missing_items'] += len(r['missing'])
        counts['extra_items'] += len(r['extra'])
        if r['expected_count'] == 0:
            counts['expected_empty'] += 1
            counts['empty_correct'] += r['whole_correct']
        if r['expected_count'] == ref['record_count']:
            counts['expected_all_records'] += 1
            counts['all_records_correct'] += r['whole_correct']
    assert counts['total'] == 48 and counts['whole_correct'] == 2
    assert not audit['task_entry_passed'] and not audit['scheduler_comparison_started']
    result = {'status':'task_entry_rejected','counts':dict(counts),
              'evidence_sha256':hashlib.sha256(audit_path.read_bytes()).hexdigest(),
              'evaluation_plan_sha256':hashlib.sha256((run/'evaluation-plan.jsonl').read_bytes()).hexdigest(),
              'new_GPU_measurements':0,'subjective_score_unchanged':88,
              'scope':'posthoc descriptive failure counts; does not identify whether temporal reasoning, prompt or grammar caused errors',
              'G1_passed':False,'final_test_accessed':False}
    with (folder/'error-patterns.json').open('x',encoding='utf-8') as f:
        json.dump(result,f,ensure_ascii=False,indent=2)
    text = '# 48道题全部结果\n\n采集发生于2026-10-26，收尾发生于2026-10-27。严格整题正确2/48；原标准和错误不改。\n\n'
    text += '|序号|请求|标准答案|模型输出|整题正确|\n|---:|---|---|---|---|\n'
    for i,r in enumerate(audit['request_results'],1):
        text += f'|{i}|{r["req_id"]}|{r["expected_answer"].replace(chr(10),"<br>")}|{r["content"].replace(chr(10),"<br>")}|{"是" if r["whole_correct"] else "否"}|\n'
    with (folder/'逐题结果.md').open('x',encoding='utf-8') as f:
        f.write(text)
    return result


if __name__ == '__main__':
    p=argparse.ArgumentParser(); p.add_argument('--folder',type=Path,required=True); p.add_argument('--run-dir',type=Path,required=True)
    a=p.parse_args(); print(json.dumps(summarize(a.folder,a.run_dir),ensure_ascii=False))
