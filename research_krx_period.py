"""Whole-period KRX data supplied by the FSC public-data portal. Private research only."""
from datetime import datetime, timedelta
import json
import re
from urllib.parse import unquote

import requests
from public_market_data import STOCK_URL, SUCCESS_CODES
from research_krx_bulk import CODES, FIELDS, SourceError, SYMBOLS

INDEX_URL = 'https://apis.data.go.kr/1160100/GetMarketIndexInfoService_V2/getStockMarketIndex_V2'
STOCK = 'FSC_getStockPriceInfo_V2'
INDEX = 'FSC_getStockMarketIndex_V2'
INDEX_SYMBOLS = {'코스피': '^KS11', '코스닥': '^KQ11'}
MARKET_FIELDS = dict(zip(FIELDS, ('mkp','hipr','lopr','clpr','trqu','trPrc')))


def fetch_period(key, symbol, days, session):
    if symbol not in SYMBOLS:
        raise SourceError('UNKNOWN_SYMBOL')
    is_index = symbol in INDEX_SYMBOLS.values()
    service = INDEX if is_index else STOCK
    key = unquote(key) if re.search(r'%[0-9A-Fa-f]{2}', key) else key
    after = days[-1] + timedelta(days=1)
    query = {'serviceKey': key, 'numOfRows': 1000, 'resultType': 'json',
             'beginBasDt': days[0].strftime('%Y%m%d'), 'endBasDt': after.strftime('%Y%m%d')}
    if is_index:
        query['idxNm'] = next(name for name, value in INDEX_SYMBOLS.items() if value == symbol)
    else:
        query['likeSrtnCd'] = symbol[:6]
    dates = set(days)
    found = {}
    count = 0
    total = None
    for page in range(1, 12):
        try:
            with session.get(INDEX_URL if is_index else STOCK_URL, params={**query,'pageNo':page},
                             timeout=(5,30), allow_redirects=False, stream=True) as response:
                if response.status_code in (401,403):
                    raise SourceError('FSC_AUTH_DENIED', service=service)
                if response.status_code != 200:
                    raise SourceError('FSC_HTTP_' + str(response.status_code), service=service)
                body = bytearray()
                for chunk in response.iter_content(65536):
                    body.extend(chunk)
                    if len(body) > 4 * 1024 * 1024:
                        raise SourceError('FSC_RESPONSE_TOO_LARGE', service=service)
        except requests.RequestException:
            raise SourceError('FSC_NETWORK_ERROR', service=service) from None
        try:
            result = json.loads(body)['response']
        except (ValueError, KeyError, TypeError):
            raise SourceError('FSC_NON_JSON_RESPONSE', service=service) from None
        code = str(result.get('header',{}).get('resultCode','')).strip()
        if code not in SUCCESS_CODES:
            raise SourceError('FSC_AUTH_DENIED' if code in ('20','30','31','32')
                              else 'FSC_PROVIDER_REJECTED', service=service)
        container = result.get('body') or {}
        items = container.get('items') or {}
        rows = items.get('item', []) if isinstance(items,dict) else None
        if isinstance(rows,dict):
            rows = [rows]
        try:
            reported = int(container['totalCount'])
            reported_page = int(container['pageNo'])
        except (KeyError, ValueError, TypeError):
            raise SourceError('FSC_INVALID_PAGINATION', service=service) from None
        if not isinstance(rows, list) or not 0 <= reported <= 1100 or reported_page != page or (total is not None and total != reported):
            raise SourceError('FSC_INVALID_PAGINATION', service=service)
        total = reported
        for row in rows:
            if not isinstance(row,dict):
                raise SourceError('FSC_INVALID_ROW', service=service)
            observed = INDEX_SYMBOLS.get(row.get('idxNm')) if is_index else CODES.get(row.get('srtnCd'))
            if observed != symbol:
                raise SourceError('FSC_IDENTITY_MISMATCH', service=service, symbol=symbol)
            try:
                day = datetime.strptime(row['basDt'], '%Y%m%d').date()
            except (KeyError, TypeError, ValueError):
                raise SourceError('FSC_DATE_INVALID', service=service, symbol=symbol) from None
            if day == after:
                continue   # some deployments treat the requested end as inclusive
            if day not in dates or day.isoformat() in found:
                raise SourceError('FSC_CALENDAR_OR_DUPLICATE', day, service, symbol)
            found[day.isoformat()] = row
        count += len(rows)
        if count == total:
            return found
        if not rows or count > total:
            break
    raise SourceError('FSC_INCOMPLETE_PAGINATION', service=service, symbol=symbol)
