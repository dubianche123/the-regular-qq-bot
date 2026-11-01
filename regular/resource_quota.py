"""代码持有的群友每日生图和搜索额度；额外份额取决于关系档位。"""
import logging
import time

import config
import relations

logger = logging.getLogger("qqbot")


def limits(kind, score):
    """返回基础额度和当前关系档位的额外额度。"""
    prefix = {"image": "IMAGE", "search": "SEARCH"}[kind]
    base = getattr(config, f"{prefix}_USER_DAILY_CAP")
    tier = relations.level_key(score)
    suffix = {"acquaintance": "ACQUAINTANCE", "friend": "FRIEND",
              "buddy": "BUDDY", "soulmate": "SOULMATE"}.get(tier)
    extra = getattr(config, f"{prefix}_USER_EXTRA_{suffix}") if suffix else 0
    return base, extra


class ResourceQuotas:
    """持久化的群内个人日额；仅首次进入额外份额返回 exception。"""

    def __init__(self, state, mark_dirty, save, today=None):
        self._state = state
        self._mark_dirty = mark_dirty
        self._save = save
        self._today = today or (lambda: time.strftime("%Y-%m-%d", time.localtime()))

    def _used(self, kind, group, user):
        if self._state.get("day") != self._today():
            return 0
        return self._state.get("counts", {}).get(kind, {}).get(group, {}).get(user, 0)

    def check(self, kind, group, user, score):
        """不改状态。normal / exception / extra / exhausted / unavailable。"""
        if not group or not user:
            return "unavailable"
        base, extra = limits(kind, score)
        used = self._used(kind, group, user)
        if used >= base + extra:
            return "exhausted"
        if used < base:
            return "normal"
        return "exception" if used == base else "extra"

    def consume(self, kind, group, user, score):
        """在请求前同步记账并落盘；失败时不允许调用，次数保守留在内存。"""
        result = self.check(kind, group, user, score)
        if result in ("exhausted", "unavailable"):
            return result
        today = self._today()
        if self._state.get("day") != today:
            self._state.clear()
            self._state.update(day=today, counts={})
        users = self._state.setdefault("counts", {}).setdefault(kind, {}).setdefault(group, {})
        users[user] = users.get(user, 0) + 1
        self._mark_dirty()
        try:
            self._save()
        except Exception as exc:
            logger.warning("资源额度落盘失败: %s", type(exc).__name__)
            return "unavailable"
        return result
