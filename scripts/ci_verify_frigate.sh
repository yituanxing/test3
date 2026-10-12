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
python -m uvicorn app.bridge:app --host 127.0.0.1 --port 8000 \
  >.ci-data/backend.log 2>&1 &
BACKEND_PID=$!
echo "::endgroup::"

echo "::group::Verify upstream genuine inference and downstream persistence"
python scripts/verify_live_frigate.py \
  --frigate http://127.0.0.1:5000 \
  --backend http://127.0.0.1:8000 \
  --camera demo --place-id 1 --require-person \
  --wait-seconds 210 --report reports/verify-live-frigate.json
echo "::endgroup::"
