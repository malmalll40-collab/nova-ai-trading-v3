"""Read-only, point-of-observation market/sector/flow inputs for NOVA research.

Markets: Yahoo daily KOSPI/KOSDAQ reference index.
Sectors: explicit researched KODEX sector *ETF proxy* mappings ONLY; never
claim the ETF is an official industry index or infer a company's industry.
Investor flows: verified historical daily share counts from existing KIS/Naver
responses.  Shares are NOT KRW; no synthetic flow amounts or personal estimates.

Live-observed context is not a point-in-time backtesting dataset. Missing or
stale sources always remain missing, and no input can alter actionable signals.
"""
from __future__ import annotations

from datetime import datetime, time as clocktime
from zoneinfo import ZoneInfo
import math
import re

import pandas as pd

KST = ZoneInfo('Asia/Seoul')
MARKET_CODES = {'.KS': ('^KS11', 'KOSPI'), '.KQ': ('^KQ11', 'KOSDAQ')}
# Conservative explicit memberships (not universal company-sector inference).
# ETFs are market-traded proxies; they include the target among constituents.
# Sources: Kodex semiconductor issuer brochure (2026-05), ETF constituents
# published through Nov 2026. Verify ETF constituents periodically.
ETF_PROXIES = {
    '005930': ('091160.KS', 'KODEX 반도체 ETF'),
    '000660': ('091160.KS', 'KODEX 반도체 ETF'),
    '042700': ('091160.KS', 'KODEX 반도체 ETF'),
    '036930': ('091160.KS', 'KODEX 반도체 ETF'),
    '105560': ('091170.KS', 'KODEX 은행 ETF'),
    '055550': ('091170.KS', 'KODEX 은행 ETF'),
    '086790': ('091170.KS', 'KODEX 은행 ETF'),
    '316140': ('091170.KS', 'KODEX 은행 ETF'),
    '005380': ('091180.KS', 'KODEX 자동차 ETF'),
    '000270': ('091180.KS', 'KODEX 자동차 ETF'),
    '012330': ('091180.KS', 'KODEX 자동차 ETF'),
}


def _row_date(x):
    t = pd.Timestamp(x)
    if pd.isna(t):
        raise ValueError('날짜 미확인')
    if t.tzinfo is not None:
        t=t.tz_convert(KST)
    return t.date()


def _date_from_flow(x):
    if not isinstance(x, str) or not re.fullmatch(r'\d{8}', x):
        raise ValueError('수급 거래일 오류')
    return datetime.strptime(x, '%Y%m%d').date()


def _status(status, detail, **kw):
    return {'status': status, 'detail':detail, **kw}


def fetch_yahoo_reference(ticker: str, now: datetime | None = None, *, period: str = '9mo') -> pd.DataFrame:
    """Strictly whitelisted reference tickers, bounded Yahoo fetch.

    `available_at` is the instant the live system OBSERVED the already-released
    price, not a claimed historical publication time. No retrospective use.
    """
    allow={'^KS11','^KQ11','091160.KS','091170.KS','091180.KS'}
    if ticker not in allow:
        raise ValueError('등록되지 않은 시장·업종 참고 지표')
    if period not in ('9mo', '2y'):
        raise ValueError('시장지수 기간은 9mo/2y만 허용')
    import yfinance as yf
    now = now or datetime.now(KST)
    rows = yf.Ticker(ticker).history(period=period, interval='1d', auto_adjust=True,
                                    prepost=False, raise_errors=True, timeout=10)
    if rows is None or rows.empty or 'Close' not in rows:
        raise ValueError('참고 지표 일봉 미수신')
    closes=pd.to_numeric(rows['Close'],errors='coerce')
    if closes.isna().any() or not closes.map(lambda x:math.isfinite(float(x)) and float(x)>0).all():
        raise ValueError('참고 지표 종가 유효성 오류')
    dates=[_row_date(x) for x in rows.index]
    if len(set(dates))!=len(dates):
        raise ValueError('참고 지표 날짜 중복')
    # Even if Yahoo emits an intraday daily candle, do not pass it as completed.
    current=now.astimezone(KST)
    cutoff=clocktime(16,0)
    kept=[(dt,float(c)) for dt,c in zip(dates,closes)
          if dt<current.date() or dt==current.date() and current.time()>=cutoff]
    if not kept:
        raise ValueError('완료된 참고 지표 일봉이 없음')
    frame=pd.DataFrame({'Close':[v for _,v in kept]},
                       index=pd.Index([d.isoformat() for d,_ in kept]))
    frame['available_at']=current.isoformat()
    return frame


def flow_share_frame(payload: dict | None, observed_at: datetime) -> tuple[pd.DataFrame | None, dict]:
    """Translate ONLY confirmed completed daily *shares*; no shares->KRW hack."""
    if payload is None:
        return None,_status('UNAVAILABLE','투자자 수급 화면에서 종목을 먼저 조회하고 분석을 다시 실행하세요 (20일 미확보 시 미확인)')
    if not isinstance(payload,dict) or payload.get('unit') != 'shares':
        return None,_status('UNAVAILABLE','수급 단위(주) 또는 원천 확인 불가')
    rows=payload.get('daily')
    if not isinstance(rows,list) or not rows:
        return None,_status('UNAVAILABLE','수급 일별 자료 없음')
    out={}
    for record in rows[:100]:
        if not isinstance(record,dict):
            continue
        try:
            day=_date_from_flow(record.get('date'))
        except (ValueError,TypeError):
            continue
        if day >= observed_at.date():
            continue # current unconfirmed session never enters totals
        if day.isoformat() in out:
            return None,_status('UNAVAILABLE','동일 거래일 중복 수급')
        values=[]
        for col in ('foreign','institution'):
            v=record.get(col)
            if isinstance(v,bool) or not isinstance(v,int):
                v=None
            values.append(v)
        out[day.isoformat()]=values
    if not out:
        return None,_status('UNAVAILABLE','확정된 거래일의 수급 자료 없음')
    # Keep missing rows null, so high_precision._source_frame fails closed.
    d=pd.DataFrame.from_dict(out,orient='index',columns=['foreign_net_shares','institution_net_shares']).sort_index()
    d['available_at']=observed_at.isoformat()
    counts={field:int(d[field].notna().sum()) for field in ['foreign_net_shares','institution_net_shares']}
    completed=int(d.dropna(subset=['foreign_net_shares','institution_net_shares']).shape[0])
    status='READY' if completed>=20 else 'PARTIAL'
    return d,_status(status, f'외국인·기관 동시 확인 {completed}/20거래일 · 단위 주(금액 아님) · 과거 발표시각 미검증',
                    rows=len(d),complete_rows=completed,counts=counts,
                    source=str(payload.get('source','수급 자료'))[:90],as_of=max(out))


def build_shadow_inputs(symbol: str, *, now: datetime, ref_loader, flow_loader,
                        historical_asof: str | None = None) -> dict:
    """Independent fail-closed provider fetches; front-end gets auditable status.

    Historic scanner evaluations must never fetch today's sources to evaluate
    past decisions; all three remain UNAVAILABLE there.
    """
    data={'benchmark':None, 'sector':None, 'flows':None, 'diagnostics':{}}
    if historical_asof is not None:
        msg='과거 기준일 분석에는 현재 수집한 외부 자료를 소급 연결하지 않음'
        data['diagnostics']={k:_status('UNAVAILABLE',msg) for k in ('benchmark','sector','flows')}
        return data
    if symbol.endswith('.KS'):
        idx,market=MARKET_CODES['.KS']
    elif symbol.endswith('.KQ'):
        idx,market=MARKET_CODES['.KQ']
    else:
        return data
    try:
        bm=ref_loader(idx)
        data['benchmark']=bm
        data['diagnostics']['benchmark']=_status('READY','시장지수 KIS 완료 일봉 (독립 원천 대조 전)',
                symbol=idx,name=market,as_of=str(bm.index[-1]),rows=len(bm))
    except Exception:
        data['diagnostics']['benchmark']=_status('UNAVAILABLE','시장지수 KIS 수집 실패/접근 제한')
    code=symbol[:6]
    proxy=ETF_PROXIES.get(code)
    if proxy:
        try:
            sec=ref_loader(proxy[0])
            data['sector']=sec
            data['diagnostics']['sector']=_status('READY','등록된 업종 ETF 대용 지표 (공식 업종지수 아님)',
                symbol=proxy[0],name=proxy[1],as_of=str(sec.index[-1]),rows=len(sec),kind='ETF_PROXY')
        except Exception:
            data['diagnostics']['sector']=_status('UNAVAILABLE',f'{proxy[1]} 시세 수집 실패/접근 제한',kind='ETF_PROXY')
    else:
        data['diagnostics']['sector']=_status('UNAVAILABLE','종목-업종 ETF 대응 관계 미검증; 업종 임의 추정 금지')
    try:
        payload=flow_loader(code)
        frame,meta=flow_share_frame(payload,now)
        data['flows']=frame
        data['diagnostics']['flows']=meta
    except Exception:
        data['diagnostics']['flows']=_status('UNAVAILABLE','수급 일별 자료 조회 실패 또는 수급 조회 화면에서 미확보')
    return data
