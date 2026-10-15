#!/usr/bin/env python3
"""Prepare a one-hour, single-Pod Data operator connection as Controller jth.

The Linux account ansible can read only the handoff directory. Its Kubernetes
token can get/exec the named Pod, not read Secrets or change workloads/RBAC.
No SQL or application server is executed by this preparation.
"""
import argparse
import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import tempfile
import time

# Published independent-check helper; one existing file is reused, not re-run.
HELPER_SHA = '5614bb056033ab4d4d7b4646342e4c7f7f462f617af88c744fd5c262d0b5e5b2'
NS = 'seokpan-cloud'


def require(ok, reason):
    if not ok:
        raise RuntimeError(reason)


def objects(name, image, run):
    meta = {'name': name, 'namespace': NS, 'labels': {'seokpan.io/data-access': run}}
    return [
        {'apiVersion': 'v1', 'kind': 'ServiceAccount', 'metadata': meta,
         'automountServiceAccountToken': False, 'imagePullSecrets': [{'name': 'seokpan-cloud-ecr-pull'}]},
        {'apiVersion': 'rbac.authorization.k8s.io/v1', 'kind': 'Role', 'metadata': meta, 'rules': [
            {'apiGroups': [''], 'resources': ['pods'], 'resourceNames': [name], 'verbs': ['get']},
            {'apiGroups': [''], 'resources': ['pods/exec'], 'resourceNames': [name], 'verbs': ['get', 'create']}]},
        {'apiVersion': 'rbac.authorization.k8s.io/v1', 'kind': 'RoleBinding', 'metadata': meta,
         'subjects': [{'kind': 'ServiceAccount', 'name': name, 'namespace': NS}],
         'roleRef': {'apiGroup': 'rbac.authorization.k8s.io', 'kind': 'Role', 'name': name}},
        {'apiVersion': 'v1', 'kind': 'Pod', 'metadata': meta, 'spec': {
            'serviceAccountName': name, 'automountServiceAccountToken': False,
            'activeDeadlineSeconds': 3600, 'restartPolicy': 'Never', 'terminationGracePeriodSeconds': 5,
            'nodeSelector': {'node-role.kubernetes.io/worker': ''},
            'affinity': {'nodeAffinity': {'requiredDuringSchedulingIgnoredDuringExecution': {'nodeSelectorTerms': [
                {'matchExpressions': [{'key': 'node-role.kubernetes.io/' + key, 'operator': 'DoesNotExist'}
                                      for key in ('infra', 'master', 'control-plane')]}]}}},
            'securityContext': {'runAsNonRoot': True, 'seccompProfile': {'type': 'RuntimeDefault'}},
            'volumes': [{'name': 'rds-ca', 'configMap': {'name': 'backend-database-ca'}}],
            'containers': [{'name': 'db-admin', 'image': image, 'imagePullPolicy': 'IfNotPresent',
                'command': ['python', '-u', '-c', 'import time; time.sleep(3550)'],
                'volumeMounts': [{'name': 'rds-ca', 'mountPath': '/run/rds-ca', 'readOnly': True}],
                'resources': {'requests': {'cpu': '25m', 'memory': '64Mi'}, 'limits': {'cpu': '250m', 'memory': '256Mi'}},
                'securityContext': {'allowPrivilegeEscalation': False, 'readOnlyRootFilesystem': True,
                                    'capabilities': {'drop': ['ALL']}}}]}}]


def limited_config(config, server, token):
    require(len(config.get('clusters', [])) == 1, 'ONE_KUBECONFIG_CLUSTER_REQUIRED')
    c = config['clusters'][0]['cluster']
    require(c.get('server', '').rstrip('/') == server.rstrip('/') and c.get('certificate-authority-data')
            and not c.get('insecure-skip-tls-verify'), 'VERIFIED_CLUSTER_CA_REQUIRED')
    return {'apiVersion': 'v1', 'kind': 'Config', 'clusters': [{'name': 'cloud', 'cluster': {
        'server': c['server'], 'certificate-authority-data': c['certificate-authority-data']}}],
        'users': [{'name': 'data-operator-temporary', 'user': {'token': token}}],
        'contexts': [{'name': 'cloud-data', 'context': {'cluster': 'cloud',
                     'user': 'data-operator-temporary', 'namespace': NS}}], 'current-context': 'cloud-data'}


def token_expiry(token, now):
    body = token.split('.')[1]
    expiry = int(json.loads(base64.urlsafe_b64decode(body + '=' * (-len(body) % 4)))['exp'])
    require(now + 300 < expiry <= now + 3900, 'SHORT_TOKEN_LIFETIME_NOT_CONFIRMED')
    return expiry


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('input_root', type=Path)
    args = p.parse_args()
    os.umask(0o077)
    import pwd
    require(pwd.getpwuid(os.getuid()).pw_name == 'jth', 'USE_CONTROLLER_JTH')
    target = pwd.getpwnam('ansible')
    require(target.pw_uid != os.getuid() and shutil.which('setfacl'), 'ANSIBLE_ACCOUNT_AND_SETFACL_REQUIRED')
    helper = Path.home() / 'rosa-independent-checks.py'
    require(helper.is_file() and not helper.is_symlink() and hashlib.sha256(helper.read_bytes()).hexdigest() == HELPER_SHA,
            'EXISTING_REVIEWED_INDEPENDENT_HELPER_REQUIRED')
    spec = importlib.util.spec_from_file_location('accepted_checks', helper)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    root = args.input_root.resolve()
    require(root.parent == Path.home() / '.local/share' and root.name.startswith('seokpan-rosa-plan-inputs-'), 'PROTECTED_ROOT_REQUIRED')
    inputs = root / 'protected/inputs.tfvars.json'
    require(m.sha(inputs.read_bytes()) == m.INPUT_SHA, 'INPUT_CHANGED')
    account = m.load(inputs)['foundation']['account_id']
    mgmt = root / 'management/2tejeb2farjaaoef1e74asdf6amrtpu3'
    handoff = m.load(mgmt / 'data-platform-handoff.json')
    oc_dir = Path.home() / '.local/share/seokpan/oc-cli-4.20'
    oc = m.regular(oc_dir / 'oc')
    require(m.sha(oc.read_bytes()) == m.load(oc_dir / 'receipt.json')['oc_sha256'], 'VERIFIED_OC_REQUIRED')
    import fcntl
    lock = os.open(root / 'cloud-continuation.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    folder = Path(tempfile.mkdtemp(prefix='data-operator-access-', dir=root))
    r = m.Runner(oc, mgmt / 'kubeconfig', folder)
    result = {'status': 'PREPARING', 'sql_executed': False, 'app_started': False, 'credential_values_printed': False,
              'human_recipient': 'A', 'linux_recipient': 'ansible'}
    try:
        require(r.call(['whoami', '--show-server'], 'target').stdout.decode().strip().rstrip('/') == handoff['api_url'].rstrip('/'), 'WRONG_CLUSTER')
        result['pull_lease'] = m.pull_lease(r, account)
        run = secrets.token_hex(5)
        name = 'data-admin-' + run
        result.update(namespace=NS, pod=name, cleanup_required=True)
        (folder / 'receipt.json').write_text(json.dumps(result))
        for obj in objects(name, m.image_uri(account, 'backend'), run):
            r.call(['create', '-f', '-', '-o', 'name'], 'create-' + obj['kind'], json.dumps(obj).encode())
        deadline = time.monotonic() + 180
        while True:
            pod = r.get(['get', 'pod', name, '-n', NS], 'pod-ready')
            if pod.get('status', {}).get('phase') == 'Running' and any(
                c.get('type') == 'Ready' and c.get('status') == 'True' for c in pod.get('status', {}).get('conditions', [])):
                break
            require(pod.get('status', {}).get('phase') not in ('Failed', 'Succeeded') and time.monotonic() < deadline,
                    'POD_NOT_READY_PRESERVE_RECEIPT_NO_RETRY')
            time.sleep(5)
        token = r.call(['create', 'token', name, '-n', NS, '--duration=1h', '--bound-object-kind=Pod',
                        '--bound-object-name=' + name, '--bound-object-uid=' + pod['metadata']['uid']], 'bound-token').stdout.decode().strip()
        expires = token_expiry(token, int(time.time()))
        parent = r.call(['config', 'view', '--raw', '--flatten', '--minify', '-o', 'json'], 'cluster-ca')
        cfg = limited_config(json.loads(parent.stdout), handoff['api_url'], token)
        public = Path(tempfile.mkdtemp(prefix='seokpan-data-access-', dir='/tmp'))
        (public / 'kubeconfig').write_text(json.dumps(cfg))
        shutil.copyfile(oc, public / 'oc')
        (public / 'oc').chmod(0o700)
        activation = ('export KUBECONFIG=' + str(public / 'kubeconfig') + '\n' +
                      'export PATH=' + str(public) + ':"$PATH"\n' + 'export DATA_ADMIN_POD=' + name + '\n')
        (public / 'activate.sh').write_text(activation)
        for filename in ('kubeconfig', 'activate.sh'):
            done = subprocess.run(['setfacl', '-m', 'u:ansible:r--', str(public / filename)], capture_output=True)
            require(done.returncode == 0, 'FILE_ACL_FAILED')
        for path in (public, public / 'oc'):
            done = subprocess.run(['setfacl', '-m', 'u:ansible:r-x', str(path)], capture_output=True)
            require(done.returncode == 0, 'EXECUTION_ACL_FAILED')
        # Execute the real scoped token, not impersonation, for allow/deny checks.
        env = dict(os.environ, KUBECONFIG=str(public / 'kubeconfig'))
        checks = {}
        for key, argv, expected in (
            ('get_named_pod', ['get', 'pod/' + name], True),
            ('exec_named_pod', ['create', 'pods/' + name, '--subresource=exec'], True),
            ('read_secret', ['get', 'secrets'], False),
            ('create_pod', ['create', 'pods'], False)):
            a = subprocess.run([str(oc), '--request-timeout=20s', 'auth', 'can-i', *argv, '-n', NS], env=env, capture_output=True, timeout=30)
            checks[key] = a.stdout.decode().strip() == ('yes' if expected else 'no')
        require(all(checks.values()), 'DELEGATED_TOKEN_SCOPE_CHECK_FAILED')
        # Prove exec with this token without touching the database.
        a = subprocess.run([str(oc), '--request-timeout=20s', 'exec', '-n', NS, name, '-c', 'db-admin', '--',
                            'python', '-c', 'import asyncmy; print("ASYNC_MYSQL_DRIVER_READY")'], env=env, capture_output=True, timeout=30)
        require(a.returncode == 0 and a.stdout.strip() == b'ASYNC_MYSQL_DRIVER_READY', 'DELEGATED_EXEC_FAILED')
        result.update(status='PASS_A_SINGLE_POD_ACCESS_PREPARED', access_directory=str(public),
                      activation_command='source ' + str(public / 'activate.sh'),
                      expires_at_utc=m.dt.datetime.fromtimestamp(expires, m.dt.timezone.utc).isoformat(),
                      access_scope_checks=checks, driver_import='PASS', ca_file='/run/rds-ca/ca.crt',
                      token_bound_to_pod=True, cleanup_resources=['Pod', 'RoleBinding', 'Role', 'ServiceAccount'])
    except Exception as exc:
        result.update(status='STOP_DATA_OPERATOR_ACCESS', reason=str(exc) if isinstance(exc, (RuntimeError, m.Stop)) else type(exc).__name__,
                      automatic_retry=False)
    finally:
        (folder / 'receipt.json').write_text(json.dumps(result, sort_keys=True) + '\n')
        print(json.dumps(result, sort_keys=True))
        print('Private access preparation directory: ' + str(folder))
        os.close(lock)
    return 0 if result['status'].startswith('PASS_') else 1


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(json.dumps({'status': 'STOP_BEFORE_DATA_ACCESS', 'error_class': type(exc).__name__}))
        raise SystemExit(1)
