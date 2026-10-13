"""Score the first NEVER-TUNED Big Buck Bunny 3-minute-scene evaluation.

Ground truth is visually reviewed and separately frozen. We explicitly
exclude the partial-ear frame rather than invent a positive/negative answer.
An FP never becomes PASS merely because >=2 other frames matched.
No model weights or thresholds are modified here.

Usage:
  python -m scripts.cartoon_holdout_score \
      --predictions reports/cartoon-full-film-holdout/evaluation/result.json \
      --summary reports/cartoon-full-film-holdout/frozen_result.json
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from scripts.cartoon_video_recognize import ROOT

TRUTH = ROOT / "tests/data/cartoon_holdout_3m_manual_truth_v1.json"


def analyze(predictions: dict, summary: dict, labels: dict) -> dict:
    assert summary["source_sha256"] == labels["segment_sha256"], (
        "Source video changed, human truth is invalid: do not mix versions")
    assert abs(summary["source_original_film_start_second"] -
               labels["original_segment_start_second"]) < 1e-6
    true_at = {float(row["second"]): row for row in labels["labels"]}
    pred_at = {float(row["second"]): row for row in predictions["frames"]}
    if set(true_at) != set(pred_at):
        raise ValueError(f"Human truth timestamps don't match decoded frames: "
                         f"truth={sorted(true_at)} actual={sorted(pred_at)}")

    count = {"tp": 0, "fn": 0, "tn": 0, "fp": 0, "ambiguous_excluded": 0}
    rows = []
    fp_details = []
    for t, annotation in sorted(true_at.items()):
        frame = pred_at[t]
        accepted = [p for p in frame["proposals"]
                    if p["verdict"] == "buck_candidate"]
        predicted = bool(accepted)
        truth = annotation["truth"]
        if truth == "ambiguous":
            outcome = "ambiguous_excluded"
        elif truth == "present":
            outcome = "tp" if predicted else "fn"
        elif truth == "absent":
            outcome = "fp" if predicted else "tn"
        else:
            raise ValueError("Unknown human truth category: " + truth)
        count[outcome] += 1
        rows.append({
            "second": t,
            "buck_visible_visual_truth": truth,
            "model_predicted_buck": predicted,
            "outcome": outcome,
            "reason": annotation["note"],
        })
        if outcome == "fp":
            fp_details.extend({
                "second": t,
                "box_xyxy": p["box_xyxy"],
                "cosine_similarity": p["cosine_similarity"],
                "source_label_from_detector": p["label"],
                "scores_against_frozen_references": p.get("reference_similarities"),
            } for p in accepted)
    assert sum(count.values()) == len(rows)
    positive = count["tp"] + count["fn"]
    negative = count["tn"] + count["fp"]
    precision_denominator = count["tp"] + count["fp"]
    result = {
        "status": ("FALSE_POSITIVE_OBSERVED" if count["fp"] > 0
                   else "NO_FALSE_POSITIVE_IN_TINY_EVALUATION"),
        "not_a_general_accuracy_claim": True,
        "annotation_is_post_prediction_not_blinded": True,
        "full_film_start_second": labels["original_segment_start_second"],
        "source_hash_verified": True,
        "frozen_match_threshold": predictions["match_threshold"],
        "confusion_counts": count,
        "frame_precision": (
            count["tp"] / precision_denominator if precision_denominator else None),
        "frame_recall_of_clearly_visible_buck": (
            count["tp"] / positive if positive else None),
        "frame_specificity_of_clearly_negative_frames": (
            count["tn"] / negative if negative else None),
        "false_positive_details": fp_details,
        "frame_by_frame_audit": rows,
        "gate_zero_false_positives": count["fp"] == 0,
        "caution": (
            "One scene, manually reviewed after inference, small sample. "
            "Do NOT tune 0.42 or models on this data then claim a fresh test."
        ),
    }
    return result


def main(argv=None):
    parser = argparse.ArgumentParser()
    folder = ROOT / "reports/cartoon-full-film-holdout"
    parser.add_argument("--predictions", type=Path,
                        default=folder / "evaluation/result.json")
    parser.add_argument("--summary", type=Path,
                        default=folder / "frozen_result.json")
    parser.add_argument("--truth", type=Path, default=TRUTH)
    parser.add_argument("--output", type=Path, default=folder / "audited_metrics.json")
    args = parser.parse_args(argv)
    def read(path):
        return json.loads(path.read_text(encoding="utf-8"))
    scored = analyze(read(args.predictions), read(args.summary), read(args.truth))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(scored, ensure_ascii=False, indent=2),
                           encoding="utf-8")
    print(json.dumps({
        "status": scored["status"],
        "confusion_counts": scored["confusion_counts"],
        "frame_precision": scored["frame_precision"],
        "frame_recall_of_clearly_visible_buck":
            scored["frame_recall_of_clearly_visible_buck"],
        "frame_specificity_of_clearly_negative_frames":
            scored["frame_specificity_of_clearly_negative_frames"],
        "false_positive_details": scored["false_positive_details"]
    }, ensure_ascii=False, indent=2))
    return 0  # Evidence capture is distinct from the strict quality gate.


if __name__ == "__main__":
    raise SystemExit(main())
