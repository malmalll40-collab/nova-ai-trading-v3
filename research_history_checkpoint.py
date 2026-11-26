"""Isolated research-only history checkpoints. No automatic collection or live adoption.

Caller owns a dedicated transaction and must rollback on any exception.
No production connection is opened here. Historical rows are not prospective signals.
"""
from datetime import date, timedelta
from hashlib import sha256
import json
import math
from nova_v576_expanded_coverage import SYMBOLS
from nova_v574_data_audit import INDEX_CODES

TABLE = 'nova_research_history_v601'
DATASETS = 'nova_research_history_manifest_v601'
BASIS = 'KIS_NATIVE_ADJUSTED_HISTORY'
SCHEMA = (
    f'CREATE TABLE IF NOT EXISTS {DATASETS} (id TEXT PRIMARY KEY, manifest TEXT NOT NULL)',
    f'CREATE TABLE IF NOT EXISTS {TABLE} (dataset TEXT NOT NULL, symbol TEXT NOT NULL, day TEXT NOT NULL, payload TEXT NOT NULL, digest TEXT NOT NULL, PRIMARY KEY(dataset,symbol,day), FOREIGN KEY(dataset) REFERENCES {DATASETS}(id))',
)

def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)

def digest(raw):
    return sha256(raw.encode('utf-8')).hexdigest()

def manifest(sessions):
    days = tuple(sessions)
    if not days or len(days)>1200 or any(type(d) is not date for d in days) or tuple(sorted(set(days)))!=days:
        raise ValueError('INVALID_SESSIONS')
    if (days[-1]-days[0]).days>1200:
        raise ValueError('INVALID_WINDOW')
    obj={'schema':1,'basis':BASIS,'sessions':[d.isoformat() for d in days],
         'symbols':list(SYMBOLS),'research_only':True,'historical_calendar_fully_verified':False,
         'point_in_time_universe_verified':False,'strategy_adoption':'HOLD'}
    raw=canonical(obj)
    return digest(raw), raw

def execute(cur,kind,sql,params=()):
    if kind not in ('sqlite','postgresql'):
        raise ValueError('UNSUPPORTED_BACKEND')
    return cur.execute(sql.replace('?','%s') if kind=='postgresql' else sql,params)

def initialize(cur,kind,sessions):
    dataset,raw=manifest(sessions)
    for sql in SCHEMA:
        execute(cur,kind,sql)
    execute(cur,kind,f'INSERT INTO {DATASETS}(id,manifest) VALUES(?,?) ON CONFLICT(id) DO NOTHING',(dataset,raw))
    execute(cur,kind,f'SELECT manifest FROM {DATASETS} WHERE id=?',(dataset,))
    if cur.fetchone()!=(raw,):
        raise ValueError('MANIFEST_CONFLICT')
    return dataset

def _manifest(cur,kind,dataset,symbol):
    execute(cur,kind,f'SELECT manifest FROM {DATASETS} WHERE id=?',(dataset,))
    row=cur.fetchone()
    if row is None or digest(row[0])!=dataset:
        raise ValueError('MANIFEST_MISSING_OR_CORRUPT')
    obj=json.loads(row[0])
    if symbol not in SYMBOLS or symbol not in obj['symbols'] or obj['basis']!=BASIS:
        raise ValueError('INVALID_SYMBOL_OR_BASIS')
    days=tuple(date.fromisoformat(d) for d in obj['sessions'])
    if manifest(days)!=(dataset,row[0]):
        raise ValueError('MANIFEST_SCHEMA_MISMATCH')
    return days

def _payload(symbol,row):
    keys={'close'} if symbol in INDEX_CODES else {'open','high','low','close','volume','turnover'}
    if not isinstance(row,dict) or set(row)!=keys:
        raise ValueError('INVALID_FIELDS')
    for k,v in row.items():
        if k=='turnover' and v is None:
            continue
        if type(v) not in (int,float) or not math.isfinite(v) or (v<0 if k in ('volume','turnover') else v<=0):
            raise ValueError('INVALID_VALUE')
    if symbol not in INDEX_CODES and (row['high']<max(row['open'],row['close'],row['low']) or row['low']>min(row['open'],row['close'])):
        raise ValueError('INVALID_OHLC')
    return canonical({k:None if v is None else float(v) for k,v in row.items()})

def saved(cur,kind,dataset,symbol):
    days=set(_manifest(cur,kind,dataset,symbol))
    execute(cur,kind,f'SELECT day,payload,digest FROM {TABLE} WHERE dataset=? AND symbol=?',(dataset,symbol))
    records={}
    for stamp,raw,checksum in cur.fetchall():
        d=date.fromisoformat(stamp)
        if d not in days or digest(raw)!=checksum or _payload(symbol,json.loads(raw))!=raw:
            raise ValueError('ROW_CORRUPT')
        records[d]=json.loads(raw)
    return records

def append(cur,kind,dataset,symbol,records):
    """Validated immutable insert; no update of conflicting adjusted history."""
    days=set(_manifest(cur,kind,dataset,symbol))
    prior=saved(cur,kind,dataset,symbol)
    pending=[]
    for d,row in records.items():
        if type(d) is not date or d not in days:
            raise ValueError('UNEXPECTED_SESSION')
        raw=_payload(symbol,row)
        if d in prior:
            if _payload(symbol,prior[d])!=raw:
                raise ValueError('HISTORY_REVISION_CONFLICT')
        else:
            pending.append((dataset,symbol,d.isoformat(),raw,digest(raw)))
    inserted=0
    for args in pending:
        execute(cur,kind,f'INSERT INTO {TABLE}(dataset,symbol,day,payload,digest) VALUES(?,?,?,?,?) ON CONFLICT(dataset,symbol,day) DO NOTHING',args)
        inserted+=int(cur.rowcount==1)
        execute(cur,kind,f'SELECT payload,digest FROM {TABLE} WHERE dataset=? AND symbol=? AND day=?',args[:3])
        if cur.fetchone()!=args[3:]:
            raise ValueError('HISTORY_REVISION_CONFLICT')
    return inserted

def missing_windows(cur,kind,dataset,symbol):
    """Consecutive missing *sessions*, not calendar days; no external requests."""
    expected=_manifest(cur,kind,dataset,symbol)
    present=saved(cur,kind,dataset,symbol)
    windows=[]
    chunk=[]
    for d in expected:
        if d in present:
            if chunk:
                windows.append(tuple(chunk));chunk=[]
        else:
            chunk.append(d)
    if chunk:
        windows.append(tuple(chunk))
    return windows
