"""Optional model installer. The package doesn't redistribute model binaries."""
from pathlib import Path
from hashlib import sha256
from urllib.request import urlopen
import os
import tempfile

DEST = Path(__file__).resolve().parents[1] / 'models' / 'face_detection_yunet_2023mar.onnx'
URL = ('https://huggingface.co/opencv/opencv_zoo/resolve/main/'
       'models/face_detection_yunet/face_detection_yunet_2023mar.onnx')
EXPECTED_SHA256 = '8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4'


def install():
    DEST.parent.mkdir(parents=True, exist_ok=True)
    if DEST.is_file() and sha256(DEST.read_bytes()).hexdigest() == EXPECTED_SHA256:
        print(f'YuNet already installed and SHA256 verified: {DEST}')
        return
    with urlopen(URL, timeout=50) as response:
        body = response.read(1024 * 1024)  # upstream model ~233 KB
    if len(body) != 232589 or sha256(body).hexdigest() != EXPECTED_SHA256:
        raise RuntimeError('YuNet size/SHA256 mismatch (possibly Git LFS pointer); refusing to install')
    if body.startswith(b'version https://git-lfs') or body.lstrip().startswith(b'<'):
        raise RuntimeError('Received a Git LFS pointer or HTML page, not an ONNX model')
    with tempfile.NamedTemporaryFile(dir=DEST.parent, suffix='.onnx', delete=False) as f:
        f.write(body)
        tmp = f.name
    try:
        # Validate weights with OpenCV before changing the installed model.
        import cv2
        cv2.FaceDetectorYN.create(str(tmp), '', (320, 320), 0.75, 0.3, 5000)
        os.replace(tmp, DEST)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    print(f'YuNet installed and loaded: {DEST} ({len(body)} bytes)')


if __name__ == '__main__':
    install()
