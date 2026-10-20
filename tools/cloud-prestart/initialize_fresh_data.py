"""Initialize only the inspected empty Cloud DB, then use the existing app stage."""
import argparse
import base64
import copy
import hashlib
import importlib.util
import ipaddress
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile


def need(ok,code):
    if not ok:raise RuntimeError(code)


def master_value(db,metadata,value,expected_host,account):
    arn=db.get('MasterUserSecret',{}).get('SecretArn','')
    need(db.get('DBInstanceStatus')=='available' and db.get('Endpoint',{}).get('Address')==expected_host
         and db['Endpoint'].get('Port')==3306,'EXACT_AVAILABLE_RDS_TARGET_REQUIRED')
    need(arn.startswith('arn:aws:secretsmanager:ap-northeast-2:'+account+':secret:')
         and metadata.get('ARN')==arn and value.get('ARN')==arn,'EXACT_RDS_MANAGED_SECRET_REQUIRED')
    stages=metadata.get('VersionIdsToStages',{})
    current=[k for k,v in stages.items() if 'AWSCURRENT' in v]
    need(len(current)==1 and value.get('VersionId')==current[0]
         and 'AWSCURRENT' in value.get('VersionStages',[]),'CURRENT_MASTER_VERSION_REQUIRED')
    need(not any('AWSPENDING' in v and k!=current[0] for k,v in stages.items()),'MASTER_ROTATION_IN_PROGRESS_NO_DB_CHANGES')
    secret=json.loads(value['SecretString'])
    need(secret.get('username')==db.get('MasterUsername') and isinstance(secret.get('password'),str)
         and secret['password'] and secret.get('host',expected_host)==expected_host
         and int(secret.get('port',3306))==3306,'CURRENT_MASTER_TARGET_REQUIRED')
    return {'username':secret['username'],'password':secret['password']}


REMOTE=r'''
import asyncio,base64,hashlib,io,json,os,re,ssl,subprocess,sys
from urllib.parse import urlsplit,unquote
TABLES=('member','game','game_result','game_participant','member_stats','move','rating_history')
class Stop(RuntimeError):pass
def need(ok,code):
    if not ok:raise Stop(code)
def safe(error):
    return {'reason':str(error) if type(error) is Stop else type(error).__name__,
        'error_code':next((x for x in error.args if type(x) is int),None)}
def account_creation_plan(rows,expected_host):
    need(expected_host=='192.168.64.0/255.255.240.0','ACCEPTED_ACCOUNT_HOST_CONTRACT_REQUIRED')
    need(not rows,'EXISTING_PURPOSE_ACCOUNT_REQUIRES_RECONCILIATION_NO_PASSWORD_RESET')
    return ['identity_svc','game_svc']
async def execute(p,connect,migrate):
    result={'status':'STOP_FRESH_DATA_INITIALIZATION','database_create_attempted':False,'database_created':False,
        'schema_upgrade_attempted':False,'schema_upgrade_executed':False,
        'accounts_created':[],'grant_statements_executed':0,'data_rows_written':0,
        'old_data_imported':False,'database_dropped':False,'passwords_reset':False,'automatic_retry':False}
    master=None;admin=None
    try:
        need(p.get('data_mode')=='fresh_test' and p.get('expected_revision')=='20260902_0002'
             and p.get('target_database')=='stone_game','EXACT_FRESH_TARGET_REQUIRED')
        need(hashlib.sha256(open('/run/rds-ca/ca.crt','rb').read()).hexdigest()==p['ca_sha256'],'CURRENT_CA_REQUIRED')
        context=ssl.create_default_context(cafile='/run/rds-ca/ca.crt')
        context.minimum_version=ssl.TLSVersion.TLSv1_2
        d=urlsplit(p['schema_url'])
        need(d.scheme=='mysql+asyncmy' and d.hostname==p['database_host'] and d.port==3306
             and unquote(d.username or '')=='db_admin' and d.password
             and unquote(d.path.lstrip('/'))=='stone_game' and not d.query and not d.fragment,'EXACT_DB_ADMIN_TARGET_REQUIRED')
        passwords={}
        for name in ('identity_svc','game_svc'):
            u=urlsplit(p['purpose_urls'][name])
            need(u.scheme=='mysql+asyncmy' and u.hostname==p['database_host'] and u.port==3306
                 and unquote(u.username or '')==name and u.password and unquote(u.path.lstrip('/'))=='stone_game'
                 and not u.query and not u.fragment,'EXACT_PURPOSE_TARGET_REQUIRED')
            passwords[name]=unquote(u.password)
        master=await connect(host=p['database_host'],port=3306,user=p['master']['username'],
            password=p['master']['password'],ssl=context,autocommit=True,connect_timeout=8)
        admin=await connect(host=d.hostname,port=3306,user='db_admin',password=unquote(d.password),
            ssl=context,autocommit=True,connect_timeout=8)
        async with master.cursor() as cur:
            await cur.execute("SHOW SESSION STATUS LIKE 'Ssl_cipher'")
            need((await cur.fetchone())[1],'MASTER_TLS_REQUIRED')
            await cur.execute('SELECT User,Host FROM mysql.user WHERE User IN (%s,%s)',('identity_svc','game_svc'))
            plan=account_creation_plan(await cur.fetchall(),p['account_host'])
            await cur.execute('SELECT SCHEMA_NAME FROM information_schema.SCHEMATA WHERE SCHEMA_NAME=%s',('stone_game',))
            need(not await cur.fetchall(),'DATABASE_NOW_EXISTS_RECONCILE_NO_DDL')
        async with admin.cursor() as cur:
            await cur.execute("SHOW SESSION STATUS LIKE 'Ssl_cipher'")
            need((await cur.fetchone())[1],'DB_ADMIN_TLS_REQUIRED')
            result['database_create_attempted']=True
            await cur.execute('CREATE DATABASE stone_game CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci')
            result['database_created']=True
        result['schema_upgrade_attempted']=True
        migration=migrate(p)
        result['schema_upgrade_executed']=migration.returncode==0
        result['private_execution_log_b64']=base64.b64encode(migration.stdout+migration.stderr).decode()
        need(migration.returncode==0,'SCHEMA_UPGRADE_FAILED_NO_AUTOMATIC_RETRY')
        async with admin.cursor() as cur:
            await cur.execute('SELECT version_num FROM stone_game.alembic_version')
            revisions=[r[0].decode() if isinstance(r[0],bytes) else r[0] for r in await cur.fetchall()]
            need(revisions==['20260902_0002'],'ACTUAL_SCHEMA_REVISION_MISMATCH')
            await cur.execute('SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA=%s',('stone_game',))
            names={r[0].decode() if isinstance(r[0],bytes) else r[0] for r in await cur.fetchall()}
            need(names==set(TABLES)|{'alembic_version'},'EXACT_INITIAL_SCHEMA_TABLES_REQUIRED')
            for table in TABLES:
                await cur.execute('SELECT 1 FROM stone_game.'+table+' LIMIT 1')
                need(not await cur.fetchall(),'INITIAL_APP_TABLE_NOT_EMPTY_NO_DELETE')
        async with master.cursor() as cur:
            for user in plan:
                await cur.execute('CREATE USER %s@%s IDENTIFIED BY %s REQUIRE SSL',(user,p['account_host'],passwords[user]))
                result['accounts_created'].append(user)
                tables=('member',) if user=='identity_svc' else TABLES[1:]
                for table in tables:
                    await cur.execute('GRANT SELECT,INSERT,UPDATE,DELETE ON stone_game.'+table+' TO %s@%s',(user,p['account_host']))
                    result['grant_statements_executed']+=1
                if user=='game_svc':
                    await cur.execute('GRANT SELECT (nickname,member_id,rating),UPDATE (rating) ON stone_game.member TO %s@%s',(user,p['account_host']))
                    result['grant_statements_executed']+=1
            await cur.execute('SELECT User,Host,plugin,ssl_type FROM mysql.user WHERE User IN (%s,%s)',('identity_svc','game_svc'))
            users=await cur.fetchall()
            need(len(users)==2 and {r[0] for r in users}==set(plan)
                 and all(r[1]==p['account_host'] and r[2]=='mysql_native_password' and r[3]=='ANY' for r in users),
                 'ACTUAL_ACCOUNT_HOST_PLUGIN_SSL_MISMATCH')
        result.update(status='PASS_FRESH_SCHEMA_AND_PURPOSE_ACCOUNTS_PREPARED',
            reported_schema_revision=revisions[0],application_tables_empty=True,
            grant_contract='DATA44_STEP7_EXACT_STATEMENTS',purpose_tls_runtime='NEXT_EXISTING_ACCEPTED_PROBE')
    except Exception as error:
        result.update(safe(error))
    finally:
        if admin is not None:admin.close()
        if master is not None:master.close()
    return result
def migrate(p):
    env=dict(os.environ,SEOKPAN_MIGRATION_DATABASE_URL=p['schema_url'],SEOKPAN_CONNECTION_PROFILE='cloud',
        SEOKPAN_DATABASE_EXPECTED_HOST=p['database_host'],SEOKPAN_DATABASE_EXPECTED_PORT='3306',
        SEOKPAN_DATABASE_EXPECTED_NAME='stone_game',SEOKPAN_DATABASE_CA_FILE='/run/rds-ca/ca.crt',PYTHONDONTWRITEBYTECODE='1')
    args=[sys.executable,'-c','from seokpan.persistence.mariadb.migration_gate import main; main()',
        'upgrade-head','--expect-host',p['database_host'],'--expect-port','3306','--expect-database','stone_game',
        '--config','/app/alembic.ini','--execute','--approval-ref',p['approval_ref']]
    return subprocess.run(args,env=env,cwd='/app',capture_output=True,timeout=120)
try:
    import asyncmy
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    need(ScriptDirectory.from_config(Config('/app/alembic.ini')).get_heads()==['20260902_0002'],'APPROVED_IMAGE_MIGRATION_HEAD_REQUIRED')
    result=asyncio.run(execute(json.load(sys.stdin),asyncmy.connect,migrate))
except Exception as error:
    result=dict(safe(error),status='STOP_FRESH_DATA_INITIALIZATION',automatic_retry=False)
print(json.dumps(result))
'''


def load(path,name):
    spec=importlib.util.spec_from_file_location(name,path)
    value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value)
    return value


def saved_infrastructure(lifecycle,root):
    cid=lifecycle['current_cluster_id']
    folder=Path(lifecycle['evidence_root'])/'held-restoration'/cid
    receipt=json.loads((folder/'fresh-worker-binding.complete.json').read_text())
    need(receipt.get('cluster_id')==cid and receipt['result'].get('verified') is True,'ACCEPTED_WORKER_BINDING_REQUIRED')
    bound=json.loads(Path(receipt['result']['bound_inputs']).read_text())
    binding=bound['worker_sg_binding'];sg=binding['worker_security_group_id']
    evidence=json.loads(Path(binding['evidence_reference']).read_text())
    group=[g for g in evidence['security_groups']['SecurityGroups'] if g['GroupId']==sg]
    need(len(group)==1,'SAVED_BOUND_WORKER_GROUP_REQUIRED')
    g=group[0];tags={x['Key']:x['Value'] for x in g.get('Tags',[])}
    result={'worker_binding_accepted':True,'worker_security_group_id':sg,'group_name':g.get('GroupName'),
        'name_tag':tags.get('Name'),'matches_A_reported_name':'seokpan-cloud-k8gqx-lb' in (g.get('GroupName'),tags.get('Name')),
        'binding_evidence':str(folder/'fresh-worker-binding.complete.json'),
        'worker_machine_evidence':binding['evidence_reference'],'operator_attachments':[],
        'operator_mapping_status':'SAVED_STATE_NOT_FOUND_NOT_ASSERTED'}
    candidates=[]
    for pattern in ('create-reconcile-read-*','ready-cluster-recovery-*','saved-refresh-resume-*'):
        for directory in root.glob(pattern):
            for path in list(directory.glob('*.json'))+list(directory.glob('*.tfstate')):
                if path.is_symlink() or not path.is_file() or path.stat().st_size>16777216:continue
                try:s=json.loads(path.read_text())
                except (ValueError,UnicodeError):continue
                resources=s.get('resources',[]) if isinstance(s,dict) else []
                clusters=[x.get('attributes',{}).get('id') for r in resources if r.get('type')=='rhcs_cluster_rosa_classic' for x in r.get('instances',[])]
                if cid not in clusters:continue
                rows=[{'role':x['attributes'].get('role'),'policy_arn':x['attributes'].get('policy_arn')}
                    for r in resources if r.get('type')=='aws_iam_role_policy_attachment' for x in r.get('instances',[])]
                roles={x['attributes'].get('name') for r in resources if r.get('type')=='aws_iam_role' for x in r.get('instances',[])}
                if len(rows)==6 and all(x['role'] in roles and x['policy_arn'] for x in rows):
                    candidates.append((s.get('serial',-1),str(path),rows))
    if candidates:
        serial,path,rows=max(candidates,key=lambda x:x[0])
        result.update(operator_attachments=rows,operator_mapping_status='SAVED_CURRENT_CLUSTER_STATE_MAPPING',
            operator_mapping_state=path,operator_mapping_state_serial=serial)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dispatch',required=True,type=Path)
    parser.add_argument('--inspection',required=True,type=Path)
    parser.add_argument('--db-instance',required=True)
    parser.add_argument('--account-network',required=True)
    parser.add_argument('--approval-ref',required=True)
    parser.add_argument('--execute',action='store_true')
    a=parser.parse_args();os.umask(0o077)
    out=None;infra=None;phase='LOCAL_ACCEPTED_INPUTS';result={'status':'STOP_FRESH_DB_PREPARE','automatic_retry':False}
    try:
        need(a.execute and re.fullmatch(r'[A-Za-z0-9_.:-]{3,160}',a.approval_ref),'EXPLICIT_FRESH_SETUP_APPROVAL_REQUIRED')
        import pwd,fcntl
        need(pwd.getpwuid(os.getuid()).pw_name=='jth','USE_CONTROLLER_JTH')
        d=json.loads(a.dispatch.read_text());P=load(Path(d['source']),'current_prestart')
        d=P.read(a.dispatch);config=P.read(d['config']);work=Path(d['work']);root=a.dispatch.parent.parent
        with open(str(work)+'.lock','a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            flow=P.Flow(config,work,prepare_data_pod=True)
            probe=P.read(a.inspection);target=P.read(work/'prepared-data-target.json')
            need(probe.get('status')=='COLLECTED_CURRENT_DATA_SETUP_STATE' and probe.get('cluster_id')==flow.cluster
                 and probe.get('pod_uid')==target['pod_uid'] and probe.get('schema',{}).get('status')=='LOGIN_AND_TLS_OK'
                 and probe['schema'].get('database_exists') is False and probe.get('redis',{}).get('status')=='TLS_AUTH_PING_OK',
                 'ACCEPTED_ABSENT_DATABASE_AND_CURRENT_TLS_INSPECTION_REQUIRED')
            c=P.read(work/'new-inputs.json')['result']['inputs']
            pointer=a.dispatch.parent/'current-fresh-db-setup.json'
            if pointer.exists():
                previous=P.read(pointer);out=Path(previous['directory'])
                need(previous['binding']==flow.binding,'SAME_FRESH_SETUP_BINDING_REQUIRED')
                if (out/'remote-attempted.json').exists():
                    accepted=P.read(out/'receipt.json') if (out/'receipt.json').exists() else {}
                    need(accepted.get('status')=='PASS_FRESH_SCHEMA_AND_PURPOSE_ACCOUNTS_PREPARED',
                         'PARTIAL_DB_SETUP_REQUIRES_RECONCILIATION_NO_RETRY')
                    result=accepted
            else:
                out=Path(tempfile.mkdtemp(prefix='fresh-db-setup-',dir=work.parent))
                P.write(pointer,{'directory':str(out),'binding':flow.binding})
            infra=saved_infrastructure(flow.lifecycle,root)
            if not (out/'infrastructure-handoff.json').exists():P.write(out/'infrastructure-handoff.json',infra)
            if (out/'current-operator-mapping.json').exists():infra=P.read(out/'current-operator-mapping.json')
            print(json.dumps({'phase':'SAVED_INFRASTRUCTURE_HANDOFF','handoff':str(out/'infrastructure-handoff.json'),
                'worker_security_group_id':infra['worker_security_group_id'],'group_name':infra['group_name'],
                'matches_A_reported_name':infra['matches_A_reported_name'],'operator_mapping_status':infra['operator_mapping_status']}),flush=True)

            if result.get('status')!='PASS_FRESH_SCHEMA_AND_PURPOSE_ACCOUNTS_PREPARED':
                phase='CURRENT_PERSONAL_MASTER_CREDENTIAL'
                foundation=P.read(flow.lifecycle['create']['var_file'])['foundation']
                def aws(*args,optional=False):
                    try:
                        proc=subprocess.run(['aws',*args,'--output','json','--no-cli-pager'],capture_output=True,timeout=35)
                    except subprocess.TimeoutExpired:
                        if optional:return None
                        raise RuntimeError('AWS_READ_TIMEOUT_NO_AUTOMATIC_RETRY')
                    if proc.returncode:
                        if optional:return None
                        code=re.search(rb'\((\w+)\)',proc.stderr)
                        raise RuntimeError('AWS_'+args[0].upper()+'_'+(code.group(1).decode() if code else 'READ_FAILED'))
                    return json.loads(proc.stdout)
                identity=aws('sts','get-caller-identity')
                need(identity['Account']==foundation['account_id'] and ':user/' in identity['Arn'],'EXPECTED_PERSONAL_AWS_ACCOUNT_REQUIRED')
                dbs=aws('rds','describe-db-instances','--db-instance-identifier',a.db_instance)['DBInstances']
                need(len(dbs)==1 and dbs[0]['DBSubnetGroup']['VpcId']==foundation['vpc_id'],'EXACT_CURRENT_DATA_VPC_REQUIRED')
                db=dbs[0];arn=db.get('MasterUserSecret',{}).get('SecretArn')
                need(arn and db['Endpoint']['Address']==c['database_host'],'EXACT_BOUND_RDS_MASTER_SECRET_REQUIRED')
                vpcs=aws('ec2','describe-vpcs','--vpc-ids',foundation['vpc_id'])['Vpcs']
                network=ipaddress.ip_network(a.account_network)
                need(str(network)=='192.168.64.0/20' and len(vpcs)==1
                     and network.subnet_of(ipaddress.ip_network(vpcs[0]['CidrBlock'])),'UNCHANGED_ACCOUNT_SOURCE_NETWORK_REQUIRED')
                meta=aws('secretsmanager','describe-secret','--secret-id',arn)
                versions=[k for k,v in meta['VersionIdsToStages'].items() if 'AWSCURRENT' in v]
                need(len(versions)==1,'ONE_CURRENT_MASTER_VERSION_REQUIRED')
                value=aws('secretsmanager','get-secret-value','--secret-id',arn,'--version-id',versions[0],'--version-stage','AWSCURRENT')
                master=master_value(db,meta,value,c['database_host'],foundation['account_id'])
                for row in infra['operator_attachments']:
                    entities=aws('iam','list-entities-for-policy','--policy-arn',row['policy_arn'],'--entity-filter','Role',optional=True)
                    row['current_expected_role_attached']=None if entities is None else row['role'] in {x['RoleName'] for x in entities.get('PolicyRoles',[])}
                P.write(out/'current-operator-mapping.json',infra)
                flow.verify_pod(target,c)
                deployments=json.loads(flow.call('get','deployment','-n','seokpan-cloud','-o','json'))
                need(all(o['spec'].get('replicas',1)==0 for o in deployments.get('items',[])),'APPS_MUST_REMAIN_HELD_BEFORE_INITIALIZATION')
                rows=P.read(config['runtime_inputs_file'])['items'];objects={(o['kind'],o['metadata']['name']):o for o in rows}
                payload={'data_mode':'fresh_test','expected_revision':'20260902_0002','target_database':'stone_game',
                    'database_host':c['database_host'],'schema_url':flow.schema['database_url'],
                    'master':master,'account_host':str(network.network_address)+'/'+str(network.netmask),
                    'ca_sha256':hashlib.sha256(objects[('ConfigMap','backend-database-ca')]['data']['ca.crt'].encode()).hexdigest(),
                    'purpose_urls':{name:base64.b64decode(objects[('Secret','backend-db-runtime')]['data'][field],validate=True).decode()
                        for name,field in (('identity_svc','SEOKPAN_IDENTITY_DATABASE_URL'),('game_svc','SEOKPAN_GAME_DATABASE_URL'))},
                    'approval_ref':a.approval_ref}
                phase='INITIALIZE_EMPTY_CLOUD_DATABASE_AND_ABSENT_PURPOSE_ACCOUNTS'
                P.write(out/'remote-attempted.json',{'binding':flow.binding,'approval_ref':a.approval_ref})
                print(json.dumps({'phase':phase,'database_changes_allowed':True,'legacy_import':False}),flush=True)
                proc=subprocess.run([config['oc'],'--kubeconfig',config['personal_kubeconfig'],'--request-timeout=20s',
                    'exec','-i','-n','seokpan-cloud',target['pod_name'],'-c','db-admin','--','python','-c',REMOTE],
                    input=P.canon(payload),capture_output=True,timeout=240)
                need(proc.returncode==0,'DB_EXEC_OUTCOME_REQUIRES_RECONCILIATION')
                result=json.loads(proc.stdout)
                log=result.pop('private_execution_log_b64',None)
                if log is not None:(out/'migration.private.log').write_bytes(base64.b64decode(log,validate=True))
                result.update(cluster_id=flow.cluster,pod_uid=target['pod_uid'],credential_values_printed=False)
                P.write(out/'receipt.json',result)
                need(result.get('status')=='PASS_FRESH_SCHEMA_AND_PURPOSE_ACCOUNTS_PREPARED','DB_INITIALIZATION_NOT_ACCEPTED_SEE_RECEIPT')

            phase='CONNECT_ACTUAL_DATA_EVIDENCE_AND_STAGE_APP'
            handoff=P.read(config['final_data_handoff'])
            if not (out/'handoff-before.json').exists():P.write(out/'handoff-before.json',handoff)
            handoff.update(purpose_accounts_evidence_ref=str(out/'receipt.json')+'#accounts',
                schema_prepared_evidence_ref=str(out/'receipt.json')+'#schema',reported_schema_revision=result['reported_schema_revision'])
            temp=Path(config['final_data_handoff']+'.setup-new')
            P.write(temp,handoff);os.replace(temp,config['final_data_handoff'])
        # The existing stage entry owns its lock and measures only stage_app, excluding SQL preparation.
        repo=Path(d['source']).parents[2]
        ledger=out/'single-clock.jsonl'
        staged_process=subprocess.run([sys.executable,'-I',str(repo/'tools/presentation-measurement/stage_clock.py'),
            'entry','--infra',str(repo),'--work',str(work),'--ledger',str(ledger),'--stage','single',
            '--','--config',d['config'],'--execute','--resume-read-only'],capture_output=True,timeout=600)
        (out/'stage-clock.private.log').write_bytes(staged_process.stdout+staged_process.stderr)
        print(staged_process.stdout.decode(errors='replace'),end='',flush=True)
        need(staged_process.returncode==0,'APP_STAGE_FAILED_DATA_SETUP_RECEIPT_PRESERVED')
        rows=[]
        for line in staged_process.stdout.decode().splitlines():
            try:rows.append(json.loads(line))
            except ValueError:pass
        successes=[x for x in rows if isinstance(x,dict) and x.get('status')=='PASS_PRESTART_TO_MANUAL_SYNC_BOUNDARY']
        need(len(successes)==1,'EXACT_APP_STAGE_BOUNDARY_RECEIPT_REQUIRED')
        staged=successes[0]
        if not (out/'app-stage-receipt.json').exists():P.write(out/'app-stage-receipt.json',staged)
        else:need(P.read(out/'app-stage-receipt.json')==staged,'EXISTING_APP_STAGE_RECEIPT_CHANGED')
        observer=out/'stage-observer-started.json'
        if not observer.exists():
            env={k:v for k,v in os.environ.items() if not k.startswith(('AWS_','RHCS_'))}
            P.write(observer,{'stage':'single','ledger':str(ledger),'seconds':600,'interval_seconds':2})
            with (out/'stage-observer.private.log').open('wb') as log:
                watcher=subprocess.Popen([sys.executable,'-I',str(repo/'tools/presentation-measurement/stage_clock.py'),
                    'observe','--infra',str(repo),'--work',str(work),'--ledger',str(ledger),'--stage','single',
                    '--kubeconfig',config['personal_kubeconfig'],'--oc',config['oc'],'--seconds','600','--interval','2'],
                    stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,env=env,start_new_session=True)
            P.write(out/'stage-observer-pid.json',{'pid':watcher.pid,'bounded_seconds':600})
        console=Path(staged['console_file']).read_text()
        need(console.count('const SYNC_NOW = false;')==1,'EXACT_REVIEWED_EXPLICIT_SYNC_SWITCH_REQUIRED')
        enabled=console.replace('const SYNC_NOW = false;','const SYNC_NOW = true;',1)
        console_file=out/'single-sync.console.js'
        if not console_file.exists():
            fd=os.open(console_file,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            with os.fdopen(fd,'w',encoding='utf-8') as f:f.write(enabled)
        else:need(console_file.read_text()==enabled,'EXISTING_SYNC_CODE_CHANGED')
        baseline=json.loads(console.split('const b = ',1)[1].split(';\n',1)[0])
        result={'status':'PASS_FRESH_SETUP_AND_APP_SOURCE_READY_FOR_EXPLICIT_SYNC','setup_receipt':str(out/'receipt.json'),
            'app_stage':staged,'stage_clock':str(ledger),'infrastructure_handoff':str(out/'infrastructure-handoff.json'),
            'argo_origin':baseline['origin'],'sync_console_file':str(console_file),'sync_console_enabled':True,
            'ready_observer_seconds':600,
            'current_operator_mapping':str(out/'current-operator-mapping.json'),
            'operator_mapping':[{'role':x['role'],'policy_name':x['policy_arn'].rsplit('/',1)[-1],
                'current_expected_role_attached':x.get('current_expected_role_attached')} for x in infra['operator_attachments']],
            'sync_started':False,'application_started':False}
    except Exception as error:
        message=str(error)
        result={'status':'STOP_FRESH_DB_PREPARE','phase':phase,
            'reason':message if re.fullmatch(r'[A-Za-z0-9_.:-]{1,200}',message) else type(error).__name__,
            'automatic_retry':False,'private_directory':str(out) if out else None}
        if out is not None:
            result['database_mutation_attempted']=(out/'remote-attempted.json').exists()
            if (out/'receipt.json').is_file():result['database_initialization']=json.loads((out/'receipt.json').read_text())
        if infra is not None:
            result['worker_handoff']={k:infra[k] for k in ('worker_security_group_id','group_name','name_tag','matches_A_reported_name')}
            result['operator_mapping']=[{'role':x['role'],'policy_name':x['policy_arn'].rsplit('/',1)[-1],
                'current_expected_role_attached':x.get('current_expected_role_attached')} for x in infra['operator_attachments']]
    print(json.dumps(result,indent=2),flush=True)
    if out is not None:
        with (out/'dispatch-result.json').open('w',encoding='utf-8') as f:json.dump(result,f,indent=2)
        print('Private fresh setup directory: '+str(out),flush=True)
        if result['status'].startswith('PASS_'):
            print('ARGO_CONSOLE_CODE_BEGIN',flush=True)
            print(Path(result['sync_console_file']).read_text(),flush=True)
            print('ARGO_CONSOLE_CODE_END',flush=True)
    return 0 if result['status'].startswith('PASS_') else 2


if __name__=='__main__':raise SystemExit(main())
