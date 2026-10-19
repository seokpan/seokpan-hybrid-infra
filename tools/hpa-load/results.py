"""Join the separate HPA load/observer/restore outputs. No live requests or invented metrics."""
import argparse
import hashlib
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent))
from common import *

def nonzero_phase_acceptance(phases):
    checks=[]
    for index,(vus,_) in enumerate(PHASES):
        if vus==0:continue
        matches=[row for row in phases if row.get('phase')==index]
        row=matches[0] if len(matches)==1 else {}
        n,rate=row.get('N'),row.get('http_5xx_fraction')
        if not matches:status='MISSING_PHASE'
        elif len(matches)!=1:status='DUPLICATE_PHASE'
        elif row.get('vus')!=vus:status='PHASE_VU_MISMATCH'
        elif type(n) is not int or n<=0:status='NO_VALID_SAMPLES'
        elif type(rate) not in (int,float) or not math.isfinite(rate) or not 0<=rate<=1:status='NO_VALID_ERROR_RATE'
        elif rate>=.01:status='HTTP_5XX_THRESHOLD_NOT_MET'
        else:status='PASS'
        checks.append({'phase':index,'vus':vus,'N':n,'http_5xx_fraction':rate,'status':status})
    return checks


def summarize(c,before,load,counts,rows,restored):
    need(load['binding']==c['binding'] and load['control_sha256']==digest(c) and before['config_sha256']==digest(c),'SAME_HPA_RUN_REQUIRED')
    need(counts['binding']==c['binding'] and counts['run_id']==c['run_id'] and counts['control_sha256']==digest(c),'COUNTS_FROM_OTHER_RUN')
    need(restored['binding']==c['binding'] and restored['config_sha256']==digest(c)
         and restored['status']=='PASS_HPA_REMOVED_ORIGINAL_SPEC_REPLICAS_READY_NOT_BUSINESS_ACCEPTANCE','ACTUAL_RESTORE_VERIFICATION_REQUIRED')
    need(rows and all(x['binding']==c['binding'] and x['config_sha256']==digest(c) for x in rows),'SAME_RUN_PLATFORM_RECORDS_REQUIRED')
    good=[x for x in rows if x.get('hpa') and x.get('db')];need(good,'HPA_DB_OBSERVATIONS_REQUIRED')
    replicas=[x['hpa'].get('status',{}).get('currentReplicas') for x in good];replicas=[x for x in replicas if type(x) is int]
    base=before['deployments']['backend']['spec']['replicas'];scaleout=bool(replicas) and max(replicas)>base
    scalein=bool(replicas) and replicas[-1]==c['min_replicas']
    phase_checks=nonzero_phase_acceptance(counts['phases'])
    threshold=all(x['status']=='PASS' for x in phase_checks)
    ok=load['status']=='COMPLETED_HPA_LOAD' and scaleout and scalein and threshold
    return {'status':'OBSERVED_HPA_SCALE_OUT_IN_SCOPED_HTTP_RESULT' if ok else 'PARTIAL_OR_THRESHOLD_NOT_MET_HPA_RESULT',
            'binding':c['binding'],'baseline_replicas':base,'max_replicas_observed':max(replicas,default=None),'scale_out_observed':scaleout,
            'last_observed_min_replica':scalein,'all_nonzero_phases_http_5xx_below_1_percent':threshold,
            'phase_acceptance':phase_checks,'counts':counts,
            'max_connections_observed':max(x['db']['connections_total'] for x in good),
            'min_global_headroom_observed':min(x['db']['global_limit']-x['db']['connections_total'] for x in good),
            'failed_or_waiting_observer_samples':sum(x['status']!='PASS_HPA_GUARD' for x in rows),
            'clock_domain_correlation':'UTC labels only; no cross-host scale latency computed',
            'restoration_verified':True,'guest_cleanup_unconfirmed':load['guest_cleanup_unconfirmed'],
            'scope':'Guest lobby/ranking reads; not game vote/WSS throughput or maximum service capacity'}

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--config',type=Path,required=True);p.add_argument('--work',type=Path,required=True);p.add_argument('--load-out',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    try:
        manifest=read(a.load_out/'SHA256SUMS.json')
        for name in ('receipt.json','counts.json'):
            need(hashlib.sha256((a.load_out/name).read_bytes()).hexdigest()==manifest[name],'LOAD_RESULT_CHECKSUM_CHANGED')
        rows=[json.loads(x) for x in (a.work/'platform-db-metrics.jsonl').read_text().splitlines()]
        r=summarize(read(a.config),read(a.work/'before.json'),read(a.load_out/'receipt.json'),read(a.load_out/'counts.json'),rows,read(a.work/'restored.json'))
        save(a.out,r);print(json.dumps(r));return 0 if r['status'].startswith('OBSERVED_') else 2
    except Exception as e:print(json.dumps({'status':'STOP_HPA_RESULT_JOIN','reason':str(e) if type(e) is RuntimeError else type(e).__name__}));return 2

if __name__=='__main__':raise SystemExit(main())
