"""群里的话筒（turntaking.py）：一个群同一时刻只有一轮在写、在发。"""
import asyncio
import unittest

from turntaking import Floor

G, H = "group-g", "group-h"


class FloorTest(unittest.IsolatedAsyncioTestCase):
    async def _queued(self, floor, claim):
        """排上队，确认它确实在等（没拿到话筒）。"""
        task = asyncio.create_task(floor.take(claim))
        await asyncio.sleep(0)
        self.assertFalse(task.done())
        return task

    async def test_one_speaker_per_group_other_groups_unaffected(self):
        floor = Floor()
        a = floor.claim(G, "a", owed=True, arrived=1)
        await floor.take(a)
        b = floor.claim(G, "b", owed=True, arrived=2)
        waiting = await self._queued(floor, b)
        other = floor.claim(H, "c", owed=False, arrived=3)
        await asyncio.wait_for(floor.take(other), 1)      # 别的群照常说
        floor.release(a)
        await asyncio.wait_for(waiting, 1)
        self.assertIs(floor.holder(G), b)

    async def test_owed_turns_go_before_optional_ones_then_by_arrival(self):
        floor = Floor()
        holder = floor.claim(G, "x", owed=True, arrived=0)
        await floor.take(holder)
        banter = floor.claim(G, "a", owed=False, arrived=1)
        late_call = floor.claim(G, "b", owed=True, arrived=3)
        early_call = floor.claim(G, "c", owed=True, arrived=2)
        tasks = {c: await self._queued(floor, c) for c in (banter, late_call, early_call)}
        order = []
        for _ in range(3):
            floor.release(floor.holder(G) or holder)
            await asyncio.sleep(0)
            current = floor.holder(G)
            order.append(current)
            await asyncio.wait_for(tasks[current], 1)
        self.assertEqual(order, [early_call, late_call, banter])

    async def test_stepping_down_hands_over_and_the_turn_can_queue_again(self):
        floor = Floor()
        drawing = floor.claim(G, "a", owed=True, arrived=1)
        await floor.take(drawing)
        other = floor.claim(G, "b", owed=True, arrived=2)
        waiting = await self._queued(floor, other)
        floor.step_down(drawing)            # 发完文字、去等图
        await asyncio.wait_for(waiting, 1)
        self.assertTrue(drawing.spoke)
        again = await self._queued(floor, drawing)
        floor.release(other)
        await asyncio.wait_for(again, 1)
        self.assertIs(floor.holder(G), drawing)

    async def test_a_stuck_holder_is_reclaimed_after_max_hold(self):
        now = [0.0]
        floor = Floor(max_hold=0.05, clock=lambda: now[0])
        stuck = floor.claim(G, "a", owed=True, arrived=1)
        await floor.take(stuck)
        nxt = floor.claim(G, "b", owed=True, arrived=2)
        task = asyncio.create_task(floor.take(nxt))
        await asyncio.sleep(0)
        now[0] = 1.0
        with self.assertLogs("qqbot", "WARNING"):
            await asyncio.wait_for(task, 1)
        self.assertIs(floor.holder(G), nxt)

    async def test_a_cancelled_waiter_leaves_the_queue(self):
        floor = Floor()
        holder = floor.claim(G, "a", owed=True, arrived=1)
        await floor.take(holder)
        gone = floor.claim(G, "b", owed=True, arrived=2)
        task = await self._queued(floor, gone)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        last = floor.claim(G, "c", owed=False, arrived=3)
        waiting = await self._queued(floor, last)
        floor.release(holder)
        await asyncio.wait_for(waiting, 1)
        self.assertIs(floor.holder(G), last)

    async def test_owed_pending_after_counts_only_later_unspoken_calls(self):
        floor = Floor()
        banter = floor.claim(G, "a", owed=False, arrived=5)
        self.assertFalse(floor.owed_pending_after(banter))
        earlier = floor.claim(G, "b", owed=True, arrived=4)
        self.assertFalse(floor.owed_pending_after(banter))   # 这句之前就叫了它：不算「期间」
        later = floor.claim(G, "c", owed=True, arrived=6)
        self.assertTrue(floor.owed_pending_after(banter))
        await floor.take(later)
        floor.step_down(later)                               # 已经说上了
        self.assertFalse(floor.owed_pending_after(banter))
        floor.release(earlier)
        floor.release(later)
        self.assertIsNone(floor.holder(G))


if __name__ == "__main__":
    unittest.main()
