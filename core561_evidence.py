"""Versioned immutable prospective unified research. No historical backfill."""
from datetime import datetime,timezone
import json
from core552_evidence import snapshot as base_snapshot, _outcomes, enabled
from evidence_store import connection
from nova_v561_hierarchical import VERSION, choose, PRESET
from collections import defaultdict
TABLE='nova_core561_observations'
SCHEMA=f'CREATE TABLE IF NOT EXISTS {TABLE} (id TEXT PRIMARY KEY, symbol TEXT NOT NULL, trading_day TEXT NOT NULL, observed_at TEXT NOT NULL, payload TEXT NOT NULL)'

def read(cur,kind,symbol):
    sql=f'SELECT payload FROM {TABLE} WHERE symbol=? ORDER BY trading_day DESC LIMIT 1000'
    cur.execute(sql.replace('?','%s') if kind=='postgresql' else sql,(symbol,))
    return [json.loads(r[0]) for r in cur.fetchall()][::-1]

def snapshot(a,now):
    s=base_snapshot(a,now)
    if s is None:return None
    s.update(version=VERSION,id=f'{VERSION}|{s["symbol"]}|{s["day"]}',risk_veto=s.get('route')=='risk_veto' or s.get('subregime')=='down')
    return s

def record(a,now=None):
    if not enabled():return 'DISABLED'
    now=now or datetime.now(timezone.utc);s=snapshot(a,now)
    if s is None:return 'UNVERIFIED'
    with connection() as (db,kind):
        cur=db.cursor();cur.execute(SCHEMA);records=read(cur,kind,s['symbol'])
        existing=next((r for r in records if r['id']==s['id']),None)
        if existing:return 'EXISTS' if existing['signals'] else 'PRICE_ONLY'
        # All stored observations are genuinely prospective, not backfilled.
        sql=f'SELECT payload FROM {TABLE} WHERE trading_day<? ORDER BY trading_day DESC LIMIT 20000'
        cur.execute(sql.replace('?','%s') if kind=='postgresql' else sql,(s['day'],))
        groups=defaultdict(list)
        for (payload,) in cur.fetchall():
            r=json.loads(payload)
            if r['symbol'] in (*PRESET,s['symbol']) and r['observed_at']<s['observed_at'] and r['cost_bps']==s['cost_bps']:groups[r['symbol']].append(r)
        history=[]
        for prior in groups.values():
            prior.sort(key=lambda r:r['day']);by={r['day']:r for r in prior};results=_outcomes(prior,now)
            history.extend({**by[r['day']],'exit_day':r['exit_day'],'net_pct':r['net_return_pct']} for r in (results or []) if r['state']=='COMPLETE' and r['exit_day']<s['day'])
        s['unified']=choose(s,history);s['probability']=None
        sql=f'INSERT INTO {TABLE}(id,symbol,trading_day,observed_at,payload) VALUES(?,?,?,?,?) ON CONFLICT(id) DO NOTHING'
        cur.execute(sql.replace('?','%s') if kind=='postgresql' else sql,(s['id'],s['symbol'],s['day'],s['observed_at'],json.dumps(s,ensure_ascii=False,sort_keys=True)));ok=cur.rowcount>0;db.commit()
        return ('SAVED' if s['signals'] else 'PRICE_ONLY') if ok else 'EXISTS'

def summarize(records,now=None):
    now=now or datetime.now(timezone.utc);rows=_outcomes(records,now)
    base={'version':VERSION,'research_only':True,'strategy_adoption':'HOLD','verified_days':len(records),'independent_future_verified':False}
    if rows is None:return {**base,'status':'UNAVAILABLE','reason':'거래일 달력 미확인'}
    by={r['day']:r for r in records};done=[r for r in rows if r['state']=='COMPLETE'];scored=[r for r in done if by[r['day']]['unified']['probability'] is not None];disjoint=[];last=''
    for r in done:
        if r['day']>=last:disjoint.append(r);last=r['exit_day']
    def metric(data,unified=False):
        vals=[r['net_return_pct'] for r in data if (by[r['day']]['unified']['signal'] if unified else r['signals']['core'])=='BUY']
        return {'buys':len(vals),'positive':sum(v>0 for v in vals),'mean_net_pct':round(sum(vals)/len(vals),4) if vals else None}
    return {**base,'status':'READY','observations':len(rows),'completed':len(done),'pending':sum(r['state']=='PENDING' for r in rows),'missing':sum(r['state'] not in ('COMPLETE','PENDING') for r in rows),'scored':len(scored),'brier':round(sum((by[r['day']]['unified']['probability']-r['net_positive'])**2 for r in scored)/len(scored),6) if scored else None,'naive_brier':round(sum((by[r['day']]['unified']['naive_probability']-r['net_positive'])**2 for r in scored)/len(scored),6) if scored else None,'nonoverlap':len(disjoint),'core':metric(disjoint),'unified':metric(disjoint,True),'latest':records[-1]['unified'] if records else None,'limitations':'v5.6.1 별도 관찰, 초기 표본 부족 가능. 배포 이후 검증 완료봉만 기록, 편도 15bp 고정, 중첩 제거 종목별 기회 평가. 독립 성과 입증 전 자동 채택 없음.'}

def report(symbol):
    if not enabled():return {'status':'DISABLED','reason':'NOVA_CORE541_EVIDENCE_ENABLED=1 및 영구 DB 필요'}
    with connection() as (db,kind):
        cur=db.cursor();cur.execute(SCHEMA);records=read(cur,kind,symbol);db.commit()
    return summarize(records)
