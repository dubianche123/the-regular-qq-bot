"""激活度：作息和心情 —— 一次回复的「内在状态」，渲染成一句状态自述。

设计原则（群主 2026-10-19 定调）：信号融合成**一个**轮廓，不各自成系统、除心情的落盘外不碰模型、全部纯计算。

- 作息/生物钟：消息自带时间戳。凌晨的消息是「把人吵醒」—— 真回了也带着起床气；
  这不是一个定时器，是看消息几点发的。
- 心情：小王自己的情绪，全局一个值（-1 烦 → +1 顺）。事件驱动：每轮好感那一票
  （分诊判的方向，现成的，零额外调用）喂一笔；读取时按半衰期自然回中。落 state.json（mood 键），重启不丢。

亲密度 2026-11-16 起不再进这里：它以前决定能看到几条背景、乘在插嘴意愿上，现在只管怎么说
（relations.build_relation_note）。对外入口：state_now() → 状态自述；主动开口另有 proactive_ok。
"""

import time
from datetime import datetime

# ── 作息/生物钟 ──
# (起始小时, 插嘴意愿系数, 状态词)。状态词为空 = 不到注入提示词的程度。
# 系数是**乘数**：乘在插嘴基数 × 亲密度权重之后。深夜不是不回，是不太会主动搭话；
# 被 @ 是另一回事——醒着的人被点名也得应，但语气是起床气。
_CIRCADIAN = (
    (0,  0.45, "凌晨被吵醒"),
    (6,  0.75, "刚睡醒"),
    (10, 1.00, ""),
    (13, 0.90, "午后有点困"),
    (15, 1.00, ""),
    (23, 0.70, "熬到半夜"),
)


def circadian(hour=None):
    """按小时返回 (插嘴意愿系数, 状态词)。hour 不传就用现在。"""
    hour = datetime.now().hour if hour is None else hour
    factor, label = 1.0, ""
    for start, f, label_ in _CIRCADIAN:
        if hour >= start:
            factor, label = f, label_
    return factor, label


# ── 心情 ──
_MOOD_HALFLIFE = 6 * 3600.0     # 6 小时半衰期：早上被惹的烦，晚上该消得差不多了
_MOOD_MAX = 1.0
_mood_state = {"v": 0.0, "ts": 0.0}


def hydrate_mood(raw):
    if isinstance(raw, dict):
        try:
            _mood_state["v"] = max(-_MOOD_MAX, min(_MOOD_MAX, float(raw.get("v") or 0.0)))
            _mood_state["ts"] = float(raw.get("ts") or 0.0)
        except (TypeError, ValueError):
            pass


def dump_mood_into(data):
    if _mood_state["ts"]:
        data["mood"] = {"v": round(_mood_state["v"], 3), "ts": _mood_state["ts"]}


def _decay(now):
    dt = max(0.0, now - _mood_state["ts"])
    return _mood_state["v"] * (0.5 ** (dt / _MOOD_HALFLIFE))


def note_mood(delta, now=None):
    """每轮好感度结算后喂一笔（delta 是裁剪过的方向值，正=被善待）。"""
    now = time.time() if now is None else now
    v = max(-_MOOD_MAX, min(_MOOD_MAX, _decay(now) + delta * 0.25))
    _mood_state["v"] = v
    _mood_state["ts"] = now


def current_mood(now=None):
    """当前有效心情（已按半衰期回中），[-1, 1]。纯读——不许刷新衰减时钟。"""
    now = time.time() if now is None else now
    return max(-_MOOD_MAX, min(_MOOD_MAX, _decay(now)))


def mood_from_state(raw, now=None):
    """别的进程（面板/桌宠）读 state.json 里落盘的心情：同一套半衰期，不碰本进程的心情。"""
    if not isinstance(raw, dict):
        return 0.0
    try:
        v = max(-_MOOD_MAX, min(_MOOD_MAX, float(raw.get("v") or 0.0)))
        ts = float(raw.get("ts") or 0.0)
    except (TypeError, ValueError):
        return 0.0
    now = time.time() if now is None else now
    if not ts or v != v:
        return 0.0
    return v * (0.5 ** (max(0.0, now - ts) / _MOOD_HALFLIFE))


# 烦透了的那条线。以前心情压在这下面半小时，整群会自动切进「发疯模式」（换一套人设、
# 发一段固定的进场词，回升了再发一段退场词）；2026-11-01 模式全删了，发疯只剩这一句状态，
# 每轮跟着心情走 —— 不用切换，也就不会卡在一个状态里。
_FED_UP = -0.7


def _mood_word(v):
    if v >= 0.3:
        return "心情不错"
    if v <= _FED_UP:
        return "烦透了"
    if v <= -0.3:
        return "有点烦"
    return ""


# 以前这里还有两样跟着亲密度走的东西，2026-11-16 撤了：
# · 插嘴意愿乘数（作息 × 心情）：11-02 起接不接由小王自己定，这个数算出来没人用。
# · 背景条数（生人 2 条到本命 10 条）：看懂群里在聊什么跟熟不熟无关，交情只管怎么说
#   （称呼与分寸，relations.build_relation_note）；生人那一轮连上一句是谁说的都可能看不到。


# ── 总装 ──
def state_line(label, mood_v):
    """把作息 + 心情渲染成一段注入提示词的状态自述。没什么可说时返回空串。"""
    bits = []
    # 只写「话该怎么说」，不写「你刚醒」这种它能念出来的情节（2026-11-03）：luna 照着状态自述
    # 开口「我这刚醒，脑内地图还在转圈」，还拿它躲问题；11-01 到 11-03 一共 27 句。
    # 早上（刚睡醒）不再写状态，只留插嘴意愿打折（_CIRCADIAN）。
    if label == "凌晨被吵醒":
        bits.append("夜很深了：回复要短，带点不耐烦的困，别展开任何话题")
    elif label == "熬到半夜":
        bits.append("夜深了，你有点撑不住了：回复更短更含糊，别开新话题")
    mw = _mood_word(mood_v)
    if mw == "烦透了":
        bits.append("你今天烦透了：说话冲、带火气，可以发几句牢骚、小小发个疯，"
                    "但火冲着事不冲着人，别迁怒眼前这位")
    elif mw == "有点烦":
        bits.append("你今天有点烦：更懒得接话，别人起哄你懒得陪")
    elif mw == "心情不错":
        bits.append("你今天心情不错：更愿意接梗闲聊两句")
    if not bits:
        return ""
    return ("【小王当前状态】" + "；".join(bits)
            + "。（这是你的真实体感，只管你说话的劲头；别念出来，也别拿它当不回答的理由）")


def state_now(hour=None, now=None):
    """这一刻的状态自述（作息 + 心情），注入提示词用；没什么可说时为空串。"""
    now = time.time() if now is None else now
    _, label = circadian(hour)
    return state_line(label, current_mood(now))


# ── 主动性 ──
# 不是定时器：所有闸门过了才开口，节奏由状态决定（麦麦/Agent Mind 同款思路的轻量版）。
_PROACTIVE_MIN_GAP = 4 * 3600.0      # 距上次主动发言至少 4 小时
_PROACTIVE_QUIET_MIN = 10 * 60.0     # 群静默 10 分钟以上（还热着轮不到我插话）
_PROACTIVE_QUIET_MAX = 6 * 3600.0    # 但凉透超过 6 小时也不硬聊（没人接）


def proactive_ok(mood_v, hour, now, last_proactive_ts, last_msg_ts):
    """主动开口的闸门：纯计算。返回 (ok, 原因)。"""
    if now - (last_proactive_ts or 0) < _PROACTIVE_MIN_GAP:
        return False, "距上次主动发言不够 4 小时"
    if hour < 8 or hour >= 24:
        return False, "深夜/清晨不主动开口"
    quiet = now - (last_msg_ts or 0)
    if quiet < _PROACTIVE_QUIET_MIN:
        return False, "群还热着，轮不到我插话"
    if quiet > _PROACTIVE_QUIET_MAX:
        return False, "群凉透了，开口也没人接"
    if mood_v <= -0.3:
        return False, "心情不好，不想开口"
    return True, ""
