"""Finish known snapshots and inspect restoration permissions without creating resources."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

REGION='ap-northeast-2'
def need(ok,code):
    if not ok:raise RuntimeError(code)
def save(path,value):
    path.write_text(json.dumps(value,sort_keys=True),encoding='utf-8');path.chmod(0o600)
def read(path):
    p=Path(path)
    need(p.is_file() and not p.is_symlink() and p.stat().st_size<=1048576,'PRIVATE_REGULAR_INPUT_REQUIRED')
    if os.name=='posix':need(p.stat().st_uid==os.getuid() and not p.stat().st_mode&0o077,'PRIVATE_OWNED_INPUT_REQUIRED')
    return json.loads(p.read_text())
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def pin(path,blob):
    p=Path(path);need(p.is_file() and not p.is_symlink(),'PINNED_DEPENDENCY_REQUIRED')
    raw=p.read_bytes()
    need(hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()==blob,'DEPENDENCY_HASH_CHANGED')

def status_command(a,accepted,config,driver):
    scope=read(accepted['terraform_scope_inputs'])
    command=[sys.executable,'-I',str(driver),'status',
        '--gap-archive',str(Path(a.root)/'rosa-restoration-gaps-c9exvd5i/restoration-gaps.sops.json'),
        '--gap-sha256',scope['rosa_monitoring_preservation_gap_sha256'],
        '--key-file',a.key_file,'--contract',a.contract,
        '--delete-manifest',config['delete']['manifest'],'--output-parent',a.root,
        '--resume-journal',a.resume_journal,'--wait-seconds','0','--sops',config['restoration']['sops_binary']]
    if a.age_keygen:command+=['--age-keygen',a.age_keygen]
    return command

def volume_dry_run(row,cluster):
    need(re.fullmatch(r'snap-[a-f0-9]+',row.get('snapshot_id',''))
         and row.get('snapshot_state')=='completed' and re.fullmatch(REGION+'[a-z]',row.get('source_az',''))
         and type(row.get('size_gib')) is int and row['size_gib']>0
         and re.fullmatch(r'arn:aws:kms:'+REGION+r':[0-9]{12}:key/[a-zA-Z0-9-]+',row.get('source_kms_key','')),
         'EXACT_COMPLETED_RESTORATION_INPUT_REQUIRED')
    return ['ec2','create-volume','--snapshot-id',row['snapshot_id'],'--availability-zone',row['source_az'],
        '--volume-type','gp3','--size',str(row['size_gib']),'--encrypted','--kms-key-id',row['source_kms_key'],
        '--tag-specifications',json.dumps([{'ResourceType':'volume','Tags':[
            {'Key':'seokpan-restored-cluster','Value':cluster},
            {'Key':'seokpan-source-snapshot','Value':row['snapshot_id']}]}]),'--dry-run']

def probe(args,out,number):
    # A create-volume request can reach AWS only with --dry-run.
    need(args[:2]==['kms','describe-key'] or (args[:2]==['ec2','create-volume'] and args.count('--dry-run')==1),
         'READ_OR_DRY_RUN_ONLY')
    env={k:v for k,v in os.environ.items() if k not in ('AWS_PROFILE','AWS_DEFAULT_PROFILE','AWS_ROLE_ARN','AWS_WEB_IDENTITY_TOKEN_FILE')}
    env.update(AWS_MAX_ATTEMPTS='1',AWS_PAGER='')
    p=subprocess.run(['aws','--region',REGION,'--output','json','--cli-connect-timeout','10','--cli-read-timeout','30',*args],
                     capture_output=True,env=env,timeout=45)
    log=out/('permission-'+str(number)+'.private.log');log.write_bytes(p.stdout+p.stderr);log.chmod(0o600)
    if args[0]=='ec2':
        code=re.search(rb'An error occurred \(([A-Za-z0-9.]+)\)',p.stderr)
        return {'status':'DRY_RUN_ALLOWED' if p.returncode!=0 and b'DryRunOperation' in p.stderr else 'DENIED_OR_FAILED',
                'action':'ec2:CreateVolume','error_code':code[1].decode() if code else 'UNCLASSIFIED'}
    if p.returncode:
        code=re.search(rb'An error occurred \(([A-Za-z0-9.]+)\)',p.stderr)
        return {'status':'KEY_READ_DENIED_OR_FAILED','action':'kms:DescribeKey',
                'error_code':code[1].decode() if code else 'UNCLASSIFIED'}
    key=json.loads(p.stdout).get('KeyMetadata',{})
    return {'status':'KEY_ENABLED' if key.get('KeyState')=='Enabled' else 'KEY_NOT_ENABLED',
            'manager':key.get('KeyManager'),'symmetric':key.get('KeySpec')=='SYMMETRIC_DEFAULT'}

def permission_preflight(journal,out):
    rows=journal['volumes']
    need(len(rows)==4 and len({r['snapshot_id'] for r in rows})==4,'FOUR_DISTINCT_RESTORE_SNAPSHOTS_REQUIRED')
    commands=[volume_dry_run(row,'preflight-only') for row in rows]
    volume=[];keys=[]
    for i,args in enumerate(commands):
        value=probe(args,out,i);volume.append(value)
        if value['status']!='DRY_RUN_ALLOWED':break
    for i,key in enumerate(sorted({r['source_kms_key'] for r in rows})):
        keys.append(probe(['kms','describe-key','--key-id',key],out,10+i))
    inputs={'schema':1,'account_id':journal['account_id'],'source_cluster_id':journal['cluster_id'],
        'region':REGION,'volumes':rows,'kms_keys':sorted({r['source_kms_key'] for r in rows}),
        'new_cluster_id':'NOT_CREATED','preflight_cluster_tag':'preflight-only',
        'required_actions':['ec2:CreateVolume','ec2:CreateTags','kms:CreateGrant','kms:Decrypt',
                            'kms:DescribeKey','kms:GenerateDataKeyWithoutPlaintext','kms:ReEncrypt*'],
        'permission_apply':'NOT_RUN','kms_cryptographic_operations_verified':False}
    save(out/'restoration-permission-inputs.private.json',inputs)
    return {'volume_dry_run_attempts':len(volume),'volume_dry_run_allowed':sum(v['status']=='DRY_RUN_ALLOWED' for v in volume),
        'volume_results':volume,'kms_metadata_results':keys,
        'probe_cluster_tag':'preflight-only','actual_new_cluster_permission_check':'PENDING_NEW_CLUSTER',
        'kms_cryptographic_operations_verified':False,'new_volume_permissions_accepted':False,
        'protected_scope_inputs':str(out/'restoration-permission-inputs.private.json'),
        'new_volumes_created':0,'iam_changes':0}

def execute(a,out):
    accepted=read(a.reconstruction_receipt);config=read(accepted['lifecycle_config'])
    need(accepted.get('classified_objects')==135 and accepted.get('unclassified_objects')==0
         and config.get('scope')=='ROSA_ONLY','ACCEPTED_HELD_RECIPE_REQUIRED')
    permission=read(Path(a.reconstruction_receipt).parent/'monitoring-policy-accepted.json')
    need(permission.get('status')=='PASS_EXACT_MONITORING_SNAPSHOT_POLICY_APPLIED_AND_READBACK'
         and permission.get('policy_sha256')==accepted['policy_sha256'],'APPLIED_EXACT_SNAPSHOT_POLICY_REQUIRED')
    journal=read(a.resume_journal)
    need(journal.get('cluster_id')==config['source_cluster_id'] and len(journal.get('volumes',[]))==4,
         'ORIGINAL_SNAPSHOT_JOURNAL_REQUIRED')
    driver=Path(config['create']['restore_driver']).parent/'rosa-monitoring-volume-backup.py'
    pin(driver,'d074249010e576217e04c1f97ff4b87dc7f7a381')
    pin(driver.with_name('prepare-rosa-checkpoint.py'),'f3f81f220be8e5b834759117b6a0b46c7842d9e9')
    cache=Path(a.resume_journal).parent/'snapshot-finalization-accepted.json'
    if cache.is_file():
        result=read(cache)
        need(result.get('status')=='PASS_FOUR_ENCRYPTED_EBS_SNAPSHOTS_AND_RESTORE_MAP'
             and sha(result['archive'])==result['archive_sha256']
             and config['restoration'].get('snapshot_map')==result['archive']
             and config['restoration'].get('snapshot_map_sha256')==result['archive_sha256'],'ACCEPTED_MAP_CHANGED')
        result['completed_snapshot_status_and_encryption_reused']=True
    else:
        p=subprocess.run(status_command(a,accepted,config,driver),capture_output=True,timeout=180)
        (out/'status.private.log').write_bytes(p.stdout+p.stderr)
        need(p.returncode==0,'SNAPSHOT_STATUS_FAILED_PRIVATE_LOG_NO_CAPTURE_RETRY')
        result=json.loads(p.stdout)
        if result.get('status')=='PENDING_FOUR_SNAPSHOTS_COMPLETION_READ_ONLY_RESUME':return result
        need(result.get('status')=='PASS_FOUR_ENCRYPTED_EBS_SNAPSHOTS_AND_RESTORE_MAP'
             and result.get('snapshots_completed')==4,'FOUR_COMPLETED_SNAPSHOTS_REQUIRED')
        handoff=read(Path(a.root)/'rosa-preservation-handoff-latest.json')
        need(any(r.get('kind')=='monitoring-snapshots' and r.get('path')==result['archive']
             and r.get('sha256')==result['archive_sha256'] for r in handoff['archives']),
             'NEW_MAP_HANDOFF_REQUIRED')
        old=config['restoration'].get('snapshot_map')
        need(old in (None,result['archive']),'OTHER_SNAPSHOT_MAP_NO_OVERWRITE')
        config['restoration'].update(snapshot_map=result['archive'],snapshot_map_sha256=result['archive_sha256'])
        target=Path(accepted['lifecycle_config']);temporary=out/'lifecycle.new.json';save(temporary,config);os.replace(temporary,target)
        result['snapshot_map_connected_to_lifecycle_config']=True
        save(cache,result)
    permission_cache=Path(a.resume_journal).parent/'restoration-permission-preflight.json'
    if permission_cache.is_file():
        result['restore_permission_preflight']=read(permission_cache)
        result['permission_preflight_reused']=True
    else:
        current=read(Path(result['private_result_directory'])/'snapshot-journal.json')
        try:result['restore_permission_preflight']=permission_preflight(current,out)
        except Exception as error:
            result['restore_permission_preflight']={'status':'PENDING_PERMISSION_PREFLIGHT',
                'reason':str(error) if type(error) is RuntimeError else type(error).__name__,
                'new_volume_permissions_accepted':False,'new_volumes_created':0,'iam_changes':0}
        save(permission_cache,result['restore_permission_preflight'])
    result.update(deletion_ready=False,independent_map_copy='NOT_RUN',snapshot_creation_repeated=False,
                  iam_apply_repeated=False,plan_repeated=False)
    return result

def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('root','reconstruction-receipt','resume-journal','contract','key-file'):p.add_argument('--'+name,required=True)
    p.add_argument('--age-keygen');a=p.parse_args();os.umask(0o077)
    out=Path(tempfile.mkdtemp(prefix='monitoring-finalize-',dir=a.root))
    try:result=execute(a,out)
    except Exception as error:result={'status':'STOP_MONITORING_FINALIZATION','reason':str(error) if type(error) is RuntimeError else type(error).__name__}
    result.update(deletion_ready=False,deletion_started=False,automatic_retry=False)
    save(out/'receipt.json',result);print(json.dumps(result,indent=2));print('Private finalization directory: '+str(out))
    return 0 if result['status'].startswith(('PASS_','PENDING_')) else 1

if __name__=='__main__':raise SystemExit(main())
