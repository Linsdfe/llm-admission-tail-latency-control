"""Shared client-side admission prototype with real native-release feedback."""
import asyncio,json,math,time,errno
from collections import defaultdict
from pathlib import Path

def quantile(values,p=.8):
 x=sorted(values); h=(len(x)-1)*p; lo=int(h)
 return x[lo]+(x[min(lo+1,len(x)-1)]-x[lo])*(h-lo)
def reserve(row): return row['input_tokens']+row['max_tokens']+1
def estimate(row,margin):
 length=min(row['max_tokens'],row['prediction']+margin,row.get('grammar_ceiling') or row['max_tokens'])
 return length,row['input_tokens']*row['input_cost_coefficient']+length*row['output_cost_coefficient']
class Controller:
 def __init__(self,policy,budget,history,server_log,aging=5000):
  assert policy in ('B3_0','B3_1','M') and budget>0
  self.policy,self.budget,self.aging=policy,budget,aging
  self.history=defaultdict(list,{k:list(v) for k,v in history.items()}); assert len(self.history)==6 and all(len(v)>=32 for v in self.history.values())
  self.server_log=Path(server_log); self.pending={}; self.active={}; self.released=set(); self.native_started=set(); self.version=0; self.read_offset=0; self.empty_read_retries=0; self.signature=None; self.fatal=None; self.closed=False; self.logqueue=asyncio.Queue()
 def now(self): return (time.monotonic()-self.origin)*1000
 def m_margin(self,row):
  h=self.history[(row['category'],row['task_kind'])]
  return 1 if len(h)<32 else max(0,math.ceil(quantile(h[-32:])))
 async def start(self,origin,out):
  self.origin=origin; self.path=Path(out)/'control'; self.path.mkdir(); self.capfile=self.server_log.open('rb',buffering=0); self.buffer=b''
  self.output=(self.path/'events.jsonl').open('x',encoding='utf-8'); self.writer=asyncio.create_task(self.persist()); self.ticker=asyncio.create_task(self.tick())
  self.emit('initial',history={':'.join(k):v for k,v in self.history.items()},policy=self.policy,budget=self.budget)
 def emit(self,event,**values): self.logqueue.put_nowait({'event':event,'observed_ms':self.now(),**values})
 async def persist(self):
  def write(value): self.output.write(json.dumps(value,ensure_ascii=False)+'\n'); self.output.flush()
  while True:
   value=await self.logqueue.get()
   if value is None: break
   await asyncio.to_thread(write,value)
  await asyncio.to_thread(self.output.close)
 def consume_native(self):
  try:
   self.capfile.seek(self.read_offset)
   chunk=self.capfile.read(65536)
  except OSError as exc:
   if exc.errno==errno.ENODATA:
    self.empty_read_retries+=1
    return
   raise
  self.read_offset+=len(chunk)
  self.buffer+=chunk; lines=self.buffer.split(b'\n'); self.buffer=lines.pop()
  for rawline in lines:
   line=rawline.decode('utf-8',errors='replace')
   if 'SCHED_CAP_RELEASE' not in line and 'SCHED_CAP_LAUNCH' not in line: continue
   fields=dict(x.split('=',1) for x in line.split() if '=' in x); req=fields.get('clientid')
   if not req: continue
   if 'SCHED_CAP_RELEASE' in line: self.released.add(req); event='native_release_seen'
   else: self.native_started.add(req); event='native_launch_seen'
   self.emit(event,req_id=req,native_event_ms=fields.get('event_ms'))
 async def tick(self):
  try:
   while not self.closed:
    self.consume_native(); self.schedule(); await asyncio.sleep(.005)
  except Exception as exc: self.fail(exc)
 def fail(self,exc):
  self.fatal=repr(exc); self.emit('fatal',error=self.fatal)
  for row,future in list(self.pending.values()):
   if not future.done(): future.set_exception(RuntimeError(self.fatal))
 def schedule(self):
  if self.fatal: return
  while self.pending and len(self.active)<2:
   now=self.now(); pool=[r for r,f in self.pending.values() if not f.done()]
   if not pool: return
   def priority(r):
    aged=r['category']=='background' and now-r['arrival_offset_ms']>=self.aging
    return (0 if aged else 1 if r['category']=='interactive' else 2,r['arrival_offset_ms'],r['req_id'])
   row=min(pool,key=priority); req=row['req_id']; aged=row['category']=='background' and now-row['arrival_offset_ms']>=self.aging
   signature=(req,tuple(sorted(self.active)),self.version,aged)
   if signature==self.signature: return
   self.signature=signature; started=time.monotonic_ns(); reservation=reserve(row)
   hard=reservation<=2048 and sum(v['reservation'] for v in self.active.values())+reservation<=4096
   m=self.m_margin(row); used=sum(v['cost'] for v in self.active.values()); shadow={}
   for policy,value in [('B3_0',0),('B3_1',1),('M',m)]:
    length,cost=estimate(row,value); reason='hard_wait' if not hard else 'empty' if not self.active else 'aged' if aged else 'normal' if used+cost<=self.budget else 'soft_wait'
    shadow[policy]={'margin':value,'length':length,'cost':cost,'admit':reason not in ('hard_wait','soft_wait'),'reason':reason}
   selected=shadow[self.policy]; bucket=(row['category'],row['task_kind'])
   self.emit('decision',decision_clock_ms=now,req_id=req,category=row['category'],task_kind=row['task_kind'],history_count=len(self.history[bucket]),history_version=self.version,active={k:dict(v) for k,v in self.active.items()},reservation=reservation,used_cost=used,shadow=shadow,selected_policy=self.policy,decision_us=(time.monotonic_ns()-started)/1000)
   if not selected['admit']: return
   self.active[req]={'reservation':reservation,'cost':selected['cost'],'release_ms':now}
   _,future=self.pending.pop(req); self.emit('permit',req_id=req,category=row['category'],task_kind=row['task_kind'],reason=selected['reason'],reservation=reservation,cost=selected['cost'])
   assert len(self.active)<=2 and sum(v['reservation'] for v in self.active.values())<=4096
   future.set_result(now)
 async def acquire(self,row):
  if self.fatal: raise RuntimeError(self.fatal)
  lag=self.now()-row['arrival_offset_ms']; self.emit('enqueue',req_id=row['req_id'],arrival_ms=row['arrival_offset_ms'],arrival_lag_ms=lag)
  if lag>50: self.fail(RuntimeError('arrival lag exceeds50ms')); raise RuntimeError(self.fatal)
  future=asyncio.get_running_loop().create_future(); self.pending[row['req_id']]=(row,future); self.signature=None; self.schedule()
  return await future
 async def complete(self,row,rec):
  req=row['req_id']; self.pending.pop(req,None)
  if req not in self.active: return
  deadline=time.monotonic()+3
  while req not in self.released:
   if time.monotonic()>deadline: self.fail(RuntimeError('native release not confirmed')); raise RuntimeError(self.fatal)
   await asyncio.sleep(.005)
  released=self.active.pop(req); rec['native_release_confirm_offset_ms']=self.now()
  self.emit('return_reservation',req_id=req,reservation=released['reservation'])
  if rec['terminal']=='completed' and rec['output_tokens_known']:
   key=(row['category'],row['task_kind']); error=max(0,rec['output_tokens']-row['prediction']); self.history[key].append(error); self.version+=1
   self.emit('feedback',req_id=req,category=row['category'],task_kind=row['task_kind'],actual=rec['output_tokens'],prediction=row['prediction'],error=error,history_count=len(self.history[key]),history_version=self.version,http_end_ms=rec['end_offset_ms'])
  self.signature=None; self.schedule()
 async def stop(self):
  self.closed=True; await self.ticker; self.capfile.close()
  self.emit('final',native_empty_read_retries=self.empty_read_retries,read_offset=self.read_offset,active=self.active,pending=list(self.pending),fatal=self.fatal,history_counts={':'.join(k):len(v) for k,v in self.history.items()})
  self.logqueue.put_nowait(None); await self.writer
