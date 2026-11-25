from datetime import datetime
import pandas as pd
import pytest
from market_sessions import KST, chart_probe_allowed
from tests.test_v544_chart_display import frames, moment
import chart_display as display
import higher_timeframes as hf
import daily_settlement_guard as guard
UNKNOWN={'status':'UNKNOWN','calendar_verified':False,'is_open':False,'calendar_error_code':'XKRX_LOAD_FAILED'}
@pytest.mark.parametrize('h,m,allowed',[(8,59,False),(9,0,True),(15,29,True),(15,30,False),(18,0,False)])
def test_probe_hours(h,m,allowed):
    assert chart_probe_allowed(moment(h,m), UNKNOWN)==allowed

def test_unknown_only_chart_not_signal(monkeypatch):
    monkeypatch.setattr('market_sessions.krx_session',lambda *_:UNKNOWN)
    prior,raw=frames()
    safe,g=guard.verify_latest_kis_daily(raw,api=None,code='005930',symbol='005930.KS',now=moment())
    assert g['signal_hold'] and not g['verified'] and 'XKRX_LOAD_FAILED' in g['reason']
    shown,meta=display.display_daily(raw,safe,moment(),schedule=UNKNOWN)
    assert meta['status']=='PROVISIONAL' and meta['calendar_verified'] is False and meta['analysis_excluded']
    assert shown.index[-1].date()==moment().date()
    pd.testing.assert_frame_equal(shown.iloc[:-1],safe,check_freq=False)
@pytest.mark.parametrize('period',['1d','1w','1mo','1y'])
def test_live_probe_requires_today(monkeypatch,period):
    prior,raw=frames()
    monkeypatch.setattr('market_sessions.krx_session',lambda *_:UNKNOWN)
    monkeypatch.setattr(hf,'fetch_kis_native_period',lambda *a,**k:raw)
    got=hf.latest_kis_period('005930.KS',period,now=moment())
    assert got['available'] and got['partial'] and not got['calendar_verified']
    monkeypatch.setattr(hf,'fetch_kis_native_period',lambda *a,**k:prior)
    assert not hf.latest_kis_period('005930.KS',period,now=moment())['available']

def test_weekend_and_confirmed_holiday_block(monkeypatch):
    prior,raw=frames()
    for when,schedule in [(datetime(2026,11,28,10,tzinfo=KST),UNKNOWN),(moment(),{'calendar_verified':True,'is_open':False})]:
        monkeypatch.setattr('market_sessions.krx_session',lambda *_:schedule)
        monkeypatch.setattr(hf,'fetch_kis_native_period',lambda *a,**k:pytest.fail('should not query'))
        assert not hf.latest_kis_period('005930.KS','1d',now=when)['available']
        assert display.display_daily(raw,prior,when,schedule=schedule)[1]['status']=='NOT_AVAILABLE'

def test_history_endpoint_unknown_calendar(monkeypatch):
    import server
    from fastapi.testclient import TestClient
    prior,raw=frames()
    class Clock(datetime):
        @classmethod
        def now(cls,tz=None):return moment()
    monkeypatch.setattr(server,'datetime',Clock)
    monkeypatch.setattr(server,'krx_session',lambda *_:UNKNOWN)
    monkeypatch.setattr('market_sessions.krx_session',lambda *_:UNKNOWN)
    monkeypatch.setattr(server,'fetch_kis_native_period',lambda *_:raw)
    monkeypatch.setenv('KIS_APP_KEY','test');monkeypatch.setenv('KIS_APP_SECRET','test');monkeypatch.delenv('NOVA_ACCESS_TOKEN',raising=False)
    monkeypatch.setattr('data_sources.KIS',lambda *a:object())
    server.CACHE.clear();server.RATE.clear()
    r=TestClient(server.app).get('/api/chart/history?symbol=005930.KS&timeframe=1d')
    assert r.status_code==200,r.text
    j=r.json();assert j['chart']['dates'][-1]=='2026-11-25' and j['chart']['partial'][-1]
    assert j['last_completed_daily']=='2026-11-24' and j['daily_integrity']['signal_hold']
    assert not j['display_current_daily']['calendar_verified']
    server.CACHE.clear();server.RATE.clear()

@pytest.mark.parametrize('case',['old_date','zero_volume','future_date'])
def test_unknown_does_not_restore_bad_data(case):
    prior,raw=frames()
    if case=='old_date':raw=prior
    elif case=='zero_volume':raw.loc[raw.index[-1],'Volume']=0
    else:raw=raw.rename(index={raw.index[-1]:pd.Timestamp('2026-11-26')})
    result,meta=display.display_daily(raw,prior,moment(),schedule=UNKNOWN)
    assert meta['status']=='NOT_AVAILABLE'
    pd.testing.assert_frame_equal(result,prior)
