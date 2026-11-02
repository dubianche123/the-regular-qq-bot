"""插嘴接纳度：把「下一句」当成反馈单位，慢慢学每个人愿不愿意被搭话。

群主 2026-10-31 的原话：「至于反馈，难道下一句是骂你还不够是反馈吗？」
对。所以这里不要任何打分指令 —— 证据只来自原生自然反馈判断
（regular/decisions.py 的 feedback_target / next_move）对**没被点名就接话**
那种发言的解释：被嫌烦记一笔负的，被接着逗记一笔正的。

### 它和已有的那枚暂停窗是什么关系

`FeedbackTracker.quiet()` 是**即时**的：被嫌烦就闭嘴三分钟，纯内存、重启就忘。
这里是**跨天**的那一层：同一个人反复嫌烦，下次就该少主动凑过去；
反复接着逗，就可以多凑一点。两层分工，不互相替代。

### 三件事分开（借 Norn 研究线的区分，见 docs/interaction-observations.md）

个人差异、证据够不够、个性化有没有额外收益，是三件事。落到代码上：

  1. **群体层**：本群所有人的合并接纳率 —— 大家共同的口径。
  2. **个人校准**：每人一个标量，按伪计数向群体层收缩。证据为 0 时
     **恰好等于**群体层，不是「先假设他讨厌机器人」。
  3. **声明门槛**：个人估计只有通过**跨成员置换检验**，才真的允许改变行为；
     否则一律按群体层走，乘数恰为 1.0。

### 声明门槛为什么是置换检验，不是手定的「几条证据 + 差多少」

第一版是手定的：等效证据 ≥ 4 且与群体层差 ≥ 0.15。2026-10-31 用本类本身在
零假设（全群真实接纳度相同）下模拟：每人 4–8 条证据时逐人误报 14–21%，
**55–69% 的群里至少有一个人被错贴了个人乘数**；每人约 40 条才压到 5% 上下。
这正是 Norn 第二步量到的那一类（严格留一 40–47%）；第七步又补了一条：
基于模型的逐人检验，模型设定一有偏就多报，跨用户校准更稳。

所以现在门槛里**没有任何手定差距**：

  - 证据按条存（时间、正负），不再只存累计数；
  - 在同一个群里，把正负标签在**所有成员的所有证据之间打乱**
    （每条证据的时间和归属不动，只换标签），重算每个人与群体层的差；
  - 拿打乱后**全群最大差**的分布当参照（maxT），某人的真实差要超过它的
    1−α 分位才声明。控制的是「全群至少错认一个人」的概率 ≤ α，
    不依赖任何参数模型，只依赖「零假设下标签在成员之间可交换」。

代价是诚实的：群里只有一个人有证据时，没有可比的人，永远不声明；
证据少时几乎不声明。这和 Norn 的结论一致 —— 证据不足时克制。

已知局限：可交换性忽略了时间。全群某一天普遍烦躁、而某人只在那天说过话，
他会被多算一些。按天分层打乱能修，但这个群的证据量下分层之后几乎没有
检验力，所以没做。

半衰期 14 天：它永远是一个**当下**的估计，不是档案。一个人一个月前烦过，
现在不烦了，先验就该跟着回去。这也是 providers.md 里那条边界的实现 ——
即时反馈路径不建立永久的「讨厌机器人」标签。

### 硬边界（刻意收紧）

  - **不进数字**。模型永远看不到这个数。2026-11-02 起插嘴门不再掷骰子（没叫它的
    消息交给它自己定接不接），这里也就不再是乘数：声明门槛过了、读出「这人不爱
    被没叫就搭话」时，那一轮给它交代一句（prompts.PROMPT_BANTER_UNWELCOME）；
    没过门槛什么都不说。
  - **只管没叫它的那几轮**。明确 @、点名、求修正一律不受它影响 ——
    被点名了就该答，这跟他烦不烦主动搭话是两件事。
  - **不碰好感度、不碰心情、不改称呼**。它不是人格描述，也不是惩罚。
  - **不调模型**。证据由原生判断产生，这里只做算术。
"""
import hashlib
import math
import random
import time

from archive import day_key

# ── 群体层的先验：没有任何人任何证据时，群体接纳率就按这个数算 ──
POPULATION_PRIOR = 0.5
POPULATION_PRIOR_WEIGHT = 8.0

# 只有这两个结论算「愿不愿意被主动搭话」的证据。其余结论（continue_normally /
# repair_briefly / no_adjustment）谈的是内容对不对，不是打扰该不该，不计。
POSITIVE_VERDICTS = frozenset(("keep_playful",))
NEGATIVE_VERDICTS = frozenset(("back_off",))

# 证据只取「没被点名就接话」之后的反应：被点名时的反馈说明不了打扰的事。
EVIDENCE_ROUTES = frozenset(("banter",))

_MAX_RECORDS = 3000
# 每人只留最近这么多条。14 天半衰期下，再往前的证据已经很轻，却会拖慢置换。
_MAX_ITEMS = 32
# 单条证据衰减到这个权重以下就在 prune 时删掉（14 天半衰期下约 93 天）。
_PRUNE_WEIGHT = 0.01
# 置换参照的缓存粒度。衰减让权重慢慢变，一小时内的变化可以忽略。
_CACHE_SECONDS = 3600


class Receptiveness:
    """每群每人一份接纳度证据 + 回忆冷却台账。随 state.json 持久化。

    每条证据只存两个数（时间、正负），不存原文、不存判断理由。
    """

    def __init__(self, *, prior_weight=4.0, half_life_days=14.0,
                 min_evidence=4.0, alpha=0.05, permutations=200, gain=1.5,
                 multiplier_min=0.5, multiplier_max=1.5,
                 callback_cooldown=21600.0, callback_max_per_day=2,
                 max_records=_MAX_RECORDS):
        self.prior_weight = float(prior_weight)
        self.half_life_days = float(half_life_days)
        self.min_evidence = float(min_evidence)
        self.alpha = float(alpha)
        self.permutations = int(permutations)
        self.gain = float(gain)
        self.multiplier_min = float(multiplier_min)
        self.multiplier_max = float(multiplier_max)
        self.callback_cooldown = float(callback_cooldown)
        self.callback_max_per_day = int(callback_max_per_day)
        self.max_records = int(max_records)
        self.records = {}      # "group|member" -> {"items": [[ts, 0|1], ...]}，按时间升序
        self.callbacks = {}    # "group|member" -> 上次端出旧话的时间
        self.quota = {}        # group -> {"day","count"}
        self._versions = {}    # group -> 证据变更计数；只在内存里，让置换缓存失效
        self._null_cache = {}  # group -> ((版本, 小时), {member: p})

    # ── 键与持久化 ──
    @staticmethod
    def _key(group, member):
        return f"{group}|{member}"

    def hydrate(self, raw):
        """坏档案一律当缺失处理 —— 它只是门控的刻度，不值得拖垮启动。

        只收逐条格式 {"items": [[ts, 0|1], ...]}。第一版的累计格式
        {"n","s","ts"} 没有逐条标签，没法参与置换，直接不收
        （换格式时还没有任何真实档案是那个格式）。
        """
        raw = raw if isinstance(raw, dict) else {}
        for key, rec in _mapping(raw.get("records")).items():
            if not isinstance(key, str) or "|" not in key \
                    or not isinstance(rec, dict):
                continue
            items = _items(rec.get("items"))
            if items:
                self.records[key] = {"items": items[-_MAX_ITEMS:]}
        for key, ts in _mapping(raw.get("callbacks")).items():
            value = _num(ts)
            if isinstance(key, str) and "|" in key and value is not None \
                    and value >= 0:
                self.callbacks[key] = value
        for group, row in _mapping(raw.get("quota")).items():
            if not isinstance(group, str) or not isinstance(row, dict):
                continue
            count = _num(row.get("count"))
            if isinstance(row.get("day"), str) and count is not None \
                    and count >= 0:
                self.quota[group] = {"day": row["day"], "count": int(count)}
        self._null_cache.clear()
        return len(self.records)

    def dump_into(self, data):
        data["receptive"] = {
            "records": {k: {"items": [list(it) for it in v["items"]]}
                        for k, v in self.records.items()},
            "callbacks": dict(self.callbacks),
            "quota": {g: dict(r) for g, r in self.quota.items()},
        }

    # ── 衰减 ──
    def _decay_factor(self, ts, now):
        if self.half_life_days <= 0 or ts <= 0:
            return 1.0
        elapsed = max(0.0, now - ts)
        return 0.5 ** (elapsed / (self.half_life_days * 86400.0))

    def _decayed(self, rec, now):
        """只读地取衰减后的 (等效条数, 正向权重)。不改档案。"""
        n = s = 0.0
        for ts, label in rec["items"]:
            weight = self._decay_factor(ts, now)
            n += weight
            s += weight * label
        return n, s

    # ── 群体层 ──
    def population(self, group, now=None):
        """本群合并接纳率：所有人的衰减后证据汇总，再向 0.5 收缩。"""
        now = time.time() if now is None else float(now)
        prefix = f"{group}|"
        total_n = total_s = 0.0
        for key, rec in self.records.items():
            if key.startswith(prefix):
                n, s = self._decayed(rec, now)
                total_n += n
                total_s += s
        return ((total_s + POPULATION_PRIOR_WEIGHT * POPULATION_PRIOR)
                / (total_n + POPULATION_PRIOR_WEIGHT))

    # ── 声明门槛：跨成员置换 ──
    def _p_values(self, group, now):
        """{成员: p}，只含等效证据够 min_evidence 的人。

        零假设：本群每条证据的正负与「是谁说的」无关。打乱标签（时间与归属不动），
        统计量取合格成员里的 max|个人 − 群体|，
        p = (1 + 打乱后最大差 ≥ 本人真实差的次数) / (B + 1)。
        随机种子由证据内容决定：同样的证据永远得到同样的结论，重启也一样。
        """
        key = (self._versions.get(group, 0), int(now // _CACHE_SECONDS))
        hit = self._null_cache.get(group)
        if hit is not None and hit[0] == key:
            return hit[1]
        prefix = f"{group}|"
        names, owners, weights, labels = [], [], [], []
        digest = hashlib.sha256(group.encode("utf-8"))
        for rkey in sorted(k for k in self.records if k.startswith(prefix)):
            idx = len(names)
            names.append(rkey[len(prefix):])
            for ts, label in self.records[rkey]["items"]:
                owners.append(idx)
                weights.append(self._decay_factor(ts, now))
                labels.append(label)
                digest.update(f"{rkey}:{ts!r}:{label};".encode("utf-8"))
        n_i = [0.0] * len(names)
        for owner, weight in zip(owners, weights):
            n_i[owner] += weight
        eligible = [i for i, n in enumerate(n_i) if n >= self.min_evidence]
        result = {}
        if eligible:
            total_n = sum(n_i)
            k = self.prior_weight

            def deviations(picked, picked_are_ones):
                acc = [0.0] * len(n_i)
                tot = 0.0
                for j in picked:
                    acc[owners[j]] += weights[j]
                    tot += weights[j]
                if picked_are_ones:
                    s_i, total_s = acc, tot
                else:
                    s_i = [n - a for n, a in zip(n_i, acc)]
                    total_s = total_n - tot
                pop = ((total_s + POPULATION_PRIOR_WEIGHT * POPULATION_PRIOR)
                       / (total_n + POPULATION_PRIOR_WEIGHT))
                return [abs((s_i[i] + k * pop) / (n_i[i] + k) - pop)
                        for i in eligible]

            ones = [j for j, label in enumerate(labels) if label]
            observed = deviations(ones, True)
            # 只抽少数那一边：标签是 0/1，打乱等价于「哪几条是正的」
            use_ones = len(ones) <= len(labels) - len(ones)
            size = len(ones) if use_ones else len(labels) - len(ones)
            pool = range(len(labels))
            rng = random.Random(int.from_bytes(digest.digest()[:8], "big"))
            exceed = [0] * len(eligible)
            for _ in range(self.permutations):
                worst = max(deviations(rng.sample(pool, size), use_ones))
                for j, d in enumerate(observed):
                    if worst >= d - 1e-12:
                        exceed[j] += 1
            result = {names[i]: (1 + exceed[j]) / (self.permutations + 1)
                      for j, i in enumerate(eligible)}
        self._null_cache[group] = (key, result)
        return result

    # ── 读：个人估计 + 声明门槛 + 乘数 ──
    def assess(self, group, member, now=None):
        """返回留痕用的小字典。`multiplier` 是唯一会影响行为的那个数。

        没有有效证据，或置换检验不支持差异时，multiplier 恰为 1.0 ——
        也就是「照群体口径办」，不是「保守一点」。
        """
        now = time.time() if now is None else float(now)
        pop = self.population(group, now=now)
        rec = self.records.get(self._key(group, member))
        if rec is None:
            n = s = 0.0
        else:
            n, s = self._decayed(rec, now)
        personal = (s + self.prior_weight * pop) / (n + self.prior_weight)
        p = self._p_values(group, now).get(member) if rec is not None else None
        claimed = p is not None and p <= self.alpha
        if claimed:
            multiplier = max(self.multiplier_min,
                             min(self.multiplier_max,
                                 1.0 + self.gain * (personal - pop)))
        else:
            multiplier = 1.0
        return {"receptive_n": round(n, 3),
                "receptive_personal": round(personal, 4),
                "receptive_population": round(pop, 4),
                "receptive_p": None if p is None else round(p, 4),
                "receptive_claimed": bool(claimed),
                "receptive_multiplier": round(multiplier, 4)}

    def multiplier(self, group, member, now=None):
        return self.assess(group, member, now=now)["receptive_multiplier"]

    # ── 写：一次自然反馈 ──
    def record(self, group, member, verdict, *, route, now=None):
        """折进一笔证据。返回 True 表示真的记了一笔。

        只认主动插嘴之后的 keep_playful / back_off；别的路由与结论一律不计，
        也不「顺便」记成中性 —— 没有证据就是没有证据，不要把 0 当成 0.5。
        """
        if not group or not member:
            return False
        if route not in EVIDENCE_ROUTES:
            return False
        if verdict in POSITIVE_VERDICTS:
            label = 1
        elif verdict in NEGATIVE_VERDICTS:
            label = 0
        else:
            return False
        now = time.time() if now is None else float(now)
        key = self._key(group, member)
        rec = self.records.get(key)
        if rec is None:
            if len(self.records) >= self.max_records:
                oldest = min(self.records,
                             key=lambda k: self.records[k]["items"][-1][0])
                self.records.pop(oldest, None)
                self._null_cache.clear()
            rec = self.records[key] = {"items": []}
        items = rec["items"]
        items.append([now, label])
        if len(items) > 1 and items[-2][0] > now:
            items.sort(key=lambda it: it[0])
        del items[:-_MAX_ITEMS]
        self._versions[group] = self._versions.get(group, 0) + 1
        return True

    # ── 回忆冷却：「居然记得」要稀，才稀奇 ──
    def callback_allowed(self, group, member, now=None):
        """能不能在这次插嘴里端出一句旧话。每人冷却 + 每群每天上限。"""
        if not group or not member:
            return False
        if self.callback_max_per_day <= 0:
            return False
        now = time.time() if now is None else float(now)
        last = self.callbacks.get(self._key(group, member), 0.0)
        if now - last < self.callback_cooldown:
            return False
        row = self.quota.get(group)
        if row and row["day"] == day_key(now) \
                and row["count"] >= self.callback_max_per_day:
            return False
        return True

    def mark_callback(self, group, member, now=None):
        """真的端出去之后才登记 —— 判断说「先别」不消耗额度。"""
        if not group or not member:
            return
        now = time.time() if now is None else float(now)
        self.callbacks[self._key(group, member)] = now
        today = day_key(now)
        row = self.quota.get(group)
        if row is None or row["day"] != today:
            self.quota[group] = {"day": today, "count": 1}
        else:
            row["count"] += 1

    def prune(self, now=None):
        """证据衰减到看不见就删掉：不留一地几乎为零的旧档案。"""
        now = time.time() if now is None else float(now)
        changed = False
        for key in list(self.records):
            items = self.records[key]["items"]
            kept = [it for it in items
                    if self._decay_factor(it[0], now) >= _PRUNE_WEIGHT]
            if len(kept) == len(items):
                continue
            changed = True
            if kept:
                self.records[key]["items"] = kept
            else:
                self.records.pop(key)
        if changed:
            self._null_cache.clear()
        for key in [k for k, ts in self.callbacks.items()
                    if now - ts > max(self.callback_cooldown * 8, 86400.0)]:
            self.callbacks.pop(key, None)
        today = day_key(now)
        for group in [g for g, row in self.quota.items()
                      if row["day"] != today]:
            self.quota.pop(group, None)


def _mapping(value):
    return value if isinstance(value, dict) else {}


def _num(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return out if math.isfinite(out) else None


def _items(value):
    """逐条证据的严格解析：任何一条脏了，整份档案都不收 —— 不许靠半条伪造接纳度。"""
    if not isinstance(value, list) or not value:
        return None
    out = []
    for item in value:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            return None
        ts, label = _num(item[0]), item[1]
        if ts is None or ts < 0:
            return None
        if isinstance(label, bool) or not isinstance(label, (int, float)) \
                or label not in (0, 1):
            return None
        out.append([ts, int(label)])
    out.sort(key=lambda it: it[0])
    return out
