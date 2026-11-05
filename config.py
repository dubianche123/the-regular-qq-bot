"""集中配置：凭证、AI 供应商、运行时参数。

凭证一律从环境变量读取（先加载同目录 .env），源码里不再出现任何密钥。
缺少必要凭证会直接启动失败并给出提示 —— 宁可 fail fast，也不要带残缺配置跑起来。
"""

import math
import os
from pathlib import Path
from dotenv import dotenv_values as _dotenv_values, load_dotenv as _load_dotenv
from regular.keys import read_api_keys

# CLI sets REGULAR_HOME explicitly; direct source runs keep the old project root.
BASE_DIR = str(Path(os.environ.get("REGULAR_HOME", Path(__file__).parent)).expanduser().resolve())
CONFIG_ERRORS = []
# The panel must distinguish explicit parent environment from values imported
# from .env, so a newly saved file is read afresh by a child bot process.
EXTERNAL_ENV_KEYS = frozenset(os.environ)
DOTENV_KEYS_AT_IMPORT = frozenset(_dotenv_values(
    os.path.join(BASE_DIR, ".env"), interpolate=False, encoding="utf-8"))


def load_dotenv(path=None):
    """Use python-dotenv's quoting/comments, without replacing real environment values."""
    return _load_dotenv(path or os.path.join(BASE_DIR, ".env"), override=False,
                        encoding="utf-8", interpolate=False)


load_dotenv()


def _env_str(name, default=""):
    val = os.environ.get(name)
    return val.strip() if val and val.strip() else default


def _env_bool(name, default=False):
    val = os.environ.get(name)
    if val is None:
        return default
    value = val.strip().lower()
    if value in ("1", "true", "yes", "on"):
        return True
    if value in ("0", "false", "no", "off", ""):
        return False
    CONFIG_ERRORS.append(f"{name} 必须是 true/false")
    return default


def _env_int(name, default):
    try:
        return int(str(os.environ.get(name, default)).strip())
    except (TypeError, ValueError):
        CONFIG_ERRORS.append(f"{name} 数值无效")
        return default


def _env_float(name, default):
    try:
        return float(str(os.environ.get(name, default)).strip())
    except (TypeError, ValueError):
        CONFIG_ERRORS.append(f"{name} 数值无效")
        return default


# ══════════════════ QQ 开放平台凭证 ══════════════════
QQ_APP_ID = _env_str("QQ_APP_ID")
QQ_APP_SECRET = _env_str("QQ_APP_SECRET")

# ══════════════════ 触发词 / 机器人身份 ══════════════════
# BOT_NAMES = 触发词：机器人在群里被叫到的名字（可多个别名，逗号分隔）。
# 它是部署时**最该先改的一处** —— 不配好，机器人在群里就是个哑巴：喊它不应、也不主动
# 接话，而且不报错（所以启动日志会把当前这串打出来，见 bot.on_ready）。
# **刻意不给默认人名**：代码里出现具体人名，换个人部署就得回来改代码，也没法开源。
# 留空就只认平台昵称（你在 QQ 那边定的机器人名字，进程一起来就知道），再不行才用通用词。
# 这一串同时管三件事：识别「是不是在叫它」、剥掉喊人前缀、提示词里的自称。
BOT_NAMES = [n.strip() for n in _env_str("BOT_NAMES", "").split(",") if n.strip()]

# ══════════════════ AI 供应商 ══════════════════
# All endpoints use the existing OpenAI-compatible SDK. Presets are conveniences,
# not statements about an account's model access, price, quota or reliability.
PROVIDER_PRESETS = {
    "openai": {
        "label": "OpenAI-compatible",
        "base_url": _env_str("OPENAI_BASE_URL"),
        "api_key": _env_str("OPENAI_API_KEY"),
        "models": [],
        "extra_body": {},
    },
    "zhipu": {
        "label": "智谱 GLM",
        "base_url": _env_str("ZHIPU_BASE_URL", "https://open.bigmodel.cn/api/paas/v4"),
        "api_key": _env_str("ZHIPU_API_KEY"),
        "models": ["glm-4.7", "glm-4.5-air", "glm-4-flash"],
        "models_digest": ["glm-4.5-air", "glm-4-flash"],
        "models_judge": ["glm-4.7"],
        "extra_body": {"thinking": {"type": "disabled"}},
        "temperature": 0.7,
    },
    "gemini": {
        "label": "Google Gemini",
        "base_url": _env_str("GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai"),
        "api_key": _env_str("GEMINI_API_KEY"),
        "models": ["gemini-3.5-flash-lite", "gemini-3.5-flash", "gemini-3.6-flash"],
        "models_digest": ["gemini-3.5-flash-lite"],
        "models_judge": ["gemini-3.5-flash-lite"],
        "extra_body": {},
    },
}

for _provider_name, _preset in PROVIDER_PRESETS.items():
    for _tier_key in ("models", "models_digest", "models_judge"):
        _model_override = _env_str(f"{_provider_name.upper()}_{_tier_key.upper()}")
        if _model_override:
            _models = [name.strip() for name in _model_override.split(",") if name.strip()]
            if _models:
                _preset[_tier_key] = list(dict.fromkeys(_models))
            else:
                CONFIG_ERRORS.append(f"{_provider_name.upper()}_{_tier_key.upper()} 至少需要一个模型名")

AI_PROVIDER = _env_str("AI_PROVIDER", "openai").lower()
if AI_PROVIDER not in PROVIDER_PRESETS:
    CONFIG_ERRORS.append("AI_PROVIDER 必须是 openai、zhipu 或 gemini")
    AI_PROVIDER = "openai"
PROVIDER = PROVIDER_PRESETS[AI_PROVIDER]
FALLBACK_PROVIDER = _env_str("FALLBACK_PROVIDER", "").lower()
if FALLBACK_PROVIDER and FALLBACK_PROVIDER not in PROVIDER_PRESETS:
    CONFIG_ERRORS.append("FALLBACK_PROVIDER 必须为空、openai、zhipu 或 gemini")
    FALLBACK_PROVIDER = ""

# 各司其职（2026-11-01）：回复、摘要、审核可以各走一家；不设就跟 AI_PROVIDER。
# 每一档照旧可以落到 FALLBACK_PROVIDER；读图另有 VISION_PROVIDER。
TIER_PROVIDERS = {}
for _tier, _env_name in (("chat", "CHAT_PROVIDER"), ("digest", "DIGEST_PROVIDER"),
                         ("judge", "JUDGE_PROVIDER")):
    _value = _env_str(_env_name, "").lower() or AI_PROVIDER
    if _value not in PROVIDER_PRESETS:
        CONFIG_ERRORS.append(f"{_env_name} 必须是 openai、zhipu 或 gemini")
        _value = AI_PROVIDER
    TIER_PROVIDERS[_tier] = _value

def load_api_keys(provider=None):
    """多 Key 灾备轮询：同级目录建 api_keys_{provider}.txt，每行一个 Key（# 开头为注释）。

    按供应商分开存，避免把智谱的 Key 误喂给 Gemini。
    """
    provider = provider or AI_PROVIDER
    preset = PROVIDER_PRESETS[provider]
    key_file = os.path.join(BASE_DIR, f"api_keys_{provider}.txt")
    return read_api_keys(key_file, preset.get("api_key") or "")


AI_KEYS = load_api_keys()
CANDIDATE_MODELS = list(PROVIDER["models"])

# ══════════════════ 安全 / 网络 ══════════════════
# 默认验证 TLS。证书问题应修复证书链；兼容旧环境时可显式关闭，并由 doctor 提示。
SSL_VERIFY = _env_bool("SSL_VERIFY", True)

# ══════════════════ 运行数据路径 ══════════════════
# New installs keep runtime files together. Existing root-level deployments stay
# in place until the operator deliberately configures DATA_DIR; never move data on import.
_LEGACY_NAMES = ("state.json", "owner.txt", "archive", "memory")
LEGACY_DATA_PATHS = [os.path.join(BASE_DIR, n) for n in _LEGACY_NAMES
                     if os.path.exists(os.path.join(BASE_DIR, n))]
_explicit_data_dir = _env_str("DATA_DIR")
_data_location = Path(_explicit_data_dir or ("." if LEGACY_DATA_PATHS else "data")).expanduser()
DATA_DIR = str((Path(BASE_DIR) / _data_location).resolve())
LEGACY_DATA_LAYOUT = not _explicit_data_dir and bool(LEGACY_DATA_PATHS)


def data_path(name, default):
    """Explicit old per-file paths remain project-relative; defaults follow DATA_DIR."""
    value = _env_str(name)
    return str((Path(BASE_DIR) / Path(value).expanduser() if value else Path(DATA_DIR) / default).resolve())


LOG_FILE = data_path("LOG_FILE", "bot.log")
LOG_MAX_BYTES = _env_int("LOG_MAX_BYTES", 2 * 1024 * 1024)
LOG_BACKUP_COUNT = _env_int("LOG_BACKUP_COUNT", 5)
LOG_LEVEL = _env_str("LOG_LEVEL", "INFO").upper()
STATE_FILE = data_path("STATE_FILE", "state.json")
OWNER_FILE = data_path("OWNER_FILE", "owner.txt")
STATUS_FILE = data_path("STATUS_FILE", "status.json")

STATE_SAVE_INTERVAL = _env_float("STATE_SAVE_INTERVAL", 30.0)  # 脏数据刷盘间隔（秒）
SESSION_PRUNE_INTERVAL = _env_float("SESSION_PRUNE_INTERVAL", 600.0)

# ══════════════════ 会话上下文（滑动窗口）══════════════════
CONTEXT_MAX_TURNS = _env_int("CONTEXT_MAX_TURNS", 8)     # 最多记住多少个来回
CONTEXT_MAX_CHARS = _env_int("CONTEXT_MAX_CHARS", 3000)  # 上下文总字数上限，防止 token 爆炸
CONTEXT_TTL_SECONDS = _env_float("CONTEXT_TTL_SECONDS", 1800.0)  # 静默 30 分钟当作换话题

# ══════════════════ 群聊背景缓存 ══════════════════
GROUP_BUFFER_SIZE = _env_int("GROUP_BUFFER_SIZE", 10)
# 背景条目的保鲜期（秒）。⚠️ 只此一份：GROUP_BUFFER_TTL_SECONDS 是唯一在用的名字，
# 插嘴提示词「由头只从最近的话里挑」的口径就建立在它之上 —— 改这里会同时改变插嘴的眼界。

# ══════════════════ 限流（群级 + 全局，刻意不做 per-user 配额）══════════════════
# 令牌桶：每个群初始 N 个令牌，每 R 秒回补 1 个。既能扛住正常聊天的突发，
# 又能在有人连刷时把请求压到平均 ~60/R 次每分钟。
GROUP_RATE_CAPACITY = _env_float("GROUP_RATE_CAPACITY", 8.0)
GROUP_RATE_REFILL_SECONDS = _env_float("GROUP_RATE_REFILL_SECONDS", 5.0)
DAILY_BUDGET = _env_int("DAILY_BUDGET", 3000)  # 全局每日 AI 调用上限，兜底防账单爆炸
BUDGET_WARN_RATIO = _env_float("BUDGET_WARN_RATIO", 0.8)

# ══════════════════ AI 调用 ══════════════════
AI_TIMEOUT_SECONDS = _env_float("AI_TIMEOUT_SECONDS", 20.0)   # 单个模型

# 每次逻辑调用的总物理尝试数；候选来自显式模型/备用端点，不隐式重试。
AI_MAX_ATTEMPTS = _env_int("AI_MAX_ATTEMPTS", 3)

# 网关看门狗：botpy 收不到任何下行（含心跳 ACK）超过这么久，就主动断开 ws 触发重连。
# 心跳每 30s 一来一回，正常静默不会超过一分钟；阈值取 90s = 连续 3 个 ACK 没来。
# 为什么必须有它：botpy 的重连依赖「接收循环自己退出」，TCP 半死时 `receive()` 会永远挂住
# —— 实测 2026-09-19 13:15 一次 1006 之后整整 18 分钟没有任何重连动作，全靠这个补。
WS_WATCHDOG_STALE_SECONDS = _env_float("WS_WATCHDOG_STALE_SECONDS", 90.0)
AI_TOTAL_TIMEOUT = _env_float("AI_TOTAL_TIMEOUT", 30.0)       # 整个模型梯队
# 以下是**输出**上限，思考模型的 reasoning token 也占这个额度：
# 实测 glm-5.3-flash 一句普通回复会先花 90~140 token 思考，上限太小会只拿到空正文。
AI_MAX_TOKENS = _env_int("AI_MAX_TOKENS", 768)
AI_MAX_TOKENS_BANTER = _env_int("AI_MAX_TOKENS_BANTER", 320)
JUDGE_MAX_TOKENS = _env_int("JUDGE_MAX_TOKENS", 400)   # 称呼审核：要容纳“依据 + OK/NG”
AI_TEMPERATURE = _env_float("AI_TEMPERATURE", 0.9)
# ══════════════════ 模型调用档案 ══════════════════
# 每个模型「怎么调」各不相同，错一个参数就是 HTTP 400 或一句空回复（2026-11-01 逐个实测，
# 见 docs/model-roles.md）：
#   token_param —— 输出上限的字段名（gpt-6-luna 只收 max_completion_tokens）
#   temperature —— 收不收温度（gpt-6-luna 只能用默认值，传了就 400）
#   off         —— 关掉推理的写法：回复、摘要、读图都用它（推理会先吃掉 256 的插嘴额度，
#                  hy4-preview 实测一个字没写就结束；它不认 thinking=disabled，只认 reasoning_effort）
#   on          —— 允许推理的写法：审核用（认谐音靠推理）。没登记 on 就当这个模型不推理。
#   effort      —— 推理强度的字段名（gpt-6-luna 是 reasoning_effort，认 none/low/medium/high/xhigh）。
#                  登记了它，各档就能用 *_REASONING 单独定强度（见下面 TIER_REASONING）。
# 没登记的模型照旧：max_tokens + 温度 + 供应商预设的 extra_body。网关模型名的「cn:」前缀查表时去掉。
MODEL_PROFILES = {
    "glm-5.3-flash": {"off": {"thinking": {"type": "disabled"}}},
    "deepseek-v4.1-flash": {"off": {"thinking": {"type": "disabled"}}, "on": {}},
    "hy4-preview": {"off": {"reasoning_effort": "none"}, "on": {}},
    "gpt-6-luna": {"token_param": "max_completion_tokens", "temperature": False,
                   "effort": "reasoning_effort",
                   "off": {"reasoning_effort": "none"}, "on": {}},
    "gemini-3.5-flash-lite": {"off": {}},     # 不推理；传任何推理参数都 400
}
# 允许推理时的输出上限（是上限不是目标）。推理先花额度：审核在 400 时，难的称呼约三分之一
# 一个字都没写出来就结束，而「没有结论」按设计是放行。摘要开 xhigh 时，339 条的一天
# 推理就用了 7,497 token（2026-11-01 实测），所以默认给到 16000。
REASONING_MAX_TOKENS = _env_int("REASONING_MAX_TOKENS", 16000)
# 各档推理强度（2026-11-01）：只对登记了 effort 的模型生效。空 = 照旧（审核档按 on 推理，其余关）。
# 线上：回复 low（盲评第一、2 秒左右），摘要 xhigh（不再把「维尼」这类写进记忆，后台跑、慢点无妨）。
REASONING_LEVELS = ("none", "low", "medium", "high", "xhigh")
TIER_REASONING = {}
for _tier in ("chat", "digest", "judge", "vision"):
    _value = _env_str(f"{_tier.upper()}_REASONING", "").lower()
    if _value and _value not in REASONING_LEVELS:
        CONFIG_ERRORS.append(f"{_tier.upper()}_REASONING 必须是 {' / '.join(REASONING_LEVELS)} 之一，或者留空")
        _value = ""
    TIER_REASONING[_tier] = _value
# 显式设置 AI_TEMPERATURE 时覆盖供应商预设；不设则沿用预设（智谱 0.7）。
AI_TEMPERATURE_EXPLICIT = "AI_TEMPERATURE" in os.environ

# ══════════════════ 没叫它的消息 ══════════════════
# 接不接由小王自己在回复里定（〔不接〕，见 bot.reply_unprompted）；2026-11-02 以前是
# 12% 的骰子 × 亲密度 × 作息 × 节奏先验 × 插嘴判断 × 接纳度。这里只剩一个数：
# 它没被叫就开过口之后多久之内，下一句要提醒它「你刚开过口」（不拦，交代给它）。
BANTER_COOLDOWN_SECONDS = _env_float("BANTER_COOLDOWN_SECONDS", 120.0)
# 没叫它的一句先等这么久再定接不接：同一个人连着发的几句只拿最后一句来问
# （2026-11-04「你发的啥」「截图看看」两秒内各回了一句，见 bot.reply_unprompted）。
BANTER_BURST_SECONDS = _env_float("BANTER_BURST_SECONDS", 3.0)

# ── 主动性（2026-10-19，激活度系统的一部分，见 activation.py）──
# 心情/作息/群温的闸门全过时，自己照着最近的话题开个话头。所有闸门见 activation.proactive_ok：
# 4 小时间隔 / 群静默 10min~6h / 只在白天 / 心情不差。巡检每 30 分钟一次，纯检查零成本。
PROACTIVE_CHECK_SECONDS = _env_float("PROACTIVE_CHECK_SECONDS", 1800.0)

# ── 读图：默认复用主聊天模型，可为纯文本主模型单独指定视觉模型/供应商 ──
# 配置继承不代表模型具备图片能力；不支持图片时单配 VISION_MODEL，或关闭读图。
VISION_ENABLED = _env_bool("VISION_ENABLED", True)
VISION_PROVIDER = _env_str("VISION_PROVIDER", AI_PROVIDER).lower()
if VISION_PROVIDER not in PROVIDER_PRESETS:
    CONFIG_ERRORS.append("VISION_PROVIDER 必须是 openai、zhipu 或 gemini")
    VISION_PROVIDER = AI_PROVIDER
_vision_model = _env_str("VISION_MODEL")
VISION_MODEL_INHERITED = not _vision_model and VISION_PROVIDER == AI_PROVIDER
VISION_MODEL = _vision_model or (CANDIDATE_MODELS[0] if VISION_MODEL_INHERITED and CANDIDATE_MODELS else "")
VISION_INPUT_MODE = _env_str("VISION_INPUT_MODE", "url").lower()
if VISION_INPUT_MODE not in ("url", "download"):
    CONFIG_ERRORS.append("VISION_INPUT_MODE 必须是 url 或 download")
VISION_COOLDOWN_SECONDS = _env_float("VISION_COOLDOWN_SECONDS", 60.0)
VISION_DAILY_CAP = _env_int("VISION_DAILY_CAP", 30)
# 回话前最多等背景里别人那几张没读完的图多久（bot.await_images）。线上从开始下载到描述回来
# 2–10 秒，原先借用 ASYNC_REPLY_SECONDS 的 6 秒，2026-11-02 21:04 差 4 秒，答了「看不到」。
# 问话人自己发的图不受它限：线上等 ASYNC_REPLY_SECONDS 还没读完，先回一句在看图，读完再答。
VISION_SETTLE_SECONDS = _env_float("VISION_SETTLE_SECONDS", 15.0)
# 读图输出上限同样包含思考 token；25 字描述 + 思考需要 300 左右，80 会整轮空正文。
VISION_MAX_TOKENS = _env_int("VISION_MAX_TOKENS", 300)

# 入站语音转写仅使用已有的官方 OpenAI 配置；预算与普通调用共用。
VOICE_TRANSCRIPTION_MODEL = _env_str("VOICE_TRANSCRIPTION_MODEL", "gpt-transcribe")

# 出站语音默认关闭；路径须指向可信且预先安装的本机依赖。
# 什么时候发由小王自己在回复里写〔语音〕（见 expression.py），这里只管开关和日额。
VOICE_OUTPUT_ENABLED = _env_bool("VOICE_OUTPUT_ENABLED", False)
VOICE_OUTPUT_DAILY_LIMIT = min(100, max(0, _env_int("VOICE_OUTPUT_DAILY_LIMIT", 10)))
# Private instance-owned codec installation beside DATA_DIR; no NODE_PATH lookup.
VOICE_CODEC_DIR = _env_str("VOICE_CODEC_DIR", str(Path(DATA_DIR).parent / ".voice-codecs"))
VOICE_OUTPUT_NODE = _env_str("VOICE_OUTPUT_NODE", "/opt/homebrew/bin/node")
VOICE_OUTPUT_SILK_ENTRY = _env_str("VOICE_OUTPUT_SILK_ENTRY", str(
    Path(VOICE_CODEC_DIR) / "node_modules" / "silk-wasm" / "lib" / "index.cjs"))

# 生图单独开关与额度；缺少凭据时自动不可用。
IMAGE_GENERATION_ENABLED = _env_bool("IMAGE_GENERATION_ENABLED", True)
IMAGE_GENERATION_BASE_URL = _env_str("IMAGE_GENERATION_BASE_URL", PROVIDER_PRESETS["openai"]["base_url"] or "https://api.openai.com/v1")
IMAGE_GENERATION_API_KEY = _env_str("IMAGE_GENERATION_API_KEY")
IMAGE_GENERATION_MODEL = _env_str("IMAGE_GENERATION_MODEL", "gpt-image-2.6-flare")
IMAGE_GENERATION_QUALITY = _env_str("IMAGE_GENERATION_QUALITY", "medium")
IMAGE_GENERATION_SIZE = _env_str("IMAGE_GENERATION_SIZE", "1024x1024")
# 空串表示不传此参数，兼容不支持 complexity 的旧模型或网关。
IMAGE_GENERATION_COMPLEXITY = os.environ.get("IMAGE_GENERATION_COMPLEXITY", "medium").strip()
IMAGE_GENERATION_GIF_ENABLED = _env_bool("IMAGE_GENERATION_GIF_ENABLED", True)
IMAGE_GENERATION_GIF_SIZE = _env_str("IMAGE_GENERATION_GIF_SIZE", "512x512")
IMAGE_GENERATION_GIF_FRAME_COUNT = _env_int("IMAGE_GENERATION_GIF_FRAME_COUNT", 9)
IMAGE_GENERATION_GIF_FRAME_DURATION_MS = _env_int("IMAGE_GENERATION_GIF_FRAME_DURATION_MS", 200)
IMAGE_GENERATION_DAILY_CAP = _env_int("IMAGE_GENERATION_DAILY_CAP", 40)
IMAGE_USER_DAILY_CAP = _env_int("IMAGE_USER_DAILY_CAP", 5)
IMAGE_USER_EXTRA_ACQUAINTANCE = _env_int("IMAGE_USER_EXTRA_ACQUAINTANCE", 1)
IMAGE_USER_EXTRA_FRIEND = _env_int("IMAGE_USER_EXTRA_FRIEND", 2)
IMAGE_USER_EXTRA_BUDDY = _env_int("IMAGE_USER_EXTRA_BUDDY", 4)
IMAGE_USER_EXTRA_SOULMATE = _env_int("IMAGE_USER_EXTRA_SOULMATE", 8)
IMAGE_GENERATION_TIMEOUT_SECONDS = _env_float("IMAGE_GENERATION_TIMEOUT_SECONDS", 150.0)

# ══════════════════ 群友关系档案（见 relations.py）══════════════════
AFFINITY_ENABLED = _env_bool("AFFINITY_ENABLED", True)
# 每轮是否让模型在回复末尾吐一行 <CMD>{"aff":n}</CMD>。
#
# 2026-10-06 改过语义：以前是让它给 -3~+3 的**自由数字**，实测很虚（普通聊天也给 +3），
# 而且没有任何东西能拦住它。现在只让它判**方向**（-1/0/1）——「这句对我是什么态度」是它的
# 强项，绝对幅度交给代码：命中本地词表就扣，方向对上就加，最后还要过每日封顶。
# 默认开着；关掉后每轮只剩本地词表，整天的印象仍由每日压缩统一评估。
CMD_PROTOCOL_ENABLED = _env_bool("CMD_PROTOCOL_ENABLED", True)
# 每日加分/扣分封顶（对称）。为什么需要：模型偶尔会判错，而好感度是**长期账本** ——
# 没有封顶的话，一次判错就可能把「铁哥们」直接扣成「生疏」，且要靠很多天才能涨回来。
AFFINITY_DAILY_GAIN_CAP = _env_int("AFFINITY_DAILY_GAIN_CAP", 10)
AFFINITY_DAILY_LOSS_CAP = _env_int("AFFINITY_DAILY_LOSS_CAP", 5)
# 压缩时一次性结算一整天的印象变化，幅度上限比单次互动（±3）放宽些
AFFINITY_DIGEST_SPAN = _env_int("AFFINITY_DIGEST_SPAN", 6)
AFFINITY_DECAY_GRACE_DAYS = _env_float("AFFINITY_DECAY_GRACE_DAYS", 3.0)  # 这几天不来往不扣
AFFINITY_DECAY_STEP_DAYS = _env_float("AFFINITY_DECAY_STEP_DAYS", 2.0)    # 之后每 N 天向 0 收敛一次
AFFINITY_DECAY_AMOUNT = _env_int("AFFINITY_DECAY_AMOUNT", 1)
AFFINITY_PRUNE_IDLE_DAYS = _env_float("AFFINITY_PRUNE_IDLE_DAYS", 90.0)   # 长期不互动就清掉档案
# ══════════════════ 群聊长期记忆（每日滚动摘要，见 digest.py）══════════════════
DIGEST_ENABLED = _env_bool("DIGEST_ENABLED", True)
DIGEST_INTERVAL_HOURS = _env_float("DIGEST_INTERVAL_HOURS", 24.0)  # 多久压一次
DIGEST_MIN_PENDING = _env_int("DIGEST_MIN_PENDING", 15)            # 消息太少不值得压
# 攒够这么多条就压一次，不必干等 24 小时 —— 活跃的群当天就能刷新长期记忆与好感度。
# DIGEST_INTERVAL_HOURS 退化成兜底：冷清的群也至少一天压一次。
DIGEST_COUNT_TRIGGER = _env_int("DIGEST_COUNT_TRIGGER", 120)
DIGEST_CHUNK_CHARS = _env_int("DIGEST_CHUNK_CHARS", 6000)          # 单块字符上限
DIGEST_MAX_BLOCKS = _env_int("DIGEST_MAX_BLOCKS", 10)              # 一次最多分几块
DIGEST_MAX_PENDING = _env_int("DIGEST_MAX_PENDING", 2000)          # 单次最多喂给模型多少条
DIGEST_CHECK_INTERVAL = _env_float("DIGEST_CHECK_INTERVAL", 1800.0)  # 后台检查间隔（秒）
DIGEST_MAX_TOKENS = _env_int("DIGEST_MAX_TOKENS", 1200)   # 摘要正文 + <DIGEST> JSON + 思考 token
# 摘要在后台跑，单次请求的时限单独给：开深推理时一次要一分多钟（xhigh 实测 68 秒）。
DIGEST_TIMEOUT_SECONDS = _env_float("DIGEST_TIMEOUT_SECONDS", 240.0)
# 个人原话的时效按**原始发言时间**算，不是压缩时间：换个措辞复述、重复压缩、
# 重读同一条消息都不会让旧材料变新（见 digest.stamp_people / render_people）。
PEOPLE_NOTE_MAX_AGE_DAYS = _env_float("PEOPLE_NOTE_MAX_AGE_DAYS", 2.0)
# 个人原话注入开关：关掉只去掉「本轮动态层」里的个人材料，称呼、游戏关系、
# 摘要与归档照常 —— 用于和「普通接话」做干净对照，不改任何收集行为。
PERSONAL_MEMORY_ENABLED = _env_bool("PERSONAL_MEMORY_ENABLED", True)
# 「名场面/梗」同样有时效（见 digest.stamp_memes / render_summary）。
# 原先只喂列表里第一条、且不限时，于是排到首位的那条梗天天在模型眼前 ——
# 收录进去的偏偏是「你死了十二天」这种针对它自己的吐槽。
MEME_NOTE_MAX_AGE_DAYS = _env_float("MEME_NOTE_MAX_AGE_DAYS", 3.0)
# 按需旧账召回（见 recall.py）：只有明确问起往事时才查本地归档，0 token。
# 条数上限故意很小——它是「核对事实」的补丁，不是把旧事重新灌回上下文。
MEMORY_RECALL_ENABLED = _env_bool("MEMORY_RECALL_ENABLED", True)
MEMORY_RECALL_MAX_ITEMS = _env_int("MEMORY_RECALL_MAX_ITEMS", 2)
MEMORY_RECALL_LOOKBACK_DAYS = _env_int("MEMORY_RECALL_LOOKBACK_DAYS", 30)
# 表达学习（压缩时顺带提取，不额外调用模型）：只保留本批至少出现 N 次的口头禅。
STYLE_NOTE_MAX_ITEMS = _env_int("STYLE_NOTE_MAX_ITEMS", 5)
STYLE_MIN_REPEATS = _env_int("STYLE_MIN_REPEATS", 2)
# 群聊背景（【群聊最近背景】）最多回看多久。buffer 会落盘、跨重启保留，
# 不用时间过滤的话，安静半天后的第一条消息配到的还是半天前的话。
GROUP_BUFFER_TTL_SECONDS = _env_float("GROUP_BUFFER_TTL_SECONDS", 1800.0)
# QQ 官方 API 的 HTTP 超时（秒）。botpy 默认 5s，晚间高峰实测会超 —— 而且 botpy 的
# http 层把 TimeoutError 吞掉不抛（只打警告返回 None），超时=这条回复凭空消失。
# 放宽到 10s 减少发生频率；真发生了由 safe_reply 的「None 检测 + 同 seq 重试」兜底。
QQ_API_TIMEOUT = _env_float("QQ_API_TIMEOUT", 10.0)
# 摘要产出后顺手写一份 MD，人直接看这个；原始消息另存 JSONL 备查
DIGEST_WRITE_MD = _env_bool("DIGEST_WRITE_MD", True)

# ══════════════════ 原始消息归档（见 archive.py）═════════════════
# 摘要是有损压缩，出错时得有地方回查，所以原文单独按天留存。
ARCHIVE_DIR = data_path("ARCHIVE_DIR", "archive")
MEMORY_DIR = data_path("MEMORY_DIR", "memory")
# 可编辑知识库：all.md 全群共享，<group_id>.md 只管一个群；维护者直接改文件。
KNOWLEDGE_ENABLED = _env_bool("KNOWLEDGE_ENABLED", True)
KNOWLEDGE_DIR = data_path("KNOWLEDGE_DIR", "knowledge")
KNOWLEDGE_MAX_ITEMS = _env_int("KNOWLEDGE_MAX_ITEMS", 2)
KNOWLEDGE_MAX_CHARS = _env_int("KNOWLEDGE_MAX_CHARS", 360)

# 联网搜索（见 search.py）。配了 RESEARCH_PROVIDER 就走它的联网查询（OpenAI Responses 的
# web_search），并且分诊判断说「这句得先查」时自动查；没配就只有「搜索 关键词」命令查 DuckDuckGo。
SEARCH_ENABLED = _env_bool("SEARCH_ENABLED", True)
SEARCH_MAX_RESULTS = _env_int("SEARCH_MAX_RESULTS", 3)
SEARCH_TIMEOUT_SECONDS = _env_float("SEARCH_TIMEOUT_SECONDS", 8.0)
# 冷却只管「搜索」命令；判断触发的自动查询不受冷却，只算进每日上限 ——
# 有人连着问几个要查的问题时，第二个不该因为冷却变成瞎答。
SEARCH_COOLDOWN_SECONDS = _env_float("SEARCH_COOLDOWN_SECONDS", 60.0)
SEARCH_DAILY_CAP = _env_int("SEARCH_DAILY_CAP", 60)
SEARCH_USER_DAILY_CAP = _env_int("SEARCH_USER_DAILY_CAP", 20)
SEARCH_USER_EXTRA_ACQUAINTANCE = _env_int("SEARCH_USER_EXTRA_ACQUAINTANCE", 5)
SEARCH_USER_EXTRA_FRIEND = _env_int("SEARCH_USER_EXTRA_FRIEND", 10)
SEARCH_USER_EXTRA_BUDDY = _env_int("SEARCH_USER_EXTRA_BUDDY", 20)
SEARCH_USER_EXTRA_SOULMATE = _env_int("SEARCH_USER_EXTRA_SOULMATE", 30)

# 联网查询 / 深度研究（2026-11-01，见 slowwork.py）：OpenAI Responses 后台任务 + web_search。
# 空 = 不开。RESEARCH_MODEL 空就用这家的第一个对话模型。
RESEARCH_PROVIDER = _env_str("RESEARCH_PROVIDER", "").lower()
if RESEARCH_PROVIDER and RESEARCH_PROVIDER not in PROVIDER_PRESETS:
    CONFIG_ERRORS.append("RESEARCH_PROVIDER 必须是 openai、zhipu 或 gemini，或者留空")
    RESEARCH_PROVIDER = ""
RESEARCH_MODEL = _env_str("RESEARCH_MODEL", "") or (
    (PROVIDER_PRESETS[RESEARCH_PROVIDER]["models"] or [""])[0] if RESEARCH_PROVIDER else "")
RESEARCH_TIMEOUT_SECONDS = _env_float("RESEARCH_TIMEOUT_SECONDS", 15.0)   # 发起 / 轮询单次 HTTP
# 「人能等的那几秒」：查询在这之内回来就一条回复；回不来先说一句在查，后台做完再回。
ASYNC_REPLY_SECONDS = _env_float("ASYNC_REPLY_SECONDS", 6.0)
# 深度研究是隐藏彩蛋：有人明确要它认真查、整理、调研时才做，几十秒到几分钟，后台做完再回。
DEEP_RESEARCH_ENABLED = _env_bool("DEEP_RESEARCH_ENABLED", True)
DEEP_RESEARCH_DAILY_CAP = _env_int("DEEP_RESEARCH_DAILY_CAP", 3)
DEEP_RESEARCH_COOLDOWN_SECONDS = _env_float("DEEP_RESEARCH_COOLDOWN_SECONDS", 600.0)
# 搜索默认直连，刻意不继承部署环境里的 HTTP(S)_PROXY：模型网关的代理未必
# 转发公网 HTTPS，实测会让 DuckDuckGo 直接连不上。需要代理时显式填写。
SEARCH_PROXY = _env_str("SEARCH_PROXY")
SEARCH_USE_ENV_PROXY = _env_bool("SEARCH_USE_ENV_PROXY", False)
ARCHIVE_KEEP_DAYS = _env_int("ARCHIVE_KEEP_DAYS", 90)   # 原始消息保留天数，0 = 永不清理
ARCHIVE_MAX_TEXT = _env_int("ARCHIVE_MAX_TEXT", 500)    # 单条消息存多少字

# ══════════════════ 交互观察日志（见 interactions.py）═════════════════
# 只进不出的本地观察：收到的消息、门控决策、实际送达与显式反馈按天 JSONL。
# 它不进提示词/摘要/召回，也不写进原始消息归档 —— 是行为研究底稿，不是模型材料。
INTERACTION_LOG_ENABLED = _env_bool("INTERACTION_LOG_ENABLED", True)
INTERACTION_LOG_DIR = data_path("INTERACTION_LOG_DIR", "interactions")
# 观察口径版本号：随每条 delivery 落盘，供离线分析区分记录口径，不影响行为。
INTERACTION_POLICY_ID = _env_str("INTERACTION_POLICY_ID", "regular-observe-v1")

# ══════════════════ 自己迭代（见 docs/self-iteration.md）═════════════════
# 每轮模型输入原样记一份（turns.py），回放不用再拼现场。私聊不记；只进不出。
TURN_LOG_ENABLED = _env_bool("TURN_LOG_ENABLED", True)
TURN_LOG_DIR = data_path("TURN_LOG_DIR", "turns")
TURN_LOG_KEEP_DAYS = _env_int("TURN_LOG_KEEP_DAYS", 30)   # 0 = 永不清理
# 体检报告、用例库、评测报告：都带真实群聊，跟归档一个待遇，不进代码库
CHECKUP_DIR = data_path("CHECKUP_DIR", "checkups")
CASES_DIR = data_path("CASES_DIR", "cases")
EVAL_DIR = data_path("EVAL_DIR", "evals")
# 体检信号与旧账检索的策略文件（docs/self-iteration.md「体检与检索策略的自迭代」）：
# `regular tune` 离线搜出来、维护者 `regular policy apply` 才写进来；没有文件就用代码里的默认值。
POLICY_DIR = data_path("POLICY_DIR", "policies")
CHECKUP_POLICY_FILE = str(Path(POLICY_DIR) / "checkup.json")
RECALL_POLICY_FILE = str(Path(POLICY_DIR) / "recall.json")
# 盲评：不参赛的评审模型。kimi-k3.1-pro 只在本机网关上有（模型名带 cn: 前缀），
# 网关是 ZHIPU_BASE_URL 指向的 127.0.0.1:17863，所以走 zhipu 这一家的地址和凭据。
# 2026-11-04 实测：发到 api.openai.com 的 kimi-k3.1-pro 一律超时，盲评整批被剔掉。
EVAL_JUDGE_PROVIDER = _env_str("EVAL_JUDGE_PROVIDER", "zhipu")
EVAL_JUDGE_MODEL = _env_str("EVAL_JUDGE_MODEL", "cn:kimi-k3.1-pro")
EVAL_CONCURRENCY = _env_int("EVAL_CONCURRENCY", 6)

# ══════════════════ 判断 API（原生 GPT Decisions，见 providers.call_decision）═════════════════
# 独立配置：端点/模型/密钥在 data/decision_api.json（面板「判断 API」页保存），
# 不是 chat/completions，不改动会话模型梯队。关掉则原生请求一律不发出。
DECISION_ENABLED = _env_bool("DECISION_ENABLED", True)
# 单次原生请求的总时限（秒）；一次调用只发一次，不自动重试。
DECISION_TIMEOUT_SECONDS = _env_float("DECISION_TIMEOUT_SECONDS", 6.0)
# 判断请求自己的每日次数上限，和聊天的 DAILY_BUDGET 分开记：判断便宜（按输入计费、输出免费），
# 又是每条群消息都问 —— 共用一份的话，刷屏就能把当天的回复额度烧光。
DECISION_DAILY_BUDGET = _env_int("DECISION_DAILY_BUDGET", 5000)
# 反馈评估只覆盖最近一条已送达模型回复（秒）；「收敛」后 unsolicited 暂停时长（秒）。
DECISION_FEEDBACK_WINDOW_SECONDS = _env_float("DECISION_FEEDBACK_WINDOW_SECONDS", 120.0)
DECISION_QUIET_SECONDS = _env_float("DECISION_QUIET_SECONDS", 180.0)
# 弃答地板：被选中那项比第二名高出不到这么多，就当这一题这次不可用，退回基线。
# 用**边际**而不是绝对概率，因为概率的刻度不能用（2026-10-31 实测 ECE 0.22、
# Platt 斜率 0.21）；边际是无标度的。实测弃掉边际 <0.3 的那 41% 样本，
# 它们挑对率 0.512（掷硬币），留下的从 0.575 升到 0.619；但阈值再往上反而变差
# （0.70 时 0.559，低于不弃答）—— 所以这是**地板**，不是越高越好。
# 设 0 关闭弃答闸（恢复「只取 argmax」的旧行为）。见 docs/decision-calibration.md。
DECISION_MIN_MARGIN = _env_float("DECISION_MIN_MARGIN", 0.25)
# 原生判过「值得长期留」的个人原话保鲜天数；未裁决/临时条目照旧 2 天。
PEOPLE_DURABLE_MAX_AGE_DAYS = _env_float("PEOPLE_DURABLE_MAX_AGE_DAYS", 90.0)
PEOPLE_MEMORY_MAX_ITEMS = _env_int("PEOPLE_MEMORY_MAX_ITEMS", 24)

# ══════════════════ 插嘴接纳度（receptive.py）═════════════════
# 把自然反馈（被嫌烦 / 被接着逗）慢慢折成每人一个估计。有把握地读出「这人不爱被
# 没叫就搭话」时，没叫它的那一轮补一句交代（prompts.PROMPT_BANTER_UNWELCOME）；
# 证据不足时什么都不说，不是「先当他讨厌机器人」。
RECEPTIVE_ENABLED = _env_bool("RECEPTIVE_ENABLED", True)
RECEPTIVE_PRIOR_WEIGHT = _env_float("RECEPTIVE_PRIOR_WEIGHT", 4.0)     # 向群体层收缩的伪计数
RECEPTIVE_HALF_LIFE_DAYS = _env_float("RECEPTIVE_HALF_LIFE_DAYS", 14.0)  # 证据半衰期，不留永久标签
RECEPTIVE_MIN_EVIDENCE = _env_float("RECEPTIVE_MIN_EVIDENCE", 4.0)     # 进入检验的最少等效证据条数
RECEPTIVE_ALPHA = _env_float("RECEPTIVE_ALPHA", 0.05)                 # 声明门槛：全群至少错认一人的概率上限
RECEPTIVE_PERMUTATIONS = _env_int("RECEPTIVE_PERMUTATIONS", 200)      # 跨成员打乱标签的次数
RECEPTIVE_GAIN = _env_float("RECEPTIVE_GAIN", 1.5)
RECEPTIVE_MULTIPLIER_MIN = _env_float("RECEPTIVE_MULTIPLIER_MIN", 0.5)
RECEPTIVE_MULTIPLIER_MAX = _env_float("RECEPTIVE_MULTIPLIER_MAX", 1.5)

# 「居然记得」：没叫它的那一轮里端出一句本人旧话，由原生判断把关是否此刻合适。
# 要稀才稀奇 —— 每人冷却 + 每群每天上限；判断不可用时一律不端出。
DECISION_CALLBACK_ENABLED = _env_bool("DECISION_CALLBACK_ENABLED", True)
DECISION_CALLBACK_COOLDOWN_SECONDS = _env_float("DECISION_CALLBACK_COOLDOWN_SECONDS", 21600.0)
DECISION_CALLBACK_MAX_PER_DAY = _env_int("DECISION_CALLBACK_MAX_PER_DAY", 2)

# ══════════════════ 承诺催债（见 digest.PromiseBook）═════════════════
PROMISE_ENABLED = _env_bool("PROMISE_ENABLED", True)
PROMISE_GRACE_HOURS = _env_float("PROMISE_GRACE_HOURS", 24.0)      # 立下之后多久才允许催
PROMISE_NAG_INTERVAL_HOURS = _env_float("PROMISE_NAG_INTERVAL_HOURS", 48.0)  # 两次催债间隔
PROMISE_MAX_NAG = _env_int("PROMISE_MAX_NAG", 2)                   # 催满几次就撤下，别变骚扰
PROMISE_MAX_ITEMS = _env_int("PROMISE_MAX_ITEMS", 12)              # 每群最多记几条
# 同一个群两次催债至少隔多久（秒）。原来是每小时一圈的检查间隔；改成定时任务后
# 每条账有自己的闹钟，几条同时到期时靠它错开，别一口气全催出来。
PROMISE_CHECK_INTERVAL = _env_float("PROMISE_CHECK_INTERVAL", 3600.0)
# 群友让小王到点提醒（reminders.py，见 docs/reminders.md）：一人一条，小王在回复里写〔提醒 …〕才算记上。
REMINDER_ENABLED = _env_bool("REMINDER_ENABLED", True)
# 定时任务（scheduler.py）多久看一次到点的（秒）。
SCHEDULE_TICK_SECONDS = _env_float("SCHEDULE_TICK_SECONDS", 60.0)
# 这些小时段内不主动开口（本地时间），免得半夜把群吵醒
def _quiet_hours():
    try:
        hours = {int(h.strip()) for h in _env_str("PROMISE_QUIET_HOURS", "23,0,1,2,3,4,5,6,7,8").split(",") if h.strip()}
        if not hours.issubset(range(24)):
            raise ValueError
        return hours
    except ValueError:
        CONFIG_ERRORS.append("PROMISE_QUIET_HOURS 必须是逗号分隔的 0~23 小时")
        return set()


PROMISE_QUIET_HOURS = _quiet_hours()

# ══════════════════ 其它 ══════════════════
DEDUP_MAX = _env_int("DEDUP_MAX", 500)   # 消息去重 ID 队列长度
RECONNECT_MIN_DELAY = _env_float("RECONNECT_MIN_DELAY", 5.0)
RECONNECT_MAX_DELAY = _env_float("RECONNECT_MAX_DELAY", 300.0)
RESET_BACKOFF_AFTER = _env_float("RESET_BACKOFF_AFTER", 120.0)  # 稳定运行这么久就重置退避计数


def validate():
    """启动前校验，缺东西就 fail fast。"""
    errors = list(CONFIG_ERRORS)
    if not 0 <= BUDGET_WARN_RATIO <= 1:
        errors.append("BUDGET_WARN_RATIO 必须在 0~1 之间")
    for name in ("DAILY_BUDGET", "VISION_DAILY_CAP", "DECISION_DAILY_BUDGET", "IMAGE_GENERATION_DAILY_CAP",
                 "IMAGE_USER_DAILY_CAP", "IMAGE_USER_EXTRA_ACQUAINTANCE", "IMAGE_USER_EXTRA_FRIEND",
                 "IMAGE_USER_EXTRA_BUDDY", "IMAGE_USER_EXTRA_SOULMATE", "SEARCH_USER_DAILY_CAP",
                 "SEARCH_USER_EXTRA_ACQUAINTANCE", "SEARCH_USER_EXTRA_FRIEND", "SEARCH_USER_EXTRA_BUDDY",
                 "SEARCH_USER_EXTRA_SOULMATE"):
        if globals()[name] < 0:
            errors.append(f"{name} 不能为负数")
    for prefix in ("IMAGE", "SEARCH"):
        extras = [globals()[f"{prefix}_USER_EXTRA_{tier}"]
                  for tier in ("ACQUAINTANCE", "FRIEND", "BUDDY", "SOULMATE")]
        if extras != sorted(extras):
            errors.append(f"{prefix}_USER_EXTRA 各档额度必须递增或相等")
    if not 0 < RECEPTIVE_MULTIPLIER_MIN <= 1 <= RECEPTIVE_MULTIPLIER_MAX:
        errors.append("RECEPTIVE_MULTIPLIER_MIN/MAX 必须满足 0 < MIN <= 1 <= MAX")
    if RECEPTIVE_PRIOR_WEIGHT <= 0 or RECEPTIVE_HALF_LIFE_DAYS <= 0:
        errors.append("RECEPTIVE_PRIOR_WEIGHT 与 RECEPTIVE_HALF_LIFE_DAYS 必须为正数")
    if not 0 < RECEPTIVE_ALPHA < 1:
        errors.append("RECEPTIVE_ALPHA 必须在 0~1 之间（不含端点）")
    elif RECEPTIVE_PERMUTATIONS + 1 < 1 / RECEPTIVE_ALPHA:
        # 置换 p 值最小是 1/(B+1)：B 太小时门槛永远过不去，个人层被悄悄关掉
        errors.append("RECEPTIVE_PERMUTATIONS 太少：至少要 %d 次，p 值才可能低于 RECEPTIVE_ALPHA"
                      % math.ceil(1 / RECEPTIVE_ALPHA - 1))
    if not 0 <= DECISION_MIN_MARGIN <= 1:
        errors.append("DECISION_MIN_MARGIN 必须在 0~1 之间")
    if not QQ_APP_ID:
        errors.append("缺少 QQ_APP_ID（在 .env 里配置）")
    if not QQ_APP_SECRET:
        errors.append("缺少 QQ_APP_SECRET（在 .env 里配置）")
    if VISION_ENABLED and not VISION_MODEL:
        if VISION_PROVIDER != AI_PROVIDER:
            errors.append("VISION_PROVIDER 与 AI_PROVIDER 不同；请显式设置该供应商的 VISION_MODEL")
        else:
            errors.append("读图无法继承主聊天模型；请配置主供应商的 MODELS 或单独设置 VISION_MODEL")
    if AI_MAX_ATTEMPTS < 1:
        errors.append("AI_MAX_ATTEMPTS 必须至少为 1")
    if AI_TIMEOUT_SECONDS <= 0 or AI_TOTAL_TIMEOUT <= 0:
        errors.append("AI_TIMEOUT_SECONDS 和 AI_TOTAL_TIMEOUT 必须大于 0")
    if not math.isfinite(IMAGE_GENERATION_TIMEOUT_SECONDS) or IMAGE_GENERATION_TIMEOUT_SECONDS <= 0:
        errors.append("IMAGE_GENERATION_TIMEOUT_SECONDS 必须大于 0")
    if IMAGE_GENERATION_COMPLEXITY not in ("", "low", "medium", "high"):
        errors.append("IMAGE_GENERATION_COMPLEXITY 必须是 low、medium、high 或空串")
    if not 2 <= IMAGE_GENERATION_GIF_FRAME_COUNT <= 64:
        errors.append("IMAGE_GENERATION_GIF_FRAME_COUNT 必须在 2~64 之间")
    if (not 20 <= IMAGE_GENERATION_GIF_FRAME_DURATION_MS <= 2000
            or IMAGE_GENERATION_GIF_FRAME_DURATION_MS % 10):
        errors.append("IMAGE_GENERATION_GIF_FRAME_DURATION_MS 必须在 20~2000 之间且是 10 的倍数")
    if IMAGE_GENERATION_GIF_SIZE != "auto":
        parts = IMAGE_GENERATION_GIF_SIZE.split("x")
        if (len(parts) != 2 or not all(part.isascii() and part.isdecimal()
                                      and int(part) > 0 and int(part) % 16 == 0 for part in parts)):
            errors.append("IMAGE_GENERATION_GIF_SIZE 必须是 auto 或两边均为正的 16 倍数，如 512x512")
    for provider_name in dict.fromkeys((AI_PROVIDER, *TIER_PROVIDERS.values(),
                                        FALLBACK_PROVIDER, RESEARCH_PROVIDER)):
        if not provider_name:
            continue
        preset = PROVIDER_PRESETS[provider_name]
        if not preset.get("base_url"):
            errors.append(f"缺少 {provider_name.upper()}_BASE_URL")
        if not preset["models"]:
            errors.append(f"缺少 {provider_name.upper()}_MODELS")
        if provider_name != AI_PROVIDER and not load_api_keys(provider_name):
            errors.append(f"缺少 {preset['label']} 的 API Key（在 .env 或 "
                          f"api_keys_{provider_name}.txt 里配置）")
    if not AI_KEYS:
        errors.append(f"缺少 {PROVIDER['label']} 的 API Key（在 .env 或 api_keys_{AI_PROVIDER}.txt 里配置）")
    return errors
