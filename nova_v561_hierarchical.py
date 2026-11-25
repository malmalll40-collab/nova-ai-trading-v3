"""Frozen research hypotheses. All labels must mature strictly before the signal date."""
from collections import Counter, defaultdict
import math
from nova_v560_unified import ACTIVE, ARCHIVE, PRESET, change_value, sector_groups
VERSION='5.6.1'
MIN_POOL=20; MIN_SYMBOLS=3; SHRINK=12
# Fixed research buckets, not official KRX sectors or ETF constituents.
BUCKETS={**dict.fromkeys(('005930','000660','058470'),'반도체·전자부품'),**dict.fromkeys(('005380','000270','012330'),'자동차'),**dict.fromkeys(('105560','055550'),'은행'),**dict.fromkeys(('035420','035720'),'인터넷 플랫폼'),**dict.fromkeys(('068270','196170'),'바이오'),**dict.fromkeys(('247540','086520','006400'),'배터리 관련'),**dict.fromkeys(('293490','263750'),'게임'),**dict.fromkeys(('005490','051910'),'소재·화학'), '028260':'복합기업'}
def bucket(symbol):return BUCKETS.get(symbol.split('.')[0])

def disjoint(rows):
    last={};out=[]
    for p in sorted(rows,key=lambda r:(r['day'],r['symbol'])):
        s=p['symbol']
        if p['day']>=last.get(s,''):
            out.append(p);last[s]=p['exit_day']
    return out

def choose(cur,history):
    eligible=None
    def pool_for(k):
        nonlocal eligible
        if eligible is None:eligible=_eligible(history,cur['day'])
        return disjoint(p for p in eligible if p.get('signals',{}).get(k)=='BUY')
    return _choose_with_pool(cur,pool_for)


def _eligible(history,day):
    return [p for p in history if p['exit_day']<day and p['day']<day and p.get('net_pct') is not None and math.isfinite(p['net_pct']) and not p.get('risk_veto')]


def _choose_with_pool(cur,pool_for):
    sig=cur.get('signals',{}); regime=cur.get('subregime'); symbol=cur['symbol'];day=cur['day']
    out={'signal':'HOLD','state':None,'route':None,'expected_net_pct':None,'probability':None,'scores':{},'research_position_pct':0,'candidate_diagnostics':{}}
    if not sig: return {**out,'state':'DATA_MISSING'}
    if cur.get('risk_veto') or regime not in ('strong_up','mild_up','sideways','up_transition'):return {**out,'state':'RISK_OR_DATA_VETO'}
    keys=[k for k in ACTIVE if sig.get(k)=='BUY']
    if not keys:return {**out,'state':'NO_BUY_CANDIDATE'}
    for k in keys:
        pool=pool_for(k)
        n=len(pool); ns=len({p['symbol'] for p in pool});diag={'pool_samples':n,'pool_symbols':ns}
        out['candidate_diagnostics'][k]=diag
        if n<MIN_POOL or ns<MIN_SYMBOLS:diag['state']='INSUFFICIENT_POOL';continue
        # Disjoint hierarchy: partition into local, sector, regime, global remainder.
        # No label counted twice. Local and sector borrow the regime/global prior.
        local=[];sector=[];reg=[];other=[]; b=bucket(symbol)
        for p in pool:
            if p['symbol']==symbol:local.append(p)
            elif b is not None and bucket(p['symbol'])==b and p.get('subregime')==regime:sector.append(p)
            elif p.get('subregime')==regime:reg.append(p)
            else:other.append(p)
        ev=0.;prob=.5
        for group in (other,reg,sector,local):
            vals=[p['net_pct'] for p in group];m=len(vals)
            ev=(sum(vals)+SHRINK*ev)/(m+SHRINK)
            prob=(sum(v>0 for v in vals)+SHRINK*prob)/(m+SHRINK)
        naive=(sum(p['net_pct']>0 for p in pool)+1)/(n+2)
        diag.update(state='ESTIMATED',local_samples=len(local),sector_samples=len(sector),regime_samples=len(reg),global_remainder_samples=len(other))
        out['scores'][k]={'expected_net_pct':round(ev,6),'probability':round(prob,6),'naive_probability':round(naive,6),**diag}
    if not out['scores']:return {**out,'state':'INSUFFICIENT_POOL'}
    k=max(out['scores'],key=lambda k:out['scores'][k]['expected_net_pct']);s=out['scores'][k];buy=s['expected_net_pct']>0
    # Research sizing only; hard risk veto remains. Do not change execution/backtest cap.
    size=25 if buy and s['local_samples']>=8 and s['sector_samples']+s['regime_samples']>=8 else (10 if buy else 0)
    return {**out,**{a:b for a,b in s.items() if a!='state'},'signal':'BUY' if buy else 'HOLD','state':'RESEARCH_ESTIMATE' if buy else 'NONPOSITIVE_EV','route':k,'research_position_pct':size}

def replay_panel(frames,symbols,horizon=5,*,holdout_bucket=False):
    """Advance all symbols synchronously, never use same-day or later exits."""
    history=[]; result=[[] for _ in frames]
    for i in range(len(frames[0][2])):
        pending=[]; eligible_by_day={}; pools={}
        # Disjoint selection is independent per symbol. Sharing the matured pool
        # across decisions on the same date does not change order or membership.
        def pool_for(day,k):
            key=(day,k)
            if key not in pools:
                if day not in eligible_by_day:eligible_by_day[day]=_eligible(history,day)
                pools[key]=disjoint(p for p in eligible_by_day[day] if p.get('signals',{}).get(k)=='BUY')
            return pools[key]
        for j,((dates,_,pts),symbol) in enumerate(zip(frames,symbols)):
            p=pts[i];cur={'symbol':symbol,'day':dates[i],'subregime':p.get('subregime'),'signals':{**p['signals'],**p['candidates']},'risk_veto':p.get('route')=='risk_veto' or p.get('subregime')=='down' or p.get('market_regime')=='down'}
            def decision_pool(k):
                pool=pool_for(cur['day'],k)
                if not holdout_bucket:return pool
                b=bucket(symbol)
                return [r for r in pool if r['symbol']!=symbol and (b is None or bucket(r['symbol'])!=b)]
            result[j].append(_choose_with_pool(cur,decision_pool));pending.append({**cur,'exit_day':dates[i+horizon],'net_pct':float(p['net'])*100})
        history.extend(pending)
    return result

def nonoverlap_changes(frames,symbols,keys,horizon=5,kind='candidates'):
    # Select non-overlapping changed opportunities separately for each candidate, not all days.
    out={}
    for k in keys:
        chosen=[]
        for (dates,_,pts),symbol in zip(frames,symbols):
            changed=[{'day':dates[i],'exit_day':dates[i+horizon],'symbol':symbol,'point':p} for i,p in enumerate(pts) if (p['signals']['core']=='BUY')!=(p.get(kind,{}).get(k)=='BUY')]
            chosen.extend(r['point'] for r in disjoint(changed))
        out[k]=change_value(chosen,kind,k)
    return out
