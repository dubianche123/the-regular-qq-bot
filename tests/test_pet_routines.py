import random

from regular.pet_repertoire import ACTIONS, curated_actions
from regular.pet_routines import EPISODES, STORIES, activity_sequence, choose_routine


def test_legacy_stories_are_only_a_fallback_for_missing_episodes():
    rng = random.Random(22)
    actions = {name: ACTIONS[name] for names in STORIES.values() for name in names}
    seen = {}
    for _ in range(500):
        family, beats = choose_routine(rng, actions, mode="story")
        if family in STORIES:
            seen[family] = beats
    assert set(seen) == set(STORIES)
    for family, beats in seen.items():
        assert [name for name, _ in beats] == list(STORIES[family])
        assert all(duration is None for name, duration in beats if not ACTIONS[name]["loop"])
    for _ in range(100):
        family, beats = choose_routine(rng, ACTIONS, mode="story")
        assert family.startswith("scene:")
        assert beats
    assert "paper" not in STORIES and "garden" not in STORIES


def test_missing_story_and_recent_family():
    actions = {name: ACTIONS[name] for name in ("life_tea_pour", "life_tea_sip")}
    assert choose_routine(random.Random(1), actions, mode="story") is None
    actions = {name: ACTIONS[name] for name in ("dragon_music_ukulele", "dragon_toy_handheld")}
    family, beats = choose_routine(random.Random(1), actions, ("ambient:strings",))
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
    # A future adopted one-shot can still work in a folder without full scenes.
    actions = {"new_toast": ACTIONS["dragon_fire_toast"]}
    assert choose_routine(random.Random(1), actions, mode="story") == (
        "once:cooking", [("new_toast", None)])


def test_activity_results_keep_the_game_and_have_win_loss_and_rematch_branches():
    branches = [activity_sequence(random.Random(seed), ACTIONS, "gamepad", 30)
                for seed in range(100)]
    assert all(beats[0] == ("gamepad", 30) for beats in branches)
    assert any(beats[1][0] == "scene_game_win" for beats in branches)
    assert any(len(beats) == 2 and beats[1][0] == "scene_game_loss" for beats in branches)
    assert any(any(name == "scene_game_rematch" for name, _ in beats) for beats in branches)
    rematches = [beats for beats in branches if any(name == "scene_game_rematch" for name, _ in beats)]
    assert {beats[-1][0] for beats in rematches} == {"scene_game_win", "scene_game_loss"}
    for beats in branches:
        assert all(name == "gamepad" or name in ACTIONS for name, _ in beats)


def test_missing_art_and_actual_qq_work_keep_the_original_activity():
    assert activity_sequence(random.Random(1), {}, "gamepad", 30) == [("gamepad", 30)]
    assert activity_sequence(random.Random(1), ACTIONS, "spell_paint", 30) == [("spell_paint", 30)]
    # A notebook sketch cannot suddenly turn into a floating canvas and palette.
    assert activity_sequence(random.Random(1), ACTIONS, "dragon_craft_sketch", 30) == [
        ("dragon_craft_sketch", 30)]
    assert activity_sequence(random.Random(1), ACTIONS, "life_note_read", 30) == [
        ("life_note_read", 30)]


def test_tea_hot_branch_cools_down_before_sipping_again_and_quiet_stays_calm():
    branches = [activity_sequence(random.Random(seed), ACTIONS, "tea", 12) for seed in range(60)]
    hot = [beats for beats in branches if any(name == "scene_tea_hot" for name, _ in beats)]
    assert hot
    assert all([name for name, _ in beats[1:]] == [
        "scene_tea_hot", "life_tea_cool", "life_tea_sip", "scene_tea_relax"] for beats in hot)
    for seed in range(10):
        assert activity_sequence(random.Random(seed), ACTIONS, "tea", 12, quiet=True) == [
            ("tea", 12), ("scene_tea_relax", None)]


def test_furniture_requires_complete_entry_stay_and_exit_and_holds_one_object():
    sofa = EPISODES["sofa"][0]
    actions = {name: ACTIONS[name] for name, _ in sofa}
    family, beats = choose_routine(random.Random(5), actions, mode="story")
    assert family == "scene:sofa"
    assert [name for name, _ in beats] == [name for name, _ in sofa]
    assert 45 <= beats[2][1] <= 75
    assert not ACTIONS[beats[0][0]]["loop"]
    assert ACTIONS[beats[2][0]]["loop"]
    assert not ACTIONS[beats[-1][0]]["loop"]
    del actions["scene_sofa_exit"]
    assert choose_routine(random.Random(5), actions, mode="story") is None


def test_tired_game_results_and_complete_scene_requests_use_current_state():
    def wins(energy):
        return sum(activity_sequence(random.Random(seed), ACTIONS, "gamepad", 30,
                                     energy=energy)[1][0] == "scene_game_win" for seed in range(200))
    assert wins(1.0) > wins(0.1)
    chair = {name: ACTIONS[name] for name, _ in EPISODES["chair"][0]}
    family, beats = choose_routine(random.Random(1), chair, mode="story")
    assert family == "scene:chair"
    assert [name for name, _ in beats] == [name for name, _ in EPISODES["chair"][0]]
    fire = {name: ACTIONS[name] for branch in EPISODES["fire"] for name, _ in branch}
    assert choose_routine(random.Random(1), fire, mode="story", quiet=True) is None


def test_a_long_home_rest_keeps_entry_and_cleanup_in_the_same_plan():
    actions = {name: ACTIONS[name] for name, _ in EPISODES["chair"][0]}
    picks = [choose_routine(random.Random(seed), actions, mode="rest", quiet=True) for seed in range(40)]
    homes = [pick for pick in picks if pick is not None]
    assert 2 <= len(homes) <= 20             # furniture is an occasion, not most rests
    family, beats = homes[0]
    assert family == "home:chair"
    assert [name for name, _ in beats] == ["scene_chair_entry", "scene_chair_rest", "scene_chair_exit"]
    assert 35 <= beats[1][1] <= 65
    actions.update({name: ACTIONS[name] for name, _ in EPISODES["sofa"][0]})
    picks = [choose_routine(random.Random(seed), actions, ("scene:chair",), mode="rest") for seed in range(40)]
    assert {pick[0] for pick in picks if pick is not None} == {"home:sofa"}


def test_furniture_limit_filters_both_home_and_scene_entries():
    furniture = {name: ACTIONS[name] for family in ("chair", "sofa")
                 for branch in EPISODES[family] for name, _ in branch}
    for mode in ("rest", "story"):
        for seed in range(10):
            assert choose_routine(random.Random(seed), furniture, mode=mode,
                                  allow_furniture=False) is None


def test_ambient_selection_uses_only_curated_loops():
    curated = curated_actions(ACTIONS)
    for seed in range(200):
        family, beats = choose_routine(random.Random(seed), ACTIONS, mode="rest")
        if family.startswith("ambient:"):
            assert beats[0][0] in curated
            assert ACTIONS[beats[0][0]]["loop"]
            assert 45 <= beats[0][1] <= 90


def test_comic_has_amusement_and_putting_it_away_without_changing_books():
    def choices(**kwargs):
        return [activity_sequence(random.Random(seed), ACTIONS, "life_comic_turn", 60, **kwargs)
                for seed in range(100)]
    awake, tired = choices(), choices(sleepy=True)
    assert {beats[-1][0] for beats in awake} == {"life_comic_amused", "life_bookmark_place"}
    assert all(beats[0] == ("life_comic_turn", 60) for beats in awake + tired)
    assert sum(beats[-1][0] == "life_bookmark_place" for beats in tired) > sum(
        beats[-1][0] == "life_bookmark_place" for beats in awake)
    assert all(beats[-1][1] == 4.0 for beats in awake)
    missing = {"life_comic_turn": ACTIONS["life_comic_turn"]}
    assert activity_sequence(random.Random(1), missing, "life_comic_turn", 60) == [("life_comic_turn", 60)]


def test_collection_can_stay_private_or_share_the_same_gem():
    choices = [activity_sequence(random.Random(seed), ACTIONS, "dragon_treasure_polish", 60)
               for seed in range(100)]
    assert any(len(beats) == 1 for beats in choices)
    assert any(beats[-1][0] == "dragon_treasure_share" for beats in choices)
    assert {name for beats in choices for name, _ in beats} == {
        "dragon_treasure_polish", "dragon_treasure_share"}


def test_recent_results_change_endings_without_forcing_a_new_story():
    for seed in range(20):
        game = activity_sequence(random.Random(seed), ACTIONS, "gamepad", 60,
                                 recent_clips=("scene_game_loss", "scene_game_win"))
        assert any(name == "scene_game_rematch" for name, _ in game)
        comic = activity_sequence(random.Random(seed), ACTIONS, "life_comic_turn", 60,
                                  recent_clips=("life_comic_amused",))
        assert comic[-1][0] == "life_bookmark_place"
    sweet = {name: ACTIONS[name] for branch in EPISODES["sweet"] for name, _ in branch}
    for seed in range(20):
        _, beats = choose_routine(random.Random(seed), sweet, mode="story",
                                  recent_clips=("scene_sweet_share", "scene_sweet_hide", "scene_sweet_caught"))
        assert [name for name, _ in beats] == ["scene_sweet_sneak", "scene_sweet_share_more"]


def test_shared_closing_pose_does_not_hide_the_previous_branch():
    for seed in range(20):
        hot = activity_sequence(random.Random(seed), ACTIONS, "tea", 30,
                                recent_clips=("scene_tea_relax",))
        assert hot[1][0] == "scene_tea_hot"
        relaxed = activity_sequence(random.Random(seed), ACTIONS, "tea", 30,
                                    recent_clips=tuple(name for name, _ in hot[1:]))
        assert relaxed == [("tea", 30), ("scene_tea_relax", None)]
        paint = activity_sequence(random.Random(seed), ACTIONS, "paint", 30,
                                  recent_clips=("scene_paint_proud", "scene_paint_cheek"))
        assert paint[1][0] == "scene_paint_rework"
        history = ("scene_paint_cheek",) + tuple(name for name, _ in paint[1:])
        changed = activity_sequence(random.Random(seed), ACTIONS, "paint", 30, recent_clips=history)
        assert not any(name == "scene_paint_rework" for name, _ in changed)


def test_quiet_collection_and_sharing_can_each_follow_the_other():
    for seed in range(10):
        private = activity_sequence(random.Random(seed), ACTIONS, "dragon_treasure_polish", 30,
                                    recent_clips=("dragon_treasure_polish", "dragon_treasure_share"))
        assert private == [("dragon_treasure_polish", 30)]
        shared = activity_sequence(random.Random(seed), ACTIONS, "dragon_treasure_polish", 30,
                                   recent_clips=("dragon_treasure_share", "dragon_treasure_polish"))
        assert shared[-1][0] == "dragon_treasure_share"


def test_sweet_repair_has_a_cause_and_discovery_has_no_failure_requirement():
    assert (("scene_sweet_sneak", None), ("scene_sweet_share_more", None)) in EPISODES["sweet"]
    assert not any(branch == (("scene_sweet_share_more", None),) for branch in EPISODES["sweet"])
    wonder = {name: ACTIONS[name] for branch in EPISODES["wonder"] for name, _ in branch}
    for seed in range(10):
        family, beats = choose_routine(random.Random(seed), wonder, mode="story")
        assert family == "scene:wonder"
        assert len(beats) == 1 and beats[0][1] is None
        assert not ACTIONS[beats[0][0]]["loop"]
