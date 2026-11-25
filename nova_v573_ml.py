"""Fixed research-only ML comparison. No live signals, order paths or DB writes."""
from collections import Counter
from statistics import mean
import numpy as np
from nova_v5_research import _features, _fit_logit, _predict
from nova_v570_accuracy import disjoint, REGIMES, MARKETS

VERSION='5.7.3'
BLOCK=20
LOOKBACK=120
MIN_TRAIN=50
MIN_DATES=20
MIN_SYMBOLS=5
MIN_CLASS=10
LOSS_THRESHOLD=-5.
FEATURES=('ret5','ret20','ema_spread','rsi','volume_log','atr_ratio',
          'adx','di_spread','ema_slope','bandwidth','relative_strength','index_volatility','index_return20')
COHORTS=('core','range_research')


def feature_vector(f,i,market,ind):
    """Only signal-day stock indicators and strictly earlier index sessions."""
    from core_comparison import _day
    x=_features(f.iloc[i])
    if x is None or i<21:return None
    idx=np.asarray([market.get(_day(t),np.nan) for t in f.index[i-21:i]],float)
    if not np.isfinite(idx).all() or (idx<=0).any():return None
    v=ind.iloc[i]; ir=idx[-1]/idx[0]-1
    values=np.r_[x, float(v.adx)/100,(float(v.plus_di)-float(v.minus_di))/100,
                 float(v.slope),float(v.width),float(f.iloc[i].ret20)-ir,
                 float(np.std(np.diff(idx)/idx[:-1])),ir]
    return [float(a) for a in values] if np.isfinite(values).all() else None


def cohort(r,name):
    if name=='core':return r['core']
    # Separate experimental population, never an order/entry signal.
    return r['market']=='range' and r['regime'] in REGIMES[:4] and r['stock_risk_ok'] is True


def sigmoid(z):return 1/(1+np.exp(-np.clip(z,-22,22)))


def fit_stumps(X,y):
    """Small deterministic logistic gradient-boosted decision stumps, not LightGBM."""
    prior=float(np.clip(np.mean(y),.01,.99)); intercept=float(np.log(prior/(1-prior)))
    z=np.full(len(y),intercept); splits=[]
    masks=[(j,float(t),X[:,j]<=t) for j in range(X.shape[1])
           for t in np.unique(np.quantile(X[:,j],[.25,.5,.75]))]
    masks=[(j,t,m) for j,t,m in masks if 10<=int(m.sum())<=len(y)-10]
    for _ in range(16):
        p=sigmoid(z); g=y-p; h=p*(1-p); best=None
        parent=float(g.sum())**2/(float(h.sum())+10)
        for j,t,m in masks:
            gl,gr=float(g[m].sum()),float(g[~m].sum())
            hl,hr=float(h[m].sum())+10,float(h[~m].sum())+10
            gain=gl*gl/hl+gr*gr/hr-parent
            if gain>0 and (best is None or gain>best[0]):best=(gain,j,t,m,gl/hl,gr/hr)
        if best is None:break
        _,j,t,m,left,right=best;left*=.15;right*=.15
        z+=np.where(m,left,right);splits.append((j,t,left,right))
    return intercept,splits


def predict_stumps(model,X):
    intercept,splits=model;z=np.full(len(X),intercept)
    for j,t,l,r in splits:z+=np.where(X[:,j]<=t,l,r)
    return sigmoid(z)


def supports(y):return min(float(y.sum()),len(y)-float(y.sum()))>=MIN_CLASS


def fit(train):
    X=np.asarray([r['x'] for r in train],float);net=np.asarray([r['net_pct'] for r in train],float)
    y=(net>0).astype(float);down=(net<=LOSS_THRESHOLD).astype(float)
    if not supports(y):return None
    center=X.mean(axis=0);std=X.std(axis=0);std=np.where(std<1e-8,1.,std)
    z=np.clip((X-center)/std,-5,5)
    # Ridge expected return head; regularization and all preprocessing train-only.
    beta=np.linalg.solve(z.T@z+20*np.eye(z.shape[1]),z.T@(net-net.mean()))
    return {'logit':_fit_logit(X,y,l2=20.),'boost':fit_stumps(X,y),'prior':float(y.mean()),
            'center':center,'std':std,'beta':beta,'mean':float(net.mean()),
            'loss':_fit_logit(X,down,l2=20.) if supports(down) else None,
            'loss_prior':float(down.mean())}


def predict(model,rows):
    X=np.asarray([r['x'] for r in rows],float)
    a=_predict(model['logit'],X); b=predict_stumps(model['boost'],X)
    exp=np.clip((X-model['center'])/model['std'],-5,5)@model['beta']+model['mean']
    loss=_predict(model['loss'],X) if model['loss'] is not None else [None]*len(X)
    return [{'logit':float(p),'boost':float(q),'naive':model['prior'],
             'expected_net_pct':float(e),'naive_net_pct':model['mean'],
             'loss_probability':None if l is None else float(l),'naive_loss':model['loss_prior']}
            for p,q,e,l in zip(a,b,exp,loss)]


def summary(rows,predictions):
    pairs=[(r,predictions[(r['day'],r['symbol'])]) for r in rows if (r['day'],r['symbol']) in predictions]
    def brier(key,data,label):
        return round(mean((p[key]-label(r))**2 for r,p in data),6) if data else None
    def mae(key):return round(mean(abs(p[key]-r['net_pct']) for r,p in pairs),4) if pairs else None
    loss=[(r,p) for r,p in pairs if p['loss_probability'] is not None]
    label=lambda r:r['net_pct']>0
    bins={}
    for key in ('logit','boost','naive'):
        bins[key]=[]
        for lo in range(0,10,2):
            group=[(r,p) for r,p in pairs if lo/10<=p[key]<(lo+2)/10 or lo==8 and p[key]==1]
            if group:bins[key].append({'lo':lo/10,'hi':(lo+2)/10,'n':len(group),
                      'mean_probability':round(mean(p[key] for r,p in group),6),
                      'positive_rate':round(mean(label(r) for r,p in group),6)})
    # Non-overlap is selected without reading forecasts/outcomes, for a second matched evaluation.
    independent=disjoint([r for r,p in pairs])
    ids={(r['day'],r['symbol']) for r in independent}
    nonoverlap=[(r,p) for r,p in pairs if (r['day'],r['symbol']) in ids]
    return {'candidates':len(rows),'scored':len(pairs),'unscored':len(rows)-len(pairs),
            'scored_dates':len({r['day'] for r,p in pairs}),
            'scored_symbols':len({r['symbol'] for r,p in pairs}),
            'brier':{k:brier(k,pairs,label) for k in ('logit','boost','naive')},
            'nonoverlap_scored':len(nonoverlap),
            'nonoverlap_brier':{k:brier(k,nonoverlap,label) for k in ('logit','boost','naive')},
            'expected_return_mae_pct':{'ridge':mae('expected_net_pct'),'naive':mae('naive_net_pct')},
            'loss_scored':len(loss),'loss_brier':{k:brier(k,loss,lambda r:r['net_pct']<=LOSS_THRESHOLD) for k in ('loss_probability','naive_loss')},
            'calibration_bins':bins}


def historical(frames,symbols,horizon,cost):
    if not frames or len(frames)!=len(symbols) or len(set(symbols))!=len(symbols):raise ValueError('invalid panel')
    if type(horizon) is not int or horizon<1:raise ValueError('invalid horizon')
    dates=frames[0][0];n=len(frames[0][2])
    if any(ds!=dates or len(pts)!=n or len(ds)!=n+horizon for ds,_,pts in frames):raise ValueError('unaligned panel')
    if any(a>=b for a,b in zip(dates,dates[1:])):raise ValueError('unordered dates')
    rows=[]
    for i,day in enumerate(dates[:n]):
        for (_,_,pts),symbol in zip(frames,symbols):
            p=pts[i];x=p.get('ml_features');net=100*float(p['net'])
            if not isinstance(x,list) or len(x)!=len(FEATURES) or not np.isfinite(np.asarray(x,float)).all():
                return {'version':VERSION,'status':'UNAVAILABLE','reason':'FEATURES_MISSING','research_only':True,'strategy_adoption':'HOLD'}
            if not np.isfinite(net) or net<=-100:raise ValueError('invalid outcome')
            rows.append({'day':day,'exit_day':dates[i+horizon],'symbol':symbol,'x':x,'net_pct':net,
                'core':p['signals'].get('core')=='BUY','market':p.get('market_regime'),
                'regime':p.get('subregime'),'stock_risk_ok':p.get('ml_stock_risk_ok') is True})
    result={}
    for name in COHORTS:
        candidates=[r for r in rows if cohort(r,name)];preds={};folds=[]
        for lo in range(0,n,BLOCK):
            day=dates[lo];hi=min(lo+BLOCK,n);lookback=dates[max(0,lo-LOOKBACK)]
            # Completed outcomes strictly before first date in each evaluation block.
            train=disjoint(r for r in candidates if lookback<=r['day'] and r['exit_day']<day)
            val=[r for r in candidates if day<=r['day']<=dates[hi-1]]
            td=len({r['day'] for r in train});ts=len({r['symbol'] for r in train})
            if not val:continue
            model=fit(train) if len(train)>=MIN_TRAIN and td>=MIN_DATES and ts>=MIN_SYMBOLS else None
            fold={'start':day,'end':dates[hi-1],'train':len(train),'train_dates':td,'train_symbols':ts,
                  'last_training_exit':max((r['exit_day'] for r in train),default=None),
                  'candidates':len(val),'scored':len(val) if model is not None else 0}
            folds.append(fold)
            if model is None:continue
            for r,p in zip(val,predict(model,val)):preds[(r['day'],r['symbol'])]=p
        result[name]={'overall':summary(candidates,preds),'folds':folds,
                      'by_regime':{g:summary([r for r in candidates if r['regime']==g],preds) for g in REGIMES},
                      'by_market':{g:summary([r for r in candidates if r['market']==g],preds) for g in MARKETS}}
    return {'version':VERSION,'status':'READY' if any(v['overall']['scored'] for v in result.values()) else 'INSUFFICIENT',
            'basis':'HISTORICAL_WALK_FORWARD','research_only':True,'strategy_adoption':'HOLD',
            'independent_future_verified':False,'features':list(FEATURES),'loss_threshold_pct':LOSS_THRESHOLD,
            'opportunities':len(rows),'market_coverage':dict(Counter(r['market'] or 'unknown' for r in rows)),
            'cohorts':result,'limitations':'Fixed post-hoc hypotheses; all outcomes historical. No new signals, orders, sizing, or shared-capital P&L.'}
