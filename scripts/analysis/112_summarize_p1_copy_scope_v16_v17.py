"""Publish both failed and accepted fresh probes, without a performance comparison."""
from collections import Counter
import hashlib
import json
from pathlib import Path
import shutil
import statistics

ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'docs/experiments/20261027-p1-copy-scope-v16-v17'
RUNS={16:'20261027-1030-p1-copy-profile-v16',17:'20261027-1040-p1-copy-profile-v17'}
def read(p):return json.loads(p.read_text(encoding='utf-8'))
def rows(p):return [json.loads(s) for s in p.read_text(encoding='utf-8').splitlines() if s]
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def dump(p,x):p.write_text(json.dumps(x,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')

def main():
    OUT.mkdir(parents=True,exist_ok=True);result={};provenance={}
    text=['# 128份新资料的全部输出','', 'v16保留失败；v17是另一批新资料及公开前处理，不能作单因素因果比较。','']
    for version,runid in RUNS.items():
        run=ROOT/'results/raw/development'/runid;a=read(run/'independent-audit.json');refs=rows(run/'evaluation-plan.jsonl');plan=rows(run/'public-plan.jsonl')
        hashes=set();history=read(run/'history-source-sha256.json')
        for name,h in history.items():
            p=ROOT/name;assert sha(p)==h,(name,'historical source changed')
            for r in rows(p):
                if isinstance(r,dict) and isinstance(r.get('prompt'),str):hashes.add(hashlib.sha256(r['prompt'].encode()).hexdigest())
        assert not hashes & {r['content_sha256'] for r in plan}
        for p,h in read(run/'source-sha256.json').items():assert sha(run/'source'/p)==h==sha(ROOT/p)
        for row in plan:
            assert row['req_id'].startswith(f'v{version}-dev-')
        provenance[str(version)]={'historical_plan_files':len(history),'historical_unique_prompts':len(hashes),
            'new_unique_prompts':64,'historical_overlap':0,'execution_source_files':len(read(run/'source-sha256.json')),
            'source_snapshots_current_and_unchanged':True,'environment_recorded_at':read(run/'environment.json')['started_at'],
            'worker':read(ROOT/f'results/raw/maintenance/20261027-p1-copy-profile-supervisor-v{version}/exit.json'),
            'actual_finished_at':read(run/'summary.json')['finished_at']}
        result[str(version)]={k:v for k,v in a.items() if k!='request_results'}
        result[str(version)]['run_id']=runid;result[str(version)]['elapsed_seconds']=read(run/'summary.json')['elapsed_seconds']
        result[str(version)]['descriptive_ranges']={}
        for category in ('interactive','background'):
            group=[r for r in a['request_results'] if r['category']==category]
            result[str(version)]['descriptive_ranges'][category]={k:{'min':min(r[k] for r in group),'median':statistics.median(r[k] for r in group),'max':max(r[k] for r in group)} for k in ('input_tokens','output_tokens','prompt_ms','predicted_ms')}
        dump(OUT/f'v{version}-audit.json',a);dump(OUT/f'v{version}-public-plan.json',plan);dump(OUT/f'v{version}-full-corpus-evaluation-only.json',refs)
        for name in ('preflight.json','pre-measurement-freeze.json','public-features-before-data.jsonl','source-sha256.json','summary.json'):
            shutil.copyfile(run/name,OUT/f'v{version}-{name}')
        for i,(row,r) in enumerate(zip(a['request_results'],refs),1):
            text.extend([f'## v{version} {i:02} {row["req_id"]}',f'类别：{row["category"]}；任务：{row["task_kind"]}；整题正确：{row["whole_correct"]}；输入/输出token：{row["input_tokens"]}/{row["output_tokens"]}。',''])
            if version==17:text.extend(['原始资料：','```text',r['raw_prompt_evaluation_only'],'```',''])
            text.extend(['实际提示：','```text',r['prompt'],'```','标准答案：','```text',row['expected_answer'],'```','模型原答：','```text',row['content'],'```',''])
    dump(OUT/'summary.json',{'versions':result,'total_measured_requests':128,'warmups':4,'native_launch_release_pairs':132,'G1_passed':False,'final_test_accessed':False})
    dump(OUT/'provenance-audit.json',{'status':'passed','versions':provenance,'final_test_accessed':False})
    (OUT/'逐题原文与结果.md').write_text('\n'.join(text),encoding='utf-8')
    shutil.copyfile(Path(__file__),OUT/'analysis-source.py')
    (OUT/'README.md').write_text('# 新资料验证v16/v17\n\nv16：59/64，后台27/32，未过采前入口。v17：公开程序取相关行后再整理，新64份全部正确，通过原门槛。两批各一次串行服务，不推断调度性能。\n\n[白话报告](../../reports/20261027/P1新资料验证与G1合同补齐.md)、[全部128题原文和回答](逐题原文与结果.md)、[独立来源核对](provenance-audit.json)、[汇总](summary.json)。完整原生日志仍在results/raw，GitHub仅含派生证据。\n',encoding='utf-8')
    dump(OUT/'report-sha256.json',{p.name:sha(p) for p in sorted(OUT.iterdir()) if p.is_file()})
    print(json.dumps({k:{'correct':v['whole_correct'],'total':64,'passed':v['task_acceptance_passed']} for k,v in result.items()}))

if __name__=='__main__':main()
