"""群聊的轮流说话：一个群同一时刻只写、只发一句（2026-11-16）。

以前每条消息各跑各的：A 的回复还在写，B 又叫了它，两句各写各的、几乎同时发出去，
后一句写的时候看不见前一句。线上 11-01~11-16 有 55 次，约占它回复的 6%：多是两个人先后叫它，
或者它正在写一句没人叫的插嘴时有人叫了它。

做法照开源聊天框架处理「上一句还没回完又来一句」的通行办法：Vercel Chat SDK 的 queue / burst
（群聊按整个频道上锁，处理期间来的消息排队，下一轮一并看见），LangGraph 的 double-texting enqueue。
落到这里是三条：

1. 一个群一支话筒：从开始写到这句送达，同一时刻只有一轮拿着。画图那轮发完文字就把话筒放下，
   等图不占着（bot._reply_with_image）。
2. 排队的先后看「欠不欠这一句」：被 @、点名该答、在回它刚才那句的（owed）排在没人叫的插嘴前面；
   同类按消息到达的先后。
3. 没人叫的插嘴写好以后，期间有人叫了它（owed_pending_after），这句不发，让给叫它的人。

同一个人连着说的几句不在这里合并：没人叫的那几句由 bot.reply_unprompted 只接最后一句（_said_more），
欠着的几句按设计都要接（同一个人的会话锁本来就让它们一句一句来，后一句看得见前一句）。

排到的那一轮照常生成：等待期间它自己说过的话，由 bot._later_scene 写进「本句到达之后发生的事」，
所以后一句写的时候看得见前一句。代码只管谁先说、谁让，说什么仍是模型的事。

一轮的状态：PLANNING（分诊说要接，还在备料）→ WAITING（排队）→ SPEAKING（拿着话筒）
→ IDLE（放下了：说完了，或者先让出来等别的）→ DONE（这一轮结束）。
"""
import asyncio
import collections
import contextvars
import dataclasses
import itertools
import logging
import time

logger = logging.getLogger("qqbot")

PLANNING, WAITING, SPEAKING, IDLE, DONE = "planning", "waiting", "speaking", "idle", "done"

# 这一轮（一条群消息的整个处理过程）排的是哪一号。bot.handle_group_msg 设，reply_in_session 读。
CURRENT = contextvars.ContextVar("turntaking_claim", default=None)


@dataclasses.dataclass(eq=False)
class Claim:
    """一轮想开口的请求。arrived 是那条消息到达的时刻（不是开始排队的时刻）。"""
    group: str
    sender: object
    owed: bool
    arrived: float
    seq: int
    state: str = PLANNING
    spoke: bool = False        # 这一轮已经说出去过一句
    since: float = 0.0         # 拿到话筒的时刻（单调时钟）
    wake: object = None


class Floor:
    """每个群一支话筒。纯内存：重启以后没有排队中的轮次，也就没有要恢复的东西。"""

    def __init__(self, *, max_hold=150.0, clock=time.monotonic):
        # 一轮最多拿着话筒这么久（生成整轮超时 90s + 发送）；超过就当它卡死了，收回来给下一轮。
        self.max_hold = max_hold
        self._clock = clock
        self._claims = collections.defaultdict(list)
        self._holder = {}
        self._seq = itertools.count()

    def claim(self, group, sender, *, owed, arrived):
        claim = Claim(group, sender, bool(owed), float(arrived), next(self._seq))
        self._claims[group].append(claim)
        return claim

    def holder(self, group):
        return self._holder.get(group)

    async def take(self, claim):
        """等到这一轮拿着话筒。已经拿着就直接返回。"""
        if claim.state == SPEAKING:
            return
        if claim.state == DONE:
            raise RuntimeError("这一轮已经结束，不能再排队")
        claim.state = WAITING
        loop = asyncio.get_running_loop()
        try:
            while True:
                self._hand_over(claim.group)
                if claim.state == SPEAKING:
                    return
                claim.wake = loop.create_future()
                # 带超时等：拿着话筒的那轮要是卡死了，到点由这里收回（_hand_over）。
                await asyncio.wait({claim.wake}, timeout=self.max_hold)
        except asyncio.CancelledError:
            self.release(claim)
            raise

    def step_down(self, claim, *, spoke=True):
        """放下话筒（这一轮还没结束：画图那轮还在等图，或者先让出来等这个人的会话锁）。"""
        if claim is None:
            return
        claim.spoke = claim.spoke or spoke
        if claim.state == SPEAKING:
            claim.state = IDLE
        if self._holder.get(claim.group) is claim:
            del self._holder[claim.group]
            self._hand_over(claim.group)

    def release(self, claim):
        """这一轮结束（说完、不说了、出错都走这里）。可以重复调用。"""
        if claim is None or claim.state == DONE:
            return
        claim.state = DONE
        claims = self._claims.get(claim.group)
        if claims is not None and claim in claims:
            claims.remove(claim)
            if not claims:
                del self._claims[claim.group]
        if self._holder.get(claim.group) is claim:
            del self._holder[claim.group]
        self._hand_over(claim.group)

    def owed_pending_after(self, claim):
        """这条消息之后，有人叫了它、那一轮还没说上话：没人叫的插嘴该让给它。"""
        if claim is None:
            return False
        return any(other is not claim and other.owed and not other.spoke
                   and other.state in (PLANNING, WAITING, IDLE)
                   and other.arrived > claim.arrived
                   for other in self._claims.get(claim.group, ()))

    def _hand_over(self, group):
        holder = self._holder.get(group)
        if holder is not None:
            if holder.state == SPEAKING and self._clock() - holder.since < self.max_hold:
                return
            if holder.state == SPEAKING:
                logger.warning("🎙️ 话筒被占了 %.0fs 没放下，收回来给下一轮（%s）",
                               self._clock() - holder.since, str(group)[-6:])
                holder.state = IDLE
            del self._holder[group]
        waiting = [c for c in self._claims.get(group, ()) if c.state == WAITING]
        if not waiting:
            return
        nxt = min(waiting, key=lambda c: (not c.owed, c.arrived, c.seq))
        nxt.state, nxt.since = SPEAKING, self._clock()
        self._holder[group] = nxt
        if nxt.wake is not None and not nxt.wake.done():
            nxt.wake.set_result(None)
