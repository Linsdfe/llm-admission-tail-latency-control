# Background execution cap prototype, v1

This private research patch is based on fixed upstream revision
`620d2225cc436ab0b001e3200e590e3b286ef9ab` plus the complete
`simple-priority-aging-v1` patch. It does not modify that earlier patch, the
original engine checkout, or historical experiment records.

Use the same new binary for all three conditions:

| Condition | SIMPLE_SCHEDULER | MAX_RUNNING_BACKGROUND |
| --- | --- | --- |
| Original engine queue behavior | 0 | 0 |
| Interactive priority and background aging | 1 | 0 |
| Same priority plus at most one running background | 1 | 1 |

The full environment names are `LLAMA_SERVER_SIMPLE_SCHEDULER`,
`LLAMA_SERVER_MAX_RUNNING_BACKGROUND`, and
`LLAMA_SERVER_BACKGROUND_AGING_MS`. The aging setting remains 5000 ms in the
performance protocol. The only accepted cap values are 0 (unlimited) and 1.
Cap 1 requires scheduler mode 1.

All three conditions also share one backend correctness fix discovered before
the performance experiment: when dispatching a CANCEL, clear its target from
pending/deferred/unhandled queues again under the queue lock after removing the
CANCEL itself. This covers a target temporarily held by the yielding worker
when the original HTTP-post cleanup ran. It does not change queue priority.
The original frozen engine and earlier simple patch are not changed. Therefore
the new mode-0 condition means original queue selection with this shared cleanup
fix and lifecycle instrumentation, rather than an untouched upstream binary.

A background slot is counted after successful task setup and slot state change
to STARTED. It remains counted until the original slot release path has set the
slot idle, flushed its statistics, and reset its request state. The existing
release callback then removes its slot entry before asking the deferred queue
for a new task. Queued cancellation never changes this count. Repeated release
notification for an already cleared slot does nothing. Each active slot stores
its task ID, client request ID and class for matching lifecycle logs.

When the cap is full, background requests are moved from the main queue to the
existing deferred queue once. Deferred selection skips them until a background
slot is released. Their first enqueue timestamp is retained. Aged background
priority cannot bypass the cap. Interactive and control tasks remain eligible.
No running request is preempted; the second execution slot may stay idle when
only background requests are waiting. Cap 0 retains the prior selection rules.
There is no predictor, adaptive threshold, KV ledger or decode change.

This validation covers text generation with one completion per request and no
explicit pinned slot. Capped background requests with parallel completions are
rejected. Other operations retain the inherited prototype limitations described
in `../simple-priority-aging-v1/README.md`.

If a task is rejected before launch on a known idle slot, the capped mode asks
the deferred queue for the next eligible task on that slot. No execution credit
is changed for the rejected task. This also lets a valid queued request proceed
after an unsupported background `n>1` request is rejected. If cancellation removes
a task after release has promoted it but before launch, capped mode also retries
eligible deferred work on actual idle slots. This retry only runs when the cancel
did not release an active slot; active cancellation keeps the original release
wakeup. Neither retry creates a release event or changes running credit.

The existing `SCHED_ENQUEUE`, `SCHED_SELECT`, and `SCHED_LAUNCH` formats remain.
`SCHED_CONFIG` adds `max_running_background=0|1`. New lifecycle events are emitted
in all three conditions, including interactive tasks:

```text
SCHED_CAP_LAUNCH id=1 clientid=B001 class=background slot=0 running_background_before=0 running_background_after=1 cap=1 event_ms=1234
SCHED_CAP_RELEASE id=1 clientid=B001 class=background slot=0 running_background_before=1 running_background_after=0 cap=1 event_ms=2345
```

`event_ms` uses the engine monotonic clock. A CAP_LAUNCH event is immediately
before the inherited SCHED_LAUNCH event. Release events represent server slot
cleanup, not client response receipt. `SCHED_CAP_DEFER` records each move blocked
by the cap. Counts must reconstruct from zero, never exceed one in the capped
condition, and return to zero after all cleanup.

`build_patch.py` generates four full source copies and the adjacent full patch
from the frozen simple prototype. `build.sh NEW_EVIDENCE_DIR` creates the separate
WSL checkout and builds the CUDA server. `run_unit_tests.sh SOURCE NEW_EVIDENCE_DIR`
runs the original nine queue checks plus the new cap lifecycle checks without
loading a model. Real-model correctness gates and performance runs are separate.
