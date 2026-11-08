"""One-shot airborne play; the flight controller owns movement and cursor contact."""

_HEADER = (
    "Use case: illustration-story. Create a 1024x1024 transparent animation sprite sheet, "
    "exactly FOUR sequential frames in a 2x2 grid of 512x512 cells, read left-to-right then "
    "top-to-bottom. Image 1 is the exact adult chibi dragon-woman character model. Image 2 "
    "is her existing airborne animation and supplies the face, outfit, head diameter, body "
    "proportions and rendering style. Preserve silver-white lavender hair, two curved lilac "
    "horns, pointed ears, lavender eyes, green clip and ivory flower, lavender scarf, sage "
    "cardigan, cream dress, white socks and sneakers, TWO small lavender bat wings and ONE "
    "scaled tail. These are her ordinary HUMAN hands, never claws or dragon paws. "
    "Exactly two arms visibly connected to the shoulders, two hands and two legs in every "
    "frame. She is FLYING throughout: both feet suspended, knees softly bent and shoes "
    "trailing, small wings visibly supporting her. Never standing, sitting, kneeling or "
    "touching a floor. Keep the same camera, head diameter, torso length, face position and "
    "whole-body scale in all cells. Small expressive changes in wing angle, knees and tail "
    "are welcome; no large jumps or arbitrary rotation. Use a consistent slight three-quarter "
    "view toward the viewer's RIGHT, with her recognizable face still visible. Complete "
    "body, horns, wings, tail, shoes, fingers and fire fit inside each cell with transparent "
    "margin. No floor, cast shadow, cursor, mouse animal, other character, detached hands, "
    "text, grid, border or backdrop. Background must be genuine alpha-zero transparency, "
    "not gray haze, white paper or a checkerboard drawing."
)

ACTIONS = {
    "air_play_fire": {
        "title": "飞着喷一小口火", "family": "air_fire", "duration": 2.5,
        "sequence": [0, 0, 1, 1, 1, 2, 2, 3, 3, 3],
        "prompt": (
            "Frame 1: hovering with wings opened, take a small anticipatory breath, cheeks "
            "slightly puffed; both hands rest loosely near her chest. Frame 2: turn only her "
            "face slightly farther RIGHT and breathe ONE short warm orange-gold flame from "
            "her MOUTH toward the right, about half a head-width long; eyes focused and body "
            "still airborne. The fire starts exactly at her lips, never in a hand or from her "
            "nose. Frame 3: fire completely stops, lips relax, wings make a small balancing "
            "stroke and both knees soften with a gentle recoil. Frame 4: satisfied little "
            "smile, shoulders settle and wings return to a relaxed hover, hands still near "
            "her chest. This is a controlled successful little breath, no explosion, scorch, "
            "object, smoke cloud or accident."
        ),
    },
    "air_play_reach": {
        "title": "飞着伸手抓光标", "family": "air_catch", "duration": 2.0,
        "sequence": [0, 0, 1, 1, 2, 2, 3, 3],
        "prompt": (
            "Frame 1: hover with both hands loosely near her chest, notice an INVISIBLE tiny "
            "target to her RIGHT at chest height; eyes track it with playful concentration. "
            "Frame 2: lean forward only a little and extend BOTH ordinary human hands to "
            "the RIGHT, arms side by side, palms facing each other with a small gap; wings "
            "spread to keep balance and both shoes remain trailing off the ground. Frame 3: "
            "bring those SAME two palms TOGETHER gently at the SAME point to try catching "
            "the invisible target; two arms still visibly connect each hand to her shoulders, "
            "no extra fist at her chest. Frame 4: open the SAME hands slightly, palms upward "
            "and clearly EMPTY, look down between them to inspect. Hand contact is forward "
            "and to the right of her torso, around chest height, never over her face. No "
            "drawn cursor, ball, insect, spark, trapped object, extra hand or dragon claw."
        ),
    },
    "air_play_miss": {
        "title": "空中抓空再找找", "family": "air_catch", "duration": 2.0,
        "sequence": [0, 0, 1, 1, 2, 2, 3, 3],
        "prompt": (
            "Continue an airborne hand-catching attempt with no props. Frame 1: hover with "
            "both arms extended forward to her RIGHT at chest height, palms upward and "
            "clearly EMPTY, look down between the two hands. Frame 2: keep those hands open "
            "and make a small puzzled blink with a slight head tilt, genuinely wondering "
            "where the target went; no smug grin or embarrassed pose. Frame 3: draw the "
            "hands partway back toward her chest and look a little farther RIGHT and up, "
            "searching again with curious eyes; wings adjust gently. Frame 4: relax both "
            "hands near her chest and return to an easy curious hover, two feet still "
            "suspended and tail soft. Keep the same camera, face size and body angle across "
            "the action. No cursor, animal, spark, object, extra arm or hand."
        ),
    },
}

for _spec in ACTIONS.values():
    _spec.update(
        prompt_header=_HEADER, style="motion_fly_flap_slow", posture="air",
        starts_from="air", role="air_play", fps=4, loop=False, scale=1.0,
        calibration_reference="motion_fly_flap_slow", calibration_frames=[0, 3],
    )

# The same relaxed airborne pose can acknowledge a real cursor catch without
# replaying the empty-palms reaction or inventing an object in her hands.
PLAYBACK_ALIASES = {
    "air_play_caught": {
        "strip": "air_play_fire", "title": "碰到光标满意悬停",
        "starts_from": "air", "posture": "air", "role": "air_play",
        "family": "air_catch", "fps": 2, "loop": False,
        "sequence": [3, 3], "duration": 1.0, "automatic": False,
    },
}
