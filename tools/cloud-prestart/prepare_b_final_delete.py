"""시험 종료 후 현재 ROSA 루트의 B 전용 삭제 Plan만 준비합니다."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace

ROOT = Path.home()/'.local/share/seokpan-rosa-plan-inputs-5psj2l62'
RUN = ROOT/'controller-run-bundle-K2Tx7HgB'
CID = '2ti8n9c2j2em1h6jbb34kc0dq95dv4fi'
SNAPSHOTS = ['snap-07cb6c69e658e9d1d','snap-041f8647778570dca',
             'snap-0f58e995b9f906d39','snap-06289fa7af87099c0']

def need(ok, code):
    if not ok:
        raise RuntimeError(code)

def read(path):
    fd = os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    with os.fdopen(fd,'rb') as stream:
        info = os.fstat(stream.fileno())
        need(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
             and info.st_size <= 33554432,'OWNED_BOUNDED_INPUT_REQUIRED')
        return json.load(stream)

def save(path, value):
    fd = os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'w') as stream:
        json.dump(value,stream,indent=2)

def prepare():
    dispatch = read(RUN/'hpa-active-time-copy.json')
    final = Path(dispatch['directory'])/'final-closeout'
    stopped = read(final/'receipt.json')
    need(stopped['status'] == 'PASS_RUN_STOPPED_RESTORED_EVIDENCE_PRESERVED_NOT_TEST_VERDICT'
         and stopped['restoration_verified'] is True,'STOPPED_RESTORED_EVIDENCE_REQUIRED')
    pointer = read(RUN/'today-recreate-dispatch-20261020.json')
    config = read(pointer['config'])
    source = Path(pointer['ops'])/'scripts/rosa-lifecycle-plan.py'
    need(not source.is_symlink() and source.is_file(),'EXISTING_LIFECYCLE_PLAN_SOURCE_REQUIRED')
    module_spec = importlib.util.spec_from_file_location('b_final_rosa_plan',source)
    lifecycle = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(lifecycle)
    out = Path(tempfile.mkdtemp(prefix='b-final-delete-plan-',dir=ROOT))
    save(out/'scope-and-disposition.json',{
        'cluster_id':CID, 'scope':'B_ROSA_ROOT_ONLY',
        'A_foundation_data_vpn_nat_eip_s3_bootstrap_excluded':True,
        'other_owner_ecr_excluded':True,
        'historical_B_monitoring_snapshots_separate_exact_candidates':SNAPSHOTS,
        'snapshot_deletion_in_this_plan':False,
        'cloud_backup_retention_requested':False,
        'github_publication_proof_present':(final/'github-publication.json').exists(),
        'post_test_receipt_sha256':hashlib.sha256((final/'receipt.json').read_bytes()).hexdigest(),
        'apply_started':False})
    args = SimpleNamespace(action='delete',cluster_id=CID,
        tf_root=config['create']['tf_root'], tf_data=config['tf_data'],
        var_file=config['create']['var_file'])
    try:
        result = lifecycle.prepare(args,out)
        result.pop('protected_files_require_encrypted_independent_copy',None)
        result.update(status='PASS_B_ROSA_DELETE_PLAN_ONLY_NOT_APPLIED',
            B_scope_only=True,A_resources_excluded=True,
            snapshot_deletion_started=False,approval_required=True,
            github_publication_required_before_apply=True,
            billing_zero_confirmed=False)
        save(out/'receipt.json',result)
        if not (RUN/'b-final-delete-plan.json').exists():
            save(RUN/'b-final-delete-plan.json',{'directory':str(out)})
        print(json.dumps(result,indent=2))
        print('Private B delete Plan directory: '+str(out))
    except Exception as error:
        save(out/'receipt.json',{'status':'STOP_B_FINAL_DELETE_PLAN',
            'reason':str(error) if type(error) is RuntimeError else type(error).__name__,
            'automatic_retry':False,'apply_started':False})
        print(json.dumps(read(out/'receipt.json'),indent=2))
        print('Private B delete Plan directory: '+str(out))
        raise SystemExit(2)

if __name__ == '__main__':
    os.umask(0o077)
    parser = argparse.ArgumentParser()
    parser.add_argument('--prepare-plan',action='store_true')
    args = parser.parse_args()
    need(args.prepare_plan,'EXPLICIT_PLAN_PREPARATION_FLAG_REQUIRED')
    need(not (RUN/'b-final-delete-plan.json').exists(),'EXISTING_DELETE_PLAN_REUSE_NO_REPEAT')
    prepare()

