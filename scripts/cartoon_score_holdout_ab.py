"""Reproduce frame-level A/B results from immutable post-inference human truth.

This script does not retune models or invent labels; output separates source
provenance, manual visual labels, unknown frames, and raw versus guarded
decisions. Thresholds are identical in both arms (0.28 detection/0.42 match).

Usage:
  python -m scripts.cartoon_score_holdout_ab \
      --result reports/cartoon-4m30-unseen-holdout/guarded_result.json \
      --summary reports/cartoon-4m30-unseen-holdout/summary.json
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from scripts.cartoon_video_recognize import ROOT

DEFAULT = ROOT / "reports/cartoon-4m30-unseen-holdout"
LABELS = ROOT / "tests/data/cartoon_holdout_4m30_visual_truth_v1.json"


def counts(entries, key):
    tally = {"tp": 0, "fp": 0, "tn": 0, "fn": 0, "uncertain_excluded": 0}
    detail = []
    for stamp, (label, frame) in sorted(entries.items()):
        truth = label["buck"]
        value = frame[key]
        if truth == "uncertain":
            outcome = "uncertain_excluded"
        elif truth == "present":
            outcome = "tp" if value else "fn"
        elif truth == "absent":
            outcome = "fp" if value else "tn"
        else:
            raise ValueError(f"Unrecognized truth label at {stamp}: {truth}")
        tally[outcome] += 1
        detail.append({"second":stamp,"truth":truth,"predicted_buck":value,
                       "outcome":outcome,"annotation":label["why"]})
    return {
        "confusion_counts": tally,
        "frame_precision": (
            tally["tp"]/(tally["tp"]+tally["fp"])
            if tally["tp"]+tally["fp"] else None),
        "recall_of_clear_buck_frames": (
            tally["tp"]/(tally["tp"]+tally["fn"])
            if tally["tp"]+tally["fn"] else None),
        "correct_rejection_of_clear_non_buck_frames": (
            tally["tn"]/(tally["tn"]+tally["fp"])
            if tally["tn"]+tally["fp"] else None),
        "frame_details":detail,
    }


def score(guarded, summary, labels):
    if summary["movie_sha256"] != labels["media_sha256"]:
        raise AssertionError("Annotated source hash differs from frozen film")
    if summary["original_film_time_window_seconds"] != [
        labels["excerpt_start_sec"],labels["excerpt_end_sec"]]:
        raise AssertionError("Model and human evaluated different film times")
    frozen = summary["parameters_frozen_before_test"]
    assert abs(frozen["box_threshold"]-0.28)<1e-10
    assert abs(frozen["match_threshold"]-0.42)<1e-10
    assert abs(guarded["match_threshold"]-0.42)<1e-10
    truth={float(x["second"]):x for x in labels["labels"]}
    prediction={float(x["second"]):x for x in guarded["frames"]}
    if set(truth) != set(prediction):
        raise AssertionError("Human and model timestamps differ; do NOT fill missing frames")
    merged={t:(truth[t],prediction[t]) for t in truth}
    a=counts(merged,"raw_predicted_buck")
    b=counts(merged,"guarded_predicted_buck")
    return {
        "source_excerpt_sha256":summary["movie_sha256"],
        "source_film_seconds":summary["original_film_time_window_seconds"],
        "visually_labeled_after_prediction_not_blind":True,
        "not_general_population_accuracy":True,
        "sampled_frames":len(prediction),
        "raw_frozen_model_A":a,
        "label_ambiguity_guard_B":b,
        "guard_added_true_buck_misses": b["confusion_counts"]["fn"]-a["confusion_counts"]["fn"],
        "guard_false_positives_prevented":a["confusion_counts"]["fp"]-b["confusion_counts"]["fp"],
        "guard_quality_gate":(
            "REGRESSION_DO_NOT_SHIP" if
            b["confusion_counts"]["fn"]>a["confusion_counts"]["fn"] or
            b["confusion_counts"]["fp"]>a["confusion_counts"]["fp"]
            else "NO_REGRESSION_OBSERVED_IN_SMALL_HOLDOUT"),
    }


def main(argv=None):
    parser=argparse.ArgumentParser()
    parser.add_argument("--result",type=Path,default=DEFAULT/"guarded_result.json")
    parser.add_argument("--summary",type=Path,default=DEFAULT/"summary.json")
    parser.add_argument("--truth",type=Path,default=LABELS)
    parser.add_argument("--out",type=Path,default=DEFAULT/"audited_ab_metrics.json")
    args=parser.parse_args(argv)
    def read(f):return json.loads(f.read_text(encoding="utf-8"))
    result=score(read(args.result),read(args.summary),read(args.truth))
    args.out.parent.mkdir(exist_ok=True,parents=True)
    args.out.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({
        "guard_quality_gate":result["guard_quality_gate"],
        "raw":result["raw_frozen_model_A"]["confusion_counts"],
        "guard":result["label_ambiguity_guard_B"]["confusion_counts"],
        "extra_fn":result["guard_added_true_buck_misses"],
        "saved":str(args.out),
    },ensure_ascii=False,indent=2))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
