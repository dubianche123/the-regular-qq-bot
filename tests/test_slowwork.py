"""慢活：联网查询和深度研究（slowwork.py + providers.start/poll_research + bot 的编排）。

2026-11-01 群主：「之后可能还有别的会超出人类体感回复速度的计算，那时候就必须在一定时间内
有回复，后台正常处理。」这里钉住三件事：时限内做完就一条回复；做不完先回一句、后台做完再跟进；
跟进送不出去（被动窗口过了）要有下家，不许把人晾着。
"""
import asyncio
import json
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import bot
import config
import prompts
import providers
import runtime
import scheduler
import search
import slowwork
from test_when_to_intervene import A, G, _Base
from regular.resource_quota import ResourceQuotas


class FakeJob:
    """poll 依次吐出 states；start 返回 handle（None = 起不了头）。"""

    def __init__(self, name, states, *, handle="resp_1", wait_first=True,
                 result=None, give_up_after=300.0):
        self.states = list(states)
        self.started = []
        self.polls = 0
        self.job = slowwork.Job(name, self.start, self.poll, poll_every=3.0,
                                give_up_after=give_up_after, wait_first=wait_first)
        self.handle = handle
        self.result = result or {"text": "确有其事：OpenAI 10 月 25 日发了预印本（[github.com](https://github.com/x)）。",
                                 "sources": [{"title": "The ETH is False", "url": "https://github.com/x"}]}

    async def start(self, query):
        self.started.append(query)
        return self.handle

    async def poll(self, handle):
        self.polls += 1
        state = self.states.pop(0) if self.states else slowwork.RUNNING
        return state, (self.result if state == slowwork.DONE else None)


class WaitTest(unittest.IsolatedAsyncioTestCase):
    async def test_returns_as_soon_as_done_or_failed(self):
        for final in (slowwork.DONE, slowwork.FAILED):
            fake = FakeJob("x", [slowwork.RUNNING, final])
            state, _ = await slowwork.wait(fake.job, "h", 5.0, step=0.0)
            self.assertEqual((state, fake.polls), (final, 2))

    async def test_still_running_at_the_deadline(self):
        clock = iter([0.0, 1.0, 2.0, 7.0])
        fake = FakeJob("x", [])
        state, _ = await slowwork.wait(fake.job, "h", 6.0, clock=lambda: next(clock),
                                       sleep=AsyncMock())
        self.assertEqual(state, slowwork.RUNNING)

    async def test_zero_seconds_still_asks_once(self):
        fake = FakeJob("x", [slowwork.DONE])
        self.assertEqual((await slowwork.wait(fake.job, "h", 0.0))[0], slowwork.DONE)


class _Resp:
    def __init__(self, status, data):
        self.status_code, self._data = status, data

    def json(self):
        return self._data


class ResearchCallTest(unittest.IsolatedAsyncioTestCase):
    """OpenAI Responses 后台任务：只发起、只查进度，不在这里等。"""

    def setUp(self):
        self.http = SimpleNamespace(request=AsyncMock())
        for target, name, value in (
                (providers, "_llm_http", self.http),
                (providers, "initialize", lambda **kw: None),
                (providers, "_reserve_attempt", lambda budget=None: None),
                (providers, "research_available", lambda: True),
                (providers, "_research_endpoint",
                 lambda path="": ("https://api.example/v1/responses" + path, {})),
                (config, "RESEARCH_MODEL", "gpt-6-luna")):
            ctx = patch.object(target, name, value)
            ctx.start()
            self.addCleanup(ctx.stop)

    async def test_start_is_a_background_web_search(self):
        self.http.request.return_value = _Resp(200, {"id": "resp_9", "status": "queued"})
        self.assertEqual(await providers.start_research("ETH 是什么", instructions="i"), "resp_9")
        body = self.http.request.await_args.kwargs["json"]
        self.assertTrue(body["background"])
        self.assertEqual(body["tools"], [{"type": "web_search", "search_context_size": "medium"}])
        self.assertEqual(body["reasoning"], {"effort": "low"})
        await providers.start_research("q", instructions="i", deep=True)
        deep = self.http.request.await_args.kwargs["json"]
        self.assertEqual((deep["reasoning"]["effort"], deep["tools"][0]["search_context_size"]),
                         ("high", "high"))

    async def test_poll_maps_statuses(self):
        done = {"status": "completed", "output": [
            {"type": "web_search_call", "status": "completed"},
            {"type": "message", "content": [{"type": "output_text", "text": "结论。",
                                             "annotations": [{"type": "url_citation", "url": "https://a",
                                                              "title": "A"}] * 2}]}]}
        cases = [(_Resp(200, {"status": "in_progress"}), "running"),
                 (_Resp(200, done), "done"),
                 (_Resp(200, {"status": "failed", "output": []}), "failed"),
                 (_Resp(500, {}), "running")]          # 网络抖一下不算失败
        for response, expected in cases:
            self.http.request.return_value = response
            state, result = await providers.poll_research("resp_9")
            self.assertEqual(state, expected)
        self.http.request.return_value = _Resp(200, done)
        _, result = await providers.poll_research("resp_9")
        self.assertEqual(result, {"text": "结论。", "sources": [{"title": "A", "url": "https://a"}]})

    async def test_unconfigured_never_calls_out(self):
        with patch.object(providers, "research_available", lambda: False):
            self.assertIsNone(await providers.start_research("q", instructions="i"))
        self.http.request.assert_not_awaited()


class RenderAndLimitTest(unittest.TestCase):
    def test_brief_drops_links_keeps_source_names(self):
        note = search.render_brief({"text": "确有其事（[github.com](https://github.com/x)），见 https://a.b/c",
                                    "sources": [{"title": "", "url": "https://papex.org/x"},
                                                {"title": "ETH is False", "url": "https://github.com/x"}]})
        self.assertNotIn("http", note)
        self.assertIn("github.com", note)
        self.assertIn("出处：papex.org；ETH is False", note)
        self.assertEqual(search.render_brief({"text": ""}), "")

    def test_automatic_lookups_skip_the_command_cooldown_but_not_the_cap(self):
        limiter = search.SearchLimiter(60, 2, clock=lambda: 1000.0)
        self.assertTrue(limiter.acquire(G)[0])
        self.assertEqual(limiter.acquire(G), (False, "cooldown"))
        self.assertTrue(limiter.acquire(G, cooldown=False)[0])
        self.assertEqual(limiter.acquire(G, cooldown=False), (False, "daily"))


class SchedulerWakeTest(unittest.IsolatedAsyncioTestCase):
    async def test_new_task_wakes_the_loop(self):
        s = scheduler.Scheduler()
        sleeper = asyncio.create_task(s.idle(60.0))
        await asyncio.sleep(0)
        s.at("k", G, time.time() + 3, task_id="a")
        await asyncio.wait_for(sleeper, timeout=1.0)

    async def test_sleeps_only_until_the_next_due(self):
        s = scheduler.Scheduler()
        s.at("k", G, time.time() + 0.05, task_id="a")
        await asyncio.wait_for(s.idle(60.0), timeout=1.0)


def answer(picks):
    async def respond(text, questions):
        out = []
        for q in questions:
            values = [c["value"] for c in q["choices"]]
            pick = picks.get(q["name"], values[-1])
            out.append({"name": q["name"], "type": "choice", "choice": pick, "confidence": 0.9,
                        "probabilities": [{"value": v, "probability": 0.9 if v == pick else
                                           0.1 / (len(values) - 1)} for v in values]})
        return {"answers": out}
    return respond


class LookupFlowTest(_Base):
    """bot 的编排：查询在时限内回来、回不来、深度研究、跟进送不出去。"""

    def setUp(self):
        super().setUp()
        self.sched = scheduler.Scheduler()
        self.sched.register("followup", bot.finish_lookup)
        self.posted = []
        self.client = SimpleNamespace(api=SimpleNamespace(post_group_message=AsyncMock(
            side_effect=lambda **kw: self.posted.append(kw) or {"id": "rcpt-follow"})))
        for target, name, value in (
                (providers, "research_available", lambda: True),
                (runtime, "SCHEDULER", self.sched),
                (runtime, "RESOURCE_QUOTAS", ResourceQuotas({}, lambda: None, lambda: None)),
                (config, "BOT_NAMES", ["小王"]),
                (config, "ASYNC_REPLY_SECONDS", 0.05),
                (bot, "_bot_ref", {"client": self.client}),
                (search, "LIMITER", search.SearchLimiter(60, 50)),
                (search, "DEEP_LIMITER", search.SearchLimiter(600, 3))):
            ctx = patch.object(target, name, value)
            ctx.start()
            self.addCleanup(ctx.stop)
        self.calls.side_effect = answer({"addressed": "answer", "lookup": "search"})

    def use(self, *fakes):
        ctx = patch.dict(slowwork.JOBS, {f.job.name: f.job for f in fakes})
        ctx.start()
        self.addCleanup(ctx.stop)

    def turn(self, index=-1):
        return self.prompts_seen[index][-1]["content"]

    async def test_fast_lookup_is_one_reply_with_the_brief(self):
        fake = FakeJob("search", [slowwork.DONE])
        self.use(fake)
        msg = await self._drive("小王 最近说ai证明了eth是不对的讲讲什么是eth")
        self.assertEqual(msg.reply.await_count, 1)
        self.assertIn(search.LOOKUP_MARK, self.turn())
        self.assertNotIn("http", self.turn())
        self.assertIn("现在有人问群里的机器人：", fake.started[0])
        self.assertEqual(self.sched.tasks, {})

    async def test_slow_lookup_answers_first_then_follows_up(self):
        fake = FakeJob("search", [slowwork.RUNNING] * 3)
        self.use(fake)
        msg = await self._drive("小王 ZO-1 的 mRNA 水平跟什么指标关系密切")
        self.assertEqual(msg.reply.await_count, 1)
        self.assertIn(prompts.PROMPT_LOOKUP_PENDING.strip(), self.turn())
        task = next(iter(self.sched.tasks.values()))
        self.assertEqual((task["kind"], task["payload"]["handle"]), ("followup", "resp_1"))
        json.dumps(task)                                    # 能落盘
        self.assertGreater(await bot.finish_lookup(task), time.time())   # 还在跑：过几秒再问
        fake.states = [slowwork.DONE]
        self.assertIsNone(await bot.finish_lookup(task))
        self.assertIn(prompts.PROMPT_LOOKUP_DONE.strip(), self.turn())
        self.assertIn(search.LOOKUP_MARK, self.turn())
        sent = self.posted[-1]
        self.assertEqual((sent["msg_id"], sent["msg_seq"]), (msg.id, 2))  # 同一条消息的第二句

    async def test_research_easter_egg_answers_right_away(self):
        self.calls.side_effect = answer({"addressed": "answer", "lookup": "research"})
        fake = FakeJob("research", [slowwork.RUNNING], wait_first=False)
        self.use(fake, FakeJob("search", [slowwork.DONE]))
        await self._drive("小王 帮我认真调研一下 ETH 被推翻这件事，整理一份")
        self.assertEqual(fake.polls, 1)
        self.assertIn(prompts.PROMPT_RESEARCH_PENDING.strip(), self.turn())
        task = next(iter(self.sched.tasks.values()))
        fake.states = [slowwork.DONE]
        await bot.finish_lookup(task)
        self.assertIn(prompts.PROMPT_RESEARCH_DONE.strip(), self.turn())

    async def test_research_over_the_daily_cap_falls_back_to_search(self):
        self.calls.side_effect = answer({"addressed": "answer", "lookup": "research"})
        deep, quick = FakeJob("research", []), FakeJob("search", [slowwork.DONE])
        self.use(deep, quick)
        with patch.object(search, "DEEP_LIMITER", search.SearchLimiter(600, 0)):
            await self._drive("小王 认真调研一下这个")
        self.assertEqual((len(deep.started), len(quick.started)), (0, 1))

    async def test_giving_up_still_closes_the_loop(self):
        fake = FakeJob("search", [slowwork.RUNNING] * 3, give_up_after=0.0)
        self.use(fake)
        await self._drive("小王 今天贵阳天气怎么样")
        task = next(iter(self.sched.tasks.values()))
        self.assertIsNone(await bot.finish_lookup(task))
        self.assertIn(prompts.PROMPT_LOOKUP_FAILED.strip(), self.turn())

    async def test_expired_window_tries_a_post_then_rides_the_next_message(self):
        fake = FakeJob("search", [slowwork.RUNNING] * 3)
        self.use(fake)
        await self._drive("小王 黄芩苷的作用机理是什么")
        task = next(iter(self.sched.tasks.values()))
        task["payload"]["msg_ts"] = "2020-01-01T00:00:00+08:00"   # 被动窗口早过了
        fake.states = [slowwork.DONE]
        with patch.object(bot, "post_to_group", AsyncMock(return_value=False)):
            self.assertIsNotNone(await bot.finish_lookup(task))
        self.assertTrue(task["payload"]["ready"])
        nxt = await self._drive("哈哈", sender="USER_WHEN_B")
        self.assertIn(task["payload"]["ready"],
                      [c.kwargs["content"] for c in nxt.reply.await_args_list])
        self.assertEqual(self.sched.tasks, {})

    async def test_no_lookup_for_quips_or_when_unconfigured(self):
        fake = FakeJob("search", [slowwork.DONE])
        self.use(fake)
        self.calls.side_effect = answer({"addressed": "quip", "lookup": "search"})
        await self._drive("小王还是个外行")
        self.assertEqual(fake.started, [])
        self.calls.side_effect = answer({"addressed": "answer"})
        with patch.object(providers, "research_available", lambda: False):
            await self._drive("小王 今天几号", msg_id="m-off")
        self.assertNotIn("lookup", {q["name"] for q in self.calls.await_args.args[1]})
        self.assertEqual(fake.started, [])

    async def test_start_failure_reports_that_lookup_failed(self):
        self.use(FakeJob("search", [], handle=None))
        msg = await self._drive("小王 最新的漫威是哪部")
        self.assertEqual(msg.reply.await_count, 1)
        self.assertIn("没查成", msg.reply.await_args.kwargs["content"])
        self.assertEqual(self.prompts_seen, [])

    async def test_explicit_search_command_goes_through_luna(self):
        fake = FakeJob("search", [slowwork.DONE])
        self.use(fake)
        with patch.object(search, "search_web", AsyncMock()) as ddg:
            await self._drive("搜索 华为最新旗舰")
        self.assertEqual(fake.started, ["华为最新旗舰"])
        ddg.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
