"""Frozen v6 liquidity shadow experiment. No broker, live orders, or network.
Append full official daily snapshots only. Local timestamp is evidence of intake,
not proof of provider publication or a tamper-proof third-party timestamp.
"""
import argparse, hashlib, io, json, math, sqlite3, zipfile
from contextlib import closing
from datetime import datetime,date,time,timezone
from zoneinfo import ZoneInfo
from pathlib import Path
import numpy as np
import pandas as pd
import exchange_calendars as xc

KST=ZoneInfo('Asia/Seoul'); FREEZE='2026-11-28T06:25:00+09:00'; LAST='20261125'
SOURCE='b3b081c4a934535fa76eef96f88d1960122d74227af01d4e90bdc19835910753'
FEE=.003; CASH=10_000_000.; SLOTS=10; K=2; HOLD=5
COLS=['market','BAS_DD','ISU_CD','TDD_CLSPRC','CMPPREVDD_PRC','TDD_OPNPRC','TDD_HGPRC','TDD_LWPRC','ACC_TRDVOL','ACC_TRDVAL','MKTCAP','LIST_SHRS']
NAMES=['market','day','code','close','delta','open','high','low','volume','turnover','cap','shares']
CAL=xc.get_calendar('XKRX')
SCHEMA='''CREATE TABLE bars(day TEXT, code TEXT, market TEXT, close INTEGER, delta INTEGER, open INTEGER, high INTEGER, low INTEGER, volume INTEGER, turnover INTEGER, cap INTEGER, shares INTEGER, PRIMARY KEY(day,code));
CREATE TABLE sessions(day TEXT PRIMARY KEY, observed_at TEXT NOT NULL, digest TEXT NOT NULL, mode TEXT NOT NULL, source_note TEXT NOT NULL, market_counts TEXT NOT NULL, index_close TEXT NOT NULL, candidates TEXT NOT NULL, equity REAL, cash REAL, positions TEXT NOT NULL, events TEXT NOT NULL);
CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);'''

def sha(b):return hashlib.sha256(b).hexdigest()
def dayfmt(x):return datetime.strptime(x,'%Y%m%d').date()
def next_day(d):return CAL.next_session(pd.Timestamp(dayfmt(d))).strftime('%Y%m%d')
def now():return datetime.now(KST)
def read_snapshot(f):
    q=pd.read_csv(f,usecols=COLS,dtype={'BAS_DD':str,'ISU_CD':str},keep_default_na=False)[COLS];q.columns=NAMES
    if q.empty or q.duplicated(['day','code']).any() or not q.code.str.fullmatch('[0-9A-Z]{6}').all() or not q.day.str.fullmatch('[0-9]{8}').all():raise ValueError('INVALID_KEY')
    if not q.market.isin(['KOSPI','KOSDAQ']).all():raise ValueError('INVALID_MARKET')
    for c in NAMES[3:]:
        x=pd.to_numeric(q[c],errors='raise')
        if not np.isfinite(x).all() or not (x==np.floor(x)).all() or (c!='delta' and x.lt(0).any()):raise ValueError('INVALID_NUMBER '+c)
        q[c]=x.astype('int64')
    return q

def active(q):return (q[['open','high','low','close','volume']]>0).all(axis=1)&q.low.le(q[['open','close','high']].min(axis=1))&q.high.ge(q[['open','close','low']].max(axis=1))
def rank(history,days):
    # v6: 65 active continuous same-market bars; share/reference changes reset warmup.
    if len(days)<65:return []
    q=history[history.day.isin(days[-65:])].sort_values(['code','day']).copy()
    if q.duplicated(['day','code']).any():raise ValueError('DUPLICATE')
    g=q.groupby('code',sort=False);prior=g[['close','shares','market']].shift()
    boundary=prior.close.notna()&((q.close-q.delta).ne(prior.close)|q.shares.ne(prior.shares)|q.market.ne(prior.market))
    q['ok']=active(q)&~boundary
    good=q.groupby('code').agg(n=('day','size'),ok=('ok','all'));good=good.index[(good.n==65)&good.ok]
    v=q[q.code.isin(good)&q.day.isin(days[-20:])].groupby('code').turnover.median()
    t=q[q.day.eq(days[-1])].set_index('code');v=v[(v>=1e9)&t.close.reindex(v.index).ge(1000)]
    z=pd.DataFrame({'code':v.index,'median20':v.values,'score':np.log1p(v.values).astype('float32')}).sort_values(['score','code'],ascending=[False,True]).head(K)
    return [{'code':str(r.code),'median20':float(r.median20),'score':float(r.score)} for r in z.itertuples()]

def insert_bars(c,q):c.executemany('INSERT INTO bars VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',q[['day','code','market',*NAMES[3:]]].itertuples(index=False,name=None))
def init(db,source):
    with open(source,'rb') as f:digest=hashlib.file_digest(f,'sha256').hexdigest()
    if digest!=SOURCE:raise ValueError('SOURCE_HASH_MISMATCH')
    if Path(db).exists():raise ValueError('DB_EXISTS')
    with zipfile.ZipFile(source) as z:
        q=pd.concat([read_snapshot(z.open(m+'_2026.csv')) for m in ['KOSPI','KOSDAQ']],ignore_index=True)
    days=sorted(q.day.unique())[-65:];q=q[q.day.isin(days)]
    if len(days)!=65 or days[-1]!=LAST or q.duplicated(['day','code']).any():raise ValueError('SEED')
    # Seed is retrospective and never contributes a prospective signal or P&L.
    c=sqlite3.connect(db)
    try:
        c.executescript(SCHEMA);c.execute('BEGIN IMMEDIATE');insert_bars(c,q)
        for d,g in q.groupby('day'):
            counts=g.groupby('market').size().to_dict()
            if set(counts)!={'KOSPI','KOSDAQ'}:raise ValueError('SEED_MARKETS')
            c.execute('INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',(d,FREEZE,digest,'SEED','historical ZIP',json.dumps(counts),'{}','[]',CASH,CASH,'{}','[]'))
        c.executemany('INSERT INTO metadata VALUES (?,?)',[('source_sha256',digest),('freeze',FREEZE),('strategy','v6_liquidity_shadow_v1'),('orders_enabled','false')]);c.commit()
        return {'seed_sessions':len(days),'seed_rows':len(q),'through':days[-1],'retrospective_dry_run_candidates':rank(q,days),'prospective_signals':0}
    except BaseException:
        c.rollback();c.close();Path(db).unlink(missing_ok=True);raise
    finally:c.close()

def mode_for(day,observed):
    d=dayfmt(day);local=observed.astimezone(KST)
    if local<datetime.fromisoformat(FREEZE) or local.date()<d:raise ValueError('FUTURE_OR_BEFORE_FREEZE')
    if local.date()==d and local.time()<time(18):raise ValueError('BEFORE_EOD')
    return 'PROSPECTIVE' if d>datetime.fromisoformat(FREEZE).date() and local.date()==d else 'LATE_OR_BRIDGE'

def step(cash,positions,prev_equity,queued,q,prior,seq):
    # Prior candidates were committed before today's intake; never backfill replacements.
    events=[];rows=q.set_index('code');prev=prior.set_index('code');valid=active(q).set_axis(q.code).to_dict()
    for x in queued:
        code=x['code']
        if code in positions or len(positions)>=SLOTS or not valid.get(code,False):events.append({'code':code,'event':'SKIP'});continue
        r=rows.loc[code];o=int(r.open)
        # Known-at-next-open eligibility cannot be reconstructed from EOD; assume fill only on traded day.
        if code not in prev.index or int(r.close-r.delta)!=int(prev.loc[code].close) or r.market!=prev.loc[code].market:events.append({'code':code,'event':'SKIP_REFERENCE_BREAK'});continue
        n=math.floor(min(cash,prev_equity/SLOTS)/(o*(1+FEE)))
        if n<=0:events.append({'code':code,'event':'SKIP_SIZE'});continue
        cost=n*o*(1+FEE);cash-=cost;positions[code]={'n':n,'entry_seq':seq,'due':seq+HOLD-1,'cost':cost,'mark':n*o,'locked':False};events.append({'code':code,'event':'ENTRY_ASSUMED','price':o,'shares':n})
    for code,p in list(positions.items()):
        if p['locked']:continue
        if code not in rows.index or code not in prev.index:
            p['locked']=True;events.append({'code':code,'event':'UNRESOLVED_MISSING'});continue
        r=rows.loc[code];pr=prev.loc[code]
        if int(r.close-r.delta)!=int(pr.close) or r.market!=pr.market or (r.volume>0 and not valid.get(code,False)):
            p['locked']=True;events.append({'code':code,'event':'UNRESOLVED_PRICE'});continue
        if not valid.get(code,False):events.append({'code':code,'event':'HALT_OR_NO_FILL'});continue
        if r.shares!=pr.shares:events.append({'code':code,'event':'SHARES_CHANGE_UNVERIFIED'})
        p['mark']=p['n']*int(r.close)
        if seq>=p['due']:
            proceeds=p['mark']*(1-FEE);cash+=proceeds;events.append({'code':code,'event':'EXIT_ASSUMED','net_pct':100*(proceeds/p['cost']-1)});del positions[code]
    if cash<-.001 or len(positions)>SLOTS:raise ValueError('ACCOUNT_INVARIANT')
    return cash,positions,events,cash+sum(p['mark'] for p in positions.values())

def ingest(db,file,manifest,observed=None):
    observed=observed or now();b=Path(file).read_bytes();m=json.loads(Path(manifest).read_text());q=read_snapshot(io.BytesIO(b));day=str(m['day']);digest=sha(b)
    if set(q.day)!={day} or digest!=m['sha256'] or q.groupby('market').size().to_dict()!=m['market_counts'] or set(m['market_counts'])!={'KOSPI','KOSDAQ'}:raise ValueError('MANIFEST_MISMATCH')
    if m.get('source')!='KRX_OFFICIAL' or m.get('complete') is not True or not str(m.get('source_note','')).strip():raise ValueError('SOURCE_ATTESTATION_REQUIRED')
    indices=m.get('index_close',{});
    if set(indices)!={'KOSPI','KOSDAQ'} or any(type(x) not in (float,int) or not math.isfinite(x) or x<=0 for x in indices.values()):raise ValueError('INDEX_REQUIRED')
    # Bind the whole manifest as well as CSV bytes to immutable ledger identity.
    digest=sha((digest+'\n'+json.dumps(m,sort_keys=True,separators=(',',':'))).encode())
    mode=mode_for(day,observed)
    with closing(sqlite3.connect(db)) as c, c:
        c.execute('BEGIN IMMEDIATE');old=c.execute('SELECT digest,mode FROM sessions WHERE day=?',(day,)).fetchone()
        if old:
            if old[0]!=digest:raise ValueError('IMMUTABLE_SESSION')
            return {'state':'ALREADY_RECORDED','day':day,'mode':old[1]}
        p=c.execute('SELECT day,mode,candidates,equity,cash,positions FROM sessions ORDER BY day DESC LIMIT 1').fetchone()
        if day!=next_day(p[0]):raise ValueError('SESSION_GAP_OR_CALENDAR_MISMATCH')
        seq=c.execute('SELECT count(*) FROM sessions').fetchone()[0]
        prior=pd.read_sql_query('SELECT * FROM bars WHERE day=?',c,params=(p[0],))
        cash,positions,events,equity=step(p[4],json.loads(p[5]),p[3],json.loads(p[2]) if p[1]=='PROSPECTIVE' else [],q,prior,seq)
        insert_bars(c,q)
        days=sorted([r[0] for r in c.execute('SELECT day FROM sessions ORDER BY day DESC LIMIT 64')]+[day])
        hist=pd.read_sql_query('SELECT * FROM bars WHERE day>=?',c,params=(days[0],));candidates=rank(hist,days) if mode=='PROSPECTIVE' else []
        c.execute('INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',(day,observed.isoformat(),digest,mode,m['source_note'],json.dumps(m['market_counts']),json.dumps(indices),json.dumps(candidates),equity,cash,json.dumps(positions),json.dumps(events)))
        return {'day':day,'mode':mode,'candidates':candidates,'equity':equity,'events':events,'orders_enabled':False}

def report(db):
    with closing(sqlite3.connect(db)) as c, c:
        a=pd.read_sql_query("SELECT * FROM sessions WHERE mode!='SEED' ORDER BY day",c);last=c.execute('SELECT day,equity,cash,positions FROM sessions ORDER BY day DESC LIMIT 1').fetchone()
    ev=[e for s in a.events for e in json.loads(s)];p=json.loads(last[3]);eq=np.array([CASH,*a.equity.tolist()]);mdd=float((eq/np.maximum.accumulate(eq)-1).min()*100)
    return {'last_day':last[0],'prospective_sessions':int(a['mode'].eq('PROSPECTIVE').sum()),'late_bridge_sessions':int(a['mode'].eq('LATE_OR_BRIDGE').sum()),'candidate_count':sum(len(json.loads(s)) for s in a.candidates),'closed_assumed':sum(e['event']=='EXIT_ASSUMED' for e in ev),'open_positions':len(p),'locked_positions':sum(x['locked'] for x in p.values()),'equity_frozen_mark':last[1],'return_pct':100*(last[1]/CASH-1),'mdd_pct':mdd,'equity_zero_locked_sensitivity':last[1]-sum(x['mark'] for x in p.values() if x['locked']),'adoption':'HOLD','orders_enabled':False}

if __name__=='__main__':
    p=argparse.ArgumentParser();sub=p.add_subparsers(dest='cmd',required=True)
    for name,args in [('init',['db','source']),('ingest',['db','file','manifest']),('report',['db'])]:
        x=sub.add_parser(name)
        for a in args:x.add_argument(a)
    a=vars(p.parse_args());cmd=a.pop('cmd');print(json.dumps(globals()[cmd](**a),ensure_ascii=False,indent=2))
