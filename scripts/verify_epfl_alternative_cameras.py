"""Bounded research-only comparison of a DIFFERENT real synchronized camera pair.

EPFL CVLab Laboratory 4-person sequence, cameras 0 & 1.  This experiment
does not replace the accepted CAVIAR evidence or certify cross-camera identity.
Licensed for research use, copyright CVLab-EPFL. No raw video is published.
"""
from __future__ import annotations

import hashlib
import json
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path

import cv2

ROOT = "https://documents.epfl.ch/groups/c/cv/cvlab-pom-video1/www"
MAX_BYTES_PER_CAMERA = 180 * 1024 * 1024
# Same time offsets for both cameras: do NOT only inspect the first 24 frames
# (CAVIAR's earlier test covered less than one second).
SAMPLED_FRAMES = list(range(126, 1207, 60))  # 5.0..48.2 seconds at 25fps
REPORT_DIR = Path("reports")


def _download(url: str, output: Path) -> dict:
    parsed = urllib.parse.urlsplit(url)
    if (parsed.scheme != "https" or parsed.hostname != "documents.epfl.ch"
            or not parsed.path.startswith("/groups/c/cv/cvlab-pom-video1/www/4p-c")
            or not parsed.path.endswith(".avi")):
        raise ValueError("EPFL official video URL allowlist violation")
    sha = hashlib.sha256()
    count = 0
    request = urllib.request.Request(
        url, headers={"User-Agent": "ZD-research-cctv-video-comparison/1.0"})
    with urllib.request.urlopen(request, timeout=90) as response:
        final = urllib.parse.urlsplit(response.geturl())
        if final.scheme != "https" or final.hostname != "documents.epfl.ch":
            raise RuntimeError("EPFL video redirected away from approved host")
        with output.open("wb") as stream:
            while True:
                part = response.read(256 * 1024)
                if not part:
                    break
                count += len(part)
                if count > MAX_BYTES_PER_CAMERA:
                    raise RuntimeError("EPFL video exceeded bounded CI download limit")
                sha.update(part)
                stream.write(part)
    if count < 1024:
        raise RuntimeError("EPFL download was empty")
    return {"bytes": count, "sha256": sha.hexdigest(), "official_url": url}


def _analyze(path: Path, camera: int, model) -> dict:
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        raise RuntimeError(f"EPFL camera {camera} could not be decoded by OpenCV")
    samples = []
    preview = []
    decoded = 0
    selected = set(SAMPLED_FRAMES)
    try:
        while decoded < max(SAMPLED_FRAMES):
            ok, frame = capture.read()
            if not ok:
                break
            decoded += 1
            if decoded not in selected:
                continue
            if frame is None or frame.size == 0:
                raise RuntimeError("OpenCV returned invalid video frame")
            detected = model.predict(
                source=frame, classes=[0], conf=0.25, imgsz=640,
                device="cpu", verbose=False)[0].boxes
            xyxy = detected.xyxy.cpu().numpy()
            conf = detected.conf.cpu().numpy()
            samples.append({
                "frame": decoded,
                "second_at_25fps": round((decoded - 1) / 25, 2),
                "person_boxes": len(xyxy),
                "boxes_xyxy": [
                    [round(float(v), 1) for v in box] for box in xyxy
                ],
                "confidences": [round(float(v), 3) for v in conf],
            })
            image = frame.copy()
            for box, score in zip(xyxy, conf):
                x1, y1, x2, y2 = [int(round(float(v))) for v in box]
                cv2.rectangle(image, (x1, y1), (x2, y2), (20, 180, 255), 2)
                cv2.putText(image, f"person {float(score):.2f}",
                            (max(0, x1), max(12, y1 - 4)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.42, (20, 180, 255), 1)
            # Contact sheet displays sampling time and DOES NOT imply GT/identity.
            image = cv2.resize(image, (400, 280), interpolation=cv2.INTER_AREA)
            cv2.rectangle(image, (0, 0), (400, 27), (20, 20, 20), -1)
            cv2.putText(image, f"EPFL cam{camera}  {samples[-1]['second_at_25fps']:.1f}s"
                        "  YOLO model, no GT", (4, 18),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.42, (255, 255, 255), 1)
            preview.append(image)
    finally:
        capture.release()
    if len(samples) < 12:
        raise RuntimeError(
            f"EPFL camera {camera} produced only {len(samples)} sampled frames "
            f"from {decoded} decoded frames")
    import numpy as np
    while len(preview) % 4:
        preview.append(np.zeros((280, 400, 3), dtype=np.uint8))
    rows = [np.hstack(preview[i:i+4]) for i in range(0, len(preview), 4)]
    sheet = np.vstack(rows)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    target = REPORT_DIR / f"epfl-lab-camera{camera}-yolo-contact.jpg"
    if not cv2.imwrite(str(target), sheet, [cv2.IMWRITE_JPEG_QUALITY, 83]):
        raise RuntimeError("Could not write diagnostic contact sheet")
    return {
        "camera": camera,
        "decoded_frames_until_last_sample": decoded,
        "width": frame.shape[1],
        "height": frame.shape[0],
        "frames_evaluated": len(samples),
        "sampled_frame_numbers": [x["frame"] for x in samples],
        "frames_with_at_least_one_model_person_box":
            sum(x["person_boxes"] > 0 for x in samples),
        "total_predicted_person_boxes": sum(x["person_boxes"] for x in samples),
        "contact_sheet": str(target),
        "sample_details": samples,
        "no_ground_truth_box_metrics": True,
    }


def main():
    from ultralytics import YOLO
    model = YOLO("yolo11n.pt")  # exact baseline model; no training or tuning
    report = {
        "experiment": "different_video_source_epfl_laboratory_four_person_two_cameras",
        "source_credit": "CVLab EPFL, research-use only",
        "source_information":
            "https://www.epfl.ch/labs/cvlab/data/data-pom-index-php/",
        "same_scene_synchronized_views": True,
        "model": "yolo11n.pt",
        "confidence": 0.25,
        "image_size": 640,
        "sampling": "same 19 timestamps (5..48 s) in both camera streams",
        "accuracy_claim": "NONE: unlike CAVIAR, per-frame 2D GT not scored",
        "global_person_identity_verified": False,
        "eligible_for_real_identity_alerts": False,
        "cameras": [],
    }
    with tempfile.TemporaryDirectory(prefix="zd-epfl-lab-") as tmp:
        for camera in (0, 1):
            url = f"{ROOT}/4p-c{camera}.avi"
            path = Path(tmp) / f"4p-c{camera}.avi"
            info = _download(url, path)
            metrics = _analyze(path, camera, model)
            report["cameras"].append({**info, **metrics})
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    (REPORT_DIR / "verify-epfl-alternative-cameras.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "experiment": report["experiment"],
        "cameras": [{
            k: c[k] for k in (
                "camera", "sha256", "bytes", "width", "height",
                "frames_evaluated", "frames_with_at_least_one_model_person_box",
                "total_predicted_person_boxes", "contact_sheet")
        } for c in report["cameras"]],
        "warning": "YOLO detections only, not ground-truth precision/recall or cross-camera identity",
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
