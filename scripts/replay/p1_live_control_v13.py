"""Development admission controller with explicit, exactly-once reclamation.

The HTTP rejection fallback requires an exclusively owned server and an idle
probe supplied by the runner. A global idle probe never replaces a CAP release
for a request whose native launch has been observed.
"""
import asyncio
import errno
import math
import time

from p1_live_control_v12 import Controller as PreviousController, estimate, reserve


class AdmissionRejected(ValueError):
    local_rejection = True


class ControlIntegrityError(RuntimeError):
    pass


class Controller(PreviousController):
    def __init__(self, policy, budget, history, server_log, aging=5000,
                 idle_probe=None, reclamation_timeout=3):
        super().__init__(policy, budget, history, server_log, aging)
        if not math.isfinite(budget) or reclamation_timeout <= 0:
            raise ValueError('finite budget and positive cleanup deadline required')
        self.idle_probe = idle_probe
        self.reclamation_timeout = reclamation_timeout
        self.seen_ids = set()
        self.returned = set()
        self.cleanups = {}

    def consume_native(self):
        try:
            self.capfile.seek(self.read_offset)
            chunk = self.capfile.read(65536)
        except OSError as exc:
            if exc.errno == errno.ENODATA:
                self.empty_read_retries += 1
                return
            raise
        self.read_offset += len(chunk)
        self.buffer += chunk
        lines = self.buffer.split(b'\n')
        self.buffer = lines.pop()
        for rawline in lines:
            line = rawline.decode('utf-8', errors='replace')
            if 'SCHED_CAP_RELEASE' not in line and 'SCHED_CAP_LAUNCH' not in line:
                continue
            fields = dict(x.split('=', 1) for x in line.split() if '=' in x)
            req = fields.get('clientid')
            if not req:
                continue
            release = 'SCHED_CAP_RELEASE' in line
            seen = self.released if release else self.native_started
            if req in seen or (release and req not in self.native_started):
                raise ControlIntegrityError('duplicate or unordered native event: ' + req)
            seen.add(req)
            self.emit('native_release_seen' if release else 'native_launch_seen',
                      req_id=req, native_event_ms=fields.get('event_ms'))

    def fail(self, exc):
        if not self.fatal:
            super().fail(exc)

    async def acquire(self, row):
        req = row['req_id']
        if req in self.seen_ids:
            self.fail(ControlIntegrityError('reused request ID: ' + req))
            raise ControlIntegrityError(self.fatal)
        self.seen_ids.add(req)
        for key in ('input_tokens', 'max_tokens'):
            if type(row[key]) is not int or row[key] < (0 if key == 'input_tokens' else 1):
                raise ValueError('invalid ' + key)
        for key in ('prediction', 'input_cost_coefficient', 'output_cost_coefficient'):
            if not isinstance(row[key], (int, float)) or not math.isfinite(row[key]) or row[key] < 0:
                raise ValueError('invalid ' + key)
        if reserve(row) > 2048:
            self.emit('local_rejection', req_id=req, reason='per_request_capacity',
                      reservation=reserve(row), request_limit=2048)
            raise AdmissionRejected('input + maximum output + 1 exceeds 2048')
        try:
            return await super().acquire(row)
        except BaseException:
            removed = self.pending.pop(req, None)
            if removed is not None:
                self.emit('queue_removed', req_id=req, reason='acquire_interrupted')
                self.signature = None
                self.schedule()
            raise

    async def complete(self, row, rec):
        req = row['req_id']
        if req not in self.cleanups:
            self.cleanups[req] = asyncio.create_task(self._complete_once(row, rec))
        await asyncio.shield(self.cleanups[req])

    async def _complete_once(self, row, rec):
        req = row['req_id']
        self.pending.pop(req, None)
        if req not in self.active:
            rec['reclaim_proof'] = 'no_reservation'
            return
        deadline = time.monotonic() + self.reclamation_timeout
        proof = None
        if rec['actual_send_offset_ms'] is None:
            proof = 'not_sent'
        while proof is None:
            if req in self.released:
                proof = 'native_release'
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self.emit('quarantine', req_id=req, reservation=self.active[req]['reservation'],
                          native_launch_seen=req in self.native_started,
                          transport_terminal=rec['terminal'])
                error = ControlIntegrityError('reclamation proof missing: ' + req)
                self.fail(error)
                raise error
            # Only an acknowledged rejection can use the exclusive idle barrier.
            # An unacknowledged POST might still be queued even when slots look idle.
            if (req not in self.native_started and self.idle_probe is not None
                    and rec['http_status'] in (400, 429, 503)):
                try:
                    idle = await asyncio.wait_for(self.idle_probe(), timeout=min(.2, remaining))
                except (OSError, asyncio.TimeoutError):
                    idle = False
                self.consume_native()
                if req in self.released:
                    proof = 'native_release'
                elif idle and req not in self.native_started:
                    proof = 'http_rejection_and_exclusive_idle'
                if proof:
                    break
            await asyncio.sleep(.005)
        assert req not in self.returned
        released = self.active.pop(req)
        self.returned.add(req)
        rec['reclaim_proof'] = proof
        rec['reclaim_confirm_offset_ms'] = self.now()
        if proof == 'native_release':
            rec['native_release_confirm_offset_ms'] = rec['reclaim_confirm_offset_ms']
        self.emit('return_reservation', req_id=req, reservation=released['reservation'], proof=proof)
        actual = rec.get('output_tokens')
        if (proof == 'native_release' and not self.fatal and rec['terminal'] == 'completed'
                and rec['output_tokens_known'] and type(actual) is int and 0 <= actual <= row['max_tokens']):
            key = (row['category'], row['task_kind'])
            error = max(0, actual - row['prediction'])
            self.history[key].append(error)
            self.version += 1
            self.emit('feedback', req_id=req, category=row['category'], task_kind=row['task_kind'],
                      actual=actual, prediction=row['prediction'], error=error,
                      history_count=len(self.history[key]), history_version=self.version,
                      http_end_ms=rec['end_offset_ms'])
        self.signature = None
        self.schedule()

    async def stop(self):
        await asyncio.gather(*self.cleanups.values(), return_exceptions=True)
        await super().stop()
        if self.fatal or self.active or self.pending:
            raise ControlIntegrityError('controller stopped with quarantined or invalid state')
