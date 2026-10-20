"""공개 증거 보존과 새 Plan 확인 후 B ROSA 루트만 삭제합니다."""
import importlib.util
import json
import os
from pathlib import Path
import sys

ROOT=Path.home()/'.local/share/seokpan-rosa-plan-inputs-5psj2l62'
RUN=ROOT/'controller-run-bundle-K2Tx7HgB'
CID='2ti8n9c2j2em1h6jbb34kc0dq95dv4fi'

def need(ok,code):
    if not ok:
        raise RuntimeError(code)

def main():
    need(len(sys.argv)==2 and sys.argv[1]=='--apply-reviewed-plan','EXPLICIT_APPLY_FLAG_REQUIRED')
    pointer=json.loads((RUN/'today-recreate-dispatch-20261020.json').read_text())
    source=Path(pointer['ops'])/'scripts/rosa-lifecycle-plan.py'
    spec=importlib.util.spec_from_file_location('b_delete_existing_plan',source)
    L=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(L)
    config=L.read_json(pointer['config'])
    plan_dir=Path(L.read_json(RUN/'b-final-delete-plan.json')['directory'])
    need(plan_dir.resolve().parent==ROOT.resolve() and not plan_dir.is_symlink(),
         'EXISTING_B_DELETE_PLAN_DIRECTORY_REQUIRED')
    m=L.read_json(plan_dir/'plan-manifest.json')
    need(m['action']=='delete' and m['cluster_id']==CID
         and m['tf_root']==str(Path(config['create']['tf_root']).resolve())
         and m['tf_data']==str(Path(config['tf_data']).resolve())
         and m['var_file']==str(Path(config['create']['var_file']).resolve()),
         'EXACT_CURRENT_B_ROSA_PLAN_REQUIRED')
    active=L.read_json(RUN/'hpa-active-time-copy.json')
    final=Path(active['directory'])/'final-closeout'
    stopped=L.read_json(final/'receipt.json')
    proof=L.read_json(final/'github-publication.json')
    need(((stopped.get('status') == 'PASS_RUN_STOPPED_RESTORED_EVIDENCE_PRESERVED_NOT_TEST_VERDICT'
         and stopped.get('restoration_verified') is True)
         or (stopped.get('status') == 'PASS_FINAL_TEARDOWN_EVIDENCE_PRESERVED_NOT_TEST_VERDICT'
         and stopped.get('final_teardown_selected') is True
         and stopped.get('generator_stopped') is True and stopped.get('B_readers_stopped') is True))
         and proof['status']=='PASS_GITHUB_PUBLIC_EVIDENCE_SAVED'
         and proof['run_id']==stopped['run_id']
         and proof['public_archive_sha256']==stopped['public_archive_sha256']
         and L.sha(stopped['public_archive'])==stopped['public_archive_sha256'],
         'GITHUB_PUBLIC_EVIDENCE_REQUIRED_BEFORE_DELETE')
    marker=plan_dir/'apply-started.NO_AUTOMATIC_RETRY.json'
    need(not marker.exists(),'PREVIOUS_APPLY_REQUIRES_READ_ONLY_RECONCILIATION')
    need(L.sha(m['plan'])==m['plan_sha256'] and L.sha(m['var_file'])==m['var_sha256']
         and L.source_hash(Path(m['tf_root']))==m['source_hash'], 'PLAN_INPUTS_CHANGED')
    env=L.environment(Path(m['tf_root']),Path(m['tf_data']))
    caller=json.loads(L.call(['aws','sts','get-caller-identity','--output','json'],env,plan_dir).stdout)
    need(caller['Account']==m['account_id'] and ':assumed-role/seokpan-tf-rosa/' in caller['Arn'],
         'EXACT_ROSA_ROLE_REQUIRED')
    need(env.get('RHCS_TOKEN'),'CURRENT_RHCS_TOKEN_REQUIRED')
    tf=['terraform','-chdir='+m['tf_root']]
    counts=L.scope(json.loads(L.call(tf+['show','-json',m['plan']],env,plan_dir).stdout),'delete',CID)
    current=json.loads(L.call(tf+['state','pull'],env,plan_dir).stdout)
    need((current.get('lineage'),current.get('serial'))==(m['state_lineage'],m['state_serial']),
         'STATE_CHANGED_REVIEW_NEW_PLAN_REQUIRED')
    print(json.dumps({'status':'REVIEW_B_ONLY_DELETE_BEFORE_CONFIRMATION','cluster_id':CID,
        'counts':counts,'plan_sha256':m['plan_sha256'],'github_urls':proof['urls'],
        'A_resources_excluded':True,'snapshots_deleted_by_this_command':False},indent=2),flush=True)
    need(sys.stdin.isatty(),'INTERACTIVE_USER_CONFIRMATION_REQUIRED')
    phrase='DELETE B ROSA '+CID+' '+m['plan_sha256'][:12]
    need(input('Type exactly '+phrase+': ')==phrase,'HUMAN_CONFIRMATION_MISMATCH')
    fd=os.open(marker,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'w') as stream:
        json.dump({'cluster_id':CID,'plan_sha256':m['plan_sha256'],
            'approval':'EXACT_USER_CONFIRMATION_IN_CONTROLLER',
            'disposition':'FINAL_B_CLOSEOUT_NO_CLOUD_BACKUP_RETENTION_REQUESTED',
            'automatic_retry':False},stream)
    outcome=L.call(tf+['apply','-input=false','-no-color','-lock-timeout=60s',m['plan']],env,plan_dir)
    (plan_dir/'apply.private.log').write_bytes(outcome.stdout+outcome.stderr)
    need(outcome.returncode==0,'APPLY_PARTIAL_OUTCOME_NO_RETRY')
    state=json.loads(L.call(tf+['state','pull'],env,plan_dir).stdout)
    managed=sum(len(x.get('instances',[])) for x in state.get('resources',[]) if x.get('mode')=='managed')
    need(managed==0,'B_ROOT_STATE_RECONCILIATION_REQUIRED_NO_RETRY')
    result={'status':'PASS_B_ROSA_DESTROY_APPLY_AND_ROOT_STATE_EMPTY',
        'cluster_id':CID,'managed_instances':managed,'A_resources_changed':False,
        'snapshots_deleted':False,'asynchronous_cleanup':'NOT_VERIFIED',
        'billing_zero_confirmed':False,'remaining':['REMOTE_CLUSTER_ABSENCE',
            'EXACT_B_MONITORING_SNAPSHOTS','EXACT_INFRA_OWNED_EC2_LB_EBS_ENI_INVENTORY']}
    with (plan_dir/'apply-receipt.json').open('x') as stream:
        json.dump(result,stream,indent=2)
    print(json.dumps(result,indent=2))

if __name__=='__main__':
    os.umask(0o077)
    try:
        main()
    except Exception as error:
        print(json.dumps({'status':'STOP_B_FINAL_DELETE','reason':str(error)
            if type(error) is RuntimeError else type(error).__name__,
            'automatic_retry':False,'A_resources_changed':False}))
        raise SystemExit(2)

