"""本机向导只保存明确字段，不回显密钥或把旧 dotenv 当外部覆盖。"""
import json
import os
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from dotenv import dotenv_values

from regular import instance_settings as settings
import storage


def configuration(root, **overrides):
    data = dict(BASE_DIR=str(root), DATA_DIR=str(root / "data"),
                STATUS_FILE=str(root / "data/status.json"),
                AI_PROVIDER="openai", DAILY_BUDGET=3000,
                EXTERNAL_ENV_KEYS=frozenset(), DOTENV_KEYS_AT_IMPORT=frozenset(),
                PROVIDER_PRESETS={"openai": {"base_url": "", "models": []},
                                  "zhipu": {"base_url": "https://zhipu.example/v1", "models": ["z-model"]},
                                  "gemini": {"base_url": "https://gemini.example/v1", "models": ["g-model"]}})
    data.update(overrides)
    return SimpleNamespace(**data)


def complete_values(**overrides):
    values = {"provider": "openai", "qq_app_id": "synthetic-app", "qq_app_secret": "qq-private-secret",
              "bot_names": "阿团,团团", "base_url": "https://model.example/v1", "models": "chat-model",
              "api_key": "private-model-key", "daily_budget": "200"}
    values.update(overrides)
    return values


class InstanceSettingsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.cfg = configuration(self.root)
        self.file = self.root / ".env"

    def test_empty_install_is_explicitly_incomplete_without_creating_files(self):
        view = settings.public_settings(self.cfg)
        self.assertFalse(view["config_exists"])
        self.assertFalse(view["ready"])
        self.assertIn("QQ AppSecret", view["missing"])
        self.assertIn("回复模型", view["missing"])
        self.assertFalse(self.file.exists())
        self.assertFalse((self.root / "data").exists())

    def test_roundtrip_preserves_quotes_comments_and_uses_one_private_replace(self):
        self.file.write_text("# kept comment\nUNRELATED='unchanged'\n", encoding="utf-8")
        secret = "private #value with 'quote' and \\ path"
        with patch.object(storage, "atomic_write_text", wraps=storage.atomic_write_text) as write:
            view = settings.save_settings(self.cfg, complete_values(api_key=secret))
        write.assert_called_once()
        saved = dotenv_values(self.file, interpolate=False)
        self.assertEqual(saved["OPENAI_API_KEY"], secret)
        self.assertEqual(saved["UNRELATED"], "unchanged")
        self.assertIn("# kept comment", self.file.read_text())
        self.assertEqual(stat.S_IMODE(self.file.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE((self.root / "data").stat().st_mode), 0o700)
        self.assertTrue(view["ready"])
        self.assertTrue(view["key_configured"])
        public = json.dumps(view, ensure_ascii=False)
        self.assertNotIn(secret, public)
        self.assertNotIn("qq-private-secret", public)
        self.assertNotIn("api_key", view)
        self.assertNotIn("qq_app_secret", view)

    def test_empty_passwords_keep_secrets_and_partial_save_lists_remaining_fields(self):
        partial = settings.save_settings(self.cfg, {"provider": "openai", "qq_app_id": "app", "qq_app_secret": "saved-secret"})
        self.assertFalse(partial["ready"])
        self.assertTrue(partial["qq_secret_configured"])
        self.assertNotIn("QQ AppSecret", partial["missing"])
        settings.save_settings(self.cfg, complete_values(qq_app_secret="", api_key="saved-model-secret"))
        view = settings.save_settings(self.cfg, {"provider": "openai", "qq_app_secret": " ", "api_key": ""})
        saved = dotenv_values(self.file, interpolate=False)
        self.assertEqual(saved["QQ_APP_SECRET"], "saved-secret")
        self.assertEqual(saved["OPENAI_API_KEY"], "saved-model-secret")
        self.assertTrue(view["ready"])

    def test_changing_provider_does_not_reuse_the_previous_providers_key(self):
        settings.save_settings(self.cfg, complete_values())
        view = settings.save_settings(self.cfg, {"provider": "zhipu", "api_key": "", "csrf": "not-a-setting"})
        self.assertEqual(view["base_url"], "https://zhipu.example/v1")
        self.assertEqual(view["models"], "z-model")
        self.assertFalse(view["key_configured"])
        saved = dotenv_values(self.file, interpolate=False)
        self.assertEqual(saved["OPENAI_API_KEY"], "private-model-key")
        self.assertNotIn("ZHIPU_API_KEY", saved)
        self.assertNotIn("csrf", saved)
        self.assertEqual(saved["AI_PROVIDER"], "zhipu")

    def test_chat_choice_replaces_an_old_tier_override_and_orders_explicit_fallbacks(self):
        self.file.write_text("AI_PROVIDER=openai\nCHAT_PROVIDER=gemini\nGEMINI_MODELS=old-chat\nDIGEST_PROVIDER=gemini\n")
        self.assertEqual(settings.public_settings(self.cfg)["active_provider"], "gemini")
        view = settings.save_settings(self.cfg, complete_values(
            models="chosen-chat", fallback_models="backup-a, backup-b, chosen-chat, backup-a"))
        saved = dotenv_values(self.file)
        self.assertEqual(saved["CHAT_PROVIDER"], "openai")
        self.assertEqual(saved["OPENAI_MODELS"], "chosen-chat,backup-a,backup-b")
        self.assertEqual(saved["DIGEST_PROVIDER"], "gemini")
        self.assertEqual(view["primary_model"], "chosen-chat")
        self.assertEqual(view["fallback_models"], "backup-a, backup-b")

    def test_external_chat_choice_remains_explicitly_reported_after_save(self):
        cfg = configuration(self.root, EXTERNAL_ENV_KEYS=frozenset({"CHAT_PROVIDER"}))
        with patch.dict(os.environ, {"CHAT_PROVIDER": "gemini"}):
            view = settings.save_settings(cfg, complete_values())
        self.assertEqual(view["active_provider"], "gemini")
        self.assertIn("CHAT_PROVIDER", view["overridden"])
        self.assertEqual(dotenv_values(self.file)["CHAT_PROVIDER"], "openai")

    def test_structured_chat_choice_does_not_silently_promote_a_backup(self):
        for values in ({"models": "", "fallback_models": "backup"},
                       {"models": "first,second", "fallback_models": ""}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                settings.save_settings(self.cfg, complete_values(**values))
            self.assertFalse(self.file.exists())

    def test_provider_key_file_keeps_its_actual_precedence(self):
        (self.root / "api_keys_zhipu.txt").write_text("# note\nprivate-file-key\n", encoding="utf-8")
        settings.save_settings(self.cfg, complete_values(provider="zhipu", api_key="new-env-key"))
        view = settings.public_settings(self.cfg)
        self.assertEqual(view["key_source"], "file")
        self.assertTrue(view["key_configured"])
        self.assertNotIn("private-file-key", json.dumps(view))
        self.assertNotIn("new-env-key", json.dumps(view))

    def test_legacy_url_credentials_are_not_reflected_in_public_form(self):
        for endpoint in ("https://user:PRIVATE_URL_SECRET@model.example/v1",
                         "https://model.example/v1?api_key=PRIVATE_URL_SECRET"):
            with self.subTest(kind="userinfo" if "@" in endpoint else "query"):
                self.file.write_text(f"OPENAI_BASE_URL='{endpoint}'\n")
                view = settings.public_settings(self.cfg)
                self.assertEqual(view["base_url"], "")
                self.assertIn("模型接口地址", view["missing"])
                self.assertNotIn("PRIVATE_URL_SECRET", json.dumps(view))
                self.assertEqual(dotenv_values(self.file)["OPENAI_BASE_URL"], endpoint)

    def test_invalid_existing_provider_still_offers_a_repairable_form(self):
        self.file.write_text("AI_PROVIDER=invalid-private-value\n")
        view = settings.public_settings(self.cfg)
        self.assertEqual(view["provider"], "openai")
        self.assertFalse(view["ready"])
        self.assertIn("有效的模型供应商", view["missing"])
        self.assertNotIn("invalid-private-value", json.dumps(view))

    def test_invalid_input_never_partially_changes_file_or_echoes_secrets(self):
        settings.save_settings(self.cfg, complete_values())
        original = self.file.read_bytes()
        for change in ({"provider": "other"}, {"base_url": "https://user:PRIVATE_PASSWORD@model.example/v1"},
                       {"base_url": "file:///tmp/models"}, {"base_url": "https:///nohost"},
                       {"base_url": "https://model.example/v1?secret=PRIVATE_PASSWORD"},
                       {"daily_budget": "-1"}, {"daily_budget": "1.5"},
                       {"api_key": "PRIVATE_PASSWORD\nANOTHER=bad"}, {"models": "model\rmodel"}):
            with self.subTest(change=next(iter(change))):
                try:
                    settings.save_settings(self.cfg, complete_values(**change))
                except ValueError as error:
                    self.assertNotIn("PRIVATE_PASSWORD", str(error))
                else:
                    self.fail("expected invalid field rejection")
                self.assertEqual(self.file.read_bytes(), original)
        self.assertEqual(list(self.root.glob(".regular-settings-*")), [])

    def test_true_external_values_win_even_when_empty_and_are_named_without_values(self):
        cfg = configuration(self.root, EXTERNAL_ENV_KEYS=frozenset({"QQ_APP_ID", "QQ_APP_SECRET", "OPENAI_API_KEY"}))
        external = {"QQ_APP_ID": "external-app", "QQ_APP_SECRET": "", "OPENAI_API_KEY": "external-private-key"}
        with patch.dict(os.environ, external):
            view = settings.save_settings(cfg, complete_values())
        self.assertEqual(view["qq_app_id"], "external-app")
        self.assertFalse(view["qq_secret_configured"])
        self.assertIn("QQ AppSecret", view["missing"])
        self.assertEqual(view["key_source"], "environment")
        self.assertIn("QQ_APP_SECRET", view["overridden"])
        self.assertNotIn("external-private-key", json.dumps(view))
        self.assertEqual(dotenv_values(self.file)["QQ_APP_ID"], "synthetic-app")

    def test_child_removes_old_dotenv_values_but_keeps_real_environment(self):
        self.file.write_text("OPENAI_API_KEY='new-private-key'\nQQ_APP_SECRET='new-secret'\nSSL_VERIFY=true\n")
        cfg = configuration(self.root, EXTERNAL_ENV_KEYS=frozenset({"SSL_VERIFY", "EXPLICIT_EMPTY"}),
                            DOTENV_KEYS_AT_IMPORT=frozenset({"OPENAI_API_KEY", "QQ_APP_SECRET", "OLD_REMOVED_KEY"}))
        with patch.dict(os.environ, {"OPENAI_API_KEY": "stale-private-key", "QQ_APP_SECRET": "stale-secret",
                                     "OLD_REMOVED_KEY": "stale-value", "SSL_VERIFY": "false", "EXPLICIT_EMPTY": ""}):
            env = settings.child_environment(cfg)
        self.assertNotIn("OPENAI_API_KEY", env)
        self.assertNotIn("QQ_APP_SECRET", env)
        self.assertNotIn("OLD_REMOVED_KEY", env)
        self.assertEqual(env["SSL_VERIFY"], "false")
        self.assertEqual(env["EXPLICIT_EMPTY"], "")
        self.assertEqual(env["REGULAR_HOME"], str(self.root))
