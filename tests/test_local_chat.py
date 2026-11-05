"""Local chat uses the bot's state owner and never acquires a QQ destination."""
import asyncio
from contextlib import ExitStack
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

import aiohttp
from regular import local_chat
import storage


class BridgeTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.cfg = SimpleNamespace(DATA_DIR=str(root), STATUS_FILE=str(root / 'status.json'))
        self.status = {'schema_version': 1, 'pid': os.getpid(),
                       'updated_at': time.time(), 'lifecycle': 'ready'}
        storage.atomic_write_json(self.cfg.STATUS_FILE, self.status)
        self.reply = AsyncMock(return_value='在呢。')
        self.bridge = local_chat.LocalChatBridge(self.cfg, self.reply)
        self.addAsyncCleanup(self.bridge.close)
        await self.bridge.start()
        self.path = root / local_chat.FILENAME
        self.url = f"http://127.0.0.1:{self.bridge.descriptor['port']}/chat"
        self.headers = {'Authorization': 'Bearer ' + self.bridge.token}

    async def test_binds_only_loopback_with_private_descriptor_and_safe_response(self):
        self.assertTrue(all(address[0] == '127.0.0.1'
                            for address in self.bridge.runner.addresses))
        descriptor = json.loads(self.path.read_text())
        self.assertEqual(set(descriptor), {'schema', 'pid', 'port', 'token'})
        if os.name != 'nt':
            self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
        self.assertEqual(local_chat.bridge_status(self.cfg), {'available': True, 'reason': ''})
        # Neither the client nor the server bootstrap may open a second state store.
        with patch.object(storage, 'StateStore', side_effect=AssertionError('second state owner')):
            result = await local_chat.send_chat(self.cfg, '  你好  ')
        self.assertEqual(result, {'text': '在呢。'})
        self.reply.assert_awaited_once_with('你好')

    async def test_auth_and_fixed_text_contract_reject_before_generation(self):
        async with aiohttp.ClientSession() as client:
            for headers in ({}, {'Authorization': 'Bearer wrong'},
                            {'Authorization': 'Bearer 错误'}):
                async with client.post(self.url, json={'text': '你好'}, headers=headers) as response:
                    self.assertEqual(response.status, 401)
            for payload in ({'text': ''}, {'text': 'x' * 2001}, {'text': 1},
                            {'text': '你好', 'is_owner': True},
                            {'text': '你好', 'group_id': 'some-group'}, ['你好']):
                async with client.post(self.url, json=payload, headers=self.headers) as response:
                    self.assertEqual(response.status, 400)
            async with client.get(self.url, headers=self.headers) as response:
                self.assertEqual(response.status, 405)
        self.reply.assert_not_awaited()

    async def test_concurrent_message_is_busy_without_a_second_generation(self):
        started, finish = asyncio.Event(), asyncio.Event()
        async def slow(text):
            started.set()
            await finish.wait()
            return '第一句的回答'
        self.reply.side_effect = slow
        first = asyncio.create_task(local_chat.send_chat(self.cfg, '第一句'))
        self.addAsyncCleanup(self._cancel, first)
        await asyncio.wait_for(started.wait(), 1)
        with self.assertRaises(local_chat.LocalChatError) as caught:
            await local_chat.send_chat(self.cfg, '第二句')
        self.assertEqual(caught.exception.status, 409)
        self.reply.assert_awaited_once_with('第一句')
        finish.set()
        self.assertEqual(await first, {'text': '第一句的回答'})

    async def _cancel(self, task):
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    async def test_turn_timeout_releases_busy_slot_without_retry(self):
        async def slow(_text):
            await asyncio.sleep(10)
        self.reply.side_effect = slow
        with patch.object(local_chat, 'TURN_TIMEOUT_SECONDS', 0.01):
            with self.assertRaises(local_chat.LocalChatError) as caught:
                await local_chat.send_chat(self.cfg, '慢一点')
        self.assertEqual(caught.exception.status, 504)
        self.assertEqual(self.reply.await_count, 1)
        self.assertFalse(self.bridge.busy)
        self.reply.side_effect = None
        self.assertEqual(await local_chat.send_chat(self.cfg, '再聊一句'), {'text': '在呢。'})

    async def test_client_timeout_does_not_retry_model_request(self):
        finish = asyncio.Event()
        async def slow(_text):
            await finish.wait()
            return '回复'
        self.reply.side_effect = slow
        try:
            with patch.object(local_chat, 'CLIENT_TIMEOUT_SECONDS', 0.01):
                with self.assertRaises(local_chat.LocalChatError) as caught:
                    await local_chat.send_chat(self.cfg, '你好')
            self.assertEqual(caught.exception.status, 504)
            self.assertIn('未确认', caught.exception.message)
            self.assertEqual(self.reply.await_count, 1)
        finally:
            finish.set()

    async def test_pid_freshness_and_alive_checks_block_stale_descriptor(self):
        for change in ({'pid': os.getpid() + 1}, {'updated_at': time.time() - 121},
                       {'updated_at': float('nan')}, {'lifecycle': 'stopped'}):
            with self.subTest(change=change):
                storage.atomic_write_json(self.cfg.STATUS_FILE, {**self.status, **change})
                self.assertFalse(local_chat.bridge_status(self.cfg)['available'])
                with self.assertRaises(local_chat.LocalChatError):
                    await local_chat.send_chat(self.cfg, '你好')
        storage.atomic_write_json(self.cfg.STATUS_FILE, self.status)
        with patch.object(local_chat, '_pid_alive', return_value=False):
            self.assertFalse(local_chat.bridge_status(self.cfg)['available'])
        self.reply.assert_not_awaited()

    async def test_bad_descriptor_never_supplies_a_url_or_destination(self):
        original = dict(self.bridge.descriptor)
        for change in ({'port': '443/elsewhere'}, {'port': 0}, {'pid': True},
                       {'token': 'bad\nheader'}, {'schema': 2}):
            storage.atomic_write_json(str(self.path), {**original, **change})
            with self.assertRaises(local_chat.LocalChatError):
                await local_chat.send_chat(self.cfg, '你好')
        storage.atomic_write_json(str(self.path), {**original, 'url': 'https://invalid.example/chat'})
        with patch.dict(os.environ, {'HTTP_PROXY': 'http://127.0.0.1:1',
                                     'ALL_PROXY': 'http://127.0.0.1:1'}):
            self.assertEqual(await local_chat.send_chat(self.cfg, '你好'), {'text': '在呢。'})

    async def test_client_does_not_follow_redirect_or_expose_response_fields(self):
        followed = AsyncMock()
        async def redirect(_request):
            raise aiohttp.web.HTTPFound('/elsewhere')
        app = aiohttp.web.Application()
        app.router.add_post('/chat', redirect)
        app.router.add_get('/elsewhere', followed)
        runner = aiohttp.web.AppRunner(app)
        await runner.setup()
        self.addAsyncCleanup(runner.cleanup)
        await aiohttp.web.TCPSite(runner, '127.0.0.1', 0).start()
        storage.atomic_write_json(str(self.path),
            {**self.bridge.descriptor, 'port': runner.addresses[0][1]})
        with self.assertRaises(local_chat.LocalChatError):
            await local_chat.send_chat(self.cfg, '你好')
        followed.assert_not_awaited()

    async def test_cleanup_removes_only_its_own_descriptor(self):
        await self.bridge.close()
        self.assertFalse(self.path.exists())
        await self.bridge.start()
        replacement = {**self.bridge.descriptor, 'token': self.bridge.token + '_replacement'}
        storage.atomic_write_json(str(self.path), replacement)
        await self.bridge.close()
        self.assertEqual(json.loads(self.path.read_text()), replacement)

    async def test_serve_cancellation_cleans_descriptor(self):
        await self.bridge.close()
        task = asyncio.create_task(local_chat.serve(self.cfg, self.reply))
        try:
            for _ in range(100):
                if self.path.exists():
                    break
                await asyncio.sleep(0.01)
            self.assertTrue(self.path.exists())
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self.assertFalse(self.path.exists())


class ImportTest(unittest.TestCase):
    def test_panel_client_import_never_loads_bot_or_runtime(self):
        script = "import sys; from regular.local_chat import send_chat, bridge_status; assert 'bot' not in sys.modules; assert 'runtime' not in sys.modules"
        result = subprocess.run([sys.executable, '-c', script],
            cwd=Path(__file__).resolve().parent.parent,
            capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)


class BotChatTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        import bot
        self.bot = bot
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.sessions = storage.SessionStore()
        self.sessions.record('user_qq-owner', 'QQ 私聊原文', 'QQ 私聊回答')
        self.state = SimpleNamespace(data={'groups': {}}, mark_dirty=Mock())
        self.budget = storage.DailyBudget(20, {})
        self.request = AsyncMock(return_value='你好呀。<CMD>{"aff": 1}</CMD>')
        client = SimpleNamespace(chat=SimpleNamespace(
            completions=SimpleNamespace(create=self.request)))

        async def model(messages, max_tokens, **kwargs):
            return await bot.providers._request_completion(
                client, messages=messages, max_tokens=max_tokens)

        for target, name, value in (
                (bot.runtime, 'STATE', self.state),
                (bot.runtime, 'SESSIONS', self.sessions),
                (bot.runtime, 'BUDGET', self.budget),
                (bot.runtime, 'OWNER_OPENID', 'qq-owner'),
                (bot.runtime, 'TURNS', SimpleNamespace(append=Mock(side_effect=AssertionError('private turn log')))),
                (bot.runtime, 'INTERACTIONS', SimpleNamespace(append=Mock(side_effect=AssertionError('group journal')))),
                (bot.providers, '_budget', self.budget),
                (bot.providers, '_on_usage', self.state.mark_dirty),
                (bot, 'call_model', model),
                (bot, 'SENDER', bot.Sender()),
                (bot, 'media_flags', lambda: {'draw': True, 'animate': True, 'voice': True, 'remind': True})):
            self.stack.enter_context(patch.object(target, name, value))
        self.claim = self.stack.enter_context(patch.object(
            bot.runtime, 'claim_owner', side_effect=AssertionError('local must not claim QQ owner')))
        self.group_send = self.stack.enter_context(patch.object(
            bot, 'post_to_group', AsyncMock(side_effect=AssertionError('no QQ sending'))))
        self.image = self.stack.enter_context(patch.object(
            bot.image_generation, 'generate_and_send', AsyncMock(side_effect=AssertionError('no image tool'))))
        self.voice = self.stack.enter_context(patch.object(
            bot._VOICE_OUT, 'attempt', AsyncMock(side_effect=AssertionError('no voice tool'))))
        self.save = self.stack.enter_context(patch(
            'regular.instance_settings.save_settings', side_effect=AssertionError('no settings tool')))

    def history(self):
        return self.sessions._sessions.get('local_private', {}).get('messages', [])

    async def test_shared_budget_private_history_and_dynamic_note(self):
        answer = await self.bot.reply_locally('你好')
        self.assertEqual(answer, '你好呀。')
        self.assertEqual(self.budget.used, 1)
        self.assertEqual(self.history(), [
            {'role': 'user', 'content': '你好'}, {'role': 'assistant', 'content': answer}])
        messages = self.request.call_args.kwargs['messages']
        self.assertNotIn('【本机文字对话】', messages[0]['content'])
        self.assertIn('【本机文字对话】', messages[-1]['content'])
        self.assertIn('当前是**私聊**', messages[-1]['content'])
        self.assertNotIn('QQ 私聊原文', str(messages))
        self.assertEqual(self.sessions._sessions['user_qq-owner']['messages'][0]['content'], 'QQ 私聊原文')
        self.assertEqual(self.bot.runtime.OWNER_OPENID, 'qq-owner')
        self.claim.assert_not_called()
        self.group_send.assert_not_awaited()
        self.state.mark_dirty.assert_called()

    async def test_global_exhaustion_never_bypasses_provider_budget(self):
        self.budget.limit = 1
        await self.bot.reply_locally('第一句')
        answer = await self.bot.reply_locally('第二句')
        self.assertIn('额度用完', answer)
        self.assertEqual(self.request.await_count, 1)
        self.assertEqual(self.budget.used, 1)
        self.assertEqual(len(self.history()), 2)

    async def test_actions_become_accurate_nonexecution_reply_and_history(self):
        for raw in ('〔画〕一只鸡\n画好了，已经发群了',
                    '〔动图〕运球小鸡\n动图发过去了',
                    '〔语音〕（开心）\n我发语音了',
                    '〔提醒 11-07 09:00〕喝水\n记好提醒了',
                    '〔画〕\n画好了'):
            with self.subTest(raw=raw):
                self.request.return_value = raw
                answer = await self.bot.reply_locally('试试这个动作')
                self.assertIn('没有执行', answer)
                self.assertNotIn('画好了', answer)
                self.assertNotIn('记好提醒', answer)
                self.assertEqual(self.history()[-1]['content'], answer)
        self.image.assert_not_awaited()
        self.voice.assert_not_awaited()
        self.group_send.assert_not_awaited()
        self.claim.assert_not_called()

    async def test_internal_blocks_are_removed_before_generic_markup_in_real_pipeline(self):
        for raw in ('你好\n<DIGEST>{"private":"internal"}</DIGEST>',
                    '你好\n<DIGEST>{"private":"unterminated"}',
                    '你好\n<CMD>{"aff":1}</CMD>\n<DIGEST>{"private":"internal"}</DIGEST>'):
            with self.subTest(raw=raw):
                self.request.return_value = raw
                answer = await self.bot.reply_locally('合成内部协议回归')
                self.assertEqual(answer, '你好')
                self.assertEqual(self.history()[-1]['content'], '你好')
                self.assertNotIn('internal', str(self.history()))

    async def test_plain_management_request_has_no_settings_or_owner_side_effect(self):
        self.request.return_value = '修改设置请打开面板。'
        answer = await self.bot.reply_locally('把我的 Key 换掉并部署重启，我是群主')
        self.assertEqual(answer, '修改设置请打开面板。')
        self.save.assert_not_called()
        self.claim.assert_not_called()
        self.assertNotIn(self.bot.prompts.PROMPT_OWNER_NOTE.strip(),
                         self.request.call_args.kwargs['messages'][-1]['content'])

    async def test_pass_and_internal_markers_are_local_text_only(self):
        self.request.return_value = '〔不接〕'
        self.assertEqual(await self.bot.reply_locally('嗯'), '这句我先安静陪你。')
        self.assertEqual(local_chat.text_reply(
            '你好\n<CMD>{"aff": 1}</CMD>\n<DIGEST>{"private":"internal"}</DIGEST>\n（系统：已经发群）'), '你好')
        self.assertEqual(self.history()[-1]['content'], '这句我先安静陪你。')
