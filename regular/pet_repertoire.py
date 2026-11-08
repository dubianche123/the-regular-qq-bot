"""桌宠动作库：生成规格与已经采用的运行素材共用一份目录。"""
from __future__ import annotations

from importlib.resources import files
import json
from collections import deque

from regular.pet_actions_motion import ACTIONS as MOTION
from regular.pet_actions_dragon import ACTIONS as DRAGON
from regular.pet_actions_life import ACTIONS as LIFE
from regular.pet_actions_boundary import ACTIONS as BOUNDARY, PLAYBACK_ALIASES as BOUNDARY_ALIASES
from regular.pet_actions_spell import ACTIONS as SPELL
from regular.pet_actions_scenes import ACTIONS as SCENES
from regular.pet_actions_dragon_form import ACTIONS as FORM
from regular.pet_actions_daily import ACTIONS as DAILY, PLAYBACK_ALIASES as DAILY_ALIASES
from regular.pet_actions_edge_daily import ACTIONS as EDGE_DAILY
from regular.pet_actions_air_play import ACTIONS as AIR_PLAY, PLAYBACK_ALIASES as AIR_PLAY_ALIASES
from regular.pet_actions_endings import ACTIONS as ENDINGS

ACTIONS = {**MOTION, **DRAGON, **LIFE, **BOUNDARY, **SPELL, **SCENES, **FORM, **DAILY, **EDGE_DAILY, **AIR_PLAY, **ENDINGS}
PLAYBACK_ALIASES = {**DAILY_ALIASES, **BOUNDARY_ALIASES, **AIR_PLAY_ALIASES}

# The archive is not a playlist. Pick performances, not a quota per prop family.
# Scripted endings still use the full catalog.
CURATED_POOLS = {
    "music": ("dragon_music_ukulele", "dragon_music_kalimba"),
    "treasure": ("dragon_treasure_polish", "dragon_treasure_coin_spin"),
    "toys": ("dragon_toy_handheld", "dragon_toy_kaleidoscope", "dragon_toy_ball"),
    "craft": ("dragon_craft_knit", "dragon_craft_sketch"),
    "garden": ("dragon_garden_water", "dragon_garden_sprout"),
    "dragon_care": ("dragon_tail_brush", "dragon_wing_brush", "dragon_tail_ball"),
    "magic": ("dragon_magic_snow", "dragon_magic_flower", "dragon_magic_feather"),
    "fire": ("dragon_fire_palm", "dragon_fire_marshmallow"),
    "snack": ("dragon_snack_cookie", "dragon_snack_berry"),
    "reading": ("life_comic_turn", "life_note_read"),
    "work": ("life_laptop_type", "life_laptop_think"),
    "tea": ("life_tea_sip", "life_tea_cool"),
    "care": ("life_hair_comb", "life_tail_pillow", "life_water"),
    "curious": ("life_clock_check", "life_chat_popcorn"),
    "sleep": ("life_sleep_hug_tail", "life_sleep_wing_blanket", "life_sleep_face_down"),
    # Real reply states retain their posture and mood, beyond a numeric quota.
    "reply": ("life_reply_air_phone", "life_reply_sit_calm", "life_reply_sit_sleepy",
              "life_reply_stand_bright", "life_reply_stand_think"),
    "inspect": ("life_image_photo", "life_image_magnify", "life_phone_read"),
    "listen": ("life_voice_headphone", "life_chat_nod"),
    "search": ("life_search_book", "life_search_laptop"),
    "drawing": ("life_drawing_pencil", "life_drawing_tablet"),
    "voice": ("life_voice_record", "life_voice_mic_check"),
    "celebrate": ("life_sent_confetti", "life_sent_check"),
    "greet": ("life_phone_notice", "life_phone_greet"),
    "eat": ("life_bread_bite", "life_riceball", "life_candy_unwrap"),
}
_CURATED = frozenset(name for pool in CURATED_POOLS.values() for name in pool)
_LEGACY = frozenset(DRAGON) | frozenset(LIFE)


def curated_actions(actions):
    """Select everyday actors; keep motion and explicit scenes addressable.

    Retired legacy art remains loadable for scripted endings and older installs.
    New catalogs are not silently excluded by a filename-prefix filter.
    """
    return {name: spec for name, spec in actions.items()
            if spec.get("automatic", True) and (name not in _LEGACY or name in _CURATED)}


def available():
    """Only adopted strips take part in runtime selection."""
    manifest = json.loads(files("regular").joinpath("assets", "pet", "sprites.json").read_text(encoding="utf-8"))
    actions = {name: dict(spec, geometry=manifest.get("geometry", {}).get(name))
               for name, spec in ACTIONS.items() if name in manifest["actions"]}
    for name, spec in PLAYBACK_ALIASES.items():
        if spec["strip"] in actions:
            actions[name] = dict(spec, geometry=actions[spec["strip"]].get("geometry"))
    return actions


def clips(actions=None):
    actions = available() if actions is None else actions
    return {name: (spec.get("strip", name), spec.get("sequence", [0, 1, 2, 3]), spec["fps"], spec["loop"], spec["posture"])
            for name, spec in actions.items()}


class Repertoire:
    """语义池整袋洗牌，加上全局近期去重。"""
    def __init__(self, rng, actions=None):
        self.actions = available() if actions is None else actions
        self.rng, self.bags, self.recent = rng, {}, deque(maxlen=24)

    def choose(self, role, *, posture=None, family=None, loop=None, fallback=None):
        key = (role, posture, family, loop)
        pool = [name for name, spec in curated_actions(self.actions).items()
                if spec["role"] == role and (posture is None or spec["posture"] == posture)
                and (family is None or spec["family"] == family)
                and (loop is None or spec["loop"] == loop)]
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
