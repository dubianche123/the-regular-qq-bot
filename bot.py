"""QQ 群聊机器人 —— 业务逻辑入口。

配置见 config.py，提示词见 prompts.py，名字解析见 naming.py，状态/上下文/限流见 storage.py。
本文件只负责：接消息 → 判定要不要回 → 走本地彩蛋还是调模型 → 安全发出去。
"""

import asyncio
import collections
import contextvars
import copy
import hashlib
import json
from types import SimpleNamespace
import logging
import logging.handlers
import os
import random
import re
import signal
import shlex
import sys
import time
import uuid
import weakref
from datetime import datetime, timezone

import aiohttp
import certifi
import botpy
from botpy.connection import ConnectionState
from botpy.errors import AuthenticationFailedError, ForbiddenError
from botpy.message import GroupMessage, C2CMessage, Message

import config
import digest as digest_mod
import expression
import image_generation
from regular import decisions as decisions_mod
import naming
import prompts
import qqtext
import quotes
import recall as recall_mod
import relations
import reminders
import scheduler as scheduler_mod
import search as search_mod
import slowwork
import storage
import wordfilter
import voice_event
import voice_runtime
import voice_output_runtime
from archive import _VOICE_URL

logger = logging.getLogger("qqbot")

# ══════════════════════ 1. 证书 / SSL / 日志 ══════════════════════

os.environ["SSL_CERT_FILE"] = certifi.where()


def setup_logging():
    """带时间戳 + 自动轮转的日志。改造前 bot.log 会无限增长且每行都没有时间信息。"""
    os.makedirs(os.path.dirname(config.LOG_FILE), exist_ok=True)
    root = logging.getLogger()
    root.setLevel(config.LOG_LEVEL)
    # botpy DEBUG includes raw gateway events, including signed media URLs and ASR.
    sdk_logger = logging.getLogger("botpy")
    if sdk_logger.getEffectiveLevel() < logging.INFO:
        sdk_logger.setLevel(logging.INFO)
    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    )
    file_handler = logging.handlers.RotatingFileHandler(
        config.LOG_FILE,
        maxBytes=config.LOG_MAX_BYTES,
        backupCount=config.LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)

    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(fmt)
    root.addHandler(stream)


def patch_aiohttp_ssl():
    """botpy 的 WS 连接在某些本机证书链环境下会握手失败，必要时要关掉校验。

    默认开启校验；只有显式 SSL_VERIFY=false 才启用兼容模式。
    """
    if config.SSL_VERIFY:
        logger.info("🔒 SSL 校验已开启（SSL_VERIFY=true）")
        return
    logger.warning("🔓 SSL 校验已关闭（本机证书兼容模式），建议本机证书正常时设 SSL_VERIFY=true")

    original_init = aiohttp.TCPConnector.__init__

    def patched_init(self, *args, **kwargs):
        kwargs.pop("verify_ssl", None)
        kwargs.pop("ssl_context", None)
        kwargs.pop("fingerprint", None)
        kwargs["ssl"] = False
        original_init(self, *args, **kwargs)

    aiohttp.TCPConnector.__init__ = patched_init


# botpy 官方 SDK 缺 group_message_create（全量群消息）解析器，这里补上
def parse_group_message_create(self, payload):
    _message = GroupMessage(self.api, payload.get("id", None), payload.get("d", {}))
    self._dispatch("group_message_create", _message)


class SceneGroupMessage(GroupMessage):
    """SDK 丢掉的引用索引（message_scene.ext）挂在 _scene 上，原始事件不留（见 quotes.py）。"""
    __slots__ = ("_scene",)

    def __init__(self, api, event_id, data):
        super().__init__(api, event_id, data)
        self._scene = quotes.scene_of(data)


ConnectionState.parse_group_message_create = voice_event.make_voice_parser(
    SceneGroupMessage, "group_message_create")
ConnectionState.parse_group_at_message_create = voice_event.make_voice_parser(
    SceneGroupMessage, "group_at_message_create")
ConnectionState.parse_c2c_message_create = voice_event.make_voice_parser(
    C2CMessage, "c2c_message_create")

# ══════════════════════ 1.5 网关看门狗 ══════════════════════
#
# ⚠️ botpy 的重连链路有个盲区：掉线（1006）后重不重连，取决于 `ws_connect` 的接收循环
# 能不能正常退出。如果 TCP 半死（对端静默掉线、没有 close 帧到达），`await receive()` 会
# 永远挂住——不报错、不重连、心跳任务也各自闭嘴，整条链就这么干等。
# 实测（2026-09-19 13:15:47）：1006 后 **18 分钟**没有任何重连尝试，期间一个日志都没有。
#
# 解法：包一层 `_is_system_event`（botpy 每收到一条下行都会经过它，心跳 ACK 也不例外），
# 记下「最后一次网关动静」的时刻；再看门狗协程定期巡检，静默超过阈值就**主动踢一刀**——
# 关掉当前 ws，让接收循环拿到 CLOSED 退出，botpy 自己的重连链路随即接手。
# 心跳每 30s 一次，正常情况下静默永远不会超过一分钟。

_ws_watch = {"last_rx": 0.0, "conn": None, "kicks": 0}


def install_ws_watchdog():
    """挂上「网关最后一响」的记录钩子。必须在 client.run() 之前调用一次。"""
    from botpy.gateway import BotWebSocket

    original = BotWebSocket._is_system_event

    async def patched(self, message_event, ws):
        _ws_watch["last_rx"] = time.time()
        _ws_watch["conn"] = ws
        handled = await original(self, message_event, ws)
        # READY is confirmed by on_ready; HELLO/heartbeats are not authentication.
        if (STATUS.lifecycle not in {"stopped", "error"}
                and message_event.get("op") == BotWebSocket.WS_DISPATCH_EVENT
                and message_event.get("t") == "RESUMED"):
            STATUS.gateway_connected = True
            STATUS.lifecycle = "ready"
            STATUS.write()
        return handled

    BotWebSocket._is_system_event = patched
    _ws_watch["last_rx"] = time.time()
    logger.info("🐕 网关看门狗已挂上（静默 %.0f 秒即踢一刀重连）",
                config.WS_WATCHDOG_STALE_SECONDS)


async def _ws_watchdog_tick():
    """看门狗巡检一轮。返回 True = 这次踢了。独立成函数是为了好测。"""
    conn = _ws_watch.get("conn")
    if conn is not None and conn.closed:
        STATUS.gateway_connected = False
        if STATUS.lifecycle != "stopped":
            STATUS.lifecycle = "reconnecting"
        STATUS.write()
    if not _ws_watch["last_rx"]:
        return False
    silent = time.time() - _ws_watch["last_rx"]
    if silent <= config.WS_WATCHDOG_STALE_SECONDS:
        return False
    # 先记账再动手：不管踢没踢成都重置计时，防止重连进行中被连环误踢
    _ws_watch["last_rx"] = time.time()
    if conn is None or conn.closed:
        # 连接已经是死的：重连链路理应正在跑，别再补刀，等新连接的下一响
        logger.warning("🐾 看门狗：网关静默 %.0f 秒（连接已关闭，等重连链路接手）", silent)
        return False
    STATUS.gateway_connected = False
    STATUS.lifecycle = "reconnecting"
    STATUS.write()
    _ws_watch["kicks"] += 1
    logger.warning("🐾 看门狗：网关 %.0f 秒没有任何下行（心跳 ACK 也没来），"
                   "主动断开让它重连（第 %d 次）", silent, _ws_watch["kicks"])
    await conn.close(code=4000)
    return True


async def ws_watchdog_loop():
    """看门狗主循环：每 15 秒巡检一次。"""
    await STATUS.sleep(config.WS_WATCHDOG_STALE_SECONDS)  # 先给首连留足时间
    while True:
        try:
            await _ws_watchdog_tick()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            STATUS.error(e)
            logger.error("❌ 看门狗巡检异常（不影响主流程）: %s", e)
        await STATUS.sleep(15)



# ══════════════ 领域模块（2026-10-06 拆分；以下是兼容别名） ══════════════
# 运行时单例：runtime.py │ 供应商容错：providers.py │ 好感度结算：affinity.py │ 昵称域：nicking.py
# bot.py 本体只留「消息流 + 指令路由 + 组装」。别名让既有引用与测试完全兼容。
import runtime
import activation
import providers
from runtime import owner_label, load_owner, named_members
call_model = providers.call_model
probe_provider = providers.probe_provider
providers.initialize(budget=runtime.BUDGET, decision_budget=runtime.DECISION_BUDGET,
                     on_usage=runtime.STATE.mark_dirty)
_voice_keys = config.load_api_keys("openai")
VOICE = voice_runtime.VoiceRuntime(
    budget=runtime.BUDGET, mark_dirty=runtime.STATE.mark_dirty, persist=runtime.STATE.save_now,
    api_key=_voice_keys[0] if _voice_keys else "",
    base_url=config.PROVIDER_PRESETS["openai"]["base_url"] or "https://api.openai.com/v1",
    model=config.VOICE_TRANSCRIPTION_MODEL)
del _voice_keys
_VOICE_OUT = voice_output_runtime.VoiceOutputRuntime(
    config, next(iter(config.load_api_keys("openai")), ""))


async def _recognize_voices(voices, event_prefix):
    """Recognize at most three distinct current attachments in arrival order."""
    if not event_prefix or not 0 < len(voices) <= 3:
        return None
    results = await asyncio.gather(*(VOICE.processor.process(
        voice, event_key=f"{event_prefix}:{index}") for index, voice in enumerate(voices)))
    texts = []
    recognized = False
    for result in results:
        text = result.transcript if result.status == "ok" else None
        if (isinstance(text, str) and text and "\x00" not in text
                and not wordfilter.has_hit(text) and not _VOICE_URL.search(text)):
            texts.append(text)
            recognized = True
        else:
            texts.append("（一段语音未识别）")
    return "；".join(texts) if recognized else None

from health import STATUS
from delivery import (Sender, PendingSend, DeliveryResult, passive_reply_window, message_age_seconds,
                      GROUP_PASSIVE_WINDOW_SECONDS, C2C_PASSIVE_WINDOW_SECONDS)
import media
from affinity import (affinity_budget, apply_affinity_delta_capped,
                      apply_digest_affinity, apply_digest_modes, affinity_ledger)
# 背景前言：每条背景都挂的一行提醒。完整的读法说明（别张冠李戴、别复读、[图片：…] 行
# 照着说）每轮都一样，2026-11-01 挪进了稳定头（prompts.PROMPT_SHARED_RULES）吃前缀缓存；
# 这里只留最要紧的一句，挨着背景本身 —— 模型最容易犯浑的位置就是读背景那一刻。
# 回话时，这人多久之内自己发的图一定放进背景，不受亲密度条数限制（见 on_group_message）；
# 这些图和他这条自己带的图还没读完，就先回 IMAGE_HOLD_LINE（见 await_images）
OWN_IMAGE_SECONDS = 120.0
IMAGE_HOLD_LINE = "我看看图，等我一下。"


def _own_recent_image(row, sender, now):
    """背景里这一条是不是这人两分钟内自己发的图。"""
    return (row["sender"] == sender and "[图片" in row["text"]
            and now - float(row.get("time") or 0) <= OWN_IMAGE_SECONDS)
OWN_DRAWING_SECONDS = 600.0


def _own_recent_drawing(line, now):
    """它自己十分钟内画的图（own_lines 里的「[图：…]」）。背景只留最近几句，画过什么不能跟着被挤掉：
    2026-11-04 11:43 背景里没了它 11:38 画的奶蛙，它只看见自己说过「只看到图片标记」，
    就对着自己画的蛙说「没真看见图」（见 docs/image-before-reply.md）。"""
    return line["text"].startswith("[图：") and now - line["time"] <= OWN_DRAWING_SECONDS


_BG_PREAMBLE = "（每句开头标的是发言人；‘当前说话人’与眼前这位是同一人，‘你’是你自己刚在群里说的，其余是其他群友；拿不准是谁就别点名）"


from nicking import (extract_mentions, mention_nicks_from_content, mention_nick_from_content,
                     learn_display_names, mention_label_for, strip_call, refresh_names,
                     sync_rename, sync_clear, reserved_nick_names, nick_locked,
                     set_nick_locked, taken_nick_names, reset_nick_flood,
                     nick_block_reason, nick_note_reject, nick_note_accept)




group_buffers = collections.defaultdict(
    lambda: collections.deque(maxlen=config.GROUP_BUFFER_SIZE)
)
for gid, items in (runtime.STATE.data.get("buffers") or {}).items():
    group_buffers[gid] = collections.deque(items, maxlen=config.GROUP_BUFFER_SIZE)

# 小王自己在群里说过的话（只记确认送达的模型回复；纯内存，重启就空）。拼进【群聊最近背景】：
# group_buffers 和归档只收人话，背景里看不见它自己，就分不出眼前这句是不是在接它刚才的话
# （2026-11-02 改成没叫它的消息也由它自己定接不接，这一条就成了必需的）。
own_lines = collections.defaultdict(
    lambda: collections.deque(maxlen=config.GROUP_BUFFER_SIZE)
)


def _note_own_line(group_id, text):
    if group_id and text:
        own_lines[group_id].append({"time": time.time(), "text": text})


# ── 认出「这是在跟小王说话」：引用它的话、@ 它（2026-11-03，见 quotes.py）──
_recent_at_ids = collections.OrderedDict()   # 消息 ID -> 正文里的 @（@ 事件晚到时拿来比）
_sent_ref_missing = []


def _self_openids(group_id, bot_id):
    """小王的几个 ID：robot.id，加上认出来的它在这个群的成员 openid。"""
    slot = (runtime.STATE.data.get("groups") or {}).get(group_id) or {}
    return {oid for oid in (bot_id, slot.get("self_openid")) if oid}


def _learn_self(group_id, oid, how):
    if not (group_id and oid):
        return
    slot = observe_group(group_id)
    if slot.get("self_openid") != oid:
        slot["self_openid"] = oid
        runtime.STATE.mark_dirty()
        logger.info("🪪 认出小王在这个群的 @（%s，%s）", oid[-4:], how)


def _learn_self_from_late_at(message):
    """@ 事件比全量那条晚到（那条已经按没叫它处理过了）：@ 事件的正文去掉了 @机器人，
    跟先到那条的 @ 一比，少掉的那一个就是小王。这一条补不回来，下一条认得。"""
    if _self_openids(message.group_openid, "") or message.id not in _recent_at_ids:
        return
    before = _recent_at_ids[message.id]
    logger.info("📨 @ 事件晚到（先到的全量那条没认出 @小王）")
    if before:
        gone = [oid for oid in before if oid not in quotes.at_ids(message.content)]
        if len(gone) == 1:
            _learn_self(message.group_openid, gone[0], "@ 事件晚到时比出来的")


def _remember_sent(group_id, receipt, text):
    """小王发出去的话记进引用索引：群友引用它，才认得出是在回小王、回的哪句。"""
    ref = quotes.receipt_ref(receipt)
    if ref:
        runtime.QUOTES.remember(group_id, ref, quotes.SELF, text)
    elif receipt and not _sent_ref_missing:
        _sent_ref_missing.append(True)
        logger.warning("⚠️ 发送回执里没有引用索引（ext_info.ref_idx），群友引用小王的话认不出来")


# 小王上一次在这个群里没被叫就开口的时间（键名沿用旧的「插嘴冷却」，落盘位置不变）
group_last_random_reply = dict(runtime.STATE.data.get("cooldowns") or {})
processed_msg_ids = collections.deque(maxlen=config.DEDUP_MAX)


def _who_label(group_id, oid):
    """openid → 群里的人话称呼：认领过的称呼 > 群里挂的显示名 > 泛称。

    以前这里拼 openid 后四位，结果模型把它当成名字复读进了回复 —— 群里没人
    看得懂。聊天背景和读图上下文共用这一份（2026-10-20：两处渲染不许漂移）。
    """
    r = runtime.RELATIONS.get(group_id, oid, create=False)
    return ((r or {}).get("nick")
            or runtime.DISPLAY_NAMES.of(group_id, oid)
            or "一位群友")


def _vision_context(group_id):
    """读图要带的群聊上下文（2026-10-20）：最近几条「昵称：文本」。

    多模态模型看图本来就该带着对话看——知道大家在聊什么，才知道这张图是
    什么语境下发的、值得描述什么。固定最近 6 条（读图是后台工具调用，不按
    亲密度伸缩）；每条截 60 字防灌屏；必过 _who_label（openid 只许进日志）
    和 refresh_names（旧称呼改写），与聊天背景同一套渲染，不许漂移。
    """
    buf = group_buffers.get(group_id)
    if not buf:
        return ""
    now_ts = time.time()
    fresh = [b for b in buf if now_ts - float(b.get("time") or 0)
             <= config.GROUP_BUFFER_TTL_SECONDS]
    if not fresh:
        return ""
    lines = [f"- {_who_label(group_id, b['sender'])}：{str(b.get('text') or '')[:60]}"
             for b in fresh[-6:]]
    return refresh_names(group_id, "\n".join(lines))


media.initialize(state=runtime.STATE, archive=runtime.ARCHIVE,
                 buffers=group_buffers, budget=runtime.BUDGET,
                 context_fn=_vision_context)


# 主动发言（催债）需要拿到 client 实例，这里存个引用
_bot_ref = {"client": None}
_media_tasks = set()
_background_tasks = set()

FALLBACK_REPLIES = [
    "（战术后仰揉了揉眼睛）这条信息量有点超纲，容我缓缓",
    "（正在专心摸鱼中）你这话把我 CPU 干烧了，让我冷静两秒",
    "（摘下耳机假装正经）等一下，先把手里这条鱼摸完",
    "（翻小本本）这条有点意思，先记上，回头连本带利一起算",
    "（卡壳中）你先说着，我脑子马上就位",
]
_last_fallback = None


def _pick_fallback():
    """兜底文案不放回连抽：网络抖动期连着两条「没看清」，群里看着就像坏了。

    另外全池不许出现「再说一遍 / 再艾特我一次」式的许诺——兜底触发时模型根本
    没有应答，许诺了也兑现不了（2026-10-15 实测连着两条装瞎求复读，群主看穿）。
    """
    global _last_fallback
    pool = [t for t in FALLBACK_REPLIES if t != _last_fallback] or FALLBACK_REPLIES
    _last_fallback = random.choice(pool)
    return _last_fallback

_throttle_notice = {}  # group_id -> last notice timestamp






# 无人认领时，首条非空 C2C 私聊自动持久认主；群聊消息永远不授权。
OWNER_HINT_DELAY_SECONDS = 10 * 60
OWNER_CLAIM_HINT = "（顺带提醒一下：我还没有认主，部署者私聊给我发句话就会自动绑定。）"

# 群里出现这些字样 = 有人在试着认领。**只用来指路去私聊，永远不会授予权限。**
# 「我是群主」留着是因为群友的第一反应就是喊这句，得把人接住。
OWNER_CLAIM_PROBE_WORDS = ("我是群主", "认领群主", "认领一下")

# 私聊收到改名命令时的回复。
#
# 为什么必须在这里挡一句：称呼是**按群**存的（`群号|openid`），而私聊消息里没有群号 ——
# 改了也不知道该改在哪个群。以前的坑是这句话直接喂给模型，被当成闲聊回了个玩笑，
# 当事人完全不知道自己那条命令压根没生效（「叫不动它」的错觉就是这么来的）。
# 所以这里是**明说**：不静默吞掉，也不假装受理。0 token、不扣额度。
NICK_PRIVATE_CHAT_HINT = (
    "（把小本本合上了）称呼这事得回群里办 —— 名字是按群记的，"
    "私聊这儿我看不出你说的是哪个群。回群里 @ 我一句「叫我 XXX」就行，"
    "随时能改，以最新一次为准。")

# 普通群友想给别人起名。同理必须**明说**：解析层按权限把这种意图整个吞掉了
# （allow_other=False），不补一句的话请求会掉进闲聊 —— 机器人顺着接一句
# 「别别别，这辈分乱套了」，群里看着像在商量、甚至像改成了，其实一个字都没存。
NICK_OTHER_DENIED = (
    "（赶紧用手按住了小本本）给别人起外号这事得{owner}点头才行。"
    "你要是自己想改称呼，直接说「叫我 XXX」就好，随时能改。")

# 起名锁上之后，普通群友想改称呼得到的答复。同一条铁律：没办成必须**明说** ——
# 静默吞掉再掉进闲聊，是最糟的一种失败（看着像在商量、甚至像办成了）。
NICK_LOCKED_DENIED = (
    "（把小本本锁进了抽屉）现在群里锁着名字，改称呼这事我先不办。"
    "要落名得{owner}点头 —— 让他 @你 说「叫 XXX」就行。")


_owner_hint_locks = weakref.WeakValueDictionary()


def owner_hint_lock(group_id):
    lock = _owner_hint_locks.get(group_id)
    if lock is None:
        lock = asyncio.Lock()
        _owner_hint_locks[group_id] = lock
    return lock


def observe_group(group_id, now=None):
    """Remember the first observed join/message; later activity must not restart the grace period."""
    slot = runtime.STATE.data.setdefault("groups", {}).setdefault(group_id, {})
    if "first_seen" not in slot:
        slot["first_seen"] = time.time() if now is None else now
        runtime.STATE.mark_dirty()
    return slot


def owner_hint_pending(group_id, now=None):
    """After a grace period, include one reminder with an ordinary group reply."""
    if runtime.OWNER_OPENID or not group_id:
        return False
    slot = observe_group(group_id, now)
    now = time.time() if now is None else now
    return not slot.get("owner_hint") and now - slot["first_seen"] >= OWNER_HINT_DELAY_SECONDS


def claim_redirect_pending(group_id):
    """群里有人试着认领时，要不要正面回一句「去私聊办」。每个群最多回一次。

    单独一个槽位，**不和 owner_hint 共用**：一个是「悄悄捎在回复末尾」，一个是「正面顶一句」，
    两条路都可能先被触发；共用一个标记会让另一条永远不出现。
    """
    if runtime.OWNER_OPENID or not group_id:
        return False
    slot = runtime.STATE.data.setdefault("groups", {}).setdefault(group_id, {})
    if slot.get("claim_redirect"):
        return False
    return True


def looks_like_claim_attempt(text):
    """群里有人在试着认领。只用于指路，**不用于授权**。

    群里宁可判宽一点：认错了不过是多一句引导（0 token），漏掉了当事人会一直找不到入口。
    """
    return any(w in (text or "") for w in OWNER_CLAIM_PROBE_WORDS)


# 对方喊停：他明确表示被你的调侃弄不舒服了。
# 判宽一点 —— 误判的代价只是「这次不开玩笑」，漏判的代价是把人越推越远。
RE_BACK_OFF = re.compile(
    r"别(?:这么|这样|再|老|总)?(?:损|嘲|怼|阴阳|挤兑|挖苦|讽|笑话|埋汰)我?"
    r"|(?:太|有点|有够|很|好)?(?:过分|过火|伤人|难听|扎心|难堪)"
    r"|(?:不要|别)(?:这样|这么)(?:说|讲话|说话|跟我)(?:话|了)?"
    r"|说(?:得)?(?:太|有点|这么)(?:狠|重|过分|难听)"
    r"|(?:听着|听)?(?:不舒服|不高兴|难受|不是滋味)"
    r"|认真点|正经点|我不是开玩笑|来真的"
)
# 刻意**不收**「别闹了」：群里这句太常见（「别闹了，说正事」），收了机器人天天变正经，
# 人设就没了。喊停信号只收那些明确指向「你刚才说话的方式」的表达。


def looks_like_back_off(text):
    """对方是不是在喊停（「别这么损我」「有点过分了」）。

    为什么不能交给模型自觉：它已经在错误方向上跑了几轮，等你下一句提示它，
    中间那几句早就把人怼跑了。实测用户连说两次，它每次都加码 —— 第二次还嘴硬
    「你平时损我也没见手软」。所以这里用正则一眼认出来，直接下死命令。
    """
    return bool(RE_BACK_OFF.search(text or ""))




# ══════════════════════ 4. AI 调用 ══════════════════════
































# 记账标签。协议写法是 <CMD>{"aff": 0}</CMD>（prompts.CMD_PROTOCOL），模型会把它写歪：
# 2026-11-03 下午 gpt-6-luna 四条原样进了群——「〔CMD>{"aff":0}」「换行后 < CMD>{"aff":1}」
# 「〈CMD>{"aff":0}」，收尾的 </CMD> 全丢了。同一份提示词里〔画〕〔不接〕〔提醒〕用全角括号，
# 会话历史里也留着这些标记（expression.record），两套写法串到了一起。
# 所以认「CMD + 右括号 + {…}」这副骨架，左括号、收尾标签写成什么样都一并拿走：
# 正常说话写不出 CMD>{，单独的括号文字（〈红楼梦〉、【公告】、<3）碰不到。
_LB, _RB = r"[<＜〈《〔【\[［]", r"[>＞〉》〕】\]］]"
CMD_TAG = re.compile(
    rf"(?:{_LB}\s*)?CMD\s*{_RB}\s*(\{{[^{{}}]*\}})(?:\s*{_LB}\s*/\s*CMD\s*{_RB})?")
# 连骨架都没写全的残片（2026-10-15「<CMD!」），见 split_cmd
_CMD_STUB = re.compile(r"<\s*CMD")


# 采样乱码特征（2026-10-15 <CMD! 残片、2026-10-19 0A9F%F5，均出自 glm-4.7）：
# 中文闲聊回复里出现**连续 4+ 个无小写、无空格**的大写/数字/符号串。闸门放行正常
# 缩写（CPU/KKP/GG/1080P），只拦带 %、密集符号或「3+ 大写夹数字」的——后一类会
# 误伤 RTX4090 这类长型号，但只重试一次不吞回复，代价可忽略。
_GARBAGE_RUN = re.compile(r"[0-9A-Z%&#*\"@$]{4,}")


def _garbage_run(text):
    """返回正文里的疑似采样乱码段；没有则 None。"""
    for run in _GARBAGE_RUN.findall(text or ""):
        symbols = sum(1 for c in run if c in "%&#*\"@$")
        uppers = sum(1 for c in run if c.isupper())
        digits = sum(1 for c in run if c.isdigit())
        if symbols >= 2:
            return run
        if symbols >= 1 and uppers >= 1 and digits >= 1:
            return run
        if uppers >= 3 and digits >= 1:
            return run
    return None


# 2026-11-01 网关 glm-5.3-flash 连着三条把整段回复包成 <p>…</p>。QQ 不渲染 HTML，
# 标签原样出现在群里；回复又进了会话历史，下一条照着学。记账协议本身也用尖括号，
# 所以只在拆掉 <CMD> 之后对正文做。
_MARKUP = re.compile(r"</?[A-Za-z][A-Za-z0-9]*(?:\s[^<>]*)?/?>")


def split_cmd(raw):
    """把模型输出拆成「正文」和「记账标签」；正文顺手去掉 HTML 标签。

    关键是容错：解析失败绝不能影响对话本身，退化成「正文即原文、不计分」即可。
    """
    if not raw:
        return "", None
    match = CMD_TAG.search(raw)
    if match and not (match.group(0).startswith("<CMD>") and match.group(0).endswith("</CMD>")):
        logger.warning("🧯 记账标签写歪了，已按协议拆掉：%s", match.group(0)[:40])
    body = CMD_TAG.sub("", raw)
    stub = _CMD_STUB.search(body)
    if stub:
        # ⚠️ 容错第二层（2026-10-15 实测）：模型偶尔把标签**打坏**——群里出现过
        # 「想卖&*%"#$#想<CMD!卖了…」，`<CMD!` 连骨架都不全，整个漏进了群里。
        # 协议规定标签永远在最末尾，所以正文里残留任何 <CMD 片段，从那里砍到结尾，
        # 并把原文记进日志——一次是模型抽风，两次以上就是新失败模式。
        logger.warning("🧯 模型输出含打坏的协议标签，已截断。原文: %s", raw[:160])
        body = body[:stub.start()]
    body = _MARKUP.sub("", body).strip()
    if not match:
        return body, None
    try:
        payload = json.loads(match.group(1))
    except Exception:
        return body, None
    return body, payload if isinstance(payload, dict) else None


# 本轮回复的注入痕迹（task-local）：只记「个人原话这次真的进去了没有」，
# 不落任何提示词内容。reply_in_session 每轮 set/reset，直接调 get_ai_reply
# 的旁路拿到的是默认值 False —— 没进就不许自称进过。
_reply_trace = contextvars.ContextVar(
    "regular_reply_trace", default=None)

# 每轮输入记录里不存的入参：session_id 单列；allow_fallback 回放时一律 False。
_TURN_SKIP_PARAMS = ("session_id", "allow_fallback")
_release_id = None


def code_release():
    """当前跑的是哪版代码：REGULAR_RELEASE 优先，其次 git 短哈希，都没有就是包版本号。只算一次。"""
    global _release_id
    if _release_id is None:
        _release_id = os.environ.get("REGULAR_RELEASE", "").strip()
        if not _release_id:
            import subprocess
            try:
                _release_id = subprocess.run(
                    ["git", "rev-parse", "--short", "HEAD"],
                    cwd=os.path.dirname(os.path.abspath(__file__)),
                    capture_output=True, text=True, timeout=2).stdout.strip()
            except (OSError, subprocess.SubprocessError):
                _release_id = ""
        if not _release_id:
            from regular import __version__
            _release_id = f"v{__version__}"
    return _release_id


def _record_turn(params, head, flags, messages, max_tokens, raw, retried):
    """一轮回复生成的全部输入原样进 data/turns（见 turns.py）。私聊不记；写失败不影响回复。

    稳定头只存哈希和长度：回放时由待测代码现拼，比对哈希就知道头变没变。
    会话历史存「这一轮真的送出去的那段」（已裁剪）；每日记忆就是入参里的 group_memory。
    turn_id 写回 _reply_trace，reply_in_session 把它挂到 delivery 行上 —— 实际发出去的
    正文以那一行为准。
    """
    if params.get("private"):
        return None
    try:
        trace = _reply_trace.get()
        trace = trace if trace is not None else {}
        memory = bool(params.get("group_memory"))
        route = (trace.get("route")
                 or ("followup" if params.get("lookup_note") else None)
                 or ("banter" if params.get("unprompted") else "reply"))
        row = runtime.TURNS.append(
            "reply", route=route, group=trace.get("group"), user=trace.get("user"),
            message_id=trace.get("message_id"), session_id=params.get("session_id"),
            release=code_release(),
            head_sha=hashlib.sha256(head.encode("utf-8")).hexdigest()[:12],
            head_chars=len(head), media=flags,
            kwargs={k: v for k, v in params.items() if k not in _TURN_SKIP_PARAMS},
            session=messages[1 + memory:-1], max_tokens=max_tokens,
            model=providers.completion_meta(),
            effort=config.TIER_REASONING.get("chat") or None,
            raw=raw, retried=retried)
    except Exception:
        logger.warning("每轮输入记录失败，不影响回复", exc_info=True)
        return None
    if row is not None:
        trace["turn_id"] = row["turn_id"]
    return row


def _record_recall(group_id, user, message_id, query, names, now, selection):
    """一次旧账检索进 data/turns（kind=recall）：问题、人名词典、时刻和挑中的消息编号。

    同时记录承诺候选快照；存例时冻结当时可用的归档，不用旧策略先筛掉干扰消息。
    体检据此报「翻旧账落空」，调参器比较新旧选择；历史资料不全时须注明覆盖缺口。
    """
    try:
        runtime.TURNS.append("recall", group=group_id, user=user, message_id=message_id,
                             query=query, names=list(names), now=now,
                             release=code_release(), **recall_mod.trace(selection))
    except Exception:
        logger.warning("检索记录失败，不影响回复", exc_info=True)


async def get_ai_reply(session_id, user_text, is_owner=False,
                       context_hint="", unprompted=False, relation_note="",
                       group_memory="", owner_label="", private=False, state_note="",
                       knowledge_note="", recall_note="", search_note="", allow_fallback=True,
                       personal_note="", feedback_note="", reply_context="",
                       callback_approved=False, back_off=False, quip=False,
                       lookup_note="", playful_note="", resource_note="",
                       media_note="", unprompted_hint=""):
    """生成候选回复；正式消息入口持session锁，并在送达后提交实际正文。

    返回 (正文, 情感分值或 None)。分值为 None 表示这次没拿到模型给的记账标签（协议关了，
    或者它没写/写坏了），调用方退回本地词表（见 config.CMD_PROTOCOL_ENABLED，默认开着）。

    owner_label 是群里对群主的称呼（档案里认领的那个，没认领就是通用词）。它只在
    人设里出现「别总提群主」这类句子里，对**同一个群**是常数，所以不影响下面这条不变量。

    提示词按「越稳定越靠前」排列，这是给前缀缓存让路（实测命中率 85% → 98%）：
        1) 稳定头   角色 + 记账口径 + 协议，与「谁在说、说到第几句」全无关 → 全群全天共享
        2) 每日记忆 群长期记忆，一天只变一次（压完才动）
        3) 会话历史 每轮只往后追加，天然前缀稳定
        4) 本轮动态 群聊背景 + 知识库/搜索/旧账召回 + 对说话人的印象 + 插嘴指令，全部塞在最末尾
    ⚠️ 任何随「发言人或轮次」变化的内容都不许往 1) 里塞 —— 它之后的一切会跟着一起作废。
       以前群主身份、群聊背景、关系档案全挤在 system 里，等于把缓存全废了。
    """
    # 喊停：调用方可以带上判断的结论（分诊里有把握的 back_off），这里再并上本地词表 ——
    # 词表这一道放在生成入口，直接调 get_ai_reply 的旁路（私聊、主动开口、测试）也绕不开。
    # ⚠️ 这一行必须是函数体第一句：locals() 此刻恰好是全部入参，每轮输入记录原样存它。
    params = dict(locals())
    back_off = bool(back_off) or looks_like_back_off(user_text)
    try:
        # ── 1) 稳定头：同一个群里，每个人、每一轮，这一段都应当逐字节相同 ──
        # 人设里的 {bot} / {owner} 在这里换成真名（代码里不写死任何人名，见 naming.py）
        flags = media_flags()
        head = (prompts.PROMPT_NORMAL
                + prompts.PROMPT_SHARED_RULES
                + prompts.PROMPT_OUTPUT_RULES
                + media_rules(flags))
        head = naming.render(head, owner=owner_label)
        if config.AFFINITY_ENABLED and config.CMD_PROTOCOL_ENABLED:
            # 输出协议：只有还让模型自评时才需要
            head += prompts.CMD_PROTOCOL

        # ── 2) 每日记忆 / 3) 会话历史 由 runtime.SESSIONS.build_messages 按稳定度插入 ──
        # ── 4) 本轮动态：随发言人和轮次变化的东西，一律压到最末尾 ──
        bits = []
        if is_owner:
            # 只点明身份，不搞「誓死效忠」那套 —— 亲近程度由关系档案的档位决定
            bits.append(naming.render(prompts.PROMPT_OWNER_NOTE, owner=owner_label).strip())
        if unprompted:
            # 没叫它的这一轮：接不接它自己定（〔不接〕）。被判过的回忆要换一段说明 ——
            # 默认那段明说「旧事不许翻」。unprompted_hint 是「刚开过口」「TA 不爱被搭话」。
            bits.append((prompts.PROMPT_BANTER_CALLBACK_NOTE
                         if callback_approved and personal_note
                         else prompts.PROMPT_BANTER_NOTE).strip())
            if unprompted_hint:
                bits.append(unprompted_hint)
        if quip:
            bits.append(prompts.PROMPT_QUIP_NOTE.strip())
        if context_hint:
            bits.append("【群聊最近背景】\n" + context_hint)
        if knowledge_note and not back_off \
                and not feedback_note:
            # 维护者维护的事实优先于摘要；对方喊停时不追加额外材料。
            bits.append(knowledge_note)
        if recall_note and not back_off \
                and not feedback_note:
            # 只在对方明确问起往事时才会非空；对方正在喊停时一律不翻旧账。
            bits.append(recall_note)
        if search_note and not feedback_note:
            # 搜索结果是用户明确要的，喊停时也保留；它只是资料，不参与玩笑。
            bits.append(search_note)
        if state_note:
            # 激活度（2026-10-19）：作息 + 心情的实时状态自述——深夜被吵醒/有点烦，
            # 模型顺着这个体感说话。放最后一段，随轮次变化的内容一律压末尾。
            bits.append(state_note)
        if (config.PERSONAL_MEMORY_ENABLED and personal_note
                and not feedback_note
                and (not unprompted or callback_approved)
                and not back_off
                and not any((knowledge_note, recall_note, search_note))):
            # 个人原话：已核实来源的当前说话人历史发言。知识库/旧账/搜索已经给了
            # 本轮要的材料时不再叠加；对方喊停和随机插嘴同样不追加 —— 这里的兜底
            # 闸是为了让直接调 get_ai_reply 的调用方也绕不开这条规则。
            bits.append(personal_note)
            trace = _reply_trace.get()
            if trace is not None:
                trace["personal_memory_used"] = True
        if relation_note:
            # 称呼与游戏关系档案：独立于好感开关 —— AFFINITY_ENABLED=false 时
            # 它只带称呼/泛称信息，游戏部分由调用方构造时决定写不写。
            bits.append(relation_note)
        if reply_context:
            # 上一条实际送达的机器人发言：只作语境，不压制其他有效材料。
            bits.append(reply_context)
        if feedback_note:
            # 原生反馈的即时话术（收敛/短修）：压在本层尾部、关系档案之后；
            # 出现它时个人原话与知识/旧账材料同轮不建（调用方已拦）。
            bits.append(feedback_note)
        if playful_note and not back_off and not feedback_note:
            bits.append(playful_note)
        if resource_note:
            bits.append(resource_note)
        if media_note:
            # 这一轮画不了、说不了、或者是破例（见 media_turn）；平时这里是空的
            bits.append(media_note)
        if private:
            # 放在 owner note **之后**：那一句里有「该损就损」，私聊里得压住它
            bits.append(prompts.PROMPT_PRIVATE_NOTE.strip())
        if back_off:
            # 最高优先级，压过上面所有「该损就损」—— 对方已经明说不舒服了
            logger.info("🛑 对方喊停，本轮强制收敛（%s）", session_id[-12:])
            bits.append(prompts.PROMPT_BACK_OFF_NOTE.strip())
        if lookup_note:
            # 慢活的进度说明（在查 / 查完了 / 没查成）：这一轮的话就是为它说的，压在最后。
            # 放在中间时实测被人设和「刚睡醒」的状态盖过，「在查」那句照样先评一嘴真假。
            bits.append(lookup_note.strip())
        turn_context = ""
        if bits:
            turn_context = "（以下是系统给你的即时提示，不是群友说的话）\n" + "\n".join(bits)

        max_tokens = (config.AI_MAX_TOKENS_BANTER if unprompted or quip
                      else config.AI_MAX_TOKENS)

        # 没叫它的那句换个框送进去（PROMPT_UNPROMPTED_LINE）；会话里记的仍是原话
        messages = runtime.SESSIONS.build_messages(
            session_id, head,
            prompts.PROMPT_UNPROMPTED_LINE.format(text=user_text) if unprompted else user_text,
            memory_block=group_memory, turn_context=turn_context,
        )
        # 对话一律走对话梯队：不为「插嘴」单独换模型，那会让同一个角色在不同路径上表现不一致
        raw = await call_model(messages, max_tokens)
        retried = False
        if not raw:
            _record_turn(params, head, flags, messages, max_tokens, raw, retried)
            return (_pick_fallback() if allow_fallback else None), None
        body, payload = split_cmd(raw)
        # 采样乱码闸（2026-10-15 <CMD! 残片 / 10-19 0A9F%F5，均智谱 glm-4.7）：
        # 命中就重生成一次；两次都乱就照发并留痕——不在这里和模型无限拉扯。
        glitch = _garbage_run(body)
        if glitch:
            logger.warning("🧯 模型输出疑似采样乱码（%s…），重生成一次。原文: %s",
                           glitch[:10], raw[:140])
            retried = True
            raw = await call_model(messages, max_tokens)
            body, payload = split_cmd(raw)
            glitch = _garbage_run(body)
            if glitch:
                logger.warning("🧯 重生成仍含乱码（%s…），按原样发送", glitch[:10])
        _record_turn(params, head, flags, messages, max_tokens, raw, retried)
        # 标签解析失败时退化：拿主干文本，不计分
        if not body:
            return (_pick_fallback() if allow_fallback else None), None
        delta = None
        if isinstance(payload, dict) and "aff" in payload:
            try:
                delta = relations.clamp_delta(int(payload["aff"]))
            except (TypeError, ValueError):
                delta = None
        return body, delta
    except Exception as e:
        logger.exception("❌ 生成回复异常: %s", e)
        return (_pick_fallback() if allow_fallback else None), None


# ══════════════════════ 5. 发送与限流提示 ══════════════════════


def _blocked_reply(message):
    return naming.render("（刚才的话没能发出来，先换个话题，等会儿再聊。）",
                         owner=owner_label(getattr(message, "group_openid", None)))


SENDER = Sender(fallback=_blocked_reply)


def _delivery_observer(outgoing_id, group_id, meta, member):
    """复制好的元数据闭包：后续轮次改不到这次投递的快照。"""
    def on_outcome(result):
        fields = {
            "outgoing_id": outgoing_id,
            "source_message_id": meta.get("source_message_id"),
            "status": result.status,
            "route": meta.get("route", "local"),
            "feedback_eligible": bool(meta.get("feedback_eligible")),
            "policy_id": meta.get("policy_id"),
            "fallback": bool(result.fallback),
        }
        if result.reason:
            fields["reason"] = result.reason
        for key in ("generation", "provider", "model", "tier",
                    "finish_reason", "knowledge_supplied", "recall_supplied",
                    "search_supplied", "remind_applied", "personal_memory_used",
                    "personal_memory_enabled", "turn_id"):
            value = meta.get(key)
            if value is not None:
                fields[key] = value
        if result.status == "delivered":
            # 只存最终真正发出去的正文 —— prepare 后的 owner hint、
            # 平台拒收后的兜底文案，都算「实际送达」；被拒的原稿不算。
            fields["text"] = result.text
            _remember_sent(group_id, result.receipt, result.text)
            rid = result.receipt
            rid = rid.get("id") if isinstance(rid, dict) else getattr(rid, "id", None)
            if isinstance(rid, (str, int)):
                fields["platform_message_id"] = str(rid)
            ref_idx = quotes.receipt_ref(result.receipt)
            if ref_idx:
                fields["ref_idx"] = ref_idx
            # 反馈观察登记【先于】日志落账：只有确认送达的模型回复才算
            # 「上一条 AI 回复」—— 本地应答、平台兜底文案、主动广播、
            # 排队/失败投递一律不算。登记失败不许影响投递提交。
            if (meta.get("generation") == "model"
                    and meta.get("route") in ("reply", "banter")
                    and not result.fallback):
                _note_own_line(group_id, result.text)
                try:
                    runtime.FEEDBACK.remember(
                        group_id, meta.get("user"), result.text,
                        route=meta.get("route"), outgoing_id=outgoing_id,
                        history=meta.get("history") or ())
                except Exception:
                    logger.exception("⚠️ 反馈观察登记失败（%s）", group_id[-6:])
        runtime.INTERACTIONS.append("delivery", group_id,
                                    user=meta.get("user"),
                                    actor="assistant", **fields)
    return on_outcome


async def safe_reply(message, reply_text, *, on_delivered=None, interaction=None,
                     **delivery):
    """群消息才写观察日志；私聊没有 group_openid，原样发送、一行不落。

    interaction 是本条回复的本地元数据（route/feedback_eligible/生成出处），
    只给观察日志用，绝不往 SDK 侧漏。
    """
    group_id = getattr(message, "group_openid", None)
    if not group_id:
        return await SENDER.reply(message, reply_text, on_delivered=on_delivered,
                                  **delivery)
    member = getattr(getattr(message, "author", None), "member_openid", None)
    meta = dict(interaction or {})
    meta.setdefault("route", "local")
    # 来源与关联人一律以实际消息为准（本地应答、AI 回复同口径），
    # 调用方 metadata 不许给这次送达安到别的发言/别的人头上；
    # policy_id 在发起投递这一刻定版，排队补投也不许漂成新口径。
    meta["source_message_id"] = getattr(message, "id", None)
    meta["user"] = member
    meta["policy_id"] = config.INTERACTION_POLICY_ID
    observer = _delivery_observer(uuid.uuid4().hex, group_id, meta, member)
    return await SENDER.reply(message, reply_text, on_delivered=on_delivered,
                              on_outcome=observer, **delivery)


async def resend_queue_loop():
    while True:
        await STATUS.sleep(10)
        await SENDER.drain()


def media_flags():
    """稳定头里那几段开没开：画图 / 语音 / 提醒。每轮输入记录存这个，回放时照着拼同一个头。"""
    return {"draw": bool(image_generation.available()), "voice": bool(_VOICE_OUT.ready()),
            "remind": bool(config.REMINDER_ENABLED)}


def media_rules(flags=None):
    """稳定头里的「你会画图 / 你会发语音 / 你能记提醒」：只看开关和凭据，同一进程里逐字节不变，不破缓存。

    flags 给了就照它拼（回放用，见 regular/replay_worker.py），不给就看当前进程的开关。
    """
    flags = media_flags() if flags is None else flags
    parts = []
    if flags.get("draw"):
        parts.append(prompts.PROMPT_DRAW_RULES)
    if flags.get("voice"):
        parts.append(prompts.PROMPT_VOICE_RULES)
    if parts:
        parts.append(prompts.PROMPT_MEDIA_RECORD)
    if flags.get("remind"):
        parts.append(prompts.PROMPT_REMIND_RULES)
    return "".join(parts)


class MediaTurn(SimpleNamespace):
    """这一轮小王能用哪几样（draw / voice / remind）、额度记在谁头上、即时提示里要补的那句（note）。"""


def media_turn(group_id, user_id, score, *, asked, remind=False):
    """在生成**之前**定这一轮的画图、语音条件，好让小王写回复时就知道。

    asked=True：有人在跟它说话、它在正经回（不是插嘴、短接、喊停、慢活）—— 只有这时才认〔画〕：
    没人找它画就不花钱。语音便宜，插嘴也认，但喊停和慢活不认（调用方传 voice=False 的那几种）。
    画累了、正在画、破例、嗓子用完：都写进 note，小王自己用人话说出来（群主 2026-11-01：
    「额度用完了也要像人一样说今天累了」）—— 以前这几句是代码里的固定话术。
    """
    draw = voice = False
    notes = []
    if asked and image_generation.available():
        state = image_generation.precheck(group_id, user_id, score)
        draw = state in ("normal", "exception", "extra")
        if state == "tired":
            notes.append(prompts.PROMPT_DRAW_TIRED)
        elif state == "busy":
            notes.append(prompts.PROMPT_DRAW_BUSY)
        elif state == "exception":
            notes.append(prompts.PROMPT_DRAW_EXCEPTION)
    if _VOICE_OUT.ready():
        if _VOICE_OUT.tired():
            notes.append(prompts.PROMPT_VOICE_TIRED)
        else:
            voice = True
    # 提醒：有人在跟它说话才认（remind=True，没叫它的那几轮不认）。现在几点、TA 记着哪条，
    # 每轮都写 —— 它得照着算时间，被问「我那条还在吗」也答得上来。
    remind = remind and config.REMINDER_ENABLED and bool(group_id and user_id)
    if remind:
        notes.append(reminders.render_note(
            runtime.SCHEDULER.tasks.get(reminders.task_id(group_id, user_id)), time.time()))
    return MediaTurn(group=group_id, user=user_id, score=score, draw=draw, voice=voice,
                     remind=remind, note="\n".join(notes))


async def reply_in_session(message, session_id, user_text, *, on_delivered=None,
                           owner_hint_group=None, prepared=None, prepared_local=False,
                           media=None, **context):
    """Serialize generation and send; queued delivery commits through the same callback.

    prepared=(正文, 分值)：正文已经写好，只管发和记账；prepared_local 标记本地文案。
    media=MediaTurn：这一轮认不认小王写的〔画〕〔语音〕（见 media_turn）；None 时标记一律当文字。
    """
    async with runtime.SESSIONS.lock(session_id):
        generation = runtime.SESSIONS.begin_turn(session_id)
        # 观察/投递用的群聊上下文快照是投递元数据，不是提示词参数 ——
        # 从这里摘走，别漏进 get_ai_reply 的关键字里。
        history = context.pop("history", ())
        if media is not None and media.note:
            context["media_note"] = media.note
        # 本轮的注入痕迹 + 实际应答候选：都先重置再取，被 mock/失败的调用
        # 不会带着上一轮的出处来冒充。
        trace = {"personal_memory_used": False,
                 # 每轮输入记录要的「谁、在哪、回的哪条」；get_ai_reply 记完把 turn_id 写回来
                 "group": getattr(message, "group_openid", None),
                 "user": getattr(getattr(message, "author", None), "member_openid", None),
                 "message_id": getattr(message, "id", None)}
        async def generate():
            token = _reply_trace.set(trace)
            providers.reset_completion_meta()
            try:
                body, delta = (prepared if prepared is not None else
                               await get_ai_reply(session_id, user_text,
                                                  allow_fallback=False, **context))
                return body, delta, providers.completion_meta()
            finally:
                _reply_trace.reset(token)

        body, delta, completion = await generate()
        generated = bool(body) and not prepared_local
        if not generated and prepared is None and context.get("unprompted"):
            return "", delta, None   # 没叫它的这轮没生成出来：不说，不拿兜底话凑一句
        said = expression.parse(body) if generated else expression.Said(None, "", body or "")
        if said.kind == "pass":
            logger.info("🤐 这句不接（%s）", session_id[-12:])
            return "", delta, None
        if said.kind == "image" and not (media is not None and media.draw):
            logger.info("🎨 这一轮不认〔画〕，只发文字（%s）", session_id[-12:])
            said = expression.Said(None, "", said.text, said.remind)
        declined = said.kind == "image" and await _drawing_declined(message, media, said, history)
        if declined:
            # 这张不画（见 _drawing_declined）：带着 PROMPT_DRAW_DECLINED 重写一遍，让它用自己的话推掉；
            # 重写的还想画、或者没写出话来，就用本地那句。
            context["media_note"] = "\n".join(
                part for part in (context.get("media_note"), prompts.PROMPT_DRAW_DECLINED) if part)
            body, delta, completion = await generate()
            said = expression.parse(body) if body else expression.PASS
            if said.kind in ("image", "pass") or not said.text:
                said = expression.Said(None, "", expression.image_failed_line("declined"), said.remind)
        if said.kind == "voice" and not (media is not None and media.voice):
            said = expression.Said(None, "", said.text, said.remind)
        body = said.text if generated else (body or _pick_fallback())
        # 〔提醒 …〕：发出去之前就登记，记不上的原因补在它那句话后面（它已经答应了，得说实话）
        remind_note = ""
        remind_applied = None
        if said.remind and getattr(media, "remind", False):
            remind_note, remind_line = reminders.apply(
                runtime.SCHEDULER, media.group, media.user, said.remind, time.time(),
                blocked=wordfilter.has_hit)
            remind_applied = bool(
                remind_note.startswith("提醒记下了：") or remind_note.startswith("提醒取消了"))
            runtime.STATE.mark_dirty()
            logger.info("⏰ %s（%s）", remind_note, session_id[-12:])
            body = "\n".join(part for part in (body, remind_line) if part) or remind_note
        elif said.remind:
            remind_applied = False
        if not body and said.kind != "image":
            return body, delta, None   # 只写了个标记、没写话：当它这轮不说
        hinted = f"{body}\n{OWNER_CLAIM_HINT}"
        # 会话里记的那条：默认就是发出去的字；画了图、发了语音，换成带标记和结果的那条
        # （expression.record）—— 下一轮小王看见的是自己实际做了什么，而不是只看见一句话。
        # 记了提醒的，再把提醒那行和记没记上包在外面（expression.record_reminder）。
        recorded = {"as": expression.record_declined(body) if declined else None}

        def as_recorded(text):
            return (expression.record_reminder(text, said.remind, remind_note)
                    if remind_note else text)

        def prepare():
            return hinted if generated and owner_hint_pending(owner_hint_group) else body

        def commit(result):
            if runtime.SESSIONS.generation(session_id) != generation:
                return  # a reset action invalidated this old pending reply
            if said.kind != "image":   # 画图那轮等图的结果出来再一起记
                runtime.SESSIONS.record(session_id, user_text, as_recorded(recorded["as"] or result.text))
            if generated and result.text in (body, hinted):
                if result.text == hinted:
                    runtime.STATE.data.setdefault("groups", {}).setdefault(owner_hint_group, {})["owner_hint"] = True
                if on_delivered:
                    on_delivered()
            runtime.STATE.mark_dirty()

        member = getattr(getattr(message, "author", None), "member_openid", None)
        source_id = getattr(message, "id", None)
        interaction = {
            "history": history,
            "route": "banter" if context.get("unprompted") else "reply",
            "generation": "model" if generated else "local_fallback",
            # supplied ≠ injected：这三个只说明上下文材料送到了组装口，
            # 喊停等抑制路径下它们可能根本没进提示词。
            "knowledge_supplied": bool(context.get("knowledge_note")),
            "recall_supplied": bool(context.get("recall_note")),
            "search_supplied": bool(context.get("search_note")),
            "personal_memory_used": bool(trace.get("personal_memory_used")),
            "personal_memory_enabled": bool(config.PERSONAL_MEMORY_ENABLED),
        }
        if completion:
            interaction.update(completion)  # 真正答上来的 provider/model/tier/finish
        if remind_applied is not None:
            interaction["remind_applied"] = remind_applied
        if trace.get("turn_id"):
            interaction["turn_id"] = trace["turn_id"]   # 这句话是哪一轮输入生成的（data/turns）
        # 只有「本群 + 有源发言者 + 有源消息 ID」的 AI 会话回复才可被反馈绑定；
        # 本地命令、主动广播、私聊都不在反馈口径里。
        interaction["feedback_eligible"] = bool(
            getattr(message, "group_openid", None) and member and source_id)
        if said.kind == "voice":
            # 发语音持 owner-hint 锁：念出去的那句不能在合成途中被认主提示改掉。
            async with owner_hint_lock(owner_hint_group):
                voice_result = receipt = None
                if not owner_hint_pending(owner_hint_group):
                    try:
                        voice_result, receipt = await _VOICE_OUT.attempt(
                            message, body, SENDER,
                            lambda: runtime.SESSIONS.generation(session_id) == generation,
                            tone=said.spec)
                    except Exception:
                        # 意外异常可能发生在 QQ 已收下语音之后：保守起见不再补发文字。
                        logger.warning("语音发送状态不明，停止本轮投递")
                        return body, delta, DeliveryResult("unknown", body)
                status = getattr(voice_result, "status", None)
                if status == "sent":
                    recorded["as"] = expression.record(said, True)
                    meta = dict(interaction)
                    meta.update(source_message_id=source_id, user=member,
                                policy_id=config.INTERACTION_POLICY_ID)
                    observer = _delivery_observer(uuid.uuid4().hex, owner_hint_group, meta, member)
                    item = PendingSend(message, body, time.time(), on_delivered=commit,
                                       on_outcome=observer)
                    result = await SENDER._delivered(item, receipt)
                    return body, delta, result
                if status == "delivery_unknown":
                    # 语音可能已经在群里了：不能再补一条文字，会话里按发过记。
                    if runtime.SESSIONS.generation(session_id) == generation:
                        runtime.SESSIONS.record(session_id, user_text,
                                                as_recorded(expression.record(said, True)))
                    return body, delta, DeliveryResult("unknown", body)
                # 其余情况都确定没发出去（含合成被拒、念的和写的对不上）：同一句话改发文字。
                # 这句本来就是小王写给群里看的，语音没成不等于这句话不能说。
                if status:
                    logger.info("🔈 语音没发成（%s），改发文字", status)
                recorded["as"] = expression.record(said, False)
        if said.kind == "image":
            return await _reply_with_image(
                message, session_id, user_text, said, media, generation, delta,
                commit=commit, interaction=interaction, prepare=prepare,
                owner_hint_group=owner_hint_group, history=history)
        result = await safe_reply(
            message, body, on_delivered=commit,
            interaction=interaction,
            is_current=lambda: runtime.SESSIONS.generation(session_id) == generation,
            retry_lock=runtime.SESSIONS.lock(session_id),
            send_lock=owner_hint_lock(owner_hint_group) if owner_hint_group else None,
            prepare=prepare if owner_hint_group else None)
        return body, delta, result


_unprompted_locks = collections.defaultdict(asyncio.Lock)


def _waited(group_id, message_id):
    """这条消息进群聊背景缓存到现在过了多久；找不到就当刚到。"""
    for row in reversed(group_buffers.get(group_id, ())):
        if row.get("message_id") == message_id:
            return time.time() - float(row.get("time") or 0)
    return 0.0


def _said_more(group_id, member, message_id):
    """这人在这条之后又说了话（群聊背景缓存里排在它后面）。"""
    after = False
    for row in group_buffers.get(group_id, ()):
        if after and row.get("sender") == member:
            return True
        after = after or row.get("message_id") == message_id
    return False


async def reply_unprompted(message, session_id, user_text, *, callback_note="", **context):
    """没 @、没叫名字的一句：接不接小王自己定（写〔不接〕就不发），代码只交代事实、记结果。

    一个群一次只问一句：同一时刻冒出来的几句排着问，前一句它接了，后一句它就知道
    自己刚开过口（PROMPT_BANTER_RECENT）—— 不然几句一起问，可能一起接。
    """
    group_id = getattr(message, "group_openid", None)
    member = getattr(getattr(message, "author", None), "member_openid", None)
    message_id = getattr(message, "id", None)
    # 同一个人连着发的几句只问最后一句：先等他说完，后面还有话就这句不问 —— 后面那句的背景里有它
    # （2026-11-04「你发的啥」「截图看看」两秒内各回了一句；前一句接歪了，后一句看见自己刚回过就接着歪）。
    await asyncio.sleep(max(0.0, config.BANTER_BURST_SECONDS - _waited(group_id, message_id)))
    async with _unprompted_locks[group_id]:
        if _said_more(group_id, member, message_id):
            runtime.INTERACTIONS.append(
                "decision", group_id, user=member, actor="system",
                source_message_id=message_id, route="banter",
                selected=False, reason="said_more")
            return "", None, None
        now = time.time()
        hints = []
        if now - group_last_random_reply.get(group_id, 0) < config.BANTER_COOLDOWN_SECONDS:
            hints.append(prompts.PROMPT_BANTER_RECENT)
        # 接纳度只在有把握地读出「这人不爱被搭话」时低于 1（见 receptive.py 的声明门槛）
        if config.RECEPTIVE_ENABLED and runtime.RECEPTIVE.multiplier(group_id, member, now=now) < 1:
            hints.append(prompts.PROMPT_BANTER_UNWELCOME)
        outcome = await reply_in_session(message, session_id, user_text,
                                         unprompted_hint="\n".join(hints), **context)
        spoke = outcome[2] is not None
        runtime.INTERACTIONS.append(
            "decision", group_id, user=member, actor="system",
            source_message_id=getattr(message, "id", None), route="banter",
            selected=spoke, reason="spoke" if spoke else "declined",
            callback=bool(callback_note))
        if spoke:
            logger.info("💬 没叫它，它接了（%s）", (member or "")[-4:])
            group_last_random_reply[group_id] = time.time()
            runtime.STATE.data.setdefault("cooldowns", {})[group_id] = group_last_random_reply[group_id]
            if callback_note:
                runtime.RECEPTIVE.mark_callback(group_id, member, now=now)
                logger.info("🫧 居然记得：端出 1 条旧话（%s）", (member or "")[-4:])
            runtime.STATE.mark_dirty()
    return outcome


async def _drawing_declined(message, media, said, history):
    """〔画〕之前看一眼整张画面（2026-11-04 维尼熊 → 吃包子的黄熊 → 中式宫殿 → 冠冕那一串，
    见 docs/drawing-guard.md）：画面加上前几句是怎么要的，判断说在影射政治，这张就不画。
    这一轮本来就不认〔画〕的不问；判断不可用照常画（runtime.FEEDBACK.check_drawing）。"""
    if media is None or not media.draw:
        return False
    verdict = await runtime.FEEDBACK.check_drawing(
        media.group, media.user, said.spec, history=history,
        message_id=getattr(message, "id", None) or "")
    if verdict is False:
        logger.info("🎨 这张不画：画面合起来像在影射政治（%s）", (media.user or "")[-4:])
    return verdict is False


async def _reply_with_image(message, session_id, user_text, said, media, generation, delta, *,
                            commit, interaction, prepare, owner_hint_group, history):
    """小王写了〔画〕：先把它那句话发出去，同时开画；图跟在后面，结果记进会话。

    这句话就是以前那句固定的「好，我画画看，稍等一下」—— 现在是小王照着这单自己说的，
    不多花一次生成（回复本来就要写）。图没发出去时补的那句仍是本地话术（expression.image_failed_line）。
    整段持着这个人的会话锁：同一个人追问「画好了吗」，会等图出来再答，答的时候知道结果。
    """
    current = lambda: runtime.SESSIONS.generation(session_id) == generation
    said_first = asyncio.Event()

    async def after_text():
        await said_first.wait()
        return SENDER._next_sequence(message)

    task = asyncio.create_task(image_generation.generate_and_send(
        message, group_id=media.group, user_id=media.user, score=media.score,
        prompt=said.spec, before_send=after_text))
    result = None
    try:
        if said.text:
            result = await safe_reply(
                message, said.text, on_delivered=commit, interaction=interaction,
                is_current=current, retry_lock=runtime.SESSIONS.lock(session_id),
                send_lock=owner_hint_lock(owner_hint_group) if owner_hint_group else None,
                prepare=prepare if owner_hint_group else None)
    finally:
        said_first.set()
    try:
        outcome = await task
    except Exception:
        logger.exception("生图处理异常")
        outcome = False
    delivered_text = said.text if getattr(result, "status", None) == "delivered" else ""
    if current():
        runtime.SESSIONS.record(session_id, user_text, expression.record(
            expression.Said("image", said.spec, delivered_text), outcome))
        runtime.STATE.mark_dirty()
    if outcome is True:
        _note_own_line(media.group, f"[图：{said.spec}]")
        outgoing_id = uuid.uuid4().hex
        # 分诊看得见「它刚画了什么」：「画的什么玩意」是在评这张图，不是新的画图请求。
        runtime.FEEDBACK.remember(
            media.group, media.user, f"{delivered_text}\n[图：{said.spec}]".strip(),
            route="reply", outgoing_id=outgoing_id, history=list(history or [])[-4:])
        runtime.INTERACTIONS.append(
            "delivery", media.group, user=media.user, actor="assistant",
            outgoing_id=outgoing_id, source_message_id=getattr(message, "id", None),
            status="delivered", route="image", generation="model")
    elif outcome is not None:
        line = expression.image_failed_line(outcome)
        logger.info("🎨 这张没画成（%s）", outcome)
        await safe_reply(message, line)
    return said.text, delta, result or DeliveryResult("delivered" if outcome is True else "failed", "")


def _throttled_notice_key(group_id, kind):
    return f"{kind}:{group_id}"


async def notify_if_quiet(message, kind, text, cool_down=30.0):
    """限流/超预算时给个提示，但每群最多每 30 秒说一次，避免提醒本身变成刷屏。"""
    key = _throttled_notice_key(message.group_openid, kind)
    now = time.time()
    if now - _throttle_notice.get(key, 0) < cool_down:
        return
    _throttle_notice[key] = now
    await safe_reply(message, text)


class BudgetGate:
    """把「群级令牌桶 + 全局日预算」两道闸封装成一个判断。"""

    @staticmethod
    async def check(message, group_key):
        ok, wait = runtime.GROUP_BUCKET.try_acquire(group_key)
        if not ok:
            logger.warning("⏳ %s 触发群级限流，%.1fs 后可再次调用", group_key, wait)
            await notify_if_quiet(
                message, "cooldown",
                "（战术后仰）慢点慢点，大伙同时开火我脑子转不过来了！容我喘口气再接招。"
            )
            return False

        if not runtime.BUDGET.can_consume():
            logger.warning("🛑 今日全局调用额度已用尽（%d/%d）", runtime.BUDGET.used, runtime.BUDGET.limit)
            await notify_if_quiet(
                message, "budget",
                naming.render("（默默关掉显示器）今天的话费额度让我用冒了，{owner}说再聊下去要卖肾了。"
                              "明天再见！", owner=owner_label(getattr(message, "group_openid", None)))
            )
            runtime.STATE.mark_dirty()
            return False

        runtime.STATE.mark_dirty()
        if runtime.BUDGET.limit and runtime.BUDGET.used >= int(runtime.BUDGET.limit * config.BUDGET_WARN_RATIO):
            logger.warning("⚠️ 今日额度已用 %d/%d（%.0f%%）", runtime.BUDGET.used, runtime.BUDGET.limit,
                           runtime.BUDGET.used / runtime.BUDGET.limit * 100)
        return True


def is_control_command(text):
    """这条是不是「本地控制指令」（不经过模型的那种操作）？

    指令是**操作**，不是聊天内容。不加区分地归档，它们就会在每日压缩时被当成人物事实
    写进长期记忆 —— 实测事故：群主喊了句「猫娘模式」，摘要里就留下了「老王还想当猫娘」。
    所以这里在**归档时就打标记**，压缩源头跳过（archive.since）。

    2026-11-01 手动模式和查榜、骰子、翻旧账这些指令都删了（27 天 1930 条消息里加起来
    不到 20 次，大半是第一周试着玩）：现在本地指令只剩起名开关；起名本身另有判断。
    """
    return bool(relations.parse_nick_lock_command((text or "").strip()))


# 正文里 @ 人时留下的**明文昵称**（QQ 群消息不给昵称字段，只给 openid）。
# 切到空白或标点为止：昵称可能含空格（「A.A 贵阳老莫（全国可飞）」），
# 宁可只记第一段，也不猜边界 —— 它只是显示兜底，短一点不会出事。
























async def handle_nick_lock(message, group_id, action, is_owner):
    """起名开关：群主一句话锁上/解开整个群的起名。0 token，纯本地。

    为什么要有它：改名节流拦的是「试得太快」，拦不住「慢慢试」。锁是最后那道
    手动闸 —— 群主看不下去的时候一句话把自由起名整个关掉，改名权收归自己
    （他本来就能 @谁 说「叫 XXX」）。状态按群存，重启不丢。
    """
    owner = owner_label(group_id)
    if not is_owner:
        logger.info("🔒 非群主想%s起名，已明说", "锁" if action == "lock" else "解")
        await safe_reply(message, f"（把钥匙揣回兜里）锁不锁名字，得{owner}说了算。")
        return
    if action == "lock":
        if nick_locked(group_id):
            await safe_reply(message, "（晃了晃上着锁的小本本）本来就是锁着的啊。")
            return
        set_nick_locked(group_id, True)
        logger.info("🔒 起名已锁，普通群友改称呼先不办")
        await safe_reply(message,
            "（咔哒一声给小本本上了锁）成。从现在起改称呼这事我一律不办，"
            f"只有你能落笔 —— @谁 说「叫 XXX」就行。想解开再说一声「解锁起名」。")
        return
    if not nick_locked(group_id):
        await safe_reply(message, "（翻了翻小本本）本来就没锁啊，大家随时能改称呼。")
        return
    set_nick_locked(group_id, False)
    logger.info("🔓 起名已解锁")
    await safe_reply(message,
        "（把抽屉钥匙转开）行，起名重新开放。想改称呼随时说「叫我 XXX」，以最新一次为准。")




# ══════════════════ 改名节流：一次围攻能试几次，才是真正的防线 ══════════════════
#
# 背景是 2026-09-18 的一场实测围攻：13 分钟内群友轮番试谐音称呼，审核拦下 10 个，
# 漏过去 7 个。回头看，**漏的并不比拦下的更干净**，它们只是「刚好没被抽中」的那一发。
# 只要每试一次的代价是零（一句话 + 一次几百 token 的调用），这种抽样就永远打不完 ——
# 把模型调准只能降低漏的概率，降不到零。
#
# 所以真正的闸门是把**一次围攻能试的次数**压下去，而且必须是本地算：
#   · 个人层 —— 同一个人的名字改完之后 3 分钟内不能再动。正常人一天改一次都算多，
#     而「刚记下就马上换下一个」正是试名字的标准动作。
#   · 群层   —— 窗口内累计拦下这么多次，就说明有人在挠这里，整群进入改名冷静期。
#     单人间隔摁不住换号/换人来试的情况，群级这一层是兜那个底的。










async def handle_nick_command(message, cmd, group_id, sender_openid, is_owner, mentioned_others,
                              verdict=None):
    """昵称管理。本地词表先过一遍（0 token），可疑的再让模型看一眼（约 137 token）。

    两道是有顺序的：词表便宜且不会误伤，能拦的先在当地拦掉；
    剩下的才花 token 问模型 —— 它认得出谐音暗指，但也会偶发误判，
    所以只让它做「第二道」，不让它当第一道。
    """
    if cmd["scope"] == "self-clear":
        rec = runtime.RELATIONS.get(group_id, sender_openid)
        old = (rec.get("nick") or "").strip()
        if not old:
            await safe_reply(message, "（翻了翻小本本）你本来就没留过称呼啊，省了我一道工序。")
            return
        runtime.RELATIONS.set_nick(group_id, sender_openid, None, source="claim")
        runtime.RENAMES.note(group_id, old, sender_openid)
        sync_clear(group_id, sender_openid, old)
        runtime.STATE.mark_dirty()
        await safe_reply(message, "（拿橡皮把小本本上的字擦干净）行，以后就不乱叫了，逮着泛称直接用。")
        return

    # 「这一笔是写给谁的」：给自己起名时是本人；群主 @ 某人起名时是被 @ 的那位。
    # 主名不能重叠，而这个人的**自己那一版**要放行（他随时能覆盖自己的名字）——
    # 所以判断占用时得先知道 subject 是谁，不能一律拿发言人算。
    subject = (mentioned_others[0] if cmd["scope"] == "other" and len(mentioned_others) == 1
               else sender_openid)
    nick = cmd["nick"]

    # 起名锁（群主可掰）：锁上之后普通群友的「叫我 XXX」一律不办。
    # 只挡 self，不挡 self-clear（后悔总得让人能后悔），也不挡群主 —— 他本来就能给任何人落名。
    if cmd["scope"] == "self" and not is_owner and nick_locked(group_id):
        logger.info("🔒 起名已锁，挡下一笔（%s 想叫「%s」）", str(sender_openid)[-4:], nick)
        await safe_reply(message, NICK_LOCKED_DENIED.format(owner=owner_label(group_id)))
        return

    # 闸门排在这里，排在**名字本身合不合格**之前：先把一次围攻能试几次压住，
    # 再看名字本身 —— 顺序无所谓对错，只是前者是「能不能问」，后者是「问到的是什么」。
    blocked = nick_block_reason(group_id, subject, is_owner=is_owner)
    if blocked:
        logger.info("⏳ 改名节流挡住一笔（%s 想叫「%s」）：%s", str(subject)[-4:], nick, blocked)
        await safe_reply(message, f"（把小本本合上一半）{blocked}，到时候再喊我一声。")
        return

    problem = relations.bad_nick(
        nick,
        reserved=reserved_nick_names(group_id, subject),
        taken=taken_nick_names(group_id, subject),
    )
    if problem:
        # 只有**撞到敏感词**才计入围攻统计：重名、冒名顶替这些是正常的业务冲突，
        # 拿来当「有人在攻击」的证据会冤枉好人。
        if wordfilter.has_hit(nick):
            nick_note_reject(group_id)
        await safe_reply(message, f"（皱眉盯着你写的字看了半天）这个名字不成（{problem}），换一个吧。")
        return

    # 词表过了不等于安全：谐音、拆字、缩写天生就是拿来绕开字面匹配的。
    # 再让审核梯队看一眼（失败返回 None = 放行，不因审核不可用而堵死起名）。
    # 两张否决票取并集：分诊里顺带问的那一题（verdict，有把握才是 refuse）和审核梯队这一道。
    # 两者都不全对（审核梯队 2026-09-18 一场围攻拦 10 漏 7；分诊见调用处），漏的不是同一批。
    # 分诊已经说 refuse 就不再花一次审核调用。拦下的照样计入围攻统计，喂给节流那一层。
    if verdict == "refuse" or await judge_nick(nick) is False:
        nick_note_reject(group_id)
        logger.info("🕵️ 称呼审核拦下：%s 想用「%s」", sender_openid[-4:], nick)
        await safe_reply(message,
            "（笔尖悬在小本本上方，半天没落下去）这名字我不敢往上写，换一个吧。")
        return

    if cmd["scope"] == "other":
        owner = owner_label(group_id)
        if not is_owner:
            await safe_reply(message, NICK_OTHER_DENIED.format(owner=owner))
            return
        if not mentioned_others:
            await safe_reply(message,
                f"（举着笔一脸茫然）{owner}，我这边没收到您艾特的人是谁——QQ 只给了我「@昵称」这几个字，"
                "拿不到他的 ID。让那位同学先在群里随便说一句话（我记下他的 ID），您再 @ 他起名就行。")
            return
        if len(mentioned_others) > 1:
            await safe_reply(message,
                f"（举着笔左右为难）{owner}您一次艾特了好几位，这一笔我只能落在一个名字上——"
                "请一次只 @ 一个人。")
            return
        target = mentioned_others[0]
        old = runtime.RELATIONS.get(group_id, target, create=False)
        old_nick = (old or {}).get("nick")
        runtime.RELATIONS.set_nick(group_id, target, nick, source="owner")
        runtime.RENAMES.note(group_id, old_nick, target)
        runtime.STATE.mark_dirty()
        sync_rename(group_id, old_nick, nick, member_id=target)
        nick_note_accept(group_id, target)
        logger.info("✍️ 采纳称呼：%s → 「%s」（%s御赐）", old_nick or "（未留名）", nick,
                    owner_label(group_id))
        if old_nick and old_nick != nick:
            await safe_reply(message,
                # ⚠️ 别说「谁也不许改」—— 听起来像**本人**也改不动，其实本人一句
                # 「叫我 XXX」就能覆盖（source=claim 同样是合法写入源）。
                f"（划掉旧名字重新落笔）收到！往后【{old_nick}】就改记作【{nick}】"
                f"——{owner}御赐，旁人动不了；本人想改，随口一句话的事。")
        else:
            # 以前这里带对方 openid 的后四位，方便核对绑没绑错；代价是群里看到一串
            # 读不懂的编号，模型还会照着复读。现在改用他**群里挂的显示名**来核对 ——
            # 一样能当场看出绑没绑错，而且是人话。
            who = runtime.DISPLAY_NAMES.of(group_id, target) or "这位"
            await safe_reply(message,
                f"（工工整整把名字写进点名册）收到！往后【{who}】我就记作【{nick}】"
                f"——{owner}御赐，旁人动不了；本人想改，随口一句话的事。")
        return

    old = runtime.RELATIONS.get(group_id, sender_openid).get("nick")
    runtime.RELATIONS.set_nick(group_id, sender_openid, nick)
    runtime.RENAMES.note(group_id, old, sender_openid)
    runtime.STATE.mark_dirty()
    sync_rename(group_id, old, nick, member_id=sender_openid)
    nick_note_accept(group_id, sender_openid)
    logger.info("✍️ 采纳称呼：%s → 「%s」（本人认领）", old or "（未留名）", nick)
    if old and old != nick:
        await safe_reply(message,
            f"（把小本本上原来的“{old}”划掉）行，改口最快——以后叫你【{nick}】，之前那个作废。")
    else:
        await safe_reply(message,
            f"（一笔一划记进小本本）记住了，以后叫你【{nick}】。想改随时喊一声，以最新一次为准。")


# ══════════════════ 群主指代（刻意不写死人名） ══════════════════

# 群主的通用指称（认人名时当作已知名字用）。不同群叫法不一，能想到的都放上。
OWNER_GENERIC_TERMS = ("群主", "群管")


def owner_reference_terms(group_id):
    """群里可能用来指群主的词。

    刻意**不写死具体人名**（比如某个群集群主的外号）—— 这机器人是要开源出去的，
    换个群、群主换个名字，都得开箱能用。所以只取两个来源：
      · 通用指称（群主 / 群管）
      · 群主自己在档案里认领的称呼（他哪天说一句「我是XX」，下次就自动进这里）
    """
    terms = set(OWNER_GENERIC_TERMS)
    if runtime.OWNER_OPENID:
        rec = runtime.RELATIONS.get(group_id, runtime.OWNER_OPENID, create=False) or {}
        nick = rec.get("nick")
        if nick:
            terms.add(nick)
    return terms


def _other_names(group_id, exclude_openid, limit=8):
    """别人认领过的称呼。给模型划红线：这些名字已经有主了，别张冠李戴。

    只给**称呼**，不带 openid 尾巴 —— 这段会进 prompt，模型一复读就成了群里的乱码。
    """
    return [r.get("nick") for oid, r in named_members(group_id)
            if oid != exclude_openid and r.get("nick")][:limit]


def _recall_names(group_id):
    """旧账召回用的「人名词典」：主名、群昵称、群主指称、机器人自称。"""
    names = {r.get("nick") for _oid, r in named_members(group_id) if r.get("nick")}
    names.update((runtime.DISPLAY_NAMES.groups.get(group_id) or {}).values())
    names.update(owner_reference_terms(group_id))
    names.update(naming.bot_names())
    return tuple(name for name in names if name and len(name) >= 2)


def _resolve_name(group_id):
    """压缩记忆时把一行的发言人写成谁：称呼 → 群昵称 → None（由压缩侧退成通用词）。

    ⚠️ 这里**不再退成 openid 后四位**。旧版 resolver 给不出名字时，压缩侧拿 `sender[-4:]`
    顶上，于是摘要里出现了「6ABA」这种人名 —— 摘要每轮注入 prompt，模型就当群里真有个
    人叫 6ABA，还会照着复读。没名字的人统一退成通用词，宁可少一点区分度。

    第二个来源是**群昵称**（当事人自己在 QQ 群里挂的那个显示名，从明文 @ 里学来）。
    它只是显示兜底、不参与判断，但拿来当压缩的署名比「一位群友」强得多。
    """
    def _fn(member_openid):
        rec = runtime.RELATIONS.get(group_id, member_openid, create=False)
        nick = (rec or {}).get("nick")
        if nick:
            return nick
        return runtime.DISPLAY_NAMES.of(group_id, member_openid)

    return _fn


async def judge_nick(nick):
    """让强模型看一眼这个称呼有没有问题。True=可以用 / False=不行 / None=判断不了。

    本地词表（relations.bad_nick）只能拦住**已经登记过**的词，拦不住新花样：
    谐音、拆字、缩写、暗指 —— 这些恰恰是设计来绕过字面匹配的。所以补这一层。

    但它是「尽力而为」，不是安全边界。2026-09-18 的实测说得最清楚：同一场围攻里
    审核拦下 10 个、漏了 7 个，漏的那几个并不比拦下的更干净 —— 它们只是没被抽中。
    所以别指望把这一层调到全对，**一次围攻能试几次**才是防线（见改名节流那一层）。

    剩下的规矩：
      · 只走审核梯队（最强那档）。弱档在这件事上等于没开 —— 实测 4.5-air 把谐音放行，
        还把正常昵称「阿澈」误杀；同批用例 4.7 全对。
        2026-11-01 三家实测（docs/model-roles.md）：这道审核该交给会推理的 gpt-6-luna
        （JUDGE_PROVIDER=openai）。走生产这条路径：政治谐音拦 14/15、正常称呼误拦 2/30、
        无结论 0；线上原来的 glm-5.3-flash 是 23/30、误拦 6/20（包括正在用的「老王」「原理」）。
        推理要额度：审核档用 REASONING_MAX_TOKENS（默认 2000），400 时难的三分之一没结论。
        token 字段、温度、推理开关按 config.MODEL_PROFILES 逐模型发，这里不用管。
      · 调用失败 / 超时 / 返回看不懂 → 返回 None，由调用方**放行**。
        不能因为审核服务不可用就把起名整个堵死，何况本地词表还在兜底。
    """
    try:
        out = await call_model(
            [
                {"role": "system", "content": prompts.PROMPT_NICK_JUDGE},
                {"role": "user", "content": f"称呼：{nick}"},
            ],
            # 让它先说一句依据再落结论（思路摊开之后更准，漏判也留得下解释）。
            # 思考模型光 reasoning 就可能花 90~140 token，160 会把真正承载 OK/NG 的正文
            # 挤成空串，审核直接 fail-open；给到 400 留足余量。
            max_tokens=config.JUDGE_MAX_TOKENS,
            tier="judge",
            temperature=0.0,       # 判断类必须冻住随机性，见 call_model 的说明
        )
    except Exception as e:
        logger.warning("🕵️ 称呼审核没跑通（先放行）: %s", str(e)[:120])
        return None
    raw = (out or "").strip()
    if not raw:
        return None
    # 取**最后一处**结论：正文里提到「OK」之类的字样不该影响判决，结论永远在末行。
    picks = re.findall(r"(?:^|\s)(OK|NG)(?:\s|$)", raw.upper())
    decision = picks[-1] if picks else ""
    if decision == "NG":
        logger.info("🕵️ 称呼审核理由（NG）：%s", raw.replace("\n", " ")[:160])
        return False
    if decision == "OK":
        return True
    logger.warning("🕵️ 称呼审核返回看不懂的内容，先放行: %r", raw[:60])
    return None


async def ask_digest(system, user):
    """摘要走**压缩梯队**（便宜、能吞原始聊天），输出上限单独配（比回复长得多）。

    不用对话梯队：压缩的 prompt 与聊天不共享前缀（换模型零缓存损失）、后台跑不怕慢、
    也不参与人格。且实测对话梯队里最强的那档更容易被内容过滤拒掉。
    """
    out = await call_model(
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        config.DIGEST_MAX_TOKENS, tier="digest",
    )
    # 出口再做一次**确定性**清洗。提示词里已经写了「敏感内容不复述」的硬规则，但实测拦不住：
    # 同一份含词的真实记录，加规则前后输出命中的词数一处没少（模型当普通昵称照抄了）。
    # 所以这里不指望模型的判断力，直接按词表把命中的词换掉。
    # 摘要正文和 <DIGEST> 结构块同源（都来自这一段 raw），洗 raw 就等于全覆盖。
    return wordfilter.scrub(out)




async def run_digest(group_id, reason=""):
    """压缩一个群的长期记忆，产出摘要 + 当天 MD + 更新承诺账本。

    同一群串行，避免定时任务和手动触发撞车。
    关键：**只有压缩成功才推进 last_run**，失败时消息仍留在归档里，下一轮会重读。
    """
    lock = runtime._digest_locks.setdefault(group_id, asyncio.Lock())
    if lock.locked():
        return
    async with lock:
        entries, batch_cursor = runtime.ARCHIVE.pending(
            group_id, runtime.DIGESTS.cursor(group_id),
            since_ts=runtime.DIGESTS.last_run(group_id), limit=config.DIGEST_MAX_PENDING)
        if not entries:
            return
        logger.info("🗜️  开始压缩群 %s 的长期记忆（%d 条待处理 / %s）",
                    group_id[-6:], len(entries), reason or "手动")
        started = time.time()
        try:
            # 个人原话裁决：新鲜提案先快照给游戏模式用（那份是「原稿」），
            # 再交给原生裁决出最终名单；换名单在摘要落槽前完成。
            mode_proposals = []

            async def curate(new_rows, old_rows, *, entries, now):
                mode_proposals.extend(copy.deepcopy(new_rows) or [])
                return await decisions_mod.curate_people(
                    group_id, new_rows, old_rows, entries=entries, now=now,
                    log=runtime.INTERACTIONS)

            result = await digest_mod.compress_group(
                runtime.DIGESTS, group_id, entries, ask_digest,
                resolver=_resolve_name(group_id),
                log=logger.info,
                refresh=lambda t: refresh_names(group_id, t),
                ledger=affinity_ledger(group_id),
                people_curator=curate,
            )
        except Exception as e:
            logger.error("❌ 群记忆压缩异常: %s", str(e)[:200])
            return
        if not result:
            logger.warning("⚠️ 群 %s 记忆未产出结果，原文保留在归档里，下轮再试", group_id[-6:])
            return

        runtime.DIGESTS.mark_compressed(
            group_id, ts=started, cursor=result.get("source_cursor", batch_cursor))
        runtime.STATE.mark_dirty()
        logger.info("📜 群 %s 记忆已更新（%.1fs / %d 条）：%s",
                    group_id[-6:], time.time() - started, result["entry_count"],
                    (result.get("brief") or "")[:70])

        # 承诺账本跟着摘要对齐：新出现的记上，摘要里消失的自动撤下
        if config.PROMISE_ENABLED:
            added, dropped = runtime.PROMISES.sync(
                group_id, (result.get("data") or {}).get("promises"),
                member_resolver=lambda name: runtime.member_id_for(group_id, name))
            if added or dropped:
                runtime.STATE.mark_dirty()
                logger.info("🧾 承诺账本已同步：新增 %d 条，撤下 %d 条", added, dropped)
            schedule_promises(group_id)

        # 好感度结算：这一整段时间里谁更亲近、谁在抬杠，一次性落库
        # （每轮那一票是回复里的记账标签，见 config.CMD_PROTOCOL_ENABLED；这里是看全天的那一道）
        if config.AFFINITY_ENABLED:
            n_aff, detail = apply_digest_affinity(
                group_id, (result.get("data") or {}).get("affinity"))
            if n_aff:
                logger.info("💗 压缩结算好感度：%d 人（%s）", n_aff, detail)
            else:
                logger.info("💗 压缩结算好感度：本段时间没有人的态度明显变化")
            # 相处模式（2026-10-19，硬枚举）：压缩按真实互动提案，代码只认白名单
            # 游戏模式只用「模型刚提议的原稿」：撤下/续存的旧条目不参与。
            n_mode, mode_detail = apply_digest_modes(group_id, mode_proposals)
            if n_mode:
                logger.info("🎭 相处模式更新：%d 人（%s）", n_mode, mode_detail)

        # 当天的人话版 MD，平时看这个就够，出错再回 archive/ 查原文
        if config.DIGEST_WRITE_MD:
            try:
                path = runtime.ARCHIVE.write_memory(
                    group_id, result.get("brief") or "", result.get("data") or {},
                    meta={"本次压缩消息数": result["entry_count"], "耗时": f"{time.time() - started:.1f}s"},
                )
                logger.info("📝 已写入 %s", os.path.relpath(path, config.BASE_DIR))
            except Exception as e:
                logger.warning("⚠️ 写入记忆 MD 失败: %s", str(e)[:120])


async def digest_loop():
    """后台定时扫描：到点就压，归档里攒太多也立即压。"""
    while True:
        await STATUS.sleep(config.DIGEST_CHECK_INTERVAL)
        if not config.DIGEST_ENABLED:
            continue
        try:
            for gid in list(runtime.DIGESTS.groups.keys()):
                pending = len(runtime.ARCHIVE.pending(gid, runtime.DIGESTS.cursor(gid), since_ts=runtime.DIGESTS.last_run(gid))[0])
                if not runtime.DIGESTS.needs_compress(
                        gid, config.DIGEST_INTERVAL_HOURS, config.DIGEST_MIN_PENDING,
                        pending, count_trigger=config.DIGEST_COUNT_TRIGGER):
                    continue
                await run_digest(gid, reason=runtime.DIGESTS.compress_reason(
                    gid, config.DIGEST_INTERVAL_HOURS, pending,
                    count_trigger=config.DIGEST_COUNT_TRIGGER))
        except Exception as e:
            STATUS.error(e)
            logger.error("⚠️ 群记忆调度异常: %s", str(e)[:200])


# ── 主动性（2026-10-19，麦麦/Agent Mind 式轻量版）──
# 心情、作息、群温都合适时自己开个话头。话头取自最近的群聊背景——主动不等于凭空造梗。
# 所有闸门在 activation.proactive_ok（4 小时间隔 / 群静默 10min~6h / 白天 / 心情不差）。
_last_proactive = {}


async def proactive_loop():
    """主动性巡检：每 30 分钟看一眼，闸门全过就让模型照着最近的话题开个头。"""
    while True:
        await STATUS.sleep(config.PROACTIVE_CHECK_SECONDS)
        try:
            now = time.time()
            hour = datetime.now().hour
            mood_v = activation.current_mood(now)
            # 挑最近还有活动的群（当前单群为主，结构上支持多群）
            candidates = [(g, b) for g, b in group_buffers.items() if b]
            if not candidates:
                continue
            gid, buf = max(candidates, key=lambda kv: max(
                float(x.get("time") or 0) for x in kv[1]))
            last_msg_ts = max(float(x.get("time") or 0) for x in buf)
            ok, why = activation.proactive_ok(
                mood_v, hour, now, _last_proactive.get(gid, 0), last_msg_ts)
            if not ok:
                continue
            fresh = [b for b in buf if now - float(b.get("time") or 0)
                     <= config.GROUP_BUFFER_TTL_SECONDS]
            if len(fresh) < 2:
                continue
            topics = "\n".join(f"- {b.get('text', '')[:60]}" for b in fresh[-4:])
            if runtime.FEEDBACK.quiet(gid, now=now):
                continue    # 反馈收敛期内不主动开口
            ok, _ = runtime.GROUP_BUCKET.try_acquire(gid)
            if not ok or not runtime.BUDGET.can_consume():
                continue
            # 说不说由小王自己定：没什么想说的就写〔不接〕（以前先问一次插嘴判断，
            # 判断说接得上才让它写；2026-11-02 起和群里没叫它的消息同一个口径）。
            prompt = naming.render(prompts.PROMPT_PROACTIVE).replace("{topics}", topics)
            trace = {"route": "proactive", "group": gid}
            token = _reply_trace.set(trace)
            try:
                body, _ = await get_ai_reply(
                    f"{gid}_proactive", prompt, context_hint=topics,
                    owner_label=owner_label(gid), state_note=activation.state_line("", mood_v),
                    allow_fallback=False)
            finally:
                _reply_trace.reset(token)
            # 主动开口没有可回的那条消息，画不了也发不了语音：写了标记只留正文；〔不接〕就是空的
            body = expression.parse(body).text if body else body
            if not body or _garbage_run(body):
                continue    # 没生成出来 / 又抽风了 —— 这轮不说话
            if await post_to_group(gid, body.strip(),
                                   model_generated=True, history=fresh[-4:],
                                   turn_id=trace.get("turn_id")):
                _last_proactive[gid] = time.time()
                logger.info("🌙 [自发开口] 距上次群消息 %.0f 分钟", (time.time() - last_msg_ts) / 60)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            STATUS.error(e)
            logger.error("❌ 主动巡检异常（不影响主流程）: %s", e)


# ── 慢活：联网查询、深度研究（2026-11-01，见 slowwork.py）──

async def _start_search(query):
    return await providers.start_research(query, instructions=search_mod.research_instructions())


async def _start_research(query):
    return await providers.start_research(
        query, instructions=search_mod.research_instructions(deep=True), deep=True)


# 普通查询实测 4–13 秒：先在线上等 ASYNC_REPLY_SECONDS，等不到才转后台，三秒一问。
# 深度研究实测一分钟上下：不在线上等，直接先应一句，十五秒一问，半小时还没好就算了。
slowwork.register(slowwork.Job("search", _start_search, providers.poll_research,
                               poll_every=3.0, give_up_after=300.0))
slowwork.register(slowwork.Job("research", _start_research, providers.poll_research,
                               poll_every=15.0, give_up_after=1800.0, wait_first=False))


def _lookup_query(user_input, buf, message_id=None, *, sender_openid=None):
    """送去查的那段话：问题本身，前面带两三句群聊当语境。不带任何 ID，命中本地词表的句子不出本机。"""
    rows = [row for row in list(buf or []) if row.get("message_id") != message_id][-3:]
    lines = []
    for row in rows:
        text = row.get("text") or ""
        if text and not wordfilter.has_hit(text):
            who = "当前说话人" if sender_openid and row.get("sender") == sender_openid else "其他群友"
            lines.append(f"{who}：{text[:120]}")
    context = "群聊前几句：\n" + "\n".join(f"- {t}" for t in lines) + "\n\n" if lines else ""
    return f"{context}现在有人问群里的机器人：{user_input}"


def _reserve_search(group_id, user_id, score, *, deep=False, cooldown=False):
    """先检查个人额度和群级限流，再同步持久化本次搜索额度。"""
    state = runtime.RESOURCE_QUOTAS.check("search", group_id, user_id, score)
    if state in ("exhausted", "unavailable"):
        return "quota" if state == "exhausted" else state
    if deep:
        allowed, reason = search_mod.DEEP_LIMITER.check(group_id)
        if not allowed:
            return "deep_" + reason
    allowed, reason = search_mod.LIMITER.check(group_id, cooldown=cooldown)
    if not allowed:
        return reason
    result = runtime.RESOURCE_QUOTAS.consume("search", group_id, user_id, score)
    if result in ("normal", "exception", "extra"):
        # 前面两次检查到这里没有 await，群级计数只在个人账成功落盘后才更新。
        if deep:
            search_mod.DEEP_LIMITER.acquire(group_id)
        search_mod.LIMITER.acquire(group_id, cooldown=cooldown)
    return result


async def await_images(message, own, others):
    """回话要看的图还没读完。own：这人自己递过来的图（这条带的、两分钟内他发的）；others：背景里别人的。

    跟联网查询一个节奏（answer_with_lookup）：线上等 ASYNC_REPLY_SECONDS，等得到就一条回复；
    他自己的图还没读完，先回一句在看图，读完再正经答（读图自己最多 AI_TOTAL_TIMEOUT）——
    以前是闷着等，等不到就答「看不到」，可图还在路上（2026-11-02 21:05）。
    别人的图不为它先开口（这句多半不是在问那张），照旧最多闷等 VISION_SETTLE_SECONDS。
    不等了也不取消读图：读图被 shield 护着，读完照常回填，下一轮能用。
    """
    mine = asyncio.ensure_future(asyncio.gather(*own, return_exceptions=True)) if own else None
    theirs = asyncio.ensure_future(asyncio.gather(*others, return_exceptions=True)) if others else None
    waits = [t for t in (mine, theirs) if t is not None]
    if not waits:
        return
    started = time.monotonic()
    await asyncio.wait(waits, timeout=config.ASYNC_REPLY_SECONDS)
    if mine is not None and not mine.done():
        logger.info("🖼 图还没读完，先回一句在看（%s）", message.group_openid[-6:])
        await safe_reply(message, IMAGE_HOLD_LINE)
        await asyncio.wait([mine], timeout=config.AI_TOTAL_TIMEOUT)
    left = config.VISION_SETTLE_SECONDS - (time.monotonic() - started)
    if theirs is not None and not theirs.done() and left > 0:
        await asyncio.wait([theirs], timeout=left)
    for task in waits:
        if not task.done():
            task.cancel()
            logger.info("🖼 背景里的图没读完，先回话")


async def answer_with_lookup(message, session_id, user_input, kind, query, *, reply_kwargs):
    """先查再答。线上等得到就一条回复；等不到先回一句在查，把手交给定时任务跟进。

    起不了头、或当场就失败了，返回 None：调用方照常回复（不带资料），别让人干等。
    """
    job = slowwork.JOBS[kind]
    handle = await job.start(query)
    if handle is None:
        return None
    state, result = await slowwork.wait(
        job, handle, config.ASYNC_REPLY_SECONDS if job.wait_first else 0.0)
    if state == slowwork.DONE:
        logger.info("🌐 联网查询当场回来了（%s）", kind)
        return await reply_in_session(message, session_id, user_input,
                                      search_note=search_mod.render_brief(result),
                                      **reply_kwargs)
    if state == slowwork.FAILED:
        return None
    outcome = await reply_in_session(message, session_id, user_input,
                                     prepared=("我去查查，等我一会儿。", None),
                                     prepared_local=True, **reply_kwargs)
    now = time.time()
    runtime.SCHEDULER.at("followup", message.group_openid, now + job.poll_every, {
        "job": kind, "handle": handle, "session": session_id, "question": user_input,
        "member": getattr(getattr(message, "author", None), "member_openid", None),
        "msg_id": message.id, "msg_ts": str(getattr(message, "timestamp", "") or ""),
        "is_owner": bool(reply_kwargs.get("is_owner")), "started": now})
    runtime.STATE.mark_dirty()
    logger.info("⏳ %s转后台，先回了一句（%s）", "深度研究" if kind == "research" else "联网查询",
                message.group_openid[-6:])
    return outcome


def _reply_target(group_id, payload):
    """定时任务里重建「回复那条消息」的对象。QQ 被动回复只要群号、原消息 ID 和
    递增的 msg_seq（Sender 按群号 + 消息 ID 续号），所以重启以后也能接着回。"""
    async def reply(**kwargs):
        client = _bot_ref.get("client")
        if client is None:
            return None
        return await client.api.post_group_message(
            group_openid=group_id, msg_id=payload["msg_id"], **kwargs)
    return SimpleNamespace(group_openid=group_id, id=payload["msg_id"],
                           timestamp=payload.get("msg_ts") or None,
                           author=SimpleNamespace(member_openid=payload.get("member")),
                           reply=reply)


async def finish_lookup(task):
    """慢活到点了就问进度；做完（或做不成）写成跟进的那句话送出去。

    QQ 被动回复只有 4 分钟：过了先试主动发言，主动发言也发不出去，就把写好的话
    留在任务里，等群里下一条消息捎带（deliver_carried），最多等一天。
    """
    p, gid, now = task["payload"], task["group"], time.time()
    if p.get("ready"):
        return None if now - p["started"] > 86400 else now + 3600
    job = slowwork.JOBS.get(p.get("job"))
    if job is None:
        return None
    state, result = await job.poll(p["handle"])
    if state == slowwork.RUNNING:
        if now - p["started"] < job.give_up_after:
            return now + job.poll_every
        state = slowwork.FAILED
    done_note = (prompts.PROMPT_RESEARCH_DONE if p["job"] == "research"
                 else prompts.PROMPT_LOOKUP_DONE)
    history = list(group_buffers.get(gid, ()))[-4:]
    completed = state == slowwork.DONE
    body, _delta, delivery = await reply_in_session(
        _reply_target(gid, p), p["session"], p["question"],
        is_owner=p.get("is_owner", False),
        owner_label=owner_label(gid),
        search_note=search_mod.render_brief(result) if completed else "",
        lookup_note=done_note if completed else "",
        prepared=None if completed else ("这次没查成，等会儿再试试。", None),
        prepared_local=not completed,
        history=history)
    status = getattr(delivery, "status", None)
    logger.info("🌐 慢活跟进（%s，%s，%.0f 秒）：%s", p["job"], state, now - p["started"], status)
    if status != "expired" or not body:
        return None   # 送达、排队补投、被拒，都到此为止
    if await post_to_group(gid, body, model_generated=completed, history=history):
        runtime.SESSIONS.record(p["session"], p["question"], body)
        return None
    p["ready"] = body
    return now + 3600


async def deliver_carried(message, group_id):
    """被动窗口和主动发言都没送出去的跟进和提醒，捎在群里下一条消息上发出去。"""
    for task in runtime.SCHEDULER.pending(kind="followup", group=group_id):
        text = task["payload"].get("ready")
        if text and runtime.SCHEDULER.cancel(task["id"]):
            runtime.STATE.mark_dirty()
            await safe_reply(message, text, interaction={"route": "followup"})
    for task in runtime.SCHEDULER.pending(kind=reminders.KIND, group=group_id):
        text = task["payload"].pop("ready", None)
        if text:
            _reminder_done(task, time.time())
            runtime.STATE.mark_dirty()
            _note_own_line(group_id, text)
            await safe_reply(message, text, interaction={"route": "reminder"})


# ── 到点提醒 ──

def _reminder_done(task, now):
    """这一次提醒了结（送到了、或者过点太久作罢）：每天的排到下一次，一次性的撤掉。"""
    nxt = reminders.next_occurrence(task["payload"], now)
    if nxt is None:
        runtime.SCHEDULER.cancel(task["id"])
        return None
    task["payload"]["at"] = nxt
    runtime.SCHEDULER.at(reminders.KIND, task["group"], nxt, task["payload"], task_id=task["id"])
    return nxt


def _reminder_hold(task, now):
    """喊停后的收敛期内先按住（主动发言这时本来也发不出去），收敛完再说。"""
    if runtime.FEEDBACK.quiet(task["group"], now=now):
        return now + runtime.FEEDBACK.quiet_seconds
    return None


async def reminder_line(group_id, payload):
    """到点说的那句：让小王照着记下的事说一句；生成不出来就用本地话术，提醒不能因此落空。"""
    who = _who_label(group_id, payload["member"])
    when = reminders.describe(payload)
    what = reminders.spell_out(payload["what"], who)
    text = None
    try:
        text = await call_model(
            [{"role": "system", "content": naming.render(prompts.SYSTEM_REMIND)},
             {"role": "user", "content": refresh_names(
                 group_id, f"要提醒的事：{what}\n（{who} 让你提醒的，定的 {when}）")}],
            config.AI_MAX_TOKENS_BANTER)
    except Exception as exc:
        logger.warning("⏰ 提醒那句没生成出来（%s），用本地话术", type(exc).__name__)
    text = wordfilter.scrub((text or "").strip().strip('"「」'))
    return text or f"{who}，到点了：{what}"


async def fire_reminder(task):
    """到点了说一句提醒。主动发不出去就留着，捎在群里下一条消息上；过点一小时还没送到就作罢。

    重启前就过了点的同理：晚一小时以上的提醒多半已经没用了（「九点提醒大家我回来了」下午才发）。
    """
    gid, p, now = task["group"], task["payload"], time.time()
    if now - p["at"] >= reminders.GRACE_SECONDS:   # >=：等捎带那条会正好在这一刻回来
        logger.info("⏰ 提醒过点太久没送出去，作罢（%s，%s）", reminders.describe(p), gid[-6:])
        p.pop("ready", None)
        return _reminder_done(task, now)
    if p.get("ready"):
        return p["at"] + reminders.GRACE_SECONDS   # 写好了在等捎带
    text = await reminder_line(gid, p)
    if await post_to_group(gid, text, model_generated=True,
                           history=list(group_buffers.get(gid, ()))[-4:]):
        logger.info("⏰ 提醒送到了（%s，%s）", reminders.describe(p), gid[-6:])
        _note_own_line(gid, text)
        return _reminder_done(task, now)
    p["ready"] = text
    return p["at"] + reminders.GRACE_SECONDS


# ── 承诺催债 ──

async def post_to_group(group_id, text, *, model_generated=False, history=(), turn_id=None):
    """主动往群里发一句话（不是回复）。催债、定时播报都要用它。

    原生反馈判过「收敛」的群，窗口内主动发言一律按住 —— 点名回复
    （safe_reply 那条路）不受影响。model_generated=True 的确认送达
    会登记进反馈缓存，让随后的人话反馈能认到这句群发。
    turn_id：这句是哪一轮输入生成的（data/turns），只进观察日志。
    """
    client = _bot_ref.get("client")
    if client is None or not text:
        return False
    if runtime.FEEDBACK.quiet(group_id):
        logger.info("🔇 [主动发言] 原生反馈收敛期内，本轮按住（%s）", group_id[-6:])
        return False
    # 主动广播也落观察日志：route=proactive、不可反馈；只记确认送达的全文，
    # 空回执/异常只算「未确认」（SDK 可能吞掉超时），不记正文。
    outgoing_id = uuid.uuid4().hex
    policy_id = config.INTERACTION_POLICY_ID   # 发起这一刻定版

    def journal(status, receipt=None, reason=""):
        try:
            fields = {"outgoing_id": outgoing_id, "source_message_id": None,
                      "status": status, "route": "proactive",
                      "feedback_eligible": False,
                      "policy_id": policy_id}
            if reason:
                fields["reason"] = reason
            if turn_id:
                fields["turn_id"] = turn_id
            if status == "delivered":
                fields["text"] = text
                rid = receipt.get("id") if isinstance(receipt, dict) \
                    else getattr(receipt, "id", None)
                if isinstance(rid, (str, int)):
                    fields["platform_message_id"] = str(rid)
            runtime.INTERACTIONS.append("delivery", group_id, user=None,
                                        actor="assistant", **fields)
        except Exception:
            # 观察记录失败不许反过来把已确认的发送说成失败，也不许补记一笔冤枉账。
            logger.warning("主动发言的观察记录失败（%s），发送结果不变", status)

    # 发送前一刻再查一次：生成期间落下的收敛信号照样按住这条主动发言。
    if runtime.FEEDBACK.quiet(group_id):
        return False
    try:
        sent = await client.api.post_group_message(group_openid=group_id, content=text, msg_type=0)
        if not sent:
            journal("unconfirmed", reason="empty receipt")
            return False
        logger.info("📢 [主动发言] 群 %s | %s", group_id[-6:], text.replace("\n", " ")[:150])
        _remember_sent(group_id, sent, text)
        # 确认送达的模型生成群发才登记反馈缓存：静态模式播报/本地文案不算。
        if model_generated:
            try:
                runtime.FEEDBACK.remember(
                    group_id, None, text, route="proactive",
                    outgoing_id=outgoing_id, history=history)
            except Exception:
                logger.exception("⚠️ 主动发言反馈登记失败（%s）", group_id[-6:])
        journal("delivered", receipt=sent)
        return True
    except Exception as e:
        logger.error("❌ 主动发言失败: %s", str(e)[:150])
        journal("unconfirmed", reason=type(e).__name__)
        return False


async def dun_promise(group_id, row):
    """让它开口要这一条账。发出去了返回 True；什么时候催、能不能开口由定时任务判。"""
    # 催谁就只给谁的材料。以前把整个未兑现清单都塞进 payload，模型看着清单自由发挥，
    # 文案说的是 A、记账却记在 dunnable[0]（B）头上 —— 账实分离
    # （实测 20:16 文案 @ 的是罗老板的蹄髈，mark 的却是家豪的账）。
    due = row.get("due_text") or "没说时间"
    payload = refresh_names(
        group_id,
        f"- {row.get('who', '有人')}：{row.get('what', '')}（说的时间：{due}）")
    text = await call_model(
        [{"role": "system", "content": naming.render(digest_mod.SYSTEM_DUN)},
         {"role": "user", "content": payload}],
        config.AI_MAX_TOKENS_BANTER,
    )
    if not text:
        return False
    text = text.strip().strip('"')
    if not await post_to_group(
            group_id, text, model_generated=True,
            history=list(group_buffers.get(group_id, ()))[-4:]):
        return False
    nag = runtime.PROMISES.mark_nagged(group_id, row["id"])
    runtime.STATE.mark_dirty()
    logger.info("🧾 已催债（第 %d 次）：%s —— %s", nag, row.get("who"), row.get("what"))
    return True


# 同一个群上一次催债的时间：几条账同时到期也别一口气全催出来。
_last_dun = {}


def _next_open_hour(now):
    """下一个不在 PROMISE_QUIET_HOURS 里的整点。"""
    hour = now - now % 3600
    for _ in range(24):
        hour += 3600
        if time.localtime(hour).tm_hour not in config.PROMISE_QUIET_HOURS:
            return hour
    return now + 3600


def _promise_hold(task, now):
    """催债出门前的规矩；挡住就返回改到什么时候。原来散在每小时一圈的 promise_loop 里。"""
    gid = task["group"]
    if time.localtime(now).tm_hour in config.PROMISE_QUIET_HOURS:
        return _next_open_hour(now)
    seen = (runtime.STATE.data.get("groups") or {}).get(gid, {}).get("last_seen", 0)
    if now - seen > 86400:
        return now + 3600          # 群里一天没人说话，就别自顾自地开口
    if runtime.FEEDBACK.quiet(gid, now=now):
        return now + runtime.FEEDBACK.quiet_seconds   # 收敛期内连催债都收声
    if not runtime.BUDGET.can_consume():
        return now + 3600
    if now - _last_dun.get(gid, 0) < config.PROMISE_CHECK_INTERVAL:
        return _last_dun[gid] + config.PROMISE_CHECK_INTERVAL
    return None


async def remind_promise(task):
    """到点催一条账。任务只是闹钟：响了回账本看这条还在不在、到没到时候。"""
    gid = task["group"]
    row = runtime.PROMISES.get(gid, task["payload"].get("id"))
    due = runtime.PROMISES.next_nag(row) if row else None
    if due is None:
        return None                # 结清了、被摘要撤下了、或催满了
    if due > time.time():
        return due
    if not await dun_promise(gid, row):
        return time.time() + scheduler_mod.RETRY_SECONDS
    _last_dun[gid] = time.time()
    return runtime.PROMISES.next_nag(row)


def schedule_promises(group_id):
    """给账本里每条承诺定一个闹钟（同一条重复定就是改期）。账本变了就调一次。"""
    for row in runtime.PROMISES.list(group_id):
        due = runtime.PROMISES.next_nag(row)
        if due is not None:
            runtime.SCHEDULER.at("promise", group_id, due, {"id": row["id"]},
                                 task_id=f"promise:{row['id']}")
    runtime.STATE.mark_dirty()


async def scheduler_loop():
    """定时任务：睡到下一条到点或有新任务（最多 SCHEDULE_TICK_SECONDS），跑到点的。"""
    while True:
        await runtime.SCHEDULER.idle(config.SCHEDULE_TICK_SECONDS)
        try:
            if await runtime.SCHEDULER.run_due():
                runtime.STATE.mark_dirty()
        except Exception as e:
            STATUS.error(e)
            logger.error("⚠️ 定时任务调度异常: %s", str(e)[:200])












# ══════════════════════ 7. 机器人主体 ══════════════════════


QQ_STARTUP_TIMEOUT_SECONDS = 60.0


class QQStartupTimeout(TimeoutError):
    """The initial gateway handshake did not confirm READY in time."""


class GroupBot(botpy.Client):
    startup_timeout = QQ_STARTUP_TIMEOUT_SECONDS

    async def start(self, *args, **kwargs):
        # botpy internally requeues failed WebSocket handshakes without raising.
        # Its existing ready Event is confirmed in our on_ready callback.
        runner = asyncio.create_task(super().start(*args, **kwargs))
        ready = asyncio.create_task(self._ready.wait())
        try:
            done, _ = await asyncio.wait((runner, ready), timeout=self.startup_timeout,
                                         return_when=asyncio.FIRST_COMPLETED)
            if not done:
                raise QQStartupTimeout()
            return await runner  # once READY arrives, normal recovery has no deadline
        finally:
            ready.cancel()
            if not runner.done():
                runner.cancel()
            await asyncio.gather(runner, ready, return_exceptions=True)

    async def _bot_login(self, token):
        try:
            return await super()._bot_login(token)
        except RuntimeError as error:
            # botpy 1.2.1 uses a plain RuntimeError for a rejected token payload.
            # Preserve its typed transport/server errors; never parse vendor text.
            if type(error) is RuntimeError:
                raise AuthenticationFailedError("QQ login did not return a usable token") from None
            raise

    async def on_ready(self):
        _bot_ref["client"] = self
        STATUS.lifecycle = "ready"
        STATUS.gateway_connected = True
        STATUS.write()
        self._ready.set()
        # 平台昵称登记进 naming：它是「附带别名」，不是触发词的权威来源 ——
        # 只会往 bot_names() 里**多加**一个叫法，顶不掉 .env 的 BOT_NAMES；
        # 而且登录时读一次，改了 QQ 昵称要重启进程才会生效。
        naming.set_platform_name(getattr(self.robot, "name", ""))
        logger.info("=" * 50)
        logger.info("🎉 机器人「%s」(ID: %s) 已更新上线！", self.robot.name, self.robot.id)
        logger.info("📛 它在这群里的叫法（触发词）：%s", "、".join(naming.bot_names()))
        if naming.bot_names() == [naming.DEFAULT_BOT_NAME]:
            # 触发词一个都没配、平台昵称也拿不到 —— 它现在只认「机器人」这个通用词。
            # 和「群主未认领」一样属于静默失效：喊它不应、也不主动接话，但没有任何报错。
            logger.warning(
                "📛 没配触发词、也没拿到平台昵称 —— 它现在只认「%s」这个通用词，"
                "群里喊别的名字它是不会应的。在 .env 里设 BOT_NAMES（可多个别名），"
                "或直接在 QQ 那边给它改个昵称",
                naming.DEFAULT_BOT_NAME)
        if runtime.OWNER_OPENID:
            logger.info("👑 群主 OpenID 已锁定: %s（好感度锁定顶格；称呼认领后自动生效）", runtime.OWNER_OPENID)
        else:
            logger.info("👑 尚未认主：首条非空私聊会自动认领并保存到 %s；"
                        "请在 QQ 后台将私聊使用范围限制给预期认领者", config.OWNER_FILE)
        logger.info("📡 全量群消息监听与智能上下文缓存池已就绪！")
        logger.info("🤖 供应商=%s 主模型=%s | 今日额度已用 %d/%d",
                    config.PROVIDER["label"], config.CANDIDATE_MODELS[0], runtime.BUDGET.used, runtime.BUDGET.limit)
        logger.info("💾 状态=%s | 会话 %s | 关系档案 %s",
                    config.STATE_FILE, runtime.SESSIONS.stats(), runtime.RELATIONS.counts())
        arch = runtime.ARCHIVE.stats()
        logger.info("🗄️  原文归档=%s/ （%d 天 / %d 条）| 摘要 MD=%s/",
                    config.ARCHIVE_DIR, arch["days"], arch["lines"], config.MEMORY_DIR)
        if config.KNOWLEDGE_ENABLED:
            logger.info("📚 可编辑知识库=%s/（改文件即生效；all.md + 每群文件）",
                        config.KNOWLEDGE_DIR)
        if config.SEARCH_ENABLED and providers.research_available():
            logger.info("🌐 联网查询已启用 | %s / %s 判断触发 + 「搜索」命令 | 线上等 %.0fs 再转后台 | "
                        "每群每日 %d 次 | 深度研究%s",
                        config.RESEARCH_PROVIDER, config.RESEARCH_MODEL,
                        config.ASYNC_REPLY_SECONDS, config.SEARCH_DAILY_CAP,
                        f"每日 {config.DEEP_RESEARCH_DAILY_CAP} 次"
                        if config.DEEP_RESEARCH_ENABLED else "关")
        elif config.SEARCH_ENABLED:
            logger.info("🌐 按需搜索已启用 | DuckDuckGo | 每群 %.0fs 冷却 / 每日 %d 次 | %s",
                        config.SEARCH_COOLDOWN_SECONDS, config.SEARCH_DAILY_CAP,
                        "显式代理" if config.SEARCH_PROXY else "直连")
        else:
            logger.info("🌐 按需搜索已关闭（SEARCH_ENABLED=false）")
        if config.DIGEST_ENABLED:
            pending = {g: len(runtime.ARCHIVE.pending(g, runtime.DIGESTS.cursor(g), since_ts=runtime.DIGESTS.last_run(g))[0]) for g in runtime.DIGESTS.groups}
            logger.info("🧠 群聊长期记忆已启用 | 攒够 %d 条或每 %.0f 小时压缩一次 | 待压缩 %s",
                        config.DIGEST_COUNT_TRIGGER, config.DIGEST_INTERVAL_HOURS,
                        "、".join(f"群{k[-6:]}:{v}条" for k, v in pending.items()) or "无")
        else:
            logger.info("🧠 群聊长期记忆已关闭（DIGEST_ENABLED=false）")
        if config.PROMISE_ENABLED:
            total = sum(len(v) for v in runtime.PROMISES.items.values())
            logger.info("🧾 承诺催债已启用 | 在账 %d 条 | 宽限 %.0f 小时，最多催 %d 次",
                        total, config.PROMISE_GRACE_HOURS, config.PROMISE_MAX_NAG)
        pending = runtime.SCHEDULER.pending()
        logger.info("⏰ 定时任务 %d 个%s", len(pending),
                    f" | 最近一个 {time.strftime('%m-%d %H:%M', time.localtime(pending[0]['due']))}"
                    f"（{pending[0]['kind']}）" if pending else "")
        logger.info("=" * 50)
        task = asyncio.create_task(probe_provider())
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)

    async def on_group_add_robot(self, event):
        if event.group_openid:
            observe_group(event.group_openid)

    async def on_group_message_create(self, message: GroupMessage):
        await self.handle_group_msg(message, is_at=False)

    async def on_group_at_message_create(self, message: GroupMessage):
        await self.handle_group_msg(message, is_at=True)

    async def handle_group_msg(self, message: GroupMessage, is_at: bool = False):

        # 1. 去重：同一条消息可能被多个事件重复投递
        if message.id in processed_msg_ids:
            if is_at:
                _learn_self_from_late_at(message)
            return
        processed_msg_ids.append(message.id)

        raw_content = message.content or ""
        group_id = message.group_openid
        sender_openid = message.author.member_openid
        voices = getattr(message, "_voice_attachments", ())
        if voices:
            suffix = "（附件过多，暂未识别）" if len(voices) > 3 else ""
            raw_content = f"[语音]{suffix} {raw_content}".strip()

        # 图片（2026-10-19，克制版读图）：群消息的图走 attachments 字段，content 往往是
        # 空的——以前纯图消息在下面的「读不出人话」闸被整条丢弃，群友发的图它连看都没看过。
        # 现在标记为 [图片] 进背景缓存，闸门放行时异步读一次图（glm-4v-flash，免费），
        # 一句话描述替换标记——**读一次永久复用**（QQ 图床 URL 有时效，描述文本才是能存的）。
        image_url = None
        for att in (getattr(message, "attachments", None) or []):
            if str(getattr(att, "content_type", "") or "").startswith("image") \
                    and getattr(att, "url", None):
                image_url = att.url
                break
        if image_url:
            raw_content = f"{raw_content} [图片]".strip()

        # 2. 识别 @机器人 或直接喊它的名字
        #    「要不要理这条」必须看**原始 content**：判定要趁 <@!openid> 还在的时候做。
        #    被 @ 的其他群友优先信事件体的 mentions。
        bot_id = getattr(self.robot, "id", "")
        bot_name = getattr(self.robot, "name", "")
        # 引用了哪条、@ 的是不是小王（2026-11-03，见 quotes.py）：群友引用小王的话时 QQ 自动 @ 它，
        # 从这里认出小王在这个群的成员 openid，以后单点 @ 也认得。
        scene = getattr(message, "_scene", None) or {}
        quoted = runtime.QUOTES.find(group_id, scene.get("ref_msg_idx"))
        if scene.get("ref_msg_idx") and quoted is None:
            logger.info("🔗 引用了一条没记着的消息（%s）", scene["ref_msg_idx"][:16])
        quotes_self = bool(quoted and quoted["who"] == quotes.SELF)
        if quotes_self:
            _learn_self(group_id, quotes.self_in(
                raw_content, [getattr(u, "member_openid", None) for u in (getattr(message, "mentions", None) or ())],
                scene.get("bots")), "群友引用它的话时带的 @")
        me = _self_openids(group_id, bot_id)
        raw_at_ids = quotes.at_ids(raw_content)
        _recent_at_ids[message.id] = raw_at_ids
        while len(_recent_at_ids) > config.DEDUP_MAX:
            _recent_at_ids.popitem(last=False)
        listed_mentions = [getattr(u, "member_openid", None)
                           for u in (getattr(message, "mentions", None) or ())
                           if getattr(u, "member_openid", None)]
        mentioned_others = [m for m in extract_mentions(message, bot_id) if m not in me]
        if mentioned_others:
            logger.info("🔗 捕捉到艾特对象 %s", "、".join(m[-4:] for m in mentioned_others))
            # 事件体只给 openid，正文里留下的明文 @昵称 是唯一能知道「群里怎么
            # 显示他」的地方 —— 日常互相 @ 是大头，别只认群主命名那一种。
            if learn_display_names(
                    group_id, mentioned_others, raw_content, naming.bot_names()):
                runtime.STATE.mark_dirty()

        # 规范化时把 @ 翻成人话（@ 了谁由档案回答）—— @ 不再被清掉，见 qqtext 的说明
        user_input = qqtext.normalize(
            raw_content, mention_label=mention_label_for(group_id, me, bot_name))
        # 名字全部按当前配置取，代码里不写死任何人名（见 naming.py）
        # 引用小王的话、又把自动带的 @ 删了：跟喊名字一样算叫到它，接不接交给分诊。
        has_name_call = quotes_self or any(n and n in user_input for n in naming.bot_names())
        bot_tags = tuple(tag for oid in me for tag in (f"<@{oid}>", f"<@!{oid}>"))
        # 平台 @ 是显性信号，一定回。正文里出现名字只是**本地提议**：
        # 「小王傻」「小王怎么样了」不等于欠它一句，要不要答交给分诊（decisions.assess）。
        at_bot = is_at or any(tag in raw_content for tag in bot_tags)
        named = has_name_call and not at_bot
        is_at = at_bot or has_name_call   # 「这条叫到了它」这个事实：归档 / 日志 / 剥称呼照旧用它
        if is_at:
            user_input = strip_call(user_input)

        # 读不出人话的消息（纯表情/纯链接）到这里就没了。整条丢弃：
        # 不入库、不进背景缓存、不回复。只有「@了机器人却一个字没说」例外，
        # 那还欠他一句「找我啥事」。
        # 图片例外（2026-10-19）：纯图消息标记为 [图片] 照常进背景——闸门放行会异步补描述，
        # 群友发的图从「整条蒸发」变成「至少留了个存在感」。
        if not user_input and not is_at and not image_url:
            return
        if not user_input and image_url:
            user_input = "[图片]"
        runtime.QUOTES.remember(group_id, scene.get("msg_idx"), sender_openid, user_input)
        # 回的是哪句：拼在前面给背景、分诊和小王看；指令解析照旧只看他自己说的（user_input）
        quote_note = ""
        if quoted or scene.get("quoted"):
            quote_note = quotes.render(
                None if quoted is None else naming.bot_name() if quotes_self
                else "自己" if quoted["who"] == sender_openid else _who_label(group_id, quoted["who"]),
                (quoted or {}).get("text") or scene.get("quoted"))
        heard = quote_note + user_input

        logger.info("📩 [%s] %s | %s", "@艾特" if is_at else "群消息", sender_openid, heard)

        # 3. 归档（这是查证用的底稿）
        # ⚠️ 本地控制指令（「猫娘模式」这类）照样落盘留痕，但打上 cmd 标记 ——
        #    压缩长期记忆时会跳过它们。它们是操作不是聊天，进了压缩就变成
        #    「这人想变猫娘」这种人物事实（2026-09-21 实测事故）。
        archived = runtime.ARCHIVE.append(
            group_id, sender_openid, heard, at=is_at,
            cmd=is_control_command(user_input), message_id=message.id,
            at_ids=raw_at_ids, mentions=listed_mentions,
            msg_idx=scene.get("msg_idx"), ref_msg_idx=scene.get("ref_msg_idx"))
        if config.DIGEST_ENABLED:
            runtime.DIGESTS.touch(group_id)
        # 记一下这个群最近有人说话：主动催债前要确认群里还活着。
        # 就地更新而不是整块替换 —— 这个槽位还存着别的每群标记（比如群主认领提示有没有说过）。
        observe_group(group_id)["last_seen"] = time.time()
        runtime.STATE.mark_dirty()

        # 3.4 交互观察：完整规范化输入落独立日志（只进不出，不进任何提示词路径）。
        runtime.INTERACTIONS.append(
            "incoming", group_id, user=sender_openid, actor="user",
            message_id=message.id, text=heard,
            at=bool(is_at), cmd=is_control_command(user_input))

        # 3.45 慢活跟进没送出去（被动窗口过了、主动发言也被拒）：捎在这条消息上。
        await deliver_carried(message, group_id)

        # 3.5 显式反馈命令：在进背景缓存/好感度/模型之前处理 —— 它是给系统的
        #     记账，不是聊天内容，进了 buffer 就会变成下轮「群聊背景」。
        # 3.5 写入群聊滑动窗口背景缓存。
        #     只挡「同一个人连着刷一模一样的内容」（复读机、表情三连），别让它把窗口占满；
        #     其余照收。以前这里是「? / 6 / 草 / 哈哈」之类的白名单枚举，来一句新的
        #     口头禅就得回来加一个词，而且把「？」这种真实的接话信号也一起挡掉了。
        #     带图的不算复读：描述回填前两张图都是「[图片]」，第二张会被当成复读丢掉
        #     （2026-11-01 12:31:53/55 连发两张，问「读这两张图」时背景里只剩一张）。
        buf = group_buffers[group_id]
        if (not buf or image_url or voices or buf[-1]["sender"] != sender_openid
                or buf[-1]["text"] != heard):
            buf.append({"sender": sender_openid, "text": heard, "time": time.time(), "message_id": message.id})
            runtime.STATE.data["buffers"][group_id] = list(buf)
        # 记录消息到达时已存在的近期上下文，后续异步处理不把更晚消息倒插到这轮。
        receipt_time = time.time()
        history_at_receipt = [row for row in buf
                              if row.get("message_id") != message.id
                              and 0 <= receipt_time - float(row.get("time") or 0)
                              <= config.GROUP_BUFFER_TTL_SECONDS]
        media.remember(group_id, message.id, sender_openid, image_url)

        if voices:
            transcript = await _recognize_voices(
                voices, f"group:{group_id}:{message.id}" if message.id else None)
            row = None
            if transcript:
                row = runtime.ARCHIVE.attach_voice_transcript(
                    group_id, message.id, transcript, sender=sender_openid,
                    buffers=group_buffers, state=runtime.STATE,
                    at=bool(is_at or any(n and n in transcript for n in naming.bot_names())))
            if row:
                user_input = user_input.replace("[语音]", row["text"], 1)
                if not at_bot and any(n and n in user_input for n in naming.bot_names()):
                    named = True
                    is_at = True
                    user_input = strip_call(user_input)
            elif not (message.content or "").strip():
                if at_bot:
                    await safe_reply(message, "这条语音我暂时听不清，能发文字吗？")
                return

        # 分诊（2026-10-31）：这条消息要问判断的那几题，**第一次用到时**才问、只问一次，
        # 之后各处复用同一份结果（见 decisions.FeedbackTracker.assess）。
        # 起名候选在 5.5 才解析出来，所以第一次调用可能在那里，也可能在 9。
        triage_box = {}

        async def triage(nick_candidate=None):
            if "result" not in triage_box:
                try:
                    triage_box["result"] = await runtime.FEEDBACK.assess(
                        group_id, sender_openid,
                        (quote_note + (user_input or (f"@{naming.bot_name()}" if at_bot else ""))).strip(),
                        message_id=message.id, is_direct=bool(at_bot), named=named,
                        history=history_at_receipt[-4:],
                        nick_candidate=nick_candidate,
                        want_recall=config.MEMORY_RECALL_ENABLED,
                        want_lookup=(config.SEARCH_ENABLED
                                     and providers.research_available()))
                except Exception:
                    logger.exception("⚠️ 分诊异常（%s）", group_id[-6:])
                    # 异常不代表「没发起过」—— 标成已尝试，同一条消息不许补发第二次。
                    triage_box["result"] = {"decision_attempted": True}
            return triage_box["result"]

        # 4. 群主认领 —— **群里永远不授权**，这里只负责把人指到私聊去。
        #    以前这段真的会在群里认主（谁先喊谁得），等于把身份挂在大喇叭上招领；
        #    现在群里说破天也只是收到一句「去私聊办」，权限一个字都不给。
        if looks_like_claim_attempt(user_input):
            if runtime.OWNER_OPENID is None:
                # 每个群只正面回一次，免得有人反复喊就反复刷屏
                if claim_redirect_pending(group_id):
                    def commit_redirect(_receipt):
                        slot = observe_group(group_id)
                        slot["claim_redirect"] = slot["owner_hint"] = True
                        runtime.STATE.mark_dirty()
                    await safe_reply(message, OWNER_CLAIM_HINT, on_delivered=commit_redirect,
                                     send_lock=owner_hint_lock(group_id),
                                     is_current=lambda: claim_redirect_pending(group_id))
                return
            await safe_reply(message, "已经认得你了，直接说就好。" if runtime.OWNER_OPENID == sender_openid
                             else "这边已经有人认领过我了。")
            return

        is_owner = runtime.OWNER_OPENID is not None and sender_openid == runtime.OWNER_OPENID

        # 5.4 起名开关：群主一句话锁上/解开整个群的起名。0 token，纯本地。
        #     排在昵称指令解析之前 —— 「锁起名」本身不含「叫我/叫X」，不会撞，
        #     但万一撞了也要让开关先说话。
        lock_cmd = relations.parse_nick_lock_command(user_input)
        if lock_cmd:
            await handle_nick_lock(message, group_id, lock_cmd, is_owner)
            return

        # 5.5 昵称管理：本人随时认领或改（以最新一次为准），群主可以 @某人 给对方指定
        #     「机器人名，叫 小满」这种带称呼前缀的要先剥掉前缀才认得出来
        bot_names = naming.bot_names()
        # mentioned_others 照传：它是「这人 @ 了别人」的事实，剥 @ 文本要用到，
        # 否则「@阿澈 叫我阿强」连本人改名都会失效。能不能给别人起名是另一件事，
        # 交给 allow_other 控制（只有群主才可能是在给别人起名）。
        nick_cmd = relations.parse_nick_command(
            user_input, bot_names=bot_names,
            mentioned_others=mentioned_others, allow_other=is_owner)
        nick_verdict = None
        if nick_cmd and nick_cmd.get("nick"):
            # 本地模式只是**提议**一个候选。判断有把握时可以否决（诱饵 / 误触发），
            # 但不能单独批准 —— 拿不准就照旧走词表、节流和审核那几道底线。
            # 2026-10-31 真实语料 32 个候选：谐音政治人名有把握拦下 6 个，
            # 却也把「姚元文」有把握地放行了；普通的「老王」五次里四次拿不准。
            # 所以它只配当否决票，不配当批准章。
            nick_verdict = (await triage(nick_candidate=nick_cmd["nick"])).get("nickname")
            if nick_verdict == "not_a_request":
                logger.info("🙅 起名模式误触发，按普通消息处理（%s）", sender_openid[-4:])
                nick_cmd = None
        if nick_cmd:
            await handle_nick_command(message, nick_cmd, group_id, sender_openid,
                                      is_owner, mentioned_others, verdict=nick_verdict)
            return
        # 解析不出命令 ≠ 没有这个意图：普通群友的「叫@某人 X」被 allow_other 吞掉了，
        # 静默掉进闲聊会让人以为在商量、甚至以为改成了。带动词的是明确意图，必须给说法。
        # 只认带动词的（不认「@老王 你好」那种任意短句），否则每句打招呼都要被回绝一次。
        if not is_owner and relations.looks_like_other_nick(
                user_input, bot_names=bot_names, mentioned_others=mentioned_others):
            logger.info("🚫 非群主要给别人起名，已明说（%s）", sender_openid[-4:])
            await safe_reply(message, NICK_OTHER_DENIED.format(owner=owner_label(group_id)))
            return

        # 9. 判定这条要不要接
        # 搜索查询先解析一次：9.5 与反馈评估共用同一判定，不重复解析。
        search_query = search_mod.extract_query(user_input)

        # 自然反馈评估：只评普通群聊文本，最多一次原生判断；指令和搜索绕过。
        # 评估用的输入都是脱敏后的本地代号，群号/openid/消息 ID 不出本机。
        feedback_result = {}
        if not is_control_command(user_input) and search_query is None:
            feedback_result = await triage()
            if feedback_result.get("target") is not None:
                # 只有解析出有效反馈才落账；内部 ID 只进本地日志。
                runtime.INTERACTIONS.append(
                    "natural_feedback", group_id, user=sender_openid,
                    actor="system", source_message_id=message.id,
                    target=feedback_result.get("target"),
                    suggested_move=feedback_result.get("suggested_move"),
                    action=feedback_result.get("action"),
                    source_reply_id=feedback_result.get("source_reply_id"),
                    policy_id=config.INTERACTION_POLICY_ID)
            # 「下一句骂你就是反馈」的跨天那一层：只收「没人请的插嘴」之后的
            # 被嫌烦/被接着逗，折进本人的插嘴接纳度（receptive.py）。
            # 内容对不对（repair_briefly / continue_normally）不算打扰的证据。
            if (config.RECEPTIVE_ENABLED
                    and feedback_result.get("target") == "bot_response"
                    and runtime.RECEPTIVE.record(
                        group_id, sender_openid,
                        feedback_result.get("action"),
                        route=feedback_result.get("source_route"))):
                runtime.STATE.mark_dirty()
        feedback_note = ""
        reply_context = ""
        if feedback_result.get("target") == "bot_response":
            if feedback_result.get("action") == "back_off":
                feedback_note = prompts.PROMPT_FEEDBACK_BACK_OFF
            elif feedback_result.get("action") == "repair_briefly":
                feedback_note = prompts.PROMPT_FEEDBACK_REPAIR
        # 跨人续谈：判断看到的上一句可能不在本人的 session；只把实际送达的
        # 原句作为本轮参考，不把反馈题的弃答改写成反馈或赋予新的回复资格。
        bot_text = feedback_result.get("bot_reply_text")
        if bot_text:
            reply_context = prompts.PROMPT_PREVIOUS_BOT_REPLY.format(
                recipient={
                    "current_member": "由本轮说话人触发的接话",
                    "another_member": "由另一位群友触发的接话",
                    "group": "群内主动发言",
                }.get(feedback_result.get("bot_reply_to"),
                      "由另一位群友触发的接话"),
                quoted_text=json.dumps(bot_text, ensure_ascii=False))

        should_reply = False
        # 没 @、没叫名字的这一轮：接不接交给小王自己（见下面最后一个分支）
        unprompted = False
        search_note = ""
        # 「居然记得」：本轮端给它的那一条本人旧话（没获批就是空串）。
        callback_note = ""
        # 门控决策：记的是「这道门当时放没放人」。没叫它的那几轮要等小王答完才知道，
        # 生成之后再记（见 11. 之后）。
        gate_decision = None
        # 收敛语气：判断有把握的 back_off，或本地那张喊停词表 —— 取并集。
        # 两者各漏一类（2026-10-31）：词表里没有「闭嘴」，判断有把握地认出了 3/3；
        # 判断对「别老损我」排第一却不够有把握，词表一眼认得。见 docs/judgment-layer.md。
        back_off = bool(feedback_result.get("back_off")) or looks_like_back_off(user_input)
        addressed = feedback_result.get("addressed")
        # 点了名：判断说「该答」就答；判断不可用也答（被叫到却被无视，比多答一句难受）——
        # 唯一例外是判断有把握地读出「收敛」：被叫闭嘴不该回一句「好的我闭嘴」。
        named_owed = named and (addressed == "answer"
                                or (addressed is None
                                    and not feedback_result.get("back_off")))
        quip = False
        if at_bot or named_owed:
            should_reply = True
            gate_decision = {"route": "direct",
                             "selected": True,
                             "reason": ("direct_address" if at_bot
                                        else "named_answer" if addressed == "answer"
                                        else "named_fallback")}
        elif named and addressed == "quip":
            # 被点名逗 / 夸 / 说起：addressed 那题已经判过「短接一句接得上」（接不上的是
            # let_it_pass），这里只决定怎么接 —— 短接，不是正经作答。暂停窗内一律不接。
            # 2026-11-01 以前还要 intervention 说 join_briefly 才接：那题问的是「没人请时
            # 插一句合不合适」，题面写明点名的消息另有人管，于是几乎总说 stay_quiet ——
            # 群主一句「小王我真的好喜欢你啊」就这样被吞了。
            quip = not runtime.FEEDBACK.quiet(group_id)
            should_reply = quip
            gate_decision = {"route": "reply", "selected": quip,
                             "reason": "named_quip" if quip else "named_quip_quiet"}
        elif (feedback_result.get("target") == "bot_response"
                and feedback_result.get("action") == "repair_briefly"):
            # 有效修正：不用点名也补一句简短修正（一条，不连环追发）。
            should_reply = True
            gate_decision = {"route": "reply", "selected": True,
                             "reason": "feedback_repair"}
        elif runtime.FEEDBACK.quiet(group_id):
            # 原生反馈判过「收敛一下」：暂停窗内没叫它的一律不问。
            gate_decision = {"route": "banter", "selected": False,
                             "reason": "feedback_quiet"}
        elif back_off:
            # 「小王闭嘴」这类没被判成该答的：不问它，免得回一句「好的我闭嘴」
            gate_decision = {"route": "banter", "selected": False,
                             "reason": "back_off"}
        elif search_query is None:
            # 没 @、没叫名字（2026-11-02 起）：不掷骰子了，交给小王 —— 是不是冲它来的、
            # 接不接，它自己看（prompts.PROMPT_BANTER_NOTE；不接就写〔不接〕）。
            # 以前这里是 12% 的骰子 × 亲密度 × 作息 × 节奏先验 × 插嘴判断 × 接纳度，
            # 冲着它说却没带名字的话（接着它刚才的话往下聊）也跟着掷 ——
            # 群主：「老任说话很明显都是对小王的」。代码只留两道底线：上面的收敛暂停窗，
            # 和下面 _unprompted_lock 一个群一次只问一句（「刚开过口」由它自己掂量）。
            should_reply = unprompted = True
            now = time.time()
            # 「居然记得」（2026-10-31 群主：「有时候让人觉得意想不到居然
            # 记得」）：本地话题闸先挑出本人旧话，再问一次原生判断 ——
            # 此刻端出来是「居然记得」还是「翻档案」。判断不可用就不端，
            # 新增的惊喜不许靠失败兜底。要稀才稀奇：每人冷却 + 每天上限。
            if (config.DECISION_CALLBACK_ENABLED
                    and config.PERSONAL_MEMORY_ENABLED
                    and config.DIGEST_ENABLED
                    and runtime.RECEPTIVE.callback_allowed(
                        group_id, sender_openid, now=now)):
                who, sources = digest_mod.match_people(
                    runtime.DIGESTS.get(group_id), group_id=group_id,
                    sender=sender_openid, text=user_input,
                    resolver=_resolve_name(group_id))
                if sources:
                    try:
                        approved = await runtime.FEEDBACK.consider_callback(
                            group_id, sender_openid,
                            quote=sources[0]["quote"],
                            quote_ts=sources[0]["ts"],
                            history=list(buf or [])[-4:], now=now,
                            source_message_id=message.id)
                    except Exception:
                        logger.exception("⚠️ 回忆判断异常（%s）", group_id[-6:])
                        approved = None
                    if approved:
                        callback_note = refresh_names(
                            group_id,
                            digest_mod.render_matched(who, sources[:1]))
        if gate_decision is not None:
            runtime.INTERACTIONS.append(
                "decision", group_id, user=sender_openid, actor="system",
                source_message_id=message.id, **gate_decision)

        # 9.5 联网：显式「搜索」命令所有群友可用、按群限流；配了联网查询时，
        #     分诊判断说「这句得先查」也会查（见下面 9.6）。lookup = (慢活名, 要查的话)。
        lookup = None
        resource_note = ""
        resource_score = (runtime.RELATIONS.get(group_id, sender_openid, create=False) or {}).get("score", 0)
        if search_query is not None:
            if not config.SEARCH_ENABLED:
                await safe_reply(message, "联网搜索没开。")
                return
            if not search_query:
                await safe_reply(message, "搜什么？把关键词带上。")
                return
            if wordfilter.has_hit(search_query):
                await safe_reply(message, "这个我不搜。")
                return
            status = _reserve_search(group_id, sender_openid, resource_score, cooldown=True)
            if status not in ("normal", "exception", "extra"):
                await safe_reply(
                    message,
                    "刚搜过，让我缓一会儿再查。" if status == "cooldown"
                    else "这会儿没法查，等会儿再试试。" if status == "unavailable"
                    else "今天查得有点累啦，明天再帮你查吧。")
                return
            if status == "exception":
                resource_note = "【本轮状态】今天的基础查询次数已用完，这次实际用了额外次数。可以自然地说这次破例帮他查，不要声称以后无限破例。"
            if providers.research_available():
                lookup = ("search", search_query)
                should_reply = True
                runtime.INTERACTIONS.append(
                    "decision", group_id, user=sender_openid, actor="system",
                    source_message_id=message.id, route="search", selected=True,
                    reason="search_override")
            else:
                results = await search_mod.search_web(search_query)
                if results is None:
                    await safe_reply(message, "搜索暂时连不上，稍后再试。")
                    return
                if not results:
                    await safe_reply(message, "（搜了一圈）没找到靠谱的结果，换个说法？")
                    return
                search_note = search_mod.render_note(results)
                should_reply = True
                logger.info("🌐 按需搜索：%d 条结果（%s）", len(results), group_id[-6:])
                # 搜索成功会改写上面的门控结果 —— 单独记一条，而不是回头涂改插嘴记录。
                runtime.INTERACTIONS.append(
                    "decision", group_id, user=sender_openid, actor="system",
                    source_message_id=message.id, route="search", selected=True,
                    reason="search_override")

        # 9.6 判断触发的联网查询：只在本轮本来就要正经回话时才查（插嘴、短接、喊停都不查）。
        #     research 是隐藏彩蛋：今天的次数用完或刚用过，就退回普通查询。
        #     自动查询不吃「搜索」命令的冷却，只算进每日上限。
        if (lookup is None and should_reply and not unprompted and not quip
                and not back_off and config.SEARCH_ENABLED
                and providers.research_available()
                and not wordfilter.has_hit(user_input)):
            kind = feedback_result.get("lookup")
            status = None
            if kind == "research":
                status = (_reserve_search(group_id, sender_openid, resource_score, deep=True)
                          if config.DEEP_RESEARCH_ENABLED else "disabled")
                if status in ("disabled", "deep_cooldown", "deep_daily"):
                    kind = "search"
            if kind == "search":
                status = _reserve_search(group_id, sender_openid, resource_score)
            if kind in ("search", "research") and status in ("quota", "daily", "unavailable"):
                await safe_reply(message, "这会儿没法查，等会儿再试试。" if status == "unavailable"
                                 else "今天查得有点累啦，明天再帮你查吧。")
                return
            if kind in ("search", "research") and status in ("normal", "exception", "extra"):
                if status == "exception":
                    resource_note = "【本轮状态】今天的基础查询次数已用完，这次实际用了额外次数。可以自然地说这次破例帮他查，不要声称以后无限破例。"
                lookup = (kind, _lookup_query(
                    (quote_note + (user_input or (f"@{naming.bot_name()}" if at_bot else ""))).strip(),
                    history_at_receipt, message.id, sender_openid=sender_openid))

        if not should_reply:
            if image_url:
                task = asyncio.create_task(media.caption_for(
                    group_id, message.id, sender_openid, image_url))
                _media_tasks.add(task)
                task.add_done_callback(_media_tasks.discard)
            return

        if is_at and not user_input and not image_url and not quote_note and not history_at_receipt:
            await safe_reply(message, f"找{naming.bot_name()}啥事？直接说就行。")
            return
        if not await BudgetGate.check(message, group_id):
            runtime.INTERACTIONS.append(
                "decision", group_id, user=sender_openid, actor="system",
                source_message_id=message.id,
                route="banter" if unprompted else "reply",
                selected=False, reason="budget_blocked")
            return
        current_read = None
        if image_url and not unprompted:
            # 这条自己带的图：读图先起着，跟背景里欠着的图一起等（见下面 await_images）
            current_read = asyncio.ensure_future(media.caption_for(
                group_id, message.id, sender_openid, image_url, foreground=True))
        elif image_url:
            # 没叫它的那一轮按后台口径读图（吃冷却）：没人问的图不值得破一次冷却，
            # 读不成也不提示它「说明看不到」—— 没人在等它看这张图。
            caption = await media.caption_for(
                group_id, message.id, sender_openid, image_url, foreground=False)
            if caption:
                user_input = f"{user_input}\n[图片：{caption}]"
            elif user_input == "[图片]":
                # 没人问、又没读成的纯图：没有可接的东西，不问它。问了它只看见裸 [图片]，
                # 就答「我这边看不到」—— 2026-11-02 21:04 群友连发几张图，张张这么答。
                runtime.INTERACTIONS.append(
                    "decision", group_id, user=sender_openid, actor="system",
                    source_message_id=message.id, route="banter",
                    selected=False, reason="image_unread")
                return
            else:
                user_input += "\n（这张图你没看；没人问你，别提它。）"

        # 回的是哪句拼到前面（指令都在上面认完了，这一截碍不着它们）
        user_input = quote_note + user_input

        # 11. 组装群聊背景 + session，走 AI
        context_hint = ""
        state_note = ""
        prev = []
        now_ts = time.time()
        buf = group_buffers.get(group_id)
        if history_at_receipt:
            # ⚠️ 必须按时间过滤（2026-10-06 实测）：buffer 是落盘的、跨重启保留，
            # 机器睡了 12 天再醒来的第一条消息，配到的还是 12 天前的对话 ——
            # 那不叫「最近背景」，叫考古。
            fresh = [b for b in history_at_receipt if 0 <= now_ts - float(b.get("time") or 0)
                     <= config.GROUP_BUFFER_TTL_SECONDS]
            # 关注度（2026-10-19）：亲密度决定背景条数——越熟的人它记得越多，
            # 生人没必要也没资格让它看到太多背景。条数表见 activation.context_turns。
            _rec = runtime.RELATIONS.get(group_id, sender_openid, create=False)
            _tier = relations.level_key((_rec or {}).get("score", 0))
            _willingness, _turns, state_note = activation.profile(_tier)
            # 带图的这条还没回填描述，别拿「[图片]」去比，把背景里别的没读的图也筛掉
            same = None if image_url else user_input
            earlier = [b for b in fresh if b["text"] != same]
            prev = earlier[-_turns:]
            # 这人刚发的图是他这句话的一部分：条数限的是能看多少别人的话，不该把他
            # 递过来的图挤掉（2026-11-02 21:05 生人发图，隔两句问「这张怎么样」，图不在背景里）
            prev = [b for b in earlier[:-_turns] if _own_recent_image(b, sender_openid, now_ts)] + prev
        if not unprompted:
            # 背景里还没读成的图先读掉（就地回填这些条目）：后台读图还在路上，或被冷却挡掉没读。
            # 不然模型只看见裸 [图片]，答「没看到图」——2026-11-01 12:32 连着三次
            # （实测见 docs/image-before-reply.md）。他自己的图等不到先回一句在看（await_images）。
            # 没叫它的那一轮不补读：补读走前台、不吃冷却，每条闲聊都补就把冷却架空了
            own = [b.get("message_id") for b in prev if _own_recent_image(b, sender_openid, now_ts)]
            await await_images(
                message, ([current_read] if current_read else []) + media.reads_owed(group_id, own),
                media.reads_owed(group_id, [b.get("message_id") for b in prev
                                            if b.get("message_id") not in own]))
            if current_read is not None:
                caption = (current_read.result()
                           if current_read.done() and not current_read.cancelled() else None)
                user_input += (f"\n[图片：{caption}]" if caption
                               else "\n（本轮图片暂不可读；明确说明这一点，不猜测图中内容。）")
        if prev:
            # 必须标出发言人。之前只给一串裸文本，模型分不清哪句是谁说的，
            # 于是把别人认领的名字安到了当前这位头上 —— 叫错人就是这么来的
            # （_who_label 已提为模块级：读图上下文共用，两处渲染不许漂移）
            rows = [(float(b.get("time") or 0),
                     f"- {'当前说话人' if b['sender'] == sender_openid else '其他群友（' + _who_label(group_id, b['sender']) + '）'}：{b['text']}")
                    for b in prev]
            # 同一段时间里它自己说过的话按时间插进去（见 own_lines）；它自己刚画的图往前多看一段（_own_recent_drawing）
            rows += [(o["time"], f"- 你：{o['text']}") for o in own_lines.get(group_id, ())
                     if o["time"] <= now_ts
                     and (rows[0][0] <= o["time"] or _own_recent_drawing(o, now_ts))]
            rows.sort(key=lambda row: row[0])
            context_hint = refresh_names(group_id, "\n".join(line for _, line in rows))
            # ⚠️ 这行是对着「爱翻旧账的模型」写的（实测 Gemini 会把背景里别人的言行安到
            # 当前说话人头上，一晚上把「当猫娘」安给了三个人）。所以要点名三件事：
            # 每句都有主，当前说话人与其他人按 ID 区分，引用前先核对标记。
            context_hint = _BG_PREAMBLE + "\n" + context_hint

        # 每人每群一份独立 session，彻底隔离会话，绝不串台
        session_id = f"{group_id}_{sender_openid}"

        # 称呼与游戏关系注入：称呼在好感开关之外也要给（不然 AFFINITY_ENABLED=false
        # 时连名字都叫不出来）；游戏部分（档位/模式/里程碑）只在好感开着时写。
        # 同时消费掉待播报的里程碑。
        target_record = runtime.RELATIONS.get(group_id, sender_openid,
                                              create=config.AFFINITY_ENABLED)
        relation_note = relations.build_relation_note(
            target_record, sender_openid,
            other_names=_other_names(group_id, sender_openid),
            include_game=config.AFFINITY_ENABLED)
        pending_milestone = ((target_record or {}).get("pending_milestone")
                             if config.AFFINITY_ENABLED else None)
        def commit_milestone():
            if pending_milestone and target_record.get("pending_milestone") == pending_milestone:
                target_record["pending_milestone"] = None
                target_record["told_day"] = time.strftime("%Y-%m-%d")

        # 群聊长期记忆注入：**每日层只留共享的群表达参考**（口头禅/语气词）。
        # 个人材料不再进这层 —— 它随说话人与话题变化，属于本轮动态层（见下方
        # personal_note）。简报/台账的独立出口照旧：群史记查询、催债定时任务、
        # 查账触发词 —— 需要时查得到，不需要时不陪跑。
        summary = runtime.DIGESTS.get(group_id) if config.DIGEST_ENABLED else None
        group_memory = refresh_names(group_id, digest_mod.render_style(summary)) if summary else ""

        # 可编辑知识库：维护者改文件、下一轮即生效；只按关键词取少量进动态层。
        knowledge_note = ""
        if (not unprompted and not quip and not back_off
                and not feedback_note and config.KNOWLEDGE_ENABLED):
            knowledge_note = refresh_names(
                group_id, runtime.KNOWLEDGE.render(group_id, user_input))
            if knowledge_note:
                logger.info("📚 知识库命中：%d 条（%s）",
                            knowledge_note.count("\n- "), session_id[-12:])

        # 旧账召回：分诊判断「这句的回答要靠本群旧账」时才查本地归档；随机插嘴不翻旧账。
        # 以前靠「上次 / 之前 / 答应」这类线索词触发，真实语料里点名消息上命中的 5 条全是误触发
        # （「之前那个模型更有趣」「我叫王红闻你就要答应」），判断全判对了。
        recall_note = ""
        if (not unprompted and not quip and not back_off
                and not feedback_note and config.MEMORY_RECALL_ENABLED
                and feedback_result.get("recall") == "look_up"):
            recall_names = _recall_names(group_id)
            recall_now = time.time()
            selection = recall_mod.select(
                group_id, user_input,
                archive=runtime.ARCHIVE, promises=runtime.PROMISES,
                names=recall_names, now=recall_now, exclude_text=user_input)
            recall_note = refresh_names(group_id, recall_mod.render(
                selection, name_of=_resolve_name(group_id), now=recall_now))
            _record_recall(group_id, sender_openid, message.id, user_input,
                           recall_names, recall_now, selection)
            if recall_note:
                logger.info("🔎 旧账召回：%d 条本地证据（%s）",
                            recall_note.count("\n- "), session_id[-12:])

        # 个人原话：当前说话人、当前话题命中、来源与时间齐备才给，最多两条。
        # 知识库/旧账/搜索已答了本轮要的东西时不再叠加；随机插嘴与喊停同样不给。
        # 插嘴轮：只认门控里被原生判断放行的那一条（callback_note）；
        # 普通接话轮照旧按本人 + 当前话题现挑。两条路都走同一道准入闸。
        personal_note = callback_note
        if (summary and not unprompted and not quip and not back_off
                and not feedback_note
                and not any((knowledge_note, recall_note, search_note, lookup))):
            personal_note = refresh_names(group_id, digest_mod.render_people(
                summary, group_id=group_id, sender=sender_openid, text=user_input,
                resolver=_resolve_name(group_id)))
            if personal_note:
                logger.info("🗣 个人原话：%d 条命中（%s）",
                            personal_note.count("\n- "), session_id[-12:])

        reply_kwargs = dict(
            on_delivered=commit_milestone, owner_hint_group=group_id,
            is_owner=is_owner,
            context_hint=context_hint, unprompted=unprompted,
            relation_note=relation_note, group_memory=group_memory,
            owner_label=owner_label(group_id), state_note=state_note,
            knowledge_note=knowledge_note, recall_note=recall_note,
            personal_note=personal_note,
            callback_approved=bool(callback_note),
            feedback_note=feedback_note, reply_context=reply_context,
            playful_note=(prompts.PROMPT_PLAYFUL_REQUEST
                          if feedback_result.get("playful_request") and not lookup
                          and not search_note else ""),
            resource_note=resource_note,
            back_off=back_off, quip=quip,
            history=list(buf or [])[-4:],
        )
        outcome = None
        if lookup:
            outcome = await answer_with_lookup(message, session_id, user_input,
                                               *lookup, reply_kwargs=reply_kwargs)
            if outcome is None:
                await safe_reply(message, "这次没查成，等会儿再试试。")
                return
        if outcome is None:
            # 画不画、说不说语音由小王在回复里自己决定；这里只定它这一轮「能不能」。
            # 搜索、喊停这轮不画也不念：前者要的是资料，后者对方已经不想听了。
            # （别叫 media：那是读图模块的名字，同一个函数里前面还在用它）
            this_turn = (None if back_off or search_note else
                         media_turn(group_id, sender_openid, resource_score,
                                    asked=not quip, remind=not unprompted))
            if unprompted:
                outcome = await reply_unprompted(message, session_id, user_input,
                                                 media=this_turn, callback_note=callback_note,
                                                 **reply_kwargs)
            else:
                outcome = await reply_in_session(message, session_id, user_input,
                                                 search_note=search_note, media=this_turn,
                                                 **reply_kwargs)
        reply_text, delta, delivery = outcome
        if delivery is None:
            # 模型行使了不说话的权利：这轮到此为止，好感度/情绪结算全部跳过
            return

        # 日内微调（2026-10-06 改）：
        #   「被 @ 且没扣分就 +1」撤掉了 —— 那等于把好感度变成「被搭理次数」，
        #   实测一天 100 多次记账里 108 次 +1，连一直骂人的也在涨。
        # 现在是：模型判**方向**（-1/0/1，见 CMD_PROTOCOL），拿不到模型判定时退回本地词表，
        #   最后统一过每日封顶。真正的印象结算仍在压缩里做（它看得到一整天，更准）。
        if config.AFFINITY_ENABLED:
            reason = "模型判方向" if delta is not None else "词表兜底"
            if delta is None:
                delta = relations.local_sentiment(user_input)
            # 有效原生反馈在场时，负向情绪不落账：被指出问题、嫌插话时机不对、
            # 甚至玩笑式粗口都不该扣交情（正向与游戏结算照旧照记）。
            if (delta or 0) < 0 and feedback_result.get("target") in (
                    "bot_response", "another_person", "unclear"):
                delta = 0
            # reason 区分开是有用的：跑几天就能从日志里看出「模型判方向」和
            # 「词表兜底」各自的占比与准头，再决定要不要把权重挪向压缩结算。
            apply_affinity_delta_capped(group_id, sender_openid, delta, reason)
            # 心情（2026-10-19）：跟着每轮结算漂移——被怼多了它会烦，被捧了会顺气。
            # 零额外模型调用：方向值是现成的，心情只是它的指数移动平均。
            if delta:
                activation.note_mood(delta)

    async def on_c2c_message_create(self, message: Message):
        """私聊。以前这里连去重都没有，重复投递会重复扣额度。"""
        
        if message.id in processed_msg_ids:
            return
        processed_msg_ids.append(message.id)

        user_input = (message.content or "").strip()
        sender_openid = message.author.user_openid
        voices = getattr(message, "_voice_attachments", ())
        if voices and sender_openid:
            transcript = await _recognize_voices(
                voices, f"private:{sender_openid}:{message.id}" if message.id else None)
            if transcript:
                user_input = f"{user_input} {transcript}".strip()
            else:
                note = "语音附件过多，暂时无法识别" if len(voices) > 3 else "语音暂时无法识别"
                if not user_input:
                    await safe_reply(message, f"{note}，能发文字吗？")
                    return
                user_input = f"{user_input}\n（{note}）"
        logger.info("📩 [私聊]%s", " [语音]" if voices else "")
        if not user_input or not sender_openid:
            return

        # Platform access scope is configured in QQ; the first nonempty C2C claims locally.
        if runtime.OWNER_OPENID is None:
            try:
                runtime.claim_owner(sender_openid)
            except (OSError, UnicodeError) as error:
                logger.error("自动认主保存失败: %s", type(error).__name__)
                await safe_reply(message, "刚才没能保存认领信息，请稍后再发一条私聊。")
                return

        is_owner = runtime.OWNER_OPENID is not None and sender_openid == runtime.OWNER_OPENID

        # 私聊不办改名：称呼是按群存的（`群号|openid`），这条消息里没有群号 ——
        # 改了也不知道该改在哪儿。必须**明说**：以前这句话直接喂给模型，被当成闲聊
        # 回了个玩笑，当事人完全不知道命令没生效，还以为「叫不动它」。0 token，不占额度。
        nick_cmd = relations.parse_nick_command(
            user_input, bot_names=naming.bot_names(),
            mentioned_others=(), allow_other=is_owner)
        if nick_cmd:
            logger.info("🚫 私聊收到改名命令（%s），已指回群里", nick_cmd.get("scope"))
            await safe_reply(message, NICK_PRIVATE_CHAT_HINT)
            return

        # 起名开关同理：锁是按群存在的，私聊没有群号，指回群里办
        if relations.parse_nick_lock_command(user_input):
            logger.info("🚫 私聊收到起名开关指令，已指回群里")
            await safe_reply(message, NICK_PRIVATE_CHAT_HINT)
            return

        if not runtime.BUDGET.can_consume():
            logger.warning("🛑 今日额度已用尽，私聊也一并拒绝（%d/%d）", runtime.BUDGET.used, runtime.BUDGET.limit)
            runtime.STATE.mark_dirty()
            await safe_reply(message, "（小声）今天额度用冒了，明天再陪你聊啊兄弟。")
            return
        runtime.STATE.mark_dirty()

        # 私聊不走分诊：不为最私密的那一路新开第三方数据流，喊停照旧靠 get_ai_reply 里的本地词表。
        await reply_in_session(
            message, f"user_{sender_openid}", user_input,
            is_owner=is_owner, private=True)


# ══════════════════════ 8. 启动 / 优雅退出 / 重连 ══════════════════════


def install_signal_handlers():
    """SIGTERM/SIGINT 时先落盘再退出，别让刚聊的上下文白丢。"""

    def handler(signum, _frame):
        name = signal.Signals(signum).name if hasattr(signal, "Signals") else str(signum)
        logger.warning("🛑 收到 %s，正在保存状态后退出...", name)
        try:
            runtime.STATE.save_now(force=True)
            logger.info("💾 状态已保存至 %s", config.STATE_FILE)
        except Exception as e:
            logger.error("❌ 退出时保存状态失败: %s", e)
        sys.exit(0)

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, handler)
        except ValueError:
            pass  # 非主线程时无法注册，忽略


async def prune_loop():
    """定期清理过期会话，避免长期运行下内存无界增长。"""
    while True:
        await STATUS.sleep(config.SESSION_PRUNE_INTERVAL)
        try:
            dropped = runtime.SESSIONS.prune()
            if dropped:
                runtime.STATE.mark_dirty()
                logger.info("🧹 已清理 %d 个过期会话，当前 %s", dropped, runtime.SESSIONS.stats())
            stale = runtime.RELATIONS.prune()
            if stale:
                runtime.STATE.mark_dirty()
                logger.info("🧹 已清理 %d 份长期失联的关系档案，当前 %s", stale, runtime.RELATIONS.counts())
        except Exception as e:
            STATUS.error(e)
            logger.error("⚠️ 会话清理异常: %s", e)


def _run_bot():
    errors = config.validate()
    if errors:
        raise SystemExit("❌ 配置缺失：\n  - " + "\n  - ".join(errors))

    setup_logging()
    patch_aiohttp_ssl()
    install_ws_watchdog()
    load_owner()

    _main_models = providers.MODEL_CHAINS.get(config.AI_PROVIDER) or []
    logger.info("🤖 供应商=%s | 主模型=%s | 梯队=%s",
                config.PROVIDER["label"],
                _main_models[0] if _main_models else "（无可用 Key）",
                " → ".join(config.PROVIDER_PRESETS[n]["label"] for n in providers.PROVIDER_CHAIN))
    # 各司其职：每一档走哪家、第一个模型是谁（config.TIER_PROVIDERS）
    for _tier, _chains in (("回复", providers.MODEL_CHAINS), ("摘要", providers.MODEL_CHAINS_DIGEST),
                           ("审核", providers.MODEL_CHAINS_JUDGE)):
        _key = {"回复": "chat", "摘要": "digest", "审核": "judge"}[_tier]
        _name = providers._TIER_PROVIDER_CHAINS[_key][0] if providers._TIER_PROVIDER_CHAINS[_key] else ""
        _first = (_chains.get(_name) or ["（无）"])[0]
        _profile = providers.model_profile(_first)
        _level = config.TIER_REASONING.get(_key) if (_profile or {}).get("effort") else ""
        logger.info("   %s → %s / %s%s", _tier, config.PROVIDER_PRESETS.get(_name, {}).get("label", "（无）"),
                    _first, (f"（推理 {_level}）" if _level else "（已登记调用档案）") if _profile else "")
    if runtime.SESSIONS._sessions:
        logger.info("💾 已恢复 %s 个会话上下文", len(runtime.SESSIONS._sessions))

    _bot_ref["client"] = None
    STATUS.lifecycle = "starting"
    STATUS.gateway_connected = False
    STATUS.configure(config.STATUS_FILE, lambda: {
        "providers": providers.status(),
        "budget": {"day": runtime.BUDGET.today(), "used": runtime.BUDGET.used,
                   "limit": runtime.BUDGET.limit, "remaining": runtime.BUDGET.remaining},
        "delivery": SENDER.status(),
        "paths": {"state": config.STATE_FILE, "archive": config.ARCHIVE_DIR,
                  "memory": config.MEMORY_DIR, "log": config.LOG_FILE}})
    STATUS.write()
    install_signal_handlers()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    jobs = {
        "flush": lambda: runtime.STATE.flush_loop(sleep=STATUS.sleep, on_error=STATUS.error),
        "watchdog": ws_watchdog_loop, "prune": prune_loop,
        "proactive": proactive_loop, "resend": resend_queue_loop,
    }
    if config.DIGEST_ENABLED:
        jobs["digest"] = digest_loop
    if config.PROMISE_ENABLED:
        runtime.SCHEDULER.register("promise", remind_promise, hold=_promise_hold)
        for gid in list(runtime.PROMISES.items):
            schedule_promises(gid)
    runtime.SCHEDULER.register("followup", finish_lookup)
    if config.REMINDER_ENABLED:
        runtime.SCHEDULER.register(reminders.KIND, fire_reminder, hold=_reminder_hold)
    jobs["schedule"] = scheduler_loop
    for name, factory in jobs.items():
        task = loop.create_task(STATUS.run(name, factory))
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)

    return _connect_bot()


def _login_failure(reason):
    command = shlex.join(["regular", "--directory", config.BASE_DIR, "run"])
    logger.error("❌ %s。请核对 %s 中的 QQ_APP_ID / QQ_APP_SECRET、QQ 后台权限和网络，"
                 "修正后运行：%s", reason, os.path.join(config.BASE_DIR, ".env"), command)


def _connect_bot():
    deadline = time.monotonic() + QQ_STARTUP_TIMEOUT_SECONDS
    attempt = 0
    while True:
        started_at = time.time()
        try:
            intents = botpy.Intents(public_messages=True, public_guild_messages=True)
            client = GroupBot(intents=intents, timeout=int(config.QQ_API_TIMEOUT),
                              ext_handlers=False)
            client.startup_timeout = (max(0, deadline - time.monotonic())
                                      if _bot_ref.get("client") is None else None)
            logger.info("📡 正在连接 QQ；收到鉴权后的 READY 才表示上线成功")
            client.run(appid=config.QQ_APP_ID, secret=config.QQ_APP_SECRET)
            return 0  # botpy also returns normally when Ctrl+C stops its runner.
        except QQStartupTimeout:
            STATUS.lifecycle = "error"
            _login_failure("首次连接在 %.0f 秒内未收到 QQ READY" % QQ_STARTUP_TIMEOUT_SECONDS)
            return 1
        except (AuthenticationFailedError, ForbiddenError) as error:
            STATUS.lifecycle = "error"
            _login_failure("QQ 登录或权限检查失败（" + type(error).__name__ + "）")
            return 1
        except SystemExit:
            raise
        except KeyboardInterrupt:
            logger.info("🛑 已停止")
            return 0
        except Exception as error:
            attempt += 1
            logger.error("⚠️ QQ 连接失败：%s", type(error).__name__)
            if _bot_ref.get("client") is None and attempt >= 3:
                STATUS.lifecycle = "error"
                _login_failure("首次连接连续 3 次未能上线")
                return 1
            STATUS.lifecycle = "reconnecting"
        finally:
            STATUS.gateway_connected = False
            STATUS.write()
            try:
                runtime.STATE.save_now(force=True)
            except Exception as error:
                logger.error("❌ 落盘失败：%s", type(error).__name__)

        # Keep recovery after a successful login; bound failures before first READY.
        uptime = time.time() - started_at
        if uptime > config.RESET_BACKOFF_AFTER and _bot_ref.get("client") is not None:
            attempt = 0
            delay = config.RECONNECT_MIN_DELAY
        else:
            delay = min(config.RECONNECT_MAX_DELAY, config.RECONNECT_MIN_DELAY * (2 ** max(0, attempt - 1)))
            delay *= random.uniform(0.8, 1.2)
        if _bot_ref.get("client") is None:
            delay = min(delay, max(0, deadline - time.monotonic()))
        logger.info("🔁 %.1f 秒后重连（第 %d 次尝试）", delay, attempt + 1)
        time.sleep(delay)


async def _shutdown(*, cancel_all=False):
    tasks = (list(asyncio.all_tasks() - {asyncio.current_task()})
             if cancel_all else list(_background_tasks))
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    await media.close(cancel=True)
    tasks = list(_media_tasks)
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    await VOICE.close()
    await providers.close()


def run_bot():
    exit_code = 0
    try:
        exit_code = _run_bot()
        return exit_code
    finally:
        STATUS.lifecycle = "error" if exit_code else "stopped"
        STATUS.gateway_connected = False
        STATUS.write()
        try:
            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                loop = None
            if loop is not None and not loop.is_closed() and not loop.is_running():
                loop.run_until_complete(_shutdown(cancel_all=True))
        finally:
            runtime.STATE.save_now(force=True)


if __name__ == "__main__":
    raise SystemExit(run_bot())
