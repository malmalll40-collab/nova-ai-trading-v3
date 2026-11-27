from datetime import date
import hashlib
import io
import json
import zipfile

import pytest

from research_history_checkpoint import canonical
from research_krx_bulk import SourceError, archive, select_rows, validate_day
from integration_checks.krx_bulk_postgres import raw_day
from research_krx_bulk_api import ticket_valid
from research_krx_bulk_store import signature

DAY = date(2026, 11, 25)


def test_full_index_fields_and_zero_padded_stock_identity():
    result = validate_day(DAY, raw_day(DAY))
    assert len(result) == 22
    assert result['^KS11'][0]['close'] == 102.37 and result['^KQ11'][0]['turnover'] == 123600
    assert result['000660.KS'][0]['code'] == '000660'


def test_confirmed_zero_activity_and_missing_prices_are_preserved():
    rows = raw_day(DAY)
    row = next(r for s, r in rows if r.get('ISU_CD') == '086520')
    row.update(TDD_OPNPRC='-', TDD_HGPRC='0', TDD_LWPRC='0', ACC_TRDVOL='0', ACC_TRDVAL='0')
    bar, source = validate_day(DAY, rows)['086520.KQ']
    assert (bar['quote_status'], bar['open'], bar['close'], bar['volume']) == ('NO_TRADES', None, 103, 0)
    assert source['TDD_OPNPRC'] == '-'


def test_market_transfer_uses_real_historical_market():
    rows = raw_day(DAY)
    pair = next(x for x in rows if x[1].get('ISU_CD') == '086520')
    pair[0] = 'stk_bydd_trd'
    pair[1]['MKT_NM'] = 'KOSPI'
    assert validate_day(DAY, rows)['086520.KQ'][0]['market'] == 'KOSPI'


@pytest.mark.parametrize('error', ['duplicate','missing','ohlc','date','market','turnover','field'])
def test_invalid_source_refused(error):
    rows = raw_day(DAY)
    pair = next(x for x in rows if x[1].get('ISU_CD') == '000660')
    if error == 'duplicate':
        rows.append(pair)
    elif error == 'missing':
        rows.remove(pair)
    else:
        field, value = {'ohlc':('TDD_HGPRC','96'), 'date':('BAS_DD','20261124'),
                        'market':('MKT_NM','KOSDAQ'), 'turnover':('ACC_TRDVAL','-'),
                        'field':('TDD_OPNPRC', None)}[error]
        if error == 'field':
            del pair[1][field]
        else:
            pair[1][field] = value
    with pytest.raises(SourceError):
        validate_day(DAY, rows)


def test_provider_error_and_other_index_selection():
    row = raw_day(DAY)[0][1]
    assert select_rows('kospi_dd_trd', DAY, {'OutBlock_1':[{**row, 'IDX_NM':'코스피 200'}]}) == []
    with pytest.raises(SourceError):
        select_rows('kospi_dd_trd', DAY, {'respCode':'401', 'OutBlock_1':[row]})


def test_archive_requires_complete_readback_preserves_raw_and_hashes():
    bars = validate_day(DAY, raw_day(DAY))
    entries = [(DAY.isoformat(), s, bar['service'], canonical(raw)) for s,(bar,raw) in sorted(bars.items())]
    check = signature(entries)
    data = archive(iter(entries), [DAY], check, 2)
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        report = json.loads(z.read('validation.json'))
        assert report['rows'] == 22 and report['legacy_rows_compared'] == 2
        assert sum(n.startswith('csv/') for n in z.namelist()) == 22
        assert all(hashlib.sha256(z.read(n)).hexdigest() == h for n,h in report['files_sha256'].items())
    with pytest.raises(ValueError):
        archive(iter(entries[:-1]), [DAY], check, 2)


def test_private_grant_expiry_and_header_scope():
    secret = 'a' * 48
    setting = '1800000030:' + hashlib.sha256(secret.encode()).hexdigest()
    assert ticket_valid('Bearer ' + secret, setting, 1800000000)
    for header, now in [('Bearer ' + 'b'*48,1800000000), (secret,1800000000), ('Bearer '+secret,1800000040)]:
        assert not ticket_valid(header, setting, now)


def test_bulk_http_requires_owner_and_post_bearer(monkeypatch):
    from fastapi import HTTPException
    from starlette.requests import Request
    from research_krx_bulk_api import router
    monkeypatch.delenv('NOVA_KRX_TRANSFER_GRANT', raising=False)
    monkeypatch.delenv('NOVA_ACCESS_TOKEN', raising=False)
    routes = router(lambda request: True).routes
    req = Request({'type':'http','headers':[]})
    for route in routes:
        with pytest.raises(HTTPException) as e:
            route.endpoint(req)
        assert e.value.status_code == 401
    monkeypatch.setenv('NOVA_ACCESS_TOKEN','configured-owner')
    with pytest.raises(HTTPException) as e:
        next(r.endpoint for r in routes if 'POST' in r.methods)(req)
    assert e.value.status_code == 401


class PeriodSession:
    def __init__(self, pages, status=200):
        self.pages = iter(pages)
        self.status_code = status
        self.calls = []
    def get(self, url, **kwargs):
        self.calls.append(kwargs['params'])
        return self
    def __enter__(self):
        return self
    def __exit__(self, *args):
        pass
    def iter_content(self, size):
        yield json.dumps(next(self.pages)).encode()


def test_whole_period_request_includes_end_and_preserves_original_fields():
    from research_krx_period import fetch_period, STOCK
    from research_krx_bulk import normalize
    row = {'basDt':'20261125','srtnCd':'005930','itmsNm':'삼성전자','mrktCtg':'KOSPI',
           'mkp':'100','hipr':'103','lopr':'97','clpr':'102','trqu':'123','trPrc':'12345'}
    body = {'response':{'header':{'resultCode':'00'},'body':{'pageNo':1,'totalCount':2,
            'items':{'item':[row,{**row,'basDt':'20261126'}]}}}}
    session = PeriodSession([body])
    found = fetch_period('fixture', '005930.KS', (DAY,), session)
    assert found == {'2026-11-25':row} and session.calls[0]['endBasDt'] == '20261126'
    assert normalize(STOCK,row,DAY)['turnover'] == 12345


def test_range_identity_and_duplicate_checks():
    from research_krx_period import fetch_period
    row = {'idxNm':'코스피','basDt':'20261125'}
    for bad in ([row,row],[{**row,'idxNm':'코스피 200'}],[{**row,'basDt':'20261120'}]):
        payload = {'response':{'header':{'resultCode':'00'},'body':{'pageNo':1,'totalCount':len(bad),'items':{'item':bad}}}}
        with pytest.raises(SourceError):
            fetch_period('fixture','^KS11',(DAY,),PeriodSession([payload]))


def test_range_rejects_service_permissions_and_changing_totals():
    from research_krx_period import fetch_period
    with pytest.raises(SourceError,match='FSC_AUTH_DENIED'):
        fetch_period('fixture','^KQ11',(DAY,),PeriodSession([],403))
    row = {'idxNm':'코스닥','basDt':'20261125'}
    def page(n, total):
        return {'response':{'header':{'resultCode':'00'},'body':{'pageNo':n,'totalCount':total,'items':{'item':[row]}}}}
    with pytest.raises(SourceError,match='FSC_INVALID_PAGINATION'):
        fetch_period('fixture','^KQ11',(DAY,),PeriodSession([page(1,2),page(2,4)]))
