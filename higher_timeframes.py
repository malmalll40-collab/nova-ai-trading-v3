"""NOVA v4.1: read-only long-period chart data.

Aggregate *complete daily candles* into calendar week/month/year in the exchange's
local timezone. The unfinished current calendar period is explicitly tagged and
is never used to create trading signals or historical success-rate claims.
"""
from __future__ import annotations

from datetime import datetime, date, timedelta, timezone
from zoneinfo import ZoneInfo
import pandas as pd
import numpy as np

from data_sources import (KST, market_zone, ticker_format, clean_bars,
                          discard_unfinished, kis_data, demo_data, KIS)

PERIODS = {'1d':'일봉', '1w':'주봉', '1mo':'월봉', '1y':'연봉'}
FIELDS = {'Open':'first','High':'max','Low':'min','Close':'last','Volume':'sum'}


def bucket(day:date, timeframe:str)->date:
    if timeframe=='1d': return day
    if timeframe=='1w': return day-timedelta(days=day.weekday())
    if timeframe=='1mo': return date(day.year, day.month, 1)
    if timeframe=='1y': return date(day.year, 1, 1)
    raise ValueError('지원되지 않는 차트 주기입니다.')


def grouped_ohlcv(daily:pd.DataFrame, timeframe:str, symbol:str, now:datetime|None=None)->pd.DataFrame:
    """Local calendar OHLCV with no future data, no forward-fill, no partial daily bars.

    Index is exchange-local when intraday timestamps are timezone-aware. Weekly
    buckets start Monday; monthly and yearly buckets start on calendar 1st.
    """
    if timeframe not in PERIODS: raise ValueError('지원되지 않는 차트 주기입니다.')
    if daily.empty: return daily.copy()
    src=clean_bars(daily)
    if src.empty:return src
    zone=market_zone(symbol)
    local_index=src.index.tz_convert(zone) if src.index.tz is not None else src.index
    days=[pd.Timestamp(x).date() for x in local_index]
    keys=[bucket(day,timeframe) for day in days]
    src=src.copy()
    src['_period']=keys
    frame=src.groupby('_period',sort=True).agg(FIELDS)
    # Derived period index is a calendar label, not an observed market price day.
    frame.index=pd.DatetimeIndex(frame.index, name='Period')
    if not frame.index.is_unique or not frame.index.is_monotonic_increasing:
        raise ValueError('봉 날짜 집계 순서가 올바르지 않습니다.')
    return frame


def _float(v):
    try:
        n=float(v)
        return round(n,6) if np.isfinite(n) else None
    except (TypeError,ValueError):return None


def chart_data(daily:pd.DataFrame, timeframe:str, symbol:str, source:str, *,
               synthetic:bool=False, now:datetime|None=None)->dict:
    if timeframe not in PERIODS:raise ValueError('지원하지 않는 차트 주기입니다.')
    normalized='DEMO' if synthetic else ticker_format(symbol)
    zone=KST if synthetic else market_zone(normalized)
    now=now or datetime.now(timezone.utc)
    frame=grouped_ohlcv(daily,timeframe,normalized,now)
    if len(frame)<2:
        raise ValueError(f'{PERIODS[timeframe]} 자료가 {len(frame)}개뿐입니다. KIS 자료가 부족합니다. 다른 주기를 이용하세요.')
    # EMA uses previous historical observations even when only the last bars are shown.
    frame['ema20']=frame['Close'].ewm(span=20,adjust=False,min_periods=20).mean()
    frame['ema50']=frame['Close'].ewm(span=50,adjust=False,min_periods=50).mean()
    shown=frame.tail(240)
    today=now.astimezone(zone).date()
    session_open=(now.astimezone(zone).weekday()<5 and 9*60<=now.astimezone(zone).hour*60+now.astimezone(zone).minute<15*60+30)
    is_partial=[(timeframe!='1d' or (source=='kis' and session_open)) and bucket(today,timeframe)==x.date() for x in shown.index]
    chart={'dates':[x.strftime('%Y-%m-%d') for x in shown.index],
           'partial':is_partial}
    for field,alias in [('Open','open'),('High','high'),('Low','low'),('Close','close'),('Volume','volume'),('ema20','ema20'),('ema50','ema50')]:
        chart[alias]=[_float(x) for x in shown[field]]
    last=pd.Timestamp(daily.index[-1])
    if last.tz is not None:last=last.tz_convert(zone)
    else:last=last.tz_localize(zone)
    return {'symbol':normalized,'source':source,'timeframe':timeframe,'timeframe_label':PERIODS[timeframe],
            'count':len(frame),'chart':chart,'synthetic':synthetic,
            'last_completed_daily':last.isoformat(),
            'partial_last':bool(is_partial[-1]),'ema50_available':bool(pd.notna(shown['ema50'].iloc[-1])),
            'note':('가상 데이터 · 실제 주가가 아닙니다.' if synthetic else
                    ('현재 회계연도/월/주가 아직 끝나지 않았다면 마지막 봉은 미완성으로 표시됩니다. '
                     '시세는 공급원에 따라 지연될 수 있습니다. 이 봉은 매매 신호나 적중률 계산에 사용되지 않습니다.'))}


def fetch_long_daily(symbol:str,source:str)->pd.DataFrame:
    """Load daily-only history; isolated from the existing trading decision engine."""
    if source=='demo':
        rng=np.random.default_rng(411)
        idx=pd.bdate_range(end=pd.Timestamp.now(tz=KST).date(),periods=25*252)
        c=60000*np.exp(np.cumsum(rng.normal(.00010,.011,len(idx))))
        o=np.r_[c[0],c[:-1]]*(1+rng.normal(0,.001,len(idx)))
        frame=pd.DataFrame({'Open':o,'High':np.maximum(c,o)*1.006,
                             'Low':np.minimum(c,o)*.994,'Close':c,
                             'Volume':rng.integers(150000,3000000,len(idx))},index=idx)
        # Match the demo swing data exactly in the recent overlap. Otherwise
        # switching '분석 주기' to '주봉/연봉' would show different prices for the
        # same fictitious DEMO symbol, an avoidable and confusing UI defect.
        recent=demo_data('스윙').bars
        before=frame.index.difference(recent.index)
        if len(before) and len(recent):
            factor=float(recent['Open'].iloc[0])/float(frame.loc[before[-1],'Close'])
            frame.loc[before,['Open','High','Low','Close']]*=factor
        frame.loc[recent.index,['Open','High','Low','Close','Volume']]=recent[['Open','High','Low','Close','Volume']].values
        return frame
    symbol=ticker_format(symbol)
    if source=='yahoo':
        try:
            import yfinance as yf
            bars=yf.Ticker(symbol).history(period='max',interval='1d',auto_adjust=True,
                                           prepost=False,raise_errors=True,timeout=15)
        except Exception as exc:
            raise RuntimeError(f'장기 Yahoo 일봉 조회 실패({type(exc).__name__}). 일시 제한 또는 공급원 연결을 확인하세요.') from exc
        if bars is None or bars.empty:raise ValueError('장기 일봉 자료가 없습니다. 해당 종목의 상장 이력을 확인하세요.')
        d=clean_bars(bars)
        d=discard_unfinished(d,'스윙',symbol,datetime.now(timezone.utc))
        return d
    if source=='kis':
        import os
        key,secret=os.environ.get('KIS_APP_KEY',''),os.environ.get('KIS_APP_SECRET','')
        if not (key and secret):raise ValueError('KIS 시세 사용에는 서버의 API 키 설정이 필요합니다.')
        return kis_data(symbol,'스윙',key,secret).bars
    raise ValueError('지원하지 않는 시세 공급원입니다.')


def fetch_kis_native_period(symbol: str, timeframe: str, *, recent_only: bool = False,
                            now: datetime | None = None) -> pd.DataFrame:
    """KIS native period candles, isolated from signal and historical backtests."""
    import os
    symbol = ticker_format(symbol)
    if not symbol.endswith(('.KS', '.KQ')):
        raise ValueError('KIS 국내 주식만 지원합니다.')
    key = os.environ.get('KIS_APP_KEY', '').strip()
    secret = os.environ.get('KIS_APP_SECRET', '').strip()
    if not key or not secret:
        raise ValueError('KIS 앱 키가 미설정입니다.')
    return KIS(key, secret).period_bars(symbol[:6], timeframe, now, recent_only=recent_only)


def latest_kis_period(symbol: str, timeframe: str, *, now: datetime | None = None) -> dict:
    """Provider-native current calendar period snapshot, NOT streaming ticks.

    Return an update only if KIS confirms a trading day belonging to the
    current period. Weekends/holidays can never stamp a previous period as
    today's live price. For reliable daily bars we require today's date.
    """
    now = now or datetime.now(KST)
    here = now.astimezone(KST)
    if timeframe not in PERIODS:
        raise ValueError('지원하지 않는 봉 주기입니다.')
    from market_sessions import krx_session, chart_probe_allowed
    schedule = krx_session(here)
    if not chart_probe_allowed(here, schedule):
        return {'available': False, 'reason': '정규장 예상 시간 외 · 장중 자동 갱신 중지',
                'timeframe': timeframe, 'retrieved_at': here.isoformat(timespec='seconds'),
                'streaming': False, 'refresh_type': 'REST polling'}
    d = fetch_kis_native_period(symbol, timeframe, recent_only=True, now=here)
    grouped = grouped_ohlcv(d, timeframe, symbol)
    if grouped.empty:
        raise ValueError('KIS 현재 기간 봉이 비어 있습니다.')
    target = bucket(here.date(), timeframe)
    # A native weekly/monthly/yearly record can be dated with the period's
    # first or last trading day. Confirm today's actual trading date from the
    # separate KIS daily endpoint rather than assuming the period label is today.
    date_of_last = pd.Timestamp(d.index[-1]).date()
    if timeframe != '1d':
        recent_daily = fetch_kis_native_period(symbol, '1d', recent_only=True, now=here)
        if recent_daily.empty:
            raise ValueError('KIS 오늘 일봉 확인 자료가 없습니다.')
        date_of_last = pd.Timestamp(recent_daily.index[-1]).date()
    latest_period = grouped.index[-1].date()
    # D means confirmed same-date, not merely a record from this week/year.
    if latest_period != target or (timeframe == '1d' and date_of_last != here.date()):
        return {'available': False, 'reason': '현재 기간의 KIS 거래 데이터 미확인(휴장 또는 공급원 지연 가능)',
                'timeframe': timeframe, 'retrieved_at': here.isoformat(timespec='seconds'),
                'last_provider_trading_date': date_of_last.isoformat(),
                'streaming': False, 'refresh_type': 'REST polling'}
    # W/M/Y may contain previous sessions in the same calendar bucket. Without
    # a trading date from today, it is not safe to call the period live.
    if date_of_last != here.date():
        return {'available': False, 'reason': '오늘 체결 날짜가 확인되지 않아 장중 봉 갱신 보류',
                'timeframe': timeframe, 'retrieved_at': here.isoformat(timespec='seconds'),
                'last_provider_trading_date': date_of_last.isoformat(),
                'streaming': False, 'refresh_type': 'REST polling'}
    bar = grouped.iloc[-1]
    if timeframe != '1d':
        daily_volume = _float(recent_daily['Volume'].iloc[-1])
        if daily_volume is None or daily_volume <= 0:
            return {'available': False, 'reason': '오늘 일봉 체결량 미확인', 'timeframe': timeframe, 'retrieved_at': here.isoformat(timespec='seconds')}
        daily_close = float(recent_daily['Close'].iloc[-1])
        if abs(float(bar['Close']) - daily_close) > max(1.0, daily_close * 0.00001):
            return {'available': False, 'reason': '일봉과 현재 기간 봉의 종가가 달라 공급원 동기화 대기',
                    'timeframe': timeframe, 'retrieved_at': here.isoformat(timespec='seconds'),
                    'last_provider_trading_date': date_of_last.isoformat(),
                    'streaming': False, 'refresh_type': 'REST polling'}
    row = {key: _float(bar[col]) for key, col in [('open','Open'), ('high','High'),
            ('low','Low'), ('close','Close'), ('volume','Volume')]}
    if any(v is None for v in row.values()) or row['open'] <= 0 or row['close'] <= 0 or row['volume'] <= 0 or row['low'] <= 0 or row['high'] < max(row['open'],row['close']) or row['low'] > min(row['open'],row['close']):
        raise ValueError('KIS 현재 기간 OHLCV 값이 유효하지 않습니다.')
    return {'available': True, 'date': latest_period.isoformat(), 'trading_date': date_of_last.isoformat(),
            'timeframe': timeframe, 'bar': row, 'partial': True,
            'calendar_verified': bool(schedule.get('calendar_verified')), 'calendar_error_code': schedule.get('calendar_error_code'),
            'retrieved_at': here.isoformat(timespec='seconds'),
            'provider': 'KIS 국내주식 기간별 시세(KRX)',
            'streaming': False, 'refresh_type': 'REST polling',
            'note': 'KIS 기간별 시세 REST 주기 조회입니다. 체결 단위 WebSocket 실시간 스트림이 아니며 실제 시세 지연은 검증되지 않았습니다.'}
