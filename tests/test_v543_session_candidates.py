from datetime import datetime,timedelta
from unittest.mock import patch
import copy
import pandas as pd
import pytest
import daily_settlement_guard as guard
from tests.test_v534_regular_reconstruction import frame,RAW,REG,NOW,FakeBroker
from kis_daily_archive import save_regular
from core_comparison import _reference
from tests.test_core_comparison_473 import sample_frames
from engine import feature_frame,Config
from nova_v541_core import point,CANDIDATES
from nova_v54_accuracy import analyze_accuracy
from nova_v542_aligned import aligned_summary

@pytest.fixture
def native():
    raw=frame(RAW);current=raw.iloc[-1:].copy();current.index=pd.DatetimeIndex(['2026-11-25']);return raw,pd.concat([raw,current])

@pytest.mark.parametrize('hour,minute',[(7,30),(9,0),(12,0),(15,29)])
def test_preclose_prior_verified(native,hour,minute):
    raw,bars=native;api=FakeBroker(secondary=raw,requery=raw)
    when=(NOW+timedelta(days=1)).replace(hour=hour,minute=minute)
    with patch.object(guard,'regular_reference',return_value=REG) as ref:
        safe,g=guard.verify_latest_kis_daily(bars,api=api,code='005930',symbol='005930.KS',now=when,force=True)
    assert g['verified'] and not g['signal_hold'] and g['latest_date']=='2026-11-24'
    assert g['unfinished_date']=='2026-11-25' and g['rejected_date'] is None
    assert safe.Close.iloc[-1]==189400 and len(safe)==len(raw)
    assert ref.call_args.args[-1]==NOW.date()

@pytest.mark.parametrize('hour,minute',[(15,30),(17,59)])
def test_postclose_pending_still_holds(native,hour,minute):
    raw,bars=native;api=FakeBroker(secondary=raw,requery=raw)
    with patch.object(guard,'regular_reference') as ref:
        safe,g=guard.verify_latest_kis_daily(bars,api=api,code='005930',symbol='005930.KS',now=(NOW+timedelta(days=1)).replace(hour=hour,minute=minute),force=True)
    assert g['state']=='SETTLEMENT_PENDING' and g['signal_hold'] and len(safe)==len(raw)
    ref.assert_not_called()

def test_prior_missing_or_invalid_holds(native):
    raw,bars=native;api=FakeBroker(secondary=raw,requery=raw);when=(NOW+timedelta(days=1)).replace(hour=8)
    with patch.object(guard,'regular_reference',return_value=None):
        _,g=guard.verify_latest_kis_daily(bars,api=api,code='005930',symbol='005930.KS',now=when,force=True)
    assert g['signal_hold'] and not g['verified']
    _,g=guard.verify_latest_kis_daily(bars.drop(raw.index[-1]),api=api,code='005930',symbol='005930.KS',now=when,force=True)
    assert g['signal_hold'] and not g['verified']

def test_archive_preferred_and_current_partial_not_in_fingerprint(native,tmp_path,monkeypatch):
    monkeypatch.delenv('DATABASE_URL',raising=False);monkeypatch.setenv('NOVA_EVIDENCE_SQLITE_PATH',str(tmp_path/'db.sqlite'));monkeypatch.setenv('NOVA_KIS_ARCHIVE_ENABLED','1')
    raw,bars=native;api=FakeBroker(secondary=raw,requery=raw)
    with patch.object(guard,'regular_reference',return_value=REG):
        safe,g=guard.verify_latest_kis_daily(raw,api=api,code='005930',symbol='005930.KS',now=NOW,force=True)
    assert save_regular('005930.KS',safe,g)=='SAVED'
    guard._REGULAR_CACHE.clear();guard._PROVIDER_CACHE.clear()
    with patch.object(guard,'regular_reference') as ref,patch.object(guard,'independent_quotes') as secondary:
        safe,g=guard.verify_latest_kis_daily(bars,api=api,code='005930',symbol='005930.KS',now=(NOW+timedelta(days=1)).replace(hour=8))
    assert g['state']=='ARCHIVE_REUSED' and not g['signal_hold'] and safe.Close.iloc[-1]==189400
    ref.assert_not_called();secondary.assert_not_called()

def test_new_candidates_no_future_and_batch():
    f,b=sample_frames(400);market,_=_reference(b);i=300
    a=point(f,i,market);assert set(a['candidates'])==set(CANDIDATES)
    raw=f[['Open','High','Low','Close','Volume']].copy();raw.iloc[i+1:,:4]*=2
    assert a==point(feature_frame(raw,'스윙'),i,market)
    assert a==point(f.iloc[:i+1],i,market)
    r=analyze_accuracy(f,Config.for_mode('스윙'),source='kis',benchmark=b)
    z=aligned_summary([{'symbol':'005930.KS','v54_accuracy':r},{'symbol':'000660.KS','v54_accuracy':copy.deepcopy(r)}]);assert z['status']=='READY'
    assert set(z['candidates'])==set(CANDIDATES) and set(z['grades'])=={'A','B'}
    assert z['strategy_adoption']=='HOLD' and z['research_only']
    for k,x in z['candidates'].items():
        assert x['mean_pct']==r['core_focus']['candidates'][k]['portfolio']['portfolio_return_pct']
        assert x['mean_mdd_pct']<=0
