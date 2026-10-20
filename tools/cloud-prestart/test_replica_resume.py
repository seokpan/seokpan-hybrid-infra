import copy,importlib.util,json,tempfile,unittest
from pathlib import Path
HERE=Path(__file__).resolve().parent
s=importlib.util.spec_from_file_location('replica_resume',HERE/'finish_presentation_link.py');P=importlib.util.module_from_spec(s);s.loader.exec_module(P)

class ReplicaResume(unittest.TestCase):
    workers=[{'node':'node-'+str(i),'hostname':'node-'+str(i),'zone':'zone-'+str(i)} for i in range(3)]
    def source(self):return {'targetRevision':'fixed','path':'runtime','kustomize':{'replicas':[{'name':'backend','count':1},{'name':'frontend','count':1}],'patches':[]}}
    def test_initial_profile_updates_existing_replica_transformer(self):
        original=self.source();result=P.profile_source(original)
        self.assertEqual(result['kustomize']['replicas'],[{'name':'backend','count':2},{'name':'frontend','count':1}])
        self.assertEqual(original,self.source())
    def test_correction_changes_counts_and_bounded_placement_only(self):
        original=P.profile_source(self.source());original['kustomize']['replicas'][0]['count']=1
        result=P.corrected_replica_source(original,self.workers)
        self.assertEqual(result['kustomize']['replicas'],[{'name':'backend','count':3},{'name':'frontend','count':3}])
        self.assertEqual(result['kustomize']['patches'][:-2],original['kustomize']['patches'])
        for name,patch in zip(('backend','frontend'),result['kustomize']['patches'][-2:]):
            self.assertEqual(patch['target']['name'],name)
            values=json.loads(patch['patch'])['spec']['template']['spec']
            self.assertEqual(set(values),{'affinity','topologySpreadConstraints'})
            self.assertEqual([x['minDomains'] for x in values['topologySpreadConstraints']],[3,3])
            self.assertNotIn('podAntiAffinity',values['affinity'])
        self.assertEqual(original['kustomize']['replicas'][0]['count'],1)
    def test_duplicate_or_other_stage_is_rejected(self):
        for count in (0,2,3,True):
            value=self.source();value['kustomize']['replicas'][0]['count']=count
            with self.assertRaises(RuntimeError):P.corrected_replica_source(value,self.workers)
        value=self.source();value['kustomize']['replicas'].append({'name':'backend','count':1})
        with self.assertRaises(RuntimeError):P.corrected_replica_source(value,self.workers)
    def test_response_loss_does_not_resubmit(self):
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp);old=self.source();P.save(out/'profile-source.json',old)
            app={'metadata':{'uid':P.APPUID,'resourceVersion':'1'},'spec':{'source':old},'status':{'sync':{'status':'Synced'},'health':{'status':'Healthy'}}}
            dep={'metadata':{'generation':1},'spec':{'replicas':1,'template':{'spec':{'containers':[{'name':'backend','resources':P.RESOURCE}]}}},'status':{'readyReplicas':1,'observedGeneration':1}}
            calls=[]
            def get(kind,*a):return dep if kind=='deployment' else app
            def call(argv):calls.append(argv);raise RuntimeError('RESPONSE_LOST')
            with self.assertRaisesRegex(RuntimeError,'RESPONSE_LOST'):P.correct_existing_profile(app,out,get,call,self.workers)
            with self.assertRaisesRegex(RuntimeError,'NO_RESUBMISSION'):P.correct_existing_profile(app,out,get,call,self.workers)
            self.assertEqual(len(calls),1)
            app['spec']['source']=P.corrected_replica_source(old,self.workers)
            P.correct_existing_profile(app,out,get,call,self.workers)
            self.assertEqual(len(calls),1)
            self.assertEqual(P.read(out/'profile-source.json'),app['spec']['source'])
    def test_different_pending_cause_has_no_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp);old=self.source();P.save(out/'profile-source.json',old)
            app={'spec':{'source':old},'status':{'sync':{'status':'Synced'},'health':{'status':'Healthy'}}}
            dep={'spec':{'replicas':2},'status':{}};calls=[]
            with self.assertRaisesRegex(RuntimeError,'DIFFERENT_PENDING_CAUSE'):
                P.correct_existing_profile(app,out,lambda *a:dep,lambda a:calls.append(a),self.workers)
            self.assertEqual(calls,[])

    def test_missing_workers_or_single_az_cannot_modify_source(self):
        for workers in (self.workers[:2],[dict(x,zone='one') for x in self.workers]):
            with self.assertRaisesRegex(RuntimeError,'THREE_READY_MULTI_AZ'):P.corrected_replica_source(self.source(),workers)

if __name__=='__main__':unittest.main()
