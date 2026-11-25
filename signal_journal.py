"""NOVA v3.5: forward-only daily-close evidence on user-owned signal snapshots.

No trades are executed, no historical signals are fabricated, and nothing is saved
on the server. Live records are written to the user's browser by journal.js.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from math import isfinite
from typing import Any

import pandas as pd

HORIZONS = (1, 3, 5, 10)
MAX_BATCH = 60
MAX_SYMBOLS = 4
VALID_MODES = {'1분', '5분', '스윙'}
VALID_SOURCES = {'yahoo', 'kis'}


def _to_date(raw: str) -> date:
    if not isinstance(raw, str) or len(raw) != 10:
        raise ValueError('날짜는 YYYY-MM-DD 형식이어야 합니다.')
    return date.fromisoformat(raw)


def validate_record(raw: Any) -> dict:
    if not isinstance(raw, dict):
        raise ValueError('기록 형식이 잘못되었습니다.')
    from data_sources import ticker_format
    symbol = ticker_format(raw.get('symbol', ''))
    if not symbol.endswith(('.KS','.KQ')):
        raise ValueError('신호 관찰은 국내 코스피·코스닥 종목만 지원합니다.')
    mode = raw.get('mode')
    source = raw.get('source')
    technical = raw.get('technical')
    filtered = raw.get('filtered')
    if mode not in VALID_MODES or source not in VALID_SOURCES:
        raise ValueError('지원되지 않는 분석 주기 또는 공급원입니다.')
    if technical not in ('BUY', 'SELL') or filtered not in ('BUY', 'SELL', 'HOLD'):
        raise ValueError('신호 값이 잘못되었습니다.')
    if filtered not in (technical, 'HOLD'):
        raise ValueError('재무 필터는 방향을 바꿀 수 없습니다.')
    entry_day = _to_date(raw.get('bar_day'))
    if entry_day > datetime.now(timezone.utc).date() + pd.Timedelta(days=1):
        raise ValueError('미래 시점의 신호입니다.')
    reference = float(raw.get('reference', 0))
    if not isfinite(reference) or reference <= 0 or reference > 10**12:
        raise ValueError('기록 가격이 유효하지 않습니다.')
    cost_bps = int(raw.get('cost_bps', 15))
    if not 0 <= cost_bps <= 100:
        raise ValueError('거래비용 가정이 유효하지 않습니다.')
    sid = raw.get('id')
    if not isinstance(sid, str) or not 1 <= len(sid) <= 160:
        raise ValueError('기록 ID가 잘못되었습니다.')
    gate = raw.get('gate', 'UNKNOWN')
    if gate not in ('PASS', 'CAUTION', 'BLOCK', 'UNKNOWN'):
        raise ValueError('재무 확인 상태가 올바르지 않습니다.')
    return {'id': sid, 'symbol': symbol, 'mode': mode, 'source': source,
            'bar_day': entry_day.isoformat(), 'reference': reference,
            'technical': technical, 'filtered': filtered,
            'gate': gate, 'cost_bps': cost_bps}


def evaluate_record(record: dict, frame: pd.DataFrame) -> dict:
    """Compute *forward* outcomes from finished close bars only.

    1 trading day means the first daily bar strictly after the signal date.
    Uses the price recorded at signal time (not reconstructed from future data).
    A price adjustment/corporate action can invalidate the comparison; do not
    mistake this for a total-return simulation or a complete strategy backtest.
    """
    entry_day = _to_date(record['bar_day'])
    closes = []
    for t, value in frame['Close'].items():
        try:
            day = pd.Timestamp(t).date()
            close = float(value)
            if isfinite(close) and close > 0:
                closes.append((day, close))
        except (ValueError, TypeError):
            continue
    closes.sort(key=lambda x: x[0])
    before = [(d, v) for d, v in closes if d == entry_day]
    after = [(d, v) for d, v in closes if d > entry_day]
    entry = record['reference']
    # For completed daily signals, significant divergence suggests corporate
    # actions / adjusted historical price mismatches. Skip rather than mislead.
    anchor_missing = (not before and bool(closes) and closes[-1][0] >= entry_day)
    # Never interpret a nonexistent start-day bar as a valid market session.
    # Today during market hours may simply have no completed daily bar yet.
    mismatch = False
    if before and record['mode'] == '스윙':
        mismatch = abs(before[-1][1] / entry - 1) > .05
    outcomes = {}
    for horizon in HORIZONS:
        if anchor_missing:
            outcomes[str(horizon)] = {'status': 'unavailable', 'reason': '신호 날짜의 완료 일봉이 없습니다. 가격 데이터와 거래소 휴장일을 확인하세요.'}
            continue
        if mismatch:
            outcomes[str(horizon)] = {'status': 'unavailable', 'reason': '과거 가격이 기록값과 5% 이상 차이납니다. 액면분할/수정주가 변동을 점검하세요.'}
            continue
        if len(after) < horizon:
            outcomes[str(horizon)] = {'status': 'pending', 'reason': f'{horizon}거래일 이후 완료 종가가 아직 없습니다.'}
            continue
        period = after[:horizon]
        target_day, close = period[-1]
        change = (close / entry - 1) * 100
        direction = change if record['technical'] == 'BUY' else -change
        # Flat counts as a miss, not a win.
        hit = direction > 0
        # ONLY for a hypothetical long after a buy signal. Sell means
        # 'reduce an owned position', NOT an executable short recommendation.
        buy_net = change - (record['cost_bps'] * 2 / 100) if record['technical'] == 'BUY' else None
        path = [entry] + [v for _, v in period]
        peak = path[0]
        mdd = 0.0
        for price in path:
            peak = max(peak, price)
            mdd = min(mdd, (price / peak - 1) * 100)
        outcomes[str(horizon)] = {
            'status': 'complete', 'settlement_day': target_day.isoformat(),
            'settlement_close': round(close, 6),
            'asset_change_pct': round(change, 4),
            'directional_score_pct': round(direction, 4),
            'direction_hit': hit,
            'buy_net_pct': round(buy_net, 4) if buy_net is not None else None,
            'close_path_drawdown_pct': round(mdd, 4),
            'reason': '완료된 일봉 종가 기반의 관찰 결과. 체결·슬리피지·세금 등 미반영',
        }
    return {'id': record['id'], 'symbol': record['symbol'], 'mode': record['mode'],
            'source': record['source'], 'bar_day': record['bar_day'], 'horizons': outcomes,
            'recorded_price': entry}


def evaluate_batch(raw_records: Any, loader) -> dict:
    if not isinstance(raw_records, list) or not 1 <= len(raw_records) <= MAX_BATCH:
        raise ValueError(f'기록은 1~{MAX_BATCH}건만 조회할 수 있습니다.')
    records = [validate_record(x) for x in raw_records]
    keys = {(x['symbol'], x['source']) for x in records}
    if len(keys) > MAX_SYMBOLS:
        raise ValueError(f'한 요청에는 최대 {MAX_SYMBOLS}개 종목·공급원만 평가할 수 있습니다.')
    frames = {}
    errors = {}
    for key in sorted(keys):
        try:
            frame = loader(*key)
            if not isinstance(frame, pd.DataFrame) or 'Close' not in frame:
                raise ValueError('유효한 일봉이 없습니다.')
            frames[key] = frame
        except Exception:
            errors[key] = '시세 공급원 조회 실패. 해당 종목·API 설정 또는 네트워크를 확인하세요.'
    rows = []
    for rec in records:
        key = (rec['symbol'], rec['source'])
        if key in errors:
            rows.append({'id': rec['id'], 'error': errors[key], 'horizons': {str(h): {'status': 'unavailable', 'reason': errors[key]} for h in HORIZONS}})
        else:
            rows.append(evaluate_record(rec, frames[key]))
    return {'results': rows, 'horizons': list(HORIZONS), 'note': '현재 기기의 신호 기록을 이용한 사후 관찰입니다. 신호 기록은 서버에 저장되지 않습니다.'}
