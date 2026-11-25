"""Read-only prospective observation audit and safe validation diagnostics."""
import json
import math
import os
import re
from contextlib import closing
from pathlib import Path
from datetime import date
from zoneinfo import ZoneInfo

KST = ZoneInfo('Asia/Seoul')


def unverified_reason(a, now):
    """Explain existing snapshot gates, never override them or log raw payloads."""
    if not isinstance(a, dict): return 'PAYLOAD_INVALID'
    day = now.astimezone(KST).date().isoformat()
    g = a.get('daily_integrity') or {}
    s = a.get('completed_session') or {}
    c = a.get('chart') or {}
    if a.get('mode') != '스윙' or a.get('source') != 'kis' or a.get('is_demo') or a.get('asof_evaluation_only'):
        return 'SOURCE_OR_MODE_INVALID'
    if not re.fullmatch(r'\d{6}\.(KS|KQ)', str(a.get('symbol', ''))): return 'SYMBOL_INVALID'
    if now.astimezone(KST).hour < 18: return 'BEFORE_WINDOW'
    if not s.get('calendar_verified'): return 'SESSION_CALENDAR_UNVERIFIED'
    if g.get('signal_hold'): return 'DAILY_INTEGRITY_HOLD'
    if not g.get('verified'): return 'DAILY_BAR_UNVERIFIED'
    if not g.get('regular_volume_verified'): return 'REGULAR_VOLUME_UNVERIFIED'
    if g.get('verification_basis') != 'KIS_REGULAR_MINUTE': return 'PRICE_BASIS_UNVERIFIED'
    if g.get('latest_date') != day or s.get('expected_completed_session') != day or str(a.get('last_completed_bar', ''))[:10] != day:
        return 'COMPLETED_DAY_MISMATCH'
    if a.get('is_stale'): return 'STALE_PAYLOAD'
    try:
        if str(c['dates'][-1])[:10] != day: return 'CHART_DAY_MISMATCH'
        o, close = float(c['open'][-1]), float(c['close'][-1])
        if not (math.isfinite(o) and math.isfinite(close) and min(o, close) > 0): return 'PRICE_INVALID'
        if int(a.get('cost_bps_one_side', 15)) != 15: return 'COST_MISMATCH'
    except (KeyError, IndexError, TypeError, ValueError, OverflowError): return 'CHART_INVALID'
    return 'SNAPSHOT_REJECTED_OTHER'


def summarize_rows(day, symbols, rows, version, total):
    states = {}
    invalid = set()
    for symbol, raw in rows:
        if symbol not in symbols: continue
        try:
            p = json.loads(raw)
            valid = (p.get('id') == f'{version}|{symbol}|{day}' and p.get('version') == version and
                     p.get('symbol') == symbol and p.get('day') == day and p.get('cost_bps') == 15 and
                     p.get('price_basis') == 'KIS_REGULAR_MINUTE' and p.get('research_only') is True and
                     isinstance(p.get('signals'), dict))
            if not valid: raise ValueError('invalid')
            state = 'EXISTS' if p['signals'] else 'PRICE_ONLY'
            if symbol in states: invalid.add(symbol)
            states[symbol] = state
        except (ValueError, TypeError, AttributeError): invalid.add(symbol)
    verified = [s for s in symbols if states.get(s) == 'EXISTS' and s not in invalid]
    price_only = [s for s in symbols if states.get(s) == 'PRICE_ONLY' and s not in invalid]
    return {'day': day, 'status': 'READ_OK', 'total_rows': total, 'day_rows': len(rows),
            'verified_signals': len(verified), 'price_only': len(price_only), 'verified_symbols': verified,
            'missing_symbols': [s for s in symbols if s not in verified and s not in price_only],
            'invalid_symbols': [s for s in symbols if s in invalid]}


def audit_day(day, symbols):
    from evidence_store import backend
    from core561_evidence import TABLE, VERSION
    symbols = list(symbols)
    base = {'day': day, 'status': 'READ_FAILED', 'total_rows': None, 'day_rows': None,
            'verified_signals': None, 'price_only': None, 'verified_symbols': None,
            'missing_symbols': None, 'invalid_symbols': None}
    try:
        date.fromisoformat(day)
        kind = backend()
        def read(cur, postgres):
            if postgres:
                cur.execute('SET TRANSACTION READ ONLY')
                cur.execute('SET LOCAL statement_timeout = 5000')
                cur.execute("SELECT to_regclass('nova_core561_observations') IS NOT NULL")
                exists = bool(cur.fetchone()[0])
            else:
                cur.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='nova_core561_observations'")
                exists = cur.fetchone() is not None
            if not exists: return {**summarize_rows(day, symbols, [], VERSION, 0), 'table_exists': False}
            cur.execute(f'SELECT COUNT(*) FROM {TABLE}')
            total = int(cur.fetchone()[0])
            cur.execute(f'SELECT symbol,payload FROM {TABLE} WHERE trading_day=' + ('%s' if postgres else '?'), (day,))
            return {**summarize_rows(day, symbols, cur.fetchall(), VERSION, total), 'table_exists': True}
        if kind == 'postgresql':
            import psycopg
            with psycopg.connect(os.environ['DATABASE_URL'].strip(), connect_timeout=5) as db:
                with db.cursor() as cur: return read(cur, True)
        if kind == 'sqlite':
            import sqlite3
            path = Path(os.environ['NOVA_EVIDENCE_SQLITE_PATH']).expanduser().resolve()
            if not path.is_file(): return {**base, 'status': 'FILE_NOT_CREATED'}
            with closing(sqlite3.connect(path.as_uri()+'?mode=ro', uri=True, timeout=5)) as db:
                return read(db.cursor(), False)
        return {**base, 'status': 'NOT_CONFIGURED'}
    except Exception:
        return base
