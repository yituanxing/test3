#!/usr/bin/env python3
"""Real Docker-free MPEG-TS/UDP loopback video -> FFmpeg -> OpenCV -> FastAPI -> SQLite.

Not RTSP, GB28181, or Frigate. Uses an unmodified upstream FFmpeg binary and
OpenCV native decoder. Bundled NASA photo test video is public domain.
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
VIDEO = ROOT / 'samples' / 'astronaut-face-test.mp4'


def spare_port(protocol='tcp'):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM if protocol == 'tcp' else socket.SOCK_DGRAM) as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def main():
    if not VIDEO.is_file():
        raise RuntimeError('Missing bundled photo test video')
    if not shutil.which('ffmpeg'):
        raise RuntimeError('FFmpeg not installed')
    webport = spare_port()
    udpport = spare_port('udp')
    report = {
        'checked_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'test': 'real localhost MPEG-TS/UDP network stream -> OpenCV Haar -> SQLite -> HTTP',
        'protocol': 'MPEG-TS over UDP, NOT RTSP and NOT Frigate',
        'frigate_verified': False, 'gb28181_verified': False,
        'person_identity_verified': False,
    }
    with tempfile.TemporaryDirectory(prefix='zd-network-') as tmp:
        env = {**os.environ, 'ZD_DATA_DIR': tmp, 'ZD_FACE_DETECTOR': 'haar'}
        web = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'app.main:app',
                '--host', '127.0.0.1', '--port', str(webport), '--log-level', 'warning'],
                cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        ff = None
        try:
            url = f'http://127.0.0.1:{webport}'
            with httpx.Client(timeout=35) as client:
                for _ in range(100):
                    try:
                        if client.get(url + '/api/health').status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    if web.poll() is not None:
                        raise RuntimeError('Backend exited')
                    time.sleep(.1)
                else:
                    raise RuntimeError('Backend did not start')

                def api(method, path, data=None):
                    r = client.request(method, url + path, json=data)
                    r.raise_for_status()
                    return r.json()

                place = api('GET', '/api/places')[0]
                uri = f'udp://127.0.0.1:{udpport}?fifo_size=500000&overrun_nonfatal=1'
                source = api('POST', '/api/sources', {
                    'place_id': place['id'], 'name': 'Docker-free UDP network test',
                    'kind': 'rtsp', 'uri': uri
                })
                # The 'rtsp' enum is kept for legacy DB compatibility. The URL is UDP,
                # never described as an RTSP protocol test.
                ff = subprocess.Popen(['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error',
                    '-re', '-stream_loop', '-1', '-i', str(VIDEO), '-an',
                    '-c:v', 'copy', '-f', 'mpegts',
                    f'udp://127.0.0.1:{udpport}?pkt_size=1316'],
                    stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
                run = api('POST', f"/api/sources/{source['id']}/process?max_frames=24&sample_every=4")
                assert run['frames_decoded'] == 24, run
                assert run['frames_analyzed'] == 6, run
                assert run['face_detections'] >= 1, run
                events = api('GET', '/api/events?limit=100')
                matched = [x for x in events if x['source_id'] == source['id']]
                assert len(matched) == 6, matched
                assert all(x['origin'] == 'video' for x in matched)
                assert all(x['detector'] == 'opencv-haar' for x in matched)
                assert sum(x['detected_faces'] for x in matched) == run['face_detections']
                stats = api('GET', '/api/monitoring/overview')
                assert stats['counts']['sampled_frames'] == 6, stats
                report.update({'result': 'PASS', 'video': str(VIDEO.relative_to(ROOT)),
                    'frames_decoded': run['frames_decoded'],
                    'frames_analyzed': run['frames_analyzed'],
                    'face_detection_boxes': run['face_detections'],
                    'sqlite_events': len(matched),
                    'statistics_sampled_frames': stats['counts']['sampled_frames']})
        finally:
            if ff is not None:
                ff.terminate()
                try:
                    _, error = ff.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    ff.kill()
                    _, error = ff.communicate(timeout=2)
                if report.get('result') != 'PASS':
                    print('FFmpeg log:', error.decode(errors='replace')[-1000:], file=sys.stderr)
            web.terminate()
            try:
                _, error = web.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                web.kill()
                _, error = web.communicate(timeout=2)
            if report.get('result') != 'PASS':
                print('Web log:', error.decode(errors='replace')[-1000:], file=sys.stderr)
    output = ROOT / 'reports' / 'acceptance-network-without-docker.json'
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(f'FAIL: {type(exc).__name__}: {exc}', file=sys.stderr)
        sys.exit(1)
