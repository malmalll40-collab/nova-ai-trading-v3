from datetime import datetime
from zoneinfo import ZoneInfo
import pandas as pd
import pytest
from fastapi.testclient import TestClient
import server
import chart_display as display
import daily_settlement_guard as guard
from tests.test_v534_regular_reconstruction import frame, RAW, REG
from tests.test_v532_daily_settlement import FakeBroker
KST=ZoneInfo('Asia/Seoul')
SCHEDULE={'calendar_verified':True,'is_open':True,'open_kst':'09:00','close_kst':'15:30'}

def moment(h=10,m=0):return datetime(2026,11,25,h,m,tzinfo=KST)

def frames():
    prior=frame(RAW);today=prior.iloc[-1:].copy();today.index=pd.DatetimeIndex(['2026-11-25']);today['Close']=190000.
    return prior,pd.concat([prior,today])

@pytest.mark.parametrize('hour,minute',[(9,0),(10,0),(15,29),(15,30),(17,59)])
def test_restore_today_chart_only(hour,minute):
    prior,raw=frames();before=raw.copy();completed=prior.copy();completed.iloc[-1]=pd.Series(REG)
    out,meta=display.display_daily(raw,completed,moment(hour,minute),schedule=SCHEDULE)
    assert meta['status']=='PROVISIONAL' and meta['analysis_excluded']
    assert out.index[-1].date().isoformat()=='2026-11-25' and out.iloc[-1].Close==190000
    assert out.iloc[-2].Close==REG['Close']
    pd.testing.assert_frame_equal(raw,before);pd.testing.assert_frame_equal(out.iloc[:-1],completed,check_freq=False)

@pytest.mark.parametrize('h,m',[(8,59),(18,0),(22,0)])
def test_outside_provisional_window(h,m):
    prior,raw=frames();out,meta=display.display_daily(raw,prior,moment(h,m),schedule=SCHEDULE)
    assert meta['status']=='NOT_AVAILABLE';pd.testing.assert_frame_equal(out,prior)

@pytest.mark.parametrize('field,value',[('Volume',0),('Volume',float('nan')),('Low',-1),('High',1),('Close',float('inf'))])
def test_invalid_native_bar_not_restored(field,value):
    prior,raw=frames();raw.loc[raw.index[-1],field]=value
    out,meta=display.display_daily(raw,prior,moment(),schedule=SCHEDULE)
    assert meta['status']=='NOT_AVAILABLE';pd.testing.assert_frame_equal(out,prior)

@pytest.mark.parametrize('schedule',[{'calendar_verified':True,'is_open':False,'open_kst':None,'close_kst':None}, {**SCHEDULE,'open_kst':'11:00'}])
def test_calendar_unknown_holiday_or_before_special_open(schedule):
    prior,raw=frames();out,meta=display.display_daily(raw,prior,moment(),schedule=schedule)
    assert meta['status']=='NOT_AVAILABLE';pd.testing.assert_frame_equal(out,prior)


def test_never_restore_previous_quarantine_or_duplicate_today():
    prior,raw=frames();safe=prior.iloc[:-1];out,_=display.display_daily(raw,safe,moment(),schedule=SCHEDULE)
    assert prior.index[-1] not in out.index
    out,meta=display.display_daily(raw,raw,moment(),schedule=SCHEDULE)
    assert meta['status']=='NOT_AVAILABLE';pd.testing.assert_frame_equal(out,raw)
    out,meta=display.display_daily(prior,safe,moment(),schedule=SCHEDULE)
    assert meta['status']=='NOT_AVAILABLE';pd.testing.assert_frame_equal(out,safe)

@pytest.mark.parametrize('hour,expected',[(8,False),(10,True),(16,True),(18,False)])
def test_history_api_provisional_separate_from_verified_guard(monkeypatch,hour,expected):
    prior,raw=frames();completed=prior.copy();completed.iloc[-1]=pd.Series(REG)
    class Clock(datetime):
        @classmethod
        def now(cls,tz=None):return moment(hour)
    monkeypatch.setattr(server,'datetime',Clock)
    monkeypatch.setattr(server,'krx_session',lambda *_:SCHEDULE)
    monkeypatch.setattr(server,'fetch_kis_native_period',lambda *_:raw)
    monkeypatch.setattr(guard,'verify_latest_kis_daily',lambda *a,**kw:(completed,{'verified':True,'signal_hold':False,'latest_date':'2026-11-24'}))
    monkeypatch.setenv('KIS_APP_KEY','test');monkeypatch.setenv('KIS_APP_SECRET','test');monkeypatch.delenv('NOVA_ACCESS_TOKEN',raising=False)
    monkeypatch.setattr('data_sources.KIS',lambda *a:object())
    monkeypatch.setattr(server,'_payload',lambda *a,**kw:pytest.fail('analysis engine touched'))
    server.CACHE.clear();server.RATE.clear()
    r=TestClient(server.app).get('/api/chart/history?symbol=005930.KS&timeframe=1d');assert r.status_code==200,r.text
    j=r.json();assert j['last_completed_daily']=='2026-11-24';assert j['chart']['dates'][-1]==('2026-11-25' if expected else '2026-11-24');assert j['partial_last']==expected
    assert j['daily_integrity']['latest_date']=='2026-11-24'
    assert j['chart']['close'][-1]==(190000 if expected else REG['Close'])
    server.CACHE.clear();server.RATE.clear()
