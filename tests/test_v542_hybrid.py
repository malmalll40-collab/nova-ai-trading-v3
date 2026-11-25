import copy
from datetime import timedelta
from unittest.mock import patch
import pytest
from engine import Config
from core_comparison import _reference
from nova_v541_core import point,indicators
from nova_v54_accuracy import analyze_accuracy
from nova_v542_aligned import aligned_summary
from tests.test_core_comparison_473 import sample_frames
from tests.test_v534_regular_reconstruction import frame,REG,RAW,NOW,FakeBroker
import daily_settlement_guard as guard
from kis_daily_archive import save_regular, native_fingerprint, load_regular
from tests.test_v541_core_adaptive import obs
from core541_evidence import snapshot,record,report

def test_hybrid_preserves_up_and_filters_sideways(monkeypatch):
    import nova_v541_core as m
    f,b=sample_frames(400);market,_=_reference(b);i=300
    monkeypatch.setattr(m,'_core_at',lambda *a:{'signal':'BUY','status':'READY','checks':[{'key':k,'pass':True} for k in m.FACTORS]})
    ind=indicators(f);t=f.index[i]
    ind.loc[t,['adx','slope','width','width_median','plus_di','minus_di']]=[23,.01,.2,.1,35,10]
    assert point(f,i,market,ind)['hybrid_signal']=='BUY'
    ind.loc[t,['adx','slope']]=[10,-.01]
    assert point(f,i,market,ind)['hybrid_signal']=='HOLD'
    ind.loc[t,['adx','slope','plus_di','minus_di']]=[30,.01,35,10]
    assert point(f,i,market,ind)['hybrid_signal']=='BUY'

def test_common_date_replay_and_missing():
    f,b=sample_frames(400);r=analyze_accuracy(f,Config.for_mode('스윙'),source='kis',benchmark=b)
    assert r['core_focus']['status']=='READY'
    a={'symbol':'005930.KS','v54_accuracy':r};b={'symbol':'000660.KS','v54_accuracy':copy.deepcopy(r)}
    z=aligned_summary([a,b]);assert z['status']=='READY'
    assert z['strategies']['core']['mean_pct']==r['core_focus']['strategies']['core']['portfolio']['portfolio_return_pct']
    assert z['strategies']['hybrid']['trades']==2*r['core_focus']['strategies']['hybrid']['portfolio']['completed_trades']
    assert len(z['ablations'])==5
    replay=b['v54_accuracy']['core_focus']['_replay'];d=replay['dates'][-6];del replay['points'][d]
    z=aligned_summary([a,b]);assert z['status']=='READY' and z['end']<r['core_focus']['end']
    del replay['points'][replay['dates'][10]]
    assert aligned_summary([a,b])['status']=='UNAVAILABLE'

def test_archive_reuses_after_restart_only_same_native(tmp_path,monkeypatch):
    monkeypatch.delenv('DATABASE_URL',raising=False);monkeypatch.setenv('NOVA_EVIDENCE_SQLITE_PATH',str(tmp_path/'db.sqlite'));monkeypatch.setenv('NOVA_KIS_ARCHIVE_ENABLED','1')
    raw=frame(RAW);api=FakeBroker(secondary=raw,requery=raw)
    guard._REGULAR_CACHE.clear();guard._PROVIDER_CACHE.clear()
    with patch.object(guard,'regular_reference',return_value=REG):
        safe,g=guard.verify_latest_kis_daily(raw,api=api,code='005930',symbol='005930.KS',now=NOW,force=True)
    assert save_regular('005930.KS',safe,g)=='SAVED'
    guard._REGULAR_CACHE.clear();guard._PROVIDER_CACHE.clear()
    with patch.object(guard,'regular_reference',return_value=None):
        safe,g=guard.verify_latest_kis_daily(raw,api=api,code='005930',symbol='005930.KS',now=NOW+timedelta(days=1))
        assert g['state']=='ARCHIVE_REUSED' and safe.Close.iloc[-1]==REG['Close']
        assert save_regular('005930.KS',safe,g)=='REUSED'
        _,g=guard.verify_latest_kis_daily(raw,api=api,code='005930',symbol='005930.KS',now=NOW+timedelta(days=1),force=True)
        assert g['signal_hold']
        revised=raw.copy();revised.iloc[-2,revised.columns.get_loc('Close')]+=1
        _,g=guard.verify_latest_kis_daily(revised,api=api,code='005930',symbol='005930.KS',now=NOW+timedelta(days=1))
        assert g['signal_hold']
    guard._REGULAR_CACHE.clear();guard._PROVIDER_CACHE.clear()

def test_hybrid_evidence_separate(tmp_path,monkeypatch):
    monkeypatch.delenv('DATABASE_URL',raising=False);monkeypatch.setenv('NOVA_EVIDENCE_SQLITE_PATH',str(tmp_path/'db.sqlite'));monkeypatch.setenv('NOVA_CORE541_EVIDENCE_ENABLED','1')
    a=obs();a['core541_current']['hybrid_signal']='BUY'
    assert record(a,NOW) and record(a,NOW,hybrid=True) and not record(a,NOW,hybrid=True)
    assert 'hybrid' not in report('005930.KS')['metrics']
    assert 'hybrid' in report('005930.KS',hybrid=True)['metrics']
    assert snapshot(a,NOW+timedelta(days=1),hybrid=True) is None

def test_public_research_excludes_replay():
    import server
    a={'v54_accuracy':{'core_focus':{'_replay':{'dates':[]},'status':'READY'}}}
    assert '_replay' not in server._public_research(a)['v54_accuracy']['core_focus']
    assert '_replay' in a['v54_accuracy']['core_focus']
