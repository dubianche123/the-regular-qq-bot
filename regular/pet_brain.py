"""桌宠的大脑：行为、物理和 agent 状态映射。纯计算，不依赖 Qt，也不发任何请求。

外壳（regular/desktop_pet.py）每帧调用 tick()，把鼠标、聊天和面板给的 presence 喂进来，
再读 pose() / particles / bubble 画出来。这里不保存、不改写机器人的任何状态：
心情、作息、群里发生了什么都只读，桌宠只是把小王「此刻在干嘛」演出来。

坐标单位是屏幕逻辑像素（点），(x, y) 是脚底锚点；scale 是人物大小倍率。
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
import math
import random

FIGURE = 160.0          # 100% 时人物站立高度（点）
WALK_SPEED = 46.0       # 点/秒，随 scale 缩放
GRAVITY = 2600.0
EDGE = 0.35             # 人物离屏幕左右边缘至少留这么多个人物高度

# clip: (strip, frame sequence, fps, loop, posture)
CLIPS = {
    "idle": ("idle", [0], 1, True, "stand"),
    "look_left": ("look", [0], 1, True, "stand"),
    "look_right": ("look", [1], 1, True, "stand"),
    "walk": ("walk", [0, 1, 2, 3, 4, 5], 9, True, "stand"),
    "wave": ("wave", [0, 1, 2, 3, 2, 3, 1], 6, False, "stand"),
    "hop": ("hop", [0, 1, 2, 3], 7, False, "stand"),
    "sit_down": ("sit", [0, 1], 5, False, "sit"),
    "sit_swing": ("sit", [1, 2], 1.6, True, "sit"),
    "stand_up": ("sit", [1, 0], 5, False, "stand"),
    "doze": ("sleep", [0, 1, 0, 1], 0.8, False, "sit"),
    "lie_down": ("sleep", [1, 2], 2, False, "lie"),
    "sleep": ("sleep", [2, 3], 0.45, True, "lie"),
    "wake": ("sleep", [2, 1, 0], 2.5, False, "sit"),
    "dangle": ("dangle", [0], 1, True, "air"),
    "fall": ("fall", [0, 1], 7, True, "air"),
    "land": ("fall", [2, 2, 3], 6, False, "stand"),
    "think": ("think", [0, 1], 0.9, True, "stand"),
    "phone": ("phone", [0, 1], 2.2, True, "stand"),
    "talk": ("talk", [0, 1, 2, 1, 3, 1], 3.5, True, "stand"),
    "stretch": ("stretch", [0, 1, 1, 2, 2, 3], 2.6, False, "stand"),
    "read": ("read", [0, 0, 1, 0, 0, 2, 0, 3], 0.9, True, "sit"),
    "draw": ("draw", [0, 1, 0, 1, 2, 0, 1, 3, 3], 2.6, False, "stand"),
    "drawing": ("draw", [0, 1, 0, 1, 0, 1, 2, 2], 2.6, True, "stand"),   # 小王正在给群里画：一直画
    "show_drawing": ("draw", [3], 1, False, "stand"),                    # 画好了，举起来给你看
    "sing": ("sing", [0, 1, 2, 3], 2.2, True, "stand"),
    "pout": ("pout", [0, 1, 0, 3, 2, 3], 2.4, False, "stand"),
    "shy": ("shy", [0, 1, 2, 3], 2.8, False, "stand"),
    "startle": ("startle", [0, 0, 1, 3, 3], 5, False, "stand"),
    "point": ("startle", [2], 1, False, "stand"),
    "cheer": ("cheer", [0, 1, 2, 3], 3.5, False, "stand"),
    "tea": ("tea", [0, 1, 2, 0, 3, 0], 0.8, True, "sit"),
    "lookaround": ("lookaround", [0, 1, 2, 3], 1.3, False, "stand"),
    "eat": ("eat", [0, 1, 2, 1, 2, 3], 2.6, False, "stand"),
    "laptop": ("laptop", [0, 1, 0, 1, 2, 3], 3.2, True, "sit"),
}
# Transition clips start from another posture than the one they end in.
STARTS_FROM = {"sit_down": "stand", "stand_up": "sit", "doze": "sit", "lie_down": "sit", "wake": "lie",
               "land": "air", "fall": "air", "dangle": "air"}
BRIDGES = {("stand", "sit"): ["sit_down"], ("stand", "lie"): ["sit_down", "doze", "lie_down"],
           ("sit", "stand"): ["stand_up"], ("sit", "lie"): ["doze", "lie_down"],
           ("lie", "sit"): ["wake"], ("lie", "stand"): ["wake", "stand_up"]}
ASLEEP = frozenset({"sleep", "doze", "lie_down"})
# Clips a coding agent may request through the panel; physics/transition clips stay internal.
CUE_ACTIONS = ("wave", "hop", "cheer", "talk", "think", "phone", "laptop", "draw", "sing", "read", "tea",
               "stretch", "lookaround", "shy", "pout", "startle", "eat", "sleep")
NIGHT = ("凌晨被吵醒", "熬到半夜")
# 小王在 QQ 里查资料 / 画图 / 录语音时，桌宠最多陪着演多久（查询五分钟放弃；生图超时 150 秒，再加上传）。
MEDIA_HOLD = {"searching": 300.0, "drawing": 200.0, "singing": 90.0}
# 群里叫到她的几种方式：平台 @、正文里喊名字、引用她的话；旧版机器人只知道「叫到了」。
CALLS = ("mention", "named", "quoted", "called")
# 她就是小王：群里刚有她的动静（被@、在回、刚说完、在画……），这几分钟里不会挑打盹。
ENGAGED_SECONDS = 180.0
BUSY_CLIP = {"searching": "laptop", "drawing": "drawing", "singing": "sing"}

LINES = {
    "mention": ("有人@我！", "群里有人@我，我去看看。", "被@了，来了来了——"),
    "named": ("嗯？好像有人在说我。", "群里有人喊我名字～", "听到有人叫我了。"),
    "quoted": ("有人引用了我刚才那句。", "我刚说的话被翻出来了。"),
    "called": ("有人在群里叫我！", "来了来了——"),
    "said": ("（在群里回了一句）", "（刚回完群里的话）"),
    "chimed": ("（刚在群里插了句嘴）", "（没人叫我，我也接了一句）"),
    "searching": ("我去查查～", "查资料中……", "等我搜一下。"),
    "proactive": ("（在群里开了个话头）", "（主动在群里冒了个泡）"),
    "drawing": ("群里有人要画，我画画看～", "在画了在画了。", "让我想想怎么画……"),
    "drew": ("画好啦，发群里了！", "给群里画了张图！"),
    "singing": ("清清嗓子——", "我去群里唱两句。", "录条语音～"),
    "voice": ("（在群里发了条语音）", "唱完啦～"),
    "offline": ("机器人没在运行，我先眯一会儿。",),
    "online": ("连上 QQ 啦。", "我回来了。"),
    "poke": ("嗯？", "哇！", "吓我一跳。"),
    "poke_again": ("干嘛呀。", "又戳！", "你是不是很闲？"),
    "poked_more": ("别戳啦！", "再戳要生气了哦。", "哼。"),
    "woken": ("唔……谁呀。", "刚睡着……", "（起床气）"),
    "petted": ("嘿嘿。", "再摸一下也行。", "舒服——"),
    "lifted": ("哇哇——", "放我下来！", "要去哪儿？"),
    "thrown": ("晕……", "轻点嘛！"),
    "bonk": ("哎哟！", "撞到头了……", "咚！"),
    "fed": ("好吃！", "谢谢投喂～", "嚼嚼嚼。"),
    "full": ("吃不下啦。", "再吃就要变成球了。"),
    "greet": ("嗨～", "在忙吗？", "我在这儿哦。"),
    "late": ("好困……", "这么晚还不睡？"),
}


@dataclass
class Step:
    clip: str
    duration: float | None = None      # None: one pass of a non-loop clip (loops default to 4s)
    target_x: float | None = None      # walking destination
    hold: str | None = None            # "chat" / "agent": held until that condition clears


@dataclass
class Particle:
    kind: str
    x: float          # relative to the feet anchor, in figure heights
    y: float
    vx: float
    vy: float
    born: float
    life: float


@dataclass
class Bubble:
    text: str
    kind: str          # say / think / status
    until: float
    serial: int


@dataclass
class Presence:
    available: bool = False
    qq_connected: bool = False
    bot: str = "unknown"        # ready / offline / unknown
    mood: float = 0.0
    circadian: str = ""
    chatter: int = 0
    known: bool = False


@dataclass
class _Pointer:
    x: float = 0.0
    y: float = 0.0
    near: bool = False
    since: float = 0.0
    reversals: deque = field(default_factory=lambda: deque(maxlen=8))
    last_dx: float = 0.0


class PetBrain:
    def __init__(self, *, rng=None, now=0.0, motion=True, quiet=False, scale=1.0):
        self.rng = rng or random.Random()
        self.motion, self.quiet, self.scale = motion, quiet, scale
        self.left, self.right, self.floor = 0.0, 1280.0, 800.0
        self.ceiling = 0.0               # top of the usable screen (below the menu bar)
        self.x, self.y = 640.0, 800.0
        self.vx = self.vy = 0.0
        self.facing = 1
        self.mode = "ground"            # ground / drag / air
        self.plan: deque[Step] = deque()
        self.step: Step | None = None
        self.clip, self.clip_started, self.step_started = "idle", now, now
        self.squash_started = self.hop_started = self.jolt_started = -10.0
        self.swing = self.swing_v = 0.0
        self.blink_started, self.blink_at = -10.0, now + 2.0
        self.particles: list[Particle] = []
        self._emit_at = 0.0
        self.bubble: Bubble | None = None
        self._serial = 0
        self.presence = Presence()
        self._seen_events: deque = deque(maxlen=256)
        self._last_status_line = -1000.0
        self._last_lines: dict[str, float] = {}
        self.chat_pending = False
        self.agent_busy_until = 0.0
        self.last_agent_event = -1000.0
        self.media: str | None = None    # laptop / drawing / sing while 小王 searches, draws or records in QQ
        self.energy = 1.0
        self.last_interaction = now
        self.pokes: deque = deque(maxlen=6)
        self.feeds: deque = deque(maxlen=6)
        self.pointer = _Pointer()
        self.attention = False
        self._greeted_at = -1000.0
        self._grab = None
        self._press = None
        self._now = now

    # ── geometry ──
    @property
    def figure(self):
        return FIGURE * self.scale

    def set_ground(self, left, right, floor, ceiling=None):
        self.left, self.right, self.floor = float(left), float(right), float(floor)
        if ceiling is not None:
            self.ceiling = float(ceiling)
        self.x = self._clamp_x(self.x)
        if self.mode == "ground":
            self.y = self.floor
        else:
            self.y = min(self.y, self.floor)

    def _clamp_x(self, x):
        margin = self.figure * EDGE
        if self.right - self.left <= 2 * margin:
            return (self.left + self.right) / 2
        return max(self.left + margin, min(x, self.right - margin))

    def place(self, x):
        self.x, self.y, self.mode = self._clamp_x(float(x)), self.floor, "ground"

    def posture(self):
        return CLIPS[self.clip][4]

    def body_rect(self):
        """Visible character box (left, top, right, bottom) in screen points."""
        f, posture = self.figure, self.posture()
        height = f * (0.66 if posture == "sit" else 0.48 if posture == "lie" else 1.0)
        width = f * (1.0 if posture == "lie" else 0.62)
        lift = self._hop_offset(self._now)
        if self.clip == "dangle":
            return (self.x - width / 2, self.y - f * 1.05, self.x + width / 2, self.y)
        return (self.x - width / 2, self.y - height + lift, self.x + width / 2, self.y + lift)

    # ── clip plumbing ──
    def _set_clip(self, clip, now, *, restart=False):
        if clip == self.clip and not restart:
            return
        self.clip, self.clip_started = clip, now
        if clip == "hop":
            self.hop_started = now
        elif clip == "land":
            self.squash_started = now

    def _clip_done(self, now):
        _, seq, fps, loop, _ = CLIPS[self.clip]
        return not loop and (now - self.clip_started) * fps >= len(seq)

    def pose(self, now=None):
        """(strip, frame, mirrored, transform) for the renderer."""
        now = self._now if now is None else now
        strip, seq, fps, loop, _ = CLIPS[self.clip]
        index = int(max(0.0, now - self.clip_started) * fps)
        frame = seq[index % len(seq) if loop else min(index, len(seq) - 1)]
        mirrored = self.facing < 0
        if self.clip == "idle" and self.motion:
            t = now - self.blink_started
            if 0 <= t < 0.05 or 0.13 <= t < 0.2:
                frame = 1
            elif 0.05 <= t < 0.13:
                frame = 2
        elif self.clip == "dangle":
            # Legs trail behind the drag direction; frames 1/2 are drawn trailing left/right.
            frame = 1 if self.swing > 8 else 2 if self.swing < -8 else (0 if int(now * 1.2) % 3 else 3)
            mirrored = False
        elif self.clip in ("look_left", "look_right"):
            mirrored = False
        return strip, frame, mirrored, self.transform(now)

    def transform(self, now):
        sx = sy = 1.0
        rot = 0.0
        if self.motion:
            posture = self.posture()
            if self.clip == "sleep":
                sy += 0.022 * math.sin(now * 1.6)
            elif posture in ("stand", "sit") and self.clip not in (
                    "walk", "hop", "land", "stand_up", "sit_down", "startle"):
                sy += 0.010 * math.sin(now * 2.1)
            t = now - self.squash_started
            if 0 <= t < 0.32:
                k = math.sin(t / 0.32 * math.pi) * (1 - t / 0.32)
                sy -= 0.18 * k
                sx += 0.12 * k
            if self._press is not None and self.mode == "ground":
                k = min(1.0, (now - self._press[0]) / 0.06)       # pressed: a soft squish right away
                sy -= 0.05 * k
                sx += 0.03 * k
            t = now - self.jolt_started
            if 0 <= t < 0.12:                                      # poked: snap tall, then settle
                k = math.sin(t / 0.12 * math.pi)
                sy += 0.07 * k
                sx -= 0.04 * k
            if self.mode == "drag":
                rot = self.swing
            elif self.mode == "air":
                rot = max(-16.0, min(16.0, self.vx * 0.01))
        return {"sx": sx, "sy": sy, "rot": rot, "dy": self._hop_offset(now) + self._jolt_offset(now),
                "pivot": "hands" if self.clip == "dangle" else "feet"}

    def _jolt_offset(self, now):
        t = now - self.jolt_started
        if not self.motion or not 0 <= t < 0.26:
            return 0.0
        return -math.sin(t / 0.26 * math.pi) * self.figure * 0.06

    def _hop_offset(self, now):
        if not self.motion or self.clip != "hop":
            return 0.0
        t = now - self.hop_started - 1 / 7      # airborne during frames 1-2
        if 0 <= t <= 2 / 7:
            p = t / (2 / 7)
            return -4 * p * (1 - p) * self.figure * 0.32
        return 0.0

    # ── planning ──
    def _end_posture(self):
        if self.plan:
            return CLIPS[self.plan[-1].clip][4]
        return CLIPS[self.step.clip][4] if self.step is not None else self.posture()

    def _queue(self, *steps, now, interrupt=False):
        """Append steps, inserting sit/stand/lie transitions so poses never jump."""
        if interrupt:
            self.plan.clear()
            self.step = None
        posture = self._end_posture()
        for step in steps:
            start = STARTS_FROM.get(step.clip, CLIPS[step.clip][4])
            for bridge in BRIDGES.get((posture, start), []):
                self.plan.append(Step(bridge))
            self.plan.append(step)
            posture = CLIPS[step.clip][4]
        if interrupt:
            self._advance(now)

    def _advance(self, now):
        self.step = self.plan.popleft() if self.plan else None
        self.step_started = now
        if self.step is None:
            if self.posture() == "stand":
                self._set_clip("idle", now)
            return
        if self.step.target_x is not None:
            self.facing = 1 if self.step.target_x >= self.x else -1
        self._set_clip(self.step.clip, now, restart=True)

    def _step_done(self, now):
        step = self.step
        if step is None:
            return True
        if step.hold == "chat":
            return not self.chat_pending
        if step.hold == "agent":
            return now >= self.agent_busy_until
        if step.target_x is not None:
            return abs(self.x - step.target_x) < 1.5
        if step.duration is not None:
            return now - self.step_started >= step.duration
        return now - self.step_started >= 4.0 if CLIPS[step.clip][3] else self._clip_done(now)

    def _choose(self, now):
        """Pick the next ambient activity from needs and the agent's real state."""
        p = self.presence
        sleepy = p.circadian in NIGHT or self.energy < 0.25
        drowsy = p.circadian == "午后有点困"     # an afternoon nap now and then, not all afternoon
        offline = p.known and p.bot != "ready"
        engaged = not offline and now - self.last_agent_event < ENGAGED_SECONDS
        mood = p.mood if p.known else 0.0
        r = self.rng.uniform
        if self.media and now < self.agent_busy_until:
            # Back from a poke, a drag or a local chat: the drawing in QQ is still going.
            return [Step(self.media, hold="agent"), Step("idle", 1)]
        self.media = None
        if self.attention:
            return [Step("idle", 1.5)]
        weights = {
            "idle": 3.0, "wander": 0.0 if self.quiet or self.pointer.near else 2.6, "lookaround": 0.7,
            "stretch": 0.4 + (1.6 if sleepy or drowsy or p.circadian == "刚睡醒" else 0.0),
            "tea": 0.8 + max(0.0, mood), "read": 0.8, "laptop": 0.4,
            "phone": 0.5 + min(2.5, p.chatter * 0.4),
            "nap": 0.0 if engaged else 0.15 + (3.5 if sleepy else 1.2 if drowsy else 0.0)
                   + (5.0 if offline else 0.0) + (2.0 if self.energy < 0.4 else 0.0),
            "hop": 0.2 + max(0.0, mood) * 1.6, "sing": 0.2 + max(0.0, mood) * 1.2,
            "cheer": max(0.0, mood - 0.3) * 1.5, "draw": 0.3,
            "pout": max(0.0, -mood - 0.2) * 3.0, "sit": 0.5,
        }
        if not self.motion:
            weights = {"idle": 5.0, "read": 1.0, "tea": 1.0, "nap": weights["nap"], "phone": weights["phone"]}
        choice = self._weighted(weights)
        if choice == "idle":
            return [Step("idle", r(3, 8))]
        if choice == "wander":
            margin = self.figure * EDGE
            low, high = self.left + margin, self.right - margin
            if high - low < self.figure:
                return [Step("idle", r(3, 6))]
            target = r(low, high)
            for _ in range(6):
                if abs(target - self.x) > self.figure * 0.6:
                    break
                target = r(low, high)
            return [Step("walk", target_x=target), Step("idle", r(1.5, 4))]
        if choice in ("tea", "read", "laptop"):
            return [Step(choice, r(14, 32)), Step("idle", r(1, 3))]
        if choice == "sit":
            return [Step("sit_swing", r(8, 16)), Step("idle", 1)]
        if choice == "nap":
            return [Step("sleep", 900.0 if offline else r(40, 120)), Step("wake"), Step("stretch")]
        if choice == "phone":
            return [Step("phone", r(5, 12)), Step("idle", 1.5)]
        if choice == "pout":
            return [Step("pout"), Step("idle", r(2, 4))]
        return [Step(choice)]

    def _weighted(self, weights):
        total = sum(max(0.0, v) for v in weights.values())
        pick = self.rng.uniform(0, total)
        for name, value in weights.items():
            pick -= max(0.0, value)
            if pick <= 0:
                return name
        return "idle"

    # ── per-frame update ──
    def tick(self, now):
        dt = max(0.0, min(0.1, now - self._now))
        self._now = now
        asleep = self.clip in ASLEEP
        self.energy = max(0.0, min(1.0, self.energy + (dt / 240 if asleep else -dt / 2400)))
        if self.mode == "air":
            self._fall(now, dt)
        elif self.mode == "drag":
            self._hang(dt)
        else:
            self._ground(now, dt)
        if self.clip == "idle" and now >= self.blink_at:
            self.blink_started = now
            self.blink_at = now + (0.3 if self.rng.random() < 0.15 else self.rng.uniform(2.4, 6.0))
        self._particles(now, dt)
        if self.bubble is not None and now >= self.bubble.until:
            self.bubble = None

    def _ground(self, now, dt):
        self.y = self.floor
        step = self.step
        if step is not None and step.target_x is not None and self.clip == "walk":
            speed = WALK_SPEED * self.scale * (1.15 if self.presence.mood > 0.3
                                               else 0.85 if self.presence.mood < -0.3 else 1.0)
            delta = step.target_x - self.x
            self.x += max(-speed * dt, min(speed * dt, delta))
            self.facing = 1 if delta >= 0 else -1
            clamped = self._clamp_x(self.x)
            if clamped != self.x:
                self.x = step.target_x = clamped
            if self.pointer.near or self.attention:
                self._queue(Step("idle", 1.5), now=now, interrupt=True)
        if self._step_done(now):
            self._advance(now)
            if self.step is None:
                self._queue(*self._choose(now), now=now)
                self._advance(now)
        self._look(now)

    def _look(self, now):
        """Idle glances follow a nearby cursor; a lingering cursor gets a wave."""
        if self.step is None or self.step.clip != "idle":
            return
        if not (self.pointer.near and now - self.pointer.since > 0.35):
            if self.clip in ("look_left", "look_right"):
                self._set_clip("idle", now)
            return
        offset = self.pointer.x - self.x
        if abs(offset) > self.figure * 0.12:
            self.facing = 1 if offset > 0 else -1
            self._set_clip("look_right" if offset > 0 else "look_left", now)
        else:
            self._set_clip("idle", now)
        if (now - self.pointer.since > 2.5 and now - self._greeted_at > 120
                and now - self.last_interaction > 60 and self.motion):
            self._greeted_at = now
            self._queue(Step("wave"), Step("idle", 2.0), now=now, interrupt=True)
            lines = LINES["late"] if self.presence.circadian in NIGHT else LINES["greet"]
            self.say(self.rng.choice(lines), now=now, seconds=3.5)

    def _fall(self, now, dt):
        self.vy += GRAVITY * self.scale * dt
        self.x += self.vx * dt
        self.y += self.vy * dt
        self.vx *= 0.995
        clamped = self._clamp_x(self.x)
        if clamped != self.x:
            self.x, self.vx = clamped, -self.vx * 0.45
        if self.vy < 0 and self.y - self.figure < self.ceiling:
            # Flung upwards she bonks the top of the screen instead of flying out of sight.
            self.y, self.vy, self.vx = self.ceiling + self.figure, -self.vy * 0.25, self.vx * 0.8
            self._emit("sparkle", now, 2)
            if self.rng.random() < 0.5:
                self.say(self.rng.choice(LINES["bonk"]), now=now, seconds=1.8)
        if self.y >= self.floor:
            impact = self.vy
            self.y, self.vx, self.vy = self.floor, 0.0, 0.0
            self.mode = "ground"
            self.swing = self.swing_v = 0.0
            self.plan.clear()
            self.step = None
            self._set_clip("land", now, restart=True)
            steps = [Step("land")]
            if impact > 1900 * self.scale:
                steps.append(Step("pout"))
                self.say(self.rng.choice(LINES["thrown"]), now=now, seconds=3.0)
                self._emit("sweat", now)
            self._queue(*steps, Step("idle", 1.5), now=now, interrupt=True)

    def _hang(self, dt):
        grab = self._grab
        # Ease from where she was grabbed to hanging by her raised hands.
        k = min(1.0, dt * 14)
        grab["dx"] += (0.0 - grab["dx"]) * k
        grab["dy"] += (self.figure * 0.98 - grab["dy"]) * k
        self.x = grab["cx"] + grab["dx"]
        self.y = min(self.floor, grab["cy"] + grab["dy"])
        target = max(-26.0, min(26.0, self.vx * 0.03))
        self.swing_v += ((target - self.swing) * 60.0 - self.swing_v * 9.0) * dt
        self.swing += self.swing_v * dt
        self.vx *= max(0.0, 1 - dt * 6)     # stillness lets the pendulum settle

    # ── particles and speech ──
    def _emit(self, kind, now, count=1):
        if not self.motion:
            return
        for _ in range(count):
            self.particles.append(Particle(
                kind, 0.2 if kind == "z" else self.rng.uniform(-0.14, 0.14),
                -0.95 if kind in ("z", "note", "heart", "sweat", "!", "puff") else -0.55,
                (0.07 if kind == "z" else 0.0) + self.rng.uniform(-0.04, 0.04),
                -self.rng.uniform(0.16, 0.3), now, 1.0 if kind in ("!", "puff") else 1.7))

    def _particles(self, now, dt):
        kind = {"sleep": "z", "sing": "note", "shy": "heart", "cheer": "sparkle",
                "hop": "sparkle", "eat": "crumb"}.get(self.clip)
        if kind and self.motion and now >= self._emit_at:
            self._emit(kind, now)
            self._emit_at = now + {"z": 1.3, "note": 0.7, "heart": 0.5}.get(kind, 0.45)
        alive = []
        for p in self.particles:
            if now - p.born < p.life:
                p.x += p.vx * dt
                p.y += p.vy * dt
                alive.append(p)
        self.particles = alive[-24:]

    def say(self, text, *, now, seconds=None, kind="say"):
        text = str(text).strip()
        if not text:
            return
        self._serial += 1
        seconds = seconds if seconds is not None else max(3.0, min(14.0, 2.2 + len(text) * 0.12))
        self.bubble = Bubble(text, kind, now + seconds, self._serial)

    def _status_line(self, key, now, *, gap=75.0, starts=False):
        """Narrate agent activity sparingly: per-kind gap, never over a reply or on top of another line.

        starts=True: she just started something long (searching, drawing, singing); that line may replace
        a status line from a moment ago ("（在群里回了一句）" for the "我去查查" right before it).
        """
        if (now - self._last_lines.get(key, -1000.0) < gap
                or (not starts and now - self._last_status_line < 6)
                or self.chat_pending or (self.bubble is not None and self.bubble.kind == "say")):
            return
        self._last_status_line = self._last_lines[key] = now
        self.say(self.rng.choice(LINES[key]), now=now, seconds=3.2, kind="status")

    # ── user input ──
    def hover(self, now, x, y, *, near):
        p = self.pointer
        if near and not p.near:
            p.since = now
        p.near = near
        left, top, right, bottom = self.body_rect()
        over_head = near and left <= x <= right and top <= y <= top + (bottom - top) * 0.42
        dx = x - p.x
        if over_head and self._press is None and abs(dx) > 1.5:
            if p.last_dx and (dx > 0) != (p.last_dx > 0):
                p.reversals.append(now)
            p.last_dx = dx
            if sum(1 for t in p.reversals if now - t < 1.4) >= 4 and self.mode == "ground":
                p.reversals.clear()
                self._petted(now)
        p.x, p.y = x, y

    def _petted(self, now):
        self.last_interaction = now
        if self.clip in ASLEEP:
            self.say("（翻了个身）", now=now, seconds=2, kind="status")
            return
        if self.clip == "shy" or self.chat_pending:
            return
        self._queue(Step("shy"), Step("idle", 2.0), now=now, interrupt=True)
        self._emit("heart", now, 2)
        if self.rng.random() < 0.5:
            self.say(self.rng.choice(LINES["petted"]), now=now, seconds=2.6)

    def press(self, now, x, y):
        self._press = (now, x, y)
        self.last_interaction = now

    def drag(self, now, x, y):
        if self._press is None:
            return
        _, px, py = self._press
        if self.mode != "drag":
            if math.hypot(x - px, y - py) < 4:
                return
            self.mode = "drag"
            self._grab = {"dx": self.x - x, "dy": self.y - y, "cx": x, "cy": y, "samples": deque(maxlen=6)}
            self.plan.clear()
            self.step = None
            self._set_clip("dangle", now, restart=True)
            self._emit("sweat", now)
            if self.rng.random() < 0.35:
                self.say(self.rng.choice(LINES["lifted"]), now=now, seconds=2.2)
        grab = self._grab
        grab["cx"], grab["cy"] = x, y
        grab["samples"].append((now, x, y))
        (t1, x1, y1), (t2, x2, y2) = grab["samples"][0], grab["samples"][-1]
        if t2 > t1:
            self.vx, self.vy = (x2 - x1) / (t2 - t1), (y2 - y1) / (t2 - t1)

    def release(self, now, x, y):
        """Return "drop" after a drag, "click" for a press without movement."""
        pressed, self._press = self._press, None
        self.last_interaction = now
        if self.mode == "drag":
            fresh = [s for s in self._grab["samples"] if now - s[0] < 0.12]
            if len(fresh) < 2:
                self.vx = self.vy = 0.0
            self.vx = max(-2600.0, min(2600.0, self.vx))
            self.vy = max(-2200.0, min(1400.0, self.vy))
            self._grab = None
            self.mode = "air"
            self._set_clip("fall", now, restart=True)
            return "drop"
        return "click" if pressed is not None else None

    def poke(self, now):
        """A click reacts at once and escalates: flinch, then "干嘛呀", then a pout."""
        self.pokes.append(now)
        self.last_interaction = now
        if self.chat_pending or self.mode != "ground":
            return
        if self.clip in ASLEEP or self.clip == "wake":
            self._queue(Step("pout"), Step("idle", 2), now=now, interrupt=True)
            self.say(self.rng.choice(LINES["woken"]), now=now, seconds=3)
            return
        recent = sum(1 for t in self.pokes if now - t < 6)
        self.jolt_started = now
        if recent >= 3:
            self.pokes.clear()
            self._queue(Step("pout"), Step("idle", 2), now=now, interrupt=True)
            self._emit("puff", now)
            self.say(self.rng.choice(LINES["poked_more"]), now=now, seconds=2.6)
            return
        self._emit("!", now)
        if self.posture() == "sit":
            # Seated: flinch in place instead of standing up just to be startled.
            self.say(self.rng.choice(LINES["poke" if recent == 1 else "poke_again"]), now=now, seconds=2)
            return
        if recent == 2:
            self._queue(Step("point", 1.4), Step("idle", 1.5), now=now, interrupt=True)
            self.say(self.rng.choice(LINES["poke_again"]), now=now, seconds=2)
            return
        self._queue(Step("startle"), Step("idle", 1.5), now=now, interrupt=True)
        if self.rng.random() < 0.6:
            self.say(self.rng.choice(LINES["poke"]), now=now, seconds=2)

    def feed(self, now):
        self.feeds.append(now)
        self.last_interaction = now
        if self.chat_pending or self.mode != "ground":
            return
        if sum(1 for t in self.feeds if now - t < 600) > 4:
            self._queue(Step("pout"), Step("idle", 2), now=now, interrupt=True)
            self.say(self.rng.choice(LINES["full"]), now=now, seconds=2.6)
            return
        self.energy = min(1.0, self.energy + 0.15)
        self._queue(Step("eat"), Step("idle", 1.5), now=now, interrupt=True)
        self.say(self.rng.choice(LINES["fed"]), now=now, seconds=2.4)

    def enter(self, now, height):
        """Drop in from above the floor (startup entrance)."""
        if self.motion:
            self.y, self.mode = self.floor - height, "air"
            self._set_clip("fall", now, restart=True)

    def settle(self, now):
        """Stop walking where she is (e.g. the user just asked her to stay put)."""
        if self.mode == "ground" and self.clip == "walk":
            self._queue(Step("idle", 1.5), now=now, interrupt=True)

    def set_attention(self, value, now):
        """Toolbar or chat open: stop wandering and face the user."""
        value = bool(value)
        if value and not self.attention and self.mode == "ground" and self.clip == "walk":
            self._queue(Step("idle", 1.5), now=now, interrupt=True)
        self.attention = value

    # ── local chat ──
    def chat_sent(self, now):
        self.chat_pending = True
        self.last_interaction = now
        if self.mode == "ground":
            busy = "laptop" if self.posture() == "sit" else "think"
            self._queue(Step(busy, hold="chat"), now=now, interrupt=True)
        self.say("…", now=now, seconds=80, kind="think")

    def chat_reply(self, now, text):
        self.chat_pending = False
        if self.mode == "ground":
            self._queue(Step("talk", max(2.5, min(8.0, len(text) * 0.09))), Step("idle", 2), now=now, interrupt=True)
        self.say(text, now=now, seconds=max(6.0, min(30.0, 4 + len(text) * 0.16)))

    def chat_failed(self, now, message):
        self.chat_pending = False
        if self.mode == "ground":
            self._queue(Step("lookaround"), Step("idle", 2), now=now, interrupt=True)
        self.say(message, now=now, seconds=6, kind="status")

    # ── agent state (read-only, from the panel) ──
    def observe(self, now, data):
        """Apply one /companion/presence snapshot. Unknown fields are ignored."""
        if not isinstance(data, dict):
            return
        old = self.presence
        mood, chatter = data.get("mood"), data.get("chatter")
        new = Presence(
            available=data.get("available") is True, qq_connected=data.get("qq_connected") is True,
            bot=data.get("bot") if data.get("bot") in ("ready", "offline") else "unknown",
            mood=max(-1.0, min(1.0, float(mood))) if type(mood) in (int, float) and math.isfinite(mood) else 0.0,
            circadian=data.get("circadian") if isinstance(data.get("circadian"), str) else "",
            chatter=max(0, min(500, chatter)) if type(chatter) is int else 0, known=True)
        self.presence = new
        if old.known and old.bot == "ready" and new.bot == "offline":
            self._status_line("offline", now, gap=0)
            if self.mode == "ground" and not self.chat_pending and self.clip not in ASLEEP:
                self._queue(Step("stretch"), Step("sleep", 900.0), Step("wake"), now=now, interrupt=True)
        elif old.known and old.bot == "offline" and new.bot == "ready":
            self._status_line("online", now, gap=0)
            if self.mode == "ground" and self.clip in ASLEEP:
                self._queue(Step("wake"), Step("stretch"), now=now, interrupt=True)
        for event in data.get("events") or ():
            if not isinstance(event, dict) or self.has_seen(event):
                continue
            self._seen_events.append(self._event_key(event))
            self._agent_event(now, event)

    @staticmethod
    def _event_key(event):
        return (event.get("kind"), event.get("ts"), event.get("id"))

    def has_seen(self, event):
        return self._event_key(event) in self._seen_events

    def _agent_event(self, now, event):
        kind = event.get("kind")
        # What 小王 is busy with in QQ is tracked even mid-drag or mid-chat, so she
        # picks the sketchbook back up afterwards (see _choose).
        finished = self.media
        if kind in MEDIA_HOLD:
            self.media = BUSY_CLIP[kind]
            self.agent_busy_until = now + MEDIA_HOLD[kind]
        elif kind in ("drew", "voice", "media_end") or (kind in ("said", "chimed", "proactive")
                                                         and self.media == "laptop"):
            self.media, self.agent_busy_until = None, 0.0    # the answer after a search ends it
        busy = self.media is not None and kind not in MEDIA_HOLD
        if kind != "cue":
            self.last_agent_event = now
        if self.mode != "ground" or self.chat_pending:
            return
        # Asleep is no excuse: whatever 小王 does in QQ wakes her (the queue walks lie -> sit -> stand).
        if kind == "cue":
            action = event.get("action")
            text = event.get("text") if isinstance(event.get("text"), str) else ""
            if action in CUE_ACTIONS:
                hold = 900.0 if action == "sleep" else 6.0 if CLIPS[action][3] else None
                self._queue(Step(action, hold), *([] if action == "sleep" else [Step("idle", 2)]),
                            now=now, interrupt=True)
            if text.strip():
                self.say(text[:120], now=now)
            return
        if kind in CALLS:
            self._emit("!", now)
            if not busy:      # mid-drawing she only glances up
                self._queue(Step("phone", 4), Step("idle", 1), now=now, interrupt=True)
                self._status_line(kind, now, gap=40)
        elif kind == "replying" and not busy:
            self.agent_busy_until = now + 40
            clip = "laptop" if self.posture() == "sit" else "phone"
            self._queue(Step(clip, hold="agent"), Step("idle", 1), now=now, interrupt=True)
        elif kind in MEDIA_HOLD:
            # Wakes her if she dozed off: someone asked her to look something up, draw or sing.
            self._queue(Step(self.media, hold="agent"), Step("idle", 1), now=now, interrupt=True)
            self._status_line(kind, now, gap=20, starts=True)
        elif kind == "media_end":
            if finished and self.step is not None and self.step.hold == "agent":
                self._queue(Step("idle", 1), now=now, interrupt=True)   # put it down; her QQ line follows
        elif kind in ("said", "chimed", "proactive") and busy:
            pass              # the "好，我画画看" before the picture: keep drawing
        elif kind in ("said", "chimed", "proactive", "drew", "voice"):
            self.agent_busy_until = 0.0
            if kind == "drew" and finished == "drawing":
                self._queue(Step("show_drawing", 2.6), Step("idle", 1.5), now=now, interrupt=True)
                self._emit("sparkle", now, 3)
            else:
                clip = {"drew": "draw", "voice": "sing"}.get(kind, "talk")
                seconds = 1.6 if finished == "sing" else 3.0
                self._queue(Step(clip, seconds if clip in ("talk", "sing") else None), Step("idle", 1.5),
                            now=now, interrupt=True)
            self._status_line(kind, now, gap=20 if kind in ("drew", "voice") else 75)

    def snapshot(self):
        return {"clip": self.clip, "mode": self.mode, "x": round(self.x, 1), "y": round(self.y, 1),
                "facing": self.facing, "plan": [s.clip for s in self.plan],
                "bubble": self.bubble.text if self.bubble else None}
