"""Optional group voice delivery; never used unless explicitly configured."""
import asyncio
import base64
import os
import time
import aiohttp
from pathlib import Path
from urllib.parse import urlsplit

from botpy.http import Route
import delivery
from realtime_synth import RealtimeSilkSynth
from voice_budget import Budget
from voice_outbound import Delivery, Settings, VoiceOutbound


try:
    import fcntl
except ImportError:  # Windows has byte-range locks instead of flock.
    import msvcrt

    def _lock_file(fd):
        # Lock the same existing byte on every handle; locking starts at its cursor.
        if os.fstat(fd).st_size == 0:
            os.write(fd, b"\0")
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)

    def _unlock_file(fd):
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
else:
    def _lock_file(fd):
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock_file(fd):
        fcntl.flock(fd, fcntl.LOCK_UN)


class QQTransport:
    def __init__(self, message, sender, current):
        self.message, self.sender, self.current = message, sender, current
        self.receipt = None

    def valid(self):
        age = delivery.message_age_seconds(self.message)
        return (self.current() and bool(getattr(self.message, 'group_openid', None)) and
                age is not None and 0 <= age < delivery.passive_reply_window(self.message) - 10)

    async def upload(self, silk):
        if not self.valid():
            raise RuntimeError('expired')
        payload = base64.b64encode(silk.read_bytes()).decode('ascii')
        route = Route('POST', '/v2/groups/{group_openid}/files',
                      group_openid=self.message.group_openid)
        result = await self.message._api._http.request(route, json={
            'file_type': 3, 'file_data': payload, 'srv_send_msg': False})
        return result.get('file_info') if isinstance(result, dict) else None

    async def send(self, file_info):
        if not self.valid():
            return Delivery.FAILED  # no message request made
        # Do not use botpy request/message.reply here: botpy retries connection resets.
        # A single raw request is required because even a reset can follow delivery.
        http = self.message._api._http
        await http.check_session()
        if not self.valid():
            return Delivery.FAILED
        sequence = self.sender._next_sequence(self.message)
        route = Route('POST', '/v2/groups/{group_openid}/messages',
                      group_openid=self.message.group_openid)
        route.is_sandbox = http.is_sandbox
        async with http._session.request(method=route.method, url=route.url,
                headers=http._headers, timeout=aiohttp.ClientTimeout(total=15),
                json={'msg_type': 7, 'msg_id': self.message.id, 'msg_seq': sequence,
                      'media': {'file_info': file_info}}, allow_redirects=False) as response:
            if response.status < 200 or response.status >= 300:
                return Delivery.UNKNOWN
            self.receipt = await response.json()
        return Delivery.SENT if isinstance(self.receipt, dict) and self.receipt.get('id') else Delivery.UNKNOWN


class VoiceOutputRuntime:
    def __init__(self, config, key):
        self.config, self.key = config, key
        self._lock = asyncio.Lock()

    def ready(self):
        """部署级的「这台机器能发语音」：开关、凭据、官方端点、本机编码器都齐。

        它决定稳定头里写不写「你会发语音」（bot.media_rules），所以只看配置，不看额度和在途。
        """
        c = self.config
        if not c.VOICE_OUTPUT_ENABLED or not c.SSL_VERIFY or not self.key:
            return False
        try:
            endpoint = urlsplit(c.PROVIDER_PRESETS['openai']['base_url'] or 'https://api.openai.com/v1')
            if (endpoint.scheme != 'https' or endpoint.hostname != 'api.openai.com'
                    or endpoint.port not in (None, 443) or endpoint.username is not None
                    or endpoint.password is not None or endpoint.query or endpoint.fragment):
                return False
        except (KeyError, TypeError, ValueError):
            return False
        return bool(c.VOICE_OUTPUT_NODE and c.VOICE_OUTPUT_SILK_ENTRY)

    def busy(self):
        return self._lock.locked()

    def tired(self):
        """今天的语音次数用完了没有（只读，不预约）。"""
        db = Path(self.config.DATA_DIR) / 'voice-outbound-budget.sqlite3'
        return Budget(str(db), 'delivery', self.config.VOICE_OUTPUT_DAILY_LIMIT).exhausted()

    async def attempt(self, message, body, sender, current, *, tone=""):
        """小王自己选了〔语音〕才会走到这里；tone 是它写在标记后面的语气。"""
        c = self.config
        if not self.ready() or self._lock.locked():
            return None, None
        async with self._lock:
            # Cross-process lock covers synthesis and upload as well as sending.
            try:
                lock_path = Path(c.DATA_DIR) / 'voice-outbound.lock'
                lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
                try:
                    _lock_file(lock_fd)
                except OSError:
                    os.close(lock_fd)
                    return None, None
            except OSError:
                return None, None
            try:
                db = str(Path(c.DATA_DIR) / 'voice-outbound-budget.sqlite3')
                synth = RealtimeSilkSynth(self.key, Budget(db, 'provider', c.VOICE_OUTPUT_DAILY_LIMIT),
                    c.VOICE_OUTPUT_NODE, c.VOICE_OUTPUT_SILK_ENTRY,
                    str(Path(__file__).resolve().parent / 'regular' / 'voice_encode.mjs'))
                start_day = time.strftime('%Y-%m-%d')
                transport = QQTransport(message, sender,
                    lambda: current() and time.strftime('%Y-%m-%d') == start_day)
                out = VoiceOutbound(synth, transport, Budget(db, 'delivery', c.VOICE_OUTPUT_DAILY_LIMIT),
                    Settings(enabled=True))
                result = await out.attempt(body, explicit=True,
                                           tone_context=(tone,) if tone else ())
                return result, transport.receipt
            finally:
                try:
                    _unlock_file(lock_fd)
                finally:
                    os.close(lock_fd)
