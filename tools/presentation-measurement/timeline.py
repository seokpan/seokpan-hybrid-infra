"""Append-only Controller reference clock. No cloud actions in this module."""
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import socket
import time


def need(ok, code):
    if not ok:
        raise RuntimeError(code)


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()


def sha(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def clock():
    # A different boot/host cannot continue the same monotonic time axis.
    boot = Path('/proc/sys/kernel/random/boot_id')
    need(boot.is_file(), 'LINUX_REFERENCE_BOOT_ID_REQUIRED')
    return {'host': socket.gethostname(), 'boot_id': boot.read_text().strip(),
            'monotonic_ns': time.monotonic_ns(),
            'utc': dt.datetime.now(dt.timezone.utc).isoformat()}


class Ledger:
    def __init__(self, path, binding, now=clock):
        self.path, self.binding, self.now = Path(path), binding, now
        self.rows = []
        if self.path.exists():
            need(not self.path.is_symlink(), 'REGULAR_LEDGER_REQUIRED')
            previous = None
            for line in self.path.read_text().splitlines():
                row = json.loads(line)
                digest = row.pop('sha256')
                need(row['previous'] == previous and sha(row) == digest and row['binding'] == binding,
                     'LEDGER_CHAIN_OR_BINDING_CHANGED')
                row['sha256'] = digest
                previous = digest
                self.rows.append(row)
        sample = now()
        if self.rows:
            first = self.rows[0]['clock']
            need((first['host'], first['boot_id']) == (sample['host'], sample['boot_id']),
                 'REFERENCE_HOST_REBOOT_OR_CHANGE_NO_CROSS_CLOCK_DURATION')
            need(sample['monotonic_ns'] >= self.rows[-1]['clock']['monotonic_ns'], 'CLOCK_REGRESSION')

    def mark(self, event, proof, observed=None):
        # Live reference host is Linux. Serialize entry/observer/failure appenders.
        with open(str(self.path)+'.lock', 'a') as lock:
            if os.name == 'posix':
                import fcntl
                fcntl.flock(lock, fcntl.LOCK_EX)
            refreshed = Ledger(self.path, self.binding, self.now)
            self.rows = refreshed.rows
            return self._mark_locked(event, proof, observed)

    def _mark_locked(self, event, proof, observed=None):
        need(event in {'s0', 's1', 's2', 's3', 'stage_target_bound', 'external_requested', 's4', 'failure', 'resume'}, 'KNOWN_EVENT_REQUIRED')
        if event in {'s0', 's1', 's2', 's3', 's4'} and any(x['event'] == event for x in self.rows):
            return next(x for x in self.rows if x['event'] == event)
        if event in {'s1', 's2', 's3', 's4'}:
            need(any(x['event'] == 's'+str(int(event[1:])-1) for x in self.rows), 'ORDERED_STAGES_REQUIRED')
        stamp = observed or self.now()
        current = self.now()
        need((stamp['host'], stamp['boot_id']) == (current['host'], current['boot_id'])
             and stamp['monotonic_ns'] <= current['monotonic_ns'], 'VALID_REFERENCE_CLOCK_REQUIRED')
        need(not self.rows or stamp['monotonic_ns'] >= self.rows[-1]['clock']['monotonic_ns'], 'EVENT_CLOCK_REGRESSION')
        row = {'event': event, 'clock': stamp, 'binding': self.binding, 'proof': proof,
               'previous': self.rows[-1]['sha256'] if self.rows else None}
        row['sha256'] = sha(row)
        fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        with os.fdopen(fd, 'ab') as stream:
            stream.write(canonical(row)+b'\n')
            stream.flush()
            os.fsync(stream.fileno())
        self.rows.append(row)
        return row

    def summary(self):
        steps = {x['event']: x for x in self.rows if x['event'] in {'s0', 's1', 's2', 's3', 's4'}}
        complete = len(steps) == 5
        sync_start_sampled = complete and steps['s2']['proof'].get('operation_phase_at_first_observation') == 'Running'
        delta = lambda a, b: (steps[b]['clock']['monotonic_ns']-steps[a]['clock']['monotonic_ns'])/1e9
        return {'status': 'MEASURED_STAGE_WITH_EXTERNAL_RECEIPT' if complete else 'INCOMPLETE_STAGE_TIMELINE',
                'binding': self.binding, 'steps': steps,
                'sync_start_observed_while_running': sync_start_sampled,
                'exact_server_sync_start_on_reference_clock': None,
                'phase_times_are_first_reference_observations_not_server_clock_subtraction': True,
                'seconds_first_execute_to_external_receipt': delta('s0', 's4') if complete else None,
                'segments_seconds': {a+'_'+b: delta(a, b) for a, b in zip(['s0', 's1', 's2', 's3'], ['s1', 's2', 's3', 's4']) if a in steps and b in steps},
                'external_transfer_wait_included': True,
                'scope': 'stage execution including failures/manual waits; not ROSA creation or service RTO'}
