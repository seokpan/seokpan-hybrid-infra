"""Reconstruct the adopted held ROSA configuration from an encrypted recipe.

Uses a new cluster and fresh credentials/Worker identity. No SQL, source cutover,
Migration or app Sync is performed. External callback/DNS and actual new login
acceptance remain explicit; a partial/unknown stage is never rerun automatically.
"""
import argparse
import copy
import datetime as dt
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time

HERE=Path(__file__).resolve().parent
def module(name):
    s=importlib.util.spec_from_file_location(name,HERE/(name+'.py'))
    m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
B=module('prepare-rosa-checkpoint')
P=module('prepare-rosa-reconstruction')
W=module('rosa-fresh-management')
G=module('rosa-gitops-bootstrap')
L=module('rosa-lifecycle-plan')
V=module('rosa-monitoring-volume-backup')
need=B.need


def validate_new_identity(observed,handoff,account):
    creator=observed.get('properties',{}).get('rosa_creator_arn','')
    need(observed.get('id')==handoff['cluster_id'] and observed.get('state')=='ready'
         and observed.get('api',{}).get('url','').rstrip('/')==handoff['api_url'].rstrip('/')
         and observed.get('region',{}).get('id')=='ap-northeast-2'
         and re.fullmatch(r'arn:aws:sts::'+re.escape(account)+r':assumed-role/seokpan-tf-rosa/[^/]+',creator),
         'NEW_CLUSTER_ID_API_REGION_CREATOR_MISMATCH')
    need(observed.get('aws',{}).get('account_id') in (None,'',account),'NEW_CLUSTER_ACCOUNT_MISMATCH')


def application_spec(value):
    s=copy.deepcopy(value)
    for patch in s.get('source',{}).get('kustomize',{}).get('patches',[]):
        if patch.get('target',{}).get('group')=='':del patch['target']['group']
        if isinstance(patch.get('patch'),str):patch['patch']=json.loads(patch['patch'])
    return s


def save(path,value):
    path.write_bytes(B.canonical(value));path.chmod(0o600)


def map_hosts(value, old_api, new_api):
    if isinstance(value,list):return [map_hosts(x,old_api,new_api) for x in value]
    if isinstance(value,dict):return {k:map_hosts(v,old_api,new_api) for k,v in value.items()}
    if isinstance(value,str):
        old=old_api.split('//',1)[1].split(':',1)[0]
        new=new_api.split('//',1)[1].split(':',1)[0]
        return value.replace(old_api,new_api).replace('apps.'+old.removeprefix('api.'),'apps.'+new.removeprefix('api.'))
    return value


def desired(o,recipe,handoff):
    result=P.normalized_metadata(o)
    if o['kind']!='Secret':
        result=map_hosts(result,recipe['contract']['expected_api'],handoff['api_url'])
    if o['kind']=='Service':
        for key in ('clusterIP','clusterIPs','ipFamilies','ipFamilyPolicy','healthCheckNodePort'):
            result.get('spec',{}).pop(key,None)
    if o['kind']=='ArgoCD':
        # Certificates and sessions are operator-generated for the new API/Ingress.
        need(not result.get('spec',{}).get('tls',{}).get('ca',{}).get('key'), 'INLINE_OLD_TLS_KEY_REQUIRES_REVIEW')
    if o['kind']=='ServiceAccount':
        generated={x['metadata']['name'] for x in recipe['objects'] if x['kind']=='Secret' and x['metadata'].get('ownerReferences')}
        result['imagePullSecrets']=[r for r in result.get('imagePullSecrets',[]) if r['name'] not in generated]
        if not result['imagePullSecrets']:result.pop('imagePullSecrets',None)
    return result


class Restore:
    def __init__(self,c,handoff,recipe,out):
        self.c,self.handoff,self.recipe,self.out=c,handoff,recipe,out
        self.cluster=handoff['cluster_id'];self.kube=out/'kubeconfig'
        self.oc=c['auth']['oc_binary'];self.rosa=c['auth']['rosa_cli']
        self.objects={P.identity(o):o for o in recipe['objects']}
        self.actions={r['id']:r['action'] for r in recipe['decisions']}
    def oc_call(self,args,payload=None,json_output=True):
        p=subprocess.run([self.oc,'--kubeconfig',str(self.kube),'--request-timeout=30s',*args],
                         input=payload,capture_output=True,timeout=60)
        if p.returncode:
            (self.out/'failed-oc-private.log').write_bytes(p.stdout+p.stderr)
            raise RuntimeError('NEW_CLUSTER_RESTORATION_API_FAILED_NO_RETRY')
        if json_output:return json.loads(p.stdout) if p.stdout.strip() else None
        return p.stdout
    def get(self,o):
        m=o['metadata'];args=['get',o['kind'],m['name'],'--ignore-not-found=true','-o','json']
        if m.get('namespace'):args+=['-n',m['namespace']]
        return self.oc_call(args)
    def put(self,o):
        # This fresh target is scoped by the protected new-cluster handoff. Existing
        # objects are changed only through UID/resourceVersion checked JSON patch.
        expected=desired(o,self.recipe,self.handoff);live=self.get(expected)
        if live is None:
            self.oc_call(['create','-f','-','-o','name'],B.canonical(expected),False)
        else:
            if o['kind']=='ServiceAccount':
                preserved=[x for x in live.get('imagePullSecrets',[]) if x['name']!=P.EXPIRED_PULL]
                names={x['name'] for x in preserved}
                expected['imagePullSecrets']=preserved+[x for x in expected.get('imagePullSecrets',[]) if x['name'] not in names]
            changes=[]
            for key,value in expected.items():
                if key in ('apiVersion','kind','metadata'):continue
                if live.get(key)!=value:changes.append({'op':'add','path':'/'+key,'value':value})
            for field in ('labels','annotations'):
                values=expected['metadata'].get(field)
                if values:
                    combined=dict(live['metadata'].get(field) or {},**values)
                    if combined!=live['metadata'].get(field):changes.append({'op':'add','path':'/metadata/'+field,'value':combined})
            if changes:
                patch=[{'op':'test','path':'/metadata/uid','value':live['metadata']['uid']},
                       {'op':'test','path':'/metadata/resourceVersion','value':live['metadata']['resourceVersion']},*changes]
                args=['patch',o['kind'],o['metadata']['name'],'--type=json','-p',json.dumps(patch),'-o','name']
                if o['metadata'].get('namespace'):args+=['-n',o['metadata']['namespace']]
                self.oc_call(args,json_output=False)
        observed=self.get(expected)
        need(observed is not None,'RESTORED_OBJECT_READBACK_REQUIRED')
        # Admission defaults may add fields; every supplied scalar/list is checked.
        def contains(actual,wanted):
            if isinstance(wanted,dict):return isinstance(actual,dict) and all(k in actual and contains(actual[k],v) for k,v in wanted.items())
            return actual==wanted
        need(all(contains(observed.get(k),v) for k,v in expected.items() if k not in ('apiVersion','kind','metadata')),
             'RESTORED_DECLARATION_DIFFERS')
        return {'id':P.identity(expected),'uid':observed['metadata']['uid']}
    def stage(self,name,fn):
        receipt=self.out/(name+'.complete.json');attempt=self.out/(name+'.attempt.json')
        if receipt.is_file():
            accepted=L.read_json(receipt)
            need(accepted['cluster_id']==self.cluster and accepted['recipe_sha256']==self.c['restoration']['recipe_sha256'],
                 'COMPLETED_STAGE_BINDING_CHANGED')
            return accepted['result']
        need(not attempt.exists(),'PARTIAL_STAGE_REQUIRES_READ_ONLY_RECONCILIATION_'+name)
        W.exclusive_marker(attempt,{'cluster_id':self.cluster,'stage':name})
        result=fn()
        save(receipt,{'cluster_id':self.cluster,'recipe_sha256':self.c['restoration']['recipe_sha256'],'result':result})
        return result
    def apply_actions(self,actions):
        rows=[o for k,o in self.objects.items() if self.actions[k] in actions]
        priority={'Namespace':0,'Secret':1,'ConfigMap':2,'ServiceAccount':3,'Role':4,'RoleBinding':5,'AppProject':6,'Application':7}
        return [self.put(o) for o in sorted(rows,key=lambda o:priority.get(o['kind'],5))]
    def management(self):
        r=W.Runner(self.out)
        admin=W.ensure_admin(r,self.rosa,self.cluster,self.handoff['api_url'])
        deadline=time.monotonic()+600
        while not r.login(self.oc,self.handoff['api_url'],admin,self.kube):
            need(time.monotonic()<deadline,'NEW_INITIAL_ADMIN_ACTIVATION_PENDING')
            time.sleep(15)
        need(self.oc_call(['whoami'],json_output=False).decode().strip()=='cluster-admin','NEW_MANAGEMENT_IDENTITY_REQUIRED')
        return {'kubeconfig':str(self.kube),'initial_admin_credentials':str(self.out/'initial-admin.json')}
    def worker_binding(self):
        nodes=self.oc_call(['get','nodes','-o','json']);W.validate_nodes(nodes)
        machines=self.oc_call(['get','machines.machine.openshift.io','-n','openshift-machine-api','-o','json'])
        inputs=L.read_json(self.c['create']['var_file']);f=inputs['foundation']
        groups=V.aws(['ec2','describe-security-groups','--filters','Name=vpc-id,Values='+f['vpc_id'],
                     'Name=tag:kubernetes.io/cluster/'+self.handoff['infra_id']+',Values=owned,shared'],self.out)
        sg=W.select_machine_worker_group(nodes,machines,groups,f['vpc_id'],f['account_id'],self.handoff['infra_id'])
        evidence=self.out/'fresh-worker-evidence.json'
        save(evidence,{'cluster_id':self.cluster,'nodes':nodes,'machines':machines,'security_groups':groups})
        binding={'cluster_id':self.cluster,'worker_security_group_id':sg,
                 'observed_at':dt.datetime.now(dt.timezone.utc).isoformat(),'evidence_reference':str(evidence)}
        new_inputs=dict(inputs,worker_sg_binding=binding);variables=self.out/'bound-inputs.tfvars.json';save(variables,new_inputs)
        source=Path(self.c['create']['tf_root']);env=L.environment(source,Path(self.c['tf_data']))
        token=subprocess.run([self.rosa,'token','--generate'],capture_output=True,timeout=60)
        need(token.returncode==0 and token.stdout.strip() and not any(x.isspace() for x in token.stdout.decode().strip()),
             'CURRENT_RHCS_SESSION_REQUIRED')
        env['RHCS_TOKEN']=token.stdout.decode().strip()
        tf=['terraform','-chdir='+str(source)];saved=self.out/'fresh-worker-binding.tfplan'
        L.call(tf+['plan','-input=false','-no-color','-lock-timeout=60s','-detailed-exitcode',
                   '-var-file='+str(variables),'-out='+str(saved)],env,self.out)
        plan=json.loads(L.call(tf+['show','-json',str(saved)],env,self.out).stdout)
        original=L.read_json(Path(self.c['restoration']['creation_result'])/'plan-private.json')
        review=W.validate_data_plan(plan,original,inputs,binding);save(self.out/'worker-plan-review.json',review)
        need(review['create']==2,'FRESH_TWO_BINDINGS_REQUIRED')
        W.exclusive_marker(self.out/'worker-apply-attempt.json',{'plan_sha256':L.sha(saved)})
        L.call(tf+['apply','-input=false','-no-color','-lock-timeout=60s',str(saved)],env,self.out)
        latest=json.loads(L.call(tf+['output','-json','platform_handoff'],env,self.out).stdout)
        need(latest['cluster_id']==self.cluster and latest['binding_enabled'] is True,'NEW_BINDING_READBACK_REQUIRED')
        save(self.out/'bound-platform-handoff.json',latest)
        return {'verified':True,'bound_inputs':str(variables),'handoff':str(self.out/'bound-platform-handoff.json')}
    def operator(self):
        r=G.Runner(self.oc,self.kube,self.out)
        G.catalog_candidate(r.get('packagemanifests',namespace='openshift-marketplace'))
        paths=[]
        for i,o in enumerate(G.manifests()):
            live=r.get(o['kind'],o['metadata']['name'],o['metadata'].get('namespace'),optional=True)
            if live:G.same_owned(live,o)
            else:
                path=self.out/('olm-'+str(i)+'.json');save(path,o);paths.append(str(path))
        if paths:
            playbook=self.out/'operator-bootstrap.json';playbook.write_text(G.PLAYBOOK)
            variables=self.out/'operator-bootstrap-vars.json'
            save(variables,{'oc_binary':self.oc,'kubeconfig':str(self.kube),'manifest_paths':paths})
            p=subprocess.run(['ansible-playbook','-i','localhost,',str(playbook),'--extra-vars','@'+str(variables)],capture_output=True,timeout=240)
            (self.out/'ansible-private.log').write_bytes(p.stdout+p.stderr)
            need(p.returncode==0,'MINIMAL_ANSIBLE_BOOTSTRAP_FAILED_PRESERVE')
        sub=G.wait_for(lambda: (s if s and s.get('status',{}).get('installPlanRef') else None)
                       if (s:=r.get('subscriptions',G.PACKAGE,G.NS,optional=True)) else None,420,'OLM_RESOLUTION')
        ref=sub['status']['installPlanRef']
        plan=G.wait_for(lambda:(p if p and p.get('status',{}).get('phase') in ('RequiresApproval','Installing','Complete') else None)
                        if (p:=r.get('installplans',ref['name'],G.NS,optional=True)) else None,420,'OLM_PLAN')
        G.exact_plan(plan,ref)
        if not plan['spec'].get('approved'):
            r.call(['patch','installplan',ref['name'],'-n',G.NS,'--type=json','-p',json.dumps([
                {'op':'test','path':'/metadata/uid','value':ref['uid']},
                {'op':'test','path':'/spec/approved','value':False},
                {'op':'replace','path':'/spec/approved','value':True}])],'approve-exact-new-installplan')
        G.wait_for(lambda:(v if v and v.get('status',{}).get('phase')=='Succeeded' and v.get('spec',{}).get('version')=='1.21.4' else None)
                   if (v:=r.get('clusterserviceversions',G.CSV,G.NS,optional=True)) else None,900,'GITOPS_1_21_4')
        G.wait_for(lambda:(v if v and v.get('status',{}).get('phase')=='Available' else None)
                   if (v:=r.get('argocd','openshift-gitops','openshift-gitops',optional=True)) else None,600,'DEFAULT_ARGO')
        return {'version':'1.21.4','approval':'Manual'}
    def humans(self):
        for user in self.c['restoration']['human_admins']:
            p=subprocess.run([self.rosa,'grant','user','dedicated-admin','--user',user,'--cluster',self.cluster],capture_output=True,timeout=120)
            need(p.returncode==0,'NEW_PERSONAL_ADMIN_GRANT_FAILED_NO_RETRY')
        self.apply_actions({'SELECTED_GROUP_CONFIGURATION','PROTECTED_SECRET_RESUPPLY'})
        oauth=next(o for o in self.recipe['objects'] if o['kind']=='OAuth')
        providers=[p for p in oauth['spec']['identityProviders'] if p['name'] in self.recipe['contract']['identity_providers']]
        live=self.oc_call(['get','oauth','cluster','-o','json'])
        retained=[p for p in live['spec'].get('identityProviders',[]) if p['name'] not in {x['name'] for x in providers}]
        patch=[{'op':'test','path':'/metadata/resourceVersion','value':live['metadata']['resourceVersion']},
               {'op':'add','path':'/spec/identityProviders','value':retained+providers}]
        self.oc_call(['patch','oauth','cluster','--type=json','-p',json.dumps(patch),'-o','name'],json_output=False)
        return {'selected_provider_restored':True,'personal_sessions':'NEW_LOGIN_REQUIRED'}
    def monitoring_volumes(self):
        a=argparse.Namespace(snapshot_map=self.c['restoration']['snapshot_map'],
            snapshot_map_sha256=self.c['restoration']['snapshot_map_sha256'],new_handoff=self.c['restoration']['new_handoff'],
            kubeconfig=str(self.kube),key_file=self.c['restoration']['key_file'],
            delete_manifest=self.c['delete']['manifest'],output_parent=str(self.out))
        m=L.read_json(self.c['delete']['manifest'])
        return V.restore_volumes(a,m,self.out,self.c['restoration']['sops_binary'],{})
    def permission_compare(self):
        def canonical_rules(rows):
            return sorted(B.canonical({k:sorted(v) if isinstance(v,list) else v for k,v in r.items()}).decode() for r in rows)
        for o in self.recipe['objects']:
            if self.actions[P.identity(o)]!='OPERATOR_RECONCILE_COMPARE' or o['kind']=='ClusterServiceVersion':continue
            live=self.get(o)
            if o['kind'] in ('Role','ClusterRole'):
                need(live and canonical_rules(live.get('rules',[]))==canonical_rules(o.get('rules',[])),
                     'OPERATOR_CLUSTER_ROLE_DIFFERS_NO_AUTOMATIC_BROADENING')
            elif o['kind'] in ('RoleBinding','ClusterRoleBinding'):
                expected=desired(o,self.recipe,self.handoff)
                need(live and live.get('roleRef')==expected.get('roleRef')
                     and sorted(live.get('subjects',[]),key=B.canonical)==sorted(expected.get('subjects',[]),key=B.canonical),
                     'OPERATOR_CLUSTER_ROLE_BINDING_DIFFERS_NO_AUTOMATIC_BROADENING')
            elif o['kind']=='ConfigMap':
                expected=desired(o,self.recipe,self.handoff)
                need(live and live.get('data',{})==expected.get('data',{}),'OPERATOR_CONFIGMAP_DIFFERS_REQUIRES_REVIEW')
            elif o['kind']=='ServiceAccount':
                need(live and live.get('automountServiceAccountToken')==o.get('automountServiceAccountToken'),
                     'SERVICE_ACCOUNT_TOKEN_MOUNT_POLICY_DIFFERS')
        return {'adopted_operator_roles_match':True}
    def held_acceptance(self):
        accepted=[]
        for o in self.recipe['objects']:
            if self.actions[P.identity(o)]!='HELD_APPLICATION_NO_SYNC':continue
            live=self.get(o);expected=desired(o,self.recipe,self.handoff)
            need(live and application_spec(live['spec'])==application_spec(expected['spec']) and not live.get('operation')
                 and live.get('spec',{}).get('syncPolicy',{}).get('automated') is None,
                 'HELD_APP_SPEC_OR_OPERATION_DIFFERS')
            accepted.append(live['metadata']['name'])
        workloads=self.oc_call(['get','deployment,statefulset','-n',self.recipe['contract']['cloud_namespace'],'-o','json'])
        need(all(o.get('spec',{}).get('replicas',1)==0 for o in workloads['items']),'CLOUD_WORKLOAD_STARTED_UNEXPECTEDLY')
        return {'applications':accepted,'backend_replicas':0,'frontend_replicas':0,'automatic_sync':False}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('platform-handoff','lifecycle-config','receipt'):p.add_argument('--'+name,required=True)
    a=p.parse_args();os.umask(0o077)
    c=L.read_json(a.lifecycle_config);h=L.read_json(a.platform_handoff);r=c['restoration']
    result={'status':'STOP_ROSA_HELD_RESTORATION','application_started':False,'SQL':0,'automatic_retry':False}
    try:
        need(c['scope']=='ROSA_ONLY' and h.get('state')=='ready' and h.get('cluster_id')!=c.get('source_cluster_id',c['current_cluster_id']),
             'DIFFERENT_NEW_READY_ROSA_REQUIRED')
        cipher=B.private_file(r['recipe_archive'],B.MAX_BYTES*3)
        need(hashlib.sha256(cipher).hexdigest()==r['recipe_sha256'],'PINNED_ENCRYPTED_RECIPE_REQUIRED')
        recipe=json.loads(B.crypt(r['sops_binary'],cipher,decrypt=True,key_file=r['key_file']))
        need(recipe.get('purpose')=='ROSA_HELD_RESTORATION_RECIPE','HELD_RECIPE_SCHEMA_REQUIRED')
        need(all(P.classify(o,recipe['contract'])==next(x['action'] for x in recipe['decisions'] if x['id']==P.identity(o))
                 for o in recipe['objects']),'RECIPE_CLASSIFICATION_CHANGED')
        for name,sha in r['dependency_sha256'].items():need(L.sha(HERE/name)==sha,'RESTORATION_DEPENDENCY_CHANGED')
        need(shutil.which('ansible-playbook') and Path(c['auth']['oc_binary']).is_file(),'ADOPTED_NATIVE_TOOLS_REQUIRED')
        root=Path(c['evidence_root'])/'held-restoration';root.mkdir(mode=0o700,exist_ok=True)
        out=root/h['cluster_id'];out.mkdir(mode=0o700,exist_ok=True)
        import fcntl
        lock=(out/'restore.lock').open('a')
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise RuntimeError('ANOTHER_RESTORATION_PROCESS_RUNNING')
        r['creation_result']=str(Path(a.platform_handoff).parent);r['new_handoff']=a.platform_handoff
        observed=W.Runner(out).json_command([c['auth']['rosa_cli'],'describe','cluster','--cluster',h['cluster_id'],'--output','json'],'current-new-cluster')
        validate_new_identity(observed,h,L.read_json(c['create']['var_file'])['foundation']['account_id'])
        restore=Restore(c,h,recipe,out)
        restore.stage('management',restore.management)
        restore.stage('fresh-worker-binding',restore.worker_binding)
        restore.stage('monitoring-volumes',restore.monitoring_volumes)
        restore.stage('gitops-operator',restore.operator)
        restore.stage('namespaces',lambda:restore.apply_actions({'FRESH_NAMESPACE'}))
        restore.stage('human-configuration',restore.humans)
        restore.stage('argocd-spec',lambda:restore.apply_actions({'ARGO_CR_SPEC_RESTORE'}))
        G.wait_for(lambda: (v if v and v.get('status',{}).get('phase')=='Available' else None)
                   if (v:=restore.get({'kind':'ArgoCD','metadata':{'name':recipe['contract']['argo_name'],
                       'namespace':recipe['contract']['argo_namespace']}})) else None,600,'RESTORED_ARGO')
        restore.stage('platform-inputs',lambda:restore.apply_actions({'PLATFORM_AND_OBSERVABILITY_DECLARATION','ZERO_REPLICA_DECLARATION','PROJECT_DECLARATION'}))
        restore.stage('monitoring-configuration',lambda:restore.apply_actions({'MONITORING_CONFIGURATION_AFTER_PVC_RESTORE'}))
        restore.stage('held-applications',lambda:restore.apply_actions({'HELD_APPLICATION_NO_SYNC'}))
        restore.stage('operator-permission-compare',restore.permission_compare)
        accepted=restore.stage('held-acceptance',restore.held_acceptance)
        oauth=restore.oc_call(['get','route','oauth-openshift','-n','openshift-authentication','-o','json'])
        callbacks={name:'https://'+oauth['spec']['host']+'/oauth2callback/'+name for name in recipe['contract']['identity_providers']}
        save(out/'new-oauth-callbacks.json',callbacks)
        need(r.get('accepted_new_oauth_callbacks')==callbacks,'NEW_OAUTH_CALLBACK_ACCEPTANCE_PENDING_USE_RESTORE_RESUME')
        result.update(status='PASS_ROSA_HELD_RESTORATION',cluster_id=h['cluster_id'],**accepted,
                      worker_data_binding_verified=True,personal_sessions='NEW_LOGIN_REQUIRED',
                      monitoring_history_runtime='NEW_DATA_READ_ACCEPTANCE_REQUIRED',
                      management_kubeconfig=str(restore.kube),restoration_directory=str(out))
    except Exception as error:
        result['reason']=str(error) if isinstance(error,(RuntimeError,W.Stop,G.Stop)) else type(error).__name__
    save(Path(a.receipt),result);print(json.dumps(result,indent=2))
    return 0 if result['status'].startswith('PASS_') else 1


if __name__=='__main__':raise SystemExit(main())
