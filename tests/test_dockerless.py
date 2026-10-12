"""Docker-free, public-domain face sample and local-network input validation."""
import cv2
import pytest
from fastapi import HTTPException
from app.admin import validate_source
from app.detector import HaarDetector
from app.main import ROOT


def test_loopback_udp_can_be_used_for_offline_transport_smoke():
    validate_source('rtsp', 'udp://127.0.0.1:18088?fifo_size=50000')
    validate_source('rtsp', 'rtsp://127.0.0.1:8554/demo')
    with pytest.raises(HTTPException):
        validate_source('rtsp', 'udp://192.168.1.99:18888')
    with pytest.raises(HTTPException):
        validate_source('rtsp', 'udp://example.org:18888')


def test_public_domain_portrait_model_real_detection():
    path = ROOT / 'samples' / 'astronaut-face-test.mp4'
    assert path.exists()
    cap = cv2.VideoCapture(str(path))
    try:
        opened, frame = cap.read()
        assert opened and frame is not None
        assert HaarDetector().detect_count(frame) >= 1
    finally:
        cap.release()
