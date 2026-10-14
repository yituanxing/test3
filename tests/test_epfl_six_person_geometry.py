"""Offline edge cases for EPFL six-person evaluation; never uses GT for matching."""
import numpy as np
import pytest

from scripts.verify_epfl_six_person_ground_truth import (
    parse_gt, parse_calibration, project_foot, assign_points, audit_pair
)


def test_official_gt_columns_are_stable_person_ids():
    # Full 2,955-row original frame-index contract, labels only every 25.
    rows = ["-1 -1 -1 -1 -1 -1"] * 2955
    rows[0] = "-2 -1 -1 -1 -1 -1"
    rows[25] = "0 3135 -2 -1 -1 -1"
    rows[50] = "56 -1 -1 -1 -1 -1"
    truth, info = parse_gt(
        "\n".join(["1", "2955 6 56 56 25 0 2950", *rows]))
    assert info["annotated_frames"] == 119
    assert truth[0] == {}
    assert set(truth[25]) == {0, 1}
    assert truth[25][0] == (358.0 / 112, 360.0 / 112)
    assert truth[25][1][0] > truth[25][0][0]
    assert set(truth[50]) == {0}


def test_calibration_direction_and_foot_pixel_used():
    file = """##########
# Camera 0
##########
# Ground plane homography
1 0 5
0 2 -4
0 0 1
# Head plane homography
1 0 0
0 1 0
0 0 1
##########
# Camera 1
##########
# Ground plane homography
2 0 2
0 3 9
0 0 1
# Head plane homography
1 0 0
0 1 0
0 0 1
"""
    matrix = parse_calibration(file)
    assert project_foot(matrix[0], [2, 3, 6, 20]) == (9., 36.)
    assert project_foot(matrix[1], [2, 3, 6, 20]) == (10., 69.)


def test_unrelated_people_not_linked_by_equal_tracker_id():
    pytest.importorskip("scipy")
    # Candidate matcher sees only points. Track number / human GT ID is absent.
    assert assign_points([(10., 10.)], [(100., 100.)], 25.) == []
    assert len(assign_points([(10., 10.)], [(12., 11.)], 25.)) == 1


def test_audit_counts_wrong_identity_and_unknown_separately():
    pytest.importorskip("scipy")
    def detection(point):
        return {"world_xy": list(point)}
    # Deliberately distinct official ID truth despite close spatial
    # positions: a geometric candidate is NOT automatic identity.
    score = audit_pair((
        [detection((10, 10))],
        [detection((12, 10))],
    ), {0: (10, 10), 1: (15, 10)})
    # Both camera detections may be within same GT gate; global one-to-one
    # can validly pick person 0 on both sides. Use audit on unequivocal
    # GT assignments instead for false-match verification:
    bad = audit_pair((
        [detection((0, 0))],
        [detection((23, 0))],
    ), {0: (0, 0), 1: (23, 0)})
    # At a coarse 30-unit GT gate each independently chooses its exact truth.
    assert bad["verified_different_gt_person_matches"] == 1
    assert bad["verified_same_gt_person_matches"] == 0
    assert bad["unscorable_pairs_no_two_sided_gt"] == 0
    unknown = audit_pair((
        [detection((0, 0))],
        [detection((24, 0))],
    ), {})
    assert unknown["unscorable_pairs_no_two_sided_gt"] == 1
    assert unknown["verified_same_gt_person_matches"] == 0
