"""Post-collection independent preservation and ordering audit, no new inference."""
import argparse
import hashlib
import json
from pathlib import Path
import re

ROOT=Path(__file__).resolve().parents[2]
def audit():
    run=ROOT/'results/raw/development/20261027-1040-p1-copy-profile-v17'
    refs=[json.loads(s) for s in (run/'evaluation-plan.jsonl').read_text(encoding='utf-8').splitlines()]
    checks=0
    for r in refs:
        raw=r['raw_prompt_evaluation_only'];actual=r['prompt']
        def body(text):return text.split('资料\n',1)[1]
        def instruction(text):return re.search(r'(?m)^(?:提取|按)以下编号[^\n]+',text)[0]
        original_instruction=instruction(raw)
        targets=re.search(r'：(R\d+(?:,R\d+)*)。',original_instruction)[1].split(',')
        raw_rows=re.findall(r'(?m)^(R\d+)\|([1-9][0-9])$',body(raw))
        actual_rows=re.findall(r'(?m)^(R\d+)\|([1-9][0-9])$',body(actual))
        assert actual_rows==[x for x in raw_rows if x[0] in set(targets)],'actual rows/order match public raw extraction'
        assert instruction(actual)==original_instruction,'complete instruction preserved'
        truth=dict(raw_rows);answer='\n'.join(t+'|'+truth[t] for t in targets)
        assert answer==r['expected_answer'],'truth derived from actual raw source and instruction'
        assert len(raw_rows)-len(actual_rows) in (32,64,96),'registered distractor count'
        checks+=4
    return {'status':'passed_read_only_preprocessing_audit','checked_requests':len(refs),'checks':checks,
        'source':'actual raw documents and sent prompts','new_inference_requests':0,
        'post_collection_check':True,'preprocessing_cpu_cost_measured':False,'G1_passed':False}

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True);a=p.parse_args();r=audit()
    with a.out.open('x',encoding='utf-8') as f:json.dump(r,f,ensure_ascii=False,indent=2)
    print(json.dumps(r,ensure_ascii=False))
