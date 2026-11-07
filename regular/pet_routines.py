"""Interruptible, stationary everyday routines for the desktop companion."""

# Each story requires every named beat, preserving cause and effect.
STORIES = {
    "tea": ("dragon_fire_kettle", "life_tea_pour", "life_tea_cool", "life_tea_sip"),
    "treasure": ("dragon_treasure_find", "dragon_treasure_polish", "dragon_treasure_count", "dragon_treasure_hide"),
    "comic": ("life_comic_open", "life_comic_turn", "life_comic_amused"),
    "paper": ("dragon_craft_origami", "life_paper_crane"),
    "sketch": ("life_pencil_sharpen", "dragon_craft_sketch", "life_book_doodle"),
    "garden": ("dragon_garden_seed", "dragon_garden_water", "dragon_garden_sprout"),
    "message": ("life_phone_notice", "life_phone_read", "life_phone_reply"),
    "stretch": ("life_clock_check", "life_wrist_stretch", "life_shoulder_roll", "life_water"),
}

AMBIENT_ROLES = frozenset({"play", "toy", "study", "curious", "magic", "fire", "craft",
                           "garden", "treasure", "music", "eat", "snack", "tea", "rest", "care"})
# Quiet accompaniment excludes animated play, flames, magic and performance.
QUIET_ROLES = frozenset({"study", "craft", "garden", "eat", "snack", "tea", "rest", "care"})
QUIET_STORIES = frozenset({"tea", "comic", "paper", "sketch", "garden", "stretch"})


def choose_routine(rng, actions, recent_families=(), *, mode="rest", quiet=False):
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
        groups = {family: names for family, names in STORIES.items()
                  if (not quiet or family in QUIET_STORIES) and all(name in actions for name in names)}
        # Standalone one-shot vignettes remain reachable even when they do not
        # belong to a multi-beat story; group them by scene for recent dedupe.
        story_clips = {name for names in STORIES.values() for name in names}
        for name, spec in actions.items():
            roles = QUIET_ROLES if quiet else AMBIENT_ROLES
            if (name not in story_clips and spec.get("role") in roles
                    and spec.get("posture") != "air" and not spec.get("loop")):
                groups.setdefault("once:" + spec["family"], []).append(name)
    else:
        groups = {}
        for name, spec in actions.items():
            if spec.get("role") not in (QUIET_ROLES if quiet else AMBIENT_ROLES) or spec.get("posture") == "air":
                continue
            # A one-shot cannot provide sustained accompaniment.
            if not spec.get("loop"):
                continue
            groups.setdefault("ambient:" + spec["family"], []).append(name)
    if not groups:
        return None
    fresh = [family for family in groups if family not in recent_families]
    family = rng.choice(fresh or list(groups))
    if mode == "rest":
        return family, [(rng.choice(groups[family]), rng.uniform(45, 90))]
    beats = []
    names = (rng.choice(groups[family]),) if family.startswith("once:") else groups[family]
    for name in names:
        spec = actions[name]
        seconds = len(spec.get("sequence", (0, 1, 2, 3))) / spec["fps"] if spec["loop"] else None
        beats.append((name, seconds))
    return family, beats
