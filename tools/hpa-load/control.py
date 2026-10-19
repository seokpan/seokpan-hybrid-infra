"""Temporary HPA lease, first-run observation and UID-bound restore. Default is local only."""
import argparse
import copy
import json
from pathlib import Path
import sys
import time
sys.path.insert(0,str(Path(__file__).resolve().parent))
from common import *

LOCK='seokpan.io/hpa-measurement'

def no_node_growth(client):
    values=client.get('machineautoscalers.autoscaling.openshift.io',ns='openshift-machine-api')['items']
    need(not any(x['spec']['minReplicas']!=x['spec']['maxReplicas'] for x in values),'DYNAMIC_MACHINE_AUTOSCALING_MUST_BE_DISABLED_FOR_FIXED_BUDGET_TEST')

def pod_rows(client):
    return [p for p in client.get('pods')['items'] if p['metadata'].get('labels',{}).get('app.kubernetes.io/name') in {'backend','frontend'}]

def baseline(client,c):
    no_node_growth(client)
    app=client.get('application',APP,ARGO);d={n:client.get('deployment',n) for n in ('backend','frontend')}
    need(app['metadata']['uid']==c['binding']['application_uid'] and not app.get('operation')
         and not app['spec'].get('syncPolicy',{}).get('automated') and not app['metadata'].get('annotations',{}).get(LOCK)
         and not app['metadata'].get('deletionTimestamp') and not app['metadata'].get('ownerReferences')
         and app['spec'].get('project')==APP and app['spec'].get('destination')=={'server':'https://kubernetes.default.svc','namespace':NS}
         and app['status']['sync']['status']=='Synced' and app['status']['health']['status']=='Healthy','MANUAL_NORMAL_APPLICATION_REQUIRED')
    need(digest(normalized(app['spec']['source']))==c['binding']['normalized_source_sha256'],'ACCEPTED_SOURCE_CHANGED')
    need(not [x for x in client.get('hpa')['items'] if x['spec']['scaleTargetRef']['name']=='backend'],'EXISTING_BACKEND_HPA_REVIEW_REQUIRED')
    for n,x in d.items():
        need(not x['spec'].get('paused') and x['status'].get('observedGeneration')==x['metadata']['generation']
             and all(x['status'].get(k,0)==x['spec']['replicas'] for k in ('replicas','updatedReplicas','readyReplicas','availableReplicas')),'NORMAL_REPLICAS_REQUIRED')
        need([z['image'] for z in x['spec']['template']['spec']['containers']]==[c['expected_images'][n]],'PINNED_SINGLE_CONTAINER_REQUIRED')
        if n=='backend':
            need(x['spec']['replicas']>=c['min_replicas'] and x['spec']['replicas']<=c['max_replicas'],'BASELINE_OUTSIDE_HPA_RANGE')
            need(all(z.get('resources',{}).get('requests',{}).get('cpu') and z.get('resources',{}).get('requests',{}).get('memory') for z in x['spec']['template']['spec']['containers']),'CPU_MEMORY_REQUESTS_REQUIRED')
            for z in x['spec']['template']['spec']['containers']:
                for value in z['resources']['requests'].values():need(str(value).strip() not in {'0','0m','0Mi','0Gi'},'POSITIVE_RESOURCE_REQUEST_REQUIRED')
    pods=pod_rows(client)
    need(all(not p['metadata'].get('deletionTimestamp') and any(x['type']=='Ready' and x['status']=='True' for x in p.get('status',{}).get('conditions',[])) for p in pods),'READY_BASELINE_PODS_REQUIRED')
    return {'application':app,'deployments':d,'pods':pods,'db':db_probe(c)}

def attempt(work,name,value,resume=False):
    p=work/(name+'.STARTED.json')
    if p.exists():
        need(resume,'ATTEMPT_EXISTS_RECONCILE_BEFORE_EXPLICIT_RESUME')
        p=work/(name+'.REVIEWED_RESUME.'+str(time.time_ns())+'.json')
    save(p,{**value,'utc':utc()})

def own_hpa(client,c,work,allow_missing=False):
    all_hpa=client.get('hpa')['items']
    need(not any(x['spec']['scaleTargetRef']['name']=='backend' and x['metadata']['name']!=hpa(c)['metadata']['name'] for x in all_hpa),'FOREIGN_BACKEND_HPA_CONFLICT')
    matches=[x for x in all_hpa if x['metadata']['name']==hpa(c)['metadata']['name']]
    need(len(matches)<=1,'UNIQUE_HPA_REQUIRED')
    if not matches:
        need(allow_missing,'OWN_HPA_MISSING');return None
    x=matches[0];need(x['metadata'].get('labels',{}).get('seokpan.io/measurement-run')==c['run_id'],'HPA_OWNER_CHANGED')
    need(not x['metadata'].get('ownerReferences'),'UNEXPECTED_HPA_CONTROLLER_OWNER')
    receipt=work/'hpa-created.json'
    if receipt.exists():need(x['metadata']['uid']==read(receipt)['uid'],'HPA_UID_CHANGED')
    return x

def current(client,c,before,leased,check_node_growth=True):
    if check_node_growth:no_node_growth(client)
    app=client.get('application',APP,ARGO)
    need(app['metadata']['uid']==c['binding']['application_uid'] and app['spec']==leased
         and app['metadata'].get('annotations',{}).get(LOCK)==c['run_id'] and not app.get('operation')
         and app.get('status',{}).get('operationState',{}).get('phase') not in {'Running','Terminating'},'APPLICATION_LEASE_OR_OPERATION_CHANGED')
    deps={n:client.get('deployment',n) for n in ('backend','frontend')}
    for n,d in deps.items():
        original=copy.deepcopy(before['deployments'][n]['spec']);actual=copy.deepcopy(d['spec']);original.pop('replicas',None);actual.pop('replicas',None)
        need(d['metadata']['uid']==before['deployments'][n]['metadata']['uid'] and actual==original,'DEPLOYMENT_TEMPLATE_OR_UID_CHANGED')
    need(deps['frontend']['spec']['replicas']==before['deployments']['frontend']['spec']['replicas'],'FRONTEND_REPLICAS_CHANGED')
    pods=pod_rows(client)
    oldfe={x['metadata']['uid'] for x in before['pods'] if x['metadata']['labels']['app.kubernetes.io/name']=='frontend'}
    need({x['metadata']['uid'] for x in pods if x['metadata']['labels']['app.kubernetes.io/name']=='frontend'}==oldfe,'FRONTEND_PODS_CHANGED')
    return app,deps,pods

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=['prepare','start','observe','guard','stop','restore','verify-restore','reconcile'])
    p.add_argument('--config',type=Path,required=True);p.add_argument('--work',type=Path,required=True);p.add_argument('--execute',action='store_true')
    p.add_argument('--resume-reviewed',action='store_true');p.add_argument('--load-receipt',type=Path);p.add_argument('--confirm-no-load-started',action='store_true');p.add_argument('--seconds',type=int,default=2400)
    a=p.parse_args();os.umask(0o077)
    try:
        c=read(a.config);pending=missing(c)
        if pending:print(json.dumps({'status':'PENDING_HPA_ACTUAL_INPUTS','missing':pending,'mutations':0}));return 2
        budget=validate(c)
        if not a.execute:print(json.dumps({'status':'PASS_LOCAL_HPA_CONTRACT_NOT_EXECUTED','budget':budget,'action':a.action,'mutations':0}));return 0
        a.work.mkdir(mode=0o700,exist_ok=True);bound={'config_sha256':digest(c),'binding':c['binding']}
        if a.action=='guard':
            x=read(a.work/'latest.json');need(x['config_sha256']==digest(c) and 0<=time.time()-stamp(x['utc'])<=45,'GUARD_FEED_STALE_OR_CHANGED')
            need(not (a.work/'STOP.json').exists() and not (a.work/'restore.STARTED.json').exists()
                 and x['status'] in {'PASS_HPA_GUARD','WAIT_HPA_METRICS'},'LOAD_MUST_STOP')
            print(json.dumps(x));return 0
        client=Client(c);client.identity()
        if a.action not in {'restore','verify-restore','stop','reconcile'}:need(stamp(c['window']['not_before'])<=time.time()<stamp(c['window']['not_after']),'EXECUTION_WINDOW_CLOSED')
        if a.action=='prepare':
            need(not (a.work/'before.json').exists(),'PREPARED_PLAN_EXISTS_REUSE_IT')
            b=baseline(client,c);b.update(bound,leased_spec=lease_spec(b['application']['spec']));save(a.work/'before.json',b)
            save(a.work/'hpa.json',hpa(c));save(a.work/'plan.json',{'schema':1,'run_id':c['run_id'],'binding':c['binding'],'control_sha256':digest(c),
                'origin':c['origin'],'guard_argv':c['generator_guard_argv'],'window':c['window'],'phases':[{'vus':n,'seconds':t} for n,t in PHASES],
                'think_ms':1000,'timeout_ms':5000,'stop_error_fraction':0.05,'workload':'guest-lobby-and-ranking-reads','max_vus':300})
            print(json.dumps({'status':'PASS_HPA_PLAN_PREPARED_NOT_CREATED','budget':budget,'files':['before.json','hpa.json','plan.json']}));return 0
        b=read(a.work/'before.json');need(b['config_sha256']==digest(c),'HPA_PLAN_CONFIG_CHANGED');leased=b['leased_spec']
        if a.action=='start':
            need(not (a.work/'restored.json').exists() and not (a.work/'STOP.json').exists(),'CLOSED_RUN_CANNOT_RESTART')
            db_probe(c)
            app=client.get('application',APP,ARGO);need(app['metadata']['uid']==c['binding']['application_uid'] and not app.get('operation'),'APPLICATION_CHANGED')
            if app['spec']==b['application']['spec'] and not app['metadata'].get('annotations',{}).get(LOCK):
                attempt(a.work,'lease',bound,a.resume_reviewed)
                client.patch('application',APP,app,[{'op':'replace','path':'/spec','value':leased},{'op':'add','path':'/metadata/annotations/'+LOCK.replace('/','~1'),'value':c['run_id']}],ARGO)
            current(client,c,b,leased)
            existing=own_hpa(client,c,a.work,True)
            if existing is None:
                attempt(a.work,'create-hpa',bound,a.resume_reviewed);client.call('create','-f','-',payload=canon(hpa(c)))
                existing=own_hpa(client,c,a.work)
            need(not existing['metadata'].get('deletionTimestamp') and subset(hpa(c)['spec'],existing['spec']),'HPA_SPEC_CHANGED_OR_DELETING')
            if not (a.work/'hpa-created.json').exists():save(a.work/'hpa-created.json',{'uid':existing['metadata']['uid'],**bound})
            print(json.dumps({'status':'PASS_TEMPORARY_HPA_CREATED_NOT_LOAD_ACCEPTANCE','uid':existing['metadata']['uid']}));return 0
        if a.action=='reconcile':
            x=own_hpa(client,c,a.work,True);app=client.get('application',APP,ARGO)
            print(json.dumps({'status':'OBSERVED_HPA_PARTIAL_STATE_NO_MUTATIONS','hpa_uid':x['metadata']['uid'] if x else None,
                'leased':app['spec']==leased,'original_spec':app['spec']==b['application']['spec'],'manual_review_before_resume':True}));return 0
        if a.action=='stop':
            if not (a.work/'STOP.json').exists():save(a.work/'STOP.json',{'utc':utc(),**bound})
            x=own_hpa(client,c,a.work,True)
            if x:
                behavior=copy.deepcopy(x['spec'].get('behavior',{}))
                for k in ('scaleUp','scaleDown'):behavior.setdefault(k,{})['selectPolicy']='Disabled'
                if behavior!=x['spec'].get('behavior'):client.patch('hpa',x['metadata']['name'],x,[{'op':'add','path':'/spec/behavior','value':behavior}])
            print(json.dumps({'status':'STOP_REQUESTED_HPA_FROZEN_GENERATOR_GUARD_WILL_ABORT','restore_required':True}));return 0
        if a.action=='restore':
            need((a.load_receipt is not None)!=a.confirm_no_load_started,'ONE_STOPPED_RECEIPT_OR_EXPLICIT_NO_LOAD_CONFIRMATION_REQUIRED')
            if a.load_receipt:
                r=read(a.load_receipt);need(r['binding']==c['binding'] and r['control_sha256']==digest(c) and r['status'] in {'COMPLETED_HPA_LOAD','ABORTED_HPA_LOAD'}
                    and r['in_flight_at_end']==0,'MATCHING_STOPPED_GENERATOR_RECEIPT_REQUIRED')
            attempt(a.work,'restore',bound,a.resume_reviewed)
            app,deps,_=current(client,c,b,leased,check_node_growth=False);x=own_hpa(client,c,a.work,True)
            if x:
                # UID is checked atomically by the API, including when status updates race.
                opts=a.work/('delete-options-'+str(time.time_ns())+'.json');save(opts,{'apiVersion':'v1','kind':'DeleteOptions','preconditions':{'uid':x['metadata']['uid']}})
                client.call('delete','--raw=/apis/autoscaling/v2/namespaces/'+NS+'/horizontalpodautoscalers/'+x['metadata']['name'],'-f',str(opts))
                need(own_hpa(client,c,a.work,True) is None,'WAIT_FOR_OWN_HPA_REMOVAL_THEN_EXPLICIT_RESTORE_RESUME')
            original=b['deployments']['backend']['spec']['replicas'];d=deps['backend']
            if d['spec']['replicas']!=original:client.patch('deployment','backend',d,[{'op':'replace','path':'/spec/replicas','value':original}])
            app=client.get('application',APP,ARGO)
            need(app['spec']==leased and app['metadata']['annotations'].get(LOCK)==c['run_id'],'APPLICATION_LEASE_CHANGED')
            client.patch('application',APP,app,[{'op':'replace','path':'/spec','value':b['application']['spec']},{'op':'remove','path':'/metadata/annotations/'+LOCK.replace('/','~1')}],ARGO)
            print(json.dumps({'status':'RESTORE_REQUESTED_VERIFY_CURRENT_READY_NO_AUTOMATIC_ROLLBACK','business_accepted':False}));return 0
        if a.action=='verify-restore':
            app=client.get('application',APP,ARGO);need(app['metadata']['uid']==c['binding']['application_uid'] and app['spec']==b['application']['spec']
                and not app['metadata'].get('annotations',{}).get(LOCK) and not app.get('operation') and app['status']['sync']['status']=='Synced'
                and app['status']['health']['status']=='Healthy','ORIGINAL_APPLICATION_NOT_RESTORED')
            need(own_hpa(client,c,a.work,True) is None,'TEMPORARY_HPA_REMAINS')
            for n,old in b['deployments'].items():
                d=client.get('deployment',n);need(d['metadata']['uid']==old['metadata']['uid'] and d['spec']==old['spec']
                    and d['status'].get('observedGeneration')==d['metadata']['generation'] and all(d['status'].get(k,0)==d['spec']['replicas'] for k in ('replicas','readyReplicas','updatedReplicas','availableReplicas')),'ORIGINAL_DEPLOYMENT_NOT_READY')
            pods=pod_rows(client);need(not any(x['metadata'].get('deletionTimestamp') for x in pods),'TERMINATING_PODS_REMAIN')
            value={'status':'PASS_HPA_REMOVED_ORIGINAL_SPEC_REPLICAS_READY_NOT_BUSINESS_ACCEPTANCE',**bound,'utc':utc()}
            if not (a.work/'restored.json').exists():save(a.work/'restored.json',value)
            print(json.dumps(value));return 0
        integer(a.seconds,1,2700,'BOUNDED_OBSERVATION_REQUIRED');end=min(time.monotonic()+a.seconds,time.monotonic()+stamp(c['window']['not_after'])-time.time())
        while time.monotonic()<end:
            row={'utc':utc(),**bound,'status':'STOP_HPA_GUARD'}
            try:
                _,deps,pods=current(client,c,b,leased);x=own_hpa(client,c,a.work);sample=db_probe(c)
                need(x['spec']['minReplicas']==c['min_replicas'] and x['spec']['maxReplicas']==c['max_replicas'],'HPA_BOUNDS_CHANGED')
                need(sum(1 for z in pods if z['metadata']['labels']['app.kubernetes.io/name']=='backend')<=budget['peak_backend_pods_bound'],'OBSERVED_POD_BOUND_EXCEEDED')
                bad=any(z.get('status',{}).get('phase')=='Failed' or any(v.get('state',{}).get('waiting',{}).get('reason') in {'CrashLoopBackOff','ImagePullBackOff'} or v.get('lastState',{}).get('terminated',{}).get('reason')=='OOMKilled' for v in z.get('status',{}).get('containerStatuses',[])) for z in pods)
                need(not bad,'POD_FAILURE_STOP_LOAD')
                need(not any(z.get('status',{}).get('phase')=='Pending' and time.time()-stamp(z['metadata']['creationTimestamp'])>60 for z in pods),'PENDING_OVER_60S_STOP_LOAD')
                old={z['metadata']['uid']:sum(x.get('restartCount',0) for x in z.get('status',{}).get('containerStatuses',[])) for z in b['pods']}
                need(not any(sum(x.get('restartCount',0) for x in z.get('status',{}).get('containerStatuses',[]))>old.get(z['metadata']['uid'],0) for z in pods),'NEW_RESTART_STOP_LOAD')
                active=any(z['type']=='ScalingActive' and z['status']=='True' for z in x.get('status',{}).get('conditions',[]))
                row.update(status='PASS_HPA_GUARD' if active and not (a.work/'STOP.json').exists() else 'WAIT_HPA_METRICS',db=sample,hpa=x,
                    deployments={n:{'uid':z['metadata']['uid'],'replicas':z['spec']['replicas'],'status':z.get('status',{})} for n,z in deps.items()},
                    pods=[{'uid':z['metadata']['uid'],'node':z['spec'].get('nodeName'),'labels':z['metadata'].get('labels',{}),'deleting':z['metadata'].get('deletionTimestamp'),'status':z.get('status',{})} for z in pods],
                    resource_metrics=json.loads(client.call('get','--raw=/apis/metrics.k8s.io/v1beta1/namespaces/'+NS+'/pods')))
            except Exception as error:row['reason']=str(error) if type(error) is RuntimeError else type(error).__name__
            replace(a.work/'latest.json',row)
            fd=os.open(a.work/'platform-db-metrics.jsonl',os.O_WRONLY|os.O_APPEND|os.O_CREAT,0o600)
            with os.fdopen(fd,'ab') as f:f.write(canon(row)+b'\n')
            if (a.work/'STOP.json').exists():break
            time.sleep(max(0,min(10,end-time.monotonic())))
        print(json.dumps({'status':'COLLECTED_HPA_PLATFORM_DB_METRICS','raw_file':str(a.work/'platform-db-metrics.jsonl'),'success_inferred':False}));return 0
    except Exception as error:
        print(json.dumps({'status':'STOP_HPA_CONTROL','reason':str(error) if type(error) is RuntimeError else type(error).__name__,'automatic_retry':False,'raw_error_printed':False}));return 2

if __name__=='__main__':raise SystemExit(main())
