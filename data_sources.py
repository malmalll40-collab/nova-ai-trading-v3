"""KIS authenticated REST quotes and minute/daily history; yfinance non-guaranteed quotes; synthetic DEMO.

KIS uses read-only quotation endpoints; NO broker ordering code is present.
Credentials are passed in memory, never written to a file by this module.
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import re, threading, time
import numpy as np
import pandas as pd
import requests
from engine import clean_bars
from nova_v578_kis_telemetry import KIS_LOAD

MINUTE_INTERVALS = {'1분': 1, '5분': 5, '15분': 15}
KST=ZoneInfo('Asia/Seoul')
NY=ZoneInfo('America/New_York')
TOKENS: dict[str,tuple[str,float]]={}
TOKEN_LOCK=threading.Lock()

@dataclass
class MarketData:
    bars:pd.DataFrame
    provider:str
    symbol:str
    interval:str
    quote:float|None
    quote_time:str
    retrieved_at:str
    freshness:str
    note:str
    currency:str
    synthetic:bool=False
    daily_integrity:dict|None=None


def ticker_format(ticker:str)->str:
    raw=ticker.strip().upper()
    if re.fullmatch(r'\d{6}',raw):
        # Six digits alone do NOT identify the exchange. Mapping known items is
        # safe; unknown symbols must be selected from search with .KS/.KQ.
        from stock_lookup import FALLBACK
        known = {symbol for _, symbol, _ in FALLBACK if symbol.startswith(raw + '.')}
        if len(known) == 1:
            return next(iter(known))
        raise ValueError('국내 6자리 종목코드는 코스피·코스닥 구분이 필요합니다. 종목 검색 결과에서 선택하거나 .KS/.KQ를 입력하세요.')
    if not re.fullmatch(r'[A-Z0-9^.=-]{1,24}',raw):
        raise ValueError('종목코드 형식이 잘못되었습니다. 예: 005930.KS, 086520.KQ')
    return raw


def market_zone(ticker:str):
    return KST if ticker.endswith(('.KS','.KQ')) else NY


def is_session_finished(now:datetime, mode:str, symbol:str)->bool:
    zone=market_zone(symbol)
    here=now.astimezone(zone)
    close = (15,30) if zone==KST else (16,0)
    return here.hour*60+here.minute >= close[0]*60+close[1]


def discard_unfinished(bars:pd.DataFrame, mode:str,symbol:str,now:datetime|None=None)->pd.DataFrame:
    """Exclude open candle according to timestamp convention (minute bar start); daily current partial.
    tz-aware index required for intraday. yfinance/KIS both use start-time minute timestamps.
    """
    if bars.empty:return bars
    now=now or datetime.now(timezone.utc)
    d=bars.sort_index().copy()
    if mode=='스윙':
        local=now.astimezone(market_zone(symbol))
        last=pd.Timestamp(d.index[-1])
        last_date=last.date()
        if last_date==local.date() and not is_session_finished(now,mode,symbol):
            return d.iloc[:-1]
    else:
        if d.index.tz is None:
            raise ValueError('분봉의 시간대 정보가 없습니다')
        delta=timedelta(minutes=MINUTE_INTERVALS[mode])
        if d.index[-1].to_pydatetime()+delta>now.astimezone(d.index.tz):
            return d.iloc[:-1]
    return d


def demo_data(mode:str,seed:int=240)->MarketData:
    rng=np.random.default_rng(seed)
    if mode=='스윙':
        idx=pd.bdate_range(end=pd.Timestamp.now(tz=KST).date(),periods=330)
        vol=0.012
    else:
        days=pd.bdate_range(end=pd.Timestamp.now(tz=KST).date(),periods=5)
        # 9:00 to 15:29 each session (synthetic 1-minute candles).
        dates=[pd.Timestamp(day.date(),tz=KST)+pd.Timedelta(hours=9) for day in days]
        idx=pd.DatetimeIndex([x+pd.Timedelta(minutes=m) for x in dates for m in range(390)])
        vol=.0008
    ret=rng.normal(.00014 if mode=='스윙' else .000016,vol,len(idx))
    close=70_000*np.exp(np.cumsum(ret))
    op=np.r_[close[0],close[:-1]]*(1+rng.normal(0,vol*.12,len(idx)))
    high=np.maximum(op,close)*(1+np.abs(rng.normal(0,vol*.28,len(idx))))
    low=np.minimum(op,close)*(1-np.abs(rng.normal(0,vol*.28,len(idx))))
    volume=rng.integers(600,6000,len(idx)).astype(float)
    df=pd.DataFrame({'Open':op,'High':high,'Low':low,'Close':close,'Volume':volume},index=idx)
    if mode in ('5분', '15분'): df=aggregate_minutes(df, MINUTE_INTERVALS[mode])
    # All entries are hypothetical and must not be interpreted as live.
    return MarketData(df,'DEMO 가상 시계열','DEMO',mode,float(df['Close'].iloc[-1]),'가상 시점','가상 데이터',
                      '가상 데이터 · 실제 시세/수익률/적중률이 아닙니다','연습용으로만 사용', 'KRW',True)


def aggregate_minutes(bars:pd.DataFrame, minutes:int)->pd.DataFrame:
    """Exchange-session-aligned 5/15 minute OHLCV without overnight mixing.

    Input must be exchange-local time-zone-aware one-minute bars.  The label is
    the starting minute of the bucket; the last unfinished bucket is discarded
    by discard_unfinished AFTER aggregation, never by this helper.
    """
    if minutes not in (5,15):
        raise ValueError('지원하지 않는 분봉 집계 간격입니다.')
    if bars.index.tz is None:
        raise ValueError('KIS 분봉 집계에는 거래소 시간대가 필요합니다.')
    if bars.empty:return bars.iloc[:0]
    pieces=[]
    for _,chunk in bars.groupby(bars.index.date):
        # Session is 09:00-15:30 KST: 5 and 15 both divide the opening time.
        r=chunk.resample(f'{minutes}min',label='left',closed='left').agg(
            {'Open':'first','High':'max','Low':'min','Close':'last','Volume':'sum'})
        pieces.append(r.dropna(subset=['Open','Close']))
    return clean_bars(pd.concat(pieces).sort_index()) if pieces else bars.iloc[:0]


def aggregate_5m(bars:pd.DataFrame)->pd.DataFrame:
    return aggregate_minutes(bars,5)


def yahoo_data(ticker:str,mode:str)->MarketData:
    try:import yfinance as yf
    except ImportError as e:raise RuntimeError('yfinance 설치가 필요합니다. pip install -r requirements.txt') from e
    ticker=ticker_format(ticker)
    interval={'1분':'1m','5분':'5m','15분':'15m','스윙':'1d'}[mode]
    period={'1분':'7d','5분':'30d','15분':'60d','스윙':'2y'}[mode]
    # Disable automatic attempts to scrape additional page resources.
    try:
        hist=yf.Ticker(ticker).history(period=period,interval=interval,auto_adjust=True,prepost=False,raise_errors=True,timeout=12)
    except Exception as exc:
        raise RuntimeError(f'Yahoo 시세 호출 실패: {type(exc).__name__} (종목/네트워크/서비스 상태 확인)') from exc
    if hist is None or hist.empty: raise RuntimeError('Yahoo에서 해당 종목의 시세가 반환되지 않았습니다')
    d=clean_bars(hist)
    now=datetime.now(timezone.utc)
    d=discard_unfinished(d,mode,ticker,now)
    if len(d)<65:raise ValueError(f'{ticker}: 완료된 봉이 {len(d)}개뿐입니다 (필요 65개). 다른 주기/종목을 선택하세요.')
    last=d.index[-1]
    return MarketData(d,'Yahoo Finance (지연·이용조건 미검증)',ticker,interval,float(d['Close'].iloc[-1]),str(last),
                      now.isoformat(timespec='seconds'),
                      '지연 미확인 · 실시간 보장 없음',
                      'Yahoo 데이터는 거래소 공식 실시간 시세가 아닐 수 있습니다. 마지막 완료봉 종가 기준 판단입니다.',
                      'KRW' if ticker.endswith(('.KS','.KQ')) else 'USD')


class KIS:
    BASE='https://openapi.koreainvestment.com:9443'
    def __init__(self,key:str,secret:str):
        if not key.strip() or not secret.strip():raise ValueError('KIS App Key와 App Secret을 모두 입력하세요')
        self.key,self.secret=key.strip(),secret.strip()
        self.session=requests.Session()
        with KIS_LOAD.foreground():
            self.token=self._token()

    def _request_token(self)->tuple[str,int]:
        try:
            with KIS_LOAD.physical(token=True):
                r=self.session.post(self.BASE+'/oauth2/tokenP',json={'grant_type':'client_credentials',
                    'appkey':self.key,'appsecret':self.secret},timeout=12)
            try:
                r.raise_for_status()
                data=r.json()
            except (requests.RequestException,ValueError):
                KIS_LOAD.failure()
                raise
            token=data.get('access_token')
            if not token:
                KIS_LOAD.failure()
                raise ValueError('토큰이 응답에 없습니다')
            duration=int(data.get('expires_in',3600))
            return token, duration
        except (requests.RequestException,ValueError,KeyError) as exc:
            raise RuntimeError('KIS 접근토큰 발급 실패. 키/활성화/네트워크/호출제한을 확인하세요.') from exc

    def _token(self)->str:
        import hashlib
        from kis_token_store import load_or_issue, persistent_cache_enabled
        cache_id=hashlib.sha256((self.key+'|'+self.secret).encode()).hexdigest()
        # Hold the lock for the entire lookup + renewal to prevent concurrent
        # network requests even without a persistent database configured.
        with TOKEN_LOCK:
            saved=TOKENS.get(cache_id)
            if saved and saved[1]>time.time()+300:return saved[0]
            if persistent_cache_enabled():
                token, expiry=load_or_issue(cache_id,self.key,self.secret,self._request_token)
            else:
                token, duration=self._request_token()
                expiry=time.time()+min(max(duration,1),24*3600)
            TOKENS[cache_id]=(token,expiry)
            return token

    def get(self,path:str,tr_id:str,params:dict)->dict:
        with KIS_LOAD.foreground():
            return self._get_foreground(path,tr_id,params)

    def _get_foreground(self,path:str,tr_id:str,params:dict)->dict:
        headers={'authorization':f'Bearer {self.token}', 'appkey':self.key,
                 'appsecret':self.secret,'tr_id':tr_id,'custtype':'P','content-type':'application/json; charset=utf-8'}
        # GET quotes are read-only. Retry a transient HTTP failure or timeout
        # just once, with bounded backoff. Never retry broker business errors.
        # Preserve *only* a non-sensitive status category for chart diagnostics.
        for attempt in range(2):
            try:
                with KIS_LOAD.physical(retry=attempt>0):
                    response=self.session.get(self.BASE+path,headers=headers,params=params,timeout=12)
            except requests.Timeout:
                if attempt == 0:
                    time.sleep(1.0)
                    continue
                raise RuntimeError('KIS_UPSTREAM_TIMEOUT') from None
            except requests.RequestException:
                if attempt == 0:
                    time.sleep(1.0)
                    continue
                raise RuntimeError('KIS_UPSTREAM_NETWORK') from None
            status=int(getattr(response,'status_code',0))
            if not 200 <= status < 300:
                KIS_LOAD.failure()
                if status in (429,500,502,503,504) and attempt == 0:
                    time.sleep(2.0 if status == 429 else 1.0)
                    continue
                raise RuntimeError(f'KIS_HTTP_{status if 100 <= status <= 599 else 0}') from None
            try:
                data=response.json()
            except ValueError:
                KIS_LOAD.failure()
                raise RuntimeError('KIS_BAD_JSON') from None
            if not isinstance(data,dict):
                KIS_LOAD.failure()
                raise RuntimeError('KIS_BAD_PAYLOAD') from None
            if data.get('rt_cd')!='0':
                KIS_LOAD.failure()
                # Existing broker-code behavior retained. Never log msg1,
                # credentials, headers, URL query or the full upstream body.
                raise RuntimeError(f"KIS 시세 오류 {data.get('msg_cd','unknown')}: {str(data.get('msg1',''))[:120]}")
            time.sleep(.10)  # modest pacing to respect rate limits
            return data
        raise RuntimeError('KIS_RETRY_EXHAUSTED')  # defensive; unreachable

    def quote(self,code:str)->tuple[float,str]:
        r=self.get('/uapi/domestic-stock/v1/quotations/inquire-price','FHKST01010100',
                   {'FID_COND_MRKT_DIV_CODE':'J','FID_INPUT_ISCD':code})
        out=r.get('output') or {}
        price=float(out.get('stck_prpr') or 0)
        if price<=0:raise RuntimeError('KIS 현재가 값이 유효하지 않습니다')
        return price,str(out.get('stck_cntg_hour') or '조회 시점 스냅샷(원체결시각 미제공)')

    def daily(self,code:str,now:datetime)->pd.DataFrame:
        start=(now-timedelta(days=600)).strftime('%Y%m%d')
        end=now.strftime('%Y%m%d')
        rows=[];seen=set()
        for _ in range(8):
            r=self.get('/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice','FHKST03010100',
                {'FID_COND_MRKT_DIV_CODE':'J','FID_INPUT_ISCD':code,'FID_INPUT_DATE_1':start,
                 'FID_INPUT_DATE_2':end,'FID_PERIOD_DIV_CODE':'D','FID_ORG_ADJ_PRC':'0'})
            records=r.get('output2') or []
            if not records:break
            rows.extend(records)
            dates=[x.get('stck_bsop_date','') for x in records if x.get('stck_bsop_date')]
            if not dates:break
            earliest=min(dates)
            if earliest<=start or earliest in seen or len(records)<100:break
            seen.add(earliest)
            end=(datetime.strptime(earliest,'%Y%m%d')-timedelta(days=1)).strftime('%Y%m%d')
        return kis_ohlc(rows,'스윙')

    def daily_price(self, code: str)->pd.DataFrame:
        """Independent KIS recent-session OHLC source; strictly read-only.

        Uses the same adjustment option as the primary KIS chart. Its values
        are still verified against independent raw prices before repair.
        """
        payload=self.get('/uapi/domestic-stock/v1/quotations/inquire-daily-price',
                         'FHKST01010400',
                         {'FID_COND_MRKT_DIV_CODE':'J','FID_INPUT_ISCD':code,
                          'FID_PERIOD_DIV_CODE':'D','FID_ORG_ADJ_PRC':'0'})
        records=payload.get('output') or []
        if not isinstance(records,list):raise RuntimeError('KIS_SECONDARY_BAD_PAYLOAD')
        return kis_ohlc(records,'스윙')

    def period_bars(self, code: str, timeframe: str, now: datetime | None = None,
                    *, recent_only: bool = False) -> pd.DataFrame:
        """KIS native D/W/M/Y historical prices; quotation endpoints only.

        The broker provides native OHLCV for each period. Do NOT use the
        shorter swing analysis daily history for yearly candles. The latest
        incomplete period is returned separately by the chart API, and is
        never fed into the buy/sell decision engine.
        """
        period = {'1d': 'D', '1w': 'W', '1mo': 'M', '1y': 'Y'}.get(timeframe)
        if period is None:
            raise ValueError('지원하지 않는 KIS 차트 기간입니다.')
        if not re.fullmatch(r'\d{6}', code):
            raise ValueError('KIS 국내주식 코드 6자리가 필요합니다.')
        now = now or datetime.now(KST)
        lookback = ({'1d': 45, '1w': 140, '1mo': 800, '1y': 4000} if recent_only else
                    {'1d': 690, '1w': 2300, '1mo': 7900, '1y': 13000})[timeframe]
        start = (now - timedelta(days=lookback)).strftime('%Y%m%d')
        end = now.strftime('%Y%m%d')
        rows, seen = [], set()
        # At most ~100 native candles per request; paginate backwards from the
        # earliest record. Bound work to avoid monopolizing Render free tier.
        pages = 1 if recent_only else 10
        for _ in range(pages):
            payload = self.get('/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice',
                               'FHKST03010100',
                               {'FID_COND_MRKT_DIV_CODE': 'J', 'FID_INPUT_ISCD': code,
                                'FID_INPUT_DATE_1': start, 'FID_INPUT_DATE_2': end,
                                'FID_PERIOD_DIV_CODE': period, 'FID_ORG_ADJ_PRC': '0'})
            records = payload.get('output2') or []
            if not isinstance(records, list) or not records:
                break
            rows.extend(records)
            dates = [x.get('stck_bsop_date', '') for x in records
                     if isinstance(x, dict) and re.fullmatch(r'\d{8}', str(x.get('stck_bsop_date', '')))]
            if not dates:
                break
            earliest = min(dates)
            if earliest <= start or earliest in seen or len(records) < 100:
                break
            seen.add(earliest)
            end = (datetime.strptime(earliest, '%Y%m%d') - timedelta(days=1)).strftime('%Y%m%d')
            if end < start:
                break
        data = kis_ohlc(rows, '스윙')
        if data.empty:
            raise ValueError('KIS 기간별 차트 자료가 없습니다. 시세 조회 권한·종목코드를 확인하세요.')
        # KIS can include overlapping pagination boundaries; de-dupe in
        # clean_bars and re-bucket to exchange-local calendar periods above.
        return data

    def minutes(self,code:str,now:datetime,sessions:int=3)->pd.DataFrame:
        # Last 3 weekday sessions, including today if a weekday. Holidays may return no records.
        days=pd.bdate_range(end=now.date(),periods=sessions)
        all_rows=[]
        for day in days:
            date=day.strftime('%Y%m%d');cursor='153000';seen=set()
            for _ in range(5):
                r=self.get('/uapi/domestic-stock/v1/quotations/inquire-time-dailychartprice','FHKST03010230',
                           {'FID_COND_MRKT_DIV_CODE':'J','FID_INPUT_ISCD':code,'FID_INPUT_HOUR_1':cursor,
                            'FID_INPUT_DATE_1':date,'FID_PW_DATA_INCU_YN':'Y','FID_FAKE_TICK_INCU_YN':''})
                rows=r.get('output2') or []
                if not rows:break
                all_rows.extend(rows)
                times=[x.get('stck_cntg_hour','') for x in rows if x.get('stck_cntg_hour')]
                if not times:break
                earliest=min(times)
                if earliest<='090000' or earliest in seen or len(rows)<120:break
                seen.add(earliest)
                # KIS API is inclusive: de-duplicate returned boundary candle.
                cursor=earliest
        return kis_ohlc(all_rows,'1분')


def kis_ohlc(rows:list[dict],mode:str)->pd.DataFrame:
    out=[]
    for v in rows:
        try:
            stamp=v['stck_bsop_date']+(v.get('stck_cntg_hour','') if mode!='스윙' else '')
            fmt='%Y%m%d%H%M%S' if mode!='스윙' else '%Y%m%d'
            when=pd.Timestamp(datetime.strptime(stamp,fmt),tz=KST) if mode!='스윙' else pd.Timestamp(datetime.strptime(stamp,fmt))
            out.append({'Date':when,'Open':float(v['stck_oprc']),'High':float(v['stck_hgpr']),
                        'Low':float(v['stck_lwpr']),
                        'Close':float(v['stck_prpr'] if mode!='스윙' else v['stck_clpr']),
                        'Volume':float(v.get('cntg_vol',0) if mode!='스윙' else v.get('acml_vol',0))})
        except (KeyError,ValueError,TypeError): continue
    if not out: return pd.DataFrame(columns=['Open','High','Low','Close','Volume'])
    d=pd.DataFrame(out).set_index('Date')
    return clean_bars(d)


def kis_data(ticker:str, mode:str, key:str,secret:str,*,scan_asof:str|None=None)->MarketData:
    normalized=ticker_format(ticker)
    if not re.fullmatch(r'\d{6}\.(KS|KQ)',normalized):
        raise ValueError('KIS 국내주식은 6자리 종목코드를 입력하세요. 예: 005930')
    code=normalized[:6]
    api=KIS(key,secret)
    now=datetime.now(KST)
    if scan_asof is not None:
        if mode!='스윙' or not re.fullmatch(r'\d{8}',scan_asof):raise ValueError('INVALID_SCAN_ASOF')
        quote,quote_stamp=None,''  # Historical scan never uses today's quote.
    else:
        quote,quote_stamp=api.quote(code)
    if mode=='스윙': bars=api.daily(code,now)
    else:
        bars=api.minutes(code,now,sessions=6 if mode=='15분' else 3)
        if mode in ('5분', '15분'):bars=aggregate_minutes(bars,MINUTE_INTERVALS[mode])
    if bars.empty:raise RuntimeError('KIS에서 캔들 데이터가 없습니다. 거래일/종목/계좌 API 권한 확인')
    integrity=None
    if mode=='스윙':
        from daily_settlement_guard import verify_latest_kis_daily
        if scan_asof is not None:
            from scan_freshness import assess_scan_sessions
            coverage=assess_scan_sessions(bars.index,scan_asof,now=now)
            cutoff=datetime.strptime(scan_asof,'%Y%m%d').date()
            # Keep raw coverage evidence before truncation; do not let a later unsettled bar veto yesterday.
            bars=bars.loc[[pd.Timestamp(t).date()<=cutoff for t in bars.index]].copy()
            if bars.empty or pd.Timestamp(bars.index[-1]).date()!=cutoff:
                raise RuntimeError('SCAN_ASOF_BAR_MISSING')
            bars,integrity=verify_latest_kis_daily(bars,api=api,code=code,symbol=normalized,now=now)
            integrity=dict(integrity or {},scan_asof=scan_asof,raw_scan_freshness=coverage)
            if integrity.get('verified') is not True or integrity.get('regular_volume_verified') is not True or integrity.get('latest_date')!=cutoff.isoformat():
                integrity['signal_hold']=True
        else:
            bars,integrity=verify_latest_kis_daily(bars,api=api,code=code,symbol=normalized,now=now)
    bars=discard_unfinished(bars,mode,normalized,datetime.now(timezone.utc))
    if len(bars)<65:
        if integrity and integrity.get('signal_hold'):
            raise RuntimeError('KIS_DAILY_SETTLEMENT_HOLD')
        raise RuntimeError(f'KIS 완료봉 {len(bars)}개로 65개 미만입니다. 추가 세션 확보 또는 스윙 모드를 이용하세요.')
    return MarketData(bars,'KIS 한국투자증권 REST 시세(주기 조회)',normalized,mode,
                      quote,f'KIS 응답 체결시각: {quote_stamp} (날짜 확인 제한)',
                      now.isoformat(timespec='seconds'),
                      'REST 조회 시점 스냅샷 · 지속 스트리밍 아님',
                      'KIS 시세/분봉. 분봉은 완료된 봉만 사용. 시장 휴장·응답 시각을 확인하세요.', 'KRW',
                      daily_integrity=integrity)
