"""Read an existing failed preflight and compile encrypted, held restore recipes.

No AWS/Kubernetes calls, permission changes, snapshots, Plan or Apply.
"""
import argparse
import base64
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import socket
import shutil
import subprocess
import tempfile

spec = importlib.util.spec_from_file_location('checkpoint', Path(__file__).with_name('prepare-rosa-checkpoint.py'))
B = importlib.util.module_from_spec(spec)
spec.loader.exec_module(B)
need = B.need
REGION = 'ap-northeast-2'
EXPIRED_PULL = 'seokpan-cloud-ecr-pull'


def identity(o):
    m = o['metadata']
    return '|'.join([o['apiVersion'], o['kind'], m.get('namespace', ''), m['name']])


def normalized_metadata(o):
    """Fresh metadata only; preserve logical ownership/tracking, never old IDs."""
    result = copy.deepcopy(o)
    result.pop('status', None)
    m = result['metadata']
    result['metadata'] = {k: copy.deepcopy(m[k]) for k in ('name', 'namespace', 'labels', 'annotations') if k in m}
    annotations = result['metadata'].get('annotations', {})
    for key in list(annotations):
        if key.startswith(('openshift.io/sa.scc.', 'operator.openshift.io/', 'kubectl.kubernetes.io/last-applied')):
            del annotations[key]
    if result['kind'] == 'ServiceAccount':
        result.pop('secrets', None)
        result['imagePullSecrets'] = [s for s in result.get('imagePullSecrets', []) if s.get('name') != EXPIRED_PULL]
        if not result['imagePullSecrets']:
            result.pop('imagePullSecrets', None)
    return result


def classify(o, contract):
    kind, m = o['kind'], o['metadata']
    ns, name = m.get('namespace'), m['name']
    cloud, argo = contract['cloud_namespace'], contract['argo_namespace']
    owned = bool(m.get('ownerReferences'))
    if kind == 'PersistentVolume':
        return 'SNAPSHOT_BACKED_NEW_VOLUME_PV_PVC'
    if kind == 'StatefulSet' and ns == 'openshift-monitoring' and name in ('alertmanager-main', 'prometheus-k8s'):
        return 'MONITORING_OPERATOR_REGENERATION'
    if kind in ('ClusterRole', 'ClusterRoleBinding', 'ClusterServiceVersion'):
        return 'OPERATOR_RECONCILE_COMPARE'
    if kind in ('Subscription', 'OperatorGroup') and ns == argo:
        return 'PINNED_OLM_BOOTSTRAP'
    if kind == 'OAuth':
        return 'SELECTED_IDP_MERGE_NEW_CALLBACK'
    if kind == 'Secret':
        if ns == cloud and name == EXPIRED_PULL:
            return 'ARCHIVE_ONLY_EXPIRED_PULL_NEVER_REATTACH'
        if o.get('type') == 'kubernetes.io/service-account-token' or owned or (ns == argo and name in (
                'argocd-secret','openshift-gitops-cluster','openshift-gitops-tls','openshift-gitops-ca')):
            return 'GENERATED_CREDENTIAL_REGENERATION'
        if (ns == cloud and name in ('backend-db-runtime', 'backend-redis-runtime')) or ns == 'openshift-config':
            return 'PROTECTED_SECRET_RESUPPLY'
        raise RuntimeError('UNREVIEWED_MANUAL_SECRET')
    if kind == 'Namespace' and name in (cloud, argo):
        return 'FRESH_NAMESPACE'
    if kind == 'Group' and name in contract['groups']:
        return 'SELECTED_GROUP_CONFIGURATION'
    if kind == 'ArgoCD' and ns == argo and name == contract['argo_name']:
        return 'ARGO_CR_SPEC_RESTORE'
    if kind=='ConfigMap' and name in ('kube-root-ca.crt','openshift-service-ca.crt'):
        return 'NEW_CLUSTER_CA_REGENERATION'
    if kind=='ServiceAccount' and (name in ('default','builder','deployer') or owned):
        return 'OPERATOR_RECONCILE_COMPARE'
    if kind=='RoleBinding' and name in ('system:deployers','system:image-builders','system:image-pullers'):
        return 'OPERATOR_RECONCILE_COMPARE'
    if ns == argo and kind in ('ConfigMap', 'Role', 'RoleBinding', 'ServiceAccount'):
        return 'OPERATOR_RECONCILE_COMPARE'
    if kind == 'Application' and ns == argo and name in contract['applications']:
        need(o.get('spec', {}).get('syncPolicy', {}).get('automated') is None
             and not o.get('operation'), 'NON_HELD_APPLICATION_REQUIRES_SEPARATE_REVIEW')
        return 'HELD_APPLICATION_NO_SYNC'
    if kind == 'AppProject' and ns == argo and name in contract['projects']:
        return 'PROJECT_DECLARATION'
    if ns == cloud and kind in ('Deployment', 'StatefulSet'):
        need(o.get('spec', {}).get('replicas') == 0, 'RUNNING_CLOUD_WORKLOAD_CANNOT_BE_RESTORED_AS_HELD')
        return 'ZERO_REPLICA_DECLARATION'
    if ns == cloud and kind in ('ConfigMap', 'ServiceAccount', 'Role', 'RoleBinding',
                              'NetworkPolicy', 'ServiceMonitor', 'PrometheusRule',
                              'Service', 'Route', 'PodDisruptionBudget'):
        return 'PLATFORM_AND_OBSERVABILITY_DECLARATION'
    if kind == 'ConfigMap' and (ns, name) in (
            ('openshift-monitoring', 'cluster-monitoring-config'),
            ('openshift-user-workload-monitoring', 'user-workload-monitoring-config')):
        return 'MONITORING_CONFIGURATION_AFTER_PVC_RESTORE'
    raise RuntimeError('UNREVIEWED_RESTORATION_OBJECT_KIND_OR_SCOPE')


def compile_recipe(base, gap):
    need(base.get('schema') == gap.get('schema') == 1, 'ACCEPTED_ARCHIVE_SCHEMA_REQUIRED')
    c = base['contract']
    rows = base['snapshots']+gap['snapshots']
    need(len(rows) == len({identity(o) for o in rows}), 'DUPLICATE_RESTORATION_OBJECT')
    pins = [o.get('spec', {}).get('version') for o in rows if o['kind'] == 'ClusterServiceVersion']
    need(pins == ['1.21.4'], 'ADOPTED_GITOPS_1_21_4_REQUIRED')
    decisions = [{'id': identity(o), 'action': classify(o, c)} for o in rows]
    return {'schema': 1, 'purpose': 'ROSA_HELD_RESTORATION_RECIPE',
            'origin_host': socket.gethostname(), 'files': [], 'snapshots': [],
            'contract': c, 'objects': rows, 'decisions': decisions,
            'operator_version': '1.21.4', 'backend_replicas': 0, 'frontend_replicas': 0,
            'automatic_sync': False, 'database_writes': False,
            'source_cutover': 'HELD', 'snapshot_map': 'INPUT_REQUIRED',
            'external_callback': 'NEW_CLUSTER_CALLBACK_MAPPING_REQUIRED', 'restoration_started': False}


def failure_diagnostic(directory, gap_directory):
    receipt = json.loads(B.private_file(directory/'receipt.json', 1048576))
    need(receipt.get('status') == 'STOP_MONITORING_SNAPSHOT_CAPTURE', 'EXISTING_FAILED_PREFLIGHT_REQUIRED')
    raw = B.private_file(directory/'aws-failure-private.log', 1048576).decode(errors='replace')
    operation = re.search(r'when calling the ([A-Za-z][A-Za-z0-9]+) operation', raw)
    explicit = re.search(r'not authorized to perform:\s*(ec2:[A-Za-z]+)', raw)
    action = explicit[1] if explicit else ('ec2:'+operation[1] if operation else None)
    need(action in ('ec2:DescribeVolumes', 'ec2:DescribeSnapshots', 'ec2:CreateSnapshot'),
         'CLASSIFIED_EBS_DENIAL_REQUIRED_NO_RAW_LOG')
    marker = gap_directory/'monitoring-snapshot-attempt.NO_AUTOMATIC_RETRY.json'
    journal = directory/'snapshot-journal.json'
    attempts = list(directory.glob('snapshot-*-attempt.json'))
    no_create = not marker.exists() and not journal.exists() and not attempts
    if re.search(r'explicit deny', raw, re.I):
        auth = 'EXPLICIT_DENY_OWNER_REVIEW_NO_BYPASS'
    elif re.search(r'no identity.based policy allows', raw, re.I):
        auth = 'IDENTITY_POLICY_ALLOW_MISSING'
    else:
        auth = 'AUTHORIZATION_DENIED_POLICY_LAYER_NOT_CONFIRMED'
    return {'failed_aws_action': action, 'authorization_class': auth,
            'this_tool_non_dry_run_snapshot_create_invoked': False if no_create else 'RECONCILIATION_REQUIRED',
            'creation_marker_present': marker.exists(), 'journal_present': journal.exists(),
            'raw_log_printed': False, 'aws_calls': 0}


def snapshot_policy(account, rows, gap_sha):
    need(re.fullmatch(r'[0-9]{12}', account), 'ACCOUNT_REQUIRED')
    need(len(rows) == 4 and len({r['volume_id'] for r in rows}) == 4
         and len({r['claim_uid'] for r in rows}) == 4
         and all(r['namespace'] == 'openshift-monitoring' and re.fullmatch(r'vol-[0-9a-f]+', r['volume_id']) for r in rows),
         'EXACT_FOUR_PROTECTED_VOLUME_INPUTS_REQUIRED')
    run = 'rosa-'+hashlib.sha256(gap_sha.encode()).hexdigest()[:16]
    region = {'aws:RequestedRegion': REGION}
    tagged = dict(region, **{'aws:RequestTag/seokpan-preservation-run': run,
        'aws:RequestTag/seokpan-preservation-scope': 'rosa-monitoring',
        'aws:RequestTag/seokpan-source-pvc-uid': sorted(r['claim_uid'] for r in rows)})
    snapshot = 'arn:aws:ec2:'+REGION+'::snapshot/*'
    statements = [
        {'Sid': 'ReadMonitoringVolumeAndSnapshotMetadata', 'Effect': 'Allow',
         'Action': ['ec2:DescribeVolumes', 'ec2:DescribeSnapshots'], 'Resource': '*',
         'Condition': {'StringEquals': region}},
        {'Sid': 'SnapshotOnlyFourCapturedVolumes', 'Effect': 'Allow', 'Action': 'ec2:CreateSnapshot',
         'Resource': sorted('arn:aws:ec2:'+REGION+':'+account+':volume/'+r['volume_id'] for r in rows),
         'Condition': {'StringEquals': region, 'Bool': {'ec2:Encrypted': 'true'}}},
        {'Sid': 'CreateOnlyTaggedMonitoringSnapshots', 'Effect': 'Allow', 'Action': 'ec2:CreateSnapshot',
         'Resource': snapshot, 'Condition': {'StringEquals': tagged,
            'ForAllValues:StringEquals': {'aws:TagKeys': ['seokpan-preservation-run', 'seokpan-preservation-scope', 'seokpan-source-pvc-uid']}}},
        {'Sid': 'TagOnlyDuringSnapshotCreation', 'Effect': 'Allow', 'Action': 'ec2:CreateTags',
         'Resource': snapshot, 'Condition': {'StringEquals': dict(tagged, **{'ec2:CreateAction': 'CreateSnapshot'})}}]
    return {'Version': '2012-10-17', 'Statement': statements}


def materialize_adopted_source(base, manifest, out):
    """Restore only exact TF source/lock bytes, never old State or saved Apply plans."""
    target=out/'adopted-source/terraform/rosa';target.mkdir(parents=True,mode=0o700)
    original=Path(manifest['tf_root'])
    selected={}
    for row in base['files']:
        path=Path(row['root'])/row['path']
        if path.parent!=original or not (path.name.endswith(('.tf','.tf.json')) or path.name=='.terraform.lock.hcl'):
            continue
        raw=base64.b64decode(row['data'],validate=True)
        need(hashlib.sha256(raw).hexdigest()==row['sha256'],'ADOPTED_SOURCE_INNER_HASH_REQUIRED')
        need(path.name not in selected or selected[path.name]==raw,'CONFLICTING_ADOPTED_SOURCE')
        selected[path.name]=raw
    need(selected and '.terraform.lock.hcl' in selected,'ADOPTED_SOURCE_AND_LOCK_REQUIRED')
    for name,raw in selected.items():(target/name).write_bytes(raw)
    text='\n'.join(name+':'+hashlib.sha256(selected[name]).hexdigest() for name in sorted(selected))
    need(hashlib.sha256(text.encode()).hexdigest()==manifest['source_hash'],'ADOPTED_TF_SOURCE_DIFFERS_FROM_SAVED_PLAN')
    return target


def lifecycle_config(a, base, manifest, recipe_archive, recipe_sha, out, binary):
    repository=Path(a.repository).resolve();scripts=repository/'scripts'
    need((scripts/'rosa-held-restoration.sh').is_file(),'CLONED_RESTORATION_SOURCE_REQUIRED')
    source=materialize_adopted_source(base,manifest,out)
    original=json.loads(B.private_file(manifest['var_file'],10485760))
    need(hashlib.sha256(B.private_file(manifest['var_file'],10485760)).hexdigest()==manifest['var_sha256'],
         'ADOPTED_INPUT_HASH_CHANGED')
    fresh=dict(original,worker_sg_binding=None,cluster_enabled=True)
    state=json.loads(B.private_file(Path(a.delete_manifest).parent/'before.tfstate'))
    version=state['outputs']['platform_handoff']['value'].get('current_version')
    need(isinstance(version,str) and re.fullmatch(r'4\.20\.[0-9]+',version),'ADOPTED_CURRENT_OPENSHIFT_VERSION_REQUIRED')
    fresh['openshift_version']=version
    variables=out/'recreate-inputs.tfvars.json';variables.write_bytes(B.canonical(fresh))
    admin_users=sorted(set(a.human_admin))
    known={user for o in base['snapshots'] if o['kind']=='Group' for user in o.get('users',[])}
    need(admin_users and set(admin_users)<=known,'HUMAN_ADMINS_MUST_BE_ACCEPTED_EXISTING_GROUP_MEMBERS')
    dependencies={name:hashlib.sha256((scripts/name).read_bytes()).hexdigest() for name in (
        'prepare-rosa-checkpoint.py','prepare-rosa-reconstruction.py','rosa-fresh-management.py',
        'rosa-gitops-bootstrap.py','rosa-held-restoration.py','rosa-lifecycle-plan.py','rosa-monitoring-volume-backup.py')}
    approval=out/'delete-approval.json'
    approval.write_bytes(B.canonical({'action':'delete','cluster_id':manifest['cluster_id'],
        'plan_sha256':manifest['plan_sha256'],'approved_by_user':False,'approval_reference':'',
        'encrypted_backup_accepted':False,'independent_decrypt_access_accepted':False,
        'pvc_data_disposition_accepted':False,'state_owner_accepted':False,
        'data_operations_released':False,'recreation_inputs_accepted':False}))
    config={'schema':1,'scope':'ROSA_ONLY','current_cluster_id':manifest['cluster_id'],
        'source_cluster_id':manifest['cluster_id'],'evidence_root':a.output_parent,
        'tf_data':manifest['tf_data'],'adopted_tf_source_hash':manifest['source_hash'],
        'auth':{'rosa_cli':a.rosa_cli,'tf_session_helper':a.session_helper,'oc_binary':a.oc_binary,
                'oc_sha256':hashlib.sha256(Path(a.oc_binary).read_bytes()).hexdigest()},
        'delete':{'manifest':a.delete_manifest,'approval_file':str(approval)},
        'create':{'var_file':str(variables),'tf_root':str(source),'cluster_name':fresh['cluster_name'],
                  'inputs_accepted':False,'network_ready':False,'budget_accepted':False,
                  'restore_driver':str(scripts/'rosa-held-restoration.sh'),
                  'restore_driver_sha256':hashlib.sha256((scripts/'rosa-held-restoration.sh').read_bytes()).hexdigest(),
                  'held_restoration_contract_accepted':False},
        'restoration':{'recipe_archive':str(recipe_archive),'recipe_sha256':recipe_sha,
                       'key_file':a.key_file,'sops_binary':binary,'dependency_sha256':dependencies,
                       'human_admins':admin_users,'snapshot_map':None,'snapshot_map_sha256':None,
                       'accepted_new_oauth_callbacks':None,'new_volume_permissions_accepted':False}}
    path=out/'rosa-lifecycle.json';path.write_bytes(B.canonical(config))
    return path


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('base-archive', 'base-sha256', 'gap-archive', 'gap-sha256', 'key-file',
                 'contract', 'delete-manifest', 'failed-run', 'output-parent'):
        p.add_argument('--'+name, required=True)
    p.add_argument('--sops')
    p.add_argument('--age-keygen')
    p.add_argument('--repository')
    p.add_argument('--rosa-cli')
    p.add_argument('--session-helper')
    p.add_argument('--oc-binary')
    p.add_argument('--human-admin',action='append',default=[])
    p.add_argument('--policy-reader',choices=('ansible',))
    a = p.parse_args()
    os.umask(0o077)
    out = Path(tempfile.mkdtemp(prefix='rosa-reconstruction-preparation-', dir=a.output_parent))
    result = {'status': 'STOP_RECONSTRUCTION_PREPARATION', 'aws_calls': 0, 'cluster_mutations': 0,
              'SQL': 0, 'previous_verification_repeated': False, 'deletion_ready': False}
    try:
        diagnostic = failure_diagnostic(Path(a.failed_run), Path(a.gap_archive).parent)
        result['snapshot_failure'] = diagnostic
        need(diagnostic['this_tool_non_dry_run_snapshot_create_invoked'] is False, 'SNAPSHOT_OUTCOME_RECONCILIATION_REQUIRED')
        binary = B.sops_binary(a, out)
        def decrypt(path, sha):
            raw = B.private_file(path, B.MAX_BYTES*3)
            need(hashlib.sha256(raw).hexdigest() == sha, 'ACCEPTED_ARCHIVE_HASH_REQUIRED')
            return json.loads(B.crypt(binary, raw, decrypt=True, key_file=a.key_file))
        base, gap = decrypt(a.base_archive, a.base_sha256), decrypt(a.gap_archive, a.gap_sha256)
        need(gap.get('base_archive_sha256') == a.base_sha256, 'BASE_GAP_BINDING_REQUIRED')
        recipe = compile_recipe(base, gap)
        recipe.update(base_archive_sha256=a.base_sha256, gap_archive_sha256=a.gap_sha256)
        manifest = json.loads(B.private_file(a.delete_manifest, 1048576))
        policy = snapshot_policy(manifest['account_id'], gap['volume_preservation_plan'], a.gap_sha256)
        (out/'monitoring-snapshot-policy.json').write_bytes(B.canonical(policy))
        inputs = {'enable_rosa_monitoring_preservation': True,
                  'rosa_monitoring_preservation_gap_sha256': a.gap_sha256,
                  'rosa_monitoring_preservation_sources': {r['volume_id']: r['claim_uid'] for r in gap['volume_preservation_plan']}}
        (out/'monitoring-snapshot-scope.tfvars.json').write_bytes(B.canonical(inputs))
        if a.policy_reader:
            import pwd
            pwd.getpwnam(a.policy_reader)
            acl=shutil.which('setfacl')
            need(acl,'EXISTING_ACL_TOOL_REQUIRED_NO_WORLD_READABLE_HANDOFF')
            handoff=Path(tempfile.mkdtemp(prefix='seokpan-monitoring-policy-'))
            for name in ('monitoring-snapshot-policy.json','monitoring-snapshot-scope.tfvars.json'):
                target=handoff/name;target.write_bytes((out/name).read_bytes());target.chmod(0o600)
                need(subprocess.run([acl,'-m','u:'+a.policy_reader+':r--',str(target)],capture_output=True).returncode==0,
                     'LOCAL_POLICY_READ_ACL_FAILED')
            need(subprocess.run([acl,'-m','u:'+a.policy_reader+':r-x',str(handoff)],capture_output=True).returncode==0,
                 'LOCAL_POLICY_DIRECTORY_ACL_FAILED')
            result['policy_handoff_directory']=str(handoff)
            result['policy_handoff_reader']=a.policy_reader
        contract = json.loads(B.private_file(a.contract, 262144))
        source = B.private_file(contract['recipient_source'], 262144)
        need(hashlib.sha256(source).hexdigest() == contract['recipient_source_sha256'], 'RECIPIENT_SOURCE_HASH_REQUIRED')
        recipients, _ = B.age_recipients(source)
        recipient = B.key_matched_recipient(recipients, a, out)
        plain = B.canonical(recipe)
        encrypted = B.crypt(binary, plain, recipient=recipient)
        archive = out/'held-restoration-recipe.sops.json'
        archive.write_bytes(encrypted)
        need(B.canonical(json.loads(B.crypt(binary, encrypted, decrypt=True, key_file=a.key_file))) == plain,
             'NEW_RECIPE_ROUNDTRIP_REQUIRED')
        counts = {}
        for row in recipe['decisions']:
            counts[row['action']] = counts.get(row['action'], 0)+1
        result.update(status='PASS_DENIAL_CLASSIFIED_AND_ENCRYPTED_HELD_RECIPE_PREPARED',
            classified_objects=len(recipe['decisions']), decisions=counts,
            unclassified_objects=0, recipe_archive=str(archive), recipe_sha256=hashlib.sha256(encrypted).hexdigest(),
            policy_file=str(out/'monitoring-snapshot-policy.json'), policy_sha256=hashlib.sha256(B.canonical(policy)).hexdigest(),
            terraform_scope_inputs=str(out/'monitoring-snapshot-scope.tfvars.json'),
            permission_apply='NOT_RUN', snapshots='NOT_RUN', restoration='NOT_RUN', independent_recipe_copy='NOT_RUN')
        if a.repository:
            need(all((a.rosa_cli,a.session_helper,a.oc_binary)),'EXISTING_NATIVE_TOOL_PATHS_REQUIRED')
            config=lifecycle_config(a,base,manifest,archive,result['recipe_sha256'],out,binary)
            result.update(lifecycle_config=str(config),delete_approval_auto_granted=False,
                create_network_budget_or_restoration_auto_accepted=False,
                adopted_tf_source_materialized=True,new_worker_binding=None)
        # Merge only ciphertext references; accepted previous copies are reused by
        # the independent-copy tool rather than decrypted again.
        handoff_path=Path(a.output_parent)/'rosa-preservation-handoff-latest.json'
        handoff=json.loads(B.private_file(handoff_path,262144))
        need(handoff.get('schema')==2,'EXISTING_SUPPLEMENT_HANDOFF_REQUIRED')
        rows=[row for row in handoff['archives'] if row['kind']!='restoration-recipe']
        need({row['kind'] for row in rows}<={'restoration-gaps','monitoring-snapshots'},'KNOWN_PREVIOUS_SUPPLEMENTS_REQUIRED')
        handoff['archives']=rows+[{'kind':'restoration-recipe','path':str(archive),'sha256':result['recipe_sha256']}]
        temporary=handoff_path.with_suffix('.new.json');temporary.write_bytes(B.canonical(handoff));temporary.chmod(0o600)
        os.replace(temporary,handoff_path)
        result['independent_handoff_file']=str(handoff_path)
    except Exception as error:
        result['status']='STOP_RECONSTRUCTION_PREPARATION'
        result['reason'] = str(error) if type(error) is RuntimeError else type(error).__name__
    (out/'receipt.json').write_bytes(B.canonical(result))
    print(json.dumps(result, indent=2))
    print('Private reconstruction preparation directory: '+str(out))
    return 0 if result['status'].startswith('PASS_') else 1


if __name__ == '__main__':
    raise SystemExit(main())
