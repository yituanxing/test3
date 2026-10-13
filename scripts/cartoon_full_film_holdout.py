"""FROZEN model/threshold audit over the entire UNSEEN ORIGINAL open movie.

First ever test of 10-minute Big Buck Bunny. No hand crop, no training,
no threshold adjustment, no temporal propagation across far-apart scenes.
Evaluate randomly distributed (deterministic) original timestamps.
Model predictions cannot be treated as accuracy without frame-level truth.
"""
from __future__ import annotations
import hashlib
import json
import subprocess
from pathlib import Path
import cv2
from PIL import Image, ImageDraw

from scripts.cartoon_video_recognize import run, choose_video_frames, ROOT
from scripts.cartoon_holdout_eval import LOCKED_PARAMETERS

# The former 320x180 URL returned actual HTTP 404 (CI #38346863286).
# Google's public 10-minute Big Buck Bunny sample allows HTTP byte ranges:
# FFmpeg seeks to an unseen later scene instead of downloading 160MB.
FULL_MOVIE = "https://storage.googleapis.com/gtv-videos-bucket/sample/BigBuckBunny.mp4"
OUT = ROOT / "reports" / "cartoon-full-film-holdout"


def get_original() -> Path:
    """FFmpeg remotely extracts a NEW 25-second scene, not any tuned sample.

    The time window 3m00s-3m25s was chosen BEFORE observing any predictions
    for this source; never tune detection/embedding thresholds on its result.
    """
    OUT.mkdir(parents=True, exist_ok=True)
    target = OUT / "heldout_original_film_3m00.mp4"
    if target.is_file() and target.stat().st_size > 150_000:
        return target
    command = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
        "-ss", "00:03:00", "-i", FULL_MOVIE, "-t", "00:00:25",
        "-an", "-vf", "scale=640:-2",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "26",
        "-movflags", "+faststart", str(target),
    ]
    try:
        subprocess.run(command, check=True, timeout=240,
                       capture_output=True, text=True)
    except (OSError, subprocess.SubprocessError) as exc:
        target.unlink(missing_ok=True)
        raise RuntimeError("Remote original-movie seek failed: " +
                           str(exc)[-260:]) from exc
    if not target.is_file() or target.stat().st_size < 150_000:
        raise RuntimeError("Original film extracted an empty scene")
    return target


def summary_contact(folder: Path, output: Path):
    frames = sorted((folder / "frames").glob("t*.jpg"),
                    key=lambda p: float(p.stem[1:]))
    # For inspection, show ALL model-annotated sampled frames together.
    sheet = Image.new("RGB", (4*365, ((len(frames)+3)//4)*250), "white")
    draw = ImageDraw.Draw(sheet)
    for i, frame in enumerate(frames):
        im = Image.open(frame).convert("RGB")
        im.thumbnail((355, 214))
        x, y = (i%4)*365, (i//4)*250
        sheet.paste(im, (x,y))
        draw.text((x+5,y+220), f"Original film {frame.stem[1:]}s", fill="black")
    sheet.save(output, quality=90)


def main():
    movie = get_original()
    digest = hashlib.sha256(movie.read_bytes()).hexdigest()
    output = OUT / "evaluation"
    reportfile = OUT / "frozen_result.json"
    parameters = dict(LOCKED_PARAMETERS)
    parameters["sampling_only"] = (
        "Predeclared 3m00s-3m25s excerpt of untested original 596s film")
    report = {
        "source": FULL_MOVIE,
        "source_sha256": digest,
        "extracted_scene_bytes": movie.stat().st_size,
        "source_original_film_start_second": 180,
        "source_original_film_end_second": 205,
        "frozen_thresholds": {
            "box_threshold": parameters["box_threshold"],
            "match_threshold": parameters["match_threshold"],
        },
        "sampling_changed_only_for_new_scene_not_model": True,
        "truth_labels_at_sample_timestamps": "pending visual audit; no assumed rabbit on every frame",
    }
    try:
        result = run(
            movie, output,
            interval=2.5, max_frames=10,
            box_threshold=parameters["box_threshold"],
            match_threshold=parameters["match_threshold"])
        report.update({
            "sampled_frames": result["frames_decoded"],
            "box_proposals": result["detected_boxes"],
            "raw_buck_match_timestamps": [
                f["second"] for f in result["frames"]
                if any(p["verdict"] == "buck_candidate" for p in f["proposals"])
            ],
            "raw_buck_candidate_count": result["buck_candidate_boxes"],
            "matcher_result": result["status"],
            "no_threshold_fitting": True,
            "caution": "A model candidate could be a false positive; inspect sheet before labeling.",
        })
        summary_contact(output, OUT / "whole_film_contact.jpg")
        print(json.dumps(report, indent=2, ensure_ascii=False), flush=True)
        return 0
    finally:
        reportfile.write_text(json.dumps(report, indent=2, ensure_ascii=False),
                              encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
