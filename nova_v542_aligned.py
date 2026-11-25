"""Replay fixed 5-Core rules on exactly the same evaluation and outcome dates.
No retuning, no stock-specific selection, no portfolio aggregation.
"""
import numpy as np
import pandas as pd
from nova_v541_core import STRATEGIES, FACTORS, REGIMES, CANDIDATES
from nova_v5_research import HORIZON,simulate_long_only
from nova_v54_accuracy import opportunity_metrics
from nova_v550_research import NAMES as V550_NAMES, change_metrics
from nova_v552_research import NAMES as V552_NAMES, signal_audit
from nova_v561_hierarchical import ACTIVE, ARCHIVE, change_value, replay_panel, sector_groups, bucket, nonoverlap_changes
from collections import Counter

def aligned_summary(rows):
    base={'status':'UNAVAILABLE','requested_stocks':len(rows),'strategy_adoption':'HOLD','research_only':True}
    reps=[r.get('v54_accuracy',{}).get('core_focus',{}) for r in rows]
    if len(rows)<2 or any(x.get('status')!='READY' or not x.get('_replay') for x in reps):return {**base,'reason':'전체 요청 종목의 공통 검증 자료 필요'}
    if len({x['cost_bps'] for x in reps})!=1:return {**base,'reason':'비용 불일치'}
    common=sorted(set.intersection(*(set(x['_replay']['points']) for x in reps)))
    if len(common)<40:return {**base,'reason':'공통 평가 40시점 미만'}
    # Do not silently bridge internal missing dates, or use different future horizons.
    frames=[]
    for x in reps:
        r=x['_replay']; dates=r['dates']; lo=dates.index(common[0]); hi=dates.index(common[-1]); span=dates[lo:hi+HORIZON+1]
        if dates[lo:hi+1]!=common or len(span)!=len(common)+HORIZON:return {**base,'reason':'공통 구간 내부 누락 또는 청산일 부족'}
        frames.append((span,pd.DataFrame({'Open':r['open'][lo:hi+HORIZON+1],'Close':r['close'][lo:hi+HORIZON+1]},index=pd.to_datetime(span)),[r['points'][d] for d in common]))
    if any(a[0]!=frames[0][0] for a in frames):return {**base,'reason':'거래일 또는 청산일 불일치'}
    cost=reps[0]['cost_bps'];ids=list(range(len(common)))
    def aggregate(items):
        active=[p for p,o,stress in items if p.get('completed_trades',0)>0]
        if any(p['status']!='READY' or stress['status']!='READY' for p,o,stress in items):return {'status':'UNAVAILABLE'}
        good=sum(o['positive_signals'] for p,o,stress in items); n=sum(o['buy_signals'] for p,o,stress in items)
        return {'status':'READY','stocks_with_trades':len(active),'positive_stocks':sum(p['portfolio_return_pct']>0 for p in active),'trades':sum(p['completed_trades'] for p in active),'mean_pct':round(float(np.mean([p['portfolio_return_pct'] for p,o,stress in items])),3),'mean_mdd_pct':round(float(np.mean([p['portfolio_mdd_pct'] for p,o,stress in items])),3),'stress_mean_pct':round(float(np.mean([stress['portfolio_return_pct'] for p,o,stress in items])),3),'buy_signals':n,'precision_pct':round(100*good/n,2) if n else None,'missed_positive':sum(o['missed_positive_opportunities'] for p,o,stress in items)}
    def evaluate(key,kind='signals',regime=None):
        items=[]; per=[]; samples=0
        for row,(_,f,pts) in zip(rows,frames):
            subset=[i for i in ids if regime is None or pts[i]['regime']==regime or pts[i]['subregime']==regime]
            samples+=len(subset)
            sig={i:('BUY' if pts[i]['grade']==key else 'HOLD') if kind=='grades' else pts[i][kind][key] for i in subset};labels={i:(None,pts[i]['label'],pts[i]['net']) for i in ids}
            p=simulate_long_only(f,sig,0,len(f),cost);stress=simulate_long_only(f,sig,0,len(f),cost*2)
            o=opportunity_metrics(labels,ids,sig) if regime is None else (opportunity_metrics(labels,subset,sig) if subset else {'buy_signals':0,'positive_signals':0,'missed_positive_opportunities':0})
            items.append((p,o,stress));per.append({'symbol':row['symbol'],'return_pct':p.get('portfolio_return_pct'),'mdd_pct':p.get('portfolio_mdd_pct'),'trades':p.get('completed_trades'),'precision_pct':o.get('precision_pct')})
        return {**aggregate(items),'per_stock':per,'evaluated_stock_days':samples}
    def changes(k):
        parts=[change_metrics({i:p['label'] for i,p in enumerate(pts)}, {i:p['signals']['core'] for i,p in enumerate(pts)}, {i:p['candidates'][k] for i,p in enumerate(pts)}) for _,_,pts in frames]
        return {name:sum(p[name] for p in parts) for name in parts[0]}
    available=[k for k in CANDIDATES if all(k in p.get('candidates',{}) for _,_,pts in frames for p in pts)]
    # Research diagnostic: sum signal opportunity returns, not actual P&L.
    all_points=[p for _,_,pts in frames for p in pts]
    symbols=[r['symbol'] for r in rows]
    decisions=replay_panel(frames,symbols,HORIZON)
    def sleeve(key,decision_rows=None):
        paths=[]; stresses=[]; count=0; per=[]
        for row,(_,f,pts),d in zip(rows,frames,decisions if decision_rows is None else decision_rows):
            sig={i:(d[i]['signal'] if key in ('unified','unified_sized','unified_holdout') else (pts[i]['signals'] if key in STRATEGIES else pts[i]['candidates'])[key]) for i in ids}
            caps={i:d[i]['research_position_pct']/100 for i in ids} if key=='unified_sized' else None
            p=simulate_long_only(f,sig,0,len(f),cost,include_equity=True,research_caps=caps)
            stress=simulate_long_only(f,sig,0,len(f),cost*2,include_equity=True,research_caps=caps)
            if p['status']!='READY' or stress['status']!='READY':return {'status':'UNAVAILABLE'}
            stresses.append(stress['equity_curve']);paths.append(p['equity_curve']);count+=p['completed_trades'];per.append({'symbol':row['symbol'],'return_pct':p['portfolio_return_pct'],'trades':p['completed_trades']})
        values=np.mean(paths,axis=0);peak=np.maximum.accumulate(values)
        return {'status':'READY','return_pct':round(100*(values[-1]-1),4),'mdd_pct':round(100*float(np.min(values/peak-1)),4),'trades':count,'stress_return_pct':round(100*(float(np.mean(stresses,axis=0)[-1])-1),4),'per_stock':per}
    portfolio={k:sleeve(k) for k in ('core',*tuple(k for k in ACTIVE if k!='core' and (k in STRATEGIES or k in available)),'unified','unified_sized')}
    holdout_decisions=replay_panel(frames,symbols,HORIZON,holdout_bucket=True)
    portfolio['unified_holdout']=sleeve('unified_holdout',holdout_decisions)
    groups=sector_groups([r['symbol'] for r in rows]); semi=set(groups.get('KODEX 반도체 ETF',[]))
    for symbol in symbols:
        groups.setdefault('연구 분류 · '+(bucket(symbol) or '미분류'),[]).append(symbol)
    groups['연구 분류 반도체 제외']=[s for s in symbols if bucket(s)!='반도체·전자부품']
    groups['반도체 ETF 등록 종목 제외']=[r['symbol'] for r in rows if r['symbol'] not in semi]
    by_group={g:{k:{'stocks':len(sub),'mean_pct':round(float(np.mean([x['return_pct'] for x in sub])),4) if sub else None} for k,v in portfolio.items() for sub in [[x for x in v.get('per_stock',[]) if x['symbol'] in syms]]} for g,syms in groups.items()}
    # Compare probabilities only at scored points; avoid treating no forecast as a correct HOLD.
    scored=[(d,p) for (_,_,pts),ds in zip(frames,decisions) for d,p in zip(ds,pts) if d['probability'] is not None]
    unified={'model_version':'5.6.1','diagnostics':dict(Counter(d['state'] for ds in decisions for d in ds)),'routes':dict(Counter(d['route'] for ds in decisions for d in ds if d['route'])),'size_research':dict(Counter(str(d['research_position_pct']) for ds in decisions for d in ds)),'nonoverlap_candidate_value':nonoverlap_changes(frames,symbols,available,HORIZON),'nonoverlap_factor_value':nonoverlap_changes(frames,symbols,FACTORS,HORIZON,kind='ablations'),'holdout_scored':sum(d['probability'] is not None for ds in holdout_decisions for d in ds),'holdout_diagnostics':dict(Counter(d['state'] for ds in holdout_decisions for d in ds)),'active':ACTIVE,'archive':ARCHIVE,'historical_only':True,'strategy_adoption':'HOLD','samples':len(all_points),'scored':len(scored),'buy_signals':sum(d['signal']=='BUY' for ds in decisions for d in ds),'brier':round(float(np.mean([(d['probability']-p['label'])**2 for d,p in scored])),6) if scored else None,'naive_brier':round(float(np.mean([(d['naive_probability']-p['label'])**2 for d,p in scored])),6) if scored else None,'expected_return_mae_pct':round(float(np.mean([abs(d['expected_net_pct']-100*p['net']) for d,p in scored])),4) if scored else None,'portfolio':portfolio,'by_group':by_group,'candidate_value':{k:change_value(all_points,'candidates',k) for k in available},'factor_value':{k:change_value(all_points,'ablations',k) for k in FACTORS},'limitations':'사후 재사용 자료. 연구 분류는 고정 가설이며 공식 업종 아님. 업종 제외 학습은 같은 업종의 과거 표본만 제외하는 민감도 검증, 독립 미래 검증 아님. 비중 연구는 10/25% 한도 가상 체결. 고정 동일비중 독립 슬리브 합산 자산경로(MDD), 총자금 공유/재배분 없음. ETF 등록군과 고정 연구 분류군을 별도 표시. 기타 미분류 종목 임의 추정 금지. 신호 수익률 합은 중첩 가능하며 실제 원화 손익 아님.'}
    try:
        from nova_v570_accuracy import historical
        v570=historical(frames,symbols,HORIZON,cost)
        v570.update(status='READY',start=common[0],end=common[-1],outcome_end=frames[0][0][-1])
    except Exception:
        v570={'status':'UNAVAILABLE','reason':'v5.7.0 공통 구간 계산 실패','strategy_adoption':'HOLD'}
    try:
        from nova_v571_policy import historical as historical571
        v571=historical571(frames,symbols,HORIZON,cost)
        v571.update(start=common[0],end=common[-1],outcome_end=frames[0][0][-1])
    except Exception:
        v571={'status':'UNAVAILABLE','reason':'v5.7.1 공통 구간 계산 실패','strategy_adoption':'HOLD'}
    try:
        from nova_v572_calibration import historical as historical572
        v572=historical572(frames,symbols,HORIZON,cost)
        v572.update(start=common[0],end=common[-1],outcome_end=frames[0][0][-1])
    except Exception:
        v572={'status':'UNAVAILABLE','reason':'v5.7.2 공통 구간 계산 실패','strategy_adoption':'HOLD'}
    try:
        from nova_v573_ml import historical as historical573
        v573=historical573(frames,symbols,HORIZON,cost)
        v573.update(start=common[0],end=common[-1],outcome_end=frames[0][0][-1])
    except Exception:
        v573={'status':'UNAVAILABLE','reason':'v5.7.3 공통 구간 계산 실패','strategy_adoption':'HOLD'}
    return {**base,'v573_ml':v573,'v572_calibration':v572,'v571_policy':v571,'v570_accuracy':v570,'status':'READY','start':common[0],'end':common[-1],'outcome_end':frames[0][0][-1],'samples_per_stock':len(common),'cost_bps':cost,'strategies':{k:evaluate(k) for k in STRATEGIES},'ablations':{k:evaluate(k,'ablations') for k in FACTORS},'candidates':{k:evaluate(k,'candidates') for k in available},'candidate_changes':{k:changes(k) for k in available},'v561_unified':unified,'v552_overlap':signal_audit([{**p['signals'],**p['candidates']} for _,_,pts in frames for p in pts]),'v550_regimes':{r:{k:evaluate(k,'candidates',regime=r) for k in (*V550_NAMES,*V552_NAMES) if k in available} for r in ('sideways','up_transition')},'grades':{k:evaluate(k,'grades') for k in ('A','B')},'regimes':{r:{k:evaluate(k,regime=r) for k in STRATEGIES} for r in (*REGIMES,'sideways','up_transition')},'limitations':'종목별 독립 가상자산 평균(무거래 현금 포함), 포트폴리오 수익률 아님. 동일 날짜의 중첩 신호, 과거 정규장 범위 미검증, 사후 설계 연구.'}
