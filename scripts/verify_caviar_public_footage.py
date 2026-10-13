"""Explicitly opted-in CI proof with LICENSED *real* CAVIAR CCTV footage.

Downloads at most two ~2MB official clips and CVML GT XML files, proves
OpenCV-decoded MPEG frames and annotated overlays through the real FastAPI
auth/DB import endpoint. Clips stay in ephemeral runner's data dir; release
ZIP and CI artifacts contain only audit JSON and two annotated stills.
"""
import hashlib
import json
import os
import tempfile
from pathlib import Path

def main():
    with tempfile.TemporaryDirectory(prefix='zd-real-caviar-') as d:
        os.environ['ZD_DATA_DIR']=d
        from app import main as server
        from app import access
        from fastapi.testclient import TestClient
        from app import cctv_dataset

        server.setup_db()
        with server.db() as db:
            root=db.execute('SELECT id FROM regions WHERE level=1 LIMIT 1').fetchone()['id']
            access.create_user(db,'publiccaviar','public-video-proof-long-password',root)
            place=db.execute("SELECT id FROM places WHERE name='测试场所 A'").fetchone()['id']
        with TestClient(server.app) as client:
            login=client.post('/api/auth/login',json={
                'username':'publiccaviar','password':'public-video-proof-long-password'})
            assert login.status_code==200,login.text
            auth={'Authorization':'Bearer '+login.json()['access_token']}
            report={'source':'CAVIAR EC-funded university public dataset',
                    'license':cctv_dataset.CAVIAR_LICENSE,
                    'accessed_from':cctv_dataset.DATASET_ROOT,
                    'fixture_type':'actual_public_human_CCTV_not_synthetic',
                    'same_place_id_for_both_cameras':place,
                    'verified_cameras':[]}
            target=Path('reports')
            target.mkdir(parents=True,exist_ok=True)
            for view in ('corridor','front'):
                response=client.post('/api/cctv/datasets/import',headers=auth,
                    json={'place_id':place,'view':view,
                          'scenario':'enter_exit_crossing_paths_1'})
                if response.status_code!=201:
                    raise AssertionError(f'{view}: actual official media import failed: HTTP {response.status_code}: {response.text[:280]}')
                got=response.json()
                assert got['status']=='imported',got
                assert got['annotation_boxes']>0,got
                # Regression guard for the actual CAVIAR MPEG2 codec quirk:
                # CAP_PROP_FRAME_COUNT previously claimed 4; real decode is 383.
                assert got['video']['frames'] >= 300,got
                assert got['xml_frames'] >= 300,got
                assert got['annotation_boxes'] >= 100,got
                assert got['video']['frame_count_method']=='actual_decoded_frames_not_unreliable_mpeg_header'
                assert got['track_semantics']=='camera_local_ground_truth_not_global_identity'
                with server.db() as db:
                    row=db.execute('SELECT uri FROM sources WHERE id=?',
                                   (got['source_id'],)).fetchone()
                    data=Path(row['uri']).read_bytes()
                activity=client.get(f"/api/cctv/sources/{got['source_id']}/activity",headers=auth)
                assert activity.status_code==200,activity.text
                tracks=activity.json()
                assert tracks['local_track_count']>0,tracks
                assert tracks['frame_annotation_count']>0
                selected=min(80,tracks['local_tracks'][0]['last_frame'])
                assert selected > 4,"Actual mid-clip frame test must exceed false MPEG frame header count"
                photo=client.get(f"/api/cctv/sources/{got['source_id']}/frame?frame={selected}",
                                 headers=auth)
                assert photo.status_code==200,photo.text[:150]
                assert photo.content[:2]==b'\xff\xd8'
                assert photo.headers['x-annotations']=='CAVIAR manual ground truth'
                (target/f'caviar-{view}-gt.jpg').write_bytes(photo.content)
                # Feed ORIGINAL official GT boxes to a MATURE EXTERNAL
                # ByteTrack tracker and report local tracking continuity.
                # This is not an automatic detector or a cross-camera ID test.
                from app import tracker_adapters
                with server.db() as db:
                    tracker_metrics=tracker_adapters.run_caviar_tracking(
                        db,got['source_id'],max_frames=500)
                assert tracker_metrics['frames_processed']>=300
                assert tracker_metrics['ground_truth_boxes']>=100
                assert tracker_metrics['automatic_tracking_ran'] is True
                assert tracker_metrics['automatic_person_detection_ran'] is False
                assert tracker_metrics['cross_camera_matching_ran'] is False
                assert tracker_metrics['assignment_coverage'] is not None
                report['verified_cameras'].append({
                    'existing_bytetrack_baseline':tracker_metrics,
                    'camera_view':view,'source_id':got['source_id'],
                    'video_bytes':len(data),
                    'video_sha256':hashlib.sha256(data).hexdigest(),
                    'decoded_frames':got['video']['frames'],
                    'resolution':[got['video']['width'],got['video']['height']],
                    'fps':got['video']['fps'],
                    'cvml_frame_count':got['xml_frames'],
                    'annotated_person_boxes':got['annotation_boxes'],
                    'camera_local_tracks':tracks['local_track_count'],
                    'actual_annotated_jpeg_frame':selected,
                    'provenance':'university manually annotated camera-local boxes'
                })
            assert len(report['verified_cameras'])==2
            assert len({x['source_id'] for x in report['verified_cameras']})==2
            report['tracker_vendor']='Roboflow trackers 2.6.0 / ByteTrack'
            report['baseline_kind']='published manual CVML detection boxes fed to existing ByteTrack'
            report['global_id_ground_truth']='NOT PRESENT; cross-camera ReID MUST NOT be scored'
            report['auto_detector_evaluated']=False
            report['verdict']='PASS'
            report['not_proven']=[
                'automatic human detection or precision/recall',
                'face identity recognition','cross-camera real-person re-identification',
                'two different venues or cross-venue movements',
                'persons per day, missing persons or personal risk'
            ]
            (target/'verify-caviar-fixed-cctv.json').write_text(
                json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
            print(json.dumps(report,ensure_ascii=False))
if __name__=='__main__':
    main()
