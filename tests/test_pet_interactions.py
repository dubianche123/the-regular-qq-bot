"""Mood-aware input reactions preserve posture and confirmed care semantics."""
import random

import pytest

from regular.pet_interactions import REACTIONS, SOFTEN_LINES, upset_reaction


def actions(*names):
    return {name: {"posture": "sit" if name in ("mood_tail_hold", "mood_tail_guard") else "stand",
                   "starts_from": "sit" if name in ("mood_tail_hold", "mood_tail_guard") else "stand"}
            for name in names}


ALL = actions("mood_pout", "mood_pout_hold", "mood_look_away", "mood_tail_hold",
              "mood_huff", "mood_hand_hide", "mood_pat_soften", "mood_tail_guard")


def test_normal_mood_keeps_the_normal_interaction_path():
    for trigger in REACTIONS:
        for mood in (0, .2, .9):
            assert upset_reaction(random.Random(1), ALL, trigger, mood=mood, posture="stand") is None


def test_pending_head_pat_never_claims_she_has_softened():
    for seed in range(20):
        clip, line = upset_reaction(random.Random(seed), ALL, "pat", mood=-.4, posture="stand")
        assert clip != "mood_pat_soften"
        assert line not in SOFTEN_LINES
    assert upset_reaction(random.Random(1), actions("mood_pat_soften"), "pat",
                          mood=-.4, posture="stand") is None


def test_confirmed_comfort_can_soften_even_if_it_just_lifted_mood_above_zero():
    for mood in (-.4, .1):
        clip, line = upset_reaction(random.Random(1), ALL, "pat", mood=mood,
                                    posture="stand", comforted=True)
        assert clip == "mood_pat_soften"
        assert line in SOFTEN_LINES
    assert upset_reaction(random.Random(1), actions("mood_pout"), "pat", mood=.1,
                          posture="stand", comforted=True) is None


def test_interaction_never_stands_up_or_changes_a_seated_actor():
    for trigger in REACTIONS:
        for seed in range(20):
            clip, _ = upset_reaction(random.Random(seed), ALL, trigger, mood=-.4, posture="sit")
            assert ALL[clip]["starts_from"] == ALL[clip]["posture"] == "sit"
            assert clip in ("mood_tail_hold", "mood_tail_guard")
        assert upset_reaction(random.Random(1), ALL, trigger, mood=-.4, posture="lie") is None
    incompatible = {"mood_tail_hold": {"starts_from": "stand", "posture": "sit"}}
    assert upset_reaction(random.Random(1), incompatible, "body", mood=-.4, posture="sit") is None


def test_missing_new_sheets_keep_the_existing_small_upset_reactions():
    old = actions("mood_pout_hold", "mood_look_away", "mood_tail_hold")
    for trigger in REACTIONS:
        for posture in ("stand", "sit"):
            clip, line = upset_reaction(random.Random(1), old, trigger, mood=-.4, posture=posture)
            assert clip in old and line
    assert upset_reaction(random.Random(1), {}, "body", mood=-.4, posture="stand") is None


def test_reactions_include_new_variants_and_avoid_recently_played_clips():
    seen = {upset_reaction(random.Random(seed), ALL, "body", mood=-.4, posture="stand")[0]
            for seed in range(80)}
    assert {"mood_hand_hide", "mood_huff"} <= seen
    clip, _ = upset_reaction(random.Random(1), ALL, "body", mood=-.4, posture="stand",
                             recent_clips=("mood_hand_hide", "mood_huff", "mood_look_away"))
    assert clip == "mood_pout_hold"
    clip, _ = upset_reaction(random.Random(1), ALL, "body", mood=-.4, posture="stand",
                             recent_clips=seen)
    assert clip in seen


def test_unknown_trigger_does_not_invent_an_interaction():
    with pytest.raises(ValueError, match="unknown interaction"):
        upset_reaction(random.Random(1), ALL, "arbitrary text", mood=-.4, posture="stand")
