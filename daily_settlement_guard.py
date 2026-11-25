"""NOVA v5.3.4: conservative completed-KRX-session price integrity gate.

Read-only. Rebuild the latest regular session from complete KIS minute OHLCV; never stitch KIS/Yahoo candles or
call an order endpoint. The caller's KIS daily frame is quarantined on doubt.
Comparison checks *the same date* and OHLC (not quote/time-of-day ticks).
"""
from __future__ import annotations
from datetime import datetime, time as clock_time, timedelta
from zoneinfo import ZoneInfo
import math
import threading
import time
import pandas as pd

KST=ZoneInfo('Asia/Seoul')
THRESHOLD_PCT=0.5
SETTLEMENT_AFTER=clock_time(18,0)
_PROVIDER_CACHE={}
_CACHE_LOCK=threading.RLock()
_REGULAR_CACHE={}
_REGULAR_LOCK=threading.RLock()
_CACHE_TTL=300
_FIELDS=('Open','High','Low','Close')


def _value_at(frame, day):
    if frame is None or frame.empty:
        return None
    try:
        dates=pd.DatetimeIndex(frame.index).date
        matched=frame.loc[dates == day]
        if matched.empty: return None
        row=matched.iloc[-1]
        values={field:float(row[field]) for field in _FIELDS}
        if any(not math.isfinite(x) or x<=0 for x in values.values()): return None
        if values['High'] < max(values['Open'],values['Close'],values['Low']): return None
        if values['Low'] > min(values['Open'],values['Close']): return None
        return values
    except (KeyError,TypeError,ValueError,IndexError):
        return None


def _match(a, b, tolerance=THRESHOLD_PCT):
    return a is not None and b is not None and all(
        abs(a[field]-b[field])/max(a[field],b[field])*100 <= tolerance
        for field in _FIELDS)


def _safe_summary(row):
    return {k:round(float(v),3) for k,v in row.items() if isinstance(v,(int,float)) and math.isfinite(v)} if row else None


def regular_reference(api, code, day):
    """Strict regular-session minute coverage. No synthetic missing minutes.
    Closing auction must have an explicit close-time row. The provider's
    minute timestamp convention still requires live confirmation.
    """
    from market_sessions import krx_session
    schedule=krx_session(datetime.combine(day,clock_time(21),tzinfo=KST))
    if not schedule.get('calendar_verified') or not schedule.get('close_kst'):
        return None
    start=pd.Timestamp(f"{day} {schedule['open_kst']}",tz=KST)
    close=pd.Timestamp(f"{day} {schedule['close_kst']}",tz=KST)
    cursor=close.strftime('%H%M%S');rows={}
    for _ in range(6):
        r=api.get('/uapi/domestic-stock/v1/quotations/inquire-time-dailychartprice','FHKST03010230',{
            'FID_COND_MRKT_DIV_CODE':'J','FID_INPUT_ISCD':code,'FID_INPUT_HOUR_1':cursor,
            'FID_INPUT_DATE_1':day.strftime('%Y%m%d'),'FID_PW_DATA_INCU_YN':'Y','FID_FAKE_TICK_INCU_YN':''})
        times=[]
        for x in r.get('output2') or []:
            if x.get('stck_bsop_date')!=day.strftime('%Y%m%d'):continue
            stamp=pd.Timestamp(datetime.strptime(x['stck_bsop_date']+x['stck_cntg_hour'],'%Y%m%d%H%M%S'),tz=KST)
            if not start<=stamp<=close:continue
            val={f:float(x[k]) for f,k in zip(_FIELDS,('stck_oprc','stck_hgpr','stck_lwpr','stck_prpr'))}
            if not all(math.isfinite(v) and v>0 for v in val.values()) or val['High']<max(val.values()) or val['Low']>min(val.values()):return None
            if stamp.second or stamp.microsecond:return None
            # cntg_vol is minute execution volume, not cumulative acml_vol.
            try: volume=float(x['cntg_vol'])
            except (KeyError,TypeError,ValueError):return None
            if not math.isfinite(volume) or volume<0 or not volume.is_integer():return None
            val['Volume']=volume
            if stamp in rows and rows[stamp]!=val:return None
            rows[stamp]=val;times.append(stamp)
        if not times or min(times)<=start:break
        next_cursor=(min(times)-pd.Timedelta(seconds=1)).strftime('%H%M%S')
        if next_cursor>=cursor:break
        cursor=next_cursor
    # Final ten minutes are closing call auction; no continuous fills required.
    expected=pd.date_range(start,close-pd.Timedelta(minutes=11),freq='min')
    if close not in rows or any(t not in rows for t in expected):return None
    if rows[start]['Volume']<=0 or rows[close]['Volume']<=0:return None
    executed=[v for v in rows.values() if v['Volume']>0]
    return {'Open':rows[start]['Open'],'High':max(v['High'] for v in executed),
            'Low':min(v['Low'] for v in executed),'Close':rows[close]['Close'],
            'Volume':sum(v['Volume'] for v in rows.values())}


def cached_regular_reference(api, code, day, *, force=False):
    key=(code,day.isoformat())
    with _REGULAR_LOCK:
        rec=_REGULAR_CACHE.get(key)
        if not force and rec and time.monotonic()-rec[0]<_CACHE_TTL:
            return rec[1]
        result=regular_reference(api,code,day)
        if len(_REGULAR_CACHE)>=256:_REGULAR_CACHE.clear()
        _REGULAR_CACHE[key]=(time.monotonic(),result)
        return result


def independent_quotes(api, code, symbol, date, *, force=False):
    key=(code,symbol,date.isoformat())
    with _CACHE_LOCK:
        rec=_PROVIDER_CACHE.get(key)
        if not force and rec and time.monotonic()-rec[0] < _CACHE_TTL:return rec[1]
    broker=regular=None;errors=[]
    try:
        broker=_value_at(api.daily_price(code),date)
        if broker is None:errors.append('KIS_SECONDARY_DATE_UNAVAILABLE')
    except Exception:errors.append('KIS_SECONDARY_QUERY_FAILED')
    # Native endpoints are diagnostic only; never establish regular-session scope.
    result=(broker,errors)
    with _CACHE_LOCK:
        if len(_PROVIDER_CACHE)>256:_PROVIDER_CACHE.clear()
        _PROVIDER_CACHE[key]=(time.monotonic(),result)
    return result


def verify_latest_kis_daily(bars, *, api, code, symbol, now=None,
                            yahoo_loader=None, get_requery=None, force=False):
    """KIS only; optional legacy yahoo_loader is deliberately never called.
    Complete regular-session OHLCV is the latest bar basis, including on later dates.
    Older native history is not independently session-verified.
    """
    moment=(now or datetime.now(KST)).astimezone(KST)
    series=bars.copy()
    base={'state':'NOT_CHECKED','verified':False,'auto_recovered':False,'signal_hold':False,
          'rejected_date':None,'latest_date':None,'checked_at':moment.isoformat(timespec='seconds'),
          'threshold_pct':0.0,'broker_chart':'KIS_FHKST03010100','broker_reference':'KIS_FHKST01010400',
          'independent_reference':'KIS_FHKST03010230_REGULAR_MINUTE','reason':'검증 대상 확정봉이 없습니다.',
          'external_exchange_verified':False,'yahoo_used':False,
          'historical_session_verified':False,'regular_volume_verified':False,
          'session_reconstructed':False,'history_note':'이전 KIS 일봉은 정규장 범위·수정주가 일치 여부 미검증'}
    if series.empty:return series,base
    latest=pd.Timestamp(series.index[-1]).date();base['latest_date']=latest.isoformat()
    def reject(state,reason):
        return series.iloc[:-1].copy(),{**base,'state':state,'signal_hold':True,'rejected_date':latest.isoformat(),'reason':reason}
    if latest>moment.date():return reject('FUTURE_DATE','미래 거래일 봉을 제외했습니다.')
    if (moment.date()-latest).days>10:return series,{**base,'state':'OLD_DATA','signal_hold':True,'reason':'오래된 봉: 최신 거래일 검증 필요'}
    if latest==moment.date() and moment.time()<SETTLEMENT_AFTER:
        # Before regular close, today's partial row is not the verification target.
        # Validate the previous *actual exchange session*, never merely yesterday.
        from market_sessions import krx_session
        from completed_session import completed_swing_bars
        schedule=krx_session(moment)
        if not schedule.get('calendar_verified') or not schedule.get('close_kst'):
            return reject('SETTLEMENT_PENDING','거래일/정규장 종료 시각 미확인: 신규 신호 보류 · 진단코드 '+str(schedule.get('calendar_error_code') or 'SESSION_HOURS_UNAVAILABLE'))
        close=clock_time.fromisoformat(schedule['close_kst'])
        if moment.time()>=close:
            return reject('SETTLEMENT_PENDING','당일 정규장 종료 · 18:00 이후 확정 검증 대기 (전일 봉 참고용)')
        prior, session=completed_swing_bars(series,now=moment)
        if session.get('stale') or prior.empty:
            return prior,{**base,'state':'UNVERIFIED','signal_hold':True,'reason':'전일 완료 거래일 자료 부족 또는 달력 미확인','rejected_date':latest.isoformat(),'unfinished_date':latest.isoformat()}
        safe,report=verify_latest_kis_daily(prior,api=api,code=code,symbol=symbol,now=moment,yahoo_loader=yahoo_loader,force=force)
        return safe,{**report,'raw_latest_date':latest.isoformat(),'unfinished_date':latest.isoformat(),'settlement_pending_today':True,
                     'reason':('당일 미완성 봉 제외 · 전일 검증 완료봉 기준. ' if report.get('verified') else '당일 미완성 봉 제외 · 전일 검증 실패. ')+report['reason']}
    from kis_daily_archive import native_fingerprint, load_regular
    fingerprint=native_fingerprint(symbol,series)
    base['native_fingerprint']=fingerprint
    primary=_value_at(series,latest)
    regular=None;reused=False;errors=[];broker=None
    # Reuse only an exact native fingerprint match. Manual force still queries KIS.
    if not force:
        try:
            regular=load_regular(symbol,series,fingerprint)
            reused=regular is not None
        except Exception:errors.append('KIS_ARCHIVE_READ_FAILED')
    if regular is None:
        broker,ref_errors=independent_quotes(api,code,symbol,latest,force=force)
        errors.extend(ref_errors)
        try:regular=cached_regular_reference(api,code,latest,force=force)
        except Exception:errors.append('KIS_REGULAR_QUERY_FAILED')
        if regular is None:errors.append('KIS_REGULAR_COVERAGE_INCOMPLETE')
    base['archive_reused']=reused
    base.update(broker_reference_ohlc=_safe_summary(broker),independent_reference_ohlc=_safe_summary(regular),
                primary_ohlc=_safe_summary(primary),errors=errors)
    reference=regular
    def complete_volume(row):
        try:
            value=float(row['Volume'])
            return math.isfinite(value) and value>0 and value.is_integer()
        except (KeyError,TypeError,ValueError):return False
    def native_volume(frame):
        try:return float(frame.loc[pd.DatetimeIndex(frame.index).date==latest,'Volume'].iloc[-1])
        except (AttributeError,KeyError,IndexError,TypeError,ValueError):return None
    # The actual minute collector returns OHLCV only after strict coverage checks.
    # OHLC-only references cannot authorize a reconstructed bar.
    volume_ok=complete_volume(reference)
    def confirmed(row,frame):
        return volume_ok and _match(row,reference,0) and native_volume(frame)==reference['Volume']
    def accepted(state,frame,reason,**extra):
        return frame,{**base,'state':'ARCHIVE_REUSED' if reused else state,'verified':True,'verification_basis':'KIS_REGULAR_MINUTE',
                      'regular_volume_verified':volume_ok,'reason':('Neon 저장 정규장 OHLCV 재사용 · 원본 최근 5봉 지문 일치 · 거래소 독립 검증 아님' if reused else reason),**extra}
    if reference is not None and not volume_ok:
        errors.append('KIS_REGULAR_VOLUME_UNVERIFIED')
        return reject('PRICE_CONFLICT','정규장 분봉 거래량 확인 실패: 최신 봉 격리, 신규 신호 보류')
    if confirmed(primary,series):
        return accepted('VERIFIED',series,'KIS 정규장 분봉과 최신 일봉 대조 일치. 과거 봉 전체 및 거래소 독립 검증은 미완료.')
    # Complete regular minute aggregate is authoritative for this session scope,
    # not for the whole-day native quote. Use its own volume, never native volume.
    if volume_ok and _match(reference,reference,0):
        if primary is None or 'Volume' not in series.columns:
            return reject('UNVERIFIED','원본 일봉 형식 확인 실패: 재구성 보류')
        fixed=series.copy()
        for f in (*_FIELDS,'Volume'):fixed.at[series.index[-1],f]=reference[f]
        return accepted('REGULAR_RECONSTRUCTED',fixed,'KIS 정규장 분봉 OHLCV로 최신 일봉 재구성. 최신 조회가와 분리 (거래소 독립 검증 아님).',
                        auto_recovered=True,session_reconstructed=True,reconstructed_ohlcv=_safe_summary(reference))
    try:refreshed=(get_requery or (lambda:api.period_bars(code,'1d',now=moment,recent_only=True)))()
    except Exception:refreshed=None;errors.append('KIS_CHART_REQUERY_FAILED')
    new_bar=_value_at(refreshed,latest)
    if confirmed(new_bar,refreshed):
        fixed=series.copy()
        for f in _FIELDS:fixed.at[series.index[-1],f]=new_bar[f]
        if 'Volume' in refreshed.columns:
            repl=refreshed.loc[pd.DatetimeIndex(refreshed.index).date==latest];fixed.at[series.index[-1],'Volume']=float(repl['Volume'].iloc[-1])
        return accepted('REQUERY_RECOVERED',fixed,'KIS 재조회 원본 봉이 같은 날짜 KIS 검증 자료와 일치하여 복구',auto_recovered=True)
    return reject('PRICE_CONFLICT' if reference is not None else 'UNVERIFIED','KIS 정규장 분봉 범위/가격/거래량 확인 실패: 최신 봉 격리, 신규 신호 보류. Yahoo 자동 조회 없음.')
