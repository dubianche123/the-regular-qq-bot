"""Prop-matched micro-endings and deliberate put-away beats for quiet activities."""

_HEADER = (
    "Use case: illustration-story. Draw a transparent 1024x1024 animation sprite sheet: "
    "EXACTLY four complete frames in a 2x2 grid, each cell 512x512, read left-to-right "
    "then top-to-bottom. Image 1 gives the original adult chibi dragon-woman identity. "
    "Image 2 is the EXACT existing activity being continued: preserve its particular "
    "prop, pose, hands, face size, body proportions, clothes, camera angle and palette. "
    "Do not redesign the prop or substitute another instrument. Silver-white lavender "
    "hair, two lilac horns, pointed ears, green clip with ivory flower, lavender scarf, "
    "sage cardigan, cream dress, white socks and sneakers, two little lavender bat "
    "wings and one scaled tail. Exactly TWO connected arms, TWO ordinary human hands "
    "and TWO legs throughout. All four cells use one fixed camera, the same head "
    "diameter, body proportions and support line. Small expressive changes in eyes, "
    "shoulders, fingers and tail are welcome; no unexplained standing/sitting change. "
    "Keep the full character and complete prop inside each cell with transparent "
    "margin. No other person, bag, case, furniture, floor, cast shadow, backdrop, "
    "letters, screen text, numbers, icons, musical notes or border. Empty background "
    "is genuine alpha-zero transparency. Never add magic, a disappearing effect, "
    "a new object or an extra hand to solve the action."
)

_WORK = (
    "Remain in the EXACT cross-legged seated pose from Image 2. The SAME small sage "
    "green laptop lies open across her lap, its lid back facing the viewer and its "
    "single tiny ivory flower emblem unchanged; preserve lid shape, width, angle "
    "and keyboard base. The left-curled tail and crossed shoes stay in place. "
)
_UKULELE = (
    "Remain STANDING with the same relaxed knees and shoes as Image 2. Hold the SAME "
    "small warm-brown ukulele with an hourglass body, dark sound hole, short neck "
    "pointing diagonally toward the viewer's RIGHT and unchanged strings/tuners. "
    "Her left hand supports its neck and her right hand rests at the strings, exactly "
    "as the reference. Do not sit or turn this into a guitar, violin or kalimba. "
)
_KALIMBA = (
    "Remain seated with both knees together in front and both shoes together, "
    "EXACTLY as Image 2, never cross-legged. Hold the SAME tiny brown rectangular "
    "kalimba with the same row of silver tines at the center of her lap, supported "
    "by both palms with both thumbs over the tines. Keep the curled tail to her left. "
    "Do not replace it with a box, book, phone or string instrument. "
)

# name, title, reference, posture, family, direction
_ROWS = [
    ("work_clear", "理顺了轻轻确认", "life_laptop_type", "sit", "work", _WORK +
     "Frame 1: copy the neutral typing pose and open laptop of Image 2, both hands at "
     "the keyboard. Frame 2: finish one deliberate key press, eyes becoming clear and "
     "focused as she understands. Frame 3: shoulders ease with a small relieved breath "
     "and the tail tip lifts slightly; a restrained satisfied smile, no victory pose. "
     "Frame 4: settle both hands back at the SAME keyboard position, laptop still fully "
     "open at the original angle, small content smile. Do not close or remove it yet."),
    ("work_pause", "卡住了先停一停", "life_laptop_type", "sit", "work", _WORK +
     "Frame 1: copy the neutral typing pose and open laptop of Image 2. Frame 2: stop "
     "typing, one hand briefly touches her chin, eyebrows draw together slightly as "
     "she studies the unseen screen. Frame 3: lower that hand without typing again, "
     "shoulders droop a little and let out a small real sigh; no smile or self-mocking "
     "performance. Frame 4: both hands rest at the SAME original keyboard position, "
     "laptop still fully open at its original angle, thoughtful tired eyes. The problem "
     "is still unresolved. Do not close or remove the laptop yet."),
    ("ukulele_resolve", "收好最后一个和弦", "dragon_music_ukulele", "stand", "ukulele", _UKULELE +
     "Frame 1: copy the reference's neutral playing pose and instrument placement. "
     "Frame 2: make one neat final strum, left hand holds the same neck position, a "
     "small bright smile. Frame 3: stop the right hand just above the strings and "
     "listen to the last chord, tail tip curls with quiet pride. Frame 4: both hands "
     "return to the SAME neutral holding positions and instrument angle as frame 1, "
     "lips closed in a restrained satisfied smile; the song is over. No bow or flourish."),
    ("ukulele_sour", "弹错后认真听一听", "dragon_music_ukulele", "stand", "ukulele", _UKULELE +
     "Frame 1: copy the reference's neutral playing pose and instrument placement. "
     "Frame 2: right hand halts after a wrong note, blink with a tiny surprised eyebrow "
     "lift, left hand keeps supporting the SAME neck. Frame 3: look down at the strings "
     "and listen carefully, shoulders soften in small genuine disappointment, no grin "
     "or embarrassed show. Frame 4: stop playing, both hands return to the SAME neutral "
     "holding positions and instrument angle as frame 1, a quiet thoughtful face. "
     "Do not retry successfully, change the instrument or make the mistake a joke."),
    ("kalimba_linger", "听着余音出神", "dragon_music_kalimba", "sit", "kalimba", _KALIMBA +
     "Frame 1: copy the reference's neutral playing pose and both thumb positions. "
     "Frame 2: gently pluck one final tine with a thumb while both palms support the "
     "SAME instrument. Frame 3: let both thumbs rest, turn one ear slightly toward the "
     "kalimba and listen with soft absorbed eyes. Frame 4: return to the SAME steady "
     "two-handed holding position as frame 1, calm faint smile and lingering gaze. "
     "The pleasure is quiet listening, no triumphant pose or extra musical effects."),
    ("kalimba_drowsy", "困了慢慢停手", "dragon_music_kalimba", "sit", "kalimba", _KALIMBA +
     "Frame 1: copy the reference's neutral playing pose and both thumb positions. "
     "Frame 2: thumb movement slows, eyelids grow heavy, both palms keep the SAME "
     "kalimba securely on her lap. Frame 3: a small sleepy head dip and long blink, "
     "thumbs have stopped, shoulders relax without dropping the instrument. Frame 4: "
     "raise the head to the original position, eyes half open, both hands in the SAME "
     "stable holding pose as frame 1. She deliberately pauses to rest, no startled "
     "recovery, sudden cheerfulness or sleeping collapse."),
    ("work_put_away", "合上电脑收好", "life_laptop_type", "sit", "work", _WORK +
     "Images 3 and 4 show the two preceding emotional endings. Continue their LAST "
     "cell's PROP AND HAND POSITION ONLY, do NOT copy their facial emotion. This "
     "practical cleanup must stay emotionally neutral in ALL FOUR cells: eyes OPEN "
     "or half-open, mouth CLOSED with a relaxed STRAIGHT line, no upturned smile, "
     "closed happy eyes, proud expression or relieved grin, especially in the final "
     "empty-handed frame. Frame 1: the laptop is still OPEN on her lap, both hands at the keyboard, "
     "matching the preceding endpoint. Frame 2: gently close that SAME lid with one "
     "hand while the other steadies its base; show the closed sage lid and flower. "
     "Frame 3: with BOTH hands slide the closed laptop sideways past her LEFT hip, "
     "toward the space BEHIND her torso and curled tail; most is occluded but a clearly "
     "recognizable strip of the SAME green lid edge is still visible between her "
     "carrying hand and tail. Keep that hand touching the lid so the slide is legible. Frame 4: "
     "the laptop now rests fully OCCLUDED behind her hip and tail, hands have visibly "
     "released it and return EMPTY to her lap. Eyes remain OPEN/HALF-OPEN and lips "
     "remain straight, calm and neutral; no smile is introduced by putting it away. "
     "Keep the same seated position, legs and camera. No bag, shrinking, dissolving, "
     "cutaway or prop replacement."),
    ("ukulele_put_away", "把小琴轻轻收好", "dragon_music_ukulele", "stand", "ukulele", _UKULELE +
     "Images 3 and 4 show the two preceding emotional endings. Continue their LAST "
     "cell with the same instrument and posture; this cleanup is calm and emotionally "
     "neutral: CLOSED MOUTH, relaxed open eyes, no smile or surprise in any frame. "
     "Frame 1: match the steady neutral two-handed holding pose and angled neck "
     "exactly. Frame 2: visibly move the WHOLE ukulele away from her chest to the "
     "VIEWER'S LEFT of her waist, held by both hands; rotate its neck downward. The "
     "ENTIRE unchanged instrument must be visible OUTSIDE her body silhouette, so "
     "we clearly see where it is going. Frame 3: move the SAME full-sized instrument "
     "from that side position just BEHIND her torso; half its brown body and part of "
     "the SAME neck remain clearly visible beside the left hip and tail, the hand "
     "still visibly carrying it. Frame 4: finish placing it fully BEHIND the opaque "
     "torso and tail, then return both EMPTY hands to her sides; no wood or neck "
     "remains visible. Feet, tail size, camera and character position stay fixed. "
     "The crucial sequence is front hold / whole instrument at LEFT SIDE / HALF "
     "OCCLUDED at same side / fully behind. Never jump straight from chest to empty "
     "hands, shrink it, grow the tail, add a case or erase it in open space."),
    ("kalimba_put_away", "放好拇指琴歇一会", "dragon_music_kalimba", "sit", "kalimba", _KALIMBA +
     "Images 3 and 4 show the two preceding emotional endings. Continue their LAST "
     "cell with the same instrument and posture; this cleanup stays emotionally "
     "neutral: CLOSED MOUTH, relaxed open eyes throughout, no smile or happy shut "
     "eyes. Frame 1: match the steady two-handed kalimba holding pose exactly. "
     "Frame 2: lower the SAME brown kalimba from the knees and carry it with BOTH "
     "hands toward her LEFT hip, silver tines and whole instrument still visible. "
     "Frame 3: slide it farther BEHIND the left hip and curled tail; only its final "
     "brown corner and a few silver tines remain visible, with the hand STILL "
     "carrying it to explain the movement. Frame 4: finish pushing it completely "
     "BEHIND the opaque hip and tail, then return both EMPTY hands to the same knees. "
     "ZERO brown wood or silver tines remain visible in the final cell: it is NOT "
     "lying outside on top of the tail. Keep knees, shoes, tail size and camera "
     "fixed. Do not stand, cross the legs, add a bag, smile, shrink the instrument "
     "or erase it while still in open space."),
]

ACTIONS = {
    "ending_" + name: {
        "title": title, "prompt_header": _HEADER, "prompt": prompt, "style": reference,
        "starts_from": posture, "posture": posture, "role": "activity_ending",
        "family": family, "fps": 4, "loop": False, "automatic": False,
        "sequence": [0, 0, 1, 1, 2, 2, 3, 3, 3, 3], "duration": 2.5,
        "scale": 1.0 if posture == "stand" else 0.8, "fixed_ground": True,
        "calibration_reference": reference, "calibration_frames": [0, 3],
    }
    for name, title, reference, posture, family, prompt in _ROWS
}

for _family, _outcomes in {
    "work": ("clear", "pause"), "ukulele": ("resolve", "sour"),
    "kalimba": ("linger", "drowsy"),
}.items():
    ACTIONS[f"ending_{_family}_put_away"]["extra_refs"] = [
        f"ending_{_family}_{outcome}" for outcome in _outcomes
    ]
