"""v5.3.2: fail-closed daily settlement and bounded same-provider recovery."""
from datetime import datetime
from zoneinfo import ZoneInfo
import pandas as pd
import pytest
from fastapi.testclient import TestClient
import daily_settlement_guard as guard

KST=ZoneInfo('Asia/Seoul')
NOW=datetime(2026,11,24,19,3,tzinfo=KST)
DATES=pd.bdate_range(end='2026-11-24',periods=6)

def bars(last=189400.0,low=185000.0):
    f=pd.DataFrame({'Open':[180000.0]*5+[193200.0],
        'High':[182000.0]*5+[196000.0],
        'Low':[176000.0]*5+[min(low,last)],
        'Close':[179000.0]*5+[last],
        'Volume':[10000]*6},index=DATES)
    return f

class FakeBroker:
    def __init__(self,secondary=None,requery=None):
        self.secondary=secondary if secondary is not None else bars()
        self.requery=requery if requery is not None else bars()
        self.secondary_count=0
        self.requery_count=0
    def daily_price(self,code):
        assert code=='005930'
        self.secondary_count+=1
        return self.secondary
    def period_bars(self,code,timeframe,*,now,recent_only):
        assert code=='005930' and timeframe=='1d' and recent_only
        self.requery_count+=1
        return self.requery

@pytest.fixture(autouse=True)
def clear_cache(monkeypatch):
    guard._PROVIDER_CACHE.clear();guard._REGULAR_CACHE.clear()
    yield
    guard._PROVIDER_CACHE.clear();guard._REGULAR_CACHE.clear()

def invoke(primary,api,reference,now=NOW,*,volume=True):
    from unittest.mock import patch
    row=guard._value_at(reference,NOW.date())
    if row is not None and volume:row['Volume']=float(reference.iloc[-1].Volume)
    with patch.object(guard,'regular_reference',lambda *a:row):
        return guard.verify_latest_kis_daily(primary,api=api,code='005930',symbol='005930.KS',
                   now=now,yahoo_loader=lambda _: (_ for _ in ()).throw(AssertionError('Yahoo must not be called')))

def test_verified_uses_original_without_mutation_and_caches_references():
    primary=bars();before=primary.copy(deep=True)
    api=FakeBroker()
    result,meta=invoke(primary,api,bars())
    assert meta['state']=='VERIFIED' and meta['verified']
    assert meta['signal_hold'] is False
    assert api.secondary_count==1 and api.requery_count==0
    pd.testing.assert_frame_equal(before,result)
    invoke(primary,api,bars())
    assert api.secondary_count==1

def test_shown_problem_quarantined_if_resend_still_wrong():
    # 2026-11-24 screenshot: KIS chart 184,100 vs raw Yahoo 189,400.
    primary=bars(last=184100.0,low=183000.0)
    api=FakeBroker(secondary=bars(last=189400.0),requery=bars(last=184100.0,low=183000.0))
    safe,meta=invoke(primary,api,bars(last=189400.0),volume=False)
    assert len(safe)==len(primary)-1 and safe.index[-1].date().isoformat()=='2026-11-23'
    assert meta['state']=='PRICE_CONFLICT' and meta['signal_hold']
    assert meta['rejected_date']=='2026-11-24' and not meta['verified']
    assert api.requery_count==0

def test_complete_regular_reference_recovers_from_same_kis_provider():
    primary=bars(last=184100.0,low=183000.0)
    api=FakeBroker(secondary=bars(),requery=bars())
    repaired,meta=invoke(primary,api,bars())
    assert meta['state']=='REGULAR_RECONSTRUCTED' and meta['auto_recovered']
    assert repaired['Close'].iloc[-1]==189400
    pd.testing.assert_frame_equal(primary.iloc[:-1],repaired.iloc[:-1])
    assert primary['Close'].iloc[-1]==184100

def test_regular_reference_disagreement_prevents_daily_endpoints_trusting_each_other():
    api=FakeBroker(secondary=bars(),requery=bars())
    primary=bars()
    safe,meta=invoke(primary,api,bars(last=184100,low=183000),volume=False)
    assert meta['state']=='PRICE_CONFLICT' and meta['signal_hold']
    assert len(safe)==5

def test_missing_regular_reference_quarantines_even_if_brokers_agree():
    api=FakeBroker();primary=bars()
    safe,meta=invoke(primary,api,bars().iloc[:-1])
    assert meta['state']=='UNVERIFIED'
    assert len(safe)==5

def test_today_before_settlement_remains_unverified_without_network():
    api=FakeBroker()
    safe,meta=invoke(bars(),api,bars(),datetime(2026,11,24,16,1,tzinfo=KST))
    assert meta['state']=='SETTLEMENT_PENDING' and meta['signal_hold']
    assert len(safe)==5 and api.secondary_count==0

def test_old_data_does_not_trigger_external_provider_lookup():
    api=FakeBroker()
    safe,meta=invoke(bars().iloc[:2],api,bars(),datetime(2026,12,10,19,3,tzinfo=KST))
    assert meta['state']=='OLD_DATA'
    assert api.secondary_count==0
    assert len(safe)==2

def test_automatic_failure_is_never_silently_replaced_by_yahoo_candles():
    primary=bars(last=184100,low=183000)
    api=FakeBroker(secondary=bars(last=189400),requery=primary)
    safe,meta=invoke(primary,api,bars(),volume=False)
    assert meta['signal_hold']
    assert safe.index[-1].date().isoformat()!='2026-11-24'

def test_route_is_authenticated_readonly_and_rejects_invalid_symbols(monkeypatch):
    import server
    monkeypatch.setenv('NOVA_ACCESS_TOKEN','test-token-v532-safeguard')
    client=TestClient(server.app)
    endpoint='/api/research/v5/daily-integrity'
    assert client.get(endpoint).status_code==401
    headers={'Authorization':'Bearer test-token-v532-safeguard'}
    assert client.get(endpoint+'?symbol=BAD',headers=headers).status_code==422
    called=[]
    class StubAPI:
        def __init__(self,k,s):called.append('new')
        def daily(self,code,now):called.append('daily');return bars()
        def daily_price(self,code):called.append('secondary');return bars()
        def period_bars(self,*a,**kw):called.append('retry');return bars()
    from data_sources import KIS
    import data_sources
    monkeypatch.setattr(data_sources,'KIS',StubAPI)
    monkeypatch.setattr(server,'_kis_creds',lambda:('dummy','dummy'))
    monkeypatch.setattr(guard,'verify_latest_kis_daily',lambda data,**kw:(data,{'state':'VERIFIED','verified':True,'auto_recovered':False,'signal_hold':False}))
    monkeypatch.setattr(server,'append_analysis',lambda *a,**k: (_ for _ in ()).throw(AssertionError('DB write')))
    out=client.get(endpoint+'?symbol=005930.KS',headers=headers)
    assert out.status_code==200 and out.json()['writes_neon'] is False
    assert out.json()['state']=='VERIFIED'
    assert client.get(endpoint+'?symbol=005930.KS',headers=headers).json()['cached'] is True
    assert called==['new','daily']

def test_frontend_shows_integrity_hold_and_safe_manual_button():
    from pathlib import Path
    root=Path(__file__).resolve().parents[1]
    js=(root/'static/app.js').read_text()
    html=(root/'static/index.html').read_text()
    dash=(root/'static/v5_dashboard.js').read_text()
    assert 'daily_integrity?.signal_hold' in js
    assert 'v532DailyIntegrityRun' in html
    assert '/api/research/v5/daily-integrity?symbol=' in dash
    assert 'KIS 최신 일봉 재검증 실행' in html
