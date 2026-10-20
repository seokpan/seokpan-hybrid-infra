import importlib.util
import io
import json
from pathlib import Path
import unittest
import zipfile

BASE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('external_receiver',BASE/'receive_external_hpa_results.py')
M = importlib.util.module_from_spec(spec);spec.loader.exec_module(M)

class ExternalResults(unittest.TestCase):
    def setUp(self):
        receipt = {'run_id':'fixture','binding':{'cluster_id':'fixture-cluster'},
                   'control_sha256':'a'*64,'status':'ABORTED_HPA_LOAD','in_flight_at_end':0}
        counts = {k:receipt[k] for k in ('run_id','binding','control_sha256')}
        self.files = {'receipt.json':json.dumps(receipt).encode(),
                      'counts.json':json.dumps(counts).encode(),'requests.jsonl':b'',
                      'events.jsonl':b'','generator.jsonl':b''}
        self.data = self.rebuilt(True)
        self.config = {'run_id':receipt['run_id'],'binding':receipt['binding']}
        self.config_hash = receipt['control_sha256']

    def rebuilt(self, renew_manifest=False):
        if renew_manifest:
            self.files['SHA256SUMS.json'] = json.dumps({n:M.sha(v) for n,v in self.files.items()
                                                       if n!='SHA256SUMS.json'}).encode()
        stream = io.BytesIO()
        with zipfile.ZipFile(stream,'w') as archive:
            for name,data in self.files.items():
                archive.writestr(name,data)
        return stream.getvalue()

    def check(self,data):
        return M.unpack(data,M.sha(data),self.config,self.config_hash)

    def test_valid_stopped_archive(self):
        self.assertEqual(set(self.check(self.data)),M.NAMES)

    def test_archive_hash_mismatch(self):
        with self.assertRaisesRegex(RuntimeError,'ARCHIVE_HASH_MISMATCH'):
            M.unpack(self.data,'0'*64,self.config,self.config_hash)

    def test_internal_hash_mismatch(self):
        self.files['requests.jsonl'] += b'\n'
        with self.assertRaisesRegex(RuntimeError,'INTERNAL_HASH_MISMATCH'):
            self.check(self.rebuilt())

    def test_foreign_binding(self):
        receipt = json.loads(self.files['receipt.json'])
        receipt['binding']['cluster_id'] = 'foreign'
        self.files['receipt.json'] = json.dumps(receipt).encode()
        with self.assertRaisesRegex(RuntimeError,'SAME_ACCEPTED_RUN_REQUIRED'):
            self.check(self.rebuilt(True))

    def test_unfinished_requests(self):
        receipt = json.loads(self.files['receipt.json']);receipt['in_flight_at_end'] = 1
        self.files['receipt.json'] = json.dumps(receipt).encode()
        with self.assertRaisesRegex(RuntimeError,'ACTUAL_STOPPED_GENERATOR_REQUIRED'):
            self.check(self.rebuilt(True))

    def test_unexpected_path(self):
        self.files['../unexpected'] = b'bad'
        with self.assertRaisesRegex(RuntimeError,'EXACT_SIX_REGULAR_RESULT_FILES_REQUIRED'):
            self.check(self.rebuilt())

if __name__=='__main__':
    unittest.main()

