"""First, FROZEN-PARAMETER holdout test for anime character video matching.

DO NOT use results to tune detector text, weights, boxes or threshold.
The first unseen Big Buck Bunny MOVIE segment plus negative Sintel MOVIE
are downloaded only when testing. Neither was used in the earlier tuning.
This reports RAW model scores and a visual audit montage, even if no match.

Sources (authorship/copyright):
  © Blender Foundation, Big Buck Bunny, CC BY 3.0
  © Blender Foundation, Sintel, CC BY 3.0
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import cv2
import numpy as np
import requests
from PIL import Image, ImageDraw

from scripts.cartoon_video_recognize import ROOT, run, choose_video_frames

DIR = ROOT / "reports" / "cartoon-holdout-v1"
BASELINE = ROOT / "reports" / "cartoon-video-proof"
SOURCES = {
    # Other sourced excerpts from the SAME open movie. No tuning on these
    # exact clips occurred in the previous trials.
    "unseen_buck": (
        "https://test-videos.co.uk/vids/bigbuckbunny/mp4/h264/720/"
        "Big_Buck_Bunny_720_10s_1MB.mp4"
    ),
    # Independently created Blender Open Movie with NO Big Buck Bunny
    # character (negative control, no rabbit identity should be returned).
    "negative_sintel": (
        "https://test-videos.co.uk/vids/sintel/mp4/h264/720/"
        "Sintel_720_10s_2MB.mp4"
    ),
}
# LOCKED. Based on the preceding development runs, never changed using
# information from this holdout. Commit history proves when locked.
LOCKED_PARAMETERS = {
    "interval": 1.25,
    "max_frames": 8,
    "box_threshold": 0.28,
    "match_threshold": 0.42,
    "text_prompt": "a big white rabbit. a cartoon bunny. a small squirrel.",
    "detector": "IDEA-Research/grounding-dino-tiny",
    "feature_model": "facebookresearch/dinov2:dinov2_vits14",
}


def download(name: str, url: str) -> Path:
    DIR.mkdir(parents=True, exist_ok=True)
    output = DIR / (name + ".mp4")
    if output.is_file() and output.stat().st_size > 100000:
        return output
    res = requests.get(url, timeout=90, headers={
        "User-Agent": "ZD-Cartoon-Holdout-Eval/1.0"
    })
    res.raise_for_status()
    if not 100000 < len(res.content) <= 16_000_000:
        raise RuntimeError(f"Holdout {name}: unexpected bytes")
    output.write_bytes(res.content)
    return output


def sha256(file: Path) -> str:
    h = hashlib.sha256()
    with file.open("rb") as stream:
        for piece in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(piece)
    return h.hexdigest()


def frame_fingerprint(bgr: np.ndarray) -> np.ndarray:
    # Perceptual dHash: distinguish an independently sourced recording from
    # clips with duplicated/near-identical images. It is not ground truth.
    grey = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    resized = cv2.resize(grey, (9, 8), interpolation=cv2.INTER_AREA)
    return resized[:, 1:] > resized[:, :-1]


def video_fingerprints(path: Path) -> list[np.ndarray]:
    return [frame_fingerprint(frame)
            for _, frame in choose_video_frames(
                path, stride_seconds=.8, max_frames=30)]


def audit_uniqueness(base: Path, holdout: Path) -> dict:
    base_frames = video_fingerprints(base)
    held_frames = video_fingerprints(holdout)
    distances = [
        min((int(np.count_nonzero(x != y)) for y in base_frames),
            default=64)
        for x in held_frames
    ]
    return {
        "baseline_sha256": sha256(base),
        "holdout_sha256": sha256(holdout),
        "different_file_bytes": sha256(base) != sha256(holdout),
        "hamming_distance_min_from_baseline_per_holdout_frame": distances,
        "frames_near_duplicate_at_hamming_le_6": sum(x <= 6 for x in distances),
        "frames_without_close_duplicate": sum(x > 6 for x in distances),
        "note": "dHash is a screening aid; different bytes or hashes do not prove different original film scenes.",
    }


def contact_sheet(source: Path, dest: Path, *, width=350):
    pictures = sorted((source / "frames").glob("*.jpg"))
    if not pictures:
        return
    rows = (len(pictures) + 3) // 4
    sheet = Image.new("RGB", (4 * width, rows * 235), "white")
    pen = ImageDraw.Draw(sheet)
    for i, filepath in enumerate(pictures):
        img = Image.open(filepath).convert("RGB")
        img.thumbnail((width-15, 205))
        x, y = (i % 4) * width, (i // 4) * 235
        sheet.paste(img, (x, y))
        pen.text((x+6, y+207), filepath.stem + " / auto boxes", fill="black")
    sheet.save(dest, quality=89)


def main():
    DIR.mkdir(parents=True, exist_ok=True)
    baselines = [
        x for x in BASELINE.glob("big_buck_bunny_montage.*")
        if x.suffix.lower() in {".ogv", ".mp4"} and x.is_file()
    ]
    if len(baselines) != 1:
        raise RuntimeError("Actual baseline film is needed for leakage audit")
    result = {
        "evaluation": "first-use holdout; absolutely no parameter fitting",
        "locked_parameters": LOCKED_PARAMETERS,
        "ground_truth": (
            "Sintel film has no Big Buck Bunny character; positive film "
            "contains Buck somewhere but sampled-frame truth not yet manually labeled"
        ),
        "precision_recall_valid_without_manual_frame_truth": False,
        "video_examples": {},
    }
    out = DIR / "holdout_result.json"
    try:
        for name, url in SOURCES.items():
            video = download(name, url)
            folder = DIR / name
            evaluation = run(
                video, folder,
                interval=LOCKED_PARAMETERS["interval"],
                max_frames=LOCKED_PARAMETERS["max_frames"],
                box_threshold=LOCKED_PARAMETERS["box_threshold"],
                match_threshold=LOCKED_PARAMETERS["match_threshold"],
            )
            uniqueness = audit_uniqueness(baselines[0], video)
            frames = evaluation["frames"]
            detected = sum(len(x["proposals"]) for x in frames)
            positive_times = sorted({
                x["second"] for x in frames if any(
                    p["verdict"] == "buck_candidate" for p in x["proposals"]
                )
            })
            highest = max((p["cosine_similarity"] for f in frames
                           for p in f["proposals"]), default=None)
            result["video_examples"][name] = {
                "source_url": url,
                "video_sha256": sha256(video),
                "status_from_frozen_matcher": evaluation["status"],
                "decoded_sampled_frames": evaluation["frames_decoded"],
                "detected_auto_boxes": detected,
                "raw_buck_candidate_times": positive_times,
                "max_raw_cosine": highest,
                "any_buck_candidate": bool(positive_times),
                "possible_source_overlap": uniqueness,
                "source_result": str(folder / "result.json"),
            }
            contact_sheet(folder, DIR / (name + "_contact.jpg"))
            print(json.dumps({name: result["video_examples"][name]},
                             ensure_ascii=False, indent=2), flush=True)
        negative = result["video_examples"]["negative_sintel"]
        result["negative_control_result"] = (
            "NO_FALSE_MATCH" if not negative["any_buck_candidate"]
            else "FALSE_MATCH_DETECTED"
        )
        result["holdout_has_new_visual_frames"] = (
            result["video_examples"]["unseen_buck"]
            ["possible_source_overlap"]["frames_without_close_duplicate"] > 0)
        result["status"] = "MEASURED_NOT_TUNED"
        return 0
    finally:
        out.write_text(json.dumps(result, ensure_ascii=False, indent=2),
                       encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
