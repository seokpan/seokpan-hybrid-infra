"""Resume the specific pre-Pod SA validation failure without replaying supplied inputs."""
import argparse
import copy
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

OLD_HEAD = '6c3a99e016997d80f5c005a4cf2ab68075742126'
IMPLEMENTATION = ('tools/cloud-prestart/runtime_probe.py', 'tools/cloud-launch/cloud_launch.py',
                  'tools/cloud-readiness/post_import_new_cluster.py')
ALLOWED = {'binding.json', 'runtime-inputs.json', 'runtime-inputs.started.json', 'new-inputs.started.json'} | {
    'supply-'+name+'.attempted.json' for name in
    ('backend-db-runtime', 'backend-redis-runtime', 'backend-database-ca', 'backend-redis-ca')}


def need(ok, reason):
    if not ok:
        raise RuntimeError(reason)


def module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def migrate_records(records, old, new):
    need(set(records) <= ALLOWED and {'binding.json', 'runtime-inputs.json', 'new-inputs.started.json'} <= set(records),
         'EXACT_PRE_POD_FAILURE_RECORDS_REQUIRED')
    need(old != new and records['binding.json'] == old, 'EXACT_OLD_BINDING_REQUIRED')
    need(records['runtime-inputs.json'].get('result', {}).get('status') == 'PASS_CURRENT_RUNTIME_INPUTS_CONNECTED',
         'ACCEPTED_RUNTIME_INPUT_RECEIPT_REQUIRED')
    result = {}
    for name, record in records.items():
        if name == 'binding.json':
            result[name] = copy.deepcopy(new)
        else:
            need(record.get('binding') == old, 'PRE_POD_RECEIPT_BINDING_MISMATCH')
            value = copy.deepcopy(record)
            value['binding'] = copy.deepcopy(new)
            result[name] = value
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', required=True, type=Path)
    parser.add_argument('--dispatch', required=True, type=Path)
    parser.add_argument('--new-head', required=True)
    parser.add_argument('--execute', action='store_true')
    a = parser.parse_args()
    os.umask(0o077)
    out = None
    phase = 'LOCAL_REVIEW'
    result = {'status': 'STOP_NATIVE_PULL_RESUME', 'automatic_retry': False,
              'resource_deletions': 0, 'sql_calls': 0, 'sync_started': False}
    try:
        need(a.execute and re.fullmatch(r'[0-9a-f]{40}', a.new_head), 'EXPLICIT_FIXED_SOURCE_EXECUTION_REQUIRED')
        import pwd
        import fcntl
        need(pwd.getpwuid(os.getuid()).pw_name == 'jth', 'USE_CONTROLLER_JTH')
        need(a.repo.is_dir() and not a.repo.is_symlink() and (a.repo/'.git').is_dir(), 'EXISTING_APP_REPOSITORY_REQUIRED')
        old = module(a.repo/'tools/cloud-prestart/prestart.py', 'old_prestart')
        dispatch = old.read(a.dispatch)
        need(Path(dispatch['source']).resolve() == (a.repo/'tools/cloud-prestart/prestart.py').resolve(),
             'SAME_DISPATCH_SOURCE_REQUIRED')
        config = old.read(dispatch['config'])
        work = Path(dispatch['work'])
        need(work.is_dir() and not work.is_symlink(), 'EXISTING_PRIVATE_RUN_REQUIRED')
        out = Path(tempfile.mkdtemp(prefix='native-pull-resume-', dir=work.parent))

        def git(*args):
            p = subprocess.run(['git', '-C', str(a.repo), *args], capture_output=True, timeout=120)
            if p.returncode:
                (out/('git-'+str(len(list(out.glob('git-*'))))+'.private.log')).write_bytes(p.stdout+p.stderr)
                raise RuntimeError('GIT_OPERATION_FAILED_PRIVATE_LOG')
            return p.stdout.decode().strip()

        need(git('remote', 'get-url', 'origin') == 'https://github.com/seokpan/seokpan-hybrid-infra.git', 'EXPECTED_REPOSITORY_REQUIRED')
        need(git('rev-parse', 'HEAD') == OLD_HEAD and not git('status', '--porcelain', '--untracked-files=all'),
             'CLEAN_ACCEPTED_OLD_HEAD_REQUIRED')
        with open(str(work)+'.lock', 'a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            phase = 'VERIFY_EXISTING_SUCCESS_AND_OLD_BINDING'
            previous = old.Flow(config, work, prepare_data_pod=True)
            need(previous.cluster == dispatch['cluster_id'], 'SAME_CLUSTER_BINDING_REQUIRED')
            names = {p.name for p in work.iterdir()}
            need(names <= ALLOWED, 'LATER_OR_UNKNOWN_RUN_ARTIFACT_NO_MIGRATION')
            records = {name: old.read(work/name) for name in names}
            original_helpers = {p: (a.repo/p).read_bytes() for p in IMPLEMENTATION}
            old.write(out/'original-records.json', records)
            (out/'original-prestart.py').write_bytes((a.repo/'tools/cloud-prestart/prestart.py').read_bytes())
            print(json.dumps({'phase': phase, 'runtime_inputs_reused': True}), flush=True)

            phase = 'ADOPT_FIXED_SOURCE'
            git('fetch', '--quiet', '--no-tags', 'origin', a.new_head)
            git('checkout', '--quiet', '--detach', a.new_head)
            need(git('rev-parse', 'HEAD') == a.new_head, 'FIXED_HEAD_REQUIRED')
            need(all((a.repo/p).read_bytes() == raw for p, raw in original_helpers.items()),
                 'UNRELATED_RUNTIME_IMPLEMENTATION_CHANGED')
            new = module(a.repo/'tools/cloud-prestart/prestart.py', 'fixed_prestart')
            probe = new.Flow(config, out/'binding-only', prepare_data_pod=True)
            updated = migrate_records(records, previous.binding, probe.binding)
            new.write(out/'source-binding-review.json', {'old_head': OLD_HEAD, 'new_head': a.new_head,
                'old_binding': previous.binding, 'new_binding': probe.binding,
                'completed_runtime_inputs_reexecuted': False, 'record_names': sorted(updated)})

            phase = 'REBIND_ONLY_REVIEWED_PRE_POD_RECORDS'
            for name, value in updated.items():
                need(old.read(work/name) == records[name], 'RUN_ARTIFACT_CHANGED_DURING_MIGRATION')
                temp = work/(name+'.native-new')
                new.write(temp, value)
                os.replace(temp, work/name)
            new.write(out/'binding-migration.complete.json', {'old_binding': previous.binding,
                'new_binding': probe.binding, 'runtime_inputs_reused': True})
            print(json.dumps({'phase': phase, 'old_success_receipt_preserved': True}), flush=True)

            phase = 'RESUME_FAILED_READ_AND_PREPARE_NEW_DATA_POD'
            flow = new.Flow(config, work, prepare_data_pod=True, resume_readonly=True)
            result = flow.prepare_data_pod()
            result.update(source_sha=a.new_head, runtime_inputs_reexecuted=False,
                          serviceaccount_or_pull_secret_deleted=False, existing_work=str(work))
    except Exception as error:
        reason = str(error)
        result.update(status='STOP_NATIVE_PULL_RESUME', phase=phase,
            reason=reason if re.fullmatch(r'[A-Za-z0-9_.:-]{1,200}', reason) else type(error).__name__)
    if out is not None:
        with (out/'receipt.json').open('w', encoding='utf-8') as f:
            json.dump(result, f, indent=2)
    print(json.dumps(result, indent=2), flush=True)
    if out is not None:
        print('Private native pull resume directory: '+str(out), flush=True)
    return 0 if result['status'].startswith('PASS_') else 2


if __name__ == '__main__':
    raise SystemExit(main())
