"""A dragon's abilities solve small everyday wishes; each sheet keeps its consequence."""

_HEADER = (
    "Use case: illustration-story. Transparent 1024x1024 sprite sheet, exactly FOUR "
    "complete character frames in a 2x2 grid, each cell 512x512, reading left to right. "
    "Image 1 is the original adult dragon-woman; Image 2 is the exact shipped idle "
    "body reference. COPY its head diameter, neck-to-dress-hem length, hip position, "
    "thigh and shin lengths, hands and shoes. Keep that SAME anatomy in all cells, "
    "not a larger head on a shorter baby body. Silver-lavender hair, lilac horns, "
    "green flower clip, sage cardigan, cream dress, lavender scarf, white socks and "
    "sneakers, two bat wings and one scaled tail. Exactly TWO arms, TWO connected "
    "hands and TWO legs. Fixed camera and character scale, same shoe support line. "
    "The body may turn, lean and react naturally. This is a small daily need being "
    "solved with her own dragon ability, not a generic wand pose. Mouth breath must "
    "visibly originate at her lips and touch its intended object. Effects are tiny "
    "and local; never cover her face. Maintain the EXACT same prop in consecutive "
    "frames. Frame 4 keeps the achieved consequence and ends with NO loose prop "
    "in front of her: her hand physically carries a used object behind her hip/tail "
    "to put it away, never erase an object in mid-air. No invented bag, case, table, "
    "floor, shadow, background, letters, numbers or panel borders. Entire character "
    "and visible props fit inside each cell with transparent margins. Real alpha "
    "transparency. All four poses remain standing."
)

_SCENES = {
    "cola": ("想喝冰可乐", (
        "Frame 1: hold a small clear glass of brown fizzy cola at chest height in "
        "one hand; look at the room-temperature drink and fan a warm cheek with "
        "the free hand, wanting it cold. Frame 2: lean toward that SAME glass and "
        "blow a short pale-blue frosty breath directly into it, a few little ice "
        "crystals forming on its rim, expression concentrated. Frame 3: take a "
        "satisfied sip from that SAME now-condensed cold glass, relaxed shoulders "
        "and happy tail tip. Frame 4: finished drink, carry the empty glass behind "
        "her hip with the same hand until it is completely occluded; the free "
        "hand touches her pleasantly cooled cheek, a quiet refreshed smile."
    )),
    "roast": ("自己吐火烤棉花糖", (
        "Frame 1: hold ONE pale marshmallow on a short wooden skewer and study it "
        "hungrily, tail curling in anticipation. Frame 2: purse her lips and send "
        "a tiny controlled orange flame FROM HER MOUTH onto that SAME marshmallow; "
        "both hands are ordinary hands, no palm flame or stove. Frame 3: the flame "
        "is gone, the marshmallow is toasted golden, she bites it off the SAME "
        "skewer with a pleased expression. Frame 4: cheeks gently chewing, move "
        "the now-empty wooden skewer behind her hip in her holding hand until "
        "hidden, other hand over her content tummy, a small proud tail curl."
    )),
    "scarf": ("暖息吹干围巾", (
        "Frame 1: hold the damp end of her OWN lavender scarf away from her chest, "
        "one tiny droplet and sagging fabric, concerned eyes. Frame 2: gently blow "
        "a small warm breath from her mouth across that same scarf end, no flame, "
        "two faint steam wisps leaving the cloth. Frame 3: the same dry scarf "
        "becomes fluffy again; softly rub it against her cheek with real relief. "
        "Frame 4: let the scarf settle back in its normal worn place, both hands "
        "free and shoulders relaxed, tail wrapping loosely by her feet. No extra "
        "scarf or new garment, and no loose prop to disappear."
    )),
    "brush": ("悬浮小刷子梳尾巴", (
        "Keep the FULL idle neck-flower-to-dress-hem and shoulder-to-hip lengths "
        "throughout; leaning or looking down must not compress the torso. "
        "Especially frames 3 and 4 keep the idle-length waist above the normal "
        "legs. Do not enlarge the head or shoes to compensate. "
        "Keep a straight relaxed spine and full standing height; look toward "
        "the tail with a small horizontal head turn, not lowered chin or hunched "
        "shoulders. Both soles stay planted, hips at their normal idle height. "
        "Frame 1: hold a tiny wooden grooming brush and turn to try reaching a "
        "ruffled patch at the far bend of her OWN tail, a mildly puzzled face. "
        "Frame 2: release that SAME brush into a small hovering arc to the hard "
        "to reach patch, one hand guides it with a restrained glow; the brush "
        "bristles physically touch the tail scales. Frame 3: the brushed tail "
        "looks smooth, she looks relieved while that SAME brush returns into her "
        "open palm. Frame 4: carry the brush behind her hip until fully hidden, "
        "the other hand strokes her now-comfortable tail, eyes softly pleased."
    )),
    "mend": ("把书角修好再收起", (
        "Frame 1: hold a small tan closed book, looking sad at a clearly bent "
        "and partly torn TOP-LEFT cover corner: a triangular flap covering one "
        "quarter to one third of the cover, bright cream underside and a visible "
        "jagged split, still attached. Frame 2: "
        "support that SAME book with one hand and trace the damage with one "
        "fingertip, a clear small gold seam joins and straightens that exact "
        "triangular corner, no large glow. Frame 3: turn the SAME now-flat repaired "
        "corner toward herself and test it gently, "
        "small absorbed smile, no new book or grand victory gesture. Frame 4: "
        "carry that SAME closed book behind her hip with her supporting hand "
        "until fully occluded; other hand rests against her chest, reassured."
    )),
}

ACTIONS = {
    "magic_daily_" + name: {
        "title": title, "prompt_header": _HEADER, "prompt": prompt, "style": "idle",
        "starts_from": "stand", "posture": "stand", "role": "magic_daily", "family": name,
        "automatic": False, "loop": False, "fps": 4,
        "sequence": [0, 0, 0, 1, 1, 1, 2, 2, 2, 2, 3, 3, 3, 3], "duration": 3.5,
        "scale": 1.0, "fixed_ground": True, "calibration_reference": "idle",
    }
    for name, (title, prompt) in _SCENES.items()
}
