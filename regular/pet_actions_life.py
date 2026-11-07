"""生活与实时状态动作素材目录；每个条目对应一张独立四帧图。"""

# name, title, style, posture, role, family, four-frame action
_ROWS = [
    ('comic_open', '翻开漫画', 'read', 'sit', 'study', 'reading', 'Hold a small open comic in both hands; lift its cover; settle it open; glance down at the panels; fingertips rest on the page.'),
    ('comic_turn', '翻漫画页', 'read', 'sit', 'study', 'reading', 'Hold an open comic steady; pinch one page corner; turn that page halfway; smooth the page down.'),
    ('comic_amused', '漫画看到有趣处', 'read', 'sit', 'play', 'reading', 'Hold an open comic; eyes follow a panel; one hand covers a small amused smile; hand returns to the book.'),
    ('note_read', '读笔记', 'read', 'sit', 'study', 'reading', 'Hold a small notebook open; scan its blank pages; slide a fingertip down the page; look back to the top.'),
    ('map_unfold', '展开地图', 'read', 'sit', 'study', 'reading', 'Hold a small folded map; open one fold; study the simple unlettered shapes; fold the edge back slightly.'),
    ('bookmark_place', '夹书签', 'read', 'sit', 'study', 'reading', 'Hold a little book open; pick up a ribbon bookmark; place it between pages; gently close the book partway.'),
    ('book_doodle', '书边涂鸦', 'read', 'sit', 'drawing', 'reading', 'Hold an open sketchbook on her lap; pencil touches a blank corner; draw a tiny star; lift pencil and inspect it.'),
    ('book_stack', '整理小书堆', 'read', 'sit', 'study', 'reading', 'A small two-book stack rests beside her; hold the top book; align its corner; pat the cover; hands return to lap.'),
    ('pencil_note', '记一笔', 'draw', 'sit', 'study', 'work', 'Hold a small notebook in one hand and pencil in the other; pencil touches blank paper; make a short mark; lift pencil thoughtfully.'),
    ('pencil_sharpen', '削铅笔', 'draw', 'sit', 'study', 'work', 'Hold a pencil and tiny handheld sharpener; insert its tip; twist the pencil slightly; inspect the tip.'),
    ('sticky_note', '贴便签', 'draw', 'sit', 'study', 'work', 'Hold a blank square sticky note and a small notebook; lift the note; press it onto the notebook cover; smooth one corner.'),
    ('whiteboard', '写小白板', 'draw', 'stand', 'study', 'work', 'Hold a tiny handheld whiteboard with one hand and marker with the other; make a simple circle; add a dot; lower the marker.'),
    ('letter_open', '拆开信封', 'read', 'sit', 'inspect', 'work', 'Hold a small sealed envelope; lift its flap; draw out a blank card a little; look at the card.'),
    ('letter_fold', '折好信纸', 'read', 'sit', 'study', 'work', 'Hold a blank sheet above an envelope; fold it in half; press the crease; slide its edge into the envelope.'),
    ('laptop_type', '陪你敲电脑', 'laptop', 'sit', 'study', 'work', 'Sit with a small laptop in her lap; fingers rest on keys; type gently; pause to read the blank screen; fingers settle.'),
    ('laptop_think', '电脑前想一想', 'laptop', 'sit', 'study', 'work', 'Sit with a small open laptop; look at its blank screen; raise one hand to chin; tap a key; hand rests.'),
    ('tea_pour', '倒一杯茶', 'tea', 'sit', 'tea', 'tea_time', 'A tiny teapot and cup rest beside her; hold the pot; tip its spout over the cup; return it upright; set it down.'),
    ('tea_sip', '捧杯喝茶', 'tea', 'sit', 'tea', 'tea_time', 'Hold a small cup with both hands; lift it near her lips; take a small sip; lower it into her lap.'),
    ('tea_cool', '吹凉热茶', 'tea', 'sit', 'tea', 'tea_time', 'Hold a small cup in both hands; look into it; blow gently over the rim; relax and cradle it again.'),
    ('tea_stir', '轻搅茶杯', 'tea', 'sit', 'tea', 'tea_time', 'Hold a small cup and teaspoon; dip the spoon; stir a little circle; lift the spoon to rest at the rim.'),
    ('bread_break', '掰小面包', 'tea', 'sit', 'eat', 'snack', 'Hold a small round bread roll in both hands; pull gently; separate one small piece; examine the piece.'),
    ('bread_bite', '吃小面包', 'tea', 'sit', 'eat', 'snack', 'Hold a small bread roll; bring it toward her mouth; take a tiny bite; lower it with a content expression.'),
    ('riceball', '吃饭团', 'tea', 'sit', 'eat', 'snack', 'Hold a triangular rice ball with a seaweed band; raise it; take a small bite; lower it carefully.'),
    ('fruit_slice', '尝水果', 'tea', 'sit', 'eat', 'snack', 'Hold an apple slice and a small plate; raise the slice; take a tiny bite; lower it over the plate.'),
    ('candy_unwrap', '剥糖纸', 'tea', 'sit', 'eat', 'snack', 'Hold one wrapped candy; untwist one end; peel the wrapper back; look at the revealed candy.'),
    ('cookie_offer', '分享饼干', 'tea', 'sit', 'care', 'snack', 'Hold a small cookie on an open palm; lift it slightly toward the viewer; hold it steady; bring the hand back.'),
    ('paper_crane', '折纸鹤', 'draw', 'sit', 'play', 'craft', 'Hold a small square of paper; fold one corner; press a crease; inspect a simple half-folded paper crane.'),
    ('paper_plane', '折纸飞机', 'draw', 'sit', 'play', 'craft', 'Hold a small sheet of paper; fold it lengthwise; press its wing crease; hold the little paper airplane still.'),
    ('yarn_ball', '拨毛线球', 'idle', 'sit', 'play', 'toys', 'Stand in exactly the same upright pose in all four frames with both feet planted. Hold a small yarn ball at waist height in one hand; gently roll it against the other palm; stop; settle. No crouching or leaning.'),
    ('windup', '拧发条玩具', 'idle', 'sit', 'play', 'toys', 'Stand upright with both feet fixed in all four frames. Hold a tiny wind-up toy at chest height; turn its key a little; watch its small head tilt; hold it still. Never sit or crouch.'),
    ('blocks', '搭积木', 'idle', 'sit', 'play', 'toys', 'Hold one small wooden block above another; lower it; align their edges; release it with a pleased look.'),
    ('spinning_top', '玩陀螺', 'idle', 'sit', 'play', 'toys', 'A small top rests on the floor by her; touch its stem; give a small twist; watch it and withdraw her hand.'),
    ('ribbon', '整理缎带', 'shy', 'sit', 'play', 'craft', 'Hold a short ribbon in both hands; cross its ends; form one small bow; straighten the loops.'),
    ('puzzle', '拼小拼图', 'idle', 'sit', 'play', 'toys', 'Hold a single jigsaw piece over two joined pieces; rotate it slightly; fit it into place; withdraw her hand.'),
    ('hair_comb', '梳梳头发', 'shy', 'sit', 'care', 'care', 'Sit awake holding a small comb; lift a loose hair strand; comb its end gently; lower the comb.'),
    ('tail_pillow', '靠尾巴休息', 'idle', 'sit', 'rest', 'care', 'Sit awake with her own tail curled beside her like a cushion; rest one hand on it; gently pat its scales; look forward.'),
    ('fan', '扇扇子', 'idle', 'sit', 'rest', 'care', 'Hold a small folding fan; open it partway; fan once with a small wrist movement; bring it back to rest.'),
    ('water', '喝口水', 'tea', 'sit', 'care', 'care', 'Hold a clear small water glass in both hands; raise it; take a small sip; lower it to her lap.'),
    ('wrist_stretch', '活动手腕', 'idle', 'stand', 'rest', 'care', 'Stand with feet still; hold one wrist gently with the other hand; rotate the wrist slightly; release and relax.'),
    ('shoulder_roll', '放松肩膀', 'idle', 'stand', 'rest', 'care', 'Stand with feet planted and arms relaxed; shoulders rise a little; ease back down; hands rest at her sides.'),
    ('clock_check', '看看时间', 'idle', 'stand', 'curious', 'care', 'Hold a tiny round pocket watch; lift it to eye level; glance at its hands; lower it gently.'),
    ('hair_clip', '夹好发夹', 'shy', 'sit', 'care', 'care', 'Hold a small hair clip; lift a loose strand with the other hand; secure the clip; lower both hands.'),
    ('phone_notice', '看到新消息', 'idle', 'stand', 'greet', 'message', 'Hold a small phone with a blank screen; notice it; lift it slightly; focus on the screen.'),
    ('phone_read', '看一眼消息', 'read', 'sit', 'inspect', 'message', 'Hold a small phone in both hands; eyes scan its blank screen; thumb moves slightly; pause and look attentive.'),
    ('phone_reply', '手机回消息', 'laptop', 'sit', 'reply', 'message', 'Hold a small phone in both hands; thumbs hover; tap the blank screen with one thumb; thumbs settle.'),
    ('phone_compose', '斟酌回复', 'laptop', 'sit', 'reply', 'message', 'Hold a phone with blank screen in one hand; the other hand touches her chin; thumb taps once; return to reading.'),
    ('phone_scroll', '浏览消息', 'read', 'sit', 'inspect', 'message', 'Hold a phone with blank screen in one hand; other thumb makes a small scroll; eyes follow; hand rests.'),
    ('phone_greet', '向消息打招呼', 'idle', 'stand', 'greet', 'message', 'Hold a small blank-screen phone at chest height; raise the free hand in a tiny wave; lower it; look at the phone.'),
    ('chat_popcorn', '热闹时吃瓜', 'tea', 'sit', 'curious', 'chat', 'Hold a tiny popcorn tub in her lap; glance toward the side; pick up one piece; return her hand to the tub.'),
    ('chat_peek', '好奇看热闹', 'idle', 'stand', 'curious', 'chat', 'Stand holding a small paper fan near her chest; eyes glance aside; lift the fan a little; eyes return forward.'),
    ('chat_listen', '认真听群友说', 'idle', 'stand', 'listen', 'chat', 'Hold a small blank-screen phone; cup one hand near her ear; tilt the phone slightly closer; relax her hand.'),
    ('chat_nod', '听懂点头', 'idle', 'sit', 'listen', 'chat', 'Hold a small phone at her lap; look at its blank screen; make a tiny acknowledging nod; return to neutral.'),
    ('image_photo', '端详图片', 'read', 'stand', 'inspect', 'image', 'Hold a small unmarked photograph with both hands; raise it slightly; eyes inspect a corner; lower it a little.'),
    ('image_magnify', '放大镜看图片', 'read', 'sit', 'inspect', 'image', 'Hold a blank picture card and a small magnifying glass; move the lens a little over the card; pause; return it.'),
    ('image_compare', '比对图片', 'read', 'sit', 'inspect', 'image', 'Hold two small unmarked picture cards side by side; look at the left card; look at the right card; bring them level.'),
    ('image_tilt', '换角度看图', 'read', 'stand', 'inspect', 'image', 'Hold one blank picture card; tilt the card slightly left; tilt it slightly right; hold it level.'),
    ('search_book', '翻资料书', 'read', 'sit', 'search', 'research', 'Hold a small open reference book; scan a page; turn one page corner; settle and continue looking.'),
    ('search_lens', '查找资料', 'read', 'sit', 'search', 'research', 'Hold a small blank document and magnifying glass; move the lens along the paper; pause; move it back.'),
    ('search_laptop', '电脑查资料', 'laptop', 'sit', 'search', 'research', 'Sit with a small laptop; tap a key; read the blank screen; touch her chin; hand returns to the keyboard.'),
    ('search_cards', '查索引卡', 'read', 'sit', 'search', 'research', 'Hold a small stack of blank index cards; slide the top card aside; inspect the next card; align the stack.'),
    ('voice_headphone', '戴耳机听语音', 'sing', 'sit', 'listen', 'audio', 'Wear small headphones around her horns; hold one earcup lightly; listen attentively; release the earcup.'),
    ('voice_speaker', '听手机语音', 'sing', 'stand', 'listen', 'audio', 'Hold a small blank-screen phone near one ear; listen quietly; adjust it slightly; lower it a little.'),
    ('voice_record', '录一段声音', 'sing', 'stand', 'voice', 'audio', 'Hold a small microphone near her mouth; take a small breath; speak softly; pause while keeping the microphone still.'),
    ('voice_mic_check', '准备录音', 'sing', 'sit', 'voice', 'audio', 'Hold a small microphone in both hands; check its position; bring it near her mouth; pause attentively.'),
    ('drawing_pencil', '用铅笔画图', 'draw', 'sit', 'drawing', 'art', 'Hold a small sketchbook on her lap; move a pencil a short stroke; add another stroke; lift the pencil.'),
    ('drawing_brush', '用画笔上色', 'draw', 'sit', 'drawing', 'art', 'Hold a small blank sketchbook and paintbrush; touch the brush to a small color patch; make a short stroke; lift it.'),
    ('drawing_palette', '调一调颜色', 'draw', 'sit', 'drawing', 'art', 'Hold a tiny palette and paintbrush; touch one paint spot; mix a little circle; inspect the brush tip.'),
    ('drawing_tablet', '数位板画图', 'draw', 'sit', 'drawing', 'art', 'Hold a small blank drawing tablet on her lap; move a stylus a short distance; pause; add another small stroke.'),
    ('sent_confetti', '发送后开心', 'shy', 'stand', 'celebrate', 'sent', 'Hold a small phone with blank screen; tap once; make a tiny pleased fist near her chest; relax her hand.'),
    ('sent_check', '发送后松口气', 'shy', 'sit', 'celebrate', 'sent', 'Hold a small phone with blank screen; tap once; lower it slightly; close eyes briefly in relief.'),
    ('wait_hourglass', '等服务回应', 'idle', 'sit', 'rest', 'waiting', 'Stand upright on both feet in every frame, never sit or crouch. Hold a tiny hourglass upright at waist height; watch its sand; turn it gently; hold it steady again.'),
    ('wait_think', '等待时想一想', 'idle', 'stand', 'curious', 'waiting', 'Hold a blank note card in one hand; touch her chin with the other; glance at the card; relax her hand.'),
]

_CONSTRAINTS = (" Four-frame 2x2 sprite sheet on transparent background; each complete figure and prop "
                "fits within its own 512px cell. Same adult chibi silver-white lavender dragon woman "
                "in sage cardigan, exactly two arms, two hands and two legs. Keep head, torso, feet, "
                "scale and position stable, with only small readable hand, eye, tail or wing movements. "
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
for _name in (
    'blocks', 'bread_bite', 'chat_nod', 'cookie_offer', 'drawing_brush', 'drawing_palette', 'fan', 'fruit_slice', 'hair_clip',
    'image_compare', 'letter_fold', 'letter_open', 'map_unfold', 'note_read', 'paper_crane', 'paper_plane',
    'pencil_note', 'pencil_sharpen', 'phone_compose', 'phone_read', 'phone_reply', 'phone_scroll', 'riceball',
    'search_book', 'search_cards', 'search_lens', 'sent_check', 'sticky_note', 'tea_stir',
    'puzzle', 'ribbon', 'spinning_top', 'voice_headphone', 'voice_mic_check', 'wait_hourglass', 'windup', 'yarn_ball',
):
    ACTIONS['life_' + _name]['posture'] = 'stand'
    ACTIONS['life_' + _name]['scale'] = 1.0

# 睡姿与回复的额外轮播，独立于原来的 72 个生活动作。
_EXTRA = [
    ('sleep_hug_tail', '抱尾睡', 'sleep', 'lie', 'sleep', 'tail', 'Lie asleep on her side, gently hugging her own curled tail; tiny breathing and a soft tail-tip movement.'),
    ('sleep_curled_tail', '尾巴环抱睡', 'sleep', 'lie', 'sleep', 'tail', 'Sleep curled on her side with her own tail wrapped loosely around her; only a tiny breathing motion.'),
    ('sleep_wing_blanket', '翅膀盖一盖', 'sleep', 'lie', 'sleep', 'wing', 'Sleep on her side with one small attached wing draped gently over her shoulders like a blanket; slow breathing.'),
    ('sleep_face_down', '趴着睡', 'sleep', 'lie', 'sleep', 'prone', 'Sleep lying on her stomach with cheek on folded hands and both legs stretched behind; breathe softly.'),
    ('sleep_side_hands', '枕手侧睡', 'sleep', 'lie', 'sleep', 'side', 'Sleep on her side with both hands tucked under her cheek and knees gently bent; tiny breathing.'),
    ('sleep_back', '仰躺睡', 'sleep', 'lie', 'sleep', 'back', 'Sleep on her back with hands folded over her cardigan and tail resting beside her; tiny breathing.'),
    ('sleep_tail_pillow', '枕尾巴睡', 'sleep', 'lie', 'sleep', 'tail', 'Sleep on her side resting her cheek on her own attached tail as a pillow; a tiny tail-tip curl.'),
    ('sleep_star_pillow', '抱星枕睡', 'sleep', 'lie', 'sleep', 'pillow', 'Sleep on her side holding a small star-shaped cushion between both hands; gentle breathing.'),
    ('sleep_round_pillow', '抱圆枕睡', 'sleep', 'lie', 'sleep', 'pillow', 'Sleep curled on her side hugging a small round cushion; subtle breathing and relaxed wings.'),
    ('sleep_book', '书边睡', 'sleep', 'lie', 'sleep', 'book', 'Sleep on her side with a small closed book resting beside her hands; tiny breathing and still book.'),
    ('sleep_wing_fold', '收翅侧睡', 'sleep', 'lie', 'sleep', 'wing', 'Sleep on her side, both small wings neatly folded behind her back, hands tucked near her chin; soft breathing.'),
    ('sleep_knees', '屈膝睡', 'sleep', 'lie', 'sleep', 'side', 'Sleep on her side with knees tucked and one hand on her own tail, the other under her cheek; subtle breathing.'),
    ('reply_air_phone', '飞着回消息', 'idle', 'air', 'reply', 'joyful', 'Hover at one fixed height with both feet off the ground, holding a blank-screen phone in both hands; tap gently with one thumb and smile.'),
    ('reply_air_note', '飞着想回复', 'idle', 'air', 'reply', 'joyful', 'Hover at one fixed height with both feet off the ground; hold a blank note card, make a small pencil mark, then look at it happily.'),
    ('reply_air_wave', '飞着打招呼', 'idle', 'air', 'reply', 'joyful', 'Hover at one fixed height with both feet off the ground; hold a blank phone in one hand, give a tiny wave with the other, and return it.'),
    ('reply_air_type', '空中打字', 'idle', 'air', 'reply', 'joyful', 'Hover at one fixed height with both feet off the ground; use both thumbs on a small blank-screen phone, then glance up with a bright smile.'),
    ('reply_sit_calm', '坐着慢慢回复', 'laptop', 'sit', 'reply', 'calm', 'Sit on the floor throughout holding a blank-screen phone, gently tap with one thumb, pause and read.'),
    ('reply_sit_sleepy', '困困地回消息', 'laptop', 'sit', 'reply', 'sleepy', 'Sit on the floor throughout, awake with relaxed sleepy eyes, hold a blank-screen phone and make a small thumb tap.'),
    ('reply_sit_note', '坐着斟酌回复', 'laptop', 'sit', 'reply', 'calm', 'Sit on the floor throughout with a blank phone in one hand, touch her chin with the other, then gently tap the screen.'),
    ('reply_sit_laptop', '坐着键盘回复', 'laptop', 'sit', 'reply', 'calm', 'Sit on the floor throughout with a small laptop on her lap, type lightly, pause and read its blank screen.'),
    ('reply_stand_bright', '站着开心回复', 'idle', 'stand', 'reply', 'bright', 'Stand on both feet throughout, hold a blank phone with both hands, type with one thumb and smile softly.'),
    ('reply_stand_quiet', '站着安静回复', 'idle', 'stand', 'reply', 'quiet', 'Stand on both feet throughout, hold a blank phone in one hand, read it and tap once with the other.'),
    ('reply_stand_think', '站着想怎么回', 'idle', 'stand', 'reply', 'quiet', 'Stand on both feet throughout, hold a blank phone, touch her chin briefly, then make a small thumb tap.'),
    ('reply_stand_wave', '回完挥挥手', 'idle', 'stand', 'reply', 'bright', 'Stand on both feet throughout, hold a blank phone, tap it and make a tiny friendly wave with her free hand.'),
]
for _name, _title, _style, _posture, _role, _family, _action in _EXTRA:
    ACTIONS['life_' + _name] = {
        'title': _title, 'prompt': _action + _CONSTRAINTS, 'style': _style,
        'posture': _posture, 'role': _role, 'family': _family,
        'fps': .7 if _role == 'sleep' else 1.0, 'loop': True,
        'scale': .7 if _posture == 'lie' else .8 if _posture == 'sit' else 1.0, 'duration': 8.0,
    }

for _name in ("phone", "note", "wave", "type"):
    ACTIONS["life_reply_air_" + _name]["prompt"] += (
        " While holding the same phone/card and keeping the face, head size, torso, hands and feet "
        "at the exact same positions, BOTH lavender wings perform one clear complete wing beat: "
        "frame 1 wing tips UP beside her head; frame 2 spread HORIZONTALLY; frame 3 tips DOWN "
        "beside her hips; frame 4 spread horizontally on the return stroke. The reference flapping "
        "sheet shows the required phase difference. The wings themselves change angle and shape "
        "strongly enough to read at desktop-pet size. No stationary decorative wings or body zoom."
    )
