"""B 삭제 후 정확한 ROSA 소유 자원과 B Snapshot 네 개만 대조합니다."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

ROOT=Path.home()/'.local/share/seokpan-rosa-plan-inputs-5psj2l62'
RUN=ROOT/'controller-run-bundle-K2Tx7HgB'
CID='2ti8n9c2j2em1h6jbb34kc0dq95dv4fi'
SNAPSHOTS=['snap-07cb6c69e658e9d1d','snap-041f8647778570dca',
           'snap-0f58e995b9f906d39','snap-06289fa7af87099c0']

def need(ok,code):
    if not ok:
        raise RuntimeError(code)

def read(path):
    need(not Path(path).is_symlink(),'REGULAR_LOCAL_RECORD_REQUIRED')
    return json.loads(Path(path).read_text())

def save(path,value):
    with path.open('x') as stream:
        json.dump(value,stream,indent=2)

def aws(args):
    env=os.environ.copy()
    env.update(AWS_REGION='ap-northeast-2',AWS_DEFAULT_REGION='ap-northeast-2',
               AWS_PAGER='',AWS_MAX_ATTEMPTS='1')
    p=subprocess.run(['aws',*args,'--output','json','--cli-connect-timeout','5',
                      '--cli-read-timeout','20'],capture_output=True,env=env,timeout=60)
    if p.returncode:
        match=re.search(rb'An error occurred \(([A-Za-z0-9_.-]+)\)',p.stderr)
        return None,match[1].decode() if match else 'UNCLASSIFIED_AWS_FAILURE'
    return json.loads(p.stdout or b'{}'),None

def main(delete_snapshots):
    plan_dir=Path(read(RUN/'b-final-delete-plan.json')['directory'])
    done=read(plan_dir/'apply-receipt.json')
    need(done['status']=='PASS_B_ROSA_DESTROY_APPLY_AND_ROOT_STATE_EMPTY'
         and done['cluster_id']==CID,'SUCCESSFUL_B_ROOT_DELETE_REQUIRED')
    manifest=read(plan_dir/'plan-manifest.json')
    state=read(plan_dir/'before.tfstate')
    handoff=state['outputs']['platform_handoff']['value']
    need(handoff['cluster_id']==CID and re.fullmatch(r'[A-Za-z0-9-]+',handoff['infra_id']),
         'CURRENT_CLUSTER_INFRA_ID_REQUIRED')
    current=read(RUN/'today-recreate-dispatch-20261020.json')
    config=read(current['config'])
    rosa=subprocess.run([config['auth']['rosa_cli'],'list','clusters','-o','json'],
        capture_output=True,timeout=60)
    need(rosa.returncode==0,'REMOTE_CLUSTER_READ_FAILED_NO_ABSENCE_CLAIM')
    clusters=json.loads(rosa.stdout)
    if isinstance(clusters,dict):
        need(isinstance(clusters.get('items'),list),'REMOTE_CLUSTER_LIST_FORMAT_REQUIRED')
        clusters=clusters['items']
    need(isinstance(clusters,list),'REMOTE_CLUSTER_LIST_FORMAT_REQUIRED')
    need(not any(x.get('id')==CID for x in clusters),'REMOTE_CLUSTER_CLEANUP_STILL_PENDING')
    caller,error=aws(['sts','get-caller-identity'])
    need(not error and caller['Account']==manifest['account_id'],'EXACT_PROJECT_ACCOUNT_REQUIRED')
    out=Path(tempfile.mkdtemp(prefix='b-final-residuals-',dir=ROOT))
    report={'status':'COLLECTED_B_FINAL_RESIDUALS','cluster_id':CID,
            'remote_cluster_absent':True,'A_resources_changed':False,
            'coverage':'EXACT_CURRENT_INFRA_OWNED_TAG_AND_FOUR_B_SNAPSHOT_IDS',
            'untagged_or_shared_absence_proven':False,'billing_zero_confirmed':False,
            'read_errors':[],'snapshots':[],'snapshots_deleted':[]}
    tag='kubernetes.io/cluster/'+handoff['infra_id']
    for name,command,key in [('instances','describe-instances','Reservations'),
                             ('volumes','describe-volumes','Volumes'),
                             ('network_interfaces','describe-network-interfaces','NetworkInterfaces')]:
        data,error=aws(['ec2',command,'--filters','Name=tag:'+tag+',Values=owned'])
        if error:
            report[name]=None
            report['read_errors'].append({'operation':command,'code':error})
            continue
        save(out/(name+'.private.json'),data)
        rows=data[key]
        if name=='instances':
            rows=[i for reservation in rows for i in reservation.get('Instances',[])
                  if i['State']['Name']!='terminated']
        report[name]=len(rows)
    data,error=aws(['resourcegroupstaggingapi','get-resources','--tag-filters',
        json.dumps([{'Key':tag,'Values':['owned']}]),'--resource-type-filters',
        'elasticloadbalancing:loadbalancer'])
    report['load_balancers']=None if error else len(data['ResourceTagMappingList'])
    if error:
        report['read_errors'].append({'operation':'tag:GetResources','code':error})
    else:
        save(out/'load_balancers.private.json',data)
    pending=[]
    for snapshot in SNAPSHOTS:
        data,error=aws(['ec2','describe-snapshots','--snapshot-ids',snapshot])
        if error=='InvalidSnapshot.NotFound':
            report['snapshots'].append({'id':snapshot,'status':'ABSENT'})
        elif error:
            report['snapshots'].append({'id':snapshot,'status':'UNKNOWN','error':error})
            report['read_errors'].append({'operation':'DescribeSnapshots','code':error})
        else:
            rows=data['Snapshots']
            need(len(rows)==1 and rows[0]['SnapshotId']==snapshot
                 and rows[0]['OwnerId']==manifest['account_id'],'EXACT_B_SNAPSHOT_OWNER_REQUIRED')
            report['snapshots'].append({'id':snapshot,'status':'PRESENT','state':rows[0]['State']})
            pending.append(snapshot)
    if delete_snapshots and pending:
        print(json.dumps({'status':'REVIEW_EXACT_B_SNAPSHOTS_BEFORE_CONFIRMATION',
            'snapshot_ids':pending,'A_resources_excluded':True},indent=2),flush=True)
        need(input('Type exactly DELETE B SNAPSHOTS: ')=='DELETE B SNAPSHOTS',
             'HUMAN_CONFIRMATION_MISMATCH')
        for snapshot in pending:
            marker=plan_dir/(snapshot+'.delete-started.NO_AUTOMATIC_RETRY.json')
            need(not marker.exists(),'PREVIOUS_SNAPSHOT_DELETE_REQUIRES_READ_ONLY_RECONCILIATION')
            save(marker,{'snapshot_id':snapshot,'approval':'EXACT_USER_CONFIRMATION'})
            _,error=aws(['ec2','delete-snapshot','--snapshot-id',snapshot])
            need(error is None,'EXACT_SNAPSHOT_DELETE_FAILED_NO_RETRY')
            report['snapshots_deleted'].append(snapshot)
        report['snapshot_absence_after_delete']='PENDING_NEW_READBACK'
    if report['read_errors']:
        report['status']='PARTIAL_B_FINAL_RESIDUALS_UNKNOWN_READS_REMAIN'
    save(out/'receipt.json',report)
    print(json.dumps(report,indent=2))
    print('Private B residual directory: '+str(out))

if __name__=='__main__':
    os.umask(0o077)
    parser=argparse.ArgumentParser()
    parser.add_argument('--delete-reviewed-snapshots',action='store_true')
    args=parser.parse_args()
    try:
        main(args.delete_reviewed_snapshots)
    except Exception as error:
        print(json.dumps({'status':'STOP_B_FINAL_RESIDUALS','reason':str(error)
            if type(error) is RuntimeError else type(error).__name__,'automatic_retry':False,
            'A_resources_changed':False,'billing_zero_confirmed':False}))
        raise SystemExit(2)

