import random

from regular.pet_repertoire import ACTIONS
from regular.pet_routines import STORIES, choose_routine


def test_stories_use_all_named_beats_and_nonloops_once():
    rng = random.Random(22)
    seen = {}
    for _ in range(500):
        family, beats = choose_routine(rng, ACTIONS, mode="story")
        if family in STORIES:
            seen[family] = beats
    assert set(seen) == set(STORIES)
    for family, beats in seen.items():
        assert [name for name, _ in beats] == list(STORIES[family])
        assert all(duration is None for name, duration in beats if not ACTIONS[name]["loop"])


def test_missing_story_and_recent_family():
    actions = {name: ACTIONS[name] for name in ("life_tea_pour", "life_tea_sip")}
    assert choose_routine(random.Random(1), actions, mode="story") is None
    actions = {name: ACTIONS[name] for name in ("dragon_music_flute", "dragon_toy_handheld")}
    family, beats = choose_routine(random.Random(1), actions, ("ambient:wind",))
    assert family == "ambient:game"
    assert beats[0][0] == "dragon_toy_handheld"
    assert 45 <= beats[0][1] <= 90


def test_quiet_filters_air_and_nonloops():
    actions = {name: ACTIONS[name] for name in ("dragon_fire_toast", "motion_fly_hover", "life_laptop_type")}
    family, beats = choose_routine(random.Random(1), actions, quiet=True)
    assert family == "ambient:work"
    assert beats[0][0] == "life_laptop_type"
    assert choose_routine(random.Random(1), {}, quiet=True) is None


def test_standalone_one_shot_remains_reachable():
    actions = {"dragon_fire_toast": ACTIONS["dragon_fire_toast"]}
    assert choose_routine(random.Random(1), actions, mode="story") == (
        "once:cooking", [("dragon_fire_toast", None)])
