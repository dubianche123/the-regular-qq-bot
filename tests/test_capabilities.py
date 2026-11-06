"""Capability configuration and live gates: no provider, QQ, or real user data."""
import json
import os
from pathlib import Path
import stat
import tempfile
from unittest import TestCase
from unittest.mock import patch

from dotenv import dotenv_values

from regular import capabilities as caps
from test_instance_settings import configuration


class CapabilitiesTest(TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.cfg = configuration(self.root, SSL_VERIFY=True)
        self.file = self.root / '.env'

    def test_empty_view_is_read_only_secret_free_and_does_not_fake_dependencies(self):
        view = caps.public_settings(self.cfg)
        self.assertFalse(view['voice_output']['configured'])
        self.assertFalse(view['voice_output']['dependencies']['node'])
        self.assertEqual(view['image']['key_source'], 'none')
        self.assertFalse(self.file.exists())
        self.assertFalse((self.root / 'data').exists())

    def test_section_save_only_changes_that_section_and_hot_flags_do_not_need_new_config(self):
        self.file.write_text('# preserve\nAI_PROVIDER=gemini\nCHAT_PROVIDER=gemini\nVOICE_OUTPUT_ENABLED=true\n')
        self.assertTrue(caps.enabled(self.cfg, 'image'))
        view = caps.save_settings(self.cfg, {'section': 'image', 'model': 'new-image',
                                           'api_key': 'dedicated-secret', 'daily_limit': '12'})
        # Saving a model never flips a switch: switches are saved on their own (set_enabled).
        self.assertTrue(caps.enabled(self.cfg, 'image'))
        self.assertTrue(caps.enabled(self.cfg, 'gif'))
        self.assertTrue(caps.enabled(self.cfg, 'voice_output'))
        caps.set_enabled(self.cfg, 'image', False)
        caps.set_enabled(self.cfg, 'gif', 'false')
        self.assertFalse(caps.enabled(self.cfg, 'image'))
        self.assertFalse(caps.enabled(self.cfg, 'gif'))
        with self.assertRaises(ValueError):
            caps.set_enabled(self.cfg, 'chat', True)
        saved = dotenv_values(self.file)
        self.assertEqual(saved['CHAT_PROVIDER'], 'gemini')
        self.assertEqual(saved['AI_PROVIDER'], 'gemini')
        self.assertEqual(saved['IMAGE_GENERATION_MODEL'], 'new-image')
        self.assertIn('# preserve', self.file.read_text())
        self.assertEqual(stat.S_IMODE(self.file.stat().st_mode), 0o600)
        self.assertNotIn('dedicated-secret', json.dumps(view))
        caps.save_settings(self.cfg, {'section': 'image', 'enabled': 'on', 'gif_enabled': 'on', 'api_key': ''})
        self.assertTrue(caps.enabled(self.cfg, 'image'))
        self.assertTrue(caps.enabled(self.cfg, 'gif'))
        self.assertEqual(dotenv_values(self.file)['IMAGE_GENERATION_API_KEY'], 'dedicated-secret')

    def test_flags_respect_external_overrides_and_never_echo_values(self):
        self.cfg.EXTERNAL_ENV_KEYS = frozenset({'VOICE_OUTPUT_ENABLED', 'VOICE_API_KEY'})
        with patch.dict(os.environ, {'VOICE_OUTPUT_ENABLED': 'false', 'VOICE_API_KEY': 'external-secret'}):
            view = caps.save_settings(self.cfg, {'section': 'voice_output', 'enabled': 'on'})
            self.assertFalse(caps.enabled(self.cfg, 'voice_output'))
        self.assertIn('VOICE_OUTPUT_ENABLED', view['voice_output']['overridden'])
        self.assertNotIn('external-secret', json.dumps(view))

    def test_bad_switch_values_never_silently_disable_a_feature(self):
        self.file.write_text('IMAGE_GENERATION_ENABLED=true\n')
        before = self.file.read_bytes()
        for value in (None, '', 'banana', 0, 1, [], {}):
            with self.subTest(value=repr(value)), self.assertRaises(ValueError):
                caps.set_enabled(self.cfg, 'image', value)
            self.assertEqual(self.file.read_bytes(), before)
        with self.assertRaises(ValueError):
            caps.save_settings(self.cfg, {'section': 'image', 'enabled': ''})
        self.assertEqual(self.file.read_bytes(), before)
        for value in ('on', 'true', '1', True):
            self.assertTrue(caps.set_enabled(self.cfg, 'image', value)['image']['enabled'])
        for value in ('off', 'false', '0', False):
            self.assertFalse(caps.set_enabled(self.cfg, 'image', value)['image']['enabled'])

    def test_immediate_switch_rejects_an_opposite_startup_override_without_writing(self):
        self.file.write_text('IMAGE_GENERATION_ENABLED=true\nKEEP=1\n')
        before = self.file.read_bytes()
        self.cfg.EXTERNAL_ENV_KEYS = frozenset({'IMAGE_GENERATION_ENABLED'})
        with patch.dict(os.environ, {'IMAGE_GENERATION_ENABLED': 'true'}):
            with self.assertRaisesRegex(ValueError, '启动环境覆盖'):
                caps.set_enabled(self.cfg, 'image', False)
        self.assertEqual(self.file.read_bytes(), before)

    def test_image_key_is_not_inherited_across_endpoints_or_silently_moved(self):
        self.file.write_text('OPENAI_BASE_URL=https://api.openai.com/v1\nOPENAI_API_KEY=official-secret\n'
                             'IMAGE_GENERATION_BASE_URL=https://other.invalid/v1\n')
        self.assertFalse(caps.public_settings(self.cfg)['image']['key_configured'])
        caps.save_settings(self.cfg, {'section': 'image', 'enabled': 'on', 'api_key': 'other-secret'})
        before = self.file.read_bytes()
        with self.assertRaises(ValueError):
            caps.save_settings(self.cfg, {'section': 'image', 'base_url': 'https://third.invalid/v1', 'api_key': ''})
        self.assertEqual(self.file.read_bytes(), before)
        caps.save_settings(self.cfg, {'section': 'image', 'base_url': 'https://third.invalid/v1', 'api_key': 'third-secret'})
        self.assertEqual(dotenv_values(self.file)['IMAGE_GENERATION_API_KEY'], 'third-secret')

    def test_shared_voice_service_is_saved_once_without_touching_switches(self):
        self.file.write_text('VOICE_INPUT_ENABLED=true\n')
        view = caps.save_settings(self.cfg, {'section': 'voice', 'voice_api_key': 'audio-secret'})
        self.assertTrue(view['voice_input']['key_configured'] and view['voice_output']['key_configured'])
        self.assertTrue(caps.enabled(self.cfg, 'voice_input'))
        self.assertNotIn('audio-secret', json.dumps(view))
        with self.assertRaises(ValueError):
            caps.save_settings(self.cfg, {'section': 'voice', 'base_url': 'https://gateway.invalid/v1'})

    def test_audio_key_is_separate_from_gateway_chat_and_shared_by_input_output(self):
        self.file.write_text('OPENAI_BASE_URL=https://gateway.invalid/v1\nOPENAI_API_KEY=gateway-secret\n')
        self.assertFalse(caps.public_settings(self.cfg)['voice_input']['key_configured'])
        view = caps.save_settings(self.cfg, {'section': 'voice_input', 'enabled': 'on',
                                            'transcription_enabled': 'on', 'voice_api_key': 'audio-secret',
                                            'base_url': 'https://api.openai.com/v1', 'model': 'transcribe-chosen'})
        self.assertTrue(view['voice_output']['key_configured'])
        self.assertEqual(view['voice_input']['key_source'], 'dedicated')
        self.assertNotIn('audio-secret', json.dumps(view))
        self.assertEqual(dotenv_values(self.file)['OPENAI_API_KEY'], 'gateway-secret')

    def test_validation_is_atomic_and_does_not_echo_secrets(self):
        self.file.write_text('KEEP=1\n')
        for form in ({'section': 'unknown'},
                     {'section': 'image', 'daily_limit': '-1', 'api_key': 'PRIVATE'},
                     {'section': 'image', 'base_url': 'https://user:PRIVATE@bad.invalid/v1'},
                     {'section': 'voice_output', 'node': 'relative/node'},
                     {'section': 'voice_output', 'base_url': 'https://bad.invalid/v1'},
                     {'section': 'voice_input', 'model': 'x\nAPI_KEY=PRIVATE'}):
            with self.subTest(form=next(iter(form.items()))):
                before = self.file.read_bytes()
                with self.assertRaises(ValueError) as error:
                    caps.save_settings(self.cfg, form)
                self.assertNotIn('PRIVATE', str(error.exception))
                self.assertEqual(self.file.read_bytes(), before)

    def test_dependency_checks_require_real_absolute_executable_and_codec_file(self):
        node, silk = self.root / 'node', self.root / 'silk.cjs'
        node.write_text(''); silk.write_text('')
        with patch.object(caps.os, 'access', return_value=False):
            self.assertFalse(caps.dependencies(str(node), str(silk))['node'])
        with patch.object(caps.os, 'access', return_value=True):
            self.assertEqual(caps.dependencies(str(node), str(silk)), {'node': True, 'silk': True})
        self.assertFalse(caps.dependencies('node', 'silk.cjs')['silk'])

    def test_runtime_attestation_reports_loaded_models_and_live_switches_without_secrets(self):
        self.cfg.VISION_MODEL = 'loaded-vision'
        self.cfg.IMAGE_GENERATION_MODEL = 'loaded-image'
        self.cfg.VOICE_API_KEY = 'SECRET'
        self.file.write_text('IMAGE_GENERATION_ENABLED=false\nVOICE_OUTPUT_ENABLED=true\n')
        status = caps.runtime_status(self.cfg)
        self.assertEqual(status['live_flags'], 1)
        self.assertFalse(status['enabled']['image'])
        self.assertTrue(status['enabled']['voice_output'])
        self.assertEqual(status['models']['image'], 'loaded-image')
        self.assertNotIn('SECRET', json.dumps(status))

    def test_qq_reference_only_mode_needs_no_model_credential(self):
        self.file.write_text('VOICE_INPUT_ENABLED=true\nVOICE_TRANSCRIPTION_ENABLED=false\n')
        view = caps.public_settings(self.cfg)['voice_input']
        self.assertTrue(view['configured'])
        self.assertFalse(view['key_configured'])
        self.assertEqual(view['missing'], [])
        self.assertTrue(view['transcription_missing'])

    def test_corrupt_saved_numbers_can_be_repaired_and_password_urls_are_not_reflected(self):
        self.file.write_text('IMAGE_GENERATION_DAILY_CAP=bad\nVISION_DAILY_CAP=-1\nVOICE_OUTPUT_DAILY_LIMIT=oops\n'
                             'IMAGE_GENERATION_BASE_URL=https://user:SECRET@invalid/v1\n')
        view = caps.public_settings(self.cfg)
        self.assertEqual(view['image']['daily_limit'], 40)
        self.assertEqual(view['vision']['daily_limit'], 30)
        self.assertNotIn('SECRET', json.dumps(view))


class ManageCommandTest(TestCase):
    """Coding agents read and flip the same switches the maintainer sees, as JSON."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.cfg = configuration(self.root, SSL_VERIFY=True, INTERACTION_LOG_DIR=str(self.root / "data/interactions"))

    def run_manage(self, *argv):
        import argparse
        import contextlib
        import io
        from regular import cli
        args = argparse.Namespace(verb=argv[0] if argv else "show", flag=argv[1] if len(argv) > 1 else "",
                                  value=argv[2] if len(argv) > 2 else None)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(cli.manage_command(self.cfg, args, argparse.ArgumentParser()), 0)
        return json.loads(out.getvalue())

    def test_show_and_set_speak_the_page_language_without_secrets(self):
        (self.root / ".env").write_text("IMAGE_GENERATION_API_KEY=private-image-secret\n")
        view = self.run_manage()
        self.assertTrue(view["flags"]["image"])
        self.assertEqual(view["states"]["image"]["tone"], "ok")
        self.assertIn("已配置", view["states"]["image"]["text"])
        self.assertNotIn("能用", view["states"]["image"]["text"])
        self.assertIn("本机还没有收到群消息记录", view["today_text"])
        view = self.run_manage("set", "image", "off")
        self.assertFalse(view["flags"]["image"])
        self.assertEqual(view["states"]["image"], {"tone": "off", "text": "已关闭"})
        self.assertFalse(caps.enabled(self.cfg, "image"))
        self.assertNotIn("private-image-secret", json.dumps(view))
        with self.assertRaises(SystemExit):
            self.run_manage("set", "chat", "off")

    def test_invalid_manage_arguments_do_not_write_configuration(self):
        for args in (("set", "image", "banana"), ("show", "image", "off"),
                     ("show", "image"), ("set", "image")):
            with self.subTest(args=args), self.assertRaises(SystemExit):
                self.run_manage(*args)
            self.assertFalse((self.root / '.env').exists())
