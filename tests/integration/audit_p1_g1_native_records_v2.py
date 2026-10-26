"""Post-collection supplementary native event checks; no scoring-rule changes."""
import argparse,importlib.util,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
s=importlib.util.spec_from_file_location('gate',ROOT/'tests/integration/verify_background_cap_engine.py'); gate=importlib.util.module_from_spec(s); s.loader.exec_module(gate)
def audit(run):
 summary=json.loads((run/'summary.json').read_text(encoding='utf-8'))
 rows=lambda p:[json.loads(x) for x in p.read_text(encoding='utf-8').splitlines() if x]
 plan=rows(run/'plan.jsonl'); warm=rows(run/'warmup/replay/client.jsonl') if (run/'warmup/replay/client.jsonl').exists() else rows(run/'warmup/client.jsonl')
 assert len(warm)==2 and all(r['terminal']=='completed' for r in warm)
 expected={r['req_id'] for r in plan}|{r['req_id'] for r in warm}
 accounting=gate.verify_cap_events(run/'server.log',0); events=gate.cap_events(run/'server.log')
 assert {e['clientid'] for e in events}==expected and len(events)==2*len(expected)==summary['cap_events']
 stop=json.loads((run/'service-stop.json').read_text(encoding='utf-8')); assert stop['exit_code']==0
 return {'status':'passed','measured':len(plan),'warmups':len(warm),'cap_events':len(events),'service_exit_zero':True,'scope':'Supplementary retrospective event/ID checks using frozen gate implementation; no score/threshold changes.'}
if __name__=='__main__':
 p=argparse.ArgumentParser(); p.add_argument('--run-dir',type=Path,required=True); p.add_argument('--out',type=Path,required=True); a=p.parse_args(); result=audit(a.run_dir)
 with a.out.open('x',encoding='utf-8') as f: json.dump(result,f,ensure_ascii=False,indent=2)
 print(json.dumps(result,ensure_ascii=False))
