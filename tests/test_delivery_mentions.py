"""Precise reminder mentions belong to the QQ wire payload, not model-visible text."""
import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

import aiohttp
from botpy.errors import ForbiddenError, ServerError

from delivery import Sender, group_message_payload


def message(script):
    return SimpleNamespace(id="synthetic-source", group_openid="synthetic-group",
                           reply=AsyncMock(side_effect=script))


class MentionPayloadTest(unittest.TestCase):
    def test_plain_message_keeps_its_original_payload(self):
        self.assertEqual(group_message_payload("到点了"), {"content": "到点了", "msg_type": 0})

    def test_only_code_supplied_member_becomes_a_mention(self):
        body = '到点了 <qqbot-at-user id="another-member" /> & <qqbot-at-everyone />'
        payload = group_message_payload(body, 'requester"injection')
        wire = payload["markdown"]["content"]
        self.assertEqual(payload["msg_type"], 2)
        self.assertNotIn("content", payload)
        self.assertEqual(wire.count("<qqbot-at-user "), 1)
        self.assertIn('id="requester&quot;injection"', wire)
        self.assertIn('&lt;qqbot-at-user id="another-member" /&gt;', wire)
        self.assertNotIn("<qqbot-at-everyone", wire)


class MentionDeliveryTest(unittest.IsolatedAsyncioTestCase):
    async def test_receipt_callbacks_keep_plain_text(self):
        m = message([{"id": "receipt"}])
        seen = []
        result = await Sender().reply(m, "到点了", mention_member="requester",
                                      on_outcome=seen.append, on_delivered=seen.append)
        self.assertTrue(result.delivered)
        self.assertEqual([r.text for r in seen], ["到点了", "到点了"])
        self.assertEqual(result.text, "到点了")
        self.assertEqual(m.reply.call_args.kwargs["markdown"]["content"],
                         '<qqbot-at-user id="requester" /> 到点了')

    async def test_explicit_rejection_downgrades_once_with_a_new_sequence(self):
        m = message([ForbiddenError("markdown rejected"), {"id": "plain-receipt"}])
        result = await Sender().reply(m, "到点了", mention_member="requester")
        first, second = [c.kwargs for c in m.reply.call_args_list]
        self.assertTrue(result.delivered)
        self.assertFalse(result.fallback)  # Only the transport format changed.
        self.assertEqual(first["msg_type"], 2)
        self.assertEqual(second["msg_type"], 0)
        self.assertEqual(second["content"], "到点了")
        self.assertNotIn("markdown", second)
        self.assertNotEqual(first["msg_seq"], second["msg_seq"])

    async def test_second_rejection_stops_without_a_retry_loop(self):
        m = message([ForbiddenError("markdown rejected"), ForbiddenError("plain rejected")])
        result = await Sender().reply(m, "到点了", mention_member="requester")
        self.assertEqual(result.status, "rejected")
        self.assertEqual(m.reply.await_count, 2)

    async def test_explicit_http_400_can_downgrade_but_ambiguous_sdk_server_error_cannot(self):
        error = aiohttp.ClientResponseError(SimpleNamespace(real_url="https://example.invalid"),
                                           (), status=400, message="invalid format")
        m = message([error, {"id": "plain"}])
        self.assertTrue((await Sender().reply(m, "到点了", mention_member="requester")).delivered)
        self.assertEqual(m.reply.call_args.kwargs["msg_type"], 0)
        m = message([ServerError("ambiguous server response")])
        result = await Sender().reply(m, "到点了", mention_member="requester")
        self.assertEqual(result.status, "rejected")
        self.assertEqual(m.reply.await_count, 1)

    async def test_timeout_queue_keeps_the_same_payload_and_sequence(self):
        for error in (asyncio.TimeoutError(), aiohttp.ServerDisconnectedError("disconnected")):
            with self.subTest(error=type(error).__name__):
                sender = Sender()
                m = message([error, {"id": "late"}])
                result = await sender.reply(m, "到点了", mention_member="requester")
                self.assertEqual(result.status, "queued")
                self.assertEqual(sender.pending[0].mention_member, "requester")
                await sender.drain()
                calls = [c.kwargs for c in m.reply.call_args_list]
                self.assertEqual(calls[0], calls[1])
                self.assertEqual(calls[1]["msg_type"], 2)

    async def test_empty_receipts_do_not_trigger_a_format_change(self):
        sender = Sender()
        m = message([None, None, {"id": "late"}])
        result = await sender.reply(m, "到点了", mention_member="requester")
        self.assertEqual(result.status, "queued")
        await sender.drain()
        self.assertEqual(len({str(c.kwargs) for c in m.reply.call_args_list}), 1)

    async def test_queued_explicit_rejection_downgrades_only_once(self):
        sender = Sender()
        m = message([ConnectionError("offline"), ForbiddenError("markdown rejected"), {"id": "plain"}])
        delivered = []
        await sender.reply(m, "到点了", mention_member="requester", on_delivered=delivered.append)
        await sender.drain()
        self.assertEqual(len(sender.pending), 1)
        self.assertIsNone(sender.pending[0].mention_member)
        self.assertEqual(delivered, [])
        await sender.drain()
        await sender.drain()
        calls = [c.kwargs for c in m.reply.call_args_list]
        self.assertEqual([c["msg_type"] for c in calls], [2, 2, 0])
        self.assertEqual(calls[0]["msg_seq"], calls[1]["msg_seq"])
        self.assertNotEqual(calls[1]["msg_seq"], calls[2]["msg_seq"])
        self.assertEqual([r.text for r in delivered], ["到点了"])
