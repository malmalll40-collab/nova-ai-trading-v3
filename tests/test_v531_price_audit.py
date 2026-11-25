"""v5.3.1: source-basis diagnostic never changes decisions or fabricates price continuity."""
import pandas as pd
from fastapi.testclient import TestClient
from price_source_audit import audit, yahoo_price_bases


def candles(dates, close, adj=None):
    data={'Open':[v*0.99 for v in close], 'High':[v*1.02 for v in close],
          'Low':[v*0.97 for v in close], 'Close':close,
          'Volume':[1000]*len(close)}
    if adj is not None:data['Adj Close']=adj
    return pd.DataFrame(data,index=pd.DatetimeIndex(dates))


def dates(n=50):return pd.bdate_range('2026-07-01',periods=n)


def test_yahoo_basis_keeps_original_and_calculates_adjusted_ratio_without_future_data():
    history=candles(dates(40),[100.0]*40,adj=[98.0]*30+[100.0]*10)
    raw,adj=yahoo_price_bases(history)
    assert raw['Close'].iloc[0]==100
    assert adj['Close'].iloc[0]==98
    assert adj['Open'].iloc[0]==raw['Open'].iloc[0]*.98
    assert history['Close'].iloc[0]==100   # no mutation
    assert adj['Close'].iloc[-1]==100


def test_discrepancy_detected_without_inventing_dates_or_reweighting():
    days=dates(30)
    k=candles(days,[100]*30)
    raw=candles(days,[98]*30)
    adj=candles(days,[97]*30)
    before=k.copy(deep=True)
    result=audit(k,raw,adj,symbol='005930.KS',assessed_at='2026-11-24T00:00:00Z')
    assert result['status']=='COMPARED'
    assert result['decision']=='PRICE_BASIS_DIFFERENCE'
    assert result['price_basis_verified'] is False
    assert result['point_in_time_verified'] is False
    assert result['overlap']['count']==30
    assert result['comparison']['kis_vs_yahoo_reference']['days_any_ohlc_over_0p5pct']==30
    assert result['sample_days'][0]['kis_close']==100
    assert result['sample_days'][0]['yahoo_reference_close']==98
    pd.testing.assert_frame_equal(k,before)


def test_missing_dates_are_not_forward_filled_and_short_history_is_uncertain():
    d=dates(30)
    k=candles(d,[100]*30)
    raw=candles(d[6:],[100]*24)
    adj=raw.copy()
    x=audit(k,raw,adj,symbol='005930.KS')
    assert x['overlap']['count']==24
    assert x['overlap']['missing_from_yahoo_count']==6
    assert x['overlap']['missing_from_yahoo_dates'][0]==str(d[0].date())
    assert x['decision']=='NO_LARGE_DIFFERENCE_OBSERVED'
    short=audit(k,raw.iloc[:12],adj.iloc[:12],symbol='005930.KS')
    assert short['decision']=='INSUFFICIENT_OVERLAP'
    assert short['status']=='INSUFFICIENT'


def test_fails_closed_when_either_feed_missing():
    d=dates(30)
    y=candles(d,[100]*30)
    a=audit(None,y,y,symbol='005930.KS',failures={'kis':'KIS_NOT_CONFIGURED'})
    assert a['status']=='UNAVAILABLE'
    assert a['providers']['kis']['error_code']=='KIS_NOT_CONFIGURED'
    assert a['comparison']=={}
    assert not a['modifies_live_signal']


def test_price_audit_route_auth_validation_and_readonly(monkeypatch):
    import server
    monkeypatch.setenv('NOVA_ACCESS_TOKEN','test-token-531-at-least-16')
    client=TestClient(server.app)
    endpoint='/api/research/v5/source-audit'
    assert client.get(endpoint+'?symbol=005930.KS').status_code==401
    headers={'Authorization':'Bearer test-token-531-at-least-16'}
    assert client.get(endpoint+'?symbol=ZZZ',headers=headers).status_code==422
    calls=[]
    def fake_compute(symbol):
        calls.append(symbol)
        return {'status':'COMPARED','decision':'PRICE_BASIS_DIFFERENCE',
                'overlap':{'count':40},'research_only':True,'modifies_live_signal':False}
    monkeypatch.setattr(server,'_source_audit_compute',fake_compute)
    monkeypatch.setattr(server,'append_analysis',lambda *_a,**_kw: (_ for _ in ()).throw(AssertionError('No DB writes')))
    r=client.get(endpoint+'?symbol=005930.KS',headers=headers)
    assert r.status_code==200 and r.json()['modifies_live_signal'] is False
    r2=client.get(endpoint+'?symbol=005930.KS',headers=headers)
    assert r2.status_code==200 and r2.json()['cached'] is True
    assert calls==['005930.KS']


def test_price_audit_frontend_is_pure_opt_in():
    from pathlib import Path
    root=Path(__file__).resolve().parents[1]
    js=(root/'static/v5_dashboard.js').read_text(encoding='utf-8')
    html=(root/'static/index.html').read_text(encoding='utf-8')
    assert 'v531SourceAuditRun' in html and 'v531SourceAuditStatus' in html
    assert "sourceButton.addEventListener('click',runPriceAudit)" in js
    assert '/api/research/v5/source-audit?symbol=' in js
    assert "research_only" in (root/'price_source_audit.py').read_text(encoding='utf-8')
