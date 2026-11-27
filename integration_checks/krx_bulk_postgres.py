"""Transactional bulk publication regression for the existing disposable CI database."""
import io
import json
import os
from datetime import date, timedelta
import zipfile

import psycopg

from research_history_checkpoint import canonical, digest
from research_krx_bulk import SourceError
from research_krx_bulk_store import BARS, LOCK, STAGE, download, initialize, publish, stage, step
from tests.test_research_krx_bulk import raw_day


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
    print('KRX bulk PostgreSQL PASS: private staging, locks, rollback, replay, atomic COPY and verified export')


if __name__ == '__main__':
    main()
