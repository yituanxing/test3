"""P0-1 source-file replay and provenance tests using REAL decoded AVI frames.

No network, mocked inference, fake people or identity linking. These are strict
business-counting and evidence-integrity contracts, not precision estimates.
"""
import cv2
import numpy as np
from fastapi.testclient import TestClient


def _write_clip(path, label):
    writer=cv2.VideoWriter(str(path),cv2.VideoWriter_fourcc(*'MJPG'),10.0,(320,240))
    assert writer.isOpened()
    for i in range(12):
        frame=np.zeros((240,320,3),dtype=np.uint8)
        cv2.putText(frame,f'{label}-{i:02d}',(22,88),cv2.FONT_HERSHEY_SIMPLEX,
                    1.25,(240,240,240),2)
        writer.write(frame)
    writer.release()
    assert path.stat().st_size>1024


def test_same_file_replayed_and_resampled_does_not_inflate_counts(tmp_path,monkeypatch):
    import app.main as main
    monkeypatch.setattr(main,'DB',tmp_path/'file-replay.sqlite3')
    main.setup_db()
    cli=TestClient(main.app)
    video=tmp_path/'replayed.avi'
    _write_clip(video,'VERSION-A')
    src=cli.post('/api/sources',json={
        'place_id':1,'name':'file-import-real','kind':'file','uri':str(video)})
    assert src.status_code==200,src.text
    sid=src.json()['id']
    url=f'/api/sources/{sid}/process'
    first=cli.post(url+'?max_frames=12&sample_every=3')
    assert first.status_code==200,first.text
    data=first.json()
    assert data['frames_analyzed']==4 and data['new_samples']==4
    assert data['previously_imported']==0
    assert data['source_revision'].startswith('sha256:')
    second=cli.post(url+'?max_frames=12&sample_every=3')
    assert second.status_code==200,second.text
    assert second.json()['frames_analyzed']==4  # true inference still ran
    assert second.json()['new_samples']==0
    assert second.json()['previously_imported']==4
    assert second.json()['structured_boxes_saved']==0
    with main.db() as c:
        assert c.execute("SELECT count(*) FROM samples WHERE source_id=?",(sid,)).fetchone()[0]==4
        assert c.execute("SELECT count(*) FROM face_boxes b JOIN samples s ON s.id=b.sample_id WHERE s.source_id=?",(sid,)).fetchone()[0]==data['structured_boxes_saved']
    # Different sampling rate: only unseen frames enter; overlapping frames
    # are not counted twice even across distinct user analysis requests.
    overlapping=cli.post(url+'?max_frames=12&sample_every=2')
    assert overlapping.status_code==200,overlapping.text
    assert overlapping.json()['new_samples']==4
    assert overlapping.json()['previously_imported']==2
    facts=cli.get(f'/api/video/observations?source_id={sid}&limit=100')
    assert facts.status_code==200
    assert facts.json()['counts']['sampled_frames']==8
    assert {f['frame_index'] for f in facts.json()['observations']}=={1,3,4,5,7,9,10,11}
    assert all(f['source_revision']==data['source_revision'] for f in facts.json()['observations'])
    # Simulates service restart: schema migration is repeatable and doesn't
    # create/alter any historical video facts.
    main.setup_db()
    assert cli.post(url+'?max_frames=12&sample_every=3').json()['new_samples']==0


def test_source_file_replacement_has_distinct_revision_and_fails_old_preview(tmp_path,monkeypatch):
    import app.main as main
    monkeypatch.setattr(main,'DB',tmp_path/'file-replaced.sqlite3')
    main.setup_db()
    cli=TestClient(main.app)
    video=tmp_path/'mutating.avi'
    _write_clip(video,'OLD')
    src=cli.post('/api/sources',json={
        'place_id':1,'name':'file-replaced-real','kind':'file','uri':str(video)})
    assert src.status_code==200
    sid=src.json()['id']
    first=cli.post(f'/api/sources/{sid}/process?max_frames=12&sample_every=3')
    assert first.status_code==200
    old_revision=first.json()['source_revision']
    old_id=cli.get(f'/api/video/observations?source_id={sid}').json()['observations'][0]['id']
    original=cli.get(f'/api/video/observations/{old_id}/preview')
    assert original.status_code==200
    # Same PATH but different pixel content; old evidence must be rejected,
    # never silently rendered from the replacement file.
    _write_clip(video,'REPLACEMENT')
    mismatch=cli.get(f'/api/video/observations/{old_id}/preview')
    assert mismatch.status_code==409,mismatch.text
    assert '内容已变化' in mismatch.json()['detail']
    new=cli.post(f'/api/sources/{sid}/process?max_frames=12&sample_every=3')
    assert new.status_code==200,new.text
    assert new.json()['source_revision']!=old_revision
    assert new.json()['new_samples']==4
    assert new.json()['previously_imported']==0
    with main.db() as c:
        assert c.execute("SELECT count(*) FROM samples WHERE source_id=?",(sid,)).fetchone()[0]==8
    # A newly captured revision can be displayed, without falsifying the old.
    new_id=cli.get(f'/api/video/observations?source_id={sid}').json()['observations'][0]['id']
    assert new_id!=old_id
    assert cli.get(f'/api/video/observations/{new_id}/preview').status_code==200
