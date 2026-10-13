"""Excel #13/#16: real video -> durable boxes -> searchable redacted evidence.

Checks actual OpenCV decoding and revisions in SQLite; a searchable detector
box is NEVER a face embedding, unique human, biometric match, or person ID.
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from fastapi.testclient import TestClient


def test_scoped_real_face_frame_search_review_paging_and_utc(tmp_path,monkeypatch):
    from app import main,access
    monkeypatch.delenv('ZD_AUTH_TEST_COMPAT',raising=False)
    monkeypatch.setattr(main,'DB',tmp_path/'real-capture-search.sqlite3')
    main.setup_db()
    client=TestClient(main.app)
    with main.db() as c:
        root=c.execute('SELECT id FROM regions WHERE level=1 LIMIT 1').fetchone()['id']
        local=c.execute('SELECT id FROM regions WHERE level=2 LIMIT 1').fetchone()['id']
        foreign=c.execute('INSERT INTO regions(name,parent_id,level) VALUES(?,?,2)',
                          ('搜索隔离县',root)).lastrowid
        st=c.execute('INSERT INTO regions(name,parent_id,level) VALUES(?,?,3)',
                     ('搜索隔离街',foreign)).lastrowid
        leaf=c.execute('INSERT INTO regions(name,parent_id,level) VALUES(?,?,4)',
                       ('搜索隔离社区',st)).lastrowid
        p0=c.execute("SELECT id FROM places WHERE name='测试场所 A'").fetchone()['id']
        p1=c.execute("SELECT id FROM places WHERE name='测试场所 B'").fetchone()['id']
        foreign_place=c.execute('INSERT INTO places(name,region_id) VALUES(?,?)',
                                ('搜索隔离校门',leaf)).lastrowid
        access.create_user(c,'searchroot','search-root-password-2026',root)
        access.create_user(c,'searchcounty','search-county-password-2026',local)
        access.create_user(c,'searchremote','search-remote-password-2026',foreign)
    def login(user,pw):
        r=client.post('/api/auth/login',json={'username':user,'password':pw})
        assert r.status_code==200,r.text
        return {'Authorization':'Bearer '+r.json()['access_token']}
    state=login('searchroot','search-root-password-2026')
    county=login('searchcounty','search-county-password-2026')
    remote=login('searchremote','search-remote-password-2026')
    clip=Path(__file__).resolve().parents[1]/'samples'/'astronaut-face-test.mp4'
    assert clip.is_file()
    def make(place,name,count):
        response=client.post('/api/sources',headers=state,json={
            'place_id':place,'name':name,'kind':'file','uri':str(clip)})
        assert response.status_code==200,response.text
        sid=response.json()['id']
        scanned=client.post(f'/api/sources/{sid}/process?max_frames={count}&sample_every=1',
                            headers=state)
        assert scanned.status_code==200,scanned.text
        assert scanned.json()['new_samples']==count
        return sid,scanned.json()
    a,detection=make(p0,'视频证据搜A',10)
    b,_=make(p1,'视频证据搜B',3)
    f,_=make(foreign_place,'搜索隔离样片',3)
    url='/api/video/observations'
    assert client.get(url).status_code==401
    all_local=client.get(url+'?limit=3',headers=county)
    assert all_local.status_code==200,all_local.text
    payload=all_local.json()
    assert payload['matched_total']==13 and payload['counts']['sampled_frames']==3
    assert payload['next_offset']==3
    assert payload['quality_totals']['sampled_frame_records']==13
    ids=[]
    for offset in (0,3,6,9,12):
        page=client.get(url+f'?limit=3&offset={offset}',headers=county).json()
        ids.extend(x['id'] for x in page['observations'])
    assert len(ids)==13 and len(set(ids))==13
    assert client.get(url+'?limit=5&offset=20',headers=county).json()['observations']==[]
    arows=client.get(url+f'?source_id={a}&place_id={p0}&limit=50',headers=county)
    assert arows.status_code==200,arows.text
    assert arows.json()['matched_total']==10
    assert {x['place_id'] for x in arows.json()['observations']}=={p0}
    assert client.get(url+f'?source_id={a}&place_id={p1}',headers=county).json()['matched_total']==0
    assert client.get(url+f'?place_id={p1}',headers=county).json()['matched_total']==3
    assert client.get(url+f'?place_id={foreign_place}',headers=county).status_code==403
    assert client.get(url+f'?source_id={f}',headers=county).status_code==403
    assert client.get(url+'?limit=50',headers=remote).json()['matched_total']==3

    # A real detector can produce a positive face detection; select ONLY a
    # frame with durable geometric face-box evidence, not an invented face.
    positives=client.get(url+f'?source_id={a}&only_with_boxes=true&limit=50',
                         headers=county)
    assert positives.status_code==200,positives.text
    found=positives.json()['observations']
    assert found and found[0]['boxes']
    assert positives.json()['matched_total']==len(found)
    assert len(found)<=10
    assert all(row['boxes'] for row in found)
    frame=found[0]
    box=frame['boxes'][0]
    sample_id=frame['id']
    picture=client.get(f'/api/video/observations/{sample_id}/preview',headers=county)
    assert picture.status_code==200
    assert picture.headers['content-type']=='image/jpeg'
    assert picture.headers['cache-control']=='no-store, private'
    assert client.get(f'/api/video/observations/{sample_id}/preview',headers=remote).status_code==403
    ref=frame['source_revision']
    assert ref.startswith('sha256:') and ref==detection['source_revision']
    review=f'/api/video/observations/{sample_id}/boxes/{box["id"]}/review'
    assert client.post(review,headers=state,json={
        'decision':'false_positive','reason':'实际视频几何框人工待排除'}).status_code==200
    narrowed=client.get(url+f'?source_id={a}&review_decision=false_positive',
                        headers=county)
    assert narrowed.status_code==200,narrowed.text
    assert narrowed.json()['matched_total']==1
    assert narrowed.json()['observations'][0]['id']==sample_id
    assert narrowed.json()['quality_total_scope']=='authorized_sources_all_time_unfiltered'
    assert narrowed.json()['quality_totals']['sampled_frame_records']==10
    assert client.get(url+'?review_decision=madeup',headers=county).status_code==422
    assert client.post(review,headers=state,json={
        'decision':'unreviewed','reason':'恢复待审并保留审核历史'}).status_code==200
    assert client.get(url+f'?source_id={a}&review_decision=false_positive',
                      headers=county).json()['matched_total']==0
    pending=client.get(url+f'?source_id={a}&review_decision=unreviewed',
                       headers=county).json()
    assert pending['matched_total']==len(found)

    timestamp=datetime.fromisoformat(frame['ingested_at']).astimezone(timezone.utc)
    # Compare instants, including a non-UTC client timezone, even when the
    # stored SQLite ingestion timestamp omitted microsecond precision.
    from_utc=timestamp.astimezone(timezone(timedelta(hours=8))).isoformat()
    until=(timestamp+timedelta(seconds=1)).isoformat()
    import urllib.parse
    query='?source_id='+str(a)+'&from_utc='+urllib.parse.quote(from_utc)+(
        '&until_utc='+urllib.parse.quote(until))
    in_range=client.get(url+query,headers=county)
    assert in_range.status_code==200,in_range.text
    assert in_range.json()['matched_total']==10
    old=(timestamp-timedelta(days=1)).isoformat()
    before=client.get(url+f'?source_id={a}&until_utc='+urllib.parse.quote(old),
                      headers=county)
    assert before.status_code==200 and before.json()['matched_total']==0
    assert client.get(url+'?from_utc=2026-10-13T11:00:00',
                      headers=county).status_code==422
    assert client.get(url+'?from_utc='+urllib.parse.quote(until)+
                      '&until_utc='+urllib.parse.quote(old),headers=county).status_code==422
    # No video face box can enter fictional subject P### history.
    assert client.get('/api/business/analysis',headers=state).json()['person_count']==0
