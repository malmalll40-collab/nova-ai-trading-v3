"""Transactional bulk publication regression for the existing disposable CI database."""
import io
import json
import os
from datetime import date
import zipfile

import psycopg

from research_krx_bulk import INDEX_FIELDS, INDEX_NAMES, MARKETS, SERVICES, STOCK_FIELDS, SourceError, select_rows
from research_krx_bulk_store import BARS, LOCK, STAGE, download, initialize, publish, stage, step


def raw_day(day):
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


def main():
    days = (date(2026,11,23), date(2026,11,24), date(2026,11,25))
    dataset = 'bulk-postgres-fixture'
    dsn = os.environ['NOVA_TEST_POSTGRES_DSN']
    with psycopg.connect(dsn,autocommit=True) as db, psycopg.connect(dsn,autocommit=True) as other:
        with db.transaction(), db.cursor() as cur:
            initialize(cur)
        first = step(db, dataset, days, raw_day, limit=1)
        assert first['staged_sessions'] == 1 and first['published_rows'] == 0
        with db.transaction(), db.cursor() as cur:
            cur.execute('SELECT pg_advisory_xact_lock(%s)', (LOCK,))
            assert step(other, dataset, days, raw_day) == {'status':'BUSY'}
        with db.transaction(), db.cursor() as cur:
            try:
                download(cur, dataset, days)
            except SourceError as exc:
                assert str(exc) == 'KRX_NOT_PUBLISHED'
            else:
                raise AssertionError('Partial download allowed')
        calls = []
        def read(day):
            calls.append(day)
            return raw_day(day)
        done = step(other, dataset, days, read)
        assert calls == list(days[1:]) and done['published_rows'] == 66
        assert step(db, dataset, days, read) == done and calls == list(days[1:])
        with db.transaction(), db.cursor() as cur:
            exported = download(cur, dataset, days)
            with zipfile.ZipFile(io.BytesIO(exported)) as z:
                assert json.loads(z.read('validation.json'))['database_sha256'] == done['sha256']
            assert publish(cur, dataset, days) == done
        try:
            with db.transaction(), db.cursor() as cur:
                cur.execute(f'UPDATE {BARS} SET close=0 WHERE dataset=%s', (dataset,))
                download(cur, dataset, days)
        except SourceError as exc:
            assert str(exc) == 'KRX_DB_VALUE_CONFLICT'
        else:
            raise AssertionError('Corrupt readback accepted')
        other_dataset = 'bulk-postgres-conflict-fixture'
        try:
            with db.transaction(), db.cursor() as cur:
                for day in days:
                    stage(cur, other_dataset, day, raw_day(day))
                cur.execute(f'UPDATE {STAGE} SET checksum=%s WHERE dataset=%s', ('broken',other_dataset))
                publish(cur, other_dataset, days)
        except SourceError as exc:
            assert str(exc) == 'KRX_STAGE_CHECKSUM_CONFLICT'
        else:
            raise AssertionError('Corrupt staging published')
        with db.cursor() as cur:
            cur.execute(f'SELECT count(*) FROM {BARS} WHERE dataset=%s',(other_dataset,))
            assert cur.fetchone() == (0,)
        from research_krx_period import STOCK, MARKET_FIELDS
        from research_krx_bulk_store import complete_day, period_step
        from research_krx_bulk import normalize
        import unittest.mock as mock
        mixed = 'bulk-period-fixture'
        def period_reader(key, symbol, calendar, session):
            if symbol.startswith('^'):
                raise SourceError('FSC_AUTH_DENIED')
            found = {}
            for day in calendar:
                for svc, raw in raw_day(day):
                    bar = normalize(svc, raw, day)
                    if bar['symbol'] == symbol:
                        row = {'basDt':day.strftime('%Y%m%d'), 'srtnCd':symbol[:6],'itmsNm':'테스트','mrktCtg':bar['market']}
                        row.update({field:str(bar[k]) for k,field in MARKET_FIELDS.items()})
                        found[day.isoformat()] = row
            return found
        with mock.patch('research_krx_bulk_store.fetch_period',side_effect=period_reader) as ranges:
            for _ in range(21):
                assert period_step(db,mixed,days,'fixture')['status'] == 'PERIOD'
            assert period_step(db,mixed,days,'fixture') is None and ranges.call_count == 21
        class GapReader:
            key = 'fixture'
            def read(self, service, day):
                assert service in INDEX_NAMES
                return [pair for pair in raw_day(day) if pair[0] == service]
        done = step(db,mixed,days,lambda day:complete_day(db,mixed,day,GapReader()))
        assert done['published_rows'] == 66
        with db.cursor() as cur:
            report = zipfile.ZipFile(io.BytesIO(download(cur,mixed,days)))
            sources = json.loads(report.read('validation.json'))['rows_per_source_service']
            assert sources == {STOCK:60,'kospi_dd_trd':3,'kosdaq_dd_trd':3}
    print('KRX bulk PostgreSQL PASS: private staging, locks, rollback, replay, atomic COPY and verified export')


if __name__ == '__main__':
    main()
