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
