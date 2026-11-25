"""Same-day, bounded prospective collection. No orders or historical backfill."""
from datetime import datetime
from zoneinfo import ZoneInfo
import json

KST = ZoneInfo('Asia/Seoul')
ACCEPTED = ('SAVED', 'EXISTS', 'PRICE_ONLY', 'UNVERIFIED', 'DISABLED', 'ERROR')


def permitted(day, now, calendar):
    from collection_gate import inspect_gate
    return inspect_gate(day, now, calendar)['reason'] == 'PERMITTED'


def run(day, symbols, *, fetch, record, existing, now, calendar, progress):
    results = {}
    if not permitted(day, now(), calendar):
        return 'skipped', results
    prior = existing(day)
    for symbol in symbols:
        if not permitted(day, now(), calendar):
            return 'partial', results
        try:
            if prior.get(symbol) in ('EXISTS', 'PRICE_ONLY'):
                status = prior[symbol]
            else:
                payload = fetch(symbol)
                observed = now()
                if not permitted(day, observed, calendar):
                    return 'partial', results
                status = record(payload, now=observed)
            results[symbol] = status if status in ACCEPTED else 'ERROR'
        except Exception:
            results[symbol] = 'ERROR'
        progress(dict(results))
    return ('complete' if all(v in ('SAVED', 'EXISTS') for v in results.values())
            else 'partial'), results


def existing_for_day(day):
    from core561_evidence import TABLE, SCHEMA
    from evidence_store import connection
    with connection() as (db, kind):
        cur = db.cursor()
        cur.execute(SCHEMA)
        sql = f'SELECT symbol,payload FROM {TABLE} WHERE trading_day=?'
        cur.execute(sql.replace('?', '%s') if kind == 'postgresql' else sql, (day,))
        results = {}
        for symbol, raw in cur.fetchall():
            p = json.loads(raw)
            if p.get('symbol') == symbol and p.get('day') == day and p.get('price_basis') == 'KIS_REGULAR_MINUTE':
                results[symbol] = 'EXISTS' if p.get('signals') else 'PRICE_ONLY'
        return results


def checkpoint_probe():
    """Read-only production evidence: never create or modify the checkpoint table."""
    import os
    from evidence_store import backend
    if backend() != 'postgresql':
        return {'status': 'NOT_POSTGRESQL', 'table_exists': None, 'rows': None}
    try:
        import psycopg
        with psycopg.connect(os.environ['DATABASE_URL'].strip(), connect_timeout=5) as db:
            with db.cursor() as cur:
                cur.execute('SET TRANSACTION READ ONLY')
                cur.execute('SET LOCAL statement_timeout = 5000')
                cur.execute("SELECT to_regclass('nova_research_checkpoints_v565') IS NOT NULL")
                exists = bool(cur.fetchone()[0])
                count = None
                if exists:
                    cur.execute('SELECT COUNT(*) FROM nova_research_checkpoints_v565')
                    count = int(cur.fetchone()[0])
                return {'status': 'READ_OK', 'table_exists': exists, 'rows': count}
    except Exception:
        return {'status': 'READ_FAILED', 'table_exists': None, 'rows': None}
