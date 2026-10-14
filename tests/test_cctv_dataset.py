"""Real fixed-camera CAVIAR dataset adapter tests.

Small generated MPEG-compatible test video proves registration, CVML parsing,
scope, idempotency and actual image encoding. It is NOT claimed to be CAVIAR
footage; the separate optional live-download job provides that proof.
"""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def context(tmp_path,monkeypatch):
    from app import main,access,cctv_dataset
    monkeypatch.delenv('ZD_AUTH_TEST_COMPAT',raising=False)
    monkeypatch.setattr(main,'DB',tmp_path/'cctv.sqlite3')
    monkeypatch.setattr(cctv_dataset,'_store_dir',tmp_path/'public-clips')
    main.setup_db()
    with main.db() as db:
        root=db.execute('SELECT id FROM regions WHERE level=1 LIMIT 1').fetchone()[0]
        county=db.execute('SELECT id FROM regions WHERE level=2 LIMIT 1').fetchone()[0]
        place=db.execute("SELECT id FROM places WHERE name='测试场所 A'").fetchone()[0]
        access.create_user(db,'cctvroot','long-cctv-state-password',root)
        access.create_user(db,'cctvcounty','long-cctv-county-password',county)
    client=TestClient(main.app)
    def token(username,password):
        response=client.post('/api/auth/login',
            json={'username':username,'password':password})
        assert response.status_code==200,response.text
        return {'Authorization':'Bearer '+response.json()['access_token']}
    return main,cctv_dataset,client,place,token('cctvroot','long-cctv-state-password'),token('cctvcounty','long-cctv-county-password')


def xml():
    return b'''<?xml version="1.0"?>
<dataset>
 <frame number="1"><objectlist>
   <object id="0"><box xc="32" yc="32" w="20" h="30"/></object>
   <object id="1"><box xc="60" yc="30" w="20" h="30"/></object>
 </objectlist></frame>
 <frame number="2"><objectlist>
   <object id="0"><box xc="35" yc="32" w="20" h="30"/></object>
 </objectlist></frame>
 <frame number="3"><objectlist>
   <object id="1"><box xc="60" yc="30" w="20" h="30"/></object>
 </objectlist></frame>
</dataset>'''


def test_fixed_cameras_share_one_real_place_no_cross_camera_identity(context,monkeypatch):
    main,mod,client,place,root,county=context
    video=(Path(__file__).resolve().parents[1]/'samples'/'astronaut-face-test.mp4').read_bytes()
    def read_fixture(url,max_bytes):
        assert 'EnterExitCrossingPaths1' in url
        assert url.startswith(mod.DATASET_ROOT)
        return xml() if url.endswith('.xml') else video
    monkeypatch.setattr(mod,'_fetch_allowlisted',read_fixture)
    url='/api/cctv/datasets/import'
    assert client.post(url,json={'place_id':place,'view':'corridor'}).status_code==401
    assert client.post(url,headers=county,json={'place_id':place}).status_code==403
    cat=client.get('/api/cctv/datasets',headers=county)
    assert cat.status_code==200
    assert 'one venue' in cat.json()['scenarios'][0]['title']
    created=[]
    for camera in ('corridor','front'):
        response=client.post(url,headers=root,json={
            'place_id':place,'scenario':'enter_exit_crossing_paths_1','view':camera})
        assert response.status_code==201,response.text
        out=response.json()
        assert out['status']=='imported' and out['annotation_boxes']==4
        assert out['video']['frames']>=3
        assert out['track_semantics']=='camera_local_ground_truth_not_global_identity'
        created.append(out['source_id'])
    assert created[0]!=created[1]
    # Both camera angles must never be misrepresented as different venues.
    other=client.get('/api/places',headers=root).json()
    different=next(x['id'] for x in other if x['id']!=place)
    assert client.post(url,headers=root,json={
        'place_id':different,'view':'front'}).status_code==409
    # Shared region monitoring shows archive evidence only in its own lane;
    # changing imported GT must never manufacture current-day people/events.
    metrics=client.get('/api/monitoring/overview',headers=root)
    assert metrics.status_code==200,metrics.text
    evidence=metrics.json()['licensed_research_video']
    assert evidence['fixed_camera_sources']==2
    assert evidence['camera_local_tracks']==4
    assert evidence['manual_person_boxes']==8
    assert evidence['counts_unique_people'] is False
    assert evidence['counts_for_date_window'] is False
    assert metrics.json()['counts']['person_objects']==0
    assert client.post(url,headers=root,json={
        'place_id':place,'view':'front'}).json()['status']=='already_imported'
    with main.db() as db:
        rows=[dict(x) for x in db.execute('''SELECT s.id,s.place_id,i.camera_view,i.license_note
            FROM public_cctv_imports i JOIN sources s ON i.source_id=s.id''')]
        assert len(rows)==2
        assert {x['place_id'] for x in rows}=={place}
        assert {x['camera_view'] for x in rows}=={'corridor','front'}
        assert all(x['license_note'].startswith('CC BY-SA') for x in rows)
        assert db.execute('SELECT COUNT(*) FROM synthetic_observations').fetchone()[0]==0
    listed=client.get('/api/cctv/sources',headers=county)
    assert listed.status_code==200,listed.text
    assert len(listed.json()['sources'])==2
    for source in created:
        activity=client.get(f'/api/cctv/sources/{source}/activity',headers=county)
        assert activity.status_code==200,activity.text
        data=activity.json()
        assert data['local_track_count']==2 and data['frame_annotation_count']==4
        assert {x['camera_local_track_id'] for x in data['local_tracks']}=={'0','1'}
        picture=client.get(f'/api/cctv/sources/{source}/frame?frame=2',headers=county)
        assert picture.status_code==200,picture.text[:120]
        assert picture.headers['content-type'].startswith('image/jpeg')
        assert picture.content[:2]==b'\xff\xd8'
        assert picture.headers['x-annotations']=='CAVIAR manual ground truth'
        assert client.get(f'/api/cctv/sources/{source}/frame?frame=99999',
                          headers=root).status_code==422
    assert client.post(url,headers=root,json={'place_id':place,'view':'not-allowed'}).status_code==422
    assert client.get('/api/cctv/sources/999999/activity',headers=root).status_code==404


def test_caviar_loader_denies_arbitrary_network_sources_or_malformed_xml(context):
    main,mod,client,place,root,county=context
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        mod._fetch_allowlisted('https://evil.invalid/private/192.168.1.1',1024)
    assert exc.value.status_code==422
    with pytest.raises(HTTPException) as exc:
        mod._annotations(b'<!DOCTYPE x [ <!ENTITY x SYSTEM "file:///etc/passwd">]><dataset/>',
                          max_frames=20)
    assert exc.value.status_code==422
    with pytest.raises(HTTPException) as exc:
        mod._annotations(b'<dataset><frame number="1"><objectlist><object id="1"><box xc="oops" yc="10" w="2" h="2"/></object></objectlist></frame></dataset>',
                         max_frames=20)
    assert exc.value.status_code==422


def test_existing_tracker_baseline_requires_real_optional_dependency_and_enforces_scope(context,monkeypatch):
    main,mod,client,place,root,county=context
    from app import tracker_adapters
    clip=(Path(__file__).resolve().parents[1]/'samples'/'astronaut-face-test.mp4').read_bytes()
    monkeypatch.setattr(mod,'_fetch_allowlisted',
                        lambda url,max_bytes: xml() if url.endswith('.xml') else clip)
    response=client.post('/api/cctv/datasets/import',headers=root,
                         json={'place_id':place,'view':'corridor'})
    assert response.status_code==201,response.text
    source=response.json()['source_id']
    path=f'/api/cctv/sources/{source}/tracking-baseline'
    assert client.get(path).status_code==401
    assert client.get(path,headers=county,params={'max_frames':0}).status_code==422
    assert client.get('/api/cctv/sources/99999/tracking-baseline',
                      headers=county).status_code==404
    from fastapi import HTTPException
    def no_dependency():
        raise HTTPException(503,'必须安装现有 ByteTrack 包')
    monkeypatch.setattr(tracker_adapters,'_dependencies',no_dependency)
    missing=client.get(path,headers=county)
    assert missing.status_code==503
    assert 'ByteTrack' in missing.text
    # A failure must not invent track IDs or write sample/identity records.
    with main.db() as c:
        assert c.execute('SELECT COUNT(*) FROM synthetic_observations').fetchone()[0]==0
        assert c.execute('SELECT COUNT(*) FROM research_occurrence_links').fetchone()[0]==0


def test_actual_roboflow_bytetrack_tracks_camera_local_ground_truth_when_installed(context,monkeypatch):
    pytest.importorskip('trackers')
    pytest.importorskip('supervision')
    main,mod,client,place,root,county=context
    clip=(Path(__file__).resolve().parents[1]/'samples'/'astronaut-face-test.mp4').read_bytes()
    monkeypatch.setattr(mod,'_fetch_allowlisted',
                        lambda url,max_bytes: xml() if url.endswith('.xml') else clip)
    ids=[]
    for view in ('corridor','front'):
        resp=client.post('/api/cctv/datasets/import',headers=root,
                         json={'place_id':place,'view':view})
        assert resp.status_code==201,resp.text
        ids.append(resp.json()['source_id'])
    outputs=[]
    for source in ids:
        r=client.get(f'/api/cctv/sources/{source}/tracking-baseline?max_frames=8',
                     headers=county)
        oc=client.get(f'/api/cctv/sources/{source}/tracking-baseline?max_frames=8&algorithm=ocsort',
                      headers=county)
        assert oc.status_code==200,oc.text
        assert oc.json()['tracker']=='roboflow_trackers_ocsort'
        assert oc.json()['ground_truth_boxes']==4
        assert oc.json()['cross_camera_matching_ran'] is False
        assert r.status_code==200,r.text
        data=r.json()
        assert data['tracker']=='roboflow_trackers_bytetrack'
        assert data['ground_truth_boxes']==4
        assert data['ground_truth_local_track_count']==2
        assert data['automatic_tracking_ran'] is True
        assert data['automatic_person_detection_ran'] is False
        assert data['cross_camera_matching_ran'] is False
        assert data['counts_unique_people'] is False
        assert data['feeds_real_roster_or_alerts'] is False
        assert data['assignment_coverage'] is not None
        assert data['assigned_boxes']+data['unconfirmed_boxes']==4
        outputs.append(data)
    assert client.get(f'/api/cctv/sources/{ids[0]}/tracking-baseline?algorithm=unknown',
                      headers=county).status_code==422
    assert outputs[0]['source_id']!=outputs[1]['source_id']
    assert {x['camera_view'] for x in outputs}=={'front','corridor'}
    with main.db() as c:
        assert c.execute('SELECT COUNT(*) FROM synthetic_observations').fetchone()[0]==0
        assert c.execute('SELECT COUNT(*) FROM research_occurrence_links').fetchone()[0]==0


def test_dual_camera_existing_tracker_baseline_is_not_global_identity(context,monkeypatch):
    pytest.importorskip('trackers')
    pytest.importorskip('supervision')
    main,mod,client,place,root,county=context
    clip=(Path(__file__).resolve().parents[1]/'samples'/'astronaut-face-test.mp4').read_bytes()
    monkeypatch.setattr(mod,'_fetch_allowlisted',
                        lambda url,max_bytes: xml() if url.endswith('.xml') else clip)
    a=client.post('/api/cctv/datasets/import',headers=root,
                  json={'place_id':place,'view':'corridor'})
    assert a.status_code==201,a.text
    sid=a.json()['source_id']
    endpoint=f'/api/cctv/sources/{sid}/pair-tracking-baseline?max_frames=8'
    assert client.get(endpoint,headers=county).status_code==409
    b=client.post('/api/cctv/datasets/import',headers=root,
                  json={'place_id':place,'view':'front'})
    assert b.status_code==201,b.text
    result=client.get(endpoint,headers=county)
    assert result.status_code==200,result.text
    data=result.json()
    assert data['camera_count']==2
    assert {x['camera_view'] for x in data['cameras']}=={'corridor','front'}
    assert all(len(x['algorithms'])==2 for x in data['cameras'])
    for cam in data['cameras']:
        assert {m['algorithm'] for m in cam['algorithms']}=={'bytetrack','ocsort'}
        assert all(m['ground_truth_boxes']==4 and m['automatic_tracking_ran'] is True
                   and m['automatic_person_detection_ran'] is False
                   for m in cam['algorithms'])
    assert data['automatic_person_detection_ran'] is False
    assert data['cross_camera_identity_matching_ran'] is False
    assert data['shared_global_identity_ground_truth'] is False
    assert data['eligible_for_real_roster_or_alerts'] is False
    with main.db() as c:
        assert c.execute('SELECT COUNT(*) FROM research_occurrence_links').fetchone()[0]==0
        assert c.execute('SELECT COUNT(*) FROM synthetic_observations').fetchone()[0]==0


def test_real_pretrained_detector_endpoint_is_scoped_and_never_fakes_missing_model(context,monkeypatch):
    from app import person_detection_adapter
    from fastapi import HTTPException
    main,mod,client,place,root,county=context
    clip=(Path(__file__).resolve().parents[1]/'samples'/'astronaut-face-test.mp4').read_bytes()
    monkeypatch.setattr(mod,'_fetch_allowlisted',
                        lambda url,max_bytes: xml() if url.endswith('.xml') else clip)
    imp=client.post('/api/cctv/datasets/import',headers=root,
                    json={'place_id':place,'view':'corridor'})
    assert imp.status_code==201,imp.text
    sid=imp.json()['source_id']
    route=f'/api/cctv/sources/{sid}/person-detector-baseline'
    assert client.get(route).status_code==401
    assert client.get(route,headers=county,params={'max_frames':65}).status_code==422
    assert client.get(route,headers=county,params={'sample_every':0}).status_code==422
    assert client.get('/api/cctv/sources/999999/person-detector-baseline',
                      headers=county).status_code==404
    def model_unavailable():
        raise HTTPException(503,'YOLO11n研究模型尚未安装')
    from app import tracker_adapters
    # Base CI intentionally omits *both* optional existing external libs.
    # Stub only the dependency loader so the test reaches the missing YOLO
    # failure; this does NOT mock actual inference or report model success.
    monkeypatch.setattr(tracker_adapters,'_dependencies',
                        lambda:(object(),object(),object()))
    monkeypatch.setattr(person_detection_adapter,'_model',model_unavailable)
    missing=client.get(route,headers=county)
    assert missing.status_code==503,missing.text
    assert 'YOLO11n' in missing.text
    # Critical: if a downloaded video is modified, do not silently infer
    # and then claim a metric that used a different video than GT.
    with main.db() as c:
        c.execute("UPDATE public_cctv_imports SET video_sha256=? WHERE source_id=?",
                  ('0'*64,sid))
    invalid=client.get(route,headers=county)
    assert invalid.status_code==409
    assert 'SHA256' in invalid.text
    with main.db() as c:
        assert c.execute('SELECT COUNT(*) FROM synthetic_observations').fetchone()[0]==0
        assert c.execute('SELECT COUNT(*) FROM research_occurrence_links').fetchone()[0]==0


def test_actual_pretrained_yolo_person_on_video_when_optional_model_installed(context,monkeypatch):
    pytest.importorskip('ultralytics')
    pytest.importorskip('trackers')
    main,mod,client,place,root,county=context
    clip=(Path(__file__).resolve().parents[1]/'samples'/'astronaut-face-test.mp4').read_bytes()
    monkeypatch.setattr(mod,'_fetch_allowlisted',
                        lambda url,max_bytes: xml() if url.endswith('.xml') else clip)
    imp=client.post('/api/cctv/datasets/import',headers=root,
                    json={'place_id':place,'view':'corridor'})
    assert imp.status_code==201,imp.text
    sid=imp.json()['source_id']
    r=client.get(f'/api/cctv/sources/{sid}/person-detector-baseline?max_frames=4',
                 headers=county)
    assert r.status_code==200,r.text
    result=r.json()
    assert result['frames_decoded']==4 and result['frames_inferred']==4
    assert result['real_pretrained_person_detector_ran'] is True
    assert result['existing_automatic_tracker_ran'] is True
    assert result['detector_received_ground_truth_boxes'] is False
    assert result['cross_camera_identity_matching_ran'] is False
    assert result['identity_accuracy_measured'] is False
    assert result['counted_unique_people'] is False
    assert sum(x['automatically_detected_person_boxes']
               for x in result['frame_summaries'])==result['predicted_person_boxes']


def test_real_detector_runs_enter_canonical_anonymous_occurrences_and_qa_with_versions(context,monkeypatch):
    """Test unified persistence contract without pretending a mocked detector is real.

    Real pretrained YOLO inference is independently exercised by the owner
    real-media job; these DB fixture boxes exercise immutable evidence flow.
    """
    from app import person_detection_adapter
    main,mod,client,place,root,county=context
    clip=(Path(__file__).resolve().parents[1]/'samples'/'astronaut-face-test.mp4').read_bytes()
    monkeypatch.setattr(mod,'_fetch_allowlisted',
                        lambda url,max_bytes: xml() if url.endswith('.xml') else clip)
    imp=client.post('/api/cctv/datasets/import',headers=root,
                    json={'place_id':place,'view':'corridor'})
    assert imp.status_code==201,imp.text
    sid=imp.json()['source_id']
    with main.db() as c:
        sha=c.execute('SELECT video_sha256 FROM public_cctv_imports WHERE source_id=?',
                      (sid,)).fetchone()[0]
    def fixture_run(c,source_id,*,max_frames,sample_every,confidence):
        assert source_id==sid and max_frames==12
        return {
            'source_id':sid,'video_sha256':sha,'place_id':place,
            'model':'controlled-fixture-for-persistence','tracker':'roboflow_trackers_bytetrack',
            'frames_decoded':12,'frames_inferred':12,'sampling_step':sample_every,
            'local_track_boxes':[
                {'frame_number':n,'local_track_id':'7','x':10+n,
                 'y':18,'width':38,'height':61,'confidence':.8}
                for n in range(1,13)],
            'real_pretrained_person_detector_ran':False,
            'existing_automatic_tracker_ran':False,
            'cross_camera_identity_matching_ran':False,
        }
    monkeypatch.setattr(person_detection_adapter,'run_real_person_detector',fixture_run)
    route=f'/api/cctv/sources/{sid}/person-detector-run?max_frames=12'
    assert client.post(route).status_code==401
    assert client.post(route,headers=county).status_code==403
    out=client.post(route,headers=root)
    assert out.status_code==200,out.text
    assert out.json()['status']=='saved'
    assert out.json()['stored_local_boxes']==12
    assert out.json()['eligible_for_real_identity_or_visit'] is False
    again=client.post(route,headers=root)
    assert again.status_code==200
    assert again.json()['status']=='existing'
    assert again.json()['run_key']==out.json()['run_key']
    evidence=client.get('/api/site/occurrences',headers=county)
    assert evidence.status_code==200,evidence.text
    records=evidence.json()['occurrences']
    yolo=[row for row in records if row['provider']=='yolo_bytetrack']
    manual=[row for row in records if row['provider']=='caviar_ground_truth']
    assert len(yolo)==1 and len(manual)==2
    item=yolo[0]
    assert item['first_seen']==1 and item['last_seen']==12
    assert item['annotation_frames']==12 and item['track_id']=='7'
    assert item['time_basis']=='archival_video_frame_number'
    assert item['subject_id'] is None and item['counts_as_visit'] is False
    assert item['occurrence_key'] not in {x['occurrence_key'] for x in manual}
    counted=client.get('/api/site/occurrences/summary?days=7',headers=county).json()
    assert counted['by_provider']['yolo_bytetrack']['camera_local_occurrences']==1
    assert counted['by_provider']['yolo_bytetrack']['annotated_frames']==12
    assert counted['by_provider']['caviar_ground_truth']['camera_local_occurrences']==2
    before=client.get('/api/site/quality/summary',headers=county).json()
    assert before['by_provider']['yolo_bytetrack']['not_reviewed']==1
    route_qa='/api/site/occurrences/'+item['occurrence_key']+'/quality'
    verdict=client.post(route_qa,headers=county,json={
        'verdict':'valid_local_target','reason':'研究模型局部目标框人工确认有效'})
    assert verdict.status_code==201,verdict.text
    assert verdict.json()['quality_status']=='valid_local_target'
    assert client.get('/api/site/quality/summary',headers=county).json()[
        'by_provider']['yolo_bytetrack']['valid_local_target']==1
    # When the actual stored tracker geometry changes, QA must become stale.
    with main.db() as c:
        c.execute("""UPDATE model_track_boxes SET x=x+1 WHERE run_key=?
             AND frame_number=6""",(out.json()['run_key'],))
    old=client.get(route_qa,headers=county).json()
    assert old['quality_status']=='stale_review'
    assert old['review_stale'] is True
    with main.db() as c:
        assert c.execute('SELECT COUNT(*) FROM model_track_runs').fetchone()[0]==1
        assert c.execute('SELECT COUNT(*) FROM model_track_boxes').fetchone()[0]==12
        assert c.execute('SELECT COUNT(*) FROM public_cctv_track_boxes').fetchone()[0]==4
        assert c.execute('SELECT COUNT(*) FROM synthetic_observations').fetchone()[0]==0
        assert c.execute('SELECT COUNT(*) FROM research_occurrence_links').fetchone()[0]==0


def test_yolo_vs_published_boxes_iou50_is_one_to_one_and_not_identity():
    from app.person_detection_adapter import count_iou50_matches
    gt=[[0,0,100,100],[200,0,300,100]]
    assert count_iou50_matches(gt,gt)=={'tp':2,'fp':0,'fn':0}
    assert count_iou50_matches([gt[0]],gt)=={'tp':1,'fp':0,'fn':1}
    # Two identical model boxes must never count as two separate GT people.
    assert count_iou50_matches([gt[0],gt[0]],gt)=={'tp':1,'fp':1,'fn':1}
    assert count_iou50_matches([],gt)=={'tp':0,'fp':0,'fn':2}
    assert count_iou50_matches(gt,[])=={'tp':0,'fp':2,'fn':0}
    assert count_iou50_matches([],[])=={'tp':0,'fp':0,'fn':0}
    assert count_iou50_matches([[0,0,10,10]],[[50,50,60,60]])=={
        'tp':0,'fp':1,'fn':1}
