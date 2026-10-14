#!/usr/bin/env python3
"""Human-run Cloud Pull authentication, connection and protected input preparation.
No application start, migration or database writes.

Reuses management/SG/Data receipts. Creates an owned application Namespace if
absent and one bounded, temporary Pod using the approved Backend image. Secret
values travel in memory/stdin only; output contains status and counts.
"""
import argparse
import base64
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.request
import urllib.parse


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


SQL_ONLY_PROBE = r'''
import asyncio,hashlib,json,re,ssl,sys,urllib.parse
p=json.load(sys.stdin)
def diagnostic(error):
    seen=set();chain=[];codes=[];current=error
    for _ in range(5):
        if current is None or id(current) in seen:break
        seen.add(id(current))
        code=current.args[0] if getattr(current,'args',()) else None
        if isinstance(code,int) and not isinstance(code,bool):codes.append(code)
        elif isinstance(code,str) and code.isdigit():codes.append(int(code))
        text=str(current)
        for value in sorted([v for v in p.get('sql_passwords',{}).values() if isinstance(v,str) and v],key=len,reverse=True):
            for form in (value,urllib.parse.quote(value,safe=''),json.dumps(value)[1:-1],repr(value)[1:-1]):
                text=text.replace(form,'<PASSWORD_REDACTED>')
        for key in ('rds_address','redis_primary_endpoint'):
            if p.get(key):text=text.replace(p[key],'<ENDPOINT_REDACTED>')
        text=re.sub(r'(?i)\b[a-z0-9.-]+\.(?:amazonaws\.com|openshiftapps\.com)\b','<HOST_REDACTED>',text)
        text=re.sub(r'\b(?:\d{1,3}\.){3}\d{1,3}\b','<IP_REDACTED>',text)
        text=re.sub(r'\b\d{12}\b','<ACCOUNT_REDACTED>',text)
        text=re.sub(r"(?i)(?:mysql(?:\+asyncmy)?://)[^\s]+",'<DATABASE_URL_REDACTED>',text)
        chain.append({'class':type(current).__name__,'message_redacted':text[:700]})
        current=current.__cause__ or current.__context__
    combined=' '.join(x['message_redacted'] for x in chain).lower()
    if 1045 in codes or 'access denied for user' in combined:status='ACCOUNT_NOT_READY_OR_AUTH_REJECTED'
    elif 1044 in codes:status='ACCOUNT_PERMISSION_REJECTED'
    elif 1049 in codes:status='DATABASE_NOT_IMPORTED'
    elif 'certificate verify failed' in combined or 'certificate_verify_failed' in combined or 'hostname mismatch' in combined:status='TLS_VERIFICATION_FAILED'
    elif 'timed out' in combined or 'timeouterror' in combined:status='DRIVER_CONNECTION_TIMEOUT'
    elif 2003 in codes:status='DRIVER_CONNECTION_FAILED_2003'
    else:status='SQL_DRIVER_OR_QUERY_ERROR'
    return {'status':status,'mysql_error_codes':sorted(set(codes)),'exception_chain':chain}
def ctx():
    context=ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.load_verify_locations(cadata=p['rds_ca'])
    context.minimum_version=ssl.TLSVersion.TLSv1_2
    return context
async def account(user,admin=False):
    password=p.get('sql_passwords',{}).get(user)
    if not password:return {'status':'PENDING_PROTECTED_PASSWORD'}
    import asyncmy
    stage='CONNECT'
    con=None
    try:
        # No database argument: max_connections can be read before import.
        con=await asyncmy.connect(host=p['rds_address'],port=3306,user=user,password=password,
                                 ssl=ctx(),connect_timeout=12,autocommit=True)
        stage='READ_CONNECTION_AND_SCHEMA'
        async with con.cursor() as cur:
            await cur.execute('SELECT @@GLOBAL.max_connections, @@GLOBAL.time_zone, USER(), CURRENT_USER(), @@GLOBAL.max_user_connections')
            maximum,zone,connected,effective,default_user_limit=await cur.fetchone()
            effective=effective.decode() if isinstance(effective,bytes) else effective
            connected=connected.decode() if isinstance(connected,bytes) else connected
            await cur.execute("SHOW SESSION STATUS LIKE 'Ssl_cipher'")
            cipher=await cur.fetchone()
            await cur.execute('SHOW GRANTS FOR CURRENT_USER()')
            grants=[str(row[0]) for row in await cur.fetchall()]
            require_ssl=any('REQUIRE SSL' in g.upper() or 'REQUIRE X509' in g.upper() for g in grants)
            account_limits=[int(v) for v in re.findall(r'(?i)MAX_USER_CONNECTIONS\s+(\d+)', '\n'.join(grants))]
            user_limit=(min(account_limits) if account_limits else 0) or int(default_user_limit)
            await cur.execute("SELECT COUNT(*) FROM information_schema.schemata WHERE schema_name='stone_game'")
            exists=int((await cur.fetchone())[0])==1
            result={'status':'PASS','max_connections':int(maximum),'time_zone_is_seoul':zone=='Asia/Seoul',
                    'expected_account':effective.split('@')[0]==user,'tls_cipher_present':bool(cipher and cipher[1]),
                    'requires_ssl_in_grants':require_ssl,'max_user_connections':user_limit,'grants_canonical_sha256':hashlib.sha256('\n'.join(sorted(grants)).encode()).hexdigest(),
                    'connection_source_sha256':hashlib.sha256(connected.encode()).hexdigest(),'stone_game_visible':exists}
            if not result['expected_account'] or not result['tls_cipher_present']:result['status']='TLS_OR_ACCOUNT_CONTRACT_REJECTED'
            if admin and exists:
                await cur.execute("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='stone_game' AND table_name='alembic_version'")
                revision_visible=int((await cur.fetchone())[0])==1
                result['alembic_version_visible']=revision_visible
                if revision_visible:
                    await cur.execute('SELECT version_num FROM stone_game.alembic_version')
                    result['expected_revision_match']=[r[0] for r in await cur.fetchall()]==['20260902_0002']
            return result
    except Exception as error:
        result=diagnostic(error);result['stage']=stage;return result
    finally:
        if con is not None:con.close()
async def main():
    out={'sql_read_only':await asyncio.wait_for(account('db_admin',True),timeout=40),'sql_runtime_accounts':{}}
    shared_failure=out['sql_read_only']['status'] in ('TLS_VERIFICATION_FAILED','DRIVER_CONNECTION_TIMEOUT','DRIVER_CONNECTION_FAILED_2003','SQL_DRIVER_OR_QUERY_ERROR')
    for user in ('identity_svc','game_svc'):
        if shared_failure:out['sql_runtime_accounts'][user]={'status':'NOT_RUN_SHARED_DRIVER_FAILURE'}
        else:out['sql_runtime_accounts'][user]=await asyncio.wait_for(account(user),timeout=35)
    out.update(application_started=False,database_writes=0,reused_previous_tls_valkey_and_secret_supply=True)
    return out
try:result=asyncio.run(asyncio.wait_for(main(),timeout=115))
except Exception as error:result={'sql_read_only':diagnostic(error),'application_started':False,'database_writes':0}
print(json.dumps(result,sort_keys=True))
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


PULL_SECRET = 'seokpan-cloud-ecr-pull'
PULL_SERVICE_ACCOUNT = 'seokpan-cloud-runtime'
OWNERSHIP_LABELS = {'app.kubernetes.io/part-of': 'seokpan', 'seokpan.io/environment': 'cloud'}
ECR_READ = ['ecr:BatchGetImage', 'ecr:GetDownloadUrlForLayer',
            'ecr:BatchCheckLayerAvailability', 'ecr:DescribeImages']


def pull_session_policy(account):
    require(re.fullmatch(r'[0-9]{12}', account) is not None, 'AWS_ACCOUNT_FORMAT')
    repos = ['arn:aws:ecr:ap-northeast-2:' + account + ':repository/seokpan-fnd-' + k
             for k in ('backend', 'frontend')]
    # Explicit denies also constrain resource-policy grants to the session.
    return {'Version': '2012-10-17', 'Statement': [
        {'Effect': 'Allow', 'Action': ['ecr:GetAuthorizationToken'], 'Resource': '*',
         'Condition': {'StringEquals': {'aws:RequestedRegion': 'ap-northeast-2'}}},
        {'Effect': 'Allow', 'Action': ECR_READ, 'Resource': repos,
         'Condition': {'StringEquals': {'aws:RequestedRegion': 'ap-northeast-2'}}},
        {'Effect': 'Deny', 'NotAction': ECR_READ + ['ecr:GetAuthorizationToken', 'sts:GetCallerIdentity'], 'Resource': '*'},
        {'Effect': 'Deny', 'Action': ECR_READ, 'NotResource': repos}]}


def pull_error_classes(text):
    rules = {
        'AUTHENTICATION_REQUIRED': r'no basic auth credentials|authentication required|unauthorized|status.?code.?401|401 Unauthorized',
        'REGISTRY_ACCESS_DENIED': r'pull access denied|403 Forbidden|status.?code.?403|denied:|AccessDenied',
        'IMAGE_OR_DIGEST_NOT_FOUND': r'manifest unknown|name unknown|not found|ImageNotFound',
        'NETWORK_DNS_OR_TIMEOUT': r'no such host|lookup .* failed|i/o timeout|timed out|network is unreachable|connection refused',
        'REGISTRY_TLS': r'x509:|certificate.*(?:unknown|invalid|expired)|certificate verify',
        'PLATFORM_MANIFEST': r'no matching manifest|no image found.*architecture'}
    return [key for key, pattern in rules.items() if re.search(pattern, text, re.I)]


def previous_pull_diagnostic(r, previous, namespace, root):
    previous = previous.resolve()
    require(previous.parent == root.resolve() and previous.name.startswith('cloud-connection-'),
            'PREVIOUS_CLOUD_RESULT_SCOPE')
    require(read_json(previous / 'stop.json').get('phase') == 'NAMESPACE_AND_APPROVED_IMAGE_PROBE',
            'PREVIOUS_IMAGE_PULL_STOP_REQUIRED')
    name = 'cloud-connection-probe-' + hashlib.sha256(previous.name.encode()).hexdigest()[:12]
    events = r.get(['get', 'events', '-n', namespace, '--field-selector', 'involvedObject.name=' + name], 'previous-pull-events')
    rows = events.get('items', [])
    write_json(r.folder / 'previous-pull-events.json', events)
    text = '\n'.join(str(x.get('message', '')) for x in rows)
    result = {'event_count': len(rows), 'classes': pull_error_classes(text) or ['EVENTS_EXPIRED_OR_UNCLASSIFIED'],
              'raw_messages': 'PROTECTED_NOT_EMITTED'}
    write_json(r.folder / 'previous-pull-diagnostic.json', result)
    emit('PREVIOUS_IMAGE_PULL_DIAGNOSTIC', **result)
    return result


def aws_json(args, env, label):
    result = subprocess.run(['aws', '--region', 'ap-northeast-2', '--output', 'json',
                             '--cli-connect-timeout', '10', '--cli-read-timeout', '30', *args],
                            env=env, capture_output=True, timeout=50)
    if result.returncode:
        text = result.stderr.decode(errors='replace')
        classes = pull_error_classes(text)
        if re.search(r'AccessDenied|not authorized|UnauthorizedOperation', text, re.I):
            classes = ['AWS_ACCESS_DENIED']
        if re.search(r'ExpiredToken|InvalidClientTokenId|Unable to locate credentials|SignatureDoesNotMatch', text, re.I):
            classes.append('AWS_CREDENTIALS')
        emit('ECR_AUTH_STEP_FAILED', operation=label, classes=classes or ['UNCLASSIFIED'], raw_output='NOT_EMITTED')
        raise Stop(label + '_FAILED_NO_IAM_CHANGE_OR_CREDENTIAL_FALLBACK')
    return json.loads(result.stdout)


def configure_ecr_pull(r, namespace, account, expected_user, image):
    require(shutil.which('aws') is not None, 'EXISTING_AWS_CLI_REQUIRED')
    require(re.fullmatch(r'[a-zA-Z0-9_+=,.@-]{1,64}', expected_user) is not None, 'EXPECTED_AWS_USERNAME_REQUIRED')
    base_env = dict(r.env)
    for key in ('AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY', 'AWS_SESSION_TOKEN', 'AWS_SECURITY_TOKEN',
                'AWS_ROLE_ARN', 'AWS_WEB_IDENTITY_TOKEN_FILE', 'AWS_PROFILE', 'AWS_DEFAULT_PROFILE'):
        base_env.pop(key, None)
    base_env.update(AWS_PAGER='', AWS_MAX_ATTEMPTS='1')
    caller = aws_json(['sts', 'get-caller-identity'], base_env, 'CONFIRMED_HUMAN_CALLER')
    require(caller.get('Arn') == 'arn:aws:iam::' + account + ':user/' + expected_user,
            'EXPECTED_HUMAN_IAM_USER_REQUIRED_NO_ROOT_OR_OTHER_ROLE')
    policy = pull_session_policy(account)
    policy_text = json.dumps(policy, separators=(',', ':'))
    require(len(policy_text) <= 2048, 'ECR_SESSION_POLICY_SIZE')
    session_name = 'seokpan-cloud-pull-' + hashlib.sha256(r.folder.name.encode()).hexdigest()[:8]
    emit('CREATING_SCOPED_ECR_READ_SESSION', aws_iam_username=expected_user,
         repositories=2, duration_seconds=43200, iam_policy_changes=0, push_or_delete_allowed=False)
    session = aws_json(['sts', 'get-federation-token', '--name', session_name,
                        '--duration-seconds', '43200', '--policy', policy_text], base_env, 'READ_ONLY_FEDERATION')
    expected_arn = 'arn:aws:sts::' + account + ':federated-user/' + session_name
    require(session.get('FederatedUser', {}).get('Arn') == expected_arn, 'FEDERATED_ECR_IDENTITY_MISMATCH')
    credentials = session['Credentials']
    env = dict(base_env, AWS_ACCESS_KEY_ID=credentials['AccessKeyId'],
               AWS_SECRET_ACCESS_KEY=credentials['SecretAccessKey'], AWS_SESSION_TOKEN=credentials['SessionToken'])
    require(aws_json(['sts', 'get-caller-identity'], env, 'SCOPED_PULL_CALLER').get('Arn') == expected_arn,
            'SCOPED_ECR_CALLER_NOT_CONFIRMED')
    digest = image.rsplit('@', 1)[1]
    metadata = aws_json(['ecr', 'describe-images', '--repository-name', 'seokpan-fnd-backend',
                         '--image-ids', 'imageDigest=' + digest], env, 'APPROVED_ECR_DIGEST_READ')
    require(any(x.get('imageDigest') == digest for x in metadata.get('imageDetails', [])), 'APPROVED_DIGEST_NOT_PRESENT')
    registry = account + '.dkr.ecr.ap-northeast-2.amazonaws.com'
    tokens = aws_json(['ecr', 'get-authorization-token', '--registry-ids', account], env, 'SCOPED_ECR_AUTH_TOKEN')
    rows = [x for x in tokens.get('authorizationData', []) if x.get('proxyEndpoint') == 'https://' + registry]
    require(len(rows) == 1, 'ONE_EXACT_ECR_REGISTRY_TOKEN_REQUIRED')
    token = rows[0]['authorizationToken']
    require(base64.b64decode(token, validate=True).startswith(b'AWS:'), 'ECR_TOKEN_FORMAT')
    def parse_time(value):
        if isinstance(value, (int, float)):
            return datetime.datetime.fromtimestamp(value, datetime.timezone.utc)
        return datetime.datetime.fromisoformat(value.replace('Z', '+00:00'))
    expiry = min(parse_time(credentials['Expiration']), parse_time(rows[0]['expiresAt']))
    require(expiry > datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=1), 'ECR_SESSION_EXPIRY_TOO_CLOSE')
    expiry_text = expiry.astimezone(datetime.timezone.utc).isoformat()
    renew_text = (expiry - datetime.timedelta(hours=1)).astimezone(datetime.timezone.utc).isoformat()
    secret_old = r.get(['get', 'secret', PULL_SECRET, '-n', namespace, '--ignore-not-found'], 'existing-pull-secret')
    if secret_old is not None:
        require(all(secret_old['metadata'].get('labels', {}).get(k) == v for k, v in OWNERSHIP_LABELS.items()) and
                secret_old.get('type') == 'kubernetes.io/dockerconfigjson', 'EXISTING_PULL_SECRET_OWNERSHIP')
    config = json.dumps({'auths': {registry: {'auth': token}}}, separators=(',', ':')).encode()
    secret = {'apiVersion': 'v1', 'kind': 'Secret', 'type': 'kubernetes.io/dockerconfigjson',
              'metadata': {'name': PULL_SECRET, 'namespace': namespace, 'labels': OWNERSHIP_LABELS,
                           'annotations': {'seokpan.io/pull-expires-at': expiry_text,
                                           'seokpan.io/pull-renew-before': renew_text,
                                           'seokpan.io/pull-scope': 'two-project-repositories-read-only'}},
              'data': {'.dockerconfigjson': base64.b64encode(config).decode()}}
    # Server-side apply avoids a second credential copy in last-applied annotations.
    r.run(['apply', '--server-side', '--field-manager=seokpan-cloud-pull', '-f', '-'], 'supply-ecr-pull-secret', json.dumps(secret).encode())
    sa = r.get(['get', 'serviceaccount', PULL_SERVICE_ACCOUNT, '-n', namespace, '--ignore-not-found'], 'pull-service-account')
    if sa is None:
        sa = {'apiVersion': 'v1', 'kind': 'ServiceAccount', 'metadata': {
              'name': PULL_SERVICE_ACCOUNT, 'namespace': namespace, 'labels': OWNERSHIP_LABELS},
              'automountServiceAccountToken': False, 'imagePullSecrets': [{'name': PULL_SECRET}]}
        r.run(['create', '-f', '-'], 'create-pull-service-account', json.dumps(sa).encode())
    else:
        require(all(sa['metadata'].get('labels', {}).get(k) == v for k, v in OWNERSHIP_LABELS.items()),
                'EXISTING_RUNTIME_SERVICE_ACCOUNT_OWNERSHIP')
        refs = sa.get('imagePullSecrets', [])
        if not any(x.get('name') == PULL_SECRET for x in refs):
            patch = {'imagePullSecrets': refs + [{'name': PULL_SECRET}]}
            r.run(['patch', 'serviceaccount', PULL_SERVICE_ACCOUNT, '-n', namespace, '--type=merge',
                   '-p', json.dumps(patch)], 'link-runtime-pull-secret')
    receipt = {'status': 'PASS_NAMESPACE_SCOPED_READ_ONLY_ECR_PULL_CONFIGURED', 'repositories': 2,
               'session_policy_sha256': hashlib.sha256(policy_text.encode()).hexdigest(),
               'expires_at_utc': expiry_text, 'renew_before_utc': renew_text,
               'iam_changes': 0, 'credentials_saved_outside_kubernetes_secret': False,
               'service_account': PULL_SERVICE_ACCOUNT, 'secret': PULL_SECRET}
    write_json(r.folder / 'ecr-pull-receipt.json', receipt)
    env.clear();credentials.clear();session.clear();tokens.clear();config=b'';token='';secret.clear()
    emit(**receipt)
    return receipt


ATS_ROOTS = (
    ('AmazonRootCA1.pem', '8ecde6884f3d87b1125ba31ac3fcb13d7016de7f57cc904fe1cb97c6ae98196e'),
    ('AmazonRootCA2.pem', '1ba5b2aa8c65401a82960118f80bec4f62304d83cec4713a19c39c011ea46db4'),
    ('AmazonRootCA3.pem', '18ce6cfe7bf14e60b2e347b8dfe868cb31d02ebb3ada271569f50343b46db3a4'),
    ('AmazonRootCA4.pem', 'e35d28419ed02025cfa69038cd623962458da5c695fbdea3c22b0bfb25897092'),
    ('SFSRootCAG2.pem', '568d6905a2c88708a4b3025190edcfedb1974a606a13c6e5290fcb2ae63edab5'))


def public_cas(previous=None):
    if previous is not None:
        for name in ('rds-ca.pem', 'valkey-ca-candidate.pem'):
            require((previous/name).is_file() and not (previous/name).is_symlink(), 'PREVIOUS_PUBLIC_CA_REQUIRED')
        rds_ca = (previous/'rds-ca.pem').read_bytes()
        redis_ca = (previous/'valkey-ca-candidate.pem').read_text()
    else:
        rds_ca = download('https://truststore.pki.rds.amazonaws.com/ap-northeast-2/ap-northeast-2-bundle.pem')
        redis_ca = ''.join(download('https://www.amazontrust.com/repository/' + name).decode('ascii').strip()+'\n'
                           for name, _ in ATS_ROOTS)
    require(hashlib.sha256(rds_ca).hexdigest() == '913fb5b814f17af79d4c1622584a8d0ceddf5b0d76fe353d0c7d1186cdd6b229', 'ACCEPTED_RDS_CA_CHANGED')
    roots = re.findall(r'-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----', redis_ca, re.S)
    require(len(roots) == 5 and [hashlib.sha256(ssl.PEM_cert_to_DER_cert(pem)).hexdigest() for pem in roots] ==
            [digest for _, digest in ATS_ROOTS], 'OFFICIAL_ATS_ROOT_CHANGED')
    return rds_ca, redis_ca


def collect_probe_failure(r, pod, namespace):
    events = r.get(['get', 'events', '-n', namespace, '--field-selector',
                    'involvedObject.uid=' + pod['metadata']['uid']], 'probe-failure-events')
    write_json(r.folder/'probe-failure-events.json', events)
    text = '\n'.join(str(x.get('message', '')) for x in events.get('items', []))
    emit('CURRENT_IMAGE_PULL_DIAGNOSTIC', event_count=len(events.get('items', [])),
         classes=pull_error_classes(text) or ['UNCLASSIFIED_PROTECTED_EVENTS_SAVED'], raw_output='NOT_EMITTED')


def cloud_input_objects(data, sql, auth, rds_ca, redis_ca, namespace):
    require(all(isinstance(sql.get(k), str) and sql[k] for k in ('identity_svc', 'game_svc')) and
            isinstance(auth.get('redis_auth_token'), str) and bool(auth['redis_auth_token']), 'CLOUD_RUNTIME_SECRET_FIELDS_REQUIRED')
    def url(user):
        return 'mysql+asyncmy://' + user + ':' + urllib.parse.quote(sql[user], safe='') + '@' + data['rds_address'] + ':3306/stone_game?charset=utf8mb4'
    def obj(kind, name, values):
        item = {'apiVersion': 'v1', 'kind': kind, 'metadata': {
                'name': name, 'namespace': namespace, 'labels': OWNERSHIP_LABELS}}
        if kind == 'Secret':
            item.update(type='Opaque', data={k: base64.b64encode(v.encode()).decode() for k, v in values.items()})
        else:item['data'] = values
        return item
    return [obj('Secret', 'backend-db-runtime', {'SEOKPAN_IDENTITY_DATABASE_URL': url('identity_svc'),
                                               'SEOKPAN_GAME_DATABASE_URL': url('game_svc')}),
            obj('Secret', 'backend-redis-runtime', {'SEOKPAN_REDIS_AUTH_TOKEN': auth['redis_auth_token']}),
            obj('ConfigMap', 'backend-database-ca', {'ca.crt': rds_ca.decode('ascii')}),
            obj('ConfigMap', 'backend-redis-ca', {'ca.crt': redis_ca})]


def supply_cloud_inputs(r, data, sql, auth, rds_ca, redis_ca, namespace, actual):
    require(actual.get('rds_tls', {}).get('status') == 'PASS' and
            actual.get('valkey_tls', {}).get('status') == 'PASS' and
            actual.get('valkey_auth_ping', {}).get('status') == 'PASS', 'VERIFIED_TLS_AND_VALKEY_AUTH_REQUIRED_BEFORE_SUPPLY')
    objects = cloud_input_objects(data, sql, auth, rds_ca, redis_ca, namespace)
    for item in objects:
        old = r.get(['get', item['kind'].lower(), item['metadata']['name'], '-n', namespace, '--ignore-not-found'],
                    'existing-' + item['metadata']['name'])
        require(old is None or all(old['metadata'].get('labels', {}).get(k) == v for k, v in OWNERSHIP_LABELS.items()),
                'EXISTING_APP_INPUT_OWNERSHIP_REQUIRES_REVIEW')
    for item in objects:
        r.run(['apply', '--server-side', '--field-manager=seokpan-cloud-inputs', '-f', '-'],
              'supply-' + item['metadata']['name'], json.dumps(item).encode())
    for item in objects:
        observed = r.get(['get', item['kind'].lower(), item['metadata']['name'], '-n', namespace],
                         'verify-' + item['metadata']['name'])
        require(observed.get('data') == item['data'] and
                (item['kind'] != 'Secret' or observed.get('type') == 'Opaque'),
                'ACTUAL_CLOUD_INPUT_CONTENT_MISMATCH_NO_APP_ACTIVATION')
    result = {'status': 'PASS_CLOUD_RUNTIME_SECRETS_AND_CA_SUPPLIED', 'secrets': 2, 'configmaps': 2, 'actual_contents_match': True,
              'runtime_accounts_grants_and_schema': 'NOT_ACCEPTED_BY_SUPPLY', 'application_started': False}
    objects.clear()
    emit(**result)
    return result


def pool_candidate(maximum):
    # Proposed only: 2 engines x 1 process x (3 replicas + surge + terminating) + reserve 10.
    if type(maximum) is not int or maximum < 20:return None
    size = 2 if maximum >= 30 else 1
    return {'pool_size_per_engine': size, 'max_overflow': 0, 'pool_timeout_seconds': 1,
            'engines_per_process': 2, 'processes_per_pod': 1, 'simultaneous_pod_budget': 5,
            'reserved_connections': 10, 'total_connections_budget': 2 * size * 5 + 10,
            'observed_max_connections': maximum, 'adopted': False}


def completed_cloud_result(path, root, cluster, namespace):
    path = path.resolve()
    require(path.parent == root.resolve() and path.name.startswith('cloud-connection-'), 'COMPLETED_CLOUD_RESULT_SCOPE')
    receipt = read_json(path/'receipt.json')
    contract = read_json(path/'cloud-platform-inputs.json')
    require(receipt.get('status') == 'CLOUD_CONNECTION_PREFLIGHT_COMPLETED' and
            contract.get('cluster_id') == cluster and contract.get('namespace') == namespace,
            'COMPLETED_CLOUD_RESULT_IDENTITY')
    actual = receipt.get('actual', {})
    require(all(actual.get(k, {}).get('status') == 'PASS' for k in ('rds_tls', 'valkey_tls', 'valkey_auth_ping')),
            'PREVIOUS_ACTUAL_TLS_AND_VALKEY_PASS_REQUIRED')
    require(receipt.get('runtime_inputs_supply', {}).get('status') == 'PASS_CLOUD_RUNTIME_SECRETS_AND_CA_SUPPLIED',
            'PREVIOUS_CLOUD_SECRET_CA_SUPPLY_REQUIRED')
    return receipt, contract


def ecr_lease_current(r, namespace, minutes=60):
    secret = r.get(['get', 'secret', PULL_SECRET, '-n', namespace], 'existing-pull-lease')
    annotations = secret.get('metadata', {}).get('annotations', {})
    value = annotations.get('seokpan.io/pull-expires-at', '')
    try:expiry = datetime.datetime.fromisoformat(value.replace('Z', '+00:00'))
    except (ValueError, TypeError):return False
    return expiry > datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=minutes)


def launch_background(root):
    folder = Path(tempfile.mkdtemp(prefix='cloud-night-run-', dir=root))
    log = folder/'progress.log'
    with log.open('xb') as output:
        proc = subprocess.Popen([sys.executable, '-u', str(Path(__file__).resolve()),
                                 *[x for x in sys.argv[1:] if x != '--background']],
                                stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
                                start_new_session=True, close_fds=True)
    write_json(folder/'job.json', {'pid': proc.pid, 'status': 'STARTED',
                                  'scope': 'UNRESOLVED_SQL_AND_MEASURED_POOL_ONLY_COMPLETED_STEPS_REUSED',
                                  'log': str(log), 'interactive_input': False})
    emit('CLOUD_CONTINUATION_BACKGROUND_STARTED', pid=proc.pid, log_file=str(log), survives_terminal_close=True)
    print('Read progress: tail -n 80 '+shlex.quote(str(log)), flush=True)
    # Show the first useful output while the human is still awake. The child
    # continues independently after this short observation period.
    until = time.monotonic()+60
    while proc.poll() is None and time.monotonic() < until:
        time.sleep(2)
    text = log.read_text(errors='replace')
    print(text, end='' if text.endswith('\n') else '\n', flush=True)
    emit('BACKGROUND_RUN_FINISHED_SEE_SQL_STATUS' if proc.poll() is not None else 'BACKGROUND_JOB_CONTINUES',
         exit_code=proc.returncode, log_file=str(log))
    return 0


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
    parser.add_argument('--resume-from', type=Path, help='Reuse the preceding failed Pull result and its public CA files')
    parser.add_argument('--configure-ecr-pull', action='store_true', help='Create a separately restricted read-only federation session and Namespace pull secret')
    parser.add_argument('--expected-aws-user', help='Confirmed human IAM user used only to mint the restricted ECR session')
    parser.add_argument('--refresh-ecr-pull-only', action='store_true', help='Renew the namespaced pull secret without starting a probe or reading Data secrets')
    parser.add_argument('--supply-cloud-inputs', action='store_true', help='After verified TLS/AUTH, supply only the two runtime Secrets and two CA ConfigMaps')
    parser.add_argument('--resume-connection-from', type=Path, help='Use a completed TLS/Valkey/input receipt and continue only unresolved SQL work')
    parser.add_argument('--sql-only', action='store_true', help='Reuse accepted transport and input supply; query DB accounts and capture sanitized driver error chains')
    parser.add_argument('--renew-pull-when-needed', action='store_true', help='Renew the existing scoped pull credential only if less than one hour remains')
    parser.add_argument('--adopt-conservative-pool-if-measured', action='store_true', help='Adopt the bounded candidate only after measured limit and both runtime accounts pass TLS/GRANT checks')
    parser.add_argument('--background', action='store_true', help='Detach from the terminal, keep a protected log and display initial progress for at most 60 seconds')
    args = parser.parse_args()
    require(os.name == 'posix' and os.getuid() != 0, 'USE_PERSONAL_CONTROLLER_LINUX_ACCOUNT')
    require(re.fullmatch(r'seokpan-cloud(?:-[a-z0-9]+)?', args.namespace) is not None, 'DEDICATED_CLOUD_NAMESPACE_REQUIRED')
    require(not args.refresh_ecr_pull_only or args.configure_ecr_pull, 'REFRESH_REQUIRES_SCOPED_ECR_CONFIGURATION')
    require(not args.sql_only or args.resume_connection_from is not None, 'SQL_RESUME_REQUIRES_COMPLETED_CLOUD_RESULT')
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
    if args.background:
        require(args.sql_only, 'BACKGROUND_SCOPE_REQUIRES_SQL_CONTINUATION')
        return launch_background(root)
    # One continuation per protected input root, including background starts.
    import fcntl
    lock_fd = os.open(root/'cloud-continuation.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(lock_fd)
        raise Stop('EXISTING_CLOUD_CONTINUATION_RUNNING_NO_DUPLICATE_START')
    folder = Path(tempfile.mkdtemp(prefix='cloud-connection-', dir=root))
    r = Runner(oc, kubeconfig, folder)
    require('SSLKEYLOGFILE' not in r.env, 'TLS_KEY_LOGGING_NOT_ALLOWED')
    pod = None
    phase = 'CURRENT_TARGET_AND_PROTECTED_INPUTS'
    try:
        server = r.run(['whoami', '--show-server'], 'server').stdout.decode().strip()
        require(server.rstrip('/') == handoff['api_url'].rstrip('/'), 'WRONG_CLUSTER_CONTEXT')
        previous_receipt = previous_contract = None
        if args.sql_only:
            previous_receipt, previous_contract = completed_cloud_result(args.resume_connection_from, root,
                                                                         management.name, args.namespace)
            emit('REUSED_ECR_TLS_VALKEY_AND_CLOUD_INPUT_SUPPLY', rerun_sg_terraform=False, resupply_secrets=False)
        if args.resume_from is not None:
            previous_pull_diagnostic(r, args.resume_from, args.namespace, root)
        data = approved_data(args.data_file.read_bytes(), args.data_sha256)
        ingress = r.get(['get', 'ingress.config.openshift.io', 'cluster'], 'ingress-domain')
        domain = ingress['spec']['domain']
        require(isinstance(domain, str) and re.fullmatch(r'apps\.[a-z0-9.-]+\.openshiftapps\.com', domain) is not None,
                'ACTUAL_ROSA_INGRESS_DOMAIN_REQUIRED')
        origin = 'https://' + args.namespace + '.' + domain
        phase = 'NAMESPACE_AND_APPROVED_IMAGE_PROBE'
        ns = r.get(['get', 'namespace', args.namespace, '--ignore-not-found'], 'namespace')
        if ns is None:
            require(not args.sql_only, 'PREVIOUS_CLOUD_NAMESPACE_MISSING')
            obj = {'apiVersion': 'v1', 'kind': 'Namespace', 'metadata': {'name': args.namespace,
                   'labels': {'app.kubernetes.io/part-of': 'seokpan', 'seokpan.io/environment': 'cloud'}}}
            ns = json.loads(r.run(['create', '-f', '-', '-o', 'json'], 'namespace-create', json.dumps(obj).encode()).stdout)
            emit('CLOUD_NAMESPACE_CREATED', application_replicas_started=0)
        else:
            labels = ns['metadata'].get('labels', {})
            require(labels.get('app.kubernetes.io/part-of') == 'seokpan' and labels.get('seokpan.io/environment') == 'cloud',
                    'EXISTING_NAMESPACE_OWNERSHIP_REQUIRES_REVIEW')
        if args.configure_ecr_pull:
            phase = 'SCOPED_ECR_PULL_CONFIGURATION'
            configure_ecr_pull(r, args.namespace, account, args.expected_aws_user or '', args.backend_image)
            renew = folder/'refresh-ecr-pull.sh'
            argv = ['python3', str(Path(__file__).resolve()), str(root.resolve()),
                    '--data-file', str(args.data_file.resolve()), '--data-sha256', args.data_sha256,
                    '--backend-image', args.backend_image, '--namespace', args.namespace,
                    '--expected-aws-user', args.expected_aws_user, '--configure-ecr-pull', '--refresh-ecr-pull-only']
            renew.write_text('#!/usr/bin/env bash\nset -euo pipefail\nexec '+shlex.join(argv)+'\n')
            renew.chmod(0o700)
            print('ECR refresh command: bash '+shlex.quote(str(renew)), flush=True)
        elif args.sql_only:
            phase = 'REUSE_EXISTING_ECR_LEASE'
            if not ecr_lease_current(r, args.namespace):
                require(args.renew_pull_when_needed, 'ECR_LEASE_RENEWAL_REQUIRED')
                configure_ecr_pull(r, args.namespace, account, args.expected_aws_user or '', args.backend_image)
            else:emit('REUSED_EXISTING_SCOPED_ECR_PULL_LEASE')
        if args.refresh_ecr_pull_only:
            return 0
        phase = 'NAMESPACE_AND_APPROVED_IMAGE_PROBE'
        run_id = hashlib.sha256(folder.name.encode()).hexdigest()[:12]
        name = 'cloud-connection-probe-' + run_id
        obj = probe_pod(name, args.namespace, args.backend_image, run_id)
        if args.configure_ecr_pull or args.sql_only:
            obj['spec']['serviceAccountName'] = PULL_SERVICE_ACCOUNT
            obj['spec']['imagePullSecrets'] = [{'name': PULL_SECRET}]
        pod = json.loads(r.run(['create', '-f', '-', '-o', 'json'], 'probe-create', json.dumps(obj).encode()).stdout)
        deadline = time.monotonic() + 150
        observed = pod
        while time.monotonic() < deadline:
            observed = r.get(['get', 'pod', name, '-n', args.namespace], 'probe-status')
            state = observed.get('status', {})
            if state.get('phase') in ('Failed', 'Succeeded'):
                raise Stop('PROBE_TERMINATED_BEFORE_CONNECTION_CHECK')
            waiting = [x.get('state', {}).get('waiting', {}).get('reason')
                       for x in state.get('containerStatuses', [])]
            if any(x in ('ImagePullBackOff', 'ErrImagePull') for x in waiting):
                write_json(folder/'probe-failed-pod.json', observed)
                raise Stop('APPROVED_IMAGE_PULL_FAILED_DIAGNOSTIC_SAVED_BEFORE_CLEANUP')
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
        rds_ca, redis_ca = public_cas(args.resume_connection_from or args.resume_from)
        (folder/'rds-ca.pem').write_bytes(rds_ca)
        (folder/'valkey-ca-candidate.pem').write_text(redis_ca)
        sql, sql_status = load_optional_secret(Path.home() / 'secrets/seokpan/data-sql-accounts.sops.yaml', '021bd0da994a', ('db_admin', 'identity_svc', 'game_svc'))
        if args.sql_only:
            auth, auth_status = {}, {'decrypt': 'NOT_REPEATED_PREVIOUS_VALKEY_PASS_REUSED'}
        else:
            auth, auth_status = load_optional_secret(Path.home() / 'secrets/seokpan/foundation-data.sops.yaml', '9a86f90e6ba6', ('redis_auth_token',))
        emit('CLOUD_PROTECTED_INPUTS_LOADED_FOR_CONNECTION', sql=sql_status, valkey=auth_status, secret_values_printed=False)
        payload = dict(data, rds_ca=rds_ca.decode('ascii'), redis_ca=redis_ca,
                       db_admin_password=sql.get('db_admin'), redis_auth_token=auth.get('redis_auth_token'))
        if args.sql_only:payload['sql_passwords'] = sql
        result = r.run(['exec', '-i', '-n', args.namespace, name, '--', 'python', '-c', SQL_ONLY_PROBE if args.sql_only else PROBE],
                       'connection-probe', json.dumps(payload).encode(), timeout=125)
        payload.clear()
        actual = json.loads(result.stdout)
        if previous_receipt is not None:
            actual = dict(previous_receipt['actual'], **actual)
        write_json(folder / 'connection-result.json', actual)
        emit('CLOUD_ACTUAL_CONNECTION_RESULTS', **actual)
        supply = dict(previous_receipt['runtime_inputs_supply'], reused=True) if previous_receipt else {'status': 'NOT_REQUESTED'}
        if args.supply_cloud_inputs and not args.sql_only:
            if (actual.get('rds_tls', {}).get('status') == 'PASS' and actual.get('valkey_tls', {}).get('status') == 'PASS'
                    and actual.get('valkey_auth_ping', {}).get('status') == 'PASS'
                    and all(sql.get(k) for k in ('identity_svc', 'game_svc'))):
                phase = 'CLOUD_RUNTIME_SECRET_AND_CA_SUPPLY'
                supply = supply_cloud_inputs(r, data, sql, auth, rds_ca, redis_ca, args.namespace, actual)
            else:
                supply = {'status': 'PENDING_TLS_AUTH_OR_PROTECTED_RUNTIME_FIELDS'}
                emit('CLOUD_INPUT_SUPPLY_PENDING', reason=supply['status'])
        sql.clear();auth.clear()
        ca_accepted = actual.get('valkey_tls', {}).get('status') == 'PASS'
        sql_result = actual.get('sql_read_only', {})
        budget = sql_result.get('max_connections') if sql_result.get('status') == 'PASS' else None
        if budget is None:
            successful = [row for row in actual.get('sql_runtime_accounts', {}).values() if row.get('status') == 'PASS']
            if successful:budget = min(row['max_connections'] for row in successful)
        pool = pool_candidate(budget)
        if pool and args.adopt_conservative_pool_if_measured:
            runtime_accounts = actual.get('sql_runtime_accounts', {})
            pool['adopted'] = all(runtime_accounts.get(k, {}).get('status') == 'PASS' and
                                  runtime_accounts[k].get('expected_account') is True and
                                  runtime_accounts[k].get('requires_ssl_in_grants') is True and
                                  (runtime_accounts[k].get('max_user_connections') == 0 or
                                   runtime_accounts[k].get('max_user_connections', -1) >= pool['pool_size_per_engine'] * pool['simultaneous_pod_budget'])
                                  for k in ('identity_svc', 'game_svc'))
            if pool['adopted']:
                pool['acceptance'] = 'B_EXPLICIT_FLAG_ACTUAL_LIMIT_TWO_ENGINES_ONE_PROCESS_FIVE_PODS_RESERVE_TEN'
        if pool:emit('DB_POOL_CANDIDATE_FROM_MEASUREMENT', **pool)
        # This is a proposal. Never silently adopt a pool budget or start writers.
        pending = []
        if not budget:pending.append('C_RDS_MAX_CONNECTIONS_OR_DB_ADMIN_ACCOUNT')
        if actual.get('valkey_auth_ping', {}).get('status') != 'PASS':pending.append('C_VALKEY_AUTH_OR_SUPPLY')
        if actual.get('rds_tls', {}).get('status') != 'PASS':pending.append('RDS_TLS_CONNECTION')
        if not ca_accepted:pending.append('VALKEY_TRUST_ROOT_CHAIN')
        if not pool or not pool['adopted']:pending.append('ACCEPT_RUNTIME_POOL_BUDGET_AFTER_ACCOUNT_LIMIT_READ')
        pending += ['C_FINAL_IMPORT_ACCOUNTS_AND_SCHEMA', 'B_CUTOVER_AND_APP_ACTIVATION']
        runtime = ('SEOKPAN_CONNECTION_PROFILE=cloud\nSEOKPAN_GAME_LIFECYCLE_MODE=captured\n'
                   'SEOKPAN_ALLOWED_ORIGINS=' + json.dumps([origin], separators=(',', ':')) + '\n'
                   'SEOKPAN_DATABASE_EXPECTED_HOST=' + data['rds_address'] + '\nSEOKPAN_DATABASE_EXPECTED_PORT=3306\n'
                   'SEOKPAN_DATABASE_EXPECTED_NAME=stone_game\nSEOKPAN_REDIS_URL=rediss://' + data['redis_primary_endpoint'] + ':6379/0\n'
                   'SEOKPAN_REDIS_EXPECTED_HOST=' + data['redis_primary_endpoint'] + '\nSEOKPAN_REDIS_EXPECTED_PORT=6379\nSEOKPAN_REDIS_EXPECTED_DATABASE=0\n')
        if pool and pool['adopted']:
            runtime += ('SEOKPAN_DATABASE_POOL_SIZE='+str(pool['pool_size_per_engine'])+'\n'
                        'SEOKPAN_DATABASE_MAX_OVERFLOW=0\nSEOKPAN_DATABASE_POOL_TIMEOUT_SECONDS=1\n')
        (folder / 'runtime.env.INCOMPLETE').write_text('# PRIVATE CANDIDATE - NOT ACTIVATED\n' + runtime)
        write_json(folder / 'cloud-platform-inputs.json', {'cluster_id': management.name, 'namespace': args.namespace,
                   'route_origin_candidate': origin, 'game_lifecycle_candidate': 'captured', 'backend_initial_replica_candidate': 1,
                   'database_pool_adopted': bool(pool and pool['adopted']), 'database_max_connections_observed': budget,
                   'database_pool_candidate': pool, 'runtime_service_account': PULL_SERVICE_ACCOUNT if args.configure_ecr_pull else None,
                   'runtime_inputs_supply': supply,
                   'valkey_ca_candidate_verified_on_actual_endpoint': ca_accepted, 'ready_for_app_activation': False,
                   'pending': pending})
        receipt = {'status': 'CLOUD_CONNECTION_PREFLIGHT_COMPLETED', 'actual': actual, 'namespace_prepared': True,
                   'protected_runtime_candidate_prepared': True, 'valkey_ca_endpoint_verified': ca_accepted,
                   'runtime_inputs_supply': supply, 'database_pool_candidate': pool,
                   'app_started': False, 'migration': 'NOT_RUN', 'pending': pending}
        write_json(folder / 'receipt.json', receipt)
        emit('CLOUD_PRIVATE_RUNTIME_CANDIDATE_PREPARED', pending=pending, app_started=False)
        sql_ready = (actual.get('sql_read_only', {}).get('status') == 'PASS' and
                     all(actual.get('sql_runtime_accounts', {}).get(k, {}).get('status') == 'PASS'
                         for k in ('identity_svc', 'game_svc')))
        summary = {'status': 'SQL_POOL_PREPARED_CUTOVER_PENDING' if sql_ready else 'SQL_BLOCKED_DIAGNOSTIC_SAVED', 'sql': actual.get('sql_read_only'),
                   'runtime_accounts': actual.get('sql_runtime_accounts', {}), 'pool_candidate': pool,
                   'reused_completed_steps': args.sql_only, 'app_started': False, 'pending': pending}
        write_json(folder/'summary.json', summary)
        emit(**summary)
    except Exception as exc:
        if pod is not None and phase == 'NAMESPACE_AND_APPROVED_IMAGE_PROBE':
            try:collect_probe_failure(r, pod, args.namespace)
            except Exception:emit('PULL_EVENT_COLLECTION_UNAVAILABLE', raw_output='NOT_EMITTED')
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
        os.close(lock_fd)
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Stop as exc:
        emit('STOP_CLOUD_LOCAL_CONTEXT', reason=str(exc))
        raise SystemExit(1)
