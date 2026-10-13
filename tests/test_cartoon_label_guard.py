"""No-network label-ambiguity safeguards; run via python -m unittest."""
from unittest import TestCase
from scripts.cartoon_label_guard import is_ambiguous,decision,apply_guard

class LabelGuardTests(TestCase):
    def test_detector_phrases(self):
        self.assertTrue(is_ambiguous("a big white rabbit a small squirrel"))
        self.assertTrue(is_ambiguous("a cartoon bunny a small squirrel"))
        for label in ("a big white rabbit","a cartoon bunny","a small squirrel",""):
            self.assertFalse(is_ambiguous(label))

    def test_do_not_change_raw_scores_or_raw_verdict(self):
        original = {
            "verdict":"buck_candidate",
            "cosine_similarity":0.453943,
            "label":"a big white rabbit a small squirrel",
            "box_xyxy":[78,59,355,356],
            "reference_similarities":{"buck_reference":0.387654,"buck_bow":0.453943},
        }
        result = decision(original)
        self.assertFalse(result["guarded_buck_candidate"])
        self.assertEqual(result["guarded_status"],"needs_review")
        self.assertTrue(result["raw_buck_candidate"])
        self.assertEqual(result["cosine_similarity"],0.453943)
        self.assertEqual(result["reference_similarities"],original["reference_similarities"])
        self.assertEqual(original["verdict"],"buck_candidate")

    def test_clean_rabbit_stays_positive(self):
        p={"verdict":"buck_candidate","cosine_similarity":0.53,
           "label":"a big white rabbit"}
        self.assertTrue(decision(p)["guarded_buck_candidate"])

    def test_ambiguous_below_threshold_does_not_get_promoted(self):
        p={"verdict":"not_confirmed","cosine_similarity":0.27,
           "label":"a big white rabbit a small squirrel"}
        self.assertFalse(decision(p)["guarded_buck_candidate"])
        self.assertEqual(decision(p)["guarded_status"],"not_confirmed")

    def test_frame_with_clean_and_ambiguous_does_not_get_lost(self):
        raw={"box_threshold":.28,"match_threshold":.42,
             "frames":[{"second":13.0,"proposals":[
                {"label":"a big white rabbit","verdict":"buck_candidate","cosine_similarity":.52},
                {"label":"a big white rabbit a small squirrel","verdict":"buck_candidate","cosine_similarity":.54},
             ]},{"second":23.0,"proposals":[
                {"label":"a big white rabbit a small squirrel","verdict":"buck_candidate","cosine_similarity":.45},
             ]}]}
        improved=apply_guard(raw)
        self.assertEqual(improved["raw_positive_timestamps"],[13.0,23.0])
        self.assertEqual(improved["guarded_positive_timestamps"],[13.0])
        self.assertEqual(improved["ambiguous_match_count"],2)
