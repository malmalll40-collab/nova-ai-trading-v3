"""Opt-in server-originated immutable signal snapshots.

Disabled by default. PostgreSQL DATABASE_URL is appropriate for Render; a
NOVA_EVIDENCE_SQLITE_PATH is for a persistent local or mounted volume ONLY.
Never silently use Render's ephemeral /tmp as a durable database.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3

from signal_journal import validate_record


SCHEMA = '''CREATE TABLE IF NOT EXISTS nova_evidence (
  id TEXT PRIMARY KEY,
  observed_at TEXT NOT NULL,
  snapshot_json TEXT NOT NULL,
  sha256 TEXT NOT NULL
)'''


def backend() -> str:
    url = os.environ.get('DATABASE_URL', '').strip()
    if url.startswith(('postgresql://', 'postgres://')):
        return 'postgresql'
    db = os.environ.get('NOVA_EVIDENCE_SQLITE_PATH', '').strip()
    if db and db != ':memory:':
        return 'sqlite'
    return 'disabled'


@contextmanager
def connection():
    kind = backend()
    if kind == 'postgresql':
        import psycopg
        url = os.environ['DATABASE_URL'].strip()
        with psycopg.connect(url, connect_timeout=5) as db:
            with db.cursor() as cur:
                cur.execute(SCHEMA)
            yield db, kind
    elif kind == 'sqlite':
        db_path = Path(os.environ['NOVA_EVIDENCE_SQLITE_PATH']).expanduser()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(str(db_path), timeout=8) as db:
            db.execute('PRAGMA busy_timeout = 8000')
            db.execute(SCHEMA)
            yield db, kind
    else:
        raise RuntimeError('NOVA evidence persistence not configured')


def make_snapshot(analysis: dict, observed_at: datetime | None = None) -> dict | None:
    """Only save live, observed non-demo swing signals, never simulated histories."""
    if (not isinstance(analysis, dict) or analysis.get('mode') != '스윙'
            or analysis.get('is_demo') or analysis.get('is_stale')
            or analysis.get('asof_evaluation_only') or not analysis.get('signal_is_current')):
        return None
    if analysis.get('raw_signal') not in ('BUY','SELL'):
        return None
    bar_time = str(analysis.get('last_completed_bar') or '')
    day = bar_time[:10]
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', day):
        return None
    observed = observed_at or datetime.now(timezone.utc)
    if observed.tzinfo is None:
        raise ValueError('observed timestamp requires timezone')
    rec = validate_record({
        'id':f"{analysis.get('source')}|{analysis.get('symbol')}|스윙|{day}",
        'symbol':analysis.get('symbol'), 'source':analysis.get('source'),
        'mode':'스윙', 'bar_day':day, 'reference':analysis.get('reference'),
        'technical':analysis.get('raw_signal'), 'filtered':analysis.get('signal'),
        'gate':(analysis.get('financial_gate') if analysis.get('financial_gate') in ('PASS','CAUTION','BLOCK','UNKNOWN') else 'UNKNOWN'),
        'cost_bps':analysis.get('cost_bps_one_side',15),
    })
    rec.update(observed_at=observed.astimezone(timezone.utc).isoformat(timespec='seconds'),
               completed_bar_time=bar_time, fetched_at=analysis.get('fetched_at'),
               quote_time=analysis.get('quote_time'), provider=analysis.get('provider'),
               price_age_minutes=analysis.get('price_age_minutes'),
               model_version='NOVA-4.5', provenance='server_analysis_request')
    return rec


def append_analysis(analysis: dict, observed_at: datetime | None = None) -> bool:
    if backend() == 'disabled':
        return False
    rec = make_snapshot(analysis, observed_at)
    if rec is None:
        return False
    raw = json.dumps(rec, ensure_ascii=False, sort_keys=True, separators=(',',':'))
    digest = hashlib.sha256(raw.encode('utf-8')).hexdigest()
    with connection() as (db, kind):
        if kind == 'postgresql':
            with db.cursor() as cur:
                cur.execute('INSERT INTO nova_evidence(id,observed_at,snapshot_json,sha256) VALUES(%s,%s,%s,%s) ON CONFLICT (id) DO NOTHING',
                            (rec['id'], rec['observed_at'],raw,digest))
                inserted = cur.rowcount > 0
        else:
            cur = db.execute('INSERT OR IGNORE INTO nova_evidence(id,observed_at,snapshot_json,sha256) VALUES(?,?,?,?)',
                             (rec['id'],rec['observed_at'],raw,digest))
            inserted = cur.rowcount > 0
        db.commit()
    return inserted


def list_snapshots(limit: int = 100) -> list[dict]:
    if not (1 <= limit <= 200):
        raise ValueError('조회 제한은 1~200건입니다.')
    with connection() as (db, kind):
        statement = ('SELECT snapshot_json, sha256 FROM nova_evidence '
                     'ORDER BY observed_at DESC, id DESC LIMIT ' + str(limit))
        if kind == 'postgresql':
            with db.cursor() as cur:
                cur.execute(statement)
                rows = cur.fetchall()
        else:
            rows = db.execute(statement).fetchall()
    result=[]
    for raw, sha in rows:
        if hashlib.sha256(raw.encode('utf-8')).hexdigest() != sha:
            raise ValueError('서버 신호 기록의 무결성 확인에 실패했습니다.')
        result.append(json.loads(raw))
    return result


def count_snapshots() -> int:
    with connection() as (db, kind):
        if kind=='postgresql':
            with db.cursor() as cur:
                cur.execute('SELECT COUNT(*) FROM nova_evidence')
                return int(cur.fetchone()[0])
        return int(db.execute('SELECT COUNT(*) FROM nova_evidence').fetchone()[0])
