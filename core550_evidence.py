"""Immutable version-specific observations; no retrospective backfill."""
from datetime import datetime,timezone
import json
from core541_evidence import snapshot as legacy_snapshot, enabled, KST
from evidence_store import connection
from nova_v541_core import STRATEGIES
from nova_v550_research import NAMES
CANDIDATES=('rs_required','entry_wider','risk_chase','transition_soft',*NAMES)
from nova_v550_research import VERSION, probability, group, score, change_metrics
TABLE='nova_core550_observations'
SCHEMA=f'CREATE TABLE IF NOT EXISTS {TABLE} (id TEXT PRIMARY KEY, symbol TEXT NOT NULL, trading_day TEXT NOT NULL, observed_at TEXT NOT NULL, payload TEXT NOT NULL)'
KEYS=tuple(STRATEGIES)+tuple(CANDIDATES)

def snapshot(a,now=None):
    s=legacy_snapshot(a,now,hybrid=True)
    if not s:return None
    p=a.get('core541_current') or {}; c=p.get('candidates') or {}
    if not s['signals'] or not set(CANDIDATES).issubset(c) or any(c[k] not in ('BUY','HOLD') for k in CANDIDATES):s['signals']={}
    else:s['signals'].update({k:c[k] for k in CANDIDATES})
    s.update(id=f'{VERSION}|{s["symbol"]}|{s["day"]}',version=VERSION,probability=None,subregime=p.get('subregime') if s['signals'] else None)
    return s

def _read(cur,kind,symbol,before=None):
    sql=f'SELECT payload FROM {TABLE} WHERE symbol=?';args=[symbol]
    if before:sql+=' AND trading_day<?';args.append(before)
    sql+=' ORDER BY trading_day DESC LIMIT 1000'
    cur.execute(sql.replace('?','%s') if kind=='postgresql' else sql,args)
    return [json.loads(r[0]) for r in cur.fetchall()][::-1]

def _outcomes(records,now):
    from core541_evidence import get_krx_calendar
    today=now.astimezone(KST).date();by={r['day']:r for r in records};result=[]
    if not records:return []
    try:
        cal=get_krx_calendar(today);sessions=[d.date().isoformat() for d in cal.sessions_in_range(records[0]['day'],today.isoformat())];pos={d:i for i,d in enumerate(sessions)}
    except Exception:return None
    for s in records:
        if not s['signals']:continue
        d=s['day'];item={'day':d,'state':'PENDING','net_return_pct':None,'net_positive':None,'signals':s['signals'],'regime':s['regime'],'subregime':s.get('subregime'),'probability':s.get('probability')}
        i=pos.get(d)
        if i is None:item['state']='UNAVAILABLE'
        elif i+5<len(sessions):
            a,b=sessions[i+1],sessions[i+5];item.update(entry_day=a,exit_day=b)
            if a in by and b in by:
                fee=s['cost_bps']/10000;net=by[b]['close']*(1-fee)/(by[a]['open']*(1+fee))-1
                item.update(state='COMPLETE',net_return_pct=round(100*net,4),net_positive=net>0)
            elif b<today.isoformat() or now.astimezone(KST).hour>=18:item['state']='MISSING_VERIFIED_BARS'
        result.append(item)
    return result

def forecast(s,records,now):
    prior=[r for r in records if r['day']<s['day'] and r['observed_at']<s['observed_at']]
    if not s['signals']:return None
    results=_outcomes(prior,now)
    if results is None:return None
    by={r['day']:r for r in prior}
    train=[(group(by[r['day']]),int(r['net_positive'])) for r in results if r['state']=='COMPLETE' and r['exit_day']<s['day'] and by[r['day']]['cost_bps']==s['cost_bps']]
    return probability(train,group(s))

def record(a,now=None):
    if not enabled():return False
    now=now or datetime.now(timezone.utc);s=snapshot(a,now)
    if not s:return False
    with connection() as (db,kind):
        cur=db.cursor();cur.execute(SCHEMA)
        s['probability']=forecast(s,_read(cur,kind,s['symbol'],s['day']),now)
        sql=f'INSERT INTO {TABLE}(id,symbol,trading_day,observed_at,payload) VALUES(?,?,?,?,?) ON CONFLICT(id) DO NOTHING'
        cur.execute(sql.replace('?','%s') if kind=='postgresql' else sql,(s['id'],s['symbol'],s['day'],s['observed_at'],json.dumps(s,ensure_ascii=False,sort_keys=True)));ok=cur.rowcount>0;db.commit();return ok

def summarize(records,now=None):
    now=now or datetime.now(timezone.utc);rows=_outcomes(records,now)
    base={'version':VERSION,'research_only':True,'strategy_adoption':'HOLD','verified_days':len(records)}
    if rows is None:return {**base,'status':'UNAVAILABLE','reason':'거래일 달력 미확인'}
    done=[r for r in rows if r['state']=='COMPLETE'];labels={r['day']:r['net_positive'] for r in done};baseline={r['day']:r['signals']['core'] for r in done}
    metrics={}
    for k in KEYS:
        selected=[r for r in done if r['signals'][k]=='BUY'];n=len(selected);good=sum(r['net_positive'] for r in selected)
        metrics[k]={'completed_buy_signals':n,'positive':good,'precision_pct':round(100*good/n,2) if n else None,'missed_positive':sum(labels.values())-good,**change_metrics(labels,baseline,{r['day']:r['signals'][k] for r in done})}
    brier=score((r['probability'],int(r['net_positive'])) for r in done if r['probability'] is not None)
    return {**base,'status':'READY','observations':len(rows),'completed':len(done),'pending':sum(r['state']=='PENDING' for r in rows),'missing':sum(r['state'] in ('MISSING_VERIFIED_BARS','UNAVAILABLE') for r in rows),'metrics':metrics,'probability':brier,'rows':rows[-30:],'limitations':'배포 후 당일 조회 기록만, 소급 생성 없음. 중첩 기회이며 거래 승률 아님. 가격 누락 제외. 종목별 비교, 자동 채택 없음.'}

def report(symbol):
    if not enabled():return {'status':'DISABLED','reason':'기존 NOVA_CORE541_EVIDENCE_ENABLED=1 및 영구 DB 연결 필요'}
    with connection() as (db,kind):
        cur=db.cursor();cur.execute(SCHEMA);records=_read(cur,kind,symbol);db.commit()
    return summarize(records)
