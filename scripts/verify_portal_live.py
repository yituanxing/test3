#!/usr/bin/env python3
"""Live main-Web acceptance: true Frigate RTSP events become portal-visible facts.

Runs after verify_live_frigate.py. Never creates fake Frigate detections or
assigns a real person to synthetic P### labels.
"""
from __future__ import annotations
import argparse
import json
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path
import httpx

from app import access
import app.main as main


def request(client, method, url, **kwargs):
    result = client.request(method, url, **kwargs)
    result.raise_for_status()
    return result.json()


def run(args):
    report = {
        'pipeline': 'RTSP/MediaMTX -> Frigate OpenVINO -> app.main -> SQLite -> authorized portal APIs',
        'time_utc': datetime.now(timezone.utc).isoformat(),
        'mode': 'real_anonymous_detection',
        'actual_unique_people_verified': False,
        'biometric_matching_used': False,
        'checks': {},
        'verdict': 'FAIL',
    }
    filepath = Path(args.report)
    filepath.parent.mkdir(parents=True, exist_ok=True)
    try:
        password = secrets.token_urlsafe(30)
        with main.db() as c:
            region = c.execute('SELECT id FROM regions WHERE level=1 ORDER BY id LIMIT 1').fetchone()
            assert region, 'No root jurisdiction in main Web DB'
            access.create_user(c, 'ci_live_portal', password, region['id'])
        with httpx.Client(timeout=12) as client:
            ready = False
            for attempt in range(30):
                try:
                    assert request(client, 'GET', args.backend + '/api/health')['status'] == 'ok'
                    ready = True
                    break
                except (httpx.HTTPError, AssertionError):
                    time.sleep(2)
            assert ready, 'Main business server failed to start'
            report['checks']['main_web_health'] = 'PASS'
            html = client.get(args.backend + '/portal')
            html.raise_for_status()
            assert '重点场所智能预警分析平台' in html.text
            report['checks']['portal_served'] = 'PASS'

            auth = request(client, 'POST', args.backend + '/api/auth/login', json={
                'username': 'ci_live_portal', 'password': password})
            headers = {'Authorization': 'Bearer ' + auth['access_token']}
            actor = request(client, 'GET', args.backend + '/api/auth/me', headers=headers)
            assert actor['level'] == 1
            report['checks']['authenticated_session'] = 'PASS'

            places = request(client, 'GET', args.backend + '/api/places', headers=headers)
            place = next((p for p in places if p['active']), None)
            assert place, 'No active local site for camera binding'
            binding = request(client, 'POST', args.backend + '/api/frigate/cameras',
                headers=headers, json={'camera': args.camera, 'place_id': place['id']})
            assert binding['place_id'] == place['id']
            report['checks']['camera_place_binding'] = 'PASS'

            upstream = request(client, 'GET', args.frigate + '/api/events',
                               params={'camera': args.camera, 'label': 'person', 'limit': 200})
            actual_ids = {e['id'] for e in upstream if e.get('id') and e.get('camera') == args.camera
                          and e.get('label') == 'person' and e.get('false_positive') is not True}
            assert actual_ids, 'No Frigate person event found; refusing fake success'
            report['genuine_upstream_event_count'] = len(actual_ids)
            report['checks']['actual_person_objects'] = 'PASS'

            synced = request(client, 'POST', args.backend + '/api/frigate/sync',
                             params={'limit': 200}, headers=headers)
            rows = request(client, 'GET', args.backend + '/api/frigate/events',
                           params={'limit': 500}, headers=headers)
            stored_ids = {e['event_id'] for e in rows}
            assert actual_ids.issubset(stored_ids), (
                'Real Frigate events absent from app.main SQLite: ' + str(sorted(actual_ids - stored_ids)[:5]))
            assert all(e['place_id'] == place['id'] for e in rows if e['event_id'] in actual_ids)
            report['checks']['main_sqlite_binding_persistence'] = 'PASS'
            report['main_sqlite_event_count'] = len(rows)
            report['sync'] = synced

            again = request(client, 'POST', args.backend + '/api/frigate/sync',
                            params={'limit': 200}, headers=headers)
            assert again['inserted'] == 0, 'Repeated synchronization duplicated real events'
            report['checks']['idempotent_main_resync'] = 'PASS'

            dashboard = request(client, 'GET', args.backend + '/api/insights/overview',
                                headers=headers)
            total = dashboard['real_video']['counts']['person_objects']
            assert total >= len(actual_ids), (
                'Main Web overview failed to count real Frigate person events')
            assert dashboard['synthetic']['mode'] == 'synthetic_only'
            assert dashboard['synthetic']['cards']['unique_synthetic_people_in_window'] == 0
            report['checks']['portal_real_count_and_synthetic_isolation'] = 'PASS'
            report['dashboard_anonymous_event_count'] = total
            report['synthetic_people'] = 0

            monitored = request(client, 'GET', args.backend + '/api/monitoring/overview',
                                headers=headers)
            by_place = {p['place_id']: p for p in monitored['places']}
            assert by_place[place['id']]['person_objects'] >= len(actual_ids)
            report['checks']['place_detail_event_count'] = 'PASS'

            quality = request(client, 'GET', args.backend + '/api/frigate/quality',
                              headers=headers)
            assert quality['identity_recognition_verified'] is False
            assert quality['unique_people'] is None and quality['precision'] is None
            report['checks']['no_fabricated_identity_quality'] = 'PASS'
            report['verdict'] = 'PASS'
            print('Verified main Web portal live Frigate events =', len(actual_ids), flush=True)
        return 0
    except Exception as exc:
        report['error'] = type(exc).__name__ + ': ' + str(exc)
        print('Main Web end-to-end FAIL:', report['error'], flush=True)
        return 1
    finally:
        filepath.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')
        print('Main Web evidence:', filepath, report['verdict'], flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--frigate', default='http://127.0.0.1:5000')
    parser.add_argument('--backend', default='http://127.0.0.1:8001')
    parser.add_argument('--camera', default='demo')
    parser.add_argument('--report', default='reports/verify-portal-live.json')
    raise SystemExit(run(parser.parse_args()))
