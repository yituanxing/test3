import os
import tempfile
from unittest.mock import Mock, patch
import httpx
import pytest
from fastapi.testclient import TestClient
from app.frigate_bridge import parse_person_event
from app.main import app


def test_parse_filters_unmatched_and_false_positive():
    assert parse_person_event({'id':'X', 'camera':'demo', 'label':'car','start_time':1234}) is None
    assert parse_person_event({'id':'X','camera':'demo','label':'person','false_positive':True,'start_time':1234}) is None
    assert parse_person_event({'id':'X','camera':'demo','label':'person','start_time':'bad'}) is None
    got = parse_person_event({'id':'X', 'camera':'demo', 'label':'person','start_time':1728000000.5,'data':{'top_score':0.93}})
    assert got.event_id == 'X' and got.score == 0.93 and got.started_at.endswith('+00:00')


def test_real_api_contract_mocked_upsert(tmp_path, monkeypatch):
    # This test exercises HTTP output contract, database writes and repeat sync.
    # It does NOT claim the Frigate container started or ran inference.
    import app.main as main
    backup = main.DB
    main.DB = tmp_path/'isolated.sqlite3'
    main.setup_db()
    data = [
        {'id':'fr-001', 'camera':'demo', 'label':'person', 'start_time':1728000000,
         'end_time':None, 'false_positive':False, 'data':{'top_score':0.83},'has_snapshot':True},
        {'id':'car-002', 'camera':'demo', 'label':'car', 'start_time':1728000001},
        {'id':'other-003', 'camera':'unmapped', 'label':'person','start_time':1728000002},
    ]
    try:
        client=TestClient(app)
        bind=client.post('/api/frigate/cameras',json={'camera':'demo','place_id':1})
        assert bind.status_code == 200,bind.text
        fake=Mock()
        fake.status_code=200
        fake.json.return_value=data
        fake.raise_for_status.return_value=None
        with patch('app.main.httpx.get', return_value=fake):
            first=client.post('/api/frigate/sync')
            assert first.status_code == 200,first.text
            assert first.json()['inserted']==1 and first.json()['skipped']==2
            data[0]['end_time']=1728000012
            second=client.post('/api/frigate/sync')
            assert second.json()['inserted']==0 and second.json()['updated']==1
        events=client.get('/api/frigate/events').json()
        assert len(events)==1 and events[0]['ended_at'] is not None
        assert events[0]['score']==0.83
        summary=client.get('/api/frigate/summary').json()
        assert next(x for x in summary['places'] if x['place_id']==1)['observed_person_objects']==1
    finally:
        main.DB = backup


def test_unavailable_upstream_returns_bad_gateway(tmp_path):
    import app.main as main
    backup=main.DB
    main.DB = tmp_path/'upstream.sqlite3'
    main.setup_db()
    try:
        with patch('app.main.httpx.get', side_effect=httpx.ConnectError('unreachable')):
            resp=TestClient(app).post('/api/frigate/sync')
        assert resp.status_code == 502
    finally:
        main.DB = backup
