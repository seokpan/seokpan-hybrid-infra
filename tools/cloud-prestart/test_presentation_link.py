import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch

HERE=Path(__file__).resolve().parent
def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
P=load('presentation_link',HERE/'finish_presentation_link.py')
D=load('presentation_db',HERE/'pod_db_observer.py' if (HERE/'pod_db_observer.py').exists() else HERE.parent/'prestart-closeout/pod_db_observer.py')

class NewPresentationBoundaries(unittest.TestCase):
    def test_source_change_keeps_frontend_release_and_old_input(self):
        original={'repoURL':'repo','path':'runtime','targetRevision':'fixed','kustomize':{'patches':[]}}
        frozen=copy.deepcopy(original);result=P.profile_source(original)
        self.assertEqual(original,frozen)
        self.assertEqual({k:result[k] for k in ('repoURL','path','targetRevision')},{k:original[k] for k in ('repoURL','path','targetRevision')})
        p=result['kustomize']['patches'][0]
        self.assertEqual(p['target']['name'],'backend')
        payload=json.loads(p['patch']);self.assertEqual(payload['spec']['replicas'],2)
        self.assertEqual(payload['spec']['template']['spec']['containers'],[{'name':'backend','resources':P.RESOURCE}])

    def test_machine_permission_is_namespaced_read_only(self):
        pair=P.role_pair('b-presentation-machineautoscaler-read','openshift-machine-api',[{'apiGroups':['autoscaling.openshift.io'],'resources':['machineautoscalers'],'verbs':['get','list','watch']}])
        self.assertEqual([x['kind'] for x in pair],['Role','RoleBinding'])
        self.assertEqual(pair[0]['metadata']['namespace'],'openshift-machine-api')
        self.assertEqual(pair[1]['subjects'],[{'apiGroup':'rbac.authorization.k8s.io','kind':'User','name':'tjung03'}])
        self.assertFalse(set(pair[0]['rules'][0]['verbs']) & {'patch','create','update','delete','*'})

    def test_full_observation_cannot_accept_second_source(self):
        c={'binding':{'normalized_source_sha256':'a'*64},'allowed_normalized_source_sha256':['a'*64,'b'*64]}
        with self.assertRaisesRegex(RuntimeError,'BOUNDED_RECORDING'):D.recording_sources(c)
        c['observation_scope']='TOTAL_ONLY';self.assertEqual(D.recording_sources(c),['a'*64,'b'*64])

    def test_recording_cannot_accept_unbound_or_third_source(self):
        for allowed in (['b'*64],['a'*64,'b'*64,'c'*64],['a'*64,'not-a-hash']):
            with self.assertRaises(RuntimeError):D.recording_sources({'binding':{'normalized_source_sha256':'a'*64},'observation_scope':'TOTAL_ONLY','allowed_normalized_source_sha256':allowed})

    def test_summary_keeps_unknown_accounts_and_raw_rows(self):
        rows=[{'sample':None},{'sample':{'connections_total':7,'per_account':None}}]
        frozen=copy.deepcopy(rows);result=D.summary_input(rows)
        self.assertEqual(rows,frozen)
        self.assertIsNone(result[0]['sample'])
        self.assertEqual(result[1]['sample']['per_account'],{'identity_svc':None,'game_svc':None})

    def test_successful_prepare_reuses_without_cloud_or_subprocess(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);out=root/'presentation-final-20261020';out.mkdir()
            (out/'receipt.json').write_text(json.dumps({'status':'PASS_SELECTED_PRESENTATION_INPUTS_READY_NOT_TESTED','window':{'not_after':'2099-01-01T00:00:00+00:00'}}))
            fake=types.SimpleNamespace(flock=lambda *a:None,LOCK_EX=1,LOCK_NB=2)
            with patch.object(P,'RUN',root),patch.object(P,'fcntl',fake),patch.object(P.sys,'argv',['tool','--execute']),patch.object(P.subprocess,'run') as run:
                self.assertEqual(P.main(),0);run.assert_not_called()

    def test_observer_marker_prevents_relaunch(self):
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp);(out/'db-observer.started.json').write_text('{}')
            r={'window':{'not_after':'2099-01-01T00:00:00+00:00'},'observer_commands':{'db-observer':['python','probe']}}
            with patch.object(P.subprocess,'Popen') as popen:
                result=P.start_observers(out,r);popen.assert_not_called();self.assertTrue(result['observer_start_requested'])

if __name__=='__main__':unittest.main()
