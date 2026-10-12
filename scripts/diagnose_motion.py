#!/usr/bin/env python3
"""Independent visual-input diagnosis: uses decoded frames from a real RTSP camera."""
import argparse
import json
import subprocess
import time
from pathlib import Path
from urllib.request import urlopen
from urllib.error import URLError


def fetch(url):
    with urlopen(url, timeout=10) as response:
        return response.read()


def pixel_bytes(jpg):
    p = subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-i", "pipe:0", "-vf",
         "scale=160:90", "-pix_fmt", "gray", "-f", "rawvideo", "pipe:1"],
        input=jpg, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15,
        check=True,
    )
    return p.stdout


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--frigate", required=True)
    p.add_argument("--camera", default="demo")
    p.add_argument("--report", default="reports/motion-diagnostics.json")
    args = p.parse_args()
    root = Path(args.report).parent
    root.mkdir(parents=True, exist_ok=True)
    result = {"video_source": "CC0 pedestrian source", "valid_frames": 0,
              "frame_change_fractions": [], "stats": [], "errors": []}
    raw_frames = []
    try:
        # The initial camera startup can take 20+ seconds on CPU CI.
        for i in range(15):
            try:
                content = fetch(f"{args.frigate}/api/{args.camera}/latest.jpg")
                if content[:3] == b"\xff\xd8\xff":
                    break
            except (URLError, TimeoutError) as exc:
                result["errors"].append(str(exc))
            time.sleep(2)
        else:
            raise RuntimeError("Could not retrieve a valid Frigate JPEG frame")
        for n in range(4):
            if n:
                time.sleep(4)
            jpg = fetch(f"{args.frigate}/api/{args.camera}/latest.jpg")
            if jpg[:3] != b"\xff\xd8\xff":
                raise RuntimeError("Expected JPEG but received unexpected format")
            (root / f"frigate-frame-{n}.jpg").write_bytes(jpg)
            raw_frames.append(pixel_bytes(jpg))
            stats = json.loads(fetch(f"{args.frigate}/api/stats"))
            cam = (stats.get("cameras") or {}).get(args.camera, {})
            result["stats"].append({k: cam.get(k) for k in
                ("camera_fps", "process_fps", "detection_fps", "detection_enabled")})
            result["valid_frames"] += 1
        for a, b in zip(raw_frames, raw_frames[1:]):
            assert len(a) == len(b) and len(a) > 0
            result["frame_change_fractions"].append(
                round(sum(abs(x-y) >= 12 for x,y in zip(a,b))/len(a),5))
        result["moving_content_observed"] = any(x > 0.005 for x in result["frame_change_fractions"])
        print("Actual processed-camera frame change / detector sampling:",
              json.dumps(result, ensure_ascii=False), flush=True)
    except Exception as exc:
        result["errors"].append(f"{type(exc).__name__}: {exc}")
        print("Visual diagnostic error:", result["errors"][-1], flush=True)
    finally:
        Path(args.report).write_text(json.dumps(result, indent=2), encoding="utf8")
    return 0 if result["valid_frames"] >= 2 else 1


if __name__ == "__main__":
    raise SystemExit(main())
