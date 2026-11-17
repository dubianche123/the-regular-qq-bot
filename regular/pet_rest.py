"""Stable resting poses. Body posture stays independent from the place she rests."""

FURNITURE = {
    "chair": {"name": "chair", "enter": ("scene_chair_entry",),
              "stay": "scene_chair_rest", "exit": "scene_chair_exit"},
    "sofa": {"name": "sofa", "enter": ("scene_sofa_entry", "scene_sofa_settle"),
             "stay": "scene_sofa_lounge", "exit": "scene_sofa_exit"},
}


def furniture_episode(name):
    pose = FURNITURE[name]
    return (tuple((clip, None) for clip in pose["enter"])
            + ((pose["stay"], (75, 180)), (pose["exit"], None)))


def current_rest(clip, actions):
    """Return an adopted quiet pose; incomplete furniture never becomes a state."""
    for pose in FURNITURE.values():
        members = (*pose["enter"], pose["stay"], pose["exit"])
        if clip in members and all(name in actions for name in members):
            return pose
    spec = actions.get(clip, {})
    if (spec.get("restful") and spec.get("loop")
            and spec.get("role") not in ("mood", "sleep")):
        return {"name": clip, "enter": (), "stay": clip, "exit": None}
    return None


def entering_rest(beats, actions):
    """A furniture choice enters its state; exiting waits for a later decision."""
    if not beats:
        return None
    pose = current_rest(beats[0][0], actions)
    return pose if pose and pose["exit"] else None
