"""生活与实时状态动作素材目录；每个条目对应一张独立四帧图。"""

# name, title, style, posture, role, family, four-frame action
_ROWS = [
    ('comic_open', '翻开漫画', 'read', 'sit', 'study', 'reading', 'Hold a small open comic in both hands; lift its cover; settle it open; glance down at the panels; fingertips rest on the page.'),
    ('comic_turn', '翻漫画页', 'read', 'sit', 'study', 'reading', 'Hold an open comic steady; pinch one page corner; turn that page halfway; smooth the page down.'),
    ('comic_amused', '漫画看到有趣处', 'read', 'sit', 'play', 'reading', 'Hold an open comic; eyes follow a panel; one hand covers a small amused smile; hand returns to the book.'),
    ('note_read', '读笔记', 'read', 'sit', 'study', 'reading', 'Hold a small notebook open; scan its blank pages; slide a fingertip down the page; look back to the top.'),
    ('bookmark_place', '夹书签', 'read', 'sit', 'study', 'reading', 'Hold a little book open; pick up a ribbon bookmark; place it between pages; gently close the book partway.'),
    ('book_doodle', '书边涂鸦', 'read', 'sit', 'drawing', 'reading', 'Hold an open sketchbook on her lap; pencil touches a blank corner; draw a tiny star; lift pencil and inspect it.'),
    ('pencil_sharpen', '削铅笔', 'draw', 'sit', 'study', 'work', 'Hold a pencil and tiny handheld sharpener; insert its tip; twist the pencil slightly; inspect the tip.'),
    ('laptop_type', '陪你敲电脑', 'laptop', 'sit', 'study', 'work', 'Sit with a small laptop in her lap; fingers rest on keys; type gently; pause to read the blank screen; fingers settle.'),
    ('laptop_think', '电脑前想一想', 'laptop', 'sit', 'study', 'work', 'Sit with a small open laptop; look at its blank screen; raise one hand to chin; tap a key; hand rests.'),
    ('tea_pour', '倒一杯茶', 'tea', 'sit', 'tea', 'tea_time', 'A tiny teapot and cup rest beside her; hold the pot; tip its spout over the cup; return it upright; set it down.'),
    ('tea_sip', '捧杯喝茶', 'tea', 'sit', 'tea', 'tea_time', 'Hold a small cup with both hands; lift it near her lips; take a small sip; lower it into her lap.'),
    ('tea_cool', '吹凉热茶', 'tea', 'sit', 'tea', 'tea_time', 'Hold a small cup in both hands; look into it; blow gently over the rim; relax and cradle it again.'),
    ('bread_bite', '吃小面包', 'tea', 'sit', 'eat', 'snack', 'Hold a small bread roll; bring it toward her mouth; take a tiny bite; lower it with a content expression.'),
    ('riceball', '吃饭团', 'tea', 'sit', 'eat', 'snack', 'Hold a triangular rice ball with a seaweed band; raise it; take a small bite; lower it carefully.'),
    ('candy_unwrap', '剥糖纸', 'tea', 'sit', 'eat', 'snack', 'Hold one wrapped candy; untwist one end; peel the wrapper back; look at the revealed candy.'),
    ('hair_comb', '梳梳头发', 'shy', 'sit', 'care', 'care', 'Sit awake holding a small comb; lift a loose hair strand; comb its end gently; lower the comb.'),
    ('tail_pillow', '靠尾巴休息', 'idle', 'sit', 'rest', 'care', 'Sit awake with her own tail curled beside her like a cushion; rest one hand on it; gently pat its scales; look forward.'),
    ('water', '喝口水', 'tea', 'sit', 'care', 'care', 'Hold a clear small water glass in both hands; raise it; take a small sip; lower it to her lap.'),
    ('wrist_stretch', '活动手腕', 'idle', 'stand', 'rest', 'care', 'Stand with feet still; hold one wrist gently with the other hand; rotate the wrist slightly; release and relax.'),
    ('shoulder_roll', '放松肩膀', 'idle', 'stand', 'rest', 'care', 'Stand with feet planted and arms relaxed; shoulders rise a little; ease back down; hands rest at her sides.'),
    ('clock_check', '看看时间', 'idle', 'stand', 'curious', 'care', 'Hold a tiny round pocket watch; lift it to eye level; glance at its hands; lower it gently.'),
    ('phone_notice', '看到新消息', 'idle', 'stand', 'greet', 'message', 'Hold a small phone with a blank screen; notice it; lift it slightly; focus on the screen.'),
    ('phone_read', '看一眼消息', 'read', 'sit', 'inspect', 'message', 'Hold a small phone in both hands; eyes scan its blank screen; thumb moves slightly; pause and look attentive.'),
    ('phone_reply', '手机回消息', 'laptop', 'sit', 'reply', 'message', 'Hold a small phone in both hands; thumbs hover; tap the blank screen with one thumb; thumbs settle.'),
    ('phone_greet', '向消息打招呼', 'idle', 'stand', 'greet', 'message', 'Hold a small blank-screen phone at chest height; raise the free hand in a tiny wave; lower it; look at the phone.'),
    ('chat_popcorn', '热闹时吃瓜', 'tea', 'sit', 'curious', 'chat', 'Hold a tiny popcorn tub in her lap; glance toward the side; pick up one piece; return her hand to the tub.'),
    ('chat_nod', '听懂点头', 'idle', 'sit', 'listen', 'chat', 'Hold a small phone at her lap; look at its blank screen; make a tiny acknowledging nod; return to neutral.'),
    ('image_photo', '端详图片', 'read', 'stand', 'inspect', 'image', 'Hold a small unmarked photograph with both hands; raise it slightly; eyes inspect a corner; lower it a little.'),
    ('image_magnify', '放大镜看图片', 'read', 'sit', 'inspect', 'image', 'Hold a blank picture card and a small magnifying glass; move the lens a little over the card; pause; return it.'),
    ('search_book', '翻资料书', 'read', 'sit', 'search', 'research', 'Hold a small open reference book; scan a page; turn one page corner; settle and continue looking.'),
    ('search_laptop', '电脑查资料', 'laptop', 'sit', 'search', 'research', 'Sit with a small laptop; tap a key; read the blank screen; touch her chin; hand returns to the keyboard.'),
    ('voice_headphone', '戴耳机听语音', 'sing', 'sit', 'listen', 'audio', 'Wear small headphones around her horns; hold one earcup lightly; listen attentively; release the earcup.'),
    ('voice_record', '录一段声音', 'sing', 'stand', 'voice', 'audio', 'Hold a small microphone near her mouth; take a small breath; speak softly; pause while keeping the microphone still.'),
    ('voice_mic_check', '准备录音', 'sing', 'sit', 'voice', 'audio', 'Hold a small microphone in both hands; check its position; bring it near her mouth; pause attentively.'),
    ('drawing_pencil', '用铅笔画图', 'draw', 'sit', 'drawing', 'art', 'Hold a small sketchbook on her lap; move a pencil a short stroke; add another stroke; lift the pencil.'),
    ('drawing_tablet', '数位板画图', 'draw', 'sit', 'drawing', 'art', 'Hold a small blank drawing tablet on her lap; move a stylus a short distance; pause; add another small stroke.'),
    ('sent_confetti', '发送后开心', 'shy', 'stand', 'celebrate', 'sent', 'Hold a small phone with blank screen; tap once; make a tiny pleased fist near her chest; relax her hand.'),
    ('sent_check', '发送后松口气', 'shy', 'sit', 'celebrate', 'sent', 'Hold a small phone with blank screen; tap once; lower it slightly; close eyes briefly in relief.'),
]

_CONSTRAINTS = (" Four-frame 2x2 sprite sheet on transparent background; each complete figure and prop "
                "fits within its own 512px cell. Same adult chibi silver-white lavender dragon woman "
                "in sage cardigan, exactly two arms, two hands and two legs. Keep the camera, head size "
                "and body proportions consistent. Let posture, weight, gaze, wings and tail express "
                "the specified intention and result. Quiet loops settle naturally; one-shot results "
                "retain their changed prop or emotion instead of undoing the action. "
                "No text, chat content, group IDs, borders, anger, stomping or pointing at anyone.")

ACTIONS = {
    'life_' + name: {
        'title': title, 'prompt': action + _CONSTRAINTS, 'style': style,
        'posture': posture, 'role': role, 'family': family,
        'fps': 1.0, 'loop': True, 'scale': 0.8 if posture == 'sit' else 1.0,
        'duration': 8.0,
    }
    for name, title, style, posture, role, family, action in _ROWS
}

# Flare 的实际素材采用站姿；碰撞框与桥接应跟随成图而非原先的坐姿意图。
# 2026-11-15 吃面包、吃饭团按原意重画成坐着吃（旧稿是坐姿比例的矮站姿），不再列在这里。
for _name in (
    'chat_nod', 'note_read', 'pencil_sharpen', 'phone_read', 'phone_reply', 'search_book', 'sent_check',
    'voice_headphone', 'voice_mic_check',
):
    ACTIONS['life_' + _name]['posture'] = 'stand'
    ACTIONS['life_' + _name]['scale'] = 1.0

# 睡姿与回复的额外轮播。
_EXTRA = [
    ('sleep_hug_tail', '抱尾睡', 'sleep', 'lie', 'sleep', 'tail', 'Lie asleep on her side, gently hugging her own curled tail; tiny breathing and a soft tail-tip movement.'),
    ('sleep_wing_blanket', '翅膀盖一盖', 'sleep', 'lie', 'sleep', 'wing', 'Sleep on her side with one small attached wing draped gently over her shoulders like a blanket; slow breathing.'),
    ('sleep_face_down', '趴着睡', 'sleep', 'lie', 'sleep', 'prone', 'Sleep lying on her stomach with cheek on folded hands and both legs stretched behind; breathe softly.'),
    ('reply_air_phone', '飞着回消息', 'idle', 'air', 'reply', 'joyful', 'Hover at one fixed height with both feet off the ground, holding a blank-screen phone in both hands; tap gently with one thumb and smile.'),
    ('reply_sit_calm', '坐着慢慢回复', 'laptop', 'sit', 'reply', 'calm', 'Sit on the floor throughout holding a blank-screen phone, gently tap with one thumb, pause and read.'),
    ('reply_sit_sleepy', '困困地回消息', 'laptop', 'sit', 'reply', 'sleepy', 'Sit on the floor throughout, awake with relaxed sleepy eyes, hold a blank-screen phone and make a small thumb tap.'),
    ('reply_stand_bright', '站着开心回复', 'idle', 'stand', 'reply', 'bright', 'Stand on both feet throughout, hold a blank phone with both hands, type with one thumb and smile softly.'),
    ('reply_stand_think', '站着想怎么回', 'idle', 'stand', 'reply', 'quiet', 'Stand on both feet throughout, hold a blank phone, touch her chin briefly, then make a small thumb tap.'),
]
for _name, _title, _style, _posture, _role, _family, _action in _EXTRA:
    ACTIONS['life_' + _name] = {
        'title': _title, 'prompt': _action + _CONSTRAINTS, 'style': _style,
        'posture': _posture, 'role': _role, 'family': _family,
        'fps': .7 if _role == 'sleep' else 1.0, 'loop': True,
        'scale': .7 if _posture == 'lie' else .8 if _posture == 'sit' else 1.0, 'duration': 8.0,
    }

ACTIONS["life_reply_air_phone"]["prompt"] += (
    " While holding the same phone/card and keeping the face, head size, torso, hands and feet "
    "at the exact same positions, BOTH lavender wings perform one clear complete wing beat: "
    "frame 1 wing tips UP beside her head; frame 2 spread HORIZONTALLY; frame 3 tips DOWN "
    "beside her hips; frame 4 spread horizontally on the return stroke. The reference flapping "
    "sheet shows the required phase difference. The wings themselves change angle and shape "
    "strongly enough to read at desktop-pet size. No stationary decorative wings or body zoom."
)
