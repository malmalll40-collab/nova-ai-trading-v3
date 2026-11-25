"""Bounded historical research cache; never a prospective signal or order store."""
from contextlib import contextmanager, closing
from datetime import datetime, timezone, timedelta
import hashlib
import json
import os
import re
import sqlite3
from pathlib import Path
from zoneinfo import ZoneInfo

from evidence_store import backend
from market_sessions import krx_session

VERSION = '5.6.5'
TABLE = 'nova_research_checkpoints_v565'
MAX_BYTES = 2_000_000
TTL = timedelta(hours=2)
RETENTION = timedelta(hours=48)
SCHEMA = f'''CREATE TABLE IF NOT EXISTS {TABLE} (
    scope TEXT NOT NULL, symbol TEXT NOT NULL, saved_at TEXT NOT NULL,
    payload TEXT NOT NULL, sha256 TEXT NOT NULL, PRIMARY KEY (scope, symbol))'''
KST = ZoneInfo('Asia/Seoul')


def scope_for(symbols, source, cost, now=None):
    """Only same verified regular-session day, after its close; no cross-day reuse."""
    now = (now or datetime.now(timezone.utc)).astimezone(KST)
    session = krx_session(now)
    close = session.get('close_kst')
    if not session.get('calendar_verified') or not close or now.strftime('%H:%M') < close:
        return None
    if source not in ('kis', 'auto') or type(cost) is not int or not 0 <= cost <= 100:
        return None
    if not 2 <= len(symbols) <= 25 or len(set(symbols)) != len(symbols) or any(not re.fullmatch(r'\d{6}\.(KS|KQ)', s) for s in symbols):
        return None
    spec = {'version': VERSION, 'revision': os.getenv('RENDER_GIT_COMMIT', VERSION),
            'day': now.date().isoformat(), 'symbols': list(symbols), 'source': source, 'cost': cost}
    return {**spec, 'key': hashlib.sha256(json.dumps(spec, sort_keys=True, separators=(',', ':')).encode()).hexdigest()}


def eligible_data(data, scope):
    session = data.get('completed_session') or {}
    integrity = data.get('daily_integrity') or {}
    day = scope['day']
    return (data.get('status') == 'READY' and data.get('source') == 'kis' and
            session.get('calendar_verified') is True and session.get('stale') is False and
            session.get('expected_completed_session') == session.get('latest_completed_session') == day and
            integrity.get('verified') is True and integrity.get('regular_volume_verified') is True and
            not integrity.get('signal_hold') and integrity.get('verification_basis') == 'KIS_REGULAR_MINUTE' and
            integrity.get('latest_date') == day and str(data.get('asof_last_bar', ''))[:10] == day)


def valid_row(row, scope, symbol):
    focus = (row.get('v54_accuracy') or {}).get('core_focus') or {}
    return (row.get('symbol') == symbol and symbol in scope['symbols'] and row.get('status') == 'READY' and
            row.get('source') == 'kis' and row.get('checkpoint_day') == scope['day'] and
            str(row.get('asof_last_bar', ''))[:10] == scope['day'] and focus.get('status') == 'READY' and
            focus.get('cost_bps') == scope['cost'] and bool(focus.get('_replay')))


def _stamp(now):
    return now.astimezone(timezone.utc).isoformat(timespec='microseconds')


@contextmanager
def _connection():
    kind = backend()
    if kind == 'postgresql':
        import psycopg
        with psycopg.connect(os.environ['DATABASE_URL'].strip(), connect_timeout=5) as db:
            with db.cursor() as cur:
                cur.execute('SET LOCAL statement_timeout = 5000')
                yield cur, kind
    elif kind == 'sqlite':
        path = Path(os.environ['NOVA_EVIDENCE_SQLITE_PATH']).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(str(path), timeout=5)) as db, db:
            yield db.cursor(), kind
    else:
        raise RuntimeError('checkpoint database unavailable')


def _execute(cur, kind, sql, params=()):
    return cur.execute(sql.replace('?', '%s') if kind == 'postgresql' else sql, params)


def save(scope, row, now=None):
    now = now or datetime.now(timezone.utc)
    if now.astimezone(KST).date().isoformat() != scope['day'] or not valid_row(row, scope, row.get('symbol')):
        return False
    raw = json.dumps(row, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
    payload = raw.encode('utf-8')
    if len(payload) > MAX_BYTES:
        return False
    with _connection() as (cur, kind):
        cur.execute(SCHEMA)
        _execute(cur, kind, f'DELETE FROM {TABLE} WHERE saved_at < ?', (_stamp(now - RETENTION),))
        _execute(cur, kind, f'''INSERT INTO {TABLE}(scope,symbol,saved_at,payload,sha256) VALUES(?,?,?,?,?)
            ON CONFLICT(scope,symbol) DO UPDATE SET saved_at=excluded.saved_at,payload=excluded.payload,sha256=excluded.sha256
            WHERE {TABLE}.saved_at <= excluded.saved_at''',
            (scope['key'], row['symbol'], _stamp(now), raw, hashlib.sha256(payload).hexdigest()))
    return True


def load(scope, now=None):
    now = now or datetime.now(timezone.utc)
    if now.astimezone(KST).date().isoformat() != scope['day']:
        return {}
    with _connection() as (cur, kind):
        cur.execute(SCHEMA)
        _execute(cur, kind, f'SELECT symbol,payload,sha256 FROM {TABLE} WHERE scope=? AND saved_at>=? AND saved_at<=? LIMIT 25',
                 (scope['key'], _stamp(now - TTL), _stamp(now)))
        rows = cur.fetchall()
    result = {}
    for symbol, raw, digest in rows:
        try:
            payload = raw.encode('utf-8')
            if len(payload) > MAX_BYTES or hashlib.sha256(payload).hexdigest() != digest:
                continue
            row = json.loads(raw)
            if valid_row(row, scope, symbol):
                result[symbol] = row
        except (TypeError, ValueError, AttributeError):
            continue
    return result
