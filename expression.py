"""小王回复里的「〔画〕」「〔语音〕」标记（2026-11-02，见 docs/media-replies.md）。

画不画、念不念、画什么、用什么语气，都由小王自己在回复里写（提示词见
prompts.PROMPT_DRAW_RULES / PROMPT_VOICE_RULES）；代码只认标记、管额度、记结果。

为什么不再让判断层先拍板：判断层决定画、代码直接调接口时，小王本人不知道自己会画、
画了什么 —— 2026-11-01 晚上一边发图一边说「我这边没法直接生成图片」，第二天语音里念
「语音我这儿发不了」。谁说话谁决定，说的和做的才对得上。

认不出标记就当普通文字：宁可少画一张，也不把半截标记发进群里。
"""
import re
from dataclasses import dataclass

# 第一行的标记；全角半角括号、方括号都认 —— 模型偶尔换括号，不值得为此丢一张图。
_TAG = re.compile(r"^\s*[〔【\[［]\s*(画|语音)\s*[〕】\]］]\s*(.*)$")
# 会话记录里系统补的那行（见 record）。模型照着写出来的一律删掉：它不许替系统作证。
_RECORD = re.compile(r"^\s*[（(]\s*系统\s*[:：].*[）)]\s*$")
_TONE = re.compile(r"^\s*[（(][^（）()]{1,20}[）)]\s*$")
# 「〔画〕完整描述：……」—— 把提示词里的说法当成了标题，画师不需要它
_LABEL = re.compile(r"^\s*(完整)?描述\s*[:：]\s*")

# 失败原因 → 会话里记下的那半句。小王下一轮看见的是这个，所以要说人话，也要说实话：
# 「审核没过」和「服务出错」是两回事，群友追问时它得答得上来。
_IMAGE_OUTCOMES = {
    True: "这张图已经发到群里了",
    None: "图发出去了，但没收到回执，群里可能看得到",
    "blocked": "这张没画成：图片审核没过",
    "generation_error": "这张没画成：画图服务出错了",
    "upload_error": "图画好了，但没能发进群",
    "quota": "这张没画：今天的画图额度用完了",
    "busy": "这张没画：手上那张还没画完",
    False: "这张没画：这会儿画不了",
}
VOICE_SENT = "这句是用语音发的"
VOICE_AS_TEXT = "这句本来要用语音，没发成，发的是文字"


@dataclass(frozen=True)
class Said:
    kind: str | None    # None / "image" / "voice"
    spec: str           # image：给画师的描述；voice：语气（可以为空）
    text: str           # 发在群里的那句 / 要念的那句


def parse(body):
    """拆出标记。只认第一处；同一条回复里第二个标记只去掉括号，内容留作正文。"""
    lines = [line for line in (body or "").splitlines() if not _RECORD.match(line)]
    kind, spec, rest = None, "", []
    for line in lines:
        m = _TAG.match(line)
        if m and kind is None:
            kind = "image" if m.group(1) == "画" else "voice"
            spec = m.group(2).strip()
            continue
        if m:
            line = m.group(2)
        rest.append(line)
    text = "\n".join(rest).strip()
    if kind == "voice":
        spec = spec.strip("（）() ")
        # 语气偶尔被写到了下一行：「〔语音〕\n（憋着笑）\n要念的话」
        first, _, more = text.partition("\n")
        if not spec and _TONE.match(first) and more.strip():
            spec, text = first.strip().strip("（）() "), more.strip()
    if kind == "image":
        spec = _LABEL.sub("", spec, count=1)
    if kind == "image" and not spec:
        kind = None   # 只有标记没有描述：没法画，当普通文字
    return Said(kind, spec, text)


def record(said, outcome):
    """记进会话的那条：原样保留标记（小王下次照着写），再补一行实际结果。"""
    if said.kind == "image":
        head = f"〔画〕{said.spec}"
        note = _IMAGE_OUTCOMES.get(outcome, _IMAGE_OUTCOMES[False])
    else:
        head = "〔语音〕" + (f"（{said.spec}）" if said.spec else "")
        note = VOICE_SENT if outcome else VOICE_AS_TEXT
    return "\n".join(part for part in (head, said.text, f"（系统：{note}）") if part)


def image_failed_line(outcome):
    """图没发出去时补的那句。本地话术、不再调模型（群主 2026-11-01：兜底不值得一次生成）。

    小王已经用自己的话接过这单了，这里只交代结果；回执不明（None）不补 —— 图可能已经在群里。
    """
    return {
        "blocked": "这张被图片审核拦下来了，换个画法吧",
        "upload_error": "图画好了，但没发进群，等会儿再试试",
        "quota": "今天画不动了，明天再画",
        "busy": "手上那张还没画完，等会儿再叫我",
    }.get(outcome, "这张没画出来，等会儿再试试") if outcome is not None else ""
