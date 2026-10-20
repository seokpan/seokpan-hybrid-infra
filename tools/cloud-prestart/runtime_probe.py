"""New-cluster read-only Schema and captured-mode admission. Private stdin only."""
import asyncio
import hashlib
import json
import ssl
import sys
from urllib.parse import unquote, urlsplit


class Stop(RuntimeError):
    pass


def need(ok, code):
    if not ok:
        raise Stop(code)


def data_admission(payload):
    mode = payload.get('data_mode', 'migrated')
    need(mode in ('migrated', 'fresh_test'), 'KNOWN_DATA_MODE_REQUIRED')
    if mode == 'fresh_test':
        need(payload.get('fresh_test_target_accepted') is True
             and payload.get('legacy_targets_excluded') is True
             and payload.get('target_database_host') == payload.get('database_host')
             and payload.get('target_redis_host') == payload.get('redis_host')
             and payload.get('reported_schema_revision') == '20260902_0002'
             and all(isinstance(payload.get(k), str) and payload[k].strip() for k in
                     ('fresh_target_evidence_ref', 'purpose_accounts_evidence_ref', 'schema_prepared_evidence_ref')),
             'FRESH_TEST_ISOLATION_ACCOUNTS_SCHEMA_REQUIRED')
        need(not payload.get('final_data_accepted') and not payload.get('all_old_writers_stopped'),
             'FRESH_MODE_LEGACY_ASSERTIONS_REJECTED')
    else:
        need(payload.get('final_data_accepted') is True and payload.get('all_old_writers_stopped') is True,
             'FINAL_DATA_AND_OLD_WRITER_HOLD_REQUIRED')
    return mode


def mode_admission(room_keys, active_games, inconsistent_results):
    # Existing room/task data needs case-specific reconciliation; never infer
    # safety from TTL, delete keys, or manufacture historical start intents.
    need(room_keys == 0 and active_games == 0 and inconsistent_results == 0,
         'EXISTING_GAME_STATE_REQUIRES_CLASSIFICATION_NO_AUTOMATIC_TRANSITION')
    return {'status': 'PASS_CAPTURED_EMPTY_RUNTIME_ADMISSION', 'mode': 'captured',
            'room_family_keys': 0, 'active_games': 0, 'inconsistent_results': 0,
            'redis_writes': 0, 'database_writes': 0,
            'retained_members_and_finished_history': True}


async def bounded_room_scan(client, max_pages=100, max_keys=20000):
    cursor, pages, count = 0, 0, 0
    while True:
        cursor, keys = await client.scan(cursor=cursor, match='stone:v1:room:*', count=200)
        pages += 1
        count += len(keys)
        need(count <= max_keys and pages <= max_pages, 'ROOM_SCAN_LIMIT_CLASSIFY_SEPARATELY')
        if cursor == 0:
            return count


async def assess(payload, connect, redis_factory):
    data_mode = data_admission(payload)
    dsn = urlsplit(payload['schema_url'])
    need(dsn.scheme == 'mysql+asyncmy' and dsn.hostname == payload['database_host']
         and dsn.port == 3306 and unquote(dsn.path.lstrip('/')) == 'stone_game'
         and unquote(dsn.username or '') == 'db_admin' and bool(dsn.password)
         and not dsn.query and not dsn.fragment, 'EXACT_SCHEMA_READER_TARGET_REQUIRED')
    ca = '/run/rds-ca/ca.crt'
    rca = '/run/redis-ca/ca.crt'
    for path, expected in ((ca, payload['database_ca_sha256']), (rca, payload['redis_ca_sha256'])):
        with open(path, 'rb') as stream:
            need(hashlib.sha256(stream.read()).hexdigest() == expected, 'CURRENT_CA_HASH_REQUIRED')
    context = ssl.create_default_context(cafile=ca)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    connection = await connect(host=dsn.hostname, port=3306, user='db_admin',
                               password=unquote(dsn.password), db='stone_game', ssl=context,
                               autocommit=True, connect_timeout=8)
    try:
        async with connection.cursor() as cur:
            await cur.execute('SET SESSION TRANSACTION READ ONLY')
            await cur.execute("SHOW SESSION STATUS LIKE 'Ssl_cipher'")
            tls = await cur.fetchone()
            need(tls and tls[1], 'SCHEMA_READER_TLS_REQUIRED')
            await cur.execute('SELECT version_num FROM stone_game.alembic_version')
            versions = [v[0].decode() if isinstance(v[0], bytes) else v[0] for v in await cur.fetchall()]
            need(versions == ['20260902_0002'], 'SCHEMA_DIFFERENCE_NO_UPGRADE_OR_STAMP')
            if data_mode == 'fresh_test':
                # 실제 목표 DB에서 읽기만 수행하며 기존 데이터를 지우지 않는다.
                for table in ('member', 'game', 'game_result', 'game_participant',
                              'member_stats', 'move', 'rating_history'):
                    await cur.execute('SELECT 1 FROM stone_game.' + table + ' LIMIT 1')
                    need(not await cur.fetchall(), 'FRESH_TEST_DATABASE_NOT_EMPTY_NO_DELETE_OR_IMPORT')
            await cur.execute("SELECT COUNT(*) FROM stone_game.game WHERE status='IN_PROGRESS'")
            active = int((await cur.fetchone())[0])
            await cur.execute("SELECT COUNT(*) FROM stone_game.game g LEFT JOIN stone_game.game_result r "
                              "ON r.game_id=g.game_id WHERE (g.status='COMPLETED' AND r.game_id IS NULL) "
                              "OR (g.status<>'COMPLETED' AND r.game_id IS NOT NULL)")
            inconsistent = int((await cur.fetchone())[0])
    finally:
        connection.close()
    redis = redis_factory(host=payload['redis_host'], port=6379, db=0,
                          password=payload['redis_auth'], ssl=True, ssl_cert_reqs='required',
                          ssl_check_hostname=True, ssl_ca_certs=rca,
                          socket_connect_timeout=8, socket_timeout=8)
    try:
        keys = await bounded_room_scan(redis)
    finally:
        await redis.aclose()
    mode = mode_admission(keys, active, inconsistent)
    return {'status': 'PASS_SCHEMA_AND_CAPTURED_ADMISSION_NOT_BUSINESS',
            'schema_revision': versions[0], 'schema_check': 'READ_ALEMBIC_VERSION_NOT_UPGRADE',
            'mode_admission': mode, 'database_writes': 0, 'redis_writes': 0,
            'migration_upgrade_or_stamp': False, 'runtime_pool_tested': False,
            'data_mode': data_mode, 'fresh_database_empty_verified': data_mode == 'fresh_test',
            'credential_values_printed': False}


def main():
    try:
        payload = json.load(sys.stdin)
        data_admission(payload)
        import asyncmy
        from redis.asyncio import Redis
        result = asyncio.run(asyncio.wait_for(assess(payload, asyncmy.connect, Redis), 75))
    except Exception as error:
        result = {'status': 'STOP_SCHEMA_AND_MODE',
                  'reason': str(error) if type(error) is Stop else type(error).__name__,
                  'database_writes': 0, 'redis_writes': 0, 'automatic_retry': False,
                  'raw_output_printed': False}
    print(json.dumps(result))


if __name__ == '__main__':
    main()

