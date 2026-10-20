"""Only new read-only discovery boundaries; no cloud, SSH or SQL tests."""
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

s=importlib.util.spec_from_file_location('reader',Path(__file__).with_name('read_sources.py'))
R=importlib.util.module_from_spec(s);s.loader.exec_module(R)

class Sources(unittest.TestCase):
    def test_nested_terraform_output_is_a_candidate_not_an_acceptance(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'outputs.json';p.write_text(json.dumps({'b_outputs':{'value':dict.fromkeys(R.FOUNDATION,'placeholder')}}))
            x=R.scan([tmp]);g=x['candidates']['foundation_handoff']
            self.assertEqual(len(g['files']),1);self.assertFalse(g['auto_adopted'])
            self.assertEqual(g['files'][0]['currentness'],'NOT_ASSERTED')

    def test_distinct_candidates_do_not_choose_newest(self):
        with tempfile.TemporaryDirectory() as tmp:
            for i in range(2):
                (Path(tmp)/(str(i)+'.json')).write_text(json.dumps({'budget':{'value':i},'window':{}}))
            x=R.scan([tmp]);self.assertEqual(x['candidates']['cost_and_window']['distinct_contents'],2)
            self.assertFalse(x['candidates']['cost_and_window']['auto_adopted'])

    def test_report_does_not_contain_json_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp)/'cost.json').write_text(json.dumps({'budget':{'password':'NEVER_EXPORT_SENTINEL'},'window':{}}))
            self.assertNotIn('NEVER_EXPORT_SENTINEL',json.dumps(R.scan([tmp])))

    def test_transport_classifies_exact_lf_without_registering(self):
        with tempfile.TemporaryDirectory() as tmp:
            run=Path(tmp);base=run/'prestart-closeout';(base/'inputs').mkdir(parents=True)
            (run/'generator-link').mkdir()
            (base/'inputs/metadata-transport-receipt.json').write_text(json.dumps({'status':'PENDING_TASK_METADATA_TRANSPORT','reason':'EXACT_ORIGINAL_RESTRICTED_DISPATCH_REQUIRED'}))
            plain=b'print("bounded")\n'
            (base/'transport-settings.json').write_text(json.dumps({'original_dispatch_sha256':hashlib.sha256(plain).hexdigest()}))
            (run/'generator-link/restricted_dispatch.py').write_bytes(b'\n'+plain)
            x=R.transport(run)
            self.assertTrue(x['matches_exact_single_leading_lf_variant']);self.assertFalse(x['registration_retried'])
            self.assertFalse((base/'original-dispatch.json').exists())

if __name__=='__main__':unittest.main()
