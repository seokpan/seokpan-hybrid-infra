"""Prepare immutable Cloud releases, stage Argo Source, and bind measurement inputs.

No ROSA creation, Data migration, credential supply, direct workload apply or
automatic sync. Stage/readback require --execute. Preparation is local only.
"""
import argparse
import copy
import datetime as dt
from decimal import Decimal
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
from urllib.parse import urlsplit

HERE = Path(__file__).resolve().parent
READY = HERE.parent / 'cloud-readiness'
APP, NS, ARGO = 'seokpan-cloud-app', 'seokpan-cloud', 'openshift-gitops'
GITOPS = 'c75a4b5e3b3b9bae4ea7d8a431962cb9ca7308d7'
APP_SHA = 'c2dad254cc407431092691f9ea5892f8324f1520'
OLD_CLUSTER = '2tejeb2farjaaoef1e74asdf6amrtpu3'
REPO = 'https://github.com/seokpan/seokpan-hybrid-gitops.git'
BASE_PATH = 'apps/overlays/cloud/'
STAGES = {'single': (1, 1), 'shared': (2, 1), 'target': (3, 3)}
EXTRA = {'apps/overlays/cloud/activation-target/kustomization.yaml': '6c7a99bd5483cb37c77fd2918e434c0b60f5cb80',
         'apps/overlays/cloud/activation-target/pdb.yaml': '9b1d604e4b332925a87e88d9334ac31c8cdbc9a8',
         'tools/validate_release_record.py': 'd454e7738c2fab32f22f5e40d1a4dd24801f40ee'}
PROTECTED = {'secret': ('backend-db-runtime', 'backend-redis-runtime'),
             'configmap': ('backend-database-ca', 'backend-redis-ca')}
INPUT_KEYS = {'schema', 'run_id', 'cluster_id', 'api_url', 'namespace_uid', 'ingress_domain',
              'database_host', 'redis_host', 'infra_sha', 'secret_revision', 'release_review_ref',
              'protected_uids', 'window', 'budget'}


class Stop(RuntimeError):
    pass


def need(ok, code):
    if not ok:
        raise Stop(code)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def stamp(value):
    parsed = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
    need(parsed.tzinfo is not None, 'TIMEZONE_REQUIRED')
    return parsed


def ref(value):
    return isinstance(value, str) and bool(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_./:#@+ -]{2,255}', value)) \
        and not re.search(r'pending|input.required|password|token=', value, re.I)


def read(path, private=True):
    path = Path(path)
    need(path.is_file() and not path.is_symlink() and path.stat().st_size <= 8*1024*1024,
         'REGULAR_BOUNDED_INPUT_REQUIRED')
    if private and os.name == 'posix':
        info = path.stat()
        need(info.st_uid == os.getuid() and not stat.S_IMODE(info.st_mode) & 0o077,
             'PRIVATE_OWNED_INPUT_REQUIRED')
    def unique(pairs):
        result = {}
        for key, value in pairs:
            need(key not in result, 'DUPLICATE_JSON_KEY')
            result[key] = value
        return result
    return json.loads(path.read_text(encoding='utf-8-sig'), object_pairs_hook=unique)


def write(path, value):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')


def helper():
    path = READY/'prepare_cloud_candidate.py'
    spec = importlib.util.spec_from_file_location('launch_candidate', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def validate_inputs(c):
    need(set(c) == INPUT_KEYS and c.get('schema') == 1, 'EXACT_LAUNCH_INPUT_FIELDS_REQUIRED')
    need(isinstance(c['run_id'], str) and re.fullmatch(r'[a-z0-9][a-z0-9-]{2,60}', c['run_id']), 'RUN_ID_REQUIRED')
    need(isinstance(c['cluster_id'], str) and re.fullmatch(r'[a-z0-9]{10,64}', c['cluster_id'])
         and c['cluster_id'] != OLD_CLUSTER, 'NEW_CLUSTER_REQUIRED')
    need(re.fullmatch(r'[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}', c['namespace_uid'] or ''), 'NEW_NAMESPACE_UID_REQUIRED')
    module = helper()
    for key in ('ingress_domain', 'database_host', 'redis_host'):
        module.host(c[key])
        need(not re.search(r'invalid|input-required|localhost', c[key]), 'ACTUAL_CONNECTION_HOST_REQUIRED')
    need(c['ingress_domain'].startswith('apps.') and c['ingress_domain'].endswith('.openshiftapps.com'), 'NEW_ROSA_INGRESS_REQUIRED')
    need('seokpan-cloud.8zrv.' not in c['ingress_domain'], 'DELETED_INGRESS_REJECTED')
    api = urlsplit(c['api_url'])
    need(api.scheme == 'https' and api.hostname == 'api.'+c['ingress_domain'][5:]
         and api.port == 6443 and not api.username and not api.password
         and api.path in ('', '/') and not api.query and not api.fragment, 'MATCHED_NEW_ROSA_API_REQUIRED')
    need(re.fullmatch(r'[a-f0-9]{40}', c['infra_sha'] or ''), 'INFRA_SOURCE_SHA_REQUIRED')
    need(ref(c['secret_revision']) and ref(c['release_review_ref']), 'LOGICAL_SECRET_REVISION_AND_REVIEW_REQUIRED')
    expected = {kind+'/'+name for kind, names in PROTECTED.items() for name in names}
    need(set(c['protected_uids']) == expected and all(re.fullmatch(r'[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}', v or '')
         for v in c['protected_uids'].values()), 'CURRENT_PROTECTED_OBJECT_UIDS_REQUIRED')
    need(set(c['window']) == {'not_before', 'not_after'}
         and stamp(c['window']['not_before']) < stamp(c['window']['not_after']), 'BOUNDED_EXECUTION_WINDOW_REQUIRED')
    return module


def budget_check(c, now=None):
    b = c['budget']
    fields = {'reported_spend_usd', 'unreported_and_retained_allowance_usd', 'hourly_upper_usd',
              'shutdown_reserve_hours', 'variable_allowance_usd', 'estimate_ref'}
    need(set(b) == fields and ref(b['estimate_ref']), 'BUDGET_BASIS_REQUIRED')
    n = {}
    for k in fields-{'estimate_ref'}:
        need(isinstance(b[k], (int, float, str)) and not isinstance(b[k], bool), 'ACTUAL_BUDGET_INPUTS_REQUIRED')
        n[k] = Decimal(str(b[k]))
        need(n[k].is_finite() and n[k] >= 0, 'NONNEGATIVE_FINITE_BUDGET_REQUIRED')
    need(n['hourly_upper_usd'] > 0 and n['shutdown_reserve_hours'] > 0, 'HOURLY_ESTIMATE_AND_SHUTDOWN_RESERVE_REQUIRED')
    start, end = map(stamp, (c['window']['not_before'], c['window']['not_after']))
    if now is not None:
        need(start <= now < end, 'CURRENT_EXECUTION_WINDOW_REQUIRED')
    hours = Decimal(str((end-start).total_seconds()))/Decimal(3600)
    total = n['reported_spend_usd'] + n['unreported_and_retained_allowance_usd'] + n['variable_allowance_usd'] \
        + n['hourly_upper_usd']*(hours+n['shutdown_reserve_hours'])
    need(total <= 450, 'PROJECTED_COST_EXCEEDS_EXISTING_450_PLAN')
    return {'projected_total_usd': str(total), 'hard_cap_usd': 500, 'planning_line_usd': 450,
            'basis': 'SUPPLIED_ESTIMATE_NOT_LIVE_BILLING_OR_GUARANTEE', 'automatic_shutdown': False}


def identity(o):
    return '|'.join((o['apiVersion'], o['kind'], o['metadata']['namespace'], o['metadata']['name']))


def prepare(source, out, c, kustomize):
    module = validate_inputs(c)
    need(not out.exists() and out.absolute() != source.resolve() and source.resolve() not in out.absolute().parents,
         'NEW_OUTPUT_OUTSIDE_SOURCE_REQUIRED')
    contract = read(READY/'source_contract.json', False)['files'] | EXTRA
    out.mkdir(mode=0o700, parents=True)
    copied = out/'source'
    for name, expected in contract.items():
        path = source/name
        need(path.is_file() and not path.is_symlink(), 'REGULAR_PINNED_SOURCE_REQUIRED')
        raw = path.read_bytes()
        need(hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest() == expected, 'PINNED_SOURCE_CONTENT_REQUIRED')
        dest = copied/name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(raw)
    import yaml
    validator_spec = importlib.util.spec_from_file_location('selected_release_record', copied/'tools/validate_release_record.py')
    validator = importlib.util.module_from_spec(validator_spec)
    validator_spec.loader.exec_module(validator)
    sources, objects, releases = {}, {}, {}
    route = 'seokpan.'+c['ingress_domain']
    env, _, _, pending = module.configuration({'route_host': route, 'database_host': c['database_host'], 'redis_host': c['redis_host']})
    need(not pending, 'NO_PLACEHOLDER_HOSTS_REQUIRED')
    for stage, replicas in STAGES.items():
        old, _, _ = module.stage_overlay(stage, {'route_host': route, 'database_host': c['database_host'], 'redis_host': c['redis_host']})
        patches = [p for p in old['patches'] if 'target' in p]
        patches.append({'target': {'version': 'v1', 'kind': 'ConfigMap', 'name': 'backend-config'},
                        'patch': json.dumps({'apiVersion': 'v1', 'kind': 'ConfigMap', 'metadata': {'name': 'backend-config'}, 'data': env})})
        release_id = c['run_id']+'-'+stage
        for app in ('backend', 'frontend'):
            patches.append({'target': {'group': 'apps', 'version': 'v1', 'kind': 'Deployment', 'name': app},
                            'patch': json.dumps({'apiVersion': 'apps/v1', 'kind': 'Deployment', 'metadata': {'name': app,
                              'annotations': {'seokpan.io/release-id': release_id, 'seokpan.io/app-source-sha': APP_SHA,
                                              'seokpan.io/release-state': 'prepared-runtime-not-accepted'}}})})
        path = BASE_PATH+('activation-target' if stage == 'target' else 'runtime')
        src = {'repoURL': REPO, 'targetRevision': GITOPS, 'path': path,
               'kustomize': {'namespace': NS, 'replicas': [{'name': a, 'count': n} for a, n in zip(('backend', 'frontend'), replicas)],
                            'patches': patches}}
        # Reproduce Argo's inline Kustomize options on a separate copy of the pinned source.
        local = out/('render-'+stage)
        shutil.copytree(copied, local)
        kpath = local/path/'kustomization.yaml'
        k = yaml.safe_load(kpath.read_text(encoding='utf-8'))
        k['namespace'], k['replicas'] = NS, src['kustomize']['replicas']
        k['patches'] = k.get('patches', []) + patches
        kpath.write_text(json.dumps(k), encoding='utf-8')
        p = subprocess.run([str(kustomize), 'build', str(kpath.parent)], capture_output=True, timeout=40)
        if p.returncode:
            (out/(stage+'.render-error.private.log')).write_bytes(p.stderr)
        need(p.returncode == 0, 'INLINE_SOURCE_LOCAL_RENDER_FAILED')
        rows = list(yaml.safe_load_all(p.stdout))
        need(len(rows) == (10 if stage == 'target' else 8) and len({identity(o) for o in rows}) == len(rows), 'EXACT_APP_OBJECT_INVENTORY_REQUIRED')
        need(all(o['metadata'].get('namespace') == NS and o['kind'] in {'Deployment', 'Service', 'ConfigMap', 'Route', 'PodDisruptionBudget'} for o in rows), 'APP_NAMESPACED_SCOPE_ONLY')
        need(not re.search(r'INPUT_REQUIRED|input-required|\.invalid', json.dumps(rows)), 'RESOLVED_APP_DECLARATIONS_REQUIRED')
        config, = [o for o in rows if o['kind'] == 'ConfigMap']
        for app, count in zip(('backend', 'frontend'), replicas):
            dep, = [o for o in rows if o['kind'] == 'Deployment' and o['metadata']['name'] == app]
            need(dep['spec']['replicas'] == count and dep['spec']['template']['spec']['serviceAccountName'] == 'seokpan-cloud-runtime', 'STAGE_REPLICAS_AND_NATIVE_SA_REQUIRED')
            need(not dep['spec']['template']['spec'].get('imagePullSecrets'), 'NO_LEGACY_PULL_REATTACHMENT')
            if app == 'backend':
                need(dep['spec']['template']['spec']['containers'][0]['envFrom'] == [{'configMapRef': {'name': config['metadata']['name']}}], 'GENERATED_CONFIG_REFERENCE_REQUIRED')
        release = {'schema_version': 1, 'record_kind': 'candidate', 'environment': 'cloud', 'completeness': 'COMPLETE', 'release_id': release_id,
                   'source': {'app_sha': APP_SHA, 'infra_sha': c['infra_sha']},
                   'images': {}, 'revisions': {'schema': '20260902_0002', 'config': digest(config['data']),
                     'secret': c['secret_revision'], 'tool_manifest': digest(contract)},
                   'review_refs': [c['release_review_ref']], 'verification': {'render': 'PASS', 'deployment': 'NOT RUN', 'acceptance': 'NOT RUN'},
                   'verification_evidence': {'render': stage+'.desired.json'}, 'missing_inputs': []}
        for dep in [o for o in rows if o['kind'] == 'Deployment']:
            release['images'][dep['metadata']['name']] = {'ecr_digest': dep['spec']['template']['spec']['containers'][0]['image'].split('@')[1], 'harbor_digest': None, 'platform': 'linux/amd64'}
        need(not validator.validate(release, 'cloud'), 'FINAL_RELEASE_RECORD_FORMAT_REQUIRED')
        for suffix, value in [('source', src), ('desired', {'apiVersion': 'v1', 'kind': 'List', 'items': rows}), ('release', release)]:
            write(out/(stage+'.'+suffix+'.json'), value)
        sources[stage], objects[stage], releases[stage] = src, {identity(o): digest(o) for o in rows}, release
    write(out/'inputs.private.json', c)
    receipt = {'status': 'PREPARED_FINAL_CLOUD_SOURCE_AND_RELEASE_NOT_APPLIED', 'sources': sources, 'objects': objects,
               'release_hashes': {s: digest(r) for s, r in releases.items()}, 'input_sha256': digest(c),
               'origin': 'https://'+route, 'argo_origin': 'https://openshift-gitops-server-openshift-gitops.'+c['ingress_domain'],
               'api_calls': 0, 'sql_calls': 0, 'application_started': False, 'recreate_invoked': False}
    write(out/'package.json', receipt)
    return receipt


def package(path, stage):
    p, c = read(path/'package.json'), read(path/'inputs.private.json')
    validate_inputs(c)
    need(digest(c) == p['input_sha256'], 'PACKAGE_INPUT_CHANGED')
    source, desired, release = (read(path/(stage+'.'+suffix+'.json')) for suffix in ('source', 'desired', 'release'))
    need(source == p['sources'][stage] and digest(release) == p['release_hashes'][stage]
         and {identity(o): digest(o) for o in desired['items']} == p['objects'][stage], 'PACKAGE_CONTENT_CHANGED')
    return p, c, source, desired, release


def normalize_source(source):
    value = copy.deepcopy(source)
    for p in value.get('kustomize', {}).get('patches', []):
        if p.get('target', {}).get('group') == '':
            del p['target']['group']
        if isinstance(p.get('patch'), str):
            p['patch'] = json.loads(p['patch'])
    return value


def current_app(app):
    m, spec = app.get('metadata', {}), app.get('spec', {})
    need(m.get('name') == APP and m.get('namespace') == ARGO and m.get('uid') and m.get('resourceVersion')
         and m.get('annotations', {}).get('seokpan.io/bootstrap-owner') == 'cloud-app-held'
         and not m.get('deletionTimestamp') and not m.get('ownerReferences') and not m.get('finalizers'), 'OWNED_CLOUD_APP_REQUIRED')
    need(spec.get('project') == APP and spec.get('destination') == {'server': 'https://kubernetes.default.svc', 'namespace': NS}
         and not spec.get('sources') and spec.get('source', {}).get('repoURL') == REPO, 'EXPECTED_APP_SCOPE_REQUIRED')
    need('automated' not in spec.get('syncPolicy', {}) and not app.get('operation')
         and app.get('status', {}).get('operationState', {}).get('phase') not in ('Running', 'Terminating'), 'MANUAL_IDLE_APP_REQUIRED')
    need(not app.get('status', {}).get('conditions'), 'ARGO_CONDITIONS_MUST_BE_RESOLVED')
    need({'Prune=false', 'Delete=false', 'FailOnSharedResource=true'} <= set(spec.get('syncPolicy', {}).get('syncOptions', [])), 'EXISTING_SYNC_PROTECTION_REQUIRED')
    return m


def oc_client(c, kubeconfig, oc, runner=subprocess.run):
    read_config = Path(kubeconfig)
    need(read_config.is_file() and not read_config.is_symlink(), 'PERSONAL_KUBECONFIG_REQUIRED')
    if os.name == 'posix':
        need(read_config.stat().st_uid == os.getuid() and not stat.S_IMODE(read_config.stat().st_mode) & 0o077,
             'PRIVATE_PERSONAL_KUBECONFIG_REQUIRED')
    def call(*args):
        r = runner([str(oc), '--kubeconfig='+str(kubeconfig), '--request-timeout=20s', *args], capture_output=True, timeout=35)
        need(r.returncode == 0, 'CURRENT_CLUSTER_COMMAND_FAILED_NO_RETRY')
        return r.stdout
    cfg = json.loads(call('config', 'view', '--minify', '-o', 'json'))['clusters'][0]['cluster']
    need(cfg.get('server', '').rstrip('/') == c['api_url'].rstrip('/') and not cfg.get('insecure-skip-tls-verify'), 'NEW_API_AND_TLS_TRUST_REQUIRED')
    need(call('whoami').strip() == b'tjung03', 'PERSONAL_TJUNG03_REQUIRED')
    ns = json.loads(call('get', 'namespace', NS, '-o', 'json'))
    need(ns['metadata']['uid'] == c['namespace_uid'] and not ns['metadata'].get('deletionTimestamp'), 'CURRENT_NAMESPACE_UID_REQUIRED')
    for kind, names in PROTECTED.items():
        for name in names:
            # Metadata only: never read Secret data into the launch process.
            uid = call('get', kind, name, '-n', NS, '-o', 'jsonpath={.metadata.uid}').decode().strip()
            need(uid == c['protected_uids'][kind+'/'+name], 'CURRENT_PROTECTED_UID_REQUIRED')
    return call


def evidence(c, e, stage):
    handoff, sql, target = (read(e[k]) for k in ('final_data_handoff', 'purpose_sql_receipt', 'purpose_sql_target'))
    need(handoff.get('accepted_by') == 'tjung03' and handoff.get('final_import_compare_and_schema_handoff_accepted') is True
         and handoff.get('source_writer_hold_accepted') is True and handoff.get('reported_schema_revision') == '20260902_0002'
         and ref(handoff.get('final_import_evidence_ref')) and ref(handoff.get('source_writer_hold_evidence_ref')), 'FINAL_DATA_AND_OLD_WRITERS_HOLD_REQUIRED')
    need(sql.get('status') == 'PASS_POST_IMPORT_PURPOSE_READS_NOT_APP_ACTIVATION'
         and sql.get('cluster_id') == c['cluster_id'] and sql.get('new_cluster_bound') is True, 'CURRENT_PURPOSE_SQL_PASS_REQUIRED')
    need(target.get('cluster_id') == c['cluster_id'] and target.get('namespace_uid') == c['namespace_uid']
         and target.get('secret_uid') == c['protected_uids']['secret/backend-db-runtime']
         and target.get('ca_uid') == c['protected_uids']['configmap/backend-database-ca'], 'SQL_TARGET_SAME_PROTECTED_INPUTS_REQUIRED')
    held = read(target['held_bundle'])
    need(hashlib.sha256(Path(target['held_bundle']).read_bytes()).hexdigest() == target['held_bundle_sha256'], 'SQL_HELD_INPUT_HASH_REQUIRED')
    configs = [o['data'] for o in held['items'] if o.get('kind') == 'ConfigMap' and 'SEOKPAN_DATABASE_EXPECTED_HOST' in o.get('data', {})]
    need(len(configs) == 1 and configs[0]['SEOKPAN_DATABASE_EXPECTED_HOST'] == c['database_host'], 'SAME_SQL_DATABASE_HOST_REQUIRED')
    need(e.get('migration_current') == '20260902_0002' and ref(e.get('migration_current_ref'))
         and e.get('captured_transition_accepted') is True and ref(e.get('captured_transition_ref'))
         and e.get('pool_candidate_accepted') is True, 'MIGRATION_CURRENT_LIFECYCLE_AND_POOL_ACCEPTANCE_REQUIRED')
    if stage != 'single':
        prior = read(e['prior_readback'])
        previous = 'single' if stage == 'shared' else 'shared'
        need(prior.get('status') == 'PASS_CLOUD_STAGE_READY_NOT_BUSINESS_ACCEPTANCE' and prior.get('stage') == previous
             and prior.get('cluster_id') == c['cluster_id'] and ref(e.get('prior_normal_business_ref')), 'PREVIOUS_STAGE_READY_AND_BUSINESS_REQUIRED')
    if stage == 'target':
        need(e.get('shared_state_accepted') is True and ref(e.get('shared_state_ref')), 'SHARED_STATE_ACCEPTANCE_REQUIRED')


def stage_app(path, stage, e, kubeconfig, oc, receipt, runner=subprocess.run):
    p, c, source, _, _ = package(path, stage)
    evidence(c, e, stage)
    budget = budget_check(c, dt.datetime.now(dt.timezone.utc))
    call = oc_client(c, kubeconfig, oc, runner)
    app = json.loads(call('get', 'application', APP, '-n', ARGO, '-o', 'json'))
    m = current_app(app)
    need(normalize_source(app['spec']['source']) != normalize_source(source), 'SOURCE_ALREADY_STAGED_RECONCILE_INSTEAD_OF_RETRY')
    if stage == 'single':
        for name in ('backend', 'frontend'):
            raw = call('get', 'deployment', name, '-n', NS, '--ignore-not-found=true', '-o', 'json')
            dep = json.loads(raw) if raw.strip() else None
            need(dep is None or dep['spec'].get('replicas') == 0, 'INITIAL_DEPLOYMENTS_MUST_BE_HELD')
    else:
        prior = read(e['prior_readback'])
        need(m['uid'] == prior['application_uid'] and normalize_source(app['spec']['source']) == normalize_source(p['sources'][prior['stage']]), 'PREVIOUS_STAGE_SOURCE_UNCHANGED_REQUIRED')
    patch = [{'op': 'test', 'path': '/metadata/uid', 'value': m['uid']},
             {'op': 'test', 'path': '/metadata/resourceVersion', 'value': m['resourceVersion']},
             {'op': 'test', 'path': '/spec', 'value': app['spec']},
             {'op': 'replace', 'path': '/spec/source', 'value': source}]
    marker = receipt.with_name(receipt.name+'.STARTED_NO_AUTOMATIC_RETRY.json')
    write(marker, {'cluster_id': c['cluster_id'], 'app_uid': m['uid'], 'stage': stage, 'mutation': 'APPLICATION_SOURCE_ONLY'})
    patch_path = receipt.with_name(receipt.name+'.patch.private.json')
    write(patch_path, patch)
    call('patch', 'application', APP, '-n', ARGO, '--type=json', '--patch-file='+str(patch_path))
    after = json.loads(call('get', 'application', APP, '-n', ARGO, '-o', 'json'))
    current_app(after)
    need(after['metadata']['uid'] == m['uid'] and normalize_source(after['spec']['source']) == normalize_source(source), 'STAGED_SOURCE_READBACK_REQUIRED')
    baseline = {'origin': p['argo_origin'], 'application_uid': m['uid'], 'revision': GITOPS,
                'source': normalize_source(source), 'objects': p['objects'][stage], 'stage': stage,
                'not_before': c['window']['not_before'], 'not_after': c['window']['not_after']}
    browser = (HERE/'compare-and-sync.js').read_text(encoding='utf-8').replace('__LAUNCH_BASELINE__', json.dumps(baseline))
    browser_path = receipt.with_name(receipt.name+'.argo-console.js')
    with open(browser_path, 'x', encoding='utf-8', newline='\n') as stream:
        stream.write(browser)
    return {'status': 'PASS_CLOUD_SOURCE_STAGED_NO_SYNC', 'stage': stage, 'cluster_id': c['cluster_id'],
            'application_uid': m['uid'], 'source_sha256': digest(source), 'console_file': str(browser_path),
            'sync_started': False, 'application_started': False, 'budget_estimate': budget}


def readback(path, stage, kubeconfig, oc, runner=subprocess.run):
    _, c, source, desired, _ = package(path, stage)
    call = oc_client(c, kubeconfig, oc, runner)
    app = json.loads(call('get', 'application', APP, '-n', ARGO, '-o', 'json'))
    m = current_app(app)
    status = app.get('status', {})
    need(normalize_source(app['spec']['source']) == normalize_source(source)
         and normalize_source(status.get('sync', {}).get('comparedTo', {}).get('source', {})) == normalize_source(source)
         and status.get('sync', {}).get('revision') == GITOPS
         and status.get('sync', {}).get('status') == 'Synced' and status.get('health', {}).get('status') == 'Healthy', 'STAGE_NOT_SYNCED_HEALTHY_NO_RETRY')
    actual = {(r.get('group') or '', r['kind'], r['namespace'], r['name']) for r in status.get('resources', [])}
    expected = {(o['apiVersion'].split('/')[0] if '/' in o['apiVersion'] else '', o['kind'], NS, o['metadata']['name']) for o in desired['items']}
    need(actual == expected, 'SERVER_RESOURCE_INVENTORY_REQUIRED')
    for name, count in zip(('backend', 'frontend'), STAGES[stage]):
        dep = json.loads(call('get', 'deployment', name, '-n', NS, '-o', 'json'))
        d = dep.get('status', {})
        need(dep['spec'].get('replicas') == count and d.get('observedGeneration') == dep['metadata']['generation']
             and all(d.get(k, 0) == count for k in ('updatedReplicas', 'availableReplicas', 'readyReplicas', 'replicas')), 'CURRENT_REPLICA_READINESS_REQUIRED')
        wanted, = [o for o in desired['items'] if o['kind'] == 'Deployment' and o['metadata']['name'] == name]
        live, selected = dep['spec']['template']['spec'], wanted['spec']['template']['spec']
        need(live.get('serviceAccountName') == selected['serviceAccountName']
             and [(x['name'], x['image'], x.get('envFrom'), x.get('env'), x.get('command'), x.get('args')) for x in live['containers']]
             == [(x['name'], x['image'], x.get('envFrom'), x.get('env'), x.get('command'), x.get('args')) for x in selected['containers']], 'CURRENT_DEPLOYED_INPUTS_REQUIRED')
    for name in ('frontend', 'backend-api', 'backend-ws'):
        route = json.loads(call('get', 'route', name, '-n', NS, '-o', 'json'))
        need(route['spec'].get('host') == 'seokpan.'+c['ingress_domain'] and
             any(any(x.get('type') == 'Admitted' and x.get('status') == 'True' for x in i.get('conditions', []))
                 for i in route.get('status', {}).get('ingress', [])), 'MATCHED_HOST_ROUTES_NOT_ADMITTED')
    return {'status': 'PASS_CLOUD_STAGE_READY_NOT_BUSINESS_ACCEPTANCE', 'cluster_id': c['cluster_id'],
            'application_uid': m['uid'], 'stage': stage, 'source_sha256': digest(source),
            'business_accepted': False, 'sql_repeated': False, 'sync_started': False}


def measurement(path, stage, ready, business_ref):
    p, c, _, _, r = package(path, stage)
    need(ready.get('status') == 'PASS_CLOUD_STAGE_READY_NOT_BUSINESS_ACCEPTANCE'
         and ready.get('cluster_id') == c['cluster_id'] and ready.get('stage') == stage
         and ready.get('source_sha256') == digest(p['sources'][stage]) and ref(business_ref), 'SAME_READY_RELEASE_AND_NORMAL_BUSINESS_REQUIRED')
    return {'schema': 1, 'run_id': c['run_id']+'-'+stage+'-normal', 'target_origin': p['origin'],
            'profile': {'clients': 6, 'warmup_seconds': 60, 'measurement_seconds': 180},
            'release': {'app_source_sha': APP_SHA, 'backend_digest': r['images']['backend']['ecr_digest'], 'frontend_digest': r['images']['frontend']['ecr_digest']},
            'runtime': {'replicas': dict(zip(('backend', 'frontend'), STAGES[stage])), 'pool_size': 2, 'max_overflow': 0,
                        'pool_timeout_seconds': 5, 'config_revision': r['revisions']['config'], 'schema_revision': '20260902_0002'},
            'evidence': {'normal_business_ref': business_ref, 'release_binding_ref': r['release_id'], 'run_window_ref': c['budget']['estimate_ref']},
            'window': c['window']}


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    prep = sub.add_parser('prepare')
    for flag in ('source', 'inputs', 'out', 'kustomize'):
        prep.add_argument('--'+flag, type=Path, required=True)
    cost = sub.add_parser('budget')
    cost.add_argument('--inputs', type=Path, required=True)
    cost.add_argument('--receipt', type=Path, required=True)
    inspect = sub.add_parser('inspect')
    inspect.add_argument('--config', type=Path, required=True)
    inspect.add_argument('--receipt', type=Path, required=True)
    for name in ('stage', 'readback', 'measurement'):
        cmd = sub.add_parser(name)
        cmd.add_argument('--package', type=Path, required=True)
        cmd.add_argument('--stage', choices=STAGES, required=True)
        cmd.add_argument('--receipt', type=Path, required=True)
        if name in ('stage', 'readback'):
            cmd.add_argument('--execute', action='store_true')
            cmd.add_argument('--kubeconfig', type=Path)
            cmd.add_argument('--oc', default='oc')
        if name == 'stage':
            cmd.add_argument('--evidence', type=Path)
        if name == 'measurement':
            cmd.add_argument('--readback', type=Path, required=True)
            cmd.add_argument('--normal-business-ref', required=True)
    args = parser.parse_args()
    try:
        if args.command == 'inspect':
            config = read(args.config)
            paths = {'lifecycle_config': args.config, 'recreate_inputs': config.get('create', {}).get('var_file'),
                     'last_platform_handoff': config.get('create', {}).get('last_platform_handoff')}
            files = {key: {'path': str(value) if value else None,
                           'exists': bool(value and Path(value).is_file() and not Path(value).is_symlink())}
                     for key, value in paths.items()}
            r = {'status': 'COLLECTED_EXISTING_CONTROLLER_PATHS_NO_NETWORK', 'files': files,
                 'source_cluster_still_in_local_config': config.get('current_cluster_id') == config.get('source_cluster_id'),
                 'required_before_create': ['CURRENT_FOUNDATION_INPUT_OUTPUT_PATHS', 'DATA_EXECUTION_INPUT_PATHS', 'CURRENT_COST_ESTIMATE_AND_START_END'],
                 'required_after_create': ['NEW_ID_API_INGRESS_NAMESPACE_PROTECTED_UIDS', 'FINAL_DATA_SQL_SCHEMA_AND_TRANSITION_RESULTS'],
                 'private_values_printed': False, 'resource_mutations': 0, 'api_calls': 0, 'recreate_invoked': False}
            write(args.receipt, r)
            print(json.dumps(r, indent=2))
            return 0
        if args.command == 'budget':
            r = budget_check(read(args.inputs))
            write(args.receipt, {'status': 'PASS_LOCAL_BUDGET_ESTIMATE_NOT_LIVE_BILLING', **r, 'resource_mutations': 0})
            print(json.dumps({'status': 'PASS_LOCAL_BUDGET_ESTIMATE_NOT_LIVE_BILLING', **r}))
            return 0
        if args.command == 'prepare':
            r = prepare(args.source, args.out, read(args.inputs), args.kustomize)
            print(json.dumps({'status': r['status'], 'api_calls': 0, 'application_started': False}))
            return 0
        need(not args.receipt.exists(), 'NEW_RECEIPT_REQUIRED')
        if args.command == 'measurement':
            r = measurement(args.package, args.stage, read(args.readback), args.normal_business_ref)
        elif not args.execute:
            package(args.package, args.stage)
            r = {'status': 'PASS_LOCAL_LAUNCH_PACKAGE_ONLY_NOT_EXECUTED', 'api_calls': 0, 'application_started': False}
        else:
            need(args.kubeconfig, 'CURRENT_PERSONAL_KUBECONFIG_REQUIRED')
            if args.command == 'stage':
                need(args.evidence, 'CURRENT_DATA_EXECUTION_EVIDENCE_REQUIRED')
                r = stage_app(args.package, args.stage, read(args.evidence), args.kubeconfig, args.oc, args.receipt)
            else:
                r = readback(args.package, args.stage, args.kubeconfig, args.oc)
        write(args.receipt, r)
        print(json.dumps({'status': r.get('status', 'PREPARED_MEASUREMENT_PLAN_NOT_EXECUTED'), 'automatic_retry': False}))
        return 0
    except Exception as error:
        print(json.dumps({'status': 'STOP_CLOUD_LAUNCH', 'reason': str(error) if type(error) is Stop else type(error).__name__,
                          'automatic_retry': False, 'raw_output_printed': False, 'recreate_invoked': False}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
