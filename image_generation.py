"""一次生图与 QQ 群图片投递；生成、上传、发送均不自动重试。"""
import asyncio
import logging
from urllib.parse import urlsplit

import httpx
from botpy.http import Route
from openai import AsyncOpenAI

import config
import delivery
import runtime
from regular import decision_settings

logger = logging.getLogger("qqbot")
_lock = asyncio.Lock()


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
    """True=确认送达，False=确定未发送，None=发送结果未知；额度用完返回 quota。

    只把图片内容交给 QQ 的 file_data 接口，不存盘、不记录生成提示或图片内容。
    """
    if not prompt or not available() or _lock.locked() or _expired(message):
        return False
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
            if not image_data or _expired(message):
                return False
            # botpy 尚未封装 file_data；与其 post_group_file 使用同一 QQ 路由。
            route = Route("POST", "/v2/groups/{group_openid}/files", group_openid=group_id)
            media = await asyncio.wait_for(message._api._http.request(route, json={
                "file_type": 1, "file_data": image_data, "srv_send_msg": False,
            }), timeout=30)
            if not media or _expired(message):
                return False
            if before_send is not None:
                msg_seq = await before_send()
            if _expired(message):
                return False
        except Exception as exc:
            logger.warning("生图或上传失败: %s", type(exc).__name__)
            return False
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
