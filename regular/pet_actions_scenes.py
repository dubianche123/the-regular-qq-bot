"""Personality-led, full-body narrative sheets for the desktop pet."""

_HEAD = (
    "Four sequential full-body frames in a 2 by 2 grid, each 512 by 512 pixels, transparent background, "
    "generous margins. One original adult chibi dragon woman from the references: silver-white lavender "
    "hair, small horns and wings, one lavender tail, sage cardigan, cream dress, lavender scarf and white "
    "shoes unchanged. Exactly two connected arms, two hands and two legs in every frame. Fixed camera, "
    "consistent face diameter and body proportions, complete character and named props fully within each "
    "cell. Expressive full-body leaning, crouching, reaching, sitting and reclining are welcome. Furniture "
    "keeps the same design, size, ground anchor and position across frames and related sheets. No text, "
    "borders, other characters, extra limbs, background, or real computer interface. "
)

# name, title, style, starting posture, ending posture, role, family, action
_ROWS = [
    ("sweet_hide", "藏起小甜点", "idle", "stand", "stand", "scene_sweet", "sweet", "One small wrapped candy. Frame 1 notice it with delight; 2 crouch and look around guiltily; 3 tuck it behind her back with both hands; 4 stand innocently while her tail curls around it."),
    ("sweet_share", "忍痛分享", "idle", "stand", "stand", "scene_sweet", "sweet", "One small cookie. Frame 1 hold it close and look at the viewer; 2 hesitate, leaning protectively over it; 3 break it into two pieces; 4 offer one piece with a warm smile and keep the other. No other hands."),
    ("sweet_sneak", "偷偷咬一口", "idle", "stand", "stand", "scene_sweet", "sweet", "One small cookie. Frame 1 crouch and hide it behind her back; 2 glance sideways; 3 sneak one bite with delight; 4 freeze after noticing the viewer, cheeks slightly puffed."),
    ("sweet_caught", "偷吃被发现", "idle", "stand", "stand", "scene_sweet", "sweet", "One partly eaten cookie. Frame 1 freeze mid-bite after noticing viewer; 2 lean back, surprised and blushing; 3 offer the remaining cookie with an apologetic bow; 4 smile sheepishly. No other person."),
    ("height_peek", "探头看高处", "idle", "stand", "stand", "scene_height", "height", "An invisible screen edge, no edge drawn. Frame 1 cautiously lean forward; 2 look downward over the imaginary edge; 3 recoil with wide anxious eyes, knees bent; 4 step back and hug her wings. Complete body inside every frame."),
    ("height_nervous", "高处脚软", "idle", "stand", "sit", "scene_height", "height", "Frame 1 look down nervously at an imaginary height; 2 knees wobble and wings tuck tight; 3 sink into a crouch hugging both wings; 4 sit safely on the ground line with a relieved but embarrassed smile. No ledge or background."),
    ("height_wings", "抱住翅膀壮胆", "idle", "sit", "stand", "scene_height", "height", "Frame 1 sit hugging her folded wings, anxious; 2 peek up and take a breath; 3 push up to standing while keeping wings close; 4 look at the viewer with a brave but nervous smile. No ledge."),
    ("height_shortcut", "传送走捷径", "idle", "stand", "stand", "scene_height", "height", "Frame 1 peer downward fearfully at an imaginary height; 2 step back and think, finger raised; 3 draw a tiny hand-sized glowing ring beside her with one hand; 4 point to it with a relieved grin as if choosing a shortcut. The ring stays small; no real hole or background."),
    ("fire_overdo", "耍帅喷火", "idle", "stand", "stand", "scene_fire", "fire", "Frame 1 plant feet and pose confidently, wings open; 2 take a theatrical breath; 3 lean forward and breathe an exaggerated but harmless FAIRY-TALE orange flame, roughly as tall as her torso, entirely inside the cell; 4 recoil with shocked eyes at her own flame. No smoke filling the scene."),
    ("fire_startled", "被自己的火吓到", "idle", "stand", "sit", "scene_fire", "fire", "A single contained cartoon flame no larger than her torso. Frame 1 admire her own flame proudly; 2 notice its size with alarm; 3 hop backward with both hands raised and wings tucked; 4 land sitting safely, eyes wide, as flame shrinks. No destruction."),
    ("fire_recover", "扑灭小火苗", "idle", "sit", "stand", "scene_fire", "fire", "A single tiny harmless flame. Frame 1 sit startled looking at it; 2 lean forward and blow gently; 3 the flame goes out, a tiny smoke wisp remains; 4 stand and wave the wisp away, smiling sheepishly. No damage."),
    ("tea_hot", "龙火把茶烫过头", "tea", "sit", "sit", "scene_fire", "tea", "One sage teacup. Frame 1 sit and warm it with a tiny fingertip flame; 2 take a sip; 3 recoil surprised that it is too hot, keeping cup upright; 4 blow gently with an embarrassed smile. Small steam only, no spill."),
    ("tea_relax", "喝茶歇一会", "tea", "sit", "sit", "scene_outcome", "tea", "One sage teacup. Frame 1 sit and blow softly; 2 sip; 3 lean back contentedly; 4 raise the cup to the viewer with a relaxed smile. No spill."),
    ("fire_controlled", "小火苗刚刚好", "idle", "stand", "stand", "scene_fire", "fire", "Frame 1 pose proudly and inhale; 2 produce one tiny harmless flame above her palm; 3 carefully reduce it to a small steady flame; 4 extinguish it and take a proud bow. No large effects."),
    ("paint_proud", "画好给你看", "draw", "stand", "stand", "scene_outcome", "painting", "The same SMALL floating square canvas on a light wooden frame as image 2, about the size of her head, its cream BACK facing the viewer until the reveal; brush in right hand; the same small round palette floats beside the canvas in all four frames and is never held. No easel, stand or table. Frame 1 paint a final stroke on its hidden front; 2 lean back to inspect while it hovers; 3 grasp and turn the canvas to the viewer, showing a lavender flower; 4 hold it proudly and bow. Same canvas dimensions in all frames, no writing."),
    ("paint_rework", "画错再改", "draw", "stand", "stand", "scene_outcome", "painting", "The same SMALL floating square canvas on a light wooden frame as image 2, about the size of her head, cream BACK facing the viewer throughout, with brush and palette. No easel, stand or table. Frame 1 paint its hidden front; 2 notice a wrong stroke and lean back surprised; 3 wipe its hidden front using a cloth; 4 repaint with her brush. Canvas stays in the same location and size. No writing."),
    ("sleepy_book", "困着装看书", "read", "sit", "sit", "scene_sleepy", "sleepy_book", "One small open comic. Frame 1 sit reading intently; 2 eyelids droop and head nods; 3 catch herself before the book falls; 4 quickly turn a page and pretend to read alertly. No legible text."),
    ("sleepy_cover", "睡醒装精神", "read", "sit", "sit", "scene_sleepy", "sleepy_book", "One small comic. Frame 1 sit dozing with book in lap; 2 jolt awake and rub one eye; 3 notice viewer and hurriedly lift the book upside down; 4 realize and turn it right way up, pretending to read. No legible text."),
    ("sweet_share_more", "多分你一半", "idle", "stand", "stand", "scene_sweet", "sweet", "One partly eaten cookie. Frame 1 hide a bite guiltily; 2 notice viewer and blush; 3 break off the larger piece; 4 lean forward and offer it sincerely, keeping the smaller piece. No other person."),
    ("sofa_settle", "沙发上靠下", "idle", "sit", "lie", "scene_furniture", "sofa", "The same full compact lavender sofa with rounded arms and short feet at the same floor anchor. Frame 1 sit supported; 2 turn and brace one hand on cushion; 3 lift both feet onto seat; 4 recline with head on armrest and body fully supported. Whole sofa visible."),
    ("chair_exit", "椅子收好啦", "idle", "sit", "stand", "scene_furniture", "chair", "The same sage folding lounge chair at the same floor anchor. Frame 1 sit supported; 2 lean forward and stand; 3 crouch and fold the chair using both hands; 4 stand holding the folded chair. The whole chair remains visible."),
    ("chair_entry", "展开椅子坐下", "idle", "stand", "sit", "scene_furniture", "chair", "One sturdy sage folding lounge chair as tall as her. Frame 1 carry it folded; 2 crouch and unfold; 3 turn and sit; 4 settle supported by its backrest. Full chair and body visible."),
    ("chair_rest", "坐椅子陪你", "idle", "sit", "sit", "scene_furniture", "chair", "The same open sage folding lounge chair at the same floor anchor. In all frames she sits supported by seat and back; frames 1-4 open eyes, soft breath, gentle blink, return. Only eyes and breathing move; furniture fixed."),
    ("sofa_entry", "小沙发出场", "idle", "stand", "sit", "scene_furniture", "sofa", "One compact lavender two-seat sofa with rounded arms, back and short feet, same floor anchor. Frame 1 stand beside it; 2 turn; 3 sit down; 4 settle supported. Full sofa and body visible."),
    ("sofa_lounge", "沙发上躺一会", "idle", "lie", "lie", "scene_furniture", "sofa", "The same complete lavender sofa at the same floor anchor. In all frames she reclines supported along the cushion, head on armrest and legs on seat. Frames 1-4 open eyes, soft breath, gentle blink, return. Furniture and pose stable."),
    ("sofa_exit", "收起小沙发", "idle", "lie", "stand", "scene_furniture", "sofa", "The same compact lavender convertible sofa with rounded arms and short feet. Frame 1 recline supported; 2 sit upright on the sofa; 3 stand beside it and fold the back onto the seat with both hands; 4 stand holding the closed sofa. Complete sofa visible."),
    ("game_win", "游戏赢啦", "gamepad", "sit", "stand", "scene_outcome", "game", "One sage handheld console is clearly visible in BOTH hands in every frame. Frame 1 sit and play; 2 recoil in delighted surprise at a win; 3 rise to her feet lifting the SAME console overhead in both hands, wings spread; 4 stand and hug it proudly. The console never disappears or changes size; exactly two arms and hands. No screen text."),
    ("game_loss", "惜败一局", "gamepad", "sit", "sit", "scene_outcome", "game", "One sage handheld console. Frame 1 sit and play; 2 freeze at a loss; 3 slump with a pout and console in lap; 4 glance up with a rueful smile. No screen text."),
    ("game_rematch", "再来一局", "gamepad", "sit", "sit", "scene_outcome", "game", "One sage handheld console. Frame 1 sit disappointed; 2 look up and perk up; 3 offer it to the viewer; 4 pull it back and lean forward to play again. No screen text or other hand."),
    ("paint_cheek", "脸上沾了颜料", "draw", "stand", "stand", "scene_outcome", "painting", "The same SMALL floating square canvas on a light wooden frame as image 2, about the size of her head, cream BACK facing the viewer, and a paintbrush. The same small round palette floats beside the canvas throughout all four frames. No notebook, easel, stand or table. Frame 1 paint its hidden front; 2 accidentally touch her cheek with the brush leaving a tiny lavender dot; 3 lean back and notice it; 4 laugh and wipe her cheek while the same canvas still floats beside her. No writing."),
    ("glass_hello", "贴着玻璃打招呼", "idle", "stand", "stand", "scene_viewer", "viewer", "Mime a transparent invisible pane, draw no pane or border. Frame 1 step close and put both open palms and her nose against the imagined pane, cheeks lightly squished; 2 look straight at the viewer with curious eyes; 3 pull her head and both hands back in surprise; 4 step back, blush and smile shyly. Full body in every frame."),
    ("fog_wipe", "擦出小窗偷看", "idle", "stand", "stand", "scene_viewer", "viewer", "Mime an invisible pane, no wall or border drawn. Frame 1 lean toward the viewer and breathe a small translucent mist patch; 2 reach up and wipe one small clear area with her cardigan sleeve; 3 lean in and peek through with a mischievous smile; 4 notice a little smudge on her sleeve and pull it back sheepishly. Only tiny localized mist, no background."),
    ("diary_hide", "日记被看到啦", "read", "sit", "sit", "scene_viewer", "viewer", "One small closed-cover sage diary and a pencil, no legible text. Frame 1 sit and write in the open diary on her lap; 2 notice viewer and recoil a little; 3 shut and hug the diary to her chest with both hands; 4 hide her blushing face behind one small wing but peek one eye out. Keep the diary visible and the full body within each frame."),
    ("pounce_miss", "扑空装伸懒腰", "idle", "sit", "stand", "scene_outcome", "pounce", "One continuous playful follow-through to a failed pounce, no cursor or other object. Frame 1 crouch low after landing and open both empty hands; 2 peer between them and realize she missed; 3 rise to standing and stretch both arms overhead as if that was always the plan; 4 lower her arms and sneak a sheepish glance at the viewer. Exactly two arms and two hands, no object or motion lines."),
]

_LOOPS = {"chair_rest", "sofa_lounge"}
# Limited-animation timing at 4 fps: settle into the first pose, move through the middle two,
# hold the outcome. One pass is ~3 s, then the next beat starts instead of a frozen last frame.
_BEATS = [0, 0, 0, 1, 1, 2, 2, 3, 3, 3, 3, 3]
ACTIONS = {
    f"scene_{name}": {
        "title": title, "prompt": _HEAD + action, "style": style,
        "starts_from": start, "posture": end, "role": role, "family": family,
        "fps": 4.0, "loop": name in _LOOPS, "scale": 1.0,
        # The height beats play quickly inside the top-edge exit, which times them itself.
        **({} if name in _LOOPS or name.startswith("height_") else {"sequence": _BEATS}),
        "duration": 45.0 if name in _LOOPS else 1.0, "drop_strays": True,
        **({"restful": True} if name in _LOOPS else {}),
        **({"frame_calibration": True, "anchor_x": 256, "fixed_ground": True}
           if role == "scene_furniture" else {}),
        **({"reuse_strip_frames": {"strip": "scene_sofa_settle", "frames": [3, 2, 3, 3]}}
           if name == "sofa_lounge" else {}),
        **({"result_family": name.split("_", 1)[0]} if name.startswith(("game_", "paint_", "tea_")) else {}),
    }
    for name, title, style, start, end, role, family, action in _ROWS
}

# Approach the viewer before the foreshortened glass pose; this is a one-shot
# perspective gag, not a camera-size correction or a repeated idle zoom.
ACTIONS["scene_glass_hello"].update(sequence=[1, 1, 1, 0, 0, 0, 0, 0, 2, 2, 3, 3, 3, 3],
                                    calibration_frames=[1, 2, 3])
ACTIONS["scene_fog_wipe"].update(overlay={
    "kind": "mist", "pos": [0.18, -0.58], "radius": 0.19,
    "duration": 1.6, "wipe_start": 0.75,
})
ACTIONS["scene_sweet_caught"]["posture"] = "sit"
ACTIONS["scene_fire_startled"].update(sequence=[1, 1, 2, 2, 3, 3, 3, 3])
