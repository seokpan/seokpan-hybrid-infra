"""가짜 jth 홈에서 저장소 설치 경로와 변경 전 검증을 확인합니다."""
import hashlib
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest import mock

SPEC=importlib.util.spec_from_file_location('post_test_installer',
    Path(__file__).with_name('install_post_test_tools.py'))
M=importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)
NAMES=('public_hpa_export.py','finish_hpa_time_copy.py','finish_hpa_final_teardown.py',
       'prepare_b_final_delete.py','apply_b_final_delete.py',
       'b_final_residuals.py','run_b_final_delete_plan.sh')

class InstallerPaths(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root=Path(self.tmp.name)
        self.home=root/'jth';self.home.mkdir()
        self.source=root/'source';self.source.mkdir()
        self.target=self.home/'prestart-closeout';self.target.mkdir()
        for name in NAMES:
            (self.source/name).write_bytes(('# fixture '+name+'\n').encode())
        self.transport=b'# reviewed post-test transport\n'
        (self.source/'post_test_metadata_transport.py').write_bytes(self.transport)
        old=b'# accepted old transport\n'
        (self.target/'metadata_transport.py').write_bytes(old)
        patches=(mock.patch.object(M,'HERE',self.source),mock.patch.object(M,'BASE',self.target),
                 mock.patch.object(M,'OLD_TRANSPORT_SHA',hashlib.sha256(old).hexdigest()),
                 mock.patch.object(M,'raw',side_effect=lambda p:Path(p).read_bytes()),
                 mock.patch.object(M.Path,'home',return_value=self.home),
                 mock.patch.object(M.os,'getuid',return_value=1001,create=True),
                 mock.patch.object(M.os,'O_NOFOLLOW',getattr(M.os,'O_NOFOLLOW',0),create=True))
        for patch in patches:
            patch.start();self.addCleanup(patch.stop)

    def test_repo_named_transport_installs_to_runtime_name(self):
        self.assertFalse((self.source/'metadata_transport.py').exists())
        result=M.install()
        self.assertEqual(result['status'],'PASS_POST_TEST_TOOLS_CONNECTED_NOT_EXECUTED')
        self.assertEqual((self.target/'metadata_transport.py').read_bytes(),self.transport)
        self.assertFalse((self.target/'post_test_metadata_transport.py').exists())
        for name in NAMES:
            self.assertEqual((self.target/name).read_bytes(),(self.source/name).read_bytes())

    def test_missing_transport_leaves_targets_unchanged(self):
        (self.source/'post_test_metadata_transport.py').unlink()
        before={p.name:p.read_bytes() for p in self.target.iterdir()}
        with self.assertRaises(FileNotFoundError):
            M.install()
        self.assertEqual(before,{p.name:p.read_bytes() for p in self.target.iterdir()})

    def test_unknown_existing_transport_blocks_before_any_copy(self):
        (self.target/'metadata_transport.py').write_bytes(b'# unknown local change\n')
        before={p.name:p.read_bytes() for p in self.target.iterdir()}
        with self.assertRaisesRegex(RuntimeError,'UNEXPECTED_EXISTING_SOURCE_PRESERVED'):
            M.install()
        self.assertEqual(before,{p.name:p.read_bytes() for p in self.target.iterdir()})

    def test_completed_install_is_reused(self):
        M.install()
        before={p.name:p.read_bytes() for p in self.target.iterdir()}
        result=M.install()
        self.assertTrue(all(x=='REUSED_IDENTICAL_SOURCE' for x in result['files'].values()))
        self.assertEqual(before,{p.name:p.read_bytes() for p in self.target.iterdir()})

if __name__=='__main__':
    unittest.main()
