"""Deterministic end-to-end business simulation using real SQLite and HTTP API.

No camera, model, biometric identity or live Frigate process is mocked as if real.
"""
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def env(tmp_path, monkeypatch):
    import app.main as main
    monkeypatch.setattr(main, 'DB', tmp_path / 'isolated.sqlite3')
    main.setup_db()
    return TestClient(main.app)


def test_seed_drives_all_seven_rules(env):
    first = env.post('/api/business/seed')
    assert first.status_code == 200
    assert first.json()['inserted'] == 16
    result = env.get('/api/business/analysis').json()
    assert result['mode'] == 'synthetic_only'
    assert result['raw_count'] == 16
    assert result['effective_observations'] == 16
    assert result['visit_count'] == 15, 'two same-person same-place observations were deduplicated'
    assert len(result['affiliation']) == 6
    assert {r['type'] for r in result['rule_counts']} == {
        'cross_place','companion','interaction','gathering','absence','appearance','headcount'
    }
    assert all(r['count'] > 0 for r in result['rule_counts'])
    assert len({x['person'] for x in result['affiliation']}) == 6, 'unique synthetic home assignment'
    assert all(a['origin'] == 'synthetic_only' for a in result['alerts'])
    assert env.post('/api/business/seed').json()['inserted'] == 0, 'seed should not duplicate'
    assert env.get('/api/business/analysis').json()['raw_count'] == 16


def test_config_change_recalculates_from_raw_events(env):
    env.post('/api/business/seed')
    before = env.get('/api/business/analysis').json()
    config = before['config']
    config['visit_gap_minutes'] = 1
    assert env.put('/api/business/config', json=config).status_code == 200
    after = env.get('/api/business/analysis').json()
    assert after['visit_count'] == before['visit_count']+1
    assert after['raw_count'] == before['raw_count']
    config['affiliation_mode'] = 'frequency'
    assert env.put('/api/business/config',json=config).status_code == 200
    freq = env.get('/api/business/analysis').json()
    a = {x['person']:x['place_id'] for x in before['affiliation']}
    b = {x['person']:x['place_id'] for x in freq['affiliation']}
    assert a['P002'] != b['P002'], 'count-priority must change synthetic home assignment'


def test_review_is_append_only_and_recomputes(env):
    env.post('/api/business/seed')
    initial = env.get('/api/business/analysis').json()
    orig = env.get('/api/business/observations').json()
    target = next(x for x in orig if x['event_key'] == 'DEMO-0003')
    res = env.post(f"/api/business/observations/{target['id']}/review",
                   json={'excluded': True, 'reason': '测试排除重复采集'})
    assert res.status_code == 200 and res.json()['original_unchanged']
    after = env.get('/api/business/analysis').json()
    assert after['raw_count'] == initial['raw_count']
    assert after['effective_observations'] == initial['effective_observations']-1
    rows = env.get('/api/business/observations').json()
    fixed = next(x for x in rows if x['id'] == target['id'])
    assert fixed['person_label'] == target['person_label'] and fixed['excluded'] == 1
    # Follow-up review can restore, also append-only.
    res = env.post(f"/api/business/observations/{target['id']}/review",
                   json={'excluded':False, 'corrected_person':'P002',
                         'reason':'测试模拟身份更正'})
    assert res.status_code == 200
    again = env.get('/api/business/observations').json()
    ref = next(x for x in again if x['id'] == target['id'])
    assert ref['person_label'] == 'P001' and ref['effective_person'] == 'P002'
    assert env.get('/api/business/analysis').json()['effective_observations'] == 16
    assert len(env.get('/api/business/audit').json()) >= 3


def test_alert_review_preserves_evidence_and_persists_status(env):
    env.post('/api/business/seed')
    before = env.get('/api/business/analysis').json()
    key = before['alerts'][0]['key']
    evidence = before['alerts'][0]['observation_ids']
    assert env.post(f'/api/business/alerts/{key}/review',json={
        'decision':'false_positive','reason':'测试人工判定数据关联误报'}).status_code == 200
    refreshed = env.get('/api/business/analysis').json()
    row = next(x for x in refreshed['alerts'] if x['key'] == key)
    assert row['status'] == 'false_positive'
    assert row['observation_ids'] == evidence
    assert refreshed['raw_count'] == before['raw_count']
    assert env.post('/api/business/alerts/no-such-key/review',json={
        'decision':'confirmed','reason':'无效命中'}).status_code == 404


def test_real_video_observations_cannot_become_synthetic_people(env):
    env.post('/api/business/seed')
    before = env.get('/api/business/analysis').json()
    env.post('/api/demo/seed')  # Old video-pipeline count-only synthetic frames
    with_ids = env.get('/api/business/analysis').json()
    assert with_ids['raw_count'] == before['raw_count']
    assert with_ids['visits'] == before['visits']
    assert env.get('/business').status_code == 200
    assert '业务闭环' in env.get('/business').text


def test_invalid_configuration_is_rejected(env):
    env.post('/api/business/seed')
    config = env.get('/api/business/config').json()
    assert env.put('/api/business/config',json={**config,'visit_gap_minutes':0}).status_code == 422
    assert env.put('/api/business/config',json={**config,'watch_place_id':999999}).status_code == 404
    assert env.get('/api/business/config').json() == config
