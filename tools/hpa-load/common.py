"""Bounded temporary HPA contract. Import/default validation never calls an API."""
import datetime as dt
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import time

NS, APP, ARGO = 'seokpan-cloud', 'seokpan-cloud-app', 'openshift-gitops'
OLD = '2tejeb2farjaaoef1e74asdf6amrtpu3'
UUID = r'[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}'
PHASES = [(0,120),(10,180),(50,300),(100,300),(200,300),(300,120),(100,180),(0,600)]

def need(ok, reason):
    if not ok: raise RuntimeError(reason)

def canon(value): return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
def digest(value): return hashlib.sha256(canon(value)).hexdigest()
def stamp(value):
    t=dt.datetime.fromisoformat(value.replace('Z','+00:00'));need(t.tzinfo is not None,'TIMEZONE_REQUIRED');return t.timestamp()
def utc(): return dt.datetime.now(dt.timezone.utc).isoformat()
def read(path):
    p=Path(path);need(p.is_file() and not p.is_symlink() and p.stat().st_size<16*1024*1024,'BOUNDED_REGULAR_FILE_REQUIRED')
    return json.loads(p.read_text(encoding='utf-8'))
def save(path,value):
    fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    with os.fdopen(fd,'wb') as f:f.write(canon(value)+b'\n')
def replace(path,value):
    p=Path(path);tmp=p.with_name(p.name+'.'+str(os.getpid())+'.tmp');save(tmp,value);os.replace(tmp,p)
def normalized(source):
    s=json.loads(json.dumps(source))
    for p in s.get('kustomize',{}).get('patches',[]):
        if p.get('target',{}).get('group')=='':p['target'].pop('group')
        if isinstance(p.get('patch'),str):p['patch']=json.loads(p['patch'])
    return s
def integer(value,low,high,reason):need(type(value) is int and low<=value<=high,reason);return value
def argv(value):need(isinstance(value,list) and value and all(isinstance(x,str) and x and '\n' not in x for x in value),'ARGV_ARRAY_REQUIRED');return value
def missing(value,path=''):
    if value is None:return [path]
    if isinstance(value,dict):return [x for k,v in value.items() if k!='prometheus' for x in missing(v,path+'.'+k if path else k)]
    if isinstance(value,list):return [x for i,v in enumerate(value) for x in missing(v,path+'['+str(i)+']')]
    return []
def subset(wanted,actual):
    if isinstance(wanted,dict):return isinstance(actual,dict) and all(k in actual and subset(v,actual[k]) for k,v in wanted.items())
    if isinstance(wanted,list):return isinstance(actual,list) and len(wanted)==len(actual) and all(subset(a,b) for a,b in zip(wanted,actual))
    return wanted==actual

def validate(c):
    need(c.get('schema')==1 and re.fullmatch(r'[a-z0-9][a-z0-9-]{2,45}',c.get('run_id') or ''),'RUN_SCHEMA_REQUIRED')
    b=c['binding'];need(re.fullmatch(r'[a-z0-9]{10,64}',b.get('cluster_id') or '') and b['cluster_id']!=OLD,'NEW_CLUSTER_REQUIRED')
    for k in ('namespace_uid','application_uid'):need(re.fullmatch(UUID,b.get(k) or ''),'ACTUAL_UID_REQUIRED')
    need(re.fullmatch(r'[0-9a-f]{64}',b.get('normalized_source_sha256') or ''),'ACCEPTED_SOURCE_REQUIRED')
    from urllib.parse import urlsplit
    api=urlsplit(c['api']);origin=urlsplit(c['origin'])
    need(api.scheme=='https' and api.port==6443 and api.hostname.startswith('api.') and not api.username and not api.password,'NEW_TRUSTED_API_REQUIRED')
    need(origin.scheme=='https' and origin.hostname and origin.port is None and not origin.username and not origin.password
         and origin.path in ('','/') and not origin.query and not origin.fragment and origin.hostname.endswith('.openshiftapps.com')
         and origin.hostname=='seokpan.apps.'+api.hostname[4:] and '8zrv.' not in origin.hostname,'BOUND_NEW_ORIGIN_REQUIRED')
    need(c['actor']=='tjung03' and c['namespace']==NS,'SCOPED_ACTOR_REQUIRED')
    lo=integer(c['min_replicas'],1,10,'MIN_REPLICA_REQUIRED');hi=integer(c['max_replicas'],lo,10,'MAX_REPLICA_REQUIRED')
    q=c['capacity'];need(q['evidence_ref'] and q['node_capacity_ref'],'CURRENT_CAPACITY_REFERENCES_REQUIRED')
    g=integer(q['global_limit'],1,100000,'GLOBAL_LIMIT_REQUIRED');r=integer(q['reserved'],0,g,'RESERVATION_REQUIRED')
    proc=integer(q['processes'],1,32,'PROCESS_COUNT_REQUIRED');size=integer(q['pool_size'],1,1000,'POOL_REQUIRED');overflow=integer(q['overflow'],0,1000,'OVERFLOW_REQUIRED')
    surge=integer(q['surge_allowance'],0,20,'SURGE_BOUND_REQUIRED');term=integer(q['terminating_allowance'],0,20,'TERMINATING_BOUND_REQUIRED')
    per=proc*(size+overflow);peak=hi+surge+term
    need(peak<=integer(q['node_backend_capacity'],1,50,'NODE_CAPACITY_REQUIRED') and peak*per*2+r<=g,'NODE_OR_GLOBAL_CONNECTION_BUDGET_EXCEEDED')
    for name in ('identity_svc','game_svc'):
        a=q['accounts'][name];user=integer(a['max_user_connections'],0,g,'ACCOUNT_LIMIT_REQUIRED');global_user=integer(a['global_max_user_connections'],0,g,'GLOBAL_USER_LIMIT_REQUIRED');limit=user or global_user
        need(a['evidence_ref'] and (not limit or peak*per<=limit),'ACCOUNT_CONNECTION_BUDGET_EXCEEDED')
    need(stamp(c['window']['not_after'])-stamp(c['window']['not_before'])>=sum(t for _,t in PHASES)+300,'WINDOW_MUST_INCLUDE_35_MINUTES_AND_SETUP_CLEANUP')
    need(c['normal_business_ref'] and c['prior_comparison_finished_ref'],'NORMAL_AND_FIXED_COMPARISON_EVIDENCE_REQUIRED')
    need(c['expected_images'].keys()=={'backend','frontend'} and all(isinstance(x,str) and re.fullmatch(r'[^\s]+@sha256:[a-f0-9]{64}',x) for x in c['expected_images'].values()),'PINNED_IMAGES_REQUIRED')
    argv(c['db_probe_argv']);argv(c['generator_guard_argv'])
    return {'peak_backend_pods_bound':peak,'declared_connections':peak*per*2,'required_with_reservation':peak*per*2+r,
            'remaining':g-r-peak*per*2,'not_observed_peak':True}

def lease_spec(spec):
    s=json.loads(json.dumps(spec));s.setdefault('ignoreDifferences',[]).append({'group':'apps','kind':'Deployment','name':'backend','namespace':NS,'jsonPointers':['/spec/replicas']})
    options=s.setdefault('syncPolicy',{}).setdefault('syncOptions',[])
    need(not any(x.startswith('RespectIgnoreDifferences=') for x in options),'EXISTING_IGNORE_POLICY_REVIEW_REQUIRED')
    options.append('RespectIgnoreDifferences=true');return s

def hpa(c):
    return {'apiVersion':'autoscaling/v2','kind':'HorizontalPodAutoscaler','metadata':{'namespace':NS,'name':'b-hpa-'+c['run_id'],'labels':{'seokpan.io/measurement-run':c['run_id']}},
        'spec':{'scaleTargetRef':{'apiVersion':'apps/v1','kind':'Deployment','name':'backend'},'minReplicas':c['min_replicas'],'maxReplicas':c['max_replicas'],
          'metrics':[{'type':'Resource','resource':{'name':'cpu','target':{'type':'Utilization','averageUtilization':60}}}],
          'behavior':{'scaleUp':{'stabilizationWindowSeconds':0,'selectPolicy':'Max','policies':[{'type':'Pods','value':2,'periodSeconds':60},{'type':'Percent','value':100,'periodSeconds':60}]},
                      'scaleDown':{'stabilizationWindowSeconds':300,'policies':[{'type':'Pods','value':1,'periodSeconds':60}]}}}}

class Client:
    def __init__(self,c,runner=subprocess.run):self.c=c;self.runner=runner
    def call(self,*args,payload=None):
        p=self.runner([self.c['oc'],'--kubeconfig='+self.c['personal_kubeconfig'],'--request-timeout=15s',*args],input=payload,capture_output=True,timeout=25)
        need(p.returncode==0,'API_RESULT_UNKNOWN_RECONCILE_NO_AUTOMATIC_RETRY');return p.stdout
    def get(self,kind,name=None,ns=NS):return json.loads(self.call('get',kind,*([name] if name else []),'-n',ns,'-o','json'))
    def identity(self):
        cfg=json.loads(self.call('config','view','--minify','-o','json'))['clusters'][0]['cluster']
        need(cfg['server'].rstrip('/')==self.c['api'].rstrip('/') and not cfg.get('insecure-skip-tls-verify'),'WRONG_OR_UNTRUSTED_API')
        need(self.call('whoami').strip()==b'tjung03','PERSONAL_B_CONTEXT_REQUIRED')
        need(self.get('namespace',NS)['metadata']['uid']==self.c['binding']['namespace_uid'],'NAMESPACE_UID_CHANGED')
    def patch(self,kind,name,obj,patch,ns=NS):
        ops=[{'op':'test','path':'/metadata/uid','value':obj['metadata']['uid']},{'op':'test','path':'/metadata/resourceVersion','value':obj['metadata']['resourceVersion']},*patch]
        return self.call('patch',kind,name,'-n',ns,'--type=json','-p',canon(ops).decode())

def db_probe(c,runner=subprocess.run):
    p=runner(argv(c['db_probe_argv']),capture_output=True,timeout=20)
    need(p.returncode==0 and len(p.stdout)<16384,'CURRENT_DB_PROBE_FAILED')
    x=json.loads(p.stdout);need(x['status']=='PASS_CURRENT_DB_SAMPLE' and x['binding']==c['binding'] and 0<=time.time()-stamp(x['utc'])<=30,'CURRENT_BOUND_DB_SAMPLE_REQUIRED')
    q=c['capacity'];need(x['global_limit']==q['global_limit'] and x['tls'] is True,'DB_LIMIT_OR_TLS_CHANGED')
    need(all(x.get('global_max_user_connections')==a['global_max_user_connections'] for a in q['accounts'].values()),'GLOBAL_USER_CONNECTION_LIMIT_CHANGED')
    need(x['connections_total']<q['global_limit']-q['reserved'],'DB_RESERVED_HEADROOM_REACHED')
    for name,a in q['accounts'].items():
        cap=a['max_user_connections'] or a['global_max_user_connections'];v=x['per_account'][name]
        need(type(v) is int and v>=0 and (not cap or v<cap),'ACCOUNT_HEADROOM_OR_VISIBILITY_FAILED')
    return x
