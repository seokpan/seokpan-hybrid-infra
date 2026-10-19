"""New read-only finalization and permission-probe boundaries."""
import argparse
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('finalize',Path(__file__).with_name('rosa-monitoring-finalize.py'))
M=importlib.util.module_from_spec(spec);spec.loader.exec_module(M)
def row():
    return {'snapshot_id':'snap-123','snapshot_state':'completed','source_az':'ap-northeast-2a',
            'size_gib':10,'source_kms_key':'arn:aws:kms:ap-northeast-2:123456789012:key/abc'}

class Finalization(unittest.TestCase):
    def test_status_command_cannot_capture_or_wait_again(self):
        a=argparse.Namespace(root='/fixture',key_file='/key',contract='/contract',resume_journal='/journal',age_keygen=None)
        with patch.object(M,'read',return_value={'rosa_monitoring_preservation_gap_sha256':'f'*64}):
            cmd=M.status_command(a,{'terraform_scope_inputs':'/scope'},
                {'delete':{'manifest':'/manifest'},'restoration':{'sops_binary':'/sops'}},'/driver')
        self.assertIn('status',cmd);self.assertNotIn('capture',cmd);self.assertEqual(cmd[cmd.index('--wait-seconds')+1],'0')
    def test_volume_probe_always_encrypted_dry_run_and_exact_source(self):
        cmd=M.volume_dry_run(row(),'preflight-only')
        self.assertEqual(cmd.count('--dry-run'),1);self.assertIn('--encrypted',cmd);self.assertEqual(cmd[cmd.index('--snapshot-id')+1],'snap-123')
    def test_pending_snapshot_or_foreign_region_stops_before_request(self):
        for field,value in [('snapshot_state','pending'),('source_az','us-east-1a'),('source_kms_key','*')]:
            r=row();r[field]=value
            with self.assertRaises(RuntimeError):M.volume_dry_run(r,'preflight-only')
    def test_probe_rejects_real_create_or_delete(self):
        with patch.object(M.subprocess,'run') as call:
            for args in (['ec2','create-volume'],['ec2','delete-snapshot','--dry-run'],['iam','put-role-policy']):
                with self.assertRaisesRegex(RuntimeError,'READ_OR_DRY_RUN_ONLY'):M.probe(args,Path('/unused'),0)
            call.assert_not_called()
    def test_denial_is_sanitized_and_not_retried(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(M.subprocess,'run',return_value=subprocess.CompletedProcess([],254,b'',b'An error occurred (UnauthorizedOperation) private-detail')) as call:
                result=M.probe(M.volume_dry_run(row(),'preflight-only'),Path(tmp),0)
            self.assertEqual(call.call_count,1);self.assertEqual(result['error_code'],'UnauthorizedOperation');self.assertNotIn('private-detail',str(result))
    def fixture(self,root,pending=False):
        recon=root/'recon';recon.mkdir();source=root/'repository/scripts';source.mkdir(parents=True)
        config=root/'lifecycle.json';journal=root/'snapshot-journal.json';accepted=recon/'receipt.json'
        M.save(config,{'scope':'ROSA_ONLY','source_cluster_id':'old','create':{'restore_driver':str(source/'driver.sh')},
            'delete':{'manifest':'/manifest'},'restoration':{'sops_binary':'/sops'}})
        M.save(accepted,{'classified_objects':135,'unclassified_objects':0,'lifecycle_config':str(config),'policy_sha256':'f'*64})
        M.save(recon/'monitoring-policy-accepted.json',{'status':'PASS_EXACT_MONITORING_SNAPSHOT_POLICY_APPLIED_AND_READBACK','policy_sha256':'f'*64})
        M.save(journal,{'cluster_id':'old','volumes':[row() for _ in range(4)]})
        a=argparse.Namespace(root=str(root),reconstruction_receipt=str(accepted),resume_journal=str(journal),key_file='/key',contract='/contract',age_keygen=None)
        out=root/'out';out.mkdir();archive=root/'map.sops.json';archive.write_bytes(b'encrypted-fixture')
        status={'status':'PENDING_FOUR_SNAPSHOTS_COMPLETION_READ_ONLY_RESUME'} if pending else {
            'status':'PASS_FOUR_ENCRYPTED_EBS_SNAPSHOTS_AND_RESTORE_MAP','snapshots_completed':4,
            'archive':str(archive),'archive_sha256':M.sha(archive),'private_result_directory':str(root)}
        M.save(root/'rosa-preservation-handoff-latest.json',{'archives':[{'kind':'monitoring-snapshots','path':str(archive),'sha256':M.sha(archive)}]})
        return a,out,status,config
    def test_pending_returns_without_permission_probe_or_map_connection(self):
        with tempfile.TemporaryDirectory() as tmp:
            a,out,status,config=self.fixture(Path(tmp),True)
            with patch.object(M,'pin'),patch.object(M,'status_command',return_value=['status']),patch.object(M.subprocess,'run',return_value=subprocess.CompletedProcess([],0,json.dumps(status).encode(),b'')),patch.object(M,'permission_preflight') as probe:
                self.assertEqual(M.execute(a,out),status);probe.assert_not_called()
            self.assertNotIn('snapshot_map',M.read(config)['restoration'])
    def test_completed_map_connects_once_and_cached_rerun_reuses_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            a,out,status,config=self.fixture(Path(tmp))
            with patch.object(M,'pin'),patch.object(M,'status_command',return_value=['status']),patch.object(M.subprocess,'run',return_value=subprocess.CompletedProcess([],0,json.dumps(status).encode(),b'')) as call,patch.object(M,'permission_preflight',return_value={'new_volume_permissions_accepted':False}):
                first=M.execute(a,out);second=M.execute(a,out)
                self.assertEqual(call.call_count,1)
            self.assertTrue(first['snapshot_map_connected_to_lifecycle_config']);self.assertTrue(second['permission_preflight_reused']);self.assertFalse(second['deletion_ready'])
            self.assertEqual(M.read(config)['restoration']['snapshot_map'],status['archive'])
    def test_permission_failure_preserves_completed_map_and_handoff(self):
        with tempfile.TemporaryDirectory() as tmp:
            a,out,status,config=self.fixture(Path(tmp))
            with patch.object(M,'pin'),patch.object(M,'status_command',return_value=['status']),patch.object(M.subprocess,'run',return_value=subprocess.CompletedProcess([],0,json.dumps(status).encode(),b'')),patch.object(M,'permission_preflight',side_effect=RuntimeError('FIXTURE_DENIAL')):
                result=M.execute(a,out)
            self.assertEqual(result['status'],'PASS_FOUR_ENCRYPTED_EBS_SNAPSHOTS_AND_RESTORE_MAP')
            self.assertFalse(result['restore_permission_preflight']['new_volume_permissions_accepted'])
            self.assertTrue((Path(tmp)/'snapshot-finalization-accepted.json').is_file())

if __name__=='__main__':unittest.main()
