"""Actual SQLite-backed scheduled generation; no synthetic HTTP mocks."""
from datetime import datetime,timedelta,timezone
from fastapi.testclient import TestClient
from app import base_registry, roster_maintenance


def test_policy_off_on_due_idempotent_and_draft_only(tmp_path,monkeypatch):
    import app.main as main
    monkeypatch.setattr(main,'DB',tmp_path/'scheduled.sqlite3')
    main.setup_db()
    now=datetime(2026,10,12,12,tzinfo=timezone.utc)
    with main.db() as c:
        assert roster_maintenance.configuration(c)['enabled']==0
        assert roster_maintenance.tick(c,now=now)['action']=='disabled'
    client=TestClient(main.app)
    assert client.post('/api/business/seed').status_code==200
    config={'enabled':True,'interval_hours':24,'days':7,'strategy':'earliest',
            'correction_mode':'reconcile'}
    assert client.put('/api/base/maintenance/config',json=config).status_code==200
    with main.db() as c:
        initial=roster_maintenance.tick(c,now=now)
        assert initial['action']=='draft_created'
        rid=initial['run_id']
        assert base_registry._run(c,rid)['state']=='draft'
        assert roster_maintenance.tick(c,now=now+timedelta(hours=1))['action']=='not_due'
        assert roster_maintenance.tick(c,now=now+timedelta(days=1))['action']=='draft_exists'
        assert base_registry._run(c,rid)['state']=='draft'
        base_registry.publish(c,rid)
        assert roster_maintenance.tick(c,now=now+timedelta(days=2))['action']=='unchanged'
        # New fictional observation outside the old snapshot window starts a
        # DRAFT and leaves the previously published version untouched.
        latest=datetime.fromisoformat(base_registry._run(c,rid)['window_end'])
        c.execute("""INSERT INTO synthetic_observations(event_key,person_label,place_id,observed_at,origin)
            VALUES(?,?,?,?, 'synthetic_only')""",
            ('SCHEDULE-NEW','P099',1,(latest+timedelta(days=1)).isoformat()))
        created=roster_maintenance.tick(c,now=now+timedelta(days=3))
        assert created['action']=='draft_created'
        assert created['run_id']!=rid
        assert base_registry._run(c,created['run_id'])['state']=='draft'
        assert base_registry._run(c,rid)['state']=='published'
    assert client.get('/api/base/runs').json()[0]['state']=='draft'


def test_scheduled_preview_never_autopublishes_or_touches_real_events(tmp_path,monkeypatch):
    import app.main as main
    monkeypatch.setattr(main,'DB',tmp_path/'schedule-no-real.sqlite3')
    main.setup_db()
    client=TestClient(main.app)
    cfg={'enabled':True,'interval_hours':1,'days':7,'strategy':'frequency',
         'correction_mode':'rebuild'}
    assert client.put('/api/base/maintenance/config',json=cfg).status_code==200
    assert client.post('/api/base/maintenance/tick').json()['action']=='no_source_observations'
    assert client.post('/api/base/demo/duplicates').json()['inserted']==103
    tick=client.post('/api/base/maintenance/tick').json()
    assert tick['action']=='draft_created'
    assert client.get('/api/base/active').json()['ready']==False
    assert client.get('/api/monitoring/overview').json()['counts']['person_objects']==0
    assert client.get('/api/video/observations').json()['counts']['face_boxes']==0
