"""Small unhappy reactions, selected from real mood rather than the ambient pool."""

_HEADER = (
    "Create a transparent 1024x1024 animation sprite sheet, exactly four full-body frames "
    "in a 2x2 grid of equal 512px cells. Match the shipped idle reference exactly: adult "
    "chibi dragon-woman, silver-lilac hair and horns, lavender eyes, ivory flower and green "
    "clip, lavender scarf, sage cardigan, cream dress, socks and sneakers, two bat wings "
    "and one tail. Preserve her head diameter, torso and leg length, costume, linework and "
    "camera scale in every frame. Exactly two arms, two hands and two legs. Keep the whole "
    "character inside each cell with transparent margin and a common shoe baseline. "
    "True alpha-zero background; no floor, cast shadow, scenery, text, symbols, props or "
    "extra characters. Cute displeasure, never violent rage or a sudden happy recovery."
)

_ROWS = (
    ("pout", "抱臂鼓脸小生气", "stand", False,
     "1: stand with brows drawn inward, small closed mouth and hands tense below chest. "
     "2: gently fold both arms across the cardigan and purse her lips. 3: keep arms crossed, "
     "puff cheeks slightly, fold wings closer and curl tail tip. 4: lift chin a little and "
     "give a small displeased sideways glance, still pouting. Feet stay planted. No stamping, "
     "shouting, pointing, crying, turning her back or smiling."),
    ("look_away", "抱臂别过脸又偷看", "stand", True,
     "Standing slow sulk loop with feet planted and arms folded. 1: slight three-quarter "
     "angle, look away from the viewer, lips pursed. 2: look slightly farther away with a "
     "small huff expressed only by face and shoulders. 3: steal a sideways glance back, "
     "still feeling wronged. 4: avert gaze toward the first side. Small head and eye motion "
     "only; frame 4 loops naturally to frame 1. No stepping, waving, tears or smile."),
    ("tail_hug", "委屈地抱住尾巴", "sit", False,
     "1: stand slightly hunched, hands near chest, brows raised inward and mouth downturned. "
     "2: bend both knees to sit and guide her own tail forward with both hands. 3: sit with "
     "knees close and shoes visible, hug the tail across her lap and lower her gaze. 4: keep "
     "the same seated pose, half-blink and glance softly toward the viewer, still quietly "
     "upset. Preserve torso length and head size while bending joints. No smile, tears, "
     "extra toy, extra tail or furniture."),
)

ACTIONS = {
    "mood_" + name: {
        "title": title, "prompt_header": _HEADER, "prompt": prompt, "style": "idle",
        "starts_from": "stand", "posture": posture, "role": "mood", "family": "upset",
        "fps": 1.0 if loop else 3.0, "loop": loop, "scale": 1.0,
        "sequence": [0, 1, 2, 3] if loop else [0, 0, 1, 1, 2, 2, 3, 3, 3],
        "duration": 30.0 if loop else 3.0, "automatic": False,
        "fixed_ground": True, "drop_strays": True, "calibration_reference": "idle",
        "calibration_frames": [0],
    }
    for name, title, posture, loop, prompt in _ROWS
}

PLAYBACK_ALIASES = {
    "mood_pout_hold": {
        "strip": "mood_pout", "title": "还在鼓着脸", "starts_from": "stand", "posture": "stand",
        "role": "mood", "family": "upset", "fps": 0.5, "loop": True, "sequence": [2, 3],
        "scale": 1.0, "duration": 30.0, "restful": True, "automatic": False,
    },
    "mood_tail_hold": {
        "strip": "mood_tail_hug", "title": "抱着尾巴缓一缓", "starts_from": "sit", "posture": "sit",
        "role": "mood", "family": "upset", "fps": 0.5, "loop": True, "sequence": [2, 3],
        "scale": 1.0, "duration": 30.0, "restful": True, "automatic": False,
    },
}
