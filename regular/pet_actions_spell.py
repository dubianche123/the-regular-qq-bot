"""Whole-body spell and stunt sheets that the brain choreographs beat by beat.

Unlike the prop library these are not loops of a still pose: each sheet is a short
performance (wind-up, action, follow-through, settle). Movement through the screen,
the magic circles and the clipping into a circle belong to the runtime, so the art
never draws floors, holes, circles or glow.
"""

_HEAD = (
    "Four sequential frames of one full-body performance, same camera and the same character scale in "
    "every frame. Exactly one character with two arms, two hands and two legs; no extra limbs. Keep her "
    "face, outfit and proportions identical to the references. The whole body, horns, wings, tail, shoes "
    "and any prop fit inside each 512 px cell with transparent margin. Strong, readable, expressive poses "
    "like a hand-animated cartoon: clear anticipation, action and follow-through, with hair, scarf, "
    "cardigan hem and tail reacting to the motion. Draw no floor, hole, magic circle, runes, light beams, "
    "glow, sparkles, motion lines or text; the game draws its own effects. "
)

# name, title, style reference, posture, family, frames
_ROWS = [
    ("cast", "施法画阵", "idle", "stand", "warp",
     "Casting a magic circle onto the floor, standing on the ground line in a front three-quarter view facing "
     "slightly to the viewer's right, feet planted in all four frames. Frame 1: she raises her right hand high "
     "above her head, index finger pointing up, left hand on her hip, determined happy smile, hair and cardigan "
     "hem lifted a little by a magical breeze. Frame 2: she sweeps that hand down in a wide arc in front of her "
     "body, cardigan and hair swinging with the motion, wings half open, sparkling eyes. Frame 3: she leans "
     "forward a little with BOTH palms open and pointing DOWN toward the floor just in front of her shoes, "
     "focused eyes looking down, small 'o' mouth of concentration, hair floating up softly as if lifted from "
     "below. Frame 4: she straightens up and looks at the viewer with a confident wink and an open-mouth grin, "
     "right hand making a small peace sign beside her face, left hand still pointing down at the floor."),
    ("dive", "跳进法阵", "hop", "stand", "warp",
     "Jumping DOWN INTO A HOLE IN THE FLOOR feet first, like a child doing a pencil jump into a swimming pool, "
     "front three-quarter view. Frame 1: deep crouch anticipation on the ground line, knees bent, both arms swung "
     "back behind her, wings slightly open, excited open-mouth grin, eyes looking down at the floor in front of "
     "her. Frame 2: launching straight UP, body fully stretched, toes pointed down, both arms thrown straight up "
     "above her head, wings spread, joyful closed-eye smile. Frame 3: dropping straight down feet first in a tight "
     "PENCIL-JUMP pose: legs pressed together and straight with toes pointed down, her right hand pinching her "
     "nose shut, the left arm raised straight above her head, cheeks puffed holding her breath, eyes squeezed "
     "shut, wings folded tight, tail hanging straight down beside her legs, hair streaming upward. Frame 4: the "
     "same pencil-jump pose but even more compact: both arms straight up above her head with palms pressed "
     "together, legs together, eyes shut, puffed cheeks, hair and scarf streaming upward."),
    ("rise", "从法阵里冒出来", "hop", "stand", "warp",
     "POPPING UP OUT OF A HOLE IN THE FLOOR like a jack-in-the-box, then landing, front three-quarter view. "
     "Frame 1: her whole body is vertical and straight, legs together with toes pointed down, both arms raised "
     "straight above her head with open hands, eyes closed with a calm smile, hair and scarf lifted upward by a "
     "rising updraft, wings folded. Frame 2: mid-air 'TA-DA!' pose: arms flung wide open to both sides, wings "
     "spread wide, legs together with knees slightly bent, sparkling open eyes, huge open-mouth smile, hair "
     "bouncing. Frame 3: landing on the ground line in a light crouch, knees bent, arms out to the sides for "
     "balance, wings half open, happy closed-eye smile. Frame 4: standing proudly on the ground line, right hand "
     "making a peace sign next to her winking eye, left hand on her hip, tail curled happily, big grin."),
    ("paint", "魔法画布作画", "draw", "stand", "paint",
     "PAINTING ON A SMALL FLOATING CANVAS, standing on the ground line in a three-quarter view facing toward the "
     "viewer's RIGHT. A small square canvas on a light wooden frame, about the size of her head, floats in "
     "mid-air at her chest height just in front of her on the viewer's right, angled toward her so the viewer "
     "sees mainly its plain cream BACK and wooden frame (the painted side is hidden). Her left hand holds a small "
     "round wooden palette with a few dabs of pastel paint; her right hand holds a long thin paintbrush. "
     "Frame 1: she dabs the brush onto the palette, looking at it. Frame 2: she reaches forward and paints a "
     "stroke on the hidden front of the canvas, tongue slightly out in concentration. Frame 3: she leans back a "
     "little, head tilted, appraising the canvas with the brush held up vertically at arm's length, one eye "
     "closed. Frame 4: she paints another stroke with a pleased smile, a tiny dab of lilac paint on her cheek. "
     "The canvas and palette keep the same size and place in all four frames; her feet stay planted. No easel, "
     "stand, table or paint splashes."),
    ("reveal", "亮出画好的画", "draw", "stand", "paint",
     "PROUDLY REVEALING A FINISHED PAINTING, standing on the ground line, front three-quarter view. The "
     "painting is a small square canvas on a light wooden frame, about the size of her head. Frame 1: she holds "
     "the canvas with both hands at chest height, its painted side facing HER (the viewer sees the plain cream "
     "back), peeking at it with an excited secretive smile. Frame 2: she turns the canvas around to face the "
     "viewer: it shows a simple colorful childlike painting of a smiling little lavender dragon under a round "
     "yellow sun above soft green hills, no text. Frame 3: she holds the canvas up high beside her head with "
     "both hands, the painting facing the viewer, eyes closed in a big proud grin, wings spread a little, tail "
     "swishing. Frame 4: she hugs the canvas against her chest with the painting still facing the viewer, "
     "winking at the viewer with a small blush. The same painting in frames 2 to 4. No easel, no signature."),
    ("tome", "翻魔法书查资料", "idle", "stand", "search",
     "SEARCHING A FLOATING MAGIC BOOK, standing on the ground line, front three-quarter view facing slightly to "
     "the viewer's right. A thick old hardcover book with a lavender cover and gold corners floats in mid-air in "
     "front of her at chest height, lying open toward her. Frame 1: she holds both hands up on either side of "
     "the floating book with fingers spread, guiding it, focused curious eyes. Frame 2: she runs her right index "
     "finger along a line in the open book, leaning in, eyes narrowed in concentration, left hand holding a small "
     "round magnifying glass near her eye. Frame 3: the pages fan and flip by themselves, she leans back a little "
     "with wide surprised eyes, hair lifted by the breeze of the pages. Frame 4: she points one finger up with a "
     "bright 'found it!' expression and open-mouth smile, the book still floating open in front of her. The book "
     "keeps the same size and roughly the same place in all four frames. Pages show only abstract grey lines."),
    ("pounce", "扑光标", "hop", "stand", "pounce",
     "A playful CAT-LIKE POUNCE at something just ahead of her, three-quarter view facing the viewer's RIGHT. "
     "Frame 1: low crouch ready to pounce on the ground line, knees deeply bent, both hands lightly touching the "
     "ground in front of her, hips raised, tail raised high in a curve, eyes wide and locked on a spot ahead at "
     "head height, determined playful expression. Frame 2: the same crouch with her hips wiggled the other way "
     "and the tail swished to the other side, eyes still locked ahead. Frame 3: leaping forward and up, body "
     "stretched diagonally toward the viewer's right, both arms reaching forward with open hands, wings flared, "
     "legs trailing behind, excited open mouth. Frame 4: landed back on the ground line in a crouch with both "
     "hands cupped together in front of her chest as if she just caught something tiny, looking into her cupped "
     "hands with delighted surprise. Draw no cursor, insect, ball or prop."),
    ("sneeze", "打喷嚏喷火", "idle", "stand", "sneeze",
     "A DRAGON SNEEZE, standing on the ground line in a front three-quarter view facing slightly to the viewer's "
     "right. Frame 1: her nose twitches, eyes half closed and watery, head tilting back a little, mouth slightly "
     "open, one hand raised near her face. Frame 2: head tilted further back, eyes squeezed shut, mouth wide open "
     "in a big inhale, shoulders raised, wings lifting. Frame 3: the sneeze: she jerks forward bending at the "
     "waist, eyes shut tight, mouth open, a SMALL round puff of orange and yellow cartoon flame with a little grey "
     "smoke cloud bursting from her mouth just in front of her face toward the viewer's right, hair flipping "
     "forward, tail puffed straight out. Frame 4: upright again, embarrassed blush, rubbing under her nose with "
     "one finger, sheepish closed-eye smile, a thin wisp of grey smoke curling up from her nose. The flame puff is "
     "only about the size of her hand and stays inside the cell."),
]

# Upright, clearly visible faces for the head-size calibration; dives and pounces hide or tilt the face.
_CALIBRATE = {"dive": [1, 2], "rise": [1, 3], "pounce": [0, 1], "sneeze": [0, 3], "cast": [0, 3]}

ACTIONS = {
    f"spell_{name}": {
        "title": title, "prompt": _HEAD + action, "style": style, "posture": posture,
        "role": "spell", "family": family, "fps": 4, "loop": False, "scale": 1.0, "duration": 1.0,
        "drop_strays": True,     # big poses spill a hand or tail tip into the neighbouring cell
        **({"calibration_frames": _CALIBRATE[name]} if name in _CALIBRATE else {}),
    }
    for name, title, style, posture, family, action in _ROWS
}
