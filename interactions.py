"""本地交互观察日志：收到的消息、门控决策、实际送达与显式反馈，按天 JSONL 追加。

定位（2026-11 观察底座）：它和 archive.py 的「原始消息归档」是两本账 ——
归档只记**人**说了什么，是摘要/召回的证据来源；这里记**系统**做了什么
（门控放没放行、回出去的字到底是什么、有没有人按「反馈」打分），是行为研究底稿。

边界（刻意收紧，别越写越多）：
    - 只进不出：这份日志不进提示词、不进摘要、不进召回，模型永远看不到它。
    - 不碰归档：assistant 输出、决策事件、反馈都不许写进 archive（人的证据账）。
    - 不保证送达语义：append+flush 是尽力而为，崩溃丢最后一行属正常；
      不写 fsync、不承诺 exactly-once，写入失败只告警、绝不打断正常接话。
    - 记录 ≠ 收益：攒下这些行只是能复盘的原料，不构成任何个性化效果的证据。
"""

import json
import logging
import math
import os
import time
import uuid
from archive import day_key

logger = logging.getLogger("qqbot")

SCHEMA = "regular.interaction.v1"

# 「反馈」命令的受控词表：只认这一小撮字面值，不做自然语言情绪猜测。
FEEDBACK_LABELS = {"合适": "appropriate", "不合适": "inappropriate", "不确定": "unsure"}
FEEDBACK_DIMENSIONS = {"整体": "overall", "时机": "timing", "内容": "content", "语气": "tone"}
_LABEL_VALUES = frozenset(FEEDBACK_LABELS.values())
_DIMENSION_VALUES = frozenset(FEEDBACK_DIMENSIONS.values())

# 追加行里不许被调用方字段覆盖的保留键。
_RESERVED = {"schema", "event_id", "ts", "kind", "group", "user"}

# 只有 AI 会话回复可被反馈绑定：本地应答与主动广播即使伪造字段也不算数。
_ELIGIBLE_ROUTES = frozenset(("reply", "banter"))


def parse_feedback(text):
    """解析「反馈 <标签> [维度]」命令。返回 None = 不是反馈命令，原样走闲聊。

    首 token 不是字面「反馈」的一律 None；是的话只认极小语法 —
    `反馈 合适/不合适/不确定 [整体/时机/内容/语气]`，其余形态返回 {"valid": False}
    让调用方回用法提示，绝不猜用户想说什么。
    """
    parts = (text or "").strip().split()
    if not parts or parts[0] != "反馈":
        return None
    if len(parts) not in (2, 3):
        return {"valid": False}
    label = FEEDBACK_LABELS.get(parts[1])
    if label is None:
        return {"valid": False}
    if len(parts) == 3:
        dimension = FEEDBACK_DIMENSIONS.get(parts[2])
        if dimension is None:
            return {"valid": False}
    else:
        dimension = "overall"
    return {"valid": True, "label": label, "dimension": dimension}


def _valid_ts(ts, now):
    """真实 JSON 数值时间戳（int/float，bool 不算），有限且不超前。

    只认数值类型：字符串「看着像数」也不收 —— 存进来的原值之后还要和
    数值时间戳比大小，字符串混进来会在比较时炸掉恢复流程。
    """
    if isinstance(ts, bool) or not isinstance(ts, (int, float)):
        return False
    try:
        t = float(ts)
    except (TypeError, ValueError, OverflowError):
        return False
    return math.isfinite(t) and t <= now


def _eligible_delivery(row, now):
    """一行能不能进「最近可反馈目标」索引。append 与重启恢复共用同一判定：

    我们自己 schema 的、assistant 发出的、确认为 delivered 的、显式标记
    feedback_eligible 的 AI 会话回复（reply/banter），且 group/user/
    source_message_id/event_id 都是非空字符串、时间戳有效不超前。
    """
    if not isinstance(row, dict):
        return False
    if (row.get("schema") != SCHEMA or row.get("kind") != "delivery"
            or row.get("actor") != "assistant" or row.get("status") != "delivered"
            or row.get("feedback_eligible") is not True):
        return False
    for key in ("group", "user", "source_message_id", "event_id", "route"):
        if not isinstance(row.get(key), str) or not row[key]:
            return False
    # membership 只在字符串校验之后做：JSON 列表/字典进 in 判断会抛 TypeError
    return (row["route"] in _ELIGIBLE_ROUTES
            and _valid_ts(row.get("ts"), now))


def _valid_feedback_row(row, now):
    """恢复时对反馈行做同样的最小结构校验：形状不齐/词表外的值一律跳过。"""
    if not isinstance(row, dict):
        return False
    if (row.get("schema") != SCHEMA or row.get("kind") != "feedback"
            or row.get("actor") != "user"):
        return False
    for key in ("group", "user", "reply_event_id", "event_id",
                "label", "dimension"):
        if not isinstance(row.get(key), str) or not row[key]:
            return False
    if (row["label"] not in _LABEL_VALUES
            or row["dimension"] not in _DIMENSION_VALUES):
        return False
    supersedes = row.get("supersedes")
    if supersedes is not None and not isinstance(supersedes, str):
        return False
    return _valid_ts(row.get("ts"), now)


class InteractionLog:
    """按天分文件的 append-only JSONL 日志 + 反馈绑定用的近期小索引。

    索引只覆盖「最近一次反馈窗口」：latest_reply 只看最近一条 eligible 的
    delivered 行；重启后从反馈窗口覆盖到的日期文件里恢复（跨午夜不丢）。
    坏行/截断行跳过；不扫旧归档、不做合成回填。目录在首次追加时才创建，
    路径不可写只会换来告警，绝不拖垮正常启动。
    """

    def __init__(self, log_dir, *, enabled=True, keep_days=0, feedback_max_age=1800.0):
        self.log_dir = log_dir
        self.enabled = bool(enabled)
        self.keep_days = max(0, int(keep_days))
        self.feedback_max_age = float(feedback_max_age)
        self._fp = None
        self._fp_day = None
        self._dir_ready = False
        self._last_prune = 0.0
        # (group, user) -> 最近一次可反馈的送达 {event_id, ts, source_message_id}
        self._latest = {}
        # (group, user, reply_event_id, dimension) -> 该维度最新反馈 event_id
        self._feedback_latest = {}
        if self.enabled:
            self._recover()

    # ── 写入 ──
    def _file_for(self, day):
        return os.path.join(self.log_dir, f"{day}.jsonl")

    def _ensure_fp(self, day):
        if self._fp is None or self._fp_day != day:
            self.close()
            if not self._dir_ready:
                os.makedirs(self.log_dir, exist_ok=True)   # 惰性创建：失败走上层告警
                self._dir_ready = True
            path = self._file_for(day)
            # 崩溃可能留下没有换行的半行 JSON：追加前先补一个分隔换行。
            # 只往文件尾加字节，绝不回写已有内容。
            try:
                if os.path.getsize(path) > 0:
                    with open(path, "rb") as probe:
                        probe.seek(-1, os.SEEK_END)
                        if probe.read(1) != b"\n":
                            with open(path, "ab") as fixer:
                                fixer.write(b"\n")
            except OSError:
                pass   # 修不了就照常继续：写入失败由 append 统一兜底
            self._fp = open(path, "a", encoding="utf-8")
            self._fp_day = day
        return self._fp

    def append(self, kind, group, user=None, **fields):
        """追加一行。返回写入的 dict；写入失败/未启用返回 None，绝不抛给接话路径。

        user 是这条事件**关联到谁**，不等于行文的作者 —— 谁是作者看 actor。
        调用方给的字段不能覆盖 schema/event_id/ts/kind/group/user 这些保留键。
        索引只在落盘成功后更新，写不进去的行不许留下「能绑定的目标」。
        """
        if not self.enabled:
            return None
        row = {"schema": SCHEMA, "event_id": uuid.uuid4().hex, "ts": time.time(),
               "kind": kind, "group": group, "user": user}
        for key, value in fields.items():
            if key not in _RESERVED:
                row[key] = value
        try:
            fp = self._ensure_fp(day_key(row["ts"]))
            fp.write(json.dumps(row, ensure_ascii=False) + "\n")
            fp.flush()
        except (OSError, TypeError, ValueError, UnicodeError):
            logger.warning("交互日志写入失败（%s），本条放弃，不影响正常接话", kind)
            return None
        if _eligible_delivery(row, row["ts"]):
            key = (row["group"], row["user"])
            prev = self._latest.get(key)
            if prev is None or row["ts"] > prev["ts"]:
                self._latest[key] = {
                    "event_id": row["event_id"], "ts": row["ts"],
                    "source_message_id": row.get("source_message_id")}
        self._maybe_prune(row["ts"])
        return row

    def close(self):
        if self._fp is not None:
            try:
                self._fp.close()
            except OSError:
                pass
            self._fp = None
            self._fp_day = None

    # ── 反馈绑定 ──
    def latest_reply(self, group, user, now=None):
        """最近一条可被反馈的目标：本群 + 本人 + 成功送达的 AI 会话回复，且在时间窗内。"""
        if not self.enabled:
            return None
        now = time.time() if now is None else now
        rec = self._latest.get((group, user))
        if not rec:
            return None
        ts = rec["ts"]
        if ts > now or now - ts > self.feedback_max_age:
            return None
        return rec

    def feedback(self, group, user, source_message_id, label, dimension="overall",
                 now=None):
        """把一次显式反馈绑到 latest_reply 上。返回追加行；无目标/写失败/词表外值 None。

        修订链按「同一条回复 + 同一个维度」算：合适/时机是两次互不覆盖的评价，
        只有同一个维度的第二次打分才会 supersedes 上一次同维度打分。
        """
        if not self.enabled:
            return None
        if label not in _LABEL_VALUES or dimension not in _DIMENSION_VALUES:
            return None
        target = self.latest_reply(group, user, now=now)
        if target is None:
            return None
        key = (group, user, target["event_id"], dimension)
        row = self.append("feedback", group, user=user, actor="user",
                          source_message_id=source_message_id,
                          reply_event_id=target["event_id"],
                          label=label, dimension=dimension,
                          supersedes=self._feedback_latest.get(key))
        if row is not None:
            self._feedback_latest[key] = row["event_id"]
        return row

    # ── 恢复与保留 ──
    def _recover(self):
        """从反馈窗口覆盖到的日期文件里重建索引（跨午夜的窗口也算在内）。

        只读文件名落在 [now - feedback_max_age, now] 日期区间内的 .jsonl；
        未来日期、非法文件名直接跳过。目录不可读只告警，不拖垮启动。
        """
        now = time.time()
        start, end = day_key(now - self.feedback_max_age), day_key(now)
        try:
            names = os.listdir(self.log_dir)
        except FileNotFoundError:
            return   # 首次运行还没有目录 —— 正常，等第一次追加再建
        except OSError:
            logger.warning("交互日志目录不可读，反馈索引从空开始（%s）", self.log_dir)
            return
        for name in sorted(names):
            if not name.endswith(".jsonl"):
                continue
            day = name[:-6]
            if not (start <= day <= end):   # 窗口外/非法/未来日期文件名跳过
                continue
            try:
                with open(self._file_for(day), "rb") as fp:
                    for raw_line in fp:
                        # 二进制读 + 逐行解码：崩溃截断的 UTF-8 尾部只丢那一行，
                        # 不会让 UnicodeDecodeError 中断整个恢复或吞掉前面的好行。
                        try:
                            row = json.loads(raw_line.decode("utf-8"))
                        except (ValueError, TypeError):
                            continue  # 截断/坏行跳过，不让半行挡住启动
                        if _eligible_delivery(row, now):
                            key = (row["group"], row["user"])
                            prev = self._latest.get(key)
                            if prev is None or row["ts"] > prev["ts"]:
                                self._latest[key] = {
                                    "event_id": row["event_id"],
                                    "ts": row["ts"],
                                    "source_message_id":
                                        row.get("source_message_id")}
                        elif _valid_feedback_row(row, now):
                            self._feedback_latest[(
                                row["group"], row["user"],
                                row["reply_event_id"],
                                row["dimension"])] = row["event_id"]
            except OSError:
                continue

    def _maybe_prune(self, now):
        """与原始归档同一个保留天数；0 = 永不清理。"""
        if self.keep_days <= 0 or now - self._last_prune < 3600.0:
            return
        self._last_prune = now
        cutoff = day_key(now - self.keep_days * 86400.0)
        try:
            for name in os.listdir(self.log_dir):
                if name.endswith(".jsonl") and name[:-6] < cutoff:
                    try:
                        os.remove(self._file_for(name[:-6]))
                    except OSError:
                        pass
        except OSError:
            pass
