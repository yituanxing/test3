"""Excel #22: audited synthetic rule instances share one existing evaluator.

SQLite and FastAPI real requests, no fake camera or real-person identities.
"""
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def case(monkeypatch,tmp_path):
    import app.main as main
    from app import access
    monkeypatch.delenv('ZD_AUTH_TEST_COMPAT',raising=False)
    monkeypatch.setattr(main,'DB',tmp_path/'instances.sqlite3')
    main.setup_db()
    with main.db() as db:
        root=db.execute('SELECT id FROM regions WHERE level=1 LIMIT 1').fetchone()[0]
        county=db.execute('SELECT id FROM regions WHERE level=2 LIMIT 1').fetchone()[0]
        access.create_user(db,'staterule','strong-state-rule-password',root)
        access.create_user(db,'countyrule','strong-county-rule-password',county)
    client=TestClient(main.app)
    def token(user):
        reply=client.post('/api/auth/login',json={
            'username':user,'password':('strong-state-rule-password'
            if user=='staterule' else 'strong-county-rule-password')})
        assert reply.status_code==200,reply.text
        return {'Authorization':'Bearer '+reply.json()['access_token']}
    return main,client,token('staterule'),token('countyrule')


def test_multi_instance_crud_review_staleness_and_legacy_recovery(case):
    main,client,state,county=case
    assert client.post('/api/business/seed',headers=state).status_code==200
    places=client.get('/api/places',headers=state).json()
    pid=next(x['id'] for x in places if x['name']=='测试场所 B')
    url='/api/business/rule-instances'
    assert client.get(url).status_code==401
    assert client.get(url,headers=county).status_code==403
    before=client.get('/api/business/analysis',headers=state).json()
    legacy=[x for x in before['alerts'] if x['type']=='appearance']
    assert legacy and all('rule_instance_id' not in x for x in legacy)
    instance={'type':'appearance','name':'合成P001出现事件','enabled':True,
        'parameters':{},'target_place_ids':[pid],
        'target_synthetic_people':['P001']}
    denied=client.post(url,headers=county,json=instance)
    assert denied.status_code==403
    create=client.post(url,headers=state,json=instance)
    assert create.status_code==201,create.text
    rid=create.json()['id']
    assert create.json()['mode']=='synthetic_only'
    assert create.json()['version']==1
    after=client.get('/api/business/analysis',headers=state).json()
    hits=[x for x in after['alerts'] if x['type']=='appearance']
    assert hits and all(x.get('rule_instance_id')==rid and x['people']==['P001'] for x in hits)
    assert not set(x['key'] for x in legacy) & set(x['key'] for x in hits)
    key=hits[0]['key']
    # Review persists, but editing its instance invalidates the old verdict.
    check=client.post(f'/api/business/alerts/{key}/review',headers=state,json={
        'decision':'confirmed','reason':'有明确模拟观测事件ID'})
    assert check.status_code==200,check.text
    reviewed=client.get('/api/business/analysis',headers=state).json()
    assert next(x for x in reviewed['alerts'] if x['key']==key)['status']=='confirmed'
    change=client.put(f'{url}/{rid}',headers=state,json={
        **instance,'name':'更新场所出现规则'})
    assert change.status_code==200,change.text
    assert change.json()['version']==2
    stale=next(x for x in client.get('/api/business/analysis',headers=state).json()['alerts']
               if x['key']==key)
    assert stale['review_stale'] is True and stale['status']=='needs_review'

    # A second instance of the SAME kind uses independent evidence/review IDs.
    second=client.post(url,headers=state,json={
        **instance,'name':'另一份独立规则'})
    assert second.status_code==201,second.text
    b=second.json()['id']
    double=[x for x in client.get('/api/business/analysis',headers=state).json()['alerts']
            if x['type']=='appearance']
    assert {x['rule_instance_id'] for x in double}=={rid,b}
    assert len({x['key'] for x in double})==len(double)

    disabled=client.put(f'{url}/{rid}',headers=state,json={
        **instance,'enabled':False})
    assert disabled.status_code==200
    one=[x for x in client.get('/api/business/analysis',headers=state).json()['alerts']
         if x['type']=='appearance']
    assert one and all(x['rule_instance_id']==b for x in one)

    assert client.delete(f'{url}/{b}',headers=state).status_code==200
    assert not [x for x in client.get('/api/business/analysis',headers=state).json()['alerts']
                if x['type']=='appearance']
    assert client.delete(f'{url}/{rid}',headers=state).status_code==200
    restored=[x for x in client.get('/api/business/analysis',headers=state).json()['alerts']
              if x['type']=='appearance']
    assert {x['key'] for x in restored}=={x['key'] for x in legacy}
    assert all('rule_instance_id' not in x for x in restored)

    history=client.get(url+'?include_archived=true',headers=state).json()['instances']
    assert len(history)==2 and all(x['archived'] for x in history)
    assert client.put(f'{url}/{rid}',headers=state,json=instance).status_code==409
    audit=client.get(url+'/audit',headers=state).json()
    assert any(x['operation']=='create' for x in audit)
    assert any(x['operation']=='update' for x in audit)
    assert sum(x['operation']=='archive' for x in audit)==2
    # Never mix this bookkeeping into actual video statistics.
    assert client.get('/api/monitoring/overview',headers=state).json()['counts']['person_objects']==0


def test_rule_instances_reject_invalid_and_preserve_original_settings(case):
    main,client,state,county=case
    assert client.post('/api/business/seed',headers=state).status_code==200
    url='/api/business/rule-instances'
    old=client.get('/api/business/config',headers=state).json()
    original=client.get('/api/business/analysis',headers=state).json()
    place=client.get('/api/places',headers=state).json()[0]['id']
    candidate={'type':'companion','name':'演示同行规则','enabled':True,
               'parameters':{'companion_min_people':3,'companion_min_episodes':2},
               'target_place_ids':[place],'target_synthetic_people':['P001']}
    good=client.post(url,headers=state,json=candidate)
    assert good.status_code==201,good.text
    for patch,status in [
        ({'parameters':{'headcount_difference':3}},422),
        ({'parameters':{'companion_min_people':1}},422),
        ({'parameters':{'companion_min_people':True}},422),
        ({'target_place_ids':[place,place]},422),
        ({'target_place_ids':[999999]},404),
        ({'target_synthetic_people':['张三']},422),
        ({'target_synthetic_people':['P001','P001']},422),
        ({'type':'appearance','target_place_ids':[]},422),
        ({'name':' '},422)
    ]:
        req={**candidate,**patch}
        got=client.post(url,headers=state,json=req)
        assert got.status_code==status,(patch,got.text)
    assert client.get('/api/business/config',headers=state).json()==old
    assert client.get(url,headers=county).status_code==403
    now=client.get('/api/business/analysis',headers=state).json()
    assert now['visit_count']==original['visit_count']
    assert now['person_count']==original['person_count']
    assert now['mode']=='synthetic_only'
