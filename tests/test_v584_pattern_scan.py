from datetime import date,datetime,timedelta
from types import SimpleNamespace
import pandas as pd
import nova_v584_pattern_scan as scan

def inputs(monkeypatch):
    end=date(2026,11,25)
    ds=[end-timedelta(days=i) for i in range(scan.WARMUP+30,-1,-1)]
    f=pd.DataFrame({'Open':[100+i*.3 for i in range(len(ds))],
                    'High':[101+i*.3 for i in range(len(ds))],
                    'Low':[99+i*.3 for i in range(len(ds))],
                    'Close':[100.2+i*.3 for i in range(len(ds))],
                    'Volume':[1000]*len(ds)},index=pd.to_datetime(ds))
    calls=[]
    def decide(frame,cfg,i):
        calls.append(i)
        return SimpleNamespace(signal='BUY' if i%2==0 else 'HOLD',risk_veto=False)
    monkeypatch.setattr(scan,'decide',decide)
    kwargs=dict(asof='20261125',source='kis',stale=False,demo=False,financial_gate='PASS',
                now=datetime(2026,11,26,10,tzinfo=scan.KST),
                calendar=lambda a,b:tuple(d for d in ds if a<=d<=b))
    return f,SimpleNamespace(mode='스윙'),kwargs,calls

def test_adapter_uses_only_existing_last_20_features(monkeypatch):
    f,c,k,calls=inputs(monkeypatch)
    p=scan.from_features(f,c,**k)
    assert p['status']=='RESEARCH_CANDIDATE' and p['counts']['20']==10
    assert calls==list(range(len(f)-20,len(f)))
    assert p['calendar_match'] and not p['price_integrity_independently_verified']
    assert p['probability'] is None

def test_asof_today_or_future_blocked(monkeypatch):
    f,c,k,calls=inputs(monkeypatch)
    assert scan.from_features(f,c,**{**k,'asof':'20261126'})['reason']=='SAME_DAY_UNVERIFIED'
    assert not calls

def test_gap_or_duplicate_blocks_before_decision(monkeypatch):
    f,c,k,calls=inputs(monkeypatch)
    assert scan.from_features(f.drop(f.index[-5]),c,**k)['reason']=='SESSION_MISMATCH'
    f.index=list(f.index[:-1])+[f.index[-2]]
    assert scan.from_features(f,c,**k)['reason']=='ASOF_MISMATCH'
    assert not calls

def test_source_finance_stale_and_invalid_bar(monkeypatch):
    f,c,k,calls=inputs(monkeypatch)
    for change in ({'source':'yahoo'},{'financial_gate':'UNKNOWN'},{'stale':True},{'demo':True}):
        assert scan.from_features(f,c,**{**k,**change})['status']=='UNAVAILABLE'
    f.loc[f.index[-1],'Volume']=0
    assert scan.from_features(f,c,**k)['reason']=='OHLCV_INVALID'

def candidate_input(monkeypatch):
    f,c,k,_=inputs(monkeypatch)
    p=scan.from_features(f,c,**k)
    snap={'symbol':'005930.KS','name':'sample','market':'kospi'}
    detail={'source':'kis','is_stale':False,'is_demo':False,'mode':'스윙',
            'financial_gate':'PASS','asof_evaluation_only':True,'last_completed_bar':'2026-11-25',
            'signal_pattern_research':p}
    return snap,detail

def test_separate_candidate_and_snapshot_date_gate(monkeypatch):
    s,d=candidate_input(monkeypatch)
    row,why=scan.candidate(s,d,'20261125','20261125')
    assert row['not_actionable'] and row['probability'] is None and why is None
    assert scan.candidate(s,d,'20261125','20261126')==(None,'SNAPSHOT_DATE_LAG')

def test_no_pattern_or_bad_score_not_ranked(monkeypatch):
    s,d=candidate_input(monkeypatch)
    p=d['signal_pattern_research']
    assert scan.candidate(s,{**d,'signal_pattern_research':None},'20261125','20261125')[0] is None
    p['score']=101
    assert scan.candidate(s,d,'20261125','20261125')==(None,'PATTERN_INTEGRITY')

def test_finish_ranks_before_display_limit():
    rows=[{'symbol':str(i).zfill(3),'score':i} for i in range(30)]
    r=scan.finish(rows,{'PATTERN_UNAVAILABLE':2},asof='20261125',target=35)
    assert r['eligible_total']==30 and r['evaluated']==32 and len(r['rows'])==20
    assert r['rows'][0]['symbol']=='029' and r['rows'][0]['rank']==1
    assert not r['probability_calibrated']
