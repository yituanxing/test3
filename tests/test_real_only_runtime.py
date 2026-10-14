"""Real-only mode: no generated business identities and no synthetic UI."""
import os
from fastapi.testclient import TestClient
from app import main

def test_real_only_mode_is_default(tmp_path, monkeypatch):
    monkeypatch.setenv("ZD_LEGACY_MODE", "0")
    monkeypatch.setattr(main, "DB", tmp_path / "real.sqlite")
    main.setup_db()
    with main.db() as db:
        assert db.execute("SELECT COUNT(*) FROM places").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM sources").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM synthetic_observations").fetchone()[0] == 0
    with TestClient(main.app) as client:
        assert client.get("/portal", follow_redirects=False).status_code == 307
        assert client.get("/", follow_redirects=False).headers["location"] == "/real"
        assert client.get("/real").status_code == 200
        for path in ("/business", "/base", "/insights", "/geo", "/anime-scenes"):
            assert client.get(path).status_code == 410
        for path in ("/api/business/seed", "/api/base/demo/duplicates", "/api/demo/seed"):
            assert client.post(path).status_code == 410
