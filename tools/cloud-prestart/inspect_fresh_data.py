"""Inspect actual fresh DB/account/Schema/TLS/Redis state through an accepted Data Pod."""
import argparse
import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time

REMOTE = r'''
import asyncio,hashlib,json,re,ssl,sys
from urllib.parse import urlsplit,unquote
TABLES=('member','game','game_result','game_participant','member_stats','move','rating_history')
calls=0
def need(ok,code):
    if not ok:raise RuntimeError(code)
def failure(error):
    code=next((x for x in error.args if type(x) is int),None)
    return {'status':'NOT_ACCEPTED','error_code':code,
        'reason':str(error) if type(error) is RuntimeError else type(error).__name__}
async def query(cur,sql,args=None):
    global calls
    calls+=1
    await cur.execute(sql,args)
    return await cur.fetchall()
async def inspect(p,connect,redis_factory):
    contexts={}
    for name,path in (('database','/run/rds-ca/ca.crt'),('redis','/run/redis-ca/ca.crt')):
        with open(path,'rb') as f:need(hashlib.sha256(f.read()).hexdigest()==p[name+'_ca_sha256'],'BOUND_CA_HASH_REQUIRED')
        context=ssl.create_default_context(cafile=path)
        context.minimum_version=ssl.TLSVersion.TLSv1_2
        contexts[name]=context
    out={'status':'COLLECTED_CURRENT_DATA_SETUP_STATE','database_writes':0,'schema_changes':0,
        'credential_values_printed':False,'automatic_retry':False,'accounts':[]}
    async def open_db(raw,expected,database):
        d=urlsplit(raw)
        need(d.scheme=='mysql+asyncmy' and d.hostname==p['database_host'] and d.port==3306
             and unquote(d.username or '')==expected and unquote(d.path.lstrip('/'))=='stone_game'
             and d.password and not d.query and not d.fragment,'EXACT_PROTECTED_DATABASE_TARGET_REQUIRED')
        return await connect(host=d.hostname,port=3306,user=expected,password=unquote(d.password),
            db=database,ssl=contexts['database'],connect_timeout=8,autocommit=True)
    connection=None
    try:
        connection=await open_db(p['schema_url'],'db_admin',None)
        async with connection.cursor() as cur:
            await query(cur,'SET SESSION TRANSACTION READ ONLY')
            rows=await query(cur,"SHOW SESSION STATUS LIKE 'Ssl_cipher'")
            need(rows and rows[0][1],'ADMIN_TLS_REQUIRED')
            caps=(await query(cur,'SELECT @@GLOBAL.max_connections, @@GLOBAL.max_user_connections'))[0]
            exists=bool(await query(cur,'SELECT SCHEMA_NAME FROM information_schema.SCHEMATA WHERE SCHEMA_NAME=%s',('stone_game',)))
            names=await query(cur,'SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA=%s',('stone_game',)) if exists else []
            names=sorted(x[0].decode() if isinstance(x[0],bytes) else x[0] for x in names)
            revision=[]
            if 'alembic_version' in names:
                revision=[x[0].decode() if isinstance(x[0],bytes) else x[0] for x in await query(cur,'SELECT version_num FROM stone_game.alembic_version')]
                need(all(isinstance(x,str) and re.fullmatch(r'[a-zA-Z0-9_]{1,64}',x) for x in revision),'SAFE_SCHEMA_REVISION_REQUIRED')
            populated=[]
            for table in TABLES:
                if table in names and await query(cur,'SELECT 1 FROM stone_game.'+table+' LIMIT 1'):
                    populated.append(table)
            grants=await query(cur,'SHOW GRANTS FOR CURRENT_USER()')
            text='\n'.join(x[0].decode() if isinstance(x[0],bytes) else x[0] for x in grants).upper()
            out['schema']={'status':'LOGIN_AND_TLS_OK','database_exists':exists,'tables':names,
                'revisions':revision,'current_revision_matches':revision==['20260902_0002'],
                'populated_application_tables':populated,
                'all_expected_tables_present':set(TABLES)<=set(names),
                'global_max_connections':int(caps[0]),'global_max_user_connections':int(caps[1]),
                'admin_ddl_grant_hint':('CREATE' in text or 'ALL PRIVILEGES' in text),
                'purpose_account_create_grant_hint':('CREATE USER' in text or 'ALL PRIVILEGES ON *.*' in text)}
    except Exception as error:
        out['schema']=failure(error)
    finally:
        if connection is not None:connection.close()
    for name in ('identity_svc','game_svc'):
        connection=None
        try:
            connection=await open_db(p['purpose_urls'][name],name,'stone_game')
            async with connection.cursor() as cur:
                await query(cur,'SET SESSION TRANSACTION READ ONLY')
                rows=await query(cur,"SHOW SESSION STATUS LIKE 'Ssl_cipher'")
                need(rows and rows[0][1],'PURPOSE_TLS_REQUIRED')
                current=(await query(cur,'SELECT CURRENT_USER()'))[0][0]
                current=current.decode() if isinstance(current,bytes) else current
                need(current.split('@',1)[0]==name,'PURPOSE_ACCOUNT_MATCH_REQUIRED')
                grants=await query(cur,'SHOW GRANTS FOR CURRENT_USER()')
                text='\n'.join(x[0].decode() if isinstance(x[0],bytes) else x[0] for x in grants)
                limits=[int(x) for x in re.findall(r'MAX_USER_CONNECTIONS\s+(\d+)',text,re.I)]
                out['accounts'].append({'account':name,'status':'LOGIN_AND_TLS_OK',
                    'account_max_user_connections':min(limits) if limits else 0,
                    'ssl_required_in_grants':bool(re.search(r'REQUIRE\s+SSL',text,re.I)),
                    'full_grant_contract_accepted':False})
        except Exception as error:
            out['accounts'].append(dict(failure(error),account=name))
        finally:
            if connection is not None:connection.close()
    client=None
    try:
        client=redis_factory(host=p['redis_host'],port=6379,password=p['redis_auth'],ssl=True,
            ssl_ca_certs='/run/redis-ca/ca.crt',ssl_cert_reqs='required',ssl_check_hostname=True,
            socket_connect_timeout=5,socket_timeout=5,decode_responses=True)
        need(await client.ping() is True,'REDIS_PING_REQUIRED')
        out['redis']={'status':'TLS_AUTH_PING_OK','writes':0}
    except Exception as error:
        out['redis']=failure(error)
    finally:
        if client is not None:await client.aclose()
    out['sql_calls']=calls
    return out
async def main():
    import asyncmy
    from redis.asyncio import Redis
    return await asyncio.wait_for(inspect(json.load(sys.stdin),asyncmy.connect,Redis),75)
try:
    result=asyncio.run(main())
except Exception as error:
    result=dict(failure(error),status='STOP_CURRENT_DATA_SETUP_INSPECTION',database_writes=0,schema_changes=0,sql_calls=calls)
print(json.dumps(result))
'''


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dispatch',required=True,type=Path)
    parser.add_argument('--execute',action='store_true')
    a=parser.parse_args()
    os.umask(0o077)
    out=None
    result={'status':'STOP_CURRENT_DATA_SETUP_INSPECTION','database_writes':0,'schema_changes':0,
            'sync_started':False,'automatic_retry':False,'credential_values_printed':False}
    try:
        if not a.execute:raise RuntimeError('EXPLICIT_READ_ONLY_EXECUTION_REQUIRED')
        import pwd,fcntl
        if pwd.getpwuid(os.getuid()).pw_name!='jth':raise RuntimeError('USE_CONTROLLER_JTH')
        if a.dispatch.is_symlink() or not a.dispatch.is_file():raise RuntimeError('EXISTING_DISPATCH_REQUIRED')
        d=json.loads(a.dispatch.read_text())
        spec=importlib.util.spec_from_file_location('prestart_inspection',d['source'])
        P=importlib.util.module_from_spec(spec);spec.loader.exec_module(P)
        d=P.read(a.dispatch);config=P.read(d['config']);work=Path(d['work'])
        out=Path(tempfile.mkdtemp(prefix='data-setup-read-',dir=work.parent))
        with open(str(work)+'.lock','a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            flow=P.Flow(config,work,prepare_data_pod=True)
            accepted=P.read(work/'new-pod.json')
            if accepted.get('binding')!=flow.binding:raise RuntimeError('ACCEPTED_POD_BINDING_REQUIRED')
            target=accepted['result']['target']
            c=P.read(work/'new-inputs.json')['result']['inputs']
            flow.verify_pod(target,c)
            rows=P.read(config['runtime_inputs_file'])['items']
            objects={(o['kind'],o['metadata']['name']):o for o in rows}
            data=objects[('Secret','backend-db-runtime')]['data']
            payload={'schema_url':flow.schema['database_url'],'database_host':c['database_host'],
                'redis_host':c['redis_host'],
                'purpose_urls':{name:base64.b64decode(data[field],validate=True).decode() for name,field in
                    (('identity_svc','SEOKPAN_IDENTITY_DATABASE_URL'),('game_svc','SEOKPAN_GAME_DATABASE_URL'))},
                'redis_auth':base64.b64decode(objects[('Secret','backend-redis-runtime')]['data']['SEOKPAN_REDIS_AUTH_TOKEN'],validate=True).decode()}
            for name in ('database','redis'):
                payload[name+'_ca_sha256']=hashlib.sha256(objects[('ConfigMap','backend-'+name+'-ca')]['data']['ca.crt'].encode()).hexdigest()
            start=time.monotonic()
            result=json.loads(flow.call('exec','-i','-n','seokpan-cloud',target['pod_name'],'-c','db-admin',
                '--','python','-c',REMOTE,payload=P.canon(payload)))
            result.update(cluster_id=flow.cluster,pod_uid=target['pod_uid'],elapsed_seconds=round(time.monotonic()-start,3),
                app_started=False,sync_started=False,existing_work=str(work))
    except Exception as error:
        message=str(error)
        result['reason']=message if re.fullmatch(r'[A-Za-z0-9_.:-]{1,200}',message) else type(error).__name__
    if out is not None:
        (out/'receipt.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result,indent=2))
    if out is not None:print('Private data setup directory: '+str(out))
    return 0 if result['status'].startswith('COLLECTED_') else 2


if __name__=='__main__':
    raise SystemExit(main())
