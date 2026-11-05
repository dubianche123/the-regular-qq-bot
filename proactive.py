"""主动开口的本轮引用编号；平台索引只由调用方留在本地。"""

import expression

HUMAN_LIMIT = 10
OWN_LIMIT = 10
TEXT_CHARS = 220
CONTEXT_CHARS = 6000


def _text(text):
    text = " ".join(str(text or "").split())
    text = text.replace("[图片]", "[图片：暂不可读，尚无描述]")
    if len(text) <= TEXT_CHARS:
        return text
    half = TEXT_CHARS // 2
    return text[:half] + "…" + text[-(TEXT_CHARS - half - 1):]


def own_text(row):
    """实际结果与正文一起展示；未确认不能被读成已经做完。"""
    medium = {"text": "文字", "image": "图片", "animation": "动图", "voice": "语音"}.get(
        row.get("medium", "text"), "消息")
    result = {"delivered": "已送达", "unconfirmed": "送达未确认，勿重复发送",
              "failed": "未完成"}.get(row.get("status", "delivered"), "结果不明")
    return f"{medium}{result}：{_text(row.get('text'))}"


def render_context(rows, own_rows, *, now, label):
    """合并有界现场；只有同一份 rows 里的人类原话可以按编号引用。"""
    timeline = []
    for index, row in enumerate(rows, 1):
        who = _text(label(row.get("sender")))[:24]
        timeline.append((float(row.get("time") or 0), f"[{index}] {who}：{_text(row.get('text'))}"))
    for row in list(own_rows)[-OWN_LIMIT:]:
        timeline.append((float(row.get("time") or 0), f"你（实际结果）·{own_text(row)}"))
    lines = []
    for timestamp, line in sorted(timeline, key=lambda item: item[0]):
        age = max(0, int(now - timestamp))
        ago = f"{age}秒前" if age < 60 else f"{age // 60}分钟前"
        lines.append(f"{ago}｜{line}")
    header = "（以下按先后记录现场；带编号的是群友原话，‘你’是机器人自己的实际结果。图片暂不可读时没有画面证据。）\n"
    omitted = "（更早的现场已略去；仅可引用以下可见编号，编号保持原映射。）\n"
    # 丢整条较早记录，不截断一半消息或重新编号；最近纠正与交付结果优先留下。
    if len(header) + sum(len(line) + 1 for line in lines) > CONTEXT_CHARS:
        while lines and len(header) + len(omitted) + sum(len(line) + 1 for line in lines) > CONTEXT_CHARS:
            lines.pop(0)
        header += omitted
    return header + "\n".join(lines)


def render_topics(rows):
    return "\n".join(f"[{i}] {_text(row.get('text'))}"
                     for i, row in enumerate(rows, 1))


def parse(body, rows):
    """返回正文与被引用的原始行；不接、无正文或引用协议错误就不发。"""
    if not body or expression.parse(body).kind == "pass":
        return None
    first, sep, rest = body.strip().partition("\n")
    first = first.strip()
    if not sep:
        return None
    if first == "〔新话题〕":
        source = None
    elif first.startswith("〔引用 ") and first.endswith("〕"):
        number = first[len("〔引用 "):-1]
        if not number.isascii() or not number.isdecimal():
            return None
        index = int(number) - 1
        if not 0 <= index < len(rows):
            return None
        source = rows[index]
    else:
        return None
    if "〔新话题〕" in rest or "〔引用" in rest:
        return None
    text = expression.parse(rest).text.strip()
    return (text, source) if text else None
