"""NOVA v5.0 independent, read-only out-of-sample strategy laboratory.

Nothing here can change the production decision, broker orders, KIS quote, or
persisted forward observations. Synthetic and historic-as-of requests fail
closed. This backtest is a research approximation, not actual fill evidence.
"""
from __future__ import annotations

import math
import numpy as np
import pandas as pd
from engine import Config, WARMUP, decide
from core_comparison import _core_at, _day, _reference
from nova_v52_risk import study_risks

HORIZON = 5
INITIAL_CASH = 10_000_000.0
FEATURES = ('ret5', 'ret20', 'trend_spread', 'rsi_centered', 'vol_log', 'atr_ratio')


def _finite(v):
    try:
        x = float(v)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError, OverflowError):
        return None


def _features(row):
    close = _finite(row.get('Close'))
    ema20 = _finite(row.get('ema20'))
    ema50 = _finite(row.get('ema50'))
    atr = _finite(row.get('atr'))
    rsi = _finite(row.get('rsi'))
    ratio = _finite(row.get('vol_ratio'))
    ret5 = _finite(row.get('ret5'))
    ret20 = _finite(row.get('ret20'))
    if any(v is None for v in (close, ema20, ema50, atr, rsi, ratio, ret5, ret20)) or close <= 0 or ema50 <= 0 or atr <= 0 or ratio < 0:
        return None
    arr=np.array([np.clip(ret5,-.5,.5),np.clip(ret20,-1,1),
                  np.clip(ema20/ema50-1,-.5,.5),
                  np.clip((rsi-50)/50,-1,1),np.clip(np.log1p(ratio),0,5),
                  np.clip(atr/close,0,.5)],dtype=float)
    return arr if np.all(np.isfinite(arr)) else None


def remove_role_vote(votes, removed_index: int, cfg: Config, risk_veto: bool) -> str:
    """Counterfactual removing exactly one independent role; recompute derived 11/12.

    Never count role 13 as a sell vote and never pretend derived reviewers are
    independent. This tests un-tuned fixed thresholds, not a newly optimised policy.
    """
    if removed_index not in range(10):
        raise ValueError('Only independent roles 01–10 can be removed')
    technical=[v.direction for j,v in enumerate(votes[:10]) if j != removed_index]
    buys=sum(v==1 for v in technical)
    sells=sum(v==-1 for v in technical)
    bullish=int(buys>=4 and buys>sells)
    bearish=-int(sells>=4 and sells>buys)
    n_buys=buys+int(bullish==1)
    n_sells=sells+int(bearish==-1)
    if n_sells>=cfg.min_agree and n_sells-n_buys>=cfg.min_margin:
        return 'SELL'
    if not risk_veto and n_buys>=cfg.min_agree and n_buys-n_sells>=cfg.min_margin:
        return 'BUY'
    return 'HOLD'


def simulate_long_only(f:pd.DataFrame, signals:dict[int,str], start:int, stop:int, cost_bps:float,
                       *, horizon:int=HORIZON, include_trades:bool=False, include_equity:bool=False, research_caps:dict[int,float]|None=None) -> dict:
    """Research daily marked-to-market portfolio, fixed 25% max exposure.

    Next completed session's OPEN entry, exit at t+5 CLOSE, non-overlapping,
    whole shares, symmetric user-defined friction per side, no short SELL.
    Missing/invalid OHLC never creates a fabricated fill.
    """
    cash=INITIAL_CASH;qty=0;exit_at=-1;opens=0;trades=[];skips=0;curve=[cash];ledger=[];unfillable=0
    end=min(stop,len(f))
    for j in range(start,end):
        # Signals from j-1 can be executed only at j open, not on j-1 close.
        if qty == 0 and j>start and signals.get(j-1)=='BUY':
            desired_exit=j-1+horizon
            if desired_exit < end:
                entry=_finite(f['Open'].iloc[j]);close=_finite(f['Close'].iloc[j]);
                if entry is not None and close is not None and entry>0 and close>0:
                    fee=cost_bps/10000
                    cap=.25 if research_caps is None else min(.25,max(0.,float(research_caps.get(j-1,0.))))
                    candidate=math.floor((cash*cap)/(entry*(1+fee)))
                    if candidate>0:
                        qty=candidate;entry_cash=entry*qty*(1+fee)
                        cash-=entry_cash;opens+=1;exit_at=desired_exit
                        trade_entry=entry;trade_fee=fee
                        trade_signal_idx=j-1; trade_entry_idx=j
                    else:
                        unfillable+=1
                else:
                    unfillable+=1
        elif qty>0 and j>start and signals.get(j-1)=='BUY':
            skips+=1
        current=_finite(f['Close'].iloc[j])
        if qty>0 and j==exit_at:
            if current is None or current<=0:
                # Invalid settlement: do not silently invent a profit.
                return {'status':'UNAVAILABLE','reason':'청산 가격 결측', 'completed_trades':len(trades)}
            proceeds=qty*current*(1-trade_fee)
            cash+=proceeds
            ret=100*((current*(1-trade_fee)/(trade_entry*(1+trade_fee)))-1)
            trades.append(ret)
            if include_trades:
                ledger.append({'signal_date':_day(f.index[trade_signal_idx]),
                               'entry_date':_day(f.index[trade_entry_idx]),'exit_date':_day(f.index[j]),
                               'entry_price':round(float(trade_entry),4),
                               'exit_price':round(float(current),4),
                               'quantity_shares':int(qty),
                               'net_trade_return_pct':round(float(ret),3),
                               'net_pnl_krw':round(float(proceeds-qty*trade_entry*(1+trade_fee)),2),
                               'entry_rule':'신호 다음 거래일 시가',
                               'exit_rule':'진입일 포함 5번째 거래일 종가',
                               'actual_fill':False})
            qty=0;exit_at=-1
        if current is None or current<=0:
            return {'status':'UNAVAILABLE','reason':'평가 가격 결측','completed_trades':len(trades)}
        curve.append(cash+qty*current)
    if qty:
        return {'status':'UNAVAILABLE','reason':'미완료 포지션','completed_trades':len(trades)}
    values=np.array(curve,dtype=float)
    maximum=np.maximum.accumulate(values)
    n=len(trades)
    result={'status':'READY','buy_opportunities':sum(signals.get(i)=='BUY' for i in range(start,end-1)),
            'completed_trades':n,'overlap_skipped':skips,
            'win_rate_pct':round(100*sum(x>0 for x in trades)/n,2) if n else None,
            'mean_trade_net_pct':round(float(np.mean(trades)),3) if n else None,
            'portfolio_return_pct':round((cash/INITIAL_CASH-1)*100,3),
            'portfolio_mdd_pct':round(float(np.min(values/maximum-1))*100,3),
            'cost_bps_per_side':cost_bps,'position_cap_pct':25,'variable_research_cap':research_caps is not None,
            'trade_returns_pct':[round(x,3) for x in trades[:40]],
            'limitations':'시가 체결·정수 주식·편도 비용 가정, 세금·시장충격·미체결·배당·조정주가 효과 미포함',
            'performance_assessable':n>0,'unfillable_opportunities':unfillable}
    if include_equity:
        result['equity_curve']=[float(x/INITIAL_CASH) for x in values]
    if include_trades:
        result['trade_ledger']=ledger[:60]
        result['trade_ledger_truncated']=len(ledger)>60
    return result


def _fit_logit(X:np.ndarray,y:np.ndarray, *, max_iter:int=170, l2:float=2.) -> dict:
    """Small bounded pure-numpy L2 logistic research model; train-only scaling."""
    center=np.median(X,axis=0)
    std=np.std(X,axis=0)
    std=np.where(std<1e-8,1.,std)
    scaled=np.clip((X-center)/std,-5,5)
    weights=np.zeros(X.shape[1],dtype=float)
    prevalence=float(np.clip(np.mean(y),.01,.99))
    intercept=float(np.log(prevalence/(1-prevalence)))
    for _ in range(max_iter):
        logits=np.clip(scaled@weights+intercept,-22,22)
        prob=1/(1+np.exp(-logits))
        residue=prob-y
        grad=scaled.T@residue/len(y) + l2*weights/len(y)
        grad_intercept=float(np.mean(residue))
        weights-=.18*np.clip(grad,-2,2)
        intercept-=.18*np.clip(grad_intercept,-2,2)
    return {'center':center,'std':std,'weights':weights,'intercept':intercept,
            'prevalence':prevalence}


def _predict(model:dict,X:np.ndarray)->np.ndarray:
    z=np.clip(((X-model['center'])/model['std']).clip(-5,5) @model['weights']+model['intercept'],-22,22)
    return 1/(1+np.exp(-z))


def _ai_research(f:pd.DataFrame, indices:list[int], cut:int, last:int, cost_bps:float)->dict:
    """Fit only on labels fully known before the validation segment starts."""
    base={'status':'INSUFFICIENT','model':'Fixed-feature L2 Logistic Regression (research)',
          'fixed_features':list(FEATURES),'score_is_calibrated_probability':False,
          'threshold':0.58,'uses_live_orders':False,
          'note':'학습형 AI 연구 점수이며 검증된 상승 확률이 아닙니다.'}
    train=[];validation=[]
    for i in indices:
        if i+HORIZON>=len(f):continue
        x=_features(f.iloc[i])
        if x is None:continue
        o=_finite(f['Open'].iloc[i+1]);c=_finite(f['Close'].iloc[i+HORIZON])
        if o is None or c is None or min(o,c)<=0:continue
        label=1 if c*(1-cost_bps/10000)>o*(1+cost_bps/10000) else 0
        item=(i,x,label)
        if i+HORIZON < cut:train.append(item)
        elif i>=cut and i+HORIZON < last:validation.append(item)
    if len(train)<70 or len(validation)<20:
        return {**base,'reason':f'학습 {len(train)} / 검증 {len(validation)}건 (최소 70/20)'}
    xtrain=np.stack([x for _,x,_ in train]);ytrain=np.array([y for _,_,y in train],dtype=float)
    if ytrain.sum()<8 or (len(ytrain)-ytrain.sum())<8:
        return {**base,'reason':'학습 라벨 한쪽 방향의 표본이 부족합니다.','train_samples':len(train)}
    xval=np.stack([x for _,x,_ in validation]);yval=np.array([y for _,_,y in validation],dtype=float)
    model=_fit_logit(xtrain,ytrain)
    score=_predict(model,xval)
    if not np.all(np.isfinite(score)):
        return {**base,'reason':'비정상 AI 점수'}
    predictions={i:('BUY' if s>=0.58 else 'HOLD') for (i,_,_),s in zip(validation,score)}
    result=simulate_long_only(f,predictions,cut,last,cost_bps,include_trades=True)
    brier=float(np.mean((score-yval)**2)); base_brier=float(np.mean((np.mean(ytrain)-yval)**2))
    return {**base,'status':'READY','train_samples':len(train),'validation_samples':len(validation),
            'train_last_signal_date':_day(f.index[train[-1][0]]),
            'validation_start_date':_day(f.index[validation[0][0]]),
            'validation_up_rate_pct':round(float(np.mean(yval))*100,2),
            'train_up_rate_pct':round(float(np.mean(ytrain))*100,2),
            'brier_score':round(brier,4),'naive_train_rate_brier':round(base_brier,4),
            'score_range':[round(float(np.min(score)),3),round(float(np.max(score)),3)],
            'research_strategy':result,'validated_edge':False,
            'limitations':'단일 종목, 짧은 비독립 5일 수익 관측, 임의 거래비용, 학습 시점에 확정된 과거 봉만 이용. 백테스트 우월성 미입증.'}


def analyze(f:pd.DataFrame, cfg:Config, *, benchmark:pd.DataFrame|None,
            source:str, is_demo:bool=False, asof_date:str|None=None)->dict:
    base={'version':'5.3.0','status':'INSUFFICIENT','research_only':True,
          'modifies_live_signal':False,'writes_observation_database':False,
          'places_orders':False,'model_promoted_to_live':False,
          'historical_signal_is_prospective':False,
          'source':source,'horizon_sessions':HORIZON,
          'policy':'No strategy adopted until separately validated on future observations.',
          'assumptions':'완료봉 판단 → 다음 거래일 시가 진입 → 5번째 거래일 종가 청산. 최대 25% 투자, 중복 포지션 금지, 편도 비용. 재무·안정형 최종 신호 소급 재현 불가.'}
    if cfg.mode!='스윙' or is_demo or asof_date is not None:
        return {**base,'status':'NOT_APPLICABLE','reason':'국내 실종목 현재 스윙 완료봉 전용'}
    if f is None or len(f)<165:
        return {**base,'reason':f'완료 일봉 최소 165개 필요 (현재 {len(f) if f is not None else 0}개)'}
    start=max(WARMUP+1,len(f)-270)
    stop=len(f)
    signal_indices=list(range(start,stop-HORIZON))
    if len(signal_indices)<85:
        return {**base,'reason':'평가 가능한 시점 부족'}
    cut=signal_indices[int(.7*len(signal_indices))]
    baseline={};ablation={j:{} for j in range(10)};vote_matrix={};veto_matrix={}
    for i in signal_indices:
        d=decide(f,cfg,i)
        baseline[i]=d.signal
        vote_matrix[i]=[int(v.direction) for v in d.votes[:10]]
        veto_matrix[i]=bool(d.risk_veto)
        for j in range(10):
            ablation[j][i]=remove_role_vote(d.votes,j,cfg,d.risk_veto)
    before=simulate_long_only(f,baseline,start,cut,cfg.cost_bps)
    after=simulate_long_only(f,baseline,cut,stop,cfg.cost_bps,include_trades=True)
    roles=[]
    latest_votes=decide(f,cfg,signal_indices[-1]).votes
    for j in range(10):
        modified=simulate_long_only(f,ablation[j],cut,stop,cfg.cost_bps,include_trades=True)
        delta=None
        if modified['status']=='READY' and after['status']=='READY':
            delta=round(modified['portfolio_return_pct']-after['portfolio_return_pct'],3)
        base_trades={x['signal_date']:x for x in after.get('trade_ledger',[])}
        modified_trades={x['signal_date']:x for x in modified.get('trade_ledger',[])}
        missing=[base_trades[k] for k in sorted(base_trades.keys()-modified_trades.keys())]
        added=[modified_trades[k] for k in sorted(modified_trades.keys()-base_trades.keys())]
        roles.append({'role':latest_votes[j].role,
                      'removed_role_index':j+1,'removed_model':modified,
                      'return_difference_pp':delta,
                      'base_completed_trades':after.get('completed_trades',0),
                      'without_completed_trades':modified.get('completed_trades',0),
                      'removed_trade_count':len(missing),'added_trade_count':len(added),
                      'removed_trade_examples':missing[:8],
                      'added_trade_examples':added[:8],
                      'interpretation_warning':('비교 거래 없음 또는 너무 적음' if max(after.get('completed_trades',0),modified.get('completed_trades',0))<5 else '표본별 차이를 재검증해야 함'),
                      'method':'같은 검증 구간·고정 임계값·11/12번 재산출. 역할 제거 실험이지 독립 기여 인과 추정 아님.'})
    market, market_reason=_reference(benchmark)
    core=None
    if market is not None:
        core_signals={};valid=0
        for i in signal_indices:
            co=_core_at(f,i,market)
            if co['status']=='READY':valid+=1
            core_signals[i]=co['signal'] if co['status']=='READY' else 'HOLD'
        # An incomplete benchmark in validation MUST NOT be interpreted as a HOLD strategy success.
        needed=sum(i>=cut for i in signal_indices)
        available=sum(_core_at(f,i,market)['status']=='READY' for i in signal_indices if i>=cut)
        if available>=needed and needed>0:
            core=simulate_long_only(f,core_signals,cut,stop,cfg.cost_bps,include_trades=True)
            core['status']='READY'
        else:
            core={'status':'UNAVAILABLE','reason':f'검증 구간 시장지수 미확인 {needed-available}개 거래일'}
    else:
        core={'status':'UNAVAILABLE','reason':market_reason}
    ai=_ai_research(f,signal_indices,cut,stop,cfg.cost_bps)
    from nova_v521_aligned import compare_paired_exit_rules
    aligned=compare_paired_exit_rules(f,baseline,cut,stop,cfg)
    risk_audit=study_risks(f,baseline,vote_matrix,formation_start=start,cut=cut,stop=stop,
                           cost_bps=cfg.cost_bps,benchmark=benchmark,simulator=simulate_long_only)
    from nova_v53_audit import (benchmark_coverage, grouped_vote_study,
                                retrospective_loss_paths, research_evidence_gate)
    benchmark_quality=benchmark_coverage(f,benchmark,signal_indices)
    grouped_study=grouped_vote_study(f,baseline,vote_matrix,veto_matrix,
                                     formation_start=start,cut=cut,stop=stop,cost_bps=cfg.cost_bps)
    loss_path_analysis=retrospective_loss_paths(f,after.get('trade_ledger',[]),risk_audit)
    evidence_gate=research_evidence_gate(baseline_trades=after.get('completed_trades',0),
                                         ai_model=ai,market_audit=benchmark_quality)

    from nova_v54_accuracy import analyze_accuracy
    accuracy=analyze_accuracy(f,cfg,source=source,benchmark=benchmark,known_signals=baseline,known_votes=vote_matrix,known_vetos=veto_matrix)
    return {**base,'status':'READY','observation_count':len(signal_indices),'v54_accuracy':accuracy,
            'trade_diagnostics':{'baseline':after.get('trade_ledger',[]),
                                 'five_core':core.get('trade_ledger',[]),
                                 'ai_model':ai.get('research_strategy',{}).get('trade_ledger',[])},
            'trade_diagnostics_note':'가상 시가·종가 체결과 비용을 가정한 거래별 추적. 신호 시점 이후 정보를 신호 계산에 사용하지 않음. 체결/실제 추천 기록 아님.',
            'formation':{'start':_day(f.index[start]),'end':_day(f.index[cut-1]),'baseline':before},
            'validation':{'start':_day(f.index[cut]),'end':_day(f.index[stop-1]),
                          'baseline':after,'five_core':core,'ai_model':ai},
            'role_ablations':roles,
            'risk_audit':risk_audit,
            'v53':{'market_data_audit':benchmark_quality,'grouped_vote_shadow':grouped_study,
                   'retrospective_loss_paths':loss_path_analysis,'research_evidence_gate':evidence_gate,
                   'source_warning':'현재 KIS 조회 과거 지수는 당시 게시시각 검증 자료가 아님',
                   'research_only':True,'modifies_live_signal':False,'writes_observation_database':False},
            'aligned_backtest_comparison':aligned,
            'recommendation':'성과가 더 좋아 보여도 채택하지 않습니다. 향후 독립 기간·다수 종목·실제 관찰 기록에서 재검증이 필요합니다.',
            'bias_warnings':['현재 수정주가/상장폐지 표본 누락 가능성','실제 당시 재무·시장정보 공개시점 미검증',
                             '가상 시가 체결/미체결·세금·유동성 충격 미반영',
                             '검증 전략 다수 비교에 따른 선택 편향','거래일별 결과의 중첩·표본 종속성']}
