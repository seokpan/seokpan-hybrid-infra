"""Capture only missing restoration inputs into an encrypted supplement.

No Kubernetes writes, AWS writes, Plan, Apply, app activation or old verification.
Existing emergency credentials are reused in an isolated kubeconfig if needed.
"""
import argparse
import datetime as dt
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import time

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('checkpoint', HERE/'prepare-rosa-checkpoint.py')
B = importlib.util.module_from_spec(spec)
spec.loader.exec_module(B)
need = B.need


def object_id(o):
    m = o['metadata']
    return '|'.join([o['apiVersion'], o['kind'], m.get('namespace', ''), m['name']])


def admin_login(credentials, kubeconfig, api):
    import pty
    import select
    import termios
    admin = json.loads(B.private_file(credentials, 65536))
    need(admin.get('api_url', '').rstrip('/') == api.rstrip('/')
         and admin.get('username') == 'cluster-admin'
         and isinstance(admin.get('password'), str) and len(admin['password']) >= 12,
         'EXISTING_ADMIN_API_BINDING_REQUIRED')
    # No password in argv, environment, logs or fallback terminal output.
    master, slave = pty.openpty()
    attrs = termios.tcgetattr(slave)
    attrs[3] &= ~termios.ECHO
    termios.tcsetattr(slave, termios.TCSANOW, attrs)
    proc = subprocess.Popen(['oc', 'login', api, '--username', 'cluster-admin',
        '--kubeconfig', str(kubeconfig), '--request-timeout=20s'],
        stdin=slave, stdout=slave, stderr=slave, close_fds=True)
    os.close(slave)
    transcript = bytearray()
    sent = False
    deadline = time.monotonic()+75
    try:
        while time.monotonic() < deadline:
            ready, _, _ = select.select([master], [], [], 0.25)
            if ready:
                try:
                    chunk = os.read(master, 65536)
                except OSError:
                    chunk = b''
                if not chunk:
                    break
                transcript.extend(chunk)
                need(len(transcript) < 1048576, 'LOGIN_OUTPUT_BOUND_EXCEEDED')
                if not sent and b'password:' in bytes(transcript).lower():
                    os.write(master, (admin['password']+'\n').encode())
                    sent = True
            if proc.poll() is not None and not ready:
                break
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        os.close(master)
    need(proc.returncode == 0, 'EXISTING_EMERGENCY_LOGIN_FAILED_NO_RESET_OR_RETRY')


def validate_pv(claim, pv):
    ref = pv['spec'].get('claimRef', {})
    need(ref.get('uid') == claim['uid'] and ref.get('name') == claim['name']
         and ref.get('namespace') == claim['namespace'], 'PV_CLAIM_OWNERSHIP_CHANGED')
    csi = pv['spec'].get('csi', {})
    need(csi.get('driver') == 'ebs.csi.aws.com'
         and re.fullmatch(r'vol-[0-9a-f]+', csi.get('volumeHandle', '')),
         'EXACT_EBS_CSI_VOLUME_REQUIRED')
    return {'namespace': claim['namespace'], 'claim': claim['name'],
            'claim_uid': claim['uid'], 'pv': pv['metadata']['name'],
            'pv_uid': pv['metadata']['uid'], 'volume_id': csi['volumeHandle'],
            'capacity': pv['spec'].get('capacity'),
            'node_affinity': pv['spec'].get('nodeAffinity'),
            'reclaim_policy': pv['spec'].get('persistentVolumeReclaimPolicy'),
            'data_preserved': False, 'restore_volume': 'INPUT_REQUIRED',
            'consistency': 'LIVE_MONITORING_VOLUME_NOT_QUIESCED'}


def restore_inventory(objects):
    rows = []
    for o in objects:
        kind, m = o['kind'], o['metadata']
        action = 'REVIEW_BEFORE_REPLAY'
        if kind == 'Secret':
            action = 'REGENERATE_OPERATOR_OR_TOKEN' if (
                o.get('type') == 'kubernetes.io/service-account-token'
                or m.get('ownerReferences') or m.get('name') in (
                    'argocd-secret', 'openshift-gitops-cluster',
                    'openshift-gitops-tls', 'openshift-gitops-ca')) else 'PROTECTED_RESUPPLY_REVIEW'
        elif kind in ('Namespace', 'Group', 'Role', 'RoleBinding', 'NetworkPolicy',
                      'ConfigMap', 'ServiceAccount'):
            action = 'RESTORE_DECLARATION_WITH_FRESH_METADATA'
        elif kind in ('ClusterRole', 'ClusterRoleBinding', 'ClusterServiceVersion'):
            action = 'OPERATOR_RECONCILE_AND_COMPARE_EFFECTIVE_PERMISSIONS'
        elif kind in ('Subscription', 'OperatorGroup', 'ArgoCD'):
            action = 'PINNED_OPERATOR_BOOTSTRAP'
        elif kind == 'OAuth':
            action = 'MAP_NEW_CALLBACK_AND_RESTORE_SELECTED_PROVIDER'
        elif kind == 'Application':
            need(o.get('spec', {}).get('syncPolicy', {}).get('automated') is None,
                 'AUTOMATIC_APPLICATION_CANNOT_BE_RESTORED_AS_HELD')
            action = 'RESTORE_MANUAL_APPLICATION_NO_SYNC'
        elif kind in ('Deployment', 'StatefulSet') and m.get('namespace') == 'seokpan-cloud':
            need(o.get('spec', {}).get('replicas') == 0, 'CLOUD_WORKLOAD_NOT_HELD')
            action = 'KEEP_ZERO_REPLICAS'
        rows.append({'id': object_id(o), 'source_uid': m.get('uid'), 'action': action,
                     'restored': False})
    return rows


def capture(base, get):
    contract = base['contract']
    argo = contract['argo_namespace']
    old = {object_id(o): o for o in base['snapshots']}
    extra = {}
    def add(o):
        if o is not None and object_id(o) not in old:
            extra[object_id(o)] = o
    def listed(resource, namespace=None):
        return get(resource, namespace=namespace)['items']
    # Reuse the accepted OAuth object; fetch only its previously inaccessible inputs.
    oauths = [o for o in old.values() if o['kind'] == 'OAuth']
    need(len(oauths) == 1, 'ACCEPTED_OAUTH_SNAPSHOT_REQUIRED')
    provider_names = set(contract['identity_providers'])
    providers = [o for o in oauths[0]['spec']['identityProviders'] if o['name'] in provider_names]
    need({o['name'] for o in providers} == provider_names, 'SELECTED_IDENTITY_PROVIDER_REQUIRED')
    for provider in providers:
        name = provider.get('github', {}).get('clientSecret', {}).get('name')
        need(name, 'EXISTING_GITHUB_SECRET_REFERENCE_REQUIRED')
        if 'v1|Secret|openshift-config|'+name not in old:
            secret = get('secret', name, 'openshift-config')
            need(secret.get('data', {}).get('clientSecret'), 'OAUTH_CLIENT_SECRET_PAYLOAD_REQUIRED')
            add(secret)
    add(get('namespace', argo))
    for resource in ('operatorgroups', 'subscriptions', 'clusterserviceversions',
                     'roles', 'rolebindings', 'serviceaccounts', 'secrets', 'configmaps'):
        for obj in listed(resource, argo):
            if obj.get('type') != 'kubernetes.io/service-account-token':
                add(obj)
    # Read cluster bindings once, then capture exact roles referenced by this namespace.
    role_names = set()
    for binding in listed('clusterrolebindings'):
        subjects = binding.get('subjects', [])
        if any(s.get('kind') == 'ServiceAccount' and s.get('namespace') == argo for s in subjects):
            add(binding)
            role_names.add(binding['roleRef']['name'])
    for role in sorted(role_names):
        add(get('clusterrole', role))
    # Previous accepted PVC list is reused. These exact PV CSI handles were not captured.
    volumes = []
    claims = base['pvc_inventory']
    need(len(claims) == 4 and all(c['namespace'] == 'openshift-monitoring' for c in claims),
         'ACCEPTED_FOUR_MONITORING_CLAIMS_REQUIRED')
    for claim in claims:
        pv = get('pv', claim['volume'])
        volumes.append(validate_pv(claim, pv))
        add(pv)
    need(len({v['volume_id'] for v in volumes}) == 4, 'FOUR_DISTINCT_EBS_VOLUMES_REQUIRED')
    for name in ('alertmanager-main', 'prometheus-k8s'):
        add(get('statefulset', name, 'openshift-monitoring'))
    inventory = restore_inventory(list(old.values())+list(extra.values()))
    return list(extra.values()), volumes, inventory


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('archive', 'archive-sha256', 'key-file', 'admin-kubeconfig',
                 'admin-credentials', 'contract', 'output-parent'):
        p.add_argument('--'+name, required=True)
    p.add_argument('--sops')
    p.add_argument('--age-keygen')
    a = p.parse_args()
    os.umask(0o077)
    import tempfile
    out = Path(tempfile.mkdtemp(prefix='rosa-restoration-gaps-', dir=a.output_parent))
    result = {'status': 'STOP_RESTORATION_GAP_CAPTURE', 'deletion_ready': False,
              'kubernetes_mutations': 0, 'aws_mutations': 0, 'SQL': 0,
              'plan_repeated': False, 'previous_verification_repeated': False,
              'automatic_retry': False, 'phase': 'LOCAL_INPUTS'}
    try:
        raw = B.private_file(a.archive, B.MAX_BYTES*3)
        need(hashlib.sha256(raw).hexdigest() == a.archive_sha256, 'ACCEPTED_ARCHIVE_HASH_REQUIRED')
        c = json.loads(B.private_file(a.contract, 262144))
        binary = B.sops_binary(a, out)
        # Decrypt for a new task: source mapping and missing-input selection, not a repeat verification.
        base = json.loads(B.crypt(binary, raw, decrypt=True, key_file=a.key_file))
        need(base.get('schema') == 1 and base.get('contract') == c, 'ARCHIVE_CONTRACT_BINDING_REQUIRED')
        api = c['expected_api']
        kubeconfig = out/'emergency-kubeconfig'
        kubeconfig.write_bytes(B.private_file(a.admin_kubeconfig, 262144))
        prefix = ['oc', '--kubeconfig', str(kubeconfig), '--request-timeout=20s']
        local = subprocess.run(prefix+['config', 'view', '--minify', '-o', 'json'], capture_output=True, timeout=25)
        need(local.returncode == 0, 'EXISTING_ADMIN_TRUST_CONFIG_REQUIRED')
        trust = json.loads(local.stdout)['clusters'][0]['cluster']
        need(trust.get('server', '').rstrip('/') == api.rstrip('/') and not trust.get('insecure-skip-tls-verify'),
             'ACCEPTED_API_TRUST_REQUIRED')
        result['phase'] = 'EXISTING_EMERGENCY_SESSION'
        who = subprocess.run(prefix+['whoami'], capture_output=True, timeout=25)
        if who.returncode:
            auth_error = any(v in who.stderr.lower() for v in (b'unauthorized', b'must be logged in', b'provide credentials'))
            need(auth_error, 'ADMIN_SESSION_READ_FAILED_NO_LOGIN_RETRY')
            admin_login(a.admin_credentials, kubeconfig, api)
            who = subprocess.run(prefix+['whoami'], capture_output=True, timeout=25)
        need(who.returncode == 0 and who.stdout.decode().strip() == 'cluster-admin',
             'EXISTING_CLUSTER_ADMIN_IDENTITY_REQUIRED')
        def get(resource, name=None, namespace=None):
            argv = prefix+['get', resource]+([name] if name else [])
            if namespace:
                argv += ['-n', namespace]
            r = subprocess.run(argv+['-o', 'json'], capture_output=True, timeout=35)
            if r.returncode:
                result['failed_read'] = {'resource': resource, 'namespace': namespace,
                                         'classification': 'FORBIDDEN' if b'forbidden' in r.stderr.lower() else 'READ_FAILED'}
                raise RuntimeError('SCOPED_GAP_READ_FAILED_NO_GRANT_OR_RETRY')
            return json.loads(r.stdout)
        result['phase'] = 'MISSING_INPUTS_AND_VOLUME_MAPPING'
        snapshots, volumes, inventory = capture(base, get)
        bundle = {'schema': 1, 'origin_host': socket.gethostname(),
            'captured_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
            'purpose': 'RESTORATION_MISSING_INPUTS_NOT_OLD_BACKUP_REPEAT',
            'base_archive_sha256': a.archive_sha256, 'snapshots': snapshots, 'files': [],
            'volume_preservation_plan': volumes, 'restoration_inventory': inventory,
            'volume_data_backed_up': False, 'automatic_sync': False}
        cipher = B.private_file(c['recipient_source'], 262144)
        need(hashlib.sha256(cipher).hexdigest() == c['recipient_source_sha256'], 'RECIPIENT_SOURCE_HASH_MISMATCH')
        recipients, _ = B.age_recipients(cipher)
        recipient = B.key_matched_recipient(recipients, a, out)
        result['phase'] = 'NEW_SUPPLEMENT_ENCRYPTION'
        plain = B.canonical(bundle)
        encrypted = B.crypt(binary, plain, recipient=recipient)
        archive = out/'restoration-gaps.sops.json'
        archive.write_bytes(encrypted)
        need(B.canonical(json.loads(B.crypt(binary, encrypted, decrypt=True, key_file=a.key_file))) == plain,
             'NEW_SUPPLEMENT_ROUNDTRIP_MISMATCH')
        # CSI handles and credential values remain inside the encrypted supplement.
        result.update(status='PASS_RESTORATION_GAPS_ENCRYPTED_VOLUME_PLAN_PREPARED',
            phase='COMPLETE', archive=str(archive), archive_sha256=hashlib.sha256(encrypted).hexdigest(),
            missing_snapshot_objects=len(snapshots), restoration_inventory_objects=len(inventory),
            exact_monitoring_ebs_volumes=len(volumes), volume_data_backed_up=False,
            manual_secret_supply='CAPTURED_REPLAY_CLASSIFICATION_REMAINS',
            independent_copy_of_new_supplement='NOT_RUN',
            actual_restoration='NOT_RUN', previous_accepted_archives_recreated=False)
    except Exception as error:
        result['reason'] = str(error) if type(error) is RuntimeError else type(error).__name__
    (out/'receipt.json').write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))
    print('Private restoration gap directory: '+str(out))
    return 0 if result['status'].startswith('PASS_') else 1


if __name__ == '__main__':
    raise SystemExit(main())
