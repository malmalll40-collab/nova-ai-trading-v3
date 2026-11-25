"""Regression: weekend/T+1 as-of scan != live quote; strict hold stays fail-closed."""
from datetime import datetime
from zoneinfo import ZoneInfo
import pandas as pd
from types import SimpleNamespace
import pytest

import scan_freshness as fresh
import market_scanner as ms
import server
from data_sources import demo_data

TZ=ZoneInfo('Asia/Seoul')


def test_friday_to_monday_uses_actual_exchange_sessions_not_72h():
    days=pd.to_datetime(['2026-11-11','2026-11-12','2026-11-13'])
    v=fresh.assess_scan_sessions(days,'20261113',now=datetime(2026,11,16,9,tzinfo=TZ))
    assert not v['stale'] and v['state']=='ASOF_ALIGNED' and v['session_gap']==0


def test_fsc_tplusone_one_new_session_allowed_but_historical_only():
    days=pd.to_datetime(['2026-11-09','2026-11-10','2026-11-11'])
    v=fresh.assess_scan_sessions(days,'20261110',now=datetime(2026,11,11,17,tzinfo=TZ))
    assert v['state']=='T_PLUS_ONE_ALIGNED' and v['session_gap']==1
    assert not v['stale'] and v['scan_is_historical'] is True


def test_two_real_sessions_lag_is_blocked():
    days=pd.to_datetime(['2026-11-09','2026-11-10','2026-11-11','2026-11-12'])
    v=fresh.assess_scan_sessions(days,'20261110',now=datetime(2026,11,12,17,tzinfo=TZ))
    assert v['stale'] and v['session_gap']==2 and v['state']=='PROVIDER_BEHIND_SESSIONS'


def test_missing_old_future_bad_sessions_are_blocked():
    now=datetime(2026,11,12,17,tzinfo=TZ)
    b=pd.to_datetime(['2026-11-09','2026-11-10','2026-11-12'])
    assert fresh.assess_scan_sessions(b,'20261111',now=now)['state']=='ASOF_BAR_MISSING'
    assert fresh.assess_scan_sessions(b,'20261105',now=now)['stale']
    assert fresh.assess_scan_sessions(b,'20261113',now=now)['state']=='FUTURE_ASOF'
    assert fresh.assess_scan_sessions(b,'invalid',now=now)['state']=='INVALID_ASOF'


def test_session_aware_live_stale_replaces_72h_for_swing_only(monkeypatch):
    monkeypatch.setenv('KIS_APP_KEY','test-key');monkeypatch.setenv('KIS_APP_SECRET','test-secret')
    def fake_yahoo(symbol,mode,*args):
        m=demo_data(mode)
        m.symbol=symbol
        m.synthetic=False
        return m
    monkeypatch.setattr(server,'fetch_kis_reference',lambda *a,**k:None)
    monkeypatch.setattr(server,'kis_data',fake_yahoo)
    monkeypatch.setattr(server,'_last_age_minutes',lambda *args:9999.0)
    monkeypatch.setattr(server,'_get_financial_state',lambda *args:('UNKNOWN','unverified',None))
    original=server._payload('005930.KS','스윙','kis',15)
    # A completed session is current even if a mocked 72h wall-clock age is old.
    assert original['is_stale'] is False and original['live_price_stale'] is True
    assert original['completed_session']['state']=='CURRENT_COMPLETED_SESSION'
    assert original['scan_freshness'] is None and original['asof_evaluation_only'] is False
    # The as-of path only: scanner uses verified session dates, no future bars.
    monkeypatch.setattr(server,'assess_scan_sessions',lambda *args:{'stale':False,'state':'ASOF_ALIGNED','session_gap':0})
    bar=demo_data('스윙').bars.index[-1].strftime('%Y%m%d')
    historical=server._payload('005930.KS','스윙','kis',15,asof_date=bar)
    assert historical['price_age_minutes']==9999.0
    assert historical['live_price_stale'] is True
    assert historical['is_stale'] is False
    assert historical['asof_evaluation_only'] is True
    assert historical['quote'] is None


def test_rejection_codes_are_distinct_and_not_mixed():
    base={'mode':'스윙','is_demo':False,'is_stale':False,'financial_gate':'PASS',
          'last_completed_bar':'2026-11-11T00:00:00', 'signal_is_current':True,
          'quality_gate':{'strict_signal':'BUY','passed_count':5},
          'signal_performance':{'selected_validation_positive':True,'selected_active_now':True,
                 'selected_id':'one','roles':[{'id':'one','validation':{'samples':20,'mean_net_pct':1.}}]},
          'final_actionable_signal':'BUY',
          'long_risk_reward':1.5,'long_stop_reference':90,'reference':100,'long_target_reference':115}
    assert ms.classify_rejection(base,'20261111')==(None,None)
    cases=[('is_demo',True,'DEMO'),('is_stale',True,'PRICE_STALE'),
           ('scan_expected_asof','20261112','SNAPSHOT_DATE_LAG'),
           ('last_completed_bar','2026-11-10','ASOF_MISMATCH'),
           ('financial_gate','UNKNOWN','FINANCIAL_UNVERIFIED'),
           ('financial_gate','BLOCK','FINANCIAL_RISK'),
           ('signal_is_current',False,'BAR_NOT_CURRENT'),
           ('final_actionable_signal','HOLD','POLICY_NOT_BUY'),
           ('long_risk_reward',1.0,'RISK_REWARD')]
    for field,val,code in cases:
        d=dict(base);d[field]=val
        assert ms.classify_rejection(d,'20261111')[0]==code
    d=dict(base,quality_gate={'strict_signal':'HOLD','passed_count':3})
    assert ms.classify_rejection(d,'20261111')[0]=='TECHNICAL_NOT_BUY'
    d=dict(base,signal_performance={'selected_validation_positive':False,'selected_active_now':True})
    assert ms.classify_rejection(d,'20261111')[0]=='VALIDATION_UNVERIFIED'


def test_snapshot_fallback_is_not_mislabeled_current_buy():
    class Fake:
        def candidate_dates(self,now):return ['20261111','20261110']
        def fetch_day(self,date,market):
            if date=='20261111':return []
            return [{'symbol':'005930.KS','name':'삼성전자','market':'kospi',
                     'close':10000,'volume':1000,'turnover_krw':4000000000,
                     'market_cap_krw':300000000000,'change_pct':1.1}]
    def detail(s,date):
        return {'mode':'스윙','is_demo':False,'is_stale':False,
                'last_completed_bar':'2026-11-10','financial_gate':'PASS','signal_is_current':True,
                'quality_gate':{'strict_signal':'BUY','passed_count':5}}
    result=ms.run_market_scan(market='kospi',api_key='secret',client=Fake(),
                              analyze=lambda _:None,analyze_asof=detail,
                              now=datetime(2026,11,12,17,tzinfo=TZ))
    assert result['snapshot_date_status']=='OLDER_THAN_EXPECTED'
    assert result['recommended_count']==0
    assert result['rejection_codes']['SNAPSHOT_DATE_LAG']==1
    assert result['rejected'][0]['reason_code']=='SNAPSHOT_DATE_LAG'


def test_ui_identifies_reason_code_and_source_date():
    js=(server.ASSETS/'recommendations.js').read_text()
    assert 'reason_code' in js and 'expected_source_date' in js
    assert 'scan_freshness_method' in js
    assert '최종 매수 조건 미충족 또는 시세 지연' not in js
