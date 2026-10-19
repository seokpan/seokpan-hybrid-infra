"""Prepare one PROCESS permission plan from actual matched-account evidence. Never executes SQL."""
import argparse
import json
import re

def plan(account,visibility,reference):
    if not isinstance(reference,str) or len(reference.strip())<8:
        raise ValueError('ACTUAL_MATCHED_ACCOUNT_EVIDENCE_REQUIRED')
    if not isinstance(account,str) or not re.fullmatch(r'db_admin@[A-Za-z0-9.:/_%\-]{1,255}',account):
        raise ValueError('EXACT_EXISTING_DB_ADMIN_ACCOUNT_REQUIRED')
    user,host=account.split('@',1)
    if visibility not in ('present','missing','unknown'):raise ValueError('VISIBILITY_STATE_REQUIRED')
    result={'status':'ALREADY_VISIBLE_NO_GRANT' if visibility=='present' else
        'PREPARED_PROCESS_ONLY_PLAN_NOT_EXECUTED' if visibility=='missing' else 'PRIVILEGE_EVIDENCE_REQUIRED_NO_GRANT_PLAN',
        'matched_account':account,'evidence_ref':reference,'account_already_exists_must_be_confirmed':True,
        'sql_calls':0,'grant_option_added':False,'application_account_privileges_changed':False}
    if visibility=='missing':
        target="'"+user+"'@'"+host+"'"
        result.update(precondition="SELECT COUNT(*) FROM mysql.user WHERE User='db_admin' AND Host='"+host+"';",
            expected_existing_accounts=1,grant_sql='GRANT PROCESS ON *.* TO '+target+';',
            verify_sql='SHOW GRANTS FOR '+target+';',
            cleanup_only_if_this_run_added_and_measurements_finished='REVOKE PROCESS ON *.* FROM '+target+';',
            execution='EXISTING_PROTECTED_DATA_ADMIN_SESSION_ONLY',
            stop_if_account_absent_or_different=True,operator_report_not_cryptographic_attestation=True)
    return result

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--matched-account',required=True);p.add_argument('--process-visibility',choices=('present','missing','unknown'),required=True)
    p.add_argument('--evidence-ref',required=True);a=p.parse_args()
    try:print(json.dumps(plan(a.matched_account,a.process_visibility,a.evidence_ref),indent=2));return 0
    except ValueError as e:print(json.dumps({'status':'STOP_OBSERVER_ACCESS_PLAN','reason':str(e),'sql_calls':0}));return 2
if __name__=='__main__':raise SystemExit(main())
