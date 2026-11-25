"""NOVA v4.4 high-precision swing BUY research gate (experimental, not trading advice).

Read-only evaluator: it may suppress a BUY from the unchanged v4.4 engine, but
it never creates a BUY when the original engine does not. No API/order calls.
Each outside input needs a historical `available_at` timestamp; unavailable,
stale, or future-published evidence is NOT treated as a positive signal.
All thresholds are exploratory hypotheses, NOT measured win-rate improvements.
"""
from __future__ import annotations

from datetime import date
from math import isfinite
from typing import Any

import numpy as np
import pandas as pd

from engine import Config, WARMUP, decide
from signal_quality import quality_gate

TZ = 'Asia/Seoul'
TECHNICAL_ROLE_GROUPS = {
    '추세': ('01', '02'),
    '모멘텀': ('03', '04', '06', '07'),
    '돌파·가격위치': ('05', '10'),
    '거래참여': ('08',),
    '역추세': ('09',),
}


def _dt(value: Any) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    if pd.isna(ts):
        raise ValueError('유효한 날짜/시간이 필요합니다.')
    return ts.tz_localize(TZ) if ts.tzinfo is None else ts.tz_convert(TZ)


def _date(value: Any) -> date:
    return _dt(value).date()


def _finite(value: Any) -> float:
    try:
        v = float(value)
        return v if isfinite(v) else float('nan')
    except (TypeError, ValueError):
        return float('nan')


def _check(code: str, name: str, state: str, detail: str) -> dict:
    return {'code':code, 'name':name, 'state':state, 'passed':state == 'PASS', 'detail':detail}


def _source_frame(frame: pd.DataFrame | None, required: tuple[str, ...],
                  current_day: date, decision_at: pd.Timestamp, window: int,
                  required_days: list[date] | None = None) -> tuple[pd.DataFrame | None, str]:
    """Only historical observations that were published before decision_at.

    A source cannot become usable just because future rows were included. Use
    original index dates (not a forward fill) and explicitly timestamp every row.
    """
    if frame is None or not isinstance(frame, pd.DataFrame) or frame.empty:
        return None, '외부 자료 미제공'
    cols = set(frame.columns)
    if not set(required + ('available_at',)).issubset(cols):
        return None, '필수 필드 또는 공개 시각 available_at 누락'
    if frame.index.has_duplicates:
        return None, '날짜 중복 데이터'
    try:
        parsed_dates = [_date(ix) for ix in frame.index]
        available = [_dt(x) for x in frame['available_at']]
    except (TypeError, ValueError, OverflowError) as exc:
        return None, f'날짜/공개 시각 오류: {exc}'
    d = frame.copy()
    d['_day'] = parsed_dates
    d['_available_at'] = available
    # A source cannot be backfilled using numbers published after this decision.
    d = d.loc[(d['_day'] <= current_day) & (d['_available_at'] <= decision_at)]
    d = d.sort_values('_day')
    if d.empty:
        return None, '판단 시각 이전에 공개된 자료 없음'
    if d['_day'].duplicated().any():
        return None, '동일 거래일 중복 자료'
    if d['_day'].iloc[-1] != current_day:
        return None, '판단 기준일 최신 자료 미도착'
    if len(d) < window:
        return None, f'유효한 역사 데이터 {window}거래일 이상 필요, 현재 {len(d)}일'
    if required_days is not None:
        if not set(required_days).issubset(set(d['_day'])):
            return None, '종목 거래일과 외부 데이터에 결측 거래일 존재 (값 대체 금지)'
        d = d.loc[d['_day'].isin(required_days)].copy()
    for col in required:
        values = pd.to_numeric(d[col], errors='coerce')
        if values.isna().any() or not np.isfinite(values.to_numpy(dtype=float)).all():
            return None, f'{col}에 유효하지 않은 값 존재'
        d[col] = values
    return d, ''


def _context_assessment(features: pd.DataFrame, i: int,
                        benchmark: pd.DataFrame | None, sector: pd.DataFrame | None,
                        flows: pd.DataFrame | None, decision_at: pd.Timestamp) -> list[dict]:
    day = _date(features.index[i]); p = features['Close'].astype(float)
    if i < 60:
        return [_check('market','시장·업종','UNAVAILABLE','60봉 미만'),
                _check('strength','상대강도','UNAVAILABLE','60봉 미만'),
                _check('flows','외국인·기관 수급','UNAVAILABLE','60봉 미만')]
    days61 = [_date(x) for x in features.index[i-60:i+1]]
    days20 = days61[-20:]
    bm, bm_error = _source_frame(benchmark, ('Close',), day, decision_at, 61, days61)
    sc, sc_error = _source_frame(sector, ('Close',), day, decision_at, 61, days61)
    # Investor histories may be published after the latest stock closing price;
    # accept at most one *completed stock session* lag as explicitly marked
    # research-only evidence. Never fabricate or forward-fill missing days.
    flow_required = ('foreign_net_value','institution_net_value')
    flow_unit = '원'
    if isinstance(flows, pd.DataFrame) and set(('foreign_net_shares','institution_net_shares')).issubset(flows.columns):
        flow_required = ('foreign_net_shares','institution_net_shares')
        flow_unit = '주(순매수 수량, 금액 아님)'
    current_flow_day = day
    required_flow_days = days20
    lag_sessions = 0
    if isinstance(flows, pd.DataFrame) and not flows.empty and 'available_at' in flows:
        try:
            dated = {_date(ix) for ix in flows.index}
            if day not in dated and i >= 20 and _date(features.index[i-1]) in dated:
                lag_sessions=1
                current_flow_day=_date(features.index[i-1])
                required_flow_days=[_date(t) for t in features.index[i-20:i]]
        except (TypeError,ValueError):
            pass
    ff, ff_error = _source_frame(flows, flow_required, current_flow_day, decision_at,
                                  20, required_flow_days)
    if bm is None or sc is None:
        why = '; '.join(x for x in [f'시장: {bm_error}' if bm_error else '시장지수 가격 연결됨', f'업종: {sc_error}' if sc_error else '업종 참고 지표 연결됨'] if x)
        market = _check('market','시장·업종','UNAVAILABLE',why)
        strength = _check('strength','시장·업종 상대강도','UNAVAILABLE',why)
    else:
        b = bm['Close'].to_numpy(dtype=float); s = sc['Close'].to_numpy(dtype=float)
        if (b[-61:] <= 0).any() or (s[-61:] <= 0).any() or (p.iloc[[i, i-20, i-60]] <= 0).any():
            market = _check('market','시장·업종','UNAVAILABLE','시장·업종 가격 오류')
            strength = _check('strength','시장·업종 상대강도','UNAVAILABLE','시장·업종 가격 오류')
        else:
            benchmark_trend = b[-1] > np.mean(b[-20:]) > np.mean(b[-50:])
            sector_trend = s[-1] > np.mean(s[-20:])
            sector_rs20 = s[-1]/s[-21] - b[-1]/b[-21]
            st20 = p.iloc[i]/p.iloc[i-20] - 1
            st60 = p.iloc[i]/p.iloc[i-60] - 1
            benchmark20 = b[-1]/b[-21]-1
            sector60 = s[-1]/s[-61]-1
            market = _check('market','시장·업종', 'PASS' if (benchmark_trend and sector_trend and sector_rs20 > 0) else 'FAIL',
                            f'시장 20일/50일 추세={benchmark_trend}, 업종 20일 추세={sector_trend}, 업종-시장 RS20={sector_rs20:+.2%}')
            strength = _check('strength','시장·업종 상대강도', 'PASS' if st20 > benchmark20 and st60 > sector60 else 'FAIL',
                              f'종목-시장 RS20={st20-benchmark20:+.2%}, 종목-업종 RS60={st60-sector60:+.2%}')
    if ff is None:
        flow = _check('flows','외국인·기관 수급','UNAVAILABLE',ff_error)
    else:
        foreign = ff[flow_required[0]].to_numpy(dtype=float)
        institution = ff[flow_required[1]].to_numpy(dtype=float)
        total = foreign + institution
        f5, i5 = float(foreign[-5:].sum()), float(institution[-5:].sum())
        flow5, flow20 = float(total[-5:].sum()), float(total[-20:].sum())
        flow = _check('flows','외국인·기관 수급','PASS' if flow5 > 0 and flow20 > 0 and (f5 > 0 or i5 > 0) else 'FAIL',
                      f'합계 순매수 5일={flow5:,.0f}, 20일={flow20:,.0f} {flow_unit} · 기준 {current_flow_day}'+(' (1거래일 지연)' if lag_sessions else '')+' · 과거 원천 공개 시각 미검증')
    return [market, strength, flow]


def _price_quality(features: pd.DataFrame, i: int) -> tuple[bool, str]:
    cols = ('Open','High','Low','Close','Volume')
    if not set(cols).issubset(features.columns):
        return False, 'OHLCV 필수 열 누락'
    sample = features.iloc[max(0,i-64):i+1]
    vals = sample[list(cols)].apply(pd.to_numeric, errors='coerce')
    if vals.isna().any().any() or not np.isfinite(vals.to_numpy(dtype=float)).all():
        return False, '최근 65봉 OHLCV 비정상 값'
    if (vals[list(cols[:4])] <= 0).any().any() or (vals['Volume'] <= 0).any():
        return False, '최근 65봉 0 이하 가격/거래량: 거래 정지 또는 데이터 오류 확인 필요'
    if (vals['High'] < vals[['Open','Close','Low']].max(axis=1)).any() or (vals['Low'] > vals[['Open','Close']].min(axis=1)).any():
        return False, '고가/저가 데이터 불일치'
    if sample.index.has_duplicates or not sample.index.is_monotonic_increasing:
        return False, '시세 인덱스 중복/역순'
    jumps = vals['Close'].pct_change().abs().dropna()
    if (jumps > .35).any():
        return False, '가격 급변(35% 초과): 액면분할/데이터 보정 검증 필요'
    return True, '최근 65봉 가격/거래량 기본 형식 검사 통과 (원천 교차검증 아님)'


def _pattern_assessment(features: pd.DataFrame, i: int, cfg: Config) -> tuple[list[dict],str|None]:
    r = features.iloc[i]; prev = features.iloc[i-1]
    p, atr = _finite(r['Close']), _finite(r['atr'])
    ema20, ema50 = _finite(r['ema20']), _finite(r['ema50'])
    rsi, hist = _finite(r['rsi']), _finite(r['macd']-r['macd_s'])
    vr = _finite(r['vol_ratio'])
    if not (isfinite(p) and isfinite(atr) and atr>0 and p>0):
        return [_check('technical','기술·진입조건','UNAVAILABLE','가격 또는 ATR 계산 실패')],None
    trend = p > ema20 > ema50 and ema50 > _finite(features['ema50'].iloc[i-5])
    momentum = 52 <= rsi <= 70 and hist > 0
    risk = .005 <= atr/p <= min(.06,cfg.max_atr_ratio) and p <= ema20+1.6*atr
    # Separate entry triggers to avoid treating correlated votes as many independent facts.
    last_5_lows = features['Low'].iloc[i-5:i].astype(float)
    touched_ema = bool((last_5_lows <= features['ema20'].iloc[i-5:i].astype(float) + .35*features['atr'].iloc[i-5:i].astype(float)).any())
    pullback = touched_ema and p > _finite(prev['High']) and p >= ema20 and _finite(r['ret1']) > 0 and vr >= .85
    # A real breakout must move above a high set before the latest completed bar.
    prior_20_high = _finite(r['p_high20'])
    recent_range = float(features['High'].iloc[i-10:i].max()-features['Low'].iloc[i-10:i].min())
    breakout = p > prior_20_high and recent_range <= 4*atr and vr >= 1.35
    pattern = '눌림목 재상승' if pullback else '거래량 돌파' if breakout else None
    checks = [
        _check('trend','중기 추세','PASS' if trend else 'FAIL',f'EMA20>EMA50 및 EMA50 상승 여부={trend}'),
        _check('momentum','과열 없는 상승 모멘텀','PASS' if momentum else 'FAIL',f'RSI={rsi:.1f}, MACD 히스토그램={hist:.3f}'),
        _check('risk','변동성·추격 제한','PASS' if risk else 'FAIL',f'ATR/주가={atr/p:.2%} (허용 0.5%~{min(.06,cfg.max_atr_ratio):.1%}), EMA20 초과={((p-ema20)/atr):+.2f}ATR (최대 +1.60ATR)'),
        _check('pattern','진입 패턴','PASS' if pattern else 'FAIL',f'눌림={pullback}, 돌파={breakout}, 거래량/20일={vr:.2f}배'),
    ]
    return checks,pattern


def _vote_diagnostic(d) -> dict:
    tech = d.votes[:10]
    positive = [v.role for v in tech if v.direction==1]
    groups = {name: [v.role for v in tech if v.role[:2] in codes and v.direction==1] for name,codes in TECHNICAL_ROLE_GROUPS.items()}
    return {
        'technical_buy_votes':len(positive), 'technical_sell_votes':sum(v.direction == -1 for v in tech),
        'derived_bull_vote':d.votes[10].direction==1,
        'derived_bear_vote':d.votes[11].direction==-1,
        'independent_evidence_group_count':sum(bool(xs) for xs in groups.values()),
        'positive_by_group':groups,
        'note':'기존 11/12번 재집계 표는 독립적 예측 근거로 세지 않음. 그룹 수는 확률 아님.',
    }


def assess_high_precision(features: pd.DataFrame, cfg: Config, *,
                          i: int = -1, symbol: str = '',
                          decision_at: str | pd.Timestamp,
                          benchmark: pd.DataFrame | None = None,
                          sector: pd.DataFrame | None = None,
                          flows: pd.DataFrame | None = None,
                          financial_gate: str = 'UNKNOWN') -> dict:
    """Evaluate a *completed* day at `decision_at` (Asia/Seoul if naive).

    benchmark/sector DataFrames: date index, Close, available_at timestamp.
    flows DataFrame: date index, foreign_net_value, institution_net_value,
    available_at timestamp. Every historical release must precede decision_at.
    Missing or unreleased context NEVER counts as a pass.

    Returns only RESEARCH BUY/HOLD/SELL; neither placement nor profitability.
    """
    if cfg.mode != '스윙':
        raise ValueError('고정밀 1단계 연구 버전은 스윙(일봉)만 지원합니다.')
    if not symbol.endswith(('.KS','.KQ')):
        raise ValueError('고정밀 1단계 연구 버전은 국내 주식(.KS/.KQ) 전용입니다.')
    if i < 0: i += len(features)
    if not (WARMUP-1 <= i < len(features)):
        raise ValueError('지표 준비 기간 범위 밖입니다.')
    at = _dt(decision_at)
    current_day = _date(features.index[i])
    if at.date() < current_day:
        raise ValueError('판단 시각이 해당 완료봉보다 앞섭니다.')
    # Do not allow an unfinished trading day to masquerade as a complete candle.
    if at.date() == current_day and (at.hour,at.minute) < (15,40):
        raise ValueError('일봉 완료·검증 전 시각입니다(15:40 KST 이후 확인).')
    d = decide(features,cfg,i)
    q = quality_gate(features,cfg,i,raw_signal=d.signal)
    valid,quality_note = _price_quality(features,i)
    checks = [_check('price_data','시세 무결성','PASS' if valid else 'UNAVAILABLE',quality_note)]
    pattern_checks,pattern = _pattern_assessment(features,i,cfg)
    checks.extend(pattern_checks)
    checks.extend(_context_assessment(features,i,benchmark,sector,flows,at))
    financial = _check('finance','재무 위험 필터', 'PASS' if financial_gate == 'PASS' else 'FAIL' if financial_gate in ('BLOCK','CAUTION') else 'UNAVAILABLE',
                       f'공시가 판단시점에 공개됐다는 별도 검증이 선행되어야 함; 입력 게이트={financial_gate}')
    checks.append(financial)
    status = 'REJECTED'
    if d.signal == 'SELL':
        status = 'ORIGINAL_SELL'
    elif d.signal == 'BUY' and q['strict_signal'] == 'BUY':
        if any(c['state'] == 'UNAVAILABLE' for c in checks):
            status = 'DATA_INCOMPLETE'
        elif all(c['passed'] for c in checks):
            status = 'RESEARCH_APPROVED'
        else:
            status = 'FILTERED'
    elif d.signal == 'BUY':
        status = 'ORIGINAL_QUALITY_REJECTED'
    else:
        status = 'ORIGINAL_HOLD'
    signal = 'BUY' if status == 'RESEARCH_APPROVED' else 'SELL' if d.signal == 'SELL' else 'HOLD'
    return {
        'symbol':symbol, 'bar_date':str(features.index[i]), 'decision_at_kst':at.isoformat(),
        'original_signal':d.signal, 'original_stable_signal':q['strict_signal'],
        'research_signal':signal, 'status':status, 'pattern':pattern,
        'checks':checks, 'reasons':[c['detail'] for c in checks if not c['passed']],
        'vote_diagnostics':_vote_diagnostic(d),
        'limitations':'연구용 고정 임계값이며 과거 적중률 미검증. 원천 자료의 공개 시각·비용·상장폐지·체결·시장/업종 정의를 별도로 검증해야 함. 자동 주문 금지.',
    }
