"""Primary/vision model configuration, isolated from local credentials."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent


def inspect_config(overrides):
    with tempfile.TemporaryDirectory() as directory:
        env = {"PATH": os.environ.get("PATH", ""), "PYTHONPATH": str(ROOT),
               "REGULAR_HOME": directory, "QQ_APP_ID": "fake", "QQ_APP_SECRET": "fake"}
        env.update(overrides)
        script = """import config,providers,json
print(json.dumps({"provider": config.AI_PROVIDER,"vision_enabled":config.VISION_ENABLED,
 "vision_provider":config.VISION_PROVIDER,"vision_model":config.VISION_MODEL,
 "vision_inherited":config.VISION_MODEL_INHERITED,"vision":providers.configuration()["vision"],
 "image":{"model":config.IMAGE_GENERATION_MODEL,"complexity":config.IMAGE_GENERATION_COMPLEXITY,
 "gif_enabled":config.IMAGE_GENERATION_GIF_ENABLED,"gif_size":config.IMAGE_GENERATION_GIF_SIZE,
 "gif_frames":config.IMAGE_GENERATION_GIF_FRAME_COUNT,
 "gif_frame_ms":config.IMAGE_GENERATION_GIF_FRAME_DURATION_MS},
 "errors":config.validate()}))"""
        result = subprocess.run([sys.executable, "-c", script], env=env, capture_output=True,
                                text=True, check=True)
        return json.loads(result.stdout)


GENERIC = {"OPENAI_BASE_URL": "https://model.invalid/v1",
           "OPENAI_API_KEY": "fake", "OPENAI_MODELS": "chat-first,chat-second"}


class ProviderConfigurationTest(unittest.TestCase):
    def test_no_configuration_requires_primary_endpoint_and_model(self):
        cfg = inspect_config({})
        self.assertEqual(cfg["provider"], "openai")
        self.assertTrue(cfg["vision_enabled"])
        self.assertEqual(cfg["vision_provider"], "openai")
        self.assertEqual(cfg["vision_model"], "")
        self.assertTrue(any("OPENAI_BASE_URL" in error for error in cfg["errors"]))
        self.assertTrue(any("OPENAI_MODELS" in error for error in cfg["errors"]))

    def test_preset_inherits_first_chat_model(self):
        cfg = inspect_config({"AI_PROVIDER": "zhipu", "ZHIPU_API_KEY": "fake"})
        self.assertEqual(cfg["vision_provider"], "zhipu")
        self.assertTrue(cfg["vision_enabled"])
        self.assertEqual(cfg["vision_model"], "glm-4.7")
        self.assertTrue(cfg["vision_inherited"])
        self.assertEqual(cfg["errors"], [])

    def test_generic_endpoint_uses_first_configured_chat_model(self):
        cfg = inspect_config(GENERIC)
        self.assertEqual(cfg["vision_model"], "chat-first")
        self.assertTrue(cfg["vision_inherited"])
        self.assertTrue(cfg["vision"]["configured"])
        self.assertEqual(cfg["errors"], [])

    def test_explicit_model_retains_same_provider_with_text_only_chat_model(self):
        cfg = inspect_config({**GENERIC, "VISION_MODEL": "image-model"})
        self.assertEqual(cfg["vision_provider"], "openai")
        self.assertEqual(cfg["vision_model"], "image-model")
        self.assertFalse(cfg["vision_inherited"])
        self.assertEqual(cfg["errors"], [])

    def test_explicit_provider_and_model_remain_independent_of_chat(self):
        cfg = inspect_config({**GENERIC, "VISION_PROVIDER": "gemini",
                              "VISION_MODEL": "image-model", "GEMINI_API_KEY": "fake-vision"})
        self.assertEqual(cfg["provider"], "openai")
        self.assertEqual(cfg["vision_provider"], "gemini")
        self.assertEqual(cfg["vision_model"], "image-model")
        self.assertFalse(cfg["vision_inherited"])
        self.assertTrue(cfg["vision"]["configured"])
        self.assertEqual(cfg["errors"], [])

    def test_separate_provider_needs_an_explicit_model(self):
        cfg = inspect_config({**GENERIC, "VISION_PROVIDER": "gemini"})
        self.assertEqual(cfg["vision_model"], "")
        self.assertFalse(cfg["vision_inherited"])
        self.assertTrue(any("VISION_MODEL" in error and "VISION_PROVIDER" in error for error in cfg["errors"]))

    def test_blank_overrides_follow_main_model(self):
        cfg = inspect_config({**GENERIC, "VISION_PROVIDER": "", "VISION_MODEL": ""})
        self.assertEqual(cfg["vision_provider"], "openai")
        self.assertEqual(cfg["vision_model"], "chat-first")
        self.assertTrue(cfg["vision_inherited"])
        self.assertEqual(cfg["errors"], [])

    def test_disabling_vision_does_not_require_separate_model(self):
        cfg = inspect_config({**GENERIC, "VISION_ENABLED": "false", "VISION_PROVIDER": "gemini"})
        self.assertFalse(cfg["vision_enabled"])
        self.assertEqual(cfg["errors"], [])

    def test_image_defaults_use_26_with_bounded_short_animation(self):
        cfg = inspect_config(GENERIC)
        self.assertEqual(cfg["image"], {"model": "gpt-image-2.6-flare", "complexity": "medium",
                                       "gif_enabled": True, "gif_size": "512x512", "gif_frames": 9,
                                       "gif_frame_ms": 200})
        self.assertEqual(cfg["errors"], [])

    def test_old_image_endpoint_can_omit_complexity_and_disable_gif(self):
        cfg = inspect_config({**GENERIC, "IMAGE_GENERATION_MODEL": "legacy-image",
                              "IMAGE_GENERATION_COMPLEXITY": "", "IMAGE_GENERATION_GIF_ENABLED": "false"})
        self.assertEqual(cfg["image"]["complexity"], "")
        self.assertFalse(cfg["image"]["gif_enabled"])
        self.assertEqual(cfg["errors"], [])

    def test_gif_settings_validate_published_api_limits(self):
        invalid = {"IMAGE_GENERATION_COMPLEXITY": ("max",),
                   "IMAGE_GENERATION_GIF_FRAME_COUNT": ("1", "65", "abc"),
                   "IMAGE_GENERATION_GIF_FRAME_DURATION_MS": ("10", "2010", "25"),
                   "IMAGE_GENERATION_GIF_SIZE": ("512", "0x512", "511x512", "512x-512", "512x512x512")}
        for name, values in invalid.items():
            for value in values:
                with self.subTest(name=name, value=value):
                    cfg = inspect_config({**GENERIC, name: value})
                    self.assertTrue(any(name in error for error in cfg["errors"]), cfg["errors"])
        for overrides in ({"IMAGE_GENERATION_GIF_FRAME_COUNT": "2", "IMAGE_GENERATION_GIF_SIZE": "auto",
                           "IMAGE_GENERATION_GIF_FRAME_DURATION_MS": "20"},
                          {"IMAGE_GENERATION_GIF_FRAME_COUNT": "64", "IMAGE_GENERATION_GIF_SIZE": "512x768",
                           "IMAGE_GENERATION_GIF_FRAME_DURATION_MS": "2000"}):
            with self.subTest(overrides=overrides):
                self.assertEqual(inspect_config({**GENERIC, **overrides})["errors"], [])
