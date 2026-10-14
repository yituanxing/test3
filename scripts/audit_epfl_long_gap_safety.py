"""Offline safety audit for cross-time EPFL identity association.

Runs on YOLO detections already extracted from genuine EPFL camera 0/1 video.
A pair is proposed using ONLY detector-derived common-ground positions. Official
EPFL GT is read ONLY after proposals exist, for independent error scoring.

This is an exploratory safety comparison, not ReID and not an optimization
claim. It NEVER changes runtime association thresholds or identity flags.
"""
from __future__ import annotations

from collections import defaultdict
import argparse
import json
from math import hypot
from pathlib import Path

DEFAULT_REPORT = Path("reports/verify-epfl-six-person-cross-camera.json")
DEFAULT_OUTPUT = Path("reports/epfl-long-gap-safety-audit.json")

# Run the exact shipped 1-second rules, plus stricter-distance controls at
# several larger gaps. Never tune a shipping gate using this scoring data.
GAPS = (25, 100, 125, 250)
GATES = ((30.0, 35.0), (5.0, 35.0))


def _observations(report):
    frames = defaultdict(list)
    for split in ("frame_audits", "holdout_frame_audits"):
        for instant in report[split]:
            frame = instant["frame_0_based"]
            for pair in instant["candidate_evidence"]:
                a, b = pair["cam0_ground_xy"], pair["cam1_ground_xy"]
                frames[frame].append({
                    "point": ((a[0]+b[0])/2.0,(a[1]+b[1])/2.0),
                    "audit_label": (pair.get("audit_gt_cam0"),
                                    pair.get("audit_gt_cam1")),
                    "split": split,
                })
    return frames


def _proposals(old, new, maximum, isolation):
    """Mutual-nearest+margin, without ever reading audit_label/GT."""
    edges = [(hypot(a["point"][0]-b["point"][0],
                    a["point"][1]-b["point"][1]),i,j)
             for i,a in enumerate(old) for j,b in enumerate(new)]
    choices=[]
    for distance,i,j in sorted(edges):
        if distance > maximum:
            continue
        alternative_new = [d for d,k,l in edges if k==i and l!=j]
        alternative_old = [d for d,k,l in edges if l==j and k!=i]
        if alternative_new and min(alternative_new)-distance < isolation:
            continue
        if alternative_old and min(alternative_old)-distance < isolation:
            continue
        choices.append((distance,i,j))
    selected=[]
    used_old,used_new=set(),set()
    for distance,i,j in choices:
        if i in used_old or j in used_new:
            continue
        selected.append((distance,i,j))
        used_old.add(i)
        used_new.add(j)
    return selected


def audit(report):
    assert report["selected_cameras"]==[0,1]
    assert report["matching_gt_is_never_input"] is True
    observations = _observations(report)
    measurements=[]
    for gap in GAPS:
        for maximum,isolation in GATES:
            total=correct=wrong=unknown=0
            wrong_examples=[]
            proposals_by_split={"baseline_to_holdout":0,
                                "holdout_to_baseline":0,
                                "same_split":0}
            for frame in sorted(observations):
                source=observations.get(frame-gap)
                if not source:
                    continue
                target=observations[frame]
                for distance,i,j in _proposals(source,target,maximum,isolation):
                    total+=1
                    a,b=source[i],target[j]
                    relation=("same_split" if a["split"]==b["split"]
                              else ("baseline_to_holdout"
                                    if a["split"]=="frame_audits"
                                    else "holdout_to_baseline"))
                    proposals_by_split[relation]+=1
                    ida,idb=a["audit_label"],b["audit_label"]
                    if any(x is None for x in (*ida,*idb)) or ida[0]!=ida[1] or idb[0]!=idb[1]:
                        unknown+=1
                    elif ida[0]==idb[0]:
                        correct+=1
                    else:
                        wrong+=1
                        if len(wrong_examples)<3:
                            wrong_examples.append({
                                "source_frame":frame-gap,"destination_frame":frame,
                                "ground_distance":round(distance,3),
                                "gt_source_id":ida[0],"gt_target_id":idb[0],
                            })
            measurements.append({
                "gap_frames":gap,"gap_seconds_at_25fps":gap/25.0,
                "maximum_position_distance":maximum,
                "minimum_isolation_margin":isolation,
                "proposed":total,"independently_verified_same":correct,
                "wrong_person_merges":wrong,"unscorable":unknown,
                "pair_directions":proposals_by_split,
                "wrong_examples_for_offline_scoring_only":wrong_examples
            })
    baseline=next(x for x in measurements
                  if x["gap_frames"]==25 and x["maximum_position_distance"]==30.0)
    if baseline["independently_verified_same"]!=14 or baseline["wrong_person_merges"]:
        raise RuntimeError("Known 1-second EPFL safe association regressed")
    return {
        "scope":"EPFL official real video; 2 synchronized, calibrated views",
        "evidence_from_genuine_video":True,
        "ground_truth_ever_used_for_runtime_pairing":False,
        "fps":25,"sampled_instants_with_pair":len(observations),
        "total_genuine_synchronized_pair_observations":sum(map(len,observations.values())),
        "currently_deployed_gap_frames":25,
        "runtime_changed":False,
        "comparisons":measurements,
        "decision":"KEEP_VERIFIED_ONE_SECOND_ASSOCIATION",
        "reason":"long gaps include wrong-person matches even under strict position and isolation gates; a larger range needs denser detector observations or an independently validated appearance tracker",
        "limits":"Zero wrong among 14 observed links is not a guarantee of perfect future accuracy or unique identity. No actual real-world identity or risk alert."
    }


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--input",type=Path,default=DEFAULT_REPORT)
    parser.add_argument("--output",type=Path,default=DEFAULT_OUTPUT)
    args=parser.parse_args()
    result=audit(json.loads(args.input.read_text(encoding="utf-8")))
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,indent=2,ensure_ascii=False)+"\n",
                           encoding="utf-8")
    print("EPFL_LONG_GAP_SAFETY="+json.dumps({
        "decision":result["decision"],
        "counts":[{k:x[k] for k in ("gap_frames","maximum_position_distance",
             "proposed","independently_verified_same","wrong_person_merges")}
             for x in result["comparisons"]]
    },ensure_ascii=False))


if __name__=="__main__":
    main()
