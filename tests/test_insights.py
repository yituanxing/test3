"""Excel requirements 4,5,15,17,18,19: synthetic-only demo analytics."""
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    import app.main as m
    monkeypatch.setattr(m,'DB',tmp_path/'insights.sqlite3')
    m.setup_db()
    with TestClient(m.app) as c:
        yield c


def seed(c):
    r=c.post('/api/business/seed')
    assert r.status_code==200, r.text
    return r.json()


def test_empty_dashboard_is_explicit_and_safe(client):
    response=client.get('/api/insights/overview')
    assert response.status_code==200, response.text
    data=response.json()
    assert data['synthetic']['cards']['synthetic_visits']==0
    assert data['synthetic']['cards']['unique_synthetic_people_in_window']==0
    assert data['synthetic']['disclaimer']
    assert data['real_video']['counts']['person_objects']==0
    assert client.get('/insights').status_code==200
    assert b'/api/insights/overview' in client.get('/insights').content
    assert client.get('/api/insights/people/P001').status_code==404


def test_seed_search_and_timeline_have_original_evidence(client):
    seed(client)
    result=client.get('/api/insights/overview?days=7').json()['synthetic']
    assert result['mode']=='synthetic_only'
    assert result['cards']['synthetic_visits']>0
    assert result['cards']['unique_synthetic_people_in_window']>=4
    assert sum(v['visits'] for v in result['trend'])==result['cards']['synthetic_visits']
    assert sum(v['visits'] for v in result['ranking'])==result['cards']['synthetic_visits']
    p=client.get('/api/insights/people?q=P00&days=7').json()
    assert p['count']>=4
    detail=client.get('/api/insights/people/P001?days=7').json()
    assert detail['mode']=='synthetic_only'
    assert detail['total_visits']>=3
    assert all(v['observation_ids'] for v in detail['visits'])
    assert all(v['place_name'] for v in detail['visits'])
    assert detail['disclaimer']
    assert not any('人脸特征' in str(v) for v in detail['visits'])


def test_day_range_and_place_filter_do_not_leak_global_counts(client):
    seed(client)
    a=client.get('/api/insights/overview?days=1').json()['synthetic']
    b=client.get('/api/insights/overview?days=7').json()['synthetic']
    assert a['cards']['synthetic_visits']<=b['cards']['synthetic_visits']
    a_places=client.get('/api/places').json()
    pid=a_places[0]['id']
    scoped=client.get(f'/api/insights/overview?place_id={pid}').json()
    assert len(scoped['synthetic']['ranking'])==1
    assert scoped['synthetic']['cards']['synthetic_visits']==scoped['synthetic']['ranking'][0]['visits']
    assert sum(v['visits'] for v in scoped['synthetic']['trend'])==scoped['synthetic']['cards']['synthetic_visits']
    assert len(scoped['real_video']['places'])==1
    assert scoped['real_video']['timeline']==[]
    assert client.get('/api/insights/overview?place_id=123456').status_code==404
    assert client.get('/api/insights/people?place_id=123456').status_code==404
    assert client.get('/api/insights/relationships?place_id=123456').status_code==404


def test_person_query_rejects_real_names_or_unbounded_parameters(client):
    seed(client)
    assert client.get('/api/insights/people/张三').status_code==422
    assert client.get('/api/insights/people/P001?days=400').status_code==422
    assert client.get('/api/insights/people?days=0').status_code==422
    assert client.get('/api/insights/overview?days=100').status_code==422
    assert client.get('/api/insights/people?q=nomatch').json()['count']==0
    # Older synthetic observations are excluded from a one-day window.
    assert client.get('/api/insights/people/P006?days=1').status_code==404
    assert client.get('/api/insights/people/P006?days=7').status_code==200


def test_copresence_windows_and_person_specific_filter(client):
    seed(client)
    graph=client.get('/api/insights/relationships?days=7').json()
    assert graph['mode']=='synthetic_only'
    assert graph['edges']
    assert all(e['windows']>=1 and len(e['place_ids'])>=1 for e in graph['edges'])
    selected=client.get('/api/insights/relationships?days=7&person=P001').json()['edges']
    assert selected
    assert all('P001' in (e['person_a'],e['person_b']) for e in selected)
    assert len(selected)<=len(graph['edges'])


def test_review_updates_insights_and_never_changes_real_video_stats(client):
    seed(client)
    first=client.get('/api/insights/overview').json()
    first_visits=first['synthetic']['cards']['synthetic_visits']
    first_real=first['real_video']['counts']
    records=client.get('/api/business/observations').json()
    # Review P001's third observation; counts need not always decrease
    # because one session might have multiple supporting frames.
    obs=next(x for x in records if x['person_label']=='P001')
    r=client.post(f'/api/business/observations/{obs["id"]}/review',json={
        'excluded':True,'reason':'核验误记录'})
    assert r.status_code==200, r.text
    after=client.get('/api/insights/overview').json()
    assert after['synthetic']['cards']['synthetic_visits']<=first_visits
    assert after['real_video']['counts']==first_real


def test_published_roster_affiliation_is_shown(client):
    seed(client)
    r=client.post('/api/base/generate',json={'days':7,'strategy':'earliest','correction_mode':'rebuild'})
    assert r.status_code==200
    rid=r.json()['run']['id']
    assert client.post(f'/api/base/runs/{rid}/publish').status_code==200
    summary=client.get('/api/insights/overview').json()['synthetic']['cards']
    assert summary['published_roster_state']=='published'
    detail=client.get('/api/insights/people/P001').json()
    assert detail['affiliation_place_id']>0


def test_synthetic_person_results_are_separate_from_real_frigate_observation(client):
    seed(client)
    import app.main as m
    orig=client.get('/api/insights/people').json()
    with m.db() as c:
        c.execute('INSERT INTO frigate_camera_bindings(camera,place_id) VALUES(?,?)',('cam_demo',1))
        c.execute("""INSERT INTO frigate_object_events
                (event_id,camera,place_id,label,started_at,ended_at,score,has_snapshot,updated_at)
                VALUES('event-real-0001','cam_demo',1,'person',?,?,?,?,?)""",
                (m.utcnow(),m.utcnow(),0.89,1,m.utcnow()))
    new=client.get('/api/insights/overview').json()
    assert new['real_video']['counts']['person_objects']==1
    assert client.get('/api/insights/people').json()==orig
    assert all('event-real-0001' not in str(v) for v in new['synthetic']['recent_alerts'])


def test_unknown_region_returns_404(client):
    seed(client)
    assert client.get('/api/insights/overview?region_id=98765').status_code==404
    assert client.get('/api/insights/people?region_id=98765').status_code==404
    assert client.get('/api/insights/people/P001?region_id=98765').status_code==404
