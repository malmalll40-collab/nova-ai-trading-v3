"""Official OpenDART financial statements + corporate disclosures (read-only).

No unofficial Naver scraping. DART_API_KEY is read only from server environment.
Numbers are historical filings, **not** point-in-time backtest features.
"""
from __future__ import annotations

import io
from functools import lru_cache
import os
import re
import threading
import time
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

API = 'https://opendart.fss.or.kr/api'
TZ_KST = timezone(timedelta(hours=9))
REPORTS = [('11014', '3분기보고서'), ('11012', '반기보고서'), ('11013', '1분기보고서')]
# account ID is strongest, account_nm fallback is only for known exact descriptions.
ACCOUNTS = {
    'revenue': ({'ifrs-full_Revenue','ifrs_Revenue','ifrs-full_RevenueFromContractsWithCustomers',
                 'ifrs-full_SalesRevenueGoodsNet','dart_Revenue'},
                {'매출액','매출액(수익)','매출','영업수익','수익(매출액)'}),
    'operating_profit': ({'dart_OperatingIncomeLoss','ifrs-full_ProfitLossFromOperatingActivities',
                          'ifrs_ProfitLossFromOperatingActivities'}, {'영업이익','영업이익(손실)'}),
    'net_income': ({'ifrs-full_ProfitLoss','ifrs_ProfitLoss'}, {'당기순이익','당기순이익(손실)'}),
    'assets': ({'ifrs-full_Assets','ifrs_Assets'}, {'자산총계'}),
    'liabilities': ({'ifrs-full_Liabilities','ifrs_Liabilities'}, {'부채총계'}),
    'equity': ({'ifrs-full_Equity','ifrs_Equity'}, {'자본총계'}),
    'operating_cashflow': ({'ifrs-full_CashFlowsFromUsedInOperatingActivities',
                            'ifrs_CashFlowsFromUsedInOperatingActivities'},
                           {'영업활동현금흐름','영업활동으로 인한 현금흐름','영업활동현금흐름(유출)'}),
}
SECTIONS = {'revenue':('IS','CIS'), 'operating_profit':('IS','CIS'),
            'net_income':('IS','CIS'), 'assets':('BS',), 'liabilities':('BS',),
            'equity':('BS',), 'operating_cashflow':('CF',)}
MARKETS = {'Y':'코스피', 'K':'코스닥', 'N':'코넥스', 'E':'기타'}

class DARTError(Exception):
    def __init__(self, message: str, code: str = ''):
        super().__init__(message)
        self.code = code


def parse_amount(raw: Any) -> int | None:
    if raw is None or isinstance(raw, bool):
        return None
    s=str(raw).strip().replace(',','').replace(' ','').replace('−','-')
    if s in ('','-','—','N/A','null'):
        return None
    neg=False
    if s.startswith('(') and s.endswith(')'):
        neg=True;s=s[1:-1]
    if s.startswith('△') or s.startswith('▲'):
        neg=True;s=s[1:]
    if not re.fullmatch(r'[+-]?\d+(?:\.\d+)?',s):
        return None
    n=float(s)
    return int(-abs(n) if neg else n)


def _items_for_financial(rows: list[dict]) -> dict:
    """Extract exact standard accounts, strict statement section, top-level only."""
    out={}
    for key, (ids, names) in ACCOUNTS.items():
        sections=SECTIONS[key]
        candidates=[]
        for row in rows:
            if row.get('sj_div') not in sections:
                continue
            detail=str(row.get('account_detail','-')).strip()
            if detail not in ('-', '', '연결재무제표'):
                continue
            value=parse_amount(row.get('thstrm_amount'))
            if value is None:
                continue
            aid=str(row.get('account_id','')).strip()
            name=str(row.get('account_nm','')).strip().replace(' ', '')
            std_id=aid in ids
            std_name=name in {n.replace(' ','') for n in names}
            if not (std_id or std_name):
                continue
            # Prefer standardized tags to entity-specific accounts, and official IS vs CIS.
            candidates.append((0 if std_id else 1, sections.index(row.get('sj_div')),
                               int(row.get('ord',9999)) if str(row.get('ord','')).isdigit() else 9999,
                               value))
        if candidates:
            candidates.sort(key=lambda x:x[:3]); out[key]=candidates[0][3]
        else:
            out[key]=None
    return out


def _pct(a,b) -> float | None:
    if a is None or b is None or b==0:
        return None
    return round(a/b*100,2)


def _growth(a,b) -> float | None:
    if a is None or b is None or b<=0:
        return None
    return round((a/b-1)*100,2)


def _summary_metrics(curr: dict, prev: dict|None):
    res={key:curr.get(key) for key in ACCOUNTS}
    res.update({
        'operating_margin_pct':_pct(curr.get('operating_profit'),curr.get('revenue')),
        'debt_ratio_pct':_pct(curr.get('liabilities'),curr.get('equity')) if (curr.get('equity') or 0)>0 else None,
        'revenue_yoy_pct':_growth(curr.get('revenue'),(prev or {}).get('revenue')),
        'operating_profit_yoy_pct':_growth(curr.get('operating_profit'),(prev or {}).get('operating_profit')),
    })
    return res


def _findings(metrics: dict) -> list[dict]:
    out=[]
    op=metrics.get('operating_profit'); cash=metrics.get('operating_cashflow')
    if op is not None and op<0:out.append({'level':'risk','message':'최근 연간 영업이익이 적자입니다.'})
    if cash is not None and cash<0:out.append({'level':'risk','message':'최근 연간 영업활동 현금흐름이 음수입니다.'})
    ratio=metrics.get('debt_ratio_pct')
    if ratio is not None and ratio>200:out.append({'level':'caution','message':'최근 연간 부채비율이 200%를 넘습니다. 업종별 기준으로 추가 검토하세요.'})
    growth=metrics.get('revenue_yoy_pct')
    if growth is not None and growth < -15:out.append({'level':'caution','message':'전년 대비 연간 매출이 15% 넘게 감소했습니다.'})
    if not out:out.append({'level':'info','message':'자동 위험 경고 조건은 확인되지 않았지만, 이는 재무적으로 안전하다는 의미가 아닙니다.'})
    return out


def _parse_registry(payload: bytes) -> dict[str,dict]:
    """DART corpCode.xml returns a ZIP containing CORPCODE.xml. Size caps avoid ZIP bombs."""
    if len(payload)>25_000_000:raise DARTError('기업 고유번호 파일이 허용된 크기를 초과했습니다.')
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as z:
            members=[i for i in z.infolist() if i.filename.lower().endswith('.xml')]
            if len(members)!=1 or members[0].file_size>80_000_000:
                raise DARTError('기업 고유번호 압축파일 형식이 올바르지 않습니다.')
            raw=z.read(members[0])
    except (zipfile.BadZipFile, ValueError, RuntimeError) as exc:
        raise DARTError('DART 기업 고유번호 ZIP 파일을 해석할 수 없습니다.') from exc
    registry={}
    try:
        context=ET.iterparse(io.BytesIO(raw),events=('end',))
        for _,elem in context:
            if elem.tag!='list':continue
            code=(elem.findtext('stock_code') or '').strip()
            corp=(elem.findtext('corp_code') or '').strip()
            name=(elem.findtext('corp_name') or '').strip()
            if re.fullmatch(r'\d{6}',code) and re.fullmatch(r'\d{8}',corp):
                registry[code]={'corp_code':corp,'corp_name':name}
            elem.clear()
    except ET.ParseError as exc:
        raise DARTError('DART 기업 고유번호 XML 파싱에 실패했습니다.') from exc
    if not registry:raise DARTError('DART에서 상장 종목코드를 확인하지 못했습니다.')
    return registry


class DARTClient:
    def __init__(self, key:str, session=None, clock=None):
        if not re.fullmatch(r'[A-Za-z0-9]{40}',key or ''):
            raise DARTError('OpenDART 인증키(40자리)가 서버에 설정되지 않았습니다.', 'no_key')
        self.key=key
        self.session=session or requests.Session()
        self.clock=clock or (lambda:datetime.now(TZ_KST))
        self._registry={}
        self._registry_until=0
        self._registry_lock=threading.Lock()

    def _call(self, resource:str, **params) -> dict:
        # Fixed resource path only; no user-specified URL (SSRF guard).
        if resource not in ('company.json','list.json','fnlttSinglAcntAll.json'):
            raise ValueError('Unsupported DART resource')
        try:
            resp=self.session.get(f'{API}/{resource}',params={'crtfc_key':self.key,**params},timeout=(5,15))
            resp.raise_for_status()
            data=resp.json()
        except (requests.RequestException,ValueError) as exc:
            # Never interpolate exc: exception may include URL with secret key.
            raise DARTError('OpenDART 서버 통신에 실패했습니다. 네트워크와 API 상태를 확인하세요.', 'network') from None
        if not isinstance(data,dict):raise DARTError('OpenDART 응답 형식이 올바르지 않습니다.', 'format')
        status=str(data.get('status',''))
        if status=='013':return {'status':'013','list':[],'message':'조회된 데이터가 없습니다.'}
        if status!='000':
            msgs={'010':'등록되지 않은 인증키입니다.', '011':'사용 중지된 인증키입니다.',
                  '012':'OpenDART에서 서버 IP를 허용하지 않았습니다.', '020':'OpenDART 요청 한도를 초과했습니다.',
                  '100':'요청 인자 오류입니다.', '800':'OpenDART 점검 중입니다.', '901':'인증키의 개인정보 보유기간이 만료됐습니다.'}
            raise DARTError(msgs.get(status, 'OpenDART에서 오류를 반환했습니다.'),status)
        return data

    def registry(self) -> dict:
        with self._registry_lock:
            if self._registry and time.monotonic()<self._registry_until:return self._registry
            try:
                resp=self.session.get(f'{API}/corpCode.xml',params={'crtfc_key':self.key},timeout=(5,25))
                resp.raise_for_status()
                registry=_parse_registry(resp.content)
            except requests.RequestException:
                raise DARTError('OpenDART 기업 고유번호 파일 다운로드에 실패했습니다.', 'registry_network') from None
            self._registry=registry
            self._registry_until=time.monotonic()+24*3600
            return registry

    def _statement(self,corp_code:str,year:int,code:str):
        for div in ('CFS','OFS'):
            d=self._call('fnlttSinglAcntAll.json',corp_code=corp_code,bsns_year=str(year),reprt_code=code,fs_div=div)
            rows=d.get('list',[])
            if isinstance(rows,list) and rows:return {'fs_div':div,'metrics':_items_for_financial(rows)}
        return None

    def overview(self, ticker:str, *, years:int=3) -> dict:
        if not re.fullmatch(r'\d{6}\.(KS|KQ)',ticker):
            raise DARTError('OpenDART는 국내 코스피·코스닥 종목코드만 조회합니다.', 'invalid_ticker')
        if not isinstance(years,int) or years<2 or years>4:raise DARTError('재무제표 기간은 2~4년입니다.', 'invalid_years')
        stock_code=ticker[:6]
        entry=self.registry().get(stock_code)
        if not entry:raise DARTError('DART에서 해당 종목코드의 기업 고유번호를 찾지 못했습니다.', 'unknown_stock')
        corp_code=entry['corp_code']
        company=self._call('company.json',corp_code=corp_code)
        cls=company.get('corp_cls','')
        if company.get('stock_code') and company.get('stock_code')!=stock_code:
            raise DARTError('DART 기업개황의 상장 종목코드와 요청 코드가 일치하지 않습니다.', 'stock_mismatch')
        if cls in ('Y','K') and ((ticker.endswith('.KS') and cls!='Y') or (ticker.endswith('.KQ') and cls!='K')):
            raise DARTError('DART 시장 구분과 요청 종목코드가 일치하지 않습니다. 상장 변경 여부를 확인하세요.', 'market_mismatch')
        today=self.clock().date()
        # Current-year annual report is generally not filed until the following year.
        annual=[]
        for year in range(today.year-1,today.year-years-1,-1):
            if year<2015:break
            stmt=self._statement(corp_code,year,'11011')
            if stmt:
                annual.append({'year':year,'type':'사업보고서','fs_div':stmt['fs_div'],'metrics':stmt['metrics']})
        current=annual[0] if annual else None
        previous=annual[1] if len(annual)>1 else None
        metrics=_summary_metrics(current['metrics'],previous['metrics'] if previous and previous['fs_div']==current['fs_div'] else None) if current else {}
        # Most recent *available* interim report. Not annual-comparable and not used for YoY.
        interim=None
        for yr in (today.year,today.year-1):
            if interim:break
            for code,name in REPORTS:
                stmt=self._statement(corp_code,yr,code)
                if stmt:
                    interim={'year':yr,'type':name,'fs_div':stmt['fs_div'],'metrics':stmt['metrics'],
                             'note':'분기·반기 손익계정은 누적 금액이 포함될 수 있어 연간 수치와 직접 비교하지 않습니다.'}
                    break
        begin=(today-timedelta(days=180)).strftime('%Y%m%d')
        filed=self._call('list.json',corp_code=corp_code,bgn_de=begin,end_de=today.strftime('%Y%m%d'),page_no=1,page_count=20)
        disclosures=[]
        for row in (filed.get('list') or [])[:20]:
            rid=str(row.get('rcept_no',''))
            if not re.fullmatch(r'\d{14}',rid):continue
            disclosures.append({'date':str(row.get('rcept_dt','')),'title':str(row.get('report_nm',''))[:180],
                                'receipt_no':rid,'url':f'https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rid}'})
        return {
            'status':'ok' if current else 'partial',
            'provider':'금융감독원 OpenDART','ticker':ticker,'company_name':company.get('corp_name') or entry['corp_name'],
            'market':MARKETS.get(cls,'확인 필요'),'corp_code':corp_code,
            'retrieved_at':self.clock().isoformat(timespec='seconds'),
            'annual':annual,'annual_latest':current['year'] if current else None,
            'metrics':metrics,'findings':_findings(metrics) if current else [],
            'interim':interim,'disclosures':disclosures,
            'valuation_note':'PER·PBR·현재 주가는 OpenDART 재무제표 값만으로 직접 산출하지 않습니다.',
            'analysis_note':'재무 분석은 별도 참고 지표이며 기존 13개 역할의 기술 신호·과거 적중률에는 반영하지 않습니다. 재무정보의 과거 시점 가용성(공시일)을 고려한 검증 후 통합해야 합니다.',
            'filing_note':'최신 재무 수치는 실제 공시된 보고서 기준이며 공시 지연·정정될 수 있습니다. 원문을 확인하세요.',
        }


@lru_cache(maxsize=2)
def _client_for_key(key: str) -> DARTClient:
    return DARTClient(key)

def get_client() -> DARTClient:
    key=os.environ.get('OPENDART_API_KEY','').strip()
    return _client_for_key(key)
