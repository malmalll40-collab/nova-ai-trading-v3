"""Read-only reporting of immutable observations; no retrospective prediction backfill."""
from collections import defaultdict, Counter
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
import json, math, os
from nova_v570_accuracy import evaluate, VERSION

KST=ZoneInfo('Asia/Seoul')
TABLE='nova_core561_observations'
LIMIT=20000


def summarize(records,now=None):
    from core552_evidence import _outcomes
    from nova_v561_hierarchical import VERSION as SOURCE_VERSION, PRESET
    now=now or datetime.now(timezone.utc);groups=defaultdict(list);rejected=0;out_of_scope=0
    for r in records:
        try:
            if r['symbol'] not in PRESET:out_of_scope+=1;continue
            at=datetime.fromisoformat(r['observed_at'])
            if at.tzinfo is None:raise ValueError
            at=at.astimezone(KST)
            if not (r['symbol'] in PRESET and r['version']==SOURCE_VERSION and r['id']==f'{SOURCE_VERSION}|{r["symbol"]}|{r["day"]}' and at.date().isoformat()==r['day'] and at.hour>=18 and at<=now and r['price_basis']=='KIS_REGULAR_MINUTE' and r['research_only'] is True and r['cost_bps']==15 and all(math.isfinite(r[k]) and r[k]>0 for k in ('open','close')) and isinstance(r['signals'],dict) and all(v in ('BUY','HOLD') for v in r['signals'].values()) and (not r['signals'] or 'core' in r['signals'])):raise ValueError
            groups[r['symbol']].append(r)
        except (KeyError,ValueError,TypeError,OverflowError,AttributeError):rejected+=1
    base={'version':VERSION,'basis':'IMMUTABLE_PROSPECTIVE','research_only':True,'strategy_adoption':'HOLD','orders_enabled':False,'rejected_records':rejected,'out_of_scope_records':out_of_scope,'stored_records':len(records),'verified_records':sum(len(v) for v in groups.values())}
    if rejected:return {**base,'status':'UNAVAILABLE','reason':'관찰 기록 무결성 확인 필요'}
    done=[];states=Counter();frozen=0;days=set();signals=0
    for group in groups.values():
        group.sort(key=lambda r:r['day']);by={r['day']:r for r in group}
        if len(by)!=len(group):return {**base,'status':'UNAVAILABLE','reason':'관찰 기록 중복'}
        days.update(by);signals+=sum(bool(r['signals']) for r in group);frozen+=sum(bool(r['signals']) and (r.get('v570') or {}).get('version')==VERSION for r in group)
        outcomes=_outcomes(group,now)
        if outcomes is None:return {**base,'status':'UNAVAILABLE','reason':'거래일 달력 미확인'}
        for o in outcomes:
            states[o['state']]+=1
            if o['state']=='COMPLETE':done.append({**by[o['day']],'exit_day':o['exit_day'],'net_pct':o['net_return_pct']})
    return {**base,**evaluate(done,15),'status':'READY','observed_days':len(days),'signal_records':signals,'shadow_frozen':frozen,'pending':states['PENDING'],'missing_outcomes':sum(v for k,v in states.items() if k not in ('PENDING','COMPLETE')),'independent_future_verified':False}


def report(now=None):
    from evidence_store import backend
    kind=backend()
    def read(cur,pg):
        if pg:
            cur.execute('SET TRANSACTION READ ONLY');cur.execute('SET LOCAL statement_timeout = 5000')
            cur.execute("SELECT to_regclass('nova_core561_observations') IS NOT NULL")
        else:cur.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='nova_core561_observations'")
        exists=cur.fetchone()
        if not exists or not exists[0]:return summarize([],now)
        cur.execute(f'SELECT payload FROM {TABLE} ORDER BY trading_day,symbol LIMIT {LIMIT+1}')
        rows=cur.fetchall()
        if len(rows)>LIMIT:return {'status':'UNAVAILABLE','reason':'관찰 조회 한도 초과 · 분할 검증 필요','strategy_adoption':'HOLD'}
        return summarize([json.loads(r[0]) for r in rows],now)
    if kind=='postgresql':
        import psycopg
        with psycopg.connect(os.environ['DATABASE_URL'].strip(),connect_timeout=5) as db:
            with db.cursor() as cur:return read(cur,True)
    if kind=='sqlite':
        import sqlite3
        path=Path(os.environ['NOVA_EVIDENCE_SQLITE_PATH']).expanduser().resolve()
        if not path.is_file():return summarize([],now)
        with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=5)) as db:return read(db.cursor(),False)
    return {'status':'DISABLED','reason':'영구 DB 미설정','strategy_adoption':'HOLD'}
