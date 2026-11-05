"""有界的原生 Decisions 产品助手：反馈理解、插嘴仲裁、记忆去留。

刻意保持「小而钝」：没有工具循环、没有插件、没有向量库；
判断只产生**本机参考动作**（安静一会儿 / 补一句修正 / 这条原话留多久），
绝不写关系档案、不发任何未经主链路确认的消息。
"""
import json
import math
import time

import config
import digest
import naming
import providers
import wordfilter
from recall import MACHINE_ID
from regular import decision_prompts

# ── 原生答案解析 ──

_DURABLE_KINDS = {"preference", "fact", "shared_moment", "correction"}
_DURABLE_ACTIONS = {"retain_quote", "replace_old"}
_TEXT_SLICE = 600
_MAX_ALIASES = 6
_ALIAS_CHARS = 40


def bot_aliases():
    """机器人在这个群里的叫法。判断**必须**知道这个。

    2026-10-31 用 U 盘真实语料实测（归档 803–808，整份记录里最干净的一句喊停
    「小王闭嘴」）：payload 不给名字时，判断答 feedback_target=another_person
    （p=0.72）—— 它以为在说第三个人，于是代码把动作压成 no_adjustment，
    暂停窗根本装不上，机器人会继续插嘴。加上这个字段之后翻成 bot_response
    （p=0.59），back_off 置信度 0.66 → 0.93。

    为什么声明它不泄露任何东西：这些名字本来就写在群友的原话里，抹不掉 ——
    那是人家说的话。真正要护住的是群号、openid 和消息 ID，那些照旧逐个抹掉。
    """
    out = []
    for name in naming.bot_names():
        name = str(name or "").strip()[:_ALIAS_CHARS]
        if (name and name not in out and not wordfilter.has_hit(name)
                and not MACHINE_ID.search(name)):
            out.append(name)
        if len(out) >= _MAX_ALIASES:
            break
    return out


def _with_aliases(payload):
    """给 payload 补上机器人的叫法；拿不到就不加这个键（判断那边也不要求它）。"""
    aliases = bot_aliases()
    if aliases:
        payload["bot_is_addressed_as"] = aliases
    return payload




def _bound(text, slice_chars=_TEXT_SLICE):
    """每条进入 API 的文本封顶：头尾各留一段，中间用省略号标记。"""
    text = str(text or "")
    if len(text) <= 2 * slice_chars:
        return text
    return text[:slice_chars] + " …[截断]… " + text[-slice_chars:]


def _sanitize(text, known=()):
    """本地脱敏：先把「已知的群号/openid/消息 ID」逐个抹掉，再补机器 ID
    形态兜底；命中敏感词的材料整条不进 API。known 只收非空白字符串。"""
    text = _bound(text)
    for ident in known:
        if isinstance(ident, str) and ident.strip() and ident in text:
            text = text.replace(ident, "[已略]")
    text = MACHINE_ID.sub("[已略]", text)
    return "" if wordfilter.has_hit(text) else text


def _vocab(questions):
    """{题名: 该题声明过的选项集合}。"""
    wanted = {}
    for question in questions or []:
        if not isinstance(question, dict):
            continue
        name = question.get("name")
        choices = [c.get("value") for c in question.get("choices") or []
                   if isinstance(c, dict)]
        if isinstance(name, str) and name:
            wanted[name] = set(v for v in choices if isinstance(v, str) and v)
    return wanted


def _unit(value):
    """[0,1] 内的有限数值；布尔不算。"""
    return (not isinstance(value, bool) and isinstance(value, (int, float))
            and 0 <= value <= 1                 # 先挡天文数字
            and math.isfinite(float(value)))


_INVALID_REASONS = frozenset({
    "missing", "refusal", "invalid_type", "invalid_choice",
    "invalid_number", "invalid_probabilities",
})


def _answer_row_with_reason(answer, allowed):
    """保留原有校验，同时只返回固定枚举诊断，不保留回答或拒答正文。"""
    answer_type = answer.get("type")
    if answer_type != "choice":
        return None, "refusal" if answer_type == "refusal" else "invalid_type"
    choice = answer.get("choice")
    if not isinstance(choice, str) or choice not in allowed:
        return None, "invalid_choice"
    for key in ("confidence", "probability"):
        if key in answer and not _unit(answer[key]):
            return None, "invalid_number"
    probs = answer.get("probabilities")
    if probs is not None:
        if not isinstance(probs, list):
            return None, "invalid_probabilities"
        seen_values = set()
        for entry in probs:
            if not isinstance(entry, dict):
                return None, "invalid_probabilities"
            value = entry.get("value")
            if (not isinstance(value, str) or value not in allowed
                    or value in seen_values):
                return None, "invalid_probabilities"
            seen_values.add(value)
            if not _unit(entry.get("probability")):
                return None, "invalid_probabilities"
    table = _prob_map(probs)
    return {"choice": choice,
            "confidence": answer.get("confidence"),
            "probabilities": table,
            # 被选中项自己的概率：离线校准要拿它和真实结果比。
            # 不能从 margin 反推 —— 只有 K=2 时才解得出。
            "p": (table or {}).get(choice),
            "margin": _margin(choice, probs)}, None


def _answer_row(answer, allowed):
    """单条答案的严格校验；保持原有返回接口。"""
    return _answer_row_with_reason(answer, allowed)[0]


def parse_each(body, questions, *, invalid_reasons=None):
    """逐题解析：{题名: 答案行 或 None}；**整份返回结构坏了**才整体返回 None。

    两种坏法分开对待（2026-10-31，一次分诊会同时带好几道题）：
      · 结构坏 —— answers 不是数组、元素不是字典、题名不认识或重复：
        这份返回整体不可信，None。
      · 单题坏 —— refusal、词表外的选项、概率越界、漏答：只作废**那一题**。
        比如起名那道题碰到政治人名的谐音时被拒答，不该连累同一次调用里
        「是不是在叫它」「要不要收敛」的答案。
    """
    if not isinstance(body, dict):
        return None
    answers = body.get("answers")
    if not isinstance(answers, list):
        return None
    wanted = _vocab(questions)
    out = {name: None for name in wanted}
    if invalid_reasons is not None:
        invalid_reasons.clear()
        invalid_reasons.update({name: "missing" for name in wanted})
    seen = set()
    for answer in answers:
        if not isinstance(answer, dict):
            return None
        name = answer.get("name")
        if not isinstance(name, str) or name not in wanted or name in seen:
            return None
        seen.add(name)
        out[name], reason = _answer_row_with_reason(answer, wanted[name])
        if invalid_reasons is not None:
            if reason is None:
                invalid_reasons.pop(name, None)
            else:
                invalid_reasons[name] = reason
    return out


def parse_answers(body, questions):
    """全有或全无：任何一题不合规就整体 None。要逐题容错用 parse_each。

    每个请求过的问题名必须恰好有一条 type=choice 的同名答案，值必须是
    该题声明过的选项；重名/缺名/多答/词表外/refusal 一律作废。
    confidence/probability 若出现必须是 [0,1] 有限数值；probabilities
    是官方返回的**数组** [{"value":..., "probability":...}]，元素必须
    是字典、value 必须属于该题词表且不重复、概率同样受限。
    这里不做任何阈值判断 —— 数值只是留档，不参与决策。
    """
    each = parse_each(body, questions)
    if each is None or any(row is None for row in each.values()):
        return None
    return each


def _prob_map(probs):
    """{选项: 概率}；没给 probabilities 就是 None（不编造）。"""
    if not probs:
        return None
    return {entry["value"]: float(entry["probability"]) for entry in probs
            if isinstance(entry.get("probability"), (int, float))
            and not isinstance(entry.get("probability"), bool)}


def _margin(choice, probs):
    """被选中那项比第二名高多少。没有可比的第二项就返回 None。

    和官方 `confidence` 的关系（2026-10-31 核对真实返回 + arXiv:2609.29429
    附录 E）：`confidence` 是 **(K·p_max − 1)/(K−1)**，K 是选项数 ——
    只依赖最大概率，不看第二名是谁。四条真实回答逐条吻合
    （K=4 → 0.560、K=5 → 0.725、K=3 → 0.445、K=2 → 0.960）。

    ⚠️ 二选一时 (2·p_max − 1) 恰好等于「前二之差」，所以在 K=2 上两者重合 ——
    早先拿 200 条二选一样本得出的「confidence 就是前二之差」是**过度推广**，
    K≥3 时两者明显不同（上面 K=5 那条：0.725 对 0.640）。

    这里仍然用**前二之差**而不是 `confidence`，是刻意的：`confidence` 是 p_max
    的单调变换，按它设阈值等于按绝对概率设阈值 —— 而绝对概率的刻度正是不能用的
    那一样（ECE 0.22、Platt 斜率 0.21）。前二之差是相对量。
    代价要讲清楚：K≥3 时「前二之差 vs confidence 哪个更会挑错」我**没有测**，
    论文也没比过这两者。见 docs/decision-calibration.md。
    """
    table = _prob_map(probs)
    if not table or choice not in table or len(table) < 2:
        return None
    mine = table[choice]
    return mine - max(v for k, v in table.items() if k != choice)


def parse_choices(body, questions):
    """只要选项名的薄包装；数值要用就走 parse_answers。"""
    answers = parse_answers(body, questions)
    if answers is None:
        return None
    return {name: row["choice"] for name, row in answers.items()}


# 个别题目不过弃答闸：错放和错拦的代价差得太远，偏向哪边就听哪边。写在这里而不是调用处，
# 回放（regular/replay_worker.py）重跑同一题时用的也是同一道闸。
QUESTION_MIN_MARGIN = {
    "draw_check": 0.0,   # 错放一张是封号，错拦一张只是少画一张（见 check_drawing）
}


def usable_choices(body, questions, min_margin=None):
    """解析 + **弃答闸**：边际太小的那一题整条丢掉，让调用方退回基线。

    返回 (choices, scores)。choices 里缺某一题 = 这一题这次不可用；
    各调用方对「缺」的处理一律是保守的（不装暂停窗、不插嘴、不端旧话、
    不改记忆），所以丢掉永远比猜一个安全。scores 留档用，含被丢掉的那些。

    为什么用**边际**而不是绝对概率（2026-10-31 实测，200 条带客观标签样本）：
      · 概率的刻度不能用 —— ECE 0.22、Platt 斜率 0.21（logit 需压缩约五倍），
        所以「p > 0.7 才行动」这种阈值无从设起；边际是**无标度**的。
      · 弃掉边际 < 0.3 的那 41%，它们的挑对率是 0.512 —— 就是掷硬币。
        留下的从 0.575 升到 0.619。
      · 但**越自信不等于越准**：阈值 0.70 时挑对率 0.559，反而低于不弃答。
        所以这是一道**地板**，不是一条越高越好的曲线，更不是行动许可。
    """
    invalid_reasons = {}
    answers = parse_each(body, questions, invalid_reasons=invalid_reasons)
    if answers is None:
        return None, {}
    default_floor = config.DECISION_MIN_MARGIN if min_margin is None else min_margin
    scores, choices = {}, {}
    for name, row in answers.items():
        if row is None:
            scores[name] = {"invalid": True, "invalid_reason": invalid_reasons[name]}
            continue
        margin = row["margin"]
        scores[name] = {"choice": row["choice"], "margin": margin,
                        "confidence": row["confidence"], "p": row["p"],
                        "probabilities": row["probabilities"]}
        floor = (QUESTION_MIN_MARGIN.get(name, default_floor) if min_margin is None
                 else default_floor)
        if margin is not None and floor > 0 and margin < floor:
            scores[name]["dropped"] = True
            continue
        choices[name] = row["choice"]
    return choices, scores


# ── 反馈观察：只记「确认送达的模型回复」的短命缓存 ──

def _alias_map(senders):
    """按批次内出现顺序给每个真实发言人一个稳定的本地代号。"""
    return {sender: f"member_{chr(ord('a') + i)}"
            for i, sender in enumerate(dict.fromkeys(s for s in senders if s))}


class FeedbackTracker:
    """按群一份的临时观察：最近一条真正发出去的模型回复 + 几条人类上下文。

    纯内存、有界、不落盘：不写关系档案、不读日志、重启就空 ——
    宁可重启后少判几次，也不拿不可信的旧记录硬凑。
    """

    def __init__(self, window_seconds, quiet_seconds, max_groups=128, log=None, turns=None):
        self.window_seconds = float(window_seconds)
        self.quiet_seconds = float(quiet_seconds)
        self.max_groups = int(max_groups)
        # 只进不出的本地观察日志。可以是一个 duck-typed 的 .append 对象，
        # **也可以是一个返回它的无参可调用** —— runtime 传的是后者。
        # 为什么必须允许晚绑定（2026-10-31 实测踩到）：在构造时把单例对象捕获住，
        # 用例 patch 掉 runtime.INTERACTIONS 就再也改不到这里，于是测试的
        # decision_scores 行被写进了**部署目录**的真实日志。
        # 这和 runtime.py 开头那条「别 from runtime import OWNER_OPENID」
        # 是同一个坑。这里**不** import runtime 或 interactions —— runtime
        # 构造本对象，反向依赖会成环。
        self._log = log
        # 每轮输入记录（turns.TurnLog）：分诊看到的那份 payload 原样留下，分诊改动才回放得了。
        # 同样晚绑定，理由同上。
        self._turns = turns
        self._groups = {}        # group -> {"reply": {...}|None, "quiet_until": ts}
        self._order = []         # LRU：最近碰过的排到末尾，满了先挤掉最旧的

    def _resolve_log(self):
        """每次用时才解析，不在构造时把单例钉死（见 __init__ 的说明）。"""
        log = self._log
        return log() if callable(log) else log

    def _record_triage(self, group, user, message_id, payload, asked, choices, stale):
        """分诊的输入和结论进每轮输入记录。payload 已是脱敏代号；写失败由 TurnLog 兜着。"""
        turns = self._turns() if callable(self._turns) else self._turns
        if turns is None:
            return
        turns.append("triage", group=group, user=user, message_id=message_id,
                     payload=payload, asked=asked, choices=choices, stale=stale)

    def _log_scores(self, group, site, scores, **extra):
        """把这次判断的数值落进本地观察日志。只进不出：它永远不进提示词。

        2026-10-31 之前这些数值在解析完就被丢掉了（`parse_choices` 只取选项名），
        于是「以后拿真实结果去校准产品判断」永远缺原料。现在逐题留档，
        包括被弃答闸丢掉的那些 —— 丢掉本身就是要复盘的事。
        """
        log = self._resolve_log()
        if log is None or not scores:
            return
        _log_scores(log, group, site, scores, **extra)

    def _slot(self, group):
        slot = self._groups.get(group)
        if slot is not None and group in self._order:
            self._order.remove(group)
        if slot is None:
            slot = {"reply": None, "quiet_until": 0.0}
            self._groups[group] = slot
        self._order.append(group)
        if len(self._order) > self.max_groups:
            self._groups.pop(self._order.pop(0), None)
        return slot

    def remember(self, group, user, text, *, route, outgoing_id, history=(),
                 now=None):
        """登记一条**确认送达**的模型回复（调用方已过滤本地应答/失败投递）。"""
        if not group:
            return
        now = time.time() if now is None else now
        human = []
        for row in list(history)[-4:]:
            if isinstance(row, dict) and row.get("text"):
                # sender/message_id 只留在本地内存里给别名词表用，绝不进 API。
                human.append({"sender": row.get("sender"),
                              "message_id": row.get("message_id"),
                              "text": _bound(row["text"], 240),
                              "ts": row.get("time")})
        self._slot(group)["reply"] = {
            "user": user, "text": text, "route": route,
            "outgoing_id": outgoing_id, "ts": now, "history": human}

    def quiet(self, group, *, now=None):
        """原生反馈判过「收敛一下」之后，插嘴暂停窗内还有没有剩。"""
        if not config.DECISION_ENABLED:
            return False
        now = time.time() if now is None else now
        return now < (self._groups.get(group) or {}).get("quiet_until", 0.0)

    @staticmethod
    def _known_ids(group, reply, history, extra=()):
        known = {group}
        if reply:
            known.update((reply.get("user"), reply.get("outgoing_id")))
        for row in history:
            if isinstance(row, dict):
                known.update((row.get("sender"), row.get("message_id")))
        known.update(extra)
        return known

    def _input_rows(self, history, aliases, known):
        rows = []
        for row in list(history)[-4:]:
            text = _sanitize(row.get("text"), known)
            if not text:
                continue
            rows.append({"speaker": aliases.get(row.get("sender"), "member"),
                         "text": text})
        return rows

    async def assess(self, group, user, text, *, message_id="", is_direct=False,
                     named=False, history=None, nick_candidate=None,
                     want_recall=False, want_lookup=False, now=None):
        """一条人类消息的**分诊**：本机先决定要问哪几题，然后最多一次原生调用。

        为什么是一次调用带一串题（2026-10-31）：这个接口按输入计费、输出不要钱，
        而几道题看的是同一段对话 —— 题目近乎免费，贵的是把对话送过去那一次。
        所以每条消息只建一份 payload，按本机已知的事实挂上用得着的题：

          · 机器人刚说过话（窗口内）  → 反馈两题（冲谁说的 / 下一步）
          · 正文里有它的名字、但不是平台 @ → addressed：answer / quip / let_it_pass
          · 被 @ 或被点名、且没有窗口内的回复 → next_move：只用来判断要不要收敛语气
          · 本轮会正经回复（被 @ / 点名）且开了召回 → recall：要不要翻本群旧账
          · 同上且配了联网查询 → lookup：要不要先上网查（search），或明确要它深挖（research）
          · 本地起名解析提出了候选 → nickname：adopt / refuse / not_a_request

        没叫它的消息接不接，2026-11-02 起由小王自己在回复里定（bot.reply_unprompted），
        这里不再问「插不插嘴」「要不要接着回」—— 跟它无关又不在反馈窗口里的消息一题都不问。

        一题都用不上就返回 {}（0 调用）。发起过就记 decision_attempted=True，
        即使请求失败也不许为同一条消息再发一次。`asked` 记下这次实际问了哪些题。

        私聊不走这里：私聊原文是最私密的那一路，不为它新开一条第三方数据流。
        """
        if not config.DECISION_ENABLED:
            return {}
        now = time.time() if now is None else now
        slot = self._groups.get(group) or {}
        reply = slot.get("reply")
        if reply and now - reply["ts"] > self.window_seconds:
            reply = None
        calls_bot = bool(is_direct or named)
        if reply and not is_direct and self.quiet(group, now=now):
            # 暂停窗内不再评反馈；但被点名的、要起名的照样要判。
            if not (named or nick_candidate):
                return {}
            reply = None
        questions = []
        if reply:
            questions += decision_prompts.feedback_questions()
        elif calls_bot:
            questions += [q for q in decision_prompts.feedback_questions()
                          if q["name"] == "next_move"]
        if named and not is_direct:
            questions += decision_prompts.addressed_questions()
        if calls_bot and want_recall:
            questions += decision_prompts.recall_questions()
        if calls_bot and want_lookup:
            questions += decision_prompts.lookup_questions()
        # 仅与对机器人说话或跟进它的回复有关时问；与分诊共用一次调用。
        if calls_bot or reply:
            questions += decision_prompts.request_kind_questions()
        if (calls_bot or reply) and config.REMINDER_ENABLED:
            questions += decision_prompts.reminder_request_questions()
        # 对已有 bot 回复，原历史是该回复的证据；但群友可能在它之后、当前句之前
        # 连发了几条。调用方提供的是接收当前消息时的快照，按时间再排除旧句和未来句。
        # 前后两段分开，避免把“回复之后的发言”错放在 bot 回复之前。
        history = [row for row in (history or ()) if isinstance(row, dict)]
        after_reply = []
        if reply:
            for row in history:
                try:
                    ts = float(row.get("time"))
                except (TypeError, ValueError):
                    continue
                if (math.isfinite(ts) and reply["ts"] < ts < now
                        and now - ts <= self.window_seconds
                        and (not message_id or row.get("message_id") != message_id)):
                    after_reply.append(row)
            after_reply = after_reply[-4:]
        rows_source = list(reply["history"]) if reply else history
        known = self._known_ids(group, reply, rows_source + after_reply, (user, message_id))
        # 候选命中本地词表：底线已经拦了，不必再问，也不把它送出去。
        candidate = _sanitize(nick_candidate, known) if nick_candidate else ""
        if candidate:
            questions += decision_prompts.nickname_questions()
        if not questions:
            return {}

        # 保留登记回复时的历史，另附 bot 回复后的消息，匿名代号在两段中一致。
        senders = ([row.get("sender") for row in rows_source + after_reply if isinstance(row, dict)]
                   + ([reply["user"]] if reply else []) + [user])
        aliases = _alias_map(senders)
        current = _sanitize(text, known)
        if not current:
            return {}
        payload = {
            "recent_human_messages": self._input_rows(rows_source, aliases, known),
            "latest_message": {"speaker": aliases.get(user, "member"),
                               "text": current},
            # 「这句叫到了它」（@ 或正文里的名字）。不是「欠它一句」—— 那是 addressed 那题的事。
            # 喊名字也必须算 directed：反馈判断在这上面实测过（「小王闭嘴」判成冲它说的
            # 概率 0.96 对 directed=false 时的 0.67，见 docs/when-to-intervene.md）。
            "latest_is_directed": calls_bot,
        }
        if reply:
            bot_text = _sanitize(reply["text"], known)
            if not bot_text:
                return {}
            payload["bot_reply"] = {
                "route": ("invited_reply" if reply["route"] == "reply"
                          else "unsolicited_interjection"),
                "reply_to": (aliases.get(reply["user"], "member")
                             if reply["user"] else "group"),
                "text": bot_text}
            if after_reply:
                payload["messages_after_bot_reply"] = self._input_rows(after_reply, aliases, known)
        if candidate:
            payload["nickname_candidate"] = candidate
        _with_aliases(payload)
        # source_route 让调用方知道这次反馈是在回应「被点名的接话」还是
        # 「没人请的插嘴」—— 只有后者才算「愿不愿意被主动搭话」的证据。
        result = {"decision_attempted": True,
                  "asked": sorted(q["name"] for q in questions)}
        if reply:
            result.update(source_reply_id=reply["outgoing_id"],
                          source_route=reply["route"])
        body = await providers.call_decision(
            json.dumps(payload, ensure_ascii=False), questions)
        # 等结果期间这条回复已被新的送达顶掉：机会已过时，只给保守的安静动作，
        # 不写暂停窗、不给修正话术。分诊里与那条回复无关的题照常作数。
        current_reply = (self._groups.get(group) or {}).get("reply")
        stale = bool(reply) and current_reply is not reply
        choices, scores = usable_choices(body, questions)
        self._log_scores(group, "feedback" if reply else "triage", scores,
                         user=user, source_message_id=message_id,
                         **({"source_reply_id": reply["outgoing_id"]}
                            if reply else {}))
        self._record_triage(group, user, message_id, payload, result["asked"],
                            choices, stale)
        choices = choices or {}
        result.update(addressed=choices.get("addressed"),
                      nickname=choices.get("nickname"),
                      recall=choices.get("recall"),
                      lookup=choices.get("lookup"),
                      reminder_request=choices.get("reminder_request"))
        # 只用「低真心」的一侧；必须确实选了 playful、三项概率完整且有可比边际。
        # 题面来自本地实验；线上语境和样本不同，不把它当成真实意图的定论。
        request_score = scores.get("request_kind") or {}
        probs = request_score.get("probabilities") or {}
        if (not stale and choices.get("request_kind") == "playful"
                and request_score.get("margin") is not None
                and request_score["margin"] >= 0
                and set(probs) == {"sincere", "playful", "not_request"}
                and abs(sum(probs.values()) - 1) <= 0.01
                and probs["sincere"] < 0.1):
            result["playful_request"] = True
        if stale:
            result.update(target=None, suggested_move=None,
                          action="stay_quiet", back_off=False)
            return result
        if not reply:
            result.update(target=None, suggested_move=choices.get("next_move"),
                          action=None,
                          back_off=choices.get("next_move") == "back_off")
            return result
        target = choices.get("feedback_target")
        action = choices.get("next_move")
        # 不是冲着 bot 的，就一律不动作：不许拿「别人被开玩笑」的低置信
        # keep_playful 当成继续逗的许可（实测踩过）。
        if target != "bot_response":
            action = "no_adjustment"
        # 收敛优先：有效 back_off 装上暂停窗，窗内没叫它的消息一律不问小王。
        if target == "bot_response" and action == "back_off":
            self._slot(group)["quiet_until"] = now + self.quiet_seconds
        result.update(target=target, suggested_move=choices.get("next_move"),
                      action=action, back_off=action == "back_off")
        # 点名接话时，分诊已看到最近一条回复；即使反馈题拿不准，也将原句作为
        # 中性语境交回，否则跨人续谈时当前说话人的 session 根本看不到它。
        # 明确是针对另一人的反馈时不借用；这不改变反馈判定或新增回复资格。
        if (target == "bot_response" or
                (named and choices.get("addressed") == "answer"
                 and target in (None, "unclear", "not_feedback"))):
            if reply["user"] is None:
                reply_to = "group"
            elif reply["user"] == user:
                reply_to = "current_member"
            else:
                reply_to = "another_member"
            result["bot_reply_text"] = payload["bot_reply"]["text"]
            result["bot_reply_to"] = reply_to
        return result

    async def consider_callback(self, group, user, *, quote, quote_ts,
                                history=(), now=None, source_message_id=""):
        """问一次：现在把这句旧话端出来，是「居然记得」还是「翻档案」。

        返回 True / False / None（None = 判断不可用，调用方按**不端出**处理 ——
        新增的惊喜不许靠失败来兜底；失败只是回到原来那个不翻旧话的插嘴）。
        这一问只在插嘴仲裁已经放行、本地话题闸已经命中、冷却与每日额度都还有
        余量时才发起，所以一天最多问两次（见 receptive.Receptiveness 的额度）。
        """
        if not config.DECISION_ENABLED or not quote:
            return None
        now = time.time() if now is None else now
        history = [row for row in (history or []) if isinstance(row, dict)]
        aliases = _alias_map([row.get("sender") for row in history] + [user])
        known = self._known_ids(group, None, history, (user,))
        rows = self._input_rows(history, aliases, known)
        if not rows:
            return None
        safe_quote = _sanitize(quote, known)
        if not safe_quote:
            return None
        try:
            days = max(0.0, (float(now) - float(quote_ts)) / 86400.0)
        except (TypeError, ValueError):
            return None
        payload = {"recent_human_messages": rows,
                   "speaker": aliases.get(user, "member"),
                   "earlier_quote": {"text": safe_quote,
                                     "said_by": aliases.get(user, "member"),
                                     "days_ago": round(days, 2)}}
        _with_aliases(payload)
        questions = decision_prompts.callback_questions()
        body = await providers.call_decision(
            json.dumps(payload, ensure_ascii=False), questions)
        choices, scores = usable_choices(body, questions)
        self._log_scores(group, "callback", scores, user=user,
                         source_message_id=source_message_id)
        if choices is None or "callback" not in choices:
            return None   # 不可用（含被弃答闸丢掉）≠ 判过了说「先别」
        return choices["callback"] == "bring_it_up"

    async def check_drawing(self, group, user, spec, *, history=(), message_id=""):
        """〔画〕之前问一次：这张画合起来，加上前几句是怎么要的，是不是在影射政治（见 DRAW_QUESTIONS）。

        返回 True（画）/ False（不画）/ None（判断不可用，调用方照常画 —— 人设里的规则和
        生图审核还在，判断接口挂了不该连带所有画图一起停）。
        不过弃答闸（QUESTION_MIN_MARGIN）：错放一张的代价是封号，错拦一张只是少画一张，判断偏向「不画」就听它的。
        """
        if not config.DECISION_ENABLED or not spec:
            return None
        history = [row for row in (history or []) if isinstance(row, dict)]
        aliases = _alias_map([row.get("sender") for row in history] + [user])
        known = self._known_ids(group, None, history, (user,))
        payload = {"image_spec": _sanitize(spec, known),
                   "recent_human_messages": self._input_rows(history, aliases, known),
                   "requester": aliases.get(user, "member")}
        _with_aliases(payload)
        questions = decision_prompts.draw_questions()
        body = await providers.call_decision(
            json.dumps(payload, ensure_ascii=False), questions)
        choices, scores = usable_choices(body, questions)   # 这一题不过弃答闸，见 QUESTION_MIN_MARGIN
        self._log_scores(group, "draw", scores, user=user, source_message_id=message_id)
        self._record_triage(group, user, message_id, payload, ["draw_check"], choices, False)
        if choices is None or "draw_check" not in choices:
            return None
        return choices["draw_check"] == "draw"


_MAX_LOGGED_SCORES = 40


def _log_scores(log, group, site, scores, **extra):
    """一行一次判断：逐题的选项、边际、原生 confidence、有没有被弃答闸丢掉。

    有界：最多 _MAX_LOGGED_SCORES 题（原话裁决一次能问几十题），
    超出只记条数不记明细。写失败由 log.append 自己兜着，绝不打断接话。
    """
    if not scores:
        return
    names = sorted(scores)
    row = {"site": site, "questions": len(names),
           "min_margin": config.DECISION_MIN_MARGIN,
           "dropped": sorted(n for n in names
                             if scores[n].get("dropped"))[:_MAX_LOGGED_SCORES]}
    invalid = sorted(n for n in names if scores[n].get("invalid"))
    if invalid:
        row["invalid"] = invalid[:_MAX_LOGGED_SCORES]
        row["invalid_reasons"] = {
            n: scores[n]["invalid_reason"] for n in invalid[:_MAX_LOGGED_SCORES]
            if scores[n].get("invalid_reason") in _INVALID_REASONS
        }
        names = [n for n in names if n not in invalid]
    if len(names) <= _MAX_LOGGED_SCORES:
        row["scores"] = {
            n: {"choice": scores[n]["choice"],
                "p": (None if scores[n].get("p") is None
                      else round(float(scores[n]["p"]), 4)),
                "margin": (None if scores[n]["margin"] is None
                           else round(float(scores[n]["margin"]), 4)),
                "confidence": (scores[n]["confidence"]
                               if isinstance(scores[n]["confidence"],
                                             (int, float))
                               and not isinstance(scores[n]["confidence"], bool)
                               else None)}
            for n in names}
    try:
        log.append("decision_scores", group, actor="system",
                   policy_id=config.INTERACTION_POLICY_ID, **row, **extra)
    except Exception:          # 观察日志永远不许拖垮判断路径
        pass


# ── 个人原话的批量裁决 ──

def _valid_source(row, now, group_id):
    """来源合格才参与：已核实、同群、未过保鲜期、话题词能留住。"""
    source = digest._people_evidence(row, now)
    if not source or source["group"] != group_id:
        return None
    if now - source["ts"] > digest.people_max_age_seconds(row):
        return None
    if not digest._people_keywords(row.get("keywords"), source["quote"],
                                   row.get("who") or ""):
        return None
    return source


def _entry_context(entries, group_id, aliases, known=()):
    """每条来源发言的前后各几句人话，脱敏后给 API 当语境。最多前2后1。

    只认本批已处理前缀里本群的人话；发言人用稳定本地代号，不带原始 ID。
    """
    by_id = {}
    rows = [e for e in entries or []
            if (isinstance(e, dict) and e.get("message_id")
                and not e.get("cmd") and not e.get("kind")
                and e.get("group", group_id) == group_id)]
    for index, entry in enumerate(rows):
        ctx = []
        for prev in rows[max(0, index - 2):index] + rows[index + 1:index + 2]:
            text = _sanitize(prev.get("text"), known)
            if text:
                ctx.append({"speaker": aliases.get(prev.get("sender"), "member"),
                            "text": text})
        by_id[entry["message_id"]] = ctx
    return by_id


async def curate_people(group_id, new_rows, old_rows, *, entries=(), now=None,
                        log=None):
    """一批新原话的去留 + 耐久旧条目的续存；一次合并请求，失败退回保守口径。

    只对「新的、还没裁决过的、来源合格」的条目问一次原生；
    失败/关闭/答案不合规：新条目照旧短时效收录，任何旧条目都不许被撤下。
    """
    now = time.time() if now is None else now
    new_rows = [dict(row) for row in new_rows or [] if isinstance(row, dict)]
    old_rows = [dict(row) for row in old_rows or [] if isinstance(row, dict)]

    # 可信旧记忆分两层：
    #   trusted —— 来源合格 + 自带**任何有效裁决**（含临时），只用来鉴定
    #              「这条提案是不是把旧来源原样又提了一遍」；
    #   carry   —— 其中耐久 kind/action 才有续存资格，过期/丢弃的不靠循环复活。
    trusted, carry = {}, []
    for row in old_rows:
        source = _valid_source(row, now, group_id)
        decision = digest.people_decision(row)
        if not source or not decision:
            continue
        trusted[(source["sender"], source["message_id"])] = (row, source)
        if (decision["kind"] in _DURABLE_KINDS
                and decision["action"] in _DURABLE_ACTIONS):
            carry.append((row, source))

    # 新行先看**裸证据**再过时效闸：伪造的耐久标签不许把一条
    # 已过短时效的未裁决发言从死里救回来 —— 先继承/抹掉注解，再验时效。
    fresh = []
    for row in new_rows:
        bare = digest._people_evidence(row, now)
        if not bare or bare["group"] != group_id:
            continue
        hit = trusted.get((bare["sender"], bare["message_id"]))
        clean = None
        if hit:
            trow, tsource = hit
            if (tsource["quote"] == bare["quote"]
                    and tsource["ts"] == bare["ts"]):
                clean = digest.people_decision(trow)
        if clean:
            row["decision"] = dict(clean)
        else:
            row.pop("decision", None)
        source = _valid_source(row, now, group_id)
        if source:
            fresh.append((row, source))

    pending = [(row, source) for row, source in fresh
               if digest.people_decision(row) is None]
    if not config.DECISION_ENABLED or not pending:
        return _merge_people(
            [row for row, _ in fresh], [row for row, _ in carry],
            now, group_id)

    # 稳定发言人代号：同一个人整批一个别名，别人占不到同一个名字。
    all_senders = [row.get("sender") for row in entries or []
                   if isinstance(row, dict)]
    all_senders += [source["sender"] for _, source in fresh]
    speaker_alias = _alias_map(all_senders)
    known = {group_id} | {s for s in all_senders if s} \
        | {row.get("message_id") for row in entries or []
           if isinstance(row, dict) and row.get("message_id")} \
        | {i for _, source in fresh + carry
           for i in (source["sender"], source["message_id"])}
    conversations = _entry_context(entries, group_id, speaker_alias, known)

    # 时间序以**原始发言时间**为准，不靠模型提案顺序：同 ts 时按它在
    # 已处理前缀里的位置排，排不进前缀的退回提案顺序（稳定序，不是随机）。
    entry_pos = {}
    for pos, entry in enumerate(entries or []):
        if isinstance(entry, dict) and entry.get("message_id"):
            entry_pos.setdefault(
                (entry.get("sender"), entry["message_id"]), pos)
    fresh_order = {id(row): i for i, (row, _s) in enumerate(fresh)}

    def chrono(pair):
        _row_, source = pair
        key = (source["sender"], source["message_id"])
        if key in entry_pos:
            return (source["ts"], 0, entry_pos[key])
        return (source["ts"], 1, fresh_order.get(id(pair[0]), 0))

    fresh_sorted = sorted(fresh, key=chrono)

    fresh_source = {}            # (sender,message_id) -> cid，本批候选专用
    per_candidate_priors = {}    # cid -> {mN: {identity,quote,ts}}，各管各的
    candidates = {}
    for row, source in pending:
        cid = f"c{len(candidates)}"
        fresh_source[(source["sender"], source["message_id"])] = cid
        # 对比池：同一个人、来源时间更早的旧记忆 —— 可信旧行 + 本批所有
        # 排在它前面的新行（已裁决的也算证据），按 (sender,message_id) 去重。
        prior_pool, seen_prior = [], set()
        target_key = (source["sender"], source["message_id"])
        target_pos = chrono((row, source))
        for old_row, old_source in carry:
            # 可信旧行在这批之前就已持久化，只用 <= 源时间挡「未来发言」
            key = (old_source["sender"], old_source["message_id"])
            if (old_source["sender"] == source["sender"]
                    and key != target_key
                    and old_source["ts"] <= source["ts"]
                    and key not in seen_prior):
                seen_prior.add(key)
                prior_pool.append((old_row, old_source))
        for f_row, f_source in fresh_sorted:
            # 本批新行必须按 chrono 严格排在它前面 —— 同 ts 时后到的记录
            # 不许被更早的候选当成「旧记忆」去撤。
            key = (f_source["sender"], f_source["message_id"])
            if (f_source["sender"] == source["sender"]
                    and key != target_key and key not in seen_prior
                    and chrono((f_row, f_source)) < target_pos):
                seen_prior.add(key)
                prior_pool.append((f_row, f_source))
        prior_pool.sort(key=lambda pair: (
            -len(set(row.get("keywords") or []) &
                 set(pair[0].get("keywords") or [])),
            -pair[1]["ts"]))
        memories = {}
        for prior_row, old_source in prior_pool[:4]:
            mid = f"m{len(memories)}"
            memories[mid] = {
                "identity": (old_source["sender"], old_source["message_id"]),
                "quote": old_source["quote"], "source_time": old_source["ts"]}
        per_candidate_priors[cid] = memories
        candidates[cid] = {
            "candidate_quote": _sanitize(source["quote"], known),
            "speaker": speaker_alias.get(source["sender"], "member"),
            "source_time": source["ts"],
            "conversation": conversations.get(source["message_id"], []),
            "existing_memories": [
                {"id": mid, "quote": _sanitize(mem["quote"], known),
                 "source_time": mem["source_time"]}
                for mid, mem in memories.items()],
        }

    questions = decision_prompts.memory_questions(candidates)
    body = await providers.call_decision(
        json.dumps({"candidates": candidates}, ensure_ascii=False), questions)
    choices, scores = usable_choices(body, questions)
    if log is not None:
        _log_scores(log, group_id, "memory", scores)
    if choices is None:
        return _merge_people(
            [row for row, _ in fresh], [row for row, _ in carry],
            now, group_id)

    superseded, dropped = set(), set()
    for row, source in pending:
        cid = fresh_source[(source["sender"], source["message_id"])]
        kind = choices.get(f"{cid}_memory_kind")
        action = choices.get(f"{cid}_memory_action")
        if kind == "discard" or action == "discard":
            dropped.add((source["sender"], source["message_id"]))
            continue
        if (kind in digest._PEOPLE_KIND_VALUES
                and action in digest._PEOPLE_ACTION_VALUES):
            row["decision"] = {"kind": kind, "action": action,
                               "message_id": source["message_id"]}
            if kind == "correction" and action == "replace_old":
                # 只撤「这条候选自己、且被它名下那题明确点名的旧记忆」——
                # mN 是本候选私有的局部代号，别处复用不到它头上。
                for mid, mem in per_candidate_priors[cid].items():
                    mem_sender, mem_id = mem["identity"]
                    if (mem_sender == source["sender"]
                            and choices.get(f"{cid}_supersede_{mid}")
                            == "supersede"):
                        superseded.add((mem_sender, mem_id))

    still_carry = [row for row, source in carry
                   if (source["sender"], source["message_id"]) not in superseded]
    kept_new = [row for row, source in fresh
                if (source["sender"], source["message_id"]) not in dropped
                and (source["sender"], source["message_id"]) not in superseded]
    return _merge_people(kept_new, still_carry, now, group_id)


def _merge_people(new_rows, carry_rows, now, group_id):
    """合并 + 复核 + 去重 + 上限：每条都重新过来源/时效/关键词闸。"""
    merged, seen_id, seen_quote = [], set(), set()
    pool = []
    for row in list(new_rows) + list(carry_rows):
        source = _valid_source(row, now, group_id)
        if source:
            pool.append((source["ts"], row, source))
    for _ts, row, source in sorted(pool, key=lambda p: -p[0]):
        ident = (source["sender"], source["message_id"])
        quote_key = (source["sender"],
                     " ".join(str(source.get("quote") or "").split()).casefold())
        if ident in seen_id or quote_key in seen_quote:
            continue
        seen_id.add(ident)
        seen_quote.add(quote_key)
        merged.append(row)
        if len(merged) >= max(1, int(config.PEOPLE_MEMORY_MAX_ITEMS)):
            break
    return merged
