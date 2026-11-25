"""Domestic market-activity rankings (NOT Naver search popularity, NOT BUY signals).

Use existing licensed data feeds for an authenticated private NOVA workflow.
Never cache/share the full vendor universe with clients; only limited ranked data.
"""
from __future__ import annotations

import re
from datetime import datetime
from zoneinfo import ZoneInfo

from market_scanner import KRXClient, MarketScanError, completed_dates, _number
from public_market_data import FSCStockClient

KST = ZoneInfo('Asia/Seoul')
MARKETS = ('all', 'kospi', 'kosdaq')
KINDS = ('turnover', 'volume', 'gainers')
MAX_ROWS = 20


def rankings_from_rows(rows: list[dict], *, asof: str, provider: str, expected_date: str | None = None):
    """A full verified *single-day* universe to three separate top20 leaderboards.

    It is a ranking of activity, never a buying recommendation, and is not
    equivalent to Naver's popularity/search ranking or real-time broker data.
    """
    if not re.fullmatch(r'\d{8}', str(asof)):
        raise MarketScanError('date_invalid', '주식시세 기준일을 확인할 수 없습니다.')
    clean = {}
    for x in rows:
        if not isinstance(x, dict):
            continue
        symbol = str(x.get('symbol') or '')
        market = x.get('market')
        if not re.fullmatch(r'\d{6}\.(KS|KQ)', symbol):
            continue
        if market not in ('kospi', 'kosdaq') or not symbol.endswith('.KS' if market == 'kospi' else '.KQ'):
            continue
        price = _number(x.get('close'))
        volume = _number(x.get('volume'))
        turnover = _number(x.get('turnover_krw'))
        change = _number(x.get('change_pct'))
        if price is None or price <= 0 or volume is None or volume <= 0 or turnover is None or turnover <= 0 or change is None:
            continue
        if not str(x.get('name') or '').strip():
            continue
        clean[symbol] = {'symbol':symbol, 'name':str(x['name']).strip()[:100],
            'market':market, 'volume':int(volume), 'turnover_krw':int(turnover),
            'change_pct':round(change, 3)}
    if not clean:
        raise MarketScanError('no_usable_rows', '유효한 국내주식 종목을 찾지 못했습니다. 공급원 오류 또는 자료 부족입니다.')
    data = list(clean.values())
    result = {}
    for market in MARKETS:
        group = data if market == 'all' else [x for x in data if x['market'] == market]
        result[market] = {}
        for kind in KINDS:
            ranked = group if kind != 'gainers' else [x for x in group if x['change_pct'] > 0]
            field = 'turnover_krw' if kind == 'turnover' else 'volume' if kind == 'volume' else 'change_pct'
            top = sorted(ranked, key=lambda x:(-x[field], -x['turnover_krw'], x['symbol']))[:MAX_ROWS]
            result[market][kind] = [dict(x, rank=i+1, classification='POPULARITY_OBSERVATION_ONLY',
                       actionable=False) for i,x in enumerate(top)]
    return {'data_date':asof, 'source':provider, 'expected_source_date':expected_date,
        'snapshot_date_status':'ON_SCHEDULE' if expected_date == asof else 'OLDER_THAN_EXPECTED',
        'marketwide':True, 'universe_count':len(data), 'rankings':result,
        'ranking_definitions':{
          'turnover':'당일 거래대금 상위 (장 마감 시세)',
          'volume':'당일 거래량 상위 (거래량 급증률 아님)',
          'gainers':'당일 상승률 상위 (양수 종목만)'},
        'not_naver_ranking':True,'not_buy_signal':True,'not_realtime':True,
        'data_lag':'공식 제공일 기준 일별 자료. 금융위원회 공급원은 T+1 이상 지연 가능',
        'disclaimer':'NOVA 활동 순위는 네이버 검색 인기순위가 아니며, 매수·수익 확률이나 실시간 현재가가 아닙니다.'}


def fetch_popularity(*, source: str, key: str, now: datetime | None = None, client=None):
    """Reuse validated FSC/KRX pagination, date publication and failure handling.

    Never downgrade to a guessed or partial snapshot. Background route owns caching.
    """
    if source not in ('fsc', 'krx'):
        raise MarketScanError('source_invalid', '인기종목 자료 공급원을 확인하세요.')
    cli = client if client is not None else (FSCStockClient(key) if source == 'fsc' else KRXClient(key))
    moment = now or datetime.now(KST)
    days = cli.candidate_dates(moment) if callable(getattr(cli,'candidate_dates',None)) else completed_dates(moment)
    days = list(days)[:10]
    expected = days[0] if days else None
    for day in days:
        try:
            per = [cli.fetch_day(day, market) for market in ('kospi', 'kosdaq')]
        except MarketScanError as exc:
            if exc.code != 'no_published_data':
                raise
            continue
        if all(per):
            age = (moment.astimezone(KST).date() - datetime.strptime(day,'%Y%m%d').date()).days
            if age > 6:
                raise MarketScanError('stale_snapshot','공식 주식시세가 6일보다 오래돼 활동 순위를 표시하지 않습니다.')
            label = getattr(cli, 'source_label', '금융위원회 T+1' if source == 'fsc' else 'KRX 장 마감')
            return rankings_from_rows(per[0] + per[1],asof=day,provider=label,expected_date=expected)
    raise MarketScanError('no_published_data','코스피·코스닥 동일 기준일 전체 자료가 없습니다. 순위를 표시하지 않습니다.')
