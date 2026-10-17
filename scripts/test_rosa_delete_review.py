"""New final State/input preservation and review boundaries; no runtime retries."""
import copy
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

def module(name):
    s=importlib.util.spec_from_file_location(name,Path(__file__).with_name(name+'.py'))
    m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
D=module('prepare-rosa-delete-review');V=module('verify-rosa-independent-supplements')

class DeleteReview(unittest.TestCase):
    def test_running_admin_pod_never_implies_interactive_work_was_released(self):
        pods=[{'metadata':{'name':'data-admin','uid':'fixture'},'status':{'phase':'Running'}}]
        result=D.impact_summary(pods,[],[],[{'spec':{'replicas':0}}],[])
        self.assertEqual(len(result['pods']),1)
        self.assertIn('NOT_PROVEN',result['interactive_data_activity'])
    def test_active_app_job_schedule_and_argo_operation_block_review(self):
        for kind in ['app','job','cron','argo']:
            args=[[],[],[],[],[]]
            if kind=='app':args[3]=[{'spec':{'replicas':1}}]
            elif kind=='job':args[1]=[{'metadata':{'name':'writer'},'status':{'active':1}}]
            elif kind=='cron':args[2]=[{'metadata':{'name':'writer'},'spec':{'suspend':False}}]
            else:args[4]=[{'operation':{'sync':{}}}]
            with self.assertRaises(RuntimeError):D.impact_summary(*args)
    def held(self,tmp):
        root=Path(tmp);source=root/'rosa';source.mkdir();(source/'main.tf').write_bytes(b'fixture');(source/'.terraform.lock.hcl').write_bytes(b'lock')
        scripts=root/'scripts';scripts.mkdir();driver=scripts/'rosa-held-restoration.sh';driver.write_bytes(b'driver')
        names=['prepare-rosa-checkpoint.py','prepare-rosa-reconstruction.py','rosa-fresh-management.py',
               'rosa-gitops-bootstrap.py','rosa-held-restoration.py','rosa-lifecycle-plan.py','rosa-monitoring-volume-backup.py']
        for name in names:(scripts/name).write_bytes(name.encode())
        recipe=root/'recipe.sops.json';recipe.write_bytes(b'cipher')
        cli=root/'rosa-cli';cli.write_bytes(b'rosa');cli.chmod(0o700)
        old=root/'old.json';D.M.save(old,{'cluster_enabled':True,'worker_sg_binding':{'old':'worker'},'openshift_version':'4.20.8'})
        new=root/'new.json';D.M.save(new,{'cluster_enabled':True,'worker_sg_binding':None,'openshift_version':'4.20.9'})
        D.M.save(root/'before.tfstate',{'outputs':{'platform_handoff':{'value':{'current_version':'4.20.9'}}}})
        manifest={'tf_root':str(source),'source_hash':D.source_hash(source),'var_file':str(old),'var_sha256':D.M.sha(old)}
        accepted={'classified_objects':135,'unclassified_objects':0,'recipe_sha256':D.M.sha(recipe)}
        c={'auth':{'rosa_cli':str(cli)},'delete':{'manifest':str(root/'manifest.json')},'adopted_tf_source_hash':D.source_hash(source),
           'create':{'tf_root':str(source),'var_file':str(new),'restore_driver':str(driver),'restore_driver_sha256':D.M.sha(driver),
                     'network_ready':False,'budget_accepted':False},
           'restoration':{'recipe_archive':str(recipe),'recipe_sha256':D.M.sha(recipe),
                          'dependency_sha256':{name:D.M.sha(scripts/name) for name in names}}}
        return c,manifest,accepted
    def test_exact_restore_binding_review_does_not_accept_future_network_or_budget(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(D.shutil,'which',return_value='/ansible-playbook'):
            c,m,a=self.held(tmp);before=copy.deepcopy(c);D.held_inputs(c,m,a)
            self.assertEqual(c,before);self.assertFalse(c['create']['network_ready']);self.assertFalse(c['create']['budget_accepted'])
    def test_old_worker_binding_or_changed_source_blocks_contract_review(self):
        for kind in ['binding','source']:
            with tempfile.TemporaryDirectory() as tmp,patch.object(D.shutil,'which',return_value='/ansible-playbook'):
                c,m,a=self.held(tmp)
                if kind=='binding':
                    value=D.M.read(c['create']['var_file']);value['worker_sg_binding']={'old':'worker'};D.M.save(Path(c['create']['var_file']),value)
                else:(Path(c['create']['tf_root'])/'main.tf').write_bytes(b'changed')
                with self.assertRaises(RuntimeError):D.held_inputs(c,m,a)
    def test_missing_restoration_dependency_stops_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            c,m,a=self.held(tmp);c['restoration']['dependency_sha256'].pop('rosa-held-restoration.py')
            with self.assertRaisesRegex(RuntimeError,'DEPENDENCY'):D.held_inputs(c,m,a)
    def test_supplement_contains_only_selected_files_with_inner_hashes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);key=root/'keys.txt';key.write_bytes(b'not-an-actual-key');key.chmod(0o600)
            state=root/'latest.tfstate';state.write_bytes(b'state');state.chmod(0o600)
            rows=D.pack_files([state,state],key)
            self.assertEqual(len(rows),1);self.assertEqual(rows[0]['sha256'],D.M.sha(state))
    def test_key_path_or_hardlink_cannot_enter_supplement(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);key=root/'keys.txt';key.write_bytes(b'not-an-actual-key');key.chmod(0o600)
            with self.assertRaisesRegex(RuntimeError,'SEPARATE_KEY'):D.pack_files([key],key)
            alias=root/'alias';os.link(key,alias)
            with self.assertRaisesRegex(RuntimeError,'SEPARATE_KEY'):D.pack_files([alias],key)
    def test_bounded_new_review_kind_is_accepted_without_changing_old_kinds(self):
        rows=[{'kind':kind} for kind in ['restoration-gaps','restoration-recipe','monitoring-snapshots','delete-review']]
        self.assertEqual(V.handoff_rows({'schema':2,'archives':rows}),rows)
    def test_unknown_duplicate_or_unbounded_supplements_are_rejected(self):
        for kinds in [['delete-review','delete-review'],['unknown'],['delete-review']*5]:
            with self.assertRaises(RuntimeError):V.handoff_rows({'schema':2,'archives':[{'kind':k} for k in kinds]})

if __name__=='__main__':unittest.main()
