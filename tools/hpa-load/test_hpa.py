import copy
import io
import json
from contextlib import redirect_stdout
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
import common as C
import control
import db_probe as DB
import prometheus as P
import results


def config():
    return {'schema':1,'run_id':'hpa-synthetic','actor':'tjung03','namespace':C.NS,'oc':'oc','personal_kubeconfig':'/private/k',
      'api':'https://api.new.p1.openshiftapps.com:6443','origin':'https://seokpan.apps.new.p1.openshiftapps.com',
      'binding':{'cluster_id':'newcluster012345','namespace_uid':'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee','application_uid':'11111111-2222-3333-4444-555555555555','normalized_source_sha256':C.digest({})},
      'window':{'not_before':'2026-10-19T00:00:00Z','not_after':'2027-10-19T03:00:00Z'},'min_replicas':2,'max_replicas':10,
      'capacity':{'evidence_ref':'current-sql','node_capacity_ref':'current-node','global_limit':68,'reserved':10,'processes':1,'pool_size':2,'overflow':0,
       'surge_allowance':1,'terminating_allowance':2,'node_backend_capacity':13,
       'accounts':{n:{'max_user_connections':0,'global_max_user_connections':0,'evidence_ref':'current-grant'} for n in ('identity_svc','game_svc')}},
      'normal_business_ref':'current-business','prior_comparison_finished_ref':'current-fixed-comparison',
      'expected_images':{n:'registry.test/'+n+'@sha256:'+'a'*64 for n in ('backend','frontend')},'db_probe_argv':['existing-db-probe'],
      'generator_guard_argv':['existing-guard'],'db_local_paths':{'mariadb':'mariadb','credential_file':'/private/client','ca_file':'/private/ca','database_host':'db.test'}}


class Fake:
    def __init__(self,c):
        self.c=c;self.hpas=[];self.writes=[]
        self.app={'metadata':{'uid':c['binding']['application_uid'],'resourceVersion':'1','annotations':{'seokpan.io/bootstrap-owner':'cloud-app-held'}},
          'spec':{'source':{},'project':C.APP,'destination':{'server':'https://kubernetes.default.svc','namespace':C.NS},'syncPolicy':{}},
          'status':{'sync':{'status':'Synced'},'health':{'status':'Healthy'}}}
        self.deps={n:{'metadata':{'uid':n+'-uid','resourceVersion':'1','generation':1},
          'spec':{'replicas':2 if n=='backend' else 1,'template':{'spec':{'containers':[{'name':n,'image':c['expected_images'][n],'resources':{'requests':{'cpu':'200m','memory':'256Mi'}}}]}}},
          'status':{'observedGeneration':1,**{k:2 if n=='backend' else 1 for k in ('replicas','readyReplicas','updatedReplicas','availableReplicas')}}} for n in ('backend','frontend')}
    def identity(self):pass
    def get(self,kind,name=None,ns=None):
        if kind=='application':r=self.app
        elif kind=='deployment':r=self.deps[name]
        elif kind=='hpa':r={'items':self.hpas}
        elif kind=='machineautoscalers.autoscaling.openshift.io':r={'items':[]}
        elif kind=='pods':r={'items':[{'metadata':{'uid':n+str(i),'labels':{'app.kubernetes.io/name':n}},'spec':{'nodeName':'worker'},
                    'status':{'phase':'Running','conditions':[{'type':'Ready','status':'True'}]}} for n,d in self.deps.items() for i in range(d['spec']['replicas'])]}
        else:raise AssertionError(kind)
        return copy.deepcopy(r)
    def patch(self,kind,name,obj,ops,ns=None):
        target=self.app if kind=='application' else self.deps[name] if kind=='deployment' else self.hpas[0]
        if target['metadata']['uid']!=obj['metadata']['uid']:raise RuntimeError('UID')
        self.writes.append(('patch',kind,copy.deepcopy(ops)))
        for op in ops:
            keys=[x.replace('~1','/').replace('~0','~') for x in op['path'].split('/')[1:]];p=target
            for k in keys[:-1]:p=p.setdefault(k,{})
            if op['op']=='remove':del p[keys[-1]]
            else:p[keys[-1]]=copy.deepcopy(op['value'])
        if kind=='deployment':
            target['metadata']['generation']+=1;target['status']={'observedGeneration':target['metadata']['generation'],**{k:target['spec']['replicas'] for k in ('replicas','readyReplicas','updatedReplicas','availableReplicas')}}
    def call(self,*args,payload=None):
        self.writes.append(tuple(args[:2]))
        if args[0]=='create':
            obj=json.loads(payload);obj['metadata'].update(uid='hpa-unique',resourceVersion='1');obj['spec']['behavior']['scaleDown']['selectPolicy']='Max';self.hpas=[obj];return b'{}'
        if args[0]=='delete':
            opts=C.read(args[-1]);assert opts['preconditions']['uid']==self.hpas[0]['metadata']['uid'];self.hpas=[];return b'{}'
        raise AssertionError(args)


class Tests(unittest.TestCase):
    def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.c=config();self.fake=Fake(self.c);self.cfg=self.root/'config.json';C.save(self.cfg,self.c);self.work=self.root/'run'
    def tearDown(self):self.tmp.cleanup()
    def action(self,name,*extra):
        args=['control.py',name,'--config',str(self.cfg),'--work',str(self.work),'--execute',*extra]
        with patch('sys.argv',args),patch('control.Client',return_value=self.fake),patch('control.db_probe',return_value={'connections_total':5}),redirect_stdout(io.StringIO()) as out:
            code=control.main()
        return code,out.getvalue()
    def test_local_budget_matches_declared_max_and_no_old_38_68_copy(self):
        x=C.validate(self.c);self.assertEqual(x['declared_connections'],52);self.assertEqual(x['required_with_reservation'],62)
    def test_global_account_node_and_unknown_terminating_bounds_fail(self):
        for field,value in [('global_limit',60),('node_backend_capacity',12),('terminating_allowance',None)]:
            c=copy.deepcopy(self.c);c['capacity'][field]=value
            with self.assertRaises(RuntimeError):C.validate(c)
        c=copy.deepcopy(self.c);c['capacity']['accounts']['identity_svc']['max_user_connections']=20
        with self.assertRaisesRegex(RuntimeError,'ACCOUNT_'):C.validate(c)
    def test_deleted_target_origin_and_unpinned_image_rejected(self):
        for path,value in [('cluster_id',C.OLD),('namespace_uid','old-uid')]:
            c=copy.deepcopy(self.c);c['binding'][path]=value
            with self.assertRaises(RuntimeError):C.validate(c)
        c=copy.deepcopy(self.c);c['expected_images']['backend']='registry.test/backend:latest'
        with self.assertRaisesRegex(RuntimeError,'PINNED'):C.validate(c)
    def test_lease_ignores_only_backend_replicas_and_keeps_source(self):
        s=self.fake.app['spec'];x=C.lease_spec(s);self.assertEqual(x['source'],s['source']);self.assertEqual(x['ignoreDifferences'][0]['jsonPointers'],['/spec/replicas'])
        self.assertNotIn('ignoreDifferences',s);self.assertIn('RespectIgnoreDifferences=true',x['syncPolicy']['syncOptions'])
    def test_prepare_start_restore_and_verify_use_owned_uid_without_normal_roll(self):
        self.assertEqual(self.action('prepare')[0],0);self.assertEqual(self.action('start')[0],0)
        self.assertEqual(len([x for x in self.fake.writes if x[0]=='create']),1)
        self.assertEqual(self.action('start')[0],0);self.assertEqual(len([x for x in self.fake.writes if x[0]=='create']),1)
        r={'binding':self.c['binding'],'control_sha256':C.digest(self.c),'status':'COMPLETED_HPA_LOAD','in_flight_at_end':0};f=self.root/'load.json';C.save(f,r)
        self.assertEqual(self.action('restore','--load-receipt',str(f))[0],0);self.assertEqual(self.action('verify-restore')[0],0)
        self.assertTrue((self.work/'restored.json').exists());self.assertEqual(self.fake.hpas,[])
    def test_foreign_hpa_is_not_adopted_or_deleted(self):
        self.action('prepare');self.fake.hpas=[{'metadata':{'name':'foreign'},'spec':{'scaleTargetRef':{'name':'backend'}}}]
        code,text=self.action('start');self.assertEqual(code,2);self.assertIn('FOREIGN_BACKEND_HPA',text);self.assertFalse(any(x[0]=='delete' for x in self.fake.writes))
    def test_stopped_run_cannot_restart_and_guard_rejects_stale_feed(self):
        self.action('prepare');self.action('start');self.assertEqual(self.action('stop')[0],0);self.assertEqual(self.action('start')[0],2)
        C.save(self.work/'latest.json',{'config_sha256':C.digest(self.c),'utc':'2020-01-01T00:00:00Z','status':'PASS_HPA_GUARD'})
        self.assertEqual(self.action('guard')[0],2)
    def test_restore_requires_stopped_same_run_receipt_or_explicit_no_load(self):
        self.action('prepare');self.action('start');self.assertEqual(self.action('restore')[0],2)
        self.assertEqual(self.action('restore','--confirm-no-load-started')[0],0)
    def test_changed_uid_or_source_cannot_be_restored_over(self):
        self.action('prepare');self.action('start');self.fake.app['spec']['source']={'path':'other'}
        code,_=self.action('restore','--confirm-no-load-started');self.assertEqual(code,2);self.assertTrue(self.fake.hpas)
    def test_hpa_api_default_fields_are_semantically_accepted(self):
        w=C.hpa(self.c)['spec'];a=copy.deepcopy(w);a['behavior']['scaleDown']['selectPolicy']='Max';self.assertTrue(C.subset(w,a));a['maxReplicas']=11;self.assertFalse(C.subset(w,a))
    def test_db_visibility_no_data_and_probe_limit_change_are_not_success(self):
        text='Ssl_cipher\tTLS\nGLOBAL_LIMIT\t68\t0\nThreads_connected\t8\nGRANT PROCESS ON *.* TO x\nACCOUNT\tidentity_svc\t2'
        x=DB.parse(text,self.c['binding']);self.assertEqual(x['per_account']['game_svc'],0)
        with self.assertRaises(RuntimeError):DB.parse(text.replace('PROCESS','USAGE'),self.c['binding'])
        x['global_limit']=69
        fake=type('R',(),{'returncode':0,'stdout':json.dumps(x).encode()})()
        with self.assertRaisesRegex(RuntimeError,'LIMIT_OR_TLS'):C.db_probe(self.c,lambda *a,**k:fake)
    def test_prometheus_empty_result_is_not_zero(self):
        self.assertEqual(P.result({'status':'success','data':{'resultType':'vector','result':[]}})['status'],'NO_DATA')
    def test_pending_template_has_no_api_or_install_side_effect(self):
        c=copy.deepcopy(self.c);c['capacity']['global_limit']=None;p=self.root/'pending.json';C.save(p,c)
        with patch('sys.argv',['control.py','start','--config',str(p),'--work',str(self.work)]),patch('control.Client',side_effect=AssertionError('API')),redirect_stdout(io.StringIO()) as out:
            self.assertEqual(control.main(),2)
        self.assertIn('capacity.global_limit',out.getvalue());self.assertFalse(self.work.exists())
    def test_result_join_requires_actual_restore_and_same_run(self):
        with self.assertRaises(RuntimeError):results.summarize(self.c,{'config_sha256':C.digest(self.c)},
            {'binding':self.c['binding'],'control_sha256':C.digest(self.c)}, {'binding':{},'run_id':'wrong','control_sha256':'x'},[],{})
    def test_dynamic_node_autoscaler_is_not_silently_enabled_by_load(self):
        client=type('C',(),{'get':lambda *a,**k:{'items':[{'spec':{'minReplicas':3,'maxReplicas':9}}]}})()
        with self.assertRaisesRegex(RuntimeError,'DYNAMIC_MACHINE'):control.no_node_growth(client)


if __name__=='__main__':unittest.main()
