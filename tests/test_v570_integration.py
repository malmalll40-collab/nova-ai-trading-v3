import copy
from datetime import timedelta
from tests.test_v550_research import observation,NOW
from tests.test_core_comparison_473 import sample_frames
from nova_v54_accuracy import analyze_accuracy
from nova_v542_aligned import aligned_summary
from engine import Config
import core561_evidence as e


def test_freeze_new_observation(tmp_path,monkeypatch):
    monkeypatch.delenv('DATABASE_URL',raising=False);monkeypatch.setenv('NOVA_EVIDENCE_SQLITE_PATH',str(tmp_path/'db'));monkeypatch.setenv('NOVA_CORE541_EVIDENCE_ENABLED','1')
    now=NOW+timedelta(days=1);a=observation(now);a['core541_current']['market_regime']='range'
    assert e.record(a,now)=='SAVED'
    with e.connection() as (db,kind):r=e.read(db.cursor(),kind,'005930.KS')[0]
    assert r['v570']['version']=='5.7.0' and r['market_regime']=='range' and r['v570']['signal']=='HOLD'
    a['core541_current']['market_regime']='down';assert e.record(a,now)=='EXISTS'
    with e.connection() as (db,kind):assert e.read(db.cursor(),kind,'005930.KS')[0]==r


def test_common_aligned_v570():
    f,b=sample_frames(400);r=analyze_accuracy(f,Config.for_mode('스윙'),source='kis',benchmark=b)
    z=aligned_summary([{'symbol':'005930.KS','v54_accuracy':r},{'symbol':'000660.KS','v54_accuracy':copy.deepcopy(r)}]);assert z['status']=='READY'
    d=z['v570_accuracy'];assert d['status']=='READY' and d['basis']=='HISTORICAL_CAUSAL_REPLAY'
    assert d['completed']==2*z['samples_per_stock'] and d['shadow_completed']==d['completed'] and d['strategy_adoption']=='HOLD'
    assert d['shadow_comparison']['v570']['trades']==0  # fewer than five distinct symbols
