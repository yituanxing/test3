"""Explicit opt-in OpenCV Zoo SFace model fetch; never bundle weights.

Legal note: OpenCV Zoo's model directory states Apache 2.0, but the exact
2021dec weight's training data/commercial redistribution provenance is not
fully clarified by upstream (see GitHub opencv_zoo issue 313).
"""
from hashlib import sha256
from pathlib import Path
from urllib.request import urlopen
import os
import tempfile

DEST=Path(__file__).resolve().parents[1]/'models'/'face_recognition_sface_2021dec.onnx'
URL=('https://huggingface.co/opencv/opencv_zoo/resolve/main/'
     'models/face_recognition_sface/face_recognition_sface_2021dec.onnx')
EXPECTED_SHA256='0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79'


def install():
    DEST.parent.mkdir(parents=True,exist_ok=True)
    if DEST.is_file() and sha256(DEST.read_bytes()).hexdigest()==EXPECTED_SHA256:
        print('SFace already installed, SHA256 verified')
        return
    with urlopen(URL,timeout=65) as response:
        model=response.read(40_000_000)
    if len(model)!=38_696_353 or sha256(model).hexdigest()!=EXPECTED_SHA256:
        raise RuntimeError('Unexpected SFace model SHA256; refusing to use untrusted download')
    with tempfile.NamedTemporaryFile(dir=DEST.parent, suffix='.onnx',delete=False) as tmp:
        tmp.write(model)
        tmp_name=tmp.name
    try:
        import cv2
        cv2.FaceRecognizerSF.create(tmp_name,'')
        os.replace(tmp_name,DEST)
    finally:
        if os.path.exists(tmp_name):os.unlink(tmp_name)
    print('Installed and validated SFace local model (not included in demo ZIP)')


if __name__=='__main__':
    install()
