"""Shared Excel place/zone/camera semantics and reversible binding history.

No real-person biometrics or cross-camera re-ID is required to execute these
tests. The only 'tracks' in the unified view are explicitly anonymous.
"""
from datetime import datetime, timedelta, timezone
import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def env(monkeypatch,tmp_path):
    from app import main, access
    monkeypatch.delenv('ZD_AUTH_TEST_COMPAT',raising=False)
    monkeypatch.setattr(main,'DB',tmp_path/'site-v1.sqlite')
    main.setup_db()
    with main.db() as c:
        root=c.execute('SELECT id FROM regions WHERE level=1 LIMIT 1').fetchone()['id']
        county=c.execute('SELECT id FROM regions WHERE level=2 LIMIT 1').fetchone()['id']
        far=c.execute('INSERT INTO regions(name,parent_id,level) VALUES(?,?,2)',
                      ('其他县区',root)).lastrowid
        street=c.execute('INSERT INTO regions(name,parent_id,level) VALUES(?,?,3)',
                         ('其他街道',far)).lastrowid
        leaf=c.execute('INSERT INTO regions(name,parent_id,level) VALUES(?,?,4)',
                       ('其他社区',street)).lastrowid
        remote=c.execute('INSERT INTO places(name,region_id) VALUES(?,?)',
                         ('外县学校',leaf)).lastrowid
        pa=c.execute("SELECT id FROM places WHERE name='测试场所 A'").fetchone()['id']
        pb=c.execute("SELECT id FROM places WHERE name='测试场所 B'").fetchone()['id']
        access.create_user(c,'siteadmin','site-admin-password-2026',root)
        access.create_user(c,'sitecounty','site-county-password-2026',county)
        access.create_user(c,'siteremote','site-remote-password-2026',far)
    client=TestClient(main.app)
    def login(user,password):
        r=client.post('/api/auth/login',json={'username':user,'password':password})
        assert r.status_code==200,r.text
        return {'Authorization':'Bearer '+r.json()['access_token']}
    return main,client,(pa,pb,remote),(
        login('siteadmin','site-admin-password-2026'),
        login('sitecounty','site-county-password-2026'),
        login('siteremote','site-remote-password-2026'))


def test_fixed_site_zone_camera_history_and_old_evidence(env):
    main,client,(pa,pb,remote),(root,county,other)=env
    base='/api/site'
    assert client.get(base+'/zones').status_code==401
    assert client.get(base+'/zones',headers=county).status_code==200
    school=client.post(base+'/zones',headers=county,json={
        'place_id':pa,'name':'学校A校门','category':'entrance'})
    assert school.status_code==201,school.text
    school=school.json()
    assert client.post(base+'/zones',headers=county,json={
        'place_id':pa,'name':'学校A校门'}).status_code==409
    hall=client.post(base+'/zones',headers=root,json={
        'place_id':pa,'name':'教学楼走廊','category':'corridor'}).json()['id']
    assert client.get(base+'/zones?place_id='+str(remote),headers=county).status_code==403
    assert client.post(base+'/zones',headers=other,json={
        'place_id':pa,'name':'越权访问'}).status_code==403
    assert client.patch(f'{base}/zones/{school["id"]}/state',
                        headers=root,json={'active':False}).status_code==200
    assert client.patch(f'{base}/zones/{school["id"]}/state',
                        headers=root,json={'active':True}).status_code==200

    # Two different camera feeds at the SAME school entrance.
    sources=[]
    for cam in ['校门外', '校门内']:
        r=client.post('/api/sources',headers=county,json={
            'place_id':pa,'name':cam,'kind':'file','uri':f'/tmp/{cam}.mp4'})
        assert r.status_code==200,r.text
        sources.append(r.json()['id'])
    for source in sources:
        linked=client.put(f'{base}/sources/{source}/binding',headers=county,json={
            'place_id':pa,'zone_id':school['id'],'reason':'同一场所两个固定角度'})
        assert linked.status_code==200,linked.text
        assert linked.json()['zone_id']==school['id']
    with main.db() as c:
        assert c.execute("""SELECT COUNT(DISTINCT source_id) FROM source_site_bindings
            WHERE zone_id=? AND valid_until IS NULL""",(school['id'],)).fetchone()[0]==2
        assert c.execute('SELECT COUNT(*) FROM synthetic_observations').fetchone()[0]==0
    assert client.patch(f'{base}/zones/{school["id"]}/state',headers=root,
                        json={'active':False}).status_code==409
    assert client.put(f'{base}/sources/{sources[0]}/binding',headers=county,json={
        'place_id':pb,'zone_id':school['id'],'reason':'错误地跨场所使用区域'}).status_code==422

    # Old video observations persist with ORIGINAL place even when camera moves.
    from app import site_model
    original=client.get(f'{base}/sources/{sources[0]}/bindings',headers=county).json()
    old_binding=original['bindings'][-1]
    assert old_binding['place_id']==pa
    old_at=old_binding['valid_from']
    with main.db() as c:
        before=site_model.resolve_binding(c,sources[0],old_at)
        assert before['place_id']==pa and before['zone_id']==school['id']
        c.execute("""INSERT INTO samples
          (source_id,place_id,origin,ingested_at,detected_faces)
          VALUES(?,?,'video',?,0)""",(sources[0],pa,main.utcnow()))
        receipt=c.execute("SELECT id,place_id FROM samples ORDER BY id DESC LIMIT 1").fetchone()
        sample_id=receipt['id']
    moved=client.put(f'{base}/sources/{sources[0]}/binding',headers=county,json={
        'place_id':pb,'zone_id':None,'reason':'实际摄像机拆机迁移到第二场所'})
    assert moved.status_code==200,moved.text
    new=moved.json()
    assert new['place_id']==pb and new['zone_id'] is None
    history=client.get(f'{base}/sources/{sources[0]}/bindings',headers=county)
    assert history.status_code==200
    intervals=history.json()['bindings']
    assert len(intervals)==3
    assert intervals[1]['valid_until']==intervals[2]['valid_from']
    assert intervals[2]['provenance']=='explicit'
    with main.db() as c:
        assert site_model.resolve_binding(c,sources[0],old_at)['place_id']==pa
        assert site_model.resolve_binding(c,sources[0],new['valid_from'])['place_id']==pb
        assert c.execute('SELECT place_id FROM samples WHERE id=?',
                         (sample_id,)).fetchone()['place_id']==pa
        assert c.execute('SELECT place_id FROM sources WHERE id=?',
                         (sources[0],)).fetchone()['place_id']==pb
    assert client.put(f'{base}/sources/{sources[0]}/binding',headers=county,json={
        'place_id':pb,'zone_id':None,'reason':'没有实际变更'}).json()['unchanged'] is True
    assert client.patch(f'{base}/zones/{school["id"]}/state',headers=county,
                        json={'active':False}).status_code==409  # second camera still active
    assert client.put(f'{base}/sources/{sources[1]}/binding',headers=county,json={
        'place_id':pa,'zone_id':hall,'reason':'移到同一个学校内教学楼'}).status_code==200
    assert client.patch(f'{base}/zones/{school["id"]}/state',headers=county,
                        json={'active':False}).status_code==200


def test_old_source_backfill_is_unknown_before_first_recorded_binding(env):
    main,client,(pa,pb,remote),(root,county,other)=env
    from app import site_model
    with main.db() as c:
        legacy=c.execute('INSERT INTO sources(place_id,name,kind,uri) VALUES(?,?,?,?)',
                         (pa,'历史遗留固定源','file','/tmp/legacy.mp4')).lastrowid
    main.setup_db()  # legacy migration makes an explicit, NEW audit baseline
    with main.db() as c:
        b=site_model._current(c,legacy)
        assert b['provenance']=='legacy_backfill'
        assert 'legacy_unknown' in b['reason']
        back=datetime.fromisoformat(b['valid_from'])-timedelta(days=1)
        assert site_model.resolve_binding(c,legacy,back.isoformat()) is None
        assert site_model.resolve_binding(c,legacy,b['valid_from'])['place_id']==pa


def test_anonymous_multicamera_projection_keeps_tracking_id_namespace(env):
    main,client,(pa,pb,remote),(root,county,other)=env
    # Real Frigate evidence: same event_id across cameras is still TWO
    # local, anonymous target segments, not one global human.
    with main.db() as c:
        for camera in ['gate_left','gate_right']:
            c.execute('INSERT INTO frigate_camera_bindings(camera,place_id) VALUES(?,?)',
                      (camera,pa))
            c.execute("""INSERT INTO frigate_object_events
                (event_id,camera,place_id,label,started_at,score,has_snapshot,updated_at)
                VALUES(?,? ,?,'person',?,0.8,0,?)""",
                ('local-1',camera,pa,main.utcnow(),main.utcnow()))
        # Same integer 'local-1' in the CAVIAR official track table is not
        # the Frigate event nor an identity. Manual box records are source data.
        src=c.execute('INSERT INTO sources(place_id,name,kind,uri) VALUES(?,?,?,?)',
                      (pa,'公共机位','file','/tmp/annotated.mp4')).lastrowid
        from app import site_model
        site_model.record_initial(c,src,pa)
        c.execute("""INSERT INTO public_cctv_imports
          (source_id,dataset,scenario,camera_view,video_sha256,video_bytes,video_url,
           annotation_url,frames_available,xml_frame_count,license_note,imported_at)
          VALUES (?,'CAVIAR','demo','corridor','hash',10,'https://a','https://a',
                200,200,'CC BY-SA',?)""",(src,main.utcnow()))
        c.execute("""INSERT INTO public_cctv_track_boxes
           (source_id,frame_number,camera_local_track_id,x,y,width,height,provenance)
           VALUES(?,?,'local-1',1,1,10,10,'CAVIAR_ANNOTATION')""",(src,44))
    r=client.get('/api/site/anonymous-events',headers=county)
    assert r.status_code==200,r.text
    data=r.json()
    assert data['identity_verified'] is False
    assert data['count_is_unique_people'] is False
    records=data['events']
    assert len(records)==3,records
    assert all(x['subject_id'] is None and x['unique_person_counted'] is False
               for x in records)
    assert {(x['provider'],x['camera']) for x in records}=={
        ('frigate','gate_left'),('frigate','gate_right'),('caviar_ground_truth','corridor')}
    assert {x['time_basis'] for x in records}=={
        'UTC_event_clock','archival_video_frame_number'}
    assert client.get('/api/site/anonymous-events?limit=1',headers=county).json()['limited']
    assert client.get('/api/site/anonymous-events',headers=other).json()['events']==[]
    assert client.get('/api/business/analysis',headers=root).json()['person_count']==0


def test_moving_across_counties_hides_former_site_binding_history(env):
    main,client,(pa,pb,remote),(root,county,other)=env
    source=client.post('/api/sources',headers=root,json={
        'place_id':pa,'name':'跨辖区摄像机','kind':'file','uri':'/tmp/p.mp4'}).json()['id']
    update=client.put('/api/sources/'+str(source),headers=root,json={
        'place_id':remote,'name':'跨辖区摄像机','kind':'file',
        'uri':'/tmp/p.mp4','enabled':True})
    assert update.status_code==200,update.text
    # Current owner may NOT read historical confidential site names in countyA.
    assert client.get(f'/api/site/sources/{source}/bindings',headers=other).status_code==403
    assert client.get(f'/api/site/sources/{source}/bindings',headers=county).status_code==403
    assert client.get(f'/api/site/sources/{source}/bindings',headers=root).status_code==200
    with main.db() as c:
        assert c.execute('SELECT place_id FROM sources WHERE id=?',(source,)).fetchone()[0]==remote
        records=c.execute('SELECT place_id FROM source_site_bindings WHERE source_id=? ORDER BY id',(source,)).fetchall()
        assert [r[0] for r in records]==[pa,remote]
