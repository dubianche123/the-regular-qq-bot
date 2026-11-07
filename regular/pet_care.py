"""Temporary local good mood; these rules never change QQ relationship scores."""
from collections import deque

CARE_GAIN = {"pat": 0.18, "feed": 0.26}
CARE_COOLDOWN = {"pat": 45.0, "feed": 90.0}
CARE_MAX = 0.6
CARE_DECAY = CARE_MAX / 300.0


def decay(warmth, previous, now):
    return max(0.0, warmth - max(0.0, now - previous) * CARE_DECAY)


def change(warmth, previous, kind, now):
    room = CARE_MAX - warmth
    if room <= 1e-6:
        return warmth, previous, "full"
    if now - previous < CARE_COOLDOWN[kind]:
        return warmth, previous, "cooldown"
    gain = min(CARE_GAIN[kind], room)
    return warmth + gain, now, "gain" if gain >= CARE_GAIN[kind] - 1e-6 else "full"


class CareState:
    """One panel owns this short-lived state for all its local clients."""
    def __init__(self, now):
        self.warmth, self.updated_at = 0.0, now
        self.last = {kind: float("-inf") for kind in CARE_GAIN}
        self.offered = dict(self.last)
        self.feeds = deque(maxlen=5)

    def snapshot(self, now):
        self.warmth = decay(self.warmth, self.updated_at, now)
        self.updated_at = now
        return {"warmth": round(self.warmth, 4), "updated_at": now,
                "cooldown": {kind: round(max(0.0, seconds - (now - self.last[kind])), 1)
                             for kind, seconds in CARE_COOLDOWN.items()}}

    def apply(self, kind, now):
        if kind not in CARE_GAIN:
            raise ValueError("unknown care action")
        self.snapshot(now)
        self.offered[kind] = now
        if kind == "feed":
            self.feeds.append(now)
            if sum(now - stamp < 600 for stamp in self.feeds) > 4:
                return "fed_full", "吃饱啦，留着下回再吃。"
        self.warmth, self.last[kind], state = change(self.warmth, self.last[kind], kind, now)
        if state == "gain":
            return state, "吃饱了，心情也好了。" if kind == "feed" else "被你摸得开心了。"
        if state == "full":
            return state, "心情已经很好啦，先陪我待一会儿。"
        return state, "刚吃过一块啦，留着下回再吃。" if kind == "feed" else "刚摸过啦，等会儿再来～"

    def facts(self, now):
        current = self.snapshot(now)
        return {"warmth": current["warmth"],
                "fed_recently": now - self.offered["feed"] < CARE_COOLDOWN["feed"],
                "patted_recently": now - self.offered["pat"] < CARE_COOLDOWN["pat"]}
