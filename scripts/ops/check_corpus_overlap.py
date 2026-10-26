"""Read-only pre-inference gate for complete-prompt overlap with registered history."""
import argparse,hashlib,json
from pathlib import Path
def rows(path): return [json.loads(x) for x in path.read_text(encoding='utf-8').splitlines() if x.strip()]
def check(candidate,history):
 seen={}; duplicates=[]; local={}; items=rows(candidate)
 for path in history:
  for row in rows(path):
   digest=hashlib.sha256(row['prompt'].encode('utf-8')).hexdigest(); seen.setdefault(digest,[]).append({'path':str(path),'req_id':row['req_id']})
 for row in items:
  digest=hashlib.sha256(row['prompt'].encode('utf-8')).hexdigest()
  if digest in seen or digest in local: duplicates.append({'req_id':row['req_id'],'sha256':digest,'history':seen.get(digest,[]),'within_batch':local.get(digest)})
  local[digest]=row['req_id']
 return {'status':'blocked_overlap' if duplicates else 'passed','candidate_requests':len(items),'unique_prompts':len(local),'duplicates':duplicates,'history_files':len(history),'inference_performed':False}
if __name__=='__main__':
 p=argparse.ArgumentParser(); p.add_argument('--candidate',type=Path,required=True); p.add_argument('--history',type=Path,action='append',required=True); a=p.parse_args(); result=check(a.candidate,a.history); print(json.dumps(result,ensure_ascii=False)); raise SystemExit(2 if result['duplicates'] else 0)
