from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from unittest.mock import patch
import pandas as pd
import pytest
import daily_settlement_guard as guard
from tests.test_v532_daily_settlement import bars, FakeBroker
KST=ZoneInfo('Asia/Seoul')
NOW=datetime(2026,11,24,22,0,tzinfo=KST)

@pytest.fixture(autouse=True)
def clear():
    guard._PROVIDER_CACHE.clear();guard._REGULAR_CACHE.clear()
    yield
    guard._PROVIDER_CACHE.clear();guard._REGULAR_CACHE.clear()

REG={'Open':193200.,'High':193700.,'Low':187500.,'Close':189400.,'Volume':15000000.}
RAW={'Open':193200.,'High':193700.,'Low':182500.,'Close':182900.,'Volume':20000000.}

def frame(row):
    f=bars().astype(float)
    for k,v in row.items():f.loc[f.index[-1],k]=v
    return f

def run(primary,ref,now=NOW):
    api=FakeBroker(secondary=primary,requery=primary)
    with patch.object(guard,'regular_reference',return_value=ref):
        safe,report=guard.verify_latest_kis_daily(primary,api=api,code='005930',symbol='005930.KS',now=now,force=True,
            yahoo_loader=lambda *_:pytest.fail('Yahoo must not be called'))
    return safe,report,api

def test_screenshot_reconstructs_all_ohlcv_not_native_volume():
    primary=frame(RAW);before=primary.copy()
    safe,report,api=run(primary,REG)
    assert report['state']=='REGULAR_RECONSTRUCTED' and report['verified'] and not report['signal_hold']
    assert report['regular_volume_verified'] and report['session_reconstructed']
    assert report['historical_session_verified'] is False
    assert safe.iloc[-1].to_dict()==REG and report['reconstructed_ohlcv']==REG
    assert report['primary_ohlc']['Close']==182900
    assert api.requery_count==0
    pd.testing.assert_frame_equal(before,primary)
    pd.testing.assert_frame_equal(safe.iloc[:-1],primary.iloc[:-1])

def test_next_day_does_not_accept_agreeing_all_day_endpoints():
    safe,report,_=run(frame(RAW),REG,NOW+timedelta(days=1))
    assert report['state']=='REGULAR_RECONSTRUCTED' and safe.Close.iloc[-1]==189400
    safe,report,_=run(frame(RAW),None,NOW+timedelta(days=1))
    assert report['signal_hold'] and report['state']=='UNVERIFIED' and len(safe)==5

@pytest.mark.parametrize('vol',[None,0,-1,float('nan'),float('inf'),1.5])
def test_invalid_volume_cannot_reconstruct(vol):
    safe,report,_=run(frame(RAW),{**REG,'Volume':vol} if vol is not None else {k:v for k,v in REG.items() if k!='Volume'})
    assert report['signal_hold'] and len(safe)==5 and not report['verified']

def test_ohlc_match_but_volume_conflict_reconstructs():
    safe,report,_=run(frame({**REG,'Volume':20000000.}),REG)
    assert report['session_reconstructed'] and safe.Volume.iloc[-1]==15000000

def test_matching_native_ohlcv_unchanged():
    primary=frame(REG);safe,report,_=run(primary,REG)
    assert report['state']=='VERIFIED' and report['regular_volume_verified']
    pd.testing.assert_frame_equal(primary,safe)

def test_minute_volume_coverage_and_after_hours(monkeypatch):
    import market_sessions
    monkeypatch.setattr(market_sessions,'krx_session',lambda *a:{'calendar_verified':True,'open_kst':'09:00','close_kst':'15:30'})
    stamps=list(pd.date_range('2026-11-24 09:00','2026-11-24 15:19',freq='min'))+[pd.Timestamp('2026-11-24 15:30'),pd.Timestamp('2026-11-24 19:00')]
    rows=[dict(stck_bsop_date='20261124',stck_cntg_hour=t.strftime('%H%M%S'),stck_oprc='100',stck_hgpr='110',stck_lwpr='90',stck_prpr='101',cntg_vol='100',acml_vol='99999999') for t in stamps]
    rows[-1].update(stck_lwpr='1',stck_prpr='1',cntg_vol='999999')
    class API:
        def __init__(self,r):self.r=r
        def get(self,path,tr,params):
            assert tr=='FHKST03010230' and params['FID_COND_MRKT_DIV_CODE']=='J'
            return {'output2':[x for x in reversed(self.r) if x['stck_cntg_hour']<=params['FID_INPUT_HOUR_1']][:120]}
    assert guard.regular_reference(API(rows),'005930',NOW.date())=={'Open':100.,'High':110.,'Low':90.,'Close':101.,'Volume':38100.}
    for bad in [rows[1:], rows[:-2]+rows[-1:], [{**x,'cntg_vol':'bad'} if i==0 else x for i,x in enumerate(rows)]]:
        assert guard.regular_reference(API(bad),'005930',NOW.date()) is None


def test_missing_volume_holds_even_if_all_prices_match():
    ref={k:v for k,v in REG.items() if k!='Volume'}
    safe,report,_=run(frame(REG),ref)
    assert report['signal_hold'] and not report['verified'] and len(safe)==5
