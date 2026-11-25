"""Opt-in immutable observations, not a substitute for live provider data."""
import os,hashlib,json,math
from evidence_store import connection,backend
from datetime import datetime,timezone

def archive_verified(symbol,bars,report):
    if os.environ.get('NOVA_KIS_ARCHIVE_ENABLED','').lower() not in ('1','true','yes') or backend()=='disabled':return False
    if not report or not report.get('verified') or not report.get('regular_volume_verified') or report.get('verification_basis')!='KIS_REGULAR_MINUTE' or bars.empty:return False
    # Legacy archives do not preserve cross-endpoint proof; keep it in research evidence only.
    if (report.get('minute_coverage') or {}).get('zero_proofs'):return False
    day=str(bars.index[-1].date())
    if day!=report.get('latest_date'):return False
    row={k:float(bars.iloc[-1][k]) for k in ('Open','High','Low','Close','Volume')}
    if not all(math.isfinite(v) for v in row.values()):return False
    raw=json.dumps({'symbol':symbol,'day':day,'basis':'KIS_J_REGULAR_MINUTE_RECONSTRUCTED' if report.get('session_reconstructed') else 'KIS_J_ADJ_0_REGULAR_MINUTE_CHECK','ohlcv':row},sort_keys=True)
    digest=hashlib.sha256(raw.encode()).hexdigest()
    with connection() as (db,kind):
        cur=db.cursor();cur.execute('CREATE TABLE IF NOT EXISTS nova_kis_daily_observations (id TEXT PRIMARY KEY, symbol TEXT NOT NULL, trading_day TEXT NOT NULL, observed_at TEXT NOT NULL, payload TEXT NOT NULL)')
        sql='INSERT INTO nova_kis_daily_observations(id,symbol,trading_day,observed_at,payload) VALUES(?,?,?,?,?) ON CONFLICT(id) DO NOTHING'
        if kind=='postgresql':sql=sql.replace('?','%s')
        cur.execute(sql,(digest,symbol,day,datetime.now(timezone.utc).isoformat(),raw));ok=cur.rowcount>0;db.commit();return ok

# v5.4.2: separately versioned, immutable reusable session references.
# Native fingerprint prevents reuse after observed price/volume revisions.
REGULAR_SCHEMA='CREATE TABLE IF NOT EXISTS nova_kis_regular_v542 (id TEXT PRIMARY KEY, lookup_key TEXT NOT NULL, payload TEXT NOT NULL)'

def regular_archive_enabled():
    flag=os.getenv('NOVA_KIS_ARCHIVE_ENABLED','').strip().lower()
    return backend()!='disabled' and (flag in ('1','true','yes') if flag else backend()=='postgresql')

def native_fingerprint(symbol,frame):
    try:
        if frame.empty or not frame.index.is_unique or not frame.index.is_monotonic_increasing:return None
        data=[]
        for t,r in frame.tail(5).iterrows():
            v=[float(r[k]) for k in ('Open','High','Low','Close','Volume')]
            if not all(math.isfinite(x) for x in v) or min(v[:4])<=0 or v[4]<0:return None
            data.append([str(t.date()),*v])
        return hashlib.sha256(json.dumps([symbol,'KIS_J_ADJ_0_REGULAR_V542',data],separators=(',',':')).encode()).hexdigest()
    except (AttributeError,KeyError,TypeError,ValueError):return None

def valid_regular(row):
    try:
        o,h,l,c,v=[float(row[k]) for k in ('Open','High','Low','Close','Volume')]
        return all(math.isfinite(x) and x>0 for x in (o,h,l,c,v)) and h>=max(o,l,c) and l<=min(o,h,c) and v.is_integer()
    except (TypeError,ValueError,KeyError):return False

def save_regular(symbol,bars,report):
    if not regular_archive_enabled():return 'DISABLED'
    if report and report.get('archive_reused'):return 'REUSED'
    if not report or not report.get('verified') or report.get('signal_hold') or not report.get('regular_volume_verified') or report.get('verification_basis')!='KIS_REGULAR_MINUTE' or report.get('archive_reused'):return 'NOT_SAVED'
    # Do not cache a cross-endpoint aggregate in a legacy proof-less archive.
    if (report.get('minute_coverage') or {}).get('zero_proofs'):return 'NOT_SAVED'
    key=report.get('native_fingerprint');day=report.get('latest_date')
    if not key or bars.empty or str(bars.index[-1].date())!=day:return 'NOT_SAVED'
    row={k:float(bars.iloc[-1][k]) for k in ('Open','High','Low','Close','Volume')}
    if not valid_regular(row):return 'NOT_SAVED'
    raw=json.dumps({'symbol':symbol,'day':day,'fingerprint':key,'basis':'KIS_REGULAR_MINUTE','ohlcv':row},sort_keys=True,separators=(',',':'))
    digest=hashlib.sha256(raw.encode()).hexdigest()
    with connection() as (db,kind):
        cur=db.cursor();cur.execute(REGULAR_SCHEMA);cur.execute('CREATE INDEX IF NOT EXISTS nova_kis_regular_v542_lookup ON nova_kis_regular_v542(lookup_key)')
        sql='INSERT INTO nova_kis_regular_v542(id,lookup_key,payload) VALUES(?,?,?) ON CONFLICT(id) DO NOTHING'
        cur.execute(sql.replace('?','%s') if kind=='postgresql' else sql,(digest,key,raw));db.commit()
    return 'SAVED'

def load_regular(symbol,frame,key):
    if not key or not regular_archive_enabled():return None
    with connection() as (db,kind):
        cur=db.cursor();cur.execute(REGULAR_SCHEMA)
        sql='SELECT id,payload FROM nova_kis_regular_v542 WHERE lookup_key=? LIMIT 2'
        cur.execute(sql.replace('?','%s') if kind=='postgresql' else sql,(key,));records=cur.fetchall();db.commit()
    # Multiple different observations for identical native input: fail closed.
    if len(records)!=1:return None
    digest,raw=records[0]
    if hashlib.sha256(raw.encode()).hexdigest()!=digest:return None
    p=json.loads(raw)
    if p.get('symbol')!=symbol or p.get('day')!=str(frame.index[-1].date()) or p.get('fingerprint')!=key or p.get('basis')!='KIS_REGULAR_MINUTE' or not valid_regular(p.get('ohlcv')):return None
    return p['ohlcv']
