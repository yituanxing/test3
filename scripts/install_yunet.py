"""Optional model installer. The package doesn't redistribute model binaries."""
from pathlib import Path
from urllib.request import urlopen
import os
import tempfile

DEST = Path(__file__).resolve().parents[1] / 'models' / 'face_detection_yunet_2023mar.onnx'
URL = ('https://raw.githubusercontent.com/opencv/opencv_zoo/main/'
       'models/face_detection_yunet/face_detection_yunet_2023mar.onnx')


def install():
    DEST.parent.mkdir(parents=True, exist_ok=True)
    with urlopen(URL, timeout=25) as response:
        body = response.read(1024 * 1024)  # upstream model ~233 KB
    if not (100_000 <= len(body) <= 800_000):
        raise RuntimeError('Downloaded artifact has unexpected size; refusing to install')
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
