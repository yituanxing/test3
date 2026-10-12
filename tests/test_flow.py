from pathlib import Path
import pytest
from fastapi.testclient import TestClient
import app.main as main

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(main, 'DB', tmp_path / 'isolated_flow.sqlite3')
    main.setup_db()
    return TestClient(main.app)

def test_end_to_end_review_rule(client):
    assert client.get('/api/health').status_code == 200
    assert client.post('/api/demo/seed').json()['inserted'] == 13
    x = client.get('/api/summary').json()
    a = next(p for p in x['places'] if p['place_name']=='测试场所 A')
    assert a['simulated_samples'] == 7
    assert a['threshold_hit_frames'] == 4
    records=client.get('/api/events').json()
    first=next(x for x in records if x['place_name']=='测试场所 A' and x['detected_faces']==4)
    assert client.post(f"/api/events/{first['id']}/review", json={'corrected_faces':0,'reason':'test correction'}).status_code == 200
    after=client.get('/api/summary').json()
    b=next(p for p in after['places'] if p['place_name']=='测试场所 A')
    assert b['model_face_detections']==a['model_face_detections']
    assert b['effective_face_detections']==a['effective_face_detections']-4
    assert b['threshold_hit_frames']==a['threshold_hit_frames']-1
    assert client.put('/api/rules',json={'face_frame_threshold':3}).status_code == 200
    c=next(p for p in client.get('/api/summary').json()['places'] if p['place_name']=='测试场所 A')
    assert c['threshold_hit_frames']==1


def test_file_video_ingestion(client, tmp_path):
    import cv2, numpy as np
    video=tmp_path/'test.avi'
    writer=cv2.VideoWriter(str(video),cv2.VideoWriter_fourcc(*'MJPG'),10.0,(320,240))
    assert writer.isOpened(), 'OpenCV in this runtime lacks MJPG video encoder'
    for i in range(12):
        frame=np.zeros((240,320,3),np.uint8)
        cv2.putText(frame,f'FRAME {i:02d}',(30,80),cv2.FONT_HERSHEY_SIMPLEX,1,(255,255,255),2)
        writer.write(frame)
    writer.release()
    src=client.post('/api/sources',json={'place_id':1,'name':'Automated AVI','kind':'file','uri':str(video)})
    assert src.status_code == 200,src.text
    got=client.post(f"/api/sources/{src.json()['id']}/process?max_frames=12&sample_every=3")
    assert got.status_code == 200,got.text
    assert got.json()['frames_decoded']==12
    assert got.json()['frames_analyzed']==4
    summary=next(p for p in client.get('/api/summary').json()['places'] if p['place_id']==1)
    assert summary['real_video_samples']==4


def test_bundled_mp4_true_decode_pipeline(client):
    """Actual video file -> OpenCV detection -> persistent SQL rows -> summary."""
    src = next((x for x in client.get('/api/sources').json()
                if x['name'] == '内置测试视频（无真人）'), None)
    assert src is not None
    place_id = src['place_id']
    old = next(p for p in client.get('/api/summary').json()['places']
               if p['place_id'] == place_id)
    result = client.post(f"/api/sources/{src['id']}/process?max_frames=80&sample_every=10")
    assert result.status_code == 200, result.text
    assert result.json()['frames_decoded'] == 80
    assert result.json()['frames_analyzed'] == 8
    new = next(p for p in client.get('/api/summary').json()['places']
               if p['place_id'] == place_id)
    assert new['real_video_samples'] == old['real_video_samples'] + 8
    assert new['sampled_frames'] == old['sampled_frames'] + 8
