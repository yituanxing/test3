"""Executable four-tier region and device lifecycle acceptance checks (all fictitious)."""
import sqlite3
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def case(tmp_path, monkeypatch):
    import app.main as main
    monkeypatch.setattr(main, 'DB', tmp_path/'regions.sqlite3')
    main.setup_db()
    return TestClient(main.app), main


def create_branch(client, suffix):
    parent=None
    output=[]
    for level,name in enumerate((f'测试州{suffix}',f'测试县市{suffix}',f'测试街道{suffix}',f'测试社区{suffix}'),1):
        result=client.post('/api/regions',json={'name':name,'parent_id':parent})
        assert result.status_code==200,result.text
        row=result.json()
        assert row['level']==level
        output.append(row)
        parent=row['id']
    return output


def test_four_level_regions_validate_hierarchy(case):
    c,_=case
    seed=c.get('/api/regions').json()
    assert [r['level'] for r in seed]==[1,2,3,4]
    branch=create_branch(c,'乙')
    assert c.post('/api/regions',json={'name':'非法第五级',
                    'parent_id':branch[-1]['id']}).status_code==422
    assert c.post('/api/regions',json={'name':'测试社区乙',
                    'parent_id':branch[-2]['id']}).status_code==409
    assert c.post('/api/regions',json={'name':'孤儿','parent_id':987654}).status_code==404
    all_regions=c.get('/api/regions').json()
    path=next(r['path'] for r in all_regions if r['id']==branch[-1]['id'])
    assert path=='测试州乙 / 测试县市乙 / 测试街道乙 / 测试社区乙'
    assert c.get('/admin').status_code==200
    assert '辖区、场所与账号管理' in c.get('/admin').text
    assert '/ui/compact-sections.js' in c.get('/admin').text


def test_region_scope_rollup_and_place_move(case):
    c,m=case
    branch=create_branch(c,'丙')
    other=c.post('/api/places',json={'name':'另一社区场所','region_id':branch[-1]['id']})
    assert other.status_code==200,other.text
    new=other.json()['id']
    assert c.post('/api/places',json={'name':'错误归属',
                  'region_id':branch[0]['id']}).status_code==422
    upstream=c.get('/api/places?region_id='+str(branch[0]['id'])).json()
    assert [p['id'] for p in upstream]==[new]
    assert c.get('/api/places?region_id=989898').status_code==404
    assert c.get('/api/monitoring/overview?region_id=989898').status_code==404
    src=c.post('/api/sources',json={'place_id':new,'name':'测试设备','kind':'file',
                                      'uri':str(m.ROOT/'samples/test-pattern.mp4')})
    assert src.status_code==200,src.text
    frames=c.post(f"/api/sources/{src.json()['id']}/process?max_frames=10&sample_every=2")
    assert frames.status_code==200,frames.text
    full=c.get('/api/monitoring/overview').json()
    scoped=c.get('/api/monitoring/overview?region_id='+str(branch[0]['id'])).json()
    assert scoped['counts']['sampled_frames']==5
    assert scoped['counts']['person_objects']==0
    assert scoped['places'][0]['place_id']==new
    assert scoped['scope']['region_id']==branch[0]['id']
    assert full['counts']['sampled_frames']==5
    # Move location to another leaf: existing source and sample IDs remain stable.
    demo_leaf=next(r['id'] for r in c.get('/api/regions').json() if r['name']=='演示社区')
    assert c.put(f'/api/places/{new}',json={'name':'另一社区场所',
                 'region_id':demo_leaf,'active':True}).status_code==200
    scoped_again=c.get('/api/monitoring/overview?region_id='+str(branch[0]['id'])).json()
    assert scoped_again['counts']['sampled_frames']==0
    assert c.get('/api/monitoring/overview').json()['counts']['sampled_frames']==5


def test_deactivate_source_and_place_keep_history(case):
    c,m=case
    place=c.get('/api/places').json()[0]
    src=next(s for s in c.get('/api/sources').json() if s['kind']=='file')
    first=c.post(f"/api/sources/{src['id']}/process?max_frames=8&sample_every=2")
    assert first.status_code==200
    pre=c.get('/api/monitoring/overview').json()['counts']['sampled_frames']
    assert pre==4
    assert c.patch(f"/api/sources/{src['id']}/state",json={'enabled':False}).status_code==200
    assert c.post(f"/api/sources/{src['id']}/process").status_code==409
    assert c.get(f"/api/sources/{src['id']}/probe").status_code==409
    assert c.patch(f"/api/sources/{src['id']}/state",json={'enabled':True}).status_code==200
    assert c.put(f"/api/places/{place['id']}",json={
        'name':place['name'],'region_id':place['region_id'],'active':False}).status_code==200
    assert c.post(f"/api/sources/{src['id']}/process").status_code==409
    assert c.get(f"/api/sources/{src['id']}/probe").status_code==409
    assert c.post('/api/frigate/cameras',json={'camera':'stopped',
                                               'place_id':place['id']}).status_code==409
    assert c.patch(f"/api/sources/{src['id']}/state",json={'enabled':True}).status_code==409
    assert c.get('/api/monitoring/overview').json()['counts']['sampled_frames']==pre
    # Events imported *before* shutdown remain visible.
    assert len(c.get('/api/events').json())==pre


def test_upstream_binding_does_not_import_when_place_deactivated(case,monkeypatch):
    c,m=case
    place=c.get('/api/places').json()[0]
    assert c.post('/api/frigate/cameras',json={'camera':'demo','place_id':place['id']}).status_code==200
    assert c.put(f"/api/places/{place['id']}",json={
        'name':place['name'],'region_id':place['region_id'],'active':False}).status_code==200
    class R:
        def raise_for_status(self):pass
        def json(self):return [{'id':'test-123','camera':'demo','label':'person',
                                 'start_time':1760000000,'end_time':1760000005,
                                 'top_score':0.8,'has_snapshot':False}]
    monkeypatch.setattr(m.httpx,'get',lambda *a,**kw:R())
    ans=c.post('/api/frigate/sync').json()
    assert ans['inserted']==0 and ans['skipped']==1
    assert c.get('/api/frigate/events').json()==[]


def test_migrate_existing_v05_place_source_database(tmp_path,monkeypatch):
    import app.main as m
    path=tmp_path/'old.sqlite3'
    with sqlite3.connect(path) as c:
        c.execute('CREATE TABLE places(id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT NOT NULL UNIQUE)')
        c.execute('CREATE TABLE sources(id INTEGER PRIMARY KEY AUTOINCREMENT,place_id INTEGER NOT NULL, name TEXT NOT NULL,kind TEXT NOT NULL,uri TEXT NOT NULL)')
        c.execute('INSERT INTO places(id,name) VALUES(77,?)',('历史场所',))
        c.execute('INSERT INTO sources(id,place_id,name,kind,uri) VALUES(44,77,?,?,?)',
                  ('历史设备','rtsp','rtsp://test/stream'))
    monkeypatch.setattr(m,'DB',path)
    m.setup_db()
    c=TestClient(m.app)
    old=next(p for p in c.get('/api/places').json() if p['id']==77)
    assert old['region_path'].endswith('演示社区')
    assert old['active'] is True
    assert next(s for s in c.get('/api/sources').json() if s['id']==44)['enabled']==1
    # Repeated start is idempotent and doesn't duplicate region rows.
    before=len(c.get('/api/regions').json())
    m.setup_db()
    assert len(c.get('/api/regions').json())==before


def test_device_audit_redacts_rtsp_uri_and_survives_restart(case):
    c,m=case
    p=c.get('/api/places').json()[0]
    resp=c.post('/api/sources',json={
        'place_id':p['id'],'name':'需要凭证的测试摄像机',
        'kind':'rtsp','uri':'rtsp://demo:supersecret@127.0.0.1:8554/test'})
    assert resp.status_code==200
    s=resp.json()['id']
    assert c.patch(f'/api/sources/{s}/state',json={'enabled':False}).status_code==200
    audit=c.get('/api/admin/audit').json()
    assert [r['operation'] for r in audit[:2]]==['source_state','source_create']
    assert all('supersecret' not in str(r) for r in audit)
    assert '[REDACTED]' in audit[1]['new_value']
    m.setup_db()
    assert len(c.get('/api/admin/audit').json())==len(audit)


def test_scoped_frigate_events_use_region_tree(case):
    c,m=case
    branch=create_branch(c,'丁')
    result=c.post('/api/places',json={'name':'丁社区场所','region_id':branch[-1]['id']})
    place=result.json()['id']
    now=datetime.now(timezone.utc).isoformat()
    with m.db() as conn:
        conn.execute('INSERT INTO frigate_camera_bindings(camera,place_id) VALUES(?,?)',('demo',place))
        conn.execute('''INSERT INTO frigate_object_events
        (event_id,camera,place_id,label,started_at,ended_at,score,has_snapshot,updated_at)
        VALUES(?,?,?,?,?,?,?,?,?)''',('event-xyz','demo',place,'person',now,None,.85,0,now))
    assert len(c.get('/api/frigate/events').json())==1
    assert len(c.get('/api/frigate/events?region_id='+str(branch[0]['id'])).json())==1
    other_root=next(r['id'] for r in c.get('/api/regions').json() if r['name']=='演示州')
    assert c.get('/api/frigate/events?region_id='+str(other_root)).json()==[]
