"""Animation storyboard: real decoded video cuts, human review and scene graph.

Tests use locally generated color clips; NO anime identity model, GPS, maps
or actual-person identity inference is claimed.
"""
from pathlib import Path
import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient


def make_clip(path:Path,frames_each=12):
    writer=cv2.VideoWriter(str(path),cv2.VideoWriter_fourcc(*'MJPG'),12.0,(96,64))
    assert writer.isOpened(),'CI OpenCV MJPG encoder needed for isolated shot test'
    for color in [(0,0,0),(255,255,255),(0,0,255)]:
        for _ in range(frames_each):
            writer.write(np.full((64,96,3),color,dtype=np.uint8))
    writer.release()


@pytest.fixture
def env(monkeypatch,tmp_path):
    from app import main,access
    monkeypatch.delenv('ZD_AUTH_TEST_COMPAT',raising=False)
    monkeypatch.setattr(main,'DB',tmp_path/'anime-scenes.sqlite3')
    main.setup_db()
    from app import anime_scenes
    monkeypatch.setattr(anime_scenes,'_upload_dir',tmp_path/'anime_uploads')
    movie=tmp_path/'fictional-clip.avi'
    make_clip(movie)
    with main.db() as c:
        root=c.execute('SELECT id FROM regions WHERE level=1 LIMIT 1').fetchone()['id']
        county=c.execute('SELECT id FROM regions WHERE level=2 LIMIT 1').fetchone()['id']
        another=c.execute('INSERT INTO regions(name,parent_id,level) VALUES(?,?,2)',('县乙',root)).lastrowid
        street=c.execute('INSERT INTO regions(name,parent_id,level) VALUES(?,?,3)',('乙街',another)).lastrowid
        leaf=c.execute('INSERT INTO regions(name,parent_id,level) VALUES(?,?,4)',('乙社区',street)).lastrowid
        other_place=c.execute('INSERT INTO places(name,region_id) VALUES(?,?)',('乙区测试场所',leaf)).lastrowid
        demo_place=c.execute("SELECT id FROM places WHERE name='测试场所 A'").fetchone()['id']
        source=c.execute("""INSERT INTO sources(place_id,name,kind,uri)
                   VALUES(?,?,?,?)""",(demo_place,'人工绘制颜色切镜视频','file',str(movie))).lastrowid
        source_other=c.execute("""INSERT INTO sources(place_id,name,kind,uri)
                   VALUES(?,?,?,?)""",(other_place,'另一个辖区视频','file',str(movie))).lastrowid
        access.create_user(c,'scenestate','state-strong-password-2026',root)
        access.create_user(c,'scenecounty','county-strong-password-2026',county)
        access.create_user(c,'sceneother','other-strong-password-2026',another)
    client=TestClient(main.app)
    def login(user,pw):
        response=client.post('/api/auth/login',json={'username':user,'password':pw})
        assert response.status_code==200,response.text
        return {'Authorization':'Bearer '+response.json()['access_token']}
    state=login('scenestate','state-strong-password-2026')
    county=login('scenecounty','county-strong-password-2026')
    other=login('sceneother','other-strong-password-2026')
    return main,client,movie,source,source_other,state,county,other


def test_real_video_cut_proposal_then_manual_scene_graph(env):
    main,client,movie,source,other_source,state,county,other=env
    root=f'/api/anime/scenes/sources/{source}'
    assert client.get(root+'/suggest').status_code==401
    assert client.get(root+'/suggest',headers=other).status_code==403
    assert client.get(f'/api/anime/scenes/sources/{other_source}/graph',headers=county).status_code==403
    assert client.get('/anime-scenes').status_code==200
    suggested=client.get(root+'/suggest?sample_stride=1&max_seconds=4&threshold=32',headers=county)
    assert suggested.status_code==200,suggested.text
    result=suggested.json()
    assert result['decoded_frames']==36
    assert len(result['segments'])==3,result
    assert [x['start_ms'] for x in result['segments']]==[0,1000,2000]
    assert all(x['end_ms']>x['start_ms'] for x in result['segments'])
    assert result['mode']=='scene_cut_candidates_only'
    assert result['source_revision']
    assert 'not available' not in str(result)

    for item,label in zip(result['segments'],['动画教室','动画街角','动画教室']):
        response=client.post(root+'/segments',headers=state,json={
            'label':label,'start_ms':item['start_ms'],'end_ms':item['end_ms'],
            'note':'对照帧实际确认，不是GPS'})
        assert response.status_code==201,response.text
    graph=client.get(root+'/graph',headers=county).json()
    assert [x['label'] for x in graph['nodes']]==['动画教室','动画街角']
    assert len(graph['segments'])==3
    assert graph['edges']==[
        {'from_scene':'动画教室','to_scene':'动画街角',
         'cuts':1,'relation':'edit_sequence_only'},
        {'from_scene':'动画街角','to_scene':'动画教室',
         'cuts':1,'relation':'edit_sequence_only'}]
    assert 'latitude' not in str(graph) and 'longitude' not in str(graph)
    assert graph['characters']=={}
    assert client.post(root+'/segments',headers=county,json={
        'label':'越权','start_ms':3500,'end_ms':3800}).status_code==403
    assert client.post(root+'/segments',headers=state,json={
        'label':'重叠','start_ms':900,'end_ms':1500}).status_code==409
    assert client.post(root+'/segments',headers=state,json={
        'label':'逆序','start_ms':2000,'end_ms':1000}).status_code==422


def test_character_timeline_requires_real_frame_evidence_and_content_revision(env):
    main,client,movie,source,other_source,state,county,other=env
    root=f'/api/anime/scenes/sources/{source}'
    seg=[]
    for i,label in [(0,'室内'),(1,'走廊')]:
        response=client.post(root+'/segments',headers=state,json={
            'label':label,'start_ms':i*1000,'end_ms':(i+1)*1000})
        assert response.status_code==201,response.text
        seg.append(response.json()['id'])
    rev=main._file_revision(str(movie))
    with main.db() as c:
        p=c.execute('SELECT place_id FROM sources WHERE id=?',(source,)).fetchone()['place_id']
        def sample(offset,source_id=source,revision=rev):
            row=c.execute("""INSERT INTO samples
                (source_id,place_id,origin,ingested_at,video_offset_ms,frame_index,
                 detected_faces,detector,source_revision)
                VALUES (?,?,?,?,?,?,?,?,?)""",
                (source_id,p,'video',main.utcnow(),offset,offset//83,0,
                 'anime-scene-evidence-test',revision))
            return row.lastrowid
        a=sample(500)
        b=sample(1500)
        outside=sample(2300)
        foreign=sample(500,other_source)
        old=sample(1500,source,'different-content-digest')
    code={'character_code':'C001','sample_id':a,'note':'人工确认虚构角色 C001'}
    def tag(segment,**change):
        return client.post(f'/api/anime/scenes/segments/{segment}/characters',
                           headers=state,json={**code,**change})
    assert tag(seg[0]).status_code==201
    assert tag(seg[0]).status_code==409
    assert tag(seg[1],sample_id=b).status_code==201
    for bad_sample,expected in [(outside,422),(foreign,422),(old,409)]:
        assert tag(seg[1],sample_id=bad_sample).status_code==expected
    assert client.post(f'/api/anime/scenes/segments/{seg[1]}/characters',
            headers=county,json={**code,'sample_id':b}).status_code==403
    result=client.get(root+'/graph',headers=county).json()
    timeline=result['characters']['C001']
    assert [(x['scene_label'],x['sample_id']) for x in timeline]==[
        ('室内',a),('走廊',b)]
    assert all(x['evidence']=='human_tag_of_actual_decoded_frame' for x in timeline)
    assert result['segments'][0]['decoded_sample_count']==1
    assert result['segments'][1]['decoded_sample_count']==1
    assert client.delete(f'/api/anime/scenes/segments/{seg[0]}',headers=state).status_code==409
    make_clip(movie,frames_each=10)
    changed=client.get(root+'/graph',headers=state).json()
    assert changed['has_stale_segments'] is True
    assert changed['characters']=={} and changed['segments']==[]
    assert tag(seg[1],sample_id=b).status_code==409


def test_nonfile_stream_rejected_without_capture_or_leaking_uri(env):
    main,client,movie,source,other_source,state,county,other=env
    with main.db() as c:
        place=c.execute('SELECT place_id FROM sources WHERE id=?',(source,)).fetchone()['place_id']
        stream=c.execute("""INSERT INTO sources(place_id,name,kind,uri)
                   VALUES(?,?,?,?)""",(place,'在线RTSP','rtsp',
                      'rtsp://example.invalid:554/no-stream')).lastrowid
    assert client.get(f'/api/anime/scenes/sources/{stream}/suggest',headers=state).status_code==422
    assert client.get(f'/api/anime/scenes/sources/{stream}/graph',headers=county).status_code==422


def test_raw_video_browser_upload_reuses_real_decode_and_scope(env):
    from app import anime_scenes
    main,client,movie,source,other_source,state,county,other=env
    with main.db() as c:
        place=c.execute('SELECT place_id FROM sources WHERE id=?',(source,)).fetchone()['place_id']
    url=f'/api/anime/scenes/uploads?place_id={place}'
    payload=movie.read_bytes()
    headers={**state,'X-Upload-Name':'cartoon-demo.avi',
             'Content-Type':'application/octet-stream'}
    assert client.post(url,content=payload).status_code==401
    assert client.post(url,headers={**county,'X-Upload-Name':'clip.avi'},
                       content=payload).status_code==403
    no=client.post(url,headers={**state,'X-Upload-Name':'script.py'},
                   content=payload)
    assert no.status_code==422
    fake=client.post(url,headers={**state,'X-Upload-Name':'fake.avi'},content=b'not a video')
    assert fake.status_code==422
    assert not list((anime_scenes._upload_dir).glob('*.avi'))
    uploaded=client.post(url,headers=headers,content=payload)
    assert uploaded.status_code==201,uploaded.text
    data=uploaded.json()
    assert data['kind']=='file' and data['size_bytes']==len(payload)
    assert data['source_revision']==main._file_revision(str(movie))
    with main.db() as c:
        stored=c.execute('SELECT uri,name FROM sources WHERE id=?',(data['id'],)).fetchone()
        assert stored and stored['name']=='上传动画 · cartoon-demo'
        uploaded_path=Path(stored['uri'])
        assert uploaded_path.exists() and uploaded_path.parent==anime_scenes._upload_dir
        assert uploaded_path.read_bytes()==payload
    shots=client.get(f"/api/anime/scenes/sources/{data['id']}/suggest?sample_stride=1&max_seconds=4",
                     headers=state)
    assert shots.status_code==200,shots.text
    assert len(shots.json()['segments'])==3
    summary=client.get(f"/api/anime/scenes/sources/{data['id']}/graph",headers=state)
    assert summary.status_code==200 and summary.json()['characters']=={}
