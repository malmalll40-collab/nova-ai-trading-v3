"""Read-only walk-forward candidate research; no live adoption or DB writes.
Fixed parameters chosen as research hypotheses, not fitted to test returns.
"""
from __future__ import annotations
import numpy as np
from engine import WARMUP, decide
from core_comparison import _day, _reference, _core_at
from nova_v5_research import _features, _finite, _fit_logit, _predict, simulate_long_only, HORIZON
from nova_v53_audit import grouped_signal

MIN_TRAIN=100
BLOCK=40
MAX_OBSERVATIONS=500
THRESHOLD=.58


def labeled_rows(f, start, cost):
    rows={}
    fee=cost/10000
    for i in range(start,len(f)-HORIZON):
        x=_features(f.iloc[i]); o=_finite(f.Open.iloc[i+1]); c=_finite(f.Close.iloc[i+HORIZON])
        if x is None or o is None or c is None or min(o,c)<=0: continue
        net=c*(1-fee)/(o*(1+fee))-1
        rows[i]=(x,int(net>0),net)
    return rows


def walk_predictions(rows, start, stop):
    """Every fit uses labels settled strictly BEFORE the first signal in its block."""
    preds={}; folds=[]
    for lo in range(start,stop,BLOCK):
        hi=min(lo+BLOCK,stop)
        train=[i for i in sorted(rows) if i+HORIZON<lo]
        val=[i for i in range(lo,hi) if i in rows]
        if len(train)<MIN_TRAIN or not val: continue
        X=np.stack([rows[i][0] for i in train]); y=np.array([rows[i][1] for i in train],float)
        if min(y.sum(),len(y)-y.sum())<10: continue
        Xv=np.stack([rows[i][0] for i in val])
        prior=float(y.mean())
        legacy=_predict(_fit_logit(X,y,l2=2.),Xv)
        # Candidate: stronger L2 and fixed shrinkage toward training base rate.
        shrunk=.5*_predict(_fit_logit(X,y,l2=20.),Xv)+.5*prior
        if not (np.isfinite(legacy).all() and np.isfinite(shrunk).all()): continue
        for i,p,q in zip(val,legacy,shrunk): preds[i]=(float(p),float(q),prior)
        folds.append({'start_idx':lo,'end_idx':hi-1,'last_training_label_idx':train[-1]+HORIZON,
                      'train_count':len(train),'validation_count':len(val)})
    return preds,folds


def score_predictions(rows,preds):
    if not preds: return {}
    ids=sorted(preds); y=np.array([rows[i][1] for i in ids]); p=np.array([preds[i] for i in ids]);
    scores=np.mean((p-y[:,None])**2,axis=0)
    return {k:round(float(v),6) for k,v in zip(('legacy_ai','shrink_ai','naive'),scores)}


def opportunity_metrics(rows,ids,signals):
    selected=[i for i in ids if signals.get(i)=='BUY']; n=len(selected)
    good=sum(rows[i][1] for i in selected); total_good=sum(rows[i][1] for i in ids)
    return {'buy_signals':n,'positive_signals':good,'negative_signals':n-good,
            'precision_pct':round(100*good/n,2) if n else None,
            'coverage_pct':round(100*n/len(ids),2),
            'missed_positive_opportunities':total_good-good,
            'positive_opportunity_recall_pct':round(100*good/total_good,2) if total_good else None,
            'mean_signal_net_pct':round(100*float(np.mean([rows[i][2] for i in selected])),3) if n else None}


def analyze_accuracy(f,cfg,*,source,benchmark=None,known_signals=None,known_votes=None,known_vetos=None):
    base={'status':'INSUFFICIENT','research_only':True,'modifies_live_signal':False,'writes_observation_database':False,
          'strategy_adoption':'HOLD','source':source,'historical_session_verified':False,
          'reason':'독립 전향 검증과 과거 세션 기준 통일 전에는 채택하지 않습니다.'}
    if source!='kis' or cfg.mode!='스윙': return {**base,'reason':'KIS 스윙 완료봉 전용'}
    if not f.index.is_monotonic_increasing or not f.index.is_unique: return {**base,'reason':'일봉 날짜 순서/중복 검증 실패'}
    start=max(WARMUP+1,len(f)-MAX_OBSERVATIONS-HORIZON)
    rows=labeled_rows(f,start,cfg.cost_bps)
    eval_start=start+MIN_TRAIN+HORIZON
    end=len(f)-HORIZON
    preds,folds=walk_predictions(rows,eval_start,end)
    ids=sorted(preds)
    # Require complete paired evaluation coverage; no missing intervals masquerading as HOLD.
    if len(ids)<40 or len(folds)<2 or ids!=list(range(eval_start,end)):
        return {**base,'reason':'공통 검증 시점 부족/결측 (최소 40시점·2구간, 평가 전 구간 유효 필요)',
                'available_bars':len(f),'paired_samples':len(ids),'fold_count':len(folds)}
    signals={k:{} for k in ('baseline','grouped','legacy_ai','shrink_ai','naive','always_buy')}
    known_signals=known_signals or {};known_votes=known_votes or {};known_vetos=known_vetos or {}
    for i in ids:
        if i in known_signals and i in known_votes and i in known_vetos:
            sig=known_signals[i]; votes=known_votes[i]; veto=known_vetos[i]
        else:
            d=decide(f,cfg,i);sig=d.signal;votes=[v.direction for v in d.votes[:10]];veto=d.risk_veto
        signals['baseline'][i]=sig
        signals['grouped'][i]=grouped_signal(votes,risk_veto=veto)['signal']
        for key,score in zip(('legacy_ai','shrink_ai','naive'),preds[i]):
            signals[key][i]='BUY' if not veto and score>=THRESHOLD else 'HOLD'
        signals['always_buy'][i]='BUY'
    market,market_reason=_reference(benchmark)
    core={i:_core_at(f,i,market) for i in ids} if market is not None else {}
    if core and all(x['status']=='READY' for x in core.values()):
        signals['five_core']={i:x['signal'] for i,x in core.items()}
    strategies={}
    for key,s in signals.items():
        strategies[key]={'opportunities':opportunity_metrics(rows,ids,s),
                         'portfolio':simulate_long_only(f,s,eval_start,len(f),cfg.cost_bps),
                         'stress_portfolio':simulate_long_only(f,s,eval_start,len(f),cfg.cost_bps*2)}
    if 'five_core' not in strategies:
        strategies['five_core']={'portfolio':{'status':'UNAVAILABLE','reason':market_reason or '공통 평가 구간 시장지수 누락'},'opportunities':{}}
    # Folds describe paired predictive scores, not independent trades or reset portfolios.
    reports=[]
    for fold in folds:
        subset={i:p for i,p in preds.items() if fold['start_idx']<=i<=fold['end_idx']}
        reports.append({'start':_day(f.index[fold['start_idx']]),'end':_day(f.index[fold['end_idx']]),
                        'last_training_outcome':_day(f.index[fold['last_training_label_idx']]),
                        'train_count':fold['train_count'],'samples':len(subset),'brier':score_predictions(rows,subset)})
    brier=score_predictions(rows,preds)
    from nova_v541_core import study as core_study
    core_focus=core_study(f,ids,rows,market,cfg.cost_bps)
    return {**base,'status':'READY','available_bars':len(f),'paired_samples':len(ids),'fold_count':len(folds),
            'start':_day(f.index[ids[0]]),'end':_day(f.index[ids[-1]]),'last_outcome_date':_day(f.index[-1]),
            'positive_opportunities':sum(rows[i][1] for i in ids),'brier':brier,'folds':reports,'strategies':strategies,
            'core_focus':core_focus,'candidate_beats_naive':brier['shrink_ai']<brier['naive'],
            'cost_bps':cfg.cost_bps,'stress_cost_bps':cfg.cost_bps*2,'threshold':THRESHOLD,
            'limitations':'동일 KIS 과거봉(과거 정규장 범위 미검증), 중첩 5일 결과, 수정주가/생존 편향 가능. 모델은 이번 연구에서 설계하여 독립 미래 검증 아님. 점수는 보정 확률 아님. 가상 매매 및 비용 가정.'}
