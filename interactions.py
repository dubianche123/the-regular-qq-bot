"""本地交互观察日志：收到的消息、门控决策与实际送达，按天 JSONL 追加。

定位（2026-11 观察底座）：它和 archive.py 的「原始消息归档」是两本账 ——
归档只记**人**说了什么，是摘要/召回的证据来源；这里记**系统**做了什么
（门控放没放行、回出去的字到底是什么），是行为研究底稿。

边界（刻意收紧，别越写越多）：
    - 只进不出：这份日志不进提示词、不进摘要、不进召回，模型永远看不到它。
    - 不碰归档：assistant 输出、决策事件都不许写进 archive（人的证据账）。
    - 不保证送达语义：append+flush 是尽力而为，崩溃丢最后一行属正常；
      不写 fsync、不承诺 exactly-once，写入失败只告警、绝不打断正常接话。
    - 记录 ≠ 收益：攒下这些行只是能复盘的原料，不构成任何个性化效果的证据。
"""

import json
import logging
import os
import time
import uuid
from archive import day_key

logger = logging.getLogger("qqbot")

SCHEMA = "regular.interaction.v1"

# 追加行里不许被调用方字段覆盖的保留键。
_RESERVED = {"schema", "event_id", "ts", "kind", "group", "user"}

class InteractionLog:
    """按天分文件的 append-only JSONL 日志。

    目录在首次追加时才创建，路径不可写只会换来告警，绝不拖垮正常启动。
    （2026-11-01 以前还有「反馈 合适/不合适」命令和绑定它用的近期索引、重启恢复；
    27 天里一次没人用过，判断层的自然反馈早就替了它，一起删了。）
    """

    def __init__(self, log_dir, *, enabled=True, keep_days=0):
        self.log_dir = log_dir
        self.enabled = bool(enabled)
        self.keep_days = max(0, int(keep_days))
        self._fp = None
        self._fp_day = None
        self._dir_ready = False
        self._last_prune = 0.0

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

    # ── 保留 ──
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
