"""群聊长期记忆：把「大伙最近在聊啥」沉淀成一份会滚动更新的摘要。

为什么不一次性读一周的原文
--------------------------
表面上很诱人（语料全在嘛），但代价是：输入随群活跃度线性膨胀，一个吵闹的群一周能堆到几十万
tokens；更要命的是模型对长文本的「中段遗忘」，一整周的原文塞进去，真正值得记的梗反而被稀释掉。

这里采用增量滚动压缩：
    新摘要 = f(旧摘要, 当天新增)
每次输入都是「上一版摘要 + 一段有界的新增」，因此开销恒定，与群有多吵无关；
而信息是逐步累积的，一周后的摘要里仍然留着周一发生的事。

当天的量实在太大时（超过一屏），先分块压成要点（map），再合并成最终摘要（reduce），
这样单次请求的上下文永远可控。

产出是「一段人话 + 一份结构化数据」，一次调用同时拿到两样东西：
    正文：《群史记》风格的幽默简报，可以直接发给群友看
    标签：<DIGEST>{...}</DIGEST>，机器可读的话题/金句/承诺/人物，留着以后做催债、点名

「当天新增」从哪来
------------------
不再自己维护待压缩队列，而是直接读 archive.py 里已提交文件偏移之后的原文。
单一数据源的好处：压缩失败不会丢消息（下次重读），重启不用管队列持久化，
state.json 也不必再塞几千条聊天记录。
"""

import difflib
import json
import logging
import re
import time
import uuid
import unicodedata

logger = logging.getLogger("qqbot")   # 与 bot.py 同一个命名 logger，共享 handler

import config
import naming
import prompts
import wordfilter
from recall import MACHINE_ID

DIGEST_TAG = re.compile(r"<DIGEST>(.*?)</DIGEST>", re.S)

# 一次压缩最多看多少字符（超出就走分块）。0.8 元/百万 tokens，1 万字 ≈ 1 分钱，主要是防失控。
DEFAULT_CHUNK_CHARS = 6000
DEFAULT_MAX_BLOCKS = 10

SYSTEM_MAP = """你在为 QQ 群整理聊天记录。下面是一段原始聊天记录，请从中提取要点。

只提取【真实出现过】的内容，绝不许脑补或推测。输出 4~8 条要点，每条不超过 25 字，形如：
- 谁 在聊 什么（一句话说清）
- 出现了什么好玩的梗/金句（照抄原话）
- 反复出现的口头禅/语气词/表情（照抄原话；只出现一次的不算）
- 谁 许下了什么承诺 / 说要做什么事（请客、发资源、还钱等），哪怕只是一句玩笑
- 谁 跟「{bot}」有来回、态度是热络示好 / 平常 / 抬杠阴阳钓鱼（照抄最能说明态度的那一句）

没有的内容就不要写。不要写总结性的套话，不要评价。
如有值得保留的个人材料，另列最多 3 条「[称呼] 完整原话」，每条 4~240 字，不受上面的
25 字要点上限限制。必须保留整条发言的否定、条件和转述对象，不用推测性格；没有就不列。

【敏感内容一律不复述 —— 这是硬规则，优先于上面所有条目】
原始记录里若出现政治性内容（人名、事件、谐音或缩写暗号、地域/族群歧视）、涉黄涉暴，
一律**不抄原话、不改写、不缩写、不做联想、不解释**，一个字都不要留在产出里。
这类内容至多概括成一句事实层面的「有人在群里试探敏感话题（不复述）」，且整份产出里只记一条。
自检标准：把这句话原样截图发到群外，会不会给{bot}的使用者惹麻烦？会，就不写。
宁可这一整条要点不写，也不要为了「记录完整」把它抄进去。"""

SYSTEM_REDUCE = """你在为 QQ 群「{bot}」整理长期记忆。你会拿到【上一版摘要】和【这一段时间的新要点】，
请把它们合并成一份新的长期记忆，并输出两段内容。

第 1 段：《群史记》风格的幽默简报，用{bot}（贴吧老哥乐子人）的口吻，150 字以内。
要求：像群里最八卦的人在做周报，可以有吐槽和调侃，但别伤人；没发生什么就直说这阵子群里安静得离谱。

第 2 段：换行后单独输出一行机器指令，严格形如：
<DIGEST>{"topics":[{"topic":"话题","who":["阿强"],"heat":3}],"memes":[{"quote":"原话","why":"好笑在哪"}],"promises":[{"who":"阿强","what":"说要请全群喝奶茶","due":""}],"people":[{"who":"阿强","note":"最近在玩黑猴，喜欢自己探索","quote":"最近在玩黑猴，喜欢自己探索","keywords":["黑猴"],"mode":"损友"}],"style":[{"phrase":"绷不住了","when":"看到离谱操作"}],"affinity":[{"who":"阿强","delta":2,"why":"一整天都在捧场接梗"}]}</DIGEST>

字段规则：
- topics：近期话题，heat 用 1~5 表示热度。最多 6 条。
- memes：值得复用的金句/梗，quote 尽量照抄原话。最多 5 条。
  ⚠️ **这两类一律不进 memes**：一是别人的糗事、不体面的事（被平台处置、当众出丑、翻车），
  二是**针对{bot}自己的吐槽**（说你掉线、说你宕机、说你死了几天的）。
  前者留着就是标签，说出去是揭短；后者会被当成好素材天天复读，把一次意外变成永久梗。
  要留的是那种「大家一起玩得起来」的梗，不涉及谁倒霉、也不涉及{bot}自己。
- promises：**明确的承诺**才进——某人说出「我答应 / 我欠 / 我请 / 回头给你」这类话，
  且能分清谁欠谁、欠的是什么。最多 5 条。
  ⚠️ 邀约（「来玩 / 来吃 / 走啊」）、请求（「你来干嘛」）、起哄和荤梗**都不是承诺**：
  实测把一句「来给我吃鸡吧」记成了对方欠的账，催债等于把荤梗公开复读。拿不准就不进（宁缺勿滥）。
- style：这段时间群里**反复出现**的口头禅、语气词、表情用法。phrase 照抄原话
  （2~12 字），when 一句话说明什么场合用。最多 5 条。
  ⚠️ 只收「大家一起用」的表达，而且每条至少在这段记录里出现过两次；只出现一次的不算。
  针对某个人的外貌/糗事、平台处置、政治/涉黄涉暴一律不收；也不要收{bot}自己的口癖
  （那会变成每轮复读）。没有就空数组，不要硬凑。
- people：只收能帮助下一次接话的具体原话，最多 6 条；不写性格速写或控诉书。
  who 必须是这段记录中可分清的说话人；quote 完整照抄 TA 的一条发言，4~240 字，
  只允许合并空白，不许删掉否定、条件或转述对象。note 同样填写这条原话。
  keywords 给 1~4 个原话中实际出现的具体话题词，每个 2~24 字；不用人名、代词或泛泛的态度词。
  泛泛的友善、尊重、希望被理解是普通接话的基线，不必记成个人特点。
  一次提到、聊天频繁、好感高，都不证明稳定喜好或已经了解此人。没有合适原话就空数组。
  不把敏感话题、威胁、骚扰或对机器人的试探归纳成某人的性格；图片描述也不是本人自述。
  可选 mode 仍只从损友 / 知心 / 护短 / 捧场中选，作为游戏里的相处风格提案；
  它不代表已证实的性格、喜好或开玩笑许可，拿不准就不写。
- affinity：**这一段时间里，每个人跟「{bot}」这段关系的净走向**。delta 取 -3~3 的整数，按这把尺子：
  +2~3 护着{bot}、热络捧场、主动帮它；+1 主动搭话、轻松接梗、玩得开心；
  0 只是提问办事，或者正负都有、互相抵消；
  -1 出现过冲着{bot}的贬损、骂它的话，或拿荤话低俗问题逗它（熟人间的笑骂不算），但整体还是在玩；
  -2~-3 反复辱骂贬损它，或者它已经拒绝了还拿荤话、擦边内容死缠。
  拿{bot}起哄、出题考它、试探边界本身不扣分，这是群里常见的玩法；扣分看的是冲着它的贬损和纠缠。
  这是唯一会决定「{bot}日后对某人什么态度」的字段，所以只依据记录里真实发生过的互动来判，
  不许凭印象编。这段时间跟{bot}有过来回的人每个都要过一遍：看得出方向的就写一条，
  轻微的给 ±1；来回很多却确实看不出方向的给 0，why 里写清为什么；没露过面的不写。
  明确要求安静、指出理解错误、嫌插话时机不对或要求别开这种玩笑，都是有效反馈，
  不能因此扣好感。先修正{bot}的行为，不把提出边界的人记成敌人。
  ⚠️ **你判的是关系走势，不是逐句流水账**：另有只看单句的方向判定在当场记账，
  那套会把熟人间的玩笑、互喷也记成敌意。你看到的是一整段时间，以你的判断为准、该对冲就对冲：
  整天互相损但气氛融洽的，净值给 0 或正，别被单句的火药味带偏；反过来单句客气、
  整体却在疏远试探的，也照实给负。你的结算就是对单句误判的最终修正。
  ⚠️ **玩笑和敌意要看上下文**：交情可以帮助理解笑骂，但不是开玩笑许可。
  【当前关系底账】给的是各人现在站在哪个档位 —— 它只帮你读懂一句话的分量，
  不授予任何许可，也不替代这段记录里真实发生过的事。
  对方笑着接梗、主动让你继续，与明确嫌烦是不同的；不能因为交情深就忽略喊停，
  也不能因为刚认识就把批评自动放大成敌意。
  ⚠️ 别一律给 +1：全都 +1 的话档位永远跨不过去，好感度就成了个摆设。
  态度明确（热络捧场、或反复抬杠冒犯）就给 ±2~3。
  ⚠️ **互动多 ≠ 亲近，脏话也不自动等于敌意**：持续人身攻击、性骚扰、恶意钓鱼可以给负分；
  熟人笑骂、纠正错误、催正常回答或嫌它打扰不要据此扣分。也别把针对别人的骂声算到{bot}头上。
- 只写记录里真实出现过的内容，绝不许编造。没有的字段给空数组 []。
- **敏感内容不复述（硬规则）**：政治性人名/事件/谐音暗号、地域族群歧视、涉黄涉暴，一律不进这份记忆 ——
  包括 memes 的 quote、people 的 note、topics 的 topic 名、以及第 1 段的《群史记》。
  memes 是「照抄原话」字段，最容易被顺手带进去，务必逐条筛。
  这类内容至多在 topics 里留一条「敏感话题试探（不复述）」，不写是谁、不写内容。
  上一版摘要里若已带有这类内容，合并时**顺手删掉**，不要因为「它是旧记录」就留着。
- 合并时：仍然新鲜的保留，已经过时/完结的丢掉，保持总量不膨胀。
- **旧摘要里的事不自动续杯**：一件只在【上一版摘要】里、这段时间**没人再提起**的旧事，
  合并时直接丢掉。摘要是滚动的，不这么卡的话一件陈年糗事能靠「每次被抄一遍」挂上几个星期，
  群里看着就像揪着人不放。只有今天又被人提起、或者还在产生影响（承诺没兑现、
  事情还没了结）时才留下。⚠️ 别人倒霉/出糗的事尤其要按这条办：留在记忆里它就变成了
  那个人的标签，说出口就是揭短。
- people 记录的是当时说过的话，不是永久的稳定状态。不要用时效词改写原话，也不要续杯：
  只有旧摘要里有、这次没有新发言支持的条目不得编造新来源或新时间；过期由代码处理。
- 压缩稿里的署名有时会写成「一位群友」：那是**系统给不出名字的人**共用的泛称，
  不是谁的外号也不是真名。别把它当成某一个人来总结，也别写进 people / promises 的 who；
  真要提到就说「某位群友」，不要复读这个泛称。
- **切换模式、查关系这类操作指令不是聊天内容**：记录里若出现「XX模式」「变回XX」
  「查好感/关系榜/称呼表/群史记」「决斗」等本地指令，那是群友在**操作**机器人，
  不是他本人的想法或处境 —— 不许写成 people 的 note、不许算进 affinity、
  也不许当成梗记进 memes，不然「他想变猫娘」这种账就记到人头上去了。
- 标签行之后不要输出任何内容。"""


# 说话人没有名字时，压缩稿里写什么。
#
# ⚠️ 旧版写的是 openid 后四位（`sender[-4:]`）。后果不是「不好看」而是**泄漏**：
# 摘要里从此出现「6ABA」这种人名，而摘要每轮注入 prompt —— 模型当群里真有个人叫 6ABA，
# 照着复读，甚至拿去叫人。这跟「回复里冒出 openid 尾巴」是同一类事故，只是入口在压缩侧。
# 没有名字就用通用词兜住：区分度差一点，但绝不把编号喂进模型嘴里。
# ⚠️ 这个字面同时写进了上面的 SYSTEM_REDUCE（要让模型知道它是个泛称），改一处要改两处。
UNAMED_WHO = "一位群友"


def render_transcript(entries, resolver=None, max_chars=DEFAULT_CHUNK_CHARS):
    """把消息渲染成给模型看的文本。有昵称就用昵称，没有也不硬编。"""
    lines = []
    used = 0
    for e in entries:
        who = (resolver(e["sender"]) if resolver else None) or UNAMED_WHO
        text = (e.get("text") or "").strip().replace("\n", " ")
        if not text:
            continue
        row = f"[{who}] {text}"
        lines.append(row)
        used += len(row) + 1
        if used >= max_chars:
            break
    return "\n".join(lines)


def split_digest(raw):
    """拆成 (可读正文, 结构化 dict)。解析失败不影响正文。"""
    if not raw:
        return "", None
    m = DIGEST_TAG.search(raw)
    body = DIGEST_TAG.sub("", raw).strip()
    if not m:
        # 不阻断正文，但必须留痕：思考模型被 max_tokens 截断时，症状就是结构块整段消失。
        logger.warning("摘要缺少 <DIGEST> 结构块（正文保留，结构化字段不更新）")
        return body or raw.strip(), None
    try:
        data = json.loads(m.group(1))
    except Exception as exc:
        logger.warning("摘要 <DIGEST> JSON 解析失败（正文保留）: %s", type(exc).__name__)
        return body, None
    if not isinstance(data, dict):
        logger.warning("摘要 <DIGEST> 不是对象（正文保留）: %s", type(data).__name__)
        return body, None
    return body, data


def normalize(data):
    """保证各字段都是列表，别把脏数据带进状态文件。"""
    out = {"topics": [], "memes": [], "promises": [], "people": [], "style": [],
           "affinity": []}
    if not isinstance(data, dict):
        return out
    for key in out:
        val = data.get(key)
        if isinstance(val, list):
            # affinity 是「一批人」，不是「几条事」，上限给宽一点
            cap = {"affinity": 8, "style": config.STYLE_NOTE_MAX_ITEMS}.get(key, 6)
            out[key] = [v for v in val if isinstance(v, dict)][:cap]
    return out


def sanitize_style(data, transcript):
    """表达学习的写入闸：模型提案、代码裁决。

    只保留在**本批原文**里至少出现 STYLE_MIN_REPEATS 次的表达。模型很容易把
    上一版的口头禅原样抄回来，或者把一次性发言当成群梗；靠计数把「最近还在用」
    变成硬条件，过期的口头禅不用另做时间戳也会自然掉出去。命中敏感词的整条丢弃。
    """
    if not isinstance(data, dict):
        return 0
    rows = data.get("style")
    if not isinstance(rows, list):
        data["style"] = []
        return 0
    kept = []
    seen = set()
    limit = max(0, config.STYLE_NOTE_MAX_ITEMS)
    if not limit:
        data["style"] = []
        return 0
    minimum = max(1, config.STYLE_MIN_REPEATS)
    for row in rows:
        if not isinstance(row, dict):
            continue
        phrase = str(row.get("phrase") or row.get("Phrase") or "").strip()
        when = str(row.get("when") or row.get("When") or "").strip()
        if not (2 <= len(phrase) <= 12) or "\n" in phrase or "\n" in when:
            continue
        if phrase in seen or wordfilter.has_hit(phrase):
            continue
        if transcript.count(phrase) < minimum:
            continue
        seen.add(phrase)
        kept.append({"phrase": phrase[:12], "when": when[:16]})
        if len(kept) >= config.STYLE_NOTE_MAX_ITEMS:
            break
    data["style"] = kept
    return len(kept)


def render_style(state, limit=None):
    """把表达学习的结果渲染成每日记忆里的一小块；没有可用条目时返回空串。"""
    rows = ((state or {}).get("data") or {}).get("style") or []
    limit = config.STYLE_NOTE_MAX_ITEMS if limit is None else int(limit)
    limit = max(0, limit)
    if not limit:
        return ""
    lines, seen = [], set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        phrase = str(row.get("phrase") or row.get("Phrase") or "").strip()
        when = str(row.get("when") or row.get("When") or "").strip()
        if (not (2 <= len(phrase) <= 12) or phrase in seen
                or wordfilter.has_hit(phrase)):
            continue
        seen.add(phrase)
        lines.append(f"- {phrase}" + (f"（{when}）" if when else ""))
        if len(lines) >= limit:
            break
    if not lines:
        return ""
    return prompts.PROMPT_STYLE_HEADER.strip() + "\n" + "\n".join(lines)


# ── 个人原话：模型提案、代码核对来源 ──
# people 条目只收「能帮助下一次接话的具体原话」。模型压缩时提议 who/quote/keywords，
# 代码把提案绑回**本批已处理**的原始消息（发言人 + 消息标识 + 时间），对不上就丢。
# 摘要里的性格推测、模型自带的 evidence/时间戳一律不作数 —— 生成的描述永远不会
# 变成可调用的「个人事实」。
def _people_text(value):
    return ' '.join(value.split()) if isinstance(value, str) else ''


def _people_keywords(values, quote, who):
    if not isinstance(values, list):
        return []
    kept = []
    for value in values:
        key = _people_text(value)
        if (2 <= len(key) <= 24 and key != who
                and key.casefold() in quote.casefold()
                and not wordfilter.has_hit(key) and not MACHINE_ID.search(key)
                and key not in kept):
            kept.append(key)
        if len(kept) == 4:
            break
    return kept


def _people_evidence(row, now):
    source = row.get('evidence') if isinstance(row, dict) else None
    if not isinstance(source, dict):
        return None
    if any(not isinstance(source.get(key), str) or not source[key]
           for key in ('group', 'sender', 'message_id')):
        return None
    ts = source.get('ts')
    if type(ts) not in (int, float) or not 0 < ts <= now:
        return None
    quote = _people_text(source.get('quote'))
    if (not 4 <= len(quote) <= 240 or wordfilter.has_hit(quote)
            or MACHINE_ID.search(quote)
            or any(source[key] in quote for key in ('group', 'sender', 'message_id'))):
        return None
    return {key: source[key] for key in ('group', 'sender', 'message_id', 'ts')} | {'quote': quote}


_PEOPLE_KIND_VALUES = {"preference", "fact", "temporary", "shared_moment",
                       "correction", "discard"}
_PEOPLE_ACTION_VALUES = {"retain_quote", "keep_briefly", "replace_old", "discard"}
_PEOPLE_DURABLE_KINDS = {"preference", "fact", "shared_moment", "correction"}
_PEOPLE_DURABLE_ACTIONS = {"retain_quote", "replace_old"}


def people_decision(row):
    """返回 {kind, action, message_id}；注解与核实来源对不上号就 None。

    kind/action 必须在原生词表内，message_id 必须恰好等于该条目已核实的
    evidence.message_id —— 防止把裁决贴到别的发言头上。
    """
    if not isinstance(row, dict):
        return None
    decision = row.get('decision')
    if not isinstance(decision, dict):
        return None
    kind, action = decision.get('kind'), decision.get('action')
    evidence = row.get('evidence')
    # 先做类型闸再做成员判断：畸形元数据只能算「没裁决过」，不许抛 TypeError。
    if (not isinstance(evidence, dict)
            or not isinstance(kind, str) or not isinstance(action, str)
            or not isinstance(evidence.get('message_id'), str)
            or not evidence['message_id']
            or not isinstance(decision.get('message_id'), str)
            or decision['message_id'] != evidence['message_id']):
        return None
    if (kind not in _PEOPLE_KIND_VALUES or action not in _PEOPLE_ACTION_VALUES):
        return None
    return {'kind': kind, 'action': action, 'message_id': evidence['message_id']}


def people_max_age_seconds(row):
    """这条原话的保鲜期：原生判过「值得长期留」的用耐久天数，其余照旧短时效。"""
    decision = people_decision(row)
    days = (config.PEOPLE_DURABLE_MAX_AGE_DAYS
            if decision and decision['kind'] in _PEOPLE_DURABLE_KINDS
            and decision['action'] in _PEOPLE_DURABLE_ACTIONS
            else config.PEOPLE_NOTE_MAX_AGE_DAYS)
    return max(0.0, days) * 86400.0


def match_people(state, limit=2, now=None, *, group_id='', sender='', text='',
                 resolver=None):
    """挑出可用的个人原话来源：条件不齐就返回 ('', [])（0 token）。

    这是「准入与相关性」闸，不是「个性化有效」的证明：只取**当前群、当前说话人本人**、
    发言时间仍新鲜、且至少一个话题词命中本轮输入的原话，最多两条、新的在前。
    取不到就什么都不喂 —— 宁可按普通群友自然接话，也不拿全群名册、别人的话或
    过期/无来源条目凑数。来源标识（群号、openid、消息 ID）只留在本地。

    从 render_people 里拆出来（2026-10-31）：插嘴前的「这句旧话现在端出来合不合适」
    判断需要**原话本身**，而不是渲染好的那段提示词。两个出口共用同一道闸，
    不许任何一侧单独放宽条件。
    """
    if (not config.PERSONAL_MEMORY_ENABLED or not group_id or not sender
            or not text or resolver is None or wordfilter.has_hit(text)):
        return '', []
    limit = min(2, max(0, int(limit)))
    if not limit:
        return '', []
    now = time.time() if now is None else now
    who = _people_text(resolver(sender))
    if (not who or who == UNAMED_WHO or who == sender
            or wordfilter.has_hit(who) or MACHINE_ID.search(who)):
        return '', []
    matched, seen = [], set()
    for row in (((state or {}).get('data') or {}).get('people') or []):
        source = _people_evidence(row, now)
        if (not source or source['group'] != group_id or source['sender'] != sender
                or now - source['ts'] > people_max_age_seconds(row)
                or source['quote'] == _people_text(text)):
            continue
        keys = _people_keywords(row.get('keywords'), source['quote'], who)
        if not any(key.casefold() in text.casefold() for key in keys):
            continue
        if source['message_id'] in seen:
            continue
        seen.add(source['message_id'])
        matched.append(source)
    matched.sort(key=lambda source: source['ts'], reverse=True)
    return who, matched[:limit]


def render_matched(who, sources):
    """把挑好的来源渲染进本轮动态层。空来源返回空串。"""
    lines = [f"- {who}（{time.strftime('%m-%d %H:%M', time.localtime(source['ts']))}）说过："
             + json.dumps(source['quote'], ensure_ascii=False)
             for source in sources or []]
    return prompts.PROMPT_MEMORY_HEADER.strip() + '\n' + '\n'.join(lines) if lines else ''


def render_people(state, limit=2, now=None, *, group_id='', sender='', text='',
                  resolver=None):
    """把已核实的个人原话渲染进**本轮动态层**；条件不齐就返回空串（0 token）。

    准入逻辑全在 match_people 里，这里只负责渲染 —— 两处出口永远同一道闸。
    """
    return render_matched(*match_people(
        state, limit, now, group_id=group_id, sender=sender, text=text,
        resolver=resolver))


class GroupDigest:
    """每个群一份「当前摘要 + 上次压缩到哪」。

    待压缩的消息不再存在这里 —— 它们住在 archive.py 的 JSONL 里，
    cursor 标记已处理的归档前缀；last_run 只用于调度间隔，兼容旧版迁移。
    """

    def __init__(self, chunk_chars=DEFAULT_CHUNK_CHARS, max_blocks=DEFAULT_MAX_BLOCKS,
                 max_entries=2000):
        self.chunk_chars = chunk_chars
        self.max_blocks = max_blocks
        self.max_entries = max_entries
        self.groups = {}   # group_id -> {"summary": {...}, "last_run": ts, "cursor": offsets}

    # ── 存取 ──
    def _slot(self, group_id):
        slot = self.groups.get(group_id)
        if slot is None:
            slot = {"summary": None, "last_run": 0.0}
            self.groups[group_id] = slot
        return slot

    def hydrate(self, raw):
        restored = 0
        for gid, obj in (raw or {}).items():
            if not isinstance(obj, dict):
                continue
            slot = self._slot(gid)
            summary = obj.get("summary")
            if isinstance(summary, dict):
                slot["summary"] = {
                    "brief": summary.get("brief") or "",
                    "data": normalize(summary.get("data")),
                    "updated": float(summary.get("updated") or 0),
                }
            slot["last_run"] = float(obj.get("last_run") or 0)
            if isinstance(obj.get("cursor"), dict):
                slot["cursor"] = dict(obj["cursor"])
            restored += 1
        return restored

    def dump_into(self, data):
        data["digests"] = {
            gid: {"summary": s["summary"], "last_run": s["last_run"],
                  "cursor": s.get("cursor")}
            for gid, s in self.groups.items()
        }

    def touch(self, group_id):
        """登记一个群，让它进入定时扫描范围（没有任何状态也要登记，否则新群永远不会被压缩）。"""
        return self._slot(group_id)

    def last_run(self, group_id):
        return self.groups.get(group_id, {}).get("last_run", 0.0)

    def cursor(self, group_id):
        """Archive offsets already committed; None migrates timestamp-only state."""
        return (self.groups.get(group_id) or {}).get("cursor")

    def mark_compressed(self, group_id, ts=None, cursor=None):
        slot = self._slot(group_id)
        slot["last_run"] = time.time() if ts is None else ts
        if cursor is not None:
            slot["cursor"] = dict(cursor)

    def needs_compress(self, group_id, interval_hours, min_entries, pending_count=None,
                       count_trigger=None):
        """该不该压：攒够了就压，兜底每天至少一次。

        count_trigger 是「攒够这么多条就压」—— 活跃的群不该干等 24 小时才更新记忆和好感度。
        interval_hours 是兜底：冷清的群只要攒够 min_entries，也保证一天压一次。

        pending_count 由调用方从归档算出来传进来（这里不持数据）。
        """
        slot = self.groups.get(group_id)
        if not slot:
            return False
        if pending_count is None:
            return False
        if pending_count < min_entries:
            return False
        if pending_count >= self.max_entries:
            return True      # 撑满了，立刻压，不等时间
        if count_trigger and pending_count >= count_trigger:
            return True      # 攒够了，不等时间
        return time.time() - slot.get("last_run", 0.0) >= interval_hours * 3600.0

    def compress_reason(self, group_id, interval_hours, pending_count, count_trigger=None):
        """给日志用的一句话，说明这次为什么压（省得事后猜）。0 token。"""
        if pending_count >= self.max_entries:
            return "队列已满"
        if count_trigger and pending_count >= count_trigger:
            return f"攒够 {pending_count} 条"
        idle_h = (time.time() - (self.groups.get(group_id) or {}).get("last_run", 0.0)) / 3600.0
        return f"每日兜底（距上次 {idle_h:.1f}h）"

    def get(self, group_id):
        return (self.groups.get(group_id) or {}).get("summary")

    def rename_in_memory(self, group_id, old, new):
        """把长期记忆里固化的旧称呼一并改掉，返回替换处数。

        每日压缩写的是「当时的昵称」，改名后摘要里还留着旧名，而摘要每轮都注入 prompt，
        等于又把旧名送回模型嘴边 —— 这是「明明改了名它照样叫旧的」的第二个源头。
        单字名字不替换（误伤面太大）。
        """
        old = (old or "").strip()
        new = (new or "").strip()
        if len(old) < 2 or not new or old == new:
            return 0
        summary = (self.groups.get(group_id) or {}).get("summary")
        if not isinstance(summary, dict):
            return 0
        counter = [0]

        def walk(node):
            if isinstance(node, str):
                if old in node:
                    counter[0] += node.count(old)
                    return node.replace(old, new)
                return node
            if isinstance(node, list):
                return [walk(x) for x in node]
            if isinstance(node, dict):
                # 改名只改展示用的称呼与原话字面；evidence 是本地留存的原始出处
                # （发言人/消息标识/时间），改写它会弄坏出处核对，原样保留。
                return {k: v if k == 'evidence' else walk(v) for k, v in node.items()}
            return node

        summary["brief"] = walk(summary.get("brief") or "")
        summary["data"] = walk(summary.get("data"))
        return counter[0]

    # ── 压缩 ──
    def build_blocks(self, slot, resolver=None):
        """把待压缩区切成若干块。每块控制在 chunk_chars 以内。"""
        blocks, cur, size = [], [], 0
        for e in slot["pending"]:
            who = (resolver(e["sender"]) if resolver else None) or UNAMED_WHO
            row_len = len(who) + len(e["text"]) + 4
            if size + row_len > self.chunk_chars and cur:
                blocks.append(cur)
                cur, size = [], 0
            cur.append(e)
            size += row_len
        if cur:
            blocks.append(cur)
        if len(blocks) > self.max_blocks:
            # Work oldest first. The returned source_cursor only commits this
            # prefix; the rest remains pending in the archive for the next run.
            blocks = blocks[:self.max_blocks]
        return blocks

    def old_summary_text(self, slot, resolver=None, refresh=None):
        """回喂给模型的上一版摘要。

        刻意**不含 affinity**：那一栏是「已结算过的好感度变化」，喂回去模型会照着再写一遍，
        等于同一批互动被反复扣分/加分。它只存在档案里备查，不参与下一轮合并。

        refresh 用来把上一版里的旧称呼改写成当前称呼 —— 这一版是要喂给模型的，
        带着旧名进去，模型下一版还会照着写，旧名就永远滚不掉。
        """
        s = slot.get("summary")
        if not s:
            return ""
        parts = [f"[上一版简报] {s.get('brief','')}"]
        data = s.get("data") or {}
        if data.get("topics"):
            parts.append("[上一版话题] " + "、".join(
                str(t.get("topic") or "") for t in data["topics"]))
        if data.get("promises"):
            parts.append("[上一版待兑现] " + "；".join(
                f"{p.get('who','')}:{p.get('what','')}" for p in data["promises"]))
        # 上一版的个人条目只回喂「已核实来源的原话」：who 用当前称呼、quote 原文、
        # keywords 话题词。不带 evidence 里的标识与时间，更不带旧版性格推测 ——
        # 那些不是这个人的事实，喂回去只会让模型把猜测续写成新一版「认知」。
        if data.get("people"):
            rows = []
            now = time.time()
            for p in data["people"]:
                source = _people_evidence(p, now)
                if (not source or now - source['ts'] > people_max_age_seconds(p)):
                    continue
                who = _people_text(resolver(source["sender"]) if resolver else "")
                if (not who or who == UNAMED_WHO or who == source['sender']
                        or wordfilter.has_hit(who) or MACHINE_ID.search(who)):
                    continue
                keywords = _people_keywords(p.get("keywords"), source["quote"], who)
                if not keywords:
                    continue
                rows.append({"who": who, "quote": source["quote"], "keywords": keywords})
            if rows:
                parts.append("[上一版个人原话] " + "；".join(
                    json.dumps(r, ensure_ascii=False) for r in rows))
        if data.get("style"):
            parts.append("[上一版口头禅] " + "、".join(
                str(s.get("phrase") or "") for s in data["style"]
                if isinstance(s, dict) and s.get("phrase")))
        text = "\n".join(p for p in parts if p.strip())
        return refresh(text) if refresh else text


# ══════════════════════════ 压缩流程 ══════════════════════════


async def compress_group(store, group_id, entries, ask, resolver=None, budget=None, log=None,
                         refresh=None, ledger=None, people_curator=None):
    """把一批新消息合并进长期摘要。

    entries: 待压缩的原始消息（来自 archive.since），元素形如 {"sender","text","ts"}
    ask(system, user) -> str | None   发起一次模型调用
    budget() -> bool                  可选，返回 False 表示今天的额度用完了
    refresh(text) -> str              可选，把回喂文本里的旧称呼换成当前称呼
    ledger: str | None                可选，【当前关系底账】（各人现在的交情档位）——
                                      affinity 判「玩笑还是敌意」得先知道交情深浅，
                                      这块必须随 payload 进 reduce，不能写死在 SYSTEM_REDUCE
                                      （那是全群共享的稳定提示词）
    块数 <= 1 时直接压；否则先分块提取要点（map），再合并（reduce）。
    返回新的 summary，压缩失败返回 None —— 此时调用方不该推进 last_run，
    这样消息会留在待压缩区，下一轮再读一遍，不会丢。
    """
    slot = store._slot(group_id)
    if not entries:
        return None

    blocks = store.build_blocks({"pending": entries}, resolver)
    processed = [entry for block in blocks for entry in block]
    if not processed:
        return None
    if len(blocks) <= 1:
        new_text = render_transcript(processed, resolver, max_chars=10 ** 9)
    else:
        points = []
        for block in blocks:
            if budget and not budget():
                if log:
                    log("🛑 额度不足，本次分块摘要中断")
                return None
            text = render_transcript(block, resolver, max_chars=10 ** 9)
            out = await ask(naming.render(SYSTEM_MAP), text)
            if not out or not out.strip():
                if log:
                    log("⚠️ 分块摘要失败，保留本批原文，下轮重试")
                return None
            points.append(out.strip())
        if not points:
            return None
        new_text = "\n".join(points)
        if log:
            log(f"🗜️  {len(blocks)} 块 → 要点已提取，正在合并")

    old = store.old_summary_text(slot, resolver, refresh=refresh)
    payload = f"{old}\n\n[这段时间的新内容]\n{new_text}" if old else f"[这段时间的新内容]\n{new_text}"
    if ledger:
        payload = f"{ledger}\n\n{payload}"

    if budget and not budget():
        if log:
            log("🛑 额度不足，本次摘要跳过")
        return None

    raw = await ask(naming.render(SYSTEM_REDUCE), payload)
    if not raw:
        return None

    body, data = split_digest(raw)
    # 表达学习是摘要的副产品：先按本批原文计数裁决，坏条目只丢自己，不拖垮整次压缩。
    if isinstance(data, dict):
        sanitize_style(data, "\n".join(str(entry.get("text") or "")
                                       for entry in processed))
    # Text alone (including provider refusals) is not a successful state
    # update. Require at least one known list field, and reject malformed rows
    # instead of silently normalizing them into an empty memory.
    fields = {"topics", "memes", "promises", "people", "affinity"}
    if not isinstance(data, dict) or not fields.intersection(data):
        return None
    if any(not isinstance(data[key], list)
           or any(not isinstance(row, dict) for row in data[key])
           for key in fields.intersection(data)):
        return None

    required = {"topics": ("topic",), "memes": ("quote",),
                "promises": ("who", "what"), "people": ("who", "note"),
                "affinity": ("who",)}
    for key in fields.intersection(data):
        for row in data[key]:
            if any(not isinstance(row.get(name), str) or not row[name].strip()
                   for name in required[key]):
                return None
            if key == "affinity" and (type(row.get("delta")) is not int):
                return None

    summary = {
        "brief": body[:400],
        "data": normalize(data),
        "updated": time.time(),
        "entry_count": len(processed),
    }
    if processed[-1].get("_cursor") is not None:
        summary["source_cursor"] = dict(processed[-1]["_cursor"])
    # 个人条目裁决：模型只提议 who/quote/keywords/mode，代码把每条 quote 绑回
    # 本批已处理前缀里的真实发言（发言人 + 消息标识 + 原始时间）。
    # 绑不上的（没说过、别人的话、指令、图片描述、时间不全）整条丢弃 ——
    # 生成的性格推测永远不会成为可调用的个人事实。
    stamp_people((slot.get("summary") or {}).get("data"), summary["data"],
                 now=summary["updated"], entries=processed, resolver=resolver,
                 group_id=group_id)
    stamp_memes((slot.get("summary") or {}).get("data"), summary["data"],
                now=summary["updated"])
    # 可选的个人原话裁决：摘要落槽【之前】换好最终名单 —— 中途不许把
    # 短时效新名单刷进 state 让耐久旧条目提前退场。回调抛错同样不许换。
    if people_curator is not None:
        old_people = (((slot.get("summary") or {}).get("data") or {})
                      .get("people") or [])
        summary["data"]["people"] = await people_curator(
            summary["data"]["people"], old_people,
            entries=processed, now=summary["updated"])
    slot["summary"] = summary
    return slot["summary"]


def stamp_memes(old_data, new_data, now=None):
    """梗也要有时间戳，判定口径：**换个说法才算新料**。

    ⚠️ 抄回来的旧梗不给新时间戳：老 state 里的梗压根没有 ts，那就让它继续没有 ——
    render 时会被当成过期的跳过。给 now 等于白送它两天的保质期，
    这正是旧名册那次「文本没变却当成新信息」续杯的翻版。
    """
    now = time.time() if now is None else now
    old = {}
    for m in ((old_data or {}).get("memes") or []):
        if isinstance(m, dict):
            q = str(m.get("quote") or "").strip()
            if q:
                old[q] = float(m.get("ts") or 0)
    for m in ((new_data or {}).get("memes") or []):
        if not isinstance(m, dict):
            continue
        q = str(m.get("quote") or "").strip()
        if not q:
            continue
        m["ts"] = now if q not in old else old[q]


def stamp_people(old_data, new_data, now=None, *, entries=(), resolver=None, group_id=''):
    """把模型的 people 提案绑回已核实的来源，返回实际收录的条数。

    来源只有两个合法出处：**本批已处理**的原始发言，或上一版摘要里已收录的
    evidence。收录口径（全部满足才进）：
      · 提案给出能分清的 who 和一条完整 quote（4~240 字、只做空白归一）；
      · quote 逐字等于来源里的原话（指令、图片描述、其他群的话不算；
        归档标记 text_complete 不是 True 的原始记录没有资格当来源）；
      · 该来源的发言人能被 resolver 叫出当前名字、且时间戳齐备；
      · 至少一个话题词（原话里真实出现的 2~24 字词）留存。

    时间口径：**ts 取原始发言时间，不是压缩时间**。模型换个说法复述旧 quote
    时沿用旧来源与旧时间；真的出现一条新消息（新 message_id）才带新时间；
    同一 message_id 无论被压多少次都不会续杯。

    旧摘要里已核实的条目允许被「换个措辞」的提案继承原来源 —— 但同一条 quote
    若同时能对上两个不同发言人（改名的模糊性），宁可丢也不猜是谁说的。
    没被任何提案引用的旧条目随摘要自然退场；legacy 行没有来源，永远不会被复活。
    """
    now = time.time() if now is None else now
    old = {}
    current = {}
    for row in ((old_data or {}).get('people') or []):
        source = _people_evidence(row, now)
        if not source or source['group'] != group_id:
            continue
        who = _people_text(resolver(source['sender']) if resolver else '')
        if who and who != UNAMED_WHO:
            old.setdefault((who, source['quote']), []).append((row, source))
    for entry in entries:
        if (not isinstance(entry, dict) or entry.get('cmd') or entry.get('kind')
                or entry.get('text_complete') is not True
                or entry.get('group', group_id) != group_id):
            continue
        source = _people_evidence({'evidence': {
            'group': group_id, 'sender': entry.get('sender'),
            'message_id': entry.get('message_id'), 'ts': entry.get('ts'),
            'quote': entry.get('text'),
        }}, now)
        if not source:
            continue
        who = _people_text(resolver(source['sender']) if resolver else '')
        if who and who != UNAMED_WHO:
            current.setdefault((who, source['quote']), []).append(source)
    kept, seen = [], set()
    for proposal in (new_data.get('people') or []):
        if not isinstance(proposal, dict):
            continue
        who = _people_text(proposal.get('who') or proposal.get('Who'))
        quote = _people_text(proposal.get('quote'))
        if not who or wordfilter.has_hit(who) or MACHINE_ID.search(who):
            continue
        previous = old.get((who, quote), [])
        candidates = current.get((who, quote), [])
        # 同名 + 同一句原话能对上两个发言人时，身份是模糊的 —— 宁可丢也不把
        # 旧来源转给一个可能是另一个人的人。
        if len({s['sender'] for s in candidates + [s for _, s in previous]}) > 1:
            continue
        sources = candidates or [source for _, source in previous]
        if not sources or len({s['sender'] for s in sources}) != 1:
            continue
        source = max(sources, key=lambda s: s['ts'])
        prior = next(((row, saved) for row, saved in previous
                      if saved['sender'] == source['sender']
                      and saved['message_id'] == source['message_id']), None)
        if prior:
            saved_row, source = prior
            keywords = _people_keywords(saved_row.get('keywords'), source['quote'], who)
        else:
            keywords = _people_keywords(proposal.get('keywords'), source['quote'], who)
        identity = (source['sender'], source['message_id'])
        if not keywords or identity in seen:
            continue
        seen.add(identity)
        item = {'who': who, 'note': source['quote'], 'quote': source['quote'],
                'keywords': keywords, 'evidence': dict(source), 'ts': source['ts']}
        if isinstance(proposal.get('mode'), str):
            item['mode'] = proposal['mode']
        # 裁决注解只能跟着「同一条已核实来源」走：旧条目自带的 decision 才允许
        # 继承，模型提案里塞的 decision 一律不认（不给新来源/新时间背书）。
        if prior:
            saved_decision = people_decision(saved_row)
            if saved_decision:
                item['decision'] = saved_decision
        kept.append(item)
        if len(kept) == 6:
            break
    new_data['people'] = kept
    return len(kept)


# ══════════════════════════ 承诺账本 ══════════════════════════

# 催债文案：让模型照着当前人设说一句，比模板有人味。一天最多一两次，成本可忽略。
# 调用方负责 naming.render 之后再用 —— 人设里的自称不能写死。
SYSTEM_DUN = """你在 QQ 群里扮演「{bot}」，一个爱吐槽、嘴贫但心地不坏的群友。
下面列了几条群里立下却还没兑现的承诺（也可能是玩笑话）。请你挑其中【最该催的那一条】，
用你的口吻写一句催债的话，1~2 句，要自然、要搞笑、要点名道姓，可以带点调侃但不要真伤人。
不要解释，不要列清单，就只输出这一句话。"""


def _promise_text_key(value):
    """One Unicode-aware normalization for identity comparisons, not storage IDs."""
    return "".join(ch for ch in str(value or "").casefold()
                   if not ch.isspace() and not unicodedata.category(ch).startswith("P"))


def _norm_promise_key(who, what):
    # Kept as a comparison helper for legacy callers; IDs are immutable UUIDs.
    return f"{_promise_text_key(who)}|{_promise_text_key(what)}"


def parse_due(text, now=None):
    """把模型给的 due 文本尽量解成时间戳；解不出来返回 None，交给宽限期兜底。

    只处理最常见的几种说法，不追求完备 —— 解不出来顶多是晚点催，不会出错。
    """
    if not text:
        return None
    now = time.time() if now is None else now
    t = str(text).strip()

    m = re.search(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", t)
    if m:
        try:
            return time.mktime(time.strptime(
                f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d} 12:00",
                "%Y-%m-%d %H:%M"))
        except Exception:
            return None

    CN_NUM = {"两": 2, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8,
              "九": 9, "十": 10, "半": 1}

    if "后天" in t:
        return now + 2 * 86400.0
    if "明天" in t or "明早" in t or "明晚" in t:
        return now + 86400.0
    if "下周" in t:
        return now + 7 * 86400.0

    # (?!前) 是为了别把「两天前」当成「两天后」
    m = re.search(r"([0-9两二三四五六七八九十]+)\s*天(?!前)[后内]?", t)
    if m:
        raw = m.group(1)
        n = int(raw) if raw.isdigit() else CN_NUM.get(raw, 0)
        if n:
            return now + n * 86400.0
    m = re.search(r"([0-9两二三四五六七八九十]+)\s*周[后内个]", t)
    if m:
        raw = m.group(1)
        n = int(raw) if raw.isdigit() else CN_NUM.get(raw, 0)
        if n:
            return now + n * 7 * 86400.0

    m = re.search(r"(?:周|星期|礼拜)\s*([一二三四五六日天1-7])", t)
    if m:
        table = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6,
                 "1": 0, "2": 1, "3": 2, "4": 3, "5": 4, "6": 5, "7": 6}
        want = table.get(m.group(1))
        if want is not None:
            cur = time.localtime(now).tm_wday
            delta = (want - cur) % 7 or 7
            return now + delta * 86400.0

    m = re.search(r"(\d{1,2})\s*[号日]", t)
    if m:
        day = int(m.group(1))
        if 1 <= day <= 31:
            lt = time.localtime(now)
            try:
                ts = time.mktime((lt.tm_year, lt.tm_mon, day, 12, 0, 0, 0, 0, lt.tm_isdst))
            except Exception:
                return None
            return ts if ts > now else ts + 30 * 86400.0
    return None


class PromiseBook:
    """每群一份「说好了却没兑现」的账本。

    数据是摘要产出的副产品：每次压缩完用 sync() 对齐一遍 —— 摘要里新出现的加进来，
    摘要里已经消失的（被模型判定为完结/过时）自动撤下。这样账本会自己收敛，
    不需要额外的模型调用去判断「这条兑现了没」。

    催债有次数上限：催满 PROMISE_MAX_NAG 次就自己撤下。
    追着人反复要奶茶那不是幽默，是骚扰。
    """

    def __init__(self, grace_hours=24.0, nag_interval_hours=48.0, max_nag=2, max_items=12):
        self.grace_hours = float(grace_hours)
        self.nag_interval_hours = float(nag_interval_hours)
        self.max_nag = int(max_nag)
        self.max_items = int(max_items)
        self.items = {}      # group_id -> [promise, ...]
        # 催满上限的承诺的**墓碑**：谁、什么事。没有它，下一次压缩 sync 会把
        # 同一件事当新承诺加回来（nag 清零）—— 催债上限形同虚设，
        # 实测罗老板一天被 @ 三次（09-23、10-06 16:40、10-06 20:16）。
        self.finished = {}   # group_id -> [{"who","what"}, ...]

    # ── 存取 ──
    def hydrate(self, raw, finished=None):
        restored = 0
        for gid, rows in (raw or {}).items():
            if not isinstance(rows, list):
                continue
            kept = []
            for r in rows:
                if not isinstance(r, dict):
                    continue
                kept.append({
                    "id": r.get("id") or uuid.uuid4().hex,
                    "member_id": str(r.get("member_id") or ""),
                    "who": r.get("who") or "有人",
                    "what": r.get("what") or "",
                    # Older versions stored only what[:80]. A format flag
                    # distinguishes an incomplete old boundary from new text.
                    "text_complete": bool(r.get("text_complete",
                                                len(str(r.get("what") or "")) != 80)),
                    "since": float(r.get("since") or time.time()),
                    "due_ts": r.get("due_ts"),
                    "due_text": r.get("due_text") or "",
                    "nag": int(r.get("nag") or 0),
                    "last_nag": float(r.get("last_nag") or 0),
                    "last_shown": str(r.get("last_shown") or ""),
                })
            if kept:
                self.items[gid] = kept
                restored += len(kept)
        for gid, tombs in (finished or {}).items():
            if isinstance(tombs, list) and tombs:
                self.finished[gid] = [t for t in tombs if isinstance(t, dict)]
        return restored

    def dump_into(self, data):
        data["promises"] = {gid: rows for gid, rows in self.items.items() if rows}
        if self.finished:
            data["promises_finished"] = {
                gid: tombs for gid, tombs in self.finished.items() if tombs
            }

    @staticmethod
    def _same_person(row, who, member_id=""):
        saved = row.get("member_id") or ""
        if saved and member_id:
            return saved == member_id
        return _promise_text_key(row.get("who")) == _promise_text_key(who)

    def _find(self, rows, who, what, member_id=""):
        key = _promise_text_key(what)
        return next((row for row in rows
                     if self._same_person(row, who, member_id)
                     and _promise_text_key(row.get("what")) == key), None)

    def _find_legacy_prefix(self, rows, proposals, who, what, member_id=""):
        """Migrate one uniquely identifiable old what[:80], without fuzzy match.

        Both sides must be unique: several saved candidates or several distinct
        fresh completions of one prefix are ambiguous and must not share an ID.
        """
        if len(what) <= 80:
            return None
        candidates = [row for row in rows
                      if not row.get("text_complete", True)
                      and len(row.get("what") or "") == 80
                      and self._same_person(row, who, member_id)
                      and what.startswith(row["what"])]
        if len(candidates) != 1:
            return None
        candidate = candidates[0]
        completions = {proposal["what"] for proposal in proposals
                       if self._same_person(candidate, proposal["who"], proposal["member_id"])
                       and len(proposal["what"]) > 80
                       and proposal["what"].startswith(candidate["what"])}
        return candidate if len(completions) == 1 else None

    def _is_finished(self, group_id, who, what, member_id=""):
        """A retired promise remains retired after a member changes their name."""
        what_n = _promise_text_key(what)
        for tomb in self.finished.get(group_id, []):
            if not self._same_person(tomb, who, member_id):
                continue
            text = _promise_text_key(tomb.get("what"))
            if not text or not what_n:
                continue
            if text in what_n or what_n in text:
                return True
            if difflib.SequenceMatcher(None, text, what_n).ratio() >= 0.5:
                return True
        return False

    def list(self, group_id):
        return self.items.get(group_id) or []

    def rename_member(self, group_id, member_id, new, old=None):
        """Update a display label without replacing IDs or nag/shown counters.

        Legacy name-only rows acquire their member ID on an explicit rename.
        The caller checks that the old label is not another member's main name.
        """
        changed = 0
        for row in self.list(group_id) + self.finished.get(group_id, []):
            matches = (member_id and row.get("member_id") == member_id)
            if not row.get("member_id") and old and row.get("who") == old:
                matches = True
            if matches and (row.get("who") != new or row.get("member_id") != member_id):
                row["who"] = new
                row["member_id"] = member_id or ""
                changed += 1
        return changed

    def sync(self, group_id, promises, now=None, member_resolver=None):
        """Align valid summary rows with the ledger, preserving existing IDs.

        member_resolver(name) supplies trusted local member IDs. Model-supplied
        IDs are not used when this resolver is present. Name-only old data keeps
        working and binds to a stable member on the next successful match.
        """
        now = time.time() if now is None else now
        old = self.items.get(group_id, [])
        merged, reused = [], set()
        added = 0
        proposals = []
        for proposal in promises or []:
            if not isinstance(proposal, dict):
                continue
            what = str(proposal.get("what") or "").strip()
            who = str(proposal.get("who") or "有人").strip()[:20]
            if not what:
                continue
            member_id = str((member_resolver(who) if member_resolver else
                             proposal.get("member_id")) or "")
            proposals.append({**proposal, "who": who, "what": what, "member_id": member_id})
        for proposal in proposals:
            who, what, member_id = proposal["who"], proposal["what"], proposal["member_id"]
            if self._find(merged, who, what, member_id):
                continue
            prev = self._find(old, who, what, member_id)
            if prev is None:
                prev = self._find_legacy_prefix(old, proposals, who, what, member_id)
            if prev:
                prev["who"] = who
                if len(what) > len(prev.get("what") or ""):
                    prev["what"] = what
                    prev["text_complete"] = True
                if member_id:
                    prev["member_id"] = member_id
                if prev["id"] not in reused:
                    merged.append(prev)
                    reused.add(prev["id"])
                continue
            if self._is_finished(group_id, who, what, member_id):
                continue
            merged.append({
                "id": uuid.uuid4().hex, "member_id": member_id,
                "who": who, "what": what, "text_complete": True, "since": now,
                "due_ts": parse_due(proposal.get("due"), now),
                "due_text": str(proposal.get("due") or ""),
                "nag": 0, "last_nag": 0.0, "last_shown": "",
            })
            added += 1
        merged.sort(key=lambda row: row.get("since") or 0)
        self.items[group_id] = merged[:self.max_items]
        return added, len(old) - len(reused)

    def take_for_prompt(self, group_id, rows, now=None):
        """Return each promise at most once per day; explicit list() is unaffected."""
        now = time.time() if now is None else now
        today = time.strftime("%Y-%m-%d", time.localtime(now))
        book = self.items.setdefault(group_id, [])
        visible = []
        for proposal in rows or []:
            if not isinstance(proposal, dict):
                continue
            what = str(proposal.get("what") or "").strip()
            who = str(proposal.get("who") or "有人").strip()[:20]
            member_id = str(proposal.get("member_id") or "")
            if not what:
                continue
            row = self._find(book, who, what, member_id)
            if row is None:
                row = {"id": uuid.uuid4().hex, "member_id": member_id,
                       "who": who, "what": what, "text_complete": True, "since": now,
                       "due_ts": None, "due_text": "", "nag": 0, "last_nag": 0.0}
                book.append(row)
            if row.get("last_shown") == today:
                continue
            row["last_shown"] = today
            visible.append(proposal)
        return visible

    # ── 催债 ──
    def get(self, group_id, promise_id):
        return next((r for r in self.list(group_id) if r["id"] == promise_id), None)

    def next_nag(self, row):
        """这条最早什么时候能催：过了宽限期（或说好的时间），且离上次催够久。

        催满上限就是 None。定时任务（scheduler.py）按它定闹钟，dunnable 按它挑。
        """
        if row.get("nag", 0) >= self.max_nag:
            return None
        start = row.get("due_ts") or (row.get("since", 0) + self.grace_hours * 3600.0)
        if row.get("last_nag"):
            start = max(start, row["last_nag"] + self.nag_interval_hours * 3600.0)
        return start

    def dunnable(self, group_id, now=None):
        """该催的承诺：过了宽限期，且离上次催够久，且还没催满上限。"""
        now = time.time() if now is None else now
        return [r for r in self.list(group_id)
                if (t := self.next_nag(r)) is not None and t <= now]

    def mark_nagged(self, group_id, promise_id, now=None):
        now = time.time() if now is None else now
        for r in self.list(group_id):
            if r["id"] == promise_id:
                r["nag"] = int(r.get("nag") or 0) + 1
                r["last_nag"] = now
                if r["nag"] >= self.max_nag:
                    # 催满撤下，但要**留墓碑**：压缩模型看到群里还在聊这事就会把它
                    # 写进摘要，没有墓碑的话 sync 会把它当新承诺加回来（nag 清零），
                    # 催债上限形同虚设 —— 实测罗老板一天被 @ 三次就是这么来的。
                    self.finished.setdefault(group_id, []).append(
                        {"id": r["id"], "member_id": r.get("member_id") or "",
                         "who": r.get("who") or "有人", "what": r.get("what") or ""})
                    self.items[group_id] = [
                        x for x in self.items[group_id] if x["id"] != promise_id
                    ]
                return r["nag"]
        return 0

    def resolve(self, group_id, keyword="", who=None, now=None):
        """手动结清：按关键词或人名撤下。群友说「我兑现了」时用。"""
        rows = self.list(group_id)
        if not rows:
            return 0
        kw = (keyword or "").strip().lower()
        keep, removed = [], 0
        for r in rows:
            hit = False
            if who and (who == r.get("member_id") or who in str(r.get("who") or "")):
                hit = True
            if kw and (kw in str(r.get("what") or "").lower()
                       or kw in str(r.get("who") or "").lower()):
                hit = True
            if hit:
                removed += 1
            else:
                keep.append(r)
        self.items[group_id] = keep
        return removed

    def render(self, group_id, title="还没兑现"):
        """0 token 渲染，用于「查账」命令。"""
        rows = self.list(group_id)
        if not rows:
            return "🧾 【承诺台账】目前是干净的——要么大伙言出必行，要么还没人敢在我面前画饼。"
        out = [f"🧾 【承诺台账】共 {len(rows)} 条："]
        for r in rows:
            due = r.get("due_text") or "没说时间"
            nag = f"，已催 {r['nag']} 次" if r.get("nag") else ""
            out.append(f"  • {r.get('who','有人')}：{r.get('what','')}（{due}{nag}）")
        return "\n".join(out)
