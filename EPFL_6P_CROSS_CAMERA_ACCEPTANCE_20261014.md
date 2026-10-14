# EPFL six-person real multi-camera proof — 2026-10-14

## What is actually verified

Research video: official EPFL CVLab *six-person laboratory*, synchronized cameras 0 and 1.
Official data and calibration: https://www.epfl.ch/labs/cvlab/data/data-pom-index-php/

- YOLO11n predicts original AVI frames directly at model confidence .25/imgsz 640.
- Original *official* 6-person grid GT (`gt_lab_6p.txt`) has 2,955 frame rows,
  119 GT-labelled instants at 25-frame intervals, 56x56 top-view grid.
  Person-ID is the **column index**; -1 is missing label, -2 is out of tracking grid.
- Download and verify two original `6p-c*.avi`, metadata, and
  `calibration-6p.txt` from official EPFL hosts. Compute SHA-256.
- Existing Roboflow ByteTrack handles two 24-frame contiguous windows **separately
  per camera** (camera-local tracking; never global IDs).
- With calibration H mapping *image to top-view*, project YOLO bounding-box
  foot-center to a common plane. Use SciPy Hungarian one-to-one assignment with
  a fixed 25-unit maximum for cross-camera candidates. The 6p human-ID GT is
  **not an input to the matcher**.
- Independently map each detected box to official GT at a fixed 30-unit
  ground-plane gate for audit. Score only pairs where both sides received an
  independently assigned official person-ID; unlabelled pairs must remain
  unscorable, not counted as correct or wrong.

## First bounded observation

CI [#38500141072](https://github.com/yituanxing/test3/actions/runs/38500141072)
(commit `b90dacca2e1ffc66a0214470323c4497d2c54dcc`) passed.

- 21 truth-labelled time instants sampled at fixed 125-frame spacing,
  from 0-based frame 250 to 2750.
- YOLO boxes: camera0 **86**, camera1 **87**.
- Geometric cross-camera candidates: **62**.
- Of these candidates, **62** matched the same official EPFL GT person-ID,
  **0** matched different GT person-IDs, **0** were unscorable on both sides.
- There were **64** truth identities detected and independently GT-assigned
  in both camera views in those frame instants. Correctly associated:
  **62/64 = 96.88% conditional on detection and evaluability**.
- Correct / scored pairs **62/62**. This is **NOT** unconditional person
  detection recall, a confidence interval, face recognition accuracy, or a
  proof of no errors in other video periods/settings.
- Real ByteTrack 24-frame samples were also verified for each camera.
- Full diagnostics: CI artifact `public-caviar-actual-video-proof` includes
  `verify-epfl-six-person-cross-camera.json` and camera contact sheets.

## Decision and remaining acceptance gaps

**Accepted as the primary *research* video for the next anonymous cross-camera
candidate demo. Not accepted as identified real-person roster or automatic alert.**

Important distinctions:
1. Our successful experiment is **synchronous overlap / calibrated-ground-plane
   association**, not long-gap appearance-based ReID. It cannot identify a
   person who left one camera and later entered an uncalibrated/different site.
2. The dataset is **one physical laboratory** filmed by several cameras, not
   multiple independent venues. It cannot verify Excel cross-*venue* risk rules.
3. The official grid labels are sparse, and some true people may be out of grid
   or missed by YOLO. All results must preserve unknown/abstention cases.
4. No production API currently consumes the research pairing output. Existing
   `/api/site/occurrences` and quality / manual-review workflows remain the
   operational evidence interfaces and must not be bypassed by GT or a CI report.
5. D### manual research links and P### synthetic scenario-based alerts are
   separate workflows. This benchmark supplies neither biometrics nor a verified
   named natural person; **do not auto-publish into any person roster**.
6. EPFL materials are copyrighted and **research-use only**; do not include
   original videos/GT in distributed Demo archives or imply unrestricted use.

## Shortest reliable path to V1 finishing

1. **Freeze** model, fixed calibration, thresholds, GT-independent matching,
   and artifact provenance. Extend held-out frame/time evaluation without tuning
   thresholds against held-out GT; include high-density/occlusion and negative
   different-person examples.
2. Add an **optional candidate-only adapter** at the existing anonymous
   evidence/quality-review boundary, carrying both source IDs, frame/time,
   local tracks, pairing distance and immutable source/model/calibration hashes.
   Disallow GT IDs and unreviewed auto-promotions.
3. In the existing /cctv UI, show actual paired frame proof + operator review
   of proposed links. Do not build a second identity/alert architecture.
4. Check the Excel 23-item acceptance matrix using distinct: verified real
   video facts, research-only candidates, synthetic rule demo, and missing.
   Audit permissions, run repeatability, source licensing, and package size.
5. Consolidate docs, mark old experiments historical, reuse the **existing
   CI** with owner label; no new workflows or extra heavy full-video loops.

**Go/no-go:** Candidate association A/B **PASS on this bounded research set**.
Production real-person identity/real alert **NO_GO** until separate
authorization, independent identity evaluation, and alert correctness evidence.

## V1 implementation and final evidence (2026-10-14)

- Code: `app/epfl_review.py`, `tests/test_epfl_review.py`, `static/cctv.html`, `scripts/browser_epfl_six_person_review.py`.
- Admin-only report import from genuine CI data; immutable research-only geometry manifest stored in SQLite; candidate review decisions append-only with `admin_audit`; official EPFL GT IDs not stored or exposed.
- Frozen holdout: 20 extra synchronization instants, 57/57 official-ID-agreeing geometry candidates, 0 mismatches. Original baseline 62/62; combined 119/119 of 41 labelled instants, 119/123 conditional association coverage. Neither a unique-person count nor general ReID.
- CI [#38503988215](https://github.com/yituanxing/test3/actions/runs/38503988215) SUCCESS: 164 passed +6 skipped, model scenario 9 passed, geometry-negative 4 passed. Same CI actual-video-produced JSON imported via Chromium; 119 candidates in same SQLite, one reviewed record, two audit entries, zero synthetic roster or real risk alerts generated.
- Uploading a locally edited JSON does not cryptographically prove results; model/video source SHA pinning reduces accidental mixing only. Promote no candidate to verified real identity. Formal Excel item statuses remain unchanged.
