"""Read-only additions to frozen analysis charts. No signal or storage writes."""
from datetime import datetime, time
import math
import pandas as pd
from market_sessions import KST, SPECIAL_HOURS, krx_session, chart_probe_allowed

INTERVALS={'1분':1,'5분':5,'15분':15}


def minute_rows(bars, mode, now, schedule):
    """Actual provider rows only; no fill, no overnight aggregation."""
    if mode not in INTERVALS: raise ValueError('지원하지 않는 분석주기')
    now=now.astimezone(KST); day=now.date(); minute=INTERVALS[mode]
    if bars.empty or bars.index.tz is None:return []
    if schedule.get('calendar_verified') and not schedule.get('open_kst'):return []
    if not schedule.get('calendar_verified') and not chart_probe_allowed(now,schedule,provisional=True):return []
    fallback= SPECIAL_HOURS.get(day.isoformat(),('09:00','15:30',''))
    opening=time.fromisoformat(schedule.get('open_kst') or fallback[0]);closing=time.fromisoformat(schedule.get('close_kst') or fallback[1]);
    b=bars.copy();b.index=b.index.tz_convert(KST)
    b=b[(b.index.date==day)&(b.index.time>=opening)&(b.index.time<closing)&(b.index<=pd.Timestamp(now))]
    b=b[~b.index.duplicated(keep='last')].sort_index()
    cols=['Open','High','Low','Close','Volume']; b=b[cols].astype(float)
    good=b.apply(lambda r: all(math.isfinite(v) for v in r) and min(r.Open,r.High,r.Low,r.Close)>0 and r.Volume>=0 and r.High>=max(r.Open,r.Close) and r.Low<=min(r.Open,r.Close),axis=1) if not b.empty else pd.Series([],dtype=bool,index=b.index)
    b=b.loc[good]
    if b.empty:return []
    counts=b.Close.resample(f'{minute}min').count()
    agg=b.resample(f'{minute}min',label='left',closed='left').agg({'Open':'first','High':'max','Low':'min','Close':'last','Volume':'sum'}).dropna()
    result=[]
    for stamp,r in agg.iterrows():
        if r.Volume<=0:continue
        end=stamp+pd.Timedelta(minutes=minute)
        # A missing constituent minute is not silently declared complete.
        partial=bool(end>pd.Timestamp(now) or counts.loc[stamp]<minute)
        result.append({'date':stamp.isoformat(),'bar':{k.lower():float(r[k]) for k in cols},'partial':partial})
    return result


def fetch(symbol,mode,key,secret,now=None):
    from data_sources import KIS
    from higher_timeframes import latest_kis_period, fetch_kis_native_period
    now=(now or datetime.now(KST)).astimezone(KST);schedule=krx_session(now)
    base={'mode':mode,'symbol':symbol,'calendar_verified':bool(schedule.get('calendar_verified')),'retrieved_at':now.isoformat(timespec='seconds'),'display_only':True,'rows':[]}
    if mode=='스윙':
        if chart_probe_allowed(now,schedule):
            r=latest_kis_period(symbol,'1d',now=now)
            if r.get('available'):base['rows']=[{'date':r['date'],'bar':r['bar'],'partial':True}]
        elif chart_probe_allowed(now,schedule,provisional=True) or (schedule.get('calendar_verified') and schedule.get('close_kst') and now.hour>=18):
            from chart_display import display_daily
            from daily_settlement_guard import verify_latest_kis_daily
            raw=fetch_kis_native_period(symbol,'1d',recent_only=True,now=now)
            completed,meta=verify_latest_kis_daily(raw,api=KIS(key,secret),code=symbol[:6],symbol=symbol,now=now)
            shown,provisional=display_daily(raw,completed,now,schedule=schedule)
            if not shown.empty and pd.Timestamp(shown.index[-1]).date()==now.date():
                r=shown.iloc[-1];base['rows']=[{'date':now.date().isoformat(),'bar':{k.lower():float(r[k]) for k in ('Open','High','Low','Close','Volume')},'partial':provisional.get('status')=='PROVISIONAL'}]
    elif mode in INTERVALS:
        if schedule.get('calendar_verified') and not schedule.get('open_kst'):return {**base,'available':False,'reason':'휴장일'}
        if not schedule.get('calendar_verified') and not chart_probe_allowed(now,schedule,provisional=True):return {**base,'available':False,'reason':'거래일 미확인 또는 조회 시간 외'}
        opening=schedule.get('open_kst') or SPECIAL_HOURS.get(now.date().isoformat(),('09:00','15:30',''))[0]
        if now.strftime('%H:%M')>=opening:base['rows']=minute_rows(KIS(key,secret).minutes(symbol[:6],now,sessions=1),mode,now,schedule)
    else:raise ValueError('지원하지 않는 분석주기')
    return {**base,'available':bool(base['rows']),'reason':'KIS 당일 봉 미확인' if not base['rows'] else '차트 전용 · 분석 신호 미반영'}
