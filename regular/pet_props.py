"""Transient desktop toy physics, independent of the UI and QQ state."""

from dataclasses import dataclass
import math


@dataclass
class Prop:
    id: int
    kind: str
    x: float
    y: float
    radius: float
    vx: float
    vy: float
    born: float
    ttl: float
    dragging: bool = False
    grounded: bool = False


class PropPlay:
    """Keep a small bounded set of interactive, short-lived props."""

    KINDS = frozenset(("ball", "coin", "gem", "star", "orb", "feather"))

    def __init__(self, rng):
        self.rng = rng
        self.items = []
        self._next_id = 1
        self._last_tick = None
        self._bounds = None

    def emit(self, kind, x, y, now, scale=1):
        if kind not in self.KINDS:
            raise ValueError(f"unknown prop kind: {kind}")
        persistent = [item for item in self.items if item.kind in ("ball", "coin", "gem")]
        if kind in ("ball", "coin", "gem") and len(persistent) >= 2:
            removable = next((item for item in persistent if not item.dragging), None)
            if removable is None:
                return None
            self.items.remove(removable)
        item = Prop(self._next_id, kind, float(x), float(y), max(1.0, 12.0 * scale),
                    self.rng.uniform(-35, 35), self.rng.uniform(-65, -25),
                    now, self.rng.uniform(20, 60) if kind in ("ball", "coin", "gem") else self.rng.uniform(4, 7))
        self._next_id += 1
        self.items.append(item)
        return item.id

    def tick(self, now, left, right, ceiling, floor):
        elapsed = 0.0 if self._last_tick is None else max(0.0, min(0.1, now - self._last_tick))
        self._last_tick = now
        self._bounds = (left, right, ceiling, floor)
        self.items[:] = [item for item in self.items if now - item.born < item.ttl]
        for item in self.items:
            if item.dragging:
                continue
            airy = item.kind in ("star", "orb", "feather")
            if not item.grounded:
                item.vy = min(110.0 if airy else 350.0, item.vy + (20.0 if airy else 300.0) * elapsed)
            elif not airy:
                item.vx *= math.exp(-4.0 * elapsed)
                if abs(item.vx) < 1.0:
                    item.vx = 0.0
            if airy:
                item.vx += math.sin(now * 2 + item.id) * 12 * elapsed
            item.x += item.vx * elapsed
            item.y += item.vy * elapsed
            min_x, max_x = left + item.radius, max(left + item.radius, right - item.radius)
            min_y, max_y = ceiling + item.radius, max(ceiling + item.radius, floor - item.radius)
            if item.x < min_x or item.x > max_x:
                item.x = min(max(item.x, min_x), max_x)
                item.vx *= -0.65
            if item.y < min_y:
                item.y = min_y
                item.vy = abs(item.vy) * 0.6
                item.grounded = False
            elif item.y >= max_y:
                item.y = max_y
                if not airy and abs(item.vy) < 35:
                    item.vy = 0.0
                    item.grounded = True
                else:
                    item.vy = -abs(item.vy) * 0.6
                    item.vx *= 0.9
                    item.grounded = False
            elif item.grounded:
                # The screen floor may move when a monitor changes.
                item.grounded = False

    def candidates(self, now):
        """Stationary ground toys the pet may approach, still subject to recheck on arrival."""
        return tuple(item for item in self.items if item.kind in ("ball", "coin", "gem")
                     and now - item.born < item.ttl and not item.dragging and item.grounded
                     and item.vx == 0 and item.vy == 0)

    def kick(self, id, now, direction):
        """Kick an eligible ball horizontally in the requested direction."""
        item = next((item for item in self.candidates(now) if item.id == id and item.kind == "ball"), None)
        if item is None or direction == 0:
            return False
        item.vx = 110.0 if direction > 0 else -110.0
        item.vy = -90.0
        item.grounded = False
        return True

    def collect(self, id, now):
        """Remove an eligible treasure; a stale, dragged or collected item is ignored."""
        item = next((item for item in self.candidates(now) if item.id == id and item.kind in ("coin", "gem")), None)
        if item is None:
            return False
        self.items.remove(item)
        return True

    def discard(self, id, now):
        """Return one still-live persistent toy to the pet."""
        item = self._find(id)
        if item is None or item.kind not in ("ball", "coin", "gem") or now - item.born >= item.ttl:
            return None
        self.items.remove(item)
        return item.kind

    def hit(self, x, y):
        for item in reversed(self.items):
            if (item.x - x) ** 2 + (item.y - y) ** 2 <= item.radius ** 2:
                return item.id
        return None

    def tap(self, id, now):
        item = self._find(id)
        if item is None or now - item.born >= item.ttl:
            return None
        if item.kind in ("coin", "gem"):
            self.items.remove(item)
        elif item.kind == "ball":
            item.vy = -150.0
            item.grounded = False
        else:
            item.vy = -45.0
        return item.kind

    def drag(self, id, x, y, now):
        item = self._find(id)
        if item is None or now - item.born >= item.ttl:
            return False
        if self._bounds is not None:
            left, right, ceiling, floor = self._bounds
            x = min(max(x, left + item.radius), max(left + item.radius, right - item.radius))
            y = min(max(y, ceiling + item.radius), max(ceiling + item.radius, floor - item.radius))
        item.x, item.y, item.dragging = float(x), float(y), True
        item.vx = item.vy = 0.0
        item.grounded = False
        return True

    def release(self, id, now, vx=0.0, vy=0.0):
        item = self._find(id)
        if item is None or now - item.born >= item.ttl or not item.dragging:
            return False
        item.dragging = False
        item.vx = max(-500.0, min(500.0, float(vx)))
        item.vy = max(-500.0, min(500.0, float(vy)))
        item.grounded = False
        return True

    def _find(self, id):
        return next ( (item for item in self.items if item.id == id), None )
