"""Restart only the detached HPA readers; preserve the active lease and plan."""
import datetime as dt
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

ROOT = Path.home()/'.local/share/seokpan-rosa-plan-inputs-5psj2l62'
RUN = ROOT/'controller-run-bundle-K2Tx7HgB'
BASE = RUN/'prestart-closeout'
os.umask(0o077)

def need(ok, code):
    if not ok:
        raise RuntimeError(code)

def read(path):
    need(path.is_file() and not path.is_symlink(), 'REGULAR_EXISTING_FILE_REQUIRED')
    return json.loads(path.read_text())

def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod

def process(row):
    proc = Path('/proc')/str(row['pid'])
    if not proc.exists():
        return False
    need(proc.stat().st_uid == os.getuid(), 'OWNED_PROCESS_REQUIRED')
    values = (proc/'cmdline').read_bytes().rstrip(b'\0').split(b'\0')
    if values == [b'']:
        return False
    need([v.decode() for v in values] == row['argv'], 'EXACT_PROCESS_ARGUMENTS_REQUIRED')
    need(os.getpgid(row['pid']) == row['pid'], 'DETACHED_PROCESS_GROUP_REQUIRED')
    return True

def main():
    import pwd
    need(pwd.getpwuid(os.getuid()).pw_name == 'jth', 'USE_CONTROLLER_JTH')
    lock = os.open(RUN/'hpa-reader-resume.lock', os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW, 0o600)
    fcntl.flock(lock, fcntl.LOCK_EX|fcntl.LOCK_NB)
    helper = BASE/'finalize_hpa.py'
    need(hashlib.sha256(helper.read_bytes()).hexdigest() ==
         '3d68d47796d0362eebc684939923dd7d2b123f6d14c38844f50e3e82c94d2710',
         'EXISTING_MERGED_HELPER_REQUIRED')
    F = module('existing_hpa_finalize', helper)
    pointer = RUN/'hpa-final-dispatch.json'
    d = F.read(pointer)
    out, work, shared = Path(d['directory']), Path(d['work']), Path(d['shared'])
    c = F.read(Path(d['config']))
    need(c['run_id'] == 'hpa-20261020-124957', 'EXPECTED_ACTIVE_RUN_REQUIRED')
    for folder in (work, shared/'state'):
        need(not (folder/'STOP.json').exists(), 'EXISTING_STOP_REQUIRES_RECONCILIATION')
    need(not (work/'restore.STARTED.json').exists(), 'RESTORATION_ALREADY_STARTED')
    marker = out/'reader-resume-attempt02.json'
    ready = shared/'attempt02-ready.json'
    need(not marker.exists() and not ready.exists(), 'EXISTING_RESUME_PRESERVED_NO_REPEAT')
    end = dt.datetime.fromisoformat(c['window']['not_after']).timestamp()
    need(end-time.time() >= 2700, 'FORTY_FIVE_MINUTES_IN_EXISTING_WINDOW_REQUIRED')
    H = F.load('existing_readonly_handoff', BASE/'readonly_handoff.py')
    fingerprint = hashlib.sha256(F.canonical(c)).hexdigest()
    H.guard_row(F.read(work/'latest.json'), fingerprint, c['binding'], time.time())
    rows = {name:F.read(out/(name+'.process.json')) for name in ('observer','publisher')}
    need(rows['observer']['argv'][3] == 'observe' and
         rows['publisher']['argv'][3] == 'feed', 'EXACT_READER_ROLES_REQUIRED')
    need(rows['observer']['argv'][2].endswith('/control.py') and
         rows['publisher']['argv'][2] == str(helper), 'EXISTING_READER_SOURCES_REQUIRED')
    for row in rows.values():
        process(row)
    F.save(marker, {'utc':dt.datetime.now(dt.timezone.utc).isoformat(),
                   'reason':'D_SSH_INTERRUPTED_BEFORE_LOAD_PHASE',
                   'config_sha256':fingerprint, 'old_processes':rows,
                   'hpa_or_plan_changed':False})
    # 게시기를 먼저 멈춰 교체 중 오래된 상태를 다시 게시하지 않습니다.
    for name in ('publisher','observer'):
        row = rows[name]
        if process(row):
            os.killpg(row['pid'], signal.SIGTERM)
        limit = time.monotonic()+5
        while process(row) and time.monotonic()<limit:
            time.sleep(.1)
        need(not process(row), 'OLD_READER_EXIT_PENDING_NO_DUPLICATE')
    start = time.time()
    argv = rows['observer']['argv'][:]
    argv[argv.index('--seconds')+1] = '2700'
    F.spawn('observer-attempt02', argv, out)
    limit = time.monotonic()+40
    while time.monotonic()<limit:
        row = F.read(work/'latest.json')
        when = dt.datetime.fromisoformat(row['utc']).timestamp()
        if when >= start:
            H.guard_row(row, fingerprint, c['binding'], time.time())
            break
        time.sleep(1)
    else:
        raise RuntimeError('NEW_OBSERVER_FIRST_SAMPLE_PENDING_DO_NOT_START_LOAD')
    H.publish_file(shared/'state/latest.json', F.canonical(row)+b'\n', 'cyj')
    F.spawn('publisher-attempt02', rows['publisher']['argv'], out)
    live_end = min(end, start+2700)
    value = {'status':'PASS_SAME_HPA_ATTEMPT02_READY', 'run_id':c['run_id'],
             'config_sha256':fingerprint, 'binding':c['binding'],
             'observation_not_after':dt.datetime.fromtimestamp(live_end,dt.timezone.utc).isoformat(),
             'original_plan_and_config_reused':True, 'hpa_created_again':False,
             'load_started':False, 'old_generator_cleanup':'NOT_CONFIRMED'}
    H.publish_file(ready, F.canonical(value)+b'\n', 'cyj', True)
    F.save(out/'reader-resume-attempt02-result.json', value)
    print(json.dumps(value, indent=2))

if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(json.dumps({'status':'STOP_HPA_READER_RESUME',
                          'reason':str(error) if type(error) is RuntimeError else type(error).__name__,
                          'automatic_retry':False, 'load_started':False}))
        raise SystemExit(2)

