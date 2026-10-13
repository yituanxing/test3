"""Zero-training Big Buck Bunny CHARACTER MATCHING proof.

NOT human face recognition, not object localization or GB28181 video inference.
A pre-trained Meta DINOv2 ViT-S/14 embeds genuinely different CC BY 3.0 film
stills; the evaluator asks whether two Buck shots rank above a different character.
Names/labels are held for reporting only: model receives only RGB pixels.
Images and model weights are downloaded at execution time, not committed.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import requests
import torch
from PIL import Image, ImageDraw, ImageFont
from torchvision import transforms

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "reports" / "cartoon-character-proof"
LICENSE = "(c) copyright 2008 Blender Foundation / www.bigbuckbunny.org; CC BY 3.0"
COMMONS = "https://commons.wikimedia.org/wiki/Special:FilePath/"
# Different film stills, NOT repeated frames. Crop annotations are source
# selection, not recognition labels fed to the model.
CASES = [
    {"name": "buck_reference", "file": "Big.Buck.Bunny.-.Bunny.Portrait.png",
     "role": "reference", "truth": "Buck", "crop": [0.0, 0.0, 1.0, 1.0]},
    {"name": "buck_meadow", "file": "Bbb-splash.png",
     "role": "candidate", "truth": "Buck", "crop": [0.22, 0.09, 0.75, 0.91]},
    {"name": "buck_profile", "file": "Big.Buck.Bunny.-.Frank.Bunny.png",
     "role": "candidate", "truth": "Buck", "crop": [0.32, 0.0, 1.0, 1.0]},
    {"name": "buck_with_bow", "file": "Big.Buck.Bunny.-.Bunny.with.Bow.png",
     "role": "candidate", "truth": "Buck", "crop": [0.0, 0.0, 1.0, 1.0]},
    {"name": "small_animal_negative", "file": "Big.Buck.Bunny.-.Frank.Bunny.png",
     "role": "candidate", "truth": "other", "crop": [0.29, 0.21, 0.51, 0.70]},
]


def download_image(entry: dict, directory: Path, session: requests.Session) -> Path:
    dest = directory / entry["file"]
    if dest.is_file():
        return dest
    url = entry.get("url", COMMONS + requests.utils.quote(entry["file"], safe=""))
    last_error = None
    for attempt in range(4):
        try:
            response = session.get(url, timeout=40,
                                   headers={"User-Agent": "ZD-Cartoon-Matching-Probe/1.0 (education; contact through GitHub yituanxing/test3)"})
            response.raise_for_status()
            if not (3000 <= len(response.content) <= 15_000_000):
                raise RuntimeError("Unexpected media size")
            dest.write_bytes(response.content)
            with Image.open(dest) as src:
                src.verify()
            return dest
        except (requests.RequestException, OSError, RuntimeError) as exc:
            last_error = type(exc).__name__
            dest.unlink(missing_ok=True)
            time.sleep(min(2**attempt, 8))
    raise RuntimeError(f"Cannot fetch licensed still {entry['name']}: {last_error}")


def crop_character(path: Path, coordinates: list[float]) -> Image.Image:
    with Image.open(path) as original:
        im = original.convert("RGB")
    w, h = im.size
    x0, y0, x1, y1 = coordinates
    if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1):
        raise ValueError("Invalid normalized crop coordinates")
    crop = im.crop((round(x0 * w), round(y0 * h),
                    round(x1 * w), round(y1 * h)))
    if min(crop.size) < 60:
        raise ValueError("Character crop too small for evidence")
    return crop


def load_model():
    # DINOv2 ViT-S/14 is OFF-THE-SHELF. No training, fine-tuning,
    # identity labels, or fitted similarity thresholds.
    torch.set_num_threads(2)
    model = torch.hub.load("facebookresearch/dinov2", "dinov2_vits14",
                           trust_repo=True)
    model.eval()
    return model


def embed(images: list[Image.Image], model) -> np.ndarray:
    transform = transforms.Compose([
        transforms.Resize(256, interpolation=transforms.InterpolationMode.BICUBIC),
        transforms.CenterCrop(224),
        transforms.ToTensor(),
        transforms.Normalize(mean=[0.485, 0.456, 0.406],
                             std=[0.229, 0.224, 0.225]),
    ])
    with torch.inference_mode():
        vectors = torch.cat([
            model(transform(img).unsqueeze(0)).float() for img in images
        ], dim=0)
        vectors = torch.nn.functional.normalize(vectors, dim=1)
    return vectors.detach().cpu().numpy()


def make_sheet(images: list[Image.Image], cases: list[dict], scores: dict, output: Path):
    cell_w, cell_h = 300, 280
    sheet = Image.new("RGB", (cell_w * len(images), cell_h), "white")
    draw = ImageDraw.Draw(sheet)
    for i, (img, case) in enumerate(zip(images, cases)):
        preview = img.copy()
        preview.thumbnail((cell_w - 20, 220))
        x = cell_w * i + (cell_w - preview.width) // 2
        y = 12 + (220 - preview.height) // 2
        sheet.paste(preview, (x, y))
        rank = "REFERENCE" if i == 0 else f"cos={scores[case['name']]:.4f}"
        draw.text((cell_w * i + 8, 236), case["name"], fill="black")
        draw.text((cell_w * i + 8, 254), rank, fill="black")
    sheet.save(output, quality=91)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Same cartoon character across different licensed film stills")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    report_file = args.out / "result.json"
    report = {
        "status": "FAIL",
        "film": "Big Buck Bunny",
        "model": "Meta DINOv2 ViT-S/14 (pretrained, not trained here)",
        "license": LICENSE,
        "goal": "same character across distinct film stills, not unique human IDs",
        "verified_at_utc": datetime.now(timezone.utc).isoformat(),
        "model_input": "cropped RGB stills only; truth names excluded",
        "real_video_pipeline_tested": False,
    }
    try:
        session = requests.Session()
        source_dir = args.out / "source"
        source_dir.mkdir(exist_ok=True)
        images = [crop_character(download_image(e, source_dir, session), e["crop"])
                  for e in CASES]
        model = load_model()
        features = embed(images, model)
        sim = features[1:] @ features[0]
        rows = [
            {"name": e["name"], "expected_same_character": e["truth"] == "Buck",
             "cosine_similarity": round(float(score), 6),
             "source": COMMONS + requests.utils.quote(e["file"], safe="")}
            for e, score in zip(CASES[1:], sim)
        ]
        rows.sort(key=lambda x: x["cosine_similarity"], reverse=True)
        positive_count = sum(x["expected_same_character"] for x in rows)
        ranked_correct = all(x["expected_same_character"] for x in rows[:positive_count])
        positives = [x["cosine_similarity"] for x in rows if x["expected_same_character"]]
        negatives = [x["cosine_similarity"] for x in rows if not x["expected_same_character"]]
        report.update({
            "comparisons": rows,
            "positive_above_all_negatives": ranked_correct,
            "positive_count": positive_count,
            "negative_count": len(rows) - positive_count,
            "positive_min_similarity": min(positives),
            "negative_max_similarity": max(negatives),
            "positive_negative_margin": round(min(positives) - max(negatives), 6),
            "real_pretrained_inference": True,
            "status": "PASS" if ranked_correct else "FAIL",
        })
        make_sheet(images, CASES,
                   {r["name"]: r["cosine_similarity"] for r in rows},
                   args.out / "evidence.jpg")
        print(json.dumps({k: v for k, v in report.items() if k != "license"},
                         ensure_ascii=False, indent=2))
        return 0 if ranked_correct else 1
    except Exception as exc:
        report["error_type"] = type(exc).__name__
        report["error_detail"] = str(exc)[:350]
        raise
    finally:
        report_file.write_text(json.dumps(report, indent=2, ensure_ascii=False),
                               encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
