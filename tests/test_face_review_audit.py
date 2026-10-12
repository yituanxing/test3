"""Real video inference -> append-only false-alarm decision -> audited totals.

This checks persistence and scoped API behavior, not the accuracy of the
reviewer's opinion or the identity of any observed person.
"""
from pathlib import Path
from fastapi.testclient import TestClient
from app import main,access


def test_face_box_human_review_preserves_model_evidence_and_can_be_reversed(tmp_path,monkeypatch):
    monkeypatch.delenv('ZD_AUTH_TEST_COMPAT',raising=False)
    monkeypatch.setattr(main,'DB',tmp_path/'face-review.sqlite3')
    main.setup_db()
    client=TestClient(main.app)
    with main.db() as c:
        state=c.execute('SELECT id FROM regions WHERE level=1 LIMIT 1').fetchone()[0]
        leaf=c.execute('SELECT id FROM regions WHERE level=4 LIMIT 1').fetchone()[0]
        access.create_user(c,'auditorstate','local-test-strong-password-2026',state)
        access.create_user(c,'auditorleaf','local-test-strong-password-2026',leaf)
    def auth(who):
        resp=client.post('/api/auth/login',json={'username':who,
                               'password':'local-test-strong-password-2026'})
        assert resp.status_code==200,resp.text
        return {'Authorization':'Bearer '+resp.json()['access_token']}
    state,leaf=auth('auditorstate'),auth('auditorleaf')
    src=client.post('/api/sources',headers=state,json={
        'place_id':1,'kind':'file','name':'NASA授权人脸实际解码',
        'uri':str(Path(__file__).resolve().parents[1]/'samples'/'astronaut-face-test.mp4')})
    assert src.status_code==200,src.text
    sid=src.json()['id']
    result=client.post(f'/api/sources/{sid}/process?max_frames=10&sample_every=5',
                       headers=state)
    assert result.status_code==200,result.text
    assert result.json()['face_detections']>0
    path=f'/api/video/observations?source_id={sid}&limit=1'
    before=client.get(path,headers=state).json()
    counts=before['quality_totals']
    sample=next(row for row in before['observations'] if row['boxes'])
    box=sample['boxes'][0]
    review=f"/api/video/observations/{sample['id']}/boxes/{box['id']}/review"
    payload={'decision':'false_positive','reason':'演示人工状态切换（不对原视频做真假结论）'}
    assert client.post(review,json=payload).status_code==401
    assert client.post(review,json=payload,headers=leaf).status_code==403
    assert client.post(f"/api/video/observations/999999/boxes/{box['id']}/review",
                       json=payload,headers=state).status_code==404
    response=client.post(review,json=payload,headers=state)
    assert response.status_code==200,response.text
    assert response.json()['raw_detection_preserved']
    assert response.json()['history_count']==1
    assert response.json()['reviewer']=='auditorstate'
    changed=client.get(path,headers=state).json()
    q=changed['quality_totals']
    assert q['raw_face_detections']==counts['raw_face_detections']
    assert q['boxes_with_geometry']==counts['boxes_with_geometry']
    assert q['manual_false_positive_boxes']==1
    assert q['not_rejected_boxes']==q['boxes_with_geometry']-1
    assert changed['observations'][0]['boxes'][0]['review_decision']=='false_positive'
    reimport=client.post(f'/api/sources/{sid}/process?max_frames=10&sample_every=5',
                         headers=state)
    assert reimport.json()['new_samples']==0
    assert client.get(path,headers=state).json()['quality_totals']==q
    rescind=client.post(review,json={'decision':'unreviewed','reason':'重新进入待复核'},
                        headers=state)
    assert rescind.status_code==200 and rescind.json()['history_count']==2
    latest=client.get(path,headers=state).json()['quality_totals']
    assert latest['manual_false_positive_boxes']==0
    assert latest['not_rejected_boxes']==counts['boxes_with_geometry']
    with main.db() as c:
        rows=[dict(x) for x in c.execute(
            'SELECT decision,reviewer FROM face_box_reviews WHERE box_id=? ORDER BY id',(box['id'],))]
        assert [x['decision'] for x in rows]==['false_positive','unreviewed']
        assert all(x['reviewer']=='auditorstate' for x in rows)
    main.setup_db()
    assert client.get(path,headers=state).json()['quality_totals']['manual_false_positive_boxes']==0


def test_human_review_metrics_do_not_leak_across_jurisdictions(tmp_path,monkeypatch):
    monkeypatch.delenv('ZD_AUTH_TEST_COMPAT',raising=False)
    monkeypatch.setattr(main,'DB',tmp_path/'face-review-regions.sqlite3')
    main.setup_db()
    client=TestClient(main.app)
    with main.db() as c:
        root=c.execute('SELECT id FROM regions WHERE level=1 LIMIT 1').fetchone()[0]
        local=c.execute('SELECT id FROM regions WHERE level=4 LIMIT 1').fetchone()[0]
        county=c.execute("INSERT INTO regions(name,parent_id,level) VALUES('review-county',?,2)",(root,)).lastrowid
        street=c.execute("INSERT INTO regions(name,parent_id,level) VALUES('review-street',?,3)",(county,)).lastrowid
        far=c.execute("INSERT INTO regions(name,parent_id,level) VALUES('review-community',?,4)",(street,)).lastrowid
        place=c.execute("INSERT INTO places(name,region_id) VALUES('review-other-place',?)",(far,)).lastrowid
        access.create_user(c,'reviewstate','review-password-2026-long',root)
        access.create_user(c,'reviewlocal','review-password-2026-long',local)
    def login(name):
        r=client.post('/api/auth/login',json={'username':name,'password':'review-password-2026-long'})
        assert r.status_code==200,r.text
        return {'Authorization':'Bearer '+r.json()['access_token']}
    root,local=login('reviewstate'),login('reviewlocal')
    src=client.post('/api/sources',headers=root,json={'kind':'file','place_id':place,
        'name':'independent place face geometry','uri':str(
          Path(__file__).resolve().parents[1]/'samples'/'astronaut-face-test.mp4')})
    assert src.status_code==200,src.text
    sid=src.json()['id']
    result=client.post(f'/api/sources/{sid}/process?max_frames=5&sample_every=5',headers=root)
    assert result.status_code==200,result.text
    assert result.json()['face_detections']>0
    sample=client.get(f'/api/video/observations?source_id={sid}',headers=root).json()['observations'][0]
    box=sample['boxes'][0]
    endpoint=f"/api/video/observations/{sample['id']}/boxes/{box['id']}/review"
    r=client.post(endpoint,headers=root,json={
        'decision':'false_positive','reason':'演示复核'})
    assert r.status_code==200,r.text
    limited=client.get('/api/video/observations',headers=local)
    assert limited.status_code==200,limited.text
    assert limited.json()['quality_totals']['manual_false_positive_boxes']==0
    assert limited.json()['quality_totals']['boxes_with_geometry']==0
    assert client.post(endpoint,headers=local,json={
        'decision':'unreviewed','reason':'跨辖区尝试撤销'}).status_code==403
