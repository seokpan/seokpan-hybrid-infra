"""One Controller entry for plan/start/stop/restore; no default cloud actions."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent))
from common import read,need,save

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=['plan','activate','stop','finish'])
    p.add_argument('--config',required=True,type=Path);p.add_argument('--work',required=True,type=Path);p.add_argument('--load-receipt',type=Path)
    p.add_argument('--execute',action='store_true');p.add_argument('--resume-reviewed',action='store_true');a=p.parse_args();os.umask(0o077)
    root=Path(__file__).resolve().parent
    def run(action,extra=()):
        args=[sys.executable,'-I',str(root/'control.py'),action,'--config',str(a.config),'--work',str(a.work),*extra]
        if a.execute:args+=['--execute']
        r=subprocess.run(args);need(r.returncode==0,'HPA_STEP_STOPPED_RECONCILE_CURRENT_STATE')
    try:
        if not a.execute:run('prepare');return 0
        if a.action=='plan':run('prepare');return 0
        if a.action=='stop':run('stop');return 0
        if a.action=='finish':
            need(a.load_receipt is not None,'STOPPED_GENERATOR_RECEIPT_REQUIRED');run('stop')
            run('restore',['--load-receipt',str(a.load_receipt),*(['--resume-reviewed'] if a.resume_reviewed else [])])
            print(json.dumps({'status':'RESTORE_REQUESTED_RUN_VERIFY_RESTORE_WHEN_READY','next':'control.py verify-restore --execute'}));return 0
        run('start',['--resume-reviewed'] if a.resume_reviewed else [])
        c=read(a.config);need(not (a.work/'collectors.STARTED.json').exists(),'COLLECTOR_START_ALREADY_ATTEMPTED_VERIFY_ITS_OUTPUT_NO_DUPLICATE')
        save(a.work/'collectors.STARTED.json',{'config_path':str(a.config),'bounded_seconds':2700})
        commands=[('platform', [sys.executable,'-I',str(root/'control.py'),'observe','--config',str(a.config),'--work',str(a.work),'--seconds','2700','--execute'])]
        if c.get('prometheus',{}).get('origin'):
            commands.append(('prometheus',[sys.executable,'-I',str(root/'prometheus.py'),'--config',str(a.config),'--out',str(a.work/'prometheus.jsonl'),'--seconds','2700','--execute']))
        for name,cmd in commands:
            with (a.work/(name+'.private.log')).open('xb') as log:
                options={'creationflags':subprocess.CREATE_NO_WINDOW} if os.name=='nt' else {'start_new_session':True}
                child=subprocess.Popen(cmd,stdin=subprocess.DEVNULL,stdout=log,stderr=log,**options)
                save(a.work/(name+'.collector.json'),{'pid':child.pid,'command':cmd,'do_not_kill_reused_pid':True})
        print(json.dumps({'status':'HPA_AND_BOUNDED_COLLECTORS_STARTED_NOT_LOAD_RESULT','generator_plan':str(a.work/'plan.json'),
            'guard':'wait for PASS_HPA_GUARD; load.mjs also allows initial metrics wait up to120s','no_browser_installed':True}));return 0
    except Exception as e:print(json.dumps({'status':'STOP_HPA_RUNNER','reason':str(e) if type(e) is RuntimeError else type(e).__name__,'automatic_retry':False}));return 2

if __name__=='__main__':raise SystemExit(main())
