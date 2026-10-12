"""WVP API adapter -> actual MediaMTX RTSP -> OpenCV -> SQLite verification.

This runs while the existing CI publisher streams a licensed MOVING sample to
rtsp://127.0.0.1:8554/demo. The WVP HTTP response is simulated by a tiny
local test double because no GB28181 IPC/NVR is present in public CI.
Evidence proves the adapter hands off a REAL decodable RTSP stream, but
explicitly does not claim to have validated SIP registration or GB PS/RTP.
"""
import json
import os
import secrets
from contextlib import contextmanager
from datetime import datetime,timezone
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from threading import Thread

from fastapi.testclient import TestClient

ROOT=Path(__file__).resolve().parents[1]
DEVICE='34020000001320000001'
CHANNEL='34020000001320000002'


@contextmanager
def fake_wvp_control_plane():
    calls=[]
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_GET(self):
            calls.append(self.path)
            if self.headers.get('api-key')!='ci-adapter-only':
                self.send_response(401);self.end_headers();return
            if self.path==f'/api/play/start/{DEVICE}/{CHANNEL}':
                result={'code':0,'msg':'mock WVP signaling boundary ONLY',
                        'data':{'stream':'gbrtspdemo','rtsp':'rtsp://127.0.0.1:8554/demo'}}
            elif self.path==f'/api/play/stop/{DEVICE}/{CHANNEL}':
                result={'code':0,'msg':'mock stop'}
            else:
                result={'code':404,'msg':'unknown route'}
            body=json.dumps(result).encode()
            self.send_response(200)
            self.send_header('Content-Type','application/json')
            self.send_header('Content-Length',str(len(body)))
            self.end_headers()
            self.wfile.write(body)
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=Thread(target=server.serve_forever,daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}',calls
    finally:
        server.shutdown();thread.join(timeout=5);server.server_close()


def main():
    from app import main as appmain,access
    report={'verified_at_utc':datetime.now(timezone.utc).isoformat(),
            'control_plane':'local HTTP test double, NOT real WVP SIP',
            'media_plane':'actual MediaMTX RTSP H264 stream from CC-BY Intel licensed footage',
            'gb28181_device_registration':'NOT_TESTED',
            'gb28181_sip_invite':'NOT_TESTED',
            'gb28181_ps_rtp_media':'NOT_TESTED',
            'verdict':'FAIL'}
    target=ROOT/'reports'/'verify-wvp-rtsp-bridge.json'
    try:
        appmain.setup_db()
        with appmain.db() as c:
            root=c.execute('SELECT id FROM regions WHERE level=1 LIMIT 1').fetchone()['id']
            place=c.execute('SELECT id FROM places WHERE active=1 ORDER BY id LIMIT 1').fetchone()['id']
            username='ci_gbmedia_bridge'
            password=secrets.token_urlsafe(30)
            access.create_user(c,username,password,root)
        client=TestClient(appmain.app)
        login=client.post('/api/auth/login',json={'username':username,'password':password})
        assert login.status_code==200,login.text
        headers={'Authorization':'Bearer '+login.json()['access_token']}
        with fake_wvp_control_plane() as (wvp,calls):
            os.environ['GB28181_WVP_URL']=wvp
            os.environ['GB28181_WVP_API_KEY']='ci-adapter-only'
            os.environ['GB28181_RTSP_HOSTS']='127.0.0.1'
            bound=client.post('/api/gb28181/bindings',headers=headers,json={
                'name':'CI real RTSP media from licensed video', 'place_id':place,
                'device_id':DEVICE,'channel_id':CHANNEL})
            assert bound.status_code==200,bound.text
            live=client.post(f"/api/gb28181/bindings/{bound.json()['id']}/live",headers=headers)
            assert live.status_code==200,live.text
            info=live.json()
            assert info['source_id']>0
            assert any('/api/play/start/' in route for route in calls)
            source=client.get('/api/sources',headers=headers)
            assert source.status_code==200
            actual=next(s for s in source.json() if s['id']==info['source_id'])
            assert actual['kind']=='rtsp'
            assert actual['uri']=='rtsp://127.0.0.1:8554/demo'
            decoded=client.post(
                f"/api/sources/{info['source_id']}/process?max_frames=30&sample_every=5",
                headers=headers)
            assert decoded.status_code==200,decoded.text
            fact=decoded.json()
            assert fact['frames_decoded']>=10
            assert fact['new_samples']>=2
            evidence=client.get('/api/video/observations?source_id='+str(info['source_id']),
                                headers=headers)
            assert evidence.status_code==200,evidence.text
            observed=evidence.json()
            assert observed['quality_totals']['sampled_frame_records']==fact['new_samples']
            assert observed['mode']=='real_video_anonymous'
            assert all(q['source_id']==info['source_id'] for q in observed['observations'])
            assert all('identity' not in q for q in observed['observations'])
            stopped=client.post(f"/api/gb28181/sessions/{info['session_id']}/stop",
                                headers=headers)
            assert stopped.status_code==200,stopped.text
            report.update({
                'source_id':info['source_id'],'binding_id':bound.json()['id'],
                'rtsp_decoded_frames':fact['frames_decoded'],
                'actual_inference_frames':fact['frames_analyzed'],
                'recorded_samples':fact['new_samples'],
                'model_face_boxes':fact['face_detections'],
                'stopped_source_disabled':stopped.json()['source_enabled'] is False,
                'wvp_adapter_http_calls':calls,
                'verdict':'PASS'})
        return 0
    except Exception as exc:
        report['error']=type(exc).__name__+': '+str(exc)[:250]
        return 1
    finally:
        target.parent.mkdir(parents=True,exist_ok=True)
        target.write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
        print('WVP-adapter to ACTUAL RTSP verdict:',report['verdict'],flush=True)
        print('actual RTSP frames:',report.get('rtsp_decoded_frames'),flush=True)
        print('GB28181 SIP verified:',report['gb28181_sip_invite'],flush=True)
        if 'error' in report:print('Error:',report['error'],flush=True)


if __name__=='__main__':
    raise SystemExit(main())
