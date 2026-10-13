"""Frozen zero-training *label ambiguity guard* on existing cartoon matches.

This file represents a SMALL, explicitly conservative rule (not a model
change): an open-vocabulary detector label that simultaneously describes a
rabbit/bunny AND a squirrel is semantically ambiguous. Never convert that
box into a CONFIRMED Buck match solely from its DINOv2 similarity.

Decision rule fixed BEFORE running a 4:30-4:55 unseen-film validation.
Existing 3:00-3:25 annotated clip is DEVELOPMENT/ERROR-REPRODUCTION data,
not new holdout. Preserve all original model decisions and similarity scores.

This deliberately does NOT reject boxes on arbitrary numeric thresholds.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from scripts.cartoon_video_recognize import ROOT

FILM_PROBE = ROOT / "reports/cartoon-full-film-holdout/evaluation/result.json"

def is_ambiguous(label: str) -> bool:
    """Detector output is a string like 'a big white rabbit a small squirrel'."""
    tokens = label.casefold()
    is_squirrel = "squirrel" in tokens
    is_rabbit = ("rabbit" in tokens or "bunny" in tokens)
    return is_squirrel and is_rabbit


def decision(proposal: dict) -> dict:
    """Do not modify the model's raw 'buck_candidate' or cosine value."""
    raw = proposal["verdict"] == "buck_candidate"
    disputed = is_ambiguous(proposal.get("label", ""))
    return {
        **proposal,
        "raw_buck_candidate": raw,
        "requires_review_ambiguous_detector_label": disputed,
        "guarded_buck_candidate": raw and not disputed,
        "guarded_status": ("needs_review" if raw and disputed else
                           "buck_candidate" if raw else "not_confirmed"),
    }


def apply_guard(raw: dict) -> dict:
    result = {
        "strategy": "conservative rabbit-and-squirrel mixed detector label abstention",
        "changes_to_models_or_similarity_thresholds": False,
        "scope": "animal-only cartoon proof; not biometric identity",
        "box_threshold": raw.get("box_threshold"),
        "match_threshold": raw.get("match_threshold"),
        "frames": [],
    }
    for frame in raw["frames"]:
        guarded = [decision(p) for p in frame["proposals"]]
        result["frames"].append({
            "second": frame["second"],
            "raw_predicted_buck": any(p["raw_buck_candidate"] for p in guarded),
            "guarded_predicted_buck": any(p["guarded_buck_candidate"]
                                           for p in guarded),
            "ambiguous_raw_matches_needing_review": sum(
                p["raw_buck_candidate"] and
                p["requires_review_ambiguous_detector_label"]
                for p in guarded),
            "proposals": guarded,
        })
    result["raw_positive_timestamps"] = [
        f["second"] for f in result["frames"] if f["raw_predicted_buck"]]
    result["guarded_positive_timestamps"] = [
        f["second"] for f in result["frames"] if f["guarded_predicted_buck"]]
    result["ambiguous_match_count"] = sum(
        f["ambiguous_raw_matches_needing_review"] for f in result["frames"])
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=FILM_PROBE)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    if args.output is None:
        args.output = args.input.with_name("guarded_result.json")
    actual = json.loads(args.input.read_text(encoding="utf-8"))
    scored = apply_guard(actual)
    args.output.write_text(json.dumps(scored, indent=2, ensure_ascii=False),
                           encoding="utf-8")
    print(json.dumps({
        "original_match_timestamps": scored["raw_positive_timestamps"],
        "guarded_match_timestamps": scored["guarded_positive_timestamps"],
        "ambiguous_match_count": scored["ambiguous_match_count"],
        "output": str(args.output),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
