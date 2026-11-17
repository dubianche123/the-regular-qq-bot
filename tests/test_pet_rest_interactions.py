"""Local interactions preserve resting actors and reflect the actual mood."""
import random

from regular.pet_brain import PetBrain, Step


def resting(clip):
    brain = PetBrain(rng=random.Random(7), now=0)
    brain.set_ground(0, 1440, 860)
    brain.place(700)
    brain._set_clip(clip, 0, restart=True)
    brain._queue(Step(clip, 120), now=0, interrupt=True)
    return brain


def point(brain, fraction):
    left, top, right, bottom = brain.body_rect()
    return (left + right) / 2, top + (bottom - top) * fraction


def test_head_pat_click_and_hover_keep_furniture_and_share_care():
    for clip in ("scene_chair_rest", "scene_sofa_lounge"):
        brain = resting(clip)
        brain.external_care = True
        brain.poke(1, *point(brain, .2))
        assert list(brain.care_requests) == ["pat"]
        assert brain.clip == clip
        brain.sync_care({"warmth": .18, "distress": 0, "updated_at": 1}, 1)
        brain.receive_care(1, {"kind": "care", "id": "pat-1", "ts": 1,
                               "action": "pat", "result": "gain", "text": "被你摸得开心了。"})
        assert brain.clip == clip
        assert brain.warmth == .18
        brain._petted(2)
        assert brain.clip == clip
        assert list(brain.care_requests) == ["pat", "pat"]


def test_chat_and_reply_stay_on_the_same_furniture():
    for clip in ("scene_chair_rest", "scene_sofa_lounge"):
        brain = resting(clip)
        brain.chat_sent(1)
        assert brain.clip == clip
        assert brain.step.hold == "chat"
        brain.tick(2)
        brain.chat_reply(3, "在这儿躺着听你说。")
        assert brain.clip == clip
        for i in range(1, 180):
            brain.tick(3 + i / 30)
            assert brain.clip == clip
        brain.clear_state(10, source="local")
        assert brain.clip == clip


def test_low_mood_body_and_seated_tail_touches_are_not_happy_greetings():
    for clip, expected in (("mood_pout_hold", {"mood_huff", "mood_hand_hide", "mood_pout_hold", "mood_look_away"}),
                           ("mood_tail_hold", {"mood_tail_guard", "mood_tail_hold"})):
        brain = resting(clip)
        brain.distress = .4
        brain.poke(1, *point(brain, .6))
        assert brain.clip in expected
        assert brain.step.hold == "mood"
        assert not any(step.clip in ("wave", "shy", "cheer", "scene_fire_controlled") for step in brain.plan)


def test_pending_pat_stays_upset_then_confirmed_comfort_softens():
    brain = resting("mood_pout_hold")
    brain.distress = .2
    brain.external_care = True
    brain.poke(1, *point(brain, .2))
    assert brain.clip != "mood_pat_soften"
    assert brain.warmth == 0
    brain.sync_care({"warmth": .18, "distress": .02, "updated_at": 1}, 1)
    brain.receive_care(1, {"kind": "care", "id": "pat-1", "ts": 1,
                           "action": "pat", "result": "gain", "text": "被你摸得开心了。"})
    assert brain.clip == "mood_pat_soften"


def test_touching_an_upset_furniture_state_does_not_remove_the_furniture():
    brain = resting("scene_sofa_lounge")
    brain.distress = .4
    brain.poke(1, *point(brain, .6))
    assert brain.clip == "scene_sofa_lounge"
    assert not brain.plan
    assert "先不闹" in brain.bubble.text


def test_chat_during_entry_finishes_sitting_before_holding_the_state():
    brain = resting("idle")
    brain._queue(Step("scene_sofa_entry"), Step("scene_sofa_settle"),
                 Step("scene_sofa_lounge", 90), now=0, interrupt=True)
    brain.tick(.5)
    started = brain.clip_started
    brain.chat_sent(.6)
    assert brain.clip == "scene_sofa_entry"
    assert brain.clip_started == started
    assert [step.clip for step in brain.plan] == ["scene_sofa_settle", "scene_sofa_lounge"]
    assert brain.plan[-1].hold == "chat"


def test_chat_during_exit_does_not_bring_the_chair_back():
    brain = resting("scene_chair_rest")
    brain._queue(Step("walk", target_x=1000), now=0, interrupt=True)
    assert brain.clip == "scene_chair_exit"
    started = brain.clip_started
    brain.tick(.5)
    brain.chat_sent(.6)
    assert brain.clip == "scene_chair_exit"
    assert brain.clip_started == started
    assert not any(step.clip.startswith("scene_chair_") for step in brain.plan)
    assert brain.plan[-1].hold == "chat"
