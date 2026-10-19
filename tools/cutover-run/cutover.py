"""Prepare/access/stop/GO chain for the reviewed source maintenance PR. No default actions."""
import argparse
import datetime as dt
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

REPO='seokpan/seokpan-gitops'
HEAD='507f016285c2aa5f8afae0fe12f1fe8e2d8a8b79'
MANIFEST='7060e2d9f7e53c070bf7d0641524fc5800d04c71acd62f5268a6f8c36cba6bc9'
VERIFIER='bf30d2d60717c226583b1b67dc5d2ea9d6af2ad5459eaf329740ebd6961f8a23'

def need(ok,why):
    if not ok:raise RuntimeError(why)
def canonical(x):return json.dumps(x,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
def sha(x):return hashlib.sha256(canonical(x)).hexdigest()
def read(p):
    p=Path(p);need(p.is_file() and not p.is_symlink() and p.stat().st_size<4*1024*1024,'BOUNDED_EXISTING_FILE_REQUIRED');return json.loads(p.read_text(encoding='utf-8'))
def save(p,x):
    fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'wb') as f:f.write(canonical(x)+b'\n')
def stamp(x):
    v=dt.datetime.fromisoformat(x.replace('Z','+00:00'));need(v.tzinfo is not None,'ZONED_TIME_REQUIRED');return v.timestamp()
def utc():return dt.datetime.now(dt.timezone.utc).isoformat()

def gates(c,external,rotation,now=None):
    now=time.time() if now is None else now;end=stamp(c['window']['not_after'])
    need(stamp(c['window']['not_before'])<=now<end,'CUTOVER_WINDOW_NOT_CURRENT')
    need(external['run_id']==c['run_id'] and external['source_api_sha256']==hashlib.sha256(c['source_api'].rstrip('/').encode()).hexdigest()
         and external['inventory_complete'] is True and external['operator'] and external['reference'],'CURRENT_EXTERNAL_WRITER_INVENTORY_REQUIRED')
    need(0<=now-stamp(external['checked_at'])<=300 and stamp(external['hold_until'])>=end,'EXTERNAL_HOLD_FRESHNESS_OR_END_INVALID')
    need(set(external['categories'])=={'external_ingress','ci','manual_direct_db','scheduled_and_other_services'},'ALL_EXTERNAL_WRITER_CATEGORIES_REQUIRED')
    for v in external['categories'].values():
        need(v['state'] in {'STOPPED','CONFIRMED_NONE'} and v['evidence_ref'],'EXTERNAL_WRITER_NOT_HELD')
    need(rotation['run_id']==c['run_id'] and rotation['operator'] and rotation['reference'] and rotation['result']=='PASS'
         and rotation['gate_seconds']==7200 and 0<=now-stamp(rotation['checked_at'])<=300
         and stamp(rotation['safe_until'])>=end,'CURRENT_A_ROTATION_7200_GATE_REQUIRED')
    return {'external_hold_sha256':sha(external),'rotation_sha256':sha(rotation),'operator_reports_not_cryptographic_attestation':True}

def destination_ready(c):
    platform=read(c['platform_handoff'])
    need(platform.get('state')=='ready' and platform.get('cluster_id')==c['cloud_cluster_id']
         and c['cloud_cluster_id']!='2tejeb2farjaaoef1e74asdf6amrtpu3','NEW_CLOUD_PLATFORM_READY_BEFORE_SOURCE_STOP')
    need(c['deployment_review_ref'] and c['cloud_connection_preparation_ref'] and c['A_preflight_ref'],'PREPARED_DEPLOYMENT_CONNECTION_AND_A_REFERENCES_REQUIRED')
    return {'platform_receipt_sha256':sha(platform),'new_cloud_cluster_id':c['cloud_cluster_id'],
            'deployment_review_ref':c['deployment_review_ref'],'cloud_connection_preparation_ref':c['cloud_connection_preparation_ref'],
            'final_import_and_app_business_not_required_before_dump':True}

def config(c):
    need(c.get('schema')==1 and re.fullmatch(r'[a-z0-9-]{3,60}',c.get('run_id') or ''),'RUN_SCHEMA_REQUIRED')
    need(c['source_api'].startswith('https://') and 'openshiftapps.com' not in c['source_api'],'ORIGINAL_KUBERNETES_API_REQUIRED')
    need(c['stop_budget_minutes']==75 and c['joint_budget_minutes']==120,'ACCEPTED_TIME_BUDGETS_REQUIRED')
    need(0<stamp(c['window']['not_after'])-stamp(c['window']['not_before'])<=120*60,'WINDOW_REQUIRED_WITHIN_JOINT_BUDGET')
    return c

def verifier(root):
    p=Path(root)/'scripts/verify-source-backend-stop.py';need(hashlib.sha256(p.read_bytes().replace(b'\r\n',b'\n')).hexdigest()==VERIFIER,'PINNED_EXISTING_STOP_VERIFIER_REQUIRED')
    s=importlib.util.spec_from_file_location('source_stop_verifier',p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m

def call(argv,payload=None):
    p=subprocess.run(argv,input=payload,capture_output=True,timeout=35);need(p.returncode==0,'COMMAND_RESULT_UNKNOWN_NO_AUTOMATIC_RETRY');return p.stdout

def access(c,v):
    expected=v.expected_inputs(read(c['source_contract']),read(c['source_read']),c['source_api']);client=v.Client(expected)
    cfg=client.read('config','view','--minify','-o','json')['clusters'][0]['cluster']
    need(cfg['server'].rstrip('/')==c['source_api'].rstrip('/') and not cfg.get('insecure-skip-tls-verify'),'WRONG_OR_UNTRUSTED_SOURCE_CONTEXT')
    identity=client.read('auth','whoami','-o','json');name=identity.get('status',{}).get('userInfo',{}).get('username');need(name,'ACTUAL_SOURCE_IDENTITY_REQUIRED')
    dep=client.read('get','deployment','backend','-n','application','-o','json');need(dep['metadata']['uid']==expected['deployment_uid'],'SOURCE_DEPLOYMENT_UID_CHANGED')
    apps=client.read('get','applications.argoproj.io','root','apps-backend','-n','argocd','-o','json')['items']
    for a in apps:need(a['metadata']['uid']==expected['applications'][a['metadata']['name']]['uid'],'ORIGINAL_ARGO_UID_CHANGED')
    return {'status':'PASS_CURRENT_SOURCE_ACCESS_NOT_WRITER_STOP','api_sha256':hashlib.sha256(c['source_api'].rstrip('/').encode()).hexdigest(),
            'caller_sha256':hashlib.sha256(name.encode()).hexdigest(),'deployment_uid':expected['deployment_uid'],'observed_at':utc()}

def gh(c,path,method='GET',payload=None):
    if not c.get('github_api_argv'):
        need(method=='GET','EXISTING_GH_PATH_OR_EXPLICIT_EXISTING_UI_MERGE_REQUIRED')
        import urllib.request
        need(path.startswith('repos/'+REPO+'/'),'SCOPED_PUBLIC_GITHUB_GET_ONLY')
        with urllib.request.urlopen('https://api.github.com/'+path,timeout=25) as response:raw=response.read(2097153)
        need(len(raw)<=2097152,'GITHUB_RESPONSE_TOO_LARGE');return json.loads(raw)
    need(isinstance(c['github_api_argv'],list) and all(isinstance(x,str) and x for x in c['github_api_argv']),'EXISTING_GITHUB_ARGV_REQUIRED')
    args=[*c['github_api_argv'],path]
    if method!='GET':args+=['--method',method,'--input','-']
    return json.loads(call(args,canonical(payload) if payload else None))

def approved_pr(pr,reviews,checks):
    need(pr['head']['sha']==HEAD and pr['base']['ref']=='main' and pr['base']['repo']['full_name']==REPO,'REVIEWED_PR156_CHANGED')
    need(not pr.get('draft') and pr.get('state')=='open' and pr.get('mergeable') is True,'PR156_READY_REVIEW_REQUIRED_BEFORE_STOP')
    latest={}
    for r in reviews:
        if r['state'] in {'APPROVED','CHANGES_REQUESTED'}:latest[r['user']['login']]=r
    need(not any(x['state']=='CHANGES_REQUESTED' for x in latest.values()) and any(x['state']=='APPROVED' and x['commit_id']==HEAD for x in latest.values()),'CURRENT_HEAD_REVIEW_APPROVAL_REQUIRED')
    need(all(x['status']=='completed' and x['conclusion'] in {'success','skipped','neutral'} for x in checks.get('check_runs',[])),'SOURCE_CI_NOT_ACCEPTED')

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=['prepare','access','stop','sync','verify','go','decision'])
    p.add_argument('--config',required=True,type=Path);p.add_argument('--work',required=True,type=Path);p.add_argument('--infra',required=True,type=Path)
    p.add_argument('--execute',action='store_true');p.add_argument('--adopt-existing-merge',action='store_true');p.add_argument('--choice',choices=['HOLD','CLOUD','SOURCE']);p.add_argument('--data-decision',type=Path)
    a=p.parse_args();os.umask(0o077)
    try:
        c=config(read(a.config));v=verifier(a.infra)
        if not a.execute:print(json.dumps({'status':'PREPARED_CUTOVER_COMMAND_NOT_EXECUTED','action':a.action,'writer_stop':False,'go_sent':False}));return 0
        a.work.mkdir(mode=0o700,exist_ok=True);fingerprint=sha(c)
        if a.action=='prepare':
            save(a.work/'commands.json',{'config_sha256':fingerprint,'sequence':['access','stop','sync if needed','verify','go','A actual Data execution','B current/SQL/app business','decision'],
                'source_pr':156,'source_head':HEAD,'external_writer_and_rotation_files': [c['external_hold'],c['rotation_receipt']],
                'go_is_prepared_not_sent':True,'runtime_access_not_verified':True});print(json.dumps({'status':'PASS_LOCAL_CUTOVER_SEQUENCE_PREPARED_NO_STOP_OR_GO'}));return 0
        if a.action=='access':
            r=access(c,v);r.update(config_sha256=fingerprint)
            save(a.work/('access-'+str(time.time_ns())+'.json'),r);print(json.dumps(r));return 0
        if a.action in {'stop','go'}:
            proof=gates(c,read(c['external_hold']),read(c['rotation_receipt']));proof.update(destination_ready(c))
        if a.action=='stop':
            who=access(c,v);pr=gh(c,'repos/'+REPO+'/pulls/156')
            if pr.get('merged'):
                if not (a.work/'merge.STARTED.json').exists():
                    need(a.adopt_existing_merge and pr['head']['sha']==HEAD and pr['base']['repo']['full_name']==REPO
                         and stamp(c['window']['not_before'])<=stamp(pr['merged_at'])<=time.time()
                         and 0<=time.time()-stamp(pr['merged_at'])<=300,'EXPLICIT_RECENT_BOUND_UI_MERGE_REQUIRED')
                    need(stamp(read(c['external_hold'])['checked_at'])<=stamp(pr['merged_at'])
                         and stamp(read(c['rotation_receipt'])['checked_at'])<=stamp(pr['merged_at']),'NO_RETROACTIVE_WRITER_OR_ROTATION_ACCEPTANCE')
                    save(a.work/'merge.STARTED.json',{'utc':pr['merged_at'],'config_sha256':fingerprint,'source_access':who,**proof,'adopted_existing_ui_merge':True})
                need(read(a.work/'merge.STARTED.json')['config_sha256']==fingerprint,'MERGE_OUTSIDE_THIS_RUN_REVIEW_REQUIRED')
                print(json.dumps({'status':'PR156_MERGED_OBSERVE_EXISTING_SYNC_NO_MERGE_RETRY','next':'verify'}));return 0
            reviews=gh(c,'repos/'+REPO+'/pulls/156/reviews?per_page=100');checks=gh(c,'repos/'+REPO+'/commits/'+HEAD+'/check-runs?per_page=100')
            approved_pr(pr,reviews,checks)
            need(not (a.work/'merge.STARTED.json').exists(),'MERGE_OUTCOME_UNKNOWN_READ_PR_NO_RETRY')
            save(a.work/'merge.STARTED.json',{'utc':utc(),'config_sha256':fingerprint,'source_access':who,**proof})
            result=gh(c,'repos/'+REPO+'/pulls/156/merge','PUT',{'sha':HEAD,'merge_method':'squash'})
            need(result.get('merged') is True,'MERGE_NOT_CONFIRMED_OBSERVE_NO_RETRY');save(a.work/'merge.json',result)
            print(json.dumps({'status':'PASS_PR156_MERGED_WAIT_EXISTING_ARGO_SYNC','merge_sha':result['sha'],'next':'verify','dump_go':False}));return 0
        if a.action=='sync':
            need((a.work/'merge.STARTED.json').exists(),'THIS_RUN_MERGE_REQUIRED')
            pr=gh(c,'repos/'+REPO+'/pulls/156');need(pr.get('merged') is True and pr['head']['sha']==HEAD,'PR156_NOT_MERGED')
            expected=v.expected_inputs(read(c['source_contract']),read(c['source_read']),c['source_api']);client=v.Client(expected);snapshot=client.snapshot()
            ready,_=v.evaluate(snapshot,expected,pr['merge_commit_sha'],MANIFEST,{})
            if ready:print(json.dumps({'status':'ALREADY_STOPPED_NO_SYNC_REPEATED'}));return 0
            if any(x.get('operation') or x.get('status',{}).get('operationState',{}).get('phase') in {'Running','Terminating'} for x in snapshot['applications']):
                print(json.dumps({'status':'OBSERVE_EXISTING_ARGO_OPERATION_NO_DUPLICATE_SYNC'}));return 0
            marker=a.work/'sync.STARTED.json';need(not marker.exists(),'SYNC_ATTEMPT_EXISTS_OBSERVE_NO_RETRY')
            app=next(x for x in snapshot['applications'] if x['metadata']['name']=='apps-backend')
            save(marker,{'utc':utc(),'config_sha256':fingerprint,'application_uid':app['metadata']['uid'],'merge_sha':pr['merge_commit_sha']})
            ops=[{'op':'test','path':'/metadata/uid','value':app['metadata']['uid']},{'op':'test','path':'/metadata/resourceVersion','value':app['metadata']['resourceVersion']},
                 {'op':'test','path':'/spec','value':app['spec']},{'op':'add','path':'/operation','value':{'initiatedBy':{'username':'B-cutover'},'retry':{'limit':0},
                   'sync':{'revision':pr['merge_commit_sha'],'prune':False,'syncOptions':['Prune=false','Delete=false']}}}]
            call([*client.prefix,'patch','application','apps-backend','-n','argocd','--type=json','-p',canonical(ops).decode()])
            print(json.dumps({'status':'REQUESTED_SOURCE_BACKEND_ZERO_SYNC_NOT_STOP_PASS','next':'verify'}));return 0
        if a.action=='verify':
            need((a.work/'merge.STARTED.json').exists(),'THIS_RUN_STOP_ATTEMPT_REQUIRED')
            # Use the accepted verifier once for this real stop. It does not stop writers itself.
            need(not (a.work/'kubernetes-stop.json').exists(),'ACCEPTED_STOP_RECEIPT_EXISTS_DO_NOT_REPEAT')
            cmd=[sys.executable,'-I',str(a.infra/'scripts/verify-source-backend-stop.py'),'--source-contract',c['source_contract'],
                 '--source-read',c['source_read'],'--source-api',c['source_api'],'--manifest-sha256',MANIFEST,'--pr','156','--settle-seconds','30','--timeout','300']
            r=subprocess.run(cmd,capture_output=True,text=True,timeout=330)
            rows=[json.loads(x) for x in r.stdout.splitlines() if x.startswith('{')]
            need(r.returncode==0 and rows and rows[0].get('status')=='PASS_KUBERNETES_BACKEND_STOP_GATE','CURRENT_KUBERNETES_STOP_NOT_ACCEPTED')
            save(a.work/'kubernetes-stop.json',{'result':rows[0],'observed_at':utc(),'config_sha256':fingerprint})
            print(json.dumps({'status':'PASS_CURRENT_KUBERNETES_STOP_REUSED_EXISTING_VERIFIER','next':'go','external_writers_not_inferred':True}));return 0
        if a.action=='go':
            stop=read(a.work/'kubernetes-stop.json');need(stop['config_sha256']==fingerprint and 0<=time.time()-stamp(stop['observed_at'])<=300,'FRESH_BOUND_STOP_RECEIPT_REQUIRED')
            # Re-read the target at GO to avoid issuing from a stale saved Kubernetes PASS.
            expected=v.expected_inputs(read(c['source_contract']),read(c['source_read']),c['source_api']);client=v.Client(expected)
            ready,_=v.evaluate(client.snapshot(),expected,stop['result']['merge_sha'],MANIFEST,{})
            need(ready,'STOP_HOLD_NO_LONGER_TRUE');need(not (a.work/'GO.json').exists(),'GO_ALREADY_PREPARED_DO_NOT_ISSUE_AGAIN')
            started=read(a.work/'merge.STARTED.json');deadline=min(stamp(c['window']['not_after']),stamp(started['utc'])+75*60)
            need(deadline>time.time(),'WRITE_STOP_BUDGET_EXPIRED_HOLD_FOR_DECISION')
            r={'status':'READY_TO_RELAY_DATA_FINAL_DUMP_GO','run_id':c['run_id'],'config_sha256':fingerprint,'issued_at':utc(),
                'valid_until':dt.datetime.fromtimestamp(deadline,dt.timezone.utc).isoformat(),'merge_sha':stop['result']['merge_sha'],'stop_receipt_sha256':sha(stop),'proof':proof,
                'remaining_write_stop_seconds':int(deadline-time.time()),
                'message':'Data final dump GO','sent_to_A':False,'A_replication_checks_and_actual_Data_execution_remain':True}
            r['message']='Data 최종 덤프 시작 GO | run='+c['run_id']+' | merge='+r['merge_sha']+' | 종료='+r['valid_until']
            save(a.work/'GO.json',r);print(json.dumps(r,ensure_ascii=False));return 0
        d=read(a.data_decision);need(d['run_id']==c['run_id'] and d['data_compare_reference'] and d['operator'],'CURRENT_DATA_DECISION_REFERENCE_REQUIRED')
        need(a.choice is not None,'EXPLICIT_DECISION_REQUIRED')
        if a.choice=='CLOUD':need(d.get('data_compare_result')=='ACCEPTED' and all(d.get(k) for k in ('purpose_sql_ref','current_schema_ref','normal_business_ref')),'DATA_SQL_SCHEMA_BUSINESS_BEFORE_CLOUD_DECISION')
        if a.choice=='SOURCE':need(d['cloud_new_writes_confirmed'] is False and d['source_reopen_approved'] is True,'NO_SIMPLE_SOURCE_REOPEN_AFTER_CLOUD_WRITES')
        r={'status':'RECORDED_EXPLICIT_TRANSITION_DECISION_NO_AUTOMATIC_REOPEN','choice':a.choice,'evidence_sha256':sha(d),'utc':utc(),'runtime_change':False}
        save(a.work/'decision.json',r);print(json.dumps(r));return 0
    except Exception as e:
        print(json.dumps({'status':'STOP_CUTOVER_CHAIN','reason':str(e) if type(e) is RuntimeError else type(e).__name__,'automatic_retry':False,'go_sent':False,'raw_logs_printed':False}));return 2

if __name__=='__main__':raise SystemExit(main())
