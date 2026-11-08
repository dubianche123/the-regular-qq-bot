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

# Anticipation/recovery is drawn; travel and hop arcs belong to PetBrain.
_WING_ACTIONS = {
    "boundary_side_launch": ("扑翼跃出边框", "air", "edge_launch", "side", 7.0,
        "An eager wing-assisted sideways hop toward the viewer's RIGHT. "
        "Frame 1: crouch anticipation, knees bent, hands close to chest, wings folded, playful expectant eyes. "
        "Frame 2: extend knees to push off, toes point down, BOTH wings sweep UP beside her head, excited smile. "
        "Frame 3: airborne with knees softly tucked, BOTH wings sweep DOWN beside her hips in a visible power stroke. "
        "Frame 4: still airborne, wings spread horizontally, feet reach downward, happy crescent eyes and tiny proud smile. "
        "BOTH hands remain close to her chest in ALL FOUR frames: exactly two arms and two hands TOTAL, "
        "never add palms, hands or arms at her hips. Only her wings and knees make the large movement."),
    "boundary_bottom_spring": ("下沿扑翼蹦出来", "air", "edge_launch", "bottom", 7.0,
        "A delighted tiny dragon springing UP out of a hiding place. Draw the complete body in every cell, no actual edge. "
        "Frame 1: compact crouch on both feet, elbows close, tail curled beside her, wings folded, cheeky anticipatory smile. "
        "Frame 2: knees extend, toes point down, both wings sweep UP beside her head, eyes widen happily. "
        "Frame 3: both feet airborne, knees gently tuck, both wings sweep DOWN beside her hips, joyful smile. "
        "Frame 4: airborne, wings return horizontally, legs extend for landing, tiny pleased wink. "
        "BOTH hands stay near her chest throughout ALL FOUR frames, exactly TWO arms and TWO hands TOTAL. "
        "No extra hand or palm at her hips or waist. The wings are separate from her arms."),
    "boundary_land": ("收翼落地得意笑", "stand", "edge_land", "landing", 6.0,
        "A soft wing-assisted LANDING followed by a cute self-satisfied greeting. Shoes share a fixed imaginary ground line, no drawn floor. "
        "Frame 1: toes just touch, heels slightly lifted, wings UP and outward to brake, hands beside waist, gentle focused eyes. "
        "Frame 2: feet planted, knees bend slightly to absorb landing, wings sweep DOWN beside hips, small closed-mouth smile. "
        "Frame 3: knees straighten, wings fold neatly behind her shoulders, hands relax. "
        "Frame 4: same standing size, a tiny head tilt and one-handed wave near her cheek, pleased crescent eyes, tail tip curls. "
        "No stomp, deep bow or leap."),
}
for _name, (_title, _posture, _role, _family, _fps, _action) in _WING_ACTIONS.items():
    ACTIONS[_name] = {
        "title": _title, "style": "motion_fly_flap_slow", "posture": _posture,
        "role": _role, "family": _family, "fps": _fps, "loop": False,
        "scale": 1.0, "duration": 4 / _fps,
        "prompt": _COMMON + _action + (
            " Fixed front three-quarter camera and head column. Match exactly the supplied flapping sheet's "
            "face diameter, body proportions, outfit and linework. Wing angles MUST visibly change between phases: "
            "folded, UP, DOWN, horizontal. Two separate lavender bat wings attached behind her shoulders; wings are not arms. "
            "Movement across the screen is handled by runtime: no travel across a cell, drawn speed lines, "
            "floor, support, glitter or background. Only joints, wing angles and expression change. "
            "Never enlarge or shrink the face or camera; keep the entire figure contained with transparent margin."
        ),
    }

# Enter the supported sleeping pose once; only the settled last two frames loop.
_REST_POSES = {
    "boundary_bottom_sleep": ("枕着手臂趴睡", "bottom_sleep_ref", "bottom", None, 350,
        "Resting her cheek ON CROSSED FOREARMS laid on an INVISIBLE horizontal ledge at y=350. "
        "Head and forearms above that line; compact kneeling body, folded wings and legs below/behind it. "
        "NOT standing. Frame 1: forearms crossed on the ledge, head a little above them, drowsy open eyes. "
        "Frame 2: lower and tilt her cheek onto the sleeves, eyes nearly shut. Frame 3: eyes fully closed, "
        "soft cheek pressed on arms, peaceful smile and one tiny clear drool bead at her mouth corner. "
        "Frame 4: exact same supported sleeping silhouette, only an ear or wing tip relaxes a tiny bit."),
    "boundary_bottom_pillow": ("掏枕头趴睡", "bottom_sleep_ref", "bottom", None, 375,
        "Place ONE SMALL cream pillow with lavender seam on an INVISIBLE ledge at y=350 and sleep on it. "
        "Head, arms and pillow above that line; compact kneeling body below/behind it. Frame 1: one hand "
        "brings the pillow from beside her cardigan, the other forearm rests on the ledge, face fully visible. "
        "Frame 2: both hands place the pillow and her head lowers. Frame 3: one cheek softly pressed into "
        "the pillow, fully closed eyes, tiny drool bead, hands on the two pillow sides. Frame 4: identical "
        "sleeping support/face position, a tiny ear/tail twitch. No leftover chest hands."),
    "boundary_side_sleep": ("把边框当床蜷睡", "side_sleep_ref", "side", 160, None,
        "Use an INVISIBLE VERTICAL edge at x=160 as a magical sideways BED. The complete little dragon "
        "is on the RIGHT of that edge, clearly CURLED ON HER SIDE in a fetal sleeping pose, knees drawn "
        "toward belly, relaxed feet, NOT upright standing. Her tail hooks round the edge for support, "
        "one small wing wraps over her body like a blanket. A small cream cushion is pressed against "
        "the edge under her cheek, exactly two connected hands hold it. Frame 1: she eases her body out "
        "from behind the edge, one foot still braced there, face visible and drowsy, cushion near cheek. "
        "Frame 2: curl both knees up, tuck feet, settle cheek onto the cushion. Frame 3: fully closed eyes, "
        "soft cheek pressed into cushion, content curled-up sleep. Frame 4: the same curled sleeping pose, "
        "only a tiny ear/tail twitch. Whole figure in every cell, no drawn edge, bed, ground or new limb."),
    "boundary_top_sleep": ("像蝙蝠裹翼挂睡", "top_curl_ref", "top", None, 100,
        "Image 2 supplies the exact COMPACT UPSIDE-DOWN CURLED sleeping pose. Preserve its deeply "
        "bent knees and folded legs: heels close to her belly, feet ABOVE the head. NEVER straighten "
        "the legs or torso into an inverted standing girl. Hook her little claws/curled tail around "
        "an INVISIBLE horizontal edge at y=100; her rounded bean-shaped body hangs BELOW it. "
        "Her face remains inverted BELOW the torso. BOTH hands hold ONE small cream pillow right "
        "against her cheek in ALL FOUR frames; no fists or leftover hands at her waist. "
        "Frame 1: already curled hanging like a bat, drowsy open eyes, wings half wrapped around "
        "her shoulders. Frame 2: cheek nestles into pillow, wings wrap closer around the compact "
        "body like a blanket. Frame 3: fully closed eyes, peaceful mouth, cozy soft wing cocoon. "
        "Frame 4: identical curled silhouette/contact/head, only a tiny ear/tail twitch. No drawn "
        "motion lines. Full figure contained, no rope, supporting object, straight legs, exposed "
        "underwear, upright torso or inverted face pasted onto a standing body."),
}
for _name, (_title, _style, _family, _rest_x, _rest_y, _pose) in _REST_POSES.items():
    ACTIONS[_name] = {
        "title": _title, "style": _style, "posture": "air" if _family == "top" else "lie",
        "role": "edge_sleep", "family": _family, "fps": 0.5, "loop": True,
        "sequence": [2, 3], "entry_sequence": [0, 1, 2], "entry_fps": 2.5,
        "scale": 1.0, "duration": 8.0, "calibration_frames": [0],
        **({"grip_x": _rest_x} if _rest_x is not None else {}),
        **({"alpha_cutoff": 24} if _family == "side" else {}),
        **({"rest_y": _rest_y} if _rest_y is not None else {}),
        "prompt": _COMMON + _pose + (
            " Fixed camera and face diameter across all four cells, same original face, green cardigan, "
            "cream dress, scarf, horns and linework. Exactly TWO arms, TWO hands, TWO wings, ONE tail. "
            "All joints connected, no extra hand at chest/waist. Contact line is fixed; runtime supplies "
            "the real edge. No drawn bed, wall, pole, line, text, Z, sparkles, floor or background. "
            "Pillow/cushion is tiny and soft, not a mattress. Keep the complete modestly clothed figure "
            "within each cell with transparent margin. Facial softness is gentle, never change scale."
        ),
    }

ACTIONS["boundary_top_look"] = {
    "title": "倒挂看鼠标", "style": "top_sleep_ref", "posture": "air",
    "role": "top_peek", "family": "ceiling", "fps": 1.0, "loop": True,
    "scale": 1.0, "duration": 5.0, "calibration_frames": [0, 1, 2],
    "prompt": _COMMON + (
        "Copy Image 2's exact UPSIDE-DOWN hanging body: shoes ABOVE torso, inverted face BELOW torso, "
        "hands near chest. Keep body, head position, hair, hands, clothes, camera and face size unchanged. "
        "Change ONLY eyes. Frame 1: both lavender irises look clearly toward the LEFT edge of the IMAGE. "
        "Frame 2: both irises look at the viewer. Frame 3: both irises look clearly toward the RIGHT edge "
        "of the IMAGE. Frame 4: both eyes gently closed mid-blink. No body rotation, head tilt, new hand, "
        "line, supporting edge, object or text. Do not turn the whole head to simulate eye direction."
    ),
}

ACTIONS["boundary_side_slip"] = {
    "title": "睡滑了扑翼抓稳", "style": "boundary_side_sleep", "posture": "air",
    "role": "edge_sleep_event", "family": "side", "fps": 6.0, "loop": False,
    "scale": 1.0, "duration": 4 / 6, "grip_x": 160, "alpha_cutoff": 24, "calibration_frames": [0, 3],
    "prompt": _COMMON + (
        "Continue Image 2's curled side-sleep pose on the RIGHT of an INVISIBLE VERTICAL edge at x=160. "
        "A tiny harmless sleep-slip, then catch the edge with a dragon wingbeat. Frame 1: her cheek "
        "leaves the little cushion, sleepy eyes half open as the tail grip loosens. Frame 2: surprised "
        "open eyes, BOTH natural hands reach toward the edge, TWO wings sweep UP, knees unfold slightly. "
        "Frame 3: both wings sweep DOWN in a clear power stroke, one hand grabs the edge and the tail "
        "hooks it again; no extra hand remains near the pillow. Frame 4: both hands hold the small "
        "cushion against the edge, curled knees, relieved shy smile, wings fold. Exactly TWO arms and "
        "TWO hands TOTAL. Same face size/camera; no travel across the cell, runtime supplies the fall "
        "and recovery. Full figure within each cell, no drawn edge, floor, motion line or text."
    ),
}

# The curled pose puts her knees and feet inside the screen. Keep that art in
# the archive; edge hiding instead dozes on the same grip as the awake peek.
ACTIONS["boundary_side_sleep"].update(automatic=False, slip_clip="boundary_side_slip")
PLAYBACK_ALIASES = {
    "boundary_side_doze": {
        "title": "躲在边框后打盹", "strip": "motion_edge_look", "posture": "air",
        "role": "edge_sleep", "family": "side", "fps": 0.5, "loop": True,
        "sequence": [3, 3], "entry_sequence": [1, 1, 3], "entry_fps": 2.5,
        "duration": 8.0,
    },
}
