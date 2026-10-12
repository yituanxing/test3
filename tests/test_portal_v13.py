"""V1.3 product portal smoke: real/synthetic provenance and authenticated flows."""
from fastapi.testclient import TestClient
from app import access
import app.main as main


def test_product_portal_serves_integrated_workbench(tmp_path, monkeypatch):
    monkeypatch.delenv('ZD_AUTH_TEST_COMPAT', raising=False)
    monkeypatch.setattr(main, 'DB', tmp_path / 'portal.sqlite3')
    main.setup_db()
    client = TestClient(main.app)
    page = client.get('/portal')
    assert page.status_code == 200
    assert '重点场所智能预警分析平台' in page.text
    assert '场所监测' in page.text and '预警中心' in page.text
    assert '匿名目标事件' in page.text and '合成演示' in page.text
    assert '/api/insights/overview?days=7' in page.text
    assert '/api/frigate/events?limit=100' in page.text
    assert client.get('/api/insights/overview').status_code == 401
    assert client.get('/api/frigate/events').status_code == 401
    with main.db() as c:
        state_id = c.execute("SELECT id FROM regions WHERE level=1 ORDER BY id LIMIT 1").fetchone()['id']
        access.create_user(c, 'portaladmin', 'password-long-enough-2026', state_id)
    auth = client.post('/api/auth/login', json={
        'username': 'portaladmin', 'password': 'password-long-enough-2026'})
    assert auth.status_code == 200, auth.text
    headers = {'Authorization': 'Bearer ' + auth.json()['access_token']}
    assert client.get('/api/auth/me', headers=headers).status_code == 200
    assert client.get('/api/insights/overview?days=7', headers=headers).status_code == 200
    assert client.get('/api/frigate/events?limit=100', headers=headers).status_code == 200
    assert client.post('/api/business/seed', headers=headers).status_code == 200
    assert client.get('/api/insights/overview?days=7', headers=headers).json()['synthetic']['cards']['place_count'] >= 1


def test_portal_place_scope_enforced_for_lower_role(tmp_path, monkeypatch):
    monkeypatch.delenv('ZD_AUTH_TEST_COMPAT', raising=False)
    monkeypatch.setattr(main, 'DB', tmp_path / 'portal-scope.sqlite3')
    main.setup_db()
    client = TestClient(main.app)
    with main.db() as c:
        region = c.execute("SELECT id FROM regions WHERE level=4 ORDER BY id LIMIT 1").fetchone()['id']
        access.create_user(c, 'portalscope', 'password-long-enough-2026', region)
    resp = client.post('/api/auth/login', json={
        'username': 'portalscope', 'password': 'password-long-enough-2026'})
    headers={'Authorization': 'Bearer ' + resp.json()['access_token']}
    assert client.get('/api/places', headers=headers).status_code == 200
    assert client.get('/api/insights/overview', headers=headers).status_code == 200
    assert client.get('/api/business/analysis', headers=headers).status_code == 403
