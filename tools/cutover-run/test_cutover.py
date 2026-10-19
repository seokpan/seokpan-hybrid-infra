import copy
import io
import json
from contextlib import redirect_stdout
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from types import SimpleNamespace
import cutover as C
import writers


def inputs():
    def zoned(n):return C.dt.datetime.fromtimestamp(n,C.dt.timezone.utc).isoformat()
    now=time.time();c={'schema':1,'run_id':'cutover-fixture','source_api':'https://10.1.93.90:6443','stop_budget_minutes':75,'joint_budget_minutes':120,
      'window':{'not_before':zoned(now-10),'not_after':zoned(now+6000)}}
    e={'run_id':c['run_id'],'source_api_sha256':C.hashlib.sha256(c['source_api'].encode()).hexdigest(),'inventory_complete':True,'operator':'B','reference':'existing-controls',
      'checked_at':zoned(now),'hold_until':zoned(now+6100),'categories':{k:{'state':'CONFIRMED_NONE','evidence_ref':'current-inventory'} for k in writers.CATEGORIES}}
    r={'run_id':c['run_id'],'operator':'A','reference':'existing-rot_check-output','gate_seconds':7200,'result':'PASS','checked_at':zoned(now),'safe_until':zoned(now+7200)}
    return c,e,r


class Tests(unittest.TestCase):
    def test_go_gates_bind_current_hold_rotation_and_valid_end(self):
        c,e,r=inputs();self.assertIn('external_hold_sha256',C.gates(c,e,r))
    def test_kubernetes_pass_does_not_substitute_external_writers(self):
        c,e,r=inputs();e['categories']['manual_direct_db']['state']='UNKNOWN'
        with self.assertRaisesRegex(RuntimeError,'WRITER_NOT_HELD'):C.gates(c,e,r)
    def test_old_rotation_wrong_run_or_too_short_hold_blocks_go(self):
        for change in ('age','run','end'):
            c,e,r=inputs()
            if change=='age':r['checked_at']='2020-01-01T00:00:00Z'
            elif change=='run':r['run_id']='other'
            else:e['hold_until']=c['window']['not_before']
            with self.assertRaises(RuntimeError):C.gates(c,e,r)
    def test_1800_gate_cannot_replace_pre_stop_7200(self):
        c,e,r=inputs();r['gate_seconds']=1800
        with self.assertRaisesRegex(RuntimeError,'7200'):C.gates(c,e,r)
    def test_pr_needs_exact_head_approval_and_ready_state(self):
        pr={'head':{'sha':C.HEAD},'base':{'ref':'main','repo':{'full_name':C.REPO}},'state':'open','mergeable':True,'draft':False}
        review=[{'state':'APPROVED','user':{'login':'reviewer'},'commit_id':C.HEAD}]
        C.approved_pr(pr,review,{'check_runs':[]})
        with self.assertRaises(RuntimeError):C.approved_pr(pr,[],{'check_runs':[]})
        pr['draft']=True
        with self.assertRaises(RuntimeError):C.approved_pr(pr,review,{'check_runs':[]})
    def test_later_changes_request_or_failed_check_blocks_merge(self):
        pr={'head':{'sha':C.HEAD},'base':{'ref':'main','repo':{'full_name':C.REPO}},'state':'open','mergeable':True,'draft':False}
        r=[{'state':'APPROVED','user':{'login':'r'},'commit_id':C.HEAD},{'state':'CHANGES_REQUESTED','user':{'login':'r'},'commit_id':C.HEAD}]
        with self.assertRaises(RuntimeError):C.approved_pr(pr,r,{'check_runs':[]})
        with self.assertRaises(RuntimeError):C.approved_pr(pr,r[:1],{'check_runs':[{'status':'completed','conclusion':'failure'}]})
    def test_existing_accepted_verifier_import_has_no_api_calls(self):
        root=Path(__file__).resolve().parents[2]
        with patch('urllib.request.urlopen',side_effect=AssertionError('API')):v=C.verifier(root)
        self.assertTrue(callable(v.evaluate))
    def test_writer_plan_requires_explicit_manual_evidence_or_argv(self):
        c,e,r=inputs();p={'schema':1,'run_id':c['run_id'],'source_api_sha256':e['source_api_sha256'],'operator':'B','inventory_complete_confirmed_by_operator':True,
             'categories':{k:{'mode':'operator_evidence','state':'CONFIRMED_NONE','evidence_ref':'existing-reference'} for k in writers.CATEGORIES}}
        writers.plan_check(p);p['categories']['ci']={'mode':'existing_command','stop_argv':'gh command','verify_argv':['check'],'command_review_ref':'existing'}
        with self.assertRaisesRegex(RuntimeError,'ARGV'):writers.plan_check(p)
    def test_default_cutover_does_not_merge_stop_verify_or_send_go(self):
        c,_,_=inputs()
        with tempfile.TemporaryDirectory() as t:
            cfg=Path(t)/'c.json';C.save(cfg,c)
            with patch('sys.argv',['cutover.py','stop','--config',str(cfg),'--work',str(Path(t)/'work'),'--infra',str(Path(__file__).resolve().parents[2])]),patch('cutover.call',side_effect=AssertionError('COMMAND')),redirect_stdout(io.StringIO()) as out:
                self.assertEqual(C.main(),0)
            self.assertIn('PREPARED_',out.getvalue());self.assertFalse((Path(t)/'work').exists())
    def test_go_is_generated_once_from_current_source_and_expires_in_75_minute_stop_budget(self):
        c,e,r=inputs()
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);w=root/'work';w.mkdir()
            c.update(source_contract=str(root/'contract.json'),source_read=str(root/'source.json'),external_hold=str(root/'external.json'),rotation_receipt=str(root/'rotation.json'))
            for name,value in [('contract',{}),('source',{}),('external',e),('rotation',r),('c',c)]:C.save(root/(name+'.json'),value)
            C.save(w/'merge.STARTED.json',{'config_sha256':C.sha(c),'utc':C.utc()})
            C.save(w/'kubernetes-stop.json',{'config_sha256':C.sha(c),'observed_at':C.utc(),'result':{'merge_sha':'a'*40}})
            v=SimpleNamespace(expected_inputs=lambda *a:{},Client=lambda *a:SimpleNamespace(snapshot=lambda:{}),evaluate=lambda *a:(True,{}))
            args=['cutover.py','go','--config',str(root/'c.json'),'--work',str(w),'--infra',str(root),'--execute']
            with patch('sys.argv',args),patch('cutover.verifier',return_value=v),redirect_stdout(io.StringIO()):
                self.assertEqual(C.main(),0);self.assertEqual(C.main(),2)
            go=C.read(w/'GO.json');self.assertFalse(go['sent_to_A']);self.assertLessEqual(go['remaining_write_stop_seconds'],4500)
            self.assertIn('Data 최종 덤프 시작 GO',go['message'])
    def test_source_reopen_decision_cannot_ignore_new_cloud_writes(self):
        c,_,_=inputs()
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);C.save(root/'c.json',c);C.save(root/'d.json',{'run_id':c['run_id'],'data_compare_reference':'A-result','operator':'B','cloud_new_writes_confirmed':True,'source_reopen_approved':True})
            with patch('sys.argv',['cutover.py','decision','--choice','SOURCE','--data-decision',str(root/'d.json'),'--config',str(root/'c.json'),'--work',str(root/'work'),'--infra',str(root),'--execute']),patch('cutover.verifier',return_value=object()),redirect_stdout(io.StringIO()) as out:
                self.assertEqual(C.main(),2)
            self.assertIn('NO_SIMPLE_SOURCE_REOPEN',out.getvalue())


if __name__=='__main__':unittest.main()
