"""每轮模型输入的原样记录（2026-11-03，见 docs/self-iteration.md 第 5 节 ①）。

为什么要单独记一份
------------------
以前回放一次事故，现场要从 bot.log、归档、交互日志里手拼：当时的称呼说明、会话历史、
即时提示都拿不到，只能编个近似的。这里在 `bot.get_ai_reply` 入口把一轮的全部变量原样留下：

    data/turns/YYYY-MM-DD.jsonl      当天，追加写
    data/turns/YYYY-MM-DD.jsonl.gz   过了当天就压缩

记录类型：

    kind=reply    一次回复生成：get_ai_reply 的入参原样 + 当时的会话历史 + 模型原始输出。
                  稳定头不存全文（回放时由待测代码现拼），只存哈希和长度。
                  实际发出去的正文在交互日志的 delivery 行里，用 turn_id 关联 ——
                  一轮生成可能排队、被拒、补投，送达结果只有那边说得准。
    kind=triage   一次分诊：decisions.FeedbackTracker.assess 送出去的那份 payload
                  （已经是脱敏后的代号）+ 问了哪几题 + 判出来的选项。
    kind=recall   一次旧账检索：分诊判了 recall=look_up 后，recall.select 的问题、人名词典、
                  时刻、挑中的归档消息编号与分数、承诺候选快照（不重复存归档正文）。

边界（和 interactions.py 同一个口径）：
    - 只进不出：不进提示词、不进摘要、不进召回，模型永远看不到它。
    - 私聊（private=True）不记 —— 私聊原文不为它新开数据流。
    - 尽力而为：写失败只告警，绝不打断接话。
"""

import gzip
import json
import os
import time
import uuid

from archive import day_key
from interactions import InteractionLog

SCHEMA = "regular.turn.v1"

_RESERVED = {"schema", "turn_id", "ts", "kind"}


class TurnLog(InteractionLog):
    """按天分文件的 append-only JSONL；过了当天的文件压成 .gz，超过 keep_days 删掉。"""

    label = "每轮输入记录"

    def append(self, kind, **fields):
        """追加一行，返回写入的 dict（带 turn_id）；未启用 / 写失败返回 None。"""
        if not self.enabled:
            return None
        row = {"schema": SCHEMA, "turn_id": uuid.uuid4().hex, "ts": time.time(), "kind": kind}
        for key, value in fields.items():
            if key not in _RESERVED:
                row[key] = value
        return self._write(row)

    def _ensure_fp(self, day):
        rolled = self._fp is not None and self._fp_day != day
        fp = super()._ensure_fp(day)
        if rolled:
            # 刚跨天：昨天的文件已经关了，马上压（一天 1MB 上下，几十毫秒）
            self._compress_before(day)
        return fp

    def _compress_before(self, today):
        """把 today 之前的 .jsonl 压成 .jsonl.gz。压缩失败就留着原文件，下次再试。"""
        try:
            names = os.listdir(self.log_dir)
        except OSError:
            return
        for name in names:
            if not name.endswith(".jsonl") or name[:-6] >= today:
                continue
            src = os.path.join(self.log_dir, name)
            dst = src + ".gz"
            try:
                with open(src, "rb") as raw, gzip.open(dst + ".tmp", "wb") as packed:
                    while chunk := raw.read(1 << 20):
                        packed.write(chunk)
                os.replace(dst + ".tmp", dst)
                os.remove(src)
            except OSError:
                try:
                    os.remove(dst + ".tmp")
                except OSError:
                    pass

    def _maybe_prune(self, now):
        """每小时一次：补压漏压的旧文件（重启跨过了零点），再删超过保留期的。"""
        if now - self._last_prune < 3600.0:
            return
        self._last_prune = now
        self._compress_before(day_key(now))
        if self.keep_days <= 0:
            return
        cutoff = day_key(now - self.keep_days * 86400.0)
        try:
            for name in os.listdir(self.log_dir):
                day = _day_of(name)
                if day and day < cutoff:
                    try:
                        os.remove(os.path.join(self.log_dir, name))
                    except OSError:
                        pass
        except OSError:
            pass


def _day_of(name):
    for suffix in (".jsonl.gz", ".jsonl"):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return None


# ── 只读：体检、用例、评测都从这里读 ──

def days(log_dir):
    """有记录的日期，升序。"""
    try:
        names = os.listdir(log_dir)
    except OSError:
        return []
    return sorted({d for d in map(_day_of, names) if d})


def read_day(log_dir, day):
    """读一天的记录（明文或压缩都认）；坏行跳过。读不到返回空列表。"""
    out = []
    for name, opener in ((f"{day}.jsonl", open), (f"{day}.jsonl.gz", gzip.open)):
        path = os.path.join(log_dir, name)
        try:
            with opener(path, "rt", encoding="utf-8") as stream:
                for line in stream:
                    try:
                        row = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(row, dict) and row.get("schema") == SCHEMA:
                        out.append(row)
        except (OSError, EOFError):
            continue
    out.sort(key=lambda r: r.get("ts") or 0.0)
    return out


def find(log_dir, turn_id, day=None):
    """按 turn_id 找一行。给了 day 只翻那一天，否则从最近的往回翻。"""
    for d in ([day] if day else reversed(days(log_dir))):
        for row in read_day(log_dir, d):
            if row.get("turn_id") == turn_id:
                return row
    return None
