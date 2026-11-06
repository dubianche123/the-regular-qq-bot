"""Native voice transport: explicit local tickets, bounded sessions and cleanup."""
import asyncio
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch

import aiohttp
from aiohttp.test_utils import TestClient, TestServer

import panel
from regular import panel_voice
from test_instance_settings import configuration


class FakeSession:
    def __init__(self, mode="ptt"):
        self.mode = mode
        self.close_count = 0
        self.received = []
        self.fail = False

    async def run(self, ws):
        await ws.send_json({"type": "ready", "mode": self.mode, "rate": 24000, "channels": 1})
        if self.fail:
            raise RuntimeError("PRIVATE_PROVIDER_KEY")
        async for message in ws:
            if message.type == aiohttp.WSMsgType.BINARY:
                self.received.append(message.data)
            elif message.type == aiohttp.WSMsgType.TEXT:
                if json.loads(message.data).get("type") == "stop":
                    break

    async def close(self):
        self.close_count += 1


class PanelVoiceTest(unittest.IsolatedAsyncioTestCase):
    TOKEN = "synthetic-local-voice-token-0123456789"

    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.cfg = configuration(Path(self.temp.name))
        self.runner = Mock()
        self.runner.close = AsyncMock()
        self.sessions = []
        self.factory = Mock(side_effect=self.make_session)
        self.readiness = Mock(return_value={"available": True, "reason": "", "daily_limit": 30,
                                            "api_key": "PRIVATE_PROVIDER_KEY", "pid": 12345})
        installer = panel_voice.install_voice_routes

        def install(app, cfg, csrf, checked_form, **kwargs):
            self.manager = installer(app, cfg, csrf, checked_form, **kwargs,
                                     status_provider=self.readiness, session_factory=self.factory)
            return self.manager

        with patch.object(panel, "PanelRuntime", return_value=self.runner), \
                patch.object(panel_voice, "install_voice_routes", side_effect=install):
            self.app = panel.create_app(self.cfg, token=self.TOKEN)
        self.client = TestClient(TestServer(self.app))
        await self.client.start_server()
        response = await self.client.get("/companion/session", headers=self.auth())
        self.csrf = (await response.json())["csrf"]

    async def asyncTearDown(self):
        await self.client.close()
        self.temp.cleanup()

    def auth(self, **extra):
        return {panel.TOKEN_HEADER: self.TOKEN, **extra}

    def make_session(self, cfg, *, mode="ptt"):
        self.assertIs(cfg, self.cfg)
        session = FakeSession(mode)
        self.sessions.append(session)
        return session

    async def start(self, **headers):
        return await self.client.post("/companion/voice/start", headers=self.auth(**headers),
                                      data={"csrf": self.csrf})

    async def ticket(self):
        response = await self.start()
        self.assertEqual(response.status, 200)
        return (await response.json())["ticket"]

    async def connect(self, ticket, **headers):
        return await self.client.ws_connect(panel_voice.STREAM_PATH,
            headers=self.auth(**{panel_voice.TICKET_HEADER: ticket, **headers}))

    async def assert_rejected(self, ticket="wrong", *, status=403, headers=None):
        with self.assertRaises(aiohttp.WSServerHandshakeError) as caught:
            await self.client.ws_connect(panel_voice.STREAM_PATH, headers=headers or self.auth(
                **{panel_voice.TICKET_HEADER: ticket}))
        self.assertEqual(caught.exception.status, status)

    async def test_status_and_start_are_read_only_until_ticket_is_consumed(self):
        response = await self.client.get("/companion/voice/status", headers=self.auth())
        self.assertEqual(response.status, 200)
        state = await response.json()
        self.assertTrue(state["available"])
        self.assertFalse(state["busy"])
        self.assertEqual(state["daily_limit"], 30)
        self.assertEqual(state["format"], panel_voice.FORMAT)
        self.assertEqual(state["limits"], panel_voice.LIMITS)
        self.assertNotIn("PRIVATE_PROVIDER_KEY", json.dumps(state))
        self.assertNotIn("pid", state)
        response = await self.start()
        body = await response.json()
        self.assertEqual(body["stream"], "/companion/voice/stream")
        self.assertGreaterEqual(len(body["ticket"]), 32)
        self.assertNotIn(self.TOKEN, json.dumps(body))
        self.assertNotIn("PRIVATE_PROVIDER_KEY", json.dumps(body))
        self.factory.assert_not_called()
        self.runner.start.assert_not_called()
        self.runner.stop.assert_not_called()

    async def test_continuous_mode_is_explicit_validated_and_bound_to_the_ticket(self):
        for mode in ("unknown", "session.update", "CONTINUOUS"):
            response = await self.client.post("/companion/voice/start", headers=self.auth(),
                                              data={"csrf": self.csrf, "mode": mode})
            self.assertEqual(response.status, 400)
            self.assertIsNone(self.manager.pending)
        response = await self.client.post("/companion/voice/start", headers=self.auth(),
                                          data={"csrf": self.csrf, "mode": "continuous"})
        body = await response.json()
        self.assertEqual(body["mode"], "continuous")
        self.factory.assert_not_called()
        # A WebSocket query cannot change the mode authorized by the POST.
        ws = await self.client.ws_connect(panel_voice.STREAM_PATH + "?mode=ptt",
            headers=self.auth(**{panel_voice.TICKET_HEADER: body["ticket"]}))
        active = self.manager.active
        self.assertEqual((await ws.receive_json())["mode"], "continuous")
        self.factory.assert_called_once_with(self.cfg, mode="continuous")
        await ws.send_json({"type": "stop"})
        await ws.receive()
        await asyncio.wait_for(active.finished.wait(), 1)

    async def test_auth_csrf_post_and_exact_origin_are_required(self):
        for route in ("/companion/voice/status", panel_voice.STREAM_PATH):
            self.assertEqual((await self.client.get(route)).status, 401)
        self.assertEqual((await self.client.post("/companion/voice/start")).status, 401)
        self.assertEqual((await self.client.get("/companion/voice/start", headers=self.auth())).status, 405)
        for csrf in ("", "wrong"):
            response = await self.client.post("/companion/voice/start", headers=self.auth(), data={"csrf": csrf})
            self.assertEqual(response.status, 400)
        for origin in ("null", "https://outside.example", "http://127.0.0.1:1"):
            response = await self.start(Origin=origin)
            self.assertEqual(response.status, 403)
            await self.assert_rejected(headers=self.auth(Origin=origin))
        origin = str(self.client.make_url("/")).rstrip("/")
        self.assertEqual((await self.start(Origin=origin)).status, 200)
        self.factory.assert_not_called()

    async def test_pending_ticket_owns_slot_and_expires_without_worker(self):
        now = [10.0]
        self.manager.clock = lambda: now[0]
        old = await self.ticket()
        self.assertEqual((await self.start()).status, 409)
        now[0] += 30
        state = await (await self.client.get("/companion/voice/status", headers=self.auth())).json()
        self.assertFalse(state["busy"])
        await self.assert_rejected(old)
        self.assertNotEqual(old, await self.ticket())
        self.factory.assert_not_called()

    async def test_websocket_requires_header_token_and_single_use_header_ticket(self):
        ticket = await self.ticket()
        self.client.session.cookie_jar.update_cookies({"panel_token": self.TOKEN})
        await self.assert_rejected(status=401, headers={panel_voice.TICKET_HEADER: ticket})
        for invalid in ("", "wrong", "错误"):
            await self.assert_rejected(invalid)
        self.factory.assert_not_called()
        response = await self.client.get(panel_voice.STREAM_PATH,
            headers=self.auth(**{panel_voice.TICKET_HEADER: ticket}))
        self.assertEqual(response.status, 400)
        self.factory.assert_not_called()
        ws = await self.connect(ticket)
        self.assertEqual(ws._response.headers.get("X-Regular-Panel"), "1")
        self.assertEqual((await ws.receive_json())["type"], "ready")
        active = self.manager.active
        self.assertEqual((await self.start()).status, 409)
        await self.assert_rejected(ticket)
        await ws.send_bytes(b"\0\0" * 480)
        await ws.send_json({"type": "stop"})
        await ws.receive()
        await asyncio.wait_for(active.finished.wait(), 1)
        self.assertEqual(self.sessions[0].received, [b"\0\0" * 480])
        self.assertEqual(self.sessions[0].close_count, 1)
        self.assertIsNone(self.manager.active)
        await self.assert_rejected(ticket)
        await self.ticket()

    async def test_disconnect_closes_worker_once_and_releases_slot(self):
        ws = await self.connect(await self.ticket())
        await ws.receive_json()
        active = self.manager.active
        await ws.close()
        await asyncio.wait_for(active.finished.wait(), 1)
        self.assertTrue(active.task.done())
        self.assertEqual(self.sessions[0].close_count, 1)
        self.assertIsNone(self.manager.active)
        await self.ticket()

    async def test_timeout_is_bounded_and_does_not_restart_worker(self):
        self.manager.session_timeout = .02
        ws = await self.connect(await self.ticket())
        active = self.manager.active
        await ws.receive_json()
        event = await ws.receive_json(timeout=1)
        self.assertEqual(event["type"], "closed")
        await ws.receive()
        await asyncio.wait_for(active.finished.wait(), 1)
        self.assertTrue(active.task.cancelled())
        self.factory.assert_called_once()
        self.assertEqual(self.sessions[0].close_count, 1)

    async def test_worker_failure_and_constructor_failure_hide_exception_details(self):
        self.factory.side_effect = RuntimeError("PRIVATE_PROVIDER_KEY")
        await self.assert_rejected(await self.ticket(), status=503)
        self.assertIsNone(self.manager.active)
        self.factory.side_effect = self.make_session
        original = self.make_session

        def failing(cfg):
            session = original(cfg)
            session.fail = True
            return session

        self.factory.side_effect = failing
        ws = await self.connect(await self.ticket())
        await ws.receive_json()
        event = await ws.receive_json()
        self.assertEqual(event["type"], "error")
        self.assertNotIn("PRIVATE_PROVIDER_KEY", json.dumps(event))
        await ws.receive()
        self.assertEqual(self.sessions[0].close_count, 1)

    async def test_shutdown_cancels_session_and_is_idempotent(self):
        ws = await self.connect(await self.ticket())
        await ws.receive_json()
        active = self.manager.active
        await self.app.shutdown()
        await self.manager.close()
        await asyncio.wait_for(active.finished.wait(), 1)
        self.assertTrue(active.task.cancelled())
        self.assertEqual(self.sessions[0].close_count, 1)
        self.assertIsNone(self.manager.active)
        self.assertEqual((await self.start()).status, 503)

    async def test_oversized_frame_closes_without_more_worker_calls(self):
        ws = await self.connect(await self.ticket())
        await ws.receive_json()
        active = self.manager.active
        await ws.send_bytes(b"\0" * (64 * 1024 + 1))
        message = await ws.receive(timeout=1)
        self.assertEqual(message.type, aiohttp.WSMsgType.CLOSE)
        self.assertEqual(message.data, 1009)
        await asyncio.wait_for(active.finished.wait(), 1)
        self.assertEqual(self.sessions[0].received, [])
        self.factory.assert_called_once()

    async def test_unavailable_status_and_errors_never_construct_worker(self):
        self.readiness.return_value = {"available": False, "reason": "请先配置语音服务。", "daily_limit": 0}
        response = await self.start()
        self.assertEqual(response.status, 503)
        self.assertEqual(await response.json(), {"error": "请先配置语音服务。"})
        self.readiness.side_effect = RuntimeError("PRIVATE_PROVIDER_KEY")
        response = await self.start()
        self.assertEqual(response.status, 503)
        self.assertNotIn("PRIVATE_PROVIDER_KEY", await response.text())
        self.factory.assert_not_called()

    async def test_cleanup_releases_unconsumed_ticket_without_connection(self):
        await self.ticket()
        handle = self.manager.expiry
        await self.manager.close()
        await self.manager.close()
        self.assertIsNone(self.manager.pending)
        self.assertTrue(handle.cancelled())
        self.factory.assert_not_called()
