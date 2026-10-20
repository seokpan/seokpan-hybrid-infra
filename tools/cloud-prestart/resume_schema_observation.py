"""Repair only the accepted schema receipt reference, then resume the same run.

No account creation, SQL, migration, credential supply, new Pod or source adoption.
The existing initializer must reuse its actual successful database receipt.
"""
import argparse
import datetime as dt
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile


def need(ok, code):
    if not ok:
        raise RuntimeError(code)


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def safe(error):
    value = str(error)
    return value if re.fullmatch(r'[A-Z][A-Z0-9_:.-]{1,180}', value) else type(error).__name__


def observation(wrapper, binding):
    need(wrapper.get('binding') == binding and isinstance(wrapper.get('completed_at_utc'), str),
         'SAME_COMPLETED_SCHEMA_STEP_BINDING_REQUIRED')
    result = wrapper.get('result', {})
    need(result.get('status') == 'PASS_SCHEMA_AND_CAPTURED_ADMISSION_NOT_BUSINESS'
         and result.get('data_mode') == 'fresh_test'
         and result.get('fresh_database_empty_verified') is True
         and result.get('schema_revision') == '20260902_0002'
         and result.get('database_writes') == 0 and result.get('redis_writes') == 0,
         'ACTUAL_SUCCESSFUL_FRESH_SCHEMA_OBSERVATION_REQUIRED')
    return result


def repaired_evidence(evidence, wrapper_path, raw_path):
    value = dict(evidence)
    keys = ('migration_current_ref', 'captured_transition_ref')
    old = all(value.get(k) == str(wrapper_path) for k in keys)
    new = all(value.get(k) == str(raw_path) for k in keys)
    need(old or new, 'ONLY_TWO_KNOWN_SCHEMA_REFERENCES_MAY_CHANGE')
    for key in keys:
        value[key] = str(raw_path)
    return value, old


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dispatch', required=True, type=Path)
    parser.add_argument('--setup-directory', required=True, type=Path)
    parser.add_argument('--initializer', required=True, type=Path)
    parser.add_argument('--inspection', required=True, type=Path)
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    os.umask(0o077)
    out = None
    report = {'status': 'STOP_SCHEMA_REFERENCE_RESUME', 'sql_repeated': False,
              'database_initialization_repeated': False, 'automatic_retry': False}
    try:
        import pwd
        import fcntl
        need(args.execute and pwd.getpwuid(os.getuid()).pw_name == 'jth', 'USE_CONTROLLER_JTH_AND_EXECUTE')
        need(hashlib.sha256(args.initializer.read_bytes()).hexdigest() ==
             'ab839b48c8c24d25ef0a898c3f0ce7888f08b5e0301b7e72826cb0c454a0135c',
             'EXISTING_APPROVED_INITIALIZER_HASH_REQUIRED')
        dispatch = json.loads(args.dispatch.read_text())
        P = load(Path(dispatch['source']), 'schema_reference_prestart')
        dispatch = P.read(args.dispatch)
        config = P.read(dispatch['config'])
        work = Path(dispatch['work'])
        setup = args.setup_directory
        with open(str(work)+'.lock', 'a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            flow = P.Flow(config, work, resume_readonly=True)
            pointer = P.read(args.dispatch.parent/'current-fresh-db-setup.json')
            need(pointer == {'directory': str(setup), 'binding': flow.binding}, 'EXACT_ACCEPTED_SETUP_POINTER_REQUIRED')
            initialized = P.read(setup/'receipt.json')
            need(initialized.get('status') == 'PASS_FRESH_SCHEMA_AND_PURPOSE_ACCOUNTS_PREPARED'
                 and initialized.get('cluster_id') == flow.cluster
                 and initialized.get('pod_uid') == P.read(work/'prepared-data-target.json')['pod_uid'],
                 'COMPLETED_DATABASE_INITIALIZATION_REQUIRED_NO_RETRY')
            for name in ('purpose-sql', 'schema-mode', 'initial-pool', 'execution-evidence-step'):
                accepted = P.read(work/(name+'.json'))
                need(accepted.get('binding') == flow.binding and accepted.get('result', {}).get('status', '').startswith('PASS_'),
                     'ACTUAL_COMPLETED_'+name.upper().replace('-', '_')+'_REQUIRED')
            marker = work/'single-source.json.STARTED_NO_AUTOMATIC_RETRY.json'
            need(not marker.exists() and not (work/'single-staged.json').exists(),
                 'SOURCE_ALREADY_ATTEMPTED_REQUIRES_SEPARATE_RECONCILIATION')
            wrapper_path = work/'schema-mode.json'
            raw_path = work/'schema-observation.accepted.json'
            observed = observation(P.read(wrapper_path), flow.binding)
            path = work/'execution-evidence.json'
            existing = P.read(path)
            fixed, old = repaired_evidence(existing, wrapper_path, raw_path)
            c = P.read(work/'new-inputs.json')['result']['inputs']
            if old:
                try:
                    flow.L.evidence(c, existing, 'single')
                except Exception as error:
                    need(safe(error) == 'CURRENT_FRESH_EMPTY_SCHEMA_OBSERVATION_REQUIRED',
                         'DIFFERENT_FAILURE_NO_REFERENCE_REPAIR:'+safe(error))
                else:
                    raise RuntimeError('KNOWN_WRAPPER_FAILURE_NOT_REPRODUCED_NO_REPAIR')
            if raw_path.exists():
                need(P.read(raw_path) == observed, 'EXISTING_RAW_SCHEMA_OBSERVATION_CHANGED')
            else:
                P.write(raw_path, observed)
            flow.L.evidence(c, fixed, 'single')
            if old:
                backup = work/'execution-evidence.before-schema-reference.json'
                if backup.exists():
                    need(P.read(backup) == existing, 'ORIGINAL_EVIDENCE_BACKUP_CHANGED')
                else:
                    P.write(backup, existing)
                fd, filename = tempfile.mkstemp(prefix='.schema-reference-', dir=work)
                with os.fdopen(fd, 'wb') as stream:
                    stream.write(P.canon(fixed)+b'\n')
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(filename, path)
            out = Path(tempfile.mkdtemp(prefix='schema-reference-resume-', dir=setup))
            report.update(status='PASS_ACTUAL_SCHEMA_RECEIPT_REFERENCE_REPAIRED',
                          cluster_id=flow.cluster, existing_schema_success_reused=True,
                          changed_reference_fields=2 if old else 0, source_binding_changed=False)
            P.write(out/'repair-receipt.json', report)
            print(json.dumps(report), flush=True)
        # Reuse the accepted initialization receipt; no AWS authentication or DDL is repeated.
        child = subprocess.run([sys.executable, '-I', str(args.initializer),
            '--dispatch', str(args.dispatch), '--inspection', str(args.inspection),
            '--db-instance', 'seokpan-fnd-mariadb', '--account-network', '192.168.64.0/20',
            '--approval-ref', 'USER_PROJECT_FRESH_DATA_APP_START_20261020', '--execute'],
            timeout=720)
        if child.returncode:
            diagnostics = []
            for label, fn in (
                ('local_evidence', lambda: flow.L.evidence(c, P.read(path), 'single')),
                ('current_window', lambda: flow.L.budget_check(c, dt.datetime.now(dt.timezone.utc))),
                ('current_application', lambda: flow.L.current_app(json.loads(flow.call(
                    'get', 'application', flow.L.APP, '-n', flow.L.ARGO, '-o', 'json'))))):
                try:
                    fn()
                    diagnostics.append({'boundary': label, 'status': 'PASS'})
                except Exception as error:
                    diagnostics.append({'boundary': label, 'reason': safe(error)})
            report = {'status': 'STOP_APP_STAGE_WITH_SANITIZED_BOUNDARY', 'diagnostics': diagnostics,
                      'database_initialization_repeated': False, 'automatic_retry': False}
            P.write(out/'app-boundary-diagnostic.json', report)
            print(json.dumps(report, indent=2), flush=True)
        return child.returncode
    except Exception as error:
        report.update(status='STOP_SCHEMA_REFERENCE_RESUME', reason=safe(error))
        print(json.dumps(report, indent=2), flush=True)
        return 2
    finally:
        if out is not None:
            print('Private schema reference resume directory: '+str(out), flush=True)


if __name__ == '__main__':
    raise SystemExit(main())
