"""Evidence-only unit tests; no database, cloud or application is started."""
import argparse
import csv
import datetime as dt
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parent

def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module

DATA = load('identity_data', ROOT/'recovery_fixture/run.py')
BUSINESS = load('identity_business', ROOT/'recovery_business_fixture/run.py')

class IdentityTests(unittest.TestCase):
    def args(self, output, operator='actual-unit-runner'):
        return SimpleNamespace(output=output, operator=operator, run_id='UNIT-NOT-RUNTIME', transport='unix', infra_sha='0'*40)

    def emit(self, module, args):
        now=dt.datetime(2026,1,1,tzinfo=dt.timezone.utc)
        events=[['unit',DATA.stamp(now),DATA.kst(now),args.operator,'no-runtime','NOT_RUN','summary.md']]
        if module is DATA:
            DATA.emit_evidence(args.output,args,now,now,[],events,{},'unit-only','UnitOnly')
        else:
            BUSINESS.emit(args,now,now,events,{},{},{},'unit-only','UnitOnly')

    def test_explicit_human_and_automation_labels(self):
        for value in ('tjung03','kshi1313-gif','github-actions','host01.operator','lab@runner_2'):
            self.assertEqual(DATA.operator_id(value),value)

    def test_invalid_labels(self):
        for value in (None,'','a'*81,'a b','a\nb','=x','-x','a/b','a`b'):
            with self.subTest(value=value), self.assertRaises(argparse.ArgumentTypeError): DATA.operator_id(value)

    def check_output(self,args):
        release=json.loads((args.output/'release.json').read_text())
        self.assertEqual(release['actual_operator'],args.operator)
        self.assertIsNone(release['reviewer'])
        self.assertEqual(release['completeness'],'INCOMPLETE')
        self.assertNotIn(args.operator,release['custodian_ref'])
        self.assertIn('`'+args.operator+'`',(args.output/'summary.md').read_text())
        with (args.output/'timeline.csv').open() as stream:
            self.assertEqual(list(csv.DictReader(stream))[0]['actor_ref'],args.operator)
        for line in (args.output/'checksums.txt').read_text().splitlines():
            expected,name=line.split('  ')
            self.assertEqual(hashlib.sha256((args.output/name).read_bytes()).hexdigest(),expected)

    def test_data_emitter(self):
        with tempfile.TemporaryDirectory() as directory:
            args=self.args(Path(directory)/'result');self.emit(DATA,args);self.check_output(args)

    def test_business_emitter_and_scope(self):
        with tempfile.TemporaryDirectory() as directory:
            args=self.args(Path(directory)/'result','another-runner');self.emit(BUSINESS,args);self.check_output(args)
            release=json.loads((args.output/'release.json').read_text())
            self.assertNotIn('HISTORICAL_RESULT_HTTP',release['missing_inputs'])
            self.assertIn('FULL_T18_ACCEPTANCE',release['missing_inputs'])
            self.assertTrue(release['known_limitations'])

    def test_business_summary_uses_adopted_scope_without_reopening_approval(self):
        with tempfile.TemporaryDirectory() as directory:
            args=self.args(Path(directory)/'result');self.emit(BUSINESS,args)
            summary=(args.output/'summary.md').read_text()
            self.assertIn('outside the adopted recovery Must',summary)
            self.assertNotIn('B and the design review must explicitly confirm',summary)
            self.assertIn('full T18 Acceptance: NOT RUN',summary)
            self.assertEqual(json.loads((args.output/'release.json').read_text())['recovery']['rto_seconds'],None)

    def test_invalid_operator_creates_no_output(self):
        with tempfile.TemporaryDirectory() as directory:
            for i,module in enumerate((DATA,BUSINESS)):
                args=self.args(Path(directory)/str(i),'bad\nactor')
                with self.assertRaises(argparse.ArgumentTypeError): self.emit(module,args)
                self.assertFalse(args.output.exists())

    def test_existing_output_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            for i,module in enumerate((DATA,BUSINESS)):
                args=self.args(Path(directory)/str(i));self.emit(module,args)
                before={p.name:p.read_bytes() for p in args.output.iterdir()}
                with self.assertRaises(FileExistsError): self.emit(module,args)
                self.assertEqual(before,{p.name:p.read_bytes() for p in args.output.iterdir()})

    def test_missing_operator_stops_before_runtime(self):
        with tempfile.TemporaryDirectory() as directory:
            for folder in ('recovery_fixture','recovery_business_fixture'):
                output=Path(directory)/folder
                command=[sys.executable,str(ROOT/folder/'run.py'),'--run-id','unit','--output',str(output)]
                if folder=='recovery_business_fixture':command+=['--app-checkout',directory,'--infra-sha','0'*40]
                result=subprocess.run(command,capture_output=True,text=True,timeout=10)
                self.assertEqual(result.returncode,2)
                self.assertIn('--operator',result.stderr)
                self.assertFalse(output.exists())

    def test_internal_run_validates_before_preflight(self):
        for module in (DATA,BUSINESS):
            with self.assertRaises(argparse.ArgumentTypeError): module.run(SimpleNamespace(operator=''))

if __name__=='__main__': unittest.main()
