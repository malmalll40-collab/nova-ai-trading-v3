"""NOVA v5.3.1: research-only cross-provider price-basis audit.

Never mix, rescale or overwrite price histories. Comparisons here are not
point-in-time verification of adjustments, corporate actions or executability.
"""
from __future__ import annotations
from datetime import datetime, timezone
from statistics import median
import math
import pandas as pd

_FIELDS=('Open','High','Low','Close')


def _daily_records(frame:pd.DataFrame|None)->dict[str,dict]:
    if frame is None or frame.empty:return {}
    out={}
    for stamp, row in frame.sort_index().iterrows():
        try:
            d=pd.Timestamp(stamp).date().isoformat()
            values={k:float(row[k]) for k in _FIELDS}
            if any(not math.isfinite(v) or v<=0 for v in values.values()):continue
            if 'Volume' in row and pd.notna(row['Volume']):values['Volume']=float(row['Volume'])
            out[d]=values
        except (KeyError,TypeError,ValueError,OverflowError):continue
    return out


def _relative_pct(a:float,b:float)->float:
    return 100.0*(a/b-1.0)


def _stats(kis, yahoo, common:list[str])->dict:
    errors={f:[] for f in _FIELDS}
    mismatched=[]
    for d in common:
        diffs={f:abs(_relative_pct(kis[d][f],yahoo[d][f])) for f in _FIELDS}
        for f in _FIELDS:errors[f].append(diffs[f])
        if max(diffs.values())>0.5:mismatched.append(d)
    return {'median_abs_pct':{f:round(median(errors[f]),4) if errors[f] else None for f in _FIELDS},
            'max_abs_pct':{f:round(max(errors[f]),4) if errors[f] else None for f in _FIELDS},
            'days_any_ohlc_over_0p5pct':len(mismatched),'sample_difference_dates':mismatched[:8]}


def audit(kis_frame:pd.DataFrame|None, yahoo_reference:pd.DataFrame|None,
          yahoo_adjusted:pd.DataFrame|None, *, symbol:str,
          failures:dict[str,str]|None=None, assessed_at:str|None=None)->dict:
    failures=failures or {}
    kis=_daily_records(kis_frame)
    raw=_daily_records(yahoo_reference)
    adj=_daily_records(yahoo_adjusted)
    common=sorted(set(kis)&set(raw)&set(adj))
    kis_only=sorted(set(kis)-set(raw))
    yahoo_only=sorted(set(raw)-set(kis))
    result={'version':'5.3.1','symbol':symbol,'research_only':True,
            'places_orders':False,'modifies_live_signal':False,
            'price_basis_verified':False,'point_in_time_verified':False,
            'assessed_at':assessed_at or datetime.now(timezone.utc).isoformat(timespec='seconds'),
            'providers':{
                'kis':{'status': 'READY' if kis else 'UNAVAILABLE', 'rows':len(kis),
                       'last':max(kis) if kis else None,'basis':'수정주가 (KIS FID_ORG_ADJ_PRC=0)',
                       'error_code':failures.get('kis')},
                'yahoo':{'status':'READY' if raw and adj else 'UNAVAILABLE',
                         'rows':len(raw),'last':max(raw) if raw else None,
                         'basis':'auto_adjust=False OHLC + Adj Close 비율로 환산한 조정 OHLC',
                         'error_code':failures.get('yahoo')}},
            'overlap':{'count':len(common),'first':common[0] if common else None,
                       'last':common[-1] if common else None,
                       'missing_from_yahoo_count':len(kis_only),'missing_from_yahoo_dates':kis_only[-12:],
                       'missing_from_kis_count':len(yahoo_only),'missing_from_kis_dates':yahoo_only[-12:]},
            'comparison':{},'sample_days':[],
            'warnings':[
                'KIS 수정주가와 Yahoo 보정 OHLC는 배당·권리·분할의 처리 기준이 다를 수 있습니다.',
                'Yahoo auto_adjust=False OHLC도 거래소 당시 원시 체결가격을 보장하지 않습니다.',
                '가격 일치만으로 공시 당시 공개 시점·수정주가의 역사적 재현성은 입증되지 않습니다.',
                '서로 다른 공급원 가격을 이어 붙이거나 자동 비율 보정해 투자 신호에 사용하지 않습니다.'
            ]}
    if not kis or not raw or not adj:
        result['status']='UNAVAILABLE';result['decision']='SOURCE_UNAVAILABLE'
        result['reason']='하나 이상의 시세 공급원 데이터를 확인할 수 없습니다.'
        return result
    if len(common)<20:
        result['status']='INSUFFICIENT';result['decision']='INSUFFICIENT_OVERLAP'
        result['reason']=f'동일 완료 거래일 {len(common)}개 · 진단 최소 20개 필요'
        return result
    result['comparison']={'kis_vs_yahoo_reference':_stats(kis,raw,common),
                          'kis_vs_yahoo_adjusted':_stats(kis,adj,common)}
    # Show the *worst* discrepancies first (most actionable for investigating a basis shift).
    ranked=sorted(common,key=lambda d:max(abs(_relative_pct(kis[d][f],raw[d][f])) for f in _FIELDS),reverse=True)
    dates=ranked[:6]
    # Always make the freshest two common dates easy to verify as well.
    for d in common[-2:]:
        if d not in dates:dates.append(d)
    result['sample_days']=[{
        'date':d,
        'kis_open':round(kis[d]['Open'],2),'kis_close':round(kis[d]['Close'],2),
        'yahoo_reference_open':round(raw[d]['Open'],2),
        'yahoo_reference_close':round(raw[d]['Close'],2),
        'yahoo_adjusted_open':round(adj[d]['Open'],2),
        'yahoo_adjusted_close':round(adj[d]['Close'],2),
        'open_diff_reference_pct':round(_relative_pct(kis[d]['Open'],raw[d]['Open']),3),
        'close_diff_reference_pct':round(_relative_pct(kis[d]['Close'],raw[d]['Close']),3),
        'close_diff_adjusted_pct':round(_relative_pct(kis[d]['Close'],adj[d]['Close']),3),
        } for d in dates]
    ref=result['comparison']['kis_vs_yahoo_reference']
    adjusted=result['comparison']['kis_vs_yahoo_adjusted']
    diverged=(ref['days_any_ohlc_over_0p5pct']>0 or adjusted['days_any_ohlc_over_0p5pct']>0)
    result['status']='COMPARED'
    result['decision']='PRICE_BASIS_DIFFERENCE' if diverged else 'NO_LARGE_DIFFERENCE_OBSERVED'
    result['reason']=('일부 동일 날짜의 OHLC 가격 차이가 0.5%를 초과했습니다. 공급원별 백테스트를 직접 비교하지 마세요.'
                      if diverged else
                      '조회 가능한 구간에서 0.5%를 넘는 OHLC 차이는 발견되지 않았지만 당시 가격 이력 일치가 검증된 것은 아닙니다.')
    return result


def yahoo_price_bases(history:pd.DataFrame)->tuple[pd.DataFrame,pd.DataFrame]:
    """From one Yahoo auto_adjust=False response, derive its auto-adjusted-style OHLC.

    Do not assume volume / trades are rescaled: this is only a price diagnostic.
    """
    if history is None or history.empty:raise ValueError('YAHOO_HISTORY_EMPTY')
    if not all(f in history for f in _FIELDS) or 'Adj Close' not in history:
        raise ValueError('YAHOO_ADJ_CLOSE_MISSING')
    raw=history.loc[:,list(_FIELDS)+(['Volume'] if 'Volume' in history else [])].copy()
    close=pd.to_numeric(history['Close'],errors='coerce')
    adj_close=pd.to_numeric(history['Adj Close'],errors='coerce')
    factor=(adj_close/close.where(close>0)).replace([math.inf,-math.inf],float('nan'))
    adjusted=raw.copy()
    for field in _FIELDS:adjusted[field]=pd.to_numeric(raw[field],errors='coerce')*factor
    return raw, adjusted
