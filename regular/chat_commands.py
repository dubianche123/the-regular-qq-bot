"""本机聊天里的斜杠命令：直接和桌宠聊、或在控制台「聊两句」里打 `/天气 杭州` 这样一行，就能管后台。

命令有固定写法，不经过模型（不花 token），由控制台进程执行。桌宠自己的设置（安静、动效、大小）和动作
（变个魔法、换个节目）作为 command 事件随心跳交给桌宠。管家（模型）在自然语言对话里只能「提议」
同样的命令，你点确认后才执行，确认时走的也是这一条路径。
"""
from __future__ import annotations

from regular.local_chat import MANAGED

_ON, _OFF = "开", "关"
# One spelling per command, the same one HELP shows; anything else is "unknown" and gets HELP back.
_NAMES = {"帮助": "help", "状态": "status", "天气": "weather", "开关": "toggle", "安静": "quiet",
          "动效": "motion", "大小": "scale", "魔法": "magic", "换一个": "next"}
SCALE_RANGE = (0.5, 1.6)
# What the steward may propose for you to confirm (the rest you type yourself).
PROPOSABLE = frozenset({"weather", "toggle", "quiet", "motion", "scale"})

HELP = """可以直接打这些命令（不走模型）：
/天气 杭州　换天气联动的城市（/天气 关　关掉；/天气　看现在）
/开关 画图 关　开关能力：读图、画图、动图、听语音、模型转写、发语音
/安静 开　桌宠安静模式（只做安静的事、不跑不飞）
/动效 关　关掉桌宠的动作和特效
/大小 1.2　桌宠大小（0.5–1.6）
/魔法　变个魔法　　/换一个　换个节目
/状态　她现在怎么样
也可以直接说「把天气换成杭州」，我提议，你点确认才算数。"""


def _switch(word):
    return True if word == _ON else False if word == _OFF else None


def parse(text):
    """A slash command as a small dict, or None when the text is ordinary chat."""
    text = str(text or "").strip()
    if not text or text[0] not in "/／":
        return None
    parts = text[1:].split()
    if not parts:
        return {"name": "help"}
    name = _NAMES.get(parts[0])
    args = parts[1:]
    if name in ("help", "status", "magic", "next"):
        return {"name": name}
    if name == "weather":
        if not args:
            return {"name": "weather", "city": None}
        if len(args) == 1 and _switch(args[0]) is False:
            return {"name": "weather", "off": True}
        return {"name": "weather", "city": " ".join(args)[:40]}
    if name == "toggle" and len(args) == 2 and args[0] in MANAGED and _switch(args[1]) is not None:
        return {"name": "toggle", "flag": MANAGED[args[0]], "label": args[0], "enabled": _switch(args[1])}
    if name in ("quiet", "motion") and len(args) == 1 and _switch(args[0]) is not None:
        return {"name": name, "enabled": _switch(args[0])}
    if name == "scale" and len(args) == 1:
        try:
            value = float(args[0])
        except ValueError:
            value = None
        if value is not None and SCALE_RANGE[0] <= value <= SCALE_RANGE[1]:
            return {"name": "scale", "value": round(value, 2)}
    return {"name": "unknown", "text": text[:40]}


def canonical(command):
    """The one-line text of a parsed command, shown on confirm cards and sent when you confirm."""
    name = command["name"]
    word = "开" if command.get("enabled") else "关"
    if name == "weather":
        return "/天气 关" if command.get("off") else "/天气 " + command["city"] if command.get("city") else "/天气"
    if name == "toggle":
        return f"/开关 {command['label']} {word}"
    if name in ("quiet", "motion"):
        return f"/{'安静' if name == 'quiet' else '动效'} {word}"
    if name == "scale":
        return f"/大小 {command['value']:g}"
    return {"help": "/帮助", "status": "/状态", "magic": "/魔法", "next": "/换一个"}.get(name, "/帮助")


def describe(command):
    """What confirming will do, in plain words."""
    name = command["name"]
    if name == "weather":
        return "关掉天气联动" if command.get("off") else f"把天气城市换成「{command['city']}」"
    if name == "toggle":
        return f"{'打开' if command['enabled'] else '关掉'}「{command['label']}」"
    if name == "quiet":
        return "打开桌宠安静模式" if command["enabled"] else "关掉桌宠安静模式"
    if name == "motion":
        return "打开桌宠的动作和特效" if command["enabled"] else "关掉桌宠的动作和特效"
    if name == "scale":
        return f"把桌宠调到 {command['value']:g} 倍大"
    return canonical(command)


def proposal_from(words):
    """The steward's 〔天气 杭州〕 / 〔桌宠 安静 开〕 line -> a confirmable command, or None."""
    words = " ".join(str(words or "").split())
    head, _, rest = words.partition(" ")
    text = "/天气 " + rest if head == "天气" else "/" + rest if head == "桌宠" else ""
    command = parse(text) if text and rest else None
    allowed = {"weather"} if head == "天气" else {"quiet", "motion", "scale"}      # 〔桌宠 …〕 is only her own settings
    if not command or command["name"] not in allowed or (command["name"] == "weather" and not
                                                         (command.get("city") or command.get("off"))):
        return None
    return {"kind": "command", "command": canonical(command), "label": describe(command)}


def proposal_valid(value):
    """Re-validate a proposal that came back from the bot or a client: only proposable commands."""
    if not isinstance(value, dict) or value.get("kind") != "command" or not isinstance(value.get("command"), str):
        return None
    command = parse(value["command"])
    if not command or command["name"] not in PROPOSABLE:
        return None
    return {"kind": "command", "command": canonical(command), "label": describe(command)}
