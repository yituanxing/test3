"""Business website opens with real evidence and a clearly labeled populated synthetic scenario."""
from app import main, base_registry
from scripts.demo_bootstrap import prepare_business_demo


def test_bootstrap_populates_only_isolated_synthetic_demo(tmp_path, monkeypatch):
    monkeypatch.setenv("ZD_LEGACY_MODE", "1")
    monkeypatch.setattr(main, "DB", tmp_path / "business-demo.sqlite")
    main.setup_db()
    before = prepare_business_demo()
    assert before["status"] == "ready"
    assert before["synthetic_people"] >= 6
    with main.db() as conn:
        count = conn.execute("SELECT COUNT(*) FROM synthetic_observations").fetchone()[0]
        assert count > 15
        assert conn.execute("SELECT COUNT(*) FROM samples WHERE origin='video'").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0] == 0
        published = base_registry.active_assignments(conn)
        assert published[1]["state"] == "published"
        assert published[0]["P099"] == conn.execute(
            "SELECT id FROM places WHERE name='测试场所 B'"
        ).fetchone()[0]
    again = prepare_business_demo()
    assert again["status"] == "preserved"
    with main.db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM synthetic_observations").fetchone()[0] == count


def test_bootstrap_never_seeds_real_only_instance(tmp_path, monkeypatch):
    monkeypatch.setenv("ZD_LEGACY_MODE", "0")
    monkeypatch.setattr(main, "DB", tmp_path / "real.sqlite")
    main.setup_db()
    assert prepare_business_demo()["status"] == "skipped"
    with main.db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM synthetic_observations").fetchone()[0] == 0
