"""Excel requirements 3 and 21: all tests use fabricated people and places."""
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def context(tmp_path,monkeypatch):
    import app.main as main
    monkeypatch.setattr(main, 'DB', tmp_path/'registry.sqlite3')
    main.setup_db()
    client=TestClient(main.app)
    assert client.post('/api/business/seed').status_code==200
    return client


def generate(client, **kwargs):
    d={'days':7,'strategy':'earliest','correction_mode':'rebuild',**kwargs}
    r=client.post('/api/base/generate',json=d)
    assert r.status_code==200, r.text
    return r.json()


def assignment(run):
    return {p['person']:p for p in run['people']}


def test_generate_earliest_and_raw_frequency_not_session_count(context):
    earliest=generate(context)
    m=assignment(earliest)
    a=next(p for p in m['P001']['candidates'] if p['place_id']==m['P001']['place_id'])
    assert a['appearances']==3 and a['visits']==2
    assert len(earliest['conflicts'])>=2
    assert earliest['unresolved_count']==0
    freq=generate(context,strategy='frequency')
    assert assignment(freq)['P002']['place_id']!=m['P002']['place_id']
    assert freq['candidate_count']>=6


def test_manual_requires_review_then_publishes(context):
    r=generate(context,strategy='manual')
    assert r['unresolved_count']>0
    id=r['run']['id']
    refuse=context.post(f'/api/base/runs/{id}/publish')
    assert refuse.status_code==409
    for item in r['people']:
        if item['unresolved']:
            picked=item['candidates'][0]['place_id']
            resp=context.post(f'/api/base/runs/{id}/decide',json={
                'person':item['person'],'place_id':picked,'action':'assign','reason':'模拟人工研判'})
            assert resp.status_code==200,resp.text
    pub=context.post(f'/api/base/runs/{id}/publish')
    assert pub.status_code==200
    assert pub.json()['unresolved_count']==0
    assert context.get('/api/business/analysis').json()['registry_status']['state']=='published'


def test_publish_changes_rule_engine_and_stales_reviews(context):
    initial=context.get('/api/business/analysis').json()
    home={p['person']:p['place_id'] for p in initial['affiliation']}
    hit=initial['alerts'][0]
    assert context.post('/api/business/alerts/'+hit['key']+'/review',json={
        'decision':'confirmed','reason':'演示复核'}) .status_code==200
    r=generate(context,strategy='earliest');pid=r['run']['id']
    dest=next(p['id'] for p in context.get('/api/places').json() if p['id']!=home['P002'])
    reviewed=context.post(f'/api/base/runs/{pid}/decide',json={
        'person':'P002','place_id':dest,'action':'assign','reason':'演示数据手工迁移'})
    assert reviewed.status_code==200
    assert context.post(f'/api/base/runs/{pid}/publish').status_code==200
    new=context.get('/api/business/analysis').json()
    assert {p['person']:p['place_id'] for p in new['affiliation']}['P002']==dest
    assert new['registry_status']['state']=='published'
    if hit['key'] in {a['key'] for a in new['alerts']}:
        assert next(a for a in new['alerts'] if a['key']==hit['key'])['review_stale']


def test_reconcile_preserves_valid_manual_assignment_then_rebuild_resets(context):
    old=generate(context)
    person='P001';opt=old['conflicts'][0]
    person=opt['person'];candidate=opt['candidates'][-1]['place_id']
    rid=old['run']['id']
    assert context.post(f'/api/base/runs/{rid}/decide',json={
        'person':person,'place_id':candidate,'action':'assign','reason':'演示人工优先归属'}).status_code==200
    assert context.post(f'/api/base/runs/{rid}/publish').status_code==200
    new=generate(context,correction_mode='reconcile')
    assert assignment(new)[person]['place_id']==candidate
    assert assignment(new)[person]['basis']=='carried_manual'
    assert context.post(f'/api/base/runs/{new["run"]["id"]}/publish').status_code==200
    pure=generate(context,correction_mode='rebuild')
    assert assignment(pure)[person]['basis']!='carried_manual'


def test_observation_review_invalidates_published_roster(context):
    r=generate(context);id=r['run']['id']
    assert context.post(f'/api/base/runs/{id}/publish').status_code==200
    obs=context.get('/api/business/observations').json()[0]
    assert context.post(f'/api/business/observations/{obs["id"]}/review',json={
        'excluded':True,'reason':'移除演示误记录'}).status_code==200
    active=context.get('/api/base/active').json()
    assert active['stale']
    assert context.get('/api/business/analysis').json()['registry_status']['state']=='stale'
    draft=generate(context)
    assert not draft['stale']
    assert context.post(f'/api/base/runs/{draft["run"]["id"]}/publish').status_code==200
    assert context.get('/api/base/active').json()['stale'] is False


def test_remove_and_manual_add_are_audited_and_rules_follow(context):
    r=generate(context);rid=r['run']['id']
    places=context.get('/api/places').json()
    assert context.post(f'/api/base/runs/{rid}/decide',json={
        'person':'P001','place_id':None,'action':'remove','reason':'模拟人员移出底库'}).status_code==200
    assert context.post(f'/api/base/runs/{rid}/decide',json={
        'person':'P099','place_id':places[0]['id'],'action':'assign','reason':'模拟人工新增底库人员'}).status_code==200
    assert context.post(f'/api/base/runs/{rid}/publish').status_code==200
    a=context.get('/api/business/analysis').json()
    assert 'P001' not in {x['person'] for x in a['affiliation']}
    assert 'P099' in {x['person'] for x in a['affiliation']}
    audit=context.get('/api/base/audit').json()
    assert {'manual_assign','manual_remove','publish','generate'}.issubset({x['operation'] for x in audit})
    assert context.get('/api/business/observations').json()[0]['origin']=='synthetic_only'


def test_no_synthetic_input_and_inactive_place_are_rejected(context):
    assert context.post('/api/business/seed?reset=true').status_code==200
    r=generate(context)
    rid=r['run']['id']
    bad=context.post(f'/api/base/runs/{rid}/decide',json={
        'person':'P007','place_id':777777,'action':'assign','reason':'测试不存在的场所'})
    assert bad.status_code==404
    assert context.post(f'/api/base/runs/{rid}/decide',json={
        'person':'INVALID','place_id':r['people'][0]['place_id'],'action':'assign','reason':'测试校验'}).status_code==422



def test_version_diff_is_evidence_backed_and_survives_publish(context):
    old=generate(context,strategy='earliest')
    oldid=old['run']['id']
    assert context.post(f'/api/base/runs/{oldid}/publish').status_code==200
    new=generate(context,strategy='earliest')
    newid=new['run']['id']
    assert new['run']['parent_run_id']==oldid
    original=assignment(old)['P001']['place_id']
    other=next(p['id'] for p in context.get('/api/places').json() if p['id']!=original)
    assert context.post(f'/api/base/runs/{newid}/decide',json={
        'person':'P001','place_id':other,'action':'assign','reason':'人工改变虚构归属'}).status_code==200
    assert context.post(f'/api/base/runs/{newid}/decide',json={
        'person':'P002','place_id':None,'action':'remove','reason':'删除虚构底库主体'}).status_code==200
    comparison=context.get(f'/api/base/runs/{newid}/diff').json()
    assert comparison['from_run_id']==oldid
    assert comparison['counts']['moved']==1
    assert comparison['counts']['removed']==1
    delta={x['person']:x for x in comparison['changes']}
    assert delta['P001']['before_place_id']==original
    assert delta['P001']['after_place_id']==other
    assert delta['P001']['after_observation_ids']
    assert delta['P002']['kind']=='removed'
    assert context.post(f'/api/base/runs/{newid}/publish').status_code==200
    after=context.get(f'/api/base/runs/{newid}/diff').json()
    assert after['counts']==comparison['counts']
    assert context.get('/api/base/active').json()['run']['id']==newid


def test_newer_observations_do_not_invalidate_completed_historical_window(context):
    old=generate(context)
    rid=old['run']['id']
    assert context.post(f'/api/base/runs/{rid}/publish').status_code==200
    from datetime import datetime,timedelta
    import app.main as main
    with main.db() as conn:
        observed_at=(datetime.fromisoformat(old['run']['window_end'])+timedelta(days=1)).isoformat()
        conn.execute("""INSERT INTO synthetic_observations
           (event_key,person_label,place_id,observed_at,origin)
           VALUES(?,?,?,?, 'synthetic_only')""",('NEW-WINDOW','P099',old['people'][0]['place_id'],observed_at))
    assert context.get('/api/base/active').json()['stale'] is False
    fresh=generate(context)
    assert assignment(fresh)['P099']['place_id'] is not None
    diff=context.get(f'/api/base/runs/{fresh["run"]["id"]}/diff').json()
    assert diff['counts']['added']==1
