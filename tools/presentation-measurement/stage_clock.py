"""Instrument existing pinned prestart without modifying its approved Source.

entry: wraps only the first actual stage_app execution (not prepare/SQL).
observe: GET-only Source/operation tracking, then existing full readback once ready.
external: receive a same-binding external browser proof on this Controller clock.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import secrets
import sys
import time
sys.path.insert(0, str(Path(__file__).resolve().parent))
from timeline import Ledger, canonical, need, sha, clock


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def read(path):
    path = Path(path)
    need(path.is_file() and not path.is_symlink() and path.stat().st_size < 16*1024*1024, 'BOUNDED_REGULAR_INPUT_REQUIRED')
    return json.loads(path.read_bytes())


def save(path, value):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(canonical(value)+b'\n')


def bound(L, work, stage):
    package, c, source, _, release = L.package(work/'package', stage)
    return {'run_id': c['run_id'], 'cluster_id': c['cluster_id'], 'stage': stage,
            'namespace_uid': c['namespace_uid'], 'infra_sha': c['infra_sha'],
            'source_sha256': L.digest(source), 'gitops_revision': L.GITOPS,
            'normalized_source_sha256': L.digest(L.normalize_source(source)),
            'images': release['images'], 'origin_sha256': hashlib.sha256(package['origin'].encode()).hexdigest()}


def entry(args, rest):
    P = module('instrumented_prestart', args.infra/'tools/cloud-prestart/prestart.py')
    original_helpers = P.helpers
    def helpers():
        L, Q = original_helpers()
        original_stage = L.stage_app
        def stage_app(path, stage, evidence, kubeconfig, oc, receipt, runner):
            first_entry = clock()
            binding = bound(L, args.work, stage)
            ledger = Ledger(args.ledger, binding)
            ledger.mark('s0', {'boundary': 'first actual stage_app entry; prestart prerequisites excluded'}, observed=first_entry)
            _, c, _, _, _ = L.package(path, stage)
            call = L.oc_client(c, kubeconfig, oc, runner)
            try:
                before = json.loads(call('get', 'application', L.APP, '-n', L.ARGO, '-o', 'json'))
                targets = [x for x in ledger.rows if x['event'] == 'stage_target_bound']
                if targets:
                    need(targets[0]['proof']['application_uid'] == before['metadata']['uid'], 'APPLICATION_UID_CHANGED')
                else:
                    ledger.mark('stage_target_bound', {'application_uid': before['metadata']['uid'],
                        'previous_operation_sha256': sha(before.get('status', {}).get('operationState'))})
                return original_stage(path, stage, evidence, kubeconfig, oc, receipt, runner)
            except Exception:
                ledger.mark('failure', {'phase': 'source_stage', 'raw_error_exported': False})
                raise
        L.stage_app = stage_app
        return L, Q
    P.helpers = helpers
    sys.argv = ['prestart.py', '--work', str(args.work), '--stage', args.stage, *rest]
    need('--execute' in rest and '--after-sync' not in rest, 'EXPLICIT_PRESTART_STAGE_EXECUTE_REQUIRED')
    result = P.main()
    print(json.dumps({'status': 'STAGE_CLOCK_PRESERVED' if args.ledger.is_file() else 'PRIOR_STAGE_NOT_TIMED_NO_REPLAY',
                      'ledger': str(args.ledger), 'prestart_exit_code': result}))
    return result


def observe(args):
    L = module('clock_launch', args.infra/'tools/cloud-launch/cloud_launch.py')
    p, c, source, _, _ = L.package(args.work/'package', args.stage)
    ledger = Ledger(args.ledger, bound(L, args.work, args.stage))
    need(ledger.rows and ledger.rows[0]['event'] == 's0', 'FIRST_EXECUTION_TIME_MISSING_DO_NOT_REPLAY')
    targets = [x for x in ledger.rows if x['event'] == 'stage_target_bound']
    need(targets, 'NO_BOUND_STAGE_TARGET_RECONCILE_FAILED_ENTRY_FIRST')
    initial = targets[0]['proof']
    call = L.oc_client(c, args.kubeconfig, args.oc)
    end = min(time.monotonic()+args.seconds, time.monotonic()+max(0, L.stamp(c['window']['not_after']).timestamp()-time.time()))
    while time.monotonic() < end:
        app = json.loads(call('get', 'application', L.APP, '-n', L.ARGO, '-o', 'json'))
        need(app['metadata']['uid'] == initial['application_uid'], 'APPLICATION_UID_CHANGED')
        need(L.normalize_source(app['spec']['source']) == L.normalize_source(source), 'STAGE_SOURCE_CHANGED')
        status = app.get('status', {})
        sync = status.get('sync', {})
        if L.normalize_source(sync.get('comparedTo', {}).get('source', {})) == L.normalize_source(source):
            ledger.mark('s1', {'application_uid': initial['application_uid'], 'source_sha256': L.digest(source),
                               'server_compared_source': True, 'revision': sync.get('revision')})
        op = status.get('operationState') or {}
        sync_source = op.get('syncResult', {}).get('source')
        if (sha(op) != initial['previous_operation_sha256'] and op.get('startedAt')
            and sync_source and L.normalize_source(sync_source) == L.normalize_source(source)
            and op.get('phase') in {'Running', 'Succeeded', 'Failed', 'Error'}
            and any(x['event'] == 's1' for x in ledger.rows)):
            ledger.mark('s2', {'operation_started_at_utc': op['startedAt'],
                'operation_phase_at_first_observation': op['phase'], 'poll_interval_seconds': args.interval,
                'boundary': 'Controller first observes explicit operation; not Source patch time'})
        if (sync.get('status') == 'Synced' and status.get('health', {}).get('status') == 'Healthy'
            and any(x['event'] == 's2' for x in ledger.rows)):
            # Reuse the existing exact Source/Image/Replica/generation/Route readback.
            ready_path = args.work/(args.stage+'-ready.json')
            if ready_path.exists():
                ready = read(ready_path)
                need(ready.get('status') == 'PASS_CLOUD_STAGE_READY_NOT_BUSINESS_ACCEPTANCE'
                     and ready.get('cluster_id') == c['cluster_id'] and ready.get('stage') == args.stage
                     and ready['source_sha256'] == L.digest(source) and ready['application_uid'] == initial['application_uid'], 'READY_RECEIPT_BINDING_CHANGED')
                provenance = 'existing_bound_ready_receipt_first_seen_by_clock'
            else:
                ready = L.readback(args.work/'package', args.stage, args.kubeconfig, args.oc)
                save(ready_path, ready)
                provenance = 'existing_full_readback_executed_once'
            ledger.mark('s3', {'ready': ready, 'provenance': provenance})
            request_path = args.ledger.with_suffix('.external-request.json')
            if not request_path.exists():
                request = {'schema': 1, 'nonce': secrets.token_hex(16), 'binding': ledger.binding,
                           'target_origin': p['origin'], 'window': c['window']}
                ledger.mark('external_requested', {'nonce': request['nonce']})
                save(request_path, request)
            print(json.dumps({'status': 'READY_FOR_EXTERNAL_HTTPS_WSS_ONLY', 'request_file': str(request_path),
                              'sync_started': False, 'sql_calls': 0}))
            return 0
        time.sleep(args.interval)
    print(json.dumps(ledger.summary()))
    return 2


def external(args):
    request = read(args.ledger.with_suffix('.external-request.json'))
    result = read(args.result)
    ledger = Ledger(args.ledger, request['binding'])
    need(result.get('status') == 'PASS_EXTERNAL_HTTPS_WSS_INITIAL_FRAME'
         and result.get('binding') == request['binding'] and result.get('nonce') == request['nonce']
         and result.get('target_origin_sha256') == request['binding']['origin_sha256']
         and result.get('https_status') == 200 and result.get('wss_initial_envelope') is True,
         'SAME_STAGE_EXTERNAL_RESULT_REQUIRED')
    started = next(x for x in ledger.rows if x['event'] == 'external_requested')
    ledger.mark('s4', {'external_receipt_sha256': sha(result), 'external_probe_elapsed_ms': result['elapsed_ms'],
        'external_host_utc_not_subtracted': result['observed_utc'],
        'request_reference_monotonic_ns': started['clock']['monotonic_ns'],
        'manual_transfer_included': True, 'external_result_age_seconds': None,
        'proof_is_supplied_browser_receipt_not_cryptographic_remote_attestation': True})
    summary = ledger.summary()
    print(json.dumps(summary, indent=2))
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['entry', 'observe', 'external', 'summary'])
    parser.add_argument('--infra', type=Path)
    parser.add_argument('--work', type=Path)
    parser.add_argument('--ledger', type=Path, required=True)
    parser.add_argument('--stage', choices=['single', 'shared', 'target'], default='single')
    parser.add_argument('--kubeconfig', type=Path)
    parser.add_argument('--oc', default='oc')
    parser.add_argument('--seconds', type=int, default=600)
    parser.add_argument('--interval', type=int, default=5)
    parser.add_argument('--result', type=Path)
    args, rest = parser.parse_known_args()
    os.umask(0o077)
    try:
        need(1 <= args.interval <= 30 and 1 <= args.seconds <= 1800, 'BOUNDED_OBSERVATION_REQUIRED')
        if args.action == 'entry': return entry(args, rest[1:] if rest[:1] == ['--'] else rest)
        need(not rest, 'UNKNOWN_ARGUMENT')
        if args.action == 'observe': return observe(args)
        if args.action == 'external': return external(args)
        request = read(args.ledger.with_suffix('.external-request.json'))
        print(json.dumps(Ledger(args.ledger, request['binding']).summary(), indent=2))
        return 0
    except Exception as error:
        print(json.dumps({'status': 'STOP_STAGE_CLOCK', 'reason': str(error) if type(error) is RuntimeError else type(error).__name__,
                          'automatic_retry': False, 'raw_error_exported': False}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
