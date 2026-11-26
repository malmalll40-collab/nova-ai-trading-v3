"""Research-only repeated completed-bar signals. No network, trading or persistence."""
from datetime import date
from math import isfinite
from statistics import mean, median

VERSION='5.8.2'
FAMILIES=('baseline','five_core')
WINDOWS=(5,10,20)
MIN_ROWS=20
BUCKETS=('1','2-3','4-5','6-9','10+')

def finite(x):
    return type(x) in (int,float) and isfinite(x)

def bucket(n):
    return '1' if n==1 else '2-3' if n<=3 else '4-5' if n<=5 else '6-9' if n<=9 else '10+'

def valid_rows(rows,family):
    if family not in FAMILIES or not isinstance(rows,list):return False
    last=None
    for r in rows:
        if not isinstance(r,dict) or r.get('completed') is not True:return False
        try:d=date.fromisoformat(r['date'])
        except (KeyError,ValueError,TypeError):return False
        if last is not None and d<=last:return False
        last=d
        if r.get(family) not in ('BUY','SELL','HOLD'):return False
        if not finite(r.get('price_close')) or r['price_close']<=0 or type(r.get('risk_veto')) is not bool:return False
    return True

def pattern(rows,family='baseline'):
    """Only the supplied prefix; never inspect outcome fields or future rows.
    Requires ordered complete observations; caller verifies session coverage.
    Scores are frozen heuristics, not fitted probabilities or trade advice.
    """
    base={'version':VERSION,'research_only':True,'strategy_adoption':'HOLD','family':family,'score':None,'probability':None}
    if not valid_rows(rows,family):return {**base,'status':'UNAVAILABLE','reason':'INVALID_HISTORY'}
    if len(rows)<MIN_ROWS:return {**base,'status':'INSUFFICIENT','reason':'HISTORY_SHORT'}
    w=rows[-20:];prices=[r['price_close'] for r in w]
    idx=[i for i,r in enumerate(w) if r[family]=='BUY' and not r['risk_veto']]
    counts={str(n):sum(r[family]=='BUY' and not r['risk_veto'] for r in w[-n:]) for n in WINDOWS}
    if not idx:return {**base,'status':'FILTERED','reason':'NO_RECENT_BUY','counts':counts}
    age=19-idx[-1];streak=0
    for r in reversed(w):
        if r[family]!='BUY' or r['risk_veto']:break
        streak+=1
    gaps=[b-a for a,b in zip(idx,idx[1:])]
    ret5=100*(prices[-1]/prices[-6]-1)
    ret10=100*(prices[-1]/prices[-11]-1)
    since=100*(prices[-1]/prices[idx[0]]-1)
    peak_drop=100*(prices[-1]/max(prices)-1)
    rising=mean(prices[-5:])>mean(prices[-10:-5]) and min(prices[-5:])>=min(prices[-10:-5])
    details={'counts':counts,'streak':streak,'last_buy_age':age,'mean_gap':round(mean(gaps),3) if gaps else None,
             'since_first_buy_pct':round(since,4),'ret5_pct':round(ret5,4),'ret10_pct':round(ret10,4),
             'drawdown_from_20d_close_peak_pct':round(peak_drop,4),'rising_close_structure':rising,
             'count_bucket':bucket(len(idx)),'as_of':w[-1]['date']}
    if w[-1]['risk_veto'] or w[-1][family]=='SELL' or age>3:
        return {**base,**details,'status':'FILTERED','reason':'RISK_OR_STALE_SIGNAL'}
    if ret5<0 and (not rising or peak_drop<=-5):
        return {**base,**details,'status':'FILTERED','pattern':'FAILURE_RISK','reason':'WEAK_CLOSE_STRUCTURE'}
    if since>=25 or ret5>=15:
        return {**base,**details,'status':'FILTERED','pattern':'CHASE_RISK','reason':'EXTENDED_MOVE'}
    if len(idx)<2:return {**base,**details,'status':'OBSERVE','pattern':'SINGLE_SIGNAL','reason':'REPEAT_REQUIRED'}
    if not rising or ret10<=0:return {**base,**details,'status':'OBSERVE','pattern':'UNCONFIRMED','reason':'TREND_UNCONFIRMED'}
    density=min(25,5*counts['5']+2*counts['10'])
    trend=25 if rising else 0
    momentum=min(20,max(0,2*ret5+ret10))
    recency=15-5*age
    risk=max(0,15+2*peak_drop)
    score=round(density+trend+momentum+recency+risk,2)
    label='EARLY' if len(idx)<=3 else 'ACCELERATING' if len(gaps)>=3 and gaps[-1]<mean(gaps[:-1]) else 'CONTINUATION'
    return {**base,**details,'status':'RESEARCH_CANDIDATE','pattern':label,'score':score,
            'components':{'density':density,'close_structure':trend,'momentum':round(momentum,2),'recency':recency,'pullback_risk':round(risk,2)}}

def rank(histories,*,as_of,expected_dates,family='baseline'):
    """Research ranking over caller-supplied verified KIS complete-bar histories.
    Exact calendar match required; no missing data filled, no live scans.
    """
    base={'version':VERSION,'research_only':True,'strategy_adoption':'HOLD','probability_calibrated':False,'as_of':as_of,'family':family}
    try:
        ds=[date.fromisoformat(x) for x in expected_dates]
        valid=len(ds)==20 and ds==sorted(set(ds)) and expected_dates[-1]==as_of
    except (ValueError,TypeError,IndexError):valid=False
    if not valid or not isinstance(histories,dict):return {**base,'status':'UNAVAILABLE','rows':[],'excluded':{}}
    candidates=[];excluded={}
    for symbol,h in histories.items():
        if not isinstance(h,dict) or h.get('source')!='kis' or h.get('session_verified') is not True:
            excluded[symbol]='SOURCE_OR_SESSION_UNVERIFIED';continue
        rows=h.get('rows')
        if not valid_rows(rows,family) or [r['date'] for r in rows[-20:]]!=list(expected_dates):
            excluded[symbol]='INCOMPLETE_OR_STALE_HISTORY';continue
        p=pattern(rows,family)
        if p['status']!='RESEARCH_CANDIDATE':excluded[symbol]=p.get('reason',p['status']);continue
        candidates.append({'symbol':symbol,**p})
    candidates.sort(key=lambda r:(-r['score'],r['symbol']))
    return {**base,'status':'READY','rows':[{'rank':i+1,**r} for i,r in enumerate(candidates)],'excluded':excluded,'evaluated':len(histories)}

def backtest(rows,family='baseline',cost_bps=15):
    """Descriptive 5-session replay using candle_research outcome fields.
    Select non-overlapping BUY opportunities before grouping by count bucket.
    Not a holdout or proof of future performance.
    """
    base={'version':VERSION,'research_only':True,'strategy_adoption':'HOLD','historical_only':True,
          'independent_future_verified':False,'family':family,'horizon_sessions':5,'cost_bps':cost_bps}
    if not valid_rows(rows,family) or not finite(cost_bps) or not 0<=cost_bps<=200:
        return {**base,'status':'UNAVAILABLE','buckets':{}}
    pool={k:[] for k in BUCKETS};last_exit=-1
    for i in range(19,len(rows)-5):
        r=rows[i]
        if i<=last_exit or r[family]!='BUY' or r['risk_veto']:continue
        p=pattern(rows[:i+1],family)
        if 'count_bucket' not in p:continue
        entry=r.get('next_open');exit=r.get('exit_close_t5')
        if r.get('outcome_status')!='EVALUATED' or not finite(entry) or not finite(exit) or min(entry,exit)<=0:continue
        fee=cost_bps/10000;net=100*(exit*(1-fee)/(entry*(1+fee))-1)
        pool[p['count_bucket']].append(net);last_exit=i+5
    def metrics(v):
        n=len(v);return {'samples':n,'hit_pct':round(100*sum(x>0 for x in v)/n,2) if n>=10 else None,
                         'mean_net_pct':round(mean(v),4) if n>=10 else None,
                         'median_net_pct':round(median(v),4) if n>=10 else None,
                         'worst_pct':round(min(v),4) if n>=10 else None,
                         'loss_5pct_rate':round(100*sum(x<=-5 for x in v)/n,2) if n>=10 else None}
    return {**base,'status':'DESCRIPTIVE_ONLY','buckets':{k:metrics(v) for k,v in pool.items()},
            'limitations':'Single-symbol non-overlapping historical opportunities; outcome fields must originate from completed-bar candle research. Small samples suppressed; no ML training or 10/20-day outcomes.'}
