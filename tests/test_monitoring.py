"""Independent real-event aggregates and correctness of reviewed evidence."""
from datetime import datetime, timezone
from fastapi.testclient import TestClient
import pytest

@pytest.fixture
def client(tmp_path, monkeypatch):
    import app.main as m
    monkeypatch.setattr(m,'DB',tmp_path/'monitor.sqlite3')
    m.setup_db()
    return TestClient(m.app),m


def test_monitor_empty_then_real_sample_independent_of_seed(client):
    c,_=client
    before=c.get('/api/monitoring/overview?days=7').json()
    assert before['counts']=={'person_objects':0,'sampled_frames':0,'face_detections':0}
    assert before['provenance']['synthetic_business_isolated']
    assert len(before['timeline'])==7
    c.post('/api/business/seed')
    seed=c.get('/api/monitoring/overview').json()
    assert seed['counts']==before['counts'],'business simulator must not inflate live counts'
    source=next(s for s in c.get('/api/sources').json() if s['kind']=='file')
    run=c.post(f"/api/sources/{source['id']}/process?max_frames=80&sample_every=10")
    assert run.status_code==200,run.text
    assert run.json()['frames_decoded']==80
    assert run.json()['frames_analyzed']==8
    after=c.get('/api/monitoring/overview').json()
    assert after['counts']['sampled_frames']==8
    assert after['counts']['face_detections']==run.json()['face_detections']
    assert after['counts']['person_objects']==0
    assert sum(v['sampled_frames'] for v in after['timeline'])==8
    assert '全链路运行台' in c.get('/monitor').text


def test_imported_upstream_events_have_separate_counts(client):
    c,m=client
    place=c.get('/api/places').json()[0]['id']
    c.post('/api/frigate/cameras',json={'camera':'demo','place_id':place})
    now=datetime.now(timezone.utc).isoformat(timespec='milliseconds')
    with m.db() as conn:
        conn.execute('''INSERT INTO frigate_object_events
          (event_id,camera,place_id,label,started_at,ended_at,score,has_snapshot,updated_at)
          VALUES(?,?,?,?,?,?,?,?,?)''',('test-1','demo',place,'person',now,None,0.9,0,now))
    info=c.get('/api/monitoring/overview').json()
    assert info['counts']['person_objects']==1
    assert info['places'][0]['cameras']==['demo']
    assert info['places'][0]['person_objects']==1
    assert info['places'][0]['active_objects']==1
    assert c.get('/api/monitoring/overview?days=32').status_code==422


def test_review_becomes_stale_when_config_changes(client):
    c,_=client
    c.post('/api/business/seed')
    found=c.get('/api/business/analysis').json()['alerts']
    analyzed=c.get('/api/business/analysis').json()
    last=max(x['observed_at'] for x in c.get('/api/business/observations').json())
    assert analyzed['simulation_time_utc'] > last[:19]  # simulation clock after all observations
    target=next(a for a in found if a['type']=='cross_place')
    key=target['key']
    r=c.post(f'/api/business/alerts/{key}/review',json={
        'decision':'confirmed','reason':'用于测试的虚构事件复核'})
    assert r.status_code==200
    assert next(a for a in c.get('/api/business/analysis').json()['alerts'] if a['key']==key)['status']=='confirmed'
    config=c.get('/api/business/config').json()
    config['headcount_difference']+=1  # unrelated rule edit: conservative approval invalidation
    assert c.put('/api/business/config',json=config).status_code==200
    changed=next(a for a in c.get('/api/business/analysis').json()['alerts'] if a['key']==key)
    assert changed['review_stale'] is True
    assert changed['status']=='needs_review'
    assert c.post(f'/api/business/alerts/{key}/review',json={
        'decision':'false_positive','reason':'变更后重新核实'}).status_code==200
    revised=next(a for a in c.get('/api/business/analysis').json()['alerts'] if a['key']==key)
    assert revised['review_stale'] is False
    assert revised['status']=='false_positive'
