"""Interruptible, stationary everyday routines for the desktop companion."""

from regular.pet_repertoire import curated_actions

# Each story requires every named beat, preserving cause and effect.
# Compatibility for old asset folders; complete character episodes take priority.
STORIES = {
    "tea": ("dragon_fire_kettle", "life_tea_pour", "life_tea_cool", "life_tea_sip"),
    "treasure": ("dragon_treasure_find", "dragon_treasure_polish", "dragon_treasure_count", "dragon_treasure_hide"),
    "comic": ("life_comic_open", "life_comic_turn", "life_comic_amused"),
    "sketch": ("life_pencil_sharpen", "dragon_craft_sketch", "life_book_doodle"),
    "message": ("life_phone_notice", "life_phone_read", "life_phone_reply"),
    "stretch": ("life_clock_check", "life_wrist_stretch", "life_shoulder_roll", "life_water"),
}

AMBIENT_ROLES = frozenset({"play", "toy", "study", "curious", "magic", "fire", "craft",
                           "garden", "treasure", "music", "eat", "snack", "tea", "rest", "care"})
# Quiet accompaniment excludes animated play, flames, magic and performance.
QUIET_ROLES = frozenset({"study", "craft", "garden", "eat", "snack", "tea", "rest", "care"})
QUIET_STORIES = frozenset({"tea", "comic", "sketch", "stretch"})

# Each branch includes its result and cleanup. Tuple durations are long stays.
EPISODES = {
    "viewer": (
        (("scene_glass_hello", None),),
        (("scene_fog_wipe", None),),
        (("scene_diary_hide", None),),
    ),
    "sweet": (
        (("scene_sweet_share", None),),
        (("scene_sweet_hide", None),),
        (("scene_sweet_sneak", None), ("scene_sweet_caught", None)),
        (("scene_sweet_sneak", None), ("scene_sweet_share_more", None)),
    ),
    "fire": (
        (("scene_fire_controlled", None),),
        (("scene_fire_overdo", None), ("scene_fire_startled", None), ("scene_fire_recover", None)),
    ),
    "wonder": (
        (("dragon_magic_flower", None),),
        (("dragon_magic_snow", None),),
    ),
    "chair": ((("scene_chair_entry", None), ("scene_chair_rest", (35, 65)),
               ("scene_chair_exit", None)),),
    "sofa": ((("scene_sofa_entry", None), ("scene_sofa_settle", None),
              ("scene_sofa_lounge", (45, 75)), ("scene_sofa_exit", None)),),
    "sleepy_read": (
        (("life_comic_turn", 8.0), ("scene_sleepy_book", None), ("life_bookmark_place", 4.0)),
    ),
}
QUIET_EPISODES = frozenset({"viewer", "sweet", "chair", "sofa", "sleepy_read"})


def _choose_ending(rng, options, recent_clips=(), *, empty_cue=None):
    """Avoid repeating the distinguishing beat, even with a shared final pose.

    History contains played clips, so an interrupted, unplayed result does not
    count. An empty ending is an intentional quiet continuation of the activity.
    """
    recent = tuple(recent_clips)
    counts = {}
    for _, beats in options:
        for name in {name for name, _ in beats}:
            counts[name] = counts.get(name, 0) + 1
    common = max(counts.values(), default=0)
    choices = []
    for weight, beats in options:
        cue = next((name for name, _ in beats if counts[name] < common),
                   beats[-1][0] if beats else empty_cue)
        choices.append((weight, beats, cue))
    fresh = [choice for choice in choices if choice[2] not in recent]
    if fresh:
        choices = fresh
    else:
        # A hot-tea scene ends in the same relaxed sip as a successful one.
        # Prefer the longer complete match at the same end of the played history.
        has_quiet_ending = any(not beats for _, beats, _ in choices)
        history = recent if has_quiet_ending else tuple(name for name in recent if name != empty_cue)
        played = []
        for _, beats, cue in choices:
            signature = tuple(name for name, _ in beats if name != empty_cue) or (empty_cue,)
            for end in range(len(signature), len(history) + 1):
                if history[end - len(signature):end] == signature:
                    played.append((end, len(signature), cue))
        endings = {cue for _, _, cue in choices}
        latest = (max(played, key=lambda item: item[:2])[2] if played else
                  next((name for name in reversed(recent) if name in endings), None))
        different = [choice for choice in choices if choice[2] != latest]
        choices = different or choices
    pick = rng.random() * sum(weight for weight, _, _ in choices)
    for weight, beats, _ in choices:
        pick -= weight
        if pick <= 0:
            return beats
    return choices[-1][1]


def _one_pass(actions, name):
    spec = actions.get(name, {})
    seconds = len(spec.get("sequence", (0, 1, 2, 3))) / spec["fps"] if spec.get("loop") else None
    return name, seconds


def activity_sequence(rng, actions, clip, duration, *, mood=0.0, energy=1.0, quiet=False,
                      sleepy=False, recent_clips=()):
    """Keep an activity and its locally chosen result in the same interruptible plan.

    These are her desktop games and doodles, not results of QQ/API work. Choose
    once when planning; interruption discards the unfinished ending naturally.
    Missing adopted art leaves the original activity alone.
    """
    spec = actions.get(clip, {})
    family = {"gamepad": "game", "paint": "floating_canvas", "tea": "tea_time"}.get(
        clip, spec.get("family"))
    main = [(clip, duration)]
    options = []
    if family == "game" and not quiet:
        # Tired/frustrated play is more likely to go wrong; she still likes a rematch.
        win = max(0.2, min(0.8, 0.4 + energy * 0.3 + mood * 0.15))
        retry = 0.35 + energy * 0.4
        rematch = [("scene_game_loss", None), ("scene_game_rematch", None), (clip, rng.uniform(6, 10))]
        options = [(win, [("scene_game_win", None)]),
                   ((1 - win) * retry * win, rematch + [("scene_game_win", None)]),
                   ((1 - win) * retry * (1 - win), rematch + [("scene_game_loss", None)]),
                   ((1 - win) * (1 - retry), [("scene_game_loss", None)])]
    elif family == "floating_canvas":
        options = [(0.6, [("scene_paint_proud", None)]),
                   (0.4, [("scene_paint_rework", None), (clip, rng.uniform(6, 10)),
                          ("scene_paint_proud", None)])]
        if not quiet:
            options.append((0.3, [("scene_paint_cheek", None)]))
    elif family == "tea_time":
        options = [(1.0, [("scene_tea_relax", None)])]
        if not quiet and clip != "life_tea_cool":
            # Her own dragon fire heats water quickly, and eagerness beats caution.
            options.append((0.4 + max(0.0, mood) * 0.3 + energy * 0.2,
                            [("scene_tea_hot", None), ("life_tea_cool", 4.0),
                             ("life_tea_sip", 3.0), ("scene_tea_relax", None)]))
    elif clip in ("life_comic_open", "life_comic_turn"):
        # Notes and maps keep their own objects. Being tired can simply mean
        # putting a bookmark in, without pretending to be wide awake again.
        close = 0.8 if sleepy or energy < 0.5 else 0.25
        options = [(1 - close, [_one_pass(actions, "life_comic_amused")]),
                   (close, [_one_pass(actions, "life_bookmark_place")])]
    elif clip == "dragon_treasure_polish":
        # The same gem is quietly admired or offered, without a coin/chest swap.
        options = [(0.6, []), (0.4, [("dragon_treasure_share", None)])]
    playable = [(weight, beats) for weight, beats in options
                if all(name == clip or name in actions for name, _ in beats)]
    if not playable:
        return main
    return main + _choose_ending(rng, playable, recent_clips, empty_cue=clip)


def choose_routine(rng, actions, recent_families=(), *, mode="rest", quiet=False,
                   mood=0.0, energy=1.0, sleepy=False, recent_clips=(), allow_furniture=True):
    """Return (family, [(clip, duration), ...]), or None when no clip fits.

    mode is 'rest' (a long stationary activity) or 'story' (ordered beats).
    Missing story beats disqualify that story. Non-loop clips use duration None
    for one pass; loops in stories run one cycle; long-stay loops run 45–90s.
    Recent families are avoided when possible. The caller owns history, posture
    transitions, movement restrictions and interruption.
    """
    if mode not in ("rest", "story"):
        raise ValueError("mode must be 'rest' or 'story'")
    if mode == "story":
        groups = {}
        for family, branches in EPISODES.items():
            if family in ("chair", "sofa") and not allow_furniture:
                continue
            if quiet and family not in QUIET_EPISODES:
                continue
            if family == "sleepy_read" and not sleepy:
                continue
            adopted = [beats for beats in branches if all(name in actions for name, _ in beats)]
            if adopted:
                groups["scene:" + family] = adopted
        # The story slot is already rate-limited by the brain. Do not dilute it
        # with procedural prop changes when complete episodes are available.
        if not groups:
            groups = {family: names for family, names in STORIES.items()
                      if (not quiet or family in QUIET_STORIES) and all(name in actions for name in names)}
            story_clips = {name for names in STORIES.values() for name in names}
            for name, spec in curated_actions(actions).items():
                roles = QUIET_ROLES if quiet else AMBIENT_ROLES
                if (name not in story_clips and spec.get("role") in roles
                        and spec.get("posture") != "air" and not spec.get("loop")):
                    groups.setdefault("once:" + spec["family"], []).append(name)
    else:
        groups = {}
        for name, spec in curated_actions(actions).items():
            if spec.get("role") not in (QUIET_ROLES if quiet else AMBIENT_ROLES) or spec.get("posture") == "air":
                continue
            # A one-shot cannot provide sustained accompaniment.
            if not spec.get("loop"):
                continue
            groups.setdefault("ambient:" + spec["family"], []).append(name)
        # Pulling out a chair or the sofa is an occasion, about once in half an hour, not every other rest.
        for family in ("chair", "sofa") if allow_furniture and rng.random() < 0.2 else ():
            adopted = [beats for beats in EPISODES[family] if all(name in actions for name, _ in beats)]
            if adopted:
                groups["home:" + family] = adopted
    if not groups:
        return None
    recent = set(recent_families)
    for family in recent_families:
        if family.startswith(("scene:", "home:")):
            key = family.split(":", 1)[1]
            recent.update(("scene:" + key, "home:" + key))
    fresh = [family for family in groups if family not in recent]
    candidates = fresh or list(groups)
    scenes = [name for name in candidates if name.startswith(("scene:", "home:"))]
    # Whole scenes are the occasional treat between quieter activities, not every other pick.
    family = rng.choice(scenes if scenes and rng.random() < 0.35 else candidates)
    if family.startswith(("scene:", "home:")):
        branches = groups[family]
        if family == "scene:fire" and len(branches) == 2:
            failure = 0.2 + max(0.0, mood) * 0.3 + max(0.0, 0.4 - energy) * 0.25
            options = [(1 - failure, branches[0]), (failure, branches[1])]
        else:
            options = [(1.0, branch) for branch in branches]
        branch = _choose_ending(rng, options, recent_clips)
        return family, [(name, rng.uniform(*duration) if isinstance(duration, tuple) else duration)
                        for name, duration in branch]
    if mode == "rest":
        return family, [(rng.choice(groups[family]), rng.uniform(45, 90))]
    beats = []
    names = (rng.choice(groups[family]),) if family.startswith("once:") else groups[family]
    for name in names:
        beats.append(_one_pass(actions, name))
    return family, beats
