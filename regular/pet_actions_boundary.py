"""Explicit four-frame transitions for screen-edge play."""

_COMMON = (
    "Four separate sequential full-body frames in a 2 by 2 grid, each 512 by 512 pixels, "
    "on transparent background. Exact original adult chibi dragon woman, silver-white lavender "
    "hair, sage cardigan, cream dress, the same face, horns, small wings, one tail and shoes. "
    "Exactly two connected arms, two hands and two legs throughout. Keep face size, camera and "
    "rendering consistent, with each complete figure fully inside its own cell. No text, lines, "
    "grid, wall, door, ceiling, pole, support, border or background; no extra limbs or characters. "
)

ACTIONS = {
    "boundary_top_peek": {
        "title": "从上边倒探头", "style": "idle", "posture": "air", "role": "top_peek",
        "family": "ceiling", "fps": 1.0, "loop": True, "scale": 1.0, "duration": 5.0,
        "head_band": "lower",
        "prompt": _COMMON + (
            "In ALL FOUR frames she is completely UPSIDE DOWN, feet and legs above the torso, "
            "her face BELOW her torso and looking toward the viewer. Draw the ENTIRE inverted body "
            "including both shoes, both wings, tail and horns with transparent margin. No hand or "
            "foot touches an actual object. Both natural arms attach to her shoulders, hands close "
            "to her cheeks. Frame 1: open curious eyes, hands still. Frame 2: eyes glance a little "
            "to the side, fingertips shift slightly. Frame 3: gentle blink, tail tip curls slightly. "
            "Frame 4: eyes reopen, hands and tail settle. Keep the inverted body and face position "
            "fixed; no rotation between frames, no cut-off body or upside-down face pasted onto an upright body."
        ),
    },
    "boundary_popout": {
        "title": "从边上轻跳出来", "style": "idle", "posture": "stand", "role": "popout",
        "family": "edge", "fps": 3.0, "loop": False, "scale": 1.0, "duration": 1.4,
        "prompt": _COMMON + (
            "A cheerful SMALL side-peek to hop transition, with the entire figure visible in every frame. "
            "No real edge is drawn; movement across the screen is handled externally. Keep the head "
            "center, face size, torso center and fixed front three-quarter camera unchanged. "
            "Frame 1: crouch lightly with hands near chest and wings folded. Frame 2: knees extend "
            "a little, wings open slightly. Frame 3: both toes lift a short distance, knees softly bent. "
            "Frame 4: toes return to the same ground line, wings settle. Both arms and hands stay "
            "naturally connected, no pointing or exaggerated leap."
        ),
    },
    "boundary_tuck": {
        "title": "收翼藏一藏", "style": "idle", "posture": "sit", "role": "hide",
        "family": "edge", "fps": 2.0, "loop": False, "scale": 0.8, "duration": 2.0,
        "prompt": _COMMON + (
            "Gentle crouching to hide, full body visible in every cell and no wall drawn. "
            "Maintain exactly the same face size and viewing angle throughout. Frame 1: knees "
            "slightly bent, wings relaxed. Frame 2: lower into a small crouch, wings fold inward. "
            "Frame 3: crouch low on both feet, tail curls close to her body, hands rest near her knees. "
            "Frame 4: hold that compact crouch, eyes gently glance out. No disappearing body, "
            "dramatic tilt, extra hands or sleeping."
        ),
    },
    "boundary_top_return": {
        "title": "倒挂松手转正", "style": "idle", "posture": "air", "role": "top_return",
        "family": "ceiling", "fps": 2.0, "loop": False, "scale": 1.0, "duration": 2.0,
        "head_bands": ["lower", "lower", "upper", "upper"],
        "calibration_frames": [0, 3], "alpha_cutoff": 21,
        "prompt": _COMMON + (
            "ANATOMY PRIORITY: exactly two arms and two hands TOTAL in every frame, each hand "
            "attached to its own shoulder by one arm. In the upside-down first frame both hands "
            "are beside her cheeks ONLY; no hands on sleeves, near her hips, or elsewhere. "
            "A smooth return from hanging upside down to standing upright, one continuous four-frame "
            "rotation with fixed camera and the same face size. Frame 1: ENTIRE body upside down, "
            "shoes above the torso and face below, arms naturally bent. Frame 2: gently release and "
            "rotate a quarter turn, wings tucked close. Frame 3: rotate further toward upright, legs "
            "below the torso, wings still tucked. Frame 4: stand fully upright on both feet, arms "
            "relaxed and wings folded. One body only per cell, no duplicate heads, extra hands, "
            "support, ceiling, motion lines or abrupt change of identity."
        ),
    },
}
