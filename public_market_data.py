"""Financial Services Commission (data.go.kr) daily stock snapshot client.

Read-only private market screening. This is a T+1, not realtime, source.
The source has licensing restrictions: do not redistribute raw bulk quotes,
including through a public API. Never echo the service key or request URL.
"""
from __future__ import annotations

import math
import re
from datetime import datetime, timedelta
from urllib.parse import unquote
from zoneinfo import ZoneInfo

import requests

from market_scanner import MarketScanError, _number

KST = ZoneInfo('Asia/Seoul')
STOCK_URL = 'https://apis.data.go.kr/1160100/GetStockSecuritiesInfoService_V2/getStockPriceInfo_V2'
PAGE_SIZE = 1000
MAX_PAGES = 25
MARKETS = {'KOSPI': 'kospi', 'KOSDAQ': 'kosdaq'}
SUCCESS_CODES = {'00', '0', '000', 'NORMAL_SERVICE'}


def published_dates(now: datetime):
    """Do not request yesterday until the next business day 13:00 KST.

    Weekday heuristic only; holidays are handled by retrying earlier dates.
    """
    local = now.astimezone(KST)
    day = local.date()
    # Always start with the preceding trading weekday.  On days before
    # 13:00 the preceding weekday is not guaranteed to be published yet.
    day -= timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    if local.hour < 13:
        day -= timedelta(days=1)
    for _ in range(8):
        while day.weekday() >= 5:
            day -= timedelta(days=1)
        yield day.strftime('%Y%m%d')
        day -= timedelta(days=1)


def _date(v):
    return str(v or '').replace('-', '').strip()


def _stock_code(v):
    text = str(v or '').strip()
    return text if re.fullmatch(r'\d{6}', text) else None


def parse_fsc_page(payload, requested_date: str):
    """Return all normalized market rows and pagination metadata.

    On a malformed or unauthorized response, raise. Missing rows are NOT
    silently counted as a complete market, preventing false coverage.
    """
    if not isinstance(payload, dict):
        raise MarketScanError('fsc_format', '금융위원회 API JSON 구조가 올바르지 않습니다.')
    root = payload.get('response')
    if not isinstance(root, dict):
        raise MarketScanError('fsc_format', '공공데이터포털 response 필드가 없습니다.')
    header = root.get('header') or {}
    code = str(header.get('resultCode') or '').strip()
    if code not in SUCCESS_CODES:
        if code in ('20', '30', '31'):
            raise MarketScanError('fsc_auth', f'공공데이터포털 인증 또는 서비스 권한 오류 (코드 {code})')
        if code in ('22', '23'):
            raise MarketScanError('fsc_rate_limit', f'공공데이터포털 호출량 제한 (코드 {code})')
        raise MarketScanError('fsc_provider', f'공공데이터포털 API 오류 (코드 {code or "없음"})')
    body = root.get('body')
    if not isinstance(body, dict):
        raise MarketScanError('fsc_format', '공공데이터포털 응답 body 형식 오류')
    try:
        total = int(body.get('totalCount', -1))
    except (ValueError, TypeError):
        raise MarketScanError('fsc_format', '전체 응답 건수를 확인할 수 없습니다.') from None
    if total < 0 or total > PAGE_SIZE * MAX_PAGES:
        raise MarketScanError('fsc_capacity', '페이지 제한으로 전체 시장 수집을 보장할 수 없습니다.')
    items = body.get('items') or {}
    if not isinstance(items, dict):
        raise MarketScanError('fsc_format', '주식시세 항목의 형식이 잘못됐습니다.')
    raw = items.get('item') or []
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, list):
        raise MarketScanError('fsc_format', '주식시세 배열 형식이 잘못됐습니다.')
    rows = []
    valid_dates = 0
    for x in raw:
        if not isinstance(x, dict):
            raise MarketScanError('fsc_format', '주식시세 일부 항목이 손상됐습니다.')
        if _date(x.get('basDt')) != requested_date:
            raise MarketScanError('fsc_date_mismatch', '응답의 기준일이 요청일과 다릅니다.')
        valid_dates += 1
        market = MARKETS.get(str(x.get('mrktCtg') or '').upper().strip())
        # Other instruments/markets are allowed in the official response and
        # count toward pagination completeness; they are not trading candidates.
        if market is None:
            continue
        code = _stock_code(x.get('srtnCd'))
        name = str(x.get('itmsNm') or '').strip()
        if not code or not name:
            continue
        if any(field not in x for field in ('clpr','trqu','trPrc','mrktTotAmt','fltRt')):
            raise MarketScanError('fsc_format', '종목 시세 필수 항목이 누락됐습니다.')
        close,volume = _number(x.get('clpr')), _number(x.get('trqu'))
        turnover,cap,change = (_number(x.get(k)) for k in ('trPrc','mrktTotAmt','fltRt'))
        rows.append({'symbol': code + ('.KS' if market == 'kospi' else '.KQ'),
                     'name':name,'market':market,'close':close or 0.0,
                     'volume':int(volume or 0),'turnover_krw':int(turnover or 0),
                     'market_cap_krw':int(cap or 0),'change_pct':round(change or 0.0, 3)})
    return rows, total, valid_dates


class FSCStockClient:
    source_label = '금융위원회 주식시세정보 (T+1)'
    def __init__(self, api_key: str, session=None):
        value = str(api_key or '').strip()
        if not value:
            raise MarketScanError('fsc_not_configured', 'Render에 FSC_STOCK_API_KEY를 등록하세요.')
        # Some portals show a percent-encoded key. requests will encode again;
        # decode ONCE before passing via `params` to prevent double encoding.
        self.key = unquote(value) if re.search(r'%[0-9A-Fa-f]{2}', value) else value
        self.session = session or requests.Session()
        self._cache = {}

    def candidate_dates(self, now: datetime):
        return published_dates(now)

    def _read_day(self, date: str):
        if date in self._cache:
            return self._cache[date]
        if not re.fullmatch(r'\d{8}', date):
            raise MarketScanError('invalid_input', '날짜 형식은 YYYYMMDD입니다.')
        all_rows = []
        count = None
        received = 0
        for page in range(1, MAX_PAGES + 1):
            try:
                response = self.session.get(
                    STOCK_URL,
                    params={'serviceKey':self.key,'resultType':'json',
                            'basDt':date,'numOfRows':PAGE_SIZE,'pageNo':page},
                    timeout=(6,22),
                    headers={'Accept':'application/json'},
                )
                if response.status_code in (401, 403):
                    raise MarketScanError('fsc_auth', '공공데이터포털 인증키 또는 서비스 권한을 확인하세요.')
                if response.status_code == 429:
                    raise MarketScanError('fsc_rate_limit', '공공데이터포털 호출량이 제한됐습니다.')
                if response.status_code != 200:
                    raise MarketScanError('fsc_network', f'공공데이터포털 응답 HTTP {response.status_code}')
                try:
                    payload = response.json()
                except (ValueError, TypeError):
                    raise MarketScanError('fsc_format', '공공데이터포털 JSON이 아닌 응답을 받았습니다.') from None
            except requests.RequestException:
                # Requests exception can include credential-bearing query URL;
                # suppress its message completely.
                raise MarketScanError('fsc_network','공공데이터포털 통신에 실패했습니다.') from None
            rows, page_total, valid_count = parse_fsc_page(payload, date)
            if count is None:
                count = page_total
            elif page_total != count:
                raise MarketScanError('fsc_inconsistent', '조회 중 전체 건수가 바뀌어 검색을 중단했습니다.')
            if not valid_count and count and received < count:
                raise MarketScanError('fsc_incomplete', '시세 페이지가 누락돼 전체 검색을 보장할 수 없습니다.')
            received += valid_count
            all_rows.extend(rows)
            if received >= count:
                if received != count:
                    raise MarketScanError('fsc_inconsistent', '실제 조회 건수가 API 전체 건수와 다릅니다.')
                break
        if count is None or received != count:
            raise MarketScanError('fsc_incomplete','전체 시세 페이지를 수신하지 못했습니다.')
        grouped = {'kospi':[], 'kosdaq':[]}
        for row in all_rows:
            grouped[row['market']].append(row)
        self._cache[date] = grouped
        return grouped

    def fetch_day(self, date: str, market: str):
        if market not in ('kospi','kosdaq'):
            raise MarketScanError('invalid_input','지원하지 않는 시장입니다.')
        return self._read_day(date)[market]
