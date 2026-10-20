"""Prepare one bounded shared-backend presentation profile. Never starts load/HPA."""
import argparse,copy,datetime as dt,hashlib,importlib.util,json,os,subprocess,sys,time
try:import fcntl
except ImportError:fcntl=None
from pathlib import Path

ROOT=Path.home()/'.local/share/seokpan-rosa-plan-inputs-5psj2l62'
RUN=ROOT/'controller-run-bundle-K2Tx7HgB'
CID='2ti8n9c2j2em1h6jbb34kc0dq95dv4fi'
APPUID='a628168b-2a30-4de5-b5d3-809f74853d15'
NSUID='01f37fd1-935d-4d39-8305-e8266312472e'
ORIGINAL_SOURCE='848b10550cc1c5c78f4b1332b80d17f90d79b4a80d79fde563231d00befa3bda'
RESOURCE={'requests':{'cpu':'100m','memory':'256Mi'},'limits':{'cpu':'500m'}}

def need(ok,code):
    if not ok:raise RuntimeError(code)

def read(p):
    p=Path(p);need(p.is_file() and not p.is_symlink() and p.stat().st_size<16777216,'REGULAR_BOUNDED_INPUT_REQUIRED')
    return json.loads(p.read_text())

def save(p,v):
    need(not p.is_symlink(),'REGULAR_OUTPUT_REQUIRED')
    with open(p,'w') as f:json.dump(v,f,indent=2)
    p.chmod(0o600)

def sha(v):return hashlib.sha256(json.dumps(v,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()

def normalized(s):
    s=copy.deepcopy(s)
    for p in s.get('kustomize',{}).get('patches',[]):
        if p.get('target',{}).get('group')=='':p['target'].pop('group')
        if isinstance(p.get('patch'),str):p['patch']=json.loads(p['patch'])
    return s

def profile_source(s):
    s=copy.deepcopy(s)
    patch={'apiVersion':'apps/v1','kind':'Deployment','metadata':{'name':'backend'},'spec':{
        'replicas':2,'template':{'spec':{'containers':[{'name':'backend','resources':copy.deepcopy(RESOURCE)}]}}}}
    s.setdefault('kustomize',{}).setdefault('patches',[]).append({'target':{'group':'apps','version':'v1','kind':'Deployment','name':'backend'},'patch':json.dumps(patch,separators=(',',':'))})
    return s

def role_pair(name,namespace,rules):
    meta={'name':name,'namespace':namespace,'annotations':{'seokpan.io/presentation-owner':CID}}
    return [{'apiVersion':'rbac.authorization.k8s.io/v1','kind':'Role','metadata':copy.deepcopy(meta),'rules':rules},
        {'apiVersion':'rbac.authorization.k8s.io/v1','kind':'RoleBinding','metadata':copy.deepcopy(meta),
         'roleRef':{'apiGroup':'rbac.authorization.k8s.io','kind':'Role','name':name},
         'subjects':[{'apiGroup':'rbac.authorization.k8s.io','kind':'User','name':'tjung03'}]}]

def module(name,path):
    spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

def adopt_observer(source):
    source=Path(source);target=RUN/'prestart-closeout/pod_db_observer.py'
    need(source.is_file() and not source.is_symlink() and not target.is_symlink(),'REGULAR_OBSERVER_SOURCE_REQUIRED')
    raw=source.read_bytes().replace(b'\r\n',b'\n');old=target.read_bytes().replace(b'\r\n',b'\n')
    digest=lambda x:hashlib.sha256(x).hexdigest()
    need(digest(old) in ('0609bcf7f62ee0e16b579fef99f0217ffd6c593b547058fc9af85fdb8844c619',digest(raw)),'UNKNOWN_OBSERVER_PRESERVED')
    if old!=raw:
        backup=target.with_name('pod_db_observer.before-presentation.py')
        need(not backup.exists(),'EXISTING_OBSERVER_BACKUP_PRESERVED')
        backup.write_bytes(old);backup.chmod(0o600)
        temporary=target.with_name('pod_db_observer.presentation.tmp');need(not temporary.exists(),'EXISTING_OBSERVER_TEMP_PRESERVED')
        with open(temporary,'xb') as f:f.write(raw)
        temporary.chmod(0o600);os.replace(temporary,target)
    return target

def start_observers(out,r):
    need(dt.datetime.now(dt.timezone.utc).timestamp()+600<dt.datetime.fromisoformat(r['window']['not_after']).timestamp(),'SUFFICIENT_CURRENT_TEST_WINDOW_REQUIRED')
    for name,argv in r['observer_commands'].items():
        marker=out/(name+'.started.json')
        if marker.exists():continue
        save(marker,{'utc':dt.datetime.now(dt.timezone.utc).isoformat(),'argv':argv,'outcome':'START_ATTEMPTED_DO_NOT_RETRY'})
        with open(out/(name+'.private.log'),'xb') as f:
            process=subprocess.Popen(argv,stdin=subprocess.DEVNULL,stdout=f,stderr=subprocess.STDOUT,start_new_session=True)
        save(out/(name+'.process.json'),{'pid':process.pid,'read_only':True,'readiness':'PROCESS_STARTED_NOT_SAMPLE_ACCEPTANCE'})
    r=copy.deepcopy(r);r['observer_start_requested']=True
    return r

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--execute',action='store_true');parser.add_argument('--observer-source',type=Path);parser.add_argument('--start-observers',action='store_true');a=parser.parse_args()
    need(a.execute,'EXPLICIT_PROFILE_PREPARATION_REQUIRED');need(fcntl is not None,'CONTROLLER_LINUX_REQUIRED');os.umask(0o077)
    out=RUN/'presentation-final-20261020';out.mkdir(mode=0o700,exist_ok=True)
    need(not out.is_symlink(),'PRIVATE_RESULT_DIRECTORY_REQUIRED')
    lock=open(out/'run.lock','a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    receipt=out/'receipt.json'
    if receipt.exists():
        previous=read(receipt)
        if previous.get('status')=='PASS_SELECTED_PRESENTATION_INPUTS_READY_NOT_TESTED':
            if a.start_observers:previous=start_observers(out,previous)
            previous['existing_preparation_reused']=True
            previous['window_expired']=dt.datetime.now(dt.timezone.utc)>dt.datetime.fromisoformat(previous['window']['not_after'])
            print(json.dumps(previous,indent=2));print('Private presentation preparation directory: '+str(out));lock.close();return 0
    r={'status':'STOP_PRESENTATION_PREPARATION','cluster_id':CID,'load_started':False,'hpa_started':False,
       'sql_calls':0,'account_changes':0,'prepared_profile':{'backend':2,'frontend':1,'backend_resources':RESOURCE},
       'full_account_db_visibility':'NOT_ACCEPTED','automatic_retry':False}
    def phase(v):print(json.dumps({'phase':v}),flush=True)
    try:
        need(a.observer_source is not None,'REVIEWED_OBSERVER_COMPANION_REQUIRED')
        observer=adopt_observer(a.observer_source)
        dispatch=read(RUN/'current-fresh-app-run.json');cfg=read(dispatch['config']);work=Path(dispatch['work'])
        inputs=read(work/'new-inputs.json')['result']['inputs'];need(inputs['cluster_id']==CID and inputs['namespace_uid']==NSUID,'CURRENT_INPUT_BINDING_REQUIRED')
        management=ROOT/'held-restoration'/CID/'kubeconfig'
        oc=cfg['oc'];personal=cfg['personal_kubeconfig']
        def call(args,admin=False,payload=None,optional=False):
            p=subprocess.run([oc,'--kubeconfig',str(management if admin else personal),'--request-timeout=10s',*args],input=payload,capture_output=True,timeout=18)
            if optional:return p
            need(p.returncode==0,'CLUSTER_COMMAND_FAILED_PRIVATE_LOG_WITHHELD');return p.stdout
        def get(kind,name,ns,admin=False,optional=False):
            p=call(['get',kind,name,*(['-n',ns] if ns else []),'--ignore-not-found=true','-o','json'],admin,optional=optional)
            if optional:return p
            return json.loads(p) if p.strip() else None
        local=json.loads(call(['config','view','--minify','-o','json'],True))['clusters'][0]['cluster']
        need(local['server'].rstrip('/')==inputs['api_url'].rstrip('/') and not local.get('insecure-skip-tls-verify'),'SAME_TRUSTED_MANAGEMENT_API_REQUIRED')
        need(call(['whoami']).strip()==b'tjung03','PERSONAL_TJUNG03_REQUIRED')
        need(get('namespace','seokpan-cloud','',True)['metadata']['uid']==NSUID,'CURRENT_NAMESPACE_UID_REQUIRED')
        phase('REPAIR_EXACT_PRESENTATION_READ_ACCESS')
        def ensure(objects):
            for wanted in objects:
                meta=wanted['metadata'];live=get(wanted['kind'],meta['name'],meta['namespace'],True)
                if live is not None:
                    need(live['metadata'].get('annotations',{}).get('seokpan.io/presentation-owner')==CID,'EXISTING_ROLE_OWNER_MISMATCH')
                    keys=('rules',) if wanted['kind']=='Role' else ('roleRef','subjects')
                    need(all(live.get(k)==wanted[k] for k in keys),'EXISTING_ROLE_SCOPE_MISMATCH')
                    continue
                marker=out/(wanted['kind']+'-'+meta['name']+'.attempted.json')
                need(not marker.exists(),'PERMISSION_WRITE_OUTCOME_REQUIRES_RECONCILIATION')
                save(marker,{'namespace':meta['namespace'],'name':meta['name'],'scope':wanted})
                call(['create','-f','-','-o','name'],True,json.dumps(wanted).encode())
        ensure(role_pair('b-presentation-machineautoscaler-read','openshift-machine-api',[
            {'apiGroups':['autoscaling.openshift.io'],'resources':['machineautoscalers'],'verbs':['get','list','watch']}]))
        values=json.loads(call(['get','machineautoscalers.autoscaling.openshift.io','-n','openshift-machine-api','-o','json']))['items']
        r['machine_autoscalers']=[{'name':x['metadata']['name'],'min':x['spec']['minReplicas'],'max':x['spec']['maxReplicas']} for x in values]
        need(all(x['min']==x['max'] for x in r['machine_autoscalers']),'DYNAMIC_MACHINE_AUTOSCALING_PRESENT_NO_PROFILE_APPLY')
        access=call(['auth','can-i','patch','applications.argoproj.io/seokpan-cloud-app','-n','openshift-gitops'],optional=True)
        if access.stdout.strip()==b'no':
            ensure(role_pair('b-presentation-owned-application','openshift-gitops',[
                {'apiGroups':['argoproj.io'],'resources':['applications'],'resourceNames':['seokpan-cloud-app'],'verbs':['get','patch']}]))
        else:need(access.returncode==0 and access.stdout.strip()==b'yes','APPLICATION_ACCESS_REVIEW_FAILED')
        phase('PREPARE_FIXED_REQUESTS_AND_TWO_BACKEND_PROFILE')
        app=get('application','seokpan-cloud-app','openshift-gitops')
        need(app['metadata']['uid']==APPUID and not app['spec'].get('syncPolicy',{}).get('automated') and not app['metadata'].get('ownerReferences')
             and not app['metadata'].get('deletionTimestamp') and app['metadata'].get('annotations',{}).get('seokpan.io/bootstrap-owner')=='cloud-app-held'
             and not app['spec'].get('sources'),'OWNED_MANUAL_APPLICATION_REQUIRED')
        need(app['spec']['project']=='seokpan-cloud-app' and app['spec']['destination']=={'server':'https://kubernetes.default.svc','namespace':'seokpan-cloud'},'APP_SCOPE_CHANGED')
        proposal=out/'profile-source.json'
        if proposal.exists():desired=read(proposal)
        else:
            need(sha(normalized(app['spec']['source']))==ORIGINAL_SOURCE,'ORIGINAL_SINGLE_SOURCE_CHANGED')
            save(out/'before-profile-application.json',app)
            save(out/'before-profile-frontend.json',get('deployment','frontend','seokpan-cloud'))
            desired=profile_source(app['spec']['source']);save(proposal,desired)
        if normalized(app['spec']['source'])!=normalized(desired):
            before=read(out/'before-profile-application.json')
            need(app['spec']==before['spec'] and not app.get('operation'),'PROFILE_PARTIAL_STATE_REQUIRES_RECONCILIATION')
            marker=out/'profile-sync.attempted.json';need(not marker.exists(),'PROFILE_SYNC_ALREADY_ATTEMPTED_NO_RESUBMISSION')
            operation={'initiatedBy':{'username':'tjung03'},'retry':{'limit':0},'sync':{'revision':desired['targetRevision'],'prune':False,
                'resources':[{'group':'apps','kind':'Deployment','namespace':'seokpan-cloud','name':'backend'}],
                'syncOptions':['Prune=false','Delete=false','FailOnSharedResource=true']}}
            patch=[{'op':'test','path':'/metadata/uid','value':APPUID},{'op':'test','path':'/metadata/resourceVersion','value':app['metadata']['resourceVersion']},
                {'op':'test','path':'/spec','value':app['spec']},{'op':'replace','path':'/spec/source','value':desired},{'op':'add','path':'/operation','value':operation}]
            save(marker,{'source_before':sha(app['spec']['source']),'source_after':sha(desired),'preparation_not_comparison_roll':True})
            call(['patch','application','seokpan-cloud-app','-n','openshift-gitops','--type=json','-p',json.dumps(patch)])
        wait=time.monotonic()+180
        while True:
            app=get('application','seokpan-cloud-app','openshift-gitops')
            deps={n:get('deployment',n,'seokpan-cloud') for n in ('backend','frontend')}
            pod_values=json.loads(call(['get','pods','-n','seokpan-cloud','-o','json']))['items']
            pod_ready=all(len([x for x in pod_values if x['metadata'].get('labels',{}).get('app.kubernetes.io/name')==n])==wanted
                and all(not x['metadata'].get('deletionTimestamp') and any(z.get('type')=='Ready' and z.get('status')=='True' for z in x.get('status',{}).get('conditions',[]))
                    for x in pod_values if x['metadata'].get('labels',{}).get('app.kubernetes.io/name')==n) for n,wanted in (('backend',2),('frontend',1)))
            ready=(not app.get('operation') and app.get('status',{}).get('sync',{}).get('status')=='Synced' and app.get('status',{}).get('health',{}).get('status')=='Healthy'
                and pod_ready and all(d['status'].get('observedGeneration')==d['metadata']['generation'] and all(d['status'].get(k,0)==wanted for k in ('replicas','readyReplicas','updatedReplicas','availableReplicas')) for n,d in deps.items() for wanted in (2 if n=='backend' else 1,)))
            if ready:break
            need(time.monotonic()<wait,'PROFILE_READY_PENDING_READBACK_ONLY_NO_RESUBMISSION')
            need(app.get('status',{}).get('operationState',{}).get('phase') not in ('Failed','Error'),'PROFILE_SYNC_FAILED_NO_RESUBMISSION')
            print(json.dumps({'phase':'WAIT_PROFILE_READY','backend_ready':deps['backend'].get('status',{}).get('readyReplicas',0)}),flush=True);time.sleep(5)
        need(sha(normalized(app['spec']['source']))==sha(normalized(desired)),'PREPARED_SOURCE_CHANGED')
        need(deps['backend']['spec']['template']['spec']['containers'][0]['resources']==RESOURCE,'CURRENT_BACKEND_RESOURCES_CHANGED')
        fe=read(out/'before-profile-frontend.json')
        need(deps['frontend']['metadata']['uid']==fe['metadata']['uid'] and deps['frontend']['spec']['template']==fe['spec']['template'],'FRONTEND_TEMPLATE_CHANGED')
        images={n:next(x['image'] for x in d['spec']['template']['spec']['containers'] if x['name']==n) for n,d in deps.items()}
        accepted=read(RUN/'current-monitoring-closeout-20261020/D-runtime-handoff.json')
        need(all(images[n]==next(x['image'] for x in accepted['deployments'][n]['containers'] if x['name']==n) for n in images),'APPROVED_IMAGES_CHANGED')
        phase('BIND_SELECTED_BASELINE_AND_ROLLING_INPUTS')
        now=dt.datetime.now(dt.timezone.utc);window={'not_before':now.isoformat(),'not_after':(now+dt.timedelta(minutes=30)).isoformat()}
        runid='b-presentation-'+now.strftime('%Y%m%d-%H%M%S')
        binding={'cluster_id':CID,'namespace_uid':NSUID,'application_uid':APPUID,'source_sha256':sha(desired),'normalized_source_sha256':sha(normalized(desired))}
        if (out/'baseline.json').exists():
            earlier=read(out/'baseline.json');need(earlier['binding']==binding,'PREPARED_MEASUREMENT_BINDING_CHANGED')
            runid=earlier['run_id'].removesuffix('-baseline');window=earlier['window']
            need(now+dt.timedelta(minutes=10)<dt.datetime.fromisoformat(window['not_after']),'EXISTING_TEST_WINDOW_EXPIRED_NO_AUTOMATIC_EXTENSION')
        release=read(work/'package/single.release.json')
        profile={'schema':1,'target_origin':'https://seokpan.'+inputs['ingress_domain'],'profile':{'clients':6,'warmup_seconds':60,'measurement_seconds':180},
            'release':{'app_source_sha':release['source']['app_sha'],'backend_digest':images['backend'].split('@')[1],'frontend_digest':images['frontend'].split('@')[1]},
            'runtime':{'replicas':{'backend':2,'frontend':1},'pool_size':2,'max_overflow':0,'pool_timeout_seconds':5,'config_revision':release['revisions']['config'],'schema_revision':release['revisions']['schema']},
            'evidence':{'normal_business_ref':'https://github.com/seokpan/seokpan-hybrid-infra/pull/99#issuecomment-6288244919',
                'precheck_scope':'SIGNUP_LOGIN_ROOM_CREATION_ONLY_NOT_FULL_GAME_ACCEPTANCE','release_binding_ref':release['release_id'],
                'run_window_ref':'USER_MINIMUM_PRESENTATION_CONTINUE_AFTER_1900_MAXIMUM_30_MINUTES','resource_preparation_ref':str(out/'receipt.json')},'window':window,'binding':binding}
        plans={}
        for scenario in ('baseline','rolling'):
            plans[scenario]=dict(profile,run_id=runid+'-'+scenario,scenario=scenario,extra_recovery_seconds=30 if scenario=='rolling' else 0)
            save(out/(scenario+'.json'),plans[scenario])
        fixed=RUN/'measurement-bindings';fixed.mkdir(mode=0o700,exist_ok=True)
        rolling={'actor':'tjung03','namespace':'seokpan-cloud','application':'seokpan-cloud-app','oc':oc,'personal_kubeconfig':personal,
            'api':inputs['api_url'],'namespace_uid':NSUID,'application_uid':APPUID,'run_id':runid+'-rolling',
            'source_sha256':binding['source_sha256'],'normalized_source_sha256':binding['normalized_source_sha256'],'expected_images':images,
            'expected_replicas':{'backend':2,'frontend':1},**window}
        if (fixed/'rolling-config.json').exists():
            need(read(fixed/'rolling-config.json')==rolling,'OTHER_ROLLING_CONFIGURATION_PRESERVED')
        else:save(fixed/'rolling-config.json',rolling)
        measure=RUN/'app-measurement/tools/presentation-measurement'
        command=[sys.executable,'-I',str(measure/'rolling_control.py'),'prepare','--config',str(fixed/'rolling-config.json'),'--work',str(fixed/'rolling-work'),'--execute']
        if (fixed/'rolling-work/before.json').exists():
            need(read(fixed/'rolling-work/before.json')['config_sha256']==sha(rolling),'OTHER_PREPARED_ROLLING_PLAN_PRESERVED')
        else:
            p=subprocess.run(command,capture_output=True,text=True,timeout=90)
            save(out/'rolling-prepare-result.json',{'exit_code':p.returncode,'stdout':p.stdout[-12000:],'stderr':p.stderr[-12000:]})
            need(p.returncode==0,'ROLLING_PREPARE_FAILED_PRIVATE_OUTPUT_WITHHELD')
        settings=read(RUN/'prestart-closeout/handoff-settings.json');share=Path(settings['handoff_root'])/runid
        need(share.parent.is_dir() and not share.parent.is_symlink(),'EXISTING_HANDOFF_ROOT_REQUIRED')
        H=module('existing_handoff',RUN/'prestart-closeout/readonly_handoff.py');H.directory(share,settings['reader'])
        for name,value in plans.items():H.publish_file(share/(name+'.json'),H.canonical(value)+b'\n',settings['reader'],True)
        H.publish_file(share/'rolling-config.json',H.canonical(rolling)+b'\n',settings['reader'],True)
        after=read(fixed/'rolling-work/before.json')['source_after']
        db=read(RUN/'current-monitoring-closeout-20261020/db-observer-total-only.json');db.update(binding=binding,duration_seconds=900,interval_seconds=10,not_after=window['not_after'],
            allowed_normalized_source_sha256=[binding['normalized_source_sha256'],sha(normalized(after))])
        save(out/'db-observer-total-only.json',db)
        commands={
            'pod-observer':[sys.executable,'-I',str(measure/'rolling_control.py'),'observe','--config',str(fixed/'rolling-config.json'),'--work',str(fixed/'rolling-work'),'--seconds','900','--execute'],
            'db-observer':[sys.executable,'-I',str(observer),'sample','--config',str(out/'db-observer-total-only.json'),'--out',str(out/'db-total-timeline.jsonl'),'--execute']}
        r.update(status='PASS_SELECTED_PRESENTATION_INPUTS_READY_NOT_TESTED',binding=binding,window=window,measurement_bundle=plans,
            rolling_config=str(fixed/'rolling-config.json'),rolling_work=str(fixed/'rolling-work'),shared_directory=str(share),
            remaining=['ACTUAL_BASELINE_AND_ROLLING_RESULTS','DB_FULL_ACCOUNT_VISIBILITY_FOR_HPA','HPA_NOT_STARTED'],
            preparation_roll_not_measured=True,comparison_configuration='FIXED_PROFILE_NOT_PERFORMANCE_OPTIMIZED',observer_commands=commands,
            observer_start_argv=[sys.executable,'-I',str(Path(__file__).resolve()),'--execute','--start-observers'],
            hpa_config_published=False,read_only_shared_inputs_only=True)
        save(out/'receipt.json',r)
        if a.start_observers:r=start_observers(out,r)
    except Exception as e:r['reason']=str(e) if type(e) is RuntimeError else type(e).__name__
    save(out/'receipt.json',r);print(json.dumps(r,indent=2));print('Private presentation preparation directory: '+str(out))
    lock.close()
    return 0 if r['status'].startswith('PASS_') else 2

if __name__=='__main__':
    try:raise SystemExit(main())
    except Exception as e:
        print(json.dumps({'status':'STOP_PRESENTATION_LINK_SETUP','reason':str(e) if type(e) is RuntimeError else type(e).__name__,'raw_logs_printed':False}));raise SystemExit(2)
