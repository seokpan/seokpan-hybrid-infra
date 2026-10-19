"""Cloneable ROSA-only lifecycle entry points; private input and approval gates."""
import argparse
import datetime as dt
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import shutil
import tempfile

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('rosa_scoped_lifecycle', HERE/'rosa-lifecycle-plan.py')
L = importlib.util.module_from_spec(spec)
spec.loader.exec_module(L)
AUTH_HELPER_BLOB = '608a765e2ba40b472b2cb431223b48c396c00866'

def require(ok, code):
    if not ok: raise RuntimeError(code)

def regular(path, executable=False):
    p = Path(path)
    require(p.is_file() and not p.is_symlink(), 'REGULAR_PINNED_TOOL_REQUIRED')
    require(not any(c in str(p) for c in '\r\n\0'), 'SINGLE_LINE_PATH_REQUIRED')
    if executable: require(os.access(p, os.X_OK), 'EXECUTABLE_TOOL_REQUIRED')
    return p


def restoration_inputs(c):
    d, r = c['create'], c.get('restoration', {})
    driver = regular(d.get('restore_driver', ''))
    require(L.sha(driver) == d.get('restore_driver_sha256'), 'REVIEWED_RESTORE_DRIVER_REQUIRED')
    require(d.get('held_restoration_contract_accepted') is True, 'HELD_RESTORATION_CONTRACT_REQUIRED')
    require(r.get('new_volume_permissions_accepted') is True, 'NEW_VOLUME_RESTORE_PERMISSIONS_REQUIRED')
    require(L.sha(regular(r.get('recipe_archive',''))) == r.get('recipe_sha256'), 'PINNED_RECIPE_REQUIRED')
    require(L.sha(regular(r.get('snapshot_map',''))) == r.get('snapshot_map_sha256'), 'COMPLETED_SNAPSHOT_MAP_REQUIRED')
    dependencies=r.get('dependency_sha256',{})
    require(set(dependencies)=={'prepare-rosa-checkpoint.py','prepare-rosa-reconstruction.py','rosa-fresh-management.py',
        'rosa-gitops-bootstrap.py','rosa-held-restoration.py','rosa-lifecycle-plan.py','rosa-monitoring-volume-backup.py'},
        'COMPLETE_RESTORATION_DEPENDENCY_SET_REQUIRED')
    for name,sha in dependencies.items():
        require(L.sha(HERE/name)==sha, 'RESTORATION_DEPENDENCY_CHANGED')
    require(L.sha(regular(c['auth'].get('oc_binary',''),executable=True))==c['auth'].get('oc_sha256'),'PINNED_OC_REQUIRED')
    regular(r.get('key_file',''));regular(r.get('sops_binary',''),executable=True)
    require(shutil.which('ansible-playbook'),'ANSIBLE_BOOTSTRAP_REQUIRED')
    module_spec=importlib.util.spec_from_file_location('checkpoint_preflight',HERE/'prepare-rosa-checkpoint.py')
    checkpoint=importlib.util.module_from_spec(module_spec);module_spec.loader.exec_module(checkpoint)
    # Validate the completed encrypted map before creating a billed cluster.
    snapshots=json.loads(checkpoint.crypt(r['sops_binary'],regular(r['snapshot_map']).read_bytes(),decrypt=True,key_file=r['key_file']))
    require(snapshots.get('purpose')=='ROSA_MONITORING_SNAPSHOT_MAP' and snapshots.get('volume_data_backed_up') is True,
            'COMPLETED_ENCRYPTED_SNAPSHOT_MAP_REQUIRED')
    journal=snapshots.get('volume_preservation',{})
    require(journal.get('cluster_id')==c['source_cluster_id'] and len(journal.get('volumes',[]))==4
            and all(row.get('snapshot_state')=='completed' for row in journal['volumes']),
            'EXACT_OLD_CLUSTER_FOUR_COMPLETED_SNAPSHOTS_REQUIRED')

def settings(config, action, repository):
    c = L.read_json(config)
    require(c.get('schema') == 1, 'LIFECYCLE_CONFIG_SCHEMA_REQUIRED')
    require(c.get('scope') == 'ROSA_ONLY', 'ROSA_ONLY_SCOPE_REQUIRED')
    auth = c.get('auth', {})
    rosa = regular(auth.get('rosa_cli', ''), executable=True)
    helper = regular(auth.get('tf_session_helper', ''))
    raw = helper.read_bytes()
    blob = hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()
    require(blob == AUTH_HELPER_BLOB, 'REVIEWED_AUTH_HELPER_REQUIRED')
    require(action in ('delete', 'create', 'restore'), 'KNOWN_ACTION_REQUIRED')
    if action == 'delete':
        d = c.get('delete', {})
        m = L.read_json(d.get('manifest', ''))
        a = L.read_json(d.get('approval_file', ''))
        require(m.get('action') == 'delete' and m.get('cluster_id') == c.get('current_cluster_id'), 'CURRENT_ROSA_DELETE_MANIFEST_REQUIRED')
        require(a.get('approved_by_user') is True and a.get('action') == 'delete'
                and a.get('cluster_id') == m['cluster_id'] and a.get('plan_sha256') == m.get('plan_sha256')
                and len(a.get('approval_reference', '')) >= 8, 'EXPLICIT_PLAN_BOUND_USER_DELETE_APPROVAL_REQUIRED')
        require(all(a.get(k) is True for k in L.approval_gates('delete')), 'PRESERVATION_GAPS_BLOCK_DELETE')
    else:
        d = c.get('create', {})
        root=Path(d.get('tf_root',''))
        require(root.is_dir() and L.source_hash(root)==c.get('adopted_tf_source_hash'),'EXACT_ADOPTED_TF_SOURCE_REQUIRED')
        if action=='restore':
            restoration_inputs(c)
            h=L.read_json(d.get('last_platform_handoff',''))
            require(h.get('cluster_id')==c['current_cluster_id'] and h.get('cluster_id')!=c['source_cluster_id']
                    and h.get('state')=='ready','CURRENT_NEW_CLUSTER_HANDOFF_REQUIRED')
            return c, {'rosa_cli':str(rosa),'tf_session_helper':str(helper)}
        v = L.read_json(d.get('var_file', ''))
        require(v.get('cluster_enabled') is True and v.get('worker_sg_binding') is None, 'NEW_CLUSTER_UNBOUND_INPUT_REQUIRED')
        require(v.get('cluster_name') == d.get('cluster_name'), 'NEW_CLUSTER_NAME_REQUIRED')
        require(all(d.get(k) is True for k in ('inputs_accepted', 'network_ready', 'budget_accepted')), 'CREATE_INPUT_NETWORK_BUDGET_REQUIRED')
        restoration_inputs(c)
        require(c.get('current_cluster_id')==c.get('source_cluster_id'), 'CREATED_CLUSTER_USE_RESTORE_ENTRY_NO_RECREATE')
    return c, {'rosa_cli': str(rosa), 'tf_session_helper': str(helper)}

def save(path, value):
    path.write_text(json.dumps(value, indent=2), encoding='utf-8');path.chmod(0o600)


def restore(c, handoff_path, out):
    d=c['create'];handoff=L.read_json(handoff_path)
    require(L.sha(d['restore_driver']) == d['restore_driver_sha256'], 'RESTORE_DRIVER_CHANGED_STOP')
    receipt = out/'held-restoration-receipt.json'
    process = subprocess.run(['bash', d['restore_driver'], '--platform-handoff',str(handoff_path),
        '--lifecycle-config',str(c['_config_path']), '--receipt',str(receipt)], capture_output=True, timeout=14400)
    (out/'restore-private.log').write_bytes(process.stdout+process.stderr)
    if receipt.is_file():
        state=L.read_json(receipt)
        if state.get('reason')=='NEW_OAUTH_CALLBACK_ACCEPTANCE_PENDING_USE_RESTORE_RESUME':
            return {'status':'PENDING_NEW_OAUTH_CALLBACK_ACCEPTANCE','cluster_created':True,
                    'restoration_receipt':str(receipt),'next_entry':'rosa-restore.sh','application_started':False}
    require(process.returncode == 0 and receipt.is_file(), 'RESTORATION_PENDING_NO_AUTOMATIC_RECREATE')
    restored = L.read_json(receipt)
    require(restored.get('status') == 'PASS_ROSA_HELD_RESTORATION' and restored.get('cluster_id') == handoff['cluster_id']
            and restored.get('backend_replicas') == 0 and restored.get('frontend_replicas') == 0
            and restored.get('automatic_sync') is False and restored.get('worker_data_binding_verified') is True,
            'HELD_RESTORATION_NOT_ACCEPTED')
    return {'status':'PASS_ROSA_CREATE_RESTORE_AND_PROTECTED_HANDOFF',
            'handoff_file':str(handoff_path),'restoration_receipt':str(receipt),
            'application_started':False,'network_creation':'EXCLUDED_SEPARATE_OWNER'}

def run(c, action, repository, out):
    if action=='restore':
        return restore(c,Path(c['create']['last_platform_handoff']),out)
    if action == 'delete':
        d = c['delete']
        args = argparse.Namespace(manifest=d['manifest'], approval_file=d['approval_file'])
        result = L.apply(args, out)
        m = L.read_json(d['manifest'])
        env = L.environment(Path(m['tf_root']), Path(m['tf_data']))
        state = json.loads(L.call(['terraform', '-chdir='+m['tf_root'], 'state', 'pull'], env, out).stdout)
        require(not any(x.get('mode') == 'managed' and x.get('instances') for x in state.get('resources', [])), 'ROSA_STATE_RESOURCES_REMAIN_RECONCILE_NO_RETRY')
        return {**result, 'status': 'PASS_ROSA_DESTROY_APPLY_AND_ROOT_STATE_EMPTY',
                'network_cleanup': 'EXCLUDED_SEPARATE_OWNER', 'billing_resource_reconciliation': 'REQUIRES_ROSA_OWNED_EC2_LB_EBS_CHECK',
                'handoff_file': str(out/'receipt.json')}
    d = c['create']
    args = argparse.Namespace(tf_root=d['tf_root'], tf_data=c['tf_data'],
        var_file=d['var_file'], action='create', cluster_id='NEW:'+d['cluster_name'])
    result = L.prepare(args, out)
    save(out/'plan-receipt.json', result)
    m = L.read_json(out/'plan-manifest.json')
    approval = {'action': 'create', 'cluster_id': m['cluster_id'], 'plan_sha256': m['plan_sha256'],
        'approved_by_user': True, 'approval_reference': 'USER_LAUNCHED_CREATE_REQUIRES_INTERACTIVE_PLAN_PHRASE',
        'recreation_inputs_accepted': d['inputs_accepted'], 'network_ready': d['network_ready'], 'budget_accepted': d['budget_accepted']}
    save(out/'create-approval.json', approval)
    L.apply(argparse.Namespace(manifest=str(out/'plan-manifest.json'), approval_file=str(out/'create-approval.json')), out)
    env = L.environment(Path(args.tf_root), Path(args.tf_data))
    handoff = json.loads(L.call(['terraform', '-chdir='+args.tf_root, 'output', '-json', 'platform_handoff'], env, out).stdout)
    require(isinstance(handoff.get('cluster_id'), str) and handoff.get('state') == 'ready', 'NEW_ROSA_READY_HANDOFF_REQUIRED')
    save(out/'platform-handoff.json', handoff)
    require(handoff['cluster_id']!=c['source_cluster_id'],'DIFFERENT_NEW_CLUSTER_REQUIRED')
    d['last_platform_handoff']=str(out/'platform-handoff.json');c['current_cluster_id']=handoff['cluster_id']
    # Persist the new identity before any bootstrap; a later interruption must
    # resume restoration rather than create another paid cluster.
    target=Path(c['_config_path']);temporary=target.with_suffix('.new.json')
    save(temporary,{k:v for k,v in c.items() if not k.startswith('_')});os.replace(temporary,target)
    return restore(c,out/'platform-handoff.json',out)

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('preflight', 'run'))
    parser.add_argument('--action', choices=('delete', 'create', 'restore'), required=True)
    parser.add_argument('--config', required=True)
    parser.add_argument('--repository', required=True)
    a = parser.parse_args()
    os.umask(0o077)
    out = None
    try:
        c, paths = settings(a.config, a.action, a.repository)
        if a.mode == 'preflight':
            print(json.dumps(paths)); return 0
        parent = Path(c['evidence_root'])
        require(parent.is_dir() and not parent.is_symlink(), 'EXISTING_PRIVATE_EVIDENCE_ROOT_REQUIRED')
        out = Path(tempfile.mkdtemp(prefix='rosa-'+a.action+'-', dir=parent))
        c['_config_path'] = str(Path(a.config).resolve())
        result = run(c, a.action, a.repository, out)
    except Exception as error:
        result = {'status': 'STOP_ROSA_LIFECYCLE_ENTRY', 'reason': str(error) if type(error) is RuntimeError else type(error).__name__,
                  'automatic_retry': False, 'network_mutations': False}
    if out: save(out/'receipt.json', result)
    print(json.dumps(result, indent=2))
    if out: print('Private result directory: '+str(out))
    return 1 if result['status'].startswith('STOP_') else 0

if __name__ == '__main__': raise SystemExit(main())
