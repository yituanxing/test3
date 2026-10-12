#!/usr/bin/env bash
# Genuine CC0 pedestrian footage -> FFmpeg -> RTSP/MediaMTX -> Frigate -> FastAPI/SQLite
# Never fabricate a person detection: an actual Frigate person event is required.
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p .ci-data reports
export ZD_DB_PATH="$PWD/.ci-data/bridge.sqlite3"
export FRIGATE_URL="http://127.0.0.1:5000"
COMPOSE="deploy/compose.frigate-demo.yml"
PUBLISH_PID=""
BACKEND_PID=""
cleanup() {
  rc=$?
  trap - EXIT
  [ -z "$PUBLISH_PID" ] || kill "$PUBLISH_PID" 2>/dev/null || true
  [ -z "$BACKEND_PID" ] || kill "$BACKEND_PID" 2>/dev/null || true
  # Capture failure-only diagnostics without dumping credentials, images or private footage.
  if [ "$rc" -ne 0 ]; then
    python - <<'PY' || true
import json
from pathlib import Path
from urllib.request import urlopen
from collections import Counter
output = {"stage": "live_frigate_failure_diagnostics"}
try:
    with urlopen("http://127.0.0.1:5000/api/stats", timeout=6) as response:
        stats = json.load(response)
    output["cameras"] = {name: {k: v for k, v in info.items()
        if k in ("camera_fps", "process_fps", "detection_fps", "detection_enabled", "motion_enabled", "skipped_fps", "ffmpeg_pid", "capture_pid")}
        for name, info in stats.get("cameras", {}).items()}
    output["detectors"] = {name: {k: v for k, v in info.items()
        if k in ("inference_speed", "detection_start", "pid")}
        for name, info in stats.get("detectors", {}).items()}
    output["detection_total"] = stats.get("detection_fps")
except Exception as exc:
    output["stats_error"] = str(exc)
try:
    with urlopen("http://127.0.0.1:5000/api/config", timeout=6) as response:
        config = json.load(response)
    camera = (config.get("cameras") or {}).get("demo", {})
    output["resolved_config"] = {
        "global_detect_enabled": (config.get("detect") or {}).get("enabled"),
        "camera_detect_enabled": (camera.get("detect") or {}).get("enabled"),
        "camera_motion_enabled": (camera.get("motion") or {}).get("enabled"),
        "camera_motion_threshold": (camera.get("motion") or {}).get("threshold"),
        "camera_motion_contour_area": (camera.get("motion") or {}).get("contour_area"),
        "camera_objects_track": (camera.get("objects") or {}).get("track"),
    }
except Exception as exc:
    output["config_error"] = str(exc)
try:
    with urlopen("http://127.0.0.1:5000/api/events?limit=200", timeout=6) as response:
        events = json.load(response)
    output["event_counts"] = dict(Counter(str(e.get("label", "?")) for e in events)) if isinstance(events, list) else {"invalid_shape": 1}
except Exception as exc:
    output["events_error"] = str(exc)
Path("reports").mkdir(exist_ok=True)
Path("reports/frigate-diagnostics.json").write_text(json.dumps(output, indent=2), encoding="utf-8")
print("Frigate failure diagnostics:", json.dumps(output, ensure_ascii=False), flush=True)
PY
  fi
  # Do not emit Frigate startup logs (may include generated credentials).
  docker compose -f "$COMPOSE" ps || true
  docker compose -f "$COMPOSE" down --remove-orphans >/dev/null 2>&1 || true
  exit "$rc"
}
trap cleanup EXIT

echo "::group::Download CC0 video from Wikimedia Commons"
VIDEO_URL='https://upload.wikimedia.org/wikipedia/commons/a/ae/Video_Codec_Test_pedestrian_area_1080p25.y4m.webm'
curl -fSL --retry 3 --connect-timeout 15 --max-time 120 \
  --user-agent 'ZD-Demo-OpenSource-Integration-Test/1.0 (anonymous video testing)' \
  "$VIDEO_URL" -o .ci-data/pedestrian-source.webm
test "$(stat -c %s .ci-data/pedestrian-source.webm)" -gt 50000
ffprobe -v error -select_streams v:0 -show_entries stream=width,height \
  -of default=noprint_wrappers=1 .ci-data/pedestrian-source.webm
ffmpeg -hide_banner -loglevel error -y -i .ci-data/pedestrian-source.webm \
  -t 15 -vf 'fps=5,scale=960:540' -an -pix_fmt yuv420p \
  -c:v libx264 -preset ultrafast -crf 26 -movflags +faststart .ci-data/pedestrian.mp4
test -s .ci-data/pedestrian.mp4
# Assert that our genuine source contains different decoded frames.
ffmpeg -hide_banner -loglevel error -i .ci-data/pedestrian.mp4 \
  -vf fps=1 -an -f framemd5 .ci-data/source-frame-hashes.txt
python - <<'PY'
from pathlib import Path
lines = Path(".ci-data/source-frame-hashes.txt").read_text().splitlines()
hashes = [line.split(",")[-1].strip() for line in lines if line and not line.startswith("#")]
print(f"Decoded video frames sampled={len(hashes)}, distinct={len(set(hashes))}", flush=True)
assert len(set(hashes)) > 2, "Source video does not contain enough frame-to-frame visual changes"
PY
echo "::endgroup::"

echo "::group::Start upstream services"
docker compose -f "$COMPOSE" pull
docker compose -f "$COMPOSE" up -d mediamtx
for i in $(seq 1 25); do
  if bash -c 'echo >/dev/tcp/127.0.0.1/8554' 2>/dev/null; then break; fi
  sleep 1
done
ffmpeg -hide_banner -loglevel warning -re -stream_loop -1 \
  -i .ci-data/pedestrian.mp4 -an -c:v copy -rtsp_transport tcp \
  -f rtsp rtsp://127.0.0.1:8554/demo >.ci-data/publisher.log 2>&1 &
PUBLISH_PID=$!
sleep 3
kill -0 "$PUBLISH_PID" || { echo "RTSP publisher exited" >&2; tail -n 50 .ci-data/publisher.log; exit 1; }
docker compose -f "$COMPOSE" up -d frigate
# Wait for the API to settle before querying JPEGs (startup may reset sockets).
for i in $(seq 1 40); do
  if curl -fsS --connect-timeout 2 --max-time 3 \
    http://127.0.0.1:5000/api/stats -o /dev/null 2>/dev/null; then
    break
  fi
  sleep 2
done
echo "::group::Independent motion and decoded-frame diagnostics"
python scripts/diagnose_motion.py --camera demo --frigate http://127.0.0.1:5000 --report reports/motion-diagnostics.json || true
echo "::endgroup::"
python -m uvicorn app.bridge:app --host 127.0.0.1 --port 8000 \
  >.ci-data/backend.log 2>&1 &
BACKEND_PID=$!
echo "::endgroup::"

echo "::group::Verify upstream genuine inference and downstream persistence"
python scripts/verify_live_frigate.py \
  --frigate http://127.0.0.1:5000 \
  --backend http://127.0.0.1:8000 \
  --camera demo --place-id 1 --require-person \
  --wait-seconds 100 --report reports/verify-live-frigate.json
echo "::endgroup::"
