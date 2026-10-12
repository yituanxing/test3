"""No biometric identity or bogus precision/recall from object events."""
from fastapi.testclient import TestClient

def test_anonymous_quality_summary(tmp_path, monkeypatch):
    import app.main as main
    monkeypatch.setattr(main, 'DB', tmp_path/'quality.sqlite3')
    main.setup_db()
    c=TestClient(main.app)
    r=c.get('/api/frigate/quality')
    assert r.status_code == 200
    j=r.json()
    assert j['task'] == 'anonymous_person_object_detection'
    assert j['stored_object_events'] == 0
    assert j['precision'] is None and j['recall'] is None
    assert j['identity_recognition_verified'] is False
    assert j['score_is_calibrated_accuracy'] is False
