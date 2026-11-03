"""按需旧账召回 —— 只在对方明确问起过去时，从本地归档取 1~2 条。

为什么不每轮注入：2026-10-06 的对照实验里，只要旧事材料在眼前，模型就会长出
「回忆尾巴」——哪怕没人问。现在长期记忆只喂人物名册，旧事由「翻旧账」命令按需查。
这里补的是同一思路的自动入口：高精度触发、只读本地归档（0 token）、只取少量，
并放进本轮最末的动态层，绝不碰稳定头和每日记忆。

触发条件刻意收紧：必须出现「上次 / 之前 / 答应过 / 还记得」这类往事线索，
并且能从中提取出人名或具体内容词。只有线索没有内容（「你还记得吗」）不查；
命中敏感词的查询和片段直接放弃 —— 宁可不召回，也不把旧雷翻出来。

检索策略本身也是可迭代对象（docs/self-iteration.md「体检与检索策略的自迭代」）：
打分权重、时效、条数等数值集中在 DEFAULT_POLICY，`regular tune recall` 拿检索用例
离线搜更好的一组，交维护者 `regular policy apply` 后写进 data/policies/recall.json。
搜索只动有界数值和开关、不加词表；敏感词、指令行、机器编号这些硬边界不在策略里，搜不掉。
"""

import hashlib
import json
import logging
import os
import re
import time

import config
import wordfilter

logger = logging.getLogger("qqbot")

RECALL_MARK = "【旧账检索"
HEADER = f"""{RECALL_MARK}（系统按你的问题从本地归档翻到的旧消息）】
只在对方问的正是这件事时拿它核对；对不上就忽略。不要主动提旧事、不要复述整段，
也不要用「我记得你以前」起头。引用时对准说话人，别把别人的事安到眼前这位头上。】"""

# 往事虚词：抽检索词之前先剥掉 —— 「上次」「之前」本身不是要找的内容。
# 2026-10-31 起它们**不再决定要不要翻旧账**：那是分诊里 recall 那一题的事
# （decisions.FeedbackTracker.assess），调用方判断过才会来这里取证据。
# 真实语料里点名消息上命中线索词的 5 条全是误触发（「之前那个模型更有趣」
# 「我叫王红闻你就要答应」），判断全判对了。
RECALL_CUES = (
    "上次", "上回", "那天", "当时", "那次", "之前", "以前", "后来",
    "还记得", "记不记得", "你说过", "跟我说过", "答应过", "答应", "承诺",
    "不是说好", "说好", "兑现", "翻旧账", "旧账", "谁说的", "哪天", "多久了",
    "来着",
)

# 先剥掉这些短语再抽内容词，避免「奶茶的事」被切成「奶茶事」而搜不到。
STOP_PHRASES = (
    "那个", "这个", "那些", "这些", "什么", "怎么", "为什么", "哪个", "哪里",
    "事情", "事儿", "东西", "时候", "一下", "一个", "一件", "有没有", "是不是",
    "能不能", "要不要", "可不可以", "帮我", "给我", "告诉我", "知道", "记得",
    "的事", "那件事", "这件事", "什么事",
)
PARTICLES = frozenset("的了是在有和跟与就都也还很太我你他她它谁哪这那")
HAN_RUN = re.compile(r"[\u4e00-\u9fff]+")
WORD_RUN = re.compile(r"[A-Za-z0-9]{2,12}")
QUOTED = re.compile(r"[「“\"《]([^」”\"》]{2,12})[」”\"》]")
MACHINE_ID = re.compile(r"<@!?[0-9A-Fa-f]{16,}>|(?<![0-9A-Fa-f])[0-9A-Fa-f]{16,}(?![0-9A-Fa-f])")

# ── 检索策略（可离线调参的数值；默认值就是 2026-11-04 之前写死的那一套）──
# max_items / lookback_days 为 None 时跟 .env 的 MEMORY_RECALL_MAX_ITEMS / _LOOKBACK_DAYS 走。
DEFAULT_POLICY = {
    "ngram_max": 4,                 # 中文内容词切到几字
    "min_text_chars": 4,            # 短于这么多字的旧消息不算证据
    "term_base": 8,                 # 命中具体内容词的底分
    "name_bonus": 2,                # 内容词命中时，再沾到问题里点名的人加分
    "extra_term_cap": 3,            # 多命中的内容词最多再加几分
    "require_term_when_terms": True,  # 问题里有内容词时，只沾人名的消息不算
    "name_only_min_chars": 12,      # 只有人名时，消息至少这么长才像一件事
    "name_only_base": 3,
    "name_only_cap": 2,
    "fresh_days": 7, "fresh_bonus": 2,
    "warm_days": 14, "warm_bonus": 1,
    "long_text_chars": 15, "long_bonus": 1,
    "min_score": 1,                 # 低于这个分的不进候选（1 = 有分就算）
    "distinct_senders_first": True,  # 先挑不同说话人
    "max_items": None,
    "lookback_days": None,
    "body_chars": 80,               # 每条证据最多给模型看多少字
}

# 调参器的搜索空间：每个可调项给一组候选值。范围就是允许的边界 ——
# 策略文件里超出这组值范围的数一律拒收（见 validate_policy），自动搜索不能把边界放宽。
POLICY_SPACE = {
    "ngram_max": (3, 4, 5),
    "min_text_chars": (2, 4, 6, 8),
    "term_base": (4, 6, 8, 10),
    "name_bonus": (0, 1, 2, 3, 4),
    "extra_term_cap": (0, 1, 2, 3, 5),
    "require_term_when_terms": (True, False),
    "name_only_min_chars": (8, 10, 12, 16, 20),
    "name_only_base": (1, 2, 3, 4, 5),
    "name_only_cap": (0, 1, 2, 3),
    "fresh_days": (3, 7, 14),
    "fresh_bonus": (0, 1, 2, 3),
    "warm_days": (7, 14, 30),
    "warm_bonus": (0, 1, 2),
    "long_text_chars": (10, 15, 20, 30),
    "long_bonus": (0, 1, 2),
    "min_score": (1, 4, 6, 8, 9, 10, 11, 12),
    "distinct_senders_first": (True, False),
    "max_items": (None, 1, 2, 3),
    "lookback_days": (None, 7, 14, 30, 60, 90),
    "body_chars": (60, 80, 120),
}


def validate_policy(raw):
    """把一份策略（dict）规整成完整策略；未知键、类型不对、超出 POLICY_SPACE 范围的值抛 ValueError。"""
    if not isinstance(raw, dict):
        raise ValueError("recall policy must be a JSON object")
    out = dict(DEFAULT_POLICY)
    for key, value in raw.items():
        if key not in DEFAULT_POLICY:
            raise ValueError(f"unknown recall policy key: {key!r}")
        space = POLICY_SPACE[key]
        if value is None:
            if None not in space:
                raise ValueError(f"recall policy {key} cannot be null")
        elif isinstance(DEFAULT_POLICY[key], bool) or all(isinstance(v, bool) for v in space):
            if not isinstance(value, bool):
                raise ValueError(f"recall policy {key} must be true/false")
        else:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"recall policy {key} must be a number")
            if key in {"ngram_max", "min_text_chars", "extra_term_cap", "name_only_min_chars",
                       "name_only_cap", "long_text_chars", "max_items", "body_chars"}:
                if not isinstance(value, int):
                    raise ValueError(f"recall policy {key} must be an integer")
            nums = [v for v in space if v is not None and not isinstance(v, bool)]
            if not min(nums) <= value <= max(nums):
                raise ValueError(f"recall policy {key}={value} outside [{min(nums)}, {max(nums)}]")
        out[key] = value
    return out


def policy_sha(policy):
    """策略的短指纹，写进每轮检索记录，回头知道当时用的是哪一套。"""
    blob = json.dumps(policy, ensure_ascii=False, sort_keys=True, default=list)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:10]


_POLICY_CACHE = {"key": None, "policy": None}


def active_policy(path=None):
    """线上生效的策略：data/policies/recall.json（维护者 `regular policy apply` 写的）覆盖默认值。

    按文件 mtime 缓存，跟知识库一样改完下一轮生效。文件坏了或越界就退回默认值并告警 ——
    策略文件出错不能让旧账召回整个停掉，更不能带着越界的数跑。
    """
    path = path or getattr(config, "RECALL_POLICY_FILE", "")
    try:
        stat = os.stat(path) if path else None
    except OSError:
        stat = None
    key = (path, stat.st_mtime_ns, stat.st_size) if stat else None
    if key is not None and _POLICY_CACHE["key"] == key:
        return _POLICY_CACHE["policy"]
    policy = dict(DEFAULT_POLICY)
    if stat is not None:
        try:
            with open(path, encoding="utf-8") as stream:
                doc = json.load(stream)
            policy = validate_policy(doc.get("policy", doc) if isinstance(doc, dict) else doc)
        except (OSError, ValueError) as exc:
            logger.warning("旧账召回策略文件不可用，退回默认值: %s", exc)
            policy = dict(DEFAULT_POLICY)
    _POLICY_CACHE.update(key=key, policy=policy)
    return policy


def _strip_phrases(text, phrases):
    for phrase in sorted(phrases, key=len, reverse=True):
        text = text.replace(phrase, " ")
    return text


def extract_terms(text, names=(), ngram_max=4):
    """从问题里抽内容词；中文取 2~ngram_max 字 n-gram，英文/数字取整段。

    人名不放进内容词集合：它在 _score 里单独计分。否则「阿强」本身会同时
    算作名字命中和内容命中，一条只提到名字的短消息会把真正相关的旧账挤掉。
    回放语料保留无关行，不依赖这里的分词提前筛选候选。
    """
    text = text or ""
    terms = set()
    for match in QUOTED.finditer(text):
        phrase = match.group(1).strip()
        if len(phrase) >= 2:
            terms.add(phrase)

    cleaned = _strip_phrases(text, RECALL_CUES)
    cleaned = _strip_phrases(cleaned, STOP_PHRASES)
    for name in sorted(names, key=len, reverse=True):
        if name:
            cleaned = cleaned.replace(name, " ")
    cleaned = "".join(" " if ch in PARTICLES else ch for ch in cleaned)
    top = max(2, int(ngram_max))
    for run in HAN_RUN.findall(cleaned):
        if len(run) < 2:
            continue
        for size in range(2, min(top, len(run)) + 1):
            for start in range(len(run) - size + 1):
                terms.add(run[start:start + size])
    for run in WORD_RUN.findall(cleaned):
        terms.add(run.lower())
    return {term for term in terms if len(term) >= 2}


def _score(row, terms, names, now, policy=None):
    """命中人名/内容词才计分；太短、敏感、指令行直接放弃。权重见 DEFAULT_POLICY。"""
    p = policy or DEFAULT_POLICY
    text = str(row.get("text") or "").strip()
    if len(text) < p["min_text_chars"] or text == "[图片]" or row.get("cmd"):
        return 0
    if wordfilter.has_hit(text):
        return 0
    name_hits = sum(1 for name in names if name and name in text)
    lowered = text.lower()
    term_hits = sum(1 for term in terms if term in lowered)
    if not name_hits and not term_hits:
        return 0
    # 问题里有具体内容词时，只沾了人名、没沾那件事的消息不算证据；
    # 否则「阿强上次说的奶茶」会把同一个人最近的无关发言也带进来。
    if terms and not term_hits and p["require_term_when_terms"]:
        return 0
    if term_hits:
        # 具体内容词命中才是主证据；人名只加分，不能盖过「奶茶」本身。
        score = (p["term_base"] + (p["name_bonus"] if name_hits else 0)
                 + min(max(term_hits - 1, 0), p["extra_term_cap"]))
    else:
        # 只有人名时只收较长、像一件事的消息，避免「阿强：草」抢占名额。
        if len(text) < p["name_only_min_chars"]:
            return 0
        score = p["name_only_base"] + min(name_hits, p["name_only_cap"])
    age_days = max(0.0, (now - float(row.get("ts") or 0)) / 86400.0)
    if age_days <= p["fresh_days"]:
        score += p["fresh_bonus"]
    elif age_days <= p["warm_days"]:
        score += p["warm_bonus"]
    if len(text) >= p["long_text_chars"]:
        score += p["long_bonus"]
    return score if score >= max(1, p["min_score"]) else 0


def _pick(rows, terms, names, now, max_items, exclude_text="", policy=None):
    """返回 [(score, row)]，按分数、时间倒序，最多 max_items 条。"""
    p = policy or DEFAULT_POLICY
    scored = []
    seen_ids = set()
    for row in rows or []:
        text = str(row.get("text") or "").strip()
        if not text or text == exclude_text:
            continue
        message_id = str(row.get("message_id") or "")
        if message_id and message_id in seen_ids:
            continue
        score = _score(row, terms, names, now, p)
        if not score:
            continue
        if message_id:
            seen_ids.add(message_id)
        scored.append((score, float(row.get("ts") or 0), row))
    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)

    # 优先不同说话人；同一句旧账被一个人连刷几条时别占满名额。
    picked, senders = [], set()
    if p["distinct_senders_first"]:
        for score_value, _ts, row in scored:
            if len(picked) >= max_items:
                break
            sender = str(row.get("sender") or "")
            if sender and sender in senders:
                continue
            picked.append((score_value, row))
            senders.add(sender)
    if len(picked) < max_items:
        ids = {id(row) for _s, row in picked}
        for score_value, _ts, row in scored:
            if len(picked) >= max_items:
                break
            if id(row) not in ids:
                picked.append((score_value, row))
                ids.add(id(row))
    return picked[:max_items]


def _promise_pick(promises, group_id, text, terms, *, rows=None):
    if rows is None:
        if promises is None:
            return None
        try:
            rows = promises.list(group_id)
        except Exception:
            return None
    best, best_score = None, 0
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        who = str(row.get("who") or "")
        what = str(row.get("what") or "")
        if not what:
            continue
        score = 0
        if who and who in text:
            score += 3
        if any(term and (term in what or term in who) for term in terms):
            score += 2
        if score > best_score:
            best, best_score = row, score
    return best


def _promise_line(promises, group_id, text, terms):
    best = _promise_pick(promises, group_id, text, terms)
    return _render_promise(best) if best else ""


def _render_promise(best):
    due = str(best.get("due_text") or "没说时间")
    return (f"- 承诺：{best.get('who') or '有人'} 说过「{best.get('what')}」"
            f"（{due}）")


def select(group_id, text, *, archive=None, rows=None, promises=None, names=(), now=None,
           max_items=None, lookback_days=None, exclude_text="", policy=None):
    """「翻的话拿什么」的纯检索部分：返回结构化的选择结果，不拼提示词。

    rows 给了就在这份行里挑（检索用例回放、调参器用），否则从 archive 读回看窗口。
    返回 {"picked": [{"score", "row"}], "promise": dict|None, "terms": [...],
          "query_names": [...], "policy": {...}, "skipped": 原因或 ""}。
    """
    p = validate_policy(policy) if policy is not None else active_policy()
    out = {"picked": [], "promise": None, "terms": [], "query_names": [], "policy": p,
           "skipped": "", "promises": [], "promises_complete": promises is None,
           "defaults": {"max_items": config.MEMORY_RECALL_MAX_ITEMS,
                        "lookback_days": config.MEMORY_RECALL_LOOKBACK_DAYS}}
    text = (text or "").strip()
    if not text or wordfilter.has_hit(text):
        out["skipped"] = "sensitive_or_empty_query"
        return out

    names = tuple(name for name in names if name and len(name) >= 2)
    # 词典里的人名只用来切词；真正参与打分的必须是**这条问题里点到的人**，
    # 否则「你还记得吗」也会因为旧账里出现任意熟人而被召回。
    query_names = tuple(name for name in names if name in text)
    terms = extract_terms(text, names, ngram_max=p["ngram_max"])
    out["terms"], out["query_names"] = sorted(terms), list(query_names)
    if not terms and not query_names:
        out["skipped"] = "no_terms"
        return out
    now = time.time() if now is None else now
    if max_items is None:
        max_items = p["max_items"] if p["max_items"] is not None else config.MEMORY_RECALL_MAX_ITEMS
    if lookback_days is None:
        lookback_days = (p["lookback_days"] if p["lookback_days"] is not None
                         else config.MEMORY_RECALL_LOOKBACK_DAYS)
    if max_items <= 0:
        out["skipped"] = "max_items_zero"
        return out
    since = now - max(1, lookback_days) * 86400.0
    if rows is None:
        try:
            rows = archive.search(group_id, "", since_ts=since, limit=3000)
        except Exception as exc:
            logger.warning("旧账召回读取归档失败: %s", type(exc).__name__)
            out["skipped"] = "archive_error"
            return out
    else:
        rows = [r for r in rows if since <= float(r.get("ts") or 0) <= now][:3000]

    promise_rows = []
    out["promises_complete"] = True
    if promises is not None:
        try:
            promise_rows = [dict(row) for row in promises.list(group_id) or ()
                            if isinstance(row, dict)]
        except Exception:
            out["promises_complete"] = False
    out["promises"] = [{k: row.get(k, "") for k in ("who", "what", "due_text")}
                       for row in promise_rows]
    out["source_limit_reached"] = len(rows) >= 3000
    promise = _promise_pick(None, group_id, text, terms, rows=promise_rows)
    if promise:
        out["promise"] = promise
        max_items -= 1
    out["picked"] = [{"score": s, "row": row}
                     for s, row in _pick(rows, terms, query_names, now, max_items,
                                         exclude_text=exclude_text, policy=p)]
    return out


def render(selection, *, name_of=None, now=None):
    """把 select 的结果拼成本轮动态层的召回块；什么都没挑到返回空串。"""
    p = selection.get("policy") or DEFAULT_POLICY
    now = time.time() if now is None else now
    lines = []
    if selection.get("promise"):
        lines.append(_render_promise(selection["promise"]))
    limit = int(p["body_chars"])
    for item in selection.get("picked") or ():
        row = item["row"]
        body = " ".join(str(row.get("text") or "").split())
        body = MACHINE_ID.sub("（群友编号）", body)
        if not body:
            continue
        if len(body) > limit:
            body = body[:limit] + "…"
        sender = str(row.get("sender") or "")
        who = (name_of(sender) if name_of else None) or "一位群友"
        when = time.strftime("%m-%d %H:%M",
                             time.localtime(float(row.get("ts") or now)))
        lines.append(f"- {when} {who}：{body}")
    if not lines:
        return ""
    return HEADER.strip() + "\n" + "\n".join(lines)


def trace(selection):
    """检索记录里存的那份摘要：挑了哪些消息、各多少分、用的哪套策略 —— 不存正文（正文在归档里）。"""
    return {
        "picked": [{"message_id": item["row"].get("message_id"),
                    "ts": item["row"].get("ts"), "score": item["score"]}
                   for item in selection.get("picked") or ()],
        "promise": (selection.get("promise") or {}).get("what") if selection.get("promise") else None,
        "promise_record": ({k: selection["promise"].get(k, "")
                            for k in ("who", "what", "due_text")}
                           if selection.get("promise") else None),
        "promises": list(selection.get("promises") or ()),
        "promises_complete": selection.get("promises_complete", False),
        "defaults": dict(selection.get("defaults") or {}),
        "policy": dict(selection.get("policy") or DEFAULT_POLICY),
        "source_limit_reached": bool(selection.get("source_limit_reached")),
        "skipped": selection.get("skipped") or "",
        "policy_sha": policy_sha(selection.get("policy") or DEFAULT_POLICY),
    }


def build_note(group_id, text, *, archive, promises=None, names=(), name_of=None,
               now=None, max_items=None, lookback_days=None, exclude_text="", policy=None):
    """返回可直接塞进本轮动态层的召回块；没有可靠命中时返回空串。

    **要不要翻**不在这里判：调用方（bot 的分诊结果 recall == look_up）已经判过。
    这里只管「翻的话拿什么」—— 没有具体内容词、命中敏感词、太老、是指令行，都不给。
    """
    if not config.MEMORY_RECALL_ENABLED:
        return ""
    selection = select(group_id, text, archive=archive, promises=promises, names=names,
                       now=now, max_items=max_items, lookback_days=lookback_days,
                       exclude_text=exclude_text, policy=policy)
    return render(selection, name_of=name_of, now=now)
