# Simple priority and aging prototype, v1

This is a local research patch for the fixed `third_party/llama.cpp` source. The
upstream checkout is left unchanged. Apply the adjacent `.patch` to an independent
checkout of the same revision, then build the usual `llama-server` target.

Use the same patched binary for both conditions:

```bash
LLAMA_SERVER_SIMPLE_SCHEDULER=0 LLAMA_SERVER_BACKGROUND_AGING_MS=5000 ./llama-server ...
LLAMA_SERVER_SIMPLE_SCHEDULER=1 LLAMA_SERVER_BACKGROUND_AGING_MS=5000 ./llama-server ...
```

- Mode 0 retains the original FIFO main queue and deferred slot affinity logic.
- Mode 1 chooses control work first, then background requests waiting at least
  5000 ms, then interactive requests, then younger background requests.
- Within an inference tier, earlier initial enqueue time wins; task ID breaks ties.
- An explicit pinned slot is an eligibility constraint. Slot affinity does not
  outrank the request class in mode 1.
- Age is assigned once when the request is first posted. Deferral and worker
  retries retain it. Cancellation uses the existing cleanup and slot release paths.
- Running work is never preempted. No slot is reserved, and no decode, KV budget,
  prompt limit, sampler, or batch construction is changed. The 5000 ms threshold
  promotes priority; it is not a maximum wait or completion guarantee. Under
  overload, many aged background requests can delay interactive requests.

Completion request fields:

```json
{"request_class":"interactive", "client_req_id":"I001"}
```

`request_class` must be `interactive` or `background` and defaults to `interactive`.
`client_req_id` has at most 96 ASCII letters, digits, or `_.:-`; it may be omitted.
The OAI chat conversion already preserves these fields. The class is explicit and
is never inferred from the token budget or prompt text.

The measured prototype uses text-only requests, one completion per request, and
omits `id_slot`. The model-free tests cover valid slot IDs 0 and 1. Out-of-range
slot IDs, parallel sampling, and slot-save/restore operations are outside this
validation scope; in particular, this patch does not reproduce the engine's
modulo handling of out-of-range pinned IDs in its deferred eligibility check.

Both modes log `SCHED_CONFIG`, `SCHED_ENQUEUE`, `SCHED_SELECT`, and `SCHED_LAUNCH`.
`SCHED_SELECT` can recur when work is declined during an active decode; only
`SCHED_LAUNCH` confirms admission to an execution slot. Queue timestamps are engine
monotonic milliseconds and must not be subtracted from client monotonic time.
Queue counts in a selection event are measured before removal of the selected task.

The model-free checks are in `tests/unit/test_simple_scheduler.cpp`. Build them as
a small target linked against the patched `server-context` library. They exercise
FIFO mode, priority, aging, control work, slot affinity, pinned-slot eligibility,
cancellation, and preservation of age across deferral without sleeping.

`build_patch.py` regenerates the four full source copies and adjacent unified diff
from the untouched source checkout. No commit, push, or upstream submission is part
of this private research prototype.
