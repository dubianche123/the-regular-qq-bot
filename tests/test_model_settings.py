"""A real model choice preserves the calling order and identity saves stay separate."""
from pathlib import Path
import json
import os
import tempfile
import unittest
from unittest.mock import patch

from dotenv import dotenv_values

from regular import instance_settings
from test_instance_settings import configuration, complete_values


class ModelSettingsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.cfg = configuration(self.root)
        instance_settings.save_settings(self.cfg, complete_values(models="first,second,third"))
        self.file = self.root / ".env"

    def test_promoting_a_saved_model_retains_the_old_primary(self):
        instance_settings.save_settings(self.cfg, {
            "provider": "openai", "model_choice": "second", "models": "first",
            "fallback_models": "second, third", "api_key": "",
        })
        saved = dotenv_values(self.file)
        self.assertEqual(saved["OPENAI_MODELS"], "second,first,third")
        self.assertEqual(saved["OPENAI_API_KEY"], "private-model-key")

    def test_explicit_backup_edit_is_respected(self):
        instance_settings.save_settings(self.cfg, {
            "provider": "openai", "model_choice": "second", "fallback_models": "third",
        })
        self.assertEqual(dotenv_values(self.file)["OPENAI_MODELS"], "second,third")

    def test_custom_model_is_explicit_and_validated_before_saving(self):
        instance_settings.save_settings(self.cfg, {
            "provider": "openai", "model_choice": "__custom__", "models": "new-model",
            "fallback_models": "first, second",
        })
        self.assertEqual(dotenv_values(self.file)["OPENAI_MODELS"], "new-model,first,second")
        before = self.file.read_bytes()
        for fields in ({"model_choice": "invented"},
                       {"model_choice": "__custom__", "models": ""},
                       {"model_choice": "__custom__", "models": "first,second"}):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                instance_settings.save_settings(self.cfg, {"provider": "openai", **fields})
            self.assertEqual(self.file.read_bytes(), before)

    def test_saving_qq_identity_never_changes_models_or_provider(self):
        before = dotenv_values(self.file)
        view = instance_settings.save_identity(self.cfg, {
            "qq_app_id": "next-app", "qq_app_secret": "", "bot_names": "新名字",
            "provider": "gemini", "models": "must-not-change", "api_key": "must-not-save",
        })
        saved = dotenv_values(self.file)
        self.assertEqual(saved["QQ_APP_ID"], "next-app")
        self.assertEqual(saved["BOT_NAMES"], "新名字")
        self.assertEqual(saved["QQ_APP_SECRET"], before["QQ_APP_SECRET"])
        self.assertEqual(saved["CHAT_PROVIDER"], before["CHAT_PROVIDER"])
        self.assertEqual(saved["OPENAI_MODELS"], before["OPENAI_MODELS"])
        self.assertEqual(saved["OPENAI_API_KEY"], before["OPENAI_API_KEY"])
        self.assertEqual(view["primary_model"], "first")

    def test_judgment_can_copy_the_same_services_chat_key_only(self):
        before = self.file.read_bytes()
        self.assertEqual(instance_settings.decision_key_from_chat(
            self.cfg, "https://model.example/v1/decisions"), "private-model-key")
        for endpoint in ("https://other.example/v1/decisions", "https://model.example/v2/decisions",
                         "https://model.example/v1/chat/completions",
                         "https://user:PRIVATE@model.example/v1/decisions",
                         "https://model.example/v1/decisions?token=PRIVATE"):
            with self.subTest(endpoint=endpoint), self.assertRaises(ValueError):
                instance_settings.decision_key_from_chat(self.cfg, endpoint)
        self.assertEqual(self.file.read_bytes(), before)
        self.assertNotIn("private-model-key", json.dumps(instance_settings.public_settings(self.cfg)))

    def test_chat_key_file_precedence_and_external_provider_are_preserved(self):
        (self.root / "api_keys_openai.txt").write_text("# comment\nprivate-file-first\nprivate-file-second\n")
        self.assertEqual(instance_settings.decision_key_from_chat(
            self.cfg, "https://model.example/v1/decisions"), "private-file-first")
        external = {"CHAT_PROVIDER": "gemini", "GEMINI_BASE_URL": "https://external.example/v1",
                    "GEMINI_API_KEY": "private-external-key"}
        cfg = configuration(self.root, EXTERNAL_ENV_KEYS=frozenset(external))
        with patch.dict(os.environ, external):
            self.assertEqual(instance_settings.decision_key_from_chat(
                cfg, "https://external.example/v1/decisions"), "private-external-key")
            with self.assertRaises(ValueError):
                instance_settings.decision_key_from_chat(cfg, "https://model.example/v1/decisions")
            self.assertNotIn("private-external-key", json.dumps(instance_settings.public_settings(cfg)))

    def test_missing_or_explicitly_empty_chat_key_cannot_be_copied(self):
        cfg = configuration(self.root, EXTERNAL_ENV_KEYS=frozenset({"OPENAI_API_KEY"}))
        with patch.dict(os.environ, {"OPENAI_API_KEY": ""}), self.assertRaises(ValueError):
            instance_settings.decision_key_from_chat(cfg, "https://model.example/v1/decisions")
