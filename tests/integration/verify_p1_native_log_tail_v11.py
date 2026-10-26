"""Growing-file tail stress on actual filesystem, no model inference."""
import asyncio,argparse,json,time,sys,hashlib
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]; sys.path.insert(0,str(ROOT/'scripts/replay'))
from p1_live_control_v11 import Controller
async def run(out):
 out.mkdir(parents=True,exist_ok=False); log=out/'growing.log'; log.write_bytes(b''); history={(c,k):[0]*32 for c in ('interactive','background') for k in ('extract','check','organize')}; ctl=Controller('M',100,history,log); ctl.origin=time.monotonic(); ctl.capfile=log.open('rb',buffering=0); ctl.buffer=b''; events=[]; ctl.emit=lambda kind,**kw:events.append((kind,kw))
 async def append():
  with log.open('ab',buffering=0) as writer:
   for i in range(1000):
    writer.write(('资料日志 '+str(i)+' x'*int(i%37)+'\n').encode())
    writer.write(f'SCHED_CAP_LAUNCH clientid=q{i} event_ms={i}\n'.encode()); await asyncio.sleep(.001)
    writer.write(f'SCHED_CAP_RELEASE clientid=q{i} event_ms={i}\n'.encode())
 writer=asyncio.create_task(append())
 while not writer.done(): ctl.consume_native(); await asyncio.sleep(.001)
 await writer
 for _ in range(100):
  ctl.consume_native()
  if len(ctl.released)==1000: break
  await asyncio.sleep(.005)
 ctl.capfile.close(); assert ctl.native_started==ctl.released=={f'q{i}' for i in range(1000)} and len(events)==2000
 result={'status':'passed','scope':'actual shared-filesystem growing-file I/O, no model','captured_launches':1000,'captured_releases':1000,'read_bytes':ctl.read_offset,'file_bytes':log.stat().st_size,'temporary_empty_read_errors':ctl.empty_read_retries,'source_sha256':{'scripts/replay/p1_live_control_v11.py':hashlib.sha256((ROOT/'scripts/replay/p1_live_control_v11.py').read_bytes()).hexdigest()}}
 (out/'summary.json').write_text(json.dumps(result,indent=2),encoding='utf-8'); print(json.dumps(result))
if __name__=='__main__':
 p=argparse.ArgumentParser(); p.add_argument('--out-dir',type=Path,required=True); a=p.parse_args(); asyncio.run(run(a.out_dir))
