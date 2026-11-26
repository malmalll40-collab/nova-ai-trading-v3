from datetime import date,timedelta
import sqlite3
import pytest
from research_history_checkpoint import initialize,append,saved,missing_windows,manifest,TABLE
from nova_v576_expanded_coverage import SYMBOLS

D=tuple(date(2026,11,23)+timedelta(days=i) for i in range(3))
S=next(s for s in SYMBOLS if s.endswith('.KS'))
ROW={'open':10,'high':12,'low':9,'close':11,'volume':5,'turnover':None}

@pytest.fixture
def store():
    with sqlite3.connect(':memory:') as db:
        cur=db.cursor()
        yield cur,initialize(cur,'sqlite',D)

def test_resume_only_missing(store):
    c,k=store
    assert append(c,'sqlite',k,S,{D[1]:ROW})==1
    assert missing_windows(c,'sqlite',k,S)==[(D[0],),(D[2],)]
    assert append(c,'sqlite',k,S,{D[0]:ROW,D[2]:ROW})==2
    assert missing_windows(c,'sqlite',k,S)==[]

def test_idempotence_and_revision(store):
    c,k=store
    assert append(c,'sqlite',k,S,{D[0]:ROW})==1
    assert append(c,'sqlite',k,S,{D[0]:ROW})==0
    with pytest.raises(ValueError,match='HISTORY_REVISION_CONFLICT'):
        append(c,'sqlite',k,S,{D[0]:dict(ROW,close=10)})
    assert saved(c,'sqlite',k,S)[D[0]]['close']==11

def test_invalid_rows_fail_before_insert(store):
    c,k=store
    with pytest.raises(ValueError):
        append(c,'sqlite',k,S,{D[0]:ROW,D[1]:dict(ROW,volume=-1)})
    assert saved(c,'sqlite',k,S)=={}

def test_outside_window_rejected(store):
    c,k=store
    with pytest.raises(ValueError,match='UNEXPECTED_SESSION'):
        append(c,'sqlite',k,S,{D[0]-timedelta(days=1):ROW})

def test_manifest_window_isolation(store):
    c,k=store
    append(c,'sqlite',k,S,{D[0]:ROW})
    other=initialize(c,'sqlite',D[1:])
    assert other!=k and saved(c,'sqlite',other,S)=={}
    with pytest.raises(ValueError):
        manifest((D[1],D[0]))

def test_corruption_detected(store):
    c,k=store
    append(c,'sqlite',k,S,{D[0]:ROW})
    c.execute(f'UPDATE {TABLE} SET digest=?',('bad',))
    with pytest.raises(ValueError,match='ROW_CORRUPT'):
        saved(c,'sqlite',k,S)
