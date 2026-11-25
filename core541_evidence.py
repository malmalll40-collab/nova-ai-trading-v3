"""Separate immutable prospective observations; opt-in, never writes legacy logs.
Only regular-minute-verified daily observations from actual same-day queries.
No hindsight backfill: absent entry/exit days remain unverified.
"""
import os,json,math,re
from datetime import datetime,timezone,date
from zoneinfo import ZoneInfo
from evidence_store import backend,connection
from nova_v541_core import NAMES,VERSION
from krx_calendar_verified import get_krx_calendar
KST=ZoneInfo('Asia/Seoul')
SCHEMA='CREATE TABLE IF NOT EXISTS nova_core541_observations (id TEXT PRIMARY KEY, symbol TEXT NOT NULL, trading_day TEXT NOT NULL, observed_at TEXT NOT NULL, payload TEXT NOT NULL)'
def enabled():return os.getenv('NOVA_CORE541_EVIDENCE_ENABLED','').lower() in ('1','true','yes') and backend()!='disabled'
def snapshot(a,now=None,*,hybrid=False):
    now=(now or datetime.now(timezone.utc)).astimezone(KST); day=now.date().isoformat(); g=a.get('daily_integrity') or {}; p=a.get('core541_current') or {}; session=a.get('completed_session') or {}; chart=a.get('chart') or {}
    if a.get('mode')!='스윙' or a.get('source')!='kis' or a.get('is_demo') or a.get('is_stale') or a.get('asof_evaluation_only') or now.hour<18:return None
    if not re.fullmatch(r'\d{6}\.(KS|KQ)',str(a.get('symbol',''))):return None
    if not (g.get('verified') and not g.get('signal_hold') and g.get('regular_volume_verified') and g.get('verification_basis')=='KIS_REGULAR_MINUTE' and g.get('latest_date')==day and session.get('calendar_verified') and session.get('expected_completed_session')==day and str(a.get('last_completed_bar',''))[:10]==day):return None
    try:
        assert str(chart['dates'][-1])[:10]==day
        o,c=float(chart['open'][-1]),float(chart['close'][-1]); cost=int(a.get('cost_bps_one_side',15))
        if not (math.isfinite(o) and math.isfinite(c) and min(o,c)>0 and 0<=cost<=100):return None
    except (KeyError,ValueError,TypeError,IndexError,AssertionError):return None
    sig=p.get('signals',{}) if p.get('status')=='READY' and p.get('day')==day else {}
    if set(sig)!=set(NAMES) or any(v not in ('BUY','HOLD') for v in sig.values()):sig={}
    if hybrid and sig and p.get('hybrid_signal') in ('BUY','HOLD'):sig={**sig,'hybrid':p['hybrid_signal']}
    elif hybrid:sig={}
    version='5.4.2' if hybrid else VERSION
    return {'id':f'{version}|{a["symbol"]}|{day}','symbol':a['symbol'],'day':day,'observed_at':now.astimezone(timezone.utc).isoformat(),'open':o,'close':c,'signals':sig,'regime':p.get('regime') if sig else None,'cost_bps':cost,'version':version,'price_basis':'KIS_REGULAR_MINUTE','research_only':True}
def record(a,now=None,*,hybrid=False):
    if not enabled():return False
    s=snapshot(a,now,hybrid=hybrid)
    if not s:return False
    with connection() as (db,kind):
        cur=db.cursor();cur.execute(SCHEMA.replace('nova_core541_observations','nova_core542_observations') if hybrid else SCHEMA)
        sql='INSERT INTO nova_core541_observations(id,symbol,trading_day,observed_at,payload) VALUES(?,?,?,?,?) ON CONFLICT(id) DO NOTHING'
        if hybrid:sql=sql.replace('nova_core541_observations','nova_core542_observations')
        if kind=='postgresql':sql=sql.replace('?','%s')
        cur.execute(sql,(s['id'],s['symbol'],s['day'],s['observed_at'],json.dumps(s,sort_keys=True,ensure_ascii=False)));ok=cur.rowcount>0;db.commit();return ok

def outcomes(records,now=None,*,hybrid=False):
    today=(now or datetime.now(timezone.utc)).astimezone(KST).date(); by={r['day']:r for r in records}; results=[]
    try:cal=get_krx_calendar(today)
    except Exception:return {'status':'UNAVAILABLE','reason':'거래일 달력 미확인'}
    for s in records:
        if not s.get('signals'):continue
        item={'day':s['day'],'signals':s['signals'],'regime':s.get('regime'),'state':'PENDING','net_return_pct':None}
        try:
            sessions=[d.date().isoformat() for d in cal.sessions_in_range(s['day'],today.isoformat()) if d.date().isoformat()>s['day']]
            if len(sessions)>=5:
                a,b=sessions[0],sessions[4];item.update(entry_day=a,exit_day=b)
                if a in by and b in by:
                    # Records must have been observed on their own dates; no backfill.
                    fee=s['cost_bps']/10000;net=by[b]['close']*(1-fee)/(by[a]['open']*(1+fee))-1
                    item.update(state='COMPLETE',net_return_pct=round(100*net,4))
                else:item['state']='MISSING_VERIFIED_BARS'
        except Exception:item['state']='UNAVAILABLE'
        results.append(item)
    metrics={}
    for k in (*NAMES,'hybrid') if hybrid else NAMES:
        completed=[r for r in results if r['state']=='COMPLETE'];selected=[r for r in completed if r['signals'].get(k)=='BUY'];positive=sum(r['net_return_pct']>0 for r in selected)
        metrics[k]={'completed_buy_signals':len(selected),'positive':positive,'precision_pct':round(100*positive/len(selected),2) if selected else None,'missed_positive':sum(r['net_return_pct']>0 and r['signals'].get(k)!='BUY' for r in completed)}
    return {'status':'READY','observations':len(results),'verified_days':len(records),'completed':sum(r['state']=='COMPLETE' for r in results),'pending':sum(r['state']=='PENDING' for r in results),'missing':sum(r['state'] in ('MISSING_VERIFIED_BARS','UNAVAILABLE') for r in results),'metrics':metrics,'rows':results[-30:],'strategy_adoption':'HOLD'}
def report(symbol,*,hybrid=False):
    if not enabled():return {'status':'DISABLED','reason':'Neon 연결 및 NOVA_CORE541_EVIDENCE_ENABLED=1 설정 필요'}
    with connection() as (db,kind):
        cur=db.cursor();cur.execute(SCHEMA.replace('nova_core541_observations','nova_core542_observations') if hybrid else SCHEMA)
        sql='SELECT payload FROM nova_core541_observations WHERE symbol=? ORDER BY trading_day DESC LIMIT 500'
        if hybrid:sql=sql.replace('nova_core541_observations','nova_core542_observations')
        cur.execute(sql.replace('?','%s') if kind=='postgresql' else sql,(symbol,));records=[json.loads(r[0]) for r in cur.fetchall()][::-1]
    return outcomes(records,hybrid=hybrid)
