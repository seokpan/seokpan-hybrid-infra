import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('minimal', Path(__file__).with_name('rosa-minimal-app-bootstrap.py'))
M = importlib.util.module_from_spec(spec)
spec.loader.exec_module(M)


class Tests(unittest.TestCase):
    def test_fresh_config_is_json_mapping(self):
        import json
        self.assertEqual(json.loads(M.workload_config('')), {'enableUserWorkload': True})

    def test_existing_json_storage_is_preserved(self):
        import json
        x = json.loads(M.workload_config('{"prometheusK8s":{"volumeClaimTemplate":{"spec":{"resources":{"requests":{"storage":"50Gi"}}}}}}'))
        self.assertTrue(x['enableUserWorkload'])
        self.assertEqual(x['prometheusK8s']['volumeClaimTemplate']['spec']['resources']['requests']['storage'], '50Gi')

    def test_yaml_changes_only_feature_boolean(self):
        text = 'enableUserWorkload: false # retained\nprometheusK8s:\n  retention: 6h\n'
        self.assertEqual(M.workload_config(text), text.replace('false', 'true', 1))

    def test_document_header_is_retained(self):
        self.assertEqual(M.workload_config('---\nfoo: bar\n'), '---\nenableUserWorkload: true\nfoo: bar\n')

    def test_unknown_or_duplicate_setting_does_not_overwrite(self):
        for text in ('"enableUserWorkload": false', 'enableUserWorkload: false\nenableUserWorkload: true'):
            with self.assertRaises(RuntimeError):
                M.workload_config(text)

    def test_non_mapping_json_is_rejected(self):
        with self.assertRaises(RuntimeError):
            M.workload_config('[]')

    def test_completed_evidence_is_bound_to_cluster_and_recipe(self):
        value = {'cluster_id':'new','recipe_sha256':'accepted','result':{'verified':True}}
        self.assertTrue(M.completed(value, 'new', 'accepted')['verified'])
        for cluster, recipe in (('old','accepted'), ('new','other')):
            with self.assertRaises(RuntimeError):
                M.completed(value, cluster, recipe)

    def test_core_does_not_reenter_completed_or_deferred_stages(self):
        class Fake:
            def operator(self): return 'operator'
            def humans(self): return 'humans'
            def apply_actions(self, kinds): return kinds
        names = []
        M.run_core(Fake(), lambda name, fn: (names.append(name), fn())[1])
        self.assertTrue({'gitops-operator','namespaces','human-configuration','argocd-spec'} <= set(names))
        self.assertFalse({'management','fresh-worker-binding','monitoring-volumes','operator-permission-compare'} & set(names))


if __name__ == '__main__':
    unittest.main()
