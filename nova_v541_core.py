"""Fixed, research-only 5-Core hypotheses. Never changes live signals.
Regime labels and filters use only signal-date stock bars and prior index bars.
"""
from __future__ import annotations
import math
import numpy as np
import pandas as pd
from core_comparison import _core_at, _reference, _day
from nova_v5_research import simulate_long_only, HORIZON

from nova_v550_research import NAMES as V550_NAMES, candidate_flags, change_metrics, historical_probability

from nova_v552_research import NAMES as V552_NAMES, select as select552, signal_audit

VERSION='5.4.1'
NAMES={'core':'기존 5-Core','strength':'5-Core + 추세 강도','adaptive':'5-Core Adaptive'}
STRATEGIES={**NAMES,'hybrid':'5-Core Hybrid'}
REGIMES={'strong_up':'강한 상승','mild_up':'완만한 상승','range':'횡보·전환','down':'하락'}
MARKET_REGIMES={'up':'지수 상승','range':'지수 횡보·전환','down':'지수 하락'}
FACTORS=('market','trend','strength','entry','risk')
CANDIDATES={'rs_required':'상대강도 필수','entry_wider':'눌림 진입 완화','risk_chase':'추격 한도 3 ATR','transition_soft':'Hybrid 전환 완화',**V550_NAMES,**V552_NAMES}

def indicators(f):
    h,l,c=(f[k].astype(float) for k in ('High','Low','Close'))
    up=h.diff(); down=-l.diff()
    plus=up.where((up>down)&(up>0),0.); minus=down.where((down>up)&(down>0),0.)
    tr=pd.concat([h-l,(h-c.shift()).abs(),(l-c.shift()).abs()],axis=1).max(axis=1)
    smooth=lambda s:s.ewm(alpha=1/14,adjust=False,min_periods=14).mean()
    den=smooth(tr).replace(0,np.nan)
    p=100*smooth(plus)/den; m=100*smooth(minus)/den
    dx=(100*(p-m).abs()/(p+m).replace(0,np.nan)).where((p+m)!=0,0.)
    width=(f.bb_hi-f.bb_lo)/((f.bb_hi+f.bb_lo)/2).replace(0,np.nan)
    return pd.DataFrame({'adx':smooth(dx),'plus_di':p,'minus_di':m,
      'slope':f.ema20/f.ema20.shift(5)-1,'width':width,'width_median':width.shift(1).rolling(60,min_periods=60).median()},index=f.index)

def _rule(checks, omit=None):
    # Mandatory gate removal bypasses it; removal of an OR input disables that branch.
    z=dict(checks)
    if omit=='strength': z['strength']=False
    if omit=='entry': z['entry']=False
    if omit in ('market','trend','risk'): z[omit]=True
    return bool(z['market'] and z['trend'] and z['risk'] and (z['strength'] or z['entry']))

def point(f,i,market,ind=None):
    base=_core_at(f,i,market)
    if base['status']!='READY':return {'status':'UNAVAILABLE','reason':'시장·종목 데이터 부족'}
    ind=ind if ind is not None else indicators(f)
    x=ind.iloc[i]; r=f.iloc[i]; prev=f.iloc[i-1]
    if not np.isfinite(x.to_numpy(dtype=float)).all():return {'status':'UNAVAILABLE','reason':'ADX/밴드폭 형성 자료 부족'}
    z={a['key']:a['pass'] for a in base['checks']}
    if not all(math.isfinite(float(r[k])) for k in ('Close','Low','ema20','ema50','atr','vol_ratio','p_high20')):return {'status':'UNAVAILABLE','reason':'진입 자료 부족'}
    adx=float(x.adx); slope=float(x.slope); directional=x.plus_di>x.minus_di
    # The benchmark regime is a separate label, using only prior completed index sessions.
    index=np.array([market[_day(t)] for t in f.index[i-51:i]],float)
    ma20,ma50=index[-20:].mean(),index[-50:].mean(); ir=index[-1]/index[-21]-1
    market_regime='up' if index[-1]>ma20>ma50 and ir>0 else ('down' if index[-1]<ma20<ma50 and ir<0 else 'range')
    compressed=x.width<=x.width_median*.8
    if r.Close<r.ema50 and slope<0: regime='down'
    elif z['trend'] and directional and slope>0 and adx>=25 and not compressed:regime='strong_up'
    elif z['trend'] and directional and slope>0 and adx>=20 and not compressed:regime='mild_up'
    else:regime='range'
    breakout=bool(r.Close>r.p_high20 and r.vol_ratio>=1.5)
    pullback=bool(r.Low<=r.ema20+.45*r.atr and r.Close>=r.ema20 and r.Close>prev.Close and r.vol_ratio>=.85)
    # Strong trend requires entry; mild trend requires pullback; range requires
    # actual volume breakout and upward direction. Down regime never buys.
    buy=base['signal']=='BUY'
    strong=buy and adx>=25 and directional and slope>0
    adaptive=buy and ((regime=='strong_up' and z['entry']) or (regime=='mild_up' and pullback) or (regime=='range' and breakout and directional and slope>0))
    # Fixed hypothesis after v5.4.1 review; no symbol-specific optimization.
    transition=bool(directional and slope>0 and r.Close>=r.ema20)
    subregime=('up_transition' if transition else 'sideways') if regime=='range' else regime
    hybrid=buy and (regime in ('strong_up','mild_up') or (regime=='range' and transition and (strong or adaptive)))
    grade=('A' if hybrid and strong else 'B' if hybrid else 'HOLD')
    # Fixed isolated hypotheses; never bypass positive volume or volatility limits.
    wider_entry=z['entry'] or (r.Low<=r.ema20+.75*r.atr and r.Close>=r.ema20 and r.Close>prev.Close and r.vol_ratio>=.85)
    relaxed_risk=bool(.003<=float(r.atr)/float(r.Close)<=.08 and float(r.Volume)>0 and r.Close<=r.ema20+3*r.atr)
    candidates={'rs_required':buy and z['strength'],
      'entry_wider':z['market'] and z['trend'] and z['risk'] and (z['strength'] or wider_entry),
      'risk_chase':z['market'] and z['trend'] and relaxed_risk and (z['strength'] or z['entry']),
      'transition_soft':buy and (regime in ('strong_up','mild_up') or (regime=='range' and transition and (strong or adaptive or (z['strength'] and z['entry']))))}
    candidates.update(candidate_flags(base_buy=buy,regime=regime,market_regime=market_regime,
        market_vol=float(np.std(np.diff(index[-21:])/index[-21:-1])),checks=z,close=float(r.Close),
        ema20=float(r.ema20),ema50=float(r.ema50),atr=float(r.atr),volume_ratio=float(r.vol_ratio),
        rsi=float(r.rsi),adx=adx,previous_adx=float(ind.iloc[i-1].adx),directional=bool(directional),
        slope=slope,previous_slope=float(ind.iloc[i-1].slope),breakout=breakout,rel_strength=float(r.ret20)-ir))
    extra,route=select552(candidates=candidates,core=buy,subregime=subregime,market_regime=market_regime,risk=z['risk'],market_gate=z['market'])
    candidates.update(extra)
    candidates={k:'BUY' if v else 'HOLD' for k,v in candidates.items()}

    return {'status':'READY','day':_day(f.index[i]),'regime':regime,'regime_label':REGIMES[regime],'market_regime':market_regime,'market_regime_label':MARKET_REGIMES[market_regime],
      'confidence_state':'UNVALIDATED','confidence_label':'검증 대기 · 미래 성과 미입증','adx':round(adx,3),'plus_di':round(float(x.plus_di),3),'minus_di':round(float(x.minus_di),3),'ema20_slope_pct':round(slope*100,3),
      'bandwidth_pct':round(float(x.width)*100,3),'compressed':bool(compressed),'breakout':breakout,'pullback':pullback,
      'subregime':subregime,'v552_route':route,'hybrid_grade':grade,'hybrid_grade_note':'규칙 기반 등급 · 확률 아님','checks':base['checks'],'signals':{'core':'BUY' if buy else 'HOLD','strength':'BUY' if strong else 'HOLD','adaptive':'BUY' if adaptive else 'HOLD'},
      'hybrid_signal':'BUY' if hybrid else 'HOLD','candidates':candidates,'ablations':{k:'BUY' if _rule(z,k) else 'HOLD' for k in FACTORS},'research_only':True}

def latest(f,benchmark):
    market,reason=_reference(benchmark)
    if market is None:return {'status':'UNAVAILABLE','reason':reason}
    return point(f,len(f)-1,market)

def study(f,ids,rows,market,cost):
    from nova_v54_accuracy import opportunity_metrics
    base={'status':'UNAVAILABLE','strategy_adoption':'HOLD','research_only':True,'historical_session_verified':False}
    if market is None or not ids:return {**base,'reason':'공통 시장지수 또는 평가 시점 부족'}
    ind=indicators(f); pts={i:point(f,i,market,ind) for i in ids}
    from nova_v573_ml import feature_vector
    def research_features(i):
        try:return feature_vector(f,i,market,ind)
        except (ValueError, TypeError, KeyError, AttributeError, IndexError, OverflowError):return None
    if any(x['status']!='READY' for x in pts.values()):return {**base,'reason':'ADX 또는 시장 자료 결측: 공통 비교 보류'}
    def report(s,subset=ids):
        if not subset:return {'status':'UNAVAILABLE','reason':'해당 국면 표본 없음'}
        sig={i:s[i] for i in subset}
        p=simulate_long_only(f,sig,ids[0],len(f),cost,include_trades=True)
        stress=simulate_long_only(f,sig,ids[0],len(f),cost*2)
        return {'status':p['status'],'portfolio':p,'opportunities':opportunity_metrics(rows,subset,sig),'stress_portfolio':stress}
    signals={k:{i:pts[i]['signals'][k] for i in ids} for k in NAMES}
    signals['hybrid']={i:pts[i]['hybrid_signal'] for i in ids}
    strategies={k:report(s) for k,s in signals.items()}
    regimes={k:{'samples':len(subset),'strategies':{j:report(s,subset) for j,s in signals.items()}} for k in REGIMES for subset in [[i for i in ids if pts[i]['regime']==k]]}
    market_regimes={k:{'samples':len(subset),'strategies':{j:report(s,subset) for j,s in signals.items()}} for k in MARKET_REGIMES for subset in [[i for i in ids if pts[i]['market_regime']==k]]}
    subregimes={k:{'samples':len(subset),'strategies':{j:report(s,subset) for j,s in signals.items()}} for k in ('sideways','up_transition') for subset in [[i for i in ids if pts[i]['subregime']==k]]}
    grades={k:opportunity_metrics(rows,ids,{i:'BUY' if pts[i]['hybrid_grade']==k else 'HOLD' for i in ids}) for k in ('A','B')}
    candidates={k:report({i:pts[i]['candidates'][k] for i in ids}) for k in CANDIDATES}
    ablations={k:report({i:pts[i]['ablations'][k] for i in ids}) for k in FACTORS}
    # Compare rule removals on same opportunities; no calibration or auto selection.
    base_s=signals['core']; changes={}
    for k,s in signals.items():
        removed=[i for i in ids if base_s[i]=='BUY' and s[i]!='BUY'];added=[i for i in ids if base_s[i]!='BUY' and s[i]=='BUY']
        changes[k]={'blocked_negative':sum(not rows[i][1] for i in removed),'blocked_positive':sum(rows[i][1] for i in removed),'added_signals':len(added)}
    return {**base,'status':'READY','start':_day(f.index[ids[0]]),'end':_day(f.index[ids[-1]]),'last_outcome_date':_day(f.index[-1]),'cost_bps':cost,'samples':len(ids),'periods':[{'start':_day(f.index[chunk[0]]),'end':_day(f.index[chunk[-1]]),'samples':len(chunk),'opportunities':{k:opportunity_metrics(rows,chunk,s) for k,s in signals.items()}} for off in range(0,len(ids),40) for chunk in [ids[off:off+40]]],'strategies':strategies,'regimes':regimes,'market_regimes':market_regimes,'subregimes':subregimes,'grades':grades,'candidates':candidates,'ablations':ablations,'changes':changes,
      'v550_probability':historical_probability(pts,rows,HORIZON),
      'v552_overlap':signal_audit([{**{k:s[i] for k,s in signals.items()},**pts[i]['candidates']} for i in ids]),
      'v552_routes':{k:sum(p['v552_route']==k for p in pts.values()) for k in ('core','rs_required','rs_hybrid','risk_veto','unknown')},
      'candidate_changes':{k:change_metrics({i:rows[i][1] for i in ids},base_s,{i:pts[i]['candidates'][k] for i in ids}) for k in CANDIDATES},
      'v550_regimes':{k:{'samples':len(subset),'strategies':{j:report({i:pts[i]['candidates'][j] for i in ids},subset) for j in (*V550_NAMES,*V552_NAMES)}} for k in ('sideways','up_transition','strong_up','mild_up','down') for subset in [[i for i in ids if pts[i]['subregime']==k]]},
      '_replay':{'dates':[_day(t) for t in f.index[ids[0]:ids[-1]+HORIZON+1]],'open':[float(v) for v in f.Open.iloc[ids[0]:ids[-1]+HORIZON+1]],'close':[float(v) for v in f.Close.iloc[ids[0]:ids[-1]+HORIZON+1]],'points':{_day(f.index[i]):{'signals':{k:s[i] for k,s in signals.items()},'ablations':pts[i]['ablations'],'candidates':pts[i]['candidates'],'regime':pts[i]['regime'],'subregime':pts[i]['subregime'],'grade':pts[i]['hybrid_grade'],'route':pts[i]['v552_route'],'market_regime':pts[i]['market_regime'],'label':int(rows[i][1]),'net':float(rows[i][2]),'ml_features':research_features(i)} for i in ids}},
      'limitations':'국면별 결과는 해당 국면에서만 진입한 별도 가상자산이며 합산 불가. 제거 실험은 연구용이며 위험 제한 제거도 실전 미적용. 고정 기준의 사후 연구, 과거 세션 미검증, 중첩 기회·생존편향, 전향 검증 필요.'}

def batch_summary(rows):
    valid=[r for r in rows if r.get('v54_accuracy',{}).get('core_focus',{}).get('status')=='READY']
    groups={}
    for r in valid:
        d=r['v54_accuracy']['core_focus']; key=(d['start'],d['end'],d['last_outcome_date'],d['cost_bps'])
        groups.setdefault(key,[]).append(d)
    out=[]
    for key,ds in groups.items():
        stats={}
        for k in STRATEGIES:
            p=[d['strategies'].get(k,{}).get('portfolio',{'status':'UNAVAILABLE','completed_trades':0}) for d in ds]; active=[v for v in p if v['status']=='READY' and v['completed_trades']>0]
            stats[k]={'stocks_with_trades':len(active),'trades':sum(v['completed_trades'] for v in active),'positive_stocks':sum(v['portfolio_return_pct']>0 for v in active),'mean_return_pct':round(float(np.mean([v['portfolio_return_pct'] for v in active])),3) if active else None,
                      'all_stocks_cash_inclusive_mean_pct':round(float(np.mean([v['portfolio_return_pct'] for v in p])),3) if all(v['status']=='READY' for v in p) else None}
        out.append({'start':key[0],'end':key[1],'last_outcome_date':key[2],'cost_bps':key[3],'stocks':len(ds),'strategies':stats})
    return {'ready_stocks':len(valid),'requested_stocks':len(rows),'groups':out,'strategy_adoption':'HOLD'}
