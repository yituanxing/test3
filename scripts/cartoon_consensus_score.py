"""Score frozen 5m30 movie A/B against post-inference visually reviewed labels.

The exact first tested video is bound to SHA256 so numbers cannot be switched
between media. This is a small, non-blinded manual audit, not an accuracy
benchmark or evidence of all-cartoon performance.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
from scripts.cartoon_video_recognize import ROOT

HERE=ROOT/"reports/cartoon-5m30-consensus-holdout"
LABELS=ROOT/"tests/data/cartoon_consensus_5m30_visual_truth_v1.json"

def confusion(timelines, truth, decision):
    counters={"tp":0,"fp":0,"tn":0,"fn":0,"uncertain_excluded":0}
    per_frame=[]
    for row in truth["frames"]:
        second=float(row["second"])
        status=row["buck"]
        predicted=timelines[second][decision]
        if status=="uncertain":
            mark="uncertain_excluded"
        elif status=="present":
            mark="tp" if predicted else "fn"
        elif status=="absent":
            mark="fp" if predicted else "tn"
        else:
            raise AssertionError(f"unrecognized human truth: {status}")
        counters[mark]+=1
        per_frame.append({
            "second":second,"visual_truth":status,
            "model_positive":predicted,"outcome":mark,
            "note":row["why"],
        })
    return {
        "count":counters,
        "precision":(counters["tp"]/(counters["tp"]+counters["fp"])
                     if (counters["tp"]+counters["fp"]) else None),
        "visible_buck_recall":(counters["tp"]/(counters["tp"]+counters["fn"])
                              if (counters["tp"]+counters["fn"]) else None),
        "false_positive_rate":(counters["fp"]/(counters["fp"]+counters["tn"])
                               if (counters["fp"]+counters["tn"]) else None),
        "per_frame":per_frame,
    }


def score(result, summary, labels):
    if summary["excerpt_sha256"] != labels["frozen_video_sha256"]:
        raise AssertionError("Actual encoded video differs from frozen visual truth!")
    if summary["precommitted_time_window_in_full_film"] != labels["original_movie_seconds"]:
        raise AssertionError("Evaluating wrong movie timeline")
    if not summary["no_new_model_training_or_threshold_changes"]:
        raise AssertionError("Pretrained model or thresholds modified")
    refs=result["frames"]
    timelines={float(r["second"]):r for r in refs}
    human={float(r["second"]) for r in labels["frames"]}
    if set(timelines)!=human:
        raise AssertionError("Manual and model frame timestamps differ")
    a=confusion(timelines,labels,"raw_predicted_buck")
    b=confusion(timelines,labels,"consensus_predicted_buck")
    return {
        "experiment":"precommitted first evaluation 5m30-5m55, identical video/boxes/features",
        "model_params_unchanged":{"detector_box_cutoff":.28,"match_cosine_cutoff":.42},
        "source_video_sha256":summary["excerpt_sha256"],
        "manual_annotation_after_results_NOT_blind":True,
        "total_sampled_frames":len(timelines),
        "clear_positive_frames":a["count"]["tp"]+a["count"]["fn"],
        "clear_negative_frames":a["count"]["tn"]+a["count"]["fp"],
        "uncertain_excluded":a["count"]["uncertain_excluded"],
        "A_old_best_of_two":a,
        "B_new_harmonic_of_two":b,
        "false_positives_prevented":a["count"]["fp"]-b["count"]["fp"],
        "additional_false_negatives":b["count"]["fn"]-a["count"]["fn"],
        "decision":"SMALL_SAMPLE_PROMISING" if
                    (a["count"]["fp"]>b["count"]["fp"] and
                     a["count"]["fn"]==b["count"]["fn"])
                    else "NOT_DEMONSTRATED_NONREGRESSIVE",
        "not_a_generalization_accuracy_estimate":True,
    }


def main(argv=None):
    p=argparse.ArgumentParser()
    p.add_argument("--result",type=Path,default=HERE/"consensus_result.json")
    p.add_argument("--summary",type=Path,default=HERE/"summary.json")
    p.add_argument("--labels",type=Path,default=LABELS)
    p.add_argument("--out",type=Path,default=HERE/"audited_5m30_ab.json")
    args=p.parse_args(argv)
    def read(p): return json.loads(p.read_text(encoding="utf-8"))
    metrics=score(read(args.result),read(args.summary),read(args.labels))
    args.out.parent.mkdir(parents=True,exist_ok=True)
    args.out.write_text(json.dumps(metrics,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({
        "A":metrics["A_old_best_of_two"]["count"],
        "B":metrics["B_new_harmonic_of_two"]["count"],
        "false_positives_prevented":metrics["false_positives_prevented"],
        "additional_false_negatives":metrics["additional_false_negatives"],
        "status":metrics["decision"],
    },ensure_ascii=False,indent=2))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
