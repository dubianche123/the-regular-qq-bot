"""一次生图与 QQ 群图片投递；生成、上传、发送均不自动重试。"""
import asyncio
import logging
from urllib.parse import urlsplit

import httpx
from botpy.http import Route
from openai import AsyncOpenAI, BadRequestError

import config
import delivery
import runtime
from regular import decision_settings

logger = logging.getLogger("qqbot")
_lock = asyncio.Lock()

# API 响应里的自由文本可能包含群聊内容；只允许已知的机器错误码进入日志。
_SAFE_ERROR_CODES = frozenset({
    "moderation_blocked", "content_policy_violation", "invalid_value",
    "invalid_image_size", "invalid_prompt", "unsupported_value",
    "model_not_found", "billing_hard_limit_reached",
})
_BLOCKED_CODES = frozenset({"moderation_blocked", "content_policy_violation"})


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
    if config.IMAGE_GENERATION_API_KEY or keys:
        return config.IMAGE_GENERATION_API_KEY or keys[0]
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
    return bool(config.IMAGE_GENERATION_ENABLED and config.IMAGE_GENERATION_BASE_URL
                and config.IMAGE_GENERATION_MODEL and _key())


def _expired(message):
    age = delivery.message_age_seconds(message)
    return age is not None and age >= delivery.passive_reply_window(message) - 5


async def _generate(prompt, key):
    # 独立实例不会继承聊天客户端的重试；一轮只有一次付费生成请求。
    async with httpx.AsyncClient(verify=config.SSL_VERIFY,
                                 mounts={"all://127.0.0.1": None,
                                         "all://localhost": None}) as http:
        client = AsyncOpenAI(api_key=key, base_url=config.IMAGE_GENERATION_BASE_URL,
                             http_client=http, max_retries=0)
        result = await client.images.generate(
            model=config.IMAGE_GENERATION_MODEL, prompt=prompt,
            quality=config.IMAGE_GENERATION_QUALITY, size=config.IMAGE_GENERATION_SIZE,
            n=1, timeout=config.IMAGE_GENERATION_TIMEOUT_SECONDS)
    data = getattr(result, "data", None) or []
    return getattr(data[0], "b64_json", None) if data else None


async def generate_and_send(message, *, group_id, user_id, score, prompt, msg_seq=1,
                            before_send=None, on_reserved=None):
    """True=确认送达，None=发送结果未知；False=本地不可用。

    quota=额度用完、busy=正在画、blocked=模型拒绝、generation_error=生成失败、
    upload_error=QQ 上传失败。所有非 True/None 的情况均确定没有发出图片。

    只把图片内容交给 QQ 的 file_data 接口，不存盘、不记录生成提示或图片内容。
    """
    if not prompt or not available() or _expired(message):
        return False
    if _lock.locked():
        return "busy"
    async with _lock:
        # 日额在请求前预约并立即落盘，失败/超时也计入；落盘失败时不发请求。
        state = runtime.RESOURCE_QUOTAS.check("image", group_id, user_id, score)
        if state in ("exhausted", "unavailable"):
            return "quota" if state == "exhausted" else False
        if not runtime.IMAGE_GENERATION_BUDGET.try_consume():
            return "quota"
        runtime.STATE.mark_dirty()
        quota = runtime.RESOURCE_QUOTAS.consume("image", group_id, user_id, score)
        if quota in ("exhausted", "unavailable"):
            return False
        if on_reserved is not None:
            on_reserved(quota)
        try:
            image_data = await asyncio.wait_for(
                _generate(prompt, _key()), timeout=config.IMAGE_GENERATION_TIMEOUT_SECONDS)
        except BadRequestError as exc:
            code = _log_generation_error(exc)
            return "blocked" if code in _BLOCKED_CODES else "generation_error"
        except Exception as exc:
            _log_generation_error(exc)
            return "generation_error"
        if not image_data or _expired(message):
            return "generation_error"
        try:
            # botpy 尚未封装 file_data；与其 post_group_file 使用同一 QQ 路由。
            route = Route("POST", "/v2/groups/{group_openid}/files", group_openid=group_id)
            media = await asyncio.wait_for(message._api._http.request(route, json={
                "file_type": 1, "file_data": image_data, "srv_send_msg": False,
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
        # 发出请求后，异常或空回执不能证明未送达，不能自动发其他回复。
        try:
            receipt = await asyncio.wait_for(
                message.reply(msg_type=7, media=media, msg_seq=msg_seq), timeout=30)
        except Exception as exc:
            logger.warning("图片发送未确认: %s", type(exc).__name__)
            return None
        if not receipt:
            logger.warning("图片发送未确认: empty receipt")
            return None
        return True
