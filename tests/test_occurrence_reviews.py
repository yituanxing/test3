"""Actual SQLite/FastAPI tests for evidence quality, not real-person identity."""
from datetime import datetime, timezone, timedelta
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def env(tmp_path,monkeypatch):
    from app import main,access,site_model
    monkeypatch.delenv('ZD_AUTH_TEST_COMPAT',raising=False)
    monkeypatch.setattr(main,'DB',tmp_path/'quality.sqlite3')
    main.setup_db()
    with main.db() as c:
        root=c.execute('SELECT id FROM regions WHERE level=1 LIMIT 1').fetchone()['id']
        county=c.execute('SELECT id FROM regions WHERE level=2 LIMIT 1').fetchone()['id']
        far=c.execute('INSERT INTO regions(name,parent_id,level) VALUES(?,?,2)',
                      ('质检异地县',root)).lastrowid
        street=c.execute('INSERT INTO regions(name,parent_id,level) VALUES(?,?,3)',
                         ('质检异地街道',far)).lastrowid
        leaf=c.execute('INSERT INTO regions(name,parent_id,level) VALUES(?,?,4)',
                       ('质检异地社区',street)).lastrowid
        other_place=c.execute('INSERT INTO places(name,region_id) VALUES(?,?)',
                              ('异地质检场所',leaf)).lastrowid
        place=c.execute("SELECT id FROM places WHERE name='测试场所 A'").fetchone()['id']
        access.create_user(c,'qa_state','quality-state-password-2026',root)
        access.create_user(c,'qa_county','quality-county-password-2026',county)
        access.create_user(c,'qa_remote','quality-remote-password-2026',far)
        access.create_user(c,'qa_community','quality-community-password-2026',c.execute(
            'SELECT id FROM regions WHERE level=4 AND id!=?',(leaf,)).fetchone()[0])
        # Same venue/source is a published dataset, tracks are anonymous camera
        # IDs, and this fixture does not pretend to contain a face identity.
        source=c.execute("""INSERT INTO sources(place_id,name,kind,uri)
            VALUES(?,?,?,?)""",(place,'CAVIAR quality proof','file','/tmp/gt-qa.mpg')).lastrowid
        site_model.record_initial(c,source,place)
        c.execute("""INSERT INTO public_cctv_imports
          (source_id,dataset,scenario,camera_view,video_sha256,video_bytes,
           video_url,annotation_url,frames_available,xml_frame_count,
           license_note,imported_at)
          VALUES(?,'CAVIAR','qa-scenario','corridor','digest-v1',100,
             'https://official.example/movie','https://official.example/gt',
             300,300,'CC BY-SA',?)""",(source,main.utcnow()))
        for frame in range(1,6):
            c.execute("""INSERT INTO public_cctv_track_boxes
                (source_id,frame_number,camera_local_track_id,x,y,width,height,provenance)
                VALUES(?,?,'local-1',10,15,20,40,'CAVIAR_ANNOTATION')""",
                (source,frame))
        c.execute('INSERT INTO frigate_camera_bindings(camera,place_id) VALUES(?,?)',
                  ('camera_gate',place))
        c.execute("""INSERT INTO frigate_object_events
            (camera,event_id,place_id,label,started_at,score,has_snapshot,updated_at)
            VALUES ('camera_gate','local-1',?,'person',?,0.83,1,?)""",
            (place,main.utcnow(),main.utcnow()))
        c.execute('INSERT INTO frigate_camera_bindings(camera,place_id) VALUES(?,?)',
                  ('camera_remote',other_place))
        c.execute("""INSERT INTO frigate_object_events
            (camera,event_id,place_id,label,started_at,score,has_snapshot,updated_at)
            VALUES ('camera_remote','local-1',?,'person',?,0.81,0,?)""",
            (other_place,main.utcnow(),main.utcnow()))
    client=TestClient(main.app)
    def login(u,p):
        r=client.post('/api/auth/login',json={'username':u,'password':p})
        assert r.status_code==200,r.text
        return {'Authorization':'Bearer '+r.json()['access_token']}
    creds={'state':login('qa_state','quality-state-password-2026'),
           'county':login('qa_county','quality-county-password-2026'),
           'remote':login('qa_remote','quality-remote-password-2026'),
           'community':login('qa_community','quality-community-password-2026')}
    return main,client,source,place,creds


def test_quality_is_append_only_and_stales_when_actual_box_changes(env):
    main,client,source,place,user=env
    all_tracks=client.get('/api/site/occurrences',headers=user['county']).json()['occurrences']
    gt=next(x for x in all_tracks if x['provider']=='caviar_ground_truth')
    local=gt['occurrence_key']
    base=f'/api/site/occurrences/{local}/quality'
    before=client.get(base,headers=user['county'])
    assert before.status_code==200,before.text
    blank=before.json()
    assert blank['quality_status']=='not_reviewed' and not blank['reviews']
    assert blank['identity_confirmed'] is False and blank['valid_site_visit'] is False
    assert client.get(base).status_code==401
    assert client.get(base,headers=user['remote']).status_code==404
    assert client.post(base,headers=user['remote'],json={
        'verdict':'false_positive','reason':'异地越权尝试'}).status_code==404
    assert client.post(base,headers=user['community'],json={
        'verdict':'false_positive','reason':'社区不能审核'}).status_code==403
    assert client.post(base,headers=user['county'],json={
        'verdict':'is_same_real_person','reason':'不允许命名真人身份'}).status_code==422
    assert client.post(base,headers=user['county'],json={
        'verdict':'false_positive','reason':'bad'}).status_code==422

    first=client.post(base,headers=user['county'],json={
        'verdict':'valid_local_target','reason':'逐帧核对是真实标注的人体框'})
    assert first.status_code==201,first.text
    assert first.json()['quality_status']=='valid_local_target'
    assert len(first.json()['reviews'])==1
    first_sig=first.json()['evidence_signature']
    with main.db() as c:
        c.execute("""UPDATE public_cctv_track_boxes SET x=x+5
            WHERE source_id=? AND frame_number=3 AND camera_local_track_id='local-1'""",
            (source,))
    stale=client.get(base,headers=user['county'])
    assert stale.status_code==200
    assert stale.json()['review_stale'] is True
    assert stale.json()['quality_status']=='stale_review'
    assert stale.json()['evidence_signature']!=first_sig
    assert len(stale.json()['reviews'])==1
    # Reviewer supersedes old verdict; does not overwrite either the CVML
    # original row or its previous review.
    changed=client.post(base,headers=user['state'],json={
        'verdict':'needs_review','reason':'第3帧边界发生变化需要复核'})
    assert changed.status_code==201
    assert changed.json()['quality_status']=='needs_review'
    assert changed.json()['review_stale'] is False
    assert len(changed.json()['reviews'])==2
    assert changed.json()['reviews'][0]['verdict']=='valid_local_target'
    restored=client.post(base,headers=user['county'],json={
        'verdict':'false_positive','reason':'经二次核对是人体框标注误检'})
    assert restored.status_code==201
    assert restored.json()['quality_status']=='false_positive'
    quality=client.get('/api/site/quality/summary',headers=user['county'])
    assert quality.status_code==200
    assert quality.json()['counts']['false_positive']==1
    assert quality.json()['counts_real_people'] is False
    assert quality.json()['counts_site_visits'] is False
    with main.db() as c:
        rows=c.execute("""SELECT verdict,evidence_signature FROM anonymous_occurrence_reviews
             WHERE occurrence_key=? ORDER BY id""",(local,)).fetchall()
        assert len(rows)==3 and rows[0]['evidence_signature']!=rows[1]['evidence_signature']
        assert c.execute('SELECT COUNT(*) FROM admin_audit WHERE operation=?',
                         ('anonymous_occurrence_quality_review',)).fetchone()[0]==3
        assert c.execute('SELECT COUNT(*) FROM synthetic_observations').fetchone()[0]==0
        assert c.execute('SELECT COUNT(*) FROM synthetic_registry_runs').fetchone()[0]==0
    # Reloading the service DB does not delete review decisions.
    main.setup_db()
    assert len(client.get(base,headers=user['county']).json()['reviews'])==3
    assert client.get('/api/business/analysis',headers=user['state']).json()['person_count']==0


def test_provider_scope_and_frigate_upstream_revision_invalidation(env):
    main,client,source,place,user=env
    entries=client.get('/api/site/occurrences',headers=user['county']).json()['occurrences']
    gt=next(x for x in entries if x['provider']=='caviar_ground_truth')
    fg=next(x for x in entries if x['provider']=='frigate')
    assert gt['occurrence_key']!=fg['occurrence_key']
    endpoint='/api/site/occurrences/'+fg['occurrence_key']+'/quality'
    original=client.post(endpoint,headers=user['county'],json={
        'verdict':'false_positive','reason':'原检测像是背景人形遮挡'})
    assert original.status_code==201
    old=original.json()['evidence_signature']
    with main.db() as c:
        c.execute("""UPDATE frigate_object_events SET score=0.94,
                 updated_at=? WHERE camera='camera_gate' AND event_id='local-1'""",
                 ((datetime.now(timezone.utc)+timedelta(seconds=10)).isoformat(),))
    again=client.get(endpoint,headers=user['county']).json()
    assert again['review_stale'] is True and again['evidence_signature']!=old
    assert again['quality_status']=='stale_review'
    all_in_county=client.get('/api/site/quality/summary',headers=user['county']).json()
    assert all_in_county['counts']['stale_review']==1
    assert all_in_county['camera_local_events_scanned']==2
    forbidden=client.get('/api/site/quality/summary',headers=user['remote']).json()
    assert forbidden['camera_local_events_scanned']==1
    assert forbidden['counts']['not_reviewed']==1
    assert client.post('/api/site/occurrences/'+gt['occurrence_key']+'/quality',
                       headers=user['remote'],json={
                           'verdict':'valid_local_target','reason':'另一辖区用户不能写此记录'}).status_code==404
    assert client.get('/api/site/occurrences/'+'f'*24+'/quality',
                      headers=user['state']).status_code==404
    assert client.get('/api/site/occurrences/'+'bad'+'/quality',
                      headers=user['state']).status_code==422  # state admin sees input validation
