"""Supported edge micro-performances and one grounded portal recovery."""

_COMMON = (
    "Four sequential full-body frames in a 2 by 2 grid, each cell 512 by 512 pixels, "
    "transparent background. Exact original ADULT chibi dragon woman: silver lavender "
    "hair, sage cardigan, cream dress, scarf, horns, two bat wings, one tail and shoes. "
    "Match Image 2's face diameter, proportions and rendering. Exactly two connected "
    "arms, two hands and two legs. Entire figure unobscured inside every cell with margin. "
    "Fixed camera and scale. No text, motion lines, furniture, drawn edge, ceiling, floor "
    "or extra appendage; runtime supplies the support and portal. "
)
_SIDE_HEADER = (
    "Image 1 gives character identity and colors ONLY; DO NOT use its standing pose. "
    "Image 2 is the REQUIRED pose and hand-placement template. Preserve its side-peek "
    "body angle, shoulder positions, elbows, BOTH hands and EVERY fingertip exactly "
    "in ALL FOUR frames. Both hands are already correctly gripping the same invisible "
    "vertical edge at x=210 in each 512px cell; copy those exact two hand positions. "
    "Only change the tail TIP, eye direction and existing ear/hair tips as requested. "
    "Do not redraw the hands, straighten the body, put a fist at the chest or add limbs. "
    "Output ONE 1024x1024 PNG with four full-body sequential cells in a 2x2 layout. "
    "True transparency: outside the character alpha=0, no checkerboard, grid, text, "
    "edge, pole, floor, shadow or background pixels. Complete figure in each cell. "
)
_SIDE = (
    "SIDE-PEEK facing viewer's RIGHT. Invisible vertical support x=210 in every cell. "
    "Hips and shoes mostly LEFT of it; shoulders lean right, face clearly RIGHT of it. "
    "Both connected hands grip that line at exactly the same positions in all frames. "
    "No sliding, hand at waist, new hand, pole or wall. "
)
_BOTTOM = (
    "Both forearms rest on an INVISIBLE horizontal ledge y=350 in every cell. Head above "
    "forearms, compact kneeling body and shoes below/behind support. Keep both elbows "
    "and forearms at exactly that height; no standing, climbing or whole-body movement. "
)
_TOP = (
    "Hang genuinely UPSIDE DOWN under an INVISIBLE horizontal edge y=100: shoe tips and "
    "curled tail hold that line, bent knees above torso, inverted face BELOW torso. "
    "Match inverted reference anatomy. Shoe/tail contacts stay fixed while supported. "
    "Hands naturally attached beside chest; no upright torso with inverted face pasted "
    "on, no exposed underwear. "
)

ACTIONS = {}

ACTIONS['edge_daily_side_tail_greet'] = {
    'title': '尾巴先打招呼', 'style': 'motion_edge_look', 'posture': 'air',
    'role': 'edge_micro', 'family': 'side', 'fps': 2.0, 'loop': False,
    'scale': 1.0, 'duration': 2.0, 'grip_x': 210, 'calibration_frames': [0, 3],
    'prompt_header': _SIDE_HEADER,
    'prompt': _COMMON + _SIDE + (
        'A shy greeting betrayed by her tail, with a quiet successful ending. Frame 1: '
        'calmly watch viewer, tail tucked behind skirt. Frame 2: only tail tip curls '
        'into view beside shoulder as a tiny hello. Frame 3: eyes notice her waving '
        'tail, a warm small smile, tail tip waves back once. Frame 4: meet viewer '
        'with same soft smile, tail settles to original curve. Hands and support never '
        'move; no shame, failure or whole-body sway. Tail stays clear of face.'
    ),
}
ACTIONS['edge_daily_side_listen'] = {
    'title': '侧耳听一会儿', 'style': 'motion_edge_look', 'posture': 'air',
    'role': 'edge_micro', 'family': 'side', 'fps': 2.0, 'loop': False,
    'scale': 1.0, 'duration': 2.0, 'grip_x': 256, 'calibration_frames': [0, 3],
    'prompt_header': _SIDE_HEADER,
    'prompt': _COMMON + _SIDE + (
        'Quietly notice something and understand it. Frame 1: relaxed outward gaze. '
        'Frame 2: existing ear and hair tuft perk slightly, eyes turn toward an unheard '
        'sound. Frame 3: hold attentive eyes, one existing ear relaxes, soft eyebrows. '
        'Frame 4: eyes return toward viewer, tiny reassured smile. The head, torso, '
        'arms and BOTH grips remain EXACTLY fixed in the reference pose. Horns stay rigid; never '
        'invent antennae, whiskers or ears.'
    ),
}
ACTIONS['edge_daily_bottom_watch'] = {
    'title': '趴着安心看你', 'style': 'bottom_sleep_ref', 'posture': 'lie',
    'role': 'edge_micro', 'family': 'bottom', 'fps': 1.5, 'loop': False,
    'scale': 1.0, 'duration': 8 / 3, 'rest_y': 366, 'calibration_frames': [0, 3],
    'prompt': _COMMON + _BOTTOM + (
        'A quiet moment of companionship, fully awake. Frame 1: chin just above crossed '
        'sleeves, gently watch viewer. Frame 2: chin nestles closer to sleeves, curious '
        'open eyes follow something low on screen. Frame 3: return eyes to viewer with '
        'an unforced small smile. Frame 4: same comfortable awake pose, relaxed lids '
        'after a slow blink. Only eyes, cheek and little tail tip change; fixed forearms. '
        'No drool or sleep.'
    ),
}
ACTIONS['edge_daily_bottom_yawn'] = {
    'title': '趴着打个小哈欠', 'style': 'bottom_sleep_ref', 'posture': 'lie',
    'role': 'edge_micro', 'family': 'bottom', 'fps': 2.0, 'loop': False,
    'scale': 1.0, 'duration': 2.0, 'rest_y': 362, 'calibration_frames': [0, 3],
    'prompt': _COMMON + _BOTTOM + (
        'One small yawn with a clear awake recovery. Frame 1: sleepy open eyes, chin '
        'above sleeves. Frame 2: eyes gently squeeze, small yawning mouth, shoulders '
        'lift slightly while elbows stay supported. Frame 3: mouth closes, tiny tear '
        'at eye corner, shoulders relax. Frame 4: eyes reopen with refreshed peaceful '
        'smile, chin returns to initial height. Both forearms remain supported; no '
        'floating hand, overhead stretching, standing or falling asleep.'
    ),
}
ACTIONS['edge_daily_top_cast'] = {
    'title': '倒挂看准再开阵', 'style': 'boundary_top_look', 'posture': 'air',
    'role': 'edge_cast', 'family': 'top', 'fps': 3.0, 'loop': False,
    'scale': 0.815, 'head_calibration': False, 'duration': 4 / 3, 'rest_y': 7, 'head_band': 'lower',
    'calibration_frames': [0, 3], 'cells': [0, 1, 3, 3],
    # Upside-down heads are calibrated against the flipped hanging reference as one strip.
    'prompt': _COMMON + _TOP + (
        'Decide on a safe magical route while firmly hanging. BOTH elbows stay FIXED '
        'beside the chest in ALL FOUR frames; exactly TWO arms and TWO hands TOTAL. '
        'Keep the two hands in the same small chest-level area. No reaching arm, no '
        'hand past the face, no extra chest fist. Frame 1: look downward with focused '
        'curiosity, both hands loosely closed at chest. Frame 2: those SAME two fists '
        'gently open into two palms WITHOUT moving either elbow. Frame 3: bring the '
        'two palms fingertips nearly together IN FRONT OF CHEST, a tiny deliberate '
        'spell gesture. Frame 4: the SAME two palms separate slightly AT CHEST with '
        'a quietly satisfied smile. Feet and tail stay hooked at exactly the same '
        'contact positions. The runtime draws the portal: no painted circle, sparkles '
        'or light. Remain inverted; NEVER fall, sit, stand or extend an arm downward.'

    ),
}
ACTIONS['edge_daily_top_release'] = {
    'title': '看准出口再松脚', 'style': 'boundary_top_look', 'posture': 'air',
    'role': 'edge_release', 'family': 'top', 'fps': 5.0, 'loop': False,
    'scale': 1.0, 'duration': 0.8, 'rest_y': 18,
    'head_bands': ['lower', 'lower', 'upper', 'upper'], 'calibration_frames': [0, 3],
    'prompt': _COMMON + _TOP + (
        'Careful release AFTER nearby portal opens. Continuous rotation in AIR, never '
        'fall to desktop. BOTH hands stay together CLOSE TO CHEST in ALL FOUR frames; '
        'exactly TWO arms and TWO hands TOTAL. Never leave old fists when moving an arm. '
        'Body rotation MUST be visibly 180 degrees, 90 degrees, 30 degrees, 0 degrees '
        'from upright in frames 1, 2, 3, 4 respectively. Frame 1: entire figure fully '
        'inverted at 180 degrees, shoe tips at y=100, hands at chest. Frame 2: unhook '
        'toes and tail, tuck knees, body clearly HORIZONTAL at 90 degrees, head on the '
        'LEFT and shoes on the RIGHT, both wings control rotation, hands at chest. '
        'Frame 3: diagonal at 30 degrees from upright, head above and left of shoes, '
        'wings brake, bent knees and suspended feet, hands at chest. Frame 4: upright '
        'at 0 degrees but clearly AIRBORNE, knees tucked, toes down, hands still close '
        'to chest, wings hold steady. No tiny lean followed by sudden flip upright. '
        'No planted foot, sitting, impact or embarrassment. Fixed face diameter.'
    ),
}
ACTIONS['edge_daily_warp_stumble'] = {
    'title': '出口踉跄后站稳', 'style': 'spell_rise', 'posture': 'stand',
    'role': 'warp_recovery', 'family': 'landing', 'fps': 4.0, 'loop': False,
    'scale': 1.0, 'duration': 1.0, 'calibration_frames': [0, 3],
    'prompt': _COMMON + (
        'A harmless occasional stumble AFTER emerging and touching ground. Invisible '
        'ground line y=450. Frame 1: both feet just landed, soft knees, arms balance. '
        'Frame 2: toe catches her own heel, knee dips, torso tips slightly, wings open '
        'to catch balance, surprised small mouth, no pain. Frame 3: one connected palm '
        'briefly braces near floor, one knee bent, tail counterbalances; no extra hand, '
        'lying flat or injured pose. Frame 4: clearly back on BOTH planted feet, upright, '
        'folded wings, relaxed arms and relieved exhale. Complete recovery, never looping '
        'fall or fake proud pose. No portal, airborne fall, giant bounce or travel.'
    ),
}
ACTIONS['edge_daily_top_wonder'] = {
    'title': '抓稳了再看新发现', 'style': 'boundary_top_look', 'posture': 'air',
    'role': 'edge_micro', 'family': 'top', 'fps': 2.0, 'loop': False,
    'sequence': [0, 1, 2, 3, 3, 3], 'scale': 0.819, 'head_calibration': False, 'duration': 3.0,
    'rest_y': 9, 'head_band': 'lower', 'calibration_frames': [0, 3],
    # Upside-down heads are calibrated against the flipped hanging reference as one strip.
    'prompt': _COMMON + _TOP + (
        'A quiet successful discovery while securely hanging. ONE tiny white fluffy '
        'feather drifts past her face. BOTH hands remain FIXED at chest in ALL FOUR '
        'frames, elbows fixed, exactly TWO arms and TWO hands TOTAL. She never reaches '
        'for or catches the feather. Frame 1: eyes notice the feather beside her face. '
        'Frame 2: only the eyes follow that SAME feather drifting sideways. Frame 3: '
        'eyes softly widen in wonder as it floats a little farther away, hands still '
        'at chest. Frame 4: quietly gaze after the SAME feather, which remains visible '
        'inside the cell, a tiny peaceful smile. Face, head, body, hands, shoe tips '
        'and tail contacts stay in the same positions throughout. Fully upside down '
        'and full body unobscured. No failure, fall, fumble, embarrassment, catching '
        'gesture, extra hand, sparkles, drawn edge or supporting object.'

    ),
}
