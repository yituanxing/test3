"""Actual OpenCV Haar/YuNet face detection boxes, without biometric identity.

YuNet ONNX model is an optional externally installed dependency. Haar is the
bundled fallback. Outputs are geometric detections, not people, face vectors,
matched identities, or validated precision/recall.
"""
import os
from pathlib import Path
import cv2

MODEL_FILE = Path(os.getenv('ZD_YUNET_MODEL',
    str(Path(__file__).resolve().parents[1] / 'models' / 'face_detection_yunet_2023mar.onnx')))


def box_quality(frame, box):
    """Heuristic image diagnostics, *not* a classifier or ground-truth score.

    A 'candidate' result only means sufficiently large/sharp according to
    an illustrative, uncalibrated threshold; never means a verified identity.
    """
    height, width = frame.shape[:2]
    x,y,w,h = (int(box[k]) for k in ('x','y','w','h'))
    if x < 0 or y < 0 or w <= 0 or h <= 0 or x+w > width or y+h > height:
        raise ValueError('Detector produced an out-of-frame rectangle')
    patch = frame[y:y+h,x:x+w]
    gray = cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY)
    sharpness = round(float(cv2.Laplacian(gray,cv2.CV_64F).var()),2)
    reasons=[]
    if min(w,h)<50: reasons.append('small_face')
    if sharpness<60: reasons.append('low_sharpness')
    # 'candidate' is NOT equivalent to a face quality pass for identity matching.
    return {'quality_state':'candidate' if not reasons else 'needs_review',
            'quality_reasons':reasons,'sharpness_laplacian_var':sharpness,
            'face_area_px':w*h,'assessment':'uncalibrated_heuristic'}


class HaarDetector:
    name = 'opencv-haar'

    def __init__(self):
        self.model = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_frontalface_default.xml')
        if self.model.empty():
            raise RuntimeError('OpenCV Haar model is missing')

    def detect(self, frame):
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gray = cv2.equalizeHist(gray)
        found = self.model.detectMultiScale(gray, scaleFactor=1.1,
                                            minNeighbors=5, minSize=(40,40))
        return [dict(x=int(x),y=int(y),w=int(w),h=int(h),detector_score=None)
                for x,y,w,h in found]

    def detect_count(self, frame):
        return len(self.detect(frame))


class YuNetDetector:
    name = 'opencv-zoo-yunet-2023mar'

    def __init__(self, model_path=MODEL_FILE):
        path = Path(model_path)
        if not path.is_file():
            raise FileNotFoundError(f'YuNet model not installed: {path}. Run scripts/install_yunet.py')
        if not hasattr(cv2, 'FaceDetectorYN'):
            raise RuntimeError('OpenCV build does not support FaceDetectorYN')
        self.model = cv2.FaceDetectorYN.create(str(path),'',(320,320),
                                               score_threshold=0.75,
                                               nms_threshold=0.3,top_k=5000)

    def detect(self, frame):
        height,width=frame.shape[:2]
        self.model.setInputSize((width,height))
        _,faces=self.model.detect(frame)
        if faces is None:
            return []
        boxes=[]
        for f in faces:
            # YuNet float rectangles can extend slightly outside input frame.
            x=max(0,int(round(float(f[0]))));y=max(0,int(round(float(f[1]))))
            w=min(int(round(float(f[2]))),width-x)
            h=min(int(round(float(f[3]))),height-y)
            if 0<=x<width and 0<=y<height and w>0 and h>0:
                boxes.append(dict(x=x,y=y,w=w,h=h,detector_score=float(f[14])))
        return boxes

    def detect_count(self, frame):
        return len(self.detect(frame))


def choose_detector(mode=None):
    mode = (mode or os.getenv('ZD_FACE_DETECTOR','auto')).lower()
    if mode == 'haar': return HaarDetector()
    if mode == 'yunet': return YuNetDetector()
    if mode == 'auto':
        return YuNetDetector() if MODEL_FILE.is_file() else HaarDetector()
    raise ValueError('ZD_FACE_DETECTOR must be auto, yunet, or haar')


def detector_info():
    requested = os.getenv('ZD_FACE_DETECTOR','auto').lower()
    present=MODEL_FILE.is_file()
    effective = ('opencv-zoo-yunet-2023mar' if requested == 'yunet' or
                 (requested=='auto' and present) else 'opencv-haar')
    return {'requested':requested,'effective':effective,
            'yunet_model_present':present,'yunet_model_path':str(MODEL_FILE)}
