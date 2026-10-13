"""FROZEN model/threshold audit over the entire UNSEEN ORIGINAL open movie.

First ever test of 10-minute Big Buck Bunny. No hand crop, no training,
no threshold adjustment, no temporal propagation across far-apart scenes.
Evaluate randomly distributed (deterministic) original timestamps.
Model predictions cannot be treated as accuracy without frame-level truth.
"""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import requests
import cv2
from PIL import Image, ImageDraw

from scripts.cartoon_video_recognize import run, choose_video_frames, ROOT
from scripts.cartoon_holdout_eval import LOCKED_PARAMETERS

FULL_MOVIE = "https://download.blender.org/peach/bigbuckbunny_movies/BigBuckBunny_320x180.mp4"
OUT = ROOT / "reports" / "cartoon-full-film-holdout"


def get_original() -> Path:
    OUT.mkdir(parents=True, exist_ok=True)
    filename = OUT / "BigBuckBunny_320x180.mp4"
    if filename.exists() and filename.stat().st_size > 50_000_000:
        return filename
    tmp = filename.with_suffix(".partial")
    with requests.get(FULL_MOVIE, stream=True, timeout=(20, 150),
                      headers={"User-Agent": "ZD-Cartoon-Frozen-Holdout/1.0"}) as rsp:
        rsp.raise_for_status()
        with tmp.open("wb") as dst:
            total = 0
            for chunk in rsp.iter_content(chunk_size=1024*1024):
                total += len(chunk)
                if total > 85_000_000:
                    raise RuntimeError("Unexpected enormous original movie")
                dst.write(chunk)
    if not 50_000_000 <= total <= 85_000_000:
        raise RuntimeError(f"Full film bytes unexpected: {total}")
    tmp.replace(filename)
    return filename


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
        "Entire 596s film, maximum 12 deterministic timestamps across duration")
    report = {
        "source": FULL_MOVIE,
        "source_sha256": digest,
        "source_bytes": movie.stat().st_size,
        "frozen_thresholds": {
            "box_threshold": parameters["box_threshold"],
            "match_threshold": parameters["match_threshold"],
        },
        "sampling_changed_to_span_longer_source_not_model": True,
        "truth_labels_at_sample_timestamps": "pending visual audit; no assumed rabbit on every frame",
    }
    try:
        result = run(
            movie, output,
            interval=50.0, max_frames=12,
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
