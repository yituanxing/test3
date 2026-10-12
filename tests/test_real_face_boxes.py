"""P0 real face geometry: actual OpenCV inference -> persistent boxes -> scoped API.

A coordinate-only quality heuristic never amounts to biometric identity.
"""
import json
from pathlib import Path
import numpy as np
import cv2
from fastapi.testclient import TestClient
from app.detector import choose_detector,box_quality


def test_actual_opencv_face_detector_returns_rectangles_not_fictional_people():
    model=choose_detector('haar')
    blank=np.zeros((360,640,3),dtype=np.uint8)
    assert model.detect(blank)==[]
    assert model.detect_count(blank)==0
    # Actual decoder / detector on the bundled public astronaut sample:
    footage=Path(__file__).resolve().parents[1]/'samples'/'astronaut-face-test.mp4'
    cap=cv2.VideoCapture(str(footage))
    assert cap.isOpened()
    saw=0
    decoded=0
    while decoded<35:
        ok,frame=cap.read()
        if not ok: break
        decoded+=1
        faces=model.detect(frame)
        saw+=len(faces)
        for b in faces:
            assert 0<=b['x']<frame.shape[1] and 0<=b['y']<frame.shape[0]
            assert b['w']>0 and b['x']+b['w']<=frame.shape[1]
            assert b['h']>0 and b['y']+b['h']<=frame.shape[0]
            qual=box_quality(frame,b)
            assert qual['assessment']=='uncalibrated_heuristic'
            assert qual['face_area_px']==b['w']*b['h']
    cap.release()
    assert decoded>=10,'must analyze real frames, not skip the model'
    # Do not invent a high accuracy metric for the detector without annotated
    # frame-level ground truth. The persistence contract is tested below.


def test_face_box_quality_uses_actual_pixels_and_is_not_accuracy():
    frame=np.full((120,120,3),120,dtype=np.uint8)
    low=box_quality(frame,{'x':20,'y':20,'w':70,'h':70})
    assert low['quality_state']=='needs_review'
    assert low['sharpness_laplacian_var']==0.0
    noise=np.random.default_rng(37).integers(0,256,size=(120,120,3),dtype=np.uint8)
    high=box_quality(noise,{'x':20,'y':20,'w':70,'h':70})
    assert high['sharpness_laplacian_var']>low['sharpness_laplacian_var']
    assert high['assessment']=='uncalibrated_heuristic'
    import pytest
    with pytest.raises(ValueError):
        box_quality(frame,{'x':100,'y':50,'w':40,'h':40})


def test_sqlite_samples_correlate_with_actual_boxes_and_authenticated_scope(tmp_path,monkeypatch):
    import app.main as main
    from app import access
    monkeypatch.delenv('ZD_AUTH_TEST_COMPAT',raising=False)
    monkeypatch.setattr(main,'DB',tmp_path/'frameboxes.sqlite3')
    main.setup_db()
    client=TestClient(main.app)
    with main.db() as c:
        root=c.execute('SELECT id FROM regions WHERE level=1 LIMIT 1').fetchone()[0]
        leaf=c.execute('SELECT id FROM regions WHERE level=4 LIMIT 1').fetchone()[0]
        b=c.execute('INSERT INTO regions(name,parent_id,level) VALUES(?,?,2)',('录屏异地县',root)).lastrowid
        st=c.execute('INSERT INTO regions(name,parent_id,level) VALUES(?,?,3)',('录屏异地街道',b)).lastrowid
        ext=c.execute('INSERT INTO regions(name,parent_id,level) VALUES(?,?,4)',('录屏异地社区',st)).lastrowid
        x=c.execute("INSERT INTO places(name,region_id) VALUES(?,?)",('录屏异地场所',ext)).lastrowid
        access.create_user(c,'videoroot','video-test-password-2026',root)
        access.create_user(c,'videoleaf','video-test-password-2026',leaf)
    def login(who):
        result=client.post('/api/auth/login',json={'username':who,'password':'video-test-password-2026'})
        assert result.status_code==200
        return {'Authorization':'Bearer '+result.json()['access_token']}
    admin=login('videoroot'); leaf_headers=login('videoleaf')
    path=Path(__file__).resolve().parents[1]/'samples'/'astronaut-face-test.mp4'
    src=client.post('/api/sources',json={'name':'受控视频解析','kind':'file',
                  'place_id':x,'uri':str(path)},headers=admin)
    assert src.status_code==200,src.text
    sid=src.json()['id']
    assert client.get('/api/video/observations',headers={}).status_code==401
    assert client.get(f'/api/video/observations?source_id={sid}',headers=leaf_headers).status_code==403
    analyzed=client.post(f'/api/sources/{sid}/process?max_frames=45&sample_every=5',headers=admin)
    assert analyzed.status_code==200,analyzed.text
    data=analyzed.json()
    assert data['frames_analyzed']==9
    assert data['structured_boxes_saved']==data['face_detections']
    facts=client.get(f'/api/video/observations?source_id={sid}',headers=admin)
    assert facts.status_code==200,facts.text
    body=facts.json()
    assert body['counts']['sampled_frames']==data['frames_analyzed']
    assert body['counts']['face_boxes']==data['face_detections']
    assert all(z['source_id']==sid and z['place_id']==x for z in body['observations'])
    assert all(sum(1 for b in z['boxes'])==z['detected_faces'] for z in body['observations'])
    assert all('identity' not in b and b['quality_assessment']=='uncalibrated_heuristic'
               for z in body['observations'] for b in z['boxes'])
    with main.db() as c:
        recorded=c.execute('SELECT count(*) FROM face_boxes').fetchone()[0]
        assert recorded==data['face_detections']
