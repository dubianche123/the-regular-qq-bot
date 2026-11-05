"""Background status uses real task transitions, independent of logs."""
import asyncio
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from health import Health


class HealthTest(unittest.IsolatedAsyncioTestCase):
    async def test_ready_snapshot_distinguishes_loaded_release_and_animation_capability(self):
        import bot

        with tempfile.TemporaryDirectory() as directory:
            health = Health()
            path = Path(directory) / "status.json"
            health.configure(path, bot._runtime_status_details)
            health.lifecycle = "ready"
            health.gateway_connected = True
            snapshots = []
            for release, model, animated in (("release-old", "gpt-image-2.5-flare", False),
                                             ("release-new", "gpt-image-2.6-flare", True)):
                source = Path(directory) / "releases" / release
                with patch.object(bot, "__file__", str(source / "bot.py")), \
                     patch.object(bot, "code_release", return_value=release), \
                     patch.multiple(bot.config, IMAGE_GENERATION_MODEL=model,
                                    IMAGE_GENERATION_GIF_ENABLED=animated), \
                     patch.object(bot.image_generation, "available", return_value=True), \
                     patch.object(bot.image_generation, "animation_available", return_value=animated):
                    health.write()
                snapshot = json.loads(path.read_text())
                self.assertEqual(snapshot["code"], {"release": release, "source_root": str(source)})
                self.assertEqual(snapshot["image_generation"], {
                    "model": model, "available": True, "gif_enabled": animated,
                    "animation_available": animated})
                self.assertEqual(snapshot["lifecycle"], "ready")
                self.assertTrue(snapshot["gateway_connected"])
                snapshots.append(snapshot)
            self.assertEqual(snapshots[0]["pid"], snapshots[1]["pid"])
            self.assertNotEqual(snapshots[0]["code"], snapshots[1]["code"])

    async def test_enabled_gif_setting_does_not_claim_available_service(self):
        import bot

        with patch.object(bot.config, "IMAGE_GENERATION_GIF_ENABLED", True), \
             patch.object(bot.image_generation, "available", return_value=False), \
             patch.object(bot.image_generation, "animation_available", return_value=False):
            images = bot._runtime_status_details()["image_generation"]
        self.assertTrue(images["gif_enabled"])
        self.assertFalse(images["available"])
        self.assertFalse(images["animation_available"])

    async def test_failed_task_is_visible_in_atomic_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            health = Health()
            path = Path(directory) / "status.json"
            health.configure(path, lambda: {"budget": {"remaining": 3}})
            async def fail():
                raise RuntimeError("test failure")
            await health.run("digest", fail)
            data = json.loads(path.read_text())
            self.assertEqual(data["schema_version"], 1)
            self.assertEqual(data["tasks"]["digest"]["status"], "failed")
            self.assertEqual(data["tasks"]["digest"]["last_error"], "RuntimeError")
            self.assertEqual(data["budget"]["remaining"], 3)

    async def test_waiting_and_cancellation_are_real_task_states(self):
        health = Health()
        async def wait():
            await health.sleep(100)
        task = asyncio.create_task(health.run("flush", wait))
        await asyncio.sleep(0)
        self.assertEqual(health.tasks["flush"]["status"], "waiting")
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        self.assertEqual(health.tasks["flush"]["status"], "cancelled")

    async def test_dedicated_runtime_shutdown_cancels_untracked_sdk_jobs(self):
        from unittest.mock import AsyncMock, patch
        import bot
        started = asyncio.Event()
        async def sdk_job():
            started.set()
            await asyncio.Event().wait()
        job = asyncio.create_task(sdk_job())
        await started.wait()
        with patch.object(bot.media, "close", AsyncMock()) as media_close, \
             patch.object(bot.providers, "close", AsyncMock()) as provider_close:
            await bot._shutdown(cancel_all=True)
        self.assertTrue(job.cancelled())
        media_close.assert_awaited_once_with(cancel=True)
        provider_close.assert_awaited_once()
