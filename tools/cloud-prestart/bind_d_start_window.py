"""Preserve the active HPA and originals; bind a separately approved time window."""
import argparse
import copy
import datetime as dt
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import pwd
import shlex
import signal
import subprocess
import sys
import time

ROOT = Path.home()/'.local/share/seokpan-rosa-plan-inputs-5psj2l62'
RUN = ROOT/'controller-run-bundle-K2Tx7HgB'
BASE = RUN/'prestart-closeout'
HELPER_SHA = '3d68d47796d0362eebc684939923dd7d2b123f6d14c38844f50e3e82c94d2710'

def need(ok, reason):
    if not ok:
        raise RuntimeError(reason)

def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    obj = importlib.util.module_from_spec(spec)
    sys.modules[name] = obj
    spec.loader.exec_module(obj)
    return obj

def helpers():
    p = BASE/'finalize_hpa.py'
    need(hashlib.sha256(p.read_bytes()).hexdigest() == HELPER_SHA, 'MERGED_HELPER_REQUIRED')
    F = module('time_window_finalize', p)
    H = F.load('time_window_handoff', BASE/'readonly_handoff.py')
    return F, H

def alive(row):
    p = Path('/proc')/str(row['pid'])
    try:
        need(p.stat().st_uid == os.getuid(), 'OWNED_READER_REQUIRED')
        raw = (p/'cmdline').read_bytes()
    except FileNotFoundError:
        return False
    if not raw:
        return False
    need([x.decode() for x in raw.rstrip(b'\0').split(b'\0')] == row['argv'],
         'EXACT_READER_ARGUMENTS_REQUIRED')
    need(os.getpgid(row['pid']) == row['pid'], 'DETACHED_READER_REQUIRED')
    return True

def observe(pointer):
    F, _ = helpers()
    d = F.read(pointer)
    c = F.read(Path(d['config']))
    end = dt.datetime.fromisoformat(c['window']['not_after']).timestamp()
    argv = d['observer_argv']
    while time.time() < end-1:
        work = Path(d['work'])
        if (work/'STOP.json').exists() or (work/'restore.STARTED.json').exists():
            return 0
        seconds = min(2700, int(end-time.time()))
        if seconds < 1:
            break
        child = subprocess.run(argv+['--seconds',str(seconds),'--execute'])
        if child.returncode:
            return child.returncode
    return 0

def feed(pointer):
    F, H = helpers()
    d = F.read(pointer)
    c = F.read(Path(d['config']))
    work, shared = Path(d['work']), Path(d['shared'])
    end = dt.datetime.fromisoformat(c['window']['not_after']).timestamp()
    fingerprint = hashlib.sha256(F.canonical(c)).hexdigest()
    lock = os.open(shared/'.publisher.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    try:
        while time.time() < end:
            need(not (work/'STOP.json').exists() and not (work/'restore.STARTED.json').exists(),
                 'CONTROL_STOPPED')
            row = F.read(work/'latest.json')
            H.guard_row(row,fingerprint,c['binding'],time.time())
            H.publish_file(shared/'state/latest.json',F.canonical(row)+b'\n','cyj')
            time.sleep(1)
        raise RuntimeError('EXPLICIT_WINDOW_ENDED')
    except Exception as error:
        reason = str(error) if type(error) is RuntimeError else type(error).__name__
        H.publish_file(shared/'state/STOP.json',F.canonical({'status':'STOP_HANDOFF',
            'reason':reason,'utc':dt.datetime.now(dt.timezone.utc).isoformat()})+b'\n','cyj')
        return 2
    finally:
        os.close(lock)


def execute(accepted):
    need(accepted, 'EXPLICIT_TIME_COPY_ACCEPTANCE_REQUIRED')
    need(pwd.getpwuid(os.getuid()).pw_name == 'jth', 'USE_CONTROLLER_JTH')
    F, H = helpers()
    lock = os.open(RUN/'hpa-reader-resume.lock',os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    prior = F.read(RUN/'hpa-active-time-copy.json')
    oldout, oldwork = Path(prior['directory']), Path(prior['work'])
    oldshare = Path(prior['shared'])
    c0 = F.read(Path(prior['config']))
    need(c0['run_id'] == 'hpa-20261020-124957','EXPECTED_ACTIVE_RUN_REQUIRED')
    need(not (oldwork/'STOP.json').exists() and not (oldshare/'state/STOP.json').exists()
         and not (oldwork/'restore.STARTED.json').exists(), 'EXISTING_STOP_REQUIRES_RECONCILIATION')
    settings = F.read(BASE/'handoff-settings.json')
    source = Path(settings['source_dir'])
    sys.path.insert(0,str(source))
    C = F.load('common',source/'common.py')
    row = F.read(oldwork/'latest.json')
    H.guard_row(row,C.digest(c0),c0['binding'],time.time())
    need(row['hpa']['metadata']['uid'] == F.read(oldwork/'hpa-created.json')['uid'],
         'EXISTING_HPA_UID_REQUIRED')
    processes = subprocess.run(['ps','-u','cyj','-o','args='],
        capture_output=True,text=True,timeout=5)
    need(processes.returncode in (0,1),'D_PROCESS_STATUS_REQUIRED')
    need(not any(('load.mjs' in line or 'd_generator.py' in line)
                 and '--plan' in line for line in processes.stdout.splitlines()),
         'D_GENERATOR_RUNNING_DO_NOT_REBIND_TIME')
    out = oldout.parent/'d-start-window'
    need(not out.exists(), 'EXISTING_TIME_COPY_PRESERVED_NO_REPEAT')
    now = dt.datetime.now(dt.timezone.utc)
    end = dt.datetime(2026,10,20,15,30,tzinfo=dt.timezone.utc)
    need((end-now).total_seconds() >= 2700,'START_WINDOW_REQUIRES_FORTY_FIVE_MINUTES')
    need(end < dt.datetime(2026,10,20,17,0,tzinfo=dt.timezone.utc),
         'KEEP_CLEANUP_BEFORE_0300_KST')
    readers = {name:F.read(oldout/(name+'.process.json'))
               for name in ('observer','publisher')}
    for r in readers.values():
        alive(r)
    out.mkdir(mode=0o700)
    work, shared = out/'control',oldshare.parent/'d-start-window'
    work.mkdir(mode=0o700)
    c = copy.deepcopy(c0)
    c['window'] = {'not_before':now.isoformat(),'not_after':end.isoformat()}
    db = F.read(oldout/'db-observer.json')
    db['not_after'] = end.isoformat()
    F.save(out/'db-observer.json',db)
    c['db_probe_argv'] = [sys.executable,'-I',str(BASE/'pod_db_observer.py'),
                          'probe','--config',str(out/'db-observer.json'),'--execute']
    guard = c['generator_guard_argv'][:]
    guard[guard.index('--config')+1] = str(shared/'config.json')
    guard[guard.index('--work')+1] = str(shared/'state')
    c['generator_guard_argv'] = guard
    allowed = {'window','db_probe_argv','generator_guard_argv'}
    need({k:v for k,v in c.items() if k not in allowed} ==
         {k:v for k,v in c0.items() if k not in allowed}, 'NON_TIME_SETTINGS_MUST_NOT_CHANGE')
    H.public_contract(c)
    C.validate(c)
    need(C.hpa(c) == C.hpa(c0),'HPA_SPEC_MUST_NOT_CHANGE')
    before = F.read(oldwork/'before.json')
    need(before['config_sha256'] == C.digest(c0),'ORIGINAL_BASELINE_BINDING_REQUIRED')
    before['config_sha256'] = C.digest(c)
    created = F.read(oldwork/'hpa-created.json')
    created['config_sha256'] = C.digest(c)
    F.save(work/'before.json',before)
    F.save(work/'hpa-created.json',created)
    F.save(work/'hpa.json',F.read(oldwork/'hpa.json'))
    plan = F.declared_plan(c,C)
    F.save(work/'plan.json',plan)
    F.save(out/'config.json',c)
    H.directory(shared,'cyj')
    H.directory(shared/'state','cyj')
    H.publish_file(shared/'config.json',F.canonical(c)+b'\n','cyj',True)
    H.publish_file(shared/'plan.json',F.canonical(plan)+b'\n','cyj',True)
    d = {'directory':str(out),'config':str(out/'config.json'),'work':str(work),
         'shared':str(shared),'observer_argv':[sys.executable,'-I',str(source/'control.py'),
             'observe','--config',str(out/'config.json'),'--work',str(work)]}
    pointer = out/'dispatch.json'
    F.save(pointer,d)
    F.save(out/'time-binding-bridge.json',{'old_dispatch':prior,
        'old_config_sha256':C.digest(c0),'new_config_sha256':C.digest(c),
        'hpa_uid':created['uid'],'old_window':c0['window'],'new_window':c['window'],
        'original_files_preserved':True,'hpa_or_app_mutations':0,
        'first_attempt_guest_cleanup':'NOT_CONFIRMED'})
    for name in ('publisher','observer'):
        r = readers[name]
        if alive(r):
            os.killpg(r['pid'],signal.SIGTERM)
        deadline = time.monotonic()+5
        while alive(r) and time.monotonic()<deadline:
            time.sleep(.1)
        need(not alive(r),'OLD_READER_EXIT_PENDING_NO_DUPLICATE')
    F.spawn('observer',[sys.executable,'-I',str(Path(__file__).resolve()),
                        'observe','--dispatch',str(pointer)],out)
    deadline = time.monotonic()+40
    while time.monotonic()<deadline:
        if (work/'latest.json').exists():
            row = F.read(work/'latest.json')
            H.guard_row(row,C.digest(c),c['binding'],time.time())
            break
        time.sleep(1)
    else:
        raise RuntimeError('NEW_SAMPLE_PENDING_DO_NOT_START_D')
    H.publish_file(shared/'state/latest.json',F.canonical(row)+b'\n','cyj')
    F.spawn('publisher',[sys.executable,'-I',str(Path(__file__).resolve()),
                         'feed','--dispatch',str(pointer)],out)
    F.save(RUN/'hpa-active-time-copy.json',d)
    ret = Path(settings['handoff_root'])/'return/cyj/d-start-window'
    stopped = ret/c['run_id']/'receipt.json'
    control = [sys.executable,'-I',str(source/'control.py')]
    args = ['--config',d['config'],'--work',d['work']]
    result = {'status':'PASS_D_START_WINDOW_BOUND_SAME_HPA_GO',
        'run_id':c['run_id'],'hpa_uid':created['uid'],'original_files_preserved':True,
        'hpa_created_again':False,'cloud_resource_writes':0,'load_started':False,
        'config':d['config'],'work':d['work'],'shared_plan':str(shared/'plan.json'),
        'observation_not_after':end.isoformat(),'config_sha256':C.digest(c),
        'D_work':'/home/cyj/hpa-load/out/'+c['run_id']+'-attempt04',
        'D_return_root':str(ret),
        'stop_command':shlex.join(control+['stop']+args+['--execute']),
        'restore_command':shlex.join(control+['restore']+args+
            ['--load-receipt',str(stopped),'--execute']),
        'verify_restore_command':shlex.join(control+['verify-restore']+args+['--execute']),
        'results_command':shlex.join([sys.executable,'-I',str(source/'results.py')]+args+
            ['--load-out',str(ret/c['run_id']),'--out',str(out/'hpa-result.json')])}
    F.save(out/'receipt.json',result)
    H.publish_file(shared/'ready.json',F.canonical(result)+b'\n','cyj',True)
    H.publish_file(oldshare.parent/'active-time-copy.json',F.canonical({
        'shared_plan':result['shared_plan'],'config_sha256':result['config_sha256'],
        'ready_file':str(shared/'ready.json'),'D_work':result['D_work'],
        'D_return_root':result['D_return_root'],'observation_not_after':end.isoformat()})+b'\n','cyj')
    return result

def main():
    p = argparse.ArgumentParser()
    p.add_argument('action',choices=['execute','observe','feed'])
    p.add_argument('--accept-time-copy',action='store_true')
    p.add_argument('--dispatch',type=Path)
    a = p.parse_args()
    os.umask(0o077)
    if a.action == 'observe':
        return observe(a.dispatch)
    if a.action == 'feed':
        return feed(a.dispatch)
    try:
        print(json.dumps(execute(a.accept_time_copy),indent=2))
        return 0
    except Exception as error:
        print(json.dumps({'status':'STOP_HPA_TIME_COPY',
            'reason':str(error) if type(error) is RuntimeError else type(error).__name__,
            'automatic_retry':False,'next':'DO_NOT_START_D_OR_REPEAT_WRITES_RECONCILE_READER_MARKERS'}))
        return 2

if __name__ == '__main__':
    raise SystemExit(main())
