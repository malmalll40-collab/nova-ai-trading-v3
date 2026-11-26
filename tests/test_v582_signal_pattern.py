from datetime import date,timedelta
from copy import deepcopy
from nova_v582_signal_pattern import pattern,rank,backtest

def history(n=20,buys=(12,14,16,19),step=0.5):
    rows=[]
    for i in range(n):
        rows.append({'date':(date(2026,1,1)+timedelta(days=i)).isoformat(),
                     'completed':True,'price_close':100+i*step,
                     'baseline':'BUY' if i in buys else 'HOLD',
                     'five_core':'HOLD','risk_veto':False,
                     'outcome_status':'EVALUATED','next_open':100,'exit_close_t5':102})
    return rows

def test_counts_recency_and_candidate():
    p=pattern(history())
    assert p['status']=='RESEARCH_CANDIDATE'
    assert p['counts']=={'5':2,'10':4,'20':4}
    assert p['last_buy_age']==0 and p['count_bucket']=='4-5'
    assert 0<=p['score']<=100 and p['probability'] is None

def test_distinct_signal_families():
    rows=history()
    assert pattern(rows,'five_core')['reason']=='NO_RECENT_BUY'

def test_outcome_fields_never_affect_pattern():
    rows=history();p=pattern(rows)
    for r in rows:
        r.update(next_open=1,exit_close_t5=999999,outcome_status='PENDING')
    assert pattern(rows)==p

def test_declining_and_extended_moves_are_filtered():
    assert pattern(history(step=-.5))['pattern']=='FAILURE_RISK'
    assert pattern(history(step=10))['pattern']=='CHASE_RISK'

def test_veto_and_stale():
    rows=history();rows[-1]['risk_veto']=True
    assert pattern(rows)['reason']=='RISK_OR_STALE_SIGNAL'
    assert pattern(history(buys=(3,4,5)))['reason']=='RISK_OR_STALE_SIGNAL'

def test_invalid_unfinished_duplicate_and_short():
    rows=history();rows[-1]['completed']=False
    assert pattern(rows)['status']=='UNAVAILABLE'
    rows=history();rows[-1]['date']=rows[-2]['date']
    assert pattern(rows)['status']=='UNAVAILABLE'
    assert pattern(history(19))['status']=='INSUFFICIENT'

def test_ranking_deterministic_and_missing_data_filtered():
    rows=history();ds=[r['date'] for r in rows]
    h={'source':'kis','session_verified':True,'rows':rows}
    missing=deepcopy(h);missing['rows'].pop(2)
    r=rank({'B':h,'A':h,'C':missing,'D':{**h,'source':'yahoo'}},as_of=ds[-1],expected_dates=ds)
    assert [x['symbol'] for x in r['rows']]==['A','B']
    assert [x['rank'] for x in r['rows']]==[1,2]
    assert len(r['excluded'])==2 and not r['probability_calibrated']

def test_asof_calendar_mismatch_blocks():
    rows=history();ds=[r['date'] for r in rows]
    assert rank({},as_of='2026-02-01',expected_dates=ds)['status']=='UNAVAILABLE'

def test_nonoverlap_and_small_samples_suppressed():
    rows=history(n=40,buys=tuple(range(40)))
    r=backtest(rows)
    assert sum(v['samples'] for v in r['buckets'].values())==3
    assert all(v['hit_pct'] is None for v in r['buckets'].values())
    assert not r['independent_future_verified']
