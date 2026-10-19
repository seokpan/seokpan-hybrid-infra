"""New reconciliation boundaries only. No real SSH, old probe, SQL or cloud tests."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shlex
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

HERE=Path(__file__).resolve().parent
def load(name):
    s=importlib.util.spec_from_file_location('known_'+name,HERE/(name+'.py'))
    m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
F=load('finish_known_inputs');D=load('restricted_delegate');P=load('finish_prestart')
def private(path,value):
    path.write_text(json.dumps(value),encoding='utf-8');path.chmod(0o600)

class KnownInputs(unittest.TestCase):
    def test_future_hpa_capacity_is_not_a_precreate_gate_and_real_gates_remain(self):
        before,later=P.requirement_phases(['CURRENT_FOUNDATION_AND_NETWORK_PROOF','CURRENT_COST_SOURCE_REQUIRED',
            'CURRENT_DB_ACCOUNT_LIMITS_RESERVATION_AND_POOL_EVIDENCE_SOURCE_REQUIRED'])
        self.assertEqual(before,['CURRENT_FOUNDATION_AND_NETWORK_PROOF','CURRENT_COST_SOURCE_REQUIRED'])
        self.assertEqual(later,['ACTUAL_DB_ACCOUNT_LIMITS_AND_NEW_NODE_CAPACITY_BEFORE_HPA'])

    def test_delegate_does_not_accept_shell_or_other_paths(self):
        c={'python':'/usr/bin/python3','run':'/private/current'}
        for command in ('sh -c id','cat /private/current/secret','b-prestart-report-v1'):
            with self.assertRaises(ValueError):D.select(command,c)
        run=Path(c['run'])
        wanted=[c['python'],'-I',str(run/'app-measurement/tools/presentation-measurement/rolling_control.py'),
            'trigger','--config',str(run/'measurement-bindings/rolling-config.json'),
            '--work',str(run/'measurement-bindings/rolling-work'),'--execute']
        self.assertEqual(D.select(shlex.join(wanted),c),wanted)
        with self.assertRaises(ValueError):D.select(shlex.join(wanted+['--other']),c)

    def test_foundation_reference_does_not_assert_current_network_or_db_limits(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);out=root/'inputs';out.mkdir();foundation=root/'foundation.json'
            private(foundation,{'old':'accepted'});registry=root/'registry.json';private(registry,{})
            private(out/'launch-inputs.NOT_ACCEPTED.json',{'budget':{'reported_spend_usd':352.31,'variable_allowance_usd':0},'window':{}})
            a=types.SimpleNamespace(foundation_file=str(foundation),foundation_sha256=hashlib.sha256(foundation.read_bytes()).hexdigest(),
                db_limit=68,db_user_limit=0,reserved=10,db_reference='existing-data-result',source_host='source-host',
                source_contract='/source/old-contract.json',source_read_directory='/source/read')
            x=F.bind_references({'out':str(out),'input_registry':str(registry)},a);r=F.read(registry)
            self.assertEqual(r['foundation_handoff_candidates'],[str(foundation)])
            self.assertNotIn('capacity_file',r);self.assertNotIn('source_contract_candidates',r)
            self.assertFalse(F.read(out/'known-inputs/foundation-reference.json')['current_network_asserted'])
            self.assertFalse(F.read(out/'known-inputs/db-reference.NOT_ACCEPTED.json')['current_values_asserted'])
            self.assertNotIn('budget.variable_allowance_usd',x['cost_missing_fields'])
            self.assertIn('budget.hourly_upper_usd',x['cost_missing_fields'])

    def test_changed_observed_dispatch_stops_before_any_authorization_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);base=root/'prestart-closeout';out=base/'inputs';out.mkdir(parents=True)
            (root/'generator-link').mkdir();p=root/'generator-link/restricted_dispatch.py';p.write_bytes(b'unknown changed code')
            private(out/'metadata-transport-receipt.json',{'reason':'EXACT_ORIGINAL_RESTRICTED_DISPATCH_REQUIRED'})
            a=types.SimpleNamespace(observed_dispatch_sha256='0'*64)
            with self.assertRaisesRegex(RuntimeError,'OBSERVED_DISPATCH_CHANGED'):
                F.reconcile({'run':str(root),'out':str(out)},a)
            self.assertFalse((out/'p05-reconcile/STARTED.NO_AUTOMATIC_RETRY.json').exists())
            self.assertEqual(p.read_bytes(),b'unknown changed code')

    @unittest.skipUnless(os.name=='posix','Real flock/owned file behavior is validated by Linux CI')
    def test_reviewed_delegate_preserves_unknown_original_and_unrelated_key(self):
        T=load('metadata_transport')
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'.ssh').mkdir();old=root/'original.py';old.write_bytes(b'raise SystemExit("must not run")\n')
            delegate=root/'restricted_delegate.py';delegate.write_bytes(b'# reviewed delegate\n')
            python=sys.executable;public='ssh-ed25519 TEST_TASK_KEY test';line='restrict,command="'+shlex.join([python,'-I',str(old)])+'" '+public+'\n'
            keyfile=root/'.ssh/authorized_keys';keyfile.write_text('ssh-ed25519 OTHER_UNTOUCHED\n'+line);keyfile.chmod(0o600)
            settings=root/'transport-settings.json'
            private(settings,{'python':python,'public_key':public,'original_dispatch_sha256':T.sha(old),
                'original_dispatch_sha256_variants':[T.sha(delegate)],
                'reviewed_delegate':{'argv':[python,'-I',str(delegate)],'sha256':T.sha(delegate)}})
            with patch.object(Path,'home',return_value=root),patch.object(T,'HERE',root):result=T.install(settings)
            saved=F.read(root/'original-dispatch.json')
            self.assertEqual(saved['argv'],[python,'-I',str(delegate)])
            self.assertEqual(saved['prior_argv'],[python,'-I',str(old)])
            self.assertEqual(old.read_bytes(),b'raise SystemExit("must not run")\n')
            self.assertIn('ssh-ed25519 OTHER_UNTOUCHED\n',keyfile.read_text())
            self.assertFalse(result['other_keys_changed'])

if __name__=='__main__':unittest.main()
