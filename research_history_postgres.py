"""Explicit PostgreSQL adapter for isolated historical research transactions.

No implicit connection, schema migration, scheduling, provider requests or
production startup hook. Caller must establish separate execution admission.
"""
from contextlib import contextmanager
from urllib.parse import urlsplit

def transaction_factory(dsn, *, connect=None):
    if not isinstance(dsn,str) or urlsplit(dsn).scheme not in ('postgres','postgresql'):
        raise ValueError('POSTGRES_DSN_REQUIRED')
    if connect is None:
        import psycopg
        connect=psycopg.connect

    @contextmanager
    def transaction():
        # psycopg context commits on normal exit, rolls back on exception.
        with connect(dsn,connect_timeout=5) as db:
            with db.cursor() as cur:
                cur.execute("SET LOCAL statement_timeout = '5s'")
                cur.execute("SET LOCAL lock_timeout = '2s'")
                yield cur,'postgresql'
    return transaction
