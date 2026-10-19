"""Run only explicitly supplied existing writer controls, then capture their verification.

No shell strings or SQL are synthesized. Manual scopes use operator evidence.
Every real change has a durable attempt marker; uncertain outcomes verify-only.
"""
import argparse
from pathlib import Path
import subprocess
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent))
from cutover import *

CATEGORIES={'external_ingress','ci','manual_direct_db','scheduled_and_other_services'}

def plan_check(x):
    need(x.get('schema')==1 and x.get('run_id') and x.get('operator') and re.fullmatch(r'[a-f0-9]{64}',x.get('source_api_sha256') or ''),'BOUND_WRITER_PLAN_REQUIRED')
    need(x.get('inventory_complete_confirmed_by_operator') is True,'WRITER_INVENTORY_NOT_CONFIRMED_COMPLETE')
    need(set(x['categories'])==CATEGORIES,'ALL_WRITER_CATEGORIES_REQUIRED')
    for cat,step in x['categories'].items():
        need(step['mode'] in {'existing_command','operator_evidence'},'EXISTING_CONTROL_OR_EXPLICIT_MANUAL_EVIDENCE_REQUIRED')
        if step['mode']=='existing_command':
            for k in ('stop_argv','verify_argv'):need(isinstance(step[k],list) and step[k] and all(isinstance(v,str) and v and '\n' not in v for v in step[k]),'EXISTING_ARGV_ARRAY_REQUIRED')
            need(step['command_review_ref'],'ACCEPTED_EXISTING_COMMAND_REFERENCE_REQUIRED')
        else:need(step['state'] in {'STOPPED','CONFIRMED_NONE'} and step['evidence_ref'],'ACTUAL_MANUAL_WRITER_EVIDENCE_REQUIRED')

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--plan',required=True,type=Path);p.add_argument('--work',required=True,type=Path)
    p.add_argument('--execute',action='store_true');p.add_argument('--verify-only',action='store_true');a=p.parse_args();os.umask(0o077)
    try:
        x=read(a.plan);plan_check(x)
        if not a.execute:print(json.dumps({'status':'LOCAL_WRITER_CONTROL_PLAN_ONLY','changes':0}));return 0
        need(stamp(x['hold_until'])>time.time(),'CURRENT_WRITER_HOLD_END_REQUIRED');a.work.mkdir(mode=0o700,exist_ok=True);categories={}
        for cat,s in x['categories'].items():
            if s['mode']=='operator_evidence':
                need(0<=time.time()-stamp(s['checked_at'])<=300,'MANUAL_WRITER_EVIDENCE_STALE')
                categories[cat]={'state':s['state'],'evidence_ref':s['evidence_ref']};continue
            marker=a.work/(cat+'.STARTED.json')
            if not a.verify_only:
                need(not marker.exists(),'WRITER_CHANGE_ATTEMPT_EXISTS_USE_VERIFY_ONLY')
                save(marker,{'run_id':x['run_id'],'plan_sha256':sha(x),'utc':utc()})
                call(s['stop_argv'])
            proof=json.loads(call(s['verify_argv']));need(proof['state'] in {'STOPPED','CONFIRMED_NONE'}
                and proof['run_id']==x['run_id'] and proof['source_api_sha256']==x['source_api_sha256'] and proof['evidence_ref'],'WRITER_CONTROL_NOT_VERIFIED')
            categories[cat]={'state':proof['state'],'evidence_ref':proof['evidence_ref']}
        result={'run_id':x['run_id'],'source_api_sha256':x['source_api_sha256'],'operator':x['operator'],'reference':str(a.plan),
            'inventory_complete':True,'checked_at':utc(),'hold_until':x['hold_until'],'categories':categories,'operator_evidence_not_independent_proof':True}
        immutable=a.work/('external-hold-'+str(time.time_ns())+'.json');save(immutable,result)
        temp=a.work/('current-'+str(time.time_ns())+'.json');save(temp,result);os.replace(temp,a.work/'external-hold.current.json')
        print(json.dumps({'status':'PASS_EXISTING_WRITER_CONTROLS_AND_EVIDENCE_ASSEMBLED','receipt_file':str(a.work/'external-hold.current.json'),
            'immutable_receipt':str(immutable),'not_kubernetes_stop':True}));return 0
    except Exception as e:print(json.dumps({'status':'STOP_WRITER_CONTROLS','reason':str(e) if type(e) is RuntimeError else type(e).__name__,'automatic_retry':False}));return 2

if __name__=='__main__':raise SystemExit(main())
