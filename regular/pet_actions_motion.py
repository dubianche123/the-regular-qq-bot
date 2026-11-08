"""Four-frame motion sheet catalog; movement through the screen belongs to physics."""

_COMMON = (
    "Four sequential frames of one action, same camera and stable character scale. "
    "Keep the original character's proportions and outfit identical, exactly one character, "
    "two arms, two hands and two legs; no extra limbs. Entire body, horns, wings, tail and "
    "shoes fit within each 512px cell with transparent margin. No large random jitter. "
)

# name, title, action, style, posture, role, family, fps, loop, duration
_ROWS = [
    ("walk_brisk", "轻快走", "A brisk relaxed walk: right foot forward, passing step, left foot forward, passing step; arms swing opposite the legs.", "walk", "stand", "walk", "gait", 6, True, 7),
    ("walk_slow", "慢慢走", "A slow measured walk: right heel leads, weight transfers, left heel leads, weight transfers; small arm swings.", "walk", "stand", "walk", "gait", 3, True, 8),
    ("walk_tiptoe", "踮脚走", "A quiet tiptoe walk: alternate raised heels and short steps, elbows bent for balance; toes stay close to the ground.", "walk", "stand", "walk", "gait", 4, True, 6),
    ("walk_backward", "倒着走", "Walk carefully backward while facing right: alternate short backward steps, glance over shoulder, arms held slightly out.", "walk", "stand", "walk", "gait", 4, True, 6),
    ("walk_phone", "边走边看手机", "Walk in short alternating steps while holding one small phone with both hands at chest height; glance down, then ahead.", "walk", "stand", "walk", "gait", 4, True, 7),
    ("walk_sleepy", "困倦小步", "Sleepy short shuffling steps: alternate feet gently with droopy eyelids, one hand briefly covers a yawn.", "walk", "stand", "walk", "gait", 3, True, 6),
    ("walk_trip", "踉跄后站稳", "Nearly trip during a walk: toe catches, arms rise for balance, other foot steps forward, recover upright. No fall.", "walk", "stand", "walk", "recovery", 5, False, 1.2),
    ("walk_message_stop", "收到消息停步", "Walking, notice a message: one step, lift a small phone, plant both feet, look at its screen.", "walk", "stand", "walk", "transition", 4, False, 1.5),
    ("walk_hands_behind", "背手散步", "Stroll with both hands held behind the back: right step, passing, left step, passing, tiny tail sway.", "walk", "stand", "walk", "gait", 4, True, 7),
    ("walk_wing_balance", "张翼散步", "Walk with small wings half open for balance: alternating steps and a gentle wing-tip flex, arms relaxed.", "walk", "stand", "walk", "gait", 4, True, 7),
    ("walk_look_up", "抬头散步", "Take alternating steps while looking upward briefly, then return gaze ahead; arms swing naturally.", "walk", "stand", "walk", "gait", 4, True, 6),
    ("walk_tail_sway", "摆尾漫步", "Slow alternating walk with a distinct gentle left-right tail sway; arms remain loosely at her sides.", "walk", "stand", "walk", "gait", 4, True, 7),
    ("walk_march", "小小踏步", "A soft playful march: alternate slightly lifted knees and opposite arms, shoes landing softly without stamping.", "walk", "stand", "walk", "gait", 5, True, 6),
    ("walk_scuff", "轻拖脚步", "A relaxed shuffling walk: right shoe slides a short way forward, transfer weight, left shoe slides, transfer weight.", "walk", "stand", "walk", "gait", 3, True, 6),
    ("walk_look_side", "边走边张望", "Walking with alternating small steps, turn gaze gently toward the viewer and then forward; torso keeps its direction.", "walk", "stand", "walk", "gait", 4, True, 6),
    ("walk_pause", "走走停停", "Take two short alternating steps, bring feet together, pause with hands settling at sides.", "walk", "stand", "walk", "transition", 4, False, 1.5),
    ("fly_takeoff", "展翼起飞", "Takeoff preparation in a fixed cell: knees soften, wings unfold, toes lift, legs hang gently; no drawn displacement.", "idle", "air", "fly", "takeoff", 4, False, 1.2),
    ("fly_takeoff_tip", "踮脚起飞", "Rise onto toes, spread small wings, lift both feet and bend knees slightly, settle into an airborne pose.", "idle", "air", "fly", "takeoff", 4, False, 1.2),
    ("fly_glide", "舒展滑翔", "Glide in place: wings held open, forearms relaxed, legs trailing; wing tips and tail flex subtly across four frames.", "dangle", "air", "fly", "glide", 3, True, 6),
    ("fly_glide_tucked", "收腿滑翔", "Glide in place with knees softly tucked, wings half spread; alternate a small wing-tip dip and tail correction.", "dangle", "air", "fly", "glide", 3, True, 6),
    ("fly_glide_arms", "伸手滑翔", "Glide in place with both arms gently extended to the sides, wings open and feet trailing, then relax the elbows slightly.", "dangle", "air", "fly", "glide", 3, True, 6),
    ("fly_flap_slow", "慢慢拍翼", "Hover in place while both small wings sweep up, halfway down, down, halfway up; legs dangle softly.", "dangle", "air", "fly", "flap", 4, True, 6),
    ("fly_flap_quick", "轻快拍翼", "Hover with small quicker wing beats: upstroke, downstroke, upstroke, downstroke; hands near chest and feet suspended.", "dangle", "air", "fly", "flap", 6, True, 5),
    ("fly_flap_asymmetric", "侧翼调平", "Hover and correct balance with a shallow alternating left and right wing stroke, tail tip counterbalances; body stays centered.", "dangle", "air", "fly", "flap", 4, True, 5),
    ("fly_hover", "安静悬停", "Hover in place, wings gently opening and closing, both legs dangling with alternating small ankle movements.", "dangle", "air", "fly", "hover", 3, True, 7),
    ("fly_hover_curious", "悬停张望", "Hover in place, hold wings out, glance left then right and make a tiny alternating foot swing; torso stays centered.", "dangle", "air", "fly", "hover", 3, True, 6),
    ("fly_hover_tail", "摆尾悬停", "Hover in place with quiet wing beats and a gentle left-right tail-tip sweep; arms rest near waist.", "dangle", "air", "fly", "hover", 3, True, 6),
    ("fly_hover_hands", "抱手悬停", "Hover with both hands loosely clasped near chest, wings pulse softly, one dangling knee bends and relaxes.", "dangle", "air", "fly", "hover", 3, True, 6),
    ("fly_land", "飞行降落", "Prepare to land in place: wings open, legs reach down, knees bend on an imaginary floor, wings fold. No drawn displacement.", "fall", "air", "fly", "landing", 4, False, 1.2),
    ("fly_land_soft", "轻轻收翼降落", "Gentle airborne landing preparation: lower feet, bring knees together, soften knees, fold wings halfway.", "fall", "air", "fly", "landing", 4, False, 1.2),
    ("fly_turn", "空中转身", "Turn gently in the air from three-quarter right through front toward three-quarter left, wings adjusting for balance; centered in every frame.", "dangle", "air", "fly", "turn", 4, False, 1.2),
    ("fly_turn_look", "回头转向", "Hover, glance over shoulder, rotate shoulders a little, shift wing angles, settle facing the other three-quarter direction.", "dangle", "air", "fly", "turn", 4, False, 1.2),
    ("fly_circle", "小飞圈", "A small airborne banking cycle in place: lean slightly right, level, lean slightly left, level; wings and legs adjust. No trajectory drawn.", "dangle", "air", "fly", "circle", 4, True, 4),
    ("fly_circle_tucked", "蜷腿小飞圈", "Airborne small banking cycle in place with knees tucked: subtle left bank, level, right bank, level; no trajectory drawn.", "dangle", "air", "fly", "circle", 4, True, 4),
    ("drag_dangle", "被拎起", "Suspended by both raised wrists from an invisible grip, legs hang straight then sway gently; no visible holder.", "dangle", "air", "drag", "held", 3, True, 6),
    ("drag_swing", "被轻轻摇晃", "Suspended by both raised wrists, legs trail left, settle, trail right, settle; upper grip remains fixed and no holder appears.", "dangle", "air", "drag", "held", 4, True, 5),
    ("drag_turn", "被拎着转向", "Suspended by both wrists, gently turn shoulders from three-quarter right through front to three-quarter left, legs dangling.", "dangle", "air", "drag", "held", 3, False, 1.5),
    ("drag_wriggle", "被拎着轻挣", "Suspended by both wrists, gently bend one knee then the other while shoulders and raised hands stay fixed; no distress.", "dangle", "air", "drag", "held", 4, True, 4),
    ("drag_hug", "被抱住平稳", "Held securely by an invisible supportive embrace: arms relaxed near chest, feet off ground, small leg swing and calm breathing; no visible holder.", "dangle", "air", "drag", "held", 3, True, 6),
    ("drag_curl", "被拎着蜷腿", "Suspended by raised wrists, both knees curl up a little, one foot relaxes, both legs lower gently, return.", "dangle", "air", "drag", "held", 3, True, 5),
    ("fall_upward", "上抛升空", "Airborne upward toss: arms lift for balance, knees trail, wings open a little, body steadies; keep same centered position.", "fall", "air", "fall", "airborne", 4, True, 3),
    ("fall_flutter", "落下时扑翼", "Falling in place with small wings fluttering, hands spread for balance, feet dangling; gentle alternating wing positions.", "fall", "air", "fall", "airborne", 5, True, 4),
    ("fall_curl", "蜷身下落", "Falling in place with knees softly curled, arms close to torso, tail and wings adjust slightly; no spinning.", "fall", "air", "fall", "airborne", 4, True, 4),
    ("fall_reach", "伸手平衡下落", "Falling in place, extend arms sideways then bend elbows, alternate foot position and return to balanced pose.", "fall", "air", "fall", "airborne", 4, True, 4),
    ("ceiling_bonk", "轻碰天花板", "An upward bump against an invisible ceiling: arms rise, shoulders compress slightly, eyes blink, head settles; no line or ceiling drawn.", "dangle", "air", "ceiling", "ceiling", 4, False, 1.3),
    ("ceiling_hold", "扶顶悬吊", "Both hands press against an invisible ceiling above her head, arms visibly connected, feet dangling; alternate small knee bends. No ceiling drawn.", "dangle", "air", "ceiling", "ceiling", 3, True, 5),
    ("ceiling_rub", "揉揉额头", "Suspended below an invisible ceiling: one hand supports overhead while the other gently rubs her forehead, then returns; legs dangle.", "dangle", "air", "ceiling", "ceiling", 3, False, 1.8),
    ("ceiling_release", "松开顶边", "Both hands support an invisible ceiling, one hand releases, the other releases, arms open for balance; feet remain suspended.", "dangle", "air", "ceiling", "ceiling", 4, False, 1.2),
    ("floor_squat", "地面缓冲", "On an invisible floor, bend both knees into a shallow squat, extend arms for balance, straighten halfway, settle upright.", "fall", "stand", "floor", "ground", 4, False, 1.3),
    ("floor_kneel", "单膝缓冲", "On an invisible floor, gently lower one knee, brace with one hand near the floor, rise to a crouch, stand.", "fall", "stand", "floor", "ground", 4, False, 1.5),
    ("floor_balance", "地面找平衡", "Standing on an invisible floor, shift weight onto one foot, extend both arms, replace the other foot, settle.", "idle", "stand", "floor", "ground", 4, False, 1.5),
    ("floor_wings", "收翼站稳", "Standing with bent knees and wings open, slowly straighten knees, fold both wings, lower hands, settle.", "idle", "stand", "floor", "ground", 3, False, 1.7),
    ("bottom_peek", "底边探头", "Full body hangs BELOW an imaginary horizontal bottom screen edge: exactly two hands grip the edge in front at shoulder width, head just above hands and body below; no edge drawn. Blink and lightly shift fingers.", "dangle", "air", "bottom", "bottom_peek", 3, True, 6),
    ("bottom_look", "底边左右张望", "Full body hangs below an imaginary horizontal edge, exactly two hands grip it in front at shoulder width; glance left, center, right, center. Draw entire body; no edge.", "dangle", "air", "bottom", "bottom_peek", 3, True, 6),
    ("bottom_grip", "底边换手指", "Full body below an imaginary horizontal edge, exactly two hands gripping it in front, both arms connected; alternate curling fingertips while feet dangle. No edge drawn.", "dangle", "air", "bottom", "bottom_peek", 3, True, 6),
    ("bottom_kick", "底边摆脚", "Full body below an imaginary horizontal edge, both hands gripping it in front, head above the hands; alternate small dangling foot kicks. No edge drawn.", "dangle", "air", "bottom", "bottom_peek", 3, True, 6),
    ("edge_peek", "侧边探头", "Full body leans right from behind an imaginary vertical left edge: hips and shoes left, head right; exactly two connected arms and two hands grip that same edge at different heights. No edge or third fist. Blink gently.", "idle", "stand", "edge", "side_peek", 3, True, 6),
    ("edge_look", "侧边张望", "Full body leans right from behind an imaginary vertical left edge, hips left and head right; exactly two connected arms and hands grip the edge, one above the other. Shift gaze; no third hand, no edge drawn.", "idle", "stand", "edge", "side_peek", 3, True, 6),
    ("edge_fingers", "侧边握紧", "Full body leaning right around an invisible vertical edge, both shoes and hips left, head right. Exactly two arms and hands grip the same edge, upper fingers curl then lower fingers curl; no third fist or hand on dress.", "idle", "stand", "edge", "side_peek", 3, True, 6),
    ("edge_tilt", "侧边歪头", "Full body leaning right around an imaginary vertical edge, hips and shoes left, head right; exactly two hands connected to two arms grip edge, one above the other. Head tilts slightly then returns; no actual edge.", "idle", "stand", "edge", "side_peek", 3, True, 6),
    ("land_soft", "轻轻落地", "Feet touch an invisible floor, knees bend gently, arms balance, straighten to standing; one consistent view.", "fall", "stand", "land", "landing", 5, False, 1.2),
    ("land_slide", "滑一下站稳", "Shoes touch the invisible floor, one foot slides a short distance, arms counterbalance, feet plant securely.", "fall", "stand", "land", "landing", 5, False, 1.3),
    ("land_one_foot", "单脚落地", "One foot touches the invisible floor, the other follows, knees bend, both feet settle firmly and arms relax.", "fall", "stand", "land", "landing", 5, False, 1.3),
    ("land_wing", "张翼缓冲落地", "Land with wings open and knees bent, lower heels, gradually fold wings, stand steady on both feet.", "fall", "stand", "land", "landing", 4, False, 1.5),
]

ACTIONS = {
    "motion_" + name: {"title": title, "prompt": _COMMON + prompt, "style": style,
                       "posture": posture, "role": role, "family": family,
                       "fps": fps, "loop": loop, "scale": 0.8 if posture == "sit" else 1.0,
                       "duration": duration}
    for name, title, prompt, style, posture, role, family, fps, loop, duration in _ROWS
}

ACTIONS["motion_land_soft"]["prompt"] += (
    " All four figures must have generous margin, especially above horns and below shoes. "
    "The background is fully transparent, alpha zero outside the figure, no haze or shadow."
)
ACTIONS["motion_bottom_grip"]["style"] = "idle"
ACTIONS["motion_bottom_grip"]["prompt"] += (
    " Image 2 is only a style reference. BOTH elbows bend downward, hands are in front "
    "of the chest with fingertips hooked over an imaginary horizontal ledge. "
    "Head above the hands, torso and both feet below the hands. Never raise hands overhead; "
    "no visible ledge, wall, handle, extra arms or extra hands."
)
for _name in ("motion_fly_flap_quick", "motion_fly_flap_asymmetric"):
    ACTIONS[_name]["style"] = "idle"
    ACTIONS[_name]["prompt"] += (
        " Image 2 is only for style, not pose. The BAT WINGS THEMSELVES must change shape "
        "visibly: frame 1 both wings lift upward, frame 2 both extend horizontally, "
        "frame 3 both sweep downward, frame 4 return toward horizontal. Their tips move "
        "by a clearly visible distance while head, torso and feet remain centered. "
        "Do not substitute body tilting for wing movement."
    )

for _name in ("motion_edge_peek", "motion_edge_look", "motion_edge_fingers", "motion_edge_tilt"):
    ACTIONS[_name]["prompt"] += (
        " Image 2 is only a rendering reference, not a pose to copy. "
        "Lean the torso to the viewer's right with waist and both shoes to the left of an "
        "imaginary vertical edge, and head clearly to its right. The TWO hands must each "
        "visibly connect to their own shoulder and grip that same imaginary edge, one above "
        "the other. Exactly two hands total: no hand at the skirt, no third fist, no pole, "
        "wall, or visible line. Entire figure remains within the cell."
        " In each 512px cell place the feet and hips around x=145, the shoulders around x=210, "
        "the gripping hands around x=225 and the center of the face around x=300. "
        "The eyes and face must be visibly to the RIGHT of BOTH hands, not behind them. "
        "These are composition instructions only; draw no numbers, edge, wall or pole."
    )

# The solid rod makes the hand contact explicit; the renderer crops it at the screen edge.
ACTIONS["motion_edge_peek"]["grip_x"] = 223
ACTIONS["motion_edge_peek"]["prompt"] = (
    "Four sequential frames of the same full-body chibi character peeking from behind "
    "a thin SOLID BRIGHT MAGENTA VERTICAL ROD. The rod right edge is at x=220 in each "
    "512px cell and extends vertically beyond both hands. Her feet and hips are to the "
    "LEFT of the rod; she leans out, with the CENTER OF HER FACE clearly to the RIGHT "
    "of the rod. EXACTLY TWO arms, each visibly connected to a shoulder, and EXACTLY TWO "
    "hands: one upper and one lower hand naturally curl fingers around the real rod. "
    "Hands grip only the rod, never clothing. No third hand or fist. Entire body, horns, "
    "wings, tail and shoes fit within each cell. Four frames differ only in a gentle blink "
    "and small finger shifts. Same character, clothes, camera and scale in all frames. "
    "Transparent background outside the character and rod. Image 2 supplies style only; "
    "replace its pose entirely."
)
for _name, _motion in {
    "motion_edge_look": "The eyes glance left, center, right, then center.",
    "motion_edge_fingers": "The upper fingers gently loosen then curl, then the lower fingers shift.",
    "motion_edge_tilt": "The head tilts a little and returns, without moving the feet or body.",
}.items():
    ACTIONS[_name]["grip_x"] = 223
    ACTIONS[_name]["prompt"] = ACTIONS["motion_edge_peek"]["prompt"] + " " + _motion
ACTIONS["motion_edge_tilt"]["grip_x"] = 217
ACTIONS["motion_edge_look"]["grip_x"] = 202
ACTIONS["motion_edge_fingers"]["grip_x"] = 210

ACTIONS["motion_fly_flap_slow"]["prompt"] += (
    " The wings must visibly perform one complete wing stroke: frame 1 BOTH wing tips point UP "
    "beside the head; frame 2 both extend HORIZONTALLY; frame 3 both sweep DOWN beside the hips; "
    "frame 4 both return horizontally. Use the supplied flapping sheet's wing phases. "
    "Keep the head, face size, torso, hands and suspended feet identical across all four frames. "
    "The pose must be airborne, not standing. No substitute body bobbing for wing articulation."
)

# Match the shipped idle anatomy, not the old short-bodied fall drawings.
_FALL_HEADER = (
    "Transparent 1024x1024 animation sheet, exactly four full-body frames in a 2x2 grid "
    "of 512px cells. Image 1 is the original adult chibi dragon-woman identity. Image 2 "
    "is her EXACT shipped idle figure and is authoritative for anatomy, clothes, camera "
    "and style. Image 3 shows normal airborne wings and legs. Preserve the silver-lilac "
    "hair, horns, pointed ears, flower clip, scarf, sage cardigan, cream dress, socks, "
    "sneakers, two bat wings and one tail. Exactly two arms, two human hands, two legs. "
    "CRITICAL: copy Image 2's head diameter AND neck-flower-to-center-dress-hem length, "
    "shoulder-to-hip length, thighs, shins and shoes. Bending knees must NOT shorten the "
    "torso or make a tiny baby body under the same head. The cardigan and dress remain "
    "their full original length. Same camera, head, body scale and torso position in all "
    "cells. Both feet stay airborne. The silhouette can naturally shorten with curled "
    "knees: do not resize the person to equalize silhouette heights. Full horns, fingers, "
    "wings, tail and shoes inside each cell with margin. No standing/landing pose, floor, "
    "shadow, props, words, symbols, motion trails or backdrop. Genuine alpha-zero background."
)
for _name, _direction in {
    "motion_fall_upward": (
        "Four stages of an airborne upward-toss adjustment: 1 arms lift beside her head, "
        "full-length torso extended, knees trail softly, eyes open; 2 elbows soften, knees "
        "bend slightly behind her and wings open; 3 arms spread halfway, wings check the "
        "motion and one lower leg relaxes; 4 return toward the first balanced airborne "
        "pose with eyes open and both feet suspended. Repeatable, never land or stand."
    ),
    "motion_fall_flutter": (
        "Four stages of an airborne flutter: 1 both small wing tips UP, arms loosely "
        "spread at shoulder height, normal-length legs dangle and eyes open; 2 wings "
        "HORIZONTAL, fingers relax and one ankle swings; 3 wing tips DOWN beside hips, "
        "elbows soften and other ankle relaxes; 4 wings return halfway up, arms and legs "
        "return toward frame 1, eyes open. Full torso stays fixed while wings articulate."
    ),
    "motion_fall_curl": (
        "Four stages of a gentle airborne knee curl, never a ground crouch: 1 full-length "
        "torso as Image 2, both arms relaxed close to her sides with exactly two hands "
        "beside the hips and NO hands near the chest, knees softly forward, eyes open; 2 knees curl "
        "a little closer without compressing the torso, wings open; 3 one lower leg "
        "unbends gently while the other stays curled, tail adjusts; 4 restore frame 1 "
        "with eyes open. Show real hip-knee-ankle joints, unchanged cardigan/dress length."
    ),
    "motion_fall_reach": (
        "Four stages of an airborne balancing reach: 1 arms extend sideways below "
        "shoulders, palms open, wings open, normal-length legs hang with soft knees, "
        "eyes open; 2 elbows bend toward chest and one knee bends a little; 3 arms "
        "extend again, opposite knee relaxes, tail counterbalances; 4 settle toward "
        "frame 1 with open eyes and both feet suspended. Full-length torso stays stable."
    ),
}.items():
    ACTIONS[_name].update(
        prompt_header=_FALL_HEADER, prompt=_direction, style="idle",
        extra_refs=["motion_fly_flap_slow"], fixed_ground=True,
        calibration_reference="idle", calibration_frames=[0, 3],
    )

# These landing endpoints had the same short-body problem even when standing.
_LAND_HEADER = (
    "Transparent 1024x1024 four-frame animation sheet in a 2x2 grid of 512px cells. "
    "Image 1 is the original adult chibi dragon-woman identity. Image 2 is her exact "
    "shipped IDLE anatomy, outfit and rendering. Image 3 shows airborne wing anatomy. "
    "Copy the idle head diameter AND full torso, neck-flower-to-center-dress-hem length, "
    "hips, thighs, shins and shoes. Preserve silver-lilac hair, two horns, pointed ears, "
    "flower clip, scarf, sage cardigan, cream dress, socks, sneakers, two small bat "
    "wings and one scaled tail. Exactly two connected arms, two hands and two legs. "
    "Fixed camera and scale, one common foot-support line. Bending knees is allowed "
    "but must NOT compress the cardigan/dress or turn the body into a tiny baby under "
    "the same head. Final standing frame MUST have the same head, torso and legs as "
    "Image 2, not just the same overall bounding height. Complete figure inside each "
    "cell with transparent margin. No drawn floor, cast shadow, background, props, "
    "words, symbols, speed lines or extra limbs. Genuine alpha-zero background."
)
for _name, _direction in {
    "motion_fly_land": (
        "Four gentle landing stages: 1 airborne with wings open and normal-length "
        "legs reaching down, feet just above the common support line; 2 toes touch "
        "that line, knees soften slightly and arms balance; 3 both soles settle, "
        "knees bend a little more and wings ease halfway closed; 4 fully straighten "
        "to the EXACT idle torso/leg proportions, shoulders relaxed and wings folded. "
        "Keep the same head size throughout. Small calm face, no collapse or squash."
    ),
    "motion_land_one_foot": (
        "Four one-foot landing stages: 1 one normal-length leg reaches toward the "
        "common support line while the other knee is softly lifted, arms balance; "
        "2 the first foot supports her as the second lowers; 3 both feet touch and "
        "knees gently absorb the landing, full torso length preserved; 4 stand "
        "securely with the SAME full torso and leg lengths as Image 2, arms relax "
        "beside her, wings settle. One consistent camera, no shrinking or pose jump."
    ),
    "motion_land_wing": (
        "Four supported landing recovery stages: 1 both feet on the common support "
        "line, knees gently bent and wings wide for balance; 2 straighten the knees "
        "halfway while wings fold halfway; 3 stand almost upright, lower both hands "
        "and ease shoulders; 4 fully upright in the SAME anatomy and proportions as "
        "the idle reference, both feet planted and small wings folded. Knees may bend "
        "but the dress and cardigan retain their original length in every frame."
    ),
}.items():
    ACTIONS[_name].update(
        prompt_header=_LAND_HEADER, prompt=_direction, style="idle",
        extra_refs=["motion_fly_flap_slow"], fixed_ground=True,
        calibration_reference="idle", calibration_frames=[0, 3],
    )

ACTIONS["motion_fly_land"]["prompt"] += (
    " All four cells MUST use the SAME head width and neck-to-hem length, matching "
    "the final standing figure. Keep the horns and ahoge at least 40px from the "
    "top of every cell. Frame 1 floats only a SMALL distance above the common "
    "foot line: do not move or enlarge the figure toward the top border. Frame 2 "
    "is not a smaller copy of frame 1. Knee bending changes joint angles only, "
    "never the scale of the head or torso. Fixed camera; four equal-size characters."
)

# This corrected sheet has an airborne reach followed by three grounded beats.
# Reuse its exact pixels and geometry on the corresponding side of touchdown.
PLAYBACK_ALIASES = {
    "motion_fly_approach": {
        "strip": "motion_fly_land", "title": "接地前伸腿收势",
        "starts_from": "air", "posture": "air", "role": "fly", "family": "approach",
        "fps": 4, "loop": False, "sequence": [0], "duration": 0.25, "automatic": False,
    },
    "motion_fly_settle": {
        "strip": "motion_fly_land", "title": "接地缓冲再收翼",
        "starts_from": "air", "posture": "stand", "role": "fly", "family": "landing",
        "fps": 4, "loop": False, "sequence": [1, 2, 3], "duration": 0.75, "automatic": False,
    },
}
