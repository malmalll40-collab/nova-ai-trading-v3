"""Resumable KRX staging and atomic, immutable full-cohort publication in PostgreSQL."""
import hashlib
import json
import time
from contextlib import closing
from datetime import date
from decimal import Decimal

import psycopg
import requests

from research_history_checkpoint import canonical, digest
from research_krx_bulk import CODES, FIELDS, SYMBOLS, SourceError, archive, select_rows, validate_day, normalize
from research_krx_collection import ORDER, read_one
from research_krx_snapshot import TABLE as LEGACY, SERVICES
from research_krx_period import INDEX, INDEX_SYMBOLS, STOCK, fetch_period

LOCK = 601576054
DAYS_PER_RESPONSE = 5
STAGE = 'nova_krx_bulk_stage_v1'
BARS = 'nova_krx_bulk_bars_v1'
RELEASES = 'nova_krx_bulk_releases_v1'
PERIOD = 'nova_krx_period_source_v1'
SCHEMA = (
    f'''CREATE TABLE IF NOT EXISTS {PERIOD} (dataset text NOT NULL, symbol text NOT NULL,
        source text NOT NULL, state text NOT NULL, body jsonb NOT NULL, total integer NOT NULL,
        PRIMARY KEY(dataset,symbol))''',
    f'CREATE TABLE IF NOT EXISTS {STAGE} (dataset text NOT NULL, day date NOT NULL, body text NOT NULL, checksum text NOT NULL, PRIMARY KEY(dataset,day))',
    f'''CREATE TABLE IF NOT EXISTS {BARS} (
        dataset text NOT NULL, day date NOT NULL, symbol text NOT NULL, service text NOT NULL,
        open numeric, high numeric, low numeric, close numeric, volume bigint, turnover bigint,
        quote_status text NOT NULL, source_row text NOT NULL, PRIMARY KEY(dataset,day,symbol))''',
    f'''CREATE TABLE IF NOT EXISTS {RELEASES} (dataset text PRIMARY KEY, checksum text NOT NULL,
        rows integer NOT NULL, sessions integer NOT NULL, overlap integer NOT NULL,
        published_at timestamptz NOT NULL DEFAULT now())''',
)


class KRXReader:
    """Sequential official reads for uncovered data only. No implicit retries."""
    def __init__(self, key):
        self.key = key
        self.session = requests.Session()
        self.next_start = 0.0

    def read(self, service, day):
        time.sleep(max(0, self.next_start - time.monotonic()))
        self.next_start = time.monotonic() + 0.25
        status, payload = read_one(self.session, self.key, service, day)
        if status != 'success':
            raise SourceError('KRX_' + status.upper(), day, service)
        return select_rows(service, day, payload)

    def close(self):
        self.session.close()


def initialize(cur):
    for ddl in SCHEMA:
        cur.execute(ddl)


def lock(cur):
    cur.execute('SELECT pg_try_advisory_xact_lock(%s)', (LOCK,))
    return cur.fetchone()[0]


def status(cur, dataset, days):
    base = {'dataset': dataset, 'expected_sessions': len(days), 'expected_rows': len(days) * len(SYMBOLS)}
    cur.execute('SELECT to_regclass(%s)', (RELEASES,))
    if cur.fetchone()[0] is None:
        return {**base, 'status': 'PENDING', 'staged_sessions': 0, 'published_rows': 0}
    cur.execute(f'SELECT checksum,rows,sessions,overlap FROM {RELEASES} WHERE dataset=%s', (dataset,))
    release = cur.fetchone()
    if release:
        checksum, rows, sessions, overlap = release
        return {**base, 'status': 'PUBLISHED', 'staged_sessions': sessions,
                'published_rows': rows, 'sha256': checksum, 'legacy_rows_compared': overlap}
    cur.execute(f'SELECT count(*) FROM {STAGE} WHERE dataset=%s', (dataset,))
    return {**base, 'status': 'STAGING', 'staged_sessions': cur.fetchone()[0], 'published_rows': 0}


def stage(cur, dataset, day, selected):
    validate_day(day, selected)
    body = canonical(sorted(selected, key=lambda pair: (pair[0], canonical(pair[1]))))
    cur.execute(f'''INSERT INTO {STAGE} VALUES (%s,%s,%s,%s) ON CONFLICT (dataset,day)
        DO NOTHING''', (dataset, day, body, digest(body)))
    if cur.rowcount == 0:
        cur.execute(f'SELECT body,checksum FROM {STAGE} WHERE dataset=%s AND day=%s', (dataset, day))
        if cur.fetchone() != (body, digest(body)):
            raise SourceError('KRX_STAGE_CONFLICT', day)


def read_bars(cur, dataset):
    cur.execute(f'''SELECT day,symbol,service,open,high,low,close,volume,turnover,quote_status,source_row
        FROM {BARS} WHERE dataset=%s ORDER BY day,symbol COLLATE "C"''', (dataset,))
    for day, symbol, service, *values, quote_status, body in cur:
        bar = normalize(service, json.loads(body), day)
        expected = [Decimal(str(bar[k])) if bar[k] is not None else None for k in FIELDS]
        if bar['symbol'] != symbol or values != expected or bar['quote_status'] != quote_status:
            raise SourceError('KRX_DB_VALUE_CONFLICT', day, service, symbol)
        yield day.isoformat(), symbol, service, body


def signature(entries):
    checksum = hashlib.sha256()
    for stamp, symbol, service, raw in entries:
        checksum.update((canonical([stamp, symbol, service, json.loads(raw)]) + '\n').encode())
    return checksum.hexdigest()


def compare_legacy(cur, dataset, bars):
    cur.execute('SELECT to_regclass(%s)', (LEGACY,))
    if cur.fetchone()[0] is None:
        return 0
    cur.execute(f'SELECT day,service,payload,digest FROM {LEGACY} WHERE dataset=%s', (dataset,))
    overlap = 0
    for stamp, service, raw, checksum in cur:
        day = date.fromisoformat(stamp)
        if digest(raw) != checksum:
            raise SourceError('KRX_LEGACY_CHECKSUM_CONFLICT', day, service)
        for symbol, old in json.loads(raw).items():
            new = bars.get((stamp, symbol))
            if new is None or (new['service'] != service and new['service'] not in (INDEX,STOCK,'KRX_existing_stock')) or any(new.get(k) != v for k,v in old.items()):
                raise SourceError('KRX_LEGACY_VALUE_CONFLICT', day, service, symbol)
            overlap += 1
    return overlap


def publish(cur, dataset, days):
    """Caller holds the dataset lock. COPY and release marker commit in the same transaction."""
    cur.execute(f'SELECT day,body,checksum FROM {STAGE} WHERE dataset=%s ORDER BY day', (dataset,))
    items = cur.fetchall()
    if tuple(row[0] for row in items) != tuple(days):
        raise SourceError('KRX_INCOMPLETE_CALENDAR')
    expected = []
    normalized = {}
    for day, body, stored_hash in items:
        if digest(body) != stored_hash:
            raise SourceError('KRX_STAGE_CHECKSUM_CONFLICT', day)
        for symbol, (bar, raw) in sorted(validate_day(day, json.loads(body)).items()):
            normalized[(day.isoformat(), symbol)] = bar
            expected.append((day.isoformat(), symbol, bar['service'], canonical(raw)))
    overlap = compare_legacy(cur, dataset, normalized)
    checksum = signature(expected)
    cur.execute(f'SELECT checksum FROM {RELEASES} WHERE dataset=%s', (dataset,))
    existing = cur.fetchone()
    if existing is None:
        with cur.copy(f'COPY {BARS} (dataset,day,symbol,service,open,high,low,close,volume,turnover,quote_status,source_row) FROM STDIN') as cp:
            for stamp, symbol, service, raw in expected:
                bar = normalized[(stamp, symbol)]
                cp.write_row((dataset, stamp, symbol, service, *(bar[k] for k in FIELDS), bar['quote_status'], raw))
    elif existing[0] != checksum:
        raise SourceError('KRX_PUBLISHED_REVISION_CONFLICT')
    if signature(read_bars(cur, dataset)) != checksum:
        raise SourceError('KRX_DB_READBACK_CONFLICT')
    cur.execute(f'INSERT INTO {RELEASES}(dataset,checksum,rows,sessions,overlap) VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING',
                (dataset, checksum, len(expected), len(days), overlap))
    return status(cur, dataset, days)


def step(db, dataset, days, reader, limit=DAYS_PER_RESPONSE):
    # Each short checkpoint is independent; the final publication is all-or-nothing.
    with db.transaction(), db.cursor() as cur:
        if not lock(cur):
            return {'status': 'BUSY'}
        initialize(cur)
        info = status(cur, dataset, days)
        if info['status'] == 'PUBLISHED':
            return info
        cur.execute(f'SELECT day FROM {STAGE} WHERE dataset=%s', (dataset,))
        saved = {r[0] for r in cur}
    pending = [d for d in days if d not in saved]
    for day in pending[:limit]:
        with db.transaction(), db.cursor() as cur:
            if not lock(cur):
                return {'status': 'BUSY'}
            # Recheck after acquiring the shared DB lock in case another client advanced.
            cur.execute(f'SELECT 1 FROM {STAGE} WHERE dataset=%s AND day=%s', (dataset, day))
            if cur.fetchone() is None:
                stage(cur, dataset, day, reader(day))
    with db.transaction(), db.cursor() as cur:
        if not lock(cur):
            return {'status': 'BUSY'}
        info = status(cur, dataset, days)
        if info['status'] != 'PUBLISHED' and info['staged_sessions'] == len(days):
            return publish(cur, dataset, days)
        return info


def period_step(db, dataset, days, key):
    """Download an entire symbol history per published query. Count only actual provider rows."""
    with db.transaction(), db.cursor() as cur:
        if not lock(cur):
            return {'status':'BUSY'}
        initialize(cur)
        cur.execute(f'SELECT symbol,source,state FROM {PERIOD} WHERE dataset=%s', (dataset,))
        saved = cur.fetchall()
        seen = {r[0] for r in saved}
        denied = {r[1] for r in saved if r[2] == 'AUTH_DENIED'}
        if not key:
            return None
        for symbol in sorted(SYMBOLS):
            source = INDEX if symbol in INDEX_SYMBOLS.values() else STOCK
            if symbol in seen or source in denied:
                continue
            try:
                with requests.Session() as session:
                    rows = fetch_period(key, symbol, days, session)
                for stamp, row in rows.items():
                    if normalize(source, row, date.fromisoformat(stamp))['symbol'] != symbol:
                        raise SourceError('FSC_IDENTITY_MISMATCH', service=source, symbol=symbol)
                state = 'RECEIVED' if rows else 'NO_DATA'
            except SourceError as exc:
                if str(exc) != 'FSC_AUTH_DENIED':
                    raise
                rows, state = {}, 'AUTH_DENIED'
            cur.execute(f'INSERT INTO {PERIOD} VALUES (%s,%s,%s,%s,%s::jsonb,%s)',
                        (dataset, symbol, source, state, canonical(rows), len(rows)))
            return {'status':'PERIOD', 'symbol':symbol, 'source':source, 'source_state':state,
                    'received_rows':len(rows), 'processed_series':len(seen)+1}
    return None


def complete_day(db, dataset, day, reader):
    """Read staged period data first. Use earlier verified stocks and query only remaining services."""
    stamp = day.isoformat()
    with db.cursor() as cur:
        cur.execute(f'SELECT symbol,source,body->%s FROM {PERIOD} WHERE dataset=%s AND body ? %s',
                    (stamp,dataset,stamp))
        selected = {symbol:[source, body] for symbol,source,body in cur}
        missing = SYMBOLS - selected.keys()
        if not missing:
            return list(selected.values())
        stocks = missing & set(CODES.values())
        if stocks:
            cur.execute('SELECT to_regclass(%s)', (LEGACY,))
            if cur.fetchone()[0]:
                cur.execute(f'SELECT service,payload,digest FROM {LEGACY} WHERE dataset=%s AND day=%s',
                            (dataset,stamp))
                for service, body, checksum in cur.fetchall():
                    if service not in ('stk_bydd_trd','ksq_bydd_trd'):
                        continue
                    if digest(body) != checksum:
                        raise SourceError('KRX_LEGACY_CHECKSUM_CONFLICT', day, service)
                    for symbol, values in json.loads(body).items():
                        if symbol in stocks:
                            raw = {'day':stamp,'symbol':symbol,'market_service':service,'values':values}
                            selected[symbol] = ['KRX_existing_stock',raw]
    missing = SYMBOLS - selected.keys()
    services = [s for s in ORDER[:2] if SERVICES[s][0] in missing]
    if missing & set(CODES.values()):
        services.extend(ORDER[2:])
    for service in services:
        if not reader.key:
            raise SourceError('KRX_KEY_REQUIRED_FOR_GAP', day, service)
        for source, raw in reader.read(service, day):
            bar = normalize(source, raw, day)
            if bar['symbol'] in missing:
                if bar['symbol'] in selected:
                    raise SourceError('KRX_DUPLICATE_SYMBOL', day, source, bar['symbol'])
                selected[bar['symbol']] = [source,raw]
    return list(selected.values())


def run_step(dsn, key, dataset, days, period_key=''):
    with psycopg.connect(dsn, connect_timeout=8, autocommit=True) as db:
        with db.cursor() as cur:
            info = status(cur, dataset, days)
        if info['status'] == 'PUBLISHED':
            return info
        response = period_step(db, dataset, days, period_key)
        if response is not None:
            return response
        with closing(KRXReader(key)) as reader:
            return step(db, dataset, days, lambda day: complete_day(db, dataset, day, reader))


def download(cur, dataset, days):
    info = status(cur, dataset, days)
    if info['status'] != 'PUBLISHED':
        raise SourceError('KRX_NOT_PUBLISHED')
    return archive(read_bars(cur, dataset), days, info['sha256'], info['legacy_rows_compared'])
