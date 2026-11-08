"""Her dragon form: a tiny round chibi dragon that still reads as her.

The change itself is a runtime puff of cloud that hides the swap (see PetBrain), so no
sheet draws a half-human, half-dragon in-between. Every dragon sheet keeps her hair as a
mane, the horns, the eyes, the flower clip and the scarf, and is scaled against the
adopted design sheet instead of the human head.
"""

_HEADER = (
    "Image 1 is the EXACT character model sheet of an original chibi dragon-woman desktop pet (an adult "
    "character drawn in chibi style). Image 2 shows HER DRAGON FORM design: match its dragon exactly. "
    "Create a NEW 4-frame ANIMATION SPRITE SHEET of her dragon form, laid out as 4 frames in a 2 x 2 grid on "
    "a 1024 x 1024 transparent canvas (one frame per 512 px cell, read left-to-right then top-to-bottom). "
    "Fixed camera and the same dragon size in every frame; each dragon fully inside its own cell with empty "
    "transparent space around it, never overlapping or cropped; grounded frames stand on one common ground "
    "line near the bottom of each cell. Clean soft anime rendering, the same line weight and colors as "
    "Image 1. No text, grid lines, borders, scenery, cast shadows, motion lines, extra creatures or a human "
    "figure."
)

_DESIGN = (
    "Her DRAGON FORM is the same character magically turned into a tiny round chibi baby dragon, a cute "
    "plush-toy-like mascot, never a realistic or western dragon. It must read instantly as HER: the same "
    "fluffy silver-white hair with lavender tips grows as a soft round mane on top of the dragon head, with "
    "her single curled ahoge strand and a few bangs over the forehead; the same pair of curled pearly "
    "lavender ram-like horns; the same big glossy lavender anime eyes with her eyelashes, pointed elf-like "
    "ears, rosy cheeks and her own small chibi face with a tiny cat-like smile. She still "
    "wears the green double-bar hair clip and the white flower with the dark green ribbon in her mane, and "
    "her lavender scarf with the small white flower tied around her neck. Body: round and chubby like a "
    "mochi, pearl-white with pale lavender scales along the back and a soft cream belly with faint plates, "
    "a short body seen from the side, four short stubby legs with three tiny rounded claws each, two small lavender bat-like wings on the back, and her same "
    "fluffy scaled lavender tail. Mascot proportions: the head is about as large as the whole body. No "
    "human body, arms or clothing apart from the scarf and the hair accessories. "
)

_DESIGN_SHEET = (
    "Image 1 is the EXACT character model sheet of an original chibi dragon-woman desktop pet (an adult "
    "character drawn in chibi style); Image 2 is an existing animation sheet of her human form for line "
    "weight, colors and rendering. Create a NEW 4-frame ANIMATION SPRITE SHEET of her DRAGON FORM, laid out "
    "as 4 frames in a 2 x 2 grid on a 1024 x 1024 transparent canvas (one frame per 512 px cell, read "
    "left-to-right then top-to-bottom). Fixed camera and the same dragon size in every frame, each dragon "
    "fully inside its own cell with empty space around it, standing on one common ground line near the "
    "bottom of each cell. No text, grid lines, borders, scenery, cast shadows, motion lines, extra "
    "creatures or a human figure."
)

# name, title, family, loop, action
_ROWS = [
    ("dragon", "小龙形态", "idle", True,
     "Front three-quarter view facing slightly to the viewer's right, standing on all four feet on the ground "
     "line in every frame, a gentle breathing loop. Frame 1: happy alert look at the viewer, tail curled up "
     "behind her. Frame 2: a soft content blink, closed happy eyes. Frame 3: a small wiggle, wings fluttering "
     "half open, tail tip swished to the other side. Frame 4: back to the pose of frame 1 with the wings "
     "folded again. The same size and placement in all frames."),
    ("dragon_hop", "小龙蹦跶", "move", True,
     "A bouncy four-legged HOP cycle moving toward the viewer's RIGHT, side three-quarter view facing right. "
     "Frame 1: crouched on all fours, ready to spring, ears back, eager smile. Frame 2: springing up and "
     "forward, all four stubby legs stretched, small wings flicked open, tail streaming behind, about a "
     "quarter of her height above the ground line. Frame 3: front paws reaching down to land, back legs "
     "tucked, mane and ears bouncing up. Frame 4: landed on all fours in a soft squash, happy closed eyes. "
     "Feet return to the same ground line in frames 1 and 4."),
    ("dragon_flap", "小龙试着飞", "flap", False,
     "Trying to FLY and not quite managing, front three-quarter view facing slightly right. Frame 1: crouched "
     "low on the ground line, wings spread wide, determined face, tongue slightly out. Frame 2: flapping hard, "
     "wings blurred upward, her stubby legs just lifted off the ground a little, proud surprised eyes. Frame "
     "3: wobbling in the air at the same small height, one wing up and one down, sweat drop, nervous grin. "
     "Frame 4: plopped back down on her belly on the ground line, legs splayed, dizzy swirly eyes and a "
     "sheepish smile, wings drooped."),
    ("dragon_reach", "爪子拆不开糖", "candy", False,
     "One small wrapped candy with twisted ends lies on the ground in front of her. Frame 1: she spots it with "
     "sparkling eyes, leaning forward on all fours. Frame 2: she pats it with one front paw and rolls it "
     "toward herself. Frame 3: sitting back on her haunches, she holds the candy between both stubby front "
     "paws and tries to twist the wrapper open, tongue out in concentration. Frame 4: still sitting, holding "
     "the unopened candy, puzzled frown at her own clumsy paws, ears drooped. The candy stays wrapped and "
     "the same size in every frame."),
    ("dragon_guard", "小龙护宝", "treasure", False,
     "One shiny gold coin and one small wrapped candy lie on the ground. Frame 1: she sniffs them happily on "
     "all fours. Frame 2: she curls her round body around them, tail wrapped protectively in front. Frame 3: "
     "curled up, chin raised proudly, eyes closed in a smug smile, the coin and candy peeking out from inside "
     "the curl. Frame 4: the same curl with her chin resting on her front paws, sleepy content half-closed "
     "eyes. The coin and candy stay the same size."),
    ("dragon_tail_chase", "追自己的尾巴", "tail", False,
     "Chasing her own tail like a puppy, on the ground line. Frame 1: she notices her tail tip twitching "
     "behind her, head turned back over her shoulder, curious eyes. Frame 2: spinning around after it, body "
     "curved in a circle, paws scrabbling, playful open mouth. Frame 3: she catches the tail tip in her mouth "
     "gently, surprised wide eyes. Frame 4: sitting back on her haunches, dizzy swirly eyes, the tail curled "
     "in front of her paws, a happy dazed smile."),
]

ACTIONS = {}
for _name, _title, _family, _loop, _action in _ROWS:
    design = _name == "dragon"
    ACTIONS["form_" + _name] = {
        "title": _title, "style": "idle" if design else "form_dragon", "posture": "stand",
        "role": "form", "family": _family, "form": "dragon",
        "fps": 3.0 if _loop and design else 6.0 if _family == "move" else 4.0,
        "loop": _loop, "duration": 3.0, "scale": 0.58, "drop_strays": True,
        "prompt_header": _DESIGN_SHEET if design else _HEADER,
        "prompt": _DESIGN + _action,
        # The design sheet sets her dragon size; the others match its head and mane.
        **({"head_calibration": False} if design else {"calibration_reference": "form_dragon"}),
        # Airborne frames keep their height above the shared ground instead of each landing on it.
        **({"fixed_ground": True} if _family in ("move", "flap") else {}),
    }

# Hold the beats that sell the joke: the flapping alternates its two airborne frames,
# the puzzled paw and the dizzy plop linger.
ACTIONS["form_dragon"]["sequence"] = [0, 0, 0, 0, 1, 0, 0, 0, 2, 3]
ACTIONS["form_dragon"]["fps"] = 4.0
ACTIONS["form_dragon_flap"]["sequence"] = [0, 0, 0, 1, 2, 1, 2, 1, 2, 3, 3, 3, 3, 3]
ACTIONS["form_dragon_reach"]["sequence"] = [0, 0, 0, 1, 1, 2, 2, 2, 3, 3, 3, 3, 3]
ACTIONS["form_dragon_guard"]["sequence"] = [0, 0, 0, 1, 1, 2, 2, 2, 2, 3, 3, 3]
ACTIONS["form_dragon_tail_chase"]["sequence"] = [0, 0, 0, 1, 1, 2, 2, 2, 3, 3, 3, 3, 3]

# Back on two legs after a puff: she fixes her hair and checks her candy, already built.
ACTIONS["form_recover"] = {
    "title": "整理头发看糖", "style": "idle", "posture": "stand", "starts_from": "stand",
    "role": "form", "family": "recover", "form": "human", "fps": 4.0, "loop": False,
    "sequence": [0, 0, 1, 1, 1, 2, 2, 2, 3, 3, 3, 3],
    "duration": 2.2, "scale": 1.0,
    "prompt": ("Back in human form, she straightens a loose hair strand, adjusts her small scarf, checks a "
               "single wrapped candy in her palm and looks relieved. Exactly two arms and hands."),
}
