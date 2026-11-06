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
            # four capability forms + the voice service shared by hearing and speaking (one Key field, once)
            self.assertEqual(sorted(f['fields']['section']['value'] for f in settings),
                             ['image', 'vision', 'voice', 'voice_input', 'voice_output'])
            self.assertEqual(text.count('name="voice_api_key"'), 1)
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
        self.assertIn('id="chat" data-tone="ok"><details class="cap-more">', text)
        self.assertNotIn('private-chat-secret', text)
        self.assertNotIn('SDK', text)
        self.assertNotIn('/chat/completions', text)

    async def test_unconfigured_chat_opens_only_its_required_connection(self):
        text, _ = await self.form('image')
        self.assertIn('id="chat" data-tone="todo"><details class="cap-more" open>', text)
        self.assertIn('id="decision" data-tone="todo"><details class="cap-more">', text)
        self.assertNotIn('data-custom-model-field hidden', text)

    async def test_judgment_offers_chat_key_without_rendering_it_and_then_defaults_to_saved(self):
        self.file.write_text('CHAT_PROVIDER=openai\nOPENAI_BASE_URL=https://api.openai.com/v1\n'
                             'OPENAI_MODELS=first\nOPENAI_API_KEY=private-chat-secret\n')
        text, _ = await self.form('image')
        self.assertIn('value="chat" data-decision-option="chat" selected', text)
        self.assertNotIn('value="existing" data-decision-option', text)
        self.assertIn('data-decision-key-field hidden', text)
        self.assertNotIn('private-chat-secret', text)
        panel.decision_settings.save_settings(self.cfg.DATA_DIR, endpoint='https://api.openai.com/v1/decisions',
                                              model='judge', api_key='private-saved-secret')
        text, _ = await self.form('image')
        self.assertIn('value="existing" data-decision-option="existing" selected', text)
        self.assertNotIn('private-saved-secret', text)

    async def test_other_services_key_is_not_selectable_for_judgment(self):
        self.file.write_text('CHAT_PROVIDER=openai\nOPENAI_BASE_URL=https://other.example/v1\n'
                             'OPENAI_MODELS=first\nOPENAI_API_KEY=private-chat-secret\n')
        text, _ = await self.form('image')
        self.assertIn('value="chat" data-decision-option="chat" disabled', text)
        self.assertIn('value="new" selected', text)
        self.assertNotIn('data-decision-key-field hidden', text)

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

    async def test_service_secrets_never_appear_in_html_and_blank_preserves_them(self):
        self.file.write_text("IMAGE_GENERATION_API_KEY=private-image-secret\nVOICE_API_KEY=private-voice-secret\n")
        response = await self.save('image', api_key='')
        self.assertEqual(response.status, 302)
        text, fields = await self.form('image')
        self.assertEqual(fields['api_key']['value'], '')
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
