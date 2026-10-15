"""Excel #3, #8, #10, #12 and #22: deterministic synthetic rule boundaries."""
from datetime import datetime, timedelta, timezone
from app.business import analyze, DEFAULT_CONFIG

BASE=datetime(2026,10,10,8,tzinfo=timezone.utc)
def e(n,p,loc,minute):
    return {'id':n,'person_label':p,'place_id':loc,
            'observed_at':(BASE+timedelta(minutes=minute)).isoformat()}
def cfg(**override):
    return {**DEFAULT_CONFIG,'absence_days':365,'headcount_difference':100,**override}
def hits(report,kind):
    return [x for x in report['alerts'] if x['type']==kind]

def test_frequency_counts_observations_not_visits():
    rows=[e(1,'P001',1,0),e(2,'P001',1,1),e(3,'P001',1,2),
          e(4,'P001',1,3),e(5,'P001',2,60),e(6,'P001',2,130)]
    report=analyze(rows,cfg(affiliation_mode='frequency'),now=BASE+timedelta(hours=3))
    assert report['visit_count']==3
    assert report['affiliation'][0]['place_id']==1

def test_interaction_requires_reciprocal_configured_rounds():
    base=[e(1,'P001',1,0),e(2,'P002',1,1),e(3,'P001',2,60),e(4,'P002',2,61)]
    home={'P001':1,'P002':2}
    config=cfg(interaction_min_rounds=2)
    assert not hits(analyze(base,config,now=BASE+timedelta(hours=5),affiliation_overrides=home),'interaction')
    at_a=base+[e(5,'P001',1,120),e(6,'P002',1,121)]
    assert not hits(analyze(at_a,config,now=BASE+timedelta(hours=5),affiliation_overrides=home),'interaction')
    full=at_a+[e(7,'P001',2,180),e(8,'P002',2,181)]
    assert len(hits(analyze(full,config,now=BASE+timedelta(hours=5),affiliation_overrides=home),'interaction'))==1

def test_headcount_waits_for_activity_closing_time():
    rows=[e(1,'P001',1,120)]
    home={'P001':1,'P002':1,'P003':1}
    config=cfg(activity_start_hour=9,activity_end_hour=17,headcount_difference=2)
    assert not hits(analyze(rows,config,now=BASE+timedelta(hours=8),affiliation_overrides=home),'headcount')
    assert len(hits(analyze(rows,config,now=BASE+timedelta(hours=10),affiliation_overrides=home),'headcount'))==1

def test_absence_can_count_completed_check_intervals():
    config=cfg(absence_mode='missed_checks',absence_check_interval_days=2,absence_missed_checks=3)
    first=[e(1,'P001',1,0)]
    assert hits(analyze(first,config,now=BASE+timedelta(days=6,hours=1)),'absence')
    assert not hits(analyze(first,config,now=BASE+timedelta(days=5,hours=23)),'absence')

def test_pause_rule_without_deleting_evidence():
    rows=[e(1,'P001',1,0),e(2,'P001',2,60)]
    paused=analyze(rows,cfg(enabled_rules=[]),now=BASE+timedelta(hours=3))
    active=analyze(rows,cfg(),now=BASE+timedelta(hours=3))
    assert not paused['alerts']
    assert paused['effective_observations']==active['effective_observations']==2
    assert hits(active,'cross_place')

def test_api_rejects_invalid_config(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    import app.main as main
    monkeypatch.setattr(main,'DB',tmp_path/'rules-v11.sqlite3')
    main.setup_db()
    client=TestClient(main.app)
    config=client.get('/api/business/config').json()
    assert client.put('/api/business/config',json={**config,'activity_start_hour':17,'activity_end_hour':17}).status_code==422
    assert client.put('/api/business/config',json={**config,'enabled_rules':['appearance','appearance']}).status_code==422
    assert client.put('/api/business/config',json={**config,'interaction_min_rounds':0}).status_code==422
    assert client.get('/api/business/config').json()==config


def test_three_person_repeated_stable_cohort_not_union_of_pairs():
    """Excel #7: N-person repeated co-presence uses one same fictional group."""
    home={'P001':1,'P002':1,'P003':1}
    rows=[e(1,'P001',2,60),e(2,'P002',2,61),e(3,'P003',2,62),
          e(4,'P001',2,160),e(5,'P002',2,161),e(6,'P003',2,162)]
    options=cfg(companion_min_people=3,companion_min_episodes=2)
    report=analyze(rows,options,now=BASE+timedelta(hours=5),
                   affiliation_overrides=home)
    matches=hits(report,'companion')
    assert len(matches)==1
    assert matches[0]['people']==['P001','P002','P003']
    assert len(matches[0]['observation_ids'])==6
    # Removing a group member from the later time window MUST defeat N=3,
    # even though there are several pairwise coincidences.
    incomplete=[x for x in rows if x['id']!=6]
    assert not hits(analyze(incomplete,options,now=BASE+timedelta(hours=5),
                            affiliation_overrides=home),'companion')
    assert not hits(analyze(rows,{**options,'companion_min_people':4},
                           now=BASE+timedelta(hours=5),
                           affiliation_overrides=home),'companion')
    # Compatibility: default N=2 still emits repeated pairwise candidates.
    assert len(hits(analyze(rows,cfg(companion_min_episodes=2),
                            now=BASE+timedelta(hours=5),
                            affiliation_overrides=home),'companion'))==3


def test_multi_place_and_synthetic_code_watchlist_with_legacy_scalar():
    """Excel #11: one shared rule evaluates target intersections."""
    rows=[e(1,'P001',1,0),e(2,'P002',2,30),
          e(3,'P001',2,60),e(4,'P001',3,90)]
    options=cfg(watch_place_ids=[1,2],watch_person_ids=['P001'],
                watch_place_id=3)
    report=analyze(rows,options,now=BASE+timedelta(hours=4))
    matches=hits(report,'appearance')
    assert len(matches)==2
    assert {tuple(x['place_ids']) for x in matches}=={(1,),(2,)}
    assert all(x['people']==['P001'] for x in matches)
    scalar=analyze(rows,cfg(watch_place_id=3),now=BASE+timedelta(hours=4))
    assert len(hits(scalar,'appearance'))==1
    assert hits(scalar,'appearance')[0]['place_ids']==[3]


def test_multi_target_config_validation_and_recompute(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    import app.main as main
    monkeypatch.setattr(main,'DB',tmp_path/'multiple-rule-targets.sqlite3')
    main.setup_db()
    client=TestClient(main.app)
    assert client.post('/api/business/seed').status_code==200
    cfg0=client.get('/api/business/config').json()
    places=client.get('/api/places').json()
    place_ids=[p['id'] for p in places[:2]]
    options={**cfg0,'watch_place_ids':place_ids,'watch_person_ids':['P001'],
             'companion_min_people':3}
    save=client.put('/api/business/config',json=options)
    assert save.status_code==200,save.text
    assert save.json()['recomputed'] is True
    assert client.get('/api/business/config').json()['watch_place_ids']==place_ids
    assert all(x['people']==['P001'] for x in
               client.get('/api/business/analysis').json()['alerts']
               if x['type']=='appearance')
    cases=[
        ({'companion_min_people':1},422),
        ({'watch_place_ids':[0]},422),
        ({'watch_place_ids':[999999]},404),
        ({'watch_place_ids':[place_ids[0],place_ids[0]]},422),
        ({'watch_person_ids':['张三']},422),
        ({'watch_person_ids':['P001','P001']},422),
    ]
    for diff,expected in cases:
        res=client.put('/api/business/config',json={**options,**diff})
        assert res.status_code==expected, (diff,res.text)
    assert client.get('/api/business/config').json()==options
