"""Acceptance: real-input operational read model never fabricates P###.
The inserted Frigate events below are *test fixtures*, not claimed as camera
proof. Actual live evidence is validated in the separate video integration CI.
"""
from datetime import datetime,timezone
from fastapi.testclient import TestClient
import pytest
from app import access
import app.main as main


@pytest.fixture
def case(tmp_path,monkeypatch):
    monkeypatch.delenv('ZD_AUTH_TEST_COMPAT',raising=False)
    monkeypatch.setattr(main,'DB',tmp_path/'actual-source-contract.sqlite3')
    main.setup_db()
    with main.db() as c:
        state=c.execute('SELECT id FROM regions WHERE level=1').fetchone()[0]
        county=c.execute('SELECT id FROM regions WHERE level=2').fetchone()[0]
        external_county=c.execute('INSERT INTO regions(name,parent_id,level) VALUES(?,?,2)',('Outside',state)).lastrowid
        ext_street=c.execute('INSERT INTO regions(name,parent_id,level) VALUES(?,?,3)',('Outside street',external_county)).lastrowid
        ext_leaf=c.execute('INSERT INTO regions(name,parent_id,level) VALUES(?,?,4)',('Outside leaf',ext_street)).lastrowid
        outsider=c.execute('INSERT INTO places(name,region_id) VALUES(?,?)',('Other area',ext_leaf)).lastrowid
        local=c.execute("SELECT id FROM places WHERE name='测试场所 A'").fetchone()[0]
        for username,reg in [('realadmin',state),('realcounty',county)]:
            access.create_user(c,username,'test-password-strong-enough-2026',reg)
    with TestClient(main.app) as client:
        def auth(user):
            resp=client.post('/api/auth/login',json={
                'username':user,'password':'test-password-strong-enough-2026'})
            assert resp.status_code==200,resp.text
            return {'Authorization':'Bearer '+resp.json()['access_token']}
        yield client,auth,local,outsider


def test_real_read_model_empty_is_truthful_even_after_synthetic_seed(case):
    client,auth,local,_=case
    h=auth('realadmin')
    assert client.get('/real').status_code==200
    assert client.get('/api/real/evidence').status_code==401
    empty=client.get('/api/real/evidence',headers=h)
    assert empty.status_code==200,empty.text
    base=empty.json()
    assert base['mode']=='real_evidence_only'
    assert base['real_event_counts']['person_objects']==0
    assert base['recent_frigate_events']==[]
    assert base['contains_synthetic_identity'] is False
    assert base['contains_synthetic_alerts'] is False
    assert base['counts_unique_people'] is False
    assert client.post('/api/business/seed',headers=h).status_code==200
    assert client.post('/api/base/demo/duplicates',headers=h).status_code==200
    after=client.get('/api/real/evidence',headers=h).json()
    assert after['real_event_counts']==base['real_event_counts']
    assert after['recent_frigate_events']==[]
    assert 'P099' not in str(after) and 'P001' not in str(after)
    assert client.get('/api/real/evidence?days=0',headers=h).status_code==422


def test_real_upstream_events_count_once_and_hide_neighboring_jurisdiction(case):
    client,auth,local,other=case
    stamp=datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    with main.db() as c:
        for event_id,camera,pid in (
           ('real-fixture-1','camera_a',local),('real-fixture-2','camera_a',local),
           ('other-county-fixture','camera_other',other)):
            c.execute('''INSERT INTO frigate_object_events
                (event_id,camera,place_id,label,started_at,ended_at,
                score,has_snapshot,updated_at)
                VALUES(?,?,?,'person',?,?,?,?,?)''',
                (event_id,camera,pid,stamp,stamp,0.85,1,stamp))
    county=client.get('/api/real/evidence',headers=auth('realcounty'))
    assert county.status_code==200,county.text
    d=county.json()
    assert d['real_event_counts']['person_objects']==2
    assert {e['event_id'] for e in d['recent_frigate_events']}=={
        'real-fixture-1','real-fixture-2'}
    assert d['confirmed_cross_camera_identity'] is False
    assert all(e['subject_id'] is None for e in d['recent_frigate_events'])
    assert 'other-county-fixture' not in str(d)
    all_=client.get('/api/real/evidence',headers=auth('realadmin')).json()
    assert all_['real_event_counts']['person_objects']==3
    assert len(all_['recent_frigate_events'])==3
    assert client.get('/api/real/evidence?limit=1',headers=auth('realadmin')).json()['recent_events_truncated'] is True
