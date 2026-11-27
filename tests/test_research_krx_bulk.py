from datetime import date
import hashlib
import io
import json
import zipfile

import pytest

from research_history_checkpoint import canonical
from research_krx_bulk import INDEX_FIELDS, INDEX_NAMES, MARKETS, SERVICES, STOCK_FIELDS, SourceError, archive, select_rows, validate_day
from research_krx_bulk_api import ticket_valid
from research_krx_bulk_store import signature

DAY = date(2026, 11, 25)


def raw_day(day=DAY):
    rows = []
    for service, symbols in SERVICES.items():
        for symbol in symbols:
            index = service in INDEX_NAMES
            row = {'BAS_DD': day.strftime('%Y%m%d')}
            fields = INDEX_FIELDS if index else STOCK_FIELDS
            for key, number in zip(('open', 'high', 'low', 'close', 'volume', 'turnover'),
                                   ('100','104','98','103','1,200','123,600')):
                row[fields[key]] = number
            if index:
                row['IDX_NM'] = '코스피' if symbol == '^KS11' else '코스닥'
                row['CLSPRC_IDX'] = '102.37'
            else:
                row.update(ISU_CD=symbol[:6], ISU_NM='테스트', MKT_NM=MARKETS[service])
            rows.extend(select_rows(service, day, {'OutBlock_1': [row]}))
    return rows


def test_full_index_fields_and_zero_padded_stock_identity():
    result = validate_day(DAY, raw_day())
    assert len(result) == 22
    assert result['^KS11'][0]['close'] == 102.37 and result['^KQ11'][0]['turnover'] == 123600
    assert result['000660.KS'][0]['code'] == '000660'


def test_confirmed_zero_activity_and_missing_prices_are_preserved():
    rows = raw_day()
    row = next(r for s, r in rows if r.get('ISU_CD') == '086520')
    row.update(TDD_OPNPRC='-', TDD_HGPRC='0', TDD_LWPRC='0', ACC_TRDVOL='0', ACC_TRDVAL='0')
    bar, source = validate_day(DAY, rows)['086520.KQ']
    assert (bar['quote_status'], bar['open'], bar['close'], bar['volume']) == ('NO_TRADES', None, 103, 0)
    assert source['TDD_OPNPRC'] == '-'


def test_market_transfer_uses_real_historical_market():
    rows = raw_day()
    pair = next(x for x in rows if x[1].get('ISU_CD') == '086520')
    pair[0] = 'stk_bydd_trd'
    pair[1]['MKT_NM'] = 'KOSPI'
    assert validate_day(DAY, rows)['086520.KQ'][0]['market'] == 'KOSPI'


@pytest.mark.parametrize('error', ['duplicate','missing','ohlc','date','market','turnover','field'])
def test_invalid_source_refused(error):
    rows = raw_day()
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
    row = raw_day()[0][1]
    assert select_rows('kospi_dd_trd', DAY, {'OutBlock_1':[{**row, 'IDX_NM':'코스피 200'}]}) == []
    with pytest.raises(SourceError):
        select_rows('kospi_dd_trd', DAY, {'respCode':'401', 'OutBlock_1':[row]})


def test_archive_requires_complete_readback_preserves_raw_and_hashes():
    bars = validate_day(DAY, raw_day())
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
