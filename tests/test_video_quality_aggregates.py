"""A real video can contain far more frames than the limited evidence page.

This tests ALL-scope quality numbers from actual OpenCV decoding and SQLite
writes, not simulated API payloads or mocked detectors.
"""
from pathlib import Path
from fastapi.testclient import TestClient

from app import main


def test_real_video_quality_totals_cover_all_rows_not_only_latest_page(tmp_path, monkeypatch):
    monkeypatch.setattr(main,'DB',tmp_path/'quality-total.sqlite3')
    main.setup_db()
    client=TestClient(main.app)
    root=Path(__file__).resolve().parents[1]
    video=root/'samples'/'astronaut-face-test.mp4'
    no_faces=root/'samples'/'test-pattern.mp4'
    created=client.post('/api/sources',json={'name':'质量实际正例','place_id':1,
                           'kind':'file','uri':str(video)})
    assert created.status_code==200,created.text
    positive_id=created.json()['id']
    positive=client.post(f'/api/sources/{positive_id}/process?max_frames=45&sample_every=5')
    assert positive.status_code==200,positive.text
    assert positive.json()['new_samples']==9
    assert positive.json()['face_detections']>0

    created=client.post('/api/sources',json={'name':'质量实际阴性','place_id':2,
                           'kind':'file','uri':str(no_faces)})
    assert created.status_code==200,created.text
    negative_id=created.json()['id']
    negative=client.post(f'/api/sources/{negative_id}/process?max_frames=80&sample_every=1')
    assert negative.status_code==200,negative.text
    assert negative.json()['new_samples']==80
    # This abstract test pattern contains NO labeled human face. Haar may
    # still output false positive boxes (currently observed: 5 / 80 frames).
    # Record the actual model result instead of inventing an accuracy claim.
    false_boxes = negative.json()['face_detections']
    assert false_boxes >= 0

    # The screen's latest-four rows are all no-face negative frames; a query
    # limited to four would otherwise hide all positive detection evidence.
    limited=client.get('/api/video/observations?limit=4')
    assert limited.status_code==200,limited.text
    values=limited.json()
    assert values['counts']['sampled_frames']==4
    assert all(row['source_id']==negative_id for row in values['observations'])
    assert values['counts']['face_boxes']==sum(len(row['boxes']) for row in values['observations'])
    assert values['quality_totals']['sampled_frame_records']==89
    assert values['quality_totals']['raw_face_detections']==positive.json()['face_detections']+false_boxes
    assert values['quality_totals']['boxes_with_geometry']==positive.json()['face_detections']+false_boxes
    assert values['quality_totals']['candidate_boxes']+values['quality_totals']['needs_review_boxes']==positive.json()['face_detections']+false_boxes
    # No-face FRAME counts depend on detector outputs; a known-negative
    # scene can contain real classifier false positives.
    assert values['quality_totals']['no_face_frames']==89 - sum(
        row['detected_faces']>0 for row in
        client.get('/api/video/observations?limit=100').json()['observations'])
    assert values['quality_totals']['legacy_detections_without_geometry']==0

    # Source filters must not change their own totals. The "negative" source
    # is known to contain no faces, but all model false alarms remain visible
    # as unverified face boxes rather than being silently dropped.
    pos=client.get(f'/api/video/observations?source_id={positive_id}&limit=2').json()
    assert pos['counts']['sampled_frames']==2
    assert pos['quality_totals']['sampled_frame_records']==9
    assert pos['quality_totals']['boxes_with_geometry']==positive.json()['face_detections']
    neg=client.get(f'/api/video/observations?source_id={negative_id}&limit=2').json()
    assert neg['quality_totals']['sampled_frame_records']==80
    assert neg['quality_totals']['boxes_with_geometry']==false_boxes
    assert neg['quality_totals']['candidate_boxes']+neg['quality_totals']['needs_review_boxes']==false_boxes
    assert neg['quality_totals']['no_face_frames']==80-sum(
        x['detected_faces']>0 for x in
        client.get(f'/api/video/observations?source_id={negative_id}&limit=100').json()['observations'])

    # Idempotent import of unchanged footage cannot inflate evidence totals.
    assert client.post(f'/api/sources/{positive_id}/process?max_frames=45&sample_every=5').json()['new_samples']==0
    assert client.get('/api/video/observations?limit=1').json()['quality_totals']==values['quality_totals']


def test_all_scope_quality_metrics_do_not_leak_cross_region_video(tmp_path,monkeypatch):
    from app import access
    # Existing test suite deliberately enables legacy auth compatibility.
    # This test must exercise the real token-based jurisdiction checks.
    monkeypatch.delenv('ZD_AUTH_TEST_COMPAT', raising=False)
    monkeypatch.setattr(main,'DB',tmp_path/'quality-region.sqlite3')
    main.setup_db()
    with main.db() as conn:
        root=conn.execute('SELECT id FROM regions WHERE level=1 LIMIT 1').fetchone()[0]
        own=conn.execute('SELECT id FROM regions WHERE level=4 LIMIT 1').fetchone()[0]
        alt=conn.execute("INSERT INTO regions(name,parent_id,level) VALUES('质量另县',?,2)",(root,)).lastrowid
        street=conn.execute("INSERT INTO regions(name,parent_id,level) VALUES('质量另街',?,3)",(alt,)).lastrowid
        leaf=conn.execute("INSERT INTO regions(name,parent_id,level) VALUES('质量另社区',?,4)",(street,)).lastrowid
        foreign_place=conn.execute("INSERT INTO places(name,region_id) VALUES('质量另场所',?)",(leaf,)).lastrowid
        access.create_user(conn,'qualityroot','quality-admin-password-2026',root)
        access.create_user(conn,'qualityleaf','quality-admin-password-2026',own)
    client=TestClient(main.app)
    def auth(who):
        response=client.post('/api/auth/login',json={'username':who,
                                 'password':'quality-admin-password-2026'})
        assert response.status_code==200,response.text
        return {'Authorization':'Bearer '+response.json()['access_token']}
    state=auth('qualityroot')
    community=auth('qualityleaf')
    path=Path(__file__).resolve().parents[1]/'samples'/'astronaut-face-test.mp4'
    src=client.post('/api/sources',headers=state,json={
        'name':'不允许泄漏的视频','place_id':foreign_place,'kind':'file','uri':str(path)})
    assert src.status_code==200
    source_id=src.json()['id']
    assert client.post(f'/api/sources/{source_id}/process?max_frames=5&sample_every=5',
                       headers=state).status_code==200
    assert client.get('/api/video/observations?limit=1',headers=state).json()['quality_totals']['sampled_frame_records']==1
    out=client.get('/api/video/observations?limit=1',headers=community)
    assert out.status_code==200
    assert out.json()['quality_totals']['sampled_frame_records']==0
    assert out.json()['quality_totals']['boxes_with_geometry']==0
    assert out.json()['observations']==[]
    assert client.get(f'/api/video/observations?source_id={source_id}',headers=community).status_code==403
