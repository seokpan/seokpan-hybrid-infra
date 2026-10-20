"""Bind new Data Pod -> SQL/Schema/mode/Pool -> reviewed Cloud Source.

No ROSA, IAM, Foundation, Import, migration upgrade, automatic Sync or deletion.
Default performs local input checking only. All cluster actions need --execute.
"""
import argparse
import base64
import copy
import datetime as dt
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import time
from urllib.parse import urlsplit, unquote

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
NS = 'seokpan-cloud'
SA = 'seokpan-cloud-runtime'
WAIT = ['python', '-u', '-c', 'import time\nwhile True: time.sleep(3600)']
FIELDS = {'schema', 'lifecycle_config', 'launch_inputs', 'final_data_handoff',
          'schema_credentials_file', 'runtime_inputs_file', 'gitops_source', 'kustomize', 'oc', 'personal_kubeconfig'}


class Stop(RuntimeError):
    pass


def need(ok, code):
    if not ok:
        raise Stop(code)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def canon(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def private(path):
    path = Path(path)
    need(not path.is_symlink() and path.is_file() and path.stat().st_size <= 8*1024*1024,
         'BOUNDED_REGULAR_INPUT_REQUIRED')
    if os.name == 'posix':
        info = path.stat()
        need(info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) in (0o400, 0o600),
             'PRIVATE_OWNED_INPUT_REQUIRED')
    return path.read_bytes()


def read(path):
    def unique(pairs):
        d = {}
        for k, v in pairs:
            need(k not in d, 'DUPLICATE_JSON_KEY')
            d[k] = v
        return d
    return json.loads(private(path), object_pairs_hook=unique)


def write(path, value):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(canon(value)+b'\n')


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def helpers():
    return (module('prestart_launch', ROOT/'tools/cloud-launch/cloud_launch.py'),
            module('prestart_sql', ROOT/'tools/cloud-readiness/post_import_new_cluster.py'))


def check_config(c):
    need(set(c) == FIELDS and c.get('schema') == 1, 'EXACT_PRESTART_CONFIG_FIELDS_REQUIRED')
    missing = []
    for key in sorted(FIELDS-{'schema'}):
        value = c[key]
        if not isinstance(value, str) or not value.strip():
            missing.append(key)
        elif not Path(value).exists():
            missing.append(key)
    return missing


def pod_spec(name, run, image):
    return {'apiVersion': 'v1', 'kind': 'Pod', 'metadata': {'name': name, 'namespace': NS,
            'labels': {'seokpan.io/cloud-prestart': run}}, 'spec': {
        'serviceAccountName': SA, 'automountServiceAccountToken': False, 'enableServiceLinks': False,
        'restartPolicy': 'Never', 'terminationGracePeriodSeconds': 5,
        'nodeSelector': {'node-role.kubernetes.io/worker': ''},
        'affinity': {'nodeAffinity': {'requiredDuringSchedulingIgnoredDuringExecution': {'nodeSelectorTerms': [
            {'matchExpressions': [{'key': 'node-role.kubernetes.io/'+k, 'operator': 'DoesNotExist'}
                                  for k in ('infra', 'master', 'control-plane')]}]}}},
        'securityContext': {'runAsNonRoot': True, 'seccompProfile': {'type': 'RuntimeDefault'}},
        'containers': [{'name': 'db-admin', 'image': image, 'imagePullPolicy': 'IfNotPresent', 'command': WAIT,
            'securityContext': {'runAsNonRoot': True, 'allowPrivilegeEscalation': False,
                                'readOnlyRootFilesystem': True, 'capabilities': {'drop': ['ALL']}},
            'resources': {'requests': {'cpu': '25m', 'memory': '64Mi'}, 'limits': {'cpu': '250m', 'memory': '256Mi'}},
            'volumeMounts': [{'name': k, 'mountPath': p, 'readOnly': True} for k, p in
                             (('rds-ca', '/run/rds-ca'), ('redis-ca', '/run/redis-ca'))]}],
        'volumes': [{'name': k, 'configMap': {'name': n, 'items': [{'key': 'ca.crt', 'path': 'ca.crt'}]}}
                    for k, n in (('rds-ca', 'backend-database-ca'), ('redis-ca', 'backend-redis-ca'))]}}


def validate_pod(live, wanted, uid=None):
    m, s, w = live.get('metadata', {}), live.get('spec', {}), wanted['spec']
    need(m.get('name') == wanted['metadata']['name'] and m.get('namespace') == NS
         and m.get('labels', {}).get('seokpan.io/cloud-prestart') == wanted['metadata']['labels']['seokpan.io/cloud-prestart']
         and m.get('uid') and (uid is None or uid == m['uid']) and not m.get('deletionTimestamp'),
         'EXACT_NEW_POD_IDENTITY_REQUIRED')
    need(s.get('serviceAccountName') == SA and s.get('automountServiceAccountToken') is False
         and not s.get('imagePullSecrets') and not s.get('initContainers') and not s.get('ephemeralContainers')
         and s.get('restartPolicy') == 'Never' and s.get('nodeSelector') == w['nodeSelector']
         and s.get('affinity') == w['affinity'] and not s.get('activeDeadlineSeconds'), 'POD_EXECUTION_CONTRACT_CHANGED')
    need(not any(s.get(k, False) for k in ('hostNetwork', 'hostPID', 'hostIPC', 'shareProcessNamespace')),
         'POD_HOST_NAMESPACE_REJECTED')
    psc = s.get('securityContext', {})
    need(psc.get('runAsNonRoot') is True and psc.get('runAsUser', 1) > 0
         and psc.get('seccompProfile') == {'type': 'RuntimeDefault'}, 'POD_SECURITY_CHANGED')
    rows = s.get('containers', [])
    need(len(rows) == 1, 'ONE_IDLE_CONTAINER_REQUIRED')
    c, wc = rows[0], w['containers'][0]
    for k in ('name', 'image', 'command', 'resources'):
        need(c.get(k) == wc[k], 'POD_CONTAINER_CHANGED')
    need(not any(c.get(k) for k in ('env', 'envFrom', 'args', 'lifecycle', 'startupProbe', 'readinessProbe', 'livenessProbe')),
         'IDLE_CONTAINER_ONLY_REQUIRED')
    cs = c.get('securityContext', {})
    need(cs.get('runAsNonRoot') is True and cs.get('runAsUser', 1) > 0
         and cs.get('readOnlyRootFilesystem') is True and cs.get('allowPrivilegeEscalation') is False
         and not cs.get('privileged') and cs.get('capabilities') == {'drop': ['ALL']}, 'CONTAINER_SECURITY_CHANGED')
    need(c.get('volumeMounts') == wc['volumeMounts'], 'PUBLIC_CA_MOUNTS_CHANGED')
    vols = copy.deepcopy(s.get('volumes', []))
    for v in vols:
        if 'configMap' in v:
            need(v['configMap'].pop('defaultMode', 420) == 420, 'CA_DEFAULT_MODE_CHANGED')
    need(vols == w['volumes'], 'ONLY_PUBLIC_CA_VOLUMES_ALLOWED')
    need(live.get('status', {}).get('phase') not in ('Failed', 'Succeeded'), 'IDLE_POD_TERMINATED')
    return m['uid']


def pool_choice(sql):
    fresh = sql.get('data_mode') == 'fresh_test'
    expected = ('PASS_FRESH_TEST_PURPOSE_READS_NOT_APP_ACTIVATION' if fresh
                else 'PASS_POST_IMPORT_PURPOSE_READS_NOT_APP_ACTIVATION')
    need(sql.get('status') == expected, 'PURPOSE_SQL_PASS_REQUIRED')
    rows = sql.get('accounts', [])
    need(len(rows) == 2 and {r['account'] for r in rows} == {'identity_svc', 'game_svc'}, 'TWO_ACCOUNT_RESULTS_REQUIRED')
    limits = {r.get('max_connections') for r in rows}
    if fresh:
        need(len(limits) == 1 and all(type(v) is int and v >= 43 for v in limits),
             'CURRENT_GLOBAL_LIMIT_AND_INITIAL_POOL_HEADROOM_REQUIRED')
        maximum = next(iter(limits))
        need(all(r.get('tls') is True for r in rows), 'CURRENT_PURPOSE_TLS_REQUIRED')
    else:
        maximum = 68
    need(all(r['status'] == 'PASS_PURPOSE_ACCOUNT_READ_ONLY' and r.get('max_connections') == maximum
             and r.get('account_limit_covers_candidate_14') is True for r in rows), 'ACCOUNT_BUDGET_NOT_ACCEPTED')
    return {'status': 'PASS_INITIAL_POOL_SELECTION_NOT_RUNTIME_TEST', 'pool_size': 2, 'max_overflow': 0,
            'timeout_seconds': 5, 'processes_per_backend': 1, 'peak_backend_pods': 7,
            'engines_per_process': 2, 'per_service_peak': 14, 'app_peak': 28, 'reserved': 10,
            'candidate_total': 38, 'accepted_database_limit': maximum, 'runtime_lifetime_tested': False,
            'data_mode': 'fresh_test' if fresh else 'migrated'}


class Flow:
    def __init__(self, config, work, runner=subprocess.run, resume_readonly=False, prepare_data_pod=False):
        self.c, self.work, self.runner = config, Path(work), runner
        self.resume_readonly = resume_readonly
        self.L, self.Q = helpers()
        self.lifecycle = read(config['lifecycle_config'])
        self.platform = read(self.lifecycle['create']['last_platform_handoff'])
        self.cluster = self.lifecycle.get('current_cluster_id')
        need(self.cluster and self.cluster != self.lifecycle.get('source_cluster_id')
             and self.platform.get('cluster_id') == self.cluster and self.platform.get('state') == 'ready',
             'NEW_READY_PLATFORM_REQUIRED_NO_RECREATION_BY_THIS_TOOL')
        self.handoff = read(config['final_data_handoff'])
        contract = self.Q.load_contract()
        self.pod_prepare_only = prepare_data_pod
        if prepare_data_pod:
            contract.fresh_target_handoff(self.handoff)
        else:
            contract.accepted_handoff(self.handoff)
        self.launch = read(config['launch_inputs'])
        self.L.budget_check(self.launch, dt.datetime.now(dt.timezone.utc))
        self.schema = read(config['schema_credentials_file'])
        need(set(self.schema) == {'schema', 'database_url'} and self.schema['schema'] == 1,
             'EXACT_PROTECTED_SCHEMA_URL_INPUT_REQUIRED')
        url = urlsplit(self.schema['database_url'])
        need(url.scheme == 'mysql+asyncmy' and url.hostname == self.launch.get('database_host')
             and url.port == 3306 and unquote(url.path.lstrip('/')) == 'stone_game'
             and unquote(url.username or '') == 'db_admin' and url.password
             and not url.query and not url.fragment, 'PROTECTED_SCHEMA_TARGET_REQUIRED')
        self.work.mkdir(mode=0o700, parents=False, exist_ok=True)
        need(not self.work.is_symlink(), 'REGULAR_WORK_DIRECTORY_REQUIRED')
        if os.name == 'posix':
            info = self.work.stat()
            need(info.st_uid == os.getuid() and not stat.S_IMODE(info.st_mode) & 0o077, 'PRIVATE_WORK_DIRECTORY_REQUIRED')
        fingerprint = digest(canon({'config': config, 'launch': self.launch, 'handoff': contract.handoff_binding(self.handoff),
            'schema_hash': digest(private(config['schema_credentials_file'])),
            'runtime_inputs_hash': digest(private(config['runtime_inputs_file'])),
            'lifecycle_hash': digest(private(config['lifecycle_config'])), 'platform': self.platform,
            'implementation': {p.name: digest(p.read_bytes()) for p in
                (HERE/'prestart.py', HERE/'runtime_probe.py', ROOT/'tools/cloud-launch/cloud_launch.py',
                 ROOT/'tools/cloud-readiness/post_import_new_cluster.py')}}))
        binding = {'cluster_id': self.cluster, 'input_sha256': fingerprint}
        bind = self.work/'binding.json'
        if bind.exists():
            need(read(bind) == binding, 'EXISTING_RUN_BINDING_CHANGED_STOP_NO_REUSE')
        else:
            write(bind, binding)
        self.binding = binding
        if not prepare_data_pod:
            self.seal_handoff()
        prefix = [config['oc'], '--kubeconfig='+config['personal_kubeconfig'], '--request-timeout=20s']
        def call(*args, payload=None):
            p = runner(prefix+list(args), input=payload, capture_output=True, timeout=90 if args[0] == 'exec' else 35)
            need(p.returncode == 0, 'OC_READ_OR_WRITE_FAILED_PRESERVE_RUN_NO_AUTOMATIC_RETRY')
            return p.stdout
        self.call = call

    def seal_handoff(self):
        self.Q.load_contract().accepted_handoff(self.handoff)
        path = self.work/'data-handoff.accepted.json'
        if path.exists():
            need(read(path) == self.handoff, 'ACCEPTED_DATA_HANDOFF_CHANGED_REQUIRES_RECONCILIATION')
        else:
            write(path, self.handoff)

    def prepare_data_pod(self):
        need(self.pod_prepare_only and self.handoff.get('data_mode') == 'fresh_test', 'FRESH_POD_PREPARATION_ONLY_REQUIRED')
        self.step('runtime-inputs', self.supply_inputs, reconcile=True)
        c = self.step('new-inputs', self.bind_inputs, readonly=True)['inputs']
        self.L.oc_client(c, self.c['personal_kubeconfig'], self.c['oc'], self.runner)
        need(self.handoff['target_database_host'] == c['database_host']
             and self.handoff['target_redis_host'] == c['redis_host'], 'FRESH_PREPARED_TARGET_MISMATCH')
        self.step('local-package', lambda: self.package(c), reconcile=True)
        target = self.step('new-pod', lambda: self.data_pod(c), reconcile=True)['target']
        path = self.work/'prepared-data-target.json'
        if path.exists(): need(read(path) == target, 'PREPARED_DATA_TARGET_CHANGED')
        else: write(path, target)
        return {'status': 'PASS_NEW_DATA_POD_READY_FOR_ACCOUNT_SCHEMA_SETUP',
                'cluster_id': self.cluster, 'target_file': str(path),
                'sql_calls': 0, 'schema_changes': 0, 'application_started': False,
                'next': 'EXISTING_AUTHORIZED_ACCOUNT_SCHEMA_SETUP_THEN_SAME_RUN_STAGE'}

    def supply_inputs(self):
        private(self.c['personal_kubeconfig'])
        cfg = json.loads(self.call('config', 'view', '--minify', '-o', 'json'))['clusters'][0]['cluster']
        need(cfg.get('server', '').rstrip('/') == self.platform['api_url'].rstrip('/')
             and not cfg.get('insecure-skip-tls-verify') and self.call('whoami').strip() == b'tjung03',
             'NEW_PERSONAL_API_TLS_REQUIRED_BEFORE_SUPPLY')
        ns = json.loads(self.call('get', 'namespace', NS, '-o', 'json'))
        nsuid = ns['metadata']['uid']
        need(not ns['metadata'].get('deletionTimestamp'), 'CURRENT_NAMESPACE_REQUIRED')
        deployments = json.loads(self.call('get', 'deployment', '-n', NS, '-o', 'json'))
        need(all(o['spec'].get('replicas', 1) == 0 for o in deployments.get('items', [])), 'SUPPLY_ONLY_WHILE_APPS_HELD')
        app = json.loads(self.call('get', 'application', self.L.APP, '-n', self.L.ARGO, '-o', 'json'))
        self.L.current_app(app)
        rows = read(self.c['runtime_inputs_file']).get('items', [])
        expected = {('Secret', 'backend-db-runtime'): {'SEOKPAN_IDENTITY_DATABASE_URL', 'SEOKPAN_GAME_DATABASE_URL'},
                    ('Secret', 'backend-redis-runtime'): {'SEOKPAN_REDIS_AUTH_TOKEN'},
                    ('ConfigMap', 'backend-database-ca'): {'ca.crt'}, ('ConfigMap', 'backend-redis-ca'): {'ca.crt'}}
        mapped = {}
        for o in rows:
            need(set(o) <= {'apiVersion', 'kind', 'metadata', 'type', 'data'} and o.get('apiVersion') == 'v1'
                 and set(o.get('metadata', {})) == {'name', 'namespace'} and o['metadata']['namespace'] == NS,
                 'EXACT_PROTECTED_OBJECT_STRUCTURE_REQUIRED')
            key = (o['kind'], o['metadata']['name'])
            need(key in expected and key not in mapped and set(o.get('data', {})) == expected[key]
                 and all(isinstance(v, str) and v for v in o['data'].values()), 'EXACT_FOUR_RUNTIME_INPUTS_REQUIRED')
            if o['kind'] == 'Secret':
                need(o.get('type') == 'Opaque', 'OPAQUE_RUNTIME_SECRET_REQUIRED')
                for value in o['data'].values():
                    base64.b64decode(value, validate=True)
            mapped[key] = o
        need(set(mapped) == set(expected), 'EXACT_FOUR_RUNTIME_INPUTS_REQUIRED')
        # Validate both runtime URLs before modifying any object.
        for field, user in (('SEOKPAN_IDENTITY_DATABASE_URL', 'identity_svc'), ('SEOKPAN_GAME_DATABASE_URL', 'game_svc')):
            url = urlsplit(base64.b64decode(mapped[('Secret', 'backend-db-runtime')]['data'][field]).decode())
            need(url.scheme == 'mysql+asyncmy' and url.hostname == self.launch['database_host'] and url.port == 3306
                 and unquote(url.path.lstrip('/')) == 'stone_game' and unquote(url.username or '') == user
                 and url.password and not url.query and not url.fragment, 'RUNTIME_PURPOSE_URL_TARGET_REQUIRED')
        changes = []
        for (kind, name), desired in mapped.items():
            live = json.loads(self.call('get', kind.lower(), name, '-n', NS, '-o', 'json'))
            m = live['metadata']
            need(not m.get('deletionTimestamp') and (kind != 'Secret' or live.get('type') == 'Opaque'), 'CURRENT_RUNTIME_OBJECT_REQUIRED')
            marker = self.work/('supply-'+name+'.attempted.json')
            bound = {'binding': self.binding, 'namespace_uid': nsuid, 'uid': m['uid'], 'desired_sha256': digest(canon(desired['data']))}
            if marker.exists():
                need(read(marker) == bound and live.get('data') == desired['data'],
                     'UNCERTAIN_RUNTIME_INPUT_PATCH_NO_SECOND_WRITE')
            elif live.get('data') != desired['data']:
                now_ns = json.loads(self.call('get', 'namespace', NS, '-o', 'json'))
                need(now_ns['metadata']['uid'] == nsuid, 'NAMESPACE_UID_CHANGED_BEFORE_SUPPLY')
                write(marker, bound)
                patch = [{'op': 'test', 'path': '/metadata/uid', 'value': m['uid']},
                         {'op': 'test', 'path': '/metadata/resourceVersion', 'value': m['resourceVersion']},
                         {'op': 'replace' if 'data' in live else 'add', 'path': '/data', 'value': desired['data']}]
                # Private stdin, not process arguments or stdout.
                self.call('patch', kind.lower(), name, '-n', NS, '--type=json', '--patch-file=/dev/stdin', payload=canon(patch))
                observed = json.loads(self.call('get', kind.lower(), name, '-n', NS, '-o', 'json'))
                need(observed['metadata']['uid'] == m['uid'] and observed.get('data') == desired['data'], 'PROTECTED_SUPPLY_READBACK_REQUIRED')
                changes.append(name)
        return {'status': 'PASS_CURRENT_RUNTIME_INPUTS_CONNECTED', 'namespace_uid': nsuid,
                'data_updates': len(changes), 'unchanged_values_reused': 4-len(changes),
                'pull_secret_changes': 0, 'credential_values_printed': False}

    def step(self, name, fn, readonly=False, reconcile=False):
        done, marker = self.work/(name+'.json'), self.work/(name+'.started.json')
        if done.exists():
            value = read(done)
            need(value.get('binding') == self.binding, 'STEP_BINDING_CHANGED')
            print(json.dumps({'stage': name, 'status': 'REUSED_SUCCESS_NO_EXECUTION'}))
            return value['result']
        if marker.exists():
            need(read(marker).get('binding') == self.binding, 'ATTEMPT_BINDING_CHANGED')
            need(reconcile or (readonly and self.resume_readonly), 'PREVIOUS_ATTEMPT_REQUIRES_READ_ONLY_RECONCILIATION')
        else:
            write(marker, {'binding': self.binding, 'started_at_utc': dt.datetime.now(dt.timezone.utc).isoformat()})
        value = fn()
        need(value.get('status', '').startswith('PASS_'), 'STEP_NOT_ACCEPTED_'+name.upper())
        write(done, {'binding': self.binding, 'completed_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(), 'result': value})
        return value

    def bind_inputs(self):
        # Only target metadata is rebound; old API/UIDs cannot survive in the new package.
        private(self.c['personal_kubeconfig'])
        cfg = json.loads(self.call('config', 'view', '--minify', '-o', 'json'))
        t = cfg['clusters'][0]['cluster']
        api = self.platform['api_url'].rstrip('/')
        need(t.get('server', '').rstrip('/') == api and not t.get('insecure-skip-tls-verify'), 'NEW_API_TLS_REQUIRED')
        need(self.call('whoami').strip() == b'tjung03', 'NEW_PERSONAL_TJUNG03_LOGIN_REQUIRED')
        host = urlsplit(api).hostname
        need(host and host.startswith('api.') and host.endswith('.openshiftapps.com'), 'NEW_ROSA_API_REQUIRED')
        c = copy.deepcopy(self.launch)
        c.update(cluster_id=self.cluster, api_url=api, ingress_domain='apps.'+host[4:])
        revision = self.runner(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], capture_output=True, timeout=15)
        clean = self.runner(['git', '-C', str(ROOT), 'status', '--porcelain', '--untracked-files=no'], capture_output=True, timeout=15)
        need(revision.returncode == 0 and re.fullmatch(rb'[0-9a-f]{40}', revision.stdout.strip())
             and clean.returncode == 0 and not clean.stdout.strip(), 'CLEAN_PINNED_INFRA_CHECKOUT_REQUIRED')
        c['infra_sha'] = revision.stdout.decode().strip()
        ns = json.loads(self.call('get', 'namespace', NS, '-o', 'json'))
        need(not ns['metadata'].get('deletionTimestamp'), 'CURRENT_NAMESPACE_REQUIRED')
        c['namespace_uid'] = ns['metadata']['uid']
        c['protected_uids'] = {}
        for kind, names in self.L.PROTECTED.items():
            for name in names:
                uid = self.call('get', kind, name, '-n', NS, '-o', 'jsonpath={.metadata.uid}').decode().strip()
                c['protected_uids'][kind+'/'+name] = uid
        self.L.validate_inputs(c)
        self.L.budget_check(c, dt.datetime.now(dt.timezone.utc))
        sa = json.loads(self.call('get', 'serviceaccount', SA, '-n', NS, '-o', 'json'))
        need(not sa.get('imagePullSecrets'), 'NATIVE_PULL_RUNTIME_SA_REQUIRED_NO_TEMP_PULL_REATTACH')
        return {'status': 'PASS_NEW_CONNECTION_INPUTS_BOUND', 'inputs': c}

    def package(self, c):
        out = self.work/'package'
        if out.exists():
            need((out/'package.json').is_file(), 'PARTIAL_LOCAL_PACKAGE_REQUIRES_INSPECTION_NO_OVERWRITE')
            for stage in self.L.STAGES:
                self.L.package(out, stage)
            need(read(out/'inputs.private.json') == c, 'PARTIAL_PACKAGE_INPUT_CHANGED')
        else:
            self.L.prepare(Path(self.c['gitops_source']), out, c, Path(self.c['kustomize']))
        desired = read(out/'single.desired.json')
        held = copy.deepcopy(desired)
        for o in held['items']:
            if o['kind'] == 'Deployment':
                o['spec']['replicas'] = 0
        path = self.work/'held-bundle.json'
        if path.exists():
            need(read(path) == held, 'HELD_BUNDLE_CHANGED')
        else:
            write(path, held)
        return {'status': 'PASS_LOCAL_NEW_LAUNCH_PACKAGE', 'package': str(out)}

    def wanted(self, c):
        held = read(self.work/'held-bundle.json')
        backend, = [o for o in held['items'] if o['kind'] == 'Deployment' and o['metadata']['name'] == 'backend']
        container, = [v for v in backend['spec']['template']['spec']['containers'] if v['name'] == 'backend']
        image = container['image']
        need(re.fullmatch(r'[A-Za-z0-9./_-]+@sha256:[0-9a-f]{64}', image), 'APPROVED_BACKEND_DIGEST_REQUIRED')
        suffix = digest(canon(self.binding))[:12]
        return pod_spec('data-admin-'+suffix, suffix, image)

    def data_pod(self, c):
        wanted = self.wanted(c)
        marker = self.work/'pod-create.attempted.json'
        live_raw = self.call('get', 'pod', wanted['metadata']['name'], '-n', NS, '--ignore-not-found=true', '-o', 'json')
        live = json.loads(live_raw) if live_raw.strip() else None
        if live is None:
            need(not marker.exists(), 'CREATE_OUTCOME_UNKNOWN_OR_POD_REMOVED_NO_RECREATE')
            deployments = json.loads(self.call('get', 'deployment', '-n', NS, '-o', 'json'))
            need(all(o['spec'].get('replicas', 1) == 0 for o in deployments.get('items', [])), 'CLOUD_APPS_MUST_STAY_HELD')
            self.call('create', '--dry-run=server', '-f', '-', '-o', 'json', payload=canon(wanted))
            write(marker, {'binding': self.binding, 'desired_sha256': digest(canon(wanted))})
            live = json.loads(self.call('create', '-f', '-', '-o', 'json', payload=canon(wanted)))
        else:
            need(marker.exists() and read(marker) == {'binding': self.binding, 'desired_sha256': digest(canon(wanted))},
                 'EXISTING_POD_NOT_OWNED_BY_THIS_RUN')
        uid = validate_pod(live, wanted)
        deadline = time.monotonic()+180
        while not (live.get('status', {}).get('phase') == 'Running' and any(
                r.get('type') == 'Ready' and r.get('status') == 'True' for r in live.get('status', {}).get('conditions', []))):
            need(time.monotonic() < deadline, 'POD_READY_TIMEOUT_RESUME_READBACK_ONLY')
            time.sleep(3)
            live = json.loads(self.call('get', 'pod', wanted['metadata']['name'], '-n', NS, '-o', 'json'))
            validate_pod(live, wanted, uid)
        target = {'schema': 1, 'cluster_id': self.cluster, 'namespace': NS,
            'namespace_uid': c['namespace_uid'], 'readonly_pod_scope_accepted': True,
            'pod_name': wanted['metadata']['name'], 'pod_uid': uid, 'container': 'db-admin',
            'secret_uid': c['protected_uids']['secret/backend-db-runtime'],
            'ca_uid': c['protected_uids']['configmap/backend-database-ca'], 'oc': self.c['oc'],
            'personal_kubeconfig': self.c['personal_kubeconfig'], 'held_bundle': str(self.work/'held-bundle.json'),
            'held_bundle_sha256': digest(private(self.work/'held-bundle.json'))}
        return {'status': 'PASS_NEW_IDLE_DATA_POD_AND_TARGET', 'target': target}

    def verify_pod(self, target, c):
        live = json.loads(self.call('get', 'pod', target['pod_name'], '-n', NS, '-o', 'json'))
        validate_pod(live, self.wanted(c), target['pod_uid'])

    def sql(self, target, c):
        self.verify_pod(target, c)
        result = self.Q.execute(self.lifecycle, self.platform, target, self.handoff,
            private(self.work/'held-bundle.json'), self.Q.load_contract(),
            (ROOT/'tools/cloud-readiness/accepted_sql_probe.py').read_bytes(), self.runner)
        expected = ('PASS_FRESH_TEST_PURPOSE_READS_NOT_APP_ACTIVATION'
                    if self.handoff.get('data_mode') == 'fresh_test'
                    else 'PASS_POST_IMPORT_PURPOSE_READS_NOT_APP_ACTIVATION')
        if result.get('status') != expected:
            write(self.work/('failed-purpose-sql-'+str(time.time_ns())+'.json'), result)
            raise Stop('PURPOSE_SQL_NOT_ACCEPTED_SEE_SANITIZED_FAILURE_RECEIPT')
        return result

    def schema_mode(self, target, c):
        self.verify_pod(target, c)
        self.L.oc_client(c, self.c['personal_kubeconfig'], self.c['oc'], self.runner)
        redis = json.loads(self.call('get', 'secret', 'backend-redis-runtime', '-n', NS, '-o', 'json'))
        ca = json.loads(self.call('get', 'configmap', 'backend-database-ca', '-n', NS, '-o', 'json'))
        rca = json.loads(self.call('get', 'configmap', 'backend-redis-ca', '-n', NS, '-o', 'json'))
        need(redis['metadata']['uid'] == c['protected_uids']['secret/backend-redis-runtime']
             and ca['metadata']['uid'] == c['protected_uids']['configmap/backend-database-ca']
             and rca['metadata']['uid'] == c['protected_uids']['configmap/backend-redis-ca'], 'CURRENT_PROTECTED_UID_REQUIRED')
        payload = {'schema_url': self.schema['database_url'], 'database_host': c['database_host'],
            'redis_host': c['redis_host'], 'redis_auth': base64.b64decode(redis['data']['SEOKPAN_REDIS_AUTH_TOKEN'], validate=True).decode(),
            'database_ca_sha256': digest(ca['data']['ca.crt'].encode()), 'redis_ca_sha256': digest(rca['data']['ca.crt'].encode()),
            'data_mode': self.handoff.get('data_mode', 'migrated')}
        if payload['data_mode'] == 'fresh_test':
            payload.update({k: self.handoff[k] for k in ('fresh_test_target_accepted',
                'legacy_targets_excluded', 'target_database_host', 'target_redis_host',
                'reported_schema_revision', 'fresh_target_evidence_ref',
                'purpose_accounts_evidence_ref', 'schema_prepared_evidence_ref')})
        else:
            payload.update(final_data_accepted=self.handoff['final_import_compare_and_schema_handoff_accepted'],
                           all_old_writers_stopped=self.handoff['source_writer_hold_accepted'])
        raw = self.call('exec', '-i', '-n', NS, target['pod_name'], '-c', 'db-admin', '--', 'python', '-c',
                        (HERE/'runtime_probe.py').read_text(encoding='utf-8'), payload=canon(payload))
        result = json.loads(raw)
        if result.get('status') != 'PASS_SCHEMA_AND_CAPTURED_ADMISSION_NOT_BUSINESS':
            reason = result.get('reason', 'PROBE_FAILED')
            need(isinstance(reason, str) and re.fullmatch(r'[A-Za-z0-9_]{1,160}', reason), 'SANITIZED_PROBE_FAILURE_REQUIRED')
            write(self.work/('failed-schema-mode-'+str(time.time_ns())+'.json'), result)
            raise Stop('SCHEMA_OR_MODE_NOT_ACCEPTED:'+reason)
        return result

    def evidence(self, sql, schema, pool):
        # Refer to receipts actually produced above. Never fill true from a template.
        path = self.work/'execution-evidence.json'
        value = {'schema': 1, 'final_data_handoff': self.c['final_data_handoff'],
            'purpose_sql_receipt': str(self.work/'purpose-sql-result.json'), 'purpose_sql_target': str(self.work/'sql-target.json'),
            'migration_current': schema['schema_revision'], 'migration_current_ref': str(self.work/'schema-mode.json'),
            'captured_transition_accepted': schema['mode_admission']['status'] == 'PASS_CAPTURED_EMPTY_RUNTIME_ADMISSION',
            'captured_transition_ref': str(self.work/'schema-mode.json'),
            'pool_candidate_accepted': pool['status'] == 'PASS_INITIAL_POOL_SELECTION_NOT_RUNTIME_TEST'}
        if path.exists():
            need(read(path) == value, 'EXISTING_EXECUTION_EVIDENCE_CHANGED')
        else:
            write(path, value)
        return {'status': 'PASS_ACTUAL_EXECUTION_EVIDENCE_CONNECTED', 'path': str(path)}

    def before_sync(self):
        self.step('runtime-inputs', self.supply_inputs, reconcile=True)
        bound = self.step('new-inputs', self.bind_inputs, readonly=True)
        c = bound['inputs']
        # Recheck live identity/UIDs on resume; this is target binding, not old test repetition.
        self.L.oc_client(c, self.c['personal_kubeconfig'], self.c['oc'], self.runner)
        self.step('local-package', lambda: self.package(c), reconcile=True)
        target = self.step('new-pod', lambda: self.data_pod(c), reconcile=True)['target']
        sql = self.step('purpose-sql', lambda: self.sql(target, c), readonly=True)
        if not (self.work/'sql-target.json').exists():
            write(self.work/'sql-target.json', target)
        else:
            need(read(self.work/'sql-target.json') == target, 'SAVED_SQL_TARGET_CHANGED')
        if not (self.work/'purpose-sql-result.json').exists():
            write(self.work/'purpose-sql-result.json', sql)
        else:
            need(read(self.work/'purpose-sql-result.json') == sql, 'SAVED_SQL_RESULT_CHANGED')
        schema = self.step('schema-mode', lambda: self.schema_mode(target, c), readonly=True)
        pool = self.step('initial-pool', lambda: pool_choice(sql), reconcile=True)
        self.step('execution-evidence-step', lambda: self.evidence(sql, schema, pool), reconcile=True)
        return c

    def stage(self, stage, business=None):
        need(not getattr(self, 'pod_prepare_only', False), 'PREPARE_ONLY_CANNOT_STAGE_APPS')
        c = self.before_sync()
        e = read(self.work/'execution-evidence.json')
        if stage != 'single':
            prior = 'single' if stage == 'shared' else 'shared'
            need(business is not None, 'PREVIOUS_NORMAL_BUSINESS_RECEIPT_REQUIRED')
            b = read(business)
            ready = read(self.work/(prior+'-ready.json'))
            need(b.get('status') == 'ACCEPTED_NORMAL_CLOUD_BUSINESS' and b.get('accepted_by') == 'tjung03'
                 and b.get('cluster_id') == self.cluster and b.get('stage') == prior
                 and b.get('source_sha256') == ready['source_sha256']
                 and self.L.ref(b.get('normal_business_ref')), 'MATCHED_PREVIOUS_BUSINESS_REQUIRED')
            e.update(prior_readback=str(self.work/(prior+'-ready.json')), prior_normal_business_ref=b['normal_business_ref'])
            if stage == 'target':
                need(b.get('shared_state_accepted') is True and self.L.ref(b.get('shared_state_ref')),
                     'SHARED_STATE_RECONNECT_RESULT_REQUIRED')
                e.update(shared_state_accepted=True, shared_state_ref=b['shared_state_ref'])
        receipt = self.work/(stage+'-source.json')
        # Existing Source helper's mutation marker remains authoritative. A new
        # receipt name is not a way to replay an uncertain Source patch.
        result = self.step(stage+'-staged', lambda: self.stage_or_reconcile(stage, e, receipt, c), reconcile=True)
        if not receipt.exists():
            write(receipt, result)
        self.step('idle-pod-cleanup', self.cleanup_pod, reconcile=True)
        return {'status': 'PASS_PRESTART_TO_MANUAL_SYNC_BOUNDARY', 'stage': stage,
                'cluster_id': self.cluster, 'console_file': result['console_file'],
                'sync_started_by_this_tool': False, 'database_writes': 0,
                'next': 'EXPLICIT_ARGO_COMPARE_AND_SYNC_THEN_AFTER_SYNC', 'work': str(self.work)}

    def cleanup_pod(self):
        target = read(self.work/'new-pod.json')['result']['target']
        c = read(self.work/'new-inputs.json')['result']['inputs']
        marker = self.work/'pod-delete.attempted.json'
        raw = self.call('get', 'pod', target['pod_name'], '-n', NS, '--ignore-not-found=true', '-o', 'json')
        if not raw.strip():
            need(marker.exists(), 'POD_DISAPPEARED_BEFORE_OWN_CLEANUP_REVIEW_REQUIRED')
            return {'status': 'PASS_OWN_IDLE_POD_REMOVAL_CONFIRMED', 'pod_uid': target['pod_uid']}
        live = json.loads(raw)
        need(live.get('metadata', {}).get('uid') == target['pod_uid'], 'CLEANUP_UID_CHANGED_NO_DELETE')
        if not marker.exists():
            validate_pod(live, self.wanted(c), target['pod_uid'])
            write(marker, {'binding': self.binding, 'pod_uid': target['pod_uid']})
            options = {'apiVersion': 'v1', 'kind': 'DeleteOptions', 'preconditions': {'uid': target['pod_uid']},
                       'gracePeriodSeconds': 5, 'propagationPolicy': 'Background'}
            self.call('delete', '--raw=/api/v1/namespaces/'+NS+'/pods/'+target['pod_name'], '-f', '-', payload=canon(options))
        else:
            need(read(marker) == {'binding': self.binding, 'pod_uid': target['pod_uid']}, 'CLEANUP_BINDING_CHANGED')
        deadline = time.monotonic()+40
        while True:
            raw = self.call('get', 'pod', target['pod_name'], '-n', NS, '--ignore-not-found=true', '-o', 'json')
            if not raw.strip():
                return {'status': 'PASS_OWN_IDLE_POD_REMOVAL_CONFIRMED', 'pod_uid': target['pod_uid']}
            need(json.loads(raw)['metadata']['uid'] == target['pod_uid'], 'CLEANUP_NAME_REUSED_STOP')
            need(time.monotonic() < deadline, 'CLEANUP_UNCONFIRMED_READ_ONLY_RESUME_NO_SECOND_DELETE')
            time.sleep(2)

    def stage_or_reconcile(self, stage, evidence, receipt, c):
        marker = receipt.with_name(receipt.name+'.STARTED_NO_AUTOMATIC_RETRY.json')
        if not marker.exists():
            return self.L.stage_app(self.work/'package', stage, evidence,
                self.c['personal_kubeconfig'], self.c['oc'], receipt, self.runner)
        # A lost patch response is reconciled using reads, never a second patch.
        attempt = read(marker)
        p, _, source, _, _ = self.L.package(self.work/'package', stage)
        call = self.L.oc_client(c, self.c['personal_kubeconfig'], self.c['oc'], self.runner)
        app = json.loads(call('get', 'application', self.L.APP, '-n', self.L.ARGO, '-o', 'json'))
        m = self.L.current_app(app)
        need(attempt == {'cluster_id': self.cluster, 'app_uid': m['uid'], 'stage': stage,
                         'mutation': 'APPLICATION_SOURCE_ONLY'}
             and self.L.normalize_source(app['spec']['source']) == self.L.normalize_source(source),
             'UNCERTAIN_SOURCE_PATCH_NOT_CONFIRMED_NO_REPATCH')
        baseline = {'origin': p['argo_origin'], 'application_uid': m['uid'], 'revision': self.L.GITOPS,
                    'source': self.L.normalize_source(source), 'objects': p['objects'][stage], 'stage': stage,
                    'not_before': c['window']['not_before'], 'not_after': c['window']['not_after']}
        browser = (ROOT/'tools/cloud-launch/compare-and-sync.js').read_text(encoding='utf-8').replace(
            '__LAUNCH_BASELINE__', json.dumps(baseline))
        path = receipt.with_name(receipt.name+'.argo-console.js')
        if path.exists():
            need(private(path).decode() == browser, 'EXISTING_SYNC_CONSOLE_CHANGED')
        else:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as stream:
                stream.write(browser)
        return {'status': 'PASS_CLOUD_SOURCE_RECONCILED_NO_PATCH_NO_SYNC', 'stage': stage,
                'cluster_id': self.cluster, 'application_uid': m['uid'],
                'source_sha256': self.L.digest(source), 'console_file': str(path),
                'sync_started': False, 'application_started': False}

    def after_sync(self, stage):
        c = read(self.work/'new-inputs.json')['result']['inputs']
        self.L.oc_client(c, self.c['personal_kubeconfig'], self.c['oc'], self.runner)
        need((self.work/(stage+'-staged.json')).exists(), 'THIS_RUN_STAGED_SOURCE_REQUIRED')
        value = self.step(stage+'-ready-step', lambda: self.L.readback(self.work/'package', stage,
            self.c['personal_kubeconfig'], self.c['oc'], self.runner), readonly=True)
        receipt = self.work/(stage+'-ready.json')
        if not receipt.exists():
            write(receipt, value)
        return {'status': 'PASS_CLOUD_DEPLOYMENT_READY_FOR_BUSINESS_VERIFICATION',
                'stage': stage, 'cluster_id': self.cluster, 'readback_receipt': str(receipt),
                'business_accepted': False, 'pool_runtime_accepted': False,
                'next': 'BUSINESS_SHARED_STATE_AND_SELECTED_MEASUREMENT', 'work': str(self.work)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--work', type=Path)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--after-sync', action='store_true')
    parser.add_argument('--prepare-data-pod', action='store_true')
    parser.add_argument('--stage', choices=('single', 'shared', 'target'), default='single')
    parser.add_argument('--business-evidence', type=Path)
    parser.add_argument('--resume-read-only', action='store_true')
    args = parser.parse_args()
    os.umask(0o077)
    try:
        c = read(args.config)
        missing = check_config(c)
        if not args.execute:
            result = {'status': 'PASS_LOCAL_PRESTART_INPUT_PATHS_NOT_RUNTIME' if not missing else 'PENDING_PRESTART_INPUT_PATHS',
                      'missing': missing, 'api_calls': 0, 'sql_calls': 0, 'recreate_invoked': False}
        else:
            need(not missing and args.work is not None, 'ACTUAL_INPUT_PATHS_AND_PRIVATE_WORK_REQUIRED')
            need(not (args.after_sync and args.prepare_data_pod), 'PREPARE_AND_AFTER_SYNC_ARE_SEPARATE')
            import pwd
            need(pwd.getpwuid(os.getuid()).pw_name == 'jth', 'USE_CONTROLLER_JTH')
            args.work.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            import fcntl
            with open(str(args.work)+'.lock', 'a') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                flow = Flow(c, args.work, resume_readonly=args.resume_read_only, prepare_data_pod=args.prepare_data_pod)
                if args.prepare_data_pod:
                    result = flow.prepare_data_pod()
                else:
                    result = flow.after_sync(args.stage) if args.after_sync else flow.stage(args.stage, args.business_evidence)
        print(json.dumps(result))
        return 0 if result['status'].startswith('PASS_') else 2
    except Exception as error:
        print(json.dumps({'status': 'STOP_CLOUD_PRESTART', 'reason': str(error) if type(error) is Stop else type(error).__name__,
                          'automatic_retry': False, 'recreate_invoked': False, 'raw_output_printed': False}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())

