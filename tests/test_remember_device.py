from pathlib import Path
from fastapi.testclient import TestClient
import server


def test_remember_device_secure_cookie_and_restart(monkeypatch):
    monkeypatch.setenv('NOVA_ACCESS_TOKEN','test-secret-for-device-1234567')
    monkeypatch.setenv('NOVA_PUBLIC_DEPLOY','1')
    with TestClient(server.app, base_url='https://testserver') as client:
        assert client.get('/api/auth/status').json() == {'authenticated':False,'token_required':True}
        assert client.get('/api/search',params={'q':'에코프로'}).status_code == 401
        login=client.post('/api/auth/login',json={'token':'test-secret-for-device-1234567','remember':True})
        assert login.status_code == 200
        header=login.headers['set-cookie']
        assert 'HttpOnly' in header and 'Secure' in header and 'SameSite=lax' in header
        assert 'Max-Age=2592000' in header
        assert 'test-secret-for-device-1234567' not in header
        assert client.get('/api/auth/status').json()['authenticated'] is True
        assert client.get('/api/analysis',params={'source':'demo'}).status_code == 200
        cookie=client.cookies.get('nova3_auth')
    with TestClient(server.app, base_url='https://testserver') as reopened:
        cookie_header={'Cookie':'nova3_auth='+cookie}
        assert reopened.get('/api/auth/status',headers=cookie_header).json()['authenticated'] is True
        assert reopened.get('/api/analysis',params={'source':'demo'},headers=cookie_header).status_code == 200
        logout=reopened.post('/api/auth/logout',headers=cookie_header)
        assert logout.status_code == 200 and 'Max-Age=0' in logout.headers['set-cookie']
        assert reopened.get('/api/auth/status').json()['authenticated'] is False
        assert reopened.get('/api/analysis',params={'source':'demo'}).status_code == 401


def test_wrong_and_tampered_cookie_are_rejected(monkeypatch):
    monkeypatch.setenv('NOVA_ACCESS_TOKEN','test-secret-for-device-1234567')
    monkeypatch.setenv('NOVA_PUBLIC_DEPLOY','1')
    with TestClient(server.app,base_url='https://testserver') as client:
        assert client.post('/api/auth/login',json={'token':'wrong','remember':True}).status_code == 401
        assert 'set-cookie' not in client.post('/api/auth/login',json={'token':'wrong'}).headers
        login=client.post('/api/auth/login',json={'token':'test-secret-for-device-1234567','remember':True})
        valid=client.cookies.get('nova3_auth')
        assert login.status_code == 200
        client.cookies.set('nova3_auth',valid[:-1]+'0' if valid[-1]!='0' else valid[:-1]+'1')
        assert client.get('/api/auth/status').json()['authenticated'] is False
        assert client.get('/api/status').status_code == 401
        client.cookies.set('nova3_auth',valid)
        monkeypatch.setenv('NOVA_ACCESS_TOKEN','rotated-secret-for-device-987654321')
        assert client.get('/api/auth/status').json()['authenticated'] is False


def test_non_remembered_cookie_is_browser_session_only(monkeypatch):
    monkeypatch.setenv('NOVA_ACCESS_TOKEN','test-secret-for-device-1234567')
    monkeypatch.delenv('NOVA_PUBLIC_DEPLOY',raising=False)
    with TestClient(server.app) as client:
        login=client.post('/api/auth/login',json={'token':'test-secret-for-device-1234567','remember':False})
        assert login.status_code == 200
        header=login.headers['set-cookie']
        assert 'Max-Age' not in header and 'Secure' not in header
        assert client.get('/api/auth/status').json()['authenticated'] is True


def test_new_mobile_ui_and_service_worker():
    html=Path(server.ASSETS/'index.html').read_text(encoding='utf-8')
    js=Path(server.ASSETS/'app.js').read_text(encoding='utf-8')
    assert 'rememberDevice' in html and 'logoutButton' in html
    assert '/api/auth/login' in js and '/api/auth/status' in js and '/api/auth/logout' in js
    assert 'nova3-shell-v45' in Path(server.ASSETS/'sw.js').read_text()
    assert 'Trading Mobile v5.6.8' in html
    assert server.app.title == 'NOVA AI Trading Mobile'
