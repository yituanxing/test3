#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
video="${1:-samples/test-pattern.mp4}"
url="${2:-rtsp://127.0.0.1:8554/demo}"
if ! command -v ffmpeg >/dev/null; then echo 'ffmpeg is not installed' >&2; exit 1; fi
if [ ! -f "$video" ]; then echo "No such video: $video" >&2; exit 1; fi
exec ffmpeg -hide_banner -loglevel warning -re -stream_loop -1 -i "$video" \
    -an -c:v copy -rtsp_transport tcp -f rtsp "$url"
