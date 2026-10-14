"""Geographic UI and isolated synthetic event geography; no GPS claims."""
from fastapi.testclient import TestClient
import pytest
from app import access
import app.main as m


@pytest.fixture
def case(monkeypatch,tmp_path):
    monkeypatch.delenv('ZD_AUTH_TEST_COMPAT',raising=False)
    monkeypatch.setattr(m,'DB',tmp_path/'geographic.sqlite3')
    m.setup_db()
    with m.db() as c:
        state=c.execute("SELECT id FROM regions WHERE level=1 LIMIT 1").fetchone()[0]
        county=c.execute("SELECT id FROM regions WHERE level=2 LIMIT 1").fetchone()[0]
        leaf=c.execute("SELECT id FROM regions WHERE level=4 LIMIT 1").fetchone()[0]
        other_county=c.execute("INSERT INTO regions(name,parent_id,level) VALUES('异县',?,2)",
                               (state,)).lastrowid
        other_street=c.execute("INSERT INTO regions(name,parent_id,level) VALUES('异街道',?,3)",
                               (other_county,)).lastrowid
        other_leaf=c.execute("INSERT INTO regions(name,parent_id,level) VALUES('异社区',?,4)",
                               (other_street,)).lastrowid
        outsider=c.execute("INSERT INTO places(name,region_id) VALUES('辖区外场所',?)",
                          (other_leaf,)).lastrowid
        for username,region in [('groot',state),('gcounty',county),('gother',other_leaf)]:
            access.create_user(c,username,'strong-test-password-2026',region)
    client=TestClient(m.app)
    def auth(user):
        r=client.post('/api/auth/login',json={'username':user,
                        'password':'strong-test-password-2026'})
        assert r.status_code==200,r.text
        return {'Authorization':'Bearer '+r.json()['access_token']}
    return client,auth,outsider,other_leaf


def test_initial_empty_and_map_page(case):
    c,auth,_,_=case
    h=auth('groot')
    r=c.get('/api/geo/places',headers=h)
    assert r.status_code==200
    assert r.json()['positioned']==0
    assert all(p['geo'] is None for p in r.json()['places'])
    page=c.get('/geo')
    assert page.status_code==200
    assert 'maplibre' in page.text.lower() and '虚构身份' in page.text
    assert '/api/geo/track' in page.text and '离线坐标示意图' in page.text
    assert c.get('/api/geo/places').status_code==401
    assert c.get('/api/geo/track?person=P001').status_code==401


def test_seed_idempotent_and_persistent_human_override(case):
    c,auth,_,_=case
    h=auth('groot')
    first=c.post('/api/geo/demo/seed',headers=h)
    assert first.status_code==200,first.text
    assert first.json()['inserted']>=3
    assert c.post('/api/geo/demo/seed',headers=h).json()['inserted']==0
    all_places=c.get('/api/geo/places',headers=h).json()['places']
    assert all_places[0]['geo']['provenance']=='fictional_seed'
    pid=all_places[0]['id']
    setval={'lat':41.12345,'lon':88.54321,'note':'人工演示坐标，不是现场实测'}
    r=c.put(f'/api/geo/places/{pid}',headers=h,json=setval)
    assert r.status_code==200,r.text
    assert r.json()['provenance']=='operator_entered_unverified'
    assert c.post('/api/geo/demo/seed',headers=h).json()['inserted']==0
    now=next(p for p in c.get('/api/geo/places',headers=h).json()['places']
             if p['id']==pid)
    assert now['geo']['lat']==setval['lat'] and now['geo']['lon']==setval['lon']
    assert now['geo']['note']==setval['note']
    for invalid in ({'lat':91,'lon':88,'note':'太远'},
                    {'lat':41,'lon':181,'note':'太远'},
                    {'lat':41,'lon':float('inf'),'note':'非法'}):
        assert c.put(f'/api/geo/places/{pid}',headers=h,json=invalid).status_code==422
    with m.db() as d:
        assert d.execute("SELECT COUNT(*) FROM demo_geo_audit").fetchone()[0]>=3


def test_synthetic_track_only_and_scope_isolates_geo_points(case):
    c,auth,outsider,other_leaf=case
    h=auth('groot')
    assert c.post('/api/business/seed',headers=h).status_code==200
    assert c.post('/api/geo/demo/seed',headers=h).status_code==200
    track=c.get('/api/geo/track?person=P001&days=30',headers=h)
    assert track.status_code==200,track.text
    result=track.json()
    assert result['mode']=='synthetic_only'
    assert result['events']
    assert all(x['person']=='P001' for x in result['events'])
    assert result['events']==sorted(result['events'],
                                   key=lambda x:(x['started_at'],x['place_id']))
    assert all(x['synthetic_observation_ids'] for x in result['events'])
    assert 'GPS' in result['disclaimer']
    assert result['visualization']=='chronological_venue_points_not_real_path'
    assert c.get('/api/geo/track?person=张三',headers=h).status_code==422
    assert c.get('/api/geo/track?person=P001&days=366',headers=h).status_code==422

    county=auth('gcounty')
    scoped=c.get('/api/geo/places',headers=county).json()['places']
    assert outsider not in {p['id'] for p in scoped}
    assert c.get(f'/api/geo/places?region_id={other_leaf}',headers=county).status_code==403
    assert c.get(f'/api/geo/track?person=P001&region_id={other_leaf}',
                 headers=county).status_code==403
    subset=c.get('/api/geo/track?person=P001',headers=county).json()
    assert outsider not in {x['place_id'] for x in subset['events']}
    assert c.post('/api/geo/demo/seed',headers=county).status_code==403
    assert c.put('/api/geo/places/1',headers=county,json={
        'lat':40,'lon':80,'note':'非法越权'}).status_code==403
    # The isolated other-county user sees ONLY its own location.
    other=auth('gother')
    r=c.get('/api/geo/places',headers=other)
    assert {x['id'] for x in r.json()['places']}=={outsider}
    assert c.get('/api/geo/track?person=P001',headers=other).json()['events']==[]


def test_missing_coordinates_cannot_fabricate_route(case):
    c,auth,_,_=case
    h=auth('groot')
    assert c.post('/api/business/seed',headers=h).status_code==200
    result=c.get('/api/geo/track?person=P001',headers=h).json()
    assert result['events']==[]
    assert result['visits_without_coordinates']>0
    assert result['total_visible_visits']==result['visits_without_coordinates']
