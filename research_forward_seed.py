"""Private, exact-seed onboarding for the frozen forward shadow protocol.

No market download, scheduler, model adoption, or broker orders. No uploaded code
is executed. All writes are insert-only into an isolated PostgreSQL namespace.
"""
import gzip
import hashlib
import io

NS = 'nova_forward_shadow_v1'
KEY = 'v6_liquidity_shadow_v1'
SEED_SHA = '575cea6ef71c32cab44fbf6c2067d78c273379940167caad12ff5d560dccbdb2'
SEED_SIZE = 16494592
SEED_DAY = '20261125'
SEED_BARS = 179772
MAX_UPLOAD = 12 * 1024 * 1024
# Local guard, not a provider quota or a measurement of total project storage.
MAX_DB_BEFORE_SEED = 384 * 1024 * 1024
DDL = (
    f'CREATE SCHEMA IF NOT EXISTS {NS}',
    f'''CREATE TABLE IF NOT EXISTS {NS}.checkpoint (
        id text PRIMARY KEY, revision bigint NOT NULL CHECK(revision>=0),
        owner text, acquired_at timestamptz, digest text NOT NULL, state bytea NOT NULL,
        last_day text NOT NULL, updated_at timestamptz NOT NULL DEFAULT now())''',
    f'''CREATE TABLE IF NOT EXISTS {NS}.response (
        id text NOT NULL REFERENCES {NS}.checkpoint(id), day text NOT NULL, service text NOT NULL,
        dispatched_at timestamptz NOT NULL DEFAULT now(), body bytea, digest text,
        PRIMARY KEY(id,day,service), CHECK((body IS NULL)=(digest IS NULL)))''',
)

class SeedError(ValueError):
    pass

def verify_upload(blob):
    if not 0 < len(blob) <= MAX_UPLOAD:
        raise SeedError('UPLOAD_SIZE')
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(blob)) as stream:
            raw = stream.read(SEED_SIZE + 1)
    except (OSError, EOFError, ValueError):
        raise SeedError('GZIP_INVALID') from None
    if len(raw) != SEED_SIZE or hashlib.sha256(raw).hexdigest() != SEED_SHA:
        raise SeedError('SEED_HASH_MISMATCH')
    # Only exact, locally verified SQLite image is permitted. No other archive,
    # arbitrary SQL, untrusted database schema, or uploaded source is opened.
    return gzip.compress(raw, compresslevel=4, mtime=0)

def read_status(cur):
    cur.execute("SELECT pg_database_size(current_database()), to_regclass(%s)",
                (f'{NS}.checkpoint',))
    size, table = cur.fetchone()
    report = {'database_bytes': int(size), 'seed_initialized': False,
              'orders_enabled': False, 'strategy_adoption': 'HOLD',
              'storage_guard_bytes': MAX_DB_BEFORE_SEED}
    if table is None:
        return report
    cur.execute(f'SELECT revision,last_day,digest,octet_length(state),owner IS NOT NULL '
                f'FROM {NS}.checkpoint WHERE id=%s', (KEY,))
    row = cur.fetchone()
    if row is None:
        return report
    revision, day, digest, n, busy = row
    if revision == 0 and (digest != SEED_SHA or day != SEED_DAY):
        raise SeedError('EXISTING_SEED_MISMATCH')
    report.update(seed_initialized=True, revision=int(revision), last_day=day,
                  compressed_bytes=int(n), busy=bool(busy),
                  original_seed_hash_match=(digest == SEED_SHA) if revision == 0 else None)
    return report

def status(dsn):
    import psycopg
    if not dsn:
        raise SeedError('DATABASE_NOT_CONFIGURED')
    with psycopg.connect(dsn, connect_timeout=10) as db, db.cursor() as cur:
        cur.execute('SET TRANSACTION READ ONLY')
        cur.execute("SET LOCAL statement_timeout = '10s'")
        return read_status(cur)

def initialize(dsn, blob):
    compressed = verify_upload(blob)
    if not dsn:
        raise SeedError('DATABASE_NOT_CONFIGURED')
    import psycopg
    with psycopg.connect(dsn, connect_timeout=10) as db, db.cursor() as cur:
        cur.execute("SET LOCAL statement_timeout = '30s'")
        # Transaction lock compatible with Neon transaction pooling.
        cur.execute('SELECT pg_try_advisory_xact_lock(%s)', (601576110,))
        if cur.fetchone() != (True,):
            raise SeedError('SEED_BUSY')
        before = read_status(cur)
        if before['seed_initialized']:
            return {**before, 'inserted': False}
        if before['database_bytes'] + len(compressed) > MAX_DB_BEFORE_SEED:
            raise SeedError('LOCAL_STORAGE_GUARD')
        for sql in DDL:
            cur.execute(sql)
        cur.execute(f'INSERT INTO {NS}.checkpoint(id,revision,digest,state,last_day) '
                    'VALUES (%s,0,%s,%s,%s) ON CONFLICT(id) DO NOTHING',
                    (KEY, SEED_SHA, compressed, SEED_DAY))
        inserted = cur.rowcount == 1
        report = read_status(cur)
        if not report['seed_initialized']:
            raise SeedError('SEED_READBACK_FAILED')
        return {**report, 'inserted': inserted}
