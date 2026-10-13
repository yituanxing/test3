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
    assert outputs[0]['source_id']!=outputs[1]['source_id']
    assert {x['camera_view'] for x in outputs}=={'front','corridor'}
    with main.db() as c:
        assert c.execute('SELECT COUNT(*) FROM synthetic_observations').fetchone()[0]==0
        assert c.execute('SELECT COUNT(*) FROM research_occurrence_links').fetchone()[0]==0
