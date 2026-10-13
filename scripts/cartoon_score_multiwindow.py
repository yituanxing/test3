"""Score TWO PRECOMMITTED 25-second cartoon holdouts with separate visual truth.

Truth JSON is created only AFTER model outputs have been archived. Evidence
SHA256, frame timestamps, algorithm names and frozen thresholds are
validated before scoring. Unknown identity is excluded; report individual
TP/FP/FN/TN, not vague 'match rate'. This is not a blinded assessment or a
general accuracy metric.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

# Pure stdlib scoring, deliberately independent of torch/transformers.
ROOT = Path(__file__).resolve().parents[1]

DIR = ROOT / "reports/cartoon-multiwindow-holdout"
LABELS = ROOT / "tests/data/cartoon_multiwindow_visual_truth_v1.json"
WINDOWS = ("original_2m00", "original_7m00")


def _rates(count):
    tp,tn,fp,fn=[count[x] for x in ("tp","tn","fp","fn")]
    return {
        "precision": tp/(tp+fp) if tp+fp else None,
        "recall_clearly_visible_buck": tp/(tp+fn) if tp+fn else None,
        "false_positive_rate": fp/(fp+tn) if fp+tn else None,
    }


def score(summary:dict, predictions:dict, truth:dict)->dict:
    if truth["annotation_blinded"]:
        raise ValueError("Post-inference labels must not pretend to be blinded")
    if set(truth["windows"])!=set(WINDOWS):
        raise ValueError("Both original precommitted windows must be labeled")
    if set(summary["windows"])!=set(WINDOWS):
        raise ValueError("Missing original windows in actual video report")
    if abs(float(summary["box_threshold"])-.28)>1e-10 or abs(
            float(summary["match_threshold"])-.42)>1e-10:
        raise ValueError("Model baseline parameter was changed after holdout selection")

    totals={
        "A":dict(tp=0,fp=0,tn=0,fn=0,uncertain_excluded=0),
        "B":dict(tp=0,fp=0,tn=0,fn=0,uncertain_excluded=0),
    }
    evidence={}
    for name in WINDOWS:
        meta=summary["windows"][name]
        evidence_by_window=truth["windows"][name]
        if meta["movie_sha256"]!=evidence_by_window["sha256"]:
            raise ValueError("Video SHA256 mismatch: "+name)
        if meta["original_movie_window"]!=evidence_by_window["original_movie_window"]:
            raise ValueError("Video time window mismatch: "+name)
        data=predictions[name]
        if data["method"]!="harmonic_mean_of_two_frozen_cosines":
            raise ValueError("Unexpected B method in "+name)
        pred={float(r["second"]):r for r in data["frames"]}
        labels={float(r["second"]):r for r in evidence_by_window["labels"]}
        if set(pred)!=set(labels):
            raise ValueError(f"Different frame timestamps in {name}")
        counts={k:dict(tp=0,fp=0,tn=0,fn=0,uncertain_excluded=0)
                for k in ("A","B")}
        rows=[]
        for timestamp in sorted(pred):
            annot=labels[timestamp]
            truth_value=annot["buck"]
            record=pred[timestamp]
            row={
                "movie_time_second":meta["original_movie_window"][0]+timestamp,
                "relative_second":timestamp,
                "human_visual_truth":truth_value,
                "visual_note":annot["note"],
                "A_raw_max_buck":record["raw_predicted_buck"],
                "B_harmonic_buck":record["consensus_predicted_buck"],
                "proposal_count":len(record["proposals"]),
            }
            for arm,col in (("A","raw_predicted_buck"),
                            ("B","consensus_predicted_buck")):
                verdict=bool(record[col])
                if truth_value=="uncertain":
                    outcome="uncertain_excluded"
                elif truth_value=="present":
                    outcome="tp" if verdict else "fn"
                elif truth_value=="absent":
                    outcome="fp" if verdict else "tn"
                else:
                    raise ValueError(f"Unrecognized human label: {truth_value}")
                counts[arm][outcome]+=1
                totals[arm][outcome]+=1
                row[arm+"_outcome"]=outcome
            rows.append(row)
        evidence[name]={
            "sha256":meta["movie_sha256"],
            "original_movie_window":meta["original_movie_window"],
            "frames":rows,
            "arms":{k:{"counts":v,**_rates(v)} for k,v in counts.items()},
        }
    a,b=totals["A"],totals["B"]
    return {
        "status":"MANUAL_POST_PREDICTION_AUDIT_SMALL_SAMPLE",
        "truth_created_after_first_holdout_model_run":True,
        "not_a_blinded_benchmark":True,
        "not_general_animation_accuracy":True,
        "source":"Blender Big Buck Bunny CC BY 3.0",
        "sampling_info":"20 original film timestamps over two unmodified 25-second video excerpts",
        "tuned_on_this_evaluation":False,
        "windows":evidence,
        "pooled_two_windows":{k:{"counts":v,**_rates(v)} for k,v in totals.items()},
        "B_false_positives_prevented":a["fp"]-b["fp"],
        "B_additional_false_negatives":b["fn"]-a["fn"],
        "B_quality_verdict":(
            "REGRESSION_HARMONIC_ADDS_FALSE_NEGATIVES"
            if b["fn"]>a["fn"] else
            "REGRESSION_HARMONIC_ADDS_FALSE_POSITIVES"
            if b["fp"]>a["fp"] else
            "NO_REGRESSION_OBSERVED_IN_THIS_SMALL_SAMPLE"),
    }


def main(argv=None):
    parser=argparse.ArgumentParser()
    parser.add_argument("--summary",type=Path,default=DIR/"summary.json")
    parser.add_argument("--labels",type=Path,default=LABELS)
    parser.add_argument("--output",type=Path,default=DIR/"audited_metrics.json")
    args=parser.parse_args(argv)
    def read(path):return json.loads(path.read_text(encoding="utf-8"))
    measured=read(args.summary)
    truth=read(args.labels)
    preds={x:read(DIR/x/"consensus.json") for x in WINDOWS}
    metrics=score(measured,preds,truth)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(
        json.dumps(metrics,indent=2,ensure_ascii=False),encoding="utf-8")
    print(json.dumps({
        "pooled_two_windows":metrics["pooled_two_windows"],
        "B_quality_verdict":metrics["B_quality_verdict"],
        "B_false_positives_prevented":metrics["B_false_positives_prevented"],
        "B_additional_false_negatives":metrics["B_additional_false_negatives"],
    },indent=2,ensure_ascii=False))
    return 0

if __name__=="__main__":
    raise SystemExit(main())
