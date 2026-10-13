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


def test_common_analytics_period_and_scoped_flows():
    """One denominator for #4 / #5 / #19: synthetic visits, not frames."""
    from app.insights import overview

    def visit(person, dest, date):
        return {'person':person,'place_id':dest,
                'started_at':date+'T11:00:00+00:00',
                'ended_at':date+'T11:05:00+00:00',
                'observation_ids':[1], 'raw_observations':1}

    # Equal-length period: previous 10-11 Oct; current 12-13 Oct.
    visits=[
        visit('P001',10,'2026-10-11'), visit('P002',20,'2026-10-11'),
        visit('P003',10,'2026-10-10'),
        visit('P001',20,'2026-10-12'),visit('P001',20,'2026-10-13'),
        visit('P002',10,'2026-10-13'),visit('P003',10,'2026-10-12'),
        # A hidden destination; must not enter the selected scope.
        visit('P002',30,'2026-10-13')]
    data={'simulation_time_utc':'2026-10-13T12:00:00+00:00',
          'visits':visits,'alerts':[],'rule_counts':[],
          'registry_status':{'state':'not_published'},
          'affiliation':[{'person':'P001','place_id':10},
                         {'person':'P002','place_id':20},
                         {'person':'P003','place_id':30}]}
    places=[{'id':10,'name':'可见场所A'},{'id':20,'name':'可见场所B'}]
    found=overview(data, places,days=2)
    c=found['comparison'];m=found['movement']
    assert found['cards']['synthetic_visits']==4
    assert c['previous_visits']==3
    assert c['period_start_day_utc']=='2026-10-10'
    assert c['period_end_day_utc']=='2026-10-11'
    assert c['visits_change']==1
    assert c['visits_change_pct']==33.3
    assert sum(t['visits'] for t in found['trend'])==c['current_visits']
    assert sum(p['visits'] for p in found['ranking'])==4
    assert sum(p['previous_visits'] for p in found['ranking'])==3
    assert m['cross_home_inbound']==4 and m['previous_cross_home_inbound']==1
    assert m['scoped_outbound']==3 and m['previous_scoped_outbound']==0
    assert m['flows']==[
        {'from_place_id':10,'to_place_id':20,'synthetic_visits':2,
         'from_place_name':'可见场所A','to_place_name':'可见场所B'},
        {'from_place_id':20,'to_place_id':10,'synthetic_visits':1,
         'from_place_name':'可见场所B','to_place_name':'可见场所A'}]
    assert '30' not in str(m['flows'])
    assert '2026-10-13' not in str(c['period_end_day_utc'])
    # Single-place filter must not reveal names of external home places.
    one=overview(data,places[:1],days=2)
    assert one['cards']['synthetic_visits']==2
    assert one['movement']['cross_home_inbound']==2
    assert one['movement']['scoped_outbound']==0
    assert one['movement']['flows']==[]


def test_analytics_comparison_zero_baseline_not_fabricated_percentage(client):
    seed(client)
    actual=client.get('/api/insights/overview?days=7')
    assert actual.status_code==200,actual.text
    data=actual.json()['synthetic']
    assert data['comparison']['current_visits']==data['cards']['synthetic_visits']
    assert sum(p['cross_home_inbound'] for p in data['ranking'])==data['movement']['cross_home_inbound']
    # API contract prevents invented growth percentage when prior count is zero.
    if data['comparison']['previous_visits']==0:
        assert data['comparison']['visits_change_pct'] is None
    p=client.get('/api/places').json()[0]['id']
    narrowed=client.get(f'/api/insights/overview?days=7&place_id={p}').json()['synthetic']
    assert len(narrowed['ranking'])==1
    assert sum(f['synthetic_visits'] for f in narrowed['movement']['flows']) <= narrowed['cards']['synthetic_visits']


def test_cross_region_alert_does_not_leak_hidden_place_ids_or_observation_ids():
    """A partial venue overlap must never return a full cross-site alert.

    Prior _filtered used set.intersection, leaking the OTHER county's place
    ID, people, and sensitive observation IDs to a user scoped to venue 10.
    """
    from app.insights import _filtered, overview, people_search, person_detail
    stamp='2026-10-13T12:00:00+00:00'
    analysis={
        'simulation_time_utc':stamp,
        'visits':[{'person':'P001','place_id':10,'started_at':stamp,
                   'ended_at':stamp,'observation_ids':[123],'raw_observations':1}],
        'alerts':[
            {'type':'cross_site','place_ids':[10,999],
             'people':['P001','P999'],'observation_ids':[123,999999],
             'key':'forbidden-region-evidence'},
            {'type':'appeared','place_ids':[10],
             'people':['P001'],'observation_ids':[123],
             'key':'visible-evidence'}],
        'rule_counts':[],'registry_status':{'state':'not_published'},
        'affiliation':[{'person':'P001','place_id':999}],
        'config':{'encounter_window_minutes':10},
    }
    view=[{'id':10,'name':'仅可见的学校'}]
    scoped=_filtered(analysis,days=1,place_ids={10})
    assert len(scoped['alerts'])==1
    assert scoped['alerts'][0]['key']=='visible-evidence'
    dashboard=overview(analysis,view,days=1)
    assert dashboard['cards']['rule_hits']==1
    assert len(dashboard['recent_alerts'])==1
    assert '999999' not in str(dashboard)
    assert 'forbidden-region-evidence' not in str(dashboard)
    roster=people_search(analysis,view,days=1)['people']
    assert len(roster)==1 and roster[0]['affiliation_place_id'] is None
    person=person_detail(analysis,view,'P001',days=1)
    assert person['affiliation_place_id'] is None
    assert person['affiliation_place_name'] is None
    assert len(person['alerts'])==1
    assert person['alerts'][0]['key']=='visible-evidence'
    assert 'P999' not in str(person)
