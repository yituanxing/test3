"""Consent-only short-lived face similarity laboratory: guarded API contract.

The ranking tests mock image->embedding; they do not claim SFace was run
or that face identity is accurate. Real inference needs separately licensed
and explicitly installed YuNet/SFace models.
"""
import numpy as np
import pytest
from fastapi.testclient import TestClient
from app import access, face_photo_lab
import app.main as m


@pytest.fixture
def secure(tmp_path,monkeypatch):
    monkeypatch.delenv('ZD_AUTH_TEST_COMPAT',raising=False)
    monkeypatch.setattr(m,'DB',tmp_path/'face-lab-secure.sqlite3')
    monkeypatch.setenv('ZD_FACE_LAB_ENABLE','1')
    m.setup_db()
    with face_photo_lab._lock:
        face_photo_lab._storage.clear()
    with m.db() as c:
        state=c.execute('SELECT id FROM regions WHERE level=1 LIMIT 1').fetchone()[0]
        county=c.execute('SELECT id FROM regions WHERE level=2 LIMIT 1').fetchone()[0]
        for user,region in [('faceadmin',state),('facelower',county)]:
            access.create_user(c,user,'strong-test-password-2026',region)
    with TestClient(m.app) as cli:
        def login(user):
            r=cli.post('/api/auth/login',json={'username':user,
                        'password':'strong-test-password-2026'})
            assert r.status_code==200
            return {'Authorization':'Bearer '+r.json()['access_token']}
        yield cli,login
    with face_photo_lab._lock:
        face_photo_lab._storage.clear()


def test_default_requirements_are_auth_and_state_admin_only(secure):
    c,login=secure
    assert c.get('/face-lab').status_code==200
    assert c.get('/api/face-lab/status').status_code==401
    assert c.get('/api/face-lab/status',headers=login('facelower')).status_code==403
    admin=login('faceadmin')
    s=c.get('/api/face-lab/status',headers=admin)
    assert s.status_code==200
    assert s.json()['mode']=='consented_photo_lab_only'
    assert s.json()['ready'] is False # no bundled model weights
    assert s.json()['count']==0
    assert c.post('/api/face-lab/enroll?code=S01',headers={**admin,
        'Content-Type':'image/png','X-Lab-Consent':'yes'},content=b'a'*150).status_code==503


def test_consent_media_code_and_ephemeral_ordered_ranking(secure,monkeypatch):
    c,login=secure
    admin=login('faceadmin')
    def embed(raw):
        # fake embeddings only to prove API ranking, not facial recognition.
        mapping={b'Photo-A':[1,0,0],b'Photo-B':[0,1,0],
                 b'Query-A':[.9,.1,0]}
        return np.asarray(mapping[raw],dtype=np.float32)
    monkeypatch.setattr(face_photo_lab,'_feature',embed)
    url='/api/face-lab/enroll?code=S01'
    assert c.post(url,headers={**admin,'Content-Type':'image/jpeg'},
                  content=b'Photo-A').status_code==403
    assert c.post('/api/face-lab/enroll?code=P001',
                  headers={**admin,'Content-Type':'image/jpeg','X-Lab-Consent':'yes'},
                  content=b'Photo-A').status_code==422
    assert c.post(url,headers={**admin,'Content-Type':'application/pdf',
                  'X-Lab-Consent':'yes'},content=b'Photo-A').status_code==415
    h={**admin,'Content-Type':'image/jpeg','X-Lab-Consent':'yes'}
    assert c.post(url,headers=h,content=b'Photo-A').status_code==200
    assert c.post('/api/face-lab/enroll?code=S02',headers=h,content=b'Photo-B').status_code==200
    r=c.post('/api/face-lab/search',headers=h,content=b'Query-A')
    assert r.status_code==200,r.text
    result=r.json()
    assert [x['sample_code'] for x in result['ranked_candidates']]==['S01','S02']
    assert result['ranked_candidates'][0]['cosine_similarity']>.85
    assert result['unverified_identity'] is True
    assert all(x['identity_confirmed'] is False for x in result['ranked_candidates'])
    assert 'embedding' not in str(result)
    # Different login session, even for same admin, must not see former gallery.
    second=login('faceadmin')
    assert c.get('/api/face-lab/status',headers=second).json()['count']==0
    assert c.post('/api/face-lab/search',headers={**second,
        'Content-Type':'image/jpeg','X-Lab-Consent':'yes'},
        content=b'Query-A').json()['ranked_candidates']==[]
    assert c.post('/api/face-lab/clear',headers=admin).json()['cleared_temporary_embeddings']==2
    assert c.get('/api/face-lab/status',headers=admin).json()['count']==0


def test_temporary_embeddings_expire_without_persistence(secure,monkeypatch):
    c,login=secure
    admin=login('faceadmin')
    monkeypatch.setattr(face_photo_lab,'_feature',lambda b:np.array([1.,0.,0.],dtype=np.float32))
    h={**admin,'Content-Type':'image/png','X-Lab-Consent':'yes'}
    assert c.post('/api/face-lab/enroll?code=S1',headers=h,content=b'fake').status_code==200
    with face_photo_lab._lock:
        token_key=next(iter(face_photo_lab._storage))
        face_photo_lab._storage[token_key]['expires']=0
    assert c.get('/api/face-lab/status',headers=admin).json()['count']==0
    with m.db() as db:
        names={x['name'] for x in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        assert not any('face_embedding' in x for x in names)
