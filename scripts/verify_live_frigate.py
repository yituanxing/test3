#!/usr/bin/env python3
"""Live acceptance: use only real events returned by a running Frigate detector."""
import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path
import httpx


def checked_json(client, method, url, **kwargs):
    r = client.request(method, url, **kwargs)
    r.raise_for_status()
    return r.json()


def run(args):
    report = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "video_source": "CC0 Wikimedia Commons pedestrian-area source",
        "camera": args.camera,
        "verified": False,
        "identity_inference_performed": False,
        "camera_fps": 0,
        "genuine_person_event_count": 0,
        "sqlite_event_count": 0,
        "checks": {},
    }
    path = Path(args.report)
    path.parent.mkdir(parents=True, exist_ok=True)

    try:
        with httpx.Client(timeout=8) as client:
            end_time = time.monotonic() + args.wait_seconds

            def await_service(service, url):
                while time.monotonic() < end_time:
                    try:
                        data = checked_json(client, "GET", url)
                        report["checks"][service] = "PASS"
                        return data
                    except (httpx.HTTPError, ValueError):
                        time.sleep(2)
                raise AssertionError(f"{service} not responding before deadline")

            assert await_service("backend", args.backend + "/api/health")["status"] == "ok"
            binding = checked_json(
                client, "POST", args.backend + "/api/frigate/cameras",
                json={"camera": args.camera, "place_id": args.place_id})
            assert binding["place_id"] == args.place_id

            while time.monotonic() < end_time:
                try:
                    stats = checked_json(client, "GET", args.frigate + "/api/stats")
                    fps = (stats.get("cameras") or {}).get(args.camera, {}).get("camera_fps", 0)
                    if isinstance(fps, (int, float)) and fps > 0:
                        report["camera_fps"] = fps
                        report["checks"]["real_rtsp_decode"] = "PASS"
                        print("Frigate actual camera_fps =", fps, flush=True)
                        break
                except (httpx.HTTPError, ValueError):
                    pass
                time.sleep(3)
            else:
                raise AssertionError("Frigate never reported nonzero camera_fps")

            real_events = []
            while time.monotonic() < end_time:
                try:
                    events = checked_json(
                        client, "GET",
                        args.frigate + "/api/events",
                        params={"camera": args.camera, "label": "person", "limit": 200})
                    if not isinstance(events, list):
                        raise ValueError("Frigate events endpoint returned non-list")
                    real_events = [
                        e for e in events if e.get("id") and e.get("camera") == args.camera
                        and e.get("label") == "person" and e.get("false_positive") is not True
                    ]
                    if real_events:
                        report["checks"]["person_inference"] = "PASS"
                        report["genuine_person_event_count"] = len(real_events)
                        print("Genuine Frigate person events =", len(real_events), flush=True)
                        break
                except (httpx.HTTPError, ValueError):
                    pass
                time.sleep(4)
            if args.require_person and not real_events:
                raise AssertionError("No true person event was generated from the downloaded footage")

            sync1 = checked_json(client, "POST", args.backend + "/api/frigate/sync", params={"limit": 200})
            after_first = checked_json(client, "GET", args.backend + "/api/frigate/events")
            persisted = {e["event_id"] for e in after_first}
            missing = {e["id"] for e in real_events} - persisted
            assert not missing, f"Events not found in SQLite: {sorted(missing)[:5]}"
            report["sqlite_event_count"] = len(after_first)
            report["checks"]["sqlite_persistence"] = "PASS"
            print("Sync result =", sync1, flush=True)

            checked_json(client, "POST", args.backend + "/api/frigate/sync", params={"limit": 200})
            after_second = checked_json(client, "GET", args.backend + "/api/frigate/events")
            ids = [e["event_id"] for e in after_second]
            assert len(ids) == len(set(ids))
            assert len(after_second) >= len(after_first)
            report["checks"]["idempotent_resync"] = "PASS"
            count_stats = checked_json(client, "GET", args.backend + "/api/statistics")
            assert count_stats["counts_are_unique_people"] is False
            report["checks"]["statistics_semantics"] = "PASS"
            report["verified"] = bool(real_events and not missing)
        report["verdict"] = "PASS"
        return 0
    except Exception as e:
        report["verdict"] = "FAIL"
        report["error"] = f"{type(e).__name__}: {e}"
        print("FAIL:", report["error"], flush=True)
        return 1
    finally:
        path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print("Evidence saved:", path, "verdict:", report.get("verdict"), flush=True)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--frigate", default="http://127.0.0.1:5000")
    p.add_argument("--backend", default="http://127.0.0.1:8000")
    p.add_argument("--camera", default="demo")
    p.add_argument("--place-id", type=int, default=1)
    p.add_argument("--wait-seconds", type=int, default=210)
    p.add_argument("--require-person", action="store_true")
    p.add_argument("--report", default="reports/verify-live-frigate.json")
    raise SystemExit(run(p.parse_args()))
