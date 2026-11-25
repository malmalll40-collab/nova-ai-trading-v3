"""Research-only fixed hypotheses. No live signal or order integration."""
import math
from collections import defaultdict
from statistics import mean

VERSION='5.6.0'
ACTIVE=('core','adaptive','rs_required','transition_soft','rs_hybrid','risk_chase')
# Duplicate/near duplicate candidates are archived, not destroyed or assumed logically equal.
ARCHIVE=('early_turn','regime_switch','range_guard','entry_wider','regime_router','hybrid','strength')
PRESET=('005930.KS','000660.KS','005380.KS','000270.KS','035420.KS','035720.KS','068270.KS','005490.KS','051910.KS','006400.KS','105560.KS','055550.KS','012330.KS','028260.KS','247540.KQ','086520.KQ','293490.KQ','196170.KQ','263750.KQ','058470.KQ')
MIN_TOTAL=20; MIN_GROUP=8; PRIOR=12

def choose(current,history):
    """Use only strictly earlier finalized 5-day outcomes, passed by caller.
    History contains net return in percentage points and signal-date/exit-date.
    Select at most one eligible family; no vote multiplication. Fixed shrinkage
    to zero, nonoverlap per family, and minimum history; no tuning on test returns.
    """
    day=current['day']; regime=current.get('subregime'); sig=current.get('signals',{})
    if not sig or current.get('risk_veto') or regime not in ('strong_up','mild_up','sideways','up_transition'):
        return {'signal':'HOLD','state':'RISK_OR_DATA_VETO','route':None,'expected_net_pct':None,'probability':None,'scores':{}}
    history=sorted((p for p in history if p['exit_day']<day and p['day']<day and p.get('net_pct') is not None and math.isfinite(p['net_pct'])),key=lambda p:p['day'])
    def disjoint(rows):
        out=[];last=''
        for p in rows:
            if p['day']>=last:out.append(p);last=p['exit_day']
        return out
    scores={}
    for k in ACTIVE:
        if sig.get(k)!='BUY':continue
        all_rows=disjoint(p for p in history if p.get('signals',{}).get(k)=='BUY' and not p.get('risk_veto'))
        same=disjoint(p for p in history if p.get('signals',{}).get(k)=='BUY' and p.get('subregime')==regime and not p.get('risk_veto'))
        if len(all_rows)<MIN_TOTAL or len(same)<MIN_GROUP:continue
        vals=[p['net_pct'] for p in same]; prior=sum(p['net_pct'] for p in all_rows)/(len(all_rows)+PRIOR)
        ev=(sum(vals)+PRIOR*prior)/(len(vals)+PRIOR)
        prob=(sum(v>0 for v in vals)+PRIOR*((sum(p['net_pct']>0 for p in all_rows)+1)/(len(all_rows)+2)))/(len(vals)+PRIOR)
        scores[k]={'expected_net_pct':round(ev,6),'probability':round(prob,6),'naive_probability':round((sum(p['net_pct']>0 for p in all_rows)+1)/(len(all_rows)+2),6),'group_samples':len(same),'all_samples':len(all_rows)}
    if not scores:return {'signal':'HOLD','state':'INSUFFICIENT','route':None,'expected_net_pct':None,'probability':None,'scores':{}}
    k=max(scores,key=lambda k:scores[k]['expected_net_pct']); s=scores[k]
    return {'signal':'BUY' if s['expected_net_pct']>0 else 'HOLD','state':'RESEARCH_ESTIMATE','route':k,**s,'scores':scores}

def change_value(points,kind,key):
    out={'added_gain_pct_sum':0.,'added_loss_pct_sum':0.,'blocked_gain_pct_sum':0.,'blocked_loss_pct_sum':0.,'added_count':0,'blocked_count':0}
    for p in points:
        a=p['signals']['core']=='BUY';b=p.get(kind,{}).get(key)=='BUY'
        if a==b:continue
        v=float(p['net'])*100; prefix='added' if b else 'blocked';out[prefix+'_count']+=1
        out[prefix+('_gain_pct_sum' if v>0 else '_loss_pct_sum')]+=v
    out['delta_pct_sum']=out['added_gain_pct_sum']+out['added_loss_pct_sum']-out['blocked_gain_pct_sum']-out['blocked_loss_pct_sum']
    # Fixed notional per signal; overlapping opportunities, not traded cash.
    out['notional_krw_per_signal']=1000000
    out.update({k.replace('_pct_sum','_krw'):round(v*10000) for k,v in list(out.items()) if k.endswith('_pct_sum')})
    return {k:round(v,4) if isinstance(v,float) else v for k,v in out.items()}

def replay(points,dates,horizon=5):
    """Strictly causal on ordered, aligned date rows; identical execution costs."""
    history=[];decisions=[]
    for i,p in enumerate(points):
        cur={'day':dates[i],'subregime':p.get('subregime'),'signals':{**p['signals'],**p['candidates']},'risk_veto':p.get('route')=='risk_veto' or p.get('subregime')=='down' or p.get('market_regime')=='down'}
        # choose filters by exit_day < day; current label is appended only after decision
        decisions.append(choose(cur,history))
        history.append({**cur,'exit_day':dates[i+horizon],'net_pct':float(p['net'])*100})
    return decisions

def sector_groups(symbols):
    from shadow_context import ETF_PROXIES
    out=defaultdict(list)
    for s in symbols:
        proxy=ETF_PROXIES.get(s.split('.')[0]);out[proxy[1] if proxy else '미분류'].append(s)
    return dict(out)
