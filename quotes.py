"""群友引用（回复）某条消息：引用的是谁、说了什么；群友 @ 的是不是小王（2026-11-03，见 docs/quotes.md）。

11-03 09:00 群友引用小王前一晚给他调的那杯酒，@ 小王问「为什么没有橘光反转爱尔兰」，
连着两次被当成「没叫它」、小王自己选了不接；第三次喊了名字才接上，可它看不见引用的是哪句，
把一个顺着它那杯酒起的名字当成了知识题，答「不知道，不敢瞎编」。两件事都出在事件体：

- @ 在正文里写成 <@成员openid>。@小王 用的是小王在这个群里的成员 openid，跟 robot.id
  不是一回事；mentions 列表又不含机器人自己（官方文档：「不含@机器人自身」）。这个 openid
  没有接口能查，只能认：群友引用小王的话时，QQ 会自动 @ 被引用的人，那个 @ 就是小王。
  认出来记在群的槽位里（self_openid），重启不丢。
- 引用不带原文。每条消息的 message_scene.ext 里有「msg_idx=…」（这条的引用索引），
  引用了别的消息时还有「ref_msg_idx=…」（被引用那条的索引）；小王自己发的消息，索引在发送
  回执的 ext_info.ref_idx。所以这里自己记一份最近几百条「索引 → 谁说的、说了什么」。
"""
import re
import time

SELF = "self"           # QuoteBook 里「小王自己说的」
KEEP = 300              # 每群记最近多少条：一晚上的群聊够了，前一晚的也还在
TEXT_CHARS = 80         # 引用原文只留个开头：认得出是哪句就行，不把整段再灌一遍

_AT = re.compile(r"<@!?([A-Za-z0-9_]+)>")


def _clip(text):
    text = " ".join(str(text or "").split())
    return text if len(text) <= TEXT_CHARS else text[:TEXT_CHARS - 1] + "…"


def scene_of(data):
    """事件体里 SDK 不留的那几样：这条的索引、引用了哪条、引用消息自带的原文（有的话）。"""
    data = data or {}
    out = {}
    # mentions 里标了 bot 的人（SDK 只留 member_openid，把这个标记丢了）
    bots = [m.get("member_openid") for m in (data.get("mentions") or ())
            if isinstance(m, dict) and m.get("bot") and m.get("member_openid")]
    if bots:
        out["bots"] = bots
    for item in ((data.get("message_scene") or {}).get("ext") or ()):
        key, sep, value = str(item).partition("=")
        if sep and value and key in ("msg_idx", "ref_msg_idx"):
            out[key] = value
    # message_type 103（引用消息）把被引用的内容放在 msg_elements 里，格式文档没细说，只取文字当兜底
    texts = [str(e.get("content") or "").strip()
             for e in (data.get("msg_elements") or ()) if isinstance(e, dict)]
    if any(texts):
        out["quoted"] = _clip(" ".join(t for t in texts if t))
    return out


def receipt_ref(receipt):
    """发送回执里这条消息的引用索引；没有就 None。"""
    ext = receipt.get("ext_info") if isinstance(receipt, dict) else getattr(receipt, "ext_info", None)
    ref = ext.get("ref_idx") if isinstance(ext, dict) else getattr(ext, "ref_idx", None)
    return ref if isinstance(ref, str) and ref else None


def at_ids(content):
    """正文里 <@openid> 的那几个 openid，按出现顺序、不重复。"""
    seen = []
    for oid in _AT.findall(content or ""):
        if oid not in seen:
            seen.append(oid)
    return seen


def self_in(content, listed, bots=()):
    """引用小王的话那条消息里，哪个 @ 是小王。

    mentions 不含机器人自己（文档原话），所以正文里的 @ 去掉 mentions 里的人，剩一个就是它；
    万一 mentions 也带上了它，就认标了 bot 的那一个。认不准返回 None ——
    宁可这次不认，也不把别人当成自己。
    """
    ids = at_ids(content)
    flagged = [oid for oid in ids if oid in set(bots or ())]
    if len(flagged) == 1:
        return flagged[0]
    rest = [oid for oid in ids if oid not in set(listed or ())]
    return rest[0] if len(rest) == 1 else None


def render(who, text):
    """拼在这句话前面的那一截：「[回复小王：「……」] 」。"""
    text = _clip(text)
    if not text:
        return ""
    return f"[回复{who}：「{text}」] " if who else f"[回复：「{text}」] "


def strip(text):
    """去掉 render 拼在前面的那一截，只剩他自己说的话（体检比相似度用）。"""
    text = text or ""
    if text.startswith("[回复"):
        end = text.find("」] ")
        if end != -1:
            return text[end + 3:]
    return text


class QuoteBook:
    """state.data["quotes"][群] = [[索引, 谁说的, 开头, 时间], ...]，只留最近 KEEP 条。

    谁说的：SELF 是小王，其余是群友的 openid（显示成什么称呼由调用方按当时的档案定）。
    """

    def __init__(self, state):
        self._state = state

    def remember(self, group, ref, who, text, now=None):
        if not (group and ref and who and text):
            return
        rows = self._state.data.setdefault("quotes", {}).setdefault(group, [])
        rows.append([ref, who, _clip(text), time.time() if now is None else now])
        del rows[:-KEEP]
        self._state.mark_dirty()

    def find(self, group, ref):
        if not (group and ref):
            return None
        for row in reversed((self._state.data.get("quotes") or {}).get(group) or ()):
            if row[0] == ref:
                return {"who": row[1], "text": row[2], "time": row[3]}
        return None
