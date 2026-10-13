"""ONE-TIME predeclared, unseen 4:30-4:55 cartoon holdout.

Compare the LOCKED A/B inference decisions without adjusting either model
or 0.28/0.42 thresholds. New scene timestamps are fixed before model use.
This script never invents ground-truth labels: contact sheet MUST be visually
reviewed separately before reporting precision/recall.

Blender Foundation Big Buck Bunny, CC BY 3.0.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import cv2
from PIL import Image, ImageDraw

from scripts.cartoon_full_film_holdout import FULL_MOVIE
from scripts.cartoon_holdout_eval import LOCKED_PARAMETERS
from scripts.cartoon_label_guard import apply_guard
from scripts.cartoon_video_recognize import ROOT, run


OUT = ROOT / "reports" / "cartoon-4m30-unseen-holdout"
EXCERPT_START = 270
EXCERPT_DURATION = 25


def acquire_new_movie_excerpt() -> Path:
    OUT.mkdir(parents=True, exist_ok=True)
    filename = OUT / "original_4m30_to_4m55.mp4"
    if filename.is_file() and filename.stat().st_size > 150_000:
        return filename
    result = subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
        "-ss", "00:04:30", "-i", FULL_MOVIE, "-t", "25",
        "-an", "-vf", "scale=640:-2",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "26",
        "-movflags", "+faststart", str(filename),
    ], check=False, capture_output=True, text=True, timeout=240)
    if result.returncode != 0:
        filename.unlink(missing_ok=True)
        raise RuntimeError("HLS extraction error: " + (result.stderr or "")[-800:])
    if not filename.is_file() or filename.stat().st_size < 150_000:
        raise RuntimeError("HLS movie excerpt empty or truncated")
    capture = cv2.VideoCapture(str(filename))
    n, fps = capture.get(cv2.CAP_PROP_FRAME_COUNT), capture.get(cv2.CAP_PROP_FPS)
    capture.release()
    duration = n / fps if fps else 0
    if not (20 <= duration <= 27):
        raise RuntimeError(f"Expected 25-second movie, got {duration:.2f}s")
    return filename


def contact_grid(root: Path, guarded: dict):
    images = []
    for frame in guarded["frames"]:
        stamp = frame["second"]
        filepath = root / "frames" / f"t{stamp:.2f}.jpg"
        img = cv2.imread(str(filepath))
        if img is None:
            continue
        for proposal in frame["proposals"]:
            if proposal["guarded_status"] != "needs_review":
                continue
            x0,y0,x1,y1 = proposal["box_xyxy"]
            cv2.rectangle(img, (x0,y0), (x1,y1), (240,0,255), 4)
            cv2.putText(img, "AMBIGUOUS - REVIEW",
                        (x0, max(18,y0-7)), cv2.FONT_HERSHEY_SIMPLEX,
                        0.56, (240,0,255), 2)
        img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        pics = Image.fromarray(img)
        pics.thumbnail((350,207))
        images.append((pics, frame))
    cols = 4
    rows = (len(images) + cols-1)//cols
    out = Image.new("RGB", (cols*355, rows*244), "white")
    pen = ImageDraw.Draw(out)
    for i,(image,frame) in enumerate(images):
        x,y = (i%cols)*355,(i//cols)*244
        out.paste(image,(x,y))
        pen.text((x+4,y+211),f"t={frame['second']:.2f}  raw={frame['raw_predicted_buck']}",fill="black")
        pen.text((x+4,y+228),f"guard={frame['guarded_predicted_buck']}  review={frame['ambiguous_raw_matches_needing_review']}",fill="black")
    out.save(OUT / "holdout_4m30_visual_comparison.jpg",quality=92)


def main():
    movie = acquire_new_movie_excerpt()
    data_root = OUT / "predictions"
    refroot = ROOT / "reports/cartoon-reference-fixtures"
    frozen_refs = [
        refroot / "reference_auto_crop.jpg",
        refroot / "reference_auto_crop_buck_bow.jpg",
    ]
    if any(not p.is_file() for p in frozen_refs):
        raise FileNotFoundError("Missing proven original auto-detected Buck reference fixtures")
    model = run(movie, data_root, interval=2.5, max_frames=10,
                box_threshold=LOCKED_PARAMETERS["box_threshold"],
                match_threshold=LOCKED_PARAMETERS["match_threshold"],
                reference_crops=frozen_refs)
    guards = apply_guard(model)
    (OUT/"guarded_result.json").write_text(json.dumps(guards,indent=2,ensure_ascii=False),encoding="utf-8")
    contact_grid(data_root,guards)
    summary = {
        "test": "unseen 4m30-4m55 from genuine 10-minute Big Buck Bunny, first evaluation",
        "source": FULL_MOVIE,
        "original_film_time_window_seconds": [EXCERPT_START,EXCERPT_START+EXCERPT_DURATION],
        "movie_sha256": hashlib.sha256(movie.read_bytes()).hexdigest(),
        "parameters_frozen_before_test": LOCKED_PARAMETERS,
        "reference_crops": [str(p) for p in frozen_refs],
        "decoded_timestamps": [f["second"] for f in model["frames"]],
        "raw_positive_timestamps": guards["raw_positive_timestamps"],
        "guarded_positive_timestamps": guards["guarded_positive_timestamps"],
        "ambiguous_raw_matches_needing_review": guards["ambiguous_match_count"],
        "no_video_truth_assumed": True,
        "not_accuracy_without_independent_manual_labels": True,
        "evidence": ["guarded_result.json","holdout_4m30_visual_comparison.jpg",
                     "predictions/result.json"],
    }
    (OUT/"summary.json").write_text(json.dumps(summary,indent=2,ensure_ascii=False),encoding="utf-8")
    print(json.dumps(summary,indent=2,ensure_ascii=False),flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
