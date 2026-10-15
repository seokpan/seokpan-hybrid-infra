#!/usr/bin/env python3
"""B01/B05/B08 independent checks. Run manually as Controller jth.

Uses accepted receipts; never logs in to SQL, starts app servers, migrates,
resupplies Data secrets, changes IAM/RBAC/IdP, or runs Terraform/Argo Sync.
Only renews an expiring existing ECR pull Secret and creates/deletes one owned
temporary Pod. All other server mutations have --dry-run=server.
"""
import argparse
import base64
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import stat
import subprocess
import tempfile
import time

NS = 'seokpan-cloud'
SA = 'seokpan-cloud-runtime'
PULL = 'seokpan-cloud-ecr-pull'
INPUT_SHA = '66e433911fb676fc811a67cdbbb446ab9ccfe5a7da9307d47d9e09abf57f5f3b'
RENDER_SHA = '804fc6ab74f3f96831016dbd23111f8427ed7014a2520d38a3bcdeb256bd8135'
DATA_SHA = '5541f3147882bc982e6d1b3cdb0b47cd712d044189b766d1dabe4a2d6c32a981'
DIGEST = {'backend': 'sha256:733f52a6f5a53c31e792cbe1d254221ff8ff86828e8c3afdc65d207e2e5fb18d',
          'frontend': 'sha256:7068a36f55427639b7b7bf8a9c5795689b891e7ec334bfd306d8cd03c5f049ff'}
READ_ACTIONS = ['ecr:BatchGetImage', 'ecr:GetDownloadUrlForLayer',
                'ecr:BatchCheckLayerAvailability', 'ecr:DescribeImages']
LABELS = {'app.kubernetes.io/part-of': 'seokpan', 'seokpan.io/environment': 'cloud'}


class Stop(Exception):
    pass


def need(ok, reason):
    if not ok:
        raise Stop(reason)


def regular(path):
    need(path.is_file() and not path.is_symlink(), 'REGULAR_EXISTING_FILE_REQUIRED')
    return path


def controller_identity_ok():
    return os.name == 'posix' and __import__('pwd').getpwuid(os.getuid()).pw_name == 'jth'


def load(path):
    return json.loads(regular(path).read_text())


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def time_value(value):
    if isinstance(value, (float, int)):
        return dt.datetime.fromtimestamp(value, dt.timezone.utc)
    out = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
    need(out.tzinfo is not None, 'TIMEZONE_REQUIRED')
    return out


def session_policy(account):
    need(re.fullmatch(r'[0-9]{12}', account), 'ACCOUNT_FORMAT')
    repos = ['arn:aws:ecr:ap-northeast-2:' + account + ':repository/seokpan-fnd-' + k
             for k in ('backend', 'frontend')]
    return {'Version': '2012-10-17', 'Statement': [
        {'Effect': 'Allow', 'Action': ['ecr:GetAuthorizationToken'], 'Resource': '*',
         'Condition': {'StringEquals': {'aws:RequestedRegion': 'ap-northeast-2'}}},
        {'Effect': 'Allow', 'Action': READ_ACTIONS, 'Resource': repos,
         'Condition': {'StringEquals': {'aws:RequestedRegion': 'ap-northeast-2'}}},
        {'Effect': 'Deny', 'NotAction': READ_ACTIONS + ['ecr:GetAuthorizationToken', 'sts:GetCallerIdentity'], 'Resource': '*'},
        {'Effect': 'Deny', 'Action': READ_ACTIONS, 'NotResource': repos}]}


def json_stream(raw):
    text = raw.decode('utf-8-sig')
    decoder = json.JSONDecoder()
    items = []
    pos = 0
    while pos < len(text):
        if text[pos].isspace():
            pos += 1
            continue
        value, pos = decoder.raw_decode(text, pos)
        need(isinstance(value, dict), 'SERVER_JSON_OBJECT_REQUIRED')
        items.extend(value['items'] if value.get('kind') == 'List' else [value])
    need(bool(items), 'SERVER_JSON_EMPTY')
    return items


def image_uri(account, component):
    return account + '.dkr.ecr.ap-northeast-2.amazonaws.com/seokpan-fnd-' + component + '@' + DIGEST[component]


def validate_render(items, account):
    need(len(items) == 8, 'EXACT_EIGHT_OBJECTS_REQUIRED')
    expected = {('Deployment', 'backend'), ('Deployment', 'frontend'),
                ('Service', 'backend'), ('Service', 'frontend'),
                ('Route', 'frontend'), ('Route', 'backend-api'), ('Route', 'backend-ws')}
    seen = set()
    configs = []
    for obj in items:
        meta = obj.get('metadata', {})
        need(meta.get('namespace') == NS, 'NAMESPACE_CHANGED')
        key = (obj.get('kind'), meta.get('name'))
        need(key not in seen, 'DUPLICATE_OBJECT')
        seen.add(key)
        if key[0] == 'ConfigMap':
            need(isinstance(key[1], str) and key[1].startswith('backend-config-'), 'ONLY_RUNTIME_CONFIGMAP_ALLOWED')
            configs.append(key)
        else:
            need(key in expected, 'UNEXPECTED_RESOURCE')
        if key[0] == 'Deployment':
            spec = obj['spec']
            need(spec.get('replicas') == 0, 'BACKEND_AND_FRONTEND_MUST_REMAIN_ZERO')
            pod = spec['template']['spec']
            need(pod.get('serviceAccountName') == SA and pod.get('automountServiceAccountToken') is False,
                 'RUNTIME_IDENTITY_CHANGED')
            need(pod.get('imagePullSecrets') == [{'name': PULL}], 'PULL_REFERENCE_CHANGED')
            containers = pod.get('containers', [])
            need(len(containers) == 1 and containers[0].get('image') == image_uri(account, key[1]),
                 'APPROVED_IMAGE_CHANGED')
        if key[0] == 'Route':
            need(obj['spec'].get('tls', {}).get('termination') == 'edge' and
                 obj['spec']['tls'].get('insecureEdgeTerminationPolicy') == 'Redirect', 'ROUTE_TLS_CHANGED')
    need(len(configs) == 1 and seen - set(configs) == expected, 'EXACT_OBJECT_SET_CHANGED')


def probe_object(name, account, run_id):
    security = {'allowPrivilegeEscalation': False, 'readOnlyRootFilesystem': True,
                'capabilities': {'drop': ['ALL']}}
    return {'apiVersion': 'v1', 'kind': 'Pod', 'metadata': {'name': name, 'namespace': NS,
            'labels': {'seokpan.io/independent-run': run_id},
            'annotations': {'seokpan.io/scope': 'B01-negative-B05-frontend-only'}},
            'spec': {'serviceAccountName': SA, 'automountServiceAccountToken': False,
                     'imagePullSecrets': [{'name': PULL}], 'restartPolicy': 'Never',
                     'activeDeadlineSeconds': 600, 'terminationGracePeriodSeconds': 5,
                     'nodeSelector': {'node-role.kubernetes.io/worker': ''},
                     'affinity': {'nodeAffinity': {'requiredDuringSchedulingIgnoredDuringExecution': {
                         'nodeSelectorTerms': [{'matchExpressions': [
                             {'key': 'node-role.kubernetes.io/' + role, 'operator': 'DoesNotExist'}
                             for role in ('infra', 'master', 'control-plane')]}]}}},
                     'securityContext': {'runAsNonRoot': True, 'seccompProfile': {'type': 'RuntimeDefault'}},
                     'containers': [
                         {'name': 'frontend-pull', 'image': image_uri(account, 'frontend'),
                          'imagePullPolicy': 'Always', 'command': ['sh', '-c', 'sleep 540'],
                          'securityContext': security,
                          'resources': {'requests': {'cpu': '25m', 'memory': '32Mi'},
                                        'limits': {'cpu': '100m', 'memory': '128Mi'}}},
                         {'name': 'negative-probe', 'image': image_uri(account, 'backend'),
                          'imagePullPolicy': 'IfNotPresent',
                          'command': ['python', '-u', '-c', 'import time;time.sleep(540)'],
                          'securityContext': security,
                          'resources': {'requests': {'cpu': '25m', 'memory': '64Mi'},
                                        'limits': {'cpu': '100m', 'memory': '192Mi'}}}]}}


NEGATIVE_PROBE = r'''
import json,socket,ssl,struct,sys
p=json.load(sys.stdin); out={}
def context(ca):
    c=ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT);c.minimum_version=ssl.TLSVersion.TLSv1_2
    c.load_verify_locations(cadata=ca);return c
def exact(s,n):
    b=b''
    while len(b)<n:
        x=s.recv(n-len(b))
        if not x:raise RuntimeError('EARLY_EOF')
        b+=x
    return b
def mysql_start(s):
    h=exact(s,4);n=int.from_bytes(h[:3],'little')
    if n>65536:raise RuntimeError('GREETING_SIZE')
    greeting=exact(s,n)
    if not greeting or greeting[0]!=10:raise RuntimeError('MYSQL_GREETING')
    pos=greeting.index(b'\0',1)+1
    if not int.from_bytes(greeting[pos+13:pos+15],'little')&0x0800:raise RuntimeError('TLS_NOT_AVAILABLE')
    payload=struct.pack('<IIB23x',0x0200|0x0800|0x8000,1024*1024,45)
    s.sendall(len(payload).to_bytes(3,'little')+b'\x01'+payload)
def rejected(kind,ca,host,want):
    try:
        address=p['rds_address'] if kind=='rds' else p['redis_address']
        port=3306 if kind=='rds' else 6379
        with socket.create_connection((address,port),timeout=8) as s:
            if kind=='rds':mysql_start(s)
            with context(ca).wrap_socket(s,server_hostname=host):pass
        return {'status':'FAIL_UNEXPECTEDLY_ACCEPTED'}
    except ssl.SSLCertVerificationError as e:
        code=getattr(e,'verify_code',None)
        expected=code in (18,19,20,21) if want=='ca' else code==62
        return {'status':'PASS' if expected else 'FAIL_OTHER_CERTIFICATE_ERROR','verify_code':code}
    except Exception as e:return {'status':'FAIL_NOT_CERTIFICATE_REJECTION','error_class':type(e).__name__}
for kind,key in [('rds','rds_address'),('valkey','redis_address')]:
    out[kind+'_wrong_ca']=rejected('rds' if kind=='rds' else 'redis',p['untrusted_ca'],p[key],'ca')
    out[kind+'_wrong_hostname']=rejected('rds' if kind=='rds' else 'redis',p['rds_ca'] if kind=='rds' else p['redis_ca'],'not-approved.invalid','host')
try:
    with socket.create_connection((p['redis_address'],6379),timeout=8) as s:
        with context(p['redis_ca']).wrap_socket(s,server_hostname=p['redis_address']) as t:
            token=p['wrong_token'].encode()
            t.sendall(b'*2\r\n$4\r\nAUTH\r\n$'+str(len(token)).encode()+b'\r\n'+token+b'\r\n')
            answer=t.makefile('rb').readline(2048)
            wrong=answer.startswith(b'-WRONGPASS') or answer.startswith(b'-ERR invalid password')
            out['valkey_wrong_auth']={'status':'PASS' if wrong else 'FAIL_UNEXPECTED_AUTH_RESULT'}
except Exception as e:out['valkey_wrong_auth']={'status':'FAIL_AUTH_NOT_REACHED','error_class':type(e).__name__}
print(json.dumps({'checks':out,'sql_login':False,'sql_queries':0,'redis_data_commands':0}))
'''


class Runner:
    def __init__(self, oc, kube, folder):
        self.oc = str(oc)
        self.env = dict(os.environ, KUBECONFIG=str(kube), AWS_PAGER='', AWS_MAX_ATTEMPTS='1')
        self.folder = folder
        self.calls = 0

    def call(self, args, label, payload=None, timeout=55, allow_failure=False):
        self.calls += 1
        try:
            r = subprocess.run([self.oc, '--request-timeout=30s', *args], env=self.env,
                               input=payload, capture_output=True, timeout=timeout,
                               stdin=subprocess.DEVNULL if payload is None else None)
        except subprocess.TimeoutExpired:
            raise Stop(label.upper() + '_TIMEOUT') from None
        (self.folder / (label + '.stderr.log')).write_bytes(r.stderr)
        if r.returncode and not allow_failure:
            text = r.stderr.decode(errors='replace')
            group = 'ACCESS_DENIED' if re.search(r'Forbidden|Unauthorized|permission denied', text, re.I) else 'FAILED'
            raise Stop(label.upper() + '_' + group)
        return r

    def get(self, args, label):
        r = self.call([*args, '-o', 'json'], label)
        return json.loads(r.stdout) if r.stdout.strip() else None

    def write(self, name, value):
        (self.folder / name).write_text(json.dumps(value, sort_keys=True) + '\n')


def aws(args, env, label):
    try:
        r = subprocess.run(['aws', '--region', 'ap-northeast-2', '--output', 'json',
                            '--cli-connect-timeout', '10', '--cli-read-timeout', '25', *args],
                           env=env, capture_output=True, timeout=45, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        raise Stop(label + '_TIMEOUT') from None
    if r.returncode:
        text = r.stderr.decode(errors='replace')
        kind = ('ACCESS_DENIED' if re.search(r'AccessDenied|not authorized', text, re.I) else
                'CREDENTIALS' if re.search(r'ExpiredToken|Unable to locate credentials|InvalidClientToken', text, re.I) else 'FAILED')
        raise Stop(label + '_' + kind)
    return json.loads(r.stdout)


def pull_lease(r, account):
    secret = r.get(['get', 'secret', PULL, '-n', NS], 'pull-secret-metadata')
    need(secret.get('type') == 'kubernetes.io/dockerconfigjson' and
         all(secret['metadata'].get('labels', {}).get(k) == v for k, v in LABELS.items()), 'PULL_SECRET_SCOPE')
    current = secret['metadata'].get('annotations', {}).get('seokpan.io/pull-expires-at', '')
    try:
        if time_value(current) > dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=1):
            return {'status': 'REUSED_CURRENT_LEASE', 'expires_at_utc': current, 'secret_changed': False}
    except (ValueError, TypeError, Stop):
        pass
    need(shutil.which('aws'), 'EXISTING_AWS_CLI_REQUIRED_FOR_PULL_RENEWAL')
    env = dict(r.env)
    for k in list(env):
        if k.startswith('AWS_') and k not in ('AWS_SHARED_CREDENTIALS_FILE', 'AWS_CONFIG_FILE'):
            env.pop(k)
    env.update(AWS_PAGER='', AWS_MAX_ATTEMPTS='1')
    need(aws(['sts', 'get-caller-identity'], env, 'PULL_HUMAN_CALLER').get('Arn') ==
         'arn:aws:iam::' + account + ':user/tjung', 'EXPECTED_TJUNG_HUMAN_CALLER')
    policy = json.dumps(session_policy(account), separators=(',', ':'))
    need(len(policy) <= 2048, 'PULL_SESSION_POLICY_SIZE')
    name = 'seokpan-cloud-pull-' + secrets.token_hex(4)
    session = aws(['sts', 'get-federation-token', '--name', name, '--duration-seconds', '43200',
                   '--policy', policy], env, 'PULL_READ_ONLY_FEDERATION')
    expected = 'arn:aws:sts::' + account + ':federated-user/' + name
    need(session.get('FederatedUser', {}).get('Arn') == expected, 'PULL_SESSION_IDENTITY')
    creds = session['Credentials']
    env.update(AWS_ACCESS_KEY_ID=creds['AccessKeyId'], AWS_SECRET_ACCESS_KEY=creds['SecretAccessKey'],
               AWS_SESSION_TOKEN=creds['SessionToken'])
    need(aws(['sts', 'get-caller-identity'], env, 'PULL_SESSION_CALLER').get('Arn') == expected, 'PULL_SESSION_CALLER_MISMATCH')
    registry = account + '.dkr.ecr.ap-northeast-2.amazonaws.com'
    answer = aws(['ecr', 'get-authorization-token', '--registry-ids', account], env, 'PULL_ECR_TOKEN')
    rows = [x for x in answer.get('authorizationData', []) if x.get('proxyEndpoint') == 'https://' + registry]
    need(len(rows) == 1, 'ONE_REGISTRY_TOKEN_REQUIRED')
    token = rows[0]['authorizationToken']
    need(base64.b64decode(token, validate=True).startswith(b'AWS:'), 'PULL_TOKEN_FORMAT')
    expiry = min(time_value(creds['Expiration']), time_value(rows[0]['expiresAt']))
    need(expiry > dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=1), 'PULL_LEASE_TOO_SHORT')
    renewed = {'apiVersion': 'v1', 'kind': 'Secret', 'metadata': {
        'name': PULL, 'namespace': NS, 'labels': LABELS,
        'annotations': {'seokpan.io/pull-expires-at': expiry.isoformat(),
                        'seokpan.io/pull-renew-before': (expiry - dt.timedelta(hours=1)).isoformat(),
                        'seokpan.io/pull-scope': 'two-project-repositories-read-only'}},
        'type': 'kubernetes.io/dockerconfigjson', 'data': {'.dockerconfigjson': base64.b64encode(
            json.dumps({'auths': {registry: {'auth': token}}}, separators=(',', ':')).encode()).decode()}}
    r.call(['apply', '--server-side', '--field-manager=seokpan-cloud-pull', '-f', '-', '-o', 'name'],
           'renew-existing-pull-secret', json.dumps(renewed).encode())
    secret.clear(); renewed.clear(); env.clear(); session.clear(); creds.clear(); answer.clear()
    return {'status': 'RENEWED_SCOPED_READ_ONLY_LEASE', 'expires_at_utc': expiry.isoformat(),
            'secret_changed': True, 'iam_changes': 0, 'secret_values_printed': False}


def inventory(r, management):
    oauth = r.get(['get', 'oauth.config.openshift.io', 'cluster'], 'oauth-inventory')
    types = [x.get('type', 'UNKNOWN') for x in oauth.get('spec', {}).get('identityProviders', [])]
    user = r.call(['whoami'], 'current-management-identity').stdout.decode().strip()
    admin = r.call(['auth', 'can-i', '*', '*', '--all-namespaces'], 'current-cluster-admin').stdout.decode().strip()
    roles = r.get(['get', 'rolebindings', '-n', NS], 'namespace-rolebindings')
    cluster_roles = r.get(['get', 'clusterrolebindings'], 'cluster-rolebindings')
    apis = r.call(['api-resources', '--api-group=argoproj.io', '-o', 'name'], 'argo-api-discovery').stdout.decode().splitlines()
    names = set(apis)
    argo = {}
    for resource in ('applications.argoproj.io', 'appprojects.argoproj.io', 'argocds.argoproj.io'):
        if resource in names:
            value = r.get(['get', resource, '--all-namespaces'], resource.split('.')[0] + '-inventory')
            argo[resource.split('.')[0]] = len(value.get('items', []))
    local = {}
    for filename in ('kubeconfig', 'initial-admin.json'):
        p = management / filename
        regular(p)
        st = p.stat()
        local[filename] = {'exists': True, 'owner_is_current_user': st.st_uid == os.getuid(),
                           'owner_only_permissions': stat.S_IMODE(st.st_mode) & 0o077 == 0}
    out = {'status': 'PASS_READ_ONLY_MANAGEMENT_INVENTORY', 'identity_provider_count': len(types),
           'identity_provider_types': types, 'caller_sha256': sha(user.encode()),
           'caller_is_initial_cluster_admin': user == 'cluster-admin',
           'current_caller_cluster_admin': admin == 'yes',
           'namespace_rolebinding_count': len(roles.get('items', [])),
           'cluster_rolebinding_count': len(cluster_roles.get('items', [])),
           'argoproj_api_resources': len(apis), 'argo_counts': argo, 'local_management_files': local,
           'normal_user_login_denied_breakglass_tests': 'NOT_RUN',
           'backup_custody': 'NOT_PROVEN_BY_CONTROLLER_FILE_PRESENCE', 'changes': 0,
           'pending': ['NORMAL_IDP_IDENTITIES_AND_ACCESS_POLICY', 'ORDINARY_USER_AND_DENIAL_TESTS',
                       'BACKUP_CUSTODY_RECORD_ACCEPTANCE', 'INITIAL_ADMIN_RETIREMENT_AFTER_NORMAL_ACCESS']}
    r.write('management-inventory.json', out)
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input_root', type=Path)
    parser.add_argument('--held-render', type=Path, required=True)
    parser.add_argument('--data-file', type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    need(controller_identity_ok(), 'USE_JTH_CONTROLLER')
    need('SSLKEYLOGFILE' not in os.environ, 'TLS_KEY_LOGGING_NOT_ALLOWED')
    root = args.input_root.resolve()
    need(root.parent == Path.home() / '.local/share' and root.name.startswith('seokpan-rosa-plan-inputs-'), 'PERSONAL_PROTECTED_INPUT_ROOT_ONLY')
    raw = regular(root / 'protected/inputs.tfvars.json').read_bytes()
    need(sha(raw) == INPUT_SHA, 'ACCEPTED_INPUTS_CHANGED')
    account = json.loads(raw)['foundation']['account_id']
    need(re.fullmatch(r'[0-9]{12}', account), 'ACCOUNT_FORMAT')
    matches = []
    for candidate in (root / 'management').iterdir():
        if candidate.is_dir() and not candidate.is_symlink() and (candidate / 'data-platform-handoff.json').is_file():
            value = load(candidate / 'data-platform-handoff.json')
            if value.get('cluster_id') == candidate.name and value.get('binding_enabled') is True:
                matches.append((candidate, value))
    need(len(matches) == 1, 'ONE_ACCEPTED_DATA_HANDOFF_REQUIRED')
    management, handoff = matches[0]
    cluster = management.name
    need(re.fullmatch(r'[a-z0-9]{10,64}', cluster), 'CLUSTER_ID_FORMAT')
    held = args.held_render.resolve()
    need(held.parent == root and held.name.startswith('cloud-held-render-') and not args.held_render.is_symlink(), 'HELD_RENDER_PATH_SCOPE')
    held_receipt = load(held / 'receipt.json')
    rendered = regular(held / 'held-render.yaml').read_bytes()
    need(held_receipt.get('status') == 'PASS_PRIVATE_HELD_RENDER_CLIENT_VALIDATION' and
         held_receipt.get('render_sha256') == RENDER_SHA and sha(rendered) == RENDER_SHA, 'ACCEPTED_HELD_RENDER_CHANGED')
    validate_render(load(held / 'held-render.json')['items'], account)
    oc_dir = Path.home() / '.local/share/seokpan/oc-cli-4.20'
    oc = regular(oc_dir / 'oc')
    need(sha(oc.read_bytes()) == load(oc_dir / 'receipt.json')['oc_sha256'], 'EXISTING_VERIFIED_OC_REQUIRED')
    kube = regular(management / 'kubeconfig')
    marker = root / 'b01-b05-b08-independent-attempt.json'
    need(not marker.exists() and not marker.is_symlink(), 'PREVIOUS_INDEPENDENT_ATTEMPT_READ_EXISTING_RESULT_NO_REPLAY')
    import fcntl
    fd = os.open(root / 'cloud-continuation.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        raise Stop('OTHER_CLOUD_CONTINUATION_RUNNING') from None
    if marker.exists() or marker.is_symlink():
        os.close(fd)
        raise Stop('PREVIOUS_INDEPENDENT_ATTEMPT_READ_EXISTING_RESULT_NO_REPLAY')
    folder = Path(tempfile.mkdtemp(prefix='cloud-independent-', dir=root))
    with marker.open('x') as stream:
        json.dump({'result_directory': str(folder), 'started_at_utc': dt.datetime.now(dt.timezone.utc).isoformat()}, stream)
    r = Runner(oc, kube, folder)
    result = {'status': 'RUNNING', 'scope': 'B01_NEGATIVE_B05_FRONTEND_ADMISSION_B08_INVENTORY',
              'steps': {}, 'temporary_pod_cleanup': 'NOT_CREATED',
              'app_started': False, 'sql_login': False, 'sql_queries': 0, 'redis_data_commands': 0,
              'terraform_argo_sync_iam_rbac_changes': False, 'completed_checks_repeated': False}
    pod = None

    def save():
        r.write('summary.json', result)

    def step(name, action):
        try:
            out = action()
            result['steps'][name] = out
        except Exception as exc:
            reason = str(exc) if isinstance(exc, Stop) else type(exc).__name__
            result['steps'][name] = {'status': 'FAIL', 'reason': reason, 'raw_output': 'PROTECTED_NOT_EMITTED'}
        save()
        print(json.dumps({'step': name, **result['steps'][name]}, sort_keys=True), flush=True)

    try:
        server = r.call(['whoami', '--show-server'], 'target-server').stdout.decode().strip()
        need(server.rstrip('/') == handoff['api_url'].rstrip('/'), 'WRONG_ROSA_CONTEXT')
        ns = r.get(['get', 'namespace', NS], 'namespace-ownership')
        need(all(ns['metadata'].get('labels', {}).get(k) == v for k, v in LABELS.items()), 'NAMESPACE_OWNERSHIP_CHANGED')
        sa = r.get(['get', 'serviceaccount', SA, '-n', NS], 'runtime-service-account')
        need(sa.get('automountServiceAccountToken') is False and any(x.get('name') == PULL for x in sa.get('imagePullSecrets', [])), 'EXISTING_RUNTIME_SA_REQUIRED')
        def admission():
            answer = r.call(['apply', '--server-side', '--dry-run=server', '--field-manager=seokpan-independent-review',
                             '-f', str(held / 'held-render.yaml'), '-o', 'json'], 'held-server-admission', timeout=100)
            r.write('server-admission.json', json_stream(answer.stdout))
            validate_render(json_stream(answer.stdout), account)
            return {'status': 'PASS', 'objects': 8, 'backend_replicas': 0, 'frontend_replicas': 0,
                    'persisted': False, 'runtime_pool_or_migration_acceptance': False}
        step('B05_server_admission', admission)
        step('B08_management_inventory', lambda: inventory(r, management))
        step('existing_ECR_lease', lambda: pull_lease(r, account))
        if result['steps']['existing_ECR_lease'].get('status') != 'FAIL':
            run_id = secrets.token_hex(6)
            name = 'seokpan-independent-' + run_id
            pod = probe_object(name, account, run_id)
            created = r.call(
                ['create', '-f', '-', '-o', 'json'], 'create-independent-probe', json.dumps(probe_object(name, account, run_id)).encode())
            pod = json.loads(created.stdout)
            need(pod['metadata'].get('uid') and pod['metadata'].get('labels', {}).get('seokpan.io/independent-run') == run_id, 'PROBE_IDENTITY')
            result['temporary_pod_cleanup'] = 'PENDING'
            r.write('temporary-pod-receipt.json', {'name': name, 'uid': pod['metadata']['uid'], 'run_id': run_id})
            current = pod
            for _ in range(60):
                current = r.get(['get', 'pod', name, '-n', NS], 'probe-status')
                statuses = {c['name']: c for c in current.get('status', {}).get('containerStatuses', [])}
                if all(statuses.get(n, {}).get('state', {}).get('running') for n in ('frontend-pull', 'negative-probe')):
                    break
                if current.get('status', {}).get('phase') in ('Failed', 'Succeeded'):
                    break
                time.sleep(2)
            statuses = {c['name']: c for c in current.get('status', {}).get('containerStatuses', [])}
            def frontend():
                state = statuses.get('frontend-pull', {})
                need(state.get('state', {}).get('running') and 'sha256:' in state.get('imageID', ''), 'FRONTEND_NOT_RUNNING_PULL_UNPROVEN')
                node_name = current['spec']['nodeName']
                labels = r.get(['get', 'node', node_name], 'frontend-compute-node')['metadata'].get('labels', {})
                need('node-role.kubernetes.io/worker' in labels and not any('node-role.kubernetes.io/' + x in labels for x in ('infra', 'master', 'control-plane')), 'COMPUTE_NODE_REQUIRED')
                return {'status': 'PASS', 'approved_frontend_image': True, 'compute_node': True,
                        'image_id_sha256': sha(state['imageID'].encode()), 'nginx_app_server_started': False}
            step('B05_frontend_pull', frontend)
            def negative():
                need(statuses.get('negative-probe', {}).get('state', {}).get('running'), 'NEGATIVE_PROBE_NOT_RUNNING')
                cms = {}
                for kind in ('database', 'redis'):
                    cm = r.get(['get', 'configmap', 'backend-' + kind + '-ca', '-n', NS], kind + '-existing-ca')
                    cms[kind] = cm['data']['ca.crt']
                matches = [p for p in root.glob('cloud-connection-*') if p.is_dir() and not p.is_symlink() and
                           (p / 'receipt.json').is_file() and not (p / 'receipt.json').is_symlink() and
                           sha((p / 'receipt.json').read_bytes()) == held_receipt.get('connection_receipt_sha256')]
                need(len(matches) == 1, 'EXACT_ACCEPTED_CONNECTION_RECEIPT_REQUIRED')
                connection = matches[0]
                contract = load(connection / 'cloud-platform-inputs.json')
                need(contract.get('cluster_id') == cluster and contract.get('namespace') == NS, 'CONNECTION_RECEIPT_IDENTITY')
                data_file = args.data_file
                data_raw = regular(data_file).read_bytes()
                need(sha(data_raw) == DATA_SHA, 'DATA_RECEIPT_CHANGED')
                data = json.loads(data_raw)
                values = {k: data[k]['value'] if isinstance(data[k], dict) else data[k]
                          for k in ('rds_address', 'rds_port', 'redis_primary_endpoint', 'redis_port')}
                need(str(values['rds_port']) == '3306' and str(values['redis_port']) == '6379', 'DATA_PORT_CHANGED')
                need(shutil.which('openssl'), 'OPENSSL_REQUIRED_FOR_UNTRUSTED_TEST_CA')
                ca = subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
                                      '-subj', '/CN=seokpan-independent-negative-only', '-keyout', os.devnull,
                                      '-out', '/dev/stdout'], capture_output=True, timeout=35, stdin=subprocess.DEVNULL)
                need(ca.returncode == 0 and b'BEGIN CERTIFICATE' in ca.stdout, 'TEMP_TEST_CA_GENERATION_FAILED')
                secret = r.get(['get', 'secret', 'backend-redis-runtime', '-n', NS], 'existing-redis-token-for-nonmatch')
                real_token = base64.b64decode(secret['data']['SEOKPAN_REDIS_AUTH_TOKEN'], validate=True).decode()
                wrong = secrets.token_hex(32)
                while wrong == real_token:
                    wrong = secrets.token_hex(32)
                payload = {'rds_address': values['rds_address'], 'redis_address': values['redis_primary_endpoint'],
                           'rds_ca': cms['database'], 'redis_ca': cms['redis'], 'untrusted_ca': ca.stdout.decode(), 'wrong_token': wrong}
                secret.clear(); real_token = ''
                answer = r.call(['exec', '--request-timeout=120s', '-i', name, '-n', NS, '-c', 'negative-probe', '--', 'python', '-c', NEGATIVE_PROBE],
                                'negative-only-results', json.dumps(payload).encode(), timeout=130)
                observed = json.loads(answer.stdout)
                need(set(observed.get('checks', {})) == {'rds_wrong_ca', 'rds_wrong_hostname', 'valkey_wrong_ca', 'valkey_wrong_hostname', 'valkey_wrong_auth'}, 'EXACT_FIVE_NEGATIVE_RESULTS_REQUIRED')
                need(observed.get('sql_queries') == 0 and observed.get('redis_data_commands') == 0, 'PROBE_SCOPE_CHANGED')
                r.write('negative-results.json', observed)
                return {'status': 'PASS' if all(x.get('status') == 'PASS' for x in observed['checks'].values()) else 'FAIL',
                        **observed, 'credential_values_printed': False}
            step('B01_rejection_checks', negative)
        else:
            result['steps']['B05_frontend_pull'] = {'status': 'NOT_RUN', 'reason': 'ECR_LEASE_UNAVAILABLE'}
            result['steps']['B01_rejection_checks'] = {'status': 'NOT_RUN', 'reason': 'PROBE_IMAGE_AUTH_UNAVAILABLE'}
    except Exception as exc:
        result['fatal'] = str(exc) if isinstance(exc, Stop) else type(exc).__name__
    finally:
        if pod is not None:
            try:
                name = pod['metadata']['name']
                current = r.get(['get', 'pod', name, '-n', NS, '--ignore-not-found'], 'owned-probe-cleanup-check')
                if current:
                    need(current['metadata'].get('labels', {}).get('seokpan.io/independent-run') ==
                         pod['metadata'].get('labels', {}).get('seokpan.io/independent-run'), 'PROBE_OWNER_CHANGED_DO_NOT_DELETE')
                    if pod['metadata'].get('uid'):
                        need(current['metadata'].get('uid') == pod['metadata']['uid'], 'PROBE_UID_CHANGED_DO_NOT_DELETE')
                    r.call(['delete', 'pod', name, '-n', NS, '--wait=true', '--timeout=45s'], 'owned-probe-delete', timeout=60)
                result['temporary_pod_cleanup'] = 'PASS_REMOVED_OWN_PROBE'
            except Exception as exc:
                result['temporary_pod_cleanup'] = 'REQUIRES_ATTENTION'
                result['cleanup_reason'] = str(exc) if isinstance(exc, Stop) else type(exc).__name__
        result['status'] = ('INDEPENDENT_BATCH_COMPLETED' if not result.get('fatal') and
                            result['temporary_pod_cleanup'] != 'REQUIRES_ATTENTION' and
                            all(v.get('status') not in ('FAIL', 'NOT_RUN') for v in result['steps'].values())
                            else 'INDEPENDENT_BATCH_NEEDS_REVIEW')
        result['B08_full_access_acceptance'] = 'NOT_COMPLETED_BY_INVENTORY'
        result['remaining_inputs'] = ['C_SQL_AUTH_LIMIT_AND_IMPORT_GRANTS_FOR_APP',
                                      'NORMAL_IDP_RBAC_AND_BACKUP_CUSTODY_ACCEPTANCE', 'EXISTING_A_PR70_AND_D_RESULTS_SEPARATE']
        result['oc_commands'] = r.calls
        save()
        print(json.dumps(result, sort_keys=True), flush=True)
        print('Private independent result directory: ' + str(folder), flush=True)
        os.close(fd)
    return 0 if result['status'] == 'INDEPENDENT_BATCH_COMPLETED' else 1


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        reason = str(exc) if isinstance(exc, Stop) else type(exc).__name__
        print(json.dumps({'status': 'STOP_INDEPENDENT_LOCAL_CONTEXT', 'reason': reason,
                          'application_started': False, 'automatic_retry': False}), flush=True)
        raise SystemExit(1)
