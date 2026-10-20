"""Extract deployment inputs once from accepted encrypted sources. Not a backup test."""
import base64
import hashlib
import json
import os
from pathlib import Path
import stat
import shutil
import subprocess
from urllib.parse import urlsplit,unquote,quote

EXPECTED={('Secret','backend-db-runtime'):{'SEOKPAN_IDENTITY_DATABASE_URL','SEOKPAN_GAME_DATABASE_URL'},
    ('Secret','backend-redis-runtime'):{'SEOKPAN_REDIS_AUTH_TOKEN'},
    ('ConfigMap','backend-database-ca'):{'ca.crt'},('ConfigMap','backend-redis-ca'):{'ca.crt'}}

def need(ok,code):
    if not ok:raise RuntimeError(code)

def private(path,limit=33554432):
    p=Path(path);need(p.is_file() and not p.is_symlink() and p.stat().st_size<=limit,'BOUNDED_PRIVATE_SOURCE_REQUIRED')
    if os.name=='posix':need(p.stat().st_uid==os.getuid() and stat.S_IMODE(p.stat().st_mode)&0o077==0,'PRIVATE_OWNED_SOURCE_REQUIRED')
    return p.read_bytes()

def h(raw):return hashlib.sha256(raw).hexdigest()

def decrypt(sops,path,key,input_type):
    env=os.environ.copy();env.pop('SOPS_AGE_KEY',None);env['SOPS_AGE_KEY_FILE']=str(key)
    p=subprocess.run([sops,'--decrypt','--input-type',input_type,'--output-type','json','/dev/stdin'],
        input=private(path),capture_output=True,timeout=120,env=env)
    need(p.returncode==0,'EXISTING_PROTECTED_SOURCE_DECRYPT_FAILED_NO_RAW_OUTPUT')
    return json.loads(p.stdout)

def runtime_objects(bundle,database_host):
    rows={}
    for o in bundle.get('snapshots',[]):
        key=(o.get('kind'),o.get('metadata',{}).get('name'))
        if key not in EXPECTED or o['metadata'].get('namespace')!='seokpan-cloud':continue
        need(key not in rows and set(o.get('data',{}))==EXPECTED[key],'EXACT_PRESERVED_RUNTIME_KEYS_REQUIRED')
        row={'apiVersion':'v1','kind':o['kind'],'metadata':{'namespace':'seokpan-cloud','name':key[1]},'data':o['data']}
        if o['kind']=='Secret':need(o.get('type')=='Opaque','OPAQUE_RUNTIME_INPUT_REQUIRED');row['type']='Opaque'
        rows[key]=row
    need(set(rows)==set(EXPECTED),'FOUR_PRESERVED_RUNTIME_OBJECTS_REQUIRED')
    for key,user in [('SEOKPAN_IDENTITY_DATABASE_URL','identity_svc'),('SEOKPAN_GAME_DATABASE_URL','game_svc')]:
        u=urlsplit(base64.b64decode(rows[('Secret','backend-db-runtime')]['data'][key],validate=True).decode())
        need(u.hostname==database_host and u.scheme=='mysql+asyncmy' and u.port==3306 and unquote(u.username or '')==user
             and unquote(u.path)=='/stone_game' and u.password,'EXACT_PRESERVED_RDS_TARGET_REQUIRED')
    return {'items':[rows[k] for k in sorted(rows)]}

def materialize(c,out,database_host):
    out=Path(out);out.mkdir(mode=0o700,exist_ok=True)
    receipt=out/'protected-materialization.json'
    archive=c['archive'];raw=private(archive)
    need(h(raw)==c['archive_sha256'],'ACCEPTED_CONFIG_ARCHIVE_HASH_REQUIRED')
    account=Path(c['account_cipher']);cipher_hash=h(private(account)) if account.is_file() else None
    source={'archive_sha256':c['archive_sha256'],'accounts_cipher_sha256':cipher_hash,'database_host':database_host}
    if receipt.exists():
        old=json.loads(private(receipt));need(old['source']==source,'PROTECTED_SOURCE_CHANGED_REVIEW_BEFORE_REPLACE')
        need(all(h(private(out/name))==digest for name,digest in old['output_sha256'].items()),'MATERIALIZED_INPUT_CHANGED')
        return dict(old,reused_without_decrypt=True,sops_decrypt_invocations=0)
    private(c['key_file'],65536)
    candidates=c['sops_candidates']+([shutil.which('sops')] if shutil.which('sops') else [])
    binary=next((str(Path(p)) for p in candidates if Path(p).is_file() and os.access(p,os.X_OK)
        and h(Path(p).read_bytes())==c['sops_sha256']),None)
    need(binary is not None,'EXISTING_SOPS_EXECUTABLE_REQUIRED')
    need(h(Path(binary).read_bytes())==c['sops_sha256'],'EXISTING_ACCEPTED_SOPS_HASH_REQUIRED')
    targets={}
    # This is necessary input extraction, not repetition of the accepted decrypt test.
    bundle=decrypt(binary,archive,c['key_file'],'json')
    targets['runtime-inputs.private.json']=runtime_objects(bundle,database_host)
    if cipher_hash:
        accounts=decrypt(binary,account,c['key_file'],'yaml')
        need(isinstance(accounts.get('db_admin'),str) and accounts['db_admin'],'EXISTING_DB_ADMIN_PROTECTED_FIELD_REQUIRED')
        secret=next(o for o in targets['runtime-inputs.private.json']['items'] if o['metadata']['name']=='backend-db-runtime')
        for field,user in [('SEOKPAN_IDENTITY_DATABASE_URL','identity_svc'),('SEOKPAN_GAME_DATABASE_URL','game_svc')]:
            need(isinstance(accounts.get(user),str) and accounts[user],'EXISTING_PURPOSE_ACCOUNT_PROTECTED_FIELDS_REQUIRED')
            value='mysql+asyncmy://'+user+':'+quote(accounts[user],safe='')+'@'+database_host+':3306/stone_game'
            secret['data'][field]=base64.b64encode(value.encode()).decode()
        targets['schema-credentials.private.json']={'schema':1,'database_url':'mysql+asyncmy://db_admin:'+quote(accounts['db_admin'],safe='')+'@'+database_host+':3306/stone_game'}
    else:raise RuntimeError('EXISTING_DATA_SQL_ACCOUNTS_CIPHER_REQUIRED')
    # Validate all sources first; never overwrite a partial or unfamiliar secret file.
    need(not any((out/name).exists() for name in targets),'PARTIAL_MATERIALIZATION_REQUIRES_READ_ONLY_RECONCILIATION')
    digests={}
    for name,value in targets.items():
        data=(json.dumps(value,sort_keys=True,separators=(',',':'))+'\n').encode()
        fd=os.open(out/name,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
        with os.fdopen(fd,'wb') as f:f.write(data)
        digests[name]=h(data)
    value={'status':'PASS_PROTECTED_INPUTS_EXTRACTED_NOT_RUNTIME_ACCEPTANCE','source':source,'output_sha256':digests,
        'runtime_inputs':str(out/'runtime-inputs.private.json'),'schema_credentials':str(out/'schema-credentials.private.json'),
        'secret_revision':'data-cipher-sha256:'+cipher_hash,'sops_decrypt_invocations':2,'reused_without_decrypt':False,
        'purpose_password_source':'CURRENT_EXISTING_PROTECTED_CIPHER_NOT_OLD_SNAPSHOT_PASSWORD',
        'secret_values_printed':False,'cluster_mutations':0,'sql_calls':0}
    fd=os.open(receipt,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    with os.fdopen(fd,'w') as f:json.dump(value,f)
    return value
