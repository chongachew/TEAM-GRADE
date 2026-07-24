"""
Regression test for a real production bug: VectorizedTraitScorer's
_get_feature_names() only read reps_data[0].keys() to determine the feature
schema for the WHOLE batch. A rep with no pose data (empty features dict -
_extract_pose_features returns {} when a rep has zero matched pose frames,
a real and common case on sparse multi-player tracks) sorting first in the
batch collapsed feature_names to [], which zeroed the (N_reps, 0) feature
matrix and, via the matmul in score_traits_batch, silently zeroed EVERY
rep's score - not just the empty one. First surfaced for real when a
completed production video (kJM5Uk9DtoQ) scored overall_grade: 0.0 /
letter_grade: "F" across all 369 reps, most of which had perfectly good
pose data.
"""

from processing.vectorized_traits import VectorizedTraitScorer

TRAIT_CONFIGS = {
    "strength": {"features": ["leg_power", "core_stability", "upper_body"], "weights": [0.5, 0.3, 0.2]},
}

GOOD_REP = {
    "leg_power": 90, "core_stability": 85, "upper_body": 80,
}


def test_empty_first_rep_no_longer_zeroes_out_reps_with_real_data():
    scorer = VectorizedTraitScorer()
    reps_data = [{}, GOOD_REP]  # first rep has NO pose data, second has real data

    scores, trait_names = scorer.score_traits_batch("unknown", reps_data, TRAIT_CONFIGS)

    assert scores[0][0] == 0.0  # the genuinely empty rep should still score 0 - that's correct
    assert scores[1][0] > 50.0, "a rep with real, good feature data must not be zeroed by an unrelated empty rep"


def test_empty_rep_anywhere_in_the_batch_does_not_affect_others():
    scorer = VectorizedTraitScorer()
    reps_data = [GOOD_REP, {}, GOOD_REP, {}, GOOD_REP]

    scores, trait_names = scorer.score_traits_batch("unknown", reps_data, TRAIT_CONFIGS)

    for i in (0, 2, 4):
        assert scores[i][0] > 50.0, f"rep {i} has real data and must not be affected by empty reps elsewhere in the batch"
    for i in (1, 3):
        assert scores[i][0] == 0.0


def test_all_empty_reps_scores_all_zero_not_a_crash():
    scorer = VectorizedTraitScorer()
    reps_data = [{}, {}, {}]

    scores, trait_names = scorer.score_traits_batch("unknown", reps_data, TRAIT_CONFIGS)

    assert scores.shape == (3, 1)
    assert (scores == 0.0).all()


def test_get_feature_names_is_the_union_across_all_reps():
    scorer = VectorizedTraitScorer()
    reps_data = [{"a": 1, "b": 2}, {"b": 3, "c": 4}]

    names = scorer._get_feature_names(reps_data)

    assert set(names) == {"a", "b", "c"}
