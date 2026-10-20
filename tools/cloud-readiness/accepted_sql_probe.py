"""Post-import purpose-account read-only check. Private stdin; no schema migration."""
import asyncio
import hashlib
import json
import re
import ssl
import sys

TABLE_READS = {
    'identity_svc': ['SELECT member_id FROM stone_game.member LIMIT 0'],
    'game_svc': [
        'SELECT nickname, member_id, rating FROM stone_game.member LIMIT 0',
        *['SELECT 1 FROM stone_game.' + table + ' LIMIT 0' for table in
          ('game', 'game_result', 'game_participant', 'member_stats', 'move', 'rating_history')]
    ]
}


class RequirementError(RuntimeError):
    pass


def need(ok, reason):
    if not ok:
        raise RequirementError(reason)


def validate_handoff(payload):
    mode = payload.get('data_mode', 'migrated')
    need(mode in ('migrated', 'fresh_test'), 'KNOWN_DATA_MODE_REQUIRED_NO_SQL_ATTEMPT')
    if mode == 'fresh_test':
        need(payload.get('fresh_test_target_accepted') is True
             and payload.get('legacy_targets_excluded') is True
             and payload.get('target_database_host') == payload.get('host')
             and payload.get('reported_schema_revision') == '20260902_0002'
             and all(isinstance(payload.get(k), str) and payload[k].strip() for k in
                     ('fresh_target_evidence_ref', 'purpose_accounts_evidence_ref',
                      'schema_prepared_evidence_ref', 'target_redis_host')),
             'FRESH_TEST_TARGET_AND_SCHEMA_HANDOFF_REQUIRED_NO_SQL_ATTEMPT')
        need(not payload.get('final_import_compare_and_schema_handoff_accepted')
             and not payload.get('source_writer_hold_accepted'), 'FRESH_MODE_LEGACY_ASSERTIONS_REJECTED')
    else:
        need(payload.get('final_import_compare_and_schema_handoff_accepted') is True,
             'FINAL_DATA_HANDOFF_REQUIRED_NO_SQL_ATTEMPT')
        need(payload.get('source_writer_hold_accepted') is True,
             'SOURCE_WRITER_HOLD_REQUIRED_NO_SQL_ATTEMPT')
    return mode


async def check_account(account, payload, context, connect):
    connection = None
    try:
        user = account['user']
        need(user in TABLE_READS, 'EXPECTED_PURPOSE_ACCOUNT_REQUIRED')
        connection = await connect(host=payload['host'], port=3306, user=user,
                                   password=account['password'], db='stone_game',
                                   ssl=context, connect_timeout=8, autocommit=True)
        async with connection.cursor() as cursor:
            await cursor.execute('SET SESSION TRANSACTION READ ONLY')
            await cursor.execute('SELECT CURRENT_USER(), @@GLOBAL.max_connections, @@GLOBAL.max_user_connections')
            matched, maximum, global_limit = await cursor.fetchone()
            matched = matched.decode() if isinstance(matched, bytes) else matched
            need(matched.split('@')[0] == user, 'EFFECTIVE_ACCOUNT_MISMATCH')
            await cursor.execute("SHOW SESSION STATUS LIKE 'Ssl_cipher'")
            cipher = await cursor.fetchone()
            need(cipher is not None and bool(cipher[1]), 'TLS_REQUIRED')
            await cursor.execute('SHOW GRANTS FOR CURRENT_USER()')
            grants = '\n'.join(row[0].decode() if isinstance(row[0], bytes) else row[0]
                               for row in await cursor.fetchall())
            limits = [int(n) for n in re.findall(r'MAX_USER_CONNECTIONS\s+(\d+)', grants, re.I)]
            effective = (min(limits) if limits else 0) or int(global_limit)
            for query in TABLE_READS[user]:
                await cursor.execute(query)
                need(not await cursor.fetchall(), 'ZERO_ROW_PROBE_REQUIRED')
        covers = effective == 0 or effective >= 14
        budget_ok = int(maximum) >= 43 if payload.get('data_mode') == 'fresh_test' else int(maximum) == 68
        return {'account': user, 'status': 'PASS_PURPOSE_ACCOUNT_READ_ONLY' if covers and budget_ok
                else 'CHECK_PURPOSE_ACCOUNT_CONNECTION_BUDGET',
                'tls': True, 'max_connections': int(maximum),
                'effective_user_connection_limit': effective,
                'account_limit_covers_candidate_14': covers,
                'zero_row_table_probes': len(TABLE_READS[user]),
                'full_grants_writes_pool_or_business_accepted': False,
                'schema_revision': 'NOT_QUERIED_BY_PURPOSE_ACCOUNT_USE_SEPARATE_MIGRATION_CURRENT'}
    except Exception as error:
        code = next((x for x in error.args if type(x) is int), None)
        return {'account': account.get('user'), 'status': 'STOP_PURPOSE_ACCOUNT_READ_ONLY',
                'error_code': code,
                'reason': str(error) if type(error) is RequirementError else type(error).__name__,
                'raw_output': 'NOT_PRINTED'}
    finally:
        if connection is not None:
            connection.close()


async def assess(payload, context, connect):
    mode = validate_handoff(payload)
    need(payload.get('database') == 'stone_game', 'EXPECTED_DATABASE_REQUIRED')
    accounts = payload.get('accounts', [])
    need(len(accounts) == 2 and {x.get('user') for x in accounts} == set(TABLE_READS),
         'EXACT_TWO_PURPOSE_ACCOUNTS_REQUIRED')
    need(all(isinstance(x.get('password'), str) and x['password'] for x in accounts),
         'EXISTING_PROTECTED_PASSWORDS_REQUIRED')
    rows = []
    for account in accounts:
        rows.append(await asyncio.wait_for(check_account(account, payload, context, connect), 25))
    good = ('PASS_FRESH_TEST_PURPOSE_READS_NOT_APP_ACTIVATION' if mode == 'fresh_test'
            else 'PASS_POST_IMPORT_PURPOSE_READS_NOT_APP_ACTIVATION')
    return {'status': good
            if all(x['status'] == 'PASS_PURPOSE_ACCOUNT_READ_ONLY' for x in rows)
            else 'STOP_POST_IMPORT_PURPOSE_READS',
            'accounts': rows, 'data_mode': mode, 'database_writes': 0, 'schema_changes': 0,
            'migration_current_run': False, 'pool_operating_values_adopted': False,
            'application_started': False, 'automatic_retry': False,
            'credential_values_printed': False}


def main():
    try:
        payload = json.load(sys.stdin)
        # Check the handoff before loading any credential client or opening a socket.
        validate_handoff(payload)
        ca_file = '/run/rds-ca/ca.crt'
        with open(ca_file, 'rb') as stream:
            need(hashlib.sha256(stream.read()).hexdigest() == payload.get('ca_sha256'),
                 'ACCEPTED_DATABASE_CA_REQUIRED')
        context = ssl.create_default_context(cafile=ca_file)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        import asyncmy
        result = asyncio.run(assess(payload, context, asyncmy.connect))
    except Exception as error:
        result = {'status': 'STOP_POST_IMPORT_PURPOSE_READS',
                  'reason': str(error) if type(error) is RequirementError else type(error).__name__,
                  'raw_output': 'NOT_PRINTED', 'database_writes': 0,
                  'application_started': False, 'automatic_retry': False}
    print(json.dumps(result))


if __name__ == '__main__':
    main()

