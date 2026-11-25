"""NOVA v4.5 read-only KIS investor flows and REST quote snapshots.

No trading APIs, credentials remain on the server. All reported price/flow data
are provider values, never derived from a simulated fallback.
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo
import re
from data_sources import KIS

KST = ZoneInfo('Asia/Seoul')
FIELDS = {'foreign': 'frgn_ntby_qty', 'institution': 'orgn_ntby_qty', 'individual': 'prsn_ntby_qty'}


def stock_code(symbol: str) -> str:
    if not isinstance(symbol, str):
        raise ValueError('국내 주식 종목코드 6자리를 입력하세요.')
    value = symbol.strip().upper()
    m = re.fullmatch(r'(\d{6})(?:\.(?:KS|KQ))?', value)
    if not m:
        raise ValueError('KIS 수급·현재가는 국내 6자리 종목코드만 지원합니다.')
    return m.group(1)


def _int(value):
    if value is None or str(value).strip() == '':
        return None
    try:
        return int(str(value).replace(',', '').strip())
    except (ValueError, TypeError):
        return None


def _float(value):
    if value is None or str(value).strip() == '':
        return None
    try:
        return float(str(value).replace(',', '').strip())
    except (ValueError, TypeError):
        return None


def summarize(rows: list[dict], code: str, now: datetime | None = None) -> dict:
    """Reject incomplete dates; preserve missing values rather than converting to zero."""
    now = now or datetime.now(KST)
    today = now.astimezone(KST).strftime('%Y%m%d')
    unique = {}
    current_day_seen = False
    for rec in rows:
        if not isinstance(rec, dict):
            continue
        day = str(rec.get('stck_bsop_date', ''))
        if not re.fullmatch(r'\d{8}', day):
            continue
        if day >= today:
            current_day_seen |= day == today
            continue  # intraday provisional data must not enter completed-day aggregates
        if day not in unique:
            unique[day] = {'date': day, **{k: _int(rec.get(field)) for k, field in FIELDS.items()}}
    ordered = [unique[date] for date in sorted(unique, reverse=True)][:20]
    def aggregate(period):
        sample = ordered[:period]
        # Fail closed: a partial window is NOT presented as a full 5/10/20-day sum.
        # Complete coverage is checked independently for each investor. One
        # missing individual row must not conceal 20 valid foreign/institution
        # rows. The legacy `complete` still means *all three* are complete.
        coverage = {k: len(sample) == period and all(r[k] is not None for r in sample)
                    for k in FIELDS}
        counts = {k: sum(r[k] is not None for r in sample) for k in FIELDS}
        return {'period': period, 'complete': all(coverage.values()), 'count': len(sample),
                'complete_by_investor': coverage, 'count_by_investor': counts,
                **{k: sum(r[k] for r in sample) if coverage[k] else None for k in FIELDS}}
    return {'symbol': code, 'unit': 'shares', 'source': 'KIS 국내주식 투자자별 매매동향 (KRX)',
            'as_of': ordered[0]['date'] if ordered else None, 'retrieved_at': now.isoformat(timespec='seconds'),
            'daily': ordered, 'aggregate': [aggregate(p) for p in (5, 10, 20)],
            'partial_today_excluded': current_day_seen, 'has_data': bool(ordered),
            'note': '주식 수량 순매수(주). 당일 미확정 데이터는 누적 계산에서 제외; 집계 부족·누락은 미확인으로 표시. 수익률/매수 신호가 아닙니다.'}


def get_flows(key: str, secret: str, code: str, now: datetime | None = None) -> dict:
    now = now or datetime.now(KST)
    api = KIS(key, secret)
    payload = api.get('/uapi/domestic-stock/v1/quotations/inquire-investor', 'FHKST01010900',
                      {'FID_COND_MRKT_DIV_CODE': 'J', 'FID_INPUT_ISCD': code})
    records = payload.get('output') or []
    if isinstance(records, dict):
        records = [records]
    if not isinstance(records, list):
        raise RuntimeError('KIS 투자자 데이터 형식이 유효하지 않습니다.')
    return summarize(records, code, now)


def parse_quote(output: dict, code: str, now: datetime | None = None) -> dict:
    now = now or datetime.now(KST)
    price = _float(output.get('stck_prpr'))
    if price is None or price <= 0:
        raise RuntimeError('KIS 현재가가 반환되지 않았습니다.')
    time_raw = str(output.get('stck_cntg_hour') or '')
    # Some REST outputs omit last trade time. Do not mistake retrieval time for exchange trade time.
    exchange_time = f'{time_raw[0:2]}:{time_raw[2:4]}:{time_raw[4:6]}' if re.fullmatch(r'\d{6}',time_raw) else None
    sign = str(output.get('prdy_vrss_sign') or '')
    change = _int(output.get('prdy_vrss'))
    pct = _float(output.get('prdy_ctrt'))
    if sign in {'4', '5'}:
        change = -abs(change) if change is not None else None
        pct = -abs(pct) if pct is not None else None
    elif sign in {'1', '2'}:
        change = abs(change) if change is not None else None
        pct = abs(pct) if pct is not None else None
    elif sign == '3':
        change = 0 if change is not None else None
        pct = 0.0 if pct is not None else None
    else:
        # Sign cannot be inferred from magnitude alone.
        change = None
        pct = None
    return {'symbol': code, 'price': price, 'change': change,
            'change_pct': pct, 'volume': _int(output.get('acml_vol')),
            'trade_time_kst': exchange_time, 'retrieved_at': now.isoformat(timespec='seconds'),
            'source': 'KIS 현재가 REST · 정규장 종가 아님', 'realtime_stream': False, 'refresh_type': 'REST polling',
            'market_hours_reference': '평일 09:00~15:30 KST (휴장일은 별도 확인)',
            'note': '조회 시점의 KIS 현재가 스냅샷(시간외 포함 가능, 정규장 종가와 별도)이며 실시간 WebSocket 틱은 아닙니다. 거래소 최종 체결시각을 제공하지 않으면 미확인입니다.'}


def get_quote(key: str, secret: str, code: str, now: datetime | None = None) -> dict:
    api = KIS(key, secret)
    data = api.get('/uapi/domestic-stock/v1/quotations/inquire-price', 'FHKST01010100',
                   {'FID_COND_MRKT_DIV_CODE': 'J', 'FID_INPUT_ISCD': code})
    return parse_quote(data.get('output') or {}, code, now)


def get_intraday(key: str, secret: str, code: str, now: datetime | None = None) -> dict:
    """Today's KIS one-minute bars. No synthetic series and no close inference."""
    now = now or datetime.now(KST)
    bars = KIS(key, secret).minutes(code, now, sessions=1)
    day = now.astimezone(KST).date()
    points = []
    if not bars.empty:
        for idx, row in bars.iterrows():
            stamp = idx.to_pydatetime() if hasattr(idx, 'to_pydatetime') else idx
            if stamp.date() != day:
                continue
            price, volume = _float(row['Close']), _int(row['Volume'])
            if price is None or price <= 0:
                continue
            points.append({'time': stamp.strftime('%H:%M'), 'price': price, 'volume': volume})
    points = points[-390:]
    return {'symbol': code, 'source': 'KIS 당일 1분 시세 REST', 'date': day.isoformat(),
            'retrieved_at': now.isoformat(timespec='seconds'), 'points': points,
            'note': '당일 1분 시세 제공 구간만 표시합니다. 진행 중인 1분 봉은 미완성일 수 있습니다. 휴장일은 데이터가 없을 수 있습니다.'}
