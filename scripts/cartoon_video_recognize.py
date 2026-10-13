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
import shutil
import subprocess
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
PUBLIC_CLIP_URLS = [
    # 15s genuine activity montage, original Wikimedia CC BY clip.
    "https://upload.wikimedia.org/wikipedia/commons/1/1b/Big_buck_bunny_montage.ogv",
    # Independent licensed originals when public Wikimedia CDN throttles CI.
    "https://www.w3schools.com/html/mov_bbb.mp4",
    "https://test-videos.co.uk/vids/bigbuckbunny/mp4/h264/720/Big_Buck_Bunny_720_10s_1MB.mp4",
]
PROMPT = "a big white rabbit. a cartoon bunny. a small squirrel."
OUT_DEFAULT = ROOT / "reports/cartoon-video-proof"


def fetch_demo_video(out: Path) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.is_file() and out.stat().st_size > 100_000:
        return out
    errors = []
    for url in PUBLIC_CLIP_URLS:
        target = out.with_suffix(".mp4") if url.endswith(".mp4") else out
        try:
            if target.is_file() and target.stat().st_size > 100_000:
                return target
            response = requests.get(
                url, timeout=45,
                headers={"User-Agent": "ZD-Cartoon-Video-Proof/1.0"})
            response.raise_for_status()
            if not 100_000 < len(response.content) < 30_000_000:
                raise RuntimeError("unexpected video size")
            target.write_bytes(response.content)
            target.with_suffix(".source.txt").write_text(url, encoding="utf-8")
            return target
        except (requests.RequestException, OSError, RuntimeError) as exc:
            target.unlink(missing_ok=True)
            errors.append(type(exc).__name__)
    raise RuntimeError("All three licensed video mirrors unavailable: " +
                       ",".join(errors))


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
        # Grounding DINO produces overlapping language phrase proposals for
        # one character. Standard IoU-NMS prevents counting a single rabbit
        # three times; each retained box is still purely detector generated.
        ranked = sorted(found, key=lambda d: d["detection_score"], reverse=True)
        retained = []
        for candidate in ranked:
            x0, y0, x1, y1 = candidate["box_xyxy"]
            too_close = False
            for prior in retained:
                a0, b0, a1, b1 = prior["box_xyxy"]
                overlap = max(0, min(x1, a1)-max(x0, a0)) * max(
                    0, min(y1, b1)-max(y0, b0))
                union = ((x1-x0)*(y1-y0) +
                         (a1-a0)*(b1-b0) - overlap)
                if union and overlap / union >= .55:
                    too_close = True
                    break
            if not too_close:
                retained.append(candidate)
        return retained


def run(video: Path, out: Path, *,
        interval: float = 1.5, max_frames: int = 10,
        box_threshold: float = .28,
        match_threshold: float = .42,
        reference: Path | None = None,
        reference_crops: list[Path] | None = None):
    """Return measured video inference. Never accept annotated/cropped input."""
    out.mkdir(parents=True, exist_ok=True)
    (out / "frames").mkdir(exist_ok=True)
    (out / "crops").mkdir(exist_ok=True)
    result_file = out / "result.json"
    report = {
        "status": "FAIL",
        "video": str(video),
        "video_source": (video.with_suffix(".source.txt").read_text(encoding="utf-8")
                         if video.with_suffix(".source.txt").is_file()
                         else "user_supplied"),
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
        # Frozen gallery from a prior successful REAL auto-detector run:
        # cropped by Grounding DINO, not by any human. The same gallery is
        # reused on unknown test films to avoid Wikimedia HTTP 429 and prevent
        # trial-to-trial reference feature changes. CLI/UI defaults still
        # create and detect references when no frozen gallery is supplied.
        detector = PretrainedBoxDetector()
        reference_crops_loaded = []
        reference_meta = []
        if reference_crops:
            for i, filename in enumerate(reference_crops):
                if not filename.is_file():
                    raise FileNotFoundError(f"Frozen auto-reference missing: {filename}")
                crop = Image.open(filename).convert("RGB")
                if min(crop.size) < 60:
                    raise ValueError("Frozen auto-reference crop too small")
                name = "buck_reference" if i == 0 else "buck_bow"
                reference_crops_loaded.append(crop)
                reference_meta.append({
                    "name": name,
                    "source": str(filename),
                    "origin": "previously model-generated Grounding DINO crop",
                })
            report["reference_gallery_mode"] = "frozen_prior_detector_crops"
        else:
            with requests.Session() as http:
                if reference is None:
                    (out / "source").mkdir(exist_ok=True)
                    reference = download_image(CASES[0], out / "source", http)
            source_entries = [("buck_reference", reference)]
            if reference.name == CASES[0]["file"]:
                bow = next(e for e in CASES if e["name"] == "buck_with_bow")
                (out / "source").mkdir(exist_ok=True)
                with requests.Session() as http:
                    bow_file = download_image(bow, out / "source", http)
                source_entries.append(("buck_bow", bow_file))
            for name, filename in source_entries:
                image = Image.open(filename).convert("RGB")
                candidates = detector.boxes(
                    image, "a large white cartoon rabbit.",
                    threshold=box_threshold)
                if not candidates:
                    if name == "buck_reference":
                        raise RuntimeError("Detector did not find Buck in reference picture")
                    continue
                detected_box = candidates[0]
                reference_crops_loaded.append(image.crop(detected_box["box_xyxy"]))
                reference_meta.append({
                    "name": name,
                    "source": str(filename),
                    "box": detected_box,
                    "origin": "Grounding DINO auto-box from unmodified photo",
                })
            report["reference_gallery_mode"] = "fresh_pretrained_detector_crops"
        if not reference_crops_loaded:
            raise RuntimeError("No usable Buck reference photos")
        for i, crop in enumerate(reference_crops_loaded):
            filename = ("reference_auto_crop.jpg" if i == 0 else
                        f"reference_auto_crop_{reference_meta[i]['name']}.jpg")
            crop.save(out / filename, quality=90)
        embeddings = load_model()
        reference_features = embed(reference_crops_loaded, embeddings)
        report["reference_detections"] = reference_meta
        report["reference_count"] = len(reference_meta)
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
                similarities = reference_features @ feature
                best_reference = int(np.argmax(similarities))
                similarity = float(similarities[best_reference])
                verdict = (
                    "buck_candidate" if similarity >= match_threshold
                    else "not_confirmed"
                )
                name = f"t{timestamp:.2f}_box{index}.jpg"
                crop.save(out / "crops" / name, quality=89)
                entry["proposals"].append({
                    **detection,
                    "cosine_similarity": round(similarity, 6),
                    "reference_similarities": {
                        item["name"]: round(float(score), 6)
                        for item, score in zip(reference_meta, similarities)
                    },
                    "best_reference": reference_meta[best_reference]["name"],
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
                    str(out / "annotated_samples_raw.mp4"), fourcc, 2.0,
                    (bgr.shape[1], bgr.shape[0]))
                if not summary_video.isOpened():
                    raise RuntimeError("Cannot encode annotated sample video")
            summary_video.write(bgr)
            report["frames"].append(entry)
        if summary_video is not None:
            summary_video.release()
            summary_video = None
            raw_preview = out / "annotated_samples_raw.mp4"
            final_preview = out / "annotated_samples.mp4"
            if shutil.which("ffmpeg"):
                try:
                    subprocess.run([
                        "ffmpeg", "-hide_banner", "-loglevel", "error",
                        "-nostdin", "-y", "-i", str(raw_preview),
                        "-an", "-c:v", "libx264", "-preset", "veryfast",
                        "-pix_fmt", "yuv420p", "-movflags", "+faststart",
                        str(final_preview)
                    ], check=True, timeout=90, capture_output=True)
                    raw_preview.unlink()
                    report["preview_codec"] = "h264_browser_compatible"
                except (OSError, subprocess.SubprocessError) as error:
                    raw_preview.rename(final_preview)
                    report["preview_codec"] = "mpeg4_fallback_unverified_in_browser"
                    report["preview_transcode_error"] = type(error).__name__
            else:
                raw_preview.rename(final_preview)
                report["preview_codec"] = "mpeg4_fallback_unverified_in_browser"
        report.update({
            "frames_decoded": decoded,
            "detected_boxes": sum(x["detections"] for x in report["frames"]),
            "buck_candidate_boxes": len(candidate_items),
            "distinct_timestamps_with_buck": len(set(x[0] for x in candidate_items)),
            "sampled_timestamps_without_buck": [
                frame["second"] for frame in report["frames"]
                if not any(p["verdict"] == "buck_candidate"
                           for p in frame["proposals"])
            ],
            "note": ("Best-of-references cosine score is NOT a calibrated "
                     "identity confidence; threshold chosen after earlier film run."),
        })
        # Require the rabbit in more than one ORIGINAL VIDEO timestamp.
        # A single lucky box is not a convincing video demonstration.
        report["status"] = ("PASS" if
            report["distinct_timestamps_with_buck"] >= 2 else "NO_MATCH")
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
    parser.add_argument("--frozen-reference-crops", type=Path, default=None,
                        help="folder of two PREVIOUSLY detector-generated Buck crops")
    parser.add_argument("--out", type=Path, default=OUT_DEFAULT)
    parser.add_argument("--interval", type=float, default=1.5)
    parser.add_argument("--max-frames", type=int, default=10)
    parser.add_argument("--box-threshold", type=float, default=.28)
    parser.add_argument("--match-threshold", type=float, default=.42)
    args = parser.parse_args(argv)
    if not 0 < args.interval <= 120 or not 1 <= args.max_frames <= 100:
        parser.error("Invalid frame interval / max frames")
    video = fetch_demo_video(args.out / "big_buck_bunny_montage.ogv") if args.demo_video else args.video
    result = run(video, args.out, interval=args.interval,
                 max_frames=args.max_frames, box_threshold=args.box_threshold,
                 match_threshold=args.match_threshold, reference=args.reference,
                 reference_crops=([
                     args.frozen_reference_crops / "reference_auto_crop.jpg",
                     args.frozen_reference_crops / "reference_auto_crop_buck_bow.jpg"
                 ] if args.frozen_reference_crops else None))
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
