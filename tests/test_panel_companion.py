"""本机群友小窗：同源认证、显式文字发送、角色资源与无副作用入口。"""
import sys
import unittest
from unittest.mock import AsyncMock, patch

import panel
from regular import local_chat
import test_panel_onboarding as onboarding


class PanelCompanionTest(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = onboarding.PanelOnboardingTest.asyncSetUp
    asyncTearDown = onboarding.PanelOnboardingTest.asyncTearDown
    auth = onboarding.PanelOnboardingTest.auth

    async def form(self):
        response = await self.client.get('/companion', headers=self.auth())
        self.assertEqual(response.status, 200)
        return onboarding._Inputs(await response.text()).fields

    async def test_every_workspace_page_has_a_collapsible_companion_without_chat_calls(self):
        with patch.object(local_chat, 'send_chat', new=AsyncMock()) as send:
            for route in ('/setup', '/', '/knowledge', '/memory', '/iterate', '/search', '/decision', '/checkup'):
                with self.subTest(route=route):
                    response = await self.client.get(route, headers=self.auth())
                    self.assertEqual(response.status, 200)
                    text = await response.text()
                    self.assertIn('data-companion-dock', text)
                    self.assertIn('class="brand-mark brand-portrait"', text)
                    self.assertIn('data-actions-toggle', text)
            send.assert_not_awaited()
        self.runner.start.assert_not_called()

    async def test_chat_auth_csrf_and_method_are_required_before_backend_calls(self):
        fields = await self.form()
        with patch.object(local_chat, 'send_chat', new=AsyncMock()) as send:
            for route in ('/companion', '/companion/status'):
                self.assertEqual((await self.client.get(route)).status, 401)
            self.assertEqual((await self.client.post('/companion/chat', data={'text': 'hello'})).status, 401)
            self.assertEqual((await self.client.get('/companion/chat', headers=self.auth())).status, 405)
            for csrf in ('', 'wrong'):
                response = await self.client.post('/companion/chat', headers=self.auth(), data={'csrf': csrf, 'text': 'hello'})
                self.assertEqual(response.status, 400)
            for text in ('', '  ', '字' * 2001):
                response = await self.client.post('/companion/chat', headers=self.auth(), data={'csrf': fields['csrf']['value'], 'text': text})
                self.assertEqual(response.status, 400)
            send.assert_not_awaited()
        self.runner.start.assert_not_called()

    async def test_one_explicit_send_returns_text_without_interpreting_it_as_html(self):
        fields = await self.form()
        reply = '<img src=x onerror=alert(1)> 只是文字'
        with patch.object(local_chat, 'send_chat', new=AsyncMock(return_value={'text': reply, 'private': 'DO_NOT_RETURN'})) as send:
            response = await self.client.post('/companion/chat', headers=self.auth(), data={'csrf': fields['csrf']['value'], 'text': '  你好呀  ', 'command': 'stop'})
            self.assertEqual(response.status, 200)
            self.assertEqual(response.content_type, 'application/json')
            self.assertEqual(await response.json(), {'text': reply})
            send.assert_awaited_once_with(self.cfg, '你好呀')
        self.runner.start.assert_not_called()
        self.runner.stop.assert_not_awaited()

    async def test_busy_unavailable_and_uncertain_results_are_not_retried(self):
        fields = await self.form()
        for status in (409, 503, 504):
            with self.subTest(status=status), patch.object(local_chat, 'send_chat', new=AsyncMock(side_effect=local_chat.LocalChatError(status, '尚未确认；没有自动重试。'))) as send:
                response = await self.client.post('/companion/chat', headers=self.auth(), data={'csrf': fields['csrf']['value'], 'text': '你好'})
                self.assertEqual(response.status, status)
                self.assertEqual(await response.json(), {'error': '尚未确认；没有自动重试。'})
                send.assert_awaited_once()

    async def test_manager_proposal_does_not_write_until_explicit_confirmation(self):
        fields = await self.form()
        action = {'kind': 'toggle', 'flag': 'image', 'label': '画图', 'enabled': False}
        with patch.object(local_chat, 'send_chat', new=AsyncMock(return_value={
                'text': '要先停一停画图吗？', 'action': action})):
            response = await self.client.post('/companion/chat', headers=self.auth(),
                data={'csrf': fields['csrf']['value'], 'text': '先别画图了'})
        self.assertEqual(response.status, 200)
        self.assertEqual((await response.json())['action'], action)
        self.assertFalse(self.file.exists())
        response = await self.client.post('/capabilities/toggle', headers=self.auth(),
            data={'csrf': fields['csrf']['value'], 'flag': 'image', 'enabled': 'false'},
            allow_redirects=False)
        self.assertEqual(response.status, 302)
        from regular.capabilities import enabled
        self.assertFalse(enabled(self.cfg, 'image'))
        self.runner.start.assert_not_called()
        self.runner.stop.assert_not_awaited()

    async def test_status_is_read_only_and_exposes_only_readiness_and_qq_connection(self):
        before = {name: sys.modules.get(name) for name in ('bot', 'runtime')}
        with patch.object(local_chat, 'bridge_status', return_value={'available': False, 'reason': '请更新机器人后由你启动。', 'pid': 123, 'token': 'PRIVATE', 'path': '/private/config'}):
            response = await self.client.get('/companion/status', headers=self.auth())
        self.assertEqual(await response.json(), {'available': False, 'reason': '请更新机器人后由你启动。', 'qq_connected': False})
        self.assertEqual(response.headers['X-Regular-Panel'], '1')
        self.assertIn("connect-src 'self'", response.headers['Content-Security-Policy'])
        for name, module in before.items():
            self.assertIs(sys.modules.get(name), module)
        self.runner.start.assert_not_called()

    async def test_native_session_requires_token_and_reuses_the_existing_csrf_without_chat(self):
        self.assertEqual((await self.client.get('/companion/session')).status, 401)
        self.assertEqual((await self.client.get('/companion/session', headers={panel.TOKEN_HEADER: 'wrong'})).status, 401)
        fields = await self.form()
        before = {name: sys.modules.get(name) for name in ('bot', 'runtime')}
        with patch.object(local_chat, 'bridge_status', return_value={'available': True, 'reason': '', 'token': 'PRIVATE', 'pid': 123}), patch.object(local_chat, 'send_chat', new=AsyncMock(return_value={'text': '你好'})) as send:
            response = await self.client.get('/companion/session', headers=self.auth())
            session = await response.json()
            self.assertEqual(session, {'csrf': fields['csrf']['value'], 'available': True, 'reason': '', 'qq_connected': False})
            self.assertEqual(response.headers['Cache-Control'], 'no-store')
            self.assertEqual(response.headers['X-Regular-Panel'], '1')
            send.assert_not_awaited()
            response = await self.client.post('/companion/chat', headers=self.auth(), data={'csrf': session['csrf'], 'text': '你好'})
            self.assertEqual(response.status, 200)
            send.assert_awaited_once_with(self.cfg, '你好')
        for name, module in before.items():
            self.assertIs(sys.modules.get(name), module)
        self.runner.start.assert_not_called()
        self.runner.stop.assert_not_awaited()
        self.assertEqual((await self.client.post('/companion/session', headers=self.auth())).status, 405)

    async def test_pet_view_is_compact_and_does_not_start_or_send_anything(self):
        with patch.object(local_chat, 'send_chat', new=AsyncMock()) as send:
            response = await self.client.get('/companion?mode=pet', headers=self.auth())
            text = await response.text()
            self.assertIn('class="pet-mode"', text)
            self.assertIn('data-chat-form', text)
            self.assertIn('maxlength="2000"', text)
            self.assertNotIn('data-companion-dock', text)
            self.assertNotIn(str(self.root), text)
            send.assert_not_awaited()
        self.runner.start.assert_not_called()

    async def test_static_poses_are_an_explicit_allowlist_and_gifs_are_not_served(self):
        with patch.object(panel, 'files') as resources:
            asset = resources.return_value.joinpath.return_value
            asset.is_file.return_value = True
            asset.read_bytes.return_value = b'\x89PNG\r\n\x1a\n synthetic test asset'
            response = await self.client.get('/assets/mascot-thinking.png', headers=self.auth())
            self.assertEqual(response.status, 200)
            self.assertEqual(response.content_type, 'image/png')
            self.assertTrue((await response.read()).startswith(b'\x89PNG'))
            for name in ('mascot-idle.gif', 'mascot-thinking.gif', 'mascot-unknown.png', '.env', 'README.md'):
                self.assertEqual((await self.client.get('/assets/' + name, headers=self.auth())).status, 404)
            asset.read_bytes.assert_called_once()
        response = await self.client.get('/companion', headers=self.auth())
        text = await response.text()
        self.assertIn('src="/assets/app-icon.png"', text)
        self.assertIn('data-pet-sprite', text)
        original = await self.client.get('/assets/mascot.png', headers=self.auth())
        self.assertEqual(original.status, 200)
        self.assertTrue((await original.read()).startswith(b'\x89PNG'))

    async def test_illustration_resources_remain_authenticated_but_settings_stay_compact(self):
        for state, route in (("welcome", "/setup"), ("knowledge", "/knowledge")):
            asset = "/assets/mascot-" + state + ".png"
            with self.subTest(state=state):
                self.assertEqual((await self.client.get(asset)).status, 401)
                response = await self.client.get(asset, headers=self.auth())
                self.assertEqual(response.status, 200)
                self.assertEqual(response.content_type, "image/png")
                page = await (await self.client.get(route, headers=self.auth())).text()
                self.assertNotIn('src="' + asset + '"', page)
                self.assertIn('src="/assets/app-icon.png"', page)
        # The checkup starts with candidates; its illustration stays available as a local asset.
        self.assertEqual((await self.client.get('/assets/mascot-checkup.png', headers=self.auth())).status, 200)
        checkup = await (await self.client.get('/checkup', headers=self.auth())).text()
        self.assertIn('aria-label="待看记录"', checkup)
        self.assertNotIn('src="/assets/mascot-checkup.png"', checkup)


class PanelPetPresenceTest(unittest.IsolatedAsyncioTestCase):
    """The pet reads content-free agent state; coding agents may cue it, nothing reaches QQ."""
    asyncSetUp = onboarding.PanelOnboardingTest.asyncSetUp
    asyncTearDown = onboarding.PanelOnboardingTest.asyncTearDown
    auth = onboarding.PanelOnboardingTest.auth
    form = PanelCompanionTest.form

    async def test_presence_needs_the_token_and_returns_only_coarse_state(self):
        self.assertEqual((await self.client.get('/companion/presence')).status, 401)
        snapshot = {'bot': 'ready', 'gateway_connected': True, 'mood': 0.4, 'circadian': '', 'chatter': 2,
                    'events': [{'kind': 'said', 'ts': 1.0}], 'now': 2.0}
        with patch('regular.presence.snapshot', return_value=snapshot) as read, \
             patch.object(local_chat, 'bridge_status', return_value={'available': True, 'reason': '', 'token': 'PRIVATE'}), \
             patch.object(local_chat, 'send_chat', new=AsyncMock()) as send:
            response = await self.client.get('/companion/presence?since=12.5', headers=self.auth())
            self.assertEqual(response.status, 200)
            body = await response.json()
            self.assertEqual(body, {'available': True, 'reason': '', 'qq_connected': False, **snapshot})
            self.assertEqual(read.call_args.kwargs['since'], 12.5)
            response = await self.client.get('/companion/presence?since=nope', headers=self.auth())
            self.assertEqual(response.status, 200)
            send.assert_not_awaited()
        self.runner.start.assert_not_called()

    async def test_cue_requires_csrf_validates_and_shows_up_in_presence(self):
        fields = await self.form()
        csrf = fields['csrf']['value']
        self.assertEqual((await self.client.post('/companion/cue', data={'action': 'cheer'})).status, 401)
        self.assertEqual((await self.client.post('/companion/cue', headers=self.auth(),
                                                 data={'csrf': 'wrong', 'action': 'cheer'})).status, 400)
        for data in ({'action': 'shell'}, {}, {'text': '长' * 121}, {'action': 'fall'}):
            with self.subTest(data=data):
                response = await self.client.post('/companion/cue', headers=self.auth(), data={'csrf': csrf, **data})
                self.assertEqual(response.status, 400)
        with patch.object(local_chat, 'send_chat', new=AsyncMock()) as send:
            response = await self.client.post('/companion/cue', headers=self.auth(),
                                              data={'csrf': csrf, 'action': 'cheer', 'text': '  测试\n全过了  '})
            self.assertEqual(response.status, 200)
            queued = (await response.json())['queued']
            send.assert_not_awaited()
        body = await (await self.client.get('/companion/presence?since=0', headers=self.auth())).json()
        cue = [e for e in body['events'] if e.get('kind') == 'cue']
        self.assertEqual([(c['id'], c['action'], c['text']) for c in cue], [(queued, 'cheer', '测试 全过了')])

    async def test_pet_sprites_are_served_by_allowlisted_type_only(self):
        self.assertEqual((await self.client.get('/assets/pet/idle.webp')).status, 401)
        response = await self.client.get('/assets/pet/idle.webp', headers=self.auth())
        self.assertEqual(response.status, 200)
        self.assertEqual(response.content_type, 'image/webp')
        self.assertEqual((await response.read())[8:12], b'WEBP')
        manifest = await self.client.get('/assets/pet/sprites.json', headers=self.auth())
        self.assertEqual((await manifest.json())['schema'], 2)
        for name in ('missing.webp', 'sprites.py', '..%2Fpanel.css', '%2E%2E%2F%2E%2E%2Fpanel.py'):
            with self.subTest(name=name):
                self.assertEqual((await self.client.get('/assets/pet/' + name, headers=self.auth())).status, 404)
