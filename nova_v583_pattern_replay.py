"""Fixed multi-horizon research replay; no network, persistence, model adoption or orders."""
from datetime import date
from statistics import mean,median
from math import ceil
from nova_v582_signal_pattern import pattern,valid_rows,finite,BUCKETS,FAMILIES

VERSION='5.8.3'
HORIZONS=(5,10,20)
MIN_SAMPLES=30
MIN_DATES=15
MIN_SYMBOLS=5
PATTERNS=('SINGLE_SIGNAL','EARLY','ACCELERATING','CONTINUATION','UNCONFIRMED','FAILURE_RISK','CHASE_RISK','OTHER')

def _bar_ok(r):
    vals=[r.get(k) for k in ('price_open','price_high','price_low','price_close')]
    if not all(finite(x) and x>0 for x in vals):return False
    o,h,l,c=vals
    return l<=min(o,c)<=max(o,c)<=h

def _metrics(rows):
    n=len(rows);d=len({r['day'] for r in rows});s=len({r['symbol'] for r in rows})
    base={'samples':n,'dates':d,'symbols':s,'sufficient':n>=MIN_SAMPLES and d>=MIN_DATES and s>=MIN_SYMBOLS}
    fields=('hit_pct','mean_net_pct','median_net_pct','stress_mean_pct','worst_net_pct','tail10_pct','loss_5pct_rate','mean_adverse_pct','adverse_5pct_rate')
    if not base['sufficient']:return {**base,**dict.fromkeys(fields)}
    v=[r['net'] for r in rows];a=[r['adverse'] for r in rows]
    return {**base,'hit_pct':round(100*mean(x>0 for x in v),2),
            'mean_net_pct':round(mean(v),4),'median_net_pct':round(median(v),4),
            'stress_mean_pct':round(mean(r['stress'] for r in rows),4),
            'worst_net_pct':round(min(v),4),'tail10_pct':round(mean(sorted(v)[:ceil(.1*n)]),4),
            'loss_5pct_rate':round(100*mean(x<=-5 for x in v),2),
            'mean_adverse_pct':round(mean(a),4),'adverse_5pct_rate':round(100*mean(x<=-5 for x in a),2)}

def _outcome(rows,i,h,cost):
    entry=rows[i+1]['price_open'];exit=rows[i+h]['price_close']
    def net(bps):
        f=bps/10000
        return 100*(exit*(1-f)/(entry*(1+f))-1)
    return {'net':net(cost),'stress':net(min(2*cost,400)),
            'adverse':min(0.,100*(min(r['price_low'] for r in rows[i+1:i+h+1])/entry-1))}

def replay(histories,*,expected_dates,family='baseline',cost_bps=15):
    """Caller supplies verified completed-bar KIS histories with exact common dates.
    A fixed final 30% date block is reported separately; no fitting/tuning occurs.
    Signal-day features use only the last 20 observations, outcome starts next open.
    Select non-overlapping BUY opportunities before candidate/bucket comparisons.
    """
    base={'version':VERSION,'research_only':True,'strategy_adoption':'HOLD','historical_only':True,
          'independent_future_verified':False,'probability_calibrated':False,
          'family':family,'cost_bps':cost_bps,'horizons':list(HORIZONS)}
    try:
        ds=[date.fromisoformat(x) for x in expected_dates]
        valid=len(ds)>=60 and ds==sorted(set(ds))
    except (TypeError,ValueError):valid=False
    if not valid or family not in FAMILIES or not isinstance(histories,dict) or not finite(cost_bps) or not 0<=cost_bps<=200:
        return {**base,'status':'UNAVAILABLE','reason':'INVALID_PANEL'}
    expected_dates=list(expected_dates);n=len(ds);split=n*7//10
    usable={};excluded={}
    for symbol,h in histories.items():
        if not isinstance(h,dict) or h.get('source')!='kis' or h.get('session_verified') is not True:
            excluded[symbol]='SOURCE_OR_SESSION_UNVERIFIED';continue
        r=h.get('rows')
        if not valid_rows(r,family) or [x['date'] for x in r]!=expected_dates or not all(_bar_ok(x) for x in r):
            excluded[symbol]='INCOMPLETE_OR_INVALID_OHLC';continue
        usable[symbol]=r
    result={}
    for horizon in HORIZONS:
        phases={}
        for label,start,end in [('earlier',19,split),('later',max(19,split),n)]:
            pool=[];crossing=0
            for symbol,rows in usable.items():
                last_exit=-1
                for i in range(start,end):
                    if i<=last_exit or rows[i][family]!='BUY' or rows[i]['risk_veto']:continue
                    if i+horizon>=end:
                        crossing+=1;continue
                    p=pattern(rows[i-19:i+1],family)
                    if 'count_bucket' not in p:continue
                    # Selection and embargo are determined before reading outcomes.
                    last_exit=i+horizon
                    pool.append({'symbol':symbol,'day':expected_dates[i],
                                 'exit_day':expected_dates[i+horizon],
                                 'bucket':p['count_bucket'],'pattern':p.get('pattern','OTHER'),
                                 'candidate':p['status']=='RESEARCH_CANDIDATE',
                                 **_outcome(rows,i,horizon,cost_bps)})
            phases[label]={'all_buy':_metrics(pool),
                           'pattern_candidate':_metrics([r for r in pool if r['candidate']]),
                           'other_buy':_metrics([r for r in pool if not r['candidate']]),
                           'by_count':{k:_metrics([r for r in pool if r['bucket']==k]) for k in BUCKETS},
                           'by_pattern':{k:_metrics([r for r in pool if r['pattern']==k]) for k in PATTERNS},
                           'excluded_cross_boundary':crossing,
                           'start':expected_dates[start],'end':expected_dates[end-1]}
        result[str(horizon)]=phases
    return {**base,'status':'DESCRIPTIVE_ONLY' if usable else 'UNAVAILABLE',
            'eligible_symbols':len(usable),'excluded':excluded,'split_date':expected_dates[split],
            'minimum_samples':MIN_SAMPLES,'minimum_dates':MIN_DATES,'minimum_symbols':MIN_SYMBOLS,
            'results':result,'limitations':'Fixed retrospective hypotheses; later block is not prospective evidence. Same-day cross-symbol outcomes may correlate. Intraday adverse excursion is not an executed stop or portfolio drawdown. Current preset may have survivorship bias. No live ranking adoption.'}
