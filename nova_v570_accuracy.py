"""Frozen research-only policy; no live signals or orders. Returns in percent points."""
from collections import defaultdict, Counter
from math import isfinite, ceil, sqrt
from statistics import mean, median, stdev

VERSION = '5.7.0'
STRATEGIES = ('core','strength','adaptive','hybrid','rs_required','transition_soft','rs_hybrid','risk_chase','range_guard','regime_switch','sideways_guard','v570')
ROUTES = ('core','adaptive','rs_required','transition_soft','rs_hybrid')
REGIMES = ('strong_up','mild_up','sideways','up_transition','down','unknown')
MARKETS = ('up','range','down','unknown')
MIN_TRADES, MIN_DATES, MIN_SYMBOLS = 30, 15, 5
LOOKBACK_DATES = 120
MAX_TAIL_LOSS = 8.0


def guarded(row, route):
    sig=row.get('signals') or {}; reg=row.get('subregime')
    if row.get('risk_veto') or reg not in REGIMES[:4] or row.get('market_regime') not in ('up','range') or sig.get(route)!='BUY':return False
    if reg=='sideways':return sig.get('range_guard')=='BUY' and sig.get('rs_required')=='BUY'
    if reg=='up_transition':return sig.get('rs_required')=='BUY' and sig.get('transition_soft')=='BUY'
    return True


def disjoint(rows):
    last={}; out=[]
    for r in sorted(rows,key=lambda x:(x['day'],x['symbol'])):
        if r['day']>=last.get(r['symbol'],''):
            out.append(r);last[r['symbol']]=r['exit_day']
    return out


def stress(net,cost):
    f=cost/10000; g=min(2*cost,200)/10000
    return 100*((1+net/100)*(1+f)/(1-f)*(1-g)/(1+g)-1)


def metrics(rows,cost):
    rows=disjoint(rows); vals=[r['net_pct'] for r in rows]; n=len(vals)
    base={'trades':n,'dates':len({r['day'] for r in rows}),'symbols':len({r['symbol'] for r in rows})}
    if not n:return {**base,**dict.fromkeys(('hit_pct','mean_net_pct','median_net_pct','stress_mean_pct','tail10_pct','worst_pct','profit_factor'),None)}
    gains=sum(v for v in vals if v>0);loss=-sum(v for v in vals if v<0)
    return {**base,'hit_pct':round(100*sum(v>0 for v in vals)/n,2),'mean_net_pct':round(mean(vals),4),'median_net_pct':round(median(vals),4),'stress_mean_pct':round(mean(stress(v,cost) for v in vals),4),'tail10_pct':round(mean(sorted(vals)[:ceil(.1*n)]),4),'worst_pct':round(min(vals),4),'profit_factor':round(gains/loss,4) if loss else None}


def fit(history,day,cost=15):
    # Strictly earlier exit days; trim by distinct signal dates, not row count.
    eligible=[r for r in history if r['day']<r['exit_day']<day and r.get('cost_bps')==cost and isinstance(r.get('net_pct'),(int,float)) and isfinite(r['net_pct']) and r['net_pct']>-100]
    days=sorted({r['day'] for r in eligible})[-LOOKBACK_DATES:]; allowed=set(days)
    eligible=[r for r in eligible if r['day'] in allowed]; out={}
    for reg in REGIMES[:4]:
        for route in ROUTES:
            pool=disjoint(r for r in eligible if r.get('subregime')==reg and guarded(r,route))
            groups=defaultdict(list)
            for r in pool:groups[r['day']].append(stress(r['net_pct'],cost))
            m=metrics(pool,cost); n=m['trades']; d=m['dates']
            if n<MIN_TRADES or d<MIN_DATES or m['symbols']<MIN_SYMBOLS:continue
            daily=[mean(v) for v in groups.values()]; lower=mean(daily)-1.645*stdev(daily)/sqrt(d)
            # Date-grouped dispersion heuristic, not an independent confidence interval.
            out[(reg,route)]={**m,'probability':(sum(r['net_pct']>0 for r in pool)+2)/(n+4),'naive_probability':(sum(r['net_pct']>0 for r in eligible)+2)/(len(eligible)+4),'lower_stress_pct':round(lower,6),'pass':lower>0 and m['stress_mean_pct']>0 and m['tail10_pct']>=-MAX_TAIL_LOSS}
    return out


def decide(row,model):
    base={'version':VERSION,'signal':'HOLD','route':None,'probability':None,'naive_probability':None,'expected_net_pct':None,'state':'FILTERED','research_only':True}
    keys=[k for k in ROUTES if guarded(row,k)]
    if not keys:return base
    scores={k:model[(row['subregime'],k)] for k in keys if (row['subregime'],k) in model}
    if not scores:return {**base,'state':'INSUFFICIENT_POOL'}
    passed=[k for k in scores if scores[k]['pass']];k=max(passed or list(scores),key=lambda k:scores[k]['lower_stress_pct']);s=scores[k]
    return {**base,'signal':'BUY' if passed else 'HOLD','state':'RESEARCH_CANDIDATE' if passed else 'RISK_OR_EV_HOLD','route':k,'probability':s['probability'],'naive_probability':s['naive_probability'],'expected_net_pct':s['mean_net_pct'],'lower_stress_pct':s['lower_stress_pct'],'tail10_pct':s['tail10_pct'],'pool_trades':s['trades'],'pool_dates':s['dates'],'pool_symbols':s['symbols']}


def choose(row,history,cost=15):return decide(row,fit(history,row['day'],cost))


def buy(r,k):
    if k=='v570':return (r.get('v570') or {}).get('signal')=='BUY'
    if k=='sideways_guard':return guarded(r,'core')
    return (r.get('signals') or {}).get(k)=='BUY'


def evaluate(rows,cost=15):
    rows=[r for r in rows if r.get('cost_bps')==cost and isinstance(r.get('net_pct'),(int,float)) and isfinite(r['net_pct']) and r['net_pct']>-100]
    shadow=[r for r in rows if (r.get('v570') or {}).get('version')==VERSION]
    def table(data,keys):return {k:metrics([r for r in data if buy(r,k)],cost) for k in keys}
    scored=[r for r in shadow if isinstance(r['v570'].get('probability'),(int,float)) and 0<=r['v570']['probability']<=1 and isinstance(r['v570'].get('naive_probability'),(int,float)) and 0<=r['v570']['naive_probability']<=1]
    brier=lambda k:round(mean((r['v570'][k]-(r['net_pct']>0))**2 for r in scored),6) if scored else None
    # Compare blocked/added on identical opportunity rows, separately from trade metrics.
    delta={}
    for k in ('sideways_guard','v570'):
        cohort=shadow if k=='v570' else [r for r in rows if r.get('market_regime') in MARKETS[:3]]
        blocked=[r['net_pct'] for r in cohort if buy(r,'core') and not buy(r,k)];added=[r['net_pct'] for r in cohort if not buy(r,'core') and buy(r,k)]
        delta[k]={'blocked_wins':sum(v>0 for v in blocked),'blocked_losses':sum(v<=0 for v in blocked),'added_wins':sum(v>0 for v in added),'added_losses':sum(v<=0 for v in added)}
    known=[r for r in rows if r.get('market_regime') in MARKETS[:3]]
    return {'version':VERSION,'research_only':True,'strategy_adoption':'HOLD','cost_bps':cost,'completed':len(rows),'shadow_completed':len(shadow),'market_known_completed':len(known),'strategies':table(rows,STRATEGIES[:-2]),'guard_comparison':table(known,('core','sideways_guard')),'shadow_comparison':table(shadow,('core','sideways_guard','v570')),'by_regime':{g:table([r for r in rows if (r.get('subregime') or 'unknown')==g],STRATEGIES[:-2]) for g in REGIMES},'by_market':{g:table([r for r in rows if (r.get('market_regime') or 'unknown')==g],STRATEGIES[:-2]) for g in MARKETS},'shadow_by_regime':{g:table([r for r in shadow if r.get('subregime')==g],('core','sideways_guard','v570')) for g in REGIMES},'changes':delta,'calibration':{'scored':len(scored),'brier':brier('probability'),'naive_brier':brier('naive_probability')},'validation_state':'COLLECTING' if not shadow or len({r['day'] for r in shadow})<40 else 'REVIEW_REQUIRED','limitations':'종목별 비중첩 5거래일 기회 수익률, 공동 자금 포트폴리오 아님. 거래비용 편도 기준/2배 스트레스. 날짜 묶음 하한은 경험적 위험 지표, 통계적 신뢰구간 아님. 자동 채택 금지.'}


def historical(frames,symbols,horizon,cost):
    history=[]; all_rows=[]
    for i in range(len(frames[0][2])):
        day=frames[0][0][i]; model=fit(history,day,cost); pending=[]
        for (dates,_,pts),symbol in zip(frames,symbols):
            p=pts[i];row={'day':dates[i],'exit_day':dates[i+horizon],'symbol':symbol,'subregime':p.get('subregime'),'market_regime':p.get('market_regime'),'risk_veto':p.get('route')=='risk_veto' or p.get('market_regime')=='down' or p.get('subregime')=='down','signals':{**p['signals'],**p['candidates']},'net_pct':100*float(p['net']),'cost_bps':cost}
            row['v570']=decide(row,model);pending.append(row)
        history.extend(pending);all_rows.extend(pending)
    return {**evaluate(all_rows,cost),'basis':'HISTORICAL_CAUSAL_REPLAY','independent_future_verified':False,'states':dict(Counter(r['v570']['state'] for r in all_rows))}
