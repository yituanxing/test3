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
        'pipeline': 'LICENSED PUBLIC CLIP -> RTSP/MediaMTX -> Frigate OpenVINO -> SQLite -> /real',
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
            # Operational landing is real-only: legacy synthetic dashboards
            # are not an accepted data source.
            landing = client.get(args.backend + '/portal', follow_redirects=False)
            assert landing.status_code == 307
            assert landing.headers['location'] == '/real'
            real_html = client.get(args.backend + '/real')
            real_html.raise_for_status()
            assert '仅真实视频 · 事件与证据' in real_html.text
            report['checks']['real_only_landing'] = 'PASS'

            auth = request(client, 'POST', args.backend + '/api/auth/login', json={
                'username': 'ci_live_portal', 'password': password})
            headers = {'Authorization': 'Bearer ' + auth['access_token']}
            actor = request(client, 'GET', args.backend + '/api/auth/me', headers=headers)
            assert actor['level'] == 1
            report['checks']['authenticated_session'] = 'PASS'

            places = request(client, 'GET', args.backend + '/api/places', headers=headers)
            place = next((p for p in places if p['active']), None)
            if place is None:
                regions = request(client, 'GET', args.backend + '/api/regions', headers=headers)
                leaf = next((r for r in regions if r['level'] == 4), None)
                assert leaf is not None, 'No configured leaf jurisdiction for this CI clip'
                place = request(client, 'POST', args.backend + '/api/places',
                    headers=headers, json={
                        'name': '授权公开视频RTSP验收场所（非现场）',
                        'region_id': leaf['id']})
            assert place['active'] is True
            # Binding must already exist BEFORE Frigate recorded its events.
            # Never rebind retroactively after inference: that makes event-time
            # ownership unknown and the API correctly skips the old events.
            bindings = request(client, 'GET', args.backend + '/api/frigate/cameras',
                               headers=headers)
            binding = next((b for b in bindings if b['camera'] == args.camera), None)
            assert binding is not None, 'Missing pre-capture camera/place binding'
            assert binding['place_id'] == place['id'], 'Camera was pre-bound to another place'
            report['checks']['binding_created_before_video_capture'] = 'PASS'

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

            # Make sure the old demo endpoints cannot inject fake people or
            # false observations even when called by an authenticated admin.
            for forbidden in ('/api/business/seed', '/api/demo/seed',
                              '/api/base/demo/duplicates'):
                blocked = client.post(args.backend + forbidden, headers=headers)
                assert blocked.status_code == 410, (forbidden, blocked.status_code)
            report['checks']['synthetic_mutation_endpoints_disabled'] = 'PASS'

            monitored = request(client, 'GET', args.backend + '/api/monitoring/overview',
                                headers=headers)
            by_place = {p['place_id']: p for p in monitored['places']}
            assert by_place[place['id']]['person_objects'] >= len(actual_ids)
            report['checks']['place_detail_event_count'] = 'PASS'

            # The user-facing /real workbench must show the SAME objects
            # observed by actual Frigate inference from the licensed RTSP
            # clip. A fixture-only empty-state browser test cannot prove this.
            real_page = client.get(args.backend + '/real')
            real_page.raise_for_status()
            assert '仅真实视频 · 事件与证据' in real_page.text
            report['checks']['real_workbench_page_served'] = 'PASS'

            real = request(client, 'GET', args.backend + '/api/real/evidence',
                           params={'days': 7, 'limit': 200}, headers=headers)
            assert real['mode'] == 'real_evidence_only'
            assert real['contains_synthetic_identity'] is False
            assert real['contains_synthetic_alerts'] is False
            assert real['counts_unique_people'] is False
            assert real['counts_real_site_visits'] is False
            assert real['confirmed_cross_camera_identity'] is False
            actual_workbench = {
                (row['camera'], row['event_id']): row
                for row in real['recent_frigate_events']
            }
            missing = actual_ids - {
                event_id for camera, event_id in actual_workbench if camera == args.camera
            }
            assert not missing, (
                'Actual Frigate objects missing from /real: ' + str(sorted(missing)[:5]))
            for event_id in actual_ids:
                row = actual_workbench[(args.camera, event_id)]
                assert row['source'] == 'frigate_actual_upstream_event'
                assert row['place_id'] == place['id']
                assert row['subject_id'] is None
                assert row['identity_confirmed'] is False
            assert real['real_event_counts']['person_objects'] >= len(actual_ids)
            real_places = {row['place_id']: row for row in real['by_place']}
            assert real_places[place['id']]['person_objects'] >= len(actual_ids)
            report['real_workbench_matched_upstream_objects'] = len(actual_ids)
            report['checks']['real_workbench_actual_frigate_provenance'] = 'PASS'

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
