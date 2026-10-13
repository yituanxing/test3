"""Consensus of two pre-existing, automatically cropped Buck reference embeddings.

Design frozen BEFORE running another original-film holdout (5:30–5:55).
Model weights, reference image bytes, detector prompt, box threshold=0.28 and
cosine cutoff=0.42 do not change.

Old baseline A: max(score_front, score_bow) >= 0.42.
Candidate B: harmonic_mean(score_front, score_bow) >= 0.42.

Rationale: max allows ONE pose-specific score to override a weak second view.
The harmonic mean penalizes that disagreement continuously, without relying
on brittle detector language, hard rejection or new trained parameters.

Both cosine similarities MUST be > 0 for harmonic mean; if not, B rejects.
This is a changed AGGREGATION RULE, motivated by observed developmental
failures. It is not an untuned method: the formula choice was made after seeing
previous 3:00 and 4:30 development scenes. Evaluation on 5:30 is the first
genuinely unseen test of this frozen formula. Do not retune against its result.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from scripts.cartoon_video_recognize import ROOT

REFERENCE_ORDER = ("buck_reference", "buck_bow")
RULE = "harmonic_mean_of_two_frozen_cosines"
EVALUATED_CUTOFF = 0.42


def consensus_match(proposal: dict, *, threshold: float = EVALUATED_CUTOFF) -> dict:
    original = dict(proposal)
    values = proposal.get("reference_similarities", {})
    if set(REFERENCE_ORDER) - values.keys():
        raise ValueError("Two real auto-detected Buck references required; no fallback to single view")
    x, y = (float(values[key]) for key in REFERENCE_ORDER)
    score = (2*x*y/(x+y)) if x > 0 and y > 0 else min(x, y)
    raw = original["verdict"] == "buck_candidate"
    result = {
        **original,
        "raw_buck_candidate": raw,
        "consensus_score": round(score, 6),
        "consensus_buck_candidate": score >= threshold,
        "consensus_status": ("buck_candidate" if score >= threshold
                             else "not_confirmed"),
        "reference_disagreement": round(abs(x-y), 6),
        "consensus_rule": RULE,
    }
    return result


def evaluate_consensus(original: dict) -> dict:
    threshold = float(original["match_threshold"])
    if abs(threshold - EVALUATED_CUTOFF) > 1e-10:
        raise ValueError("Frozen A/B requires original .42 cutoff, not test-time tuning")
    if int(original["reference_count"]) != 2:
        raise ValueError("Frozen A/B requires exactly two prior detector-cropped reference images")
    result = {
        "formula_selected_after_development_observations": True,
        "heldout_rule_frozen_before_first_5m30_test": True,
        "unchanged_original_detector_and_reference_embeddings": True,
        "model_cutoff_unmodified": threshold,
        "method": RULE,
        "frames": [],
    }
    for frame in original["frames"]:
        rows = [consensus_match(proposal, threshold=threshold)
                for proposal in frame["proposals"]]
        result["frames"].append({
            "second": frame["second"],
            "raw_predicted_buck": any(p["raw_buck_candidate"] for p in rows),
            "consensus_predicted_buck": any(
                p["consensus_buck_candidate"] for p in rows),
            "proposals": rows,
        })
    result["raw_positive_timestamps"] = [
        f["second"] for f in result["frames"] if f["raw_predicted_buck"]]
    result["consensus_positive_timestamps"] = [
        f["second"] for f in result["frames"] if f["consensus_predicted_buck"]]
    result["original_positive_frames_rejected_by_consensus"] = [
        f["second"] for f in result["frames"]
        if f["raw_predicted_buck"] and not f["consensus_predicted_buck"]]
    result["new_positive_frames_not_in_original"] = [
        f["second"] for f in result["frames"]
        if f["consensus_predicted_buck"] and not f["raw_predicted_buck"]]
    return result


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path,
                        default=ROOT/"reports/cartoon-full-film-holdout/evaluation/result.json")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    if args.output is None:
        args.output = args.input.with_name("consensus_result.json")
    measured = json.loads(args.input.read_text(encoding="utf-8"))
    result = evaluate_consensus(measured)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False),
                           encoding="utf-8")
    print(json.dumps({
        "rule": RULE,
        "raw_positive": result["raw_positive_timestamps"],
        "consensus_positive": result["consensus_positive_timestamps"],
        "candidate_rejected": result["original_positive_frames_rejected_by_consensus"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
