"""Read-only DB observation through the approved running Backend. No new Pod/client."""
import argparse
import copy
import datetime as dt
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import ssl
import subprocess
import sys
import time
from urllib.parse import urlsplit,unquote

PROGRAM=r'''
import asyncio,hashlib,json,ssl,sys
import asyncmy
from urllib.parse import urlsplit,unquote
async def main():
 p=json.load(sys.stdin);u=urlsplit(p['database_url'])
 with open(p['ca_file'],'rb') as f:
  if hashlib.sha256(f.read()).hexdigest()!=p['ca_sha256']:raise RuntimeError('CURRENT_DATABASE_CA_CONTENT_MISMATCH')
 ctx=ssl.create_default_context(cafile=p['ca_file'])
 ctx.check_hostname=True;ctx.minimum_version=ssl.TLSVersion.TLSv1_2
 conn=await asyncmy.connect(host=u.hostname,port=u.port,user=unquote(u.username),
  password=unquote(u.password),db=unquote(u.path.lstrip('/')),ssl=ctx,
  connect_timeout=5,autocommit=True)
 try:
  async with conn.cursor() as cur:
   await cur.execute("SHOW SESSION STATUS LIKE 'Ssl_cipher'");tls=await cur.fetchone()
   await cur.execute('SELECT @@GLOBAL.max_connections,@@GLOBAL.max_user_connections');limits=await cur.fetchone()
   await cur.execute("SHOW GLOBAL STATUS LIKE 'Threads_connected'");total=await cur.fetchone()
   await cur.execute('SHOW GRANTS FOR CURRENT_USER');grants=await cur.fetchall()
   visible=any(__import__('re').search(r'\b(ALL PRIVILEGES|PROCESS)\b.*\bON\s+\*\.\*',
    (x[0].decode() if isinstance(x[0],bytes) else x[0]).upper()) for x in grants)
   if not tls or not tls[1]:raise RuntimeError('TLS_REQUIRED')
   if not visible and p.get('observation_scope')!='TOTAL_ONLY':raise RuntimeError('GLOBAL_PROCESS_VISIBILITY_REQUIRED')
   counts={}
   if visible:
    await cur.execute('SELECT USER,COUNT(*) FROM INFORMATION_SCHEMA.PROCESSLIST GROUP BY USER');counts=dict(await cur.fetchall())
   print(json.dumps({'status':'PASS_CURRENT_DB_SAMPLE' if visible else 'PASS_CURRENT_DB_TOTAL_SAMPLE','binding':p['binding'],
    'utc':__import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat(),
    'tls':True,'global_limit':int(limits[0]),'global_max_user_connections':int(limits[1]),
    'connections_total':int(total[1]),'observer_connection_included':True,
    'per_account':{n:int(counts.get(n,0)) for n in ('identity_svc','game_svc')} if visible else None,
    'process_grant_observed':visible,'account_visibility_accepted':visible,
    'counts_not_atomic':True}))
 finally:conn.close()
try:asyncio.run(main())
except Exception as e:
 reason=str(e) if type(e) is RuntimeError else type(e).__name__
 if not __import__('re').fullmatch(r'[A-Za-z0-9_]{1,100}',reason):reason='UNCLASSIFIED_PRIVATE_ERROR'
 print(json.dumps({'status':'STOP_POD_DB_SAMPLE','reason':reason,
  'error_code':next((x for x in e.args if type(x) is int),None)}));sys.exit(2)
'''

def need(ok,code):
    if not ok:raise RuntimeError(code)

def read(path,private=False):
    p=Path(path);need(p.is_file() and not p.is_symlink() and p.stat().st_size<4194304,'BOUNDED_REGULAR_INPUT_REQUIRED')
    if private and os.name=='posix':need(p.stat().st_uid==os.getuid() and p.stat().st_mode&0o077==0,'PRIVATE_OWNED_INPUT_REQUIRED')
    return json.loads(p.read_text())

def target(c,config):
    u=urlsplit(config['database_url'])
    need(config.get('schema')==1 and u.scheme=='mysql+asyncmy' and u.hostname==c['database_host'] and u.port==3306
        and unquote(u.username or '')=='db_admin' and u.password and unquote(u.path)=='/stone_game'
        and not u.query and not u.fragment,'EXACT_EXISTING_DB_OBSERVER_CREDENTIAL_REQUIRED')


def database_ca_path(pod):
    container,=[x for x in pod['spec']['containers'] if x['name']=='backend']
    matches=[]
    for volume in pod['spec'].get('volumes',[]):
        cm=volume.get('configMap',{})
        if cm.get('name')!='backend-database-ca':continue
        items=cm.get('items',[{'key':'ca.crt','path':'ca.crt'}])
        keys=[x['path'] for x in items if x.get('key')=='ca.crt']
        need(len(keys)==1,'EXACT_DATABASE_CA_KEY_REQUIRED')
        rel=PurePosixPath(keys[0])
        need(not rel.is_absolute() and '..' not in rel.parts and str(rel) not in ('','.'),
             'REGULAR_RELATIVE_CA_ITEM_REQUIRED')
        for mount in container.get('volumeMounts',[]):
            if mount['name']!=volume['name']:continue
            base=PurePosixPath(mount['mountPath'])
            need(mount.get('readOnly') is True and base.is_absolute() and '..' not in base.parts
                 and not mount.get('subPath') and not mount.get('subPathExpr'),'READ_ONLY_FULL_DATABASE_CA_MOUNT_REQUIRED')
            matches.append(str(base/rel))
    need(len(matches)==1,'UNIQUE_CURRENT_DATABASE_CA_MOUNT_REQUIRED')
    return matches[0]

def sample(c,runner=subprocess.run):
    scope=c.get('observation_scope','FULL')
    need(scope in ('FULL','TOTAL_ONLY'),'EXPLICIT_DB_OBSERVATION_SCOPE_REQUIRED')
    b=c['binding'];schema=read(c['schema_credentials_file'],True);target(c,schema)
    need(b['cluster_id']!='2tejeb2farjaaoef1e74asdf6amrtpu3' and b.get('namespace_uid') and b.get('application_uid'),
         'NEW_BOUND_CLUSTER_REQUIRED')
    prefix=[c['oc'],'--kubeconfig='+c['personal_kubeconfig'],'--request-timeout=10s']
    def call(args,payload=None):
        p=runner(prefix+args,input=payload,capture_output=True,timeout=18)
        if p.returncode:
            try:
                remote=json.loads(p.stdout)
            except Exception:remote={}
            reason=remote.get('reason')
            if remote.get('status')=='STOP_POD_DB_SAMPLE' and isinstance(reason,str) and re.fullmatch(r'[A-Za-z0-9_]{1,100}',reason):
                raise RuntimeError('REMOTE_DATABASE_'+reason)
            raise RuntimeError('CURRENT_DB_OBSERVER_CONTEXT_OR_POD_UNAVAILABLE')
        return p.stdout
    local=json.loads(call(['config','view','--minify','-o','json']))['clusters'][0]['cluster']
    need(local.get('server')==c['api'] and not local.get('insecure-skip-tls-verify'),'SAME_TRUSTED_API_REQUIRED')
    need(call(['whoami']).strip()==b'tjung03','PERSONAL_B_CONTEXT_REQUIRED')
    ns=json.loads(call(['get','namespace','seokpan-cloud','-o','json']))
    app=json.loads(call(['get','application','seokpan-cloud-app','-n','openshift-gitops','-o','json']))
    need(ns['metadata']['uid']==b['namespace_uid'] and app['metadata']['uid']==b['application_uid'],
         'DB_OBSERVER_TARGET_UID_CHANGED')
    C=load_common(c)
    observed_source=C.digest(C.normalized(app['spec']['source']))
    allowed=recording_sources(c)
    need(observed_source in allowed,'OBSERVED_SOURCE_CHANGED')
    pods=json.loads(call(['get','pods','-n','seokpan-cloud','-l','app.kubernetes.io/name=backend','-o','json']))['items']
    eligible=[p for p in pods if not p['metadata'].get('deletionTimestamp') and p.get('status',{}).get('phase')=='Running'
        and any(x['type']=='Ready' and x['status']=='True' for x in p.get('status',{}).get('conditions',[]))
        and len(p['spec']['containers'])==1 and p['spec']['containers'][0]['image']==c['backend_image']]
    need(eligible,'APPROVED_READY_BACKEND_REQUIRED')
    pod=sorted(eligible,key=lambda x:x['metadata']['name'])[0];name=pod['metadata']['name'];uid=pod['metadata']['uid']
    ca_file=database_ca_path(pod)
    ca=json.loads(call(['get','configmap','backend-database-ca','-n','seokpan-cloud','-o','json']))
    need(c.get('database_ca_uid') and ca['metadata']['uid']==c['database_ca_uid'],
         'ACCEPTED_DATABASE_CA_UID_REQUIRED')
    ca_hash=hashlib.sha256(ca['data']['ca.crt'].encode()).hexdigest()
    result=json.loads(call(['exec','-i','-n','seokpan-cloud',name,'-c','backend','--','python','-c',PROGRAM],
        json.dumps({'binding':b,'database_url':schema['database_url'],'ca_file':ca_file,'ca_sha256':ca_hash,
                    'observation_scope':scope}).encode()))
    after=json.loads(call(['get','pod',name,'-n','seokpan-cloud','-o','json']))
    need(after['metadata']['uid']==uid and not after['metadata'].get('deletionTimestamp'),'OBSERVER_POD_REPLACED_SAMPLE_UNKNOWN')
    accepted=('PASS_CURRENT_DB_SAMPLE','PASS_CURRENT_DB_TOTAL_SAMPLE') if scope=='TOTAL_ONLY' else ('PASS_CURRENT_DB_SAMPLE',)
    need(result['status'] in accepted and result['binding']==b,'CURRENT_DB_SAMPLE_REQUIRED')
    result.update(collector_pod_uid=uid,collector_pod=name,transport='EXISTING_BACKEND_EXEC_PRIVATE_STDIN',
                  credentials_in_argv_or_logs=False,new_pods_created=0,database_ca_file=ca_file,
                  database_ca_uid=ca['metadata']['uid'],database_ca_sha256=ca_hash,observed_normalized_source_sha256=observed_source)
    return result

def load_common(c):
    path=Path(c['hpa_source'])/'common.py'
    need(hashlib.sha256(path.read_bytes().replace(b'\r\n',b'\n')).hexdigest()==c['common_sha256'],'ADOPTED_HPA_COMMON_REQUIRED')
    spec=importlib.util.spec_from_file_location('pod_db_common',path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

def recording_sources(c):
    bound=c['binding']['normalized_source_sha256'];scope=c.get('observation_scope','FULL')
    allowed=c.get('allowed_normalized_source_sha256',[bound])
    need(isinstance(allowed,list) and 1<=len(allowed)<=2 and bound in allowed
         and all(isinstance(x,str) and re.fullmatch(r'[0-9a-f]{64}',x) for x in allowed)
         and (scope=='TOTAL_ONLY' or allowed==[bound]),'BOUNDED_RECORDING_SOURCE_ALLOWLIST_REQUIRED')
    return allowed

def summary_input(rows):
    result=copy.deepcopy(rows)
    for row in result:
        if row.get('sample') and row['sample'].get('per_account') is None:
            row['sample']['per_account']={'identity_svc':None,'game_svc':None}
    return result

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=['probe','sample'])
    p.add_argument('--config',required=True,type=Path);p.add_argument('--out',type=Path);p.add_argument('--execute',action='store_true');a=p.parse_args()
    os.umask(0o077)
    try:
        need(a.execute,'EXPLICIT_READ_ONLY_SQL_OBSERVATION_REQUIRED');c=read(a.config,True)
        if a.action=='probe':print(json.dumps(sample(c)));return 0
        need(a.out is not None and not a.out.exists(),'NEW_OBSERVATION_OUTPUT_REQUIRED')
        need(2<=c['interval_seconds']<=60 and 1<=c['duration_seconds']<=900,'BOUNDED_SAMPLE_WINDOW_REQUIRED')
        end=min(time.monotonic()+c['duration_seconds'],time.monotonic()+max(0,dt.datetime.fromisoformat(c['not_after'].replace('Z','+00:00')).timestamp()-time.time()))
        need(end>time.monotonic(),'CURRENT_OBSERVATION_WINDOW_REQUIRED');rows=[]
        while time.monotonic()<end:
            start=time.monotonic();row={'binding':c['binding'],'utc':dt.datetime.now(dt.timezone.utc).isoformat(),'monotonic_ns':time.monotonic_ns(),'sample':None}
            try:row['sample']=sample(c)
            except Exception:row['error']='READ_OR_POD_REPLACEMENT_FAILED_RAW_NOT_EXPORTED'
            rows.append(row);fd=os.open(a.out,os.O_CREAT|os.O_APPEND|os.O_WRONLY,0o600)
            with os.fdopen(fd,'w') as f:f.write(json.dumps(row)+'\n')
            time.sleep(max(0,min(end-time.monotonic(),c['interval_seconds']-(time.monotonic()-start))))
        path=Path(c['measurement_source'])/'db_observation.py'
        need(hashlib.sha256(path.read_bytes().replace(b'\r\n',b'\n')).hexdigest()==c['db_observation_sha256'],'ADOPTED_DB_SUMMARIZER_REQUIRED')
        sys.path.insert(0,str(path.parent));spec=importlib.util.spec_from_file_location('pod_db_approved_summary',path)
        m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
        result=m.observed_summary(summary_input(rows))
        result['account_visibility_accepted']=bool(rows) and all((x.get('sample') or {}).get('account_visibility_accepted') is True for x in rows)
        result.update(transport='EXISTING_BACKEND_EXEC_PRIVATE_STDIN',new_pods_created=0,
            requested_interval_seconds=c['interval_seconds'],requested_duration_seconds=c['duration_seconds'])
        print(json.dumps(result));return 0 if result.get('valid_samples',0)>0 else 2
    except Exception as e:
        print(json.dumps({'status':'STOP_POD_DB_OBSERVER','reason':str(e) if type(e) is RuntimeError else type(e).__name__,
            'credentials_exported':False,'new_pods_created':0}));return 2

if __name__=='__main__':raise SystemExit(main())
