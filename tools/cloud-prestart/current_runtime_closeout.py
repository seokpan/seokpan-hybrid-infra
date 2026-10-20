"""Reuse accepted app evidence, sample DB once, sync only current monitoring."""
import datetime as dt
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import ssl
import subprocess
import sys
import time
import urllib.parse
import urllib.request

CID = '2ti8n9c2j2em1h6jbb34kc0dq95dv4fi'
NSUID = '01f37fd1-935d-4d39-8305-e8266312472e'
APPUID = 'a628168b-2a30-4de5-b5d3-809f74853d15'
MONUID = '3f813d76-ba51-43b4-85c4-0a3e27007ecb'
REV = '16fd14cd1c085a8314a5e2e18b89e2cd3b051626'
MON = 'seokpan-cloud-monitoring'
REPO = 'https://github.com/seokpan/seokpan-hybrid-gitops.git'
ROOT = Path.home()/'.local/share/seokpan-rosa-plan-inputs-5psj2l62'
RUN = ROOT/'controller-run-bundle-K2Tx7HgB'
END = dt.datetime.fromisoformat('2026-10-20T19:00:00+09:00').timestamp()
ITEMS = [('monitoring.coreos.com','ServiceMonitor','seokpan-backend'),
         ('monitoring.coreos.com','PrometheusRule','seokpan-app-availability'),
         ('networking.k8s.io','NetworkPolicy','seokpan-uwm-to-backend')]

def need(ok, code):
    if not ok: raise RuntimeError(code)

def read(path):
    p=Path(path)
    need(p.is_file() and not p.is_symlink() and p.stat().st_size<8388608,'BOUNDED_REGULAR_INPUT_REQUIRED')
    return json.loads(p.read_text(encoding='utf-8'))

def save(path,value):
    with open(path,'w',encoding='utf-8') as f: json.dump(value,f,ensure_ascii=False,indent=2)
    Path(path).chmod(0o600)

def module(name,path):
    s=importlib.util.spec_from_file_location(name,path)
    m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m

def source_ok(a):
    s=a['spec']
    need(a['metadata']['uid']==MONUID,'MONITORING_UID_CHANGED')
    need(s.get('project')==MON and s.get('source')=={'repoURL':REPO,'path':'platform/rosa-monitoring/app','targetRevision':REV}
         and not s.get('sources') and s.get('destination')=={'server':'https://kubernetes.default.svc','namespace':'seokpan-cloud'}
         and not s.get('syncPolicy',{}).get('automated'),'MONITORING_SOURCE_OR_SCOPE_CHANGED')
    need(not a['metadata'].get('deletionTimestamp'),'MONITORING_DELETING')

def healthy(a):
    s=a.get('status',{})
    return (s.get('sync',{}).get('revision')==REV and s.get('sync',{}).get('status')=='Synced'
            and s.get('health',{}).get('status')=='Healthy' and not a.get('operation'))

def main():
    global END
    os.umask(0o077)
    out=RUN/'current-monitoring-closeout-20261020';out.mkdir(mode=0o700,exist_ok=True)
    need(not out.is_symlink(),'REGULAR_RESULT_DIRECTORY_REQUIRED')
    lock=open(out/'run.lock','a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    r={'status':'PARTIAL_B_RUNTIME_CLOSEOUT','cluster_id':CID,'app_sync_repeated':False,
       'schema_or_account_changes':0,'new_pods':0,'monitoring_sync_submitted':False,
       'automatic_retry':False,'window_extended':False,'remaining':[]}
    def phase(name): print(json.dumps({'phase':name}),flush=True)
    dispatch=read(RUN/'current-fresh-app-run.json');c=read(dispatch['config']);work=Path(dispatch['work'])
    inputs=read(work/'new-inputs.json')['result']['inputs']
    need(inputs['cluster_id']==CID and inputs['namespace_uid']==NSUID,'CURRENT_BOUND_INPUT_REQUIRED')
    END=min(END,dt.datetime.fromisoformat(inputs['window']['not_after'].replace('Z','+00:00')).timestamp())
    oc=c['oc'];kube=c['personal_kubeconfig']
    def call(args,selected=kube,write=False):
        if write: need(time.time()<END,'EXECUTION_WINDOW_ENDED_NO_WRITE')
        p=subprocess.run([oc,'--kubeconfig',str(selected),'--request-timeout=10s',*args],capture_output=True,timeout=18)
        need(p.returncode==0,'CLUSTER_COMMAND_FAILED_PRIVATE_OUTPUT_WITHHELD')
        return p.stdout
    def get(kind,name,ns='openshift-gitops',selected=kube):
        return json.loads(call(['get',kind,name,'-n',ns,'-o','json'],selected))
    phase('REUSE_EXISTING_SUCCESS_AND_COLLECT_D_HANDOFF')
    release=read(work/'package/single.release.json')
    sql=read(work/'purpose-sql-result.json')
    need(sql.get('status')=='PASS_FRESH_TEST_PURPOSE_READS_NOT_APP_ACTIVATION','ACCEPTED_PURPOSE_SQL_RESULT_REQUIRED')
    fields=('account','status','tls','max_connections','effective_user_connection_limit')
    r['accounts']=[{k:a.get(k) for k in fields} for a in sql['accounts']]
    r['release_images']=release['images'];r['ready_receipt_present']=(work/'single-ready.json').is_file()
    r['deployments']={}
    for name in ('backend','frontend'):
        d=get('deployment',name,'seokpan-cloud')
        r['deployments'][name]={'replicas':d['spec'].get('replicas'),'ready':d.get('status',{}).get('readyReplicas',0),
            'containers':[{'name':x['name'],'image':x['image'],'resources':x.get('resources',{})} for x in d['spec']['template']['spec']['containers']]}
    pods=json.loads(call(['get','pods','-n','seokpan-cloud','-o','json']))['items']
    r['pods']=[{'name':p['metadata']['name'],'uid':p['metadata']['uid'],'node':p['spec'].get('nodeName'),
               'phase':p.get('status',{}).get('phase'),'terminating':bool(p['metadata'].get('deletionTimestamp'))}
              for p in pods if p['metadata'].get('labels',{}).get('app.kubernetes.io/name') in ('backend','frontend')]
    save(out/'D-runtime-handoff.json',r)
    phase('CONNECT_EXISTING_BACKEND_DB_OBSERVER')
    try:
        need(time.time()+25<END,'INSUFFICIENT_WINDOW_FOR_DB_SAMPLE')
        script=RUN/'prestart-closeout/pod_db_observer.py'
        hpa=RUN/'hpa-cutover/tools/hpa-load';measure=RUN/'app-measurement/tools/presentation-measurement'
        def sha(p): return hashlib.sha256(p.read_bytes().replace(b'\r\n',b'\n')).hexdigest()
        common=module('closeout_hpa_common',hpa/'common.py')
        app=get('application','seokpan-cloud-app')
        need(app['metadata']['uid']==APPUID,'APP_UID_CHANGED')
        binding={'cluster_id':CID,'namespace_uid':NSUID,'application_uid':APPUID,
                 'normalized_source_sha256':common.digest(common.normalized(app['spec']['source']))}
        backend=r['deployments']['backend']['containers']
        image=next(x['image'] for x in backend if x['name']=='backend')
        cfg={'binding':binding,'database_host':inputs['database_host'],'api':inputs['api_url'],
             'oc':oc,'personal_kubeconfig':kube,'schema_credentials_file':c['schema_credentials_file'],
             'backend_image':image,'hpa_source':str(hpa),'common_sha256':sha(hpa/'common.py'),
             'measurement_source':str(measure),'db_observation_sha256':sha(measure/'db_observation.py'),
             'interval_seconds':10,'duration_seconds':30,'not_after':inputs['window']['not_after']}
        save(out/'db-observer.json',cfg)
        if (out/'db-sample.json').exists(): sample=read(out/'db-sample.json')
        else:
            observer=module('existing_pod_db_observer',script);sample=observer.sample(cfg);save(out/'db-sample.json',sample)
        r['db_observer']=sample;r['db_observer_config']=str(out/'db-observer.json')
    except Exception as e:
        r['db_observer']={'status':'PENDING','reason':str(e) if type(e) is RuntimeError else type(e).__name__}
        r['remaining'].append('DB_OBSERVER_ACTUAL_ACCEPTANCE')
    phase('SYNC_CURRENT_MONITORING_ONLY')
    try:
        a=get('application',MON);source_ok(a)
        project=get('appproject',MON)['spec']
        need(project.get('sourceRepos')==[REPO] and project.get('destinations')==[{'server':'https://kubernetes.default.svc','namespace':'seokpan-cloud'}]
             and not project.get('clusterResourceWhitelist') and project.get('clusterResourceBlacklist')==[{'group':'*','kind':'*'}]
             and {(x['group'],x['kind']) for x in project.get('namespaceResourceWhitelist',[])}=={(g,k) for g,k,n in ITEMS},'MONITORING_PROJECT_SCOPE_CHANGED')
        if not healthy(a) and not a.get('operation'):
            need(time.time()+45<END,'INSUFFICIENT_WINDOW_FOR_MONITORING_SYNC')
            state=a.get('status',{}).get('operationState',{})
            need(not (state.get('phase') in ('Failed','Error') and state.get('syncResult',{}).get('revision')==REV),'PREVIOUS_MONITORING_FAILURE_REQUIRES_RECONCILIATION')
            marker=out/'monitoring-sync.attempted.json'
            need(not marker.exists(),'MONITORING_SYNC_OUTCOME_REQUIRES_READBACK_NO_RETRY')
            operation={'initiatedBy':{'username':'tjung03'},'retry':{'limit':0},'sync':{'revision':REV,'prune':False,
                'resources':[{'group':g,'kind':k,'namespace':'seokpan-cloud','name':n} for g,k,n in ITEMS],
                'syncOptions':['Prune=false','Delete=false','FailOnSharedResource=true']}}
            patch=[{'op':'test','path':'/metadata/uid','value':MONUID},
                   {'op':'test','path':'/metadata/resourceVersion','value':a['metadata']['resourceVersion']},
                   {'op':'test','path':'/spec','value':a['spec']},{'op':'add','path':'/operation','value':operation}]
            fd=os.open(marker,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
            with os.fdopen(fd,'w') as f: json.dump({'uid':MONUID,'revision':REV},f)
            call(['patch','application',MON,'-n','openshift-gitops','--type=json','-p',json.dumps(patch)],write=True)
            r['monitoring_sync_submitted']=True
        wait=min(time.time()+100,END)
        while not healthy(a) and time.time()<wait:
            time.sleep(min(3,max(0,wait-time.time())));a=get('application',MON);source_ok(a)
            need(a.get('status',{}).get('operationState',{}).get('phase') not in ('Failed','Error'),'MONITORING_SYNC_FAILED_NO_RETRY')
        need(healthy(a),'MONITORING_READY_PENDING_READBACK_ONLY')
        r['monitoring']={'status':'SYNCED_HEALTHY','revision':REV}
    except Exception as e:
        r['monitoring']={'status':'PENDING','reason':str(e) if type(e) is RuntimeError else type(e).__name__}
        r['remaining'].append('CURRENT_MONITORING_SYNC_ACCEPTANCE')
    phase('READ_CURRENT_BACKEND_UP_SAMPLE')
    try:
        need(r['monitoring']['status']=='SYNCED_HEALTHY' and time.time()+12<END,'METRIC_WINDOW_OR_SYNC_PENDING')
        management=ROOT/'held-restoration'/CID/'kubeconfig'
        need(management.is_file() and not management.is_symlink(),'EXISTING_SYSTEM_METRICS_CONTEXT_REQUIRED')
        route=get('route','thanos-querier','openshift-monitoring',management)
        host=route['spec']['host'];need(host.endswith('.'+inputs['ingress_domain']),'CURRENT_METRICS_ROUTE_REQUIRED')
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self,*args,**kwargs): return None
        opener=urllib.request.build_opener(NoRedirect(),urllib.request.HTTPSHandler(context=ssl.create_default_context()))
        token=call(['whoami','-t'],management).decode().strip()
        need(token and not any(x.isspace() for x in token),'EXISTING_METRICS_TOKEN_REQUIRED')
        query='up{namespace="seokpan-cloud",service="backend"}'
        until=min(time.time()+65,END-2);rows=[]
        while time.time()+8<until:
            request=urllib.request.Request('https://'+host+'/api/v1/query?'+urllib.parse.urlencode({'query':query}),headers={'Authorization':'Bearer '+token})
            with opener.open(request,timeout=7) as response: raw=response.read(1048577)
            need(len(raw)<=1048576,'BOUNDED_METRICS_RESPONSE_REQUIRED');value=json.loads(raw)
            need(value.get('status')=='success','PROMETHEUS_RESPONSE_REQUIRED');rows=value['data']['result']
            if rows and all(float(x['value'][1])==1 for x in rows): break
            time.sleep(min(10,max(0,until-time.time()-8)))
        token=None
        r['scrape']={'status':'PASS_CURRENT_BACKEND_UP' if rows and all(float(x['value'][1])==1 for x in rows) else 'NO_ACCEPTED_UP_SAMPLE',
                     'query':query,'rows':rows,'metrics_read_context':'EXISTING_MANAGEMENT_CONTEXT_READ_ONLY'}
        if r['scrape']['status']!='PASS_CURRENT_BACKEND_UP': r['remaining'].append('CURRENT_SCRAPE_UP_SAMPLE')
    except Exception as e:
        r['scrape']={'status':'PENDING','reason':str(e) if type(e) is RuntimeError else type(e).__name__}
        r['remaining'].append('CURRENT_SCRAPE_UP_SAMPLE')
    r['status']='PASS_B_RUNTIME_CLOSEOUT_NOT_LOAD_TEST' if not r['remaining'] else 'PARTIAL_B_RUNTIME_CLOSEOUT'
    r['remaining_seconds']=max(0,int(END-time.time()));r['load_test_started']=False
    save(out/'D-runtime-handoff.json',r);save(out/'receipt.json',r)
    print(json.dumps(r,ensure_ascii=False,indent=2));print('Private closeout directory: '+str(out))
    return 0 if not r['remaining'] else 2

if __name__=='__main__':
    try: raise SystemExit(main())
    except Exception as e:
        print(json.dumps({'status':'STOP_B_RUNTIME_CLOSEOUT','reason':str(e) if type(e) is RuntimeError else type(e).__name__,
                          'automatic_retry':False,'raw_logs_printed':False}));raise SystemExit(2)
