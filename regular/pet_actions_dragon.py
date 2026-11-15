"""Dragon-themed desktop pet action catalogue.

Prompts describe four gentle, readable animation poses; shared character and sheet
constraints are included so each entry can also be used independently.
"""

_SHEET = (
    "Four-frame animation sprite sheet, four separate complete full-body poses arranged in a 2 columns by 2 rows grid, each cell 512 by 512 pixels in a 1024 by 1024 image, "
    "transparent background. The exact same original adult chibi dragon woman in every frame: "
    "silver-white and pale lavender hair, little dragon wings and tail, unchanged sage-green cardigan "
    "and original clothes. Exactly two arms, two hands, and two legs. Fixed camera, constant head size "
    "and body proportions; acting may lean, crouch, shift weight and use the wings and tail. "
    "Each figure and prop fully contained within its cell. Frame 1 establishes intent, frame 2 acts, "
    "frame 3 shows a visible consequence, and frame 4 settles into that result. A one-shot keeps its "
    "consequence instead of resetting the prop. Only a sustained accompaniment loop returns naturally "
    "toward its opening pose; do not confuse fixed scale with a frozen body. "
    "Tiny contained fire or magic only, no full-screen flashes. No text, borders, background, furniture, "
    "extra characters, extra limbs, or costume changes. "
)

# name, title, four-frame action, style, posture, role, family
_ROWS = [
    ("fire_palm", "掌心小火苗", "A tiny harmless flame grows and settles above her open palm.", "idle", "stand", "fire", "flame"),
    ("fire_marshmallow", "烤棉花糖", "Hold a marshmallow on a stick over a tiny flame and gently turn it.", "idle", "stand", "fire", "cooking"),
    ("fire_kettle", "热水壶", "Hold a miniature kettle and warm its base with a tiny flame, then watch a wisp of steam.", "tea", "sit", "fire", "cooking"),
    ("wing_brush", "梳理龙翼", "Reach back with a small soft brush and gently brush one wing.", "idle", "stand", "craft", "wing_care"),
    ("tail_ball", "尾巴滚球", "A brightly colored small ball rests beside her clearly visible long tail. Frame 1 tail tip touches ball on left, frame 2 tail pushes ball to the right, frame 3 tail tip touches the ball on right, frame 4 pushes it back left. Show tail tip physically touching ball and the complete tail and ball in every frame; hands remain at her sides.", "idle", "stand", "play", "tail"),
    ("tail_brush", "梳梳尾巴", "Hold her tail across her lap and stroke it with a small brush.", "read", "sit", "craft", "tail"),
    ("magic_flower", "变出小花", "Wave one hand over an empty palm and reveal a single small flower.", "idle", "stand", "magic", "conjure"),
    ("magic_feather", "漂浮羽毛", "Float a single feather above one palm and gently guide it with the other.", "idle", "stand", "magic", "levitation"),
    ("magic_snow", "掌心雪花", "Summon a single small snowflake above her palm and watch it melt.", "idle", "stand", "magic", "conjure"),
    ("treasure_count", "数金币", "Count a few gold coins one by one between her two hands.", "idle", "stand", "treasure", "coins"),
    ("treasure_polish", "擦宝石", "Polish one small gem with a soft cloth, then admire it.", "idle", "stand", "treasure", "gems"),
    ("treasure_hide", "藏小宝箱", "Hug a tiny closed treasure chest and tuck it beside her feet.", "idle", "stand", "treasure", "chest"),
    ("treasure_find", "找回硬币", "Notice one coin by her foot, reach down and retrieve it to her hand.", "idle", "stand", "treasure", "coins"),
    ("treasure_share", "分享宝石", "Present a single small gem forward in both open hands.", "idle", "stand", "treasure", "gems"),
    ("treasure_coin_spin", "转转金币", "Spin a single gold coin between her fingertips, then catch it.", "idle", "stand", "treasure", "coins"),
    ("toy_ball", "抛接小球", "Gently toss a small colorful ball a short distance between her hands.", "idle", "stand", "toy", "ball"),
    ("toy_handheld", "掌机游戏", "Hold a little handheld game console and tap its buttons with her thumbs.", "laptop", "sit", "toy", "game"),
    ("toy_kaleidoscope", "万花筒", "Hold a small kaleidoscope to one eye and gently rotate its end.", "idle", "stand", "toy", "optical"),
    ("music_ukulele", "弹尤克里里", "Hold a small ukulele and gently strum its strings.", "sing", "sit", "music", "strings"),
    ("music_kalimba", "弹拇指琴", "Hold a small kalimba on her lap and pluck its keys with both thumbs.", "sing", "sit", "music", "strings"),
    ("craft_sketch", "画小龙", "Draw with a pencil on a small sketchbook on her lap and lift the pencil.", "draw", "sit", "craft", "drawing"),
    ("craft_knit", "织小围巾", "Move two knitting needles gently over a tiny piece of soft yarn.", "draw", "sit", "craft", "yarn"),
    ("garden_water", "浇小花", "Tilt a miniature watering can toward a small potted flower held in her other hand.", "idle", "stand", "garden", "flower"),
    ("garden_sprout", "观察新芽", "Hold a tiny pot with a sprout and gently lift one leaf with a fingertip.", "idle", "stand", "garden", "planting"),
    ("snack_cookie", "掰小饼干", "Hold a small cookie and break off a bite-sized piece with both hands.", "tea", "sit", "snack", "cookie"),
    ("snack_berry", "尝小莓果", "Pick a berry from a tiny bowl and taste it, then lower her hand.", "tea", "sit", "snack", "fruit"),
]

ACTIONS = {
    f"dragon_{name}": {
        "title": title, "prompt": _SHEET + action, "style": style,
        "posture": posture, "role": role, "family": family,
        "fps": 2, "loop": True, "scale": 0.8 if posture == "sit" else 1.0,
        "duration": 8.0,
    }
    for name, title, action, style, posture, role, family in _ROWS
}

# The generated sheets below show standing figures despite their seated prompts.
# Match playback transitions and size to the actual artwork. dragon_snack_cookie was redrawn
# seated on 2026-11-15 (the old sheet stood with a seated figure's short body).
for _name in (
    "dragon_music_ukulele", "dragon_craft_sketch", "dragon_craft_knit", "dragon_snack_berry",
):
    ACTIONS[_name]["posture"] = "stand"
    ACTIONS[_name]["scale"] = 1.0

# These are small one-shot vignettes: their last frame does not return to the
# first, so repeating them would make a prop or gesture jump at the seam.
for _name in (
    "dragon_magic_flower", "dragon_magic_snow", "dragon_treasure_hide", "dragon_treasure_find",
    "dragon_treasure_share",
):
    ACTIONS[_name]["loop"] = False
    ACTIONS[_name]["duration"] = 2.0

# Keep the berry loop's prop, but use idle anatomy rather than its old short body.
ACTIONS["dragon_snack_berry"].update(
    style="idle", extra_refs=["dragon_snack_berry_ref"],
    calibration_reference="idle", calibration_frames=[0, 3], fixed_ground=True,
    prompt_header=(
        "Transparent 1024x1024 animation sheet, four complete full-body frames in a "
        "2x2 grid of 512px cells. Image 1 defines the original adult chibi dragon "
        "woman. Image 2 is the exact shipped idle and is authoritative for head "
        "diameter, FULL torso, collar-flower-to-dress-hem length, hips, thighs, shins "
        "and sneakers. Preserve the original clothes, horns, hair, flower, wings "
        "and tail. Image 3 supplies ONLY the tiny brown bowl, purple berries and "
        "hand action: do NOT copy its shortened baby torso or short legs. Exactly "
        "two arms, two human hands, two legs. Fixed camera, constant head AND body "
        "proportions in every cell, feet planted on one common support line. Full "
        "figure with margin, genuine alpha-zero background, no shadow or floor, "
        "no extra objects, letters, symbols or limbs."
    ),
    prompt=(
        "Stand in the same relaxed full-length idle stance throughout. The hand "
        "on the viewer's right cradles the SAME tiny shallow warm-brown bowl of "
        "dark purple berries as Image 3 at waist height in all four frames. The "
        "other hand takes one berry, never a spoon. Frame 1 fingertips pick a "
        "berry above the bowl, eyes open; frame 2 bring it to her lips and taste; "
        "frame 3 finish the small bite with a quiet pleased expression; frame 4 "
        "lower the empty hand back beside the bowl and reopen her eyes, matching "
        "frame 1 for a gentle loop. Bowl size, shape, color and holding hand stay "
        "unchanged. Her full-length cardigan and cream dress remain the exact "
        "idle length; no body compression under the head."
    ),
)
