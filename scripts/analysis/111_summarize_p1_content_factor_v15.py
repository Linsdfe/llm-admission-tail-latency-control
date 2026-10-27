"""Describe all32 diagnostic outputs, without changing the registered score."""
from collections import Counter
import argparse
import hashlib
import json
from pathlib import Path
import re


def analyze(folder,run):
    p=folder/'independent-audit.json';audit=json.loads(p.read_text(encoding='utf-8'))
    fixtures={r['req_id']:r for r in json.loads((run/'fixtures-evaluation-only.json').read_text(encoding='utf-8'))}
    counts=Counter();by_arm={c:Counter() for c in ('TG','TU','SG','SU')}
    for r in audit['request_results']:
        c=by_arm[r['condition']];ref=fixtures[r['source_req_id']]
        raw=r['content'].strip();lines=raw.splitlines();observed=[] if raw=='无' else lines
        expected=ref['expected_ids'];ids_only=raw=='无' or bool(lines) and all(re.fullmatch(r'R[0-9]{4}',s) for s in lines)
        c['set_correct_ignoring_order_posthoc']+=ids_only and len(observed)==len(set(observed)) and set(observed)==set(expected)
        c['ids_only_but_unsorted']+=ids_only and observed!=sorted(set(observed))
        c['non_id_explanation_or_invalid_text']+=not ids_only
        if not expected:counts['empty_fixture_condition_appearances']+=1;counts['empty_correct']+=r['strict_correct']
        if len(expected)==ref['record_count']:counts['full_fixture_condition_appearances']+=1;counts['full_correct']+=r['strict_correct']
    result={'status':'completed_posthoc_description','counts':dict(counts),'by_arm':{c:dict(v) for c,v in by_arm.items()},
            'new_GPU_measurements':0,'primary_score_not_replaced':True,'subjective_score_unchanged':88,
            'limits':'Set equality ignoring order is a posthoc explanatory count, not the registered strict or field-only success metric.',
            'audit_sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'G1_passed':False}
    with (folder/'posthoc-error-description.json').open('x',encoding='utf-8') as f:json.dump(result,f,ensure_ascii=False,indent=2)
    text='# 全部32次诊断结果\n\n8组已见事实，四条件TG/TU/SG/SU，不替换v14的2/48。严格正确是主指标；只去首尾空白是辅助指标，乱序仍不算正确。\n\n'
    text+='|序号|源资料|条件|标准答案|实际输出|严格正确|\n|---:|---|---|---|---|---|\n'
    for i,r in enumerate(audit['request_results'],1):
        text+=f'|{i}|{r["source_req_id"]}|{r["condition"]}|{r["expected_answer"].replace(chr(10),"<br>")}|{r["content"].replace(chr(10),"<br>")}|{"是" if r["strict_correct"] else "否"}|\n'
    with (folder/'逐题结果.md').open('x',encoding='utf-8') as f:f.write(text)
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--folder',type=Path,required=True);p.add_argument('--run-dir',type=Path,required=True)
    a=p.parse_args();print(json.dumps(analyze(a.folder,a.run_dir),ensure_ascii=False))
