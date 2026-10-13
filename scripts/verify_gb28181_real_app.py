"""End-to-end GB28181 -> REAL WVP -> RTSP -> test3/OpenCV -> SQLite.

Invoked inside ci_verify_gb28181_sip.sh while the pinned WVP, real GB
simulator and ZLMediaKit are still running. NO WVP mock or captured JPEG is
used for the application acceptance path. The NASA public-domain astronaut
video repeats ONE annotated human still through actual RTP/PS. This is a
face-localization smoke, NOT distinct-person count, real-world recall or
hardware interoperability.
"""
import json
import os
import secrets
import sys
from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "reports/verify-gb28181-real-app.json"
DEVICE = "34020000001320000001"
CHANNEL = "34020000001320000002"


def expect(response, label, code=200):
    if response.status_code != code:
        raise AssertionError(f"{label}: HTTP {response.status_code}")
    return response.json()


def main():
    result = {
        "verdict": "FAIL",
        "control_plane": "real pinned WVP-GB28181-pro, no HTTP test double",
        "media_plane": "actual GB28181 simulator H264 PS/RTP via ZLMediaKit RTSP",
        "test3_adapter": "actual FastAPI API routing through TestClient",
        "model": "anonymous frame-level face geometry; no identity recognition",
        "face_detection_truth": "one manually annotated NASA astronaut still, repeated",
        "representative_accuracy_benchmark": False,
        "distinct_people_count": "NOT_ESTIMATED",
        "recorded_playback": "NOT_TESTED",
        "verified_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    session_id = None
    source_id = None
    try:
        assert os.environ.get("GB28181_WVP_URL") == "http://127.0.0.1:18978", "WVP URL must be local actual CI server"
        assert os.environ.get("GB28181_RTSP_HOSTS") == "127.0.0.1", "RTSP allowlist not pinned"
        assert os.environ.get("ZD_DATA_DIR"), "refuse to use a shared demo database"
        from app import main as demo, access

        with demo.db() as c:
            root = c.execute("SELECT id FROM regions WHERE level=1 LIMIT 1").fetchone()["id"]
            place = c.execute("SELECT id FROM places WHERE active=1 ORDER BY id LIMIT 1").fetchone()["id"]
            user = "ci_real_gb_e2e"
            password = secrets.token_urlsafe(32)
            access.create_user(c, user, password, root)

        with TestClient(demo.app) as client:
            login = expect(client.post("/api/auth/login", json={
                "username": user, "password": password
            }), "login")
            headers = {"Authorization": "Bearer " + login["access_token"]}
            status = expect(client.get("/api/gb28181/status", headers=headers), "GB status")
            assert status["configured"], "GB adapter not configured"

            device_list = expect(client.get("/api/gb28181/devices",
                headers=headers), "real WVP devices")
            devices = device_list["devices"]
            assert any(row["device_id"] == DEVICE for row in devices), "registered SIP device absent from app API"
            channels = expect(client.get(
                f"/api/gb28181/devices/{DEVICE}/channels", headers=headers),
                "real WVP Catalog channels")
            assert any(row["channel_id"] == CHANNEL for row in channels["channels"]), "real Catalog channel absent from app API"
            result["real_wvp_device_discovered"] = True
            result["real_wvp_channel_discovered"] = True

            bound = expect(client.post("/api/gb28181/bindings", headers=headers, json={
                "place_id": place, "device_id": DEVICE, "channel_id": CHANNEL,
                "name": "Synthetic GB28181 E2E CI camera",
            }), "bind real channel")
            binding_id = bound["id"]
            result["binding_created"] = True

            try:
                started = expect(client.post(
                    f"/api/gb28181/bindings/{binding_id}/live",
                    headers=headers), "real WVP INVITE via test3")
                session_id = started["session_id"]
                source_id = started["source_id"]
                assert started["source_kind"] == "rtsp" and started["state"] == "active"
                with demo.db() as c:
                    db_source = c.execute(
                        "SELECT kind,uri,place_id,enabled FROM sources WHERE id=?",
                        (source_id,)).fetchone()
                    assert db_source and db_source["kind"] == "rtsp"
                    assert db_source["uri"].startswith("rtsp://127.0.0.1:10002/")
                    assert db_source["place_id"] == place and db_source["enabled"] == 1
                result["real_rtsp_source_created"] = True

                # This endpoint invokes genuine cv2.VideoCapture + CPU inference
                # on WVP's RTSP stream, not the previous FFmpeg JPEG artifact.
                processed = expect(client.post(
                    f"/api/sources/{source_id}/process?max_frames=30&sample_every=5",
                    headers=headers), "OpenCV real GB RTP/PS source")
                assert processed["frames_decoded"] >= 10, "OpenCV did not decode live GB video"
                assert processed["frames_analyzed"] >= 2
                assert processed["new_samples"] >= 2, "no durable anonymous frames"
                assert processed["face_detections"] >= 2, (
                    "human face was not detected after real GB RTP/PS decoding"
                )
                result.update({
                    "opencv_frames_decoded": processed["frames_decoded"],
                    "cpu_frames_analyzed": processed["frames_analyzed"],
                    "new_video_samples": processed["new_samples"],
                    "face_boxes": processed["face_detections"],
                })

                observed = expect(client.get(
                    f"/api/video/observations?source_id={source_id}",
                    headers=headers), "real GB observations")
                assert observed["mode"] == "real_video_anonymous"
                assert observed["quality_totals"]["sampled_frame_records"] == processed["new_samples"]
                assert len(observed["observations"]) == processed["new_samples"]
                assert all(o["place_id"] == place and o["source_id"] == source_id
                           and o["source_revision"] is None
                           for o in observed["observations"])
                # Source independent of the detector: a manually marked public
                # NASA astronaut still. Verify a real box overlaps the known
                # face after GB28181 transport, not just a nonzero counter.
                from scripts.verify_face_box_truth import intersection_over_union
                annotation = json.loads((ROOT / "samples/face_box_truth_v1.json").read_text())
                positive = next(c for c in annotation["cases"]
                    if c["path"] == "samples/astronaut-face-test.mp4")
                reference = positive["reference_face_boxes_xywh"][0]
                refsize = annotation["coordinate_reference"]
                matched = 0
                highest_iou = 0.0
                all_boxes = 0
                for event in observed["observations"]:
                    boxes = event["boxes"]
                    all_boxes += len(boxes)
                    for box in boxes:
                        scaled = [
                            round(reference[0] * box["frame_width"] / refsize["width"]),
                            round(reference[1] * box["frame_height"] / refsize["height"]),
                            round(reference[2] * box["frame_width"] / refsize["width"]),
                            round(reference[3] * box["frame_height"] / refsize["height"]),
                        ]
                        overlap = intersection_over_union(scaled, [
                            box["x"], box["y"], box["w"], box["h"]
                        ])
                        highest_iou = max(highest_iou, overlap)
                        if overlap >= 0.35:
                            matched += 1
                            break
                result.update({
                    "actual_face_boxes_in_sqlite": all_boxes,
                    "matched_annotated_face_frames": matched,
                    "maximum_face_iou": round(highest_iou, 4),
                    "reused_single_still_not_unique_people": True,
                })
                assert matched >= 2, (
                    "no reliable overlap with manually annotated astronaut face"
                )
                result["durable_place_linked_video_observations"] = True

            finally:
                if session_id is not None:
                    stopped = expect(client.post(
                        f"/api/gb28181/sessions/{session_id}/stop",
                        headers=headers), "stop WVP session")
                    assert stopped["state"] == "stopped" and stopped["source_enabled"] is False
                    with demo.db() as c:
                        active = c.execute(
                            "SELECT enabled FROM sources WHERE id=?", (source_id,)
                        ).fetchone()
                        assert active and active["enabled"] == 0, "stopped source still enabled"
                    result["stream_stopped_and_source_disabled"] = True

        result["verdict"] = "PASS"
        return 0
    except Exception as exc:
        # Do not print RTSP playback grants, credentials or individual video data.
        result["error_type"] = type(exc).__name__
        result["error_stage"] = next(
            (name for name in (
                "stream_stopped_and_source_disabled",
                "durable_place_linked_video_observations",
                "real_rtsp_source_created",
                "binding_created",
                "real_wvp_channel_discovered",
                "real_wvp_device_discovered",
            ) if not result.get(name)), "unknown")
        print("GB28181 application integration failed:",
              result["error_type"], result["error_stage"], flush=True)
        return 1
    finally:
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.write_text(json.dumps(result, indent=2, ensure_ascii=False),
                          encoding="utf-8")
        print("REAL WVP -> test3 -> OpenCV -> SQLite verdict:",
              result["verdict"], flush=True)


if __name__ == "__main__":
    sys.exit(main())
