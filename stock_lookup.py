"""Read-only Korea stock name search.

Search names via Naver's publicly accessible *unofficial* autocomplete API.
Results and exchange labels are not official KRX listings, and the upstream
endpoint may change or be rate limited. A small local fallback is explicitly
marked as limited. Never infer KOSPI vs KOSDAQ from a bare six-digit code.
"""
from __future__ import annotations

import re
from typing import Any
import requests

# Small fallback only; this is NOT the full Korean equity listing.
FALLBACK = [
    ('삼성전자', '005930.KS', '코스피'),
    ('SK하이닉스', '000660.KS', '코스피'),
    ('NAVER', '035420.KS', '코스피'),
    ('카카오', '035720.KS', '코스피'),
    ('현대차', '005380.KS', '코스피'),
    ('기아', '000270.KS', '코스피'),
    ('LG에너지솔루션', '373220.KS', '코스피'),
    ('삼성바이오로직스', '207940.KS', '코스피'),
    ('에코프로', '086520.KQ', '코스닥'),
    ('에코프로비엠', '247540.KQ', '코스닥'),
    ('HLB', '028300.KQ', '코스닥'),
    ('펄어비스', '263750.KQ', '코스닥'),
    ('CJ ENM', '035760.KQ', '코스닥'),
    ('스튜디오드래곤', '253450.KQ', '코스닥'),
    ('카카오게임즈', '293490.KQ', '코스닥'),
    ('JYP Ent.', '035900.KQ', '코스닥'),
    ('솔브레인', '357780.KQ', '코스닥'),
]

QUERY = re.compile(r'^[\w\s.&()\-+가-힣]{1,40}$', re.UNICODE)
CODE = re.compile(r'^\d{6}$')
NAVER_SEARCH = 'https://m.stock.naver.com/front-api/search/autoComplete'
YAHOO_SEARCH = 'https://query1.finance.yahoo.com/v1/finance/search'


def _market(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    v = value.upper().replace(' ', '')
    if '코스닥' in value or 'KOSDAQ' in v or v.endswith('.KQ'):
        return '코스닥'
    if '코스피' in value or 'KOSPI' in v or v.endswith('.KS'):
        return '코스피'
    return None


def _normalize_item(obj: Any) -> dict | None:
    if not isinstance(obj, dict):
        return None
    code = str(obj.get('code') or obj.get('itemCode') or obj.get('symbol') or '').strip()
    code_match = re.fullmatch(r'(\d{6})(?:\.(?:KS|KQ))?', code, flags=re.IGNORECASE)
    name = str(obj.get('name') or obj.get('itemName') or '').strip()
    if not code_match or not name or len(name) > 70:
        return None
    market = next((_market(obj.get(k)) for k in (
        'typeName', 'marketName', 'marketType', 'market', 'stockExchangeType',
        'exchange', 'reutersCode', 'symbol', 'code') if _market(obj.get(k))), None)
    if not market:
        return None  # Never guess KOSDAQ/KOSPI suffix.
    suffix = '.KQ' if market == '코스닥' else '.KS'
    return {'name': name, 'code': code_match.group(1), 'symbol': code_match.group(1) + suffix,
            'market': market, 'verified': False}


def _candidate_lists(payload: Any) -> list[dict]:
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if not isinstance(payload, dict):
        return []
    for path in (("result", "items"), ("result", "stocks"), ("items",), ("results",), ("result",)):
        cur = payload
        for key in path:
            if not isinstance(cur, dict):
                cur = None
                break
            cur = cur.get(key)
        if isinstance(cur, list):
            return [x for x in cur if isinstance(x, dict)]
    return []


def _fallback(q: str, limit: int) -> list[dict]:
    needle = q.casefold().replace(' ', '')
    out = []
    for name, symbol, market in FALLBACK:
        if needle in name.casefold().replace(' ', '') or needle in symbol[:6]:
            out.append({'name': name, 'code': symbol[:6], 'symbol': symbol,
                        'market': market, 'verified': False})
    return sorted(out, key=lambda item: (not item['name'].casefold().startswith(q.casefold()), item['name']))[:limit]


def _yahoo_candidates(q: str, limit: int) -> list[dict]:
    """Secondary read-only lookup when Naver's non-public search is unavailable."""
    r = requests.get(YAHOO_SEARCH,
                     params={'q': q, 'quotesCount': limit, 'newsCount': 0},
                     headers={'User-Agent': 'Mozilla/5.0 (compatible; NOVAReadOnly/3.1)',
                              'Accept': 'application/json'}, timeout=5)
    r.raise_for_status()
    data = r.json()
    if not isinstance(data, dict):
        return []
    found = []
    seen = set()
    for obj in data.get('quotes', []):
        if not isinstance(obj, dict) or obj.get('quoteType', 'EQUITY') != 'EQUITY':
            continue
        symbol = str(obj.get('symbol') or '').upper().strip()
        if not re.fullmatch(r'\d{6}\.(KS|KQ)', symbol) or symbol in seen:
            continue
        name = str(obj.get('shortname') or obj.get('longname') or symbol).strip()[:70]
        market = '코스닥' if symbol.endswith('.KQ') else '코스피'
        found.append({'name': name, 'code': symbol[:6], 'symbol': symbol,
                      'market': market, 'verified': False})
        seen.add(symbol)
        if len(found) >= limit:
            break
    return found


def search_stocks(query: str, limit: int = 12) -> dict:
    q = query.strip()
    if not QUERY.fullmatch(q):
        raise ValueError('검색어는 한글·영문·숫자 1~40자로 입력하세요.')
    limit = min(max(int(limit), 1), 12)
    results: list[dict] = []
    error = False
    try:
        r = requests.get(NAVER_SEARCH,
            params={'query': q, 'target': 'stock'},
            headers={'User-Agent': 'Mozilla/5.0 (compatible; NOVAReadOnly/3.1)',
                     'Accept': 'application/json', 'Referer': 'https://m.stock.naver.com/'},
            timeout=5)
        r.raise_for_status()
        payload = r.json()
        seen: set[str] = set()
        for raw in _candidate_lists(payload):
            item = _normalize_item(raw)
            if item and item['symbol'] not in seen:
                seen.add(item['symbol'])
                results.append(item)
                if len(results) >= limit:
                    break
    except (requests.RequestException, ValueError, TypeError):
        error = True

    # If Naver's shape changes, don't claim the fallback is a complete universe.
    if results:
        return {'query': q, 'source': '네이버 자동완성 (비공식)', 'limited': False,
                'results': results, 'warning': '시장·종목 표기는 비공식 검색 결과이며 실시간 상장정보를 보장하지 않습니다.'}
    fallback = _fallback(q, limit)
    return {'query': q, 'source': '내장 주요 종목 목록 (제한적)', 'limited': True,
            'results': fallback,
            'warning': '외부 종목명 검색이 일시적으로 불가능하거나 결과가 없어 주요 종목만 표시합니다. 전체 종목 검색이 아닙니다.'}
