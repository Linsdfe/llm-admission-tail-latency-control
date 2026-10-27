"""Profile-only gate: no online controller or unrelated warm history is implied."""
from collections import Counter
import hashlib


def validate(rows, historical_hashes, purpose):
    if purpose != 'serial_task_profile':
        raise ValueError('this gate does not authorize online calibration or scheduling')
    if len(rows) != 48:
        raise ValueError('exactly48 registered tasks required')
    seen = set()
    for r in rows:
        h = hashlib.sha256(r['prompt'].encode('utf-8')).hexdigest()
        if h in seen or h in historical_hashes or h != r['content_sha256']:
            raise ValueError('duplicate or mismatched prompt')
        seen.add(h)
        if r['split'] != 'development' or r['seed'] not in (34101,34102) or r['task_kind'] != 'select_current':
            raise ValueError('unregistered task split/seed')
        lo, hi = r['input_token_range']
        if not lo <= r['input_tokens'] <= hi or r['input_tokens'] + r['max_tokens'] + 1 > 2048:
            raise ValueError('input range or context budget')
        if not r.get('grammar') or any(k in r for k in ('expected_answer','expected_ids','facts_evaluation_only')):
            raise ValueError('missing public grammar or leaked oracle')
    cells = Counter((r['seed'], r['category']) for r in rows)
    if cells != Counter({(s,c):12 for s in (34101,34102) for c in ('interactive','background')}):
        raise ValueError('unbalanced registered corpus')
    return {'status':'passed','unique_prompts':48,'purpose':purpose,'online_updates_enabled':False,
            'warm_history_minimum_changed':False,'final_test_accessed':False}
