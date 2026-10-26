"""Synthetic TCP transport/controller regression gate, not GPU evidence."""
import asyncio,json,sys,tempfile,hashlib,argparse
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]; sys.path.insert(0,str(ROOT/'scripts/replay'))
import p1_controlled_replay_v11 as client
from p1_live_control_v12 import Controller
async def trial(policy,root):
 log=root/'synthetic-server.log'; log.write_text('',encoding='utf-8'); active=peak=0
 def event(kind,req):
  with log.open('a',encoding='utf-8') as f: f.write(f'SCHED_CAP_{kind} clientid={req} event_ms=0\n')
 async def handle(reader,writer):
  nonlocal active,peak
  headers={}; await reader.readline()
  while True:
   line=await reader.readline()
   if line in (b'\r\n',b''): break
   k,v=line.decode().split(':',1); headers[k.lower()]=v.strip()
  body=json.loads(await reader.readexactly(int(headers['content-length']))); req=body['client_req_id']; event('LAUNCH',req); active+=1; peak=max(peak,active)
  await asyncio.sleep(.07 if req=='A' else .02)
  value={'stop':True,'content':'ok','tokens_evaluated':5 if req=='A' else 10,'tokens_predicted':2 if req=='A' else 8,'truncated':False,'stop_type':'eos','timings':{'prompt_n':5 if req=='A' else 10,'cache_n':0,'prompt_ms':1,'predicted_n':2 if req=='A' else 8,'predicted_ms':2}}
  writer.write(b'HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nConnection: close\r\n\r\n'+('data: '+json.dumps(value)+'\n\n').encode()); await writer.drain()
  await asyncio.sleep(.02); event('RELEASE',req); active-=1; writer.close(); await writer.wait_closed()
 server=await asyncio.start_server(handle,'127.0.0.1',0); port=server.sockets[0].getsockname()[1]
 history={(c,k):[0]*32 for c in ('interactive','background') for k in ('extract','check','organize')}; ctl=Controller(policy,100,history,log)
 rows=[{'req_id':name,'category':'interactive','task_kind':'extract','arrival_offset_ms':i*5,'prompt':'test','input_tokens':5 if name=='A' else 10,'max_tokens':64,'temperature':0,'ignore_eos':False,'cache_prompt':False,'prediction':1 if name=='A' else 7,'grammar_ceiling':None,'input_cost_coefficient':1,'output_cost_coefficient':10} for i,name in enumerate(['A','B'])]
 try: summary=await client.replay(rows,root/'replay',f'http://127.0.0.1:{port}',.3,2,1,50,2,controller=ctl)
 finally: server.close(); await server.wait_closed()
 records=[json.loads(x) for x in (root/'replay/client.jsonl').read_text().splitlines()]; events=[json.loads(x) for x in (root/'replay/control/events.jsonl').read_text().splitlines()]
 assert summary['terminal_counts']['completed']==2 and not ctl.active and not ctl.fatal and peak<=2
 for r in records:
  assert r['native_release_confirm_offset_ms']>=r['end_offset_ms'] and r['send_lag_ms']<=50
  feedback=next(e for e in events if e['event']=='feedback' and e['req_id']==r['req_id']); release=next(e for e in events if e['event']=='native_release_seen' and e['req_id']==r['req_id']); assert feedback['observed_ms']>=release['observed_ms']
 return {'policy':policy,'completed':2,'peak':peak,'decisions':len([e for e in events if e['event']=='decision']),'conditional_shadow_differences':sum(e['shadow']['M']['admit']!=e['shadow']['B3_1']['admit'] for e in events if e['event']=='decision')}
async def run(out):
 out.mkdir(parents=True,exist_ok=False); results=[]
 for policy in ('M','B3_1'):
  directory=out/policy; directory.mkdir(); results.append(await trial(policy,directory))
 assert results[0]['conditional_shadow_differences']>0
 names=['scripts/replay/p1_controlled_replay_v11.py','scripts/replay/p1_live_control_v12.py','tests/integration/verify_p1_controlled_client_v12.py']
 result={'status':'passed','scope':'synthetic TCP tests only, not native model measurements','trials':results,'source_sha256':{p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in names}}
 (out/'summary.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8'); print(json.dumps(result,ensure_ascii=False))
if __name__=='__main__':
 p=argparse.ArgumentParser(); p.add_argument('--out-dir',type=Path,required=True); a=p.parse_args(); asyncio.run(run(a.out_dir))
