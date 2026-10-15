#!/usr/bin/env python3
"""Prepare or resume a single-Pod Data operator connection as Controller jth.

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
import shlex
import shutil
import subprocess
import tempfile
import time

# Published independent-check helper; one existing file is reused, not re-run.
HELPER_SHA = '5614bb056033ab4d4d7b4646342e4c7f7f462f617af88c744fd5c262d0b5e5b2'
NS = 'seokpan-cloud'
TOKEN_SECONDS = 43200
WAIT_COMMAND = ['python', '-u', '-c', 'import time\nwhile True: time.sleep(3600)']


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
            'restartPolicy': 'Never', 'terminationGracePeriodSeconds': 5,
            'nodeSelector': {'node-role.kubernetes.io/worker': ''},
            'affinity': {'nodeAffinity': {'requiredDuringSchedulingIgnoredDuringExecution': {'nodeSelectorTerms': [
                {'matchExpressions': [{'key': 'node-role.kubernetes.io/' + key, 'operator': 'DoesNotExist'}
                                      for key in ('infra', 'master', 'control-plane')]}]}}},
            'securityContext': {'runAsNonRoot': True, 'seccompProfile': {'type': 'RuntimeDefault'}},
            'volumes': [{'name': 'rds-ca', 'configMap': {'name': 'backend-database-ca'}}],
            'containers': [{'name': 'db-admin', 'image': image, 'imagePullPolicy': 'IfNotPresent',
                'command': WAIT_COMMAND,
                'volumeMounts': [{'name': 'rds-ca', 'mountPath': '/run/rds-ca', 'readOnly': True}],
                'resources': {'requests': {'cpu': '25m', 'memory': '64Mi'}, 'limits': {'cpu': '250m', 'memory': '256Mi'}},
                'securityContext': {'allowPrivilegeEscalation': False, 'readOnlyRootFilesystem': True,
                                    'capabilities': {'drop': ['ALL']}}}]}}]


def limited_config(config, server, token):
    require(len(config.get('clusters', [])) == 1, 'ONE_KUBECONFIG_CLUSTER_REQUIRED')
    c = config['clusters'][0]['cluster']
    require(c.get('server', '').rstrip('/') == server.rstrip('/'), 'CLUSTER_SERVER_CHANGED')
    require(not c.get('insecure-skip-tls-verify'), 'TLS_VERIFICATION_MUST_STAY_ENABLED')
    require(not c.get('certificate-authority'), 'FLATTENED_CA_FILE_REQUIRED')
    cluster = {'server': c['server']}
    # An omitted CA means the normal OS trust store, not disabled TLS.
    for key in ('certificate-authority-data', 'tls-server-name'):
        if c.get(key):
            cluster[key] = c[key]
    return {'apiVersion': 'v1', 'kind': 'Config', 'clusters': [{'name': 'cloud', 'cluster': cluster}],
        'users': [{'name': 'data-operator-temporary', 'user': {'token': token}}],
        'contexts': [{'name': 'cloud-data', 'context': {'cluster': 'cloud',
                     'user': 'data-operator-temporary', 'namespace': NS}}], 'current-context': 'cloud-data'}


def token_expiry(token, now):
    body = token.split('.')[1]
    expiry = int(json.loads(base64.urlsafe_b64decode(body + '=' * (-len(body) % 4)))['exp'])
    require(now + 300 < expiry <= now + TOKEN_SECONDS + 300, 'TOKEN_LIFETIME_NOT_CONFIRMED')
    return expiry


def accepted_receipt(root, folder, renewal):
    original = folder
    folder = folder.resolve()
    require(not original.is_symlink() and folder.parent == root and folder.name.startswith('data-operator-access-'),
            'RESUME_FOLDER_SCOPE')
    receipt_path = folder / 'receipt.json'
    require(receipt_path.is_file() and not receipt_path.is_symlink(), 'EXISTING_RECEIPT_REQUIRED')
    v = json.loads(receipt_path.read_text())
    require(v.get('namespace') == NS and v.get('linux_recipient') == 'ansible' and v.get('human_recipient') == 'A'
            and v.get('sql_executed') is False and v.get('app_started') is False, 'RESUME_RECEIPT_SCOPE')
    name = v.get('pod', '')
    import re
    require(re.fullmatch(r'data-admin-[a-f0-9]{10}', name), 'RESUME_POD_NAME')
    if renewal:
        require(v.get('status') == 'PASS_A_SINGLE_POD_ACCESS_PREPARED' and v.get('persistent_until_cleanup') is True,
                'ACCEPTED_LONG_RUNNING_ACCESS_REQUIRED_FOR_RENEWAL')
    else:
        require(v.get('status') == 'STOP_DATA_OPERATOR_ACCESS' and v.get('reason') == 'VERIFIED_CLUSTER_CA_REQUIRED',
                'ONLY_REPORTED_CA_STOP_CAN_BE_RESUMED')
    return v


def ensure_reused_objects(r, name, image, folder, result):
    desired = objects(name, image, name.removeprefix('data-admin-'))
    resources = ['serviceaccount', 'role', 'rolebinding']
    for resource, expected in zip(resources, desired[:3]):
        live = r.get(['get', resource, name, '-n', NS], 'reuse-' + resource)
        require(live.get('metadata', {}).get('labels', {}).get('seokpan.io/data-access') == name[11:], 'EXISTING_ACCESS_OWNER_CHANGED')
        if resource == 'serviceaccount':
            require(live.get('automountServiceAccountToken') is False and
                    {'name': 'seokpan-cloud-ecr-pull'} in live.get('imagePullSecrets', []), 'EXISTING_SERVICEACCOUNT_CHANGED')
            continue
        for key, value in expected.items():
            if key not in ('metadata', 'apiVersion', 'kind'):
                require(live.get(key) == value, 'EXISTING_ACCESS_PERMISSION_CHANGED')
    result['access_objects_reused'] = 3
    pod = r.get(['get', 'pod', name, '-n', NS, '--ignore-not-found'], 'existing-access-pod')
    if pod:
        require(pod.get('metadata', {}).get('labels', {}).get('seokpan.io/data-access') == name[11:], 'EXISTING_POD_OWNER_CHANGED')
        spec = pod.get('spec', {})
        require(spec.get('serviceAccountName') == name and spec.get('automountServiceAccountToken') is False,
                'EXISTING_POD_SERVICE_ACCOUNT_CHANGED')
        containers = spec.get('containers', [])
        require(len(containers) == 1 and containers[0].get('name') == 'db-admin' and containers[0].get('image') == image,
                'EXISTING_POD_IMAGE_CHANGED')
        require(not containers[0].get('env') and not containers[0].get('envFrom') and
                containers[0].get('securityContext', {}).get('readOnlyRootFilesystem') is True,
                'EXISTING_POD_RUNTIME_CHANGED')
        require(len(spec.get('volumes', [])) == 1 and spec['volumes'][0].get('configMap', {}).get('name') == 'backend-database-ca',
                'EXISTING_POD_PUBLIC_CA_VOLUME_CHANGED')
        current = containers[0].get('command')
        old = ['python', '-u', '-c', 'import time; time.sleep(3550)']
        require(current in (old, WAIT_COMMAND), 'EXISTING_POD_COMMAND_CHANGED')
        if current == old:
            result['old_pod_uid'] = pod['metadata']['uid']
            (folder / 'receipt.json').write_text(json.dumps(result))
            # Only the named idle diagnostic Pod is replaced. SA/Role/Binding remain.
            r.call(['delete', 'pod', name, '-n', NS, '--wait=true', '--timeout=40s'], 'replace-old-idle-pod')
            pod = None
            result['old_one_hour_pod_replaced'] = True
        else:
            require(not spec.get('activeDeadlineSeconds'), 'ACCESS_POD_STILL_HAS_DEADLINE')
            require(pod.get('status', {}).get('phase') not in ('Failed', 'Succeeded'), 'ACCESS_POD_TERMINATED')
            result['pod_reused'] = True
    if not pod:
        r.call(['create', '-f', '-', '-o', 'name'], 'create-persistent-access-pod', json.dumps(desired[3]).encode())


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('input_root', type=Path)
    group = p.add_mutually_exclusive_group()
    group.add_argument('--resume-from', type=Path)
    group.add_argument('--renew-from', type=Path)
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
    previous = accepted_receipt(root, args.resume_from or args.renew_from, bool(args.renew_from)) if (args.resume_from or args.renew_from) else None
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
              'human_recipient': 'A', 'linux_recipient': 'ansible', 'persistent_until_cleanup': True,
              'token_requested_seconds': TOKEN_SECONDS}
    try:
        require(r.call(['whoami', '--show-server'], 'target').stdout.decode().strip().rstrip('/') == handoff['api_url'].rstrip('/'), 'WRONG_CLUSTER')
        parent = r.call(['config', 'view', '--raw', '--flatten', '--minify', '-o', 'json'], 'cluster-trust')
        parent_config = json.loads(parent.stdout)
        template = limited_config(parent_config, handoff['api_url'], 'NOT_ISSUED')
        result['cluster_trust'] = ('EXPLICIT_CA' if template['clusters'][0]['cluster'].get('certificate-authority-data') else 'SYSTEM_TRUST')
        r.call(['get', '--raw=/version'], 'existing-verified-tls-api')
        result['tls_verification'] = True
        result['pull_lease'] = m.pull_lease(r, account)
        run = previous['pod'][11:] if previous else secrets.token_hex(5)
        name = 'data-admin-' + run
        result.update(namespace=NS, pod=name, cleanup_required=True)
        (folder / 'receipt.json').write_text(json.dumps(result))
        if previous:
            result['resumed_from'] = str(args.resume_from or args.renew_from)
            ensure_reused_objects(r, name, m.image_uri(account, 'backend'), folder, result)
        else:
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
        token = r.call(['create', 'token', name, '-n', NS, '--duration=12h', '--bound-object-kind=Pod',
                        '--bound-object-name=' + name, '--bound-object-uid=' + pod['metadata']['uid']], 'bound-token').stdout.decode().strip()
        expires = token_expiry(token, int(time.time()))
        cfg = limited_config(parent_config, handoff['api_url'], token)
        private_config = folder / 'delegated-kubeconfig'
        private_config.write_text(json.dumps(cfg))
        # Execute the real scoped token, not impersonation, for allow/deny checks.
        env = dict(os.environ, KUBECONFIG=str(private_config))
        a = subprocess.run([str(oc), '--request-timeout=20s', 'get', '--raw=/version'], env=env, capture_output=True, timeout=30)
        require(a.returncode == 0, 'DELEGATED_TLS_API_FAILED')
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
        if previous and previous.get('access_directory'):
            public = Path(previous['access_directory'])
            require(public.parent == Path('/tmp') and public.name.startswith('seokpan-data-access-')
                    and public.is_dir() and not public.is_symlink() and public.stat().st_uid == os.getuid(), 'EXISTING_HANDOFF_DIRECTORY_SCOPE')
        else:
            public = Path(tempfile.mkdtemp(prefix='seokpan-data-access-', dir='/tmp'))
        result['access_directory'] = str(public)
        kube = public / 'kubeconfig'
        require(not kube.is_symlink(), 'HANDOFF_KUBECONFIG_SYMLINK')
        tmp = public / ('kubeconfig-' + secrets.token_hex(4))
        tmp.write_text(json.dumps(cfg))
        done = subprocess.run(['setfacl', '-m', 'u:ansible:r--', str(tmp)], capture_output=True)
        require(done.returncode == 0, 'KUBECONFIG_ACL_FAILED')
        os.replace(tmp, kube)
        for filename in ('oc', 'activate.sh', 'A-handoff.txt'):
            require(not (public / filename).is_symlink(), 'HANDOFF_FILE_SYMLINK')
        if (public / 'oc').exists():
            require(m.sha((public / 'oc').read_bytes()) == m.sha(oc.read_bytes()), 'EXISTING_HANDOFF_OC_CHANGED')
        else:
            shutil.copyfile(oc, public / 'oc')
            (public / 'oc').chmod(0o700)
        expiry_kst = m.dt.datetime.fromtimestamp(expires, m.dt.timezone(m.dt.timedelta(hours=9))).isoformat()
        activation = ('export KUBECONFIG=' + shlex.quote(str(kube)) + '\n' +
                      'export PATH=' + shlex.quote(str(public)) + ':"$PATH"\n' + 'export DATA_ADMIN_POD=' + name + '\n' +
                      'export DATA_ADMIN_TOKEN_EXPIRES_AT=' + shlex.quote(expiry_kst) + '\n')
        (public / 'activate.sh').write_text(activation)
        reply = ('유빈님, Controller ansible 계정에서 아래 명령으로 접속하시면 됩니다.\n\n' +
            'source ' + str(public / 'activate.sh') + '\n' +
            'oc get pod ' + name + ' -n seokpan-cloud\n' +
            'oc exec -it -n seokpan-cloud ' + name + ' -c db-admin -- python\n\n' +
            'Pod 안 asyncmy 드라이버와 /run/rds-ca/ca.crt를 사용하고 RDS 실제 호스트 이름의 TLS 검증을 유지해 주세요.\n' +
            '마스터 계정으로 db_admin 설정 점검/필요 정정 뒤 db_admin TLS 로그인, @@GLOBAL.max_connections, '
            '@@GLOBAL.max_user_connections와 예약 연결 수를 알려 주세요. 비밀번호는 기존 보호 자료를 사용해 주세요.\n' +
            '접속 토큰 실제 만료: ' + expiry_kst + ' (KST). Pod는 작업 완료까지 유지합니다. '
            '연장이 필요하면 Pod 재생성 없이 토큰을 갱신하고, 완료 후 임시 권한/Pod/파일을 정리하겠습니다.\n')
        (public / 'A-handoff.txt').write_text(reply)
        for filename in ('activate.sh', 'A-handoff.txt'):
            done = subprocess.run(['setfacl', '-m', 'u:ansible:r--', str(public / filename)], capture_output=True)
            require(done.returncode == 0, 'FILE_ACL_FAILED')
        for path in (public, public / 'oc'):
            done = subprocess.run(['setfacl', '-m', 'u:ansible:r-x', str(path)], capture_output=True)
            require(done.returncode == 0, 'EXECUTION_ACL_FAILED')
        result.update(status='PASS_A_SINGLE_POD_ACCESS_PREPARED', access_directory=str(public),
                      activation_command='source ' + str(public / 'activate.sh'),
                      expires_at_utc=m.dt.datetime.fromtimestamp(expires, m.dt.timezone.utc).isoformat(),
                      expires_at_kst=expiry_kst, token_actual_seconds=expires - int(time.time()),
                      renewal_command='python3 ' + shlex.quote(str(Path(__file__).resolve())) + ' ' + shlex.quote(str(root)) +
                          ' --renew-from ' + shlex.quote(str(folder)),
                      a_handoff_file=str(public / 'A-handoff.txt'),
                      access_scope_checks=checks, driver_import='PASS', ca_file='/run/rds-ca/ca.crt',
                      token_bound_to_pod=True, cleanup_resources=['Pod', 'RoleBinding', 'Role', 'ServiceAccount'])
    except Exception as exc:
        result.update(status='STOP_DATA_OPERATOR_ACCESS', reason=str(exc) if isinstance(exc, (RuntimeError, m.Stop)) else type(exc).__name__,
                      automatic_retry=False)
    finally:
        (folder / 'receipt.json').write_text(json.dumps(result, sort_keys=True) + '\n')
        print(json.dumps(result, sort_keys=True))
        print('Private access preparation directory: ' + str(folder))
        if result['status'] == 'PASS_A_SINGLE_POD_ACCESS_PREPARED':
            print('A_REPLY_BEGIN\n' + reply + 'A_REPLY_END')
        os.close(lock)
    return 0 if result['status'].startswith('PASS_') else 1


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(json.dumps({'status': 'STOP_BEFORE_DATA_ACCESS', 'error_class': type(exc).__name__}))
        raise SystemExit(1)
