"""External component contract checks without requiring Docker/network access."""
from pathlib import Path
import os
import subprocess
import pytest
import numpy as np
from fastapi.testclient import TestClient
from app import detector
from app.main import app

ROOT = Path(__file__).resolve().parents[1]
client = TestClient(app)


def test_actual_ffprobe_on_bundled_h264_video():
    if not subprocess.run(['which', 'ffprobe'], capture_output=True).returncode == 0:
        pytest.skip('FFmpeg binary not installed')
    src = next(x for x in client.get('/api/sources').json()
               if x['name'] == '内置测试视频（无真人）')
    response = client.get(f"/api/sources/{src['id']}/probe")
    assert response.status_code == 200, response.text
    streams = response.json()['video_streams']
    assert streams and streams[0]['codec_name'] == 'h264'
    assert streams[0]['width'] == 640
    assert streams[0]['height'] == 360


def test_real_haar_detector_no_false_positive_on_empty_frame():
    model = detector.choose_detector('haar')
    assert model.name == 'opencv-haar'
    assert model.detect_count(np.zeros((360, 640, 3), dtype=np.uint8)) == 0


def test_explicit_missing_yunet_fails_instead_of_silent_fallback(monkeypatch, tmp_path):
    missing = tmp_path / 'not-installed.onnx'
    with pytest.raises(FileNotFoundError):
        detector.YuNetDetector(missing)


def test_media_setup_is_upstream_not_custom_server():
    compose = (ROOT / 'deploy' / 'compose.media.yml').read_text()
    assert 'bluenviron/mediamtx:' in compose
    assert '8554:8554' in compose
    script = (ROOT / 'scripts' / 'publish_sample.sh').read_text()
    assert 'ffmpeg' in script
    assert '-stream_loop -1' in script


def test_event_detector_provenance():
    src = next(x for x in client.get('/api/sources').json()
               if x['name'] == '内置测试视频（无真人）')
    got = client.post(f"/api/sources/{src['id']}/process?max_frames=10&sample_every=5")
    assert got.status_code == 200, got.text
    assert got.json()['detector'] == 'opencv-haar'  # no optional ONNX weights bundled
    observed = client.get('/api/events').json()
    assert any(row['origin'] == 'video' and row['detector'] == 'opencv-haar'
               for row in observed)
