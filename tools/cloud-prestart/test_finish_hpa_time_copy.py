import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('closeout',Path(__file__).with_name('finish_hpa_time_copy.py'))
M=importlib.util.module_from_spec(spec)
spec.loader.exec_module(M)

class EvidenceBoundaries(unittest.TestCase):
    def test_public_sample_removes_pod_address_and_credential_fields(self):
        row={'utc':'2026-10-20T14:00:00+00:00','status':'PASS_HPA_GUARD',
             'hpa':{'metadata':{'private_name':'hidden'},'status':{'currentReplicas':4}},
             'db':{'connections_total':12,'password':'hidden','host':'private.invalid'},
             'pods':[{'uid':'private','status':{'podIP':'192.0.2.5'}}]}
        result=M.public_row(row)
        self.assertEqual(result['hpa']['current'],4)
        self.assertEqual(result['db']['connections_total'],12)
        self.assertNotIn('password',result['db'])
        self.assertNotIn('host',result['db'])
        self.assertNotIn('pods',result)

    def test_error_text_with_private_location_is_withheld(self):
        result=M.public_row({'utc':'2026-10-20T14:00:00+00:00',
            'status':'STOP_HPA_GUARD','reason':'failed at private.invalid token=value'})
        self.assertEqual(result['reason'],'DETAIL_WITHHELD')

    def test_unknown_previous_write_never_runs_again(self):
        with tempfile.TemporaryDirectory() as folder:
            out=Path(folder)
            M.save(out/'restore.attempted.json',{'status':'UNKNOWN'})
            with patch.object(M.subprocess,'run') as run:
                with self.assertRaisesRegex(RuntimeError,'OUTCOME_REQUIRES_RECONCILIATION'):
                    M.command(['not-executed'],out,'restore')
                run.assert_not_called()

    def test_completed_write_reuses_receipt_without_calling_api(self):
        with tempfile.TemporaryDirectory() as folder:
            out=Path(folder)
            M.save(out/'stop.complete.json',{'status':'STOP_REQUESTED'})
            with patch.object(M.subprocess,'run') as run:
                self.assertEqual(M.command(['not-executed'],out,'stop')['status'],'STOP_REQUESTED')
                run.assert_not_called()

    def test_symlink_is_not_read_as_evidence(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'link'
            try:
                path.symlink_to(Path(folder)/'missing')
            except (OSError,NotImplementedError):
                self.skipTest('Local filesystem does not permit symlinks')
            with self.assertRaises(OSError):
                M.raw(path)

if __name__=='__main__':
    unittest.main()

