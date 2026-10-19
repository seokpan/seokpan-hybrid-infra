"""Capture only new State/restore inputs for review; never authorize or delete ROSA."""
import argparse
import base64
import datetime as dt
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile

HERE=Path(__file__).resolve().parent
def module(name,expected):
    path=HERE/(name+'.py')
    if hashlib.sha256(path.read_bytes()).hexdigest()!=expected:raise RuntimeError('PINNED_REVIEW_DEPENDENCY_REQUIRED')
    spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
B=module('prepare-rosa-checkpoint','11463a8fb7f2b7e3b136dc543de86df7c51cabfce44cbe4cd2e08dfa74ddbbfd')
R=module('rosa-monitoring-restore-permission','26e18086c3c5e654fe76e7d71a29659dcec274f0509775e1b4befbc59410b9ba')
M=R.M;need=M.need
POLICY='d72defef84f0c620afbaa673ba485e467337f21c97def1e51e3392e24e916935'
DELETE_PLAN='a495369eeb2a66e0603bbaf1fa903944067f2e5cb4435645c9dd2190f669daed'

def source_hash(root):
    paths=sorted([*root.glob('*.tf'),*root.glob('*.tf.json'),root/'.terraform.lock.hcl'])
    need(paths and all(p.is_file() and not p.is_symlink() for p in paths),'REGULAR_SOURCE_AND_LOCK_REQUIRED')
    need(not list(root.glob('*.auto.tfvars*')) and not (root/'terraform.tfvars').exists()
         and not (root/'terraform.tfvars.json').exists(),'UNREVIEWED_AUTO_INPUTS_REJECTED')
    return hashlib.sha256('\n'.join(p.name+':'+M.sha(p) for p in paths).encode()).hexdigest()

def accepted_inputs(root):
    accepted=M.read(root/'rosa-reconstruction-preparation-0kqodl8m/receipt.json')
    c=M.read(accepted['lifecycle_config']);manifest=M.read(c['delete']['manifest'])
    cache=M.read(Path(accepted['lifecycle_config']).parent/'restore-policy-accepted.json')
    permission=M.read(c['restoration']['permission_preflight_receipt'])
    need(cache.get('status')=='PASS_EXACT_RESTORE_POLICY_APPLIED_AND_ATTACHED' and cache.get('policy_sha256')==POLICY
         and cache.get('partial_apply_reconciled') is True,'ACCEPTED_RECONCILED_IAM_REQUIRED')
    need(permission.get('status')=='PASS_RESTORE_PERMISSION_PREREQUISITES_NOT_RUNTIME'
         and permission.get('volume_dry_run_allowed')==4 and permission.get('new_volume_permissions_accepted') is True
         and c['restoration'].get('new_volume_permissions_accepted') is True,'ACCEPTED_PERMISSION_RECEIPT_REQUIRED')
    need(c.get('scope')=='ROSA_ONLY' and c['current_cluster_id']==c['source_cluster_id']==manifest['cluster_id']
         and manifest.get('action')=='delete' and manifest.get('plan_sha256')==DELETE_PLAN
         and M.sha(manifest['plan'])==DELETE_PLAN,'SAVED_DELETE_BINDING_REQUIRED')
    return accepted,c,manifest,cache

def pull(run,target,data,key,account):
    meta=M.read(data/'terraform.tfstate')['backend']
    need(meta['type']=='s3' and meta['config']['key']==key and meta['config']['bucket']=='seokpan-tfstate-'+account
         and meta['config']['region']=='ap-northeast-2','EXPECTED_REMOTE_STATE_BACKEND_REQUIRED')
    run.env['TF_DATA_DIR']=str(data)
    state=json.loads(run.command(['terraform','-chdir='+str(target),'state','pull']).stdout)
    return state

def held_inputs(c,manifest,accepted):
    need(accepted.get('classified_objects')==135 and accepted.get('unclassified_objects')==0,'ACCEPTED_RECIPE_CLASSIFICATION_REQUIRED')
    need(c['restoration']['recipe_sha256']==accepted['recipe_sha256']
         and M.sha(c['restoration']['recipe_archive'])==accepted['recipe_sha256'],'ACCEPTED_RECIPE_BINDING_REQUIRED')
    need(source_hash(Path(c['create']['tf_root']))==c['adopted_tf_source_hash']==manifest['source_hash']
         and source_hash(Path(manifest['tf_root']))==manifest['source_hash']
         and M.sha(manifest['var_file'])==manifest['var_sha256'],'ADOPTED_SOURCE_INPUT_BINDING_REQUIRED')
    dependencies=c['restoration']['dependency_sha256'];driver=Path(c['create']['restore_driver'])
    need(driver.name=='rosa-held-restoration.sh' and M.sha(driver)==c['create']['restore_driver_sha256'],'HELD_DRIVER_BINDING_REQUIRED')
    required={'prepare-rosa-checkpoint.py','prepare-rosa-reconstruction.py','rosa-fresh-management.py',
              'rosa-gitops-bootstrap.py','rosa-held-restoration.py','rosa-lifecycle-plan.py','rosa-monitoring-volume-backup.py'}
    need(set(dependencies)==required and all(M.sha(driver.parent/name)==sha for name,sha in dependencies.items()),
         'HELD_DEPENDENCY_BINDING_REQUIRED')
    need(shutil.which('ansible-playbook') and Path(c['auth']['rosa_cli']).is_file()
         and os.access(c['auth']['rosa_cli'],os.X_OK),'EXISTING_RECREATION_TOOLS_REQUIRED')
    old_state=M.read(Path(c['delete']['manifest']).parent/'before.tfstate')
    expected=M.read(manifest['var_file']);expected.update(worker_sg_binding=None,cluster_enabled=True,
        openshift_version=old_state['outputs']['platform_handoff']['value']['current_version'])
    need(M.read(c['create']['var_file'])==expected,'FUNCTIONALLY_EQUIVALENT_RECREATE_INPUTS_REQUIRED')

def pack_files(files,separate_key):
    rows=[];total=0;key=Path(separate_key)
    for file in sorted(set(files)):
        need(file.resolve()!=key.resolve() and not os.path.samefile(file,key),'SEPARATE_KEY_MUST_NOT_ENTER_ARCHIVE')
        raw=B.private_file(file);total+=len(raw);need(total<=B.MAX_BYTES,'NEW_SUPPLEMENT_TOO_LARGE')
        rows.append({'root':str(file.parent),'path':file.name,'sha256':hashlib.sha256(raw).hexdigest(),'data':base64.b64encode(raw).decode()})
    return rows

def impact_summary(pods,jobs,cronjobs,deployments,apps):
    need(all(o.get('spec',{}).get('replicas',1)==0 for o in deployments),'UNEXPECTED_CLOUD_APP_REPLICAS_BLOCK_REVIEW')
    need(not any(a.get('operation') or a.get('status',{}).get('operationState',{}).get('phase') in ('Running','Terminating') for a in apps),
         'ACTIVE_ARGO_OPERATION_BLOCK_REVIEW')
    active=[o['metadata']['name'] for o in jobs if o.get('status',{}).get('active',0)>0]
    enabled=[o['metadata']['name'] for o in cronjobs if o.get('spec',{}).get('suspend') is not True]
    need(not active and not enabled,'ACTIVE_SCHEDULED_DATA_WORK_BLOCK_REVIEW')
    return {'pods':[{'name':o['metadata']['name'],'phase':o.get('status',{}).get('phase'),'uid':o['metadata']['uid']} for o in pods],
            'active_jobs':active,'unsuspended_cronjobs':enabled,'app_replicas_zero':True,'active_argo_operations':False,
            'interactive_data_activity':'NOT_PROVEN_BY_POD_STATE_REQUIRES_DELETE_IMPACT_ACCEPTANCE'}

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('mode',choices=('bootstrap','finish'));p.add_argument('--root',required=True)
    p.add_argument('--contract',required=True);p.add_argument('--age-keygen');a=p.parse_args();root=Path(a.root);os.umask(0o077)
    out=Path(tempfile.mkdtemp(prefix='rosa-delete-review-',dir=root));run=M.Run(out);phase='ACCEPTED_INPUTS'
    result={'status':'STOP_DELETE_REVIEW_PREPARATION','deletion_ready':False,'deletion_started':False,
            'resource_mutations':0,'sql_calls':0,'old_snapshot_or_decrypt_repeated':False,'plan_repeated':False,'automatic_retry':False}
    try:
        accepted,c,manifest,cache=accepted_inputs(root);account=manifest['account_id']
        caller=run.aws(['sts','get-caller-identity']);role='bootstrap' if a.mode=='bootstrap' else 'rosa'
        need(caller.get('Account')==account and ':assumed-role/seokpan-tf-'+role+'/' in caller.get('Arn',''),'EXPECTED_PURPOSE_ROLE_REQUIRED')
        if a.mode=='bootstrap':
            phase='LATEST_BOOTSTRAP_STATE_CAPTURE';partial=root/'monitoring-restore-permission-cq4vn692'
            state=pull(run,partial/'source/terraform/bootstrap',partial/'tf-data','phase2/bootstrap/terraform.tfstate',account)
            policies=[i['attributes'] for r in state['resources'] if r.get('mode')=='managed' and r.get('type')=='aws_iam_policy'
                      and r.get('name')=='tf_rosa_monitoring_restore' for i in r.get('instances',[])]
            expected=M.read(partial/'restore-scope.tfvars.json')['rosa_monitoring_restore_policy_json']
            need(len(policies)==1 and R.doc(policies[0]['policy'])==R.doc(expected),'POST_IAM_STATE_POLICY_REQUIRED')
            M.save(out/'latest-bootstrap.tfstate',state)
            receipt={'status':'PASS_NEW_BOOTSTRAP_STATE_CAPTURED_NO_PERMISSION_RETEST','state_file':str(out/'latest-bootstrap.tfstate'),
                     'state_sha256':M.sha(out/'latest-bootstrap.tfstate'),'policy_sha256':POLICY,'root_scope':'BOOTSTRAP_ONLY',
                     'deletion_ready':False,'deletion_started':False}
            M.save(root/'rosa-delete-review-bootstrap-latest.json',receipt);result.update(receipt)
        else:
            phase='LATEST_ROSA_STATE_AND_RESTORE_BINDINGS';captured=M.read(root/'rosa-delete-review-bootstrap-latest.json')
            need(captured.get('status')=='PASS_NEW_BOOTSTRAP_STATE_CAPTURED_NO_PERMISSION_RETEST'
                 and M.sha(captured['state_file'])==captured['state_sha256'],'NEW_BOOTSTRAP_STATE_CAPTURE_REQUIRED')
            held_inputs(c,manifest,accepted)
            state=pull(run,Path(manifest['tf_root']),Path(manifest['tf_data']),'phase2/rosa/terraform.tfstate',account)
            need((state['lineage'],state['serial'])==(manifest['state_lineage'],manifest['state_serial']),
                 'ROSA_STATE_CHANGED_REQUIRES_PLAN_REVIEW')
            M.save(out/'latest-rosa.tfstate',state)
            phase='CURRENT_EPHEMERAL_WORK_IMPACT'
            kube=root/'rosa-restoration-gaps-c9exvd5i/emergency-kubeconfig';oc=c['auth']['oc_binary']
            need(M.sha(oc)==c['auth']['oc_sha256'],'PINNED_OC_REQUIRED')
            def get(kind,namespace):
                return json.loads(run.command([oc,'--kubeconfig',str(kube),'--request-timeout=20s','get',kind,'-n',namespace,'-o','json']).stdout)['items']
            impact=impact_summary(get('pods','seokpan-cloud'),get('jobs','seokpan-cloud'),get('cronjobs','seokpan-cloud'),
                                  get('deployments','seokpan-cloud'),get('applications.argoproj.io','openshift-gitops'))
            journal=M.read(Path(c['restoration']['snapshot_map']).parent/'snapshot-journal.json')
            need(len(journal['volumes'])==4 and all(r['snapshot_state']=='completed' for r in journal['volumes']),
                 'COMPLETED_MAP_JOURNAL_REQUIRED')
            review={'scope':'ROSA_ONLY','saved_plan_sha256':DELETE_PLAN,'snapshot_cutoffs_utc':[r.get('snapshot_start_time') for r in journal['volumes']],
                    'application_consistency':'NOT_QUIESCED','impact':impact,'deletion_ready':False,'approved_by_user':False,
                    'remaining':['NEW_SUPPLEMENT_EXTERNAL_DECRYPT','USER_CUTOFF_AND_EPHEMERAL_IMPACT_ACCEPTANCE','EXPLICIT_USER_DELETE_APPROVAL'],
                    'future_create_network_and_budget':'SEPARATE_FROM_DELETE_APPROVAL'}
            c['create'].update(held_restoration_contract_accepted=True,inputs_accepted=True)
            phase='CONFIG_AND_NEW_SUPPLEMENT_ENCRYPTION'
            temporary=out/'lifecycle.updated.json';M.save(temporary,c);os.replace(temporary,accepted['lifecycle_config'])
            M.save(out/'delete-review.json',review)
            files=[Path(captured['state_file']),out/'latest-rosa.tfstate',out/'delete-review.json',Path(accepted['lifecycle_config']),
                   Path(c['create']['var_file']),Path(c['delete']['manifest']),Path(c['restoration']['permission_preflight_receipt']),
                   Path(accepted['lifecycle_config']).parent/'restore-policy-accepted.json']
            files+=list(Path(c['create']['tf_root']).glob('*.tf'))+[Path(c['create']['tf_root'])/'.terraform.lock.hcl']
            driver=Path(c['create']['restore_driver']);files+=[driver]+[driver.parent/name for name in c['restoration']['dependency_sha256']]
            files+=list((root/'monitoring-restore-permission-cq4vn692/source/terraform/bootstrap').glob('*.tf'))
            files+=[root/'monitoring-restore-permission-cq4vn692/source/terraform/bootstrap/.terraform.lock.hcl',
                    root/'monitoring-restore-permission-cq4vn692/restore-scope.tfvars.json']
            rows=pack_files(files,c['restoration']['key_file'])
            contract=M.read(a.contract);cipher=B.private_file(contract['recipient_source'],262144)
            need(hashlib.sha256(cipher).hexdigest()==contract['recipient_source_sha256'],'RECIPIENT_SOURCE_HASH_REQUIRED')
            values,_=B.age_recipients(cipher);a.key_file=c['restoration']['key_file']
            recipient=B.key_matched_recipient(values,a,out);sops=c['restoration']['sops_binary']
            need(M.sha(sops)==B.SOPS_SHA,'PINNED_SOPS_REQUIRED')
            bundle={'schema':1,'origin_host':socket.gethostname(),'captured_at_utc':dt.datetime.now(dt.timezone.utc).isoformat(),
                    'snapshots':[],'files':rows,'purpose':'ROSA_DELETE_REVIEW_RESTART_SUPPLEMENT_NOT_APPROVAL'}
            raw=B.canonical(bundle);encrypted=B.crypt(sops,raw,recipient=recipient);archive=out/'delete-review.sops.json';archive.write_bytes(encrypted)
            need(B.canonical(json.loads(B.crypt(sops,encrypted,decrypt=True,key_file=a.key_file)))==raw,'NEW_SUPPLEMENT_ROUNDTRIP_FAILED')
            handoff=root/'rosa-preservation-handoff-latest.json';old=M.read(handoff)
            old['archives']=[r for r in old['archives'] if r['kind']!='delete-review']+[
                {'kind':'delete-review','path':str(archive),'sha256':M.sha(archive)}]
            temp=out/'handoff.updated.json';M.save(temp,old);os.replace(temp,handoff)
            result.update(status='PASS_NEW_STATE_AND_RESTART_SUPPLEMENT_LOCAL_ROUNDTRIP',archive=str(archive),archive_sha256=M.sha(archive),
                          files=len(rows),held_restoration_contract_accepted=True,recreate_inputs_accepted=True,
                          impact=impact,snapshot_cutoffs_utc=review['snapshot_cutoffs_utc'],remaining=review['remaining'],
                          independent_new_supplement_decrypt='NOT_RUN',network_ready=c['create']['network_ready'],budget_accepted=c['create']['budget_accepted'])
    except Exception as error:result.update(reason=str(error) if type(error) is RuntimeError else type(error).__name__,phase=phase)
    M.save(out/'receipt.json',result);print(json.dumps(result,indent=2));print('Private delete review directory: '+str(out))
    return 0 if result['status'].startswith('PASS_') else 1

if __name__=='__main__':raise SystemExit(main())
