"""一次生图与 QQ 群图片投递；生成、上传、发送均不自动重试。"""
import asyncio
import base64
import binascii
import logging
from urllib.parse import urlsplit

import httpx
from botpy.http import Route
from openai import AsyncOpenAI, BadRequestError

import config
import delivery
import quotes
import runtime
from regular import capabilities, decision_settings

logger = logging.getLogger("qqbot")
_lock = asyncio.Lock()

# API 响应里的自由文本可能包含群聊内容；只允许已知的机器错误码进入日志。
_SAFE_ERROR_CODES = frozenset({
    "moderation_blocked", "content_policy_violation", "invalid_value",
    "invalid_image_size", "invalid_prompt", "unsupported_value",
    "model_not_found", "billing_hard_limit_reached",
})
_BLOCKED_CODES = frozenset({"moderation_blocked", "content_policy_violation"})
# QQ file_data 直传要求小于 5M；按十进制上限保守处理。
_QQ_FILE_DATA_LIMIT = 5_000_000


def _log_generation_error(exc):
    code = getattr(exc, "code", None)
    code = code if isinstance(code, str) and code in _SAFE_ERROR_CODES else "other"
    status = getattr(exc, "status_code", None)
    if not isinstance(status, int) or isinstance(status, bool) or not 400 <= status <= 599:
        status = None
    logger.warning("生图请求失败: type=%s status=%s code=%s",
                   type(exc).__name__, status, code)
    return code


def _key():
    keys = config.load_api_keys("openai")
    if config.IMAGE_GENERATION_API_KEY:
        return config.IMAGE_GENERATION_API_KEY
    source = config.PROVIDER_PRESETS["openai"].get("base_url", "")
    if keys and capabilities.same_endpoint(config.IMAGE_GENERATION_BASE_URL, source):
        return keys[0]
    # 密钥只能在原本发给官方 OpenAI 且生图目标也是官方时复用。
    settings = decision_settings.load_settings(config.DATA_DIR)
    if settings and _official(config.IMAGE_GENERATION_BASE_URL) and _official(settings["endpoint"]):
        return settings["api_key"]
    return ""


def _official(url):
    try:
        parts = urlsplit(url)
        return (parts.scheme == "https" and parts.hostname == "api.openai.com"
                and parts.port in (None, 443) and not parts.username and not parts.password)
    except ValueError:
        return False


def available():
    """生图已启用且配置、凭据就绪。额度和在途状态在请求时检查。"""
    return bool(capabilities.enabled(config, "image") and config.IMAGE_GENERATION_BASE_URL
                and config.IMAGE_GENERATION_MODEL and _key())


def animation_available():
    """动图使用同一个 Images API 与额度，由部署配置声明支持能力。"""
    return bool(capabilities.enabled(config, "gif") and available())


def precheck(group_id, user_id, score):
    """生成回复之前看一眼：normal / exception / extra 能画，tired / busy / off 不能。

    只读不预约 —— 真正的预约在 generate_and_send 里，和这里之间可能被别人抢先，那边会再判一次。
    exception 是「今天的基础份刚用完、这张开始用好感给的额外份」：小王可以说一句破例。
    """
    if not available():
        return "off"
    if _lock.locked():
        return "busy"
    state = runtime.RESOURCE_QUOTAS.check("image", group_id, user_id, score)
    if state == "unavailable":
        return "off"
    if state == "exhausted" or runtime.IMAGE_GENERATION_BUDGET.remaining <= 0:
        return "tired"
    return state


def _expired(message):
    age = delivery.message_age_seconds(message)
    return age is not None and age >= delivery.passive_reply_window(message) - 5


async def _generate(prompt, key, *, animated=False):
    # 独立实例不会继承聊天客户端的重试；一轮只有一次付费生成请求。
    extra = {}
    if config.IMAGE_GENERATION_COMPLEXITY:
        extra["complexity"] = config.IMAGE_GENERATION_COMPLEXITY
    if animated:
        extra.update(output_format="gif",
                     frame_count=config.IMAGE_GENERATION_GIF_FRAME_COUNT,
                     frame_duration_ms=config.IMAGE_GENERATION_GIF_FRAME_DURATION_MS)
    async with httpx.AsyncClient(verify=config.SSL_VERIFY,
                                 mounts={"all://127.0.0.1": None,
                                         "all://localhost": None}) as http:
        client = AsyncOpenAI(api_key=key, base_url=config.IMAGE_GENERATION_BASE_URL,
                             http_client=http, max_retries=0)
        result = await client.images.generate(
            model=config.IMAGE_GENERATION_MODEL, prompt=prompt,
            quality=config.IMAGE_GENERATION_QUALITY,
            size=config.IMAGE_GENERATION_GIF_SIZE if animated else config.IMAGE_GENERATION_SIZE,
            n=1, timeout=config.IMAGE_GENERATION_TIMEOUT_SECONDS,
            # 兼容本仓库现有 SDK；新 Images 参数按原字段透传，不依赖 SDK 类型枚举。
            **({"extra_body": extra} if extra else {}))
    data = getattr(result, "data", None) or []
    return getattr(data[0], "b64_json", None) if data else None


async def generate_and_send(message, *, group_id, user_id, score, prompt, msg_seq=1,
                            before_send=None, animated=False):
    """True=确认送达，None=发送结果未知；False=本地不可用。

    quota=额度用完、busy=正在画、blocked=模型拒绝、generation_error=生成失败、
    upload_error=QQ 上传失败。所有非 True/None 的情况均确定没有发出图片。

    只把图片内容交给 QQ 的 file_data 接口，不存盘、不记录生成提示或图片内容。
    """
    if (not prompt or not available() or _expired(message)
            or (animated and not capabilities.enabled(config, "gif"))):
        return False
    if _lock.locked():
        return "busy"
    async with _lock:
        # 请求前预约并立即落盘；个人次数在确定没送出图片时退回。
        state = runtime.RESOURCE_QUOTAS.check("image", group_id, user_id, score)
        if state in ("exhausted", "unavailable"):
            return "quota" if state == "exhausted" else False
        if not runtime.IMAGE_GENERATION_BUDGET.try_consume():
            return "quota"
        budget_day = runtime.IMAGE_GENERATION_BUDGET.reservation_day
        runtime.STATE.mark_dirty()
        quota = runtime.RESOURCE_QUOTAS.consume("image", group_id, user_id, score)
        if quota in ("exhausted", "unavailable"):
            # 本地落盘失败时没有调用供应商；恢复两个账本的内存预约。
            if quota == "unavailable":
                runtime.RESOURCE_QUOTAS.release(
                    "image", group_id, user_id,
                    runtime.RESOURCE_QUOTAS.reservation_day, persist=False)
            runtime.IMAGE_GENERATION_BUDGET.release(budget_day)
            runtime.STATE.mark_dirty()
            return False
        reservation_day = runtime.RESOURCE_QUOTAS.reservation_day
        if (budget_day != reservation_day
                or budget_day != runtime.IMAGE_GENERATION_BUDGET.today()):
            # 两本账如果恰好跨日，不能让新一天无全局预约地发起请求。
            runtime.IMAGE_GENERATION_BUDGET.release(budget_day)
            runtime.STATE.mark_dirty()
            if not runtime.RESOURCE_QUOTAS.release(
                    "image", group_id, user_id, reservation_day):
                logger.warning("跨日生图预约取消时个人额度未能退还")
            return False
        request_started = False
        possible_delivery = False
        try:
            try:
                request_started = True
                image_data = await asyncio.wait_for(
                    _generate(prompt, _key(), **({"animated": True} if animated else {})),
                    timeout=config.IMAGE_GENERATION_TIMEOUT_SECONDS)
            except BadRequestError as exc:
                code = _log_generation_error(exc)
                return "blocked" if code in _BLOCKED_CODES else "generation_error"
            except Exception as exc:
                _log_generation_error(exc)
                return "generation_error"
            if not image_data or _expired(message):
                return "generation_error"
            try:
                # 原样上传编码，解码仅校验格式与大小，不转码、不丢 GIF 帧。
                try:
                    decoded = base64.b64decode(image_data, validate=True)
                except (binascii.Error, TypeError, ValueError):
                    return "generation_error"
                if animated and not decoded.startswith((b"GIF87a", b"GIF89a")):
                    return "generation_error"
                if len(decoded) >= _QQ_FILE_DATA_LIMIT:
                    return "upload_error"
                del decoded
                # botpy 尚未封装 file_data；与其 post_group_file 使用同一 QQ 路由。
                route = Route("POST", "/v2/groups/{group_openid}/files", group_openid=group_id)
                media = await asyncio.wait_for(message._api._http.request(route, json={
                    "file_type": 1, "file_data": image_data, "srv_send_msg": False,
                    "file_name": "generated.gif" if animated else "generated.png",
                }), timeout=30)
                file_info = media.get("file_info") if isinstance(media, dict) else None
                if not isinstance(file_info, str) or not file_info or _expired(message):
                    return "upload_error"
                media = {"file_info": file_info}
                if before_send is not None:
                    msg_seq = await before_send()
                if _expired(message):
                    return "upload_error"
            except Exception as exc:
                logger.warning("图片上传或发送准备失败: type=%s", type(exc).__name__)
                return "upload_error"
            # 发出请求后，异常、取消或空回执都不能证明未送达，保留个人预约。
            possible_delivery = True
            try:
                receipt = await asyncio.wait_for(
                    message.reply(msg_type=7, media=media, msg_seq=msg_seq), timeout=30)
            except Exception as exc:
                logger.warning("图片发送未确认: %s", type(exc).__name__)
                return None
            if not receipt:
                logger.warning("图片发送未确认: empty receipt")
                return None
            # 群友常引用这张图说话（「这图反了」）：记进引用索引，认得出是在回小王的图
            label = "动图" if animated else "图"
            runtime.QUOTES.remember(group_id, quotes.receipt_ref(receipt), quotes.SELF,
                                    f"[{label}：{prompt}]")
            return True
        finally:
            if not possible_delivery:
                # 没有实际向供应商发出请求时，两本账都撤销；可能已计费的尝试
                # 全局账保守保留，避免超时或审核造成无限成本。
                if not request_started:
                    runtime.IMAGE_GENERATION_BUDGET.release(budget_day)
                if not runtime.RESOURCE_QUOTAS.release(
                        "image", group_id, user_id, reservation_day):
                    logger.warning("未能保存生图个人额度退还")
