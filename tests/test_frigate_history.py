"""Fixed-camera location truth: event-time provenance, not sync-time guesses.

These are API + real SQLite tests. Remote Frigate HTTP transport is mocked;
the evidence we validate is *where* a local anonymous event belongs.
"""
from datetime import datetime, timedelta, timezone
import time
from unittest.mock import Mock,patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def scenario(tmp_path,monkeypatch):
    from app import main,access
    monkeypatch.delenv('ZD_AUTH_TEST_COMPAT',raising=False)
    monkeypatch.setattr(main,'DB',tmp_path/'frigate-historic-camera.sqlite')
    main.setup_db()
    with main.db() as c:
        root=c.execute('SELECT id FROM regions WHERE level=1 LIMIT 1').fetchone()['id']
        county=c.execute('SELECT id FROM regions WHERE level=2 LIMIT 1').fetchone()['id']
        a=c.execute("SELECT id FROM places WHERE name='测试场所 A'").fetchone()['id']
        b=c.execute("SELECT id FROM places WHERE name='测试场所 B'").fetchone()['id']
        foreign=c.execute('INSERT INTO regions(name,parent_id,level) VALUES(?,?,2)',
                          ('异地县摄像机验收',root)).lastrowid
        street=c.execute('INSERT INTO regions(name,parent_id,level) VALUES(?,?,3)',
                         ('异地县摄像机街道',foreign)).lastrowid
        leaf=c.execute('INSERT INTO regions(name,parent_id,level) VALUES(?,?,4)',
                       ('异地县摄像机社区',street)).lastrowid
        remote=c.execute('INSERT INTO places(name,region_id) VALUES(?,?)',
                         ('异地录像场所',leaf)).lastrowid
        access.create_user(c,'epochstate','epoch-state-password-2026',root)
        access.create_user(c,'epochcounty','epoch-county-password-2026',county)
        access.create_user(c,'epochremote','epoch-remote-password-2026',foreign)
    cli=TestClient(main.app)
    def token(user,pw):
        resp=cli.post('/api/auth/login',json={'username':user,'password':pw})
        assert resp.status_code==200,resp.text
        return {'Authorization':'Bearer '+resp.json()['access_token']}
    return main,cli,a,b,remote,token('epochstate','epoch-state-password-2026'),token(
        'epochcounty','epoch-county-password-2026'),token('epochremote','epoch-remote-password-2026')


def person(event_id,camera,when):
    return {'id':event_id,'camera':camera,'label':'person',
            'start_time':when,'false_positive':False,
            'data':{'top_score':0.87},'has_snapshot':True}


def test_late_fr_gate_event_follows_historic_epoch_not_current_location(scenario):
    main,client,a,b,remote,root,county,other=scenario
    bind='/api/frigate/cameras'
    result=client.post(bind,headers=root,json={'camera':'main_door','place_id':a})
    assert result.status_code==200,result.text
    first=client.get('/api/site/frigate/main_door/bindings',headers=root).json()
    assert len(first['epochs'])==1 and first['epochs'][0]['provenance']=='explicit'
    time.sleep(0.018)
    first_seen=time.time()
    time.sleep(0.018)
    changed=client.post(bind,headers=root,json={'camera':'main_door','place_id':b})
    assert changed.status_code==200,changed.text
    time.sleep(0.018)
    second_seen=time.time()
    timeline=client.get('/api/site/frigate/main_door/bindings',headers=county)
    assert timeline.status_code==200,timeline.text
    epochs=timeline.json()['epochs']
    assert len(epochs)==2
    assert epochs[0]['place_id']==a and epochs[1]['place_id']==b
    assert epochs[0]['valid_until']==epochs[1]['valid_from']
    assert epochs[1]['valid_until'] is None
    # Camera registered at t0. There is NO proof that it was at A at t0-1h.
    unknown=(datetime.fromisoformat(epochs[0]['valid_from'])-
             timedelta(hours=1)).timestamp()
    fake=Mock()
    fake.raise_for_status.return_value=None
    fake.json.return_value=[person('late_a','main_door',first_seen),
                            person('recent_b','main_door',second_seen),
                            person('historic_unknown','main_door',unknown)]
    with patch('app.main.httpx.get',return_value=fake):
        first_sync=client.post('/api/frigate/sync',headers=root)
        assert first_sync.status_code==200,first_sync.text
        assert first_sync.json()['inserted']==2
        assert first_sync.json()['skipped']==1
        assert first_sync.json()['unlocated_before_known_binding']==1
        replay=client.post('/api/frigate/sync',headers=root).json()
        assert replay['inserted']==0 and replay['updated']==2
        assert replay['unlocated_before_known_binding']==1
    persisted={e['event_id']:e for e in client.get('/api/frigate/events',headers=root).json()}
    assert set(persisted)=={'late_a','recent_b'}
    assert persisted['late_a']['place_id']==a
    assert persisted['recent_b']['place_id']==b
    assert persisted['late_a']['binding_epoch_id']==epochs[0]['id']
    assert persisted['recent_b']['binding_epoch_id']==epochs[1]['id']
    occurrences=client.get('/api/site/occurrences',headers=county).json()['occurrences']
    assert len(occurrences)==2
    assert {x['binding_epoch_id'] for x in occurrences}=={e['id'] for e in epochs}
    assert all(x['subject_id'] is None and x['counts_as_unique_person'] is False
               for x in occurrences)
    assert client.get('/api/monitoring/overview?days=1',headers=county).json()[
        'counts']['person_objects']==2
    # The old first epoch is historic; changing pointer cannot rewrite it.
    with main.db() as c:
        assert [x['place_id'] for x in c.execute(
            "SELECT place_id FROM frigate_object_events ORDER BY event_id")]==[a,b]
        assert c.execute("SELECT COUNT(*) FROM synthetic_observations").fetchone()[0]==0


def test_legacy_camera_backfill_never_claims_unknown_old_video(scenario):
    main,client,a,b,remote,root,county,other=scenario
    with main.db() as c:
        c.execute("INSERT INTO frigate_camera_bindings(camera,place_id) VALUES('oldcam',?)",(a,))
        c.execute("""INSERT INTO frigate_object_events
          (event_id,camera,place_id,label,started_at,score,has_snapshot,updated_at)
          VALUES('old_provenance','oldcam',?,'person',
                 '2023-05-01T08:00:00+00:00',0.9,0,'2023-05-01T08:00:00+00:00')""",(a,))
    main.setup_db() # actual installed-DB migration, NOT synthetic backdating
    h=client.get('/api/site/frigate/oldcam/bindings',headers=root).json()
    assert len(h['epochs'])==1 and h['epochs'][0]['provenance']=='legacy_backfill'
    assert h['epochs'][0]['valid_from'] > '2023-05-01'
    initial_id=h['epochs'][0]['id']
    fake=Mock()
    fake.raise_for_status.return_value=None
    fake.json.return_value=[person('old_provenance','oldcam',1682928000),
                            person('new_missing','oldcam',1682928000)]
    with patch('app.main.httpx.get',return_value=fake):
        result=client.post('/api/frigate/sync',headers=root)
        assert result.status_code==200,result.text
        assert result.json()['updated']==1 and result.json()['inserted']==0
        assert result.json()['unlocated_before_known_binding']==1
    with main.db() as c:
        original=c.execute("SELECT place_id,binding_epoch_id,started_at FROM frigate_object_events WHERE event_id='old_provenance'").fetchone()
        assert original['place_id']==a and original['binding_epoch_id'] is None
        assert original['started_at']=='2023-05-01T08:00:00+00:00'
    # A manually inserted old pointer can also be re-bound without claiming
    # that it was historically bound to A before its first audit timestamp.
    moved=client.post('/api/frigate/cameras',headers=root,
                      json={'camera':'oldcam','place_id':b})
    assert moved.status_code==200,moved.text
    h2=client.get('/api/site/frigate/oldcam/bindings',headers=root).json()['epochs']
    assert len(h2)==2 and h2[0]['id']==initial_id


def test_relocated_camera_history_cannot_leak_across_counties(scenario):
    main,client,a,b,remote,root,county,other=scenario
    name='privacy_gate'
    assert client.post('/api/frigate/cameras',headers=root,
                       json={'camera':name,'place_id':a}).status_code==200
    assert client.get('/api/site/frigate/privacy_gate/bindings',headers=county).status_code==200
    assert client.post('/api/frigate/cameras',headers=root,
                       json={'camera':name,'place_id':remote}).status_code==200
    full=client.get('/api/site/frigate/privacy_gate/bindings',headers=root)
    assert full.status_code==200
    assert len(full.json()['epochs'])==2
    # Seeing CURRENT camera ownership is not permission to view historic
    # private place names of another county.
    assert client.get('/api/site/frigate/privacy_gate/bindings',headers=other).status_code==403
    assert client.get('/api/site/frigate/privacy_gate/bindings',headers=county).status_code==403
    assert client.get('/api/site/frigate/no_such_camera/bindings',headers=root).status_code==404
