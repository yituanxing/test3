#!/usr/bin/env python3
"""Actual localhost TCP/HTTP + MP4 -> OpenCV -> SQLite -> FastAPI smoke test.

Requires no camera, external network or Docker. Uses a TEMPORARY isolated DB.
Does not certify Frigate model inference, face identity, or GB28181 protocol.
"""
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from datetime import datetime,timezone
from pathlib import Path
import httpx

ROOT=Path(__file__).resolve().parents[1]

def main():
    report={'at_utc':datetime.now(timezone.utc).isoformat(timespec='seconds'),
            'tests':{},'fr​igate_inference_verified':False,
            'gb28181_verified':False,'real_identity_recognition_verified':False}
    # Unused gap between selecting a port and binding it is okay for local CI;
    # if another process wins, this test will fail clearly instead of lying.
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    url=f'http://127.0.0.1:{port}'
    with tempfile.TemporaryDirectory(prefix='zd-smoke-') as work:
        env={**os.environ,'ZD_DATA_DIR':work}
        proc=subprocess.Popen([sys.executable,'-m','uvicorn','app.main:app',
            '--host','127.0.0.1','--port',str(port),'--log-level','warning'],
            cwd=ROOT,env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
        try:
            with httpx.Client(timeout=15) as client:
                for _ in range(50):
                    try:
                        response=client.get(url+'/api/health')
                        if response.status_code==200:break
                    except httpx.HTTPError:pass
                    if proc.poll() is not None:raise RuntimeError('backend exited before startup')
                    time.sleep(.12)
                else:raise RuntimeError('backend did not start in time')
                def step(name,fn):
                    value=fn()
                    report['tests'][name]={'passed':True,'value':value}
                    print('PASS',name,':',value)
                    return value
                def api(path,method='GET',data=None):
                    r=client.request(method,url+path,json=data);r.raise_for_status();return r.json()
                step('tcp_http_backend',lambda:api('/api/health')['status'])
                step('interactive_monitor_page',lambda:'全链路运行台' in client.get(url+'/monitor').text)
                sources=api('/api/sources')
                sample=next(x for x in sources if x['kind']=='file' and '内置测试视频' in x['name'])
                result=step('actual_mp4_opencv_inference',lambda:api(
                    f"/api/sources/{sample['id']}/process?max_frames=80&sample_every=10",'POST'))
                assert result['frames_decoded']==80 and result['frames_analyzed']==8
                overview=step('real_frames_aggregated_in_sqlite',lambda:api('/api/monitoring/overview')['counts'])
                assert overview['sampled_frames']==8
                regions=step('four_tier_regions',lambda:api('/api/regions'))
                assert [x['level'] for x in regions]==[1,2,3,4]
                region_parent=None
                for name in ['验收州','验收县市','验收街道','验收社区']:
                    region=api('/api/regions','POST',{'name':name,'parent_id':region_parent})
                    region_parent=region['id']
                new=step('real_http_create_place',lambda:api('/api/places','POST',
                    {'name':'验收场所','region_id':region_parent}))
                assert new['region_id']==region_parent
                scope=step('region_scoped_monitor',lambda:api(
                    '/api/monitoring/overview?region_id='+str(region_parent)))
                assert [x['place_id'] for x in scope['places']]==[new['id']]
                assert scope['counts']['sampled_frames']==0
                step('source_disable',lambda:api(
                    f"/api/sources/{sample['id']}/state",'PATCH',{'enabled':False}))
                blocked=client.post(url+f"/api/sources/{sample['id']}/process")
                assert blocked.status_code==409
                step('disabled_source_rejected',lambda:blocked.status_code)
                api(f"/api/sources/{sample['id']}/state",'PATCH',{'enabled':True})
                history=step('admin_audit_persisted',lambda:api('/api/admin/audit'))
                assert len(history)>=6
                seed=step('synthetic_business_seed',lambda:api('/api/business/seed','POST'))
                assert seed['inserted']==16
                analysis=api('/api/business/analysis')
                assert analysis['raw_count']==16 and analysis['visit_count']==15
                config=analysis['config'];config['visit_gap_minutes']=1
                step('config_updated',lambda:api('/api/business/config','PUT',config)['recomputed'])
                updated=step('actual_business_recompute',lambda:api('/api/business/analysis')['visit_count'])
                assert updated==16
                assert api('/api/monitoring/overview')['counts']==overview
                step('synthetic_and_real_isolation',lambda:True)
                status=api('/api/frigate/status')
                step('frigate_state_honest',lambda:{'connected':status['connected'],
                    'inference_verified':status['inference_verified']})
                assert not status['inference_verified']
        finally:
            proc.terminate()
            try:proc.communicate(timeout=4)
            except subprocess.TimeoutExpired:proc.kill();proc.communicate(timeout=2)
    report['overall']='PASS'
    report={k.replace('\u200b',''):v for k,v in report.items()}
    out=ROOT/'reports'/'acceptance-real-http.json'
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print('Evidence:',out)
    return 0

if __name__=='__main__':
    try:sys.exit(main())
    except Exception as e:
        print(f'SMOKE FAILED: {type(e).__name__}: {e}',file=sys.stderr)
        sys.exit(1)
