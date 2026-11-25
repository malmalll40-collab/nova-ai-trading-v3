"""Research-only hierarchical probability and exposure challenger, no order or live policy."""
from collections import Counter, defaultdict
from statistics import mean
from math import isfinite, ceil
from nova_v570_accuracy import disjoint, metrics, stress, REGIMES, MARKETS
from nova_v571_policy import valid, eligible

VERSION = '5.7.2'
LOOKBACK = 120
PRIOR_WEIGHT = 20
MIN_TRADES, MIN_DATES, MIN_SYMBOLS = 15, 10, 5
FEATURES = ('rs_required', 'rs_hybrid', 'transition_soft', 'adaptive')
PARENT = {'strong_up':'trend','mild_up':'trend','sideways':'transition','up_transition':'transition'}


def feature(r):
    signals = r.get('signals') or {}
    return tuple(signals.get(k) == 'BUY' for k in FEATURES)


def stat(rows, cost):
    sample = disjoint(rows)
    m = metrics(sample, cost)
    return {'n':len(sample),'wins':sum(r['net_pct']>0 for r in sample),
            'dates':m['dates'],'symbols':m['symbols'],'stress':m['stress_mean_pct'],
            'tail':m['tail10_pct']}


def smooth(s, prior):
    return (s['wins'] + PRIOR_WEIGHT * prior) / (s['n'] + PRIOR_WEIGHT)


def fit(history, day, cost):
    past = [r for r in history if valid(r,cost) and r['exit_day'] < day and eligible(r)]
    allowed = set(sorted({r['day'] for r in past})[-LOOKBACK:])
    past = [r for r in past if r['day'] in allowed]
    groups = defaultdict(list)
    for r in past:
        reg = r['subregime']
        groups[('parent',PARENT[reg])].append(r)
        groups[('regime',reg)].append(r)
        groups[('feature',reg,feature(r))].append(r)
    base = stat(past,cost)
    prior = (base['wins']+2)/(base['n']+4)
    return {'base':base,'prior':prior,'groups':{k:stat(v,cost) for k,v in groups.items()}}


def decide(row, model):
    core = (row.get('signals') or {}).get('core') == 'BUY'
    # Preserve the original entry policy; only simulate exposure and probability.
    out = {'version':VERSION,'signal':'BUY' if core else 'HOLD',
           'exposure':1.0 if core else 0.0,'probability':None,'naive_probability':None,
           'state':'CORE_UNCHANGED' if core else 'NOT_CORE','research_only':True}
    if not eligible(row): return out
    reg = row['subregime']; groups = model['groups']; prior = model['prior']
    p = prior
    chosen = model['base']
    for key in (('parent',PARENT[reg]),('regime',reg),('feature',reg,feature(row))):
        s = groups.get(key)
        if s:
            p = smooth(s,p)
            if s['n']>=MIN_TRADES and s['dates']>=MIN_DATES and s['symbols']>=MIN_SYMBOLS:
                chosen = s
    out.update(probability=p,naive_probability=prior,state='POOLED' if model['base']['n'] else 'NO_HISTORY')
    # Fixed hypothesis: reduce research exposure only with adequate past evidence
    # of non-positive stressed return. Never suppress a core signal.
    if (chosen['n']>=MIN_TRADES and chosen['dates']>=MIN_DATES and chosen['symbols']>=MIN_SYMBOLS
            and chosen['stress'] is not None and chosen['stress']<=0):
        out.update(exposure=.5,state='RISK_HALF')
    return out


def historical(frames,symbols,horizon,cost):
    if not frames or len(frames)!=len(symbols) or len(set(symbols))!=len(symbols):
        raise ValueError('invalid panel')
    if type(horizon) is not int or horizon<1: raise ValueError('invalid horizon')
    if any(dates!=frames[0][0] or len(pts)!=len(frames[0][2]) or len(dates)!=len(pts)+horizon for dates,_,pts in frames):
        raise ValueError('unaligned panel')
    if any(a>=b for a,b in zip(frames[0][0],frames[0][0][1:])): raise ValueError('unordered dates')
    rows=[]
    for i,day in enumerate(frames[0][0][:len(frames[0][2])]):
        model=fit(rows,day,cost); pending=[]
        for (dates,_,pts),symbol in zip(frames,symbols):
            p=pts[i];r={'day':dates[i],'exit_day':dates[i+horizon],'symbol':symbol,
                'subregime':p.get('subregime'),'market_regime':p.get('market_regime'),
                'risk_veto':p.get('route')=='risk_veto' or p.get('market_regime')=='down' or p.get('subregime')=='down',
                'signals':{**p['signals'],**p['candidates']},'net_pct':100*float(p['net']),'cost_bps':cost}
            if not valid(r,cost): raise ValueError('invalid outcome')
            r['decision']=decide(r,model);pending.append(r)
        rows.extend(pending)
    def compare(data):
        base=disjoint(r for r in data if r['signals'].get('core')=='BUY')
        scored=[r for r in data if r['decision']['probability'] is not None]
        def brier(key):
            return round(mean((r['decision'][key]-(r['net_pct']>0))**2 for r in scored),6) if scored else None
        def exposure_stats(weights):
            n=len(base)
            if not n:return {'trades':0,'mean_exposure':None,'mean_net_pct':None,'stress_mean_pct':None,'tail10_pct':None,'return_per_exposure_pct':None}
            vals=[r['net_pct']*w for r,w in zip(base,weights)]
            return {'trades':n,'mean_exposure':round(mean(weights),4),
                    'mean_net_pct':round(mean(vals),4),
                    'stress_mean_pct':round(mean(stress(r['net_pct'],cost)*w for r,w in zip(base,weights)),4),
                    'tail10_pct':round(mean(sorted(vals)[:ceil(.1*n)]),4),
                    'return_per_exposure_pct':round(sum(vals)/sum(weights),4)}
        weights=[r['decision']['exposure'] for r in base]
        # Equal-average-exposure reference is retrospective, not deployable.
        ref=[mean(weights)]*len(weights) if weights else []
        bins=[]
        for lo in range(0,10,2):
            group=[r for r in scored if lo/10<=r['decision']['probability']<(lo+2)/10 or lo==8 and r['decision']['probability']==1]
            if group:bins.append({'lo':lo/10,'hi':(lo+2)/10,'n':len(group),'mean_probability':round(mean(r['decision']['probability'] for r in group),6),'positive_rate':round(mean(r['net_pct']>0 for r in group),6)})
        return {'opportunities':len(data),'core_candidates':sum(r['signals'].get('core')=='BUY' for r in data),
                'eligible_candidates':sum(eligible(r) for r in data),'scored':len(scored),
                'brier':brier('probability'),'naive_brier':brier('naive_probability'),'calibration_bins':bins,
                'core':metrics(base,cost),'unchanged':exposure_stats([1.0]*len(base)),
                'v572_sized':exposure_stats(weights),'equal_exposure_reference':exposure_stats(ref),
                'reduced_trades':sum(w<1 for w in weights),'states':dict(Counter(r['decision']['state'] for r in data))}
    return {'version':VERSION,'status':'READY','basis':'HISTORICAL_CAUSAL_REPLAY',
            'research_only':True,'strategy_adoption':'HOLD','independent_future_verified':False,
            'overall':compare(rows),'by_regime':{g:compare([r for r in rows if r['subregime']==g]) for g in REGIMES},
            'by_market':{g:compare([r for r in rows if r['market_regime']==g]) for g in MARKETS},
            'market_coverage':dict(Counter(r['market_regime'] or 'unknown' for r in rows)),
            'limitations':'Post-hoc hypothesis; sized opportunity return per original notional, not portfolio P&L. Equal exposure comparator is retrospective. No new entries outside core; no live adoption.'}
