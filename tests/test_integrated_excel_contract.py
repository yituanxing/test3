"""Single-DB vertical acceptance: VIDEO->LOCAL EVENT->QA alongside synthetic
ROSTER->RULES->INSIGHTS. This checks the joint system contract, NOT fake
cross-camera person identification or offline GB28181 protocol certification.
"""
from datetime import datetime, timezone
from unittest.mock import Mock, patch

import cv2
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient


@pytest.fixture
def system(tmp_path,monkeypatch):
    from app import main, access
    monkeypatch.delenv('ZD_AUTH_TEST_COMPAT',raising=False)
    monkeypatch.setattr(main,'DB',tmp_path/'integrated-real-and-synthetic.sqlite3')
    main.setup_db()
    with main.db() as c:
        root=c.execute('SELECT id FROM regions WHERE level=1 LIMIT 1').fetchone()['id']
        county=c.execute('SELECT id FROM regions WHERE level=2 LIMIT 1').fetchone()['id']
        far=c.execute('INSERT INTO regions(name,parent_id,level) VALUES(?,?,2)',
                      ('另一个隔离县',root)).lastrowid
        place=c.execute("SELECT id FROM places WHERE name='测试场所 A'").fetchone()['id']
        second=c.execute("SELECT id FROM places WHERE name='测试场所 B'").fetchone()['id']
        access.create_user(c,'verticalstate','vertical-state-password-2026',root)
        access.create_user(c,'verticalcounty','vertical-county-password-2026',county)
        access.create_user(c,'verticalremote','vertical-remote-password-2026',far)
    client=TestClient(main.app)
    def auth(name,password):
        r=client.post('/api/auth/login',json={'username':name,'password':password})
        assert r.status_code==200,r.text
        return {'Authorization':'Bearer '+r.json()['access_token']}
    return (main,client,place,second,
        auth('verticalstate','vertical-state-password-2026'),
        auth('verticalcounty','vertical-county-password-2026'),
        auth('verticalremote','vertical-remote-password-2026'))


def test_one_database_real_observations_through_qa_and_synthetic_business_isolation(system):
    """End-to-end cross-module assertions with ACTUAL local video decoding.

    Frigate HTTP is deliberately mocked in this test; independent CI's real
    detector/GB/CAVIAR media validations remain separate evidence.
    """
    main,client,place,other_place,state,county,remote=system

    # Excel #20: one venue has many zones, one zone has many camera sources.
    zone=client.post('/api/site/zones',headers=county,json={
        'place_id':place,'name':'单机位验收通道','category':'corridor'})
    assert zone.status_code==201,zone.text
    zone_id=zone.json()['id']
    media=str(main.ROOT/'samples'/'astronaut-face-test.mp4')
    result=client.post('/api/sources',headers=county,json={
        'place_id':place,'name':'实际可解码研究样片','kind':'file','uri':media})
    assert result.status_code==200,result.text
    sid=result.json()['id']
    assigned=client.put(f'/api/site/sources/{sid}/binding',headers=county,json={
        'place_id':place,'zone_id':zone_id,'reason':'现场确认机位属于走廊'})
    assert assigned.status_code==200,assigned.text

    # Real FFmpeg/OpenCV reads bytes; this test does not claim these are a
    # licensed CAVIAR feed or that a Haar face box is an identity.
    processed=client.post(f'/api/sources/{sid}/process?max_frames=16&sample_every=4',
                          headers=state)
    assert processed.status_code==200,processed.text
    values=processed.json()
    assert values['frames_decoded']>=4 and values['new_samples']>=1
    assert values['source_revision'].startswith('sha256:')
    again=client.post(f'/api/sources/{sid}/process?max_frames=16&sample_every=4',
                      headers=state)
    assert again.status_code==200,again.text
    assert again.json()['new_samples']==0 and again.json()['previously_imported']==values['new_samples']
    with main.db() as c:
        saved=[dict(x) for x in c.execute('SELECT * FROM samples WHERE source_id=?',(sid,))]
        assert len(saved)==values['new_samples']
        assert all(x['place_id']==place and x['origin']=='video' for x in saved)
        assert c.execute('SELECT COUNT(*) FROM synthetic_observations').fetchone()[0]==0

    # Frigate records are *anonymous local camera events*. Same local event id
    # in two distinct cameras is not one real-world person.
    for camera in ('school_door_one','school_door_two'):
        bound=client.post('/api/frigate/cameras',headers=state,json={
            'camera':camera,'place_id':place})
        assert bound.status_code==200,bound.text
    event_time=datetime.now(timezone.utc).timestamp()-40
    raw=[{'id':'local-track-1','camera':camera,'label':'person',
          'start_time':event_time,'false_positive':False,
          'data':{'top_score':0.87},'has_snapshot':True}
         for camera in ('school_door_one','school_door_two')]
    response=Mock()
    response.raise_for_status.return_value=None
    response.json.return_value=raw
    with patch('app.main.httpx.get',return_value=response):
        synced=client.post('/api/frigate/sync',headers=state)
        assert synced.status_code==200,synced.text
        assert synced.json()['inserted']==2
        repeat=client.post('/api/frigate/sync',headers=state)
        assert repeat.status_code==200 and repeat.json()['inserted']==0
        assert repeat.json()['updated']==2
    local=client.get('/api/site/occurrences',headers=county).json()['occurrences']
    tracks=[r for r in local if r['provider']=='frigate']
    assert len(tracks)==2
    assert len({r['occurrence_key'] for r in tracks})==2
    assert {r['venue_id'] for r in tracks}=={place}
    assert all(r['subject_id'] is None and not r['counts_as_visit'] for r in tracks)

    # QA validates only a camera-local target, and it has an evidence signature.
    key=tracks[0]['occurrence_key']
    quality=client.post('/api/site/occurrences/'+key+'/quality',headers=county,json={
        'verdict':'valid_local_target','reason':'按上游来源检查局部目标和证据'})
    assert quality.status_code==201,quality.text
    assert quality.json()['identity_confirmed'] is False
    assert quality.json()['valid_site_visit'] is False
    assert client.get('/api/site/quality/summary',headers=county).json()['counts']['valid_local_target']==1

    # Cross-boundary denial and the three different measuring units must agree.
    assert client.get('/api/site/occurrences',headers=remote).json()['occurrences']==[]
    monitor=client.get('/api/monitoring/overview',headers=county).json()
    assert monitor['counts']['person_objects']==2
    assert monitor['counts']['sampled_frames']==values['new_samples']
    scoped=client.get('/api/site/occurrences/summary',headers=county).json()
    assert scoped['by_provider']['frigate']['camera_local_occurrences']==2
    assert scoped['counts_unique_people'] is False
    assert scoped['counts_site_visits'] is False
    assert client.get('/api/monitoring/overview',headers=remote).json()['counts']['person_objects']==0

    # Then exercise the *existing* Excel #3 / #6–12 / #4/5/15/18/19
    # synthetic workflow without copying or mutating any physical-video data.
    seeded=client.post('/api/business/seed',headers=state)
    assert seeded.status_code==200,seeded.text
    assert seeded.json()['mode']=='synthetic_only'
    draft=client.post('/api/base/generate',headers=state,json={
        'days':7,'strategy':'frequency','correction_mode':'rebuild'})
    assert draft.status_code==200,draft.text
    rid=draft.json()['run']['id']
    published=client.post(f'/api/base/runs/{rid}/publish',headers=state)
    assert published.status_code==200,published.text
    assert published.json()['run']['state']=='published'
    analysis=client.get('/api/business/analysis',headers=state).json()
    assert analysis['registry_status']['state']=='published'
    assert analysis['visit_count']>0 and analysis['alerts']
    assert all(a['people'] and a['place_ids'] for a in analysis['alerts'])

    # Excel #22: real workflow uses the same old seven-rule evaluator and
    # published synthetic roster, never the anonymous Frigate track IDs.
    rule=client.post('/api/business/rule-instances',headers=state,json={
        'type':'appearance','name':'完整验收的虚构人员出现规则',
        'enabled':True,'parameters':{},
        'target_place_ids':[other_place],
        'target_synthetic_people':['P001']})
    assert rule.status_code==201,rule.text
    rule_id=rule.json()['id']
    ruled=client.get('/api/business/analysis',headers=state).json()
    hits=[a for a in ruled['alerts']
          if a['type']=='appearance' and a.get('rule_instance_id')==rule_id]
    assert hits and all(a['people']==['P001'] and other_place in a['place_ids']
                        for a in hits)
    check=client.post('/api/business/alerts/'+hits[0]['key']+'/review',
        headers=state,json={'decision':'confirmed',
                            'reason':'依据明确的P001合成到访及规则实例核对'})
    assert check.status_code==200,check.text
    assert next(a for a in client.get('/api/business/analysis',headers=state).json()['alerts']
                if a['key']==hits[0]['key'])['status']=='confirmed'
    assert not any('local-track-1' in str(x) for x in ruled['alerts'])

    insight=client.get('/api/insights/overview?days=7',headers=county)
    assert insight.status_code==200,insight.text
    model=insight.json()
    assert model['synthetic']['mode']=='synthetic_only'
    assert model['synthetic']['cards']['synthetic_visits']>0
    assert model['real_video']['counts']==monitor['counts']

    with main.db() as c:
        assert c.execute('SELECT COUNT(*) FROM samples WHERE source_id=?',
                         (sid,)).fetchone()[0]==len(saved)
        assert c.execute('SELECT COUNT(*) FROM frigate_object_events').fetchone()[0]==2
        assert c.execute('SELECT COUNT(*) FROM anonymous_occurrence_reviews').fetchone()[0]==1
        assert c.execute('SELECT COUNT(*) FROM synthetic_registry_runs WHERE state=?',
                         ('published',)).fetchone()[0]==1
        assert c.execute('SELECT COUNT(*) FROM synthetic_observations').fetchone()[0]>0
    # Fictional people and detected anonymous camera tracks are distinct IDs.
    assert all(x['person'].startswith('P') for x in
               client.get('/api/insights/people',headers=county).json()['people'])
    assert client.get('/api/insights/people',headers=remote).json()['people']==[]


def test_source_changes_during_decode_are_not_saved_as_evidence(system):
    """An in-flight rebind must reject the entire batch, not misassign frames."""
    main,client,place,new_place,state,county,remote=system
    media=str(main.ROOT/'samples'/'astronaut-face-test.mp4')
    src=client.post('/api/sources',headers=state,json={
        'place_id':place,'name':'摄像头重绑并发防护','kind':'file','uri':media})
    assert src.status_code==200,src.text
    sid=src.json()['id']
    class RebindOnFrame:
        name='test-trigger-not-a-real-detector'
        moved=False
        def detect(self,frame):
            if not self.moved:
                with main.db() as c:
                    c.execute('UPDATE sources SET place_id=? WHERE id=?',(new_place,sid))
                self.moved=True
            return []
    cap=cv2.VideoCapture(media)
    assert cap.isOpened()
    try:
        with pytest.raises(HTTPException) as exc:
            main._process_source_impl(sid,max_frames=8,sample_every=2,
                                     _capture=cap,_detector=RebindOnFrame())
        assert exc.value.status_code==409
    finally:
        cap.release()
    with main.db() as c:
        assert c.execute('SELECT COUNT(*) FROM samples WHERE source_id=?',
                         (sid,)).fetchone()[0]==0
