"""桌宠动作库：生成规格与已经采用的运行素材共用一份目录。"""
from __future__ import annotations

from importlib.resources import files
import json
from collections import deque

from regular.pet_actions_motion import ACTIONS as MOTION
from regular.pet_actions_dragon import ACTIONS as DRAGON
from regular.pet_actions_life import ACTIONS as LIFE
from regular.pet_actions_boundary import ACTIONS as BOUNDARY
from regular.pet_actions_spell import ACTIONS as SPELL

ACTIONS = {**MOTION, **DRAGON, **LIFE, **BOUNDARY, **SPELL}


def available():
    """Only adopted strips take part in runtime selection."""
    manifest = json.loads(files("regular").joinpath("assets", "pet", "sprites.json").read_text(encoding="utf-8"))
    return {name: dict(spec, geometry=manifest.get("geometry", {}).get(name))
            for name, spec in ACTIONS.items() if name in manifest["actions"]}


def clips(actions=None):
    actions = available() if actions is None else actions
    return {name: (name, spec.get("sequence", [0, 1, 2, 3]), spec["fps"], spec["loop"], spec["posture"])
            for name, spec in actions.items()}


class Repertoire:
    """语义池整袋洗牌，加上全局近期去重。"""
    def __init__(self, rng, actions=None):
        self.actions = available() if actions is None else actions
        self.rng, self.bags, self.recent = rng, {}, deque(maxlen=24)

    def choose(self, role, *, posture=None, family=None, fallback=None):
        key = (role, posture, family)
        pool = [name for name, spec in self.actions.items()
                if spec["role"] == role and (posture is None or spec["posture"] == posture)
                and (family is None or spec["family"] == family)]
        if not pool:
            return fallback
        bag = self.bags.setdefault(key, [])
        if not bag:
            bag.extend(pool)
            self.rng.shuffle(bag)
        candidates = [name for name in bag if name not in self.recent]
        if not candidates:
            candidates = [name for name in bag if not self.recent or name != self.recent[-1]] or bag
        name = self.rng.choice(candidates)
        bag.remove(name)
        self.recent.append(name)
        return name
