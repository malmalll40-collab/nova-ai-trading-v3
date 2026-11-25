"""Public Naver investor flows. Read-only, bounded fetch; unofficial source.

This module intentionally fails closed on unknown fields, missing dates and units.
No user cookie, account data, request bypass or repeated scraping is performed.
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo
import re

import requests
from bs4 import BeautifulSoup
from investor_flow import stock_code, summarize

KST = ZoneInfo('Asia/Seoul')
NAVER_NEW = 'https://stock.naver.com'
NAVER_LEGACY_MOBILE = 'https://m.stock.naver.com'
HEADERS = {
    'User-Agent': 'NOVA-InvestorFlow/1.0 (read-only, low-frequency; contact via site operator)',
    'Accept': 'application/json',
}
# All are explicitly *quantity* fields (shares); monetary amounts are not accepted.
COLUMNS = {
    'foreign': ('foreignerPureBuyQuant', 'foreignPureBuyQuant', 'foreignNetBuyQuant'),
    'institution': ('organPureBuyQuant', 'organizationPureBuyQuant', 'organNetBuyQuant'),
    'individual': ('individualPureBuyQuant', 'personalPureBuyQuant', 'individualNetBuyQuant'),
}
DATES = ('bizdate', 'businessDate', 'localDate', 'date', 'tradeDate', 'tradingDate')


class NaverFlowError(RuntimeError):
    """Safe, non-sensitive and actionable retrieval error."""


class NaverEndpointMissing(NaverFlowError):
    """Only a missing public endpoint allows trying the other documented endpoint."""


def _safe_number(value):
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if abs(value) <= 10**15 else None
    if isinstance(value, float):
        return int(value) if value.is_integer() and abs(value) <= 10**15 else None
    if not isinstance(value, str):
        return None
    v = value.strip().replace(',', '').replace(' ', '').replace('−','-')
    if not re.fullmatch(r'[+-]?\d+', v):
        return None
    n = int(v)
    return n if abs(n) <= 10**15 else None


def _date(value):
    if value is None:
        return None
    text = str(value).strip().replace('-', '').replace('.', '')
    if not re.fullmatch(r'\d{8}', text):
        return None
    try:
        datetime.strptime(text, '%Y%m%d')
    except ValueError:
        return None
    return text


def _rows(payload: object) -> list[dict]:
    """Recognize only explicitly documented public stock investor rows."""
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ('dealTrendInfos', 'content', 'items', 'list', 'trends'):
        val = payload.get(key)
        if isinstance(val, list):
            return [x for x in val if isinstance(x, dict)]
    for key in ('result', 'data', 'sections'):
        val = payload.get(key)
        if isinstance(val, (dict, list)):
            nested = _rows(val)
            if nested:
                return nested
    return []


def parse_naver(payload: object, code: str, *, now: datetime | None = None,
                endpoint: str = 'Naver public investor trend') -> dict:
    code = stock_code(code)
    now = now or datetime.now(KST)
    entries = _rows(payload)
    if not entries:
        raise NaverFlowError('네이버 수급 응답에 일별 자료가 없습니다.')
    unique: dict[str, dict] = {}
    unknown = 0
    for item in entries[:120]:
        date = next((d for key in DATES if (d := _date(item.get(key)))), None)
        if date is None:
            unknown += 1
            continue
        values = {}
        for investor, options in COLUMNS.items():
            values[investor] = next((_safe_number(item.get(k)) for k in options
                                    if _safe_number(item.get(k)) is not None), None)
        if not any(v is not None for v in values.values()):
            unknown += 1
            continue
        rec = {'stck_bsop_date':date, 'frgn_ntby_qty':values['foreign'],
               'orgn_ntby_qty':values['institution'], 'prsn_ntby_qty':values['individual']}
        if date in unique and unique[date] != rec:
            raise NaverFlowError('네이버 수급 동일 날짜에 서로 다른 수량이 있어 비교할 수 없습니다.')
        unique[date] = rec
    if not unique:
        raise NaverFlowError('네이버 수급의 날짜·주식 수량 단위를 확인할 수 없습니다.')
    result = summarize(list(unique.values()), code, now)
    if not result['has_data']:
        raise NaverFlowError('네이버 수급에서 확정 전 일자만 확인됩니다. 완료 거래일 자료가 없습니다.')
    result.update({
        'source': 'NAVER 증권 공개 종목별 투자자 동향 (비공식)',
        'source_detail': endpoint,
        'provider': 'naver',
        'independent_source_verified': False,
        'realtime_confirmed': False,
        'rows_unrecognized': unknown,
        'note': ('네이버 공개 화면의 투자자별 순매수 수량(주). 비공식 조회로 지연·변경·차단될 수 있습니다. '
                 '당일 미확정 자료 제외; 누락·표본 부족 시 누적 수급은 미확인. 다른 제공원과 교차검증되지 않았습니다.'),
    })
    return result


def _fetch_json(session, url: str):
    try:
        resp = session.get(url, headers=HEADERS, timeout=(3, 8), allow_redirects=False)
    except requests.RequestException as exc:
        raise NaverFlowError('네이버 조회 연결이 실패했습니다. 서버 네트워크를 확인하세요.') from exc
    if resp.status_code in (401, 403, 429):
        raise NaverFlowError(f'네이버 접근이 제한됐습니다 (HTTP {resp.status_code}). 재시도·우회하지 않습니다.')
    if resp.status_code in (301,302,303,307,308):
        raise NaverFlowError('네이버 수급 조회가 다른 페이지로 이동했습니다. 자료 형식을 확인하세요.')
    if resp.status_code == 404:
        raise NaverEndpointMissing('네이버 공개 API 경로가 변경되었습니다 (HTTP 404).')
    if resp.status_code != 200:
        raise NaverFlowError(f'네이버 수급 조회 실패 (HTTP {resp.status_code})')
    raw = resp.content
    if len(raw) > 2_000_000:
        raise NaverFlowError('네이버 응답 용량이 허용 범위를 넘었습니다.')
    if 'json' not in resp.headers.get('Content-Type', '').lower():
        raise NaverFlowError('네이버가 JSON이 아닌 응답을 보냈습니다.')
    try:
        return resp.json()
    except ValueError as exc:
        raise NaverFlowError('네이버 JSON 응답을 해석할 수 없습니다.') from exc


def _flow_coverage(flow: dict) -> tuple[int, int, int, int]:
    """Value only usable per-investor 20/10/5-day windows, not bare row counts."""
    agg = {r.get('period'): r for r in flow.get('aggregate', [])}
    def count_for(period: int) -> int:
        row = agg.get(period) or {}
        coverage = row.get('complete_by_investor')
        if not isinstance(coverage, dict):
            coverage = {k:bool(row.get('complete')) for k in ('foreign','institution','individual')}
        return sum(bool(coverage.get(k)) for k in ('foreign','institution','individual'))
    return (count_for(20),count_for(10),count_for(5),len(flow.get('daily', [])))


def _coverage_note(flow: dict) -> str:
    agg = {r.get('period'): r for r in flow.get('aggregate', [])}
    parts=[]
    for p in (5,10,20):
        r=agg.get(p) or {}
        c=r.get('count_by_investor') or {}
        parts.append(f"{p}일 외국인 {c.get('foreign',0)}/{p}, 기관 {c.get('institution',0)}/{p}, 개인 {c.get('individual',0)}/{p}")
    return ' · '.join(parts)


def _merge_verified_same_provider(mobile: dict, expanded: dict, code: str,
                                  now: datetime) -> dict:
    """Unite *verified non-conflicting* Naver dates, never add values or extrapolate.

    Do not blend conflicting KRX/consolidated scopes. Full daily coverage
    requires each investor's own observed quantity on every session.
    """
    by_day={r['date']: dict(r) for r in expanded.get('daily',[])}
    for row in mobile.get('daily',[]):
        day=row['date']
        if day in by_day:
            for investor in ('foreign','institution','individual'):
                if by_day[day].get(investor) is None:
                    by_day[day][investor]=row.get(investor)
        else:
            by_day[day]=dict(row)
    converted=[{'stck_bsop_date':r['date'],
                'frgn_ntby_qty':r.get('foreign'),
                'orgn_ntby_qty':r.get('institution'),
                'prsn_ntby_qty':r.get('individual')}
               for r in by_day.values()]
    result=summarize(converted, code, now)
    result.update(provider='naver',
                  source='NAVER 공개 종목별 투자자 동향 (비공식, 2경로 일별 값 대조)',
                  source_detail=mobile.get('source_detail','네이버 공개 정보')+' + '+expanded.get('source_detail','네이버 일별 조회'),
                  independent_source_verified=False,realtime_confirmed=False,
                  rows_unrecognized=int(mobile.get('rows_unrecognized',0))+int(expanded.get('rows_unrecognized',0)),
                  partial_today_excluded=bool(mobile.get('partial_today_excluded') or expanded.get('partial_today_excluded')),
                  note='서로 일치하는 네이버 내 두 경로에서 제공된 투자자별 순매수 수량(주). 미공개 값 추정 없음. KRX 독립 교차검증 아님.')
    return result


def _overlap_conflicts(first: dict, second: dict) -> bool:
    """Compare overlapping completed-day shares; do not trust contradictory series."""
    latest = {row['date']: row for row in first.get('daily', [])}
    for row in second.get('daily', []):
        base = latest.get(row.get('date'))
        if base is None:
            continue
        for name in ('foreign', 'institution', 'individual'):
            if base.get(name) is not None and row.get(name) is not None and base[name] != row[name]:
                return True
    return False


def _parse_pc_investor_table(content: bytes) -> list[dict]:
    """Read only observed *share-count* columns in public legacy investor HTML.

    PC Naver finance tables normally include: date, close, change, return,
    volume, institutional net shares, foreign net shares, foreign holdings,
    foreign ratio. It does NOT contain reliable personal net shares.
    """
    soup = BeautifulSoup(content, 'html.parser')
    out = []
    for table in soup.find_all('table'):
        heads=' '.join(th.get_text(' ',strip=True) for th in table.find_all('th'))
        compact = re.sub(r'\s+', '', heads)
        # Reject unrelated tables, e.g. daily prices with no investor values.
        if not all(word in compact for word in ('기관','외국인','순매매')):
            continue
        for tr in table.find_all('tr'):
            tds=tr.find_all('td',recursive=False)
            if len(tds)<9 or any(td.get('colspan') for td in tds):
                continue
            day=_date(tds[0].get_text(' ',strip=True))
            if day is None:
                continue
            institution=_safe_number(tds[5].get_text(' ',strip=True))
            foreign=_safe_number(tds[6].get_text(' ',strip=True))
            if foreign is None and institution is None:
                continue
            out.append({'bizdate':day,'foreignerPureBuyQuant':foreign,
                        'organPureBuyQuant':institution})
    return out


def _fetch_pc_page(session, code: str, page: int) -> list[dict]:
    """Single public HTML fetch; no cookies, redirects, blocking bypass or retry."""
    url=f'https://finance.naver.com/item/frgn.naver?code={code}&page={page}'
    try:
        resp=session.get(url,headers={**HEADERS,'Accept':'text/html'},
                         timeout=(3,8),allow_redirects=False)
    except requests.RequestException as exc:
        raise NaverFlowError('네이버 PC 일별 조회 연결 실패') from exc
    if resp.status_code in (401,403,429):
        raise NaverFlowError(f'네이버 PC 일별 조회 접근 제한 HTTP {resp.status_code}; 추가 조회 중단')
    if resp.status_code != 200:
        raise NaverFlowError(f'네이버 PC 일별 조회 HTTP {resp.status_code}')
    if len(resp.content)>2_000_000:
        raise NaverFlowError('네이버 PC 일별 조회 응답 크기 제한 초과')
    content_type=resp.headers.get('Content-Type','').lower()
    if 'html' not in content_type:
        raise NaverFlowError('네이버 PC 일별 조회 HTML 응답 아님')
    return _parse_pc_investor_table(resp.content)


def _pc_history(session, code: str, now: datetime, base: dict) -> dict:
    """Bounded 1-3 pages, only after JSON schema/route missing or too short.

    Distinguish foreign/institution daily share counts from personal unknown.
    Compare overlapping rows; disallow any inconsistent provider values.
    """
    rows=[]
    seen={}
    for page in (1,2,3):
        page_rows=_fetch_pc_page(session,code,page)
        if not page_rows:
            if not rows:
                raise NaverFlowError('네이버 PC 수급표에 날짜별 수량 데이터 없음')
            break
        for r in page_rows:
            day=r['bizdate']
            new=(r.get('foreignerPureBuyQuant'),r.get('organPureBuyQuant'))
            if day in seen:
                if seen[day]!=new:
                    raise NaverFlowError('네이버 PC 수급표의 동일 날짜 값 불일치')
                continue
            seen[day]=new
            rows.append(r)
        if len(rows)>=22:  # allow provisional today, then completed sessions
            break
    parsed=parse_naver({'items':rows},code,now=now,
                       endpoint='finance.naver.com 공개 투자자별 매매동향 HTML')
    if base is not None:
        if _overlap_conflicts(base,parsed):
            raise NaverFlowError('네이버 통합정보와 PC 투자자 동향의 동일 날짜 값 불일치 (KRX/NXT 기준 확인 필요)')
        if parsed.get('as_of','')<base.get('as_of',''):
            raise NaverFlowError('네이버 PC 일별 조회 기준일이 5일 요약보다 오래됨')
        parsed=_merge_verified_same_provider(base,parsed,code,now)
    parsed['source']='네이버 공개 투자자 동향 (모바일 요약 + PC HTML, 비공식)'
    parsed['history_rows_available']=len(parsed['daily'])
    parsed['history_coverage_note']=_coverage_note(parsed)
    parsed['history_expansion_status']=(
       'PC 일별 조회로 외국인·기관 기간 확대; 개인은 확보한 일자만 표시'
       if len(parsed['daily'])>=10 else
       'PC 일별 조회에서도 완료 거래일 10일치 부족')
    return parsed


def get_naver_flows(code: str, now: datetime | None = None, session=None) -> dict:
    """Return up to 20 completed daily rows, when the public source provides them.

    The integration snapshot normally contains only 5 rows. If fewer than 20
    completed sessions are available, make ONE read-only attempt against the
    stock-specific trend endpoint (bounded page size 25). Never synthesize
    missing quantity data, retry after blocks, or mix two sources' daily rows.
    """
    code = stock_code(code)
    now = now or datetime.now(KST)
    session = session or requests.Session()
    mobile_url = f'{NAVER_LEGACY_MOBILE}/api/stock/{code}/integration'
    mobile_result = None
    try:
        mobile_data = _fetch_json(session, mobile_url)
        mobile_result = parse_naver(mobile_data, code, now=now,
                                    endpoint='m.stock.naver.com 종목 통합 정보')
        if _flow_coverage(mobile_result)[0] == 3:
            mobile_result['history_expansion_status'] = '20거래일 확보'
            mobile_result['history_coverage_note'] = _coverage_note(mobile_result)
            return mobile_result
    except NaverEndpointMissing:
        pass
    except NaverFlowError as exc:
        # A blocked, rate-limited or unreachable mobile service must not
        # trigger an alternate request to bypass that restriction.
        if not str(exc).startswith(('네이버 수급 응답', '네이버 수급의', '네이버 수급에서 확정')):
            raise

    # Additional public trend history: 25 allows for a provisional current-day
    # row and non-trading holidays while remaining a strictly bounded request.
    modern_url = (f'{NAVER_NEW}/api/domestic/detail/{code}/trend'
                  '?tradeType=KRX&startIdx=0&pageSize=25')
    try:
        modern_data = _fetch_json(session, modern_url)
        expanded = parse_naver(modern_data, code, now=now,
                               endpoint='stock.naver.com 종목 투자자 동향')
    except NaverFlowError as exc:
        if mobile_result is None:
            raise
        # When the modern route is genuinely missing or returns an unrecognized
        # 200 JSON structure, try one separately documented public route.
        # Never use the additional route to bypass rate-limits, access-denied
        # responses, redirects, or a network failure.
        missing_schema = isinstance(exc, NaverEndpointMissing) or str(exc).startswith(
            ('네이버 수급 응답에', '네이버 수급의 날짜', '네이버 수급에서 확정'))
        if missing_schema and len(mobile_result.get('daily',[])) >= 5:
            front_url = f'{NAVER_LEGACY_MOBILE}/front-api/stock/domestic/trend?code={code}'
            try:
                front_json=_fetch_json(session,front_url)
                front=parse_naver(front_json,code,now=now,
                                  endpoint='m.stock.naver.com/front-api 일별 투자자 동향')
                if _overlap_conflicts(mobile_result,front):
                    raise NaverFlowError('공개 경로 간 동일 날짜 수급 값 불일치')
                if front.get('as_of','') < mobile_result.get('as_of',''):
                    raise NaverFlowError('대체 일별 자료 기준일이 기존 요약보다 오래됨')
                merged=_merge_verified_same_provider(mobile_result,front,code,now)
                chosen=merged if _flow_coverage(merged)>_flow_coverage(mobile_result) else mobile_result
                chosen['history_rows_available']=len(chosen['daily'])
                chosen['history_coverage_note']=_coverage_note(chosen)
                chosen['history_expansion_status']=(
                    '대체 일별 자료에서 확보한 투자자별 기간을 표시' if chosen is merged
                    else '대체 일별 자료도 추가 완료 거래일 부족')
                return chosen
            except NaverFlowError as front_exc:
                exc=NaverFlowError(f'{exc}; 대체 일별 조회도 미확인: {front_exc}')
        # Only missing route / unrecognized schema may fall back to public PC
        # investor HTML. Never use a different route after 401/403/429,
        # redirect, timeout, or conflict (which would bypass a source block).
        recoverable = missing_schema and not any(x in str(exc) for x in (
            '접근이 제한', 'HTTP 429', 'HTTP 403', 'HTTP 401',
            '연결이 실패', '동일 날짜', '불일치', '오래됨'))
        if recoverable:
            try:
                pc = _pc_history(session, code, now, mobile_result)
                if _flow_coverage(pc) > _flow_coverage(mobile_result):
                    pc['history_expansion_status'] += f' · JSON 추가 조회 오류: {exc}'
                    return pc
                exc = NaverFlowError(f'{exc}; PC에서도 기간별 자료 불충분')
            except NaverFlowError as html_exc:
                exc = NaverFlowError(f'{exc}; PC 일별 자료도 미확인: {html_exc}')
        mobile_result['history_expansion_status'] = (
            f'추가 일별 자료 조회 실패: {exc}; 기존 수급만 제공')
        mobile_result['history_rows_available'] = len(mobile_result['daily'])
        mobile_result['history_coverage_note'] = _coverage_note(mobile_result)
        return mobile_result

    if mobile_result is not None and _overlap_conflicts(mobile_result, expanded):
        mobile_result['history_rows_available'] = len(mobile_result['daily'])
        mobile_result['history_expansion_status'] = (
            '두 네이버 조회 경로의 같은 날짜 수급이 불일치하여 기간 확대 보류 (KRX/NXT 기준 확인 필요)')
        mobile_result['history_coverage_note'] = _coverage_note(mobile_result)
        return mobile_result

    if mobile_result is not None and expanded.get('as_of', '') < mobile_result.get('as_of', ''):
        mobile_result['history_rows_available'] = len(mobile_result['daily'])
        mobile_result['history_expansion_status'] = '추가 일별 자료의 최신 기준일이 기존 5일 요약보다 과거입니다.'
        mobile_result['history_coverage_note'] = _coverage_note(mobile_result)
        return mobile_result

    if mobile_result is None:
        chosen = expanded
    else:
        merged = _merge_verified_same_provider(mobile_result, expanded,code,now)
        chosen = merged if _flow_coverage(merged) > _flow_coverage(mobile_result) else mobile_result
    # An additional documented front-api trend route can return longer history
    # when BOTH public JSON routes successfully returned only a short sample.
    # Do not use it to bypass 401/403/429, redirects, or a failed connection.
    alternative_note = ''
    if mobile_result is not None and len(mobile_result.get('daily', [])) >= 5 and len(expanded.get('daily', [])) <= 5:
        front_url = f'{NAVER_LEGACY_MOBILE}/front-api/stock/domestic/trend?code={code}'
        try:
            front_json = _fetch_json(session, front_url)
            front = parse_naver(front_json, code, now=now,
                                endpoint='m.stock.naver.com/front-api 일별 투자자 동향')
            if _overlap_conflicts(chosen, front):
                alternative_note = '3번째 공개 경로의 동일 날짜 값이 달라 합산하지 않음'
            elif front.get('as_of','') < chosen.get('as_of',''):
                alternative_note = '3번째 공개 경로의 기준일이 오래되어 제외'
            else:
                merged = _merge_verified_same_provider(chosen,front,code,now)
                if _flow_coverage(merged) > _flow_coverage(chosen):
                    chosen = merged
                else:
                    alternative_note = '3번째 공개 경로도 필요한 투자자별 완료일 부족'
        except NaverFlowError as exc:
            alternative_note = f'3번째 공개 경로 자료 미확인: {exc}'
    # On a short but valid JSON response, PC offers another public table.
    # If the third JSON endpoint was explicitly denied/blocked or contradicted,
    # never try another route to get around that restriction.
    if len(chosen.get('daily', [])) >= 5 and _flow_coverage(chosen)[0] < 2 and not any(x in alternative_note for x in (
        '접근이 제한', 'HTTP 429', 'HTTP 403', 'HTTP 401',
        '연결이 실패', '동일 날짜 값이 달라')):
        try:
            pc = _pc_history(session,code,now,chosen)
            if _flow_coverage(pc)>_flow_coverage(chosen):
                chosen=pc
                alternative_note += ' · PC HTML에서 완료 거래일 추가 확보'
        except NaverFlowError as html_exc:
            alternative_note += f' · PC 일별 자료 미확인: {html_exc}'
    days = len(chosen.get('daily', []))
    chosen['history_rows_available'] = days
    chosen['history_coverage_note'] = _coverage_note(chosen)
    n20=_flow_coverage(chosen)[0]
    chosen['history_expansion_status'] = (
        '외국인·기관·개인 20거래일 모두 확보' if n20==3
        else f'추가 일별 자료는 받았으나 20일 누적 가능 투자자 {n20}/3개; 세부 수집 건수 확인')
    if alternative_note:
        chosen['history_expansion_status'] += ' · ' + alternative_note
    return chosen
