"""Bind existing results, grant observer PROCESS, and publish one HPA run."""
import argparse
import copy
import datetime as dt
from decimal import Decimal
import fcntl
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import pwd
import re
import shlex
import subprocess
import sys
import time
from urllib.parse import urlsplit

ROOT = Path.home()/'.local/share/seokpan-rosa-plan-inputs-5psj2l62'
RUN = ROOT/'controller-run-bundle-K2Tx7HgB'
BASE = RUN/'prestart-closeout'
CID = '2ti8n9c2j2em1h6jbb34kc0dq95dv4fi'
NSUID = '01f37fd1-935d-4d39-8305-e8266312472e'
APPUID = 'a628168b-2a30-4de5-b5d3-809f74853d15'


def need(ok, code):
    if not ok:
        raise RuntimeError(code)


def read(path):
    path = Path(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        s = os.fstat(stream.fileno())
        import stat
        need(stat.S_ISREG(s.st_mode) and s.st_uid == os.getuid()
             and s.st_size < 33554432, 'OWNED_BOUNDED_FILE_REQUIRED')
        return json.load(stream)


def canonical(x):
    return json.dumps(x, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def save(path, value):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(canonical(value)+b'\n')


def load(name, path):
    m = importlib.util.module_from_spec(importlib.util.spec_from_file_location(name, path))
    sys.modules[name] = m
    m.__spec__.loader.exec_module(m)
    return m


def quantity(value):
    raw = str(value)
    match = re.fullmatch(r'([0-9]+(?:\.[0-9]+)?)([a-zA-Z]*)', raw)
    need(match is not None, 'SUPPORTED_RESOURCE_QUANTITY_REQUIRED')
    scale = {'': 1, 'm': Decimal('.001'), 'n': Decimal('.000000001'),
             'u': Decimal('.000001'), 'Ki': 1024, 'Mi': 1024**2,
             'Gi': 1024**3, 'Ti': 1024**4, 'k': 1000, 'M': 1000**2,
             'G': 1000**3, 'T': 1000**4}
    need(match[2] in scale, 'SUPPORTED_RESOURCE_UNIT_REQUIRED')
    return Decimal(match[1])*scale[match[2]]


def node_capacity(nodes, pods, selected):
    result = []
    for name in selected:
        node, = [n for n in nodes if n['metadata']['name'] == name]
        alloc = node['status']['allocatable']
        other = [p for p in pods if p['spec'].get('nodeName') == name
                 and p.get('status', {}).get('phase') not in ('Succeeded', 'Failed')
                 and not (p['metadata'].get('namespace') == 'seokpan-cloud'
                          and p['metadata'].get('labels', {}).get('app.kubernetes.io/name') == 'backend')]
        used = {'cpu': Decimal(0), 'memory': Decimal(0)}
        for pod in other:
            # Init requests are summed conservatively instead of understated.
            for container in pod['spec'].get('containers', []) + pod['spec'].get('initContainers', []):
                requests = container.get('resources', {}).get('requests', {})
                for key in used:
                    used[key] += quantity(requests.get(key, '0'))
            for key in used:
                used[key] += quantity(pod['spec'].get('overhead', {}).get(key, '0'))
        count = max(0, min(int((quantity(alloc['cpu'])-used['cpu'])/Decimal('.1')),
                           int((quantity(alloc['memory'])-used['memory'])/(256*1024**2)),
                           int(alloc['pods'])-len(other)))
        result.append({'node': name, 'backend_request_slots': count})
    return min(50, sum(x['backend_request_slots'] for x in result)), result


def declared_plan(c, C):
    return {'schema': 1, 'run_id': c['run_id'], 'binding': c['binding'],
            'control_sha256': C.digest(c), 'origin': c['origin'],
            'guard_argv': c['generator_guard_argv'], 'window': c['window'],
            'phases': [{'vus': n, 'seconds': s} for n, s in C.PHASES],
            'think_ms': 1000, 'timeout_ms': 5000, 'stop_error_fraction': .05,
            'workload': 'guest-lobby-and-ranking-reads', 'max_vus': 300}


def master_value(db, meta, value, host, account):
    arn = db.get('MasterUserSecret', {}).get('SecretArn', '')
    need(db.get('DBInstanceStatus') == 'available' and db['Endpoint']['Address'] == host
         and db['Endpoint']['Port'] == 3306, 'EXACT_AVAILABLE_RDS_REQUIRED')
    need(arn.startswith('arn:aws:secretsmanager:ap-northeast-2:'+account+':secret:')
         and meta['ARN'] == arn and value['ARN'] == arn, 'EXACT_MASTER_SECRET_REQUIRED')
    current = [k for k, v in meta['VersionIdsToStages'].items() if 'AWSCURRENT' in v]
    need(len(current) == 1 and value['VersionId'] == current[0]
         and 'AWSCURRENT' in value['VersionStages'], 'CURRENT_MASTER_VERSION_REQUIRED')
    need(not any('AWSPENDING' in v and k != current[0]
                 for k, v in meta['VersionIdsToStages'].items()), 'MASTER_ROTATION_PENDING')
    secret = json.loads(value['SecretString'])
    need(secret['username'] == db['MasterUsername'] and secret.get('password')
         and secret.get('host', host) == host and int(secret.get('port', 3306)) == 3306,
         'BOUND_MASTER_CREDENTIAL_REQUIRED')
    return {'username': secret['username'], 'password': secret['password']}


GRANT_PROGRAM = r'''
import asyncio,hashlib,json,re,ssl,sys
from urllib.parse import urlsplit,unquote
import asyncmy
def need(ok,code):
 if not ok:raise RuntimeError(code)
def visible(rows):
 return any(re.search(r'\b(?:ALL PRIVILEGES|PROCESS)\b.*\bON\s+\*\.\*',
  (x[0].decode() if isinstance(x[0],bytes) else x[0]).upper()) for x in rows)
async def apply(p):
 with open(p['ca_file'],'rb') as f:need(hashlib.sha256(f.read()).hexdigest()==p['ca_sha256'],'BOUND_CA_REQUIRED')
 context=ssl.create_default_context(cafile=p['ca_file']);context.minimum_version=ssl.TLSVersion.TLSv1_2
 u=urlsplit(p['schema_url']);need(unquote(u.username or '')=='db_admin' and u.hostname==p['host'],'EXACT_OBSERVER_REQUIRED')
 options=dict(host=p['host'],port=3306,ssl=context,autocommit=True,connect_timeout=8)
 observer=await asyncmy.connect(user='db_admin',password=unquote(u.password),**options)
 master=None
 try:
  async with observer.cursor() as cur:
   await cur.execute("SHOW SESSION STATUS LIKE 'Ssl_cipher'");need((await cur.fetchone())[1],'TLS_REQUIRED')
   await cur.execute('SELECT CURRENT_USER()');matched=(await cur.fetchone())[0]
   if isinstance(matched,bytes):matched=matched.decode()
   user,host=matched.rsplit('@',1);need(user=='db_admin' and re.fullmatch(r'[A-Za-z0-9_:.%/-]+',host),'EXACT_MATCHED_OBSERVER_REQUIRED')
   await cur.execute('SHOW GRANTS FOR CURRENT_USER()');present=visible(await cur.fetchall())
  changed=False
  if not present:
   need(p['allow_grant'],'PRIOR_GRANT_ATTEMPT_REQUIRES_RECONCILIATION')
   master=await asyncmy.connect(user=p['master']['username'],password=p['master']['password'],**options)
   async with master.cursor() as cur:
    await cur.execute("SHOW SESSION STATUS LIKE 'Ssl_cipher'");need((await cur.fetchone())[1],'MASTER_TLS_REQUIRED')
    await cur.execute('GRANT PROCESS ON *.* TO %s@%s',(user,host));changed=True
  async with observer.cursor() as cur:
   await cur.execute('SHOW GRANTS FOR CURRENT_USER()');need(visible(await cur.fetchall()),'PROCESS_READBACK_REQUIRED')
  return {'status':'PASS_EXACT_OBSERVER_PROCESS_GRANTED_OR_REUSED','account':user,'matched_host':host,
   'grant_added':changed,'purpose_accounts_changed':False,'schema_changes':0,'tls':True}
 finally:
  observer.close()
  if master is not None:master.close()
try:print(json.dumps(asyncio.run(apply(json.load(sys.stdin)))))
except Exception as e:
 reason=str(e) if type(e) is RuntimeError else type(e).__name__
 if not re.fullmatch(r'[A-Za-z0-9_]{1,160}',reason):reason='PRIVATE_SQL_FAILURE'
 print(json.dumps({'status':'STOP_OBSERVER_PROCESS_GRANT','reason':reason,
  'sql_error_code':next((x for x in e.args if type(x) is int),None),'automatic_retry':False}));sys.exit(2)
'''


def aws(*args):
    p = subprocess.run(['aws', *args, '--region', 'ap-northeast-2', '--output', 'json',
                        '--no-cli-pager'], capture_output=True, timeout=40)
    if p.returncode:
        match = re.search(rb'\(([A-Za-z0-9_]+)\)', p.stderr)
        raise RuntimeError('AWS_'+args[0].upper()+'_'+(match[1].decode() if match else 'READ_FAILED'))
    return json.loads(p.stdout)


def grant_process(dbconfig, out, call, foundation):
    accepted = out/'process-grant.json'
    if accepted.exists():
        return read(accepted)
    identity = aws('sts', 'get-caller-identity')
    need(identity['Account'] == foundation['account_id'] and ':user/' in identity['Arn'],
         'EXISTING_PERSONAL_AWS_SESSION_REQUIRED')
    db, = [x for x in aws('rds', 'describe-db-instances')['DBInstances']
           if x.get('Endpoint', {}).get('Address') == dbconfig['database_host']]
    need(db['DBSubnetGroup']['VpcId'] == foundation['vpc_id'], 'BOUND_RDS_VPC_REQUIRED')
    arn = db['MasterUserSecret']['SecretArn']
    meta = aws('secretsmanager', 'describe-secret', '--secret-id', arn)
    version, = [k for k, v in meta['VersionIdsToStages'].items() if 'AWSCURRENT' in v]
    value = aws('secretsmanager', 'get-secret-value', '--secret-id', arn,
                '--version-id', version, '--version-stage', 'AWSCURRENT')
    master = master_value(db, meta, value, dbconfig['database_host'], foundation['account_id'])
    observer = load('grant_existing_observer', BASE/'pod_db_observer.py')
    pods = json.loads(call(['get', 'pods', '-n', 'seokpan-cloud', '-l',
                           'app.kubernetes.io/name=backend', '-o', 'json']))['items']
    eligible = [x for x in pods if not x['metadata'].get('deletionTimestamp')
                and any(c['type'] == 'Ready' and c['status'] == 'True' for c in x.get('status', {}).get('conditions', []))
                and len(x['spec']['containers']) == 1 and x['spec']['containers'][0]['image'] == dbconfig['backend_image']]
    need(eligible, 'CURRENT_APPROVED_BACKEND_REQUIRED')
    pod = sorted(eligible, key=lambda x: x['metadata']['name'])[0]
    ca = json.loads(call(['get', 'configmap', 'backend-database-ca', '-n', 'seokpan-cloud', '-o', 'json']))
    need(ca['metadata']['uid'] == dbconfig['database_ca_uid'], 'SAME_DATABASE_CA_REQUIRED')
    schema = read(dbconfig['schema_credentials_file'])
    marker = out/'process-grant.attempted.json'
    allow = not marker.exists()
    if allow:
        save(marker, {'scope': 'GRANT_PROCESS_ONLY_MATCHED_DB_ADMIN', 'automatic_retry': False})
    payload = {'host': dbconfig['database_host'], 'schema_url': schema['database_url'],
               'master': master, 'allow_grant': allow,
               'ca_file': observer.database_ca_path(pod),
               'ca_sha256': hashlib.sha256(ca['data']['ca.crt'].encode()).hexdigest()}
    result = json.loads(call(['exec', '-i', '-n', 'seokpan-cloud', pod['metadata']['name'],
                              '-c', 'backend', '--', 'python', '-c', GRANT_PROGRAM], canonical(payload)))
    need(result['status'] == 'PASS_EXACT_OBSERVER_PROCESS_GRANTED_OR_REUSED', 'OBSERVER_PROCESS_GRANT_FAILED')
    save(accepted, result)
    return result


def feed(dispatch):
    d = read(dispatch)
    c = read(d['config'])
    H = load('hpa_existing_publication', BASE/'readonly_handoff.py')
    work, target = Path(d['work']), Path(d['shared'])
    lock = os.open(target/'.publisher.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    fingerprint = hashlib.sha256(canonical(c)).hexdigest()
    try:
        deadline = min(time.monotonic()+3600, time.monotonic()+max(0,
            dt.datetime.fromisoformat(c['window']['not_after']).timestamp()-time.time()))
        while time.monotonic() < deadline:
            need(not (work/'STOP.json').exists() and not (work/'restore.STARTED.json').exists(), 'CONTROL_STOPPED')
            row = read(work/'latest.json')
            H.guard_row(row, fingerprint, c['binding'], time.time())
            H.publish_file(target/'state/latest.json', canonical(row)+b'\n', 'cyj')
            time.sleep(1)
        raise RuntimeError('HPA_PUBLICATION_WINDOW_ENDED')
    except Exception as e:
        reason = str(e) if type(e) is RuntimeError else type(e).__name__
        H.publish_file(target/'state/STOP.json', canonical({'status': 'STOP_HANDOFF',
            'reason': reason, 'utc': dt.datetime.now(dt.timezone.utc).isoformat()})+b'\n', 'cyj')
        return 2
    finally:
        os.close(lock)


def spawn(name, argv, out):
    marker = out/(name+'.process.json')
    need(not marker.exists(), 'EXISTING_PROCESS_PRESERVED_NO_DUPLICATE')
    fd = os.open(out/(name+'.private.log'), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        environment = os.environ.copy()
        for key in ('AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY', 'AWS_SESSION_TOKEN', 'AWS_SECURITY_TOKEN'):
            environment.pop(key, None)
        p = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=stream,
                             stderr=subprocess.STDOUT, start_new_session=True, env=environment)
    save(marker, {'pid': p.pid, 'argv': argv})


def execute(activate):
    need(pwd.getpwuid(os.getuid()).pw_name == 'jth', 'USE_CONTROLLER_JTH')
    settings = read(BASE/'handoff-settings.json')
    prepared = read(RUN/'presentation-final-20261020/receipt.json')
    need(prepared['status'] == 'PASS_SELECTED_PRESENTATION_INPUTS_READY_NOT_TESTED'
         and prepared['cluster_id'] == CID and prepared['binding']['namespace_uid'] == NSUID
         and prepared['binding']['application_uid'] == APPUID, 'EXISTING_PROFILE_SUCCESS_REQUIRED')
    pointer = RUN/'hpa-final-dispatch.json'
    result = {'status': 'STOP_HPA_FINAL_BIND', 'load_started': False,
              'previous_tests_repeated': False, 'credentials_printed': False, 'automatic_retry': False}
    if pointer.exists():
        dispatch = read(pointer)
        out = Path(dispatch['directory'])
        c = read(dispatch['config'])
        if (out/'activated.json').exists():
            H = load('hpa_existing_live_feed', BASE/'readonly_handoff.py')
            target = Path(dispatch['shared'])
            need(not (target/'state/STOP.json').exists(), 'EXISTING_HPA_FEED_STOPPED_RECONCILE')
            H.guard_row(read(target/'state/latest.json'), hashlib.sha256(canonical(c)).hexdigest(), c['binding'], time.time())
            reused = read(out/'activated.json')
            reused.update(status='PASS_EXISTING_HPA_ACTIVATION_REUSED_NO_SECOND_START', activation_repeated=False)
            return reused
        need(dt.datetime.fromisoformat(c['window']['not_after']).timestamp()-time.time() > 3600,
             'EXISTING_HPA_WINDOW_NEEDS_NEW_RUN_NOT_SILENT_EXTENSION')
    else:
        now = dt.datetime.now(dt.timezone.utc)
        run_id = 'hpa-'+now.strftime('%Y%m%d-%H%M%S')
        out = RUN/run_id
        out.mkdir(mode=0o700)
        dispatch = {'directory': str(out), 'config': str(out/'config.json'),
                    'work': str(out/'control'), 'shared': str(Path(settings['handoff_root'])/run_id)}
        save(out/'initial-dispatch.json', dispatch)
    try:
        H = load('hpa_public_contract', BASE/'readonly_handoff.py')
        source = Path(settings['source_dir'])
        sys.path.insert(0, str(source))
        C = load('common', source/'common.py')
        code = Path(settings['handoff_root'])/('code-'+settings['adopted_ref'][:12])
        preparation = read(Path(settings['handoff_root'])/'preparation.json')
        need(preparation['adopted_ref'] == settings['adopted_ref']
             and preparation['code_dir'] == str(code), 'EXISTING_ADOPTED_SHARED_CODE_REQUIRED')
        for name in ('common.py', 'control.py', 'load.mjs'):
            need(hashlib.sha256((code/name).read_bytes().replace(b'\r\n', b'\n')).hexdigest()
                 == preparation['source_sha256'][name], 'SHARED_EXECUTION_SOURCE_CHANGED')
            need(hashlib.sha256((source/name).read_bytes().replace(b'\r\n', b'\n')).hexdigest()
                 == preparation['source_sha256'][name], 'PRIVATE_AND_SHARED_EXECUTION_SOURCE_DIFFER')
        initial = read(RUN/'current-fresh-app-run.json')
        pre = read(initial['config'])
        inputs = read(Path(initial['work'])/'new-inputs.json')['result']['inputs']
        def call(args, payload=None, admin=False):
            context = ROOT/'held-restoration'/CID/'kubeconfig' if admin else Path(pre['personal_kubeconfig'])
            p = subprocess.run([pre['oc'], '--kubeconfig', str(context), '--request-timeout=10s', *args],
                               input=payload, capture_output=True, timeout=50)
            if p.returncode:
                try:
                    failure = json.loads(p.stdout)
                except Exception:
                    failure = {}
                reason = failure.get('reason', 'CURRENT_COMMAND_FAILED_PRIVATE_OUTPUT')
                need(False, reason if re.fullmatch(r'[A-Za-z0-9_]{1,160}', reason) else 'CURRENT_COMMAND_FAILED')
            return p.stdout
        if not pointer.exists():
            nodes = json.loads(call(['get', 'nodes', '-o', 'json'], admin=True))['items']
            pods = json.loads(call(['get', 'pods', '-A', '-o', 'json'], admin=True))['items']
            slots, slot_rows = node_capacity(nodes, pods, [x['node'] for x in prepared['existing_workers']])
            save(out/'node-capacity.json', {'capacity': slots, 'nodes': slot_rows,
                 'scope': 'REQUEST_BOUND_CONSERVATIVE_NOT_PERFORMANCE_CAPACITY'})
            deployment = json.loads(call(['get', 'deployment', 'backend', '-n', 'seokpan-cloud', '-o', 'json']))
            surge_raw = deployment['spec'].get('strategy', {}).get('rollingUpdate', {}).get('maxSurge', '25%')
            surge = math.ceil(6*int(surge_raw[:-1])/100) if isinstance(surge_raw, str) and surge_raw.endswith('%') else int(surge_raw)
            need(slots >= 6+surge+1, 'CURRENT_NODES_CANNOT_SUPPORT_DECLARED_PEAK')
            runtime = read(RUN/'current-monitoring-closeout-20261020/receipt.json')
            accounts = {}
            for name in ('identity_svc', 'game_svc'):
                row, = [x for x in runtime['accounts'] if x['account'] == name]
                need(row['status'] == 'PASS_PURPOSE_ACCOUNT_READ_ONLY' and row['tls'] is True
                     and row['max_connections'] == 68 and row['effective_user_connection_limit'] == 0,
                     'EXISTING_PURPOSE_ACCOUNT_LIMIT_EVIDENCE_REQUIRED')
                accounts[name] = {'max_user_connections': 0, 'global_max_user_connections': 0,
                                  'evidence_ref': str(RUN/'current-monitoring-closeout-20261020/receipt.json')}
            c = copy.deepcopy(read(settings['source_config']))
            end = now+dt.timedelta(minutes=75)
            need(end < dt.datetime(2026, 10, 20, 18, 0, tzinfo=dt.timezone.utc), 'KEEP_ROSA_DELETE_BEFORE_0300_KST')
            c.update(run_id=run_id, actor='tjung03', namespace='seokpan-cloud', oc=pre['oc'],
                     personal_kubeconfig=pre['personal_kubeconfig'], api=inputs['api_url'],
                     origin='https://seokpan.'+inputs['ingress_domain'],
                     binding={k: prepared['binding'][k] for k in ('cluster_id', 'namespace_uid', 'application_uid', 'normalized_source_sha256')},
                     window={'not_before': now.isoformat(), 'not_after': end.isoformat()},
                     min_replicas=3, max_replicas=6,
                     expected_images=read(prepared['rolling_config'])['expected_images'],
                     normal_business_ref='https://github.com/seokpan/seokpan-hybrid-infra/pull/105#issuecomment-6290800041',
                     prior_comparison_finished_ref='https://github.com/seokpan/seokpan-hybrid-infra/pull/105#issuecomment-6290800041')
            c['capacity'] = {'evidence_ref': str(RUN/'current-monitoring-closeout-20261020/receipt.json'),
                'node_capacity_ref': str(out/'node-capacity.json'), 'global_limit': 68, 'reserved': 15,
                'processes': 1, 'pool_size': 2, 'overflow': 0, 'surge_allowance': surge,
                'terminating_allowance': 1, 'node_backend_capacity': slots, 'accounts': accounts}
            db = read(RUN/'current-monitoring-closeout-20261020/db-observer-total-only.json')
            db.update(binding=c['binding'], observation_scope='FULL', allowed_normalized_source_sha256=[c['binding']['normalized_source_sha256']],
                      not_after=end.isoformat(), duration_seconds=900, interval_seconds=10)
            save(out/'db-observer.json', db)
            c['db_probe_argv'] = [sys.executable, '-I', str(BASE/'pod_db_observer.py'), 'probe', '--config', str(out/'db-observer.json'), '--execute']
            c['db_local_paths'] = {'transport': 'EXISTING_BACKEND_EXEC',
                'schema_credentials_file': db['schema_credentials_file'], 'ca_file': '/etc/seokpan/database-ca/ca.crt',
                'database_host': db['database_host']}
            target, guard = H.target_paths(settings, run_id, code)
            c['generator_guard_argv'] = guard
            H.public_contract(c)
            need(not C.missing(c), 'UNFILLED_HPA_INPUTS_REMAIN')
            C.validate(c)
            save(Path(dispatch['config']), c)
            Path(dispatch['work']).mkdir(mode=0o700)
            save(pointer, dispatch)
        c = read(dispatch['config'])
        target = Path(dispatch['shared'])
        H.directory(target, 'cyj')
        H.directory(target/'state', 'cyj')
        plan = declared_plan(c, C)
        H.publish_file(target/'config.json', canonical(c)+b'\n', 'cyj', True)
        H.publish_file(target/'plan.json', canonical(plan)+b'\n', 'cyj', True)
        if not (out/'activated.json').exists():
            H.publish_file(target/'state/latest.json', canonical({'status': 'PENDING_HPA_ACTIVATION',
                'config_sha256': C.digest(c), 'binding': c['binding'], 'utc': C.utc()})+b'\n', 'cyj')
        result.update(config_published=True, plan_published=True, state_published=True,
                      public_plan_acceptance='PENDING_RUNTIME_PREPARE', directory=str(out),
                      run_id=c['run_id'], config=dispatch['config'], shared=str(target), guard_argv=c['generator_guard_argv'])
        print(json.dumps({'phase': 'EXISTING_VALUES_BOUND_AND_PUBLIC_FILES_PUBLISHED', 'run_id': c['run_id']}), flush=True)
        db = read(out/'db-observer.json')
        lifecycle_pointer = read(RUN/'today-recreate-dispatch-20261020.json')
        lifecycle = read(lifecycle_pointer['config'])
        foundation = read(lifecycle['create']['var_file'])['foundation']
        if 'account_id' not in foundation:
            foundation['account_id'] = '847835841591'
        result['observer_process'] = grant_process(db, out, call, foundation)
        argv = [sys.executable, '-I', str(source/'control.py')]
        work = Path(dispatch['work'])
        def control(action):
            p = subprocess.run(argv+[action, '--config', dispatch['config'], '--work', str(work), '--execute'],
                               capture_output=True, timeout=120)
            stamp = str(time.time_ns())
            save(out/(action+'-'+stamp+'.private.json'), {'exit': p.returncode, 'stdout': p.stdout.decode(errors='replace'), 'stderr': p.stderr.decode(errors='replace')})
            need(p.returncode == 0, 'HPA_'+action.upper()+'_FAILED_PRIVATE_RESULT')
            return json.loads(p.stdout.splitlines()[-1])
        if not (work/'before.json').exists():
            result['prepare'] = control('prepare')
        need(read(work/'plan.json') == plan, 'PREPARED_PLAN_DIFFERS_FROM_PUBLISHED_PLAN')
        result['public_plan_acceptance'] = 'PASS_RUNTIME_PREPARED'
        if not activate:
            result['status'] = 'PASS_HPA_FILES_AND_FULL_DB_PREPARED_NOT_ACTIVATED'
            return result
        marker = out/'activation.attempted.json'
        need(not marker.exists(), 'PARTIAL_HPA_ACTIVATION_REQUIRES_RECONCILIATION')
        need(dt.datetime.fromisoformat(c['window']['not_after']).timestamp()-time.time() >= 3600,
             'SIXTY_MINUTE_HPA_WINDOW_REQUIRED')
        save(marker, {'utc': C.utc(), 'automatic_retry': False})
        result['activation'] = control('start')
        activated_at = C.utc()
        spawn('observer', argv+['observe', '--config', dispatch['config'], '--work', str(work), '--seconds', '2700', '--execute'], out)
        deadline = time.monotonic()+60
        while time.monotonic() < deadline:
            if (work/'latest.json').exists():
                row = read(work/'latest.json')
                if row['status'] in ('PASS_HPA_GUARD', 'WAIT_HPA_METRICS'):
                    break
                need(False, 'HPA_INITIAL_OBSERVER_STOPPED')
            time.sleep(1)
        need((work/'latest.json').exists(), 'HPA_FIRST_STATE_PENDING')
        H.guard_row(read(work/'latest.json'), C.digest(c), c['binding'], time.time())
        H.publish_file(target/'state/latest.json', canonical(read(work/'latest.json'))+b'\n', 'cyj')
        spawn('publisher', [sys.executable, '-I', str(Path(__file__).resolve()), 'feed', '--dispatch', str(pointer)], out)
        dwork = '/home/cyj/hpa-load/out/'+c['run_id']
        dargv = [settings['python'], '-I', str(Path(settings['handoff_root'])/'d_generator.py'),
                 '--preparation', str(Path(settings['handoff_root'])/'preparation.json'),
                 '--plan', str(target/'plan.json'), '--work', dwork,
                 '--return-root', str(Path(settings['handoff_root'])/'return/cyj'), '--execute']
        result.update(status='PASS_HPA_ACTIVATED_AND_LIVE_STATE_PUBLISHED_D_START_GO',
            hpa_created=True, activate_utc=activated_at, window=c['window'],
            shared_state=str(target/'state/latest.json'),
            d_start_command='mkdir -p -- '+shlex.quote('/home/cyj/hpa-load/out')+' '+shlex.quote(str(Path(settings['handoff_root'])/'return/cyj'))+' && '+shlex.join(dargv),
            d_abort_file=dwork+'.abort', d_return_root=str(Path(settings['handoff_root'])/'return/cyj'),
            stop_command=shlex.join(argv+['stop', '--config', dispatch['config'], '--work', str(work), '--execute']),
            restore_command=shlex.join(argv+['restore', '--config', dispatch['config'], '--work', str(work),
                '--load-receipt', str(Path(settings['handoff_root'])/'return/cyj'/c['run_id']/'receipt.json'), '--execute']),
            verify_restore_command=shlex.join(argv+['verify-restore', '--config', dispatch['config'], '--work', str(work), '--execute']),
            rosa_delete_deadline_kst='2026-10-21T03:00:00+09:00',
            interpretation='STANDALONE_HPA_NOT_SUCCESSFUL_ROLLING_COMPARISON')
        save(out/'activated.json', result)
        return result
    except Exception as e:
        reason = str(e) if type(e) is RuntimeError else type(e).__name__
        result.update(reason=reason, directory=str(out))
        save(out/('stop-'+str(time.time_ns())+'.json'), result)
        return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=['execute', 'feed'])
    p.add_argument('--activate', action='store_true')
    p.add_argument('--dispatch', type=Path)
    a = p.parse_args()
    os.umask(0o077)
    if a.action == 'feed':
        return feed(a.dispatch)
    lock = os.open(RUN/'hpa-finalize.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        try:
            result = execute(a.activate)
        except Exception as e:
            reason = str(e) if type(e) is RuntimeError else type(e).__name__
            result = {'status': 'STOP_HPA_FINAL_BIND', 'reason': reason,
                      'automatic_retry': False, 'credentials_printed': False}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result['status'].startswith('PASS_') else 2
    finally:
        os.close(lock)


if __name__ == '__main__':
    raise SystemExit(main())
