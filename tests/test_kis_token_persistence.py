"""KIS token cache checks without broker calls or a real database."""
import sys
import threading
import time
import types

import pytest
from data_sources import KIS, TOKENS
import kis_token_store as cache


class Cursor:
    def __init__(self, table):
        self.table=table
        self.row=None
    def __enter__(self): return self
    def __exit__(self, *_): return False
    def execute(self, statement, params=()):
        if 'SELECT nonce, encrypted_token, expires_at' in statement:
            self.row=self.table.get(params[0])
        elif 'INSERT INTO nova_kis_oauth_cache_v1' in statement:
            self.table[params[0]]=(params[1],params[2],params[3])
        elif statement.startswith(('CREATE TABLE','SELECT pg_advisory_xact_lock')):
            pass
        else: raise AssertionError(statement)
    def fetchone(self): return self.row


class Connection:
    def __init__(self,table): self.table=table
    def __enter__(self): return self
    def __exit__(self, *_): return False
    def cursor(self): return Cursor(self.table)


def fake_database(monkeypatch, table):
    class FakeDbError(Exception):pass
    fake=types.SimpleNamespace(connect=lambda url,connect_timeout: Connection(table),Error=FakeDbError)
    monkeypatch.setitem(sys.modules,'psycopg',fake)
    monkeypatch.setenv('DATABASE_URL','postgresql://unused-test-url')
    return fake


def test_persist_across_cold_starts_encrypted(monkeypatch):
    table={}
    fake_database(monkeypatch,table)
    TOKENS.clear()
    calls=[]
    def fake_issue(self):
        calls.append('asked broker')
        return 'sensitive-token-from-kis', 86400
    monkeypatch.setattr(KIS,'_request_token',fake_issue)
    first=KIS('test-app-key','test-secret')
    assert first.token=='sensitive-token-from-kis'
    assert len(calls)==1 and len(table)==1
    row=next(iter(table.values()))
    assert b'sensitive-token-from-kis' not in bytes(row[1])
    TOKENS.clear()  # simulate Render process restart
    second=KIS('test-app-key','test-secret')
    assert second.token==first.token and len(calls)==1
    TOKENS.clear()


def test_expired_cache_refreshes(monkeypatch):
    table={}
    fake_database(monkeypatch,table)
    TOKENS.clear()
    calls=[]
    def fake_issue(self):
        calls.append('request')
        return f'test-token-{len(calls)}',86400
    monkeypatch.setattr(KIS,'_request_token',fake_issue)
    first=KIS('key','secret').token
    for cache_id,row in table.items():
        table[cache_id]=(row[0],row[1],time.time()-1)
    TOKENS.clear()
    second=KIS('key','secret').token
    assert first=='test-token-1' and second=='test-token-2'
    assert len(calls)==2
    TOKENS.clear()


def test_failed_postgres_does_not_reissue(monkeypatch):
    class FakeDbError(Exception): pass
    def unavailable(url,connect_timeout):raise FakeDbError()
    monkeypatch.setitem(sys.modules,'psycopg',types.SimpleNamespace(connect=unavailable,Error=FakeDbError))
    monkeypatch.setenv('DATABASE_URL','postgresql://unavailable')
    TOKENS.clear()
    calls=[]
    monkeypatch.setattr(KIS,'_request_token',lambda self:(calls.append('called') or ('t',86400)))
    with pytest.raises(RuntimeError,match='KIS_TOKEN_DATABASE_UNAVAILABLE'):
        KIS('key','secret')
    assert calls==[]
    TOKENS.clear()


def test_without_database_still_uses_memory(monkeypatch):
    monkeypatch.delenv('DATABASE_URL',raising=False)
    TOKENS.clear()
    calls=[]
    monkeypatch.setattr(KIS,'_request_token',lambda self:(calls.append('call') or ('local',86400)))
    assert KIS('k','s').token=='local'
    assert KIS('k','s').token=='local'
    assert calls==['call']
    TOKENS.clear()


def test_local_concurrency_avoids_duplicate_broker_calls(monkeypatch):
    monkeypatch.delenv('DATABASE_URL',raising=False)
    TOKENS.clear()
    counter=[]
    def fake_issue(self):
        time.sleep(.02)
        counter.append(1)
        return 'same-token',86400
    monkeypatch.setattr(KIS,'_request_token',fake_issue)
    result=[]
    threads=[threading.Thread(target=lambda: result.append(KIS('concurrent','key').token)) for _ in range(8)]
    for th in threads:th.start()
    for th in threads:th.join()
    assert len(result)==8 and all(x=='same-token' for x in result)
    assert len(counter)==1
    TOKENS.clear()
