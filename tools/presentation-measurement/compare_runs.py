"""Local normal/rolling paired outputs. No field is populated from historical results."""
import argparse
import json
import hashlib
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from stage_clock import read, save
from timeline import need


def compare(baseline, rolling, baseline_counts, rolling_counts, verification=None):
    need(baseline['scenario'] == 'baseline' and rolling['scenario'] == 'rolling', 'ONE_BASELINE_AND_ONE_ROLLING_REQUIRED')
    for field in ('binding', 'release', 'runtime', 'workload'):
        need(baseline[field] == rolling[field], 'SAME_BOUND_SOURCE_IMAGE_REPLICA_LOAD_REQUIRED')
    for receipt, value in ((baseline, baseline_counts), (rolling, rolling_counts)):
        need(value.get('run_id') == receipt.get('run_id') and value.get('binding') == receipt['binding'], 'COUNTS_MUST_BELONG_TO_SAME_RUN_RECEIPT')
    need(rolling['trigger_result'] and rolling['trigger_result']['status'] == 'REQUESTED_ONE_GITOPS_BACKEND_ROLL', 'ACTUAL_SINGLE_TRIGGER_RECEIPT_REQUIRED')
    need(verification and verification.get('status') == 'PASS_BACKEND_ROLL_RECONCILED_NOT_BUSINESS_ACCEPTANCE'
         and verification.get('application_uid') == rolling['binding']['application_uid']
         and verification.get('namespace_uid') == rolling['binding']['namespace_uid']
         and verification.get('source_before_normalized_sha256') == rolling['binding']['normalized_source_sha256'],
         'ACTUAL_BACKEND_ROLL_VERIFICATION_REQUIRED_NOT_JUST_REQUEST')
    for name in ('backend', 'frontend'):
        need(verification['images'][name].endswith('@'+rolling['release'][name+'_digest'])
            and verification['replicas'][name] == rolling['runtime']['replicas'][name], 'VERIFIED_IMAGE_REPLICAS_MUST_MATCH_OBSERVATION')
    return {'status': 'PAIRED_SCOPED_OBSERVATIONS' if all(x['status'].startswith('COMPLETED_') for x in (baseline, rolling)) else 'INCOMPLETE_PAIRED_OBSERVATIONS',
            'baseline': baseline_counts, 'rolling': rolling_counts, 'rolling_reconnects': rolling['reconnects'],
            'fixed_windows_seconds': [180, 180], 'extra_recovery_in_fixed_statistics': False,
            'cause_or_improvement_percentage_claimed': False, 'not_maximum_capacity': True}


def load_run(path):
    path = Path(path)
    manifest = read(path/'SHA256SUMS.json')
    for name in ('receipt.json', 'counts.json'):
        need(hashlib.sha256((path/name).read_bytes()).hexdigest() == manifest.get(name), 'RUN_FILE_CHECKSUM_CHANGED')
    return read(path/'receipt.json'), read(path/'counts.json')


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--baseline', required=True)
    p.add_argument('--rolling', required=True)
    p.add_argument('--out', required=True)
    p.add_argument('--rolling-verification', required=True)
    a = p.parse_args()
    from pathlib import Path
    b, r = Path(a.baseline), Path(a.rolling)
    try:
        br, bc = load_run(b);rr, rc = load_run(r)
        result = compare(br, rr, bc, rc, read(a.rolling_verification))
        save(a.out, result);print(json.dumps(result, indent=2))
        if result['status'] != 'PAIRED_SCOPED_OBSERVATIONS': raise SystemExit(2)
    except Exception as e:
        print(json.dumps({'status': 'STOP_PAIRED_SUMMARY', 'reason': str(e) if type(e) is RuntimeError else type(e).__name__}));raise SystemExit(2)
