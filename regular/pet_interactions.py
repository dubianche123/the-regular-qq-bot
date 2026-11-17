"""Small mood-aware reactions; the caller owns care, state and interruption."""


REACTIONS = {
    "pat": (
        ("mood_pout_hold", "mood_look_away", "mood_pout", "mood_tail_hold"),
        ("哼……轻一点。", "还没消气呢……", "先摸轻一点嘛。"),
    ),
    "body": (
        ("mood_hand_hide", "mood_huff", "mood_tail_guard", "mood_pout_hold",
         "mood_look_away", "mood_tail_hold"),
        ("先不闹我嘛。", "又戳……我还在闹别扭呢。", "哼，让我缓一会儿。"),
    ),
    "feet": (
        ("mood_huff", "mood_tail_guard", "mood_look_away", "mood_tail_hold"),
        ("嗯……脚边也别闹啦。", "哼，我看见你啦。"),
    ),
    "hover": (
        ("mood_look_away", "mood_tail_guard", "mood_pout_hold", "mood_tail_hold"),
        ("偷偷看你一眼……", "我还在呢，先缓一会儿。"),
    ),
}
SOFTEN_LINES = ("……好一点了。", "就再摸一下。", "嗯……不用那么担心。")


def upset_reaction(rng, actions, trigger, *, mood, posture, comforted=False, recent_clips=()):
    """Return an adopted (clip, line) in the same physical posture, or None.

    ``comforted`` means code has confirmed care and an actual mood improvement;
    a click or a pending request alone must not set it. It permits the small
    softening reaction even when that improvement has just lifted mood past zero.
    Furniture-specific states keep their own actor in the caller rather than
    replacing a chair or sofa with one of these free-standing reaction sheets.
    """
    if trigger not in REACTIONS:
        raise ValueError("unknown interaction trigger")

    def matching(names):
        return [name for name in names if name in actions
                and actions[name].get("posture") == posture
                and actions[name].get("starts_from", posture) == posture]

    if trigger == "pat" and comforted:
        softened = matching(("mood_pat_soften",))
        if softened:
            return softened[0], rng.choice(SOFTEN_LINES)
    if mood >= 0:
        return None
    names, lines = REACTIONS[trigger]
    candidates = matching(names)
    if not candidates:
        return None
    fresh = [name for name in candidates if name not in recent_clips]
    return rng.choice(fresh or candidates), rng.choice(lines)
