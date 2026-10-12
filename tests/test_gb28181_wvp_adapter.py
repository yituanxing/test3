"""WVP upstream API CONTRACT against a real localhost HTTP test double.

The TCP/HTTP exchange, API-key header, FastAPI authentication, SQLite source
insert, session lifecycle, and zone-aware playback mapping are real. The fake
WVP upstream is not a SIP device: these tests make NO claim of actual GB28181
registration, RTP reception or historical-device playback success.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from urllib.parse import urlparse,parse_qs

import pytest
from fastapi.testclient import TestClient
from app import main, access

DEVICE='34020000001320000001'
CHANNEL='34020000001320000002'
PASSWORD='test-only-gb-strong-password'


@contextmanager
def upstream_wvp():
    calls=[]
    switches={'malformed_rtsp':False,'upstream_failure':False,'foreign_rtsp':False}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):
            pass
        def do_GET(self):
            route=urlparse(self.path)
            calls.append({'path':route.path,'params':parse_qs(route.query),
                          'api_key':self.headers.get('api-key')})
            if self.headers.get('api-key')!='integration-local-only-key':
                self.send_response(403)
                self.end_headers()
                return
            if route.path=='/api/device/query/devices':
                output={'list':[{'deviceId':DEVICE,'name':'授权测试设备','online':1}],
                        'total':1}
            elif route.path==f'/api/device/query/devices/{DEVICE}/channels':
                output={'list':[{'deviceId':CHANNEL,'name':'测试视频通道','online':1}],
                        'total':1}
            elif '/api/play/start/' in route.path or '/api/playback/start/' in route.path:
                if switches['upstream_failure']:
                    output={'code':486,'msg':'camera timed out','data':None}
                else:
                    name='history' if 'playback' in route.path else 'live'
                    rtsp=('http://bad.example/video' if switches['malformed_rtsp'] else
                          'rtsp://host.not.allowed.invalid:8554/stream' if switches['foreign_rtsp']
                          else 'rtsp://127.0.0.1:8554/'+name)
                    output={'code':0,'msg':'ok','data':{'stream':'test_'+name,
                                                        'rtsp':rtsp}}
            elif '/api/play/stop/' in route.path or '/api/playback/stop/' in route.path:
                output={'code':0,'msg':'ok'}
            else:
                output={'code':404,'msg':'unexpected test route'}
            import json
            body=json.dumps(output).encode()
            self.send_response(200)
            self.send_header('Content-Type','application/json')
            self.send_header('Content-Length',str(len(body)))
            self.end_headers()
            self.wfile.write(body)
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    worker=Thread(target=server.serve_forever,daemon=True)
    worker.start()
    try:
        yield 'http://127.0.0.1:'+str(server.server_port),calls,switches
    finally:
        server.shutdown()
        worker.join(timeout=3)
        server.server_close()


@pytest.fixture
def client(tmp_path,monkeypatch):
    monkeypatch.delenv('ZD_AUTH_TEST_COMPAT',raising=False)
    monkeypatch.setattr(main,'DB',tmp_path/'gb-source.sqlite3')
    main.setup_db()
    with main.db() as c:
        root=c.execute('SELECT id FROM regions WHERE level=1 LIMIT 1').fetchone()['id']
        leaf=c.execute('SELECT id FROM regions WHERE level=4 LIMIT 1').fetchone()['id']
        access.create_user(c,'gbroot',PASSWORD,root)
        access.create_user(c,'gbleaf',PASSWORD,leaf)
    c=TestClient(main.app)
    def headers(username):
        token=c.post('/api/auth/login',json={'username':username,'password':PASSWORD})
        assert token.status_code==200,token.text
        return {'Authorization':'Bearer '+token.json()['access_token']}
    return c,headers('gbroot'),headers('gbleaf')


def test_wvp_device_catalog_real_http_and_live_to_authorized_source(client,monkeypatch):
    c,admin,local=client
    with upstream_wvp() as (base,calls,_):
        monkeypatch.setenv('GB28181_WVP_URL',base)
        monkeypatch.setenv('GB28181_WVP_API_KEY','integration-local-only-key')
        monkeypatch.setenv('GB28181_RTSP_HOSTS','127.0.0.1')
        assert c.get('/api/gb28181/status').status_code==401
        status=c.get('/api/gb28181/status',headers=local).json()
        assert status['configured'] is True
        assert status['registration_proven'] is False
        page=c.get('/api/gb28181/devices',headers=admin)
        assert page.status_code==200,page.text
        assert page.json()['devices'][0]['device_id']==DEVICE
        channel=c.get(f'/api/gb28181/devices/{DEVICE}/channels',headers=admin)
        assert channel.status_code==200,channel.text
        assert channel.json()['channels'][0]['channel_id']==CHANNEL
        assert c.get('/api/gb28181/devices',headers=local).status_code==403
        assert c.get('/api/gb28181/devices/invalid/channels',headers=admin).status_code==422
        result=c.post('/api/gb28181/bindings',headers=admin,json={
            'name':'场所入口国标通道','place_id':1,'device_id':DEVICE,'channel_id':CHANNEL})
        assert result.status_code==200,result.text
        bid=result.json()['id']
        assert c.post('/api/gb28181/bindings',headers=admin,json={
            'name':'重复绑定','place_id':1,'device_id':DEVICE,'channel_id':CHANNEL}).status_code==409
        assert c.post(f'/api/gb28181/bindings/{bid}/live',headers=local).status_code==403
        started=c.post(f'/api/gb28181/bindings/{bid}/live',headers=admin)
        assert started.status_code==200,started.text
        body=started.json()
        assert body['state']=='active'
        assert body['source_kind']=='rtsp'
        assert body['mode']=='live'
        assert 'rtsp' not in body
        with main.db() as db:
            row=db.execute('SELECT * FROM sources WHERE id=?',(body['source_id'],)).fetchone()
            assert row['kind']=='rtsp'
            assert row['uri']=='rtsp://127.0.0.1:8554/live'
            assert row['place_id']==1
            assert row['enabled']==1
        assert c.get('/api/gb28181/sessions',headers=admin).json()['sessions'][0]['source_id']==body['source_id']
        assert c.post(f"/api/gb28181/sessions/{body['session_id']}/stop",headers=local).status_code==403
        stopped=c.post(f"/api/gb28181/sessions/{body['session_id']}/stop",headers=admin)
        assert stopped.status_code==200,stopped.text
        assert stopped.json()['source_enabled'] is False
        assert c.post(f"/api/gb28181/sessions/{body['session_id']}/stop",headers=admin).json()['repeated'] is True
        with main.db() as db:
            assert db.execute('SELECT enabled FROM sources WHERE id=?',
                              (body['source_id'],)).fetchone()[0]==0
            assert db.execute("SELECT COUNT(*) FROM admin_audit WHERE operation IN ('gb28181_bind','gb28181_start','gb28181_stop')").fetchone()[0]==3
            assert 'integration-local-only-key' not in '\n'.join(row[0] for row in db.execute('SELECT new_value FROM admin_audit'))
        assert calls
        assert all(x['api_key']=='integration-local-only-key' for x in calls)
        assert any(x['path']==f'/api/play/start/{DEVICE}/{CHANNEL}' for x in calls)


def test_wvp_recorded_playback_uses_explicit_timezone_and_correct_routes(client,monkeypatch):
    c,admin,_=client
    with upstream_wvp() as (base,calls,_):
        monkeypatch.setenv('GB28181_WVP_URL',base)
        monkeypatch.setenv('GB28181_WVP_API_KEY','integration-local-only-key')
        monkeypatch.setenv('GB28181_DEVICE_TZ','Asia/Shanghai')
        body={'name':'视频回放设备','place_id':1,'device_id':DEVICE,'channel_id':CHANNEL}
        bid=c.post('/api/gb28181/bindings',headers=admin,json=body).json()['id']
        url=f'/api/gb28181/bindings/{bid}/playback'
        naive=c.post(url,headers=admin,json={
            'start_at':'2026-10-12T00:00:00','end_at':'2026-10-12T01:00:00'})
        assert naive.status_code==422
        reversed_window=c.post(url,headers=admin,json={
            'start_at':'2026-10-12T02:00:00Z','end_at':'2026-10-12T01:00:00Z'})
        assert reversed_window.status_code==422
        response=c.post(url,headers=admin,json={
            'start_at':'2026-10-12T00:00:00Z','end_at':'2026-10-12T01:00:00Z'})
        assert response.status_code==200,response.text
        item=response.json()
        assert item['mode']=='playback'
        cmd=next(k for k in calls if k['path']==f'/api/playback/start/{DEVICE}/{CHANNEL}')
        assert cmd['params']['startTime']==['2026-10-12 08:00:00']
        assert cmd['params']['endTime']==['2026-10-12 09:00:00']
        stop=c.post(f"/api/gb28181/sessions/{item['session_id']}/stop",headers=admin)
        assert stop.status_code==200,stop.text
        assert any(x['path']==f'/api/playback/stop/{DEVICE}/{CHANNEL}/test_history' for x in calls)


def test_invalid_provider_response_refused_without_persisting_fake_media(client,monkeypatch):
    c,admin,_=client
    with upstream_wvp() as (base,calls,flags):
        monkeypatch.setenv('GB28181_WVP_URL',base)
        monkeypatch.setenv('GB28181_WVP_API_KEY','integration-local-only-key')
        b=c.post('/api/gb28181/bindings',headers=admin,json={
            'name':'不可信上游','place_id':1,'device_id':DEVICE,'channel_id':CHANNEL})
        assert b.status_code==200
        path=f"/api/gb28181/bindings/{b.json()['id']}/live"
        for key in ['malformed_rtsp','foreign_rtsp','upstream_failure']:
            flags[key]=True
            fail=c.post(path,headers=admin)
            assert fail.status_code==502,fail.text
            flags[key]=False
        with main.db() as db:
            assert db.execute('SELECT COUNT(*) FROM gb28181_stream_sessions').fetchone()[0]==0
            assert db.execute("SELECT COUNT(*) FROM sources WHERE name LIKE 'GB28181 %'").fetchone()[0]==0
        monkeypatch.delenv('GB28181_WVP_API_KEY')
        assert c.post(path,headers=admin).status_code==503
