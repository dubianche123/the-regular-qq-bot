"""Daily performances with a physical choice and a distinct emotional ending."""

_HEADER = (
    "Create a 1024x1024 transparent ANIMATION SPRITE SHEET: exactly four frames in a 2x2 grid, "
    "each cell 512x512, read left-to-right then top-to-bottom. Image 1 is the exact original ADULT "
    "chibi dragon-woman character model; Image 2 provides her face, outfit, head-to-body proportions, "
    "linework and palette, not a pose to copy. Silver-white lavender hair and ahoge, pearl-lilac curved "
    "horns, pointed ears, lavender eyes, green clip and ivory flower, lavender scarf, sage knitted "
    "cardigan, cream dress, white socks and sneakers, two small lavender bat wings and one scaled tail. "
    "Keep the SAME head diameter, torso length, limb proportions and camera scale in all four frames, "
    "matching the standing reference. Never enlarge the head or shorten the body to fit a pose. "
    "Exactly two arms, two connected hands and two legs. Full character, horns, wings, tail, shoes and "
    "all props completely within each cell, at least 30px transparent margin on every side; no touching "
    "or overlapping adjacent cells. Fixed camera and common ground line; expressive weight shifts "
    "are welcome. No background, visible floor, cast shadow, text, numbers, grid, borders, other "
    "characters or disembodied hands. Empty space must be alpha-zero, never a gray gradient, white "
    "paper, glow, checkerboard texture or translucent backdrop. The NEW four-frame action below "
    "determines the posture and props."
)

# name, title, style reference, start, end, family, four-frame direction
_ROWS = [
    ("cookie_receive", "接过饼干认真吃", "idle", "stand", "stand", "cookie",
     "One offered round chocolate-chip cookie. Frame 1: look up expectantly, lean forward slightly "
     "and extend both EMPTY palms. Frame 2: receive ONE cookie in both hands and draw it close to "
     "her chest, delighted. Frame 3: take one small bite, cheeks softly puffed, holding the SAME "
     "partly eaten cookie. Frame 4: the cookie is finished, both palms EMPTY, contented smile and "
     "tail tip lifted; settle her weight naturally. No extra hand, plate, food or duplicated cookie."),
    ("book_hug_sleep", "抱好书安心睡", "read", "sit", "sit", "sleepy_read",
     "One small sage paperback with a lavender bookmark. Frame 1: sit reading the open book, "
     "head beginning to droop and one wing lowering. Frame 2: deliberately close the book with "
     "the bookmark in place and pull it against her chest. Frame 3: curl her legs comfortably "
     "to one side, hug the CLOSED book, cheek against its cover, eyes peacefully closed and one "
     "wing loosely around her shoulders. Frame 4: the SAME sleeping pose and fixed head position, "
     "fingers soften on the cover and tail tip settles. Frames 3/4 can alternate gently. No startled waking."),
    ("last_cookie", "最后一块留给你", "idle", "stand", "stand", "sweet",
     "Exactly ONE round chocolate-chip cookie. Frame 1: hold the last cookie on both palms and "
     "look down with pleased anticipation. Frame 2: lift it toward her mouth but pause, glance "
     "toward the viewer, tail curling protectively inward. Frame 3: crouch and set the INTACT "
     "cookie carefully on the invisible ground forward-right toward the viewer; release both hands. "
     "Frame 4: stand back with EMPTY hands over her chest, sincere small smile and relaxed tail; "
     "the SAME cookie stays visibly where she placed it. No bite, duplicated food or receiving hand."),
    ("lamp_shelter", "把小火用来护灯", "idle", "stand", "sit", "fire",
     "One tiny sage portable lantern with a visible candle flame beside her in ALL frames. "
     "Frame 1: prepare a proud magic flourish, palm lifted and wings opening, then notice the "
     "lantern's weak flame at her feet. Frame 2: abandon the big pose and kneel beside it, both "
     "hands loosely cupping the lantern with concern. Frame 3: breathe one tiny controlled warm "
     "spark toward its wick, restoring a steady flame. Frame 4: sit beside the safely lit lantern, "
     "one small wing curved behind it as a windbreak, hands relaxed, quietly satisfied. No big fire or failure."),
    ("second_cup", "第二杯先吹凉", "tea", "sit", "sit", "tea_time",
     "Exactly TWO small sage teacups. One stays on the invisible ground to her left throughout; "
     "she handles the second. Frame 1: sit holding the second cup with both hands, notice its small "
     "wisp of steam and glance toward the viewer. Frame 2: patiently blow across its rim with "
     "rounded attentive shoulders, keeping it upright. Frame 3: lower it slightly and inspect "
     "the now-faint steam; do NOT drink from this cup. Frame 4: lean forward and offer the cooled "
     "cup with both hands. The first cup remains visible on the left. No extra cup, spill or other hand."),
    ("old_keepsake", "翻到忘记的小收藏", "idle", "stand", "sit", "keepsake",
     "One small open sage tin containing two dull buttons and ONE worn ivory star charm. "
     "Frame 1: stand holding the tin, intending to sort it. Frame 2: kneel, put the tin beside "
     "her and unexpectedly find the worn star charm with her fingertips. Frame 3: sit with the "
     "SAME charm carefully cradled in both hands at her chest, stop sorting and study its chipped "
     "point. Frame 4: tilt it slightly toward the light and gaze with absorbed affection, tail "
     "wrapped loosely around the tin. The two buttons stay in the tin. No treasure burst or punchline."),
    ("quiet_space", "收回招呼留个位置", "idle", "stand", "sit", "viewer",
     "No props or furniture. Frame 1: turn toward the viewer with one hand halfway raised for "
     "an eager greeting, tail tip lifted. Frame 2: pause, notice the viewer is occupied, soften "
     "her shoulders without looking offended. Frame 3: lower the hand, sit down and shift her "
     "hips slightly left, drawing her tail BEHIND herself to clear an empty space on her right. "
     "Frame 4: lightly pat that EMPTY space once, then look forward with a calm welcoming smile. "
     "No second person or demand for attention."),
    ("gem_release", "舍不得也愿意放手", "idle", "stand", "sit", "gems",
     "One pale mint faceted gemstone, same cut and size throughout. Frame 1: stand holding it "
     "outward in both hands, wanting to share. Frame 2: unconsciously pull it back to her chest, "
     "tail curling protectively around her ankles. Frame 3: take a small breath, kneel and lower "
     "the SAME gem to the invisible ground forward-right, fingers still touching it. Frame 4: "
     "sit back with both hands withdrawn and OPEN on her lap, tender wistful smile as her tail "
     "uncurls. The gem stays visibly where she placed it. No receiving hand or disappearing stone."),
    ("audience_prepare", "先确认你在看", "idle", "stand", "stand", "show_off",
     "No props or magic yet. Frame 1: about to show a trick, confidently raise one index finger. "
     "Frame 2: lean toward the viewer and look directly at them to check for attention, wings "
     "and tail paused expectantly. Frame 3: give a small deliberate nod, lower the finger, plant "
     "both feet more firmly and take a preparatory breath. Frame 4: straighten with open palms "
     "ready at waist height and wings half-open, focused rather than boastful. This prepares "
     "the controlled-flame trick; do not draw the trick or its result yet."),
    ("painting_gift", "把画留在你这边", "draw", "stand", "stand", "floating_canvas",
     "One head-sized square wooden canvas showing a simple lavender flower, one small floating "
     "round palette and brush. NO EASEL, stand, legs, support, chair or table in ANY frame. "
     "The canvas floats unsupported until held. Frame 1: stand making a careful finishing touch on the SAME "
     "canvas, front partly visible. Frame 2: leave the brush floating beside the palette, hold "
     "the finished canvas tenderly in both hands and glance toward the viewer. Frame 3: crouch "
     "and place it upright forward-right on the invisible ground, slightly leaned back so the "
     "flower is visible. Frame 4: stand back with empty hands over her heart and a hopeful smile, "
     "leaving the SAME painting visible. Palette and brush float quietly beside her throughout. No text or recipient."),
    ("game_set_down", "今天先玩到这里", "gamepad", "sit", "sit", "game",
     "One sage handheld game console, no readable screen. Frame 1: sit playing; thumbs stop "
     "as she realizes she lost, tired eyes fixed on the screen. Frame 2: shoulders and wings "
     "droop, lower the SAME console into her lap and breathe out, genuinely disappointed. "
     "Frame 3: deliberately set it face-up beside her right leg on the invisible ground and "
     "release her hands. Frame 4: pull her knees closer, rest her forearms on them, gaze down "
     "quietly with tail still; the console stays beside her. No rematch, victory, comic tears, angry throw or grin."),
    ("joy_tail_catch", "开心藏不住尾巴", "idle", "stand", "stand", "joy",
     "No props. Frame 1: receive good news, eyes brighten and hands draw to her chest while "
     "the tail tip lifts first. Frame 2: rise gently onto her toes, wings spreading and tail "
     "curling excitedly alongside one arm in a full-body burst of delight. Frame 3: lightly "
     "catch and hug her OWN tail with both hands, eyes joyfully closed, staying balanced. "
     "Frame 4: settle onto flat feet, release the tail into a happy upward curl and open both "
     "palms warmly toward the viewer. Successful delight, no stumble or embarrassment."),
    ("low_tail_comfort", "失落时抱抱自己", "idle", "stand", "sit", "low",
     "No props. Frame 1: stand with lowered shoulders and wings, fingers loosely clasped and "
     "gaze downcast. Frame 2: sit carefully, bring her knees closer and draw her OWN tail into "
     "her lap without hiding her face. Frame 3: rest one hand on the tail and stroke it once "
     "with the other, taking a slow breath. Frame 4: settle her cheek onto a forearm over her "
     "knees, tail gently held and eyes still a little sad. Physical self-comfort, no tantrum, "
     "tears, sudden smile or recovery gag."),
    ("curious_feather", "小羽毛也看出神", "idle", "stand", "sit", "discovery",
     "One tiny ivory feather remains the SAME feather. Frame 1: notice its unusual curved "
     "tip near her feet, lean forward and fold her wings closer to see better. Frame 2: kneel "
     "and blow gently so it turns over in place, entirely within the cell. Frame 3: scoop it "
     "onto one palm and follow its fine edge with the other index finger without plucking it. "
     "Frame 4: sit absorbed, hold it at eye level against the transparent air, head tilted and "
     "tail relaxed. Wonder and concentration, no failed catch, sneeze or magical object change."),
    ("quiet_tail_rest", "安静坐着陪你", "read", "sit", "sit", "quiet_company",
     "Seamless seated accompaniment loop, NO props. One leg folded and the other relaxed, "
     "own lavender tail across her lap and one hand resting on it. Pelvis, shoulders and head "
     "position stay fixed. Frame 1: eyes softly open, tail tip loosely curled. Frame 2: slow "
     "half-blink, fingertips relax on tail. Frame 3: eyes gently close, tail tip uncurls slightly "
     "without shifting the body. Frame 4: eyes reopen and tail tip returns to the first curl. "
     "No breathing zoom, swaying torso, nodding head or posture change."),
]

_BEATS = [0, 0, 0, 1, 1, 2, 2, 3, 3, 3, 3, 3]
ACTIONS = {
    "daily_" + name: {
        "title": title, "prompt_header": _HEADER, "prompt": prompt, "style": style,
        "starts_from": start, "posture": end,
        "role": "care" if name == "cookie_receive" else "rest" if name == "quiet_tail_rest" else "scene_daily",
        "family": family, "fps": 0.75 if name == "quiet_tail_rest" else 4.0,
        "loop": name == "quiet_tail_rest", "scale": 1.0,
        "sequence": [0, 1, 2, 3] if name == "quiet_tail_rest" else _BEATS,
        "duration": 60.0 if name == "quiet_tail_rest" else 3.0,
        "drop_strays": True,
        **({"automatic": False} if name == "cookie_receive" else {}),
        **({"restful": True} if name == "quiet_tail_rest" else {}),
    }
    for name, title, style, start, end, family, prompt in _ROWS
}

# Playback view of adopted art, not another image to generate.
PLAYBACK_ALIASES = {
    "daily_book_sleep_hold": {
        "strip": "daily_book_hug_sleep", "title": "抱着书睡一会", "starts_from": "sit", "posture": "sit",
        "role": "sleep", "family": "book", "fps": 0.5, "loop": True, "sequence": [2, 3],
        "scale": 1.0, "duration": 45.0, "restful": True,
        "automatic": False,
    },
}
