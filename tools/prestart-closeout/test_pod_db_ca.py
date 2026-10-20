import copy
import importlib.util
from pathlib import Path
import unittest

spec=importlib.util.spec_from_file_location('ca_observer',Path(__file__).with_name('pod_db_observer.py'))
M=importlib.util.module_from_spec(spec);spec.loader.exec_module(M)

class DatabaseCaTests(unittest.TestCase):
    def setUp(self):
        self.pod={'spec':{'containers':[{'name':'backend','volumeMounts':[
            {'name':'db-ca','mountPath':'/etc/seokpan/database-ca','readOnly':True}]}],
            'volumes':[{'name':'db-ca','configMap':{'name':'backend-database-ca',
                        'items':[{'key':'ca.crt','path':'bundle/ca.pem'}]}}]}}

    def test_uses_live_mount_and_item_mapping(self):
        self.assertEqual(M.database_ca_path(self.pod),'/etc/seokpan/database-ca/bundle/ca.pem')

    def test_supports_default_configmap_key_projection(self):
        self.pod['spec']['volumes'][0]['configMap'].pop('items')
        self.assertEqual(M.database_ca_path(self.pod),'/etc/seokpan/database-ca/ca.crt')

    def test_rejects_missing_or_writable_or_subpath_mount(self):
        for change in ('missing','writable','subpath'):
            pod=copy.deepcopy(self.pod);mount=pod['spec']['containers'][0]['volumeMounts'][0]
            if change=='missing':pod['spec']['volumes'][0]['configMap']['name']='other'
            if change=='writable':mount['readOnly']=False
            if change=='subpath':mount['subPath']='ca.crt'
            with self.assertRaises(RuntimeError):M.database_ca_path(pod)

    def test_rejects_path_escape_and_ambiguous_projection(self):
        for path in ('../ca.crt','/unrelated/ca.crt',''):
            pod=copy.deepcopy(self.pod)
            pod['spec']['volumes'][0]['configMap']['items'][0]['path']=path
            with self.assertRaises(RuntimeError):M.database_ca_path(pod)
        self.pod['spec']['containers'][0]['volumeMounts'].append(copy.deepcopy(self.pod['spec']['containers'][0]['volumeMounts'][0]))
        with self.assertRaises(RuntimeError):M.database_ca_path(self.pod)

if __name__=='__main__':unittest.main()
