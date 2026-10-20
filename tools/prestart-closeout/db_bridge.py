"""Use the existing DB host without copying its credentials. Default: metadata only."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys

REMOTE = r'''
import hashlib,json,os,re,shutil,socket,stat,subprocess,sys,tempfile
from pathlib import Path
p=json.load(sys.stdin);os.umask(0o077)
def need(ok,code):
 if not ok:raise RuntimeError(code)
def meta(path,private=False):
 q=Path(path)
 if q.is_symlink() or not q.is_file():return False
 s=q.stat()
 return bool(stat.S_ISREG(s.st_mode) and os.access(q,os.R_OK) and
  (not private or s.st_uid==os.getuid() and stat.S_IMODE(s.st_mode)&0o077==0))
try:
 d=p['paths'];binary=shutil.which('mariadb')
 result={'status':'COLLECTED_DB_HOST_METADATA_NO_SQL','host':socket.gethostname(),
  'mariadb':binary,'credential_file':d['credential_file'],'ca_file':d['ca_file'],
  'credential_private_owned_readable':meta(d['credential_file'],True),
  'ca_readable_regular':meta(d['ca_file']),'sql_calls':0,'credential_values_exported':False}
 if p['action']=='metadata':print(json.dumps(result));sys.exit(0)
 need(binary and result['credential_private_owned_readable'] and result['ca_readable_regular'],
  'EXISTING_DB_EXECUTION_PATHS_REQUIRED')
 with tempfile.TemporaryDirectory(prefix='b-db-observe-') as t:
  root=Path(t);config=p['config'];config['mariadb']=binary
  config['credential_file']=d['credential_file'];config['ca_file']=d['ca_file']
  for name,row in p['files'].items():
   need(re.fullmatch(r'[a-z_]+\.py',name),'EXACT_OBSERVER_FILE_REQUIRED')
   raw=__import__('base64').b64decode(row['base64'],validate=True)
   need(hashlib.sha256(raw).hexdigest()==row['sha256'],'OBSERVER_SOURCE_HASH_MISMATCH')
   (root/name).write_bytes(raw)
  if p['action']=='probe':
   config={'binding':p['config']['binding'],'db_local_paths':dict(d,mariadb=binary,
     database_host=p['config']['database_host'])}
   script='db_probe.py';args=[]
  else:script='db_observation.py';args=['sample','--out',str(root/'samples.jsonl')]
  (root/'config.json').write_text(json.dumps(config))
  child=subprocess.run([sys.executable,'-I',str(root/script),*args,'--config',
   str(root/'config.json'),'--execute'],capture_output=True,text=True,timeout=p['timeout'])
  need(child.returncode==0,'DB_OBSERVER_STOPPED_RAW_LOG_NOT_EXPORTED')
  value=json.loads(child.stdout)
  if p['action']=='probe':print(json.dumps(value))
  else:
   raw=(root/'samples.jsonl').read_bytes();need(len(raw)<=2097152,'BOUNDED_SAMPLE_OUTPUT_REQUIRED')
   print(json.dumps({'status':'COLLECTED_REMOTE_DB_SAMPLES','summary':value,
     'samples_base64':__import__('base64').b64encode(raw).decode(),
     'collector_host':socket.gethostname(),'credential_values_exported':False}))
except Exception as e:
 print(json.dumps({'status':'STOP_DB_BRIDGE_REMOTE','reason':str(e) if type(e) is RuntimeError else type(e).__name__,
  'raw_logs_exported':False}));sys.exit(2)
'''

def need(ok,code):
    if not ok:raise RuntimeError(code)

def read(path):
    p=Path(path);need(p.is_file() and not p.is_symlink() and p.stat().st_size<4194304,'BOUNDED_INPUT_REQUIRED')
    return json.loads(p.read_text(encoding='utf-8'))

def packet(settings,action,config=None):
    need(action in ('metadata','probe','sample'),'KNOWN_DB_BRIDGE_ACTION_REQUIRED')
    files={}
    if action!='metadata':
        source=Path(settings['hpa_source'] if action=='probe' else settings['measurement_source'])
        names=['db_probe.py','common.py'] if action=='probe' else ['db_observation.py','stage_clock.py','timeline.py']
        for name in names:
            path=source/name;raw=path.read_bytes().replace(b'\r\n',b'\n')
            digest=hashlib.sha256(raw).hexdigest()
            need(digest==settings['source_sha256'][name],'ADOPTED_DB_OBSERVER_SOURCE_REQUIRED')
            files[name]={'sha256':digest,'base64':base64.b64encode(raw).decode()}
        need(config and config.get('binding'),'CURRENT_BOUND_DB_CONFIG_REQUIRED')
        need(config.get('database_host')==settings.get('database_host'),'SAME_EXPECTED_DATABASE_REQUIRED')
        if action=='sample':
            need(type(config.get('duration_seconds')) is int and 1<=config['duration_seconds']<=900,
                 'BOUNDED_DB_OBSERVATION_REQUIRED')
    return {'action':action,'config':config,'paths':settings['paths'],'files':files,
            'timeout':20 if action=='probe' else 930}

def invoke(settings,p,runner=subprocess.run):
    need(isinstance(settings['ssh_prefix'],list) and settings['ssh_prefix'],'EXISTING_SSH_PREFIX_REQUIRED')
    need(settings['peer'].startswith('root@') and re.fullmatch(r'root@[a-zA-Z0-9.:-]+',settings['peer']),
         'EXACT_EXISTING_DB_PEER_REQUIRED')
    command=shlex.join([settings['remote_python'],'-I','-c',REMOTE])
    args=settings['ssh_prefix']+['-o','BatchMode=yes','-o','StrictHostKeyChecking=yes',
        '-o','ConnectTimeout=5','-o','ConnectionAttempts=1',settings['peer'],command]
    r=runner(args,input=json.dumps(p).encode(),capture_output=True,timeout=15 if p['action']=='metadata' else p['timeout']+15)
    need(r.returncode==0,'EXISTING_DB_SSH_OR_REMOTE_METADATA_UNAVAILABLE_NO_RETRY')
    need(len(r.stdout)<=3145728,'BOUNDED_REMOTE_RESULT_REQUIRED')
    result=json.loads(r.stdout);need(not result['status'].startswith('STOP_'),'REMOTE_DB_OBSERVER_STOPPED')
    return result

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=['metadata','probe','sample'])
    p.add_argument('--settings',required=True,type=Path);p.add_argument('--config',type=Path)
    p.add_argument('--out',type=Path);p.add_argument('--execute',action='store_true');a=p.parse_args()
    os.umask(0o077)
    try:
        c=read(a.settings)
        need(a.action=='metadata' or a.execute,'EXPLICIT_SQL_OBSERVATION_EXECUTION_REQUIRED')
        config=read(a.config) if a.config else None
        if a.action=='probe' and 'db_local_paths' in config:
            config={'binding':config['binding'],'database_host':config['db_local_paths']['database_host']}
        if a.action=='sample':need(a.out is not None and not a.out.exists(),'NEW_DB_SAMPLE_OUTPUT_REQUIRED')
        result=invoke(c,packet(c,a.action,config))
        if a.action=='sample':
            data=base64.b64decode(result.pop('samples_base64'),validate=True)
            fd=os.open(a.out,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
            with os.fdopen(fd,'wb') as f:f.write(data)
            result['out']=str(a.out)
        print(json.dumps(result));return 0
    except Exception as e:
        print(json.dumps({'status':'STOP_DB_BRIDGE','reason':str(e) if type(e) is RuntimeError else type(e).__name__,
            'credential_values_exported':False,'automatic_retry':False}));return 2

if __name__=='__main__':raise SystemExit(main())
