"""开口时机先验：现在这一刻，群里有没有「接话的空档」。纯本地、零模型调用。

它刻意只回答一件事 —— **此刻像不像一个会有人接话的时刻**；
「接这句合不合适」归原生判断（regular/decisions.py），「接谁」归亲密度权重，
「想不想说话」归作息心情（activation.py）。四件事分开，谁出问题都查得到。

为什么要有它（2026-10-31 群主定调「何时介入是 HCI 里最重要的课题之一」）：
原来的插嘴门是一枚**与对话无关**的骰子 —— BANTER_PROBABILITY × 亲密度 × 作息。
它在三个人你一句我一句的热场里，和在冷了二十分钟的死群里，掷的是同一个概率。
机器人接得尴不尴尬，八成就差在这一下。

先验从哪来：本机 2026-10-23 迁移快照的归档（1,782 条收到的群消息、一个群、
九个发言标识、2026-09-16 至 10-23），拟合「这条消息之后 90 秒内，有没有
**另一个人**开口」。按日期切分（训练至 10-16，留出 10-17 起 412 条）：

    留出 Brier 0.238 / 常数基线 0.277；AUC 0.655；
    五分位实际命中率 0.280 → 0.341 → 0.476 → 0.573 → 0.646（单调）。

**这几个数字说明的和不说明的**（别混）：
  - 说明：本地节奏特征能把「有人会接话」的时刻排出 2.3 倍的高低差，且单调。
  - 不说明：不说明「机器人此刻插嘴受欢迎」。人接人和人接机器人不是一回事；
    这只是**有没有空档**的代理指标。受不受欢迎由 decisions 看内容、由
    receptive.py 按本人的后续反应慢慢修。
  - 不说明：一个群、六个活跃的人、五周。换群、换人数要重新看，别当普适常数。
  - 这不是随机实验，也没有分组；它只是把一枚瞎掷的骰子换成看得见节奏的骰子。

保量不加量：`shape()` 把分数折成**均值约为 1** 的乘数（按本群自己的分数均值
归一），所以 BANTER_PROBABILITY 仍然决定群里整体话量，先验只决定这些话
**落在哪些时刻**。这条和亲密度权重的约定一致：基数不动，权重只管偏好。
"""
import math
import time

# ── 冻结系数：见模块开头的拟合与留出结果。改动请连同留出数字一起改。 ──
# 特征归一化必须和拟合时逐字一致，否则系数无意义（这是最容易悄悄漂掉的地方）。
COEF = {
    "bias": -1.163,
    "log_gap": -0.521,       # 距上一条越久，越没人接
    "burst60": 1.518,        # 最近 60 秒的消息密度
    "speakers5m": 2.870,     # 最近 5 分钟的不同发言人数 —— 单项最强
    "is_bracket": -1.204,    # [图片]/[聊天记录] 这类非文字消息，接的人明显少
    "length": -0.150,        # 长篇大论没人接
    "monologue": -0.170,     # 上一条也是同一个人在说
}
# 归档里「90 秒内有另一个人开口」的总体比例；只用作新群 EMA 的起点。
POPULATION_BASE = 0.593
WINDOW_SECONDS = 90.0        # 标签窗口，留档用：先验本身不依赖它

_BURST_WINDOW = 60.0
_SPEAKER_WINDOW = 300.0
_GAP_CAP = 3600.0
_LEN_CAP = 80.0

# shape 的上下限。均值归一本来就把乘数拉向 1，夹一下只防极端值和 EMA 冷启动。
SHAPE_MIN = 0.40
SHAPE_MAX = 2.00
# 均值用「前 32 条算真均值、之后转 EMA」—— 新群别被头几条带偏，老群跟得上变化。
_MEAN_SWITCH = 32


def _sigmoid(z):
    return 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, z))))


def _row_time(row):
    try:
        return float(row.get("time") or 0.0)
    except (TypeError, ValueError, AttributeError):
        return 0.0


def features(rows, now=None):
    """从「本群最近消息，当前这条在最后」算出特征。rows 为空返回 None。

    rows 的形状沿用 bot.group_buffers 的那一份：{sender, text, time, message_id}。
    ⚠️ 语义必须和离线拟合一致：burst60 **不含**当前这条，speakers5m **含**当前这条。
    缓冲区只有 10 条，所以 60 秒里挤进十条以上时 burst60 会少算 —— 但该项本身
    就在 8 处截断，影响只落在最热的尾部，不改排序方向。
    """
    rows = [r for r in (rows or []) if isinstance(r, dict)]
    if not rows:
        return None
    current = rows[-1]
    now = _row_time(current) if now is None else float(now)
    prev = rows[-2] if len(rows) > 1 else None
    gap = (now - _row_time(prev)) if prev is not None else _GAP_CAP
    gap = max(0.0, min(_GAP_CAP, gap))
    burst = sum(1 for r in rows[:-1] if 0 <= now - _row_time(r) <= _BURST_WINDOW)
    speakers = {r.get("sender") for r in rows
                if r.get("sender") and 0 <= now - _row_time(r) <= _SPEAKER_WINDOW}
    text = str(current.get("text") or "")
    return {
        "log_gap": math.log1p(gap) / 8.0,
        "burst60": min(burst, 8) / 8.0,
        "speakers5m": min(len(speakers), 4) / 4.0,
        "is_bracket": 1.0 if text.startswith("[") else 0.0,
        "length": min(len(text), _LEN_CAP) / _LEN_CAP,
        "monologue": (1.0 if prev is not None
                      and prev.get("sender") == current.get("sender") else 0.0),
    }


def estimate(rows, now=None):
    """此刻有人接话的本地估计，0–1。材料不足（空缓冲）时返回 None，调用方照旧。"""
    feats = features(rows, now=now)
    if feats is None:
        return None
    z = COEF["bias"] + sum(COEF[k] * v for k, v in feats.items())
    return _sigmoid(z)


class OpeningPrior:
    """按群记一个分数均值，把估计折成**保量**的乘数。随 state.json 持久化。

    只存两个数（均值、样本数）—— 不存任何原文、发言人或消息标识，
    也不进提示词：它是门控的刻度，不是机器人知道的事。

    这套折算不挑分数从哪来：插嘴判断给的 p(join_briefly) 也用它（key="intervention"，
    见 runtime.py）。key 同时是 state.json 里的字段名和留痕字段的前缀。
    """

    def __init__(self, base=POPULATION_BASE, shape_min=SHAPE_MIN,
                 shape_max=SHAPE_MAX, max_groups=512, key="opening"):
        self.key = key
        self.base = float(base)
        self.shape_min = float(shape_min)
        self.shape_max = float(shape_max)
        self.max_groups = int(max_groups)
        self.records = {}

    # ── 持久化：与其它档案同一套 hydrate / dump_into 约定 ──
    def hydrate(self, raw):
        for group, rec in (raw or {}).items():
            if not isinstance(group, str) or not isinstance(rec, dict):
                continue
            mean, count = rec.get("mean"), rec.get("count")
            # 只认真正的 JSON 数值：字符串「看着像数」不收 —— float("0.7")
            # 会悄悄通过，而这些值之后要参与算术，混进字符串迟早在别处炸。
            if isinstance(mean, bool) or not isinstance(mean, (int, float)) \
                    or isinstance(count, bool) or not isinstance(count, int):
                continue
            mean = float(mean)
            if not math.isfinite(mean) or not 0.0 < mean < 1.0 or count < 0:
                continue   # 脏值一律丢回默认，不让坏档案悄悄改门控
            self.records[group] = {"mean": mean, "count": min(count, 10 ** 6),
                                   "ts": _safe_ts(rec.get("ts"))}
        return len(self.records)

    def dump_into(self, data):
        data[self.key] = {g: dict(r) for g, r in self.records.items()}

    def mean_for(self, group):
        rec = self.records.get(group)
        return rec["mean"] if rec else self.base

    def observe(self, group, score, *, now=None):
        """把一条分数折进本群均值。前 32 条算真均值，之后按 1/32 的 EMA 跟随。"""
        if not group or score is None:
            return
        rec = self.records.get(group)
        if rec is None:
            if len(self.records) >= self.max_groups:
                oldest = min(self.records, key=lambda g: self.records[g]["ts"])
                self.records.pop(oldest, None)
            rec = self.records[group] = {"mean": self.base, "count": 0,
                                         "ts": 0.0}
        count = rec["count"]
        alpha = 1.0 / (count + 1) if count < _MEAN_SWITCH else 1.0 / _MEAN_SWITCH
        rec["mean"] = (1.0 - alpha) * rec["mean"] + alpha * float(score)
        rec["mean"] = min(0.999, max(0.001, rec["mean"]))
        rec["count"] = count + 1
        rec["ts"] = time.time() if now is None else float(now)

    def shape(self, group, score):
        """分数 → 插嘴概率的乘数。按本群均值归一，所以长期均值≈1（夹断前）。

        夹断与非稳态都会让它偏离 1 一点点 —— 这是「约等于保量」，不是守恒定律。
        """
        if score is None:
            return 1.0
        mean = self.mean_for(group)
        if mean <= 0.0:
            return 1.0
        return max(self.shape_min, min(self.shape_max, float(score) / mean))

    def assess(self, group, rows, *, now=None):
        """本地节奏分：从最近几条消息算分，再 fold。"""
        return self.fold(group, estimate(rows, now=now), now=now)

    def fold(self, group, score, *, now=None, observe=True):
        """一次搞定：取乘数 → 把分数折进均值。返回留痕用的小字典。

        顺序要紧：乘数用的是**折入当前分数之前**的均值，否则每条消息都在
        跟自己比，乘数会被压平。observe=False 只取乘数、不改均值 —— 给口径
        不同的那部分消息用，免得把均值带偏。
        """
        k = self.key
        mean = self.mean_for(group)
        if score is None:
            return {f"{k}_score": None, f"{k}_shape": 1.0, f"{k}_mean": mean}
        shape = self.shape(group, score)
        if observe:
            self.observe(group, score, now=now)
        return {f"{k}_score": round(score, 4),
                f"{k}_shape": round(shape, 4),
                f"{k}_mean": round(mean, 4)}


def _safe_ts(value):
    try:
        ts = float(value)
    except (TypeError, ValueError):
        return 0.0
    return ts if math.isfinite(ts) and ts >= 0 else 0.0
