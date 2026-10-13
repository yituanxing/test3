"""Frozen scoring aggregation unit tests, no downloading models/videos."""
import unittest

from scripts.cartoon_consensus_match import consensus_match, evaluate_consensus


def box(front, bow, verdict="buck_candidate"):
    return {
        "reference_similarities":{"buck_reference":front,"buck_bow":bow},
        "cosine_similarity":max(front,bow),
        "verdict":verdict,
        "label":"a big white rabbit a small squirrel",
    }


class TwoReferenceConsensusTests(unittest.TestCase):
    def test_two_disagreeing_views_must_not_rely_on_max(self):
        # Historic DEVELOPMENT squirrel; 0.454 max incorrectly passed.
        r=consensus_match(box(.387654,.453943))
        self.assertTrue(r["raw_buck_candidate"])
        self.assertFalse(r["consensus_buck_candidate"])
        self.assertLess(r["consensus_score"],.42)

    def test_real_development_buck_with_mixed_detector_label_remains(self):
        # Genuine Buck @ 4m30 +3.0s, shows why label veto was incorrect.
        r=consensus_match(box(.46585,.539256))
        self.assertTrue(r["consensus_buck_candidate"])
        self.assertEqual(r["label"],"a big white rabbit a small squirrel")

    def test_two_genuine_views_remain(self):
        r=consensus_match(box(.5299,.39819))
        self.assertTrue(r["consensus_buck_candidate"])

    def test_cannot_make_unconfirmed_box_positive(self):
        for x,y in [(0.3,0.35),(-0.3,.65),(0.39,0.40)]:
            r=consensus_match(box(x,y,verdict="not_confirmed"))
            self.assertFalse(r["consensus_buck_candidate"])

    def test_missing_second_reference_is_not_silently_accepted(self):
        with self.assertRaises(ValueError):
            consensus_match({"verdict":"buck_candidate",
                             "reference_similarities":{"buck_reference":.9}})

    def test_takes_identical_original_boxes_and_no_new_inference(self):
        source={
            "match_threshold":.42,
            "reference_count":2,
            "frames":[
                {"second":0.5,"proposals":[box(.50,.53)]},
                {"second":3.0,"proposals":[box(.20,.53)]},
                {"second":5.5,"proposals":[]},
            ],
        }
        result=evaluate_consensus(source)
        self.assertEqual(result["raw_positive_timestamps"],[.5,3.])
        self.assertEqual(result["consensus_positive_timestamps"],[.5])
        self.assertEqual(result["original_positive_frames_rejected_by_consensus"],[3.])
        self.assertEqual(source["frames"][1]["proposals"][0]["verdict"],
                         "buck_candidate")


if __name__=="__main__":
    unittest.main()
