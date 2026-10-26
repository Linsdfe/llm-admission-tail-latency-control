"""Unified pre-generation-inference gate; pure validation over actual tokenized plans."""
from collections import Counter
import hashlib
def digest(prompt): return hashlib.sha256(prompt.encode('utf-8')).hexdigest()
def validate(corpus,historical_hashes,prior_calibration,minimum=32):
 errors=[]; seen=set()
 for r in corpus:
  h=digest(r['prompt'])
  if h in seen: errors.append('within_batch_duplicate')
  if h in historical_hashes: errors.append('historical_duplicate')
  seen.add(h)
  if r['split'] not in ('calibration','development'): errors.append('forbidden_split')
  low,high=r['input_token_range']
  if not low<=r['input_tokens']<=high: errors.append('input_bucket_infeasible')
  if r['input_tokens']+r['max_tokens']+1>2048: errors.append('context_budget')
  if r['content_sha256']!=h: errors.append('prompt_hash_mismatch')
 counts=Counter((r['category'],r['task_kind']) for r in prior_calibration+corpus if r['split']=='calibration')
 expected={(c,k) for c in ('interactive','background') for k in ('extract','check','organize')}
 if set(counts)!=expected or any(counts[k]<minimum for k in expected): errors.append('warm_history_insufficient')
 if errors: raise ValueError(','.join(sorted(set(errors))))
 return {'status':'passed','new_corpus':len(corpus),'unique_prompts':len(seen),'history_buckets':{':'.join(k):v for k,v in sorted(counts.items())},'minimum':minimum,'actual_token_counts_checked':True,'inference_allowed':True}
