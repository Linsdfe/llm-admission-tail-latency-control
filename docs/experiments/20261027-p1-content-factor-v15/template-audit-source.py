"""Post-collection offline template/token audit; no model is started."""
import argparse
import hashlib
import json
from pathlib import Path
import jinja2


def audit(run):
    props=json.loads((run/'props.json').read_text(encoding='utf-8'))
    template=jinja2.Environment(autoescape=False).from_string(props['chat_template'])
    prefix='<|im_start|>system\n只按资料筛选，不补信息。<|im_end|>\n<|im_start|>user\n'
    suffix='<|im_end|>\n<|im_start|>assistant\n'
    plan=[json.loads(s) for s in (run/'public-plan.jsonl').read_text(encoding='utf-8').splitlines() if s]
    checks=[]
    for prompt in dict.fromkeys(r['prompt'] for r in plan):
        assert prompt.startswith(prefix) and prompt.endswith(suffix)
        messages=[{'role':'system','content':'只按资料筛选，不补信息。'},
                  {'role':'user','content':prompt[len(prefix):-len(suffix)]}]
        rendered=template.render(messages=messages,tools=[],add_generation_prompt=True)
        checks.append({'prompt_sha256':hashlib.sha256(prompt.encode('utf-8')).hexdigest(),'rendered_equals_actual':rendered==prompt})
    special=[]
    for line in (run/'tokenization.jsonl').read_text(encoding='utf-8').splitlines():
        r=json.loads(line);tokens=json.loads(r['response_body'])['tokens']
        selected=[t for t in tokens if t in (151643,151644,151645)]
        special.append({'prompt_sha256':hashlib.sha256(r['request']['content'].encode('utf-8')).hexdigest(),
                        'chatml_tokens':selected,'correct_markers':selected==[151644,151645,151644,151645,151644]})
    ok=len(checks)==len(special)==16 and all(r['rendered_equals_actual'] for r in checks) and all(r['correct_markers'] for r in special)
    return {'status':'passed_read_only_template_audit' if ok else 'template_or_token_discrepancy',
            'model_started':False,'new_GPU_measurements':0,'jinja2_version':jinja2.__version__,
            'unique_prompts_checked':len(checks),'request_checks':checks,'token_checks':special,
            'limits':'Checks the recorded service template and special IDs; does not rule out all runtime or model issues.',
            'G1_passed':False}


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--run-dir',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    a=p.parse_args();r=audit(a.run_dir)
    with a.out.open('x',encoding='utf-8') as f:json.dump(r,f,ensure_ascii=False,indent=2)
    print(json.dumps({k:v for k,v in r.items() if k not in ('request_checks','token_checks')},ensure_ascii=False))
