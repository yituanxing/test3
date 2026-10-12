"""Adapters over upstream OpenCV face detectors.

YuNet is an ONNX model distributed separately by OpenCV Zoo (MIT), downloaded
only when the user opts in. Haar is the offline OpenCV fallback.
Neither detector performs identity recognition.
"""
import os
from pathlib import Path
import cv2

MODEL_FILE = Path(os.getenv('ZD_YUNET_MODEL',
    str(Path(__file__).resolve().parents[1] / 'models' / 'face_detection_yunet_2023mar.onnx')))


class HaarDetector:
    name = 'opencv-haar'

    def __init__(self):
        self.model = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_frontalface_default.xml')
        if self.model.empty():
            raise RuntimeError('OpenCV Haar model is missing')

    def detect_count(self, frame):
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gray = cv2.equalizeHist(gray)
        return len(self.model.detectMultiScale(gray, scaleFactor=1.1,
                                              minNeighbors=5, minSize=(40, 40)))


class YuNetDetector:
    name = 'opencv-zoo-yunet-2023mar'

    def __init__(self, model_path=MODEL_FILE):
        path = Path(model_path)
        if not path.is_file():
            raise FileNotFoundError(f'YuNet model not installed: {path}. Run scripts/install_yunet.py')
        if not hasattr(cv2, 'FaceDetectorYN'):
            raise RuntimeError('OpenCV build does not support FaceDetectorYN')
        self.model = cv2.FaceDetectorYN.create(str(path), '', (320, 320),
                                               score_threshold=0.75,
                                               nms_threshold=0.3, top_k=5000)

    def detect_count(self, frame):
        height, width = frame.shape[:2]
        self.model.setInputSize((width, height))
        _, faces = self.model.detect(frame)
        return 0 if faces is None else len(faces)


def choose_detector(mode=None):
    mode = (mode or os.getenv('ZD_FACE_DETECTOR', 'auto')).lower()
    if mode == 'haar':
        return HaarDetector()
    if mode == 'yunet':
        return YuNetDetector()
    if mode == 'auto':
        return YuNetDetector() if MODEL_FILE.is_file() else HaarDetector()
    raise ValueError('ZD_FACE_DETECTOR must be auto, yunet, or haar')


def detector_info():
    requested = os.getenv('ZD_FACE_DETECTOR', 'auto').lower()
    present = MODEL_FILE.is_file()
    effective = ('opencv-zoo-yunet-2023mar' if requested == 'yunet' or
                 (requested == 'auto' and present) else 'opencv-haar')
    return {'requested': requested, 'effective': effective,
            'yunet_model_present': present, 'yunet_model_path': str(MODEL_FILE)}
