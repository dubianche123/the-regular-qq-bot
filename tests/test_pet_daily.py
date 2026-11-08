"""New daily art stays opt-in and preserves complete, state-appropriate scenes."""
import json
import random

from regular.pet_actions_daily import ACTIONS as DAILY, PLAYBACK_ALIASES
from regular.pet_actions_scenes import ACTIONS as SCENES
from regular.pet_repertoire import available, clips, curated_actions
from regular.pet_routines import choose_routine


def test_daily_sheets_define_readable_fixed_scale_performances():
    assert len(DAILY) == 15
    for name, spec in DAILY.items():
        assert name.startswith("daily_")
        assert all(f"Frame {frame}:" in spec["prompt"] for frame in range(1, 5))
        assert "1024x1024" in spec["prompt_header"] and "512x512" in spec["prompt_header"]
        assert "30px" in spec["prompt_header"]
        assert spec["starts_from"] in ("sit", "stand")
        assert spec["posture"] in ("sit", "stand")
        assert spec["scale"] == 1.0
    assert {name for name, spec in DAILY.items() if spec["loop"]} == {"daily_quiet_tail_rest"}


def test_unadopted_sheets_and_aliases_are_not_playable(tmp_path, monkeypatch):
    import regular.pet_repertoire as repertoire
    folder = tmp_path / "assets" / "pet"
    folder.mkdir(parents=True)
    source = "daily_book_hug_sleep"
    manifest = {"actions": {source: {"file": source + ".webp", "frames": 4}},
                "geometry": {source: {"bounds": [-90, -250, 100, 0]}}}
    (folder / "sprites.json").write_text(json.dumps(manifest))
    monkeypatch.setattr(repertoire, "files", lambda package: tmp_path)
    actions = available()
    assert set(actions) == {source, "daily_book_sleep_hold"}
    hold = actions["daily_book_sleep_hold"]
    assert hold["geometry"] == actions[source]["geometry"]
    assert clips(actions)["daily_book_sleep_hold"][:2] == (source, [2, 3])
    assert "daily_book_sleep_hold" not in curated_actions(actions)
    (folder / "sprites.json").write_text(json.dumps({"actions": {}}))
    assert available() == {}


def test_cookie_and_sleep_hold_are_never_standalone_automatic_choices():
    assert "daily_cookie_receive" not in curated_actions(DAILY)
    only = {"daily_cookie_receive": DAILY["daily_cookie_receive"], **PLAYBACK_ALIASES}
    for mode in ("story", "rest"):
        assert choose_routine(random.Random(1), only, mode=mode, sleepy=True) is None


def test_new_scenes_are_reachable_without_turning_them_into_long_loops():
    actions = {**DAILY, **PLAYBACK_ALIASES, "scene_fire_controlled": SCENES["scene_fire_controlled"]}
    seen = set()
    for mood in (-0.4, 0.4):
        rng = random.Random(73)
        for _ in range(300):
            family, beats = choose_routine(rng, actions, mode="story", sleepy=True,
                                           energy=0.3, mood=mood, allow_furniture=False)
            assert family.startswith("scene:")
            seen.update(name for name, _ in beats)
            for name, duration in beats:
                if name != "daily_book_sleep_hold":
                    assert duration is None
                    assert not actions[name]["loop"]
    assert set(DAILY) - {"daily_cookie_receive", "daily_quiet_tail_rest"} <= seen


def test_book_keeps_its_own_closed_book_and_sleeps_without_repeating_the_entry():
    actions = {"daily_book_hug_sleep": DAILY["daily_book_hug_sleep"], **PLAYBACK_ALIASES}
    family, beats = choose_routine(random.Random(7), actions, mode="story", sleepy=True)
    assert family == "scene:sleepy_read"
    assert [name for name, _ in beats] == ["daily_book_hug_sleep", "daily_book_sleep_hold"]
    assert beats[0][1] is None
    assert 35 <= beats[1][1] <= 60
    assert clips(actions)[beats[1][0]][0] == beats[0][0]
    assert choose_routine(random.Random(7), actions, mode="story", sleepy=False) is None


def test_attention_preparation_requires_a_real_result_and_preserves_the_order():
    actions = {"daily_audience_prepare": DAILY["daily_audience_prepare"]}
    assert choose_routine(random.Random(1), actions, mode="story") is None
    actions["scene_fire_controlled"] = SCENES["scene_fire_controlled"]
    choices = [choose_routine(random.Random(seed), actions, mode="story") for seed in range(30)]
    prepared = [beats for _, beats in choices if beats[0][0] == "daily_audience_prepare"]
    assert prepared
    assert all([name for name, _ in beats] == ["daily_audience_prepare", "scene_fire_controlled"]
               for beats in prepared)


def test_emotional_scenes_follow_context_and_quiet_rest_stays_long():
    actions = {name: DAILY[name] for name in ("daily_joy_tail_catch", "daily_low_tail_comfort",
                                             "daily_game_set_down", "daily_quiet_tail_rest")}
    assert choose_routine(random.Random(1), actions, mode="story", mood=0) is None
    assert choose_routine(random.Random(1), actions, mode="story", mood=0.5)[0] == "scene:joy"
    assert choose_routine(random.Random(1), actions, mode="story", mood=-0.5)[0] == "scene:low"
    assert choose_routine(random.Random(1), actions, mode="story", energy=0.3)[0] == "scene:tired_game"
    assert choose_routine(random.Random(1), actions, mode="story", mood=0.5, quiet=True) is None
    family, beats = choose_routine(random.Random(1), actions, mode="rest", quiet=True)
    assert family == "ambient:quiet_company"
    assert beats[0][0] == "daily_quiet_tail_rest" and 45 <= beats[0][1] <= 90


def test_new_objects_start_in_complete_sheets_instead_of_changing_old_props():
    for name in ("daily_second_cup", "daily_old_keepsake", "daily_gem_release", "daily_painting_gift"):
        family, beats = choose_routine(random.Random(1), {name: DAILY[name]}, mode="story")
        assert family.startswith("scene:")
        assert beats == [(name, None)]
