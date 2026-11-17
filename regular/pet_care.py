"""Temporary local comfort and distress; never change QQ relationship scores."""
from collections import deque

CARE_GAIN = {"pat": 0.18, "feed": 0.26}
CARE_COOLDOWN = {"pat": 45.0, "feed": 90.0}
CARE_MAX = 0.6
CARE_DECAY = CARE_MAX / 300.0
DISTRESS_GAIN = 0.2
DISTRESS_MAX = 0.6
DISTRESS_DECAY = DISTRESS_MAX / 300.0
DROP_COOLDOWN = 3.0


def decay(warmth, previous, now):
    return max(0.0, warmth - max(0.0, now - previous) * CARE_DECAY)


def decay_distress(distress, previous, now):
    return max(0.0, distress - max(0.0, now - previous) * DISTRESS_DECAY)


def change(warmth, previous, kind, now):
    room = CARE_MAX - warmth
    if room <= 1e-6:
        return warmth, previous, "full"
    if now - previous < CARE_COOLDOWN[kind]:
        return warmth, previous, "cooldown"
    gain = min(CARE_GAIN[kind], room)
    return warmth + gain, now, "gain" if gain >= CARE_GAIN[kind] - 1e-6 else "full"


def change_distress(distress, previous, kind, now):
    """A hard drop unsettles her; accepted care also helps her settle down."""
    cooldown = DROP_COOLDOWN if kind == "drop" else CARE_COOLDOWN[kind]
    amount = min(DISTRESS_GAIN, DISTRESS_MAX - distress) if kind == "drop" else min(CARE_GAIN[kind], distress)
    if amount <= 1e-6:
        return distress, previous, "full" if kind == "drop" else "unchanged"
    if now - previous < cooldown:
        return distress, previous, "cooldown"
    if kind == "drop":
        return distress + amount, now, "loss"
    return distress - amount, now, "relief"


class CareState:
    """One panel owns this short-lived state for all its local clients."""
    def __init__(self, now):
        self.warmth, self.distress, self.updated_at = 0.0, 0.0, now
        self.last = {kind: float("-inf") for kind in (*CARE_GAIN, "drop")}
        self.offered = {kind: float("-inf") for kind in CARE_GAIN}
        self.feeds = deque(maxlen=5)

    def snapshot(self, now):
        self.warmth = decay(self.warmth, self.updated_at, now)
        self.distress = decay_distress(self.distress, self.updated_at, now)
        self.updated_at = now
        return {"warmth": round(self.warmth, 4), "distress": round(self.distress, 4), "updated_at": now,
                "cooldown": {kind: round(max(0.0, seconds - (now - self.last[kind])), 1)
                             for kind, seconds in {**CARE_COOLDOWN, "drop": DROP_COOLDOWN}.items()}}

    def apply(self, kind, now):
        if kind not in (*CARE_GAIN, "drop"):
            raise ValueError("unknown care action")
        self.snapshot(now)
        if kind == "drop":
            self.distress, self.last[kind], state = change_distress(self.distress, self.last[kind], kind, now)
            return state, "吓到了……让我缓一会儿。"
        self.offered[kind] = now
        if kind == "feed":
            self.feeds.append(now)
            if sum(now - stamp < 600 for stamp in self.feeds) > 4:
                return "fed_full", "吃饱啦，留着下回再吃。"
        previous = self.last[kind]
        self.warmth, warm_at, state = change(self.warmth, previous, kind, now)
        self.distress, calm_at, calm = change_distress(self.distress, previous, kind, now)
        self.last[kind] = max(warm_at, calm_at)
        if calm == "relief":
            state = "gain"
        if state == "gain":
            return state, "吃饱了，心情也好了。" if kind == "feed" else "被你摸得开心了。"
        if state == "full":
            return state, "心情已经很好啦，先陪我待一会儿。"
        return state, "刚吃过一块啦，留着下回再吃。" if kind == "feed" else "刚摸过啦，等会儿再来～"

    def facts(self, now):
        current = self.snapshot(now)
        return {"warmth": current["warmth"], "distress": current["distress"],
                "dropped_recently": now - self.last["drop"] < 300,
                "fed_recently": now - self.offered["feed"] < CARE_COOLDOWN["feed"],
                "patted_recently": now - self.offered["pat"] < CARE_COOLDOWN["pat"]}
