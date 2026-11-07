"""Capabilities are explicit local configuration, independent of chat credentials."""
from html.parser import HTMLParser
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch
import unittest

from dotenv import dotenv_values

import panel
from regular import capabilities, local_chat
import test_panel_onboarding as onboarding


class Forms(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.forms, self.current = [], None
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        row = dict(attrs)
        if tag == "form":
            self.current = {"action": row.get("action"), "fields": {}}
            self.forms.append(self.current)
        elif self.current is not None and tag == "input" and row.get("name"):
            self.current["fields"][row["name"]] = row

    def handle_endtag(self, tag):
        if tag == "form":
            self.current = None


class CapabilitiesPageTest(unittest.IsolatedAsyncioTestCase):
    asyncTearDown = onboarding.PanelOnboardingTest.asyncTearDown
    auth = onboarding.PanelOnboardingTest.auth

    async def asyncSetUp(self):
        await onboarding.PanelOnboardingTest.asyncSetUp(self)
        self.root.mkdir(parents=True, exist_ok=True)

    async def form(self, section):
        response = await self.client.get('/capabilities', headers=self.auth())
        self.assertEqual(response.status, 200)
        text = await response.text()
        forms = Forms(text).forms
        return text, next(row['fields'] for row in forms
                          if row['fields'].get('section', {}).get('value') == section)

    async def save(self, section, **values):
        _, fields = await self.form(section)
        return await self.client.post('/capabilities/save', headers=self.auth(),
            data={'section': section, 'csrf': fields['csrf']['value'], **values}, allow_redirects=False)

    async def test_four_services_have_independent_forms_without_starting_or_calling(self):
        with patch.object(local_chat, 'send_chat', new=AsyncMock()) as chat:
            text, _ = await self.form('image')
            for label in ('读图', '画图', '听语音', '发语音', '转写模型', '音色', '语音服务'):
                self.assertIn(label, text)
            forms = Forms(text).forms
            settings = [f for f in forms if f['action'] == '/capabilities/save']
            switches = [f for f in forms if f['action'] == '/capabilities/toggle']
            # four capability forms + the voice service shared by hearing and speaking
            self.assertEqual(sorted(f['fields']['section']['value'] for f in settings),
                             ['image', 'vision', 'voice', 'voice_input', 'voice_output'])
            # Shared OpenAI credentials can be edited without changing chat providers.
            self.assertIn('action="/models/openai"', text)
            self.assertEqual(text.count('name="api_key"'), 2)
            self.assertNotIn('name="voice_api_key"', text)
            self.assertNotIn('data-decision-credential', text)
            self.assertIn('cap-key-card', text)
            self.assertEqual(sorted(f['fields']['flag']['value'] for f in switches),
                             sorted(capabilities.FLAGS))
            self.assertIn('保存后下次启动时使用', text)
            self.assertIn('<h1>模型与能力</h1>', text)
            self.assertIn('action="/models/save"', text)
            self.assertIn('action="/decision/save"', text)
            self.assertIn('<select id="chat-model-choice" name="model_choice" data-model-choice>', text)
            self.assertNotIn('<datalist', text)
            self.assertIn('src="/assets/app-icon.png"', text)
            self.assertIn('rel="icon"', text)
            self.assertNotIn('brand-mascot', text)
            chat.assert_not_awaited()
        self.assertFalse(self.file.exists())
        self.runner.start.assert_not_called()
        self.runner.stop.assert_not_awaited()

    async def test_auth_csrf_and_post_required_before_configuration_write(self):
        _, fields = await self.form('image')
        self.assertEqual((await self.client.get('/capabilities')).status, 401)
        self.assertEqual((await self.client.get('/capabilities/save', headers=self.auth())).status, 405)
        self.assertEqual((await self.client.post('/capabilities/save', data={'section': 'image', 'csrf': fields['csrf']['value']})).status, 401)
        for csrf in ('', 'wrong'):
            self.assertEqual((await self.client.post('/capabilities/save', headers=self.auth(), data={'section': 'image', 'csrf': csrf})).status, 400)
        self.assertFalse(self.file.exists())

    async def test_configured_models_are_summary_rows_with_a_real_choice(self):
        self.file.write_text('CHAT_PROVIDER=openai\nOPENAI_BASE_URL=https://model.example/v1\n'
                             'OPENAI_MODELS=first,second\nOPENAI_API_KEY=private-chat-secret\n')
        response = await self.client.get('/capabilities', headers=self.auth())
        text = await response.text()
        self.assertIn('<option value="first" selected>first</option>', text)
        self.assertIn('<option value="second">second</option>', text)
        self.assertIn('<option value="__custom__">填写其他模型…</option>', text)
        self.assertIn('data-custom-model-field hidden', text)
        self.assertIn('id="chat" data-tone="ok"><details class="cap-more" name="cap">', text)
        self.assertNotIn('private-chat-secret', text)
        self.assertNotIn('SDK', text)
        self.assertNotIn('/chat/completions', text)

    async def test_official_starter_opens_only_the_shared_key_connection(self):
        self.cfg.PROVIDER_PRESETS['openai'] = {
            'base_url': 'https://api.openai.com/v1',
            'models': ['gpt-6-luna', 'gpt-6.1-terra']}
        text, _ = await self.form('image')
        key_card = text[text.index('<details class="cap-key-card"'):].split('<p class="cap-runtime"')[0]
        self.assertIn('id="openai" data-tone="todo" open>', key_card)
        self.assertIn('id="chat" data-tone="todo"><details class="cap-more" name="cap">', text)
        self.assertIn('id="decision" data-tone="todo"><details class="cap-more" name="cap">', text)
        self.assertIn('data-custom-model-field hidden', text)
        self.assertIn('默认模型已配好', key_card)
        self.assertIn('<details class="cap-advanced"><summary>接口地址</summary>', key_card)

    async def test_unconfigured_compatible_service_still_asks_for_its_own_model(self):
        self.file.write_text('CHAT_PROVIDER=openai\nOPENAI_BASE_URL=https://gateway.example/v1\n')
        text, _ = await self.form('image')
        self.assertIn('id="chat" data-tone="todo"><details class="cap-more" name="cap" open>', text)
        self.assertNotIn('data-custom-model-field hidden', text)
        key_card = text[text.index('id="openai"'):].split('<p class="cap-runtime"')[0]
        self.assertNotIn('默认模型已配好', key_card)

    async def test_judgment_reuses_openai_connection_without_its_own_key_input(self):
        self.file.write_text('CHAT_PROVIDER=openai\nOPENAI_BASE_URL=https://api.openai.com/v1\n'
                             'OPENAI_MODELS=first\nOPENAI_API_KEY=private-chat-secret\n')
        text, _ = await self.form('image')
        self.assertIn('一把 OpenAI Key 已就位', text)
        self.assertIn('共用上方 OpenAI 连接的 Key', text)
        self.assertEqual(text.count('name="api_key"'), 2)
        self.assertIn('name="credential_source" value="shared"', text)
        self.assertNotIn('private-chat-secret', text)
        panel.decision_settings.save_settings(
            self.cfg.DATA_DIR, endpoint='https://api.openai.com/v1/decisions',
            model='judge', credential='shared')
        saved = json.loads((Path(self.cfg.DATA_DIR) / 'decision_api.json').read_text(encoding='utf-8'))
        self.assertEqual(saved, {'endpoint': 'https://api.openai.com/v1/decisions',
                                 'model': 'judge', 'credential': 'shared'})
        text, _ = await self.form('image')
        self.assertIn('judge', text)
        self.assertNotIn('private-chat-secret', text)

    async def test_other_services_endpoint_cannot_reuse_the_openai_key(self):
        self.file.write_text('CHAT_PROVIDER=openai\nOPENAI_BASE_URL=https://other.example/v1\n'
                             'OPENAI_MODELS=first\nOPENAI_API_KEY=private-chat-secret\n')
        text, _ = await self.form('image')
        self.assertIn('让判断接口使用同一地址', text)
        self.assertNotIn('private-chat-secret', text)
        # The credential resolver refuses to hand the key to a different host.
        self.assertEqual(capabilities.key_for_endpoint(
            self.cfg, 'https://api.openai.com/v1/decisions', suffix='/decisions'), '')

    async def toggle(self, flag, enabled, *, json_reply=True):
        _, fields = await self.form('image')
        headers = {**self.auth(), **({'Accept': 'application/json'} if json_reply else {})}
        return await self.client.post('/capabilities/toggle', headers=headers, allow_redirects=False,
                                      data={'flag': flag, 'enabled': enabled, 'csrf': fields['csrf']['value']})

    async def test_turning_off_image_is_live_and_preserves_chat_and_other_capabilities(self):
        self.file.write_text("# keep\nCHAT_PROVIDER=gemini\nGEMINI_API_KEY=private-chat-secret\nVOICE_OUTPUT_ENABLED=true\nIMAGE_GENERATION_ENABLED=true\nIMAGE_GENERATION_GIF_ENABLED=true\n")
        self.assertTrue(capabilities.enabled(self.cfg, 'image'))
        response = await self.save('image', model='kept-model')         # settings never flip a switch
        self.assertEqual(response.headers['Location'], '/capabilities?saved=image#image')
        self.assertTrue(capabilities.enabled(self.cfg, 'image'))
        response = await self.toggle('image', 'false')
        self.assertEqual(response.status, 200)
        body = await response.json()
        self.assertEqual((body['flags']['image'], body['states']['image']['tone']), (False, 'off'))
        self.assertTrue(body['flags']['gif'])                            # its own switch, untouched
        response = await self.toggle('gif', 'false', json_reply=False)
        self.assertEqual((response.status, response.headers['Location']), (302, '/capabilities#image'))
        for flag in ('chat', ''):
            self.assertEqual((await self.toggle(flag, 'true')).status, 400)
        saved = dotenv_values(self.file)
        self.assertFalse(capabilities.enabled(self.cfg, 'image'))
        self.assertFalse(capabilities.enabled(self.cfg, 'gif'))
        self.assertEqual(saved['CHAT_PROVIDER'], 'gemini')
        self.assertEqual(saved['GEMINI_API_KEY'], 'private-chat-secret')
        self.assertEqual(saved['VOICE_OUTPUT_ENABLED'], 'true')
        self.assertIn('# keep', self.file.read_text())
        self.runner.start.assert_not_called()

    async def test_shared_key_can_be_saved_without_switching_the_chat_provider(self):
        self.file.write_text('AI_PROVIDER=zhipu\nCHAT_PROVIDER=zhipu\nZHIPU_API_KEY=private-z-key\n'
                             'ZHIPU_MODELS=z-chat\n# keep\n')
        text, _ = await self.form('image')
        self.assertNotIn('一把 OpenAI Key 已就位', text)
        self.assertIn('连接 OpenAI', text)
        self.assertIn('还差OpenAI Key', text)
        self.assertNotIn('聊天服务的 OpenAI Key', text)
        form = next(f for f in Forms(text).forms if f['action'] == '/models/openai')
        response = await self.client.post('/models/openai', headers=self.auth(), allow_redirects=False,
            data={'csrf': form['fields']['csrf']['value'], 'api_key': 'private-openai-key',
                  'base_url': 'https://api.openai.com/v1', 'provider': 'openai', 'models': 'ignored'})
        self.assertEqual(response.status, 302)
        saved = dotenv_values(self.file)
        self.assertEqual(saved['CHAT_PROVIDER'], 'zhipu')
        self.assertEqual(saved['AI_PROVIDER'], 'zhipu')
        self.assertEqual(saved['ZHIPU_MODELS'], 'z-chat')
        self.assertNotIn('OPENAI_MODELS', saved)
        self.assertEqual(saved['OPENAI_API_KEY'], 'private-openai-key')
        self.assertIn('# keep', self.file.read_text())
        text = await (await self.client.get(response.headers['Location'], headers=self.auth())).text()
        self.assertIn('一把 OpenAI Key 已就位', text)
        self.assertIn('id="openai" data-tone="ok" open', text)
        self.assertNotIn('private-openai-key', text)
        # Saving the same connection with a blank key retains it.
        response = await self.client.post('/models/openai', headers=self.auth(), allow_redirects=False,
            data={'csrf': form['fields']['csrf']['value'], 'api_key': '', 'base_url': 'https://api.openai.com/v1'})
        self.assertEqual(response.status, 302)
        self.assertEqual(dotenv_values(self.file)['OPENAI_API_KEY'], 'private-openai-key')
        self.runner.start.assert_not_called()
        self.runner.stop.assert_not_awaited()

    async def test_switching_shared_connection_address_requires_a_fresh_key(self):
        self.file.write_text('CHAT_PROVIDER=zhipu\nOPENAI_BASE_URL=https://api.openai.com/v1\n'
                             'OPENAI_API_KEY=private-old-key\n')
        text, _ = await self.form('image')
        fields = next(f['fields'] for f in Forms(text).forms if f['action'] == '/models/openai')
        before = self.file.read_bytes()
        values = {'csrf': fields['csrf']['value'], 'base_url': 'https://new-service.example/v1', 'api_key': ''}
        response = await self.client.post('/models/openai', headers=self.auth(), data=values)
        self.assertEqual(response.status, 400)
        self.assertIn('同时填写新服务的 Key', await response.text())
        self.assertEqual(self.file.read_bytes(), before)
        # Host casing and a trailing slash do not change the service.
        response = await self.client.post('/models/openai', headers=self.auth(), allow_redirects=False,
            data={**values, 'base_url': 'https://API.OPENAI.COM/v1/'})
        self.assertEqual(response.status, 302)
        self.assertEqual(dotenv_values(self.file)['OPENAI_API_KEY'], 'private-old-key')
        response = await self.client.post('/models/openai', headers=self.auth(), allow_redirects=False,
            data={**values, 'api_key': 'private-new-key'})
        self.assertEqual(response.status, 302)
        saved = dotenv_values(self.file)
        self.assertEqual(saved['OPENAI_BASE_URL'], 'https://new-service.example/v1')
        self.assertEqual(saved['OPENAI_API_KEY'], 'private-new-key')
        self.assertEqual(saved['CHAT_PROVIDER'], 'zhipu')

    async def test_environment_managed_base_cannot_be_silently_overwritten_but_key_can_change(self):
        self.file.write_text('CHAT_PROVIDER=zhipu\nOPENAI_BASE_URL=https://old-file.example/v1\n'
                             'OPENAI_API_KEY=private-old-key\n')
        self.cfg.EXTERNAL_ENV_KEYS = frozenset({'OPENAI_BASE_URL'})
        with patch.dict('os.environ', {'OPENAI_BASE_URL': 'https://api.openai.com/v1'}):
            text, _ = await self.form('image')
            fields = next(f['fields'] for f in Forms(text).forms if f['action'] == '/models/openai')
            self.assertIn('disabled', fields['base_url'])
            self.assertEqual(fields['base_url']['value'], 'https://api.openai.com/v1')
            self.assertIn('地址由安装配置管理', text)
            before = self.file.read_bytes()
            values = {'csrf': fields['csrf']['value'], 'api_key': 'private-new-key'}
            response = await self.client.post('/models/openai', headers=self.auth(),
                data={**values, 'base_url': 'https://new-service.example/v1'})
            self.assertEqual(response.status, 400)
            self.assertIn('地址由安装配置管理', await response.text())
            self.assertEqual(self.file.read_bytes(), before)
            # The disabled address field is omitted by the browser; key-only saves work.
            response = await self.client.post('/models/openai', headers=self.auth(),
                data=values, allow_redirects=False)
            self.assertEqual(response.status, 302)
            saved = dotenv_values(self.file)
            self.assertEqual(saved['OPENAI_BASE_URL'], 'https://old-file.example/v1')
            self.assertEqual(saved['OPENAI_API_KEY'], 'private-new-key')

    async def test_openai_connection_does_not_claim_a_compatible_key_can_supply_official_voice(self):
        self.file.write_text('CHAT_PROVIDER=openai\nOPENAI_BASE_URL=https://gateway.example/v1\n'
                             'OPENAI_API_KEY=private-gateway-key\n')
        text, _ = await self.form('image')
        self.assertIn('OpenAI 兼容服务已连接', text)
        self.assertNotIn('一把 OpenAI Key 已就位', text)
        self.assertIn('实时语音需要 OpenAI 官方连接', text)
        self.assertNotIn('private-gateway-key', text)

    async def test_openai_connection_save_checks_auth_csrf_and_atomic_validation(self):
        text, _ = await self.form('image')
        form = next(f for f in Forms(text).forms if f['action'] == '/models/openai')
        values = {'csrf': form['fields']['csrf']['value'], 'api_key': 'private-key',
                  'base_url': 'https://api.openai.com/v1'}
        self.assertEqual((await self.client.post('/models/openai', data=values)).status, 401)
        self.assertEqual((await self.client.post('/models/openai', headers=self.auth(),
            data={**values, 'csrf': 'wrong'})).status, 400)
        response = await self.client.post('/models/openai', headers=self.auth(),
            data={**values, 'base_url': 'https://user:private-password@bad.example/v1'})
        self.assertEqual(response.status, 400)
        self.assertNotIn('private', await response.text())
        self.assertFalse(self.file.exists())

    async def test_shared_judgment_uses_openai_connection_when_chat_is_another_provider(self):
        self.file.write_text('CHAT_PROVIDER=zhipu\nZHIPU_API_KEY=private-z-key\n'
                             'OPENAI_BASE_URL=https://api.openai.com/v1\nOPENAI_API_KEY=private-openai-key\n')
        text, _ = await self.form('image')
        form = next(f for f in Forms(text).forms if f['action'] == '/decision/save')
        response = await self.client.post('/decision/save', headers=self.auth(), allow_redirects=False,
            data={'csrf': form['fields']['csrf']['value'], 'credential_source': 'shared',
                  'endpoint': 'https://api.openai.com/v1/decisions', 'model': 'judge'})
        self.assertEqual(response.status, 302)
        text, _ = await self.form('image')
        self.assertIn('id="decision" data-tone="ok"', text)
        self.assertIn('共用 OpenAI Key', text)
        before = (Path(self.cfg.DATA_DIR) / 'decision_api.json').read_bytes()
        response = await self.client.post('/decision/save', headers=self.auth(),
            data={'csrf': form['fields']['csrf']['value'], 'credential_source': 'shared',
                  'endpoint': 'https://unrelated.example/v1/decisions', 'model': 'judge'})
        self.assertEqual(response.status, 400)
        self.assertEqual((Path(self.cfg.DATA_DIR) / 'decision_api.json').read_bytes(), before)

    async def test_official_key_uses_default_judgment_without_an_extra_save(self):
        self.file.write_text('CHAT_PROVIDER=zhipu\nZHIPU_API_KEY=private-z-key\n'
                             'OPENAI_BASE_URL=https://api.openai.com/v1\nOPENAI_API_KEY=private-openai-key\n')
        text, _ = await self.form('image')
        decision = text[text.index('id="decision"'):].split('</article>')[0]
        self.assertIn('<span class="cap-state">gpt-6-luna</span>', decision)
        self.assertIn('共用 OpenAI Key', decision)
        self.assertIn('默认配置', decision)
        self.assertIn('已使用默认判断模型', decision)
        self.assertFalse((Path(self.cfg.DATA_DIR) / 'decision_api.json').exists())
        self.assertNotIn('还没连接', decision)
        panel.decision_settings.save_settings(self.cfg.DATA_DIR,
            endpoint='https://api.openai.com/v1/decisions', model='judge', credential='shared')
        text, _ = await self.form('image')
        decision = text[text.index('id="decision"'):].split('</article>')[0]
        self.assertIn('<span class="cap-state">judge</span>', decision)
        self.assertIn('共用 OpenAI Key', decision)
        self.assertNotIn('默认配置', decision)
        self.assertNotIn('待保存判断设置', decision)
        self.file.write_text(self.file.read_text() + 'DECISION_ENABLED=false\n')
        text, _ = await self.form('image')
        decision = text[text.index('id="decision"'):].split('</article>')[0]
        self.assertIn('data-tone="off"', decision)
        self.assertIn('<span class="cap-state">已关闭</span>', decision)
        self.assertNotIn('还没连接', decision)

    async def test_model_and_daily_quota_sit_together_without_opening_settings(self):
        text, _ = await self.form('image')
        image = text[text.index('id="image"'):]
        image = image[:image.index('</article>')]
        # Both are visible on the row itself, before 「设置」 is ever clicked.
        self.assertIn('class="cap-facts"', image)
        for label in ('模型', '每天', '每人'):
            self.assertIn(f'<small>{label}</small>', image)
        # And both are editable in the main part of the form, not hidden in 更多设置.
        main = image.split('<details class="cap-advanced">')[0]
        for name in ('model', 'daily_limit', 'user_daily_limit'):
            self.assertIn(f'name="{name}"', main)
        for label in ('每天最多画几张', '每人每天最多几张'):
            self.assertIn(label, main)

    async def test_service_secrets_never_appear_in_html_and_blank_preserves_them(self):
        self.file.write_text("IMAGE_GENERATION_API_KEY=private-image-secret\nVOICE_API_KEY=private-voice-secret\n")
        response = await self.save('image', api_key='')
        self.assertEqual(response.status, 302)
        text, fields = await self.form('image')
        self.assertNotIn('api_key', fields)
        self.assertNotIn('name="voice_api_key"', text)
        self.assertNotIn('private-image-secret', text)
        self.assertNotIn('private-voice-secret', text)
        self.assertEqual(dotenv_values(self.file)['IMAGE_GENERATION_API_KEY'], 'private-image-secret')

    async def test_invalid_fields_do_not_partially_save_or_echo_credentials(self):
        self.file.write_text('CHAT_PROVIDER=gemini\n')
        before = self.file.read_text()
        for values in ({'daily_limit': '-1'}, {'base_url': 'https://secret:password@images.example/v1'}):
            response = await self.save('image', enabled='true', **values)
            self.assertEqual(response.status, 400)
            self.assertNotIn('secret:password', await response.text())
            self.assertEqual(self.file.read_text(), before)

    async def test_app_icon_is_authenticated_and_served_as_a_real_png(self):
        self.assertEqual((await self.client.get('/assets/app-icon.png')).status, 401)
        response = await self.client.get('/assets/app-icon.png', headers=self.auth())
        self.assertEqual(response.status, 200)
        self.assertEqual(response.content_type, 'image/png')
        self.assertTrue((await response.read()).startswith(b'\x89PNG\r\n\x1a\n'))
        self.assertEqual((await self.client.get('/assets/unlisted.png', headers=self.auth())).status, 404)

    async def test_hot_controls_are_claimed_only_for_a_matching_capable_process(self):
        status = Path(self.cfg.STATUS_FILE)
        status.parent.mkdir(parents=True, exist_ok=True)
        self.managed.update(running=True, stale=False, pid=4321)
        status.write_text(json.dumps({'pid': 4321, 'schema_version': 1}))
        text, _ = await self.form('image')
        self.assertIn('机器人需要更新后才能使用这些开关', text)
        self.assertNotIn('当前使用', text)
        status.write_text(json.dumps({'pid': 4321, 'schema_version': 1,
            'capabilities': {'live_flags': 1, 'enabled': {'image': False}, 'models': {'image': 'running-image-model'}}}))
        text, _ = await self.form('image')
        self.assertIn('开关对新消息生效', text)
        self.assertIn('当前使用 running-image-model，重启后使用新设置', text)
        self.managed['stale'] = True
        text, _ = await self.form('image')
        self.assertNotIn('当前使用 running-image-model', text)
