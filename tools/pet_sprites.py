#!/usr/bin/env python3
"""Maintainer-only desktop-pet sprite pipeline. A dry plan is the default.

Stages (each request is one paid Images call, never retried):
  base    chibi desk-pet model sheet, using regular/assets/mascot.png as identity reference
  anchor  walk / wave / hop sheets, referencing the base only
  sheets  every other action sheet, referencing the base and the walk sheet (scale/style anchor)
  faces   masked blink edits on the idle sheet; only the eye ellipse is composited back
  library motion / dragon / life catalogs, each action using its own pose reference

  python tools/pet_sprites.py generate --directory ~/the-regular --output /tmp/pet-src \
      --stage base --allow-paid
  python tools/pet_sprites.py build --source /tmp/pet-src --out regular/assets/pet

Generated sheets stay outside the repository. `build` is offline: it slices each
2x2 / 3x2 sheet of 512 px cells, anchors every frame on the head column and the
feet, normalizes legacy art against idle and new art with one shared scale per
strip, and writes WebP strips plus sprites.json. Edge grips align to the runtime
screen cut. Review the strips at display size before committing.
Requires Pillow (maintainer only; the runtime pet reads the WebP files via Qt).
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
from pathlib import Path
import sys
import time
import uuid
import statistics

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ENDPOINT = "https://api.openai.com/v1/images/edits"
MODELS = ("gpt-image-2.6-flare", "gpt-image-2.6-sunburst")
CELL = 512
FIGURE_HEIGHT = 320          # idle figure height in output pixels (2x of the default on-screen size)
CANVAS = (512, 512)         # Extra transparent room for raised hands and spread wings.
ANCHOR = (256, 492)         # head column x, feet y
SEATED = 0.80                # seated close-up sheets are drawn larger than standing ones
EYES = (212, 124, 336, 176)  # idle sheet, top-left cell: the only blink-editable pixels
ROOT = Path(__file__).resolve().parents[1]

IDENTITY = """lock every identity detail: wavy silver-white hair with soft lavender shading and one ahoge,
two curved pearl-lilac dragon horns, pointed ears, lavender eyes, small hair clip plus dark green
ribbon with an ivory flower, lavender scarf tied in a bow with a flower clasp, oversized sage-green
cable-knit cardigan with dark green bows and small flower embroidery, cream knee-length dress with
embroidered hem, white socks, white sneakers with sage accents, small lavender bat-like dragon wings,
and a lavender scaled dragon tail."""

BASE_PROMPT = f"""Image 1 is the official character design reference of an original adult dragon woman mascot called 老群友.
Draw the SAME character as a small animated desktop-pet figure; {IDENTITY}
Same colors, same outfit, same warm playful personality.
Style: clean crisp anime lineart, slightly bolder outline than the reference for readability at
small sizes, soft flat cel shading, pastel muted palette, no texture noise.
Background: genuinely transparent. No floor shadow, no text, no frame, no extra characters.
Proportions: cute chibi desktop-pet proportions, about 3 heads tall, big expressive head, short limbs;
she is still an adult character, not a child.
Pose: standing idle, body in a gentle three-quarter front view facing slightly to the viewer's right,
arms relaxed at her sides, empty hands, tail curling behind her, wings folded, feet together
on an invisible floor. Friendly closed-mouth smile, eyes open looking at the viewer.
Full body centered in the canvas with generous margin; horns, wings, tail and shoes fully visible."""

SHEET_RULES = """Keep identical design in every frame: same face, wavy silver-white hair with lavender shading and ahoge,
pearl-lilac curved horns, pointed ears, lavender eyes, green ribbon with ivory flower and hair clip,
lavender scarf bow, oversized sage-green cable-knit cardigan with dark green bows, cream dress,
white socks, white sneakers with sage accents, small lavender bat wings, lavender scaled tail."""

ANCHOR_HEAD = f"""Image 1 is the EXACT character model sheet of an original chibi dragon-woman desktop pet.
Create an ANIMATION SPRITE SHEET of this exact same character for a desktop pet game.
{SHEET_RULES}
Same chibi proportions (about 3.5 heads tall), same size and same line weight in every frame,
same clean anime lineart and soft flat cel shading as Image 1.
Layout rules: {{layout}}. Frames are evenly spaced, each character fully inside its own cell with clear
empty transparent space between frames, never overlapping, never cropped. All frames share one
common ground line (feet at the same height) unless the action is airborne.
Transparent background. No grid lines, no frame borders, no numbers, no text, no shadows, no motion lines,
no effects, no extra props unless stated."""

SHEET_HEAD = f"""Image 1 is the EXACT character model sheet of an original chibi dragon-woman desktop pet (an adult character drawn in chibi style).
Image 2 is an existing animation sprite sheet of her: match its character scale (each figure about 400 px tall inside a 512 px cell), line weight, colors and rendering exactly.
Create a NEW ANIMATION SPRITE SHEET of this exact same character.
{SHEET_RULES}
Same chibi proportions and the same size in every frame.
Layout: 4 frames in a 2 x 2 grid on a 1024 x 1024 canvas (one frame per 512 px cell), read left-to-right then top-to-bottom.
Each figure fully inside its own cell with empty transparent space around it, never overlapping, never cropped.
Grounded frames stand on one common ground line near the bottom of each cell.
Transparent background. No grid lines, borders, numbers, text, cast shadows, motion lines or effects. No extra props unless stated."""

GRID4 = "4 frames arranged in 2 columns x 2 rows, read left-to-right then top-to-bottom"
GRID6 = "6 frames arranged in 3 columns x 2 rows, read left-to-right then top-to-bottom"

GENTLE_LOOP = """A quiet seamless four-frame idle loop for watching over a long time, not a dramatic performance.
Keep the face, head, torso, hips, ground contact, camera and character scale at the SAME position in every frame.
Only the specified hands, eyes, feet, tail tip or wing tips move a small, readable amount.
Keep the same props and their size throughout; no large body sway, head bobbing, jumping, sudden pose change,
floating effects or motion lines. Frame 4 settles toward frame 1 for a gentle repeat.
Use Image 2 for the existing character's face, proportions, clothing, colors and line weight."""

# Anchor sheets reference only the base; their order fixes the walk sheet used as a style anchor.
ANCHOR_SHEETS = {
    "walk": ("1536x1024", GRID6, "a smooth looping WALK CYCLE seen from the side (profile view), walking toward the viewer's RIGHT. Frame 1 contact (right leg forward), frame 2 down, frame 3 passing, frame 4 contact (left leg forward), frame 5 down, frame 6 passing. Arms swing opposite to legs, tail sways gently, small natural head bob. Cheerful relaxed expression."),
    "wave": ("1024x1024", GRID4, "a friendly WAVE, front three-quarter view, same standing pose as Image 1. Frame 1: right arm starting to rise. Frame 2: right hand raised high beside her head, palm open, waving outward. Frame 3: hand tilted inward mid-wave. Frame 4: hand tilted outward again. Big happy open-mouth smile from frame 2 on. Only the waving arm, face and a tiny body lean change between frames."),
    "hop": ("1024x1024", GRID4, "a JOYFUL HOP. Frame 1: crouch anticipation, knees bent, arms down. Frame 2: launching upward, both arms thrown up in joy, eyes happy closed crescents. Frame 3: peak of the jump, legs tucked, wings spread, arms up, big smile. Frame 4: landing with knees bent, arms out for balance. Front three-quarter view."),
}

SHEETS = {
    "idle": "calm IDLE standing, front three-quarter view facing slightly to the viewer's right, arms relaxed at her sides, wings folded, tail curled behind. Frame 1: neutral friendly closed-mouth smile looking at viewer. Frame 2: identical pose, slight inhale (shoulders a tiny bit higher). Frame 3: identical pose, head turned to glance toward the viewer's left. Frame 4: identical pose, head turned to glance toward the viewer's right. Body, arms and legs stay the same in all four frames.",
    "sit": "SIT DOWN on the floor. Frame 1: standing idle (front three-quarter view). Frame 2: crouching halfway down, knees bent, hands reaching toward the floor. Frame 3: sitting on the floor, legs stretched forward, hands resting on the floor beside her, content smile. Frame 4: same sitting pose, feet happily swinging, eyes closed in a relaxed smile.",
    "sleep": "FALL ASLEEP. Frame 1: sitting on the floor, legs stretched forward, sleepy half-closed eyes. Frame 2: same sitting pose, head nodding forward, eyes closed. Frame 3: lying curled up on her side on the floor, eyes closed, tail wrapped around her, hands tucked under her cheek. Frame 4: same lying sleeping pose with a slightly rounder breath-in posture and a tiny peaceful smile.",
    "dangle": "she has been SCOOPED UP and is HANGING IN MID-AIR. Her feet are clearly off the ground with toes pointing down, legs dangling loosely below her, and her whole body hangs from her two arms which are stretched straight UP above her head as if an invisible giant hand is holding her up by the wrists. Frame 1: startled, eyes wide, legs together. Frame 2: legs swinging toward the viewer's left, flustered face, small sweat drop. Frame 3: legs swinging toward the viewer's right, pouting. Frame 4: legs kicking playfully, laughing. Arms stay raised straight up in every frame; no visible hand or hook is drawn.",
    "fall": "FALLING and LANDING. Frame 1: falling in the air, arms flung up, legs apart, surprised open mouth, wings flared. Frame 2: still falling, eyes squeezed shut, arms up. Frame 3: just landed, deep squat with knees bent and arms out for balance, eyes closed. Frame 4: standing back up, dusting off her cardigan with a sheepish smile.",
    "think": "THINKING and checking her phone. Frame 1: standing, one finger on her chin, eyes looking up, small curious mouth. Frame 2: same, head tilted the other way, a tiny frown of concentration. Frame 3: standing, holding a small sage-green smartphone in both hands near her chest, looking down at it with interest. Frame 4: same phone pose, typing on it with her thumbs, smiling slyly.",
    "talk": "TALKING cheerfully to the viewer, front three-quarter view. Frame 1: mouth open mid-sentence, one hand raised palm-up in an explaining gesture. Frame 2: mouth closed in a smile, both hands relaxed. Frame 3: mouth wide open laughing, one hand covering her mouth playfully. Frame 4: mouth open, pointing a finger upward as if making a point, confident wink.",
    "stretch": "STRETCH and YAWN. Frame 1: standing, arms beginning to rise. Frame 2: big stretch with both arms straight up over her head, on tiptoes, eyes closed. Frame 3: big yawn with mouth wide open, one hand covering it, eyes squeezed, tiny tear. Frame 4: rubbing one eye with her fist, sleepy smile.",
    "read": "READING a book while sitting on the floor. Frame 1: sitting with legs stretched forward, holding an open cream hardcover book in both hands, reading. Frame 2: same, turning a page with one hand. Frame 3: same reading pose, eyes widened with interest. Frame 4: same, giggling at the book with closed happy eyes.",
    "draw": "DRAWING a picture. Frame 1: standing, holding a small cream sketchbook in one arm and a pencil in the other hand, sketching with focus, tongue slightly out. Frame 2: same, pencil moving in a different spot. Frame 3: same, pencil tapping her chin while she looks at the sketchbook thoughtfully. Frame 4: proudly holding the sketchbook up toward the viewer with both hands, back cover facing the viewer, big grin.",
    "sing": "SINGING into a small sage-green handheld microphone. Frame 1: holding the microphone near her mouth with one hand, singing with mouth open, eyes closed. Frame 2: leaning back slightly, other hand raised, singing a high note. Frame 3: swaying to the other side, mouth in an O shape. Frame 4: happy wink, microphone pointed toward the viewer.",
    "pout": "GRUMPY and POUTING. Frame 1: arms crossed, cheeks puffed, frowning, looking away to the viewer's left. Frame 2: same arms crossed pose, stomping one foot, angry cross-popping vein mark is NOT drawn, just a grumpy face. Frame 3: hands on hips, leaning forward, scolding with mouth open. Frame 4: arms crossed, turned slightly away, side-eye glance at the viewer with a small huff.",
    "shy": "being PETTED on the head and loving it. Frame 1: head slightly lowered, eyes closed, pink blush on her cheeks, happy smile. Frame 2: both hands on her cheeks, blushing, happy squinting eyes. Frame 3: giggling with closed crescent eyes and open mouth, shoulders raised. Frame 4: looking up shyly at the viewer, blushing, hands clasped behind her back.",
    "startle": "POKED by surprise. Frame 1: jolting backward, eyes wide, mouth open in surprise, arms raised, tail fluffed straight up. Frame 2: leaning back with hands held up defensively, flustered. Frame 3: puffed cheeks, pointing at the viewer accusingly. Frame 4: laughing it off with closed eyes and one hand behind her head.",
    "cheer": "CELEBRATING good news. Frame 1: both fists raised in victory, big open smile. Frame 2: one fist pumped up, winking. Frame 3: clapping hands together happily, eyes closed. Frame 4: making a cute peace sign near her face, smiling at the viewer.",
    "tea": "relaxing with TEA while sitting on the floor. Frame 1: sitting with legs stretched forward, holding a small ceramic teacup in both hands near her chest, content smile. Frame 2: sipping the tea, eyes closed. Frame 3: lowering the cup, sighing happily with closed eyes. Frame 4: blowing gently on the tea, cheeks slightly puffed.",
    "lookaround": "LOOKING AROUND curiously, standing. Frame 1: shading her eyes with one hand, looking toward the viewer's left. Frame 2: same gesture, looking toward the viewer's right. Frame 3: head tilted with a question-mark-like confused face, one hand scratching her head. Frame 4: shrugging with both palms up, playful smile.",
    "eat": "EATING a snack, standing, front three-quarter view. Frame 1: holding a round cookie in both hands, eyes sparkling with excitement. Frame 2: taking a big bite of the cookie, eyes closed, cheeks puffed. Frame 3: chewing happily with closed crescent eyes and stuffed cheeks, a few crumbs. Frame 4: licking her lips with a satisfied smile, hands patting her tummy.",
    "laptop": "WORKING on a small sage-green laptop while sitting on the floor with crossed legs, the laptop resting on her lap with its screen facing her (the viewer sees the back of the lid). Frame 1: typing quickly with focused eyes. Frame 2: typing, tongue slightly out in concentration. Frame 3: leaning back slightly, one hand on chin, reading the screen. Frame 4: typing again with a confident smile.",
    # Waking up is played frame by frame between "sleep" and standing, so these two continue the sleep sheet.
    "wake": "WAKING UP from a nap on the floor, slow and groggy, continuing from the lying pose of Image 2 (its bottom-left frame). Frame 1: still lying curled up on her side exactly like that sleeping pose, but one eye cracking open, eyebrows knitted sleepily, one hand starting to press against the floor. Frame 2: propping her upper body up on one straight arm from the lying pose, head drooping, eyes half closed, hair messy, legs still on the floor. Frame 3: sitting up on the floor with legs stretched forward (the same sitting pose as the top-left frame of Image 2), rubbing one eye with her fist, other hand on the floor, drowsy. Frame 4: same sitting pose, hands resting in her lap, looking at the viewer with heavy half-closed eyes and a tiny sleepy pout.",
    "getup": "GETTING UP from the floor after a nap, still sleepy. Frame 1: sitting on the floor with knees pulled in, both hands pressed on the floor beside her hips, about to push herself up, half-closed eyes. Frame 2: rising on one knee, one hand on the raised knee, the other hand still touching the floor, drowsy face. Frame 3: standing up, shoulders slightly slouched, scratching the side of her messy hair with one hand, eyes half closed. Frame 4: standing, a small yawn with one hand covering her mouth and eyes squeezed shut.",
    "popcorn": GENTLE_LOOP + "\nSitting comfortably on the floor with her legs tucked to one side, watching an amusing conversation. A small cream paper tub of popcorn rests in her lap, held steady by her left hand. Frame 1: looking slightly left, a soft interested smile, right hand resting at the rim. Frame 2: eyes glance slightly right, right fingers pick up ONE piece of popcorn just above the rim. Frame 3: right hand brings that piece close to her mouth, lips in a tiny content smile. Frame 4: hand returns to the rim, looking forward with an amused closed-mouth smile. No bouncing, surprise face or exaggerated chewing. Keep both shoulders and her whole seated silhouette steady.",
    "headphones": GENTLE_LOOP + "\nSitting on the floor with her legs comfortably stretched forward, wearing simple sage-green over-ear headphones whose band sits behind her horns without changing or hiding them. Both hands rest in her lap. Frame 1: eyes relaxed and half closed, small peaceful smile. Frame 2: eyes gently close, tail tip curls inward a little. Frame 3: eyes remain closed, tail tip relaxes back, fingertips tap one tiny beat against her knee. Frame 4: eyes open to the same relaxed half-closed expression as frame 1, hands settle. Her head stays almost perfectly still; suggest the music with her eyes, fingertips and tail tip, not repeated whole-body rocking. No music notes, wires or extra device.",
    "gamepad": GENTLE_LOOP + "\nSitting cross-legged on the floor, playing a SMALL sage-green dedicated HANDHELD GAME CONSOLE held horizontally just above her lap. The device is only a little wider than her two hands together: a small central screen, thick rounded controller grips on BOTH sides, a clearly visible dark D-pad and tiny joystick on the left grip, and four little dark face buttons on the right grip. Tilt the console slightly upward at a shallow angle so the viewer can recognize the physical controls and her thumbs resting on them; her eyes look down toward the screen. BOTH hands wrap around the side grips, fingers curled behind the device, BOTH thumbs operate the physical buttons. Frame 1: focused eyes, thumbs resting on the controls. Frame 2: left thumb nudges the little joystick, eyes narrow slightly with concentration. Frame 3: right thumb presses a face button, tiny pleased smile. Frame 4: both thumbs settle toward frame 1, satisfied closed-mouth smile. Keep the console, head and torso aligned in exactly the same place throughout. NOT a tablet, iPad, laptop, book or phone; no large blank flat rectangle, no touchscreen tapping, no extended pointing index finger, no separate controller. No victory jump, raised arms, tilted whole body or flashing display.",
    "tail_care": GENTLE_LOOP + "\nSitting on the floor with legs tucked to one side, her lavender scaled dragon tail curled forward across her lap. She cradles the tail gently with her left hand and strokes its scales with her right hand. Frame 1: right palm resting on the upper side of her tail, looking down fondly. Frame 2: right hand slides a short distance along the scales, tail tip curls slightly. Frame 3: hand pauses, eyes gently close in a content smile, the small wing tips open only a little. Frame 4: right hand returns near its starting point, wings fold back, eyes reopen. Same tail shape, length and attachment to her body in all four frames; do not add another tail or make the tail float.",
    "lie_chin": GENTLE_LOOP + "\nAWAKE and lying on her stomach along the invisible floor, three-quarter front view, head and upper body facing the viewer. Her chin rests in BOTH palms, elbows firmly planted; her cream dress and green cardigan cover her body naturally, lower legs bent behind her with white shoes visible. This is a low wide resting silhouette, not sleeping, sitting or a close-up. Keep horns, wings, tail, elbows and both shoes fully inside each 512 px cell. Frame 1: warm curious eyes open, tiny closed-mouth smile, both shoes relaxed. Frame 2: eyes glance a little to one side, one shoe tip lifts only a little. Frame 3: eyes gently blink, the shoe settles while the other shoe tip lifts a little. Frame 4: eyes open looking forward again, both feet settle toward frame 1. Her face, chin, elbows and upper body never shift; movement is only the small eye expression and alternating shoe tips. No pillow, furniture, floor shadow, exposed underwear or extra props.",
    "edge_peek": GENTLE_LOOP + "\nA CLASSIC PEEKING-OUT FROM BEHIND A DOOR ON THE LEFT pose, but the door itself is invisible and must NOT be drawn. Anatomy: EXACTLY TWO ARMS AND TWO HANDS TOTAL, one hand from each arm. One upper gripping hand around y=235 and one lower gripping hand around y=295 in each 512 px cell; both arms must connect visibly to their own shoulders. Only those two hands touch the imaginary edge. No third hand near the waist, dress or hem. Use Image 2 for character identity and rendering only; REPLACE its upright pose with one clearly SIDE-LEANING full-body pose, about 25 DEGREES toward the viewer's RIGHT, fixed across all four frames. For composition inside each 512 px cell, imagine a vertical edge near x=220: shoes and waist centered around x=140-170, shoulders around x=230, and head centered around x=300. These approximate coordinates describe spatial placement only; do not draw coordinates or a line. Her waist, hips, legs and BOTH grounded shoes are on the LEFT side of that imagined line. Both hands reach to the RIGHT side of her torso and grip that SAME imagined line, one above the other, fingers curling around the unseen edge. Her neck, face and head lean across to the RIGHT side of that line: the center of her head must be at least HALF A HEAD'S WIDTH farther RIGHT than the center of her waist. This left-body / right-head separation is essential: her lower body is hiding to the LEFT, while her curious face peeks out to the RIGHT. Do NOT draw an upright girl standing to the right of her hands, hugging a left-hand frame, holding a pole, or keeping her head directly above her hips. Draw her ENTIRE body even on the imagined hidden left side, including both pearl-lilac horns, lavender tail, wings and both white shoes, fully visible inside each 512 px cell with transparent margin. No actual wall, door, panel, vertical line, border or prop appears anywhere; the runtime will crop her left-side body behind the real screen edge later. Frame 1: soft curious eyes looking right, fingers resting on the invisible edge, tiny closed-mouth smile. Frame 2: upper fingertips shift slightly along that same edge, eyes glance a little farther right. Frame 3: gentle blink and a small amused smile, lower fingertips curl a little. Frame 4: eyes and fingertips settle back to frame 1. Keep the SAME clear 25-degree side lean and LEFT-waist / RIGHT-head positions in every frame; do not stand up straight, step, bob the head, pull back or lean farther out.",
    "wing_stretch": GENTLE_LOOP + "\nA gentle DRAGON WING STRETCH while standing firmly on both feet in the same calm three-quarter front pose as the idle reference. Her small lavender bat-like wings stay attached behind the green cardigan in their original places. Her head, torso, hips and feet stay still; for this action her forearms may also extend a little while the wing tips unfold a short distance. Frame 1: wings folded comfortably, hands relaxed near her waist, peaceful open eyes. Frame 2: both small wings open slightly and symmetrically behind her shoulders, elbows stay near her sides while her forearms stretch gently outward a little. Frame 3: hold a modest half-open wing stretch, fingertips extend softly, eyes close in a content smile. Frame 4: wings fold back into their original resting shape, arms settle toward frame 1, eyes reopen. Keep the same face, horns, pointed ears, silver-lavender hair, green cardigan, cream dress, shoes and single lavender tail. Fully contain both wing tips, horns, hands, tail and shoes inside every 512 px cell with transparent margin. No huge wingspan, flapping, flight, jumping, raised shoulders, giant overhead arm stretch, wind, sparkles or dramatic pose.",
}
# Image 2 (the scale/style anchor) for sheets that continue another sheet; everything else uses the walk sheet.
STYLE_REF = {"wake": "sleep", "popcorn": "tea", "headphones": "read", "gamepad": "tea",
             "tail_care": "tea", "lie_chin": "read", "edge_peek": "idle", "wing_stretch": "idle"}

FACE_EDIT = """Image 1 is a sprite sheet of an original chibi dragon-woman desktop pet. Edit ONLY the transparent masked area, which covers the two eyes of the TOP-LEFT figure.
Keep every pixel outside the mask exactly unchanged: same canvas, same four figures, same positions, colors, lineart and transparent background.
Inside the mask keep the same eye positions, eyelash style, skin tone, hair strands crossing the eyes and soft anime rendering. No other changes."""
FACES = {
    "blink_half": "Change: both eyes are HALF CLOSED mid-blink: upper eyelids lowered to cover the top half of each lavender iris, relaxed expression.",
    "blink_closed": "Change: both eyes are FULLY CLOSED in a natural relaxed blink: gently curved closed eyelid lines with lashes, no iris or pupil visible.",
}

# Runtime strips: action -> (source sheet, cells, scale multiplier). Frame sequencing lives in regular/pet_brain.py.
STRIPS = {
    "idle": ("idle", [0], 1.0),            # + blink_half, blink_closed composited on cell 0
    "look": ("idle", [2, 3], 1.0),
    "walk": ("walk", [0, 1, 2, 3, 4, 5], 1.0),
    "wave": ("wave", [0, 1, 2, 3], 1.0),
    "hop": ("hop", [0, 1, 2, 3], 1.0),
    "sit": ("sit", [1, 2, 3], 1.0),
    "sleep": ("sleep", [0, 1, 2, 3], SEATED),
    "dangle": ("dangle", [0, 1, 2, 3], 1.0),
    "fall": ("fall", [0, 1, 2, 3], 1.0),
    "think": ("think", [0, 1], 1.0),
    "phone": ("think", [2, 3], 1.0),
    "talk": ("talk", [0, 1, 2, 3], 1.0),
    "stretch": ("stretch", [0, 1, 2, 3], 1.0),
    "read": ("read", [0, 1, 2, 3], SEATED),
    "draw": ("draw", [0, 1, 2, 3], 1.0),
    "sing": ("sing", [0, 1, 2, 3], 1.0),
    "pout": ("pout", [0, 1, 2, 3], 1.0),
    "shy": ("shy", [0, 1, 2, 3], 1.0),
    "startle": ("startle", [0, 1, 2, 3], 1.0),
    "cheer": ("cheer", [0, 1, 2, 3], 1.0),
    "tea": ("tea", [0, 1, 2, 3], SEATED),
    "lookaround": ("lookaround", [0, 1, 2, 3], 1.0),
    "eat": ("eat", [0, 1, 2, 3], 1.0),
    "laptop": ("laptop", [0, 1, 2, 3], SEATED),
    "wake": ("wake", [0, 1, 2, 3], SEATED),
    "getup": ("getup", [0, 1, 2, 3], 1.0),
    "popcorn": ("popcorn", [0, 1, 2, 3], SEATED),
    "headphones": ("headphones", [0, 1, 2, 3], SEATED),
    "gamepad": ("gamepad", [0, 1, 2, 3], SEATED),
    "tail_care": ("tail_care", [0, 1, 2, 3], SEATED),
    "lie_chin": ("lie_chin", [0, 1, 2, 3], SEATED),
    "edge_peek": ("edge_peek", [0, 1, 2, 3], 1.0),
    "wing_stretch": ("wing_stretch", [0, 1, 2, 3], 1.0),
}


def _selected_jobs(args):
    jobs = [job for job in _jobs(args.stage, Path(args.output), args.model)
            if (not args.only or job["name"] in args.only)
            and (not getattr(args, "group", None) or job.get("group") == args.group)]
    if getattr(args, "prompt_file", None):
        if len(jobs) != 1:
            raise ValueError("replacement prompt requires one selected action")
        jobs[0]["prompt"] = SHEET_HEAD + "\nAction: " + Path(args.prompt_file).read_text(encoding="utf-8")
    if getattr(args, "style_reference", None):
        for job in jobs:
            job["refs"] = [job["refs"][0], Path(args.style_reference)]
    return jobs


def plan(args):
    jobs = _selected_jobs(args)
    return {"stage": args.stage, "model": args.model, "requests": len(jobs),
            "outputs": [job["name"] for job in jobs], "endpoint": ENDPOINT,
            "sends": "character design prompts and this repository's mascot/sprite images only"}


def _jobs(stage, out, model):
    base = ROOT / "regular/assets/mascot.png"
    if stage == "base":
        return [{"name": "base", "refs": [base], "prompt": BASE_PROMPT, "size": "1024x1024", "model": model}]
    if stage == "anchor":
        return [{"name": name, "refs": [out / "base.png"], "size": size, "model": model,
                 "prompt": ANCHOR_HEAD.format(layout=layout) + "\nAction: " + action}
                for name, (size, layout, action) in ANCHOR_SHEETS.items()]
    if stage == "sheets":
        return [{"name": name, "refs": [out / "base.png", out / f"{STYLE_REF.get(name, 'walk')}.png"],
                 "size": "1024x1024", "model": model, "prompt": SHEET_HEAD + "\nAction: " + action}
                for name, action in SHEETS.items()]
    if stage == "library":
        from regular.pet_repertoire import ACTIONS
        return [{"name": name, "group": name.split("_", 1)[0],
                 "refs": [out / "base.png", out / (spec["style"] + ".png")],
                 "size": "1024x1024", "model": model,
                 "prompt": SHEET_HEAD + "\nImage 2 supplies face, outfit, proportions, palette and linework only. The NEW action below determines pose, arm position and props; replace the reference pose when it differs.\nAction: " + spec["prompt"]}
                for name, spec in ACTIONS.items()]
    if stage == "faces":
        return [{"name": name, "refs": [out / "idle.png"], "mask": out / "eyes-mask.png", "size": "1024x1024",
                 "model": model, "prompt": FACE_EDIT + "\n" + change} for name, change in FACES.items()]
    raise ValueError(stage)


def _ledger(out, row):
    with (out / "requests.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(row, ensure_ascii=False) + "\n")


async def _request(http, key, job, out):
    target = out / f"{job['name']}.png"
    if target.exists():
        return {"name": job["name"], "skipped": "exists"}
    files = [("image[]" if len(job["refs"]) > 1 else "image", (Path(r).name, Path(r).read_bytes(), "image/png"))
             for r in job["refs"]]
    if job.get("mask"):
        files.append(("mask", ("mask.png", Path(job["mask"]).read_bytes(), "image/png")))
    data = {"model": job["model"], "prompt": job["prompt"], "size": job["size"], "quality": "medium",
            "background": "transparent", "output_format": "png", "complexity": "medium", "n": "1"}
    call = {"id": uuid.uuid4().hex, "name": job["name"], "model": job["model"], "ts": time.time()}
    _ledger(out, call | {"event": "started"})
    try:
        response = await http.post(ENDPOINT, headers={"Authorization": "Bearer " + key}, files=files, data=data)
        if response.status_code != 200:
            _ledger(out, call | {"event": "completed", "status": response.status_code, "ok": False})
            return {"name": job["name"], "status": response.status_code, "requests_attempted": 1}
        target.write_bytes(base64.b64decode(response.json()["data"][0]["b64_json"], validate=True))
    except Exception as error:  # never print response bodies or credentials
        _ledger(out, call | {"event": "completed", "error": type(error).__name__, "ok": False})
        return {"name": job["name"], "error": type(error).__name__, "requests_attempted": 1}
    _ledger(out, call | {"event": "completed", "status": 200, "ok": True,
                         "bytes": target.stat().st_size, "finished_at": time.time()})
    (out / f"{job['name']}.json").write_text(json.dumps(
        {k: str(v) if isinstance(v, Path) else v for k, v in job.items() if k != "refs"}
        | {"refs": [Path(r).name for r in job["refs"]]}, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return {"name": job["name"], "file": str(target), "requests_attempted": 1}


async def generate(args):
    import httpx
    from dotenv import dotenv_values
    from PIL import Image, ImageDraw

    key = dotenv_values(Path(args.directory).expanduser() / ".env", interpolate=False).get("OPENAI_API_KEY")
    if not key:
        raise SystemExit("Configure OPENAI_API_KEY in the private instance .env")
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    if args.stage == "faces":
        mask = Image.new("RGBA", (1024, 1024), (0, 0, 0, 255))
        ImageDraw.Draw(mask).ellipse((EYES[0] - 6, EYES[1] - 6, EYES[2] + 6, EYES[3] + 6), fill=(0, 0, 0, 0))
        mask.save(out / "eyes-mask.png")
    jobs = _selected_jobs(args)
    semaphore = asyncio.Semaphore(args.parallel)
    transport = httpx.AsyncHTTPTransport(retries=0)
    async with httpx.AsyncClient(transport=transport, timeout=400, trust_env=False, follow_redirects=False) as http:
        async def one(job):
            async with semaphore:
                result = await _request(http, key, job, out)
                print(json.dumps(result, ensure_ascii=False), flush=True)
                return result
        results = await asyncio.gather(*(one(job) for job in jobs))
    return 0 if all("error" not in r and "status" not in r for r in results) else 1


def _cell(sheet, index):
    cols = sheet.width // CELL
    x, y = (index % cols) * CELL, (index // cols) * CELL
    return sheet.crop((x, y, x + CELL, y + CELL))


def _anchor(frame):
    """Head-column x and feet y: limbs may swing, the head stays over one point."""
    alpha = frame.getchannel("A").point(lambda v: 255 if v > 40 else 0)
    left, top, right, bottom = alpha.getbbox()
    head = alpha.crop((left, top, right, top + int((bottom - top) * 0.38)))
    columns = [0] * head.width
    pixels = head.load()
    for y in range(head.height):
        for x in range(head.width):
            if pixels[x, y]:
                columns[x] += 1
    total = sum(columns) or 1
    return left + sum(i * v for i, v in enumerate(columns)) / total, bottom, (left, top, right, bottom)


def _idle_reference(out):
    """A head matcher for the idle frame already in the output folder, if there is one."""
    from PIL import Image

    try:
        spec = json.loads((out / "sprites.json").read_text(encoding="utf-8"))
        with Image.open(out / spec["actions"]["idle"]["file"]) as strip:
            return HeadMatcher(strip.crop((0, 0, CANVAS[0], CANVAS[1])).convert("RGBA"))
    except (OSError, KeyError, ValueError):
        return None


def _drop_support(canvas, cut_x):
    """Paint out the magenta pole edge sheets are drawn gripping. The screen edge hides it while she
    peeks, but the frame still dissolves for a moment after she pops out, so it must not exist at all.
    The pole crosses her hair and cardigan: each row is bridged from the colours on either side, and
    only background rows become transparent (in a neutral colour, so lossy WebP cannot bleed it back)."""
    import numpy as np
    from PIL import Image

    pixels = np.asarray(canvas.convert("RGBA")).astype(np.float32)
    r, g, b, a = (pixels[..., i] for i in range(4))
    band = np.zeros(a.shape, dtype=bool)
    band[:, max(0, round(cut_x) - 48): round(cut_x) + 3] = True        # the pole sits at or left of the cut
    support = band & (a > 0) & (r > 120) & (b > 120) & ((r + b) / 2 - g > 90)
    for y in np.nonzero(support.any(axis=1))[0]:
        xs = np.nonzero(support[y])[0]
        runs = np.split(xs, np.nonzero(np.diff(xs) > 1)[0] + 1)
        for run in runs:
            x0, x1 = max(0, run[0] - 3), min(a.shape[1] - 1, run[-1] + 3)      # include the tinted fringe
            left, right = pixels[y, max(0, x0 - 1)], pixels[y, min(a.shape[1] - 1, x1 + 1)]
            if left[3] > 200 and right[3] > 200:
                k = np.linspace(0, 1, x1 - x0 + 3)[1:-1, None]
                pixels[y, x0: x1 + 1] = left * (1 - k) + right * k
            else:
                fill = left if left[3] >= right[3] else right
                pixels[y, x0: x1 + 1, :3] = fill[:3] if fill[3] > 0 else (200, 200, 210)
                pixels[y, x0: x1 + 1, 3] = 0
    return Image.fromarray(np.clip(pixels, 0, 255).astype(np.uint8), "RGBA")


class HeadMatcher:
    """How large her head is in a frame, relative to the idle pose.

    Skin-colour and hair-width estimates drift with hands, props, bangs and head tilt, which
    left adopted strips anywhere from 0.7x to 1.3x the idle head. This matches the idle head
    itself (multi-scale normalized cross-correlation, mirrored too), so a strip that agrees
    with the idle head plays at the same size. Lying and upside-down poses fall back to
    rotated templates; frames that still match nothing leave the strip unchanged.
    """
    WORK = 256                       # matching resolution (half of the 512 canvas)
    FFT = 384

    def __init__(self, idle_frame):
        import numpy as np
        from PIL import Image

        self.np, self.Image = np, Image
        luma, alpha = self._feature(idle_frame)
        ys, xs = np.nonzero(alpha > 0.3)
        top, height = ys.min(), ys.max() - ys.min()
        band = alpha[top: top + int(height * 0.36)]
        cols = np.nonzero(band.max(0) > 0.3)[0]
        self.template = luma[top: top + int(height * 0.36), cols.min(): cols.max() + 1]
        self._banks = {}

    def _feature(self, frame):
        np = self.np if hasattr(self, "np") else __import__("numpy")
        from PIL import Image

        rgba = np.asarray(frame.convert("RGBA").resize((self.WORK, self.WORK), Image.LANCZOS),
                          dtype=np.float64) / 255
        alpha = rgba[..., 3]
        luma = rgba[..., :3] @ np.array([0.3, 0.59, 0.11])
        return luma * alpha + 0.62 * (1 - alpha), alpha

    def _bank(self, scales, angles):
        key = (tuple(round(s, 4) for s in scales), angles)
        if key in self._banks:
            return self._banks[key]
        np, Image = self.np, self.Image
        bank = []
        base = Image.fromarray(self.template.astype(np.float32))
        for angle in angles:
            rotated = base.rotate(angle, resample=Image.BILINEAR, expand=True, fillcolor=0.62)
            for scale in scales:
                size = (max(4, round(rotated.width * scale)), max(4, round(rotated.height * scale)))
                if max(size) >= self.WORK:
                    continue
                t = np.asarray(rotated.resize(size, Image.BILINEAR), dtype=np.float64)
                for mirror in (False, True):
                    m = t[:, ::-1] if mirror else t
                    m = m - m.mean()
                    norm = float(np.sqrt((m * m).sum()))
                    if norm:
                        bank.append((float(scale), m.shape, np.fft.rfft2(m[::-1, ::-1], s=(self.FFT, self.FFT)), norm))
        self._banks[key] = bank
        return bank

    def _best(self, spectrum, ii, ii2, bank):
        np, W = self.np, self.WORK
        best = (-1.0, 1.0)
        for scale, (h, w), template, norm in bank:
            corr = np.fft.irfft2(spectrum * template, s=(self.FFT, self.FFT))[h - 1: W, w - 1: W]
            s1 = ii[h:, w:] - ii[:-h, w:] - ii[h:, :-w] + ii[:-h, :-w]
            s2 = ii2[h:, w:] - ii2[:-h, w:] - ii2[h:, :-w] + ii2[:-h, :-w]
            var = s2 - s1 * s1 / (h * w)
            # Flat background windows have no texture to correlate with.
            score = np.where(var > 0.3 * norm * norm, corr / (norm * np.sqrt(np.maximum(var, 1e-9))), -1.0)
            value = float(score.max())
            if value > best[0]:
                best = (value, scale)
        return best

    def measure(self, frame):
        """(confidence, scale): scale > 1 means her head is drawn larger than in the idle pose."""
        np = self.np
        luma, _ = self._feature(frame)
        spectrum = np.fft.rfft2(luma, s=(self.FFT, self.FFT))
        ii = np.pad(luma, ((1, 0), (1, 0))).cumsum(0).cumsum(1)
        ii2 = np.pad(luma * luma, ((1, 0), (1, 0))).cumsum(0).cumsum(1)
        coarse = np.exp(np.linspace(np.log(0.6), np.log(1.6), 22))
        score, scale = self._best(spectrum, ii, ii2, self._bank(coarse, (0,)))
        angles = (0,)
        if score < 0.5:              # lying down, curled up or hanging upside down
            angles = (0, 30, -30, 60, -60, 90, -90, 180)
            score, scale = self._best(spectrum, ii, ii2, self._bank(coarse, angles))
        fine = scale * np.exp(np.linspace(-0.05, 0.05, 11))
        return self._best(spectrum, ii, ii2, self._bank(fine, angles))

    def strip_scale(self, frames, *, confident=0.5, weak=0.42):
        """Median relative scale over the frames that clearly show her face, or None.

        Curled-up and tilted poses only match weakly; they still count when most frames agree.
        """
        rows = [self.measure(frame) for frame in frames]
        good = [scale for score, scale in rows if score >= confident]
        if not good:
            agree = [scale for score, scale in rows if score >= weak]
            if len(agree) * 2 >= len(rows) and agree and max(agree) / min(agree) <= 1.03:
                good = agree
        return (statistics.median(good) if good else None), rows


def _fit_scale(geometry, scale, source_x, target_x):
    for x, feet, box in geometry:
        x = x if source_x is None else source_x
        if x > box[0]:
            scale = min(scale, (target_x - 6) / (x - box[0]))
        if box[2] > x:
            scale = min(scale, (CANVAS[0] - target_x - 6) / (box[2] - x))
        scale = min(scale, (ANCHOR[1] - 6) / (feet - box[1]))
    return scale


def _place(frame, scale, *, source_x=None, target_x=None):
    from PIL import Image

    x, feet, box = _anchor(frame)
    x = x if source_x is None else source_x
    target_x = ANCHOR[0] if target_x is None else target_x
    scaled = frame.convert("RGBa").resize((round(frame.width * scale), round(frame.height * scale)),
                                          Image.LANCZOS).convert("RGBA")
    canvas = Image.new("RGBA", CANVAS, (0, 0, 0, 0))
    ox, oy = round(target_x - x * scale), round(ANCHOR[1] - feet * scale)
    canvas.paste(scaled, (ox, oy), scaled)
    clipped = (ox + box[0] * scale < 0 or oy + box[1] * scale < 0
               or ox + box[2] * scale > CANVAS[0] or oy + box[3] * scale > CANVAS[1])
    return canvas, clipped


def build(args):
    from PIL import Image, ImageDraw, ImageFilter

    source, out = Path(args.source), Path(args.out)
    from regular.pet_repertoire import ACTIONS
    strips = STRIPS | {name: (name, [0, 1, 2, 3], spec["scale"]) for name, spec in ACTIONS.items()
                       if (source / (name + ".png")).is_file() or args.only and name in args.only}
    out.mkdir(parents=True, exist_ok=True)
    sheets = {}

    def sheet(name):
        if name not in sheets:
            sheets[name] = Image.open(source / f"{name}.png").convert("RGBA")
        return sheets[name]

    def clean(frame, cutoff=12):
        frame.putalpha(frame.getchannel("A").point(lambda v: 0 if v < cutoff else v))
        return frame

    idle = clean(_cell(sheet("idle"), 0))
    box = _anchor(idle)[2]
    base_scale = FIGURE_HEIGHT / (box[3] - box[1])
    eyes = Image.new("L", (CELL, CELL), 0)
    ImageDraw.Draw(eyes).ellipse(EYES, fill=255)
    eyes = eyes.filter(ImageFilter.GaussianBlur(5))
    # Strips made elsewhere (hand-picked candidates) stay listed; this build only replaces its own.
    previous = out / "sprites.json"
    previous_spec = json.loads(previous.read_text(encoding="utf-8")) if previous.is_file() else {}
    if previous_spec.get("frame", list(CANVAS)) != list(CANVAS):
        raise ValueError("calibrate the existing library before adding strips with the new canvas")
    kept = previous_spec.get("actions", {})
    manifest = {**previous_spec, "schema": 2, "frame": list(CANVAS), "anchor": list(ANCHOR), "figure_height": FIGURE_HEIGHT,
                "actions": {name: action for name, action in kept.items() if name not in strips or args.only}}
    manifest.setdefault("geometry", {})
    warnings = []
    reference = None
    for name, (sheet_name, cells, multiplier) in strips.items():
        if args.only and name not in args.only:
            continue
        action_spec = ACTIONS.get(name, {})
        frames = [clean(_cell(sheet(sheet_name), index), action_spec.get("alpha_cutoff", 12)) for index in cells]
        if name == "idle":
            frames += [clean(Image.composite(_cell(sheet(face), 0), frames[0], eyes)) for face in FACES]
        strip = Image.new("RGBA", (CANVAS[0] * len(frames), CANVAS[1]), (0, 0, 0, 0))
        scale = base_scale * multiplier
        grip = ACTIONS.get(name, {}).get("grip_x")
        # Hide the support behind the screen cut, including resize/compression fringes.
        target_x = ANCHOR[0] - FIGURE_HEIGHT * .12 - 3 if grip is not None else ANCHOR[0]
        if name in ACTIONS:
            geometry = [_anchor(frame) for frame in frames]
            scale = _fit_scale(geometry, FIGURE_HEIGHT * multiplier / statistics.median(
                box[3] - box[1] for _, _, box in geometry), grip, target_x)
            # Then match the shipped idle head, so a new strip plays at the size she already is.
            reference = reference or _idle_reference(out)
            if reference is not None:
                frames_at = [frames[i] for i in action_spec.get("calibration_frames", range(len(frames)))]
                relative, _ = reference.strip_scale([_place(frame, scale, source_x=grip, target_x=target_x)[0]
                                                     for frame in frames_at])
                if relative:
                    scale = _fit_scale(geometry, scale / relative, grip, target_x)
        placed_bounds = []
        for index, frame in enumerate(frames):
            placed, clipped = _place(frame, scale, source_x=grip, target_x=target_x)
            if grip is not None:
                placed = _drop_support(placed, target_x)
            if clipped:
                warnings.append(f"{name}[{index}]")
            strip.alpha_composite(placed, (index * CANVAS[0], 0))
            placed_bounds.append(placed.getchannel("A").point(lambda a: 255 if a > 40 else 0).getbbox())
        if name == "idle":
            reference = HeadMatcher(strip.crop((0, 0, CANVAS[0], CANVAS[1])))
        filename = f"{name}.webp"
        strip.save(out / filename, "WEBP", quality=86, alpha_quality=92, method=6)
        manifest["actions"][name] = {"file": filename, "frames": len(frames)}
        manifest["geometry"][name] = {"bounds": [min(b[0] for b in placed_bounds) - ANCHOR[0],
                                                   min(b[1] for b in placed_bounds) - ANCHOR[1],
                                                   max(b[2] for b in placed_bounds) - ANCHOR[0],
                                                   max(b[3] for b in placed_bounds) - ANCHOR[1]]}
    (out / "sprites.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    size = sum(p.stat().st_size for p in out.glob("*.webp"))
    print(json.dumps({"actions": len(manifest["actions"]),
                      "frames": sum(a["frames"] for a in manifest["actions"].values()),
                      "bytes": size, "clipped": warnings}, ensure_ascii=False))
    return 0


def calibrate(args):
    """Give every adopted strip the idle head size without rerendering the animation."""
    from PIL import Image
    from regular.pet_repertoire import ACTIONS

    source, out = Path(args.source), Path(args.out)
    if source.resolve() == out.resolve():
        raise ValueError("calibrate into a separate directory before adopting")
    spec = json.loads((source / "sprites.json").read_text(encoding="utf-8"))
    width, height = spec["frame"]
    old_x, old_y = spec["anchor"]
    out.mkdir(parents=True, exist_ok=True)
    result = dict(spec, frame=list(CANVAS), anchor=list(ANCHOR), geometry={},
                  calibration={"method": "head-match-v2", "reference": "idle"})

    def frames_of(name):
        action = spec["actions"][name]
        with Image.open(source / action["file"]) as strip:
            return [strip.crop((i * width, 0, (i + 1) * width, height)).convert("RGBA")
                    for i in range(action["frames"])]

    idle = frames_of("idle")
    alpha = idle[0].getchannel("A").point(lambda a: 255 if a > 40 else 0).getbbox()
    # The idle figure defines the size on screen; matching only needs a square canvas.
    base = 1.0 if [width, height] == list(CANVAS) else FIGURE_HEIGHT / (alpha[3] - alpha[1])
    padded = lambda frame: frame if frame.size == (width, width) else frame.crop((0, 0, max(width, height), max(width, height)))
    matcher = HeadMatcher(padded(idle[0]))
    measured = {}
    for name in spec["actions"]:
        if args.only and name not in args.only:
            measured[name] = (1.0, [], "kept")
        else:
            relative, rows = matcher.strip_scale([padded(frame) for frame in frames_of(name)])
            measured[name] = (relative, rows, "measured" if relative else None)

    def family(name):
        parts = name.split("_")
        return "_".join(parts[:2]) if len(parts) > 2 else name

    # A face hidden by a pillow, a wing or the screen edge: use its sheet family, drawn in one batch.
    for name, (relative, rows, source_kind) in list(measured.items()):
        if relative is None:
            kin = [value for other, (value, _, kind) in measured.items()
                   if kind == "measured" and other != name and family(other) == family(name)]
            if kin:
                measured[name] = (statistics.median(kin), rows, "family")
    report = {}
    for name, action in spec["actions"].items():
        frames = frames_of(name)
        relative, rows, source_kind = measured[name]
        desired = base / relative if relative else base
        grip = name == "edge_peek" or ACTIONS.get(name, {}).get("grip_x") is not None
        source_x = old_x - FIGURE_HEIGHT * .12 - 3 if grip else old_x
        target_x = ANCHOR[0] - FIGURE_HEIGHT * .12 - 3 if grip else ANCHOR[0]
        geometry = [(source_x, old_y, frame.getchannel("A").point(lambda a: 255 if a > 40 else 0).getbbox())
                    for frame in frames]
        scale = _fit_scale(geometry, desired, source_x, target_x)
        output = Image.new("RGBA", (CANVAS[0] * len(frames), CANVAS[1]))
        bounds = []
        for i, frame in enumerate(frames):
            if abs(scale - 1.0) < 0.004 and frame.size == CANVAS and (source_x, old_y) == (target_x, ANCHOR[1]) and not grip:
                canvas = frame              # already right: do not re-encode a second generation
            else:
                resized = frame.convert("RGBa").resize((round(width * scale), round(height * scale)),
                                                        Image.Resampling.LANCZOS).convert("RGBA")
                canvas = Image.new("RGBA", CANVAS)
                canvas.alpha_composite(resized, (round(target_x - source_x * scale), round(ANCHOR[1] - old_y * scale)))
            if grip:
                canvas = _drop_support(canvas, target_x)
            bounds.append(canvas.getchannel("A").point(lambda a: 255 if a > 40 else 0).getbbox())
            output.alpha_composite(canvas, (i * CANVAS[0], 0))
        output.save(out / action["file"], "WEBP", quality=90, alpha_quality=95, method=6)
        union = [min(box[0] for box in bounds), min(box[1] for box in bounds),
                 max(box[2] for box in bounds), max(box[3] for box in bounds)]
        result["geometry"][name] = {"bounds": [union[0] - ANCHOR[0], union[1] - ANCHOR[1],
                                               union[2] - ANCHOR[0], union[3] - ANCHOR[1]]}
        report[name] = {"relative_head": round(relative, 3) if relative else None, "from": source_kind,
                        "scale": round(scale, 4),
                        "limited": scale < desired * .98,
                        "frames": [[round(score, 2), round(rel, 3)] for score, rel in rows]}
        print(json.dumps({"name": name, **{k: v for k, v in report[name].items() if k != "frames"}}), flush=True)
    (out / "sprites.json").write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    (out / "calibration-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n")
    print(json.dumps({"actions": len(report), "limited": [name for name, row in report.items() if row["limited"]],
                      "unmeasured": [name for name, row in report.items() if row["relative_head"] is None]}))
    return 0


def review(args):
    from PIL import Image
    from regular.pet_repertoire import ACTIONS
    source, out = Path(args.source), Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    for name in (n for n in ACTIONS if n.startswith(args.group + "_")):
        path = source / (name + ".png")
        if not path.is_file():
            rows.append({"name": name, "missing": True})
            continue
        sheet = Image.open(path).convert("RGBA")
        issues = [] if sheet.size == (1024, 1024) else ["layout"]
        for index in range(4):
            alpha = _cell(sheet, index).getchannel("A")
            if sum(alpha.histogram()[:12]) < CELL * CELL * .12:
                issues.append(f"opaque-{index}")
            box = alpha.point(lambda v: 255 if v > 40 else 0).getbbox()
            if box is None:
                issues.append(f"empty-{index}")
            elif min(box[:2]) < 2 or max(box[2:]) > CELL - 2:
                issues.append(f"edge-{index}")
        rows.append({"name": name, "issues": issues})
    (out / "review.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1) + "\n")
    print(json.dumps({"present": sum(not r.get("missing") for r in rows), "flagged": [r for r in rows if r.get("issues")], "missing": [r["name"] for r in rows if r.get("missing")]}, ensure_ascii=False))
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    gen = sub.add_parser("generate", help="paid sheet generation (plan only without --allow-paid)")
    gen.add_argument("--directory", required=True, help="private instance directory holding .env")
    gen.add_argument("--output", required=True, help="directory outside the repository for raw sheets")
    gen.add_argument("--stage", choices=("base", "anchor", "sheets", "faces", "library"), required=True)
    gen.add_argument("--group", choices=("motion", "dragon", "life", "boundary"), help="select one library director's group")
    gen.add_argument("--prompt-file", help="replacement action prompt for one selected sheet")
    gen.add_argument("--style-reference", help="override the style-reference PNG")
    gen.add_argument("--only", nargs="+", help="limit to these sheet names")
    gen.add_argument("--model", choices=MODELS, default=MODELS[0])
    gen.add_argument("--parallel", type=int, default=4)
    gen.add_argument("--allow-paid", action="store_true")
    bld = sub.add_parser("build", help="offline: slice reviewed sheets into runtime strips")
    bld.add_argument("--source", required=True)
    bld.add_argument("--out", default=str(ROOT / "regular/assets/pet"))
    bld.add_argument("--only", nargs="+", help="rebuild only these strips; the others stay as they are")
    cal = sub.add_parser("calibrate", help="offline: unify adopted character scale using head measurements")
    cal.add_argument("--source", required=True)
    cal.add_argument("--out", required=True)
    cal.add_argument("--only", nargs="+", help="measure only these strips; the others are copied at their current scale")
    rev = sub.add_parser("review", help="offline layout and transparency checks")
    rev.add_argument("--source", required=True)
    rev.add_argument("--out", required=True)
    rev.add_argument("--group", choices=("motion", "dragon", "life", "boundary"), required=True)
    args = parser.parse_args()
    if args.command == "build":
        return build(args)
    if args.command == "calibrate":
        return calibrate(args)
    if args.command == "review":
        return review(args)
    if Path(args.output).resolve().is_relative_to(ROOT):
        parser.error("keep raw generated sheets outside the repository")
    if not args.allow_paid:
        print(json.dumps(plan(args), ensure_ascii=False, indent=1))
        return 0
    return asyncio.run(generate(args))


if __name__ == "__main__":
    sys.exit(main())
