"""Frozen multi-window positive/negative generalization audit.

Both windows [2:00, 7:00] are committed BEFORE their first model inference.
No threshold adjustment, no new feature extractor, no extra reference image.
The same Grounding DINO outputs and pre-trained DINOv2 vectors yield:
  A: max of 2 reference similarities >= 0.42
  B: harmonic mean of 2 reference similarities >= 0.42

These two movie windows were NOT used in any earlier experiments. The film
is still Big Buck Bunny, so it is not an independent-animation benchmark.
Visual labels MUST be frozen separately AFTER first-run evidence, for honest
frame-level TP/FP/FN, with ambiguous partial character frames excluded.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import cv2
from PIL import Image, ImageDraw

from scripts.cartoon_consensus_match import evaluate_consensus
from scripts.cartoon_full_film_holdout import FULL_MOVIE
from scripts.cartoon_holdout_eval import LOCKED_PARAMETERS
from scripts.cartoon_video_recognize import ROOT, run

OUT = ROOT / "reports/cartoon-multiwindow-holdout"
# Fixed and committed before inference, not moved in response to outcomes.
FROZEN_WINDOWS = (
    {"name": "original_2m00", "start": 120, "duration": 25},
    {"name": "original_7m00", "start": 420, "duration": 25},
)
FROZEN_REFS = (
    ROOT / "reports/cartoon-reference-fixtures/reference_auto_crop.jpg",
    ROOT / "reports/cartoon-reference-fixtures/reference_auto_crop_buck_bow.jpg",
)
FRAME_INTERVAL = 2.5
MAX_FRAMES = 10


def fetch_window(spec: dict) -> Path:
    directory = OUT / spec["name"]
    directory.mkdir(parents=True, exist_ok=True)
    movie = directory / "unmodified_scene_moving_movie.mp4"
    if movie.is_file() and movie.stat().st_size > 150_000:
        return movie
    proc = subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
        "-ss", str(spec["start"]), "-i", FULL_MOVIE,
        "-t", str(spec["duration"]), "-an",
        "-vf", "scale=640:-2",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "26",
        "-movflags", "+faststart", str(movie),
    ], capture_output=True, text=True, timeout=240)
    if proc.returncode:
        movie.unlink(missing_ok=True)
        raise RuntimeError("Cannot fetch unseen film window: " + proc.stderr[-800:])
    source = cv2.VideoCapture(str(movie))
    frames, fps = (source.get(cv2.CAP_PROP_FRAME_COUNT),
                   source.get(cv2.CAP_PROP_FPS))
    source.release()
    duration = frames/fps if fps else 0
    if not 20 <= duration <= 27:
        raise RuntimeError(f"Wrong source duration: {duration:.3f}")
    return movie


def render(directory: Path, consensus: dict) -> Path:
    evidence = directory / "first_untuned_ab.jpg"
    rows = (len(consensus["frames"]) + 3) // 4
    sheet = Image.new("RGB", (4*365, rows*255), "white")
    marker = ImageDraw.Draw(sheet)
    for idx, frame in enumerate(consensus["frames"]):
        time = float(frame["second"])
        input_img = directory / "predictions/frames" / f"t{time:.2f}.jpg"
        original = cv2.imread(str(input_img))
        if original is None:
            raise RuntimeError("Frame evidence missing: " + str(input_img))
        for candidate in frame["proposals"]:
            if not candidate["raw_buck_candidate"] or candidate["consensus_buck_candidate"]:
                continue
            x0,y0,x1,y1=candidate["box_xyxy"]
            cv2.rectangle(original, (x0,y0), (x1,y1), (255,0,255), 3)
            cv2.putText(original, "MAX ONLY",
                        (x0, max(15,y0-7)), cv2.FONT_HERSHEY_SIMPLEX,
                        .52, (255,0,255), 2)
        image = Image.fromarray(cv2.cvtColor(original, cv2.COLOR_BGR2RGB))
        image.thumbnail((355,215))
        x,y = (idx%4)*365, (idx//4)*255
        sheet.paste(image,(x,y))
        marker.text((x+3,y+218),f"t={time:.1f}s max={frame['raw_predicted_buck']}",fill="black")
        marker.text((x+3,y+235),f"harmonic={frame['consensus_predicted_buck']}",fill="black")
    sheet.save(evidence,quality=92)
    return evidence


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    for ref in FROZEN_REFS:
        if not ref.is_file():
            raise FileNotFoundError(f"Missing verified automated reference crop {ref}")
    common = {
        "source": FULL_MOVIE,
        "source_reused_prior_automated_reference_crops":True,
        "box_threshold":LOCKED_PARAMETERS["box_threshold"],
        "match_threshold":LOCKED_PARAMETERS["match_threshold"],
        "aggregations":{
            "A":"max of two frozen reference feature cosines",
            "B":"harmonic mean of two frozen reference feature cosines"},
        "sampling_interval_seconds":FRAME_INTERVAL,
        "sampling_max_frames":MAX_FRAMES,
        "window_first_use":"Committed windows 120..145 and 420..445 before first detection inference",
        "truth":"UNLABELED; inspect real source frames, then add independent manual labels",
        "quality_gate":"No automatic pass based solely on model outputs",
        "windows":{},
    }
    summaryfile = OUT / "summary.json"
    try:
        for spec in FROZEN_WINDOWS:
            movie = fetch_window(spec)
            folder = OUT / spec["name"]
            raw = run(
                movie, folder / "predictions",
                interval=FRAME_INTERVAL, max_frames=MAX_FRAMES,
                box_threshold=LOCKED_PARAMETERS["box_threshold"],
                match_threshold=LOCKED_PARAMETERS["match_threshold"],
                reference_crops=list(FROZEN_REFS),
            )
            b = evaluate_consensus(raw)
            (folder/"consensus.json").write_text(
                json.dumps(b,indent=2,ensure_ascii=False),encoding="utf-8")
            render(folder,b)
            report = {
                "original_movie_window":[spec["start"], spec["start"]+spec["duration"]],
                "movie_sha256":hashlib.sha256(movie.read_bytes()).hexdigest(),
                "frames_decoded":raw["frames_decoded"],
                "detected_boxes":raw["detected_boxes"],
                "A_original_buck_timestamps":b["raw_positive_timestamps"],
                "B_harmonic_buck_timestamps":b["consensus_positive_timestamps"],
                "old_only_timestamps":b["original_positive_frames_rejected_by_consensus"],
                "no_truth_claim_until_visual_review":True,
            }
            common["windows"][spec["name"]] = report
            print(json.dumps({spec["name"]:report},ensure_ascii=False,indent=2),flush=True)
        return 0
    finally:
        summaryfile.write_text(json.dumps(common,indent=2,ensure_ascii=False),
                               encoding="utf-8")


if __name__=="__main__":
    raise SystemExit(main())
