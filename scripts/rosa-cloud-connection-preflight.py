#!/usr/bin/env python3
"""Human-run Cloud connection probe. No application start, migration or DB writes.

Reuses management/SG/Data receipts. Creates an owned application Namespace if
absent and one bounded, temporary Pod using the approved Backend image. Secret
values travel in memory/stdin only; output contains status and counts.
"""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import ssl
import subprocess
import tempfile
import time
import urllib.request


class Stop(Exception):
    pass


def require(ok, reason):
    if not ok:
        raise Stop(reason)


def read_json(path):
    require(path.is_file() and not path.is_symlink(), 'REGULAR_JSON_REQUIRED')
    return json.loads(path.read_text())


def write_json(path, value):
    require(not path.exists() and not path.is_symlink(), 'NEW_OUTPUT_REQUIRED')
    with path.open('x') as stream:
        json.dump(value, stream, sort_keys=True)
    path.chmod(0o600)


def emit(status, **fields):
    print(json.dumps(dict(status=status, **fields), sort_keys=True), flush=True)


def download(url, limit=131072):
    with urllib.request.urlopen(url, timeout=30) as response:
        data = response.read(limit + 1)
    require(len(data) <= limit, 'PUBLIC_CA_RESPONSE_TOO_LARGE')
    return data


def approved_data(raw, expected_sha):
    require(hashlib.sha256(raw).hexdigest() == expected_sha, 'ACCEPTED_DATA_FILE_CHANGED')
    data = json.loads(raw)
    result = {k: data[k].get('value') if isinstance(data[k], dict) else data[k]
              for k in ('rds_address', 'rds_port', 'redis_primary_endpoint', 'redis_port')}
    for key, suffix in (('rds_address', '.rds.amazonaws.com'),
                        ('redis_primary_endpoint', '.cache.amazonaws.com')):
        require(isinstance(result[key], str) and len(result[key]) < 254 and
                re.fullmatch(r'[a-z0-9][a-z0-9.-]+', result[key]) is not None and
                result[key].endswith(suffix), 'DIRECT_AWS_ENDPOINT_REQUIRED')
    require(str(result['rds_port']) == '3306' and str(result['redis_port']) == '6379', 'DATA_PORT_CONTRACT')
    return result


def probe_pod(name, namespace, image, run_id):
    require(re.fullmatch(r'[0-9]{12}\.dkr\.ecr\.ap-northeast-2\.amazonaws\.com/seokpan-fnd-backend@sha256:[0-9a-f]{64}', image) is not None, 'PINNED_BACKEND_ECR_IMAGE_REQUIRED')
    return {'apiVersion': 'v1', 'kind': 'Pod',
            'metadata': {'name': name, 'namespace': namespace,
                         'labels': {'app.kubernetes.io/part-of': 'seokpan', 'seokpan.io/probe-run': run_id}},
            'spec': {'restartPolicy': 'Never', 'activeDeadlineSeconds': 300,
                     'terminationGracePeriodSeconds': 5, 'automountServiceAccountToken': False,
                     'nodeSelector': {'node-role.kubernetes.io/worker': ''},
                     'affinity': {'nodeAffinity': {'requiredDuringSchedulingIgnoredDuringExecution': {
                         'nodeSelectorTerms': [{'matchExpressions': [
                             {'key': 'node-role.kubernetes.io/' + role, 'operator': 'DoesNotExist'}
                             for role in ('infra', 'master', 'control-plane')]}]}}},
                     'securityContext': {'runAsNonRoot': True, 'seccompProfile': {'type': 'RuntimeDefault'}},
                     'containers': [{'name': 'probe', 'image': image, 'imagePullPolicy': 'Always',
                                     'command': ['python', '-u', '-c', 'import time; time.sleep(270)'],
                                     'resources': {'requests': {'cpu': '25m', 'memory': '64Mi'},
                                                   'limits': {'cpu': '100m', 'memory': '128Mi'}},
                                     'securityContext': {'allowPrivilegeEscalation': False,
                                                         'capabilities': {'drop': ['ALL']},
                                                         'readOnlyRootFilesystem': True}}]}}


PROBE = r'''
import asyncio,hashlib,json,socket,ssl,struct,sys
p=json.load(sys.stdin);out={}
def classify(e):
    if isinstance(e,ssl.SSLCertVerificationError):return 'TLS_CA_OR_HOSTNAME_REJECTED'
    if isinstance(e,(TimeoutError,socket.timeout)):return 'CONNECTION_TIMEOUT'
    if isinstance(e,socket.gaierror):return 'DNS_FAILED'
    if isinstance(e,ConnectionRefusedError):return 'CONNECTION_REFUSED'
    code=e.args[0] if getattr(e,'args',None) else None
    if code==1045:return 'ACCOUNT_NOT_READY_OR_AUTH_REJECTED'
    if code==1044:return 'ACCOUNT_PERMISSION_REJECTED'
    if code==1049:return 'DATABASE_NOT_IMPORTED'
    return type(e).__name__
def ctx(ca):
    c=ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT);c.load_verify_locations(cadata=ca)
    c.minimum_version=ssl.TLSVersion.TLSv1_2
    return c
def recv_exact(s,n):
    b=b''
    while len(b)<n:
        chunk=s.recv(n-len(b))
        if not chunk:raise RuntimeError('MYSQL_EARLY_EOF')
        b+=chunk
    return b
def packet(s):
    h=recv_exact(s,4);n=int.from_bytes(h[:3],'little')
    if n>65536:raise RuntimeError('MYSQL_GREETING_SIZE')
    return recv_exact(s,n)
def rds_tls():
    with socket.create_connection((p['rds_address'],3306),timeout=8) as s:
        greeting=packet(s)
        if not greeting or greeting[0]!=10:raise RuntimeError('MYSQL_GREETING_FORMAT')
        pos=greeting.index(b'\0',1)+1
        lower=int.from_bytes(greeting[pos+13:pos+15],'little')
        if not lower & 0x0800:raise RuntimeError('MYSQL_SERVER_TLS_UNAVAILABLE')
        flags=0x0200|0x0800|0x8000
        payload=struct.pack('<IIB23x',flags,1024*1024,45)
        s.sendall(len(payload).to_bytes(3,'little')+b'\x01'+payload)
        with ctx(p['rds_ca']).wrap_socket(s,server_hostname=p['rds_address']) as tls:
            return {'status':'PASS','tls_version':tls.version(),'hostname_verified':True,
                    'leaf_sha256':hashlib.sha256(tls.getpeercert(binary_form=True)).hexdigest()}
try:out['rds_tls']=rds_tls()
except Exception as e:out['rds_tls']={'status':classify(e)}
try:
    with socket.create_connection((p['redis_primary_endpoint'],6379),timeout=8) as s:
        with ctx(p['redis_ca']).wrap_socket(s,server_hostname=p['redis_primary_endpoint']) as tls:
            out['valkey_tls']={'status':'PASS','tls_version':tls.version(),'hostname_verified':True,
                              'leaf_sha256':hashlib.sha256(tls.getpeercert(binary_form=True)).hexdigest()}
            stream=tls.makefile('rb')
            def command(*args):
                parts=[x.encode() if isinstance(x,str) else x for x in args]
                tls.sendall(b'*'+str(len(parts)).encode()+b'\r\n'+b''.join(b'$'+str(len(x)).encode()+b'\r\n'+x+b'\r\n' for x in parts))
                return stream.readline(2048).strip()
            if p.get('redis_auth_token'):
                response=command('AUTH',p['redis_auth_token'])
                if response!=b'+OK':out['valkey_auth_ping']={'status':'AUTH_REJECTED'}
                else:out['valkey_auth_ping']={'status':'PASS' if command('PING')==b'+PONG' else 'PING_REJECTED'}
            else:out['valkey_auth_ping']={'status':'PENDING_PROTECTED_AUTH_INPUT'}
except Exception as e:
    if out.get('valkey_tls',{}).get('status')=='PASS':out['valkey_auth_ping']={'status':classify(e)}
    else:out['valkey_tls']={'status':classify(e)}
async def sql():
    if not p.get('db_admin_password'):return {'status':'PENDING_PROTECTED_DB_ADMIN_INPUT'}
    import asyncmy
    con=await asyncmy.connect(host=p['rds_address'],port=3306,user='db_admin',password=p['db_admin_password'],
                             ssl=ctx(p['rds_ca']),connect_timeout=8,autocommit=True)
    try:
        async with con.cursor() as cur:
            await cur.execute('SELECT @@GLOBAL.max_connections, @@GLOBAL.time_zone, USER(), CURRENT_USER()')
            limit,zone,user,current=await cur.fetchone()
            await cur.execute("SHOW SESSION STATUS LIKE 'Ssl_cipher'")
            cipher=await cur.fetchone()
            await cur.execute("SELECT COUNT(*) FROM information_schema.schemata WHERE schema_name='stone_game'")
            exists=(await cur.fetchone())[0]==1
            result={'status':'PASS','max_connections':int(limit),'time_zone_is_seoul':zone=='Asia/Seoul',
                    'expected_db_admin_account':current.split('@')[0]=='db_admin','tls_cipher_present':bool(cipher and cipher[1]),
                    'connection_source_sha256':hashlib.sha256(user.encode()).hexdigest(), 'stone_game_visible':exists}
            if not result['expected_db_admin_account'] or not result['tls_cipher_present']:
                result['status']='TLS_OR_ACCOUNT_CONTRACT_REJECTED'
            if exists:
                await cur.execute("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='stone_game' AND table_name='alembic_version'")
                revision_visible=(await cur.fetchone())[0]==1
                result['alembic_version_visible']=revision_visible
                if revision_visible:
                    await cur.execute('SELECT version_num FROM stone_game.alembic_version')
                    result['expected_revision_match']=[r[0] for r in await cur.fetchall()]==['20260902_0002']
            return result
    finally:con.close()
try:
    out['sql_read_only']=asyncio.run(asyncio.wait_for(sql(),timeout=35))
except Exception as e:out['sql_read_only']={'status':classify(e)}
out['application_started']=False;out['database_writes']=0;out['redis_data_writes']=0
print(json.dumps(out,sort_keys=True))
'''


class Runner:
    def __init__(self, oc, kubeconfig, folder):
        self.oc = str(oc)
        self.env = dict(os.environ, KUBECONFIG=str(kubeconfig), AWS_PAGER='')
        self.folder = folder

    def run(self, args, label, payload=None, ok=(0,), timeout=45):
        result = subprocess.run([self.oc, *args], env=self.env, input=payload,
                                capture_output=True, timeout=timeout)
        # stderr may contain endpoints. Never emit it or payload to console.
        (self.folder / (label + '.stderr.log')).write_bytes(result.stderr)
        require(result.returncode in ok, label.upper().replace('-', '_') + '_FAILED')
        return result

    def get(self, args, label):
        result = self.run([*args, '-o', 'json', '--request-timeout=30s'], label)
        return json.loads(result.stdout) if result.stdout.strip() else None


def load_optional_secret(path, expected_prefix, fields):
    status = {'available': path.is_file(), 'decrypt': 'NOT_RUN'}
    if not path.is_file():
        return {}, status
    info = path.lstat()
    require(not path.is_symlink() and info.st_uid == os.getuid() and info.st_mode & 0o077 == 0,
            'PRIVATE_SOPS_FILE_PERMISSIONS')
    raw = path.read_bytes()
    require(hashlib.sha256(raw).hexdigest().startswith(expected_prefix), 'PREVIOUSLY_SUPPLIED_SOPS_CHANGED')
    if not shutil.which('sops'):
        return {}, dict(status, decrypt='SOPS_NOT_INSTALLED')
    proc = subprocess.run(['sops', '--decrypt', '--input-type', 'yaml', '--output-type', 'json', '/dev/stdin'],
                          input=raw, capture_output=True, timeout=30)
    if proc.returncode:
        return {}, dict(status, decrypt='DECRYPT_FAILED_NO_RAW_OUTPUT')
    value = json.loads(proc.stdout)
    result = {key: value[key] for key in fields if isinstance(value.get(key), str) and value[key]}
    return result, dict(status, decrypt='PASS', expected_fields_present=sorted(result))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input_root', type=Path)
    parser.add_argument('--data-file', required=True, type=Path)
    parser.add_argument('--data-sha256', required=True)
    parser.add_argument('--backend-image', required=True)
    parser.add_argument('--namespace', default='seokpan-cloud')
    args = parser.parse_args()
    require(os.name == 'posix' and os.getuid() != 0, 'USE_PERSONAL_CONTROLLER_LINUX_ACCOUNT')
    require(re.fullmatch(r'seokpan-cloud(?:-[a-z0-9]+)?', args.namespace) is not None, 'DEDICATED_CLOUD_NAMESPACE_REQUIRED')
    os.umask(0o077)
    root = args.input_root
    # Derive the already accepted cluster identity without repeating creation/readiness.
    inputs = read_json(root / 'protected/inputs.tfvars.json')
    account = inputs['foundation']['account_id']
    require(args.backend_image.startswith(account + '.dkr.ecr.ap-northeast-2.amazonaws.com/'), 'ECR_ACCOUNT_MISMATCH')
    matches = []
    for management in (root / 'management').iterdir():
        receipt_path = management / 'data-binding/receipt.json'
        if receipt_path.is_file():
            receipt = read_json(receipt_path)
            if receipt.get('status') in ('PASS_DATA_CONNECTION_RULES_APPLIED', 'PASS_DATA_CONNECTION_RULES_ALREADY_PRESENT'):
                matches.append(management)
    require(len(matches) == 1, 'ONE_SUCCESSFUL_DATA_BINDING_REQUIRED')
    management = matches[0]
    handoff = read_json(management / 'data-platform-handoff.json')
    require(handoff.get('cluster_id') == management.name and handoff.get('binding_enabled') is True,
            'COMPLETED_DATA_HANDOFF_REQUIRED')
    oc_dir = Path.home() / '.local/share/seokpan/oc-cli-4.20'
    oc = oc_dir / 'oc'
    require(oc.is_file() and not oc.is_symlink() and hashlib.sha256(oc.read_bytes()).hexdigest() ==
            read_json(oc_dir / 'receipt.json')['oc_sha256'], 'EXISTING_VERIFIED_OC_REQUIRED')
    kubeconfig = management / 'kubeconfig'
    require(kubeconfig.is_file() and not kubeconfig.is_symlink(), 'EXISTING_KUBECONFIG_REQUIRED')
    folder = Path(tempfile.mkdtemp(prefix='cloud-connection-', dir=root))
    r = Runner(oc, kubeconfig, folder)
    pod = None
    phase = 'CURRENT_TARGET_AND_PROTECTED_INPUTS'
    try:
        server = r.run(['whoami', '--show-server'], 'server').stdout.decode().strip()
        require(server.rstrip('/') == handoff['api_url'].rstrip('/'), 'WRONG_CLUSTER_CONTEXT')
        data = approved_data(args.data_file.read_bytes(), args.data_sha256)
        ingress = r.get(['get', 'ingress.config.openshift.io', 'cluster'], 'ingress-domain')
        domain = ingress['spec']['domain']
        require(isinstance(domain, str) and re.fullmatch(r'apps\.[a-z0-9.-]+\.openshiftapps\.com', domain) is not None,
                'ACTUAL_ROSA_INGRESS_DOMAIN_REQUIRED')
        origin = 'https://' + args.namespace + '.' + domain
        rds_ca = download('https://truststore.pki.rds.amazonaws.com/ap-northeast-2/ap-northeast-2-bundle.pem')
        require(hashlib.sha256(rds_ca).hexdigest() == '913fb5b814f17af79d4c1622584a8d0ceddf5b0d76fe353d0c7d1186cdd6b229', 'ACCEPTED_RDS_CA_CHANGED')
        # ATS recommends all five roots for custom trust stores. Verify each
        # official root; never trust a certificate copied from an unverified peer.
        root_specs = (
            ('AmazonRootCA1.pem', '8ecde6884f3d87b1125ba31ac3fcb13d7016de7f57cc904fe1cb97c6ae98196e'),
            ('AmazonRootCA2.pem', '1ba5b2aa8c65401a82960118f80bec4f62304d83cec4713a19c39c011ea46db4'),
            ('AmazonRootCA3.pem', '18ce6cfe7bf14e60b2e347b8dfe868cb31d02ebb3ada271569f50343b46db3a4'),
            ('AmazonRootCA4.pem', 'e35d28419ed02025cfa69038cd623962458da5c695fbdea3c22b0bfb25897092'),
            ('SFSRootCAG2.pem', '568d6905a2c88708a4b3025190edcfedb1974a606a13c6e5290fcb2ae63edab5'))
        roots = []
        for name, digest in root_specs:
            pem = download('https://www.amazontrust.com/repository/' + name).decode('ascii')
            require(hashlib.sha256(ssl.PEM_cert_to_DER_cert(pem)).hexdigest() == digest, 'OFFICIAL_ATS_ROOT_CHANGED')
            roots.append(pem.strip() + '\n')
        redis_ca = ''.join(roots)
        (folder / 'rds-ca.pem').write_bytes(rds_ca)
        (folder / 'valkey-ca-candidate.pem').write_text(redis_ca)
        sql, sql_status = load_optional_secret(Path.home() / 'secrets/seokpan/data-sql-accounts.sops.yaml', '021bd0da994a', ('db_admin', 'identity_svc', 'game_svc'))
        auth, auth_status = load_optional_secret(Path.home() / 'secrets/seokpan/foundation-data.sops.yaml', '9a86f90e6ba6', ('redis_auth_token',))
        emit('CLOUD_PROTECTED_INPUTS_READ', sql=sql_status, valkey=auth_status, secret_values_printed=False)
        phase = 'NAMESPACE_AND_APPROVED_IMAGE_PROBE'
        ns = r.get(['get', 'namespace', args.namespace, '--ignore-not-found'], 'namespace')
        if ns is None:
            obj = {'apiVersion': 'v1', 'kind': 'Namespace', 'metadata': {'name': args.namespace,
                   'labels': {'app.kubernetes.io/part-of': 'seokpan', 'seokpan.io/environment': 'cloud'}}}
            ns = json.loads(r.run(['create', '-f', '-', '-o', 'json'], 'namespace-create', json.dumps(obj).encode()).stdout)
            emit('CLOUD_NAMESPACE_CREATED', application_replicas_started=0)
        else:
            labels = ns['metadata'].get('labels', {})
            require(labels.get('app.kubernetes.io/part-of') == 'seokpan' and labels.get('seokpan.io/environment') == 'cloud',
                    'EXISTING_NAMESPACE_OWNERSHIP_REQUIRES_REVIEW')
        run_id = hashlib.sha256(folder.name.encode()).hexdigest()[:12]
        name = 'cloud-connection-probe-' + run_id
        obj = probe_pod(name, args.namespace, args.backend_image, run_id)
        pod = json.loads(r.run(['create', '-f', '-', '-o', 'json'], 'probe-create', json.dumps(obj).encode()).stdout)
        deadline = time.monotonic() + 150
        observed = pod
        while time.monotonic() < deadline:
            observed = r.get(['get', 'pod', name, '-n', args.namespace], 'probe-status')
            state = observed.get('status', {})
            if state.get('phase') in ('Failed', 'Succeeded'):
                raise Stop('PROBE_TERMINATED_BEFORE_CONNECTION_CHECK')
            if any(x.get('type') == 'Ready' and x.get('status') == 'True' for x in state.get('conditions', [])):
                break
            time.sleep(5)
        else:
            reasons = [x.get('state', {}).get('waiting', {}).get('reason', 'UNKNOWN')
                       for x in observed.get('status', {}).get('containerStatuses', [])]
            emit('PROBE_NOT_READY', reasons=[x if x in ('ImagePullBackOff', 'ErrImagePull', 'ContainerCreating', 'CreateContainerConfigError', 'CrashLoopBackOff') else 'OTHER' for x in reasons])
            raise Stop('APPROVED_IMAGE_PULL_OR_SCHEDULING_NOT_READY')
        image_id = observed['status']['containerStatuses'][0].get('imageID', '')
        require('sha256:' in image_id, 'ACTUAL_PULLED_IMAGE_ID_REQUIRED')
        write_json(folder / 'probe-pod.json', observed)
        emit('PASS_APPROVED_ECR_BACKEND_POD_RUNNING', compute_node=True, pod_count=1, app_server_started=False)
        phase = 'CLOUD_TLS_AUTH_AND_READ_ONLY_SQL'
        payload = dict(data, rds_ca=rds_ca.decode('ascii'), redis_ca=redis_ca,
                       db_admin_password=sql.get('db_admin'), redis_auth_token=auth.get('redis_auth_token'))
        result = r.run(['exec', '-i', '-n', args.namespace, name, '--', 'python', '-c', PROBE],
                       'connection-probe', json.dumps(payload).encode(), timeout=110)
        sql.clear();auth.clear();payload.clear()
        actual = json.loads(result.stdout)
        write_json(folder / 'connection-result.json', actual)
        emit('CLOUD_ACTUAL_CONNECTION_RESULTS', **actual)
        ca_accepted = actual.get('valkey_tls', {}).get('status') == 'PASS'
        sql_result = actual.get('sql_read_only', {})
        budget = sql_result.get('max_connections') if sql_result.get('status') == 'PASS' else None
        # This is a proposal. Never silently adopt a pool budget or start writers.
        pending = []
        if not budget:pending.append('C_RDS_MAX_CONNECTIONS_OR_DB_ADMIN_ACCOUNT')
        if actual.get('valkey_auth_ping', {}).get('status') != 'PASS':pending.append('C_VALKEY_AUTH_OR_SUPPLY')
        if actual.get('rds_tls', {}).get('status') != 'PASS':pending.append('RDS_TLS_CONNECTION')
        if not ca_accepted:pending.append('VALKEY_TRUST_ROOT_CHAIN')
        pending += ['ACCEPT_RUNTIME_POOL_BUDGET', 'C_FINAL_IMPORT_ACCOUNTS_AND_SCHEMA', 'B_CUTOVER_AND_APP_ACTIVATION']
        runtime = ('SEOKPAN_CONNECTION_PROFILE=cloud\nSEOKPAN_GAME_LIFECYCLE_MODE=captured\n'
                   'SEOKPAN_ALLOWED_ORIGINS=' + json.dumps([origin], separators=(',', ':')) + '\n'
                   'SEOKPAN_DATABASE_EXPECTED_HOST=' + data['rds_address'] + '\nSEOKPAN_DATABASE_EXPECTED_PORT=3306\n'
                   'SEOKPAN_DATABASE_EXPECTED_NAME=stone_game\nSEOKPAN_REDIS_URL=rediss://' + data['redis_primary_endpoint'] + ':6379/0\n'
                   'SEOKPAN_REDIS_EXPECTED_HOST=' + data['redis_primary_endpoint'] + '\nSEOKPAN_REDIS_EXPECTED_PORT=6379\nSEOKPAN_REDIS_EXPECTED_DATABASE=0\n')
        (folder / 'runtime.env.INCOMPLETE').write_text('# PRIVATE CANDIDATE - NOT ACTIVATED\n' + runtime)
        write_json(folder / 'cloud-platform-inputs.json', {'cluster_id': management.name, 'namespace': args.namespace,
                   'route_origin_candidate': origin, 'game_lifecycle_candidate': 'captured', 'backend_initial_replica_candidate': 1,
                   'database_pool_adopted': False, 'database_max_connections_observed': budget,
                   'valkey_ca_candidate_verified_on_actual_endpoint': ca_accepted, 'ready_for_app_activation': False,
                   'pending': pending})
        receipt = {'status': 'CLOUD_CONNECTION_PREFLIGHT_COMPLETED', 'actual': actual, 'namespace_prepared': True,
                   'protected_runtime_candidate_prepared': True, 'valkey_ca_endpoint_verified': ca_accepted,
                   'app_started': False, 'migration': 'NOT_RUN', 'pending': pending}
        write_json(folder / 'receipt.json', receipt)
        emit('CLOUD_PRIVATE_RUNTIME_CANDIDATE_PREPARED', pending=pending, app_started=False)
    except Exception as exc:
        reason = str(exc) if isinstance(exc, Stop) else type(exc).__name__
        emit('STOP_CLOUD_CONNECTION_PREFLIGHT', phase=phase, reason=reason, app_started=False, database_writes=0)
        write_json(folder / 'stop.json', {'phase': phase, 'reason': reason})
        return 1
    finally:
        if pod is not None:
            try:
                observed = r.get(['get', 'pod', pod['metadata']['name'], '-n', args.namespace, '--ignore-not-found'], 'probe-cleanup-check')
                if observed is not None:
                    require(observed['metadata']['uid'] == pod['metadata']['uid'], 'PROBE_UID_CHANGED_NO_DELETE')
                    r.run(['delete', 'pod', pod['metadata']['name'], '-n', args.namespace, '--wait=true', '--timeout=30s'], 'probe-cleanup')
                emit('TEMPORARY_PROBE_REMOVED', namespace_preserved=True)
            except Exception:
                emit('PROBE_CLEANUP_REQUIRES_ATTENTION', temporary_pod_name=pod['metadata']['name'])
        print('Private Cloud result directory: ' + str(folder), flush=True)
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Stop as exc:
        emit('STOP_CLOUD_LOCAL_CONTEXT', reason=str(exc))
        raise SystemExit(1)
