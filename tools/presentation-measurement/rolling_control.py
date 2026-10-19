"""One explicitly requested Backend rolling change, source-bound; no default mutation.

Uses the new personal Kubernetes context to submit an Argo Application Source
and one explicit operation. Unknown responses reconcile, never auto-resubmit.
"""
import argparse
import copy
import datetime as dt
import importlib.util
import json
import os
import re
from pathlib import Path
import subprocess
import sys
import time
sys.path.insert(0, str(Path(__file__).resolve().parent))
from timeline import canonical, need, sha
from stage_clock import read, save

NS, ARGO, APP = 'seokpan-cloud', 'openshift-gitops', 'seokpan-cloud-app'
KEY = 'seokpan.io/presentation-rollout'


def normalized(source):
    s = copy.deepcopy(source)
    for patch in s.get('kustomize', {}).get('patches', []):
        if patch.get('target', {}).get('group') == '': patch['target'].pop('group')
        if isinstance(patch.get('patch'), str): patch['patch'] = json.loads(patch['patch'])
    return s


def desired_source(original, run):
    source = copy.deepcopy(original)
    patch = {'apiVersion': 'apps/v1', 'kind': 'Deployment', 'metadata': {'name': 'backend'},
             'spec': {'template': {'metadata': {'annotations': {KEY: run}}}}}
    source.setdefault('kustomize', {}).setdefault('patches', []).append({
        'target': {'group': 'apps', 'version': 'v1', 'kind': 'Deployment', 'name': 'backend'},
        'patch': json.dumps(patch, separators=(',', ':'))})
    return source


def operation_patch(app, source):
    return [{'op': 'test', 'path': '/metadata/uid', 'value': app['metadata']['uid']},
            {'op': 'test', 'path': '/metadata/resourceVersion', 'value': app['metadata']['resourceVersion']},
            {'op': 'test', 'path': '/spec', 'value': app['spec']},
            {'op': 'replace', 'path': '/spec/source', 'value': source},
            {'op': 'add', 'path': '/operation', 'value': {'initiatedBy': {'username': 'tjung03'},
             'retry': {'limit': 0}, 'sync': {'revision': source['targetRevision'], 'prune': False,
                      'syncOptions': ['FailOnSharedResource=true', 'Prune=false', 'Delete=false'],
                      }}}]


def templates(before, after):
    need(after['frontend']['spec']['template'] == before['frontend']['spec']['template'], 'FRONTEND_TEMPLATE_CHANGED_STOP')
    for name in ('backend', 'frontend'):
        need(after[name]['metadata']['uid'] == before[name]['metadata']['uid']
             and after[name]['spec']['replicas'] == before[name]['spec']['replicas'], 'UID_OR_BASE_REPLICAS_CHANGED')
    b, a = copy.deepcopy(before['backend']['spec']['template']), copy.deepcopy(after['backend']['spec']['template'])
    for t in (b, a):
        m = t.setdefault('metadata', {})
        m.setdefault('annotations', {}).pop(KEY, None)
        if not m['annotations']: m.pop('annotations')
    need(a == b, 'BACKEND_CHANGE_EXCEEDS_ROLLOUT_ANNOTATION')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=['prepare', 'trigger', 'verify', 'observe', 'pause', 'resume', 'recover'])
    p.add_argument('--config', type=Path, required=True)
    p.add_argument('--work', type=Path, required=True)
    p.add_argument('--execute', action='store_true')
    p.add_argument('--seconds', type=int, default=600)
    p.add_argument('--accept-recovery-roll', action='store_true')
    args = p.parse_args()
    os.umask(0o077)
    try:
        c = read(args.config)
        need(c['actor'] == 'tjung03' and c['namespace'] == NS and c['application'] == APP,
             'EXACT_EXECUTOR_TARGET_REQUIRED')
        need(0 < args.seconds <= 900, 'BOUNDED_OBSERVATION_REQUIRED')
        if not args.execute:
            print(json.dumps({'status': 'PREPARED_ROLLING_COMMAND_NOT_EXECUTED', 'action': args.action, 'mutations': 0}))
            return 0
        need(isinstance(c.get('run_id'), str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{2,79}', c['run_id']), 'ACTUAL_ROLLING_RUN_ID_REQUIRED')
        cached_path = args.work/'rolling-verified.json'
        if args.action == 'verify' and cached_path.exists():
            accepted = read(cached_path)
            need(accepted.get('config_sha256') == sha(c), 'VERIFIED_ROLL_RECEIPT_BINDING_CHANGED')
            print(json.dumps({**accepted, 'receipt_reused': True, 'current_live_state_requeried': False}))
            return 0
        need(dt.datetime.now(dt.timezone.utc) < dt.datetime.fromisoformat(c['not_after'].replace('Z', '+00:00')), 'EXECUTION_WINDOW_ENDED')
        need(dt.datetime.now(dt.timezone.utc) >= dt.datetime.fromisoformat(c['not_before'].replace('Z', '+00:00')), 'EXECUTION_WINDOW_NOT_STARTED')
        args.work.mkdir(mode=0o700, exist_ok=True)
        def call(*argv, payload=None):
            r = subprocess.run([c['oc'], '--kubeconfig='+c['personal_kubeconfig'], '--request-timeout=15s', *argv],
                               input=payload, capture_output=True, timeout=25)
            need(r.returncode == 0, 'KUBERNETES_RESULT_UNKNOWN_RECONCILE_NO_RETRY')
            return r.stdout
        cfg = json.loads(call('config', 'view', '--minify', '-o', 'json'))['clusters'][0]['cluster']
        need(cfg['server'].rstrip('/') == c['api'].rstrip('/') and not cfg.get('insecure-skip-tls-verify'), 'NEW_TRUSTED_API_REQUIRED')
        need(call('whoami').strip() == b'tjung03', 'ACTUAL_PERSONAL_EXECUTOR_REQUIRED')
        ns = json.loads(call('get', 'namespace', NS, '-o', 'json'))
        need(ns['metadata']['uid'] == c['namespace_uid'], 'NAMESPACE_UID_CHANGED')
        get = lambda kind, name, namespace: json.loads(call('get', kind, name, '-n', namespace, '-o', 'json'))
        app = get('application', APP, ARGO)
        need(app['metadata']['uid'] == c['application_uid'] and not app['spec'].get('syncPolicy', {}).get('automated'), 'SAME_MANUAL_APPLICATION_REQUIRED')
        need(app['metadata'].get('annotations', {}).get('seokpan.io/bootstrap-owner') == 'cloud-app-held'
             and not app['metadata'].get('deletionTimestamp') and not app['metadata'].get('ownerReferences')
             and app['spec'].get('project') == APP and app['spec'].get('destination') == {'namespace': NS, 'server': 'https://kubernetes.default.svc'}
             and not app['spec'].get('sources'), 'OWNED_LOCAL_CLOUD_DESTINATION_REQUIRED')
        dep = lambda: {n: get('deployment', n, NS) for n in ('backend', 'frontend')}
        def pods(name):
            return json.loads(call('get', 'pods', '-n', NS, '-l', 'app.kubernetes.io/name='+name, '-o', 'json'))['items']
        if args.action == 'prepare':
            need(not (args.work/'before.json').exists(), 'EXISTING_PLAN_REUSE_DO_NOT_PREPARE_AGAIN')
            need(not app.get('operation') and app.get('status', {}).get('sync', {}).get('status') == 'Synced'
                 and app.get('status', {}).get('health', {}).get('status') == 'Healthy', 'NORMAL_BASELINE_REQUIRED')
            need(sha(normalized(app['spec']['source'])) == c['normalized_source_sha256'], 'ACCEPTED_SOURCE_REQUIRED')
            before = dep()
            for name in before:
                d = before[name]
                need(not d['spec'].get('paused') and d['status'].get('observedGeneration') == d['metadata']['generation']
                     and all(d['status'].get(k, 0) == d['spec']['replicas'] for k in ('readyReplicas', 'updatedReplicas', 'availableReplicas', 'replicas')), 'READY_BASELINE_REQUIRED')
                need(d['spec']['replicas'] == c['expected_replicas'][name]
                     and [x['image'] for x in d['spec']['template']['spec']['containers']] == [c['expected_images'][name]],
                     'ACTUAL_IMAGE_AND_BASE_REPLICA_BINDING_REQUIRED')
            need(KEY not in before['backend']['spec']['template'].get('metadata', {}).get('annotations', {}), 'NEW_RUN_WITHOUT_OLD_ROLLOUT_MARK_REQUIRED')
            old_pods = {name: pods(name) for name in before}
            for name, values in old_pods.items():
                need(len(values) == c['expected_replicas'][name] and all(not x['metadata'].get('deletionTimestamp')
                    and any(v.get('type') == 'Ready' and v.get('status') == 'True' for v in x.get('status', {}).get('conditions', [])) for x in values),
                    'READY_BASELINE_POD_SET_REQUIRED')
            save(args.work/'before.json', {'config_sha256': sha(c), 'application': app, 'deployments': before,
                                         'pod_uids': {n: [x['metadata']['uid'] for x in values] for n, values in old_pods.items()},
                                         'source_after': desired_source(app['spec']['source'], c['run_id'])})
            print(json.dumps({'status': 'PASS_BOUND_BACKEND_ROLLING_PLAN_NO_CHANGE', 'source_before': c['source_sha256'], 'frontend_held': True}))
            return 0
        before = read(args.work/'before.json')
        need(before['config_sha256'] == sha(c), 'CONFIG_BINDING_CHANGED')
        if args.action == 'verify':
            need((args.work/'trigger.STARTED.json').exists(), 'ACTUAL_TRIGGER_ATTEMPT_REQUIRED')
            op = app.get('status', {}).get('operationState', {})
            sync = app.get('status', {}).get('sync', {})
            need(app['spec']['source'] == before['source_after'] and not app.get('operation')
                 and op.get('phase') == 'Succeeded' and sync.get('status') == 'Synced'
                 and app.get('status', {}).get('health', {}).get('status') == 'Healthy'
                 and sync.get('revision') == before['source_after']['targetRevision']
                 and op.get('syncResult', {}).get('revision') == before['source_after']['targetRevision']
                 and normalized(sync.get('comparedTo', {}).get('source', {})) == normalized(before['source_after'])
                 and normalized(op.get('syncResult', {}).get('source', {})) == normalized(before['source_after']),
                 'ROLL_OPERATION_SOURCE_SYNC_HEALTH_NOT_ACCEPTED')
            current = dep();templates(before['deployments'], current)
            backend = current['backend']
            need(backend['metadata']['generation'] > before['deployments']['backend']['metadata']['generation']
                 and backend['spec']['template'].get('metadata', {}).get('annotations', {}).get(KEY) == c['run_id'], 'ACTUAL_BACKEND_TEMPLATE_ROLL_NOT_OBSERVED')
            for name, value in current.items():
                need(value['status'].get('observedGeneration') == value['metadata']['generation']
                    and all(value['status'].get(k, 0) == value['spec']['replicas'] for k in ('readyReplicas', 'updatedReplicas', 'availableReplicas', 'replicas')),
                    'ROLL_CURRENT_REPLICA_READY_REQUIRED')
            actual = {name: pods(name) for name in current}
            need({x['metadata']['uid'] for x in actual['frontend']} == set(before['pod_uids']['frontend']), 'FRONTEND_POD_REPLACEMENT_CONFOUNDS_COMPARISON')
            new = actual['backend']
            need(len(new) == backend['spec']['replicas'] and not ({x['metadata']['uid'] for x in new} & set(before['pod_uids']['backend']))
                and all(not x['metadata'].get('deletionTimestamp') and any(v.get('type') == 'Ready' and v.get('status') == 'True'
                    for v in x.get('status', {}).get('conditions', [])) for x in new), 'OLD_BACKEND_PODS_REMAIN_OR_NEW_SET_NOT_READY')
            value = {'status': 'PASS_BACKEND_ROLL_RECONCILED_NOT_BUSINESS_ACCEPTANCE', 'config_sha256': sha(c), 'application_uid': c['application_uid'],
                'namespace_uid': c['namespace_uid'], 'source_before_normalized_sha256': sha(normalized(before['application']['spec']['source'])),
                'source_after_normalized_sha256': sha(normalized(before['source_after'])),
                'frontend_template_and_pod_uids_unchanged': True, 'old_backend_pods_absent': True,
                'backend_pod_uids': [x['metadata']['uid'] for x in new],
                'images': {name: current[name]['spec']['template']['spec']['containers'][0]['image'] for name in current},
                'replicas': {name: current[name]['spec']['replicas'] for name in current},
                'utc': dt.datetime.now(dt.timezone.utc).isoformat()}
            result_path = args.work/'rolling-verified.json'
            if result_path.exists(): need(read(result_path) == value, 'EXISTING_ROLL_VERIFY_RECEIPT_REUSE_DO_NOT_OVERWRITE')
            else: save(result_path, value)
            print(json.dumps(value));return 0
        if args.action in {'trigger', 'recover'}:
            recovering = args.action == 'recover'
            need(not recovering or args.accept_recovery_roll, 'RECOVERY_MAY_ROLL_AGAIN_EXPLICIT_ACCEPTANCE_REQUIRED')
            target = before['application']['spec']['source'] if recovering else before['source_after']
            expected = before['source_after'] if recovering else before['application']['spec']['source']
            marker = args.work/('recovery.STARTED.json' if recovering else 'trigger.STARTED.json')
            need(not marker.exists(), 'MUTATION_ALREADY_ATTEMPTED_OBSERVE_ONLY_NO_RESUBMISSION')
            need(app['spec']['source'] == expected and not app.get('operation')
                 and app.get('status', {}).get('operationState', {}).get('phase') not in {'Running', 'Terminating'}, 'CURRENT_SOURCE_OR_OPERATION_CHANGED')
            current = dep()
            templates(before['deployments'], current)
            # A pause is a temporary emergency intervention. Unpause is manual and not hidden here.
            need(not current['backend']['spec'].get('paused'), 'BACKEND_PAUSED_REVIEW_BEFORE_RECOVERY')
            patch = operation_patch(app, target)
            save(marker, {'application_uid': c['application_uid'], 'source_before': sha(expected), 'source_after': sha(target),
                          'monotonic_ns': time.monotonic_ns(), 'utc': dt.datetime.now(dt.timezone.utc).isoformat()})
            call('patch', 'application', APP, '-n', ARGO, '--type=json', '-p', canonical(patch).decode())
            actual = get('application', APP, ARGO)
            need(actual['spec']['source'] == target and actual['metadata']['uid'] == c['application_uid'], 'UNKNOWN_PATCH_RESULT_OBSERVE_ONLY')
            print(json.dumps({'status': 'REQUESTED_ONE_GITOPS_BACKEND_RECOVERY_ROLL' if recovering else 'REQUESTED_ONE_GITOPS_BACKEND_ROLL',
                              'source_after_sha256': sha(target), 'frontend_changed_by_command': False,
                              'application_uid': c['application_uid'], 'namespace_uid': c['namespace_uid'],
                              'runtime_roll_completion': 'NOT_YET_VERIFIED', 'automatic_retry': False}))
            return 0
        if args.action in {'pause', 'resume'}:
            need(not app.get('operation') and app.get('status', {}).get('operationState', {}).get('phase') not in {'Running', 'Terminating'},
                 'TERMINATE_AND_RECONCILE_ARGO_OPERATION_BEFORE_PAUSE_OR_RESUME')
            current = dep()
            templates(before['deployments'], current)
            pausing = args.action == 'pause'
            need(app['spec']['source'] in (before['source_after'], before['application']['spec']['source']), 'SOURCE_CHANGED_BEFORE_PAUSE_RESUME')
            need(pausing or current['backend']['spec'].get('paused') is True, 'ONLY_PAUSED_BACKEND_CAN_RESUME')
            marker = args.work/('pause.STARTED.json' if pausing else 'resume.STARTED.json')
            need(not marker.exists(), 'PAUSE_RESUME_ALREADY_ATTEMPTED_READ_CURRENT_STATE')
            save(marker, {'backend_uid': current['backend']['metadata']['uid'], 'temporary_live_gitops_drift': True})
            patch = [{'op': 'test', 'path': '/metadata/uid', 'value': current['backend']['metadata']['uid']},
                     {'op': 'test', 'path': '/metadata/resourceVersion', 'value': current['backend']['metadata']['resourceVersion']},
                     {'op': 'add', 'path': '/spec/paused', 'value': pausing}]
            call('patch', 'deployment', 'backend', '-n', NS, '--type=json', '-p', canonical(patch).decode())
            print(json.dumps({'status': 'PAUSED_BACKEND_DEPLOYMENT_FOR_REVIEW' if pausing else 'RESUMED_RECONCILED_BACKEND_ROLL', 'temporary_live_gitops_drift': pausing,
                              'resume': 'explicitly reconcile paused/source/ReplicaSets before unpausing; no automatic rollback'}))
            return 0
        deadline = min(time.monotonic()+args.seconds, time.monotonic()+max(0, dt.datetime.fromisoformat(c['not_after'].replace('Z', '+00:00')).timestamp()-time.time()))
        journal = args.work/'deployment-pod-timeline.jsonl'
        while time.monotonic() < deadline:
            current = dep()
            templates(before['deployments'], current)
            pods = json.loads(call('get', 'pods', '-n', NS, '-o', 'json'))['items']
            row = {'utc': dt.datetime.now(dt.timezone.utc).isoformat(), 'monotonic_ns': time.monotonic_ns(),
                'deployments': {n: {'uid': d['metadata']['uid'], 'generation': d['metadata']['generation'],
                    'replicas': d['spec']['replicas'], 'status': d.get('status'), 'paused': d['spec'].get('paused', False)} for n, d in current.items()},
                'pods': [{'uid': x['metadata']['uid'], 'node': x['spec'].get('nodeName'), 'created_at_utc': x['metadata']['creationTimestamp'],
                          'deleting_at_utc': x['metadata'].get('deletionTimestamp'), 'phase': x.get('status', {}).get('phase'),
                          'ready': any(y.get('type') == 'Ready' and y.get('status') == 'True' for y in x.get('status', {}).get('conditions', [])),
                          'owner_uids': [o['uid'] for o in x['metadata'].get('ownerReferences', [])]}
                         for x in pods if x['metadata'].get('labels', {}).get('app.kubernetes.io/name') in {'backend', 'frontend'}]}
            fd = os.open(journal, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            with os.fdopen(fd, 'ab') as f: f.write(canonical(row)+b'\n')
            time.sleep(5)
        print(json.dumps({'status': 'COLLECTED_BOUNDED_BACKEND_POD_NODE_TIMELINE', 'file': str(journal),
                          'sampling_seconds': 5, 'roll_success_not_inferred_from_sampling': True}))
        return 0
    except Exception as error:
        print(json.dumps({'status': 'STOP_ROLLING_CONTROL', 'reason': str(error) if type(error) is RuntimeError else type(error).__name__,
                          'automatic_retry': False, 'raw_error_exported': False}))
        return 2


if __name__ == '__main__': raise SystemExit(main())
