"""Minimal manual market-scan projection. Identical gates/inputs; no chart research or I/O."""
from nova_v584_pattern_scan import from_features

def project(f,cfg,*,market,source,stale,asof,financial_gate,finance_note,scan_freshness,profile,decision,quality,stats,policy,safe_float):
    if profile not in ('full','pattern') or cfg.mode!='스윙' or source!='kis':
        raise ValueError('Invalid projection')
    try:
        pattern=from_features(f,cfg,asof=asof,source=source,stale=stale,demo=market.synthetic,financial_gate=financial_gate)
    except Exception:
        pattern={'status':'UNAVAILABLE','reason':'PATTERN_INPUT_ERROR'}
    out={'symbol':market.symbol,'mode':cfg.mode,'source':source,'provider':market.provider,
         'is_demo':market.synthetic,'is_stale':stale,'scan_freshness':scan_freshness,
         'asof_evaluation_only':True,'financial_gate':financial_gate,
         'signal_pattern_research':pattern,'last_completed_bar':f.index[-1].isoformat()}
    if profile=='pattern':
        return out
    d=decision(f,cfg)
    q=quality(f,cfg,raw_signal=d.signal)
    # Keep independent recent validation unchanged: it is a strict-rank input.
    st=stats(f,cfg)
    p=policy(q['strict_signal'],mode=cfg.mode,symbol=market.symbol,financial_gate=financial_gate,
             finance_reason=finance_note,stale=stale,is_demo=market.synthetic)
    out.update(last_completed_bar=d.time,quality_gate=q,signal_performance=st,
               signal_is_current=p['signal_is_current'],final_actionable_signal=p['final_actionable_signal'],
               reference=safe_float(d.reference),long_stop_reference=safe_float(d.stop),
               long_target_reference=safe_float(d.target),long_risk_reward=safe_float(d.risk_reward))
    return out
