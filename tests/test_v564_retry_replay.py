from datetime import date, timedelta
import random
import pytest
import nova_v561_hierarchical as h


@pytest.mark.parametrize('holdout',[False,True])
def test_shared_pool_matches_uncached_reference(holdout):
    rng=random.Random(564); symbols=h.PRESET[:8]; n=90; horizon=5
    dates=[(date(2025,1,1)+timedelta(days=i)).isoformat() for i in range(n+horizon)]
    frames=[]
    for symbol in symbols:
        pts=[{'signals':{'core':rng.choice(['BUY','HOLD'])},
              'candidates':{k:rng.choice(['BUY','HOLD']) for k in h.ACTIVE if k!='core'},
              'subregime':rng.choice(['sideways','mild_up','strong_up','down','up_transition']),
              'market_regime':rng.choice(['up','up','down']),
              'net':rng.uniform(-.08,.10)} for _ in range(n)]
        frames.append((dates,None,pts))
    history=[]; reference=[[] for _ in frames]
    for i in range(n):
        pending=[]
        for j,((ds,_,pts),symbol) in enumerate(zip(frames,symbols)):
            p=pts[i]; cur={'symbol':symbol,'day':ds[i],'subregime':p.get('subregime'),'signals':{**p['signals'],**p['candidates']},'risk_veto':p.get('route')=='risk_veto' or p.get('subregime')=='down' or p.get('market_regime')=='down'}
            train=history if not holdout else [r for r in history if r['symbol']!=symbol and (h.bucket(symbol) is None or h.bucket(r['symbol'])!=h.bucket(symbol))]
            reference[j].append(h.choose(cur,train)); pending.append({**cur,'exit_day':ds[i+horizon],'net_pct':float(p['net'])*100})
        history.extend(pending)
    assert h.replay_panel(frames,symbols,horizon,holdout_bucket=holdout)==reference


@pytest.mark.parametrize('first', ['EXCEPTION','UNAVAILABLE','INSUFFICIENT','READY'])
def test_bounded_retry(monkeypatch,caplog,first):
    import server
    calls=[]; waits=[]
    symbol='086520.KQ'; key=('v5_lab',symbol,'kis',15)
    monkeypatch.setattr(server,'CACHE',{key:(0,{'status':'UNAVAILABLE'})})
    monkeypatch.setattr(server,'_batch_retry_wait',lambda:waits.append(2))
    def one(*args):
        assert args==(symbol,'kis',15); calls.append(args)
        if len(calls)==1:
            if first=='EXCEPTION':raise RuntimeError('private-value')
            return {'status':first}
        assert key not in server.CACHE
        return {'status':'READY'}
    monkeypatch.setattr(server,'_v5_one',one)
    with caplog.at_level('INFO',logger='uvicorn.error'):
        result=server._v5_batch_fetch(symbol,'kis',15)
    retry=first in ('EXCEPTION','UNAVAILABLE')
    assert len(calls)==(2 if retry else 1)
    assert waits==([2] if retry else [])
    assert result['status']==('READY' if retry else first)
    assert 'private-value' not in caplog.text


def test_persistent_failure_is_not_promoted(monkeypatch):
    import server
    calls=[]
    def one(*args):calls.append(args);return {'status':'UNAVAILABLE'}
    monkeypatch.setattr(server,'_v5_one',one)
    monkeypatch.setattr(server,'_batch_retry_wait',lambda:None)
    assert server._v5_batch_fetch('086520.KQ','kis',15)=={'status':'UNAVAILABLE'}
    assert len(calls)==2
