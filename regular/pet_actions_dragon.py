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
    ("fire_candle", "吹灭蜡烛", "Hold a little candle, lean slightly and blow its tiny flame out, then smile.", "idle", "stand", "fire", "candle"),
    ("fire_teacup", "暖茶杯", "Cup a mug in both hands and warm it with a tiny harmless flame under it.", "tea", "sit", "fire", "cooking"),
    ("fire_lantern", "点亮灯笼", "Carefully light a tiny handheld lantern with a fingertip flame.", "idle", "stand", "fire", "candle"),
    ("fire_toast", "烤小吐司", "Hold a clearly square slice of bread on a short fork in one hand, toast it using a tiny flame from the other palm. Frame 1 bread is pale, frame 2 tiny flame touches bread, frame 3 bread has golden brown toast edges, frame 4 lower the flame. No marshmallow, no grill, no stove.", "idle", "stand", "fire", "cooking"),
    ("fire_spark", "火星跳舞", "Guide one tiny harmless spark as it hops between her two open palms.", "idle", "stand", "fire", "flame"),
    ("fire_kettle", "热水壶", "Hold a miniature kettle and warm its base with a tiny flame, then watch a wisp of steam.", "tea", "sit", "fire", "cooking"),
    ("wing_brush", "梳理龙翼", "Reach back with a small soft brush and gently brush one wing.", "idle", "stand", "craft", "wing_care"),
    ("wing_stretch", "舒展龙翼", "Slowly open both little wings halfway and fold them back without moving her feet.", "stretch", "stand", "play", "wing_care"),
    ("wing_polish", "擦亮翼尖", "Hold a soft cloth in one hand and carefully polish a wing tip.", "idle", "stand", "craft", "wing_care"),
    ("wing_ribbon", "整理翼结", "Adjust a tiny removable ribbon tied around one wing tip with both hands.", "idle", "stand", "craft", "wing_care"),
    ("tail_chase", "尾巴捉迷藏", "Her clearly visible long tail curls far out to her left in frame 1, swings behind her to the right in frame 2, curls out to the right in frame 3, then returns left in frame 4. Her eyes and pointing hand follow the tail. Show the complete tail in each cell, keeping body and feet still.", "idle", "stand", "play", "tail"),
    ("tail_bow", "尾尖蝴蝶结", "Hold her curled tail tip and carefully tie a little ribbon bow onto it.", "idle", "stand", "craft", "tail"),
    ("tail_ball", "尾巴滚球", "A brightly colored small ball rests beside her clearly visible long tail. Frame 1 tail tip touches ball on left, frame 2 tail pushes ball to the right, frame 3 tail tip touches the ball on right, frame 4 pushes it back left. Show tail tip physically touching ball and the complete tail and ball in every frame; hands remain at her sides.", "idle", "stand", "play", "tail"),
    ("tail_brush", "梳梳尾巴", "Hold her tail across her lap and stroke it with a small brush.", "read", "sit", "craft", "tail"),
    ("magic_star", "星星光球", "Roll one tiny starry orb slowly between her two palms.", "idle", "stand", "magic", "orb"),
    ("magic_book", "悬浮书本", "Guide a small open book hovering just above her hands as its page turns.", "read", "sit", "magic", "levitation"),
    ("magic_crane", "纸鹤飞飞", "Send one paper crane fluttering in a small arc just above her palms.", "idle", "stand", "magic", "levitation"),
    ("magic_flower", "变出小花", "Wave one hand over an empty palm and reveal a single small flower.", "idle", "stand", "magic", "conjure"),
    ("magic_portal", "迷你传送门", "Open a palm-sized glowing ring above one hand and peek at it as it closes.", "idle", "stand", "magic", "portal"),
    ("magic_crystal", "水晶球", "Cradle a small crystal ball and trace a tiny glowing star inside with one finger.", "idle", "stand", "magic", "orb"),
    ("magic_wand", "魔杖画星", "Draw a tiny star in the air with a short wand and watch it fade.", "idle", "stand", "magic", "wand"),
    ("magic_feather", "漂浮羽毛", "Float a single feather above one palm and gently guide it with the other.", "idle", "stand", "magic", "levitation"),
    ("magic_butterfly", "纸蝶魔法", "Transform a folded paper butterfly into a gently fluttering paper butterfly above her hands.", "idle", "stand", "magic", "conjure"),
    ("magic_moon", "小月亮", "Raise and lower a tiny crescent moon hovering between her hands.", "idle", "stand", "magic", "orb"),
    ("magic_snow", "掌心雪花", "Summon a single small snowflake above her palm and watch it melt.", "idle", "stand", "magic", "conjure"),
    ("magic_bubble", "泡泡魔法", "Tap a tiny floating iridescent bubble with a wand and let it vanish.", "idle", "stand", "magic", "wand"),
    ("treasure_count", "数金币", "Count a few gold coins one by one between her two hands.", "idle", "stand", "treasure", "coins"),
    ("treasure_polish", "擦宝石", "Polish one small gem with a soft cloth, then admire it.", "idle", "stand", "treasure", "gems"),
    ("treasure_hide", "藏小宝箱", "Hug a tiny closed treasure chest and tuck it beside her feet.", "idle", "stand", "treasure", "chest"),
    ("treasure_pouch", "护住钱袋", "Hug a small coin pouch close and gently pat its tied top.", "idle", "stand", "treasure", "pouch"),
    ("treasure_find", "找回硬币", "Notice one coin by her foot, reach down and retrieve it to her hand.", "idle", "stand", "treasure", "coins"),
    ("treasure_share", "分享宝石", "Present a single small gem forward in both open hands.", "idle", "stand", "treasure", "gems"),
    ("treasure_sort", "整理宝石", "Sort three differently colored little gems into a small handheld tray.", "read", "sit", "treasure", "gems"),
    ("treasure_open", "打开宝箱", "Open a tiny chest held on her lap, peek inside, then close the lid.", "read", "sit", "treasure", "chest"),
    ("treasure_coin_spin", "转转金币", "Spin a single gold coin between her fingertips, then catch it.", "idle", "stand", "treasure", "coins"),
    ("treasure_key", "宝箱钥匙", "Turn a small key in the lock of a tiny treasure chest she holds.", "idle", "stand", "treasure", "chest"),
    ("toy_ball", "抛接小球", "Gently toss a small colorful ball a short distance between her hands.", "idle", "stand", "toy", "ball"),
    ("toy_top", "转陀螺", "Twist a small spinning top in her palm and watch it turn.", "idle", "stand", "toy", "spinning"),
    ("toy_plane", "纸飞机", "Fold the wing of a paper airplane and glide it a short distance in her hand.", "idle", "stand", "toy", "paper"),
    ("toy_pinwheel", "吹风车", "Hold a small pinwheel and blow softly so its blades rotate.", "idle", "stand", "toy", "spinning"),
    ("toy_clockwork", "发条玩具", "Wind a tiny clockwork bird held in her palm and watch its wings move.", "idle", "stand", "toy", "clockwork"),
    ("toy_bubbles", "吹泡泡", "Dip a bubble wand and blow one or two small bubbles beside her face.", "idle", "stand", "toy", "bubbles"),
    ("toy_puzzle", "拼图时间", "Fit a single puzzle piece into a small puzzle board on her lap.", "read", "sit", "toy", "puzzle"),
    ("toy_handheld", "掌机游戏", "Hold a little handheld game console and tap its buttons with her thumbs.", "laptop", "sit", "toy", "game"),
    ("toy_musicbox", "打开音乐盒", "Open a tiny music box on her lap and turn its crank gently.", "read", "sit", "music", "musicbox"),
    ("toy_yoyo", "悠悠球", "Move a small yo-yo down and up on a short string beside her hand.", "idle", "stand", "toy", "string"),
    ("toy_blocks", "叠积木", "Stack a few small colorful blocks on a little board on her lap.", "read", "sit", "toy", "blocks"),
    ("toy_kaleidoscope", "万花筒", "Hold a small kaleidoscope to one eye and gently rotate its end.", "idle", "stand", "toy", "optical"),
    ("music_ukulele", "弹尤克里里", "Hold a small ukulele and gently strum its strings.", "sing", "sit", "music", "strings"),
    ("music_flute", "吹小笛子", "Hold a small flute at her lips and lift and lower her fingers on its holes.", "sing", "stand", "music", "wind"),
    ("music_bell", "摇小铃铛", "Shake a single small handbell gently with one hand.", "sing", "stand", "music", "percussion"),
    ("music_kalimba", "弹拇指琴", "Hold a small kalimba on her lap and pluck its keys with both thumbs.", "sing", "sit", "music", "strings"),
    ("music_tambourine", "摇铃鼓", "Tap a tiny handheld tambourine with the fingers of her other hand.", "sing", "stand", "music", "percussion"),
    ("music_harmonica", "吹口琴", "Hold a small harmonica in both hands and sway it gently at her lips.", "sing", "sit", "music", "wind"),
    ("craft_sketch", "画小龙", "Draw with a pencil on a small sketchbook on her lap and lift the pencil.", "draw", "sit", "craft", "drawing"),
    ("craft_paint", "画水彩", "Dab a fine brush into a tiny watercolor palette and paint a small card.", "draw", "sit", "craft", "painting"),
    ("craft_origami", "折纸星星", "Fold a strip of paper carefully into a small paper star with both hands.", "draw", "sit", "craft", "paper"),
    ("craft_knit", "织小围巾", "Move two knitting needles gently over a tiny piece of soft yarn.", "draw", "sit", "craft", "yarn"),
    ("craft_stamp", "盖小印章", "Press a tiny stamp onto a blank card and lift it to look at the mark.", "draw", "sit", "craft", "paper"),
    ("craft_clay", "捏小陶星", "Shape a little clay star between both hands and smooth its points.", "draw", "sit", "craft", "clay"),
    ("garden_water", "浇小花", "Tilt a miniature watering can toward a small potted flower held in her other hand.", "idle", "stand", "garden", "flower"),
    ("garden_seed", "播种", "Place one seed into a little flowerpot held in her lap and pat the soil.", "read", "sit", "garden", "planting"),
    ("garden_sprout", "观察新芽", "Hold a tiny pot with a sprout and gently lift one leaf with a fingertip.", "idle", "stand", "garden", "planting"),
    ("garden_prune", "修剪花叶", "Carefully trim one leaf on a handheld little plant with tiny safe craft scissors.", "idle", "stand", "garden", "flower"),
    ("snack_cookie", "掰小饼干", "Hold a small cookie and break off a bite-sized piece with both hands.", "tea", "sit", "snack", "cookie"),
    ("snack_berry", "尝小莓果", "Pick a berry from a tiny bowl and taste it, then lower her hand.", "tea", "sit", "snack", "fruit"),
    ("snack_stir", "搅拌热可可", "Stir a little mug of cocoa with a spoon and watch a wisp of steam.", "tea", "sit", "snack", "drink"),
    ("play_bubble_catch", "接泡泡", "Reach out with one open palm to catch a single small floating bubble.", "idle", "stand", "play", "bubbles"),
    ("play_paper_fan", "摇纸扇", "Open a small folding paper fan and wave it gently in front of her chest.", "idle", "stand", "play", "paper"),
    ("play_ribbon", "舞彩带", "Draw a small slow loop with a short ribbon wand beside her body.", "idle", "stand", "play", "ribbon"),
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
# Match playback transitions and size to the actual artwork.
for _name in (
    "dragon_magic_book", "dragon_music_ukulele", "dragon_music_harmonica",
    "dragon_craft_sketch", "dragon_craft_paint", "dragon_craft_origami",
    "dragon_craft_knit", "dragon_craft_stamp", "dragon_craft_clay",
    "dragon_snack_cookie", "dragon_snack_berry", "dragon_snack_stir",
    "dragon_treasure_sort",
):
    ACTIONS[_name]["posture"] = "stand"
    ACTIONS[_name]["scale"] = 1.0

# These are small one-shot vignettes: their last frame does not return to the
# first, so repeating them would make a prop or gesture jump at the seam.
for _name in (
    "dragon_fire_candle", "dragon_fire_lantern", "dragon_fire_toast",
    "dragon_magic_book", "dragon_magic_crane", "dragon_magic_flower",
    "dragon_magic_portal", "dragon_magic_butterfly", "dragon_magic_snow",
    "dragon_magic_bubble", "dragon_treasure_hide", "dragon_treasure_find",
    "dragon_treasure_share", "dragon_treasure_open", "dragon_treasure_key",
    "dragon_toy_plane", "dragon_toy_bubbles", "dragon_toy_clockwork",
    "dragon_toy_musicbox", "dragon_craft_origami", "dragon_craft_stamp",
    "dragon_garden_seed", "dragon_play_bubble_catch", "dragon_play_ribbon",
):
    ACTIONS[_name]["loop"] = False
    ACTIONS[_name]["duration"] = 2.0
