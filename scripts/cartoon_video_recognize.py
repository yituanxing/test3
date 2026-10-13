"""Video -> pretrained open-vocabulary detector -> AUTO boxes -> pretrained DINOv2.

No training, manual boxes, person identity, or app database. This is an
isolated cartoon-character VIDEO experiment. A model-provided bounding box
is a crop, NOT pixel-perfect SAM segmentation.

Install: pip install -r requirements-cartoon-video.txt
Run: python -m scripts.cartoon_video_recognize --video my_animation.mp4
  or: python -m scripts.cartoon_video_recognize --demo-video

Reports: reports/cartoon-video-proof/{result.json,annotated.mp4,frames/*.jpg,
crops/*.jpg}. Failing detection is recorded honestly; never fake a match.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np
import requests
import torch
from PIL import Image
from transformers import AutoProcessor, AutoModelForZeroShotObjectDetection

from scripts.verify_cartoon_character import (
    CASES, LICENSE, ROOT, download_image, embed, load_model,
)

DETECTOR_ID = "IDEA-Research/grounding-dino-tiny"
# The film clip is actual moving video, NOT assembled photo slides.
PUBLIC_CLIP_URL = (
    "https://upload.wikimedia.org/wikipedia/commons/1/1b/"
    "Big_buck_bunny_montage.ogv"
)
PROMPT = "a big white rabbit. a cartoon bunny. a small squirrel."
OUT_DEFAULT = ROOT / "reports/cartoon-video-proof"


def fetch_demo_video(out: Path) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.is_file() and out.stat().st_size > 500_000:
        return out
    response = requests.get(PUBLIC_CLIP_URL, timeout=70,
                            headers={"User-Agent": "ZD-Cartoon-Video-Proof/1.0"})
    response.raise_for_status()
    if not 500_000 < len(response.content) < 30_000_000:
        raise RuntimeError("Demo source unexpected size")
    out.write_bytes(response.content)
    return out


def choose_video_frames(video: Path, *, stride_seconds: float, max_frames: int):
    video_reader = cv2.VideoCapture(str(video))
    if not video_reader.isOpened():
        raise RuntimeError("Cannot open supplied video")
    try:
        fps = video_reader.get(cv2.CAP_PROP_FPS)
        fps = fps if 1 <= fps <= 240 else 25.0
        frames = video_reader.get(cv2.CAP_PROP_FRAME_COUNT)
        if frames <= 0:
            raise RuntimeError("Video frame count unavailable")
        duration = frames / fps
        if duration <= 0.1:
            raise RuntimeError("Video has no significant duration")
        sample_ts = np.arange(0.5, duration, stride_seconds, dtype=float)
        if len(sample_ts) > max_frames:
            # Sample ACROSS the clip, not only first few frames/titles.
            sample_ts = sample_ts[np.linspace(
                0, len(sample_ts) - 1, max_frames).astype(int)]
        for second in sample_ts:
            video_reader.set(cv2.CAP_PROP_POS_MSEC, float(second * 1000))
            ok, frame = video_reader.read()
            if ok:
                yield float(second), frame
    finally:
        video_reader.release()


class PretrainedBoxDetector:
    """Wrapper around OFF-THE-SHELF HuggingFace Grounding DINO weights."""

    def __init__(self):
        torch.set_num_threads(2)
        self.processor = AutoProcessor.from_pretrained(DETECTOR_ID)
        self.model = AutoModelForZeroShotObjectDetection.from_pretrained(
            DETECTOR_ID).eval()

    def boxes(self, image: Image.Image, text: str, *, threshold: float):
        inputs = self.processor(images=image, text=text, return_tensors="pt")
        with torch.inference_mode():
            outputs = self.model(**inputs)
        detections = self.processor.post_process_grounded_object_detection(
            outputs, inputs.input_ids,
            box_threshold=threshold, text_threshold=0.20,
            target_sizes=[(image.height, image.width)])[0]
        found = []
        for box, score, label in zip(
                detections["boxes"], detections["scores"],
                detections["labels"]):
            coords = [int(round(x)) for x in box.tolist()]
            x0, y0, x1, y1 = coords
            x0, x1 = max(0, x0), min(image.width, x1)
            y0, y1 = max(0, y0), min(image.height, y1)
            if x1-x0 < 35 or y1-y0 < 35:
                continue
            found.append({
                "box_xyxy": [x0, y0, x1, y1],
                "detection_score": float(score),
                "label": str(label),
            })
        return sorted(found, key=lambda d: d["detection_score"], reverse=True)


def run(video: Path, out: Path, *,
        interval: float = 1.5, max_frames: int = 10,
        box_threshold: float = .28,
        match_threshold: float = .50,
        reference: Path | None = None):
    """Return measured video inference. Never accept annotated/cropped input."""
    out.mkdir(parents=True, exist_ok=True)
    (out / "frames").mkdir(exist_ok=True)
    (out / "crops").mkdir(exist_ok=True)
    result_file = out / "result.json"
    report = {
        "status": "FAIL",
        "video": str(video),
        "video_source": PUBLIC_CLIP_URL if video.name == "big_buck_bunny_montage.ogv" else "user_supplied",
        "licensed_media": LICENSE,
        "detector": DETECTOR_ID,
        "embedding_model": "Meta DINOv2 ViT-S/14, frozen",
        "prompt": PROMPT,
        "bbox_source": "pretrained object detector (no hand drawn boxes)",
        "reference_bbox_source": "pretrained object detector (no hand drawn boxes)",
        "thresholds_exploratory_not_calibrated": True,
        "box_threshold": box_threshold,
        "match_threshold": match_threshold,
        "run_utc": datetime.now(timezone.utc).isoformat(),
        "frames": [],
    }
    try:
        with requests.Session() as http:
            if reference is None:
                (out / "source").mkdir(exist_ok=True)
                reference = download_image(CASES[0], out / "source", http)
        image = Image.open(reference).convert("RGB")
        detector = PretrainedBoxDetector()
        bboxes = detector.boxes(image, PROMPT, threshold=box_threshold)
        if not bboxes:
            raise RuntimeError("Grounding DINO did not detect Buck in reference photo")
        reference_box = bboxes[0]
        reference_crop = image.crop(reference_box["box_xyxy"])
        reference_crop.save(out / "reference_auto_crop.jpg", quality=90)
        embeddings = load_model()
        reference_feature = embed([reference_crop], embeddings)[0]
        report["reference_detection"] = reference_box
        candidate_items = []
        decoded = 0
        summary_video = None
        for timestamp, bgr in choose_video_frames(
                video, stride_seconds=interval, max_frames=max_frames):
            decoded += 1
            pil = Image.fromarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
            proposals = detector.boxes(pil, PROMPT, threshold=box_threshold)
            entry = {
                "second": round(timestamp, 3),
                "detections": len(proposals),
                "proposals": [],
            }
            # Each bbox is provided by Grounding DINO, never a manually
            # specified rectangle. Only use the best few per frame.
            for index, detection in enumerate(proposals[:4]):
                crop = pil.crop(detection["box_xyxy"])
                feature = embed([crop], embeddings)[0]
                similarity = float(feature @ reference_feature)
                verdict = (
                    "buck_candidate" if similarity >= match_threshold
                    else "not_confirmed"
                )
                name = f"t{timestamp:.2f}_box{index}.jpg"
                crop.save(out / "crops" / name, quality=89)
                entry["proposals"].append({
                    **detection,
                    "cosine_similarity": round(similarity, 6),
                    "verdict": verdict,
                    "crop": f"crops/{name}",
                })
                if verdict == "buck_candidate":
                    candidate_items.append((timestamp, detection, similarity))
                x0, y0, x1, y1 = detection["box_xyxy"]
                shade = (0, 230, 0) if verdict == "buck_candidate" else (50, 190, 255)
                cv2.rectangle(bgr, (x0, y0), (x1, y1), shade, 2)
                cv2.putText(bgr, f"{verdict}: {similarity:.2f}",
                            (x0, max(16, y0 - 5)),
                            cv2.FONT_HERSHEY_SIMPLEX, .47, shade, 1)
            cv2.imwrite(str(out / "frames" / f"t{timestamp:.2f}.jpg"), bgr)
            if summary_video is None:
                fourcc = cv2.VideoWriter_fourcc(*"mp4v")
                summary_video = cv2.VideoWriter(
                    str(out / "annotated_samples.mp4"), fourcc, 2.0,
                    (bgr.shape[1], bgr.shape[0]))
                if not summary_video.isOpened():
                    raise RuntimeError("Cannot encode annotated sample video")
            summary_video.write(bgr)
            report["frames"].append(entry)
        if summary_video is not None:
            summary_video.release()
            summary_video = None
        report.update({
            "frames_decoded": decoded,
            "detected_boxes": sum(x["detections"] for x in report["frames"]),
            "buck_candidate_boxes": len(candidate_items),
            "distinct_timestamps_with_buck": len(set(x[0] for x in candidate_items)),
            "note": "Cosine score is not a calibrated identity confidence.",
        })
        if candidate_items:
            report["status"] = "PASS"
        else:
            report["status"] = "NO_MATCH"
        return report
    except Exception as exc:
        report["error_type"] = type(exc).__name__
        report["error_detail"] = str(exc)[:240]
        raise
    finally:
        # The preview is only sampled annotated frames; original duration and
        # rate are NOT reconstructed, so never label it full original footage.
        if "summary_video" in locals() and summary_video is not None:
            summary_video.release()
        result_file.write_text(json.dumps(report, indent=2, ensure_ascii=False),
                               encoding="utf-8")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Automated cartoon character auto-box matching from untrimmed video")
    exclusive = parser.add_mutually_exclusive_group(required=True)
    exclusive.add_argument("--video", type=Path, help="any input MP4/MOV/OGV clip")
    exclusive.add_argument("--demo-video", action="store_true",
                           help="download CC BY film montage 15s from Commons")
    parser.add_argument("--reference", type=Path, default=None,
                        help="optional un-cropped character reference image")
    parser.add_argument("--out", type=Path, default=OUT_DEFAULT)
    parser.add_argument("--interval", type=float, default=1.5)
    parser.add_argument("--max-frames", type=int, default=10)
    parser.add_argument("--box-threshold", type=float, default=.28)
    parser.add_argument("--match-threshold", type=float, default=.50)
    args = parser.parse_args(argv)
    if not 0 < args.interval <= 120 or not 1 <= args.max_frames <= 100:
        parser.error("Invalid frame interval / max frames")
    video = fetch_demo_video(args.out / "big_buck_bunny_montage.ogv") if args.demo_video else args.video
    result = run(video, args.out, interval=args.interval,
                 max_frames=args.max_frames, box_threshold=args.box_threshold,
                 match_threshold=args.match_threshold, reference=args.reference)
    print(json.dumps({
        "status": result["status"],
        "frames_decoded": result["frames_decoded"],
        "detected_boxes": result["detected_boxes"],
        "buck_candidate_boxes": result["buck_candidate_boxes"],
        "evidence_dir": str(args.out),
    }, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
