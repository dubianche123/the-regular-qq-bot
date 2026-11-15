"""A dragon's abilities solve small everyday wishes; each sheet keeps its consequence.

2026-11-15 重画：群主说「变个魔法」里很多看不出是什么魔法，动作也卡。旧稿四帧、每帧停一秒多，
道具和吹出来的气在桌面尺寸上只有几个像素。现在每段六拍（想要 → 准备 → 施展 → 见效 → 享受 → 收好），
道具画大、手势画清楚；吹气、火苗、热气、金线这些特效不再画进图里，而由引擎在对应那一拍从嘴边或指尖
发出粒子（见 effects），所以小尺寸也看得清，帧与帧之间再叠一小段交叉淡入（frame_blend）。
开场那句话（wish）说明她想做什么。
"""

_HEADER = (
    "Use case: illustration-story. Transparent 1536x1024 sprite sheet, exactly SIX "
    "complete character frames in a 3x2 grid, each cell 512x512, reading left to right, "
    "top row first. Image 1 is the original adult dragon-woman; Image 2 is the exact "
    "shipped idle body reference. COPY its head diameter, neck-to-dress-hem length, hip "
    "position, thigh and shin lengths, hands and shoes in every cell: never a larger "
    "head on a shorter baby body. Silver-lavender hair, lilac horns, green flower clip, "
    "sage cardigan, cream dress, lavender scarf, white socks and sneakers, two bat wings "
    "and one scaled tail. Exactly TWO arms, TWO connected hands and TWO legs. Fixed "
    "camera and character scale, same shoe support line; all six poses remain standing. "
    "The six frames are consecutive beats of ONE continuous motion, like animation "
    "key poses: each frame differs from the previous one by a clear but small change, "
    "so playing them in order looks smooth, not like six unrelated poses. The prop is "
    "drawn LARGE and simple so it reads at a small desktop size: about as wide as her "
    "face, held in front of her chest on the viewer's right side, never covering her "
    "face. Do NOT draw breath, flames, frost, steam, glow, sparkles or motion lines: "
    "those effects are added later by the program. Facial expression and body language "
    "must tell the story on their own. Keep the EXACT same prop in consecutive frames. "
    "Frame 6 ends with NO loose prop in front of her: her hand physically carries a used "
    "object behind her hip/tail to put it away, never erase an object in mid-air. No "
    "invented bag, table, floor, shadow, background, letters, numbers or panel borders. "
    "Entire character and props fit inside each cell with transparent margins. Real "
    "alpha transparency."
)

# (title, wish line said at the start, six beats)
_SCENES = {
    "cola": ("想喝冰可乐", "好热……来点冰的！", (
        "Frame 1: too hot: she holds a LARGE clear glass of brown cola with a straw at "
        "chest height and fans her flushed cheek with the free hand, tired half-closed "
        "eyes. Frame 2: she looks at the glass, eyes light up with an idea, ears perk. "
        "Frame 3: she breathes in, cheeks puffed round, glass raised a little toward her "
        "mouth. Frame 4: lips pursed in an 'o', blowing gently toward the SAME glass "
        "(no visible breath), the glass now frosted pale blue-white with little ice cubes "
        "inside. Frame 5: she sips through the straw from the SAME frosted glass, eyes "
        "closed in bliss, shoulders relaxed, tail tip curled happily. Frame 6: refreshed "
        "smile, the free hand touches her cooled cheek while the other hand carries the "
        "empty glass behind her hip until it is hidden."
    )),
    "roast": ("自己吐火烤棉花糖", "想吃烤棉花糖～", (
        "Frame 1: she holds ONE big white marshmallow on a short wooden skewer at chest "
        "height and stares at it hungrily, tail curling. Frame 2: deep breath in, chest "
        "lifted, cheeks puffed, eyes on the marshmallow. Frame 3: lips pursed toward the "
        "SAME marshmallow (no flame drawn), eyes focused, the marshmallow already turning "
        "golden at its tip. Frame 4: the marshmallow is toasted golden-brown all over; "
        "she blows on it softly to cool it, pleased. Frame 5: she bites it off the SAME "
        "skewer, eyes closed, cheek round and happy, a little stretchy string. Frame 6: "
        "chewing contentedly, free hand on her tummy, the other hand carries the empty "
        "skewer behind her hip until hidden."
    )),
    "scarf": ("暖息吹干围巾", "围巾湿了……烘一下。", (
        "Frame 1: she holds out the end of her OWN lavender scarf with both hands; it is "
        "clearly soggy, darker and drooping, she looks dismayed. Frame 2: she lifts that "
        "SAME scarf end toward her face and breathes in, cheeks puffed. Frame 3: lips "
        "pursed blowing along the scarf (no visible breath), eyes half closed, the scarf "
        "already lighter at one end. Frame 4: the SAME scarf is dry, light lavender and "
        "fluffy again; she holds it up, pleased. Frame 5: she wraps it snugly around her "
        "neck with both hands. Frame 6: she nuzzles her chin into the warm scarf, eyes "
        "closed, both hands free, cozy smile, tail wrapped by her feet."
    )),
    "brush": ("悬浮小刷子梳尾巴", "尾巴那块够不着……", (
        "Keep the FULL idle torso length; look toward the tail with a head turn, not a "
        "hunch. Frame 1: she twists to reach a ruffled patch near the tip of her OWN tail "
        "with a small wooden grooming brush in one hand, but it is out of reach, puzzled "
        "frown. Frame 2: she lets go: the SAME brush floats in the air beside her, she "
        "points at it with one finger. Frame 3: the floating brush moves along the "
        "ruffled part of the tail, bristles touching the scales, she watches amused. "
        "Frame 4: the tail is smooth and shiny, the brush hovering at its tip, she smiles. "
        "Frame 5: the SAME brush floats back into her open palm, the tail wags happily. "
        "Frame 6: she tucks the brush behind her hip until hidden, the other hand strokes "
        "her comfortable tail, eyes softly pleased."
    )),
    "mend": ("把书角修好再收起", "书角破了，修一修。", (
        "Frame 1: she holds a LARGE tan book open toward herself, noticing a big torn "
        "triangular top corner flap on its cover, sad face. Frame 2: she turns the SAME "
        "book slightly so the torn corner faces the viewer, and points one fingertip at "
        "it. Frame 3: her fingertip touches the tear, eyes focused, the flap half lifted "
        "back into place. Frame 4: the corner is whole and flat again; she checks it "
        "closely, small absorbed smile. Frame 5: she closes the SAME book and hugs it to "
        "her chest happily. Frame 6: she carries the closed book behind her hip until it "
        "is hidden, the other hand on her chest, reassured."
    )),
}

# Beats at 4 fps: want, ready, cast, works, enjoy, put away (~6.5 s each).
_BEAT_TICKS = {
    "cola": (5, 3, 3, 5, 5, 4),
    "roast": (5, 3, 5, 4, 5, 4),
    "scarf": (5, 3, 5, 4, 4, 4),
    "brush": (5, 3, 6, 4, 4, 4),
    "mend": (5, 3, 5, 4, 4, 4),
}

# Effects in figure heights from her feet; x points the way she faces. The mouth sits about
# 0.08 in front and 0.72 up, the held prop about 0.22 in front at chest height.
_EFFECTS = {
    "cola": [
        {"frames": [0], "kind": "sweat", "at": [0.05, -0.92], "every": 1.1},
        {"frames": [3], "kind": "frost", "at": [0.1, -0.72], "toward": [0.26, -0.52], "every": 0.05},
        {"frames": [4], "kind": "frost", "at": [0.24, -0.5], "toward": [0.3, -0.75], "every": 0.22},
        {"frames": [5], "kind": "sparkle", "at": [0.1, -0.9], "every": 0.6},
    ],
    "roast": [
        {"frames": [2], "kind": "flame", "at": [0.1, -0.72], "toward": [0.3, -0.58], "every": 0.04},
        {"frames": [3], "kind": "steam", "at": [0.3, -0.58], "toward": [0.32, -0.9], "every": 0.25},
        {"frames": [4], "kind": "heart", "at": [0.12, -0.95], "every": 0.9},
    ],
    "scarf": [
        {"frames": [0], "kind": "drip", "at": [0.22, -0.5], "toward": [0.22, -0.1], "every": 0.35},
        {"frames": [2], "kind": "heat", "at": [0.1, -0.72], "toward": [0.3, -0.55], "every": 0.06},
        {"frames": [3], "kind": "steam", "at": [0.24, -0.6], "toward": [0.28, -0.95], "every": 0.2},
        {"frames": [5], "kind": "heart", "at": [0.1, -0.95], "every": 0.9},
    ],
    "brush": [
        {"frames": [1, 2, 3, 4], "kind": "violet", "at": [-0.3, -0.35], "toward": [-0.42, -0.2],
         "every": 0.08},
        {"frames": [4], "kind": "sparkle", "at": [-0.35, -0.25], "every": 0.4},
    ],
    "mend": [
        {"frames": [2, 3], "kind": "gold", "at": [0.22, -0.62], "toward": [0.3, -0.7], "every": 0.05},
        {"frames": [4], "kind": "sparkle", "at": [0.15, -0.6], "every": 0.5},
    ],
}

# The first cola/roast/scarf drafts kept the idle head but drew the body ~10% short; say it first.
_FULL_HEIGHT = (
    "BODY HEIGHT FIRST: she is exactly as TALL as Image 2 in every frame: the same long sage cardigan "
    "reaching mid-thigh, the same dress length and the same long legs; the top of her hair is as high "
    "above her shoes as in Image 2. Do not draw a slimmer, shorter or smaller version of her. "
)

ACTIONS = {
    "magic_daily_" + name: {
        "title": title, "wish": wish, "prompt_header": _HEADER, "prompt": _FULL_HEIGHT + prompt, "style": "idle",
        "starts_from": "stand", "posture": "stand", "role": "magic_daily", "family": name,
        "automatic": False, "loop": False, "fps": 4,
        "sequence": [frame for frame, ticks in enumerate(_BEAT_TICKS[name]) for _ in range(ticks)],
        "duration": sum(_BEAT_TICKS[name]) / 4,
        "size": "1536x1024", "cells": [0, 1, 2, 3, 4, 5], "frame_blend": 0.16,
        "effects": _EFFECTS[name], **({} if name == "brush" else {"drop_marks": 0.02}),   # the brush floats free
        "scale": 1.0, "fixed_ground": True, "calibration_reference": "idle", "calibration_frames": [0, 5],
    }
    for name, (title, wish, prompt) in _SCENES.items()
}
