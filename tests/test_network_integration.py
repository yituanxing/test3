"""Loopback real-HTTP contract integration: not a real Frigate container test."""
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
from threading import Thread
import json

from fastapi.testclient import TestClient


def test_real_loopback_http_contract(tmp_path, monkeypatch):
    import app.main as m
    monkeypatch.setattr(m,'DB',tmp_path/'network.sqlite3')
    m.setup_db()
    c=TestClient(m.app)
    place = c.get('/api/places').json()[0]['id']
    assert c.post('/api/frigate/cameras',json={'camera':'demo','place_id':place}).status_code == 200

    class FakeUpstreamHTTP(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.startswith('/api/version'):
                content=b'0.test-loopback'
            elif self.path.startswith('/api/events'):
                content=json.dumps([
                    {'id':'person-0001','camera':'demo','label':'person',
                     'start_time':1728000000.0,'end_time':1728000005.0,'data':{'top_score':0.89},
                     'has_snapshot':True},
                    {'id':'car-0002','camera':'demo','label':'car','start_time':1728000000.0},
                ]).encode()
            else:
                self.send_response(404);self.end_headers();return
            self.send_response(200)
            self.send_header('Content-Type','application/json')
            self.send_header('Content-Length',str(len(content)))
            self.end_headers();self.wfile.write(content)
        def log_message(self,*args):pass

    server = ThreadingHTTPServer(('127.0.0.1',0),FakeUpstreamHTTP)
    thread=Thread(target=server.serve_forever,daemon=True)
    thread.start()
    try:
        monkeypatch.setenv('ZD_FRIGATE_API',f'http://127.0.0.1:{server.server_port}')
        status=c.get('/api/frigate/status').json()
        assert status['connected'] is True and status['inference_verified'] is False
        first=c.post('/api/frigate/sync').json()
        assert first['inserted'] == 1 and first['skipped'] == 1
        repeat=c.post('/api/frigate/sync').json()
        assert repeat['inserted'] == 0 and repeat['updated'] == 1
        assert len(c.get('/api/frigate/events').json()) == 1
        assert c.get('/api/business/analysis').json()['raw_count'] == 0
    finally:
        server.shutdown();server.server_close();thread.join(timeout=2)
