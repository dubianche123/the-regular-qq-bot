"""慢活：超过人能等的那几秒的事，先在时限内回一句，后台接着做，做完再回。

2026-11-01 群主：「之后可能还有别的会超出人类体感回复速度的计算，那时候就必须在
一定时间内有回复，后台正常处理。」联网查询（实测 4–13 秒）和深度研究（约 60 秒）
是头两个用户。

一种慢活登记成一个 Job，只要会两件事：

    start(**参数) -> 把手        起个头就返回；把手要能存进 state.json（字符串就行）
    poll(把手)    -> (状态, 结果) RUNNING / DONE 带结果 / FAILED

联网查询走 OpenAI 后台 Responses，把手就是 response id，bot 重启也丢不了。
以后本地的慢计算照这个接口接进来：把手在重启后找不回时 poll 报 FAILED，话照样能圆上。

在时限之内做完（wait 返回 DONE）就是一条普通回复；做不完先回一句「在弄」，
把手交给定时任务（scheduler.py）每 poll_every 秒问一次，做完由 bot 写成正式回复
再发；超过 give_up_after 还没好就当失败，同样要回一句，不许把人晾在那儿。
"""
import asyncio
import time
from dataclasses import dataclass
from typing import Callable

RUNNING, DONE, FAILED = "running", "done", "failed"


@dataclass(frozen=True)
class Job:
    name: str
    start: Callable          # async (**params) -> 把手 | None（起不了头）
    poll: Callable           # async (把手) -> (RUNNING | DONE | FAILED, 结果)
    poll_every: float        # 转进后台以后多久问一次
    give_up_after: float     # 从起头算，多久还没好就当失败
    wait_first: bool = True  # False：不在线上等，直接先回一句（明知要很久的活）


JOBS = {}


def register(job):
    JOBS[job.name] = job
    return job


async def wait(job, handle, seconds, *, step=1.0, clock=time.monotonic, sleep=asyncio.sleep):
    """seconds 之内反复问：做完或失败立刻返回，到点还在跑就返回 RUNNING。至少问一次。"""
    deadline = clock() + max(0.0, seconds)
    while True:
        state, result = await job.poll(handle)
        if state != RUNNING or clock() >= deadline:
            return state, result
        await sleep(min(step, max(0.0, deadline - clock())))
