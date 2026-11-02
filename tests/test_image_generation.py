import asyncio
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, Mock, patch

import httpx
from openai import BadRequestError

import image_generation as image
from storage import DailyBudget
from regular.resource_quota import ResourceQuotas


class ImageGenerationTest(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.budget = DailyBudget(2, {})
        self.state = SimpleNamespace(mark_dirty=Mock(), save_now=Mock())
        self.upload = AsyncMock(return_value={"file_info": "qq-file"})
        self.message = SimpleNamespace(
            timestamp=None, _api=SimpleNamespace(_http=SimpleNamespace(request=self.upload)),
            reply=AsyncMock(return_value={"id": "sent"}))
        self.config = patch.multiple(image.config, IMAGE_GENERATION_ENABLED=True,
                                    IMAGE_GENERATION_BASE_URL="https://example.invalid/v1",
                                    IMAGE_GENERATION_API_KEY="test-key",
                                    IMAGE_GENERATION_MODEL="gpt-image-2.5-flare",
                                    IMAGE_GENERATION_TIMEOUT_SECONDS=1)
        self.config.start()
        self.addCleanup(self.config.stop)
        self.patch_budget = patch.object(image.runtime, "IMAGE_GENERATION_BUDGET", self.budget)
        self.patch_budget.start()
        self.addCleanup(self.patch_budget.stop)
        self.patch_state = patch.object(image.runtime, "STATE", self.state)
        self.patch_state.start()
        self.addCleanup(self.patch_state.stop)
        self.quotas = ResourceQuotas({}, self.state.mark_dirty, self.state.save_now)
        self.patch_quotas = patch.object(image.runtime, "RESOURCE_QUOTAS", self.quotas)
        self.patch_quotas.start()
        self.addCleanup(self.patch_quotas.stop)
        self.gen = patch.object(image, "_generate", AsyncMock(return_value="base64-image"))
        self.generate = self.gen.start()
        self.addCleanup(self.gen.stop)

    async def test_success_uploads_base64_and_replies_with_media(self):
        next_seq = AsyncMock(return_value=4)
        result = await image.generate_and_send(self.message, group_id="g", user_id="u", score=0, prompt="cat",
                                               before_send=next_seq)
        self.assertIs(result, True)
        self.assertEqual(self.budget.used, 1)
        self.state.save_now.assert_called_once()
        self.generate.assert_awaited_once_with("cat", "test-key")
        self.assertEqual(self.upload.await_args.kwargs["json"], {
            "file_type": 1, "file_data": "base64-image", "srv_send_msg": False})
        self.message.reply.assert_awaited_once_with(msg_type=7, media={"file_info": "qq-file"}, msg_seq=4)

    async def test_failures_consume_once_but_do_not_send(self):
        self.generate.side_effect = asyncio.TimeoutError()
        self.assertEqual(await image.generate_and_send(self.message, group_id="g", user_id="u", score=0, prompt="cat"), "generation_error")
        # 上游可能已经计费，全局成本护栏保留；未收到图片的个人次数退回。
        self.assertEqual(self.budget.used, 1)
        self.assertEqual(self.quotas._used("image", "g", "u"), 0)
        self.assertEqual(self.state.save_now.call_count, 2)
        self.upload.assert_not_awaited()
        self.message.reply.assert_not_awaited()

    async def test_upload_failure_is_definitely_not_sent(self):
        self.upload.side_effect = OSError()
        self.assertEqual(await image.generate_and_send(self.message, group_id="g", user_id="u", score=0, prompt="cat"), "upload_error")
        self.message.reply.assert_not_awaited()
        self.assertEqual(self.quotas._used("image", "g", "u"), 0)
        self.assertEqual(self.budget.used, 1)

    async def test_sequence_callback_failure_is_definitely_not_sent(self):
        next_seq = AsyncMock(side_effect=ValueError())
        self.assertEqual(await image.generate_and_send(self.message, group_id="g", user_id="u", score=0, prompt="cat",
                                                       before_send=next_seq), "upload_error")
        self.message.reply.assert_not_awaited()

    async def test_unconfirmed_send_must_not_trigger_fallback(self):
        self.message.reply.side_effect = asyncio.TimeoutError()
        self.assertIsNone(await image.generate_and_send(self.message, group_id="g", user_id="u", score=0, prompt="cat"))
        # 可能已经送到群里，不能把回执未知当成确定未送达。
        self.assertEqual(self.quotas._used("image", "g", "u"), 1)
        self.message.reply.side_effect = None
        self.message.reply.return_value = None
        self.assertIsNone(await image.generate_and_send(self.message, group_id="g", user_id="u", score=0, prompt="cat"))
        self.assertEqual(self.quotas._used("image", "g", "u"), 2)

    async def test_disabled_and_exhausted_do_not_generate(self):
        with patch.object(image.config, "IMAGE_GENERATION_ENABLED", False):
            self.assertIs(await image.generate_and_send(self.message, group_id="g", user_id="u", score=0, prompt="cat"), False)
        self.budget.limit = 0
        self.assertEqual(await image.generate_and_send(self.message, group_id="g", user_id="u", score=0, prompt="cat"), "quota")
        self.generate.assert_not_awaited()

    async def test_busy_request_does_not_queue_another_paid_request(self):
        started, finish = asyncio.Event(), asyncio.Event()

        async def pending(*args):
            started.set()
            await finish.wait()
            return "base64-image"

        self.generate.side_effect = pending
        first = asyncio.create_task(image.generate_and_send(self.message, group_id="g", user_id="u", score=0, prompt="cat"))
        await started.wait()
        self.assertEqual(await image.generate_and_send(self.message, group_id="g", user_id="u", score=0, prompt="dog"), "busy")
        finish.set()
        self.assertIs(await first, True)
        self.assertEqual(self.budget.used, 1)

    async def test_moderation_and_invalid_request_are_distinct_without_logging_private_text(self):
        response = httpx.Response(400, request=httpx.Request("POST", "https://example.invalid"))
        for code, expected in (("moderation_blocked", "blocked"),
                               ("invalid_value", "generation_error")):
            self.generate.side_effect = BadRequestError(
                "sensitive prompt secret", response=response,
                body={"code": code, "message": "sensitive prompt secret"})
            with self.assertLogs("qqbot", level="WARNING") as captured:
                result = await image.generate_and_send(
                    self.message, group_id="g", user_id="u", score=0, prompt="secret")
            self.assertEqual(result, expected)
            self.assertIn("status=400", captured.output[0])
            self.assertIn(f"code={code}", captured.output[0])
            self.assertNotIn("secret", captured.output[0])
        self.upload.assert_not_awaited()

    async def test_unrecognized_provider_code_is_not_logged(self):
        response = httpx.Response(400, request=httpx.Request("POST", "https://example.invalid"))
        self.generate.side_effect = BadRequestError(
            "sensitive prompt", response=response,
            body={"code": "secret_as_untrusted_code", "message": "sensitive prompt"})
        with self.assertLogs("qqbot", level="WARNING") as captured:
            result = await image.generate_and_send(
                self.message, group_id="g", user_id="u", score=0, prompt="secret")
        self.assertEqual(result, "generation_error")
        self.assertIn("code=other", captured.output[0])
        self.assertNotIn("secret", captured.output[0])

    async def test_upload_response_requires_file_info_and_only_sends_that_field(self):
        self.upload.return_value = {"file_info": "qq-file", "file_uuid": "opaque", "ttl": 60}
        self.assertIs(await image.generate_and_send(
            self.message, group_id="g", user_id="u", score=0, prompt="cat"), True)
        self.assertEqual(self.message.reply.await_args.kwargs["media"], {"file_info": "qq-file"})
        self.upload.return_value = {"file_uuid": "opaque"}
        self.assertEqual(await image.generate_and_send(
            self.message, group_id="g", user_id="u", score=0, prompt="cat"), "upload_error")
        self.assertEqual(self.message.reply.await_count, 1)

    async def test_failed_reservation_persistence_never_sends_request(self):
        self.state.save_now.side_effect = OSError()
        self.assertIs(await image.generate_and_send(self.message, group_id="g", user_id="u", score=0, prompt="cat"), False)
        self.generate.assert_not_awaited()
        self.assertEqual(self.quotas._used("image", "g", "u"), 0)
        self.assertEqual(self.budget.used, 0)

    async def test_refund_save_failure_keeps_personal_count_conservatively(self):
        self.state.save_now.side_effect = [None, OSError()]
        self.generate.side_effect = asyncio.TimeoutError()
        with self.assertLogs("qqbot", level="WARNING") as captured:
            result = await image.generate_and_send(
                self.message, group_id="g", user_id="u", score=0, prompt="cat")
        self.assertEqual(result, "generation_error")
        self.assertEqual(self.quotas._used("image", "g", "u"), 1)
        self.assertTrue(any("退还落盘失败" in line for line in captured.output))

    async def test_cancellation_before_qq_send_refunds_personal(self):
        started = asyncio.Event()
        async def pending(*args):
            started.set()
            await asyncio.Event().wait()
        self.generate.side_effect = pending
        task = asyncio.create_task(image.generate_and_send(
            self.message, group_id="g", user_id="u", score=0, prompt="cat"))
        await started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(self.quotas._used("image", "g", "u"), 0)
        self.assertEqual(self.budget.used, 1)

    async def test_cancellation_during_qq_send_keeps_personal(self):
        started = asyncio.Event()
        async def pending(**kwargs):
            started.set()
            await asyncio.Event().wait()
        self.message.reply.side_effect = pending
        task = asyncio.create_task(image.generate_and_send(
            self.message, group_id="g", user_id="u", score=0, prompt="cat"))
        await started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(self.quotas._used("image", "g", "u"), 1)

    async def test_overnight_failure_does_not_refund_new_day(self):
        day = ["2026-11-01"]
        self.quotas._today = lambda: day[0]
        async def generation(*args):
            day[0] = "2026-11-02"
            self.assertEqual(self.quotas.consume("image", "g", "u", 0), "normal")
            return None
        self.generate.side_effect = generation
        with patch.object(self.budget, "today", side_effect=lambda: day[0]):
            result = await image.generate_and_send(
                self.message, group_id="g", user_id="u", score=0, prompt="cat")
        self.assertEqual(result, "generation_error")
        self.assertEqual(self.quotas._used("image", "g", "u"), 1)

    async def test_midnight_between_budget_and_personal_reservations_never_calls_api(self):
        day = ["2026-11-01"]
        self.quotas._today = lambda: day[0]
        original_consume = self.budget.try_consume

        def cross_midnight():
            result = original_consume()
            day[0] = "2026-11-02"
            return result

        with patch.object(self.budget, "today", side_effect=lambda: day[0]), \
             patch.object(self.budget, "try_consume", side_effect=cross_midnight):
            result = await image.generate_and_send(
                self.message, group_id="g", user_id="u", score=0, prompt="cat")
            self.assertEqual(self.budget.reservation_day, "2026-11-01")
            self.assertEqual(self.budget.used, 0)
        self.assertIs(result, False)
        self.assertEqual(self.quotas._used("image", "g", "u"), 0)
        self.generate.assert_not_awaited()

    async def test_personal_quota_extra_and_other_member(self):
        """precheck 在小王写回复之前报状态：第二张起是破例（exception），用满是 tired。"""
        self.budget.limit = 3
        seen = []
        with patch.object(image.config, "IMAGE_USER_DAILY_CAP", 1), \
             patch.object(image.config, "IMAGE_USER_EXTRA_ACQUAINTANCE", 1):
            async def request(user):
                seen.append(image.precheck("g", user, 10))
                return await image.generate_and_send(
                    self.message, group_id="g", user_id=user, score=10, prompt="cat")
            self.assertIs(await request("u"), True)
            self.assertIs(await request("u"), True)
            self.assertEqual(await request("u"), "quota")
            self.assertIs(await request("other"), True)
        self.assertEqual(seen, ["normal", "exception", "tired", "normal"])
        self.assertEqual(self.generate.await_count, 3)
        self.assertEqual(self.budget.used, 3)

    async def test_personal_denial_does_not_consume_global_quota(self):
        with patch.object(image.config, "IMAGE_USER_DAILY_CAP", 0):
            result = await image.generate_and_send(
                self.message, group_id="g", user_id="u", score=0, prompt="cat")
        self.assertEqual(result, "quota")
        self.assertEqual(self.budget.used, 0)
        self.generate.assert_not_awaited()

    async def test_generation_uses_one_sdk_attempt_with_configured_parameters(self):
        self.gen.stop()
        generate = AsyncMock(return_value=SimpleNamespace(data=[SimpleNamespace(b64_json="encoded")]))
        client = SimpleNamespace(images=SimpleNamespace(generate=generate))
        with patch.object(image, "AsyncOpenAI", return_value=client) as factory:
            self.assertEqual(await image._generate("prompt", "key"), "encoded")
        self.assertEqual(factory.call_args.kwargs["max_retries"], 0)
        generate.assert_awaited_once()
        self.assertEqual(generate.await_args.kwargs["model"], "gpt-image-2.5-flare")
        self.assertEqual(generate.await_args.kwargs["quality"], image.config.IMAGE_GENERATION_QUALITY)

    async def test_decision_key_only_reused_between_official_endpoints(self):
        official = {"endpoint": "https://api.openai.com/v1/decisions", "api_key": "decision-key"}
        with patch.object(image.config, "IMAGE_GENERATION_API_KEY", ""), \
             patch.object(image.config, "load_api_keys", return_value=[]), \
             patch.object(image.decision_settings, "load_settings", return_value=official), \
             patch.object(image.config, "IMAGE_GENERATION_BASE_URL", "https://api.openai.com/v1"):
            self.assertEqual(image._key(), "decision-key")
            with patch.object(image.config, "IMAGE_GENERATION_BASE_URL", "https://gateway.invalid/v1"):
                self.assertEqual(image._key(), "")
            official["endpoint"] = "https://api.openai.com.evil.invalid/v1/decisions"
            self.assertEqual(image._key(), "")
