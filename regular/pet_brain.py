"""桌宠的大脑：行为、物理和 agent 状态映射。纯计算，不依赖 Qt，也不发任何请求。

外壳（regular/desktop_pet.py）每帧调用 tick()，把鼠标、聊天和面板给的 presence 喂进来，
再读 pose() / particles / bubble 画出来。这里不保存、不改写机器人的任何状态：
群聊心情、作息、群里发生了什么都只读，桌宠只是把小团团「此刻在干嘛」演出来。
warmth 是摸头、喂饼干带来的短暂好心情，有上限、独立冷却，会随时间消退。
正常运行由本机面板持有，脑中只保留表现快照；独立离线脑使用相同纯规则。
它只叠加在只读心情之上，从不写回 presence 或群聊关系。

坐标单位是屏幕逻辑像素（点），(x, y) 是脚底锚点；scale 是人物大小倍率。
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
import math
import random

from regular.pet_care import CARE_GAIN, CARE_COOLDOWN, CARE_MAX, CARE_DECAY, change, decay
from regular.pet_repertoire import Repertoire, clips as repertoire_clips
from regular.pet_props import PropPlay
from regular.pet_routines import ACTIVITY_ENDINGS, DAILY_MAGIC, EPISODES, activity_sequence, choose_routine

FIGURE = 160.0          # 100% 时人物站立高度（点）
WALK_SPEED = 46.0       # 点/秒，随 scale 缩放
GRAVITY = 2600.0
EDGE = 0.35             # 人物离屏幕左右边缘至少留这么多个人物高度
EDGE_PEEK = 0.12        # 脸和抓边的手露在屏内，身子藏到选中边界外
EDGE_COOLDOWN = 240.0

# clip: (strip, frame sequence, fps, loop, posture)
CLIPS = {
    "idle": ("idle", [0], 1, True, "stand"),
    "look_left": ("look", [0], 1, True, "stand"),
    "look_right": ("look", [1], 1, True, "stand"),
    "walk": ("walk", [0, 1, 2, 3, 4, 5], 9, True, "stand"),
    "wave": ("wave", [0, 1, 2, 3, 2, 3, 1], 6, False, "stand"),
    "hop": ("hop", [0, 1, 2, 3], 7, False, "stand"),
    "sit_down": ("sit", [0, 1], 5, False, "sit"),
    "sit_swing": ("sit", [1, 2], 1.6, True, "sit"),
    "stand_up": ("sit", [1, 0], 5, False, "stand"),
    "sit_up": ("wake", [1, 2], 2.2, False, "sit"),
    "doze": ("sleep", [0, 1, 0, 1], 0.8, False, "sit"),
    "lie_down": ("wake", [1, 0], 1.6, False, "lie"),               # slumps, then the eyes fall shut
    "sleep": ("sleep", [2, 3], 0.45, True, "lie"),
    # Waking is acted out: stir, prop up, sit rubbing an eye; then kneel, stand, scratch, yawn.
    "wake": ("wake", [0, 0, 1, 2, 2, 2], 2.4, False, "sit"),
    "grumble": ("wake", [3], 1, False, "sit"),                     # poked awake: a heavy-lidded look first
    "get_up": ("getup", [0, 1, 2, 2, 3, 3], 3, False, "stand"),
    "dangle": ("dangle", [0], 1, True, "air"),
    "fall": ("fall", [0, 1], 7, True, "air"),
    "land": ("fall", [2, 2, 3], 6, False, "stand"),
    "think": ("think", [0, 1], 0.9, True, "stand"),
    "phone": ("phone", [0, 1], 2.2, True, "stand"),
    "talk": ("talk", [0, 1, 2, 1, 3, 1], 3.5, True, "stand"),
    "stretch": ("stretch", [0, 1, 1, 2, 2, 3], 2.6, False, "stand"),
    "wing_stretch": ("wing_stretch", [0, 1, 1, 2, 2, 3, 0], 1.4, False, "stand"),
    "read": ("read", [0, 0, 1, 0, 0, 2, 0, 3], 0.9, True, "sit"),
    "draw": ("draw", [0, 1, 0, 1, 2, 0, 1, 3, 3], 2.6, False, "stand"),
    "drawing": ("draw", [0, 1, 0, 1, 0, 1, 2, 2], 2.6, True, "stand"),   # 小团团正在给群里画：一直画
    "show_drawing": ("draw", [3], 1, False, "stand"),                    # 画好了，举起来给你看
    "sing": ("sing", [0, 1, 2, 3], 2.2, True, "stand"),
    # 退役的大幅动作：抬脚跺地 → 抱臂背身 → 转回来瞪人。素材留着兼容旧存档，
    # 但任何点击、起床、投喂、落地、日常动作都不再触发（见 poke/feed/_physics/_choose）。
    "pout": ("pout", [0], 1.0, False, "stand"),
    "point": ("startle", [2], 1, False, "stand"),
    "shy": ("shy", [0, 1, 2, 3], 2.8, False, "stand"),
    # 只留「吓一跳 + 抬手挡一下」两帧，砍掉转身笑 / 指人的大幅度帧。
    "startle": ("startle", [0, 1, 0, 1], 4.0, False, "stand"),
    "cheer": ("cheer", [0, 1, 2, 3], 3.5, False, "stand"),
    "tea": ("tea", [0, 1, 2, 0, 3, 0], 0.8, True, "sit"),
    "lookaround": ("lookaround", [0, 1, 2, 3], 1.3, False, "stand"),
    "eat": ("eat", [0, 1, 2, 1, 2, 3], 2.6, False, "stand"),
    # One offered cookie uses one sheet in order. Frame 2 has already finished
    # the cookie; returning to frame 1 would put it back in her hands.
    "feed_expect": ("peek", [0, 0, 1, 1, 1], 5.0, False, "stand"),
    "feed_accept": ("eat", [0, 0, 0], 4.0, False, "stand"),
    "feed_bite": ("eat", [1, 1, 1, 1], 4.0, False, "stand"),
    "feed_content": ("eat", [2, 2, 2, 3, 3, 3], 4.0, False, "stand"),
    "laptop": ("laptop", [0, 1, 0, 1, 2, 3], 3.2, True, "sit"),
    # 状态层：她此刻真的在做什么，由面板的实时状态驱动，不是随机挑的卖萌动作。
    # 坐/蹲的 strip 一律标 "sit"，_queue() 会自动插 sit_down / stand_up 桥接，不跳帧。
    "reply": ("reply", [0, 1, 2, 1, 0, 3], 2.0, True, "sit"),       # 坐着回消息，末帧抬头笑
    "listen": ("listen", [0, 1, 2, 3], 2.0, True, "stand"),          # 侧身贴耳听语音
    "inspect": ("inspect", [0, 1, 2, 3], 1.6, True, "stand"),        # 把图片举高端详
    "checkup": ("checkup", [0, 1, 2, 3], 1.6, True, "sit"),          # 蹲下举放大镜做体检
    "handoff": ("handoff", [0, 1, 2, 3], 2.0, True, "stand"),        # 迈步递便签给 Agent
    "stuck": ("stuck", [0, 1, 2, 3], 1.6, True, "stand"),            # 挠头摊手：这项没配好
    # 互动层：只动头、一只手和眼皮，脚不动、不转身、不指人、不生气。
    "pat": ("pat", [0, 1, 2, 3], 1.6, False, "stand"),                # 被摸头，眯眼笑
    "highfive": ("highfive", [0, 1, 2, 3], 1.6, False, "stand"),      # 举着等你击掌
    "peek": ("peek", [0, 1, 2, 3], 1.6, False, "stand"),              # 手背后歪头看你
    "nod": ("nod", [0, 1, 2, 3], 1.6, False, "stand"),                # 轻轻点个头
    # 久挂日常：同一姿势里只动手、眼神和尾巴，慢慢循环。
    "popcorn": ("popcorn", [0, 0, 1, 1, 2, 2, 3, 3], 1.0, True, "sit"),
    "headphones": ("headphones", [0, 1, 0, 2, 0, 3], 0.9, True, "sit"),
    "gamepad": ("gamepad", [0, 0, 1, 0, 2, 0, 3, 0], 1.1, True, "sit"),
    "tail_care": ("tail_care", [0, 1, 0, 2, 0, 3], 0.8, True, "sit"),
    "lie_chin": ("lie_chin", [0, 0, 1, 0, 0, 2, 0, 3], 0.7, True, "lie"),
    "edge_peek": ("edge_peek", [0, 0, 1, 0, 2, 0, 3, 0], 0.9, True, "stand"),
}
CLIPS.update(repertoire_clips())
if "daily_cookie_receive" in CLIPS:
    # Keep one adopted, consistently proportioned actor for the whole cookie.
    for _feed_name, _feed_frame, _feed_count in (("feed_expect", 0, 5), ("feed_accept", 1, 3),
                                               ("feed_bite", 2, 4), ("feed_content", 3, 6)):
        CLIPS[_feed_name] = ("daily_cookie_receive", [_feed_frame] * _feed_count, 4.0, False, "stand")
if "form_recover" in CLIPS:
    # Tidying her hair belongs to every transformation; checking the candy in
    # the later frames belongs only to the candy trick.
    CLIPS["form_tidy"] = ("form_recover", [0, 0, 1, 1, 1], 4.0, False, "stand")

# 法术与特技：一整段表演拆成拍子，位移、法阵和「沉进阵里」的裁切由 _warp / 扑跃物理逐拍调度。
SPELL_CLIPS = {
    "warp_cast": ("spell_cast", [0, 1, 2, 2, 2, 3, 3], 4.5, False, "stand"),   # 抬手 → 划弧 → 按向地面 → 眨眼
    "warp_crouch": ("spell_dive", [0], 1, True, "stand"),
    "warp_leap": ("spell_dive", [1], 1, True, "air"),
    "warp_sink": ("spell_dive", [2, 3], 5, False, "air"),                      # 捏鼻子「冰棍跳」沉下去
    "warp_rise": ("spell_rise", [0], 1, True, "air"),
    "warp_tada": ("spell_rise", [1], 1, True, "air"),
    "warp_land": ("spell_rise", [2, 2, 3, 3, 3, 3], 4, False, "stand"),
    "conjure": ("spell_cast", [0, 1, 1], 4, False, "stand"),
    "paint": ("spell_paint", [0, 1, 0, 1, 2, 2, 3, 1], 2.2, True, "stand"),
    "paint_reveal": ("spell_reveal", [0, 0, 1, 2, 2, 2, 3, 3, 3], 2.6, False, "stand"),
    "tome": ("spell_tome", [0, 1, 1, 1, 2, 1, 1], 1.6, True, "stand"),
    "tome_found": ("spell_tome", [3], 1, False, "stand"),
    "pounce_crouch": ("spell_pounce", [0, 1], 4.5, True, "stand"),
    "pounce_leap": ("spell_pounce", [2], 1, True, "air"),
    "pounce_catch": ("spell_pounce", [3], 1, False, "stand"),
    "sneeze": ("spell_sneeze", [0, 0, 1, 1, 2, 2, 3, 3, 3], 4, False, "stand"),
}
# Without the spell sheets (an older asset folder) the same beats borrow existing strips.
SPELL_FALLBACK = {
    "spell_cast": ("wave", {0: 0, 1: 1, 2: 2, 3: 3}), "spell_dive": ("hop", {0: 0, 1: 1, 2: 2, 3: 2}),
    "spell_rise": ("hop", {0: 2, 1: 2, 2: 3, 3: 3}), "spell_paint": ("draw", {0: 0, 1: 1, 2: 2, 3: 1}),
    "spell_reveal": ("draw", {0: 2, 1: 3, 2: 3, 3: 3}), "spell_tome": ("think", {0: 0, 1: 1, 2: 0, 3: 1}),
    "spell_pounce": ("hop", {0: 0, 1: 0, 2: 2, 3: 3}), "spell_sneeze": ("startle", {0: 0, 1: 1, 2: 0, 3: 1}),
}
for _name, (_strip, _seq, _fps, _loop, _posture) in SPELL_CLIPS.items():
    if _strip not in CLIPS:
        _strip, _frames = SPELL_FALLBACK[_strip]
        _seq = [_frames[i] for i in _seq]
    CLIPS[_name] = (_strip, _seq, _fps, _loop, _posture)

# Transition clips start from another posture than the one they end in.
STARTS_FROM = {"sit_down": "stand", "stand_up": "sit", "doze": "sit", "lie_down": "sit", "wake": "lie",
               "get_up": "sit", "sit_up": "lie", "lie_chin": "sit",
               "land": "air", "fall": "air", "dangle": "air"}
BRIDGES = {("stand", "sit"): ["sit_down"], ("stand", "lie"): ["sit_down", "doze", "lie_down"],
           ("sit", "stand"): ["stand_up"], ("sit", "lie"): ["doze", "lie_down"],
           ("lie", "sit"): ["wake"], ("lie", "stand"): ["wake", "get_up"],
           ("sit", "air"): ["stand_up"], ("lie", "air"): ["wake", "get_up"]}
ASLEEP = frozenset({"sleep", "doze", "lie_down"} | {
    name for name, spec in Repertoire(random.Random()).actions.items() if spec["role"] == "sleep"
})
WAKING = frozenset({"wake", "grumble", "get_up"})
SLOW_BLEND = ASLEEP | WAKING | {"sit_up"}  # 起床 / 入睡演出允许在换动作时叠画，普通动作不叠
# Seconds a new frame dissolves in from the previous one. Only slow and posture-changing clips blend:
# blinks, walking, hops and a startled flinch stay crisp. Between two clips the longer blend wins.
BLEND = {"wake": 0.22, "grumble": 0.2, "get_up": 0.16, "lie_down": 0.25, "doze": 0.3, "sleep": 0.5,
         "sit_down": 0.1, "stand_up": 0.1, "sit_swing": 0.25, "read": 0.2, "tea": 0.25, "laptop": 0.12,
         "stretch": 0.12, "reply": 0.15, "checkup": 0.15,
         "pat": 0.18, "highfive": 0.16, "peek": 0.18, "nod": 0.18,
         "sit_up": 0.2, "lie_chin": 0.25, "popcorn": 0.2, "headphones": 0.2,
         "gamepad": 0.2, "tail_care": 0.2}
# Clips a coding agent may request through the panel; physics/transition clips stay internal.
# pout / point 已从可请求列表移除：那套「跺脚 + 背身 + 指人」不再出现在任何交互里。
CUE_ACTIONS = ("wave", "hop", "cheer", "talk", "think", "phone", "laptop", "draw", "sing", "read", "tea",
               "stretch", "lookaround", "shy", "startle", "eat", "sleep",
               "reply", "listen", "inspect", "checkup", "handoff", "stuck",
               "pat", "highfive", "peek", "nod",
               "popcorn", "headphones", "gamepad", "tail_care", "lie_chin", "wing_stretch")
# 主动互动池：每一下都只换一个小幅姿势，且不与上一次重复。
PLAY = {
    "pat": ("摸摸头", ("嘿嘿，舒服。", "再摸一下也行。", "（眯起眼睛）")),
    "highfive": ("击个掌", ("击掌！", "来，啪～", "手给我！")),
    "peek": ("歪头看你", ("在看什么呀？", "嗯？", "我在呢。")),
    "nod": ("点个头", ("好呀。", "收到～", "嗯嗯。")),
    "shy": ("害羞一下", ("嘿嘿。", "别看啦～", "有点不好意思。")),
    "think": ("想一想", ("让我想想……", "嗯……", "有主意了！")),
    "wave": ("挥挥手", ("嗨～", "在这儿呢！", "你来啦。")),
    "lookaround": ("看看四周", ("刚刚有动静？", "东张西望一下。", "没谁呀。")),
}
TAP_POOL = tuple(PLAY)
# 「玩一下」是一局击掌接龙，不是再抽一个小动作：她举手，你得在她收手前点到她。
PLAY_WINDOW = 2.4                  # 第一下的反应时间
PLAY_MIN_WINDOW = 1.05            # 连击越快，窗口越短，但不会短到没法玩
PLAY_SPEEDUP = 0.16
PLAY_GAP = 0.55                   # 接住之后她重新抬手前的空档
PLAY_MAX_STREAK = 8               # 一口气接满就收工，别让她一直举着手
PLAY_START = ("来，击个掌！", "手举着呢，快点～", "接住这一下！")
PLAY_AGAIN = ("又来了！", "还有！", "接着～")
PLAY_HIT = ("啪！接住了。", "手挺快啊。", "嘿，接得漂亮。")
PLAY_STREAK = ("连着 {n} 下！", "{n} 连了，再来？", "越来越快了，{n} 下！")
PLAY_BEST = ("新纪录：{n} 连！", "破纪录啦，{n} 下！")
PLAY_MISS = ("慢了半拍～", "手缩回来啦。", "这下没接着，再来？")
PLAY_OVER = ("这一轮连了 {n} 下，最高 {m}。", "收工！{n} 连，纪录 {m}。")
PLAY_WAIT = ("别急，手还没举起来呢～", "等一下下～")
PLAY_CANCEL = "先聊，回头接着玩。"
# 被照顾的心情：摸头和喂饼干真的会让她高兴一阵，但刷不出满心情，也会自己消退。
# presence.mood 仍是只读统计；照顾加成由本机面板与显示端共享，不改写它。
CARE_LINES = {
    "pat": ("今天心情很好。", "嘿嘿，被你摸得开心了。", "（往你手心里蹭了蹭）"),
    "feed": ("吃饱了，心情也好了。", "这一块最香。", "（眯着眼嚼完）"),
}
CARE_COOL_LINES = {
    "pat": ("刚摸过啦～", "还痒着呢。"),
    "feed": ("刚吃过一块啦。", "留着下回再吃～"),
}
CARE_FULL_LINES = ("今天开心够啦～", "够啦够啦，先歇会儿。")
# 状态层：面板每次心跳带来的是「现在」的状态，不是一件事发生过。
# STATE_HEARTBEAT 是心跳丢失后还能撑多久；状态一停她就会自己回到日常，不用长超时假装忙。
STATE_CLIPS = {"reply": "reply", "listen": "listen", "inspect": "inspect",
               "checkup": "checkup", "handoff": "handoff", "stuck": "stuck"}
STATE_HEARTBEAT = 12.0
STATE_ONCE = {"handoff": 6.0, "stuck": 6.0, "checkup": 8.0}   # 一次性动作不占着状态不放
NIGHT = ("凌晨被吵醒", "熬到半夜")
# 小团团在 QQ 里查资料 / 画图 / 录语音时，桌宠最多陪着演多久（查询五分钟放弃；生图超时 150 秒，再加上传）。
MEDIA_HOLD = {"searching": 300.0, "drawing": 200.0, "singing": 90.0}
# 群里叫到她的几种方式：平台 @、正文里喊名字、引用她的话；旧版机器人只知道「叫到了」。
CALLS = ("mention", "named", "quoted", "called")
# 她就是小团团：群里刚有她的动静（被@、在回、刚说完、在画……），这几分钟里不会挑打盹。
ENGAGED_SECONDS = 180.0
BUSY_CLIP = {"searching": "laptop", "drawing": "drawing", "singing": "sing"}
PORTAL_RADIUS = 0.5       # 法阵半径（人物高度的倍数）；地上的阵按透视压扁成椭圆
PORTAL_TILT = 0.3         # 椭圆短轴 / 长轴
WARP_COOLDOWN = 75.0      # 日常里两次传送至少隔这么久；被 @ 时的「这就过去」另算
POUNCE_COOLDOWN = 20.0
# 一段小剧场的结果拍各配一句；陪伴长驻那几拍保持安静。
BEAT_LINES = {
    "dragon_fire_kettle": "烧点热水～", "life_tea_sip": "刚刚好。", "dragon_treasure_find": "找到啦。",
    "life_comic_amused": "这页好玩。", "dragon_garden_sprout": "冒芽了。",
    "scene_sweet_caught": "……被你看到了。", "scene_sweet_share_more": "大的这半给你。",
    "scene_fire_startled": "哇——太大了！", "scene_fire_recover": "……刚才那是特效。",
    "scene_tea_hot": "烫烫烫！", "scene_game_win": "赢啦！", "scene_game_loss": "就差一点……",
    "scene_game_rematch": "再来一局！", "scene_paint_proud": "画好啦，看！", "scene_paint_rework": "这笔歪了。",
    "scene_paint_cheek": "……脸上也画到了。", "scene_sleepy_cover": "我、我在看书。",
    "scene_diary_hide": "不许偷看！", "scene_glass_hello": "你好呀～",
    "scene_height_nervous": "……刚才好高。", "scene_height_wings": "没、没有怕。",
}
DRAGON_COOLDOWN = 1500.0  # 变小龙是偶遇的惊喜：日常里至少隔二十几分钟
FURNITURE_COOLDOWN = 1800.0
SHOW_OFF_COOLDOWN = 300.0
# 被你拎到高处再松手：会飞，但不是自己飞上去的，落地后腿会软一下。
HEIGHT_FEAR_DROP = 1.6          # 至少比自己高出这么多个身位才算「高」
HEIGHT_FEAR_COOLDOWN = 240.0
AIR_FIRE_COOLDOWN = 240.0
AIR_CHASE_COOLDOWN = 60.0
AIR_CHASE_SECONDS = 3.0
AIR_CHASE_RANGE = 2.5             # figure heights from the takeoff / invitation point
AIR_HAND = (38 / 320, -158 / 320)  # adopted reach frame 2, relative to the feet anchor
AIR_CATCH_RADIUS = 0.16
# 小龙形态的一段：变身后做哪件事、做完变回来时说什么
DRAGON_TRICKS = {
    "form_dragon_flap": ("飞给你看——", "……飞行还要再练练。"),
    "form_dragon_reach": ("糖！", "还是手好用。"),
    "form_dragon_guard": ("这些都是我的。", "宝贝都还在。"),
    "form_dragon_tail_chase": ("尾巴别跑！", "晕……先变回来缓缓。"),
}

LINES = {
    "mention": ("有人@我！", "群里有人@我，我去看看。", "被@了，来了来了——"),
    "named": ("嗯？好像有人在说我。", "群里有人喊我名字～", "听到有人叫我了。"),
    "quoted": ("有人引用了我刚才那句。", "我刚说的话被翻出来了。"),
    "called": ("有人在群里叫我！", "来了来了——"),
    "said": ("（在群里回了一句）", "（刚回完群里的话）"),
    "chimed": ("（刚在群里插了句嘴）", "（没人叫我，我也接了一句）"),
    "searching": ("我去查查～", "查资料中……", "等我搜一下。"),
    "proactive": ("（在群里开了个话头）", "（主动在群里冒了个泡）"),
    "drawing": ("群里有人要画，我画画看～", "在画了在画了。", "让我想想怎么画……"),
    "drew": ("画好啦，发群里了！", "给群里画了张图！"),
    "singing": ("清清嗓子——", "我去群里唱两句。", "录条语音～"),
    "voice": ("（在群里发了条语音）", "唱完啦～"),
    "offline": ("机器人没在运行，我先在这里陪你。",),
    "online": ("连上 QQ 啦。", "我回来了。"),
    "poke": ("嗯？", "在呢。", "怎么啦？"),
    "poke_again": ("又戳～", "我还在这儿呢。", "嘿嘿。"),
    "poked_more": ("连着戳呀。", "好好好，我在。", "陪你玩会儿？"),
    "woken": ("唔……谁呀。", "刚睡着……", "醒啦。"),
    "waking": ("醒了醒了……", "在起了在起了。"),
    "petted": ("嘿嘿。", "再摸一下也行。", "舒服——"),
    "tapped_feet": ("痒！", "脚脚不许戳～", "嘿嘿。"),
    "lifted": ("哇哇——", "放我下来！", "要去哪儿？"),
    "thrown": ("晕……", "轻点嘛！"),
    "bonk": ("哎哟！", "撞到头了……", "咚！"),
    "fed": ("好吃！", "谢谢投喂～", "嚼嚼嚼。"),
    "full": ("吃不下啦。", "再吃就要变成球了。"),
    "greet": ("嗨～", "在忙吗？", "我在这儿哦。"),
    "late": ("好困……", "这么晚还不睡？"),
    "reply": ("群里有人找我，我回一句～", "打字中……", "回好啦。"),
    "listen": ("我听着呢，你说。", "嗯嗯，在听。"),
    "inspect": ("这张图我看看。", "举高点瞧瞧……"),
    "checkup": ("翻翻今天接的话，看看有没有接偏。", "体检一下～"),
    "handoff": ("这段交给 Agent 看看吧。", "我记下来了，给你。"),
    "stuck": ("这项好像还没配好……", "咦，没连上？"),
    "warp": ("看我的——", "走，换个地方。", "嘿咻——"),
    "warp_in": ("噔噔！", "到啦～", "我在这儿！"),
    "warp_sky": ("从天而降！", "哇——接住我！"),
    "warp_called": ("有人@我？这就过去——", "叫我？传送过去！"),
    "sneeze": ("哈啾！", "阿嚏——", "（揉鼻子）"),
    "sneeze_warp": ("……咦？我怎么在这儿。", "打个喷嚏就传送了？？"),
    "pounce_caught": ("抓到啦！", "嘿，逮住了！", "（捧着手偷看）"),
    "pounce_missed": ("诶，跑了……", "差一点！", "再晃一下试试？"),
    "found": ("找到了！", "查到啦。"),
    "dragon": ("变身！", "嘿——小龙形态！", "看我变～"),
    "show_off": ("被夸了就要露一手。", "看我的～"),
    "share": ("……分你一半。", "喏，这半块给你。"),
}


@dataclass
class Step:
    clip: str
    duration: float | None = None      # None: one pass of a non-loop clip (loops default to 4s)
    target_x: float | None = None      # walking destination
    target_y: float | None = None      # controlled flight destination
    hold: str | None = None            # "chat" / "agent" / "state": held until that condition clears
    edge_side: int = 0                 # -1 / +1: approach, peek and retreat at this screen edge
    line: str | None = None            # short, beat-specific speech


@dataclass
class Particle:
    kind: str
    x: float          # relative to the feet anchor, in figure heights
    y: float
    vx: float
    vy: float
    born: float
    life: float


@dataclass
class Portal:
    """A magic circle on the floor or just under the menu bar; the shell paints it in its own window."""
    id: int
    x: float           # centre, screen points
    y: float           # the plane she sinks through
    side: str          # "floor": she sinks below y / rises from below; "ceiling": she drops out below y
    radius: float      # half the long axis, points
    born: float
    closing: float | None = None
    flare: float = -10.0      # last time she passed through (bright pulse and light column)

    def openness(self, now):
        opening_until = now if self.closing is None else min(now, self.closing)
        opened = max(0.0, min(1.0, (opening_until - self.born) / 0.45))
        if self.closing is None:
            return opened
        return opened * max(0.0, 1.0 - (now - self.closing) / 0.5)

    def gone(self, now):
        return self.closing is not None and now - self.closing >= 0.5


@dataclass
class Bubble:
    text: str
    kind: str          # say / think / status
    until: float
    serial: int


@dataclass
class Presence:
    available: bool = False
    qq_connected: bool = False
    bot: str = "unknown"        # ready / offline / unknown
    mood: float = 0.0
    circadian: str = ""
    chatter: int = 0
    known: bool = False


@dataclass
class _Pointer:
    x: float = 0.0
    y: float = 0.0
    near: bool = False
    since: float = 0.0
    reversals: deque = field(default_factory=lambda: deque(maxlen=8))
    last_dx: float = 0.0
    seen: bool = False             # the shell has reported a real cursor position at least once


class PetBrain:
    def __init__(self, *, rng=None, now=0.0, motion=True, quiet=False, scale=1.0):
        self.rng = rng or random.Random()
        self.repertoire = Repertoire(self.rng)
        self.props = PropPlay(self.rng)
        self._prop_at = now - 180.0
        self._last_story_at = now
        self._rests_since_story = 0
        self._furniture_at = now - FURNITURE_COOLDOWN
        self._last_trick_at = now - 120.0
        self._prop_voice_at = now - 5.0
        self._prop_check_at = now
        self._next_pending = False
        self._move_origin = (0.0, 0.0)
        self._move_duration = 1.0
        self._flight_vx = 0.0
        self._hide_origin = (0.0, 0.0)
        self._hide_started = now
        self._hide_duration = 1.0
        self._nap_at = now - 480.0
        self.bottom_peek = False
        self._state_switch_at = now
        self._ceiling_until = 0.0
        self.hidden_edge = None
        self._hidden_clip = None
        self._hidden_awake_clip = None
        self.hidden_asleep = False
        self._hidden_gaze = 1
        self._hidden_gaze_candidate = 1
        self._hidden_gaze_since = now
        self._hidden_blink_started = -10.0
        self._hidden_blink_at = now + 4.0
        self._hidden_since = now
        self._hidden_sleep_at = float("inf")
        self._hidden_micro_at = float("inf")
        self._hidden_side_y = None
        self._hidden_sleep_started = 0.0
        self._hidden_slip_started = None
        self._hidden_slip_check_at = float("inf")
        self._hidden_slip_at = now - 600.0
        self._edge_exit = None
        self.top_peek = False
        self._poof_at = -10.0
        self._dragon_at = now - DRAGON_COOLDOWN + 600.0   # 刚启动的十分钟里不变身
        self._dragon_recent: deque = deque(maxlen=2)
        self._magic_used = set()
        self._magic_dragon_shown = False
        self._show_off_at = now - SHOW_OFF_COOLDOWN
        self._height_fear_at = now - HEIGHT_FEAR_COOLDOWN
        self._air_top: float | None = None    # 被松手后飞得最高的位置，只为落地后那一下腿软
        self._sleep_cue_until = 0.0
        self._prop_chase_at = now - 120.0
        self._chasing_prop = None
        self.motion, self.quiet, self.scale = motion, quiet, scale
        self.left, self.right, self.floor = 0.0, 1280.0, 800.0
        self.ceiling = 0.0               # top of the usable screen (below the menu bar)
        self.x, self.y = 640.0, 800.0
        self.vx = self.vy = 0.0
        self.facing = 1
        self.mode = "ground"            # ground / drag / air
        self.plan: deque[Step] = deque()
        self.step: Step | None = None
        self.clip, self.clip_started, self.step_started = "idle", now, now
        self.squash_started = self.hop_started = self.jolt_started = -10.0
        self.swing = self.swing_v = 0.0
        self.blink_started, self.blink_at = -10.0, now + 2.0
        self.particles: list[Particle] = []
        self._emit_at = 0.0
        self.bubble: Bubble | None = None
        self._serial = 0
        self.presence = Presence()
        self._seen_events: deque = deque(maxlen=256)
        self._last_status_line = -1000.0
        self._last_lines: dict[str, float] = {}
        self.chat_pending = False
        self.agent_busy_until = 0.0
        self.last_agent_event = -1000.0
        self.media: str | None = None    # laptop / drawing / sing while 小团团 searches, draws or records in QQ
        # 状态层：本地对话优先，其余跟面板心跳；都高于日志事件与随机 ambient。
        self.state_clip: str | None = None
        self.state_name: str | None = None
        self.state_until = 0.0
        self.state_source: str | None = None  # panel heartbeat / one-shot event / local conversation
        self.energy = 1.0
        self.last_interaction = now
        self.pokes: deque = deque(maxlen=6)
        self._shown = (None, None, None, None)   # strip, frame, mirrored, clip on screen
        self._fade = None                        # (strip, frame, mirrored, started, seconds) dissolving out
        self.feeds: deque = deque(maxlen=6)
        self._feed_waiting = False
        self.warmth = 0.0                # 被照顾出来的短暂好心情，独立于只读的群聊心情
        self._warmth_at = now
        self._care_at = {"pat": -1000.0, "feed": -1000.0}
        self.external_care = False
        self.care_requests = deque(maxlen=1)
        self._care_snapshot_at = float("-inf")
        self._last_tap_clip: str | None = None     # 连点不重样
        self._tap_bag: list[str] = []             # 洗牌袋：一轮里不重复
        self._ambient_recent: deque = deque(maxlen=3)  # 日常小剧场不紧接着重复最近三轮
        self._ambient_families: deque = deque(maxlen=3)
        self._edge_at = -EDGE_COOLDOWN
        self._last_tap_at = -1000.0
        self._last_play_at = -1000.0
        self.game_active = False           # 一整局连点是否还在进行
        self.game_until = 0.0              # 当前这一下的截止时刻，0 = 手没举着
        self.game_next_at = 0.0            # 接住之后自动抬下一手的时刻
        self.game_streak = 0
        self.game_best = 0
        self.pointer = _Pointer()
        self.attention = False
        self._greeted_at = -1000.0
        self._grab = None
        self._press = None
        self._now = now
        # 法阵与传送：portals 由显示端各开一个透明窗口画；portal_clip 是「沉进阵里」时要藏起来的那一侧。
        self.portals: list[Portal] = []
        self._portal_serial = 0
        self.warp: dict | None = None
        self._warp_request: dict | None = None
        self.portal_clip: tuple[float, str] | None = None
        self._warp_at = now - 600.0
        self._warp_stumbled_last = False
        self._pounce_at = now - POUNCE_COOLDOWN
        self._pounce_target: tuple[float, float] | None = None
        self._landing: str | None = None
        self._jiggles: deque = deque(maxlen=8)
        self._jiggle_dir = (0.0, 0.0)
        self._air_fire_at = now - AIR_FIRE_COOLDOWN
        self._air_chase_at = now - AIR_CHASE_COOLDOWN
        self._air_chase = None
        self._air_returning = False
        self._air_jiggles: deque = deque(maxlen=8)
        self._air_jiggle_dir = (0.0, 0.0)
        self._sneezed_at = -1000.0
        self._sneeze_warp = False
        self._sneeze_slip = False
        self._spark_at = 0.0

    # ── geometry ──
    @property
    def figure(self):
        return FIGURE * self.scale

    @property
    def air_play_active(self):
        """The shell lets clicks pass through while she plays with the cursor."""
        return self._air_chase is not None or self._air_returning or self._role(self.clip) == "air_play"

    def set_ground(self, left, right, floor, ceiling=None):
        self.left, self.right, self.floor = float(left), float(right), float(floor)
        if ceiling is not None:
            self.ceiling = float(ceiling)
        if self.mode == "warp":
            return                       # mid-spell she may be under the floor plane on purpose
        if self.mode == "hidden" and self.hidden_edge:
            if self._hidden_slip_started is None:
                self.x, self.y = self._hidden_target()
            return
        self.x = self._clamp_x(self.x)
        if self.mode == "edge_exit":
            return
        if self.mode == "ground":
            self.y = self.floor
        else:
            self.y = min(self.y, self._bottom_target() if self.bottom_peek else self.floor)

    @property
    def edge_side(self):
        if self.mode in ("hiding", "hidden"):
            return -1 if self.hidden_edge == "left" else 1 if self.hidden_edge == "right" else 0
        if self.mode == "edge_exit" and self._edge_exit:
            return -1 if self._edge_exit["edge"] == "left" else 1 if self._edge_exit["edge"] == "right" else 0
        return self.step.edge_side if self.mode == "ground" and self.step is not None else 0

    def _clamp_x(self, x, *, edge_side=None):
        side = self.edge_side if edge_side is None else edge_side
        left_margin = self.figure * (EDGE_PEEK if side == -1 else EDGE)
        right_margin = self.figure * (EDGE_PEEK if side == 1 else EDGE)
        if self.right - self.left <= left_margin + right_margin:
            return (self.left + self.right) / 2
        return max(self.left + left_margin, min(x, self.right - right_margin))

    def place(self, x):
        self.x, self.y, self.mode = self._clamp_x(float(x)), self.floor, "ground"
        self._air_chase, self._air_returning = None, False
        self._air_top = None

    def posture(self):
        return CLIPS[self.clip][4]

    def body_rect(self):
        """Visible character box (left, top, right, bottom) in screen points."""
        f, posture = self.figure, self.posture()
        height = f * (0.66 if posture == "sit" else 0.48 if posture == "lie" else 1.0)
        width = f * (1.0 if posture == "lie" else 0.62)
        lift = self._hop_offset(self._now)
        if self.clip == "dangle":
            return (self.x - width / 2, self.y - f * 1.05, self.x + width / 2, self.y)
        left, right = self.x - width / 2, self.x + width / 2
        if self.edge_side:
            left, right = max(self.left, left), min(self.right, right)
        return (left, self.y - height + lift, right, self.y + lift)

    # ── clip plumbing ──
    def _set_clip(self, clip, now, *, restart=False):
        if clip == self.clip and not restart:
            return
        if self._form(clip) != self._form():
            self._poof(now)               # a puff of cloud hides the swap between her two forms
        self.clip, self.clip_started = clip, now
        if clip == "hop":
            self.hop_started = now
        elif clip == "land":
            self.squash_started = now

    def _clip_done(self, now):
        _, seq, fps, loop, _ = CLIPS[self.clip]
        return not loop and (now - self.clip_started) * fps >= len(seq)

    def pose(self, now=None):
        """(strip, frame, mirrored, transform) for the renderer; transform["fade"] is the frame dissolving out."""
        now = self._now if now is None else now
        strip, frame, mirrored = self._frame(now)
        transform = self.transform(now)
        transform["fade"] = self._fading(now)
        return strip, frame, mirrored, transform

    def _frame(self, now):
        strip, seq, fps, loop, _ = CLIPS[self.clip]
        index = int(max(0.0, now - self.clip_started) * fps)
        frame = seq[index % len(seq) if loop else min(index, len(seq) - 1)]
        if self.mode == "edge_exit" and self._edge_exit and self._edge_exit.get("phase") == "wake":
            waking = self._edge_exit.get("wake_sequence")
            if waking:
                frame = waking[min(int(max(0.0, now - self._edge_exit["since"]) * 5), len(waking) - 1)]
            elif self.clip in ("motion_edge_look", "motion_bottom_look", "boundary_top_look"):
                frame = seq[1]             # meet the viewer, not frame zero's sideways glance
        elif (self.mode == "edge_exit" and self._edge_exit and self._edge_exit["edge"] == "top"
              and self._edge_exit["phase"] == "prepare" and self.clip in ("boundary_top_look", "boundary_top_peek")):
            frame = seq[1 if self.clip == "boundary_top_look" else 0]
        elif self.mode == "edge_exit" and self.clip == "boundary_top_return":
            frame = seq[min(int(max(0.0, now - self.clip_started) * 6), len(seq) - 1)]
        elif self.mode == "edge_exit" and self._edge_exit and self._role(self.clip) == "edge_launch":
            if self._edge_exit["phase"] == "prepare":
                frame = seq[0]
            elif self._edge_exit["phase"] == "flight":
                frame = seq[min(1 + int(max(0.0, now - self._edge_exit["since"]) * 7), len(seq) - 1)]
        elif self.mode == "hidden" and self.hidden_asleep and self.clip != "boundary_side_slip":
            if self._role(self.clip) != "edge_sleep":
                frame = seq[-1]
            else:
                spec = self.repertoire.actions.get(self.clip, {})
                entry, entry_fps = spec.get("entry_sequence"), spec.get("entry_fps")
                if entry and entry_fps:
                    elapsed = max(0.0, now - self.clip_started)
                    entry_seconds = len(entry) / entry_fps
                    frame = (entry[min(int(elapsed * entry_fps), len(entry) - 1)] if elapsed < entry_seconds
                             else seq[int((elapsed - entry_seconds) * fps) % len(seq)])
        elif self.mode == "hidden" and not self.hidden_asleep and self.clip in (
                "motion_edge_look", "motion_bottom_look", "boundary_top_peek", "boundary_top_look"):
            if 0 <= now - self._hidden_blink_started < 0.16:
                frame = seq[2 if self.clip == "boundary_top_peek" else 3]
            elif self.clip != "boundary_top_peek":
                index = 2 - self._hidden_gaze if self.facing < 0 else self._hidden_gaze
                frame = seq[index]
            else:
                frame = seq[0]
        mirrored = self.facing < 0
        if self.clip == "idle" and self.motion:
            t = now - self.blink_started
            if 0 <= t < 0.05 or 0.13 <= t < 0.2:
                frame = 1
            elif 0.05 <= t < 0.13:
                frame = 2
        elif self.clip == "dangle":
            # Legs trail behind the drag direction; frames 1/2 are drawn trailing left/right.
            frame = 1 if self.swing > 8 else 2 if self.swing < -8 else (0 if int(now * 1.2) % 3 else 3)
            mirrored = False
        elif self.clip in ("look_left", "look_right"):
            mirrored = False
        return strip, frame, mirrored

    def _track_frame(self, now):
        """Dissolve only when the CLIP changes; frames inside one strip stay crisp.

        Blending every frame change used to draw two half-transparent copies of her
        on top of each other, so the whole figure visibly dimmed and grew a ghost
        outline several times per gesture. A dissolve is for posture changes, not
        for the ordinary frames of an animation.
        """
        shown = self._frame(now) + (self.clip,)
        old = self._shown
        if shown[:3] == old[:3]:
            return
        self._shown = shown
        changed_clip = old[3] is not None and old[3] != self.clip
        if not changed_clip:
            return                         # let a dissolve already in flight finish
        # Only posture changes and the deliberately slow waking / falling-asleep
        # clips dissolve. Two standing poses on top of each other just make a
        # double outline; that was the source of the visible image flicker.
        needs_dissolve = (CLIPS[old[3]][4] != CLIPS[self.clip][4]
                          or old[3] in SLOW_BLEND or self.clip in SLOW_BLEND)
        if not self.motion or self.mode != "ground" or not needs_dissolve:
            self._fade = None
            return
        seconds = max(BLEND.get(old[3], 0.0), BLEND.get(self.clip, 0.0))
        self._fade = (old[0], old[1], old[2], now, seconds) if seconds else None

    def _fading(self, now):
        """(strip, frame, mirrored, opacity) of the outgoing frame while it dissolves, else None."""
        if self._fade is None:
            return None
        strip, frame, mirrored, started, seconds = self._fade
        k = (now - started) / seconds
        if not 0 <= k < 1:
            return None
        return strip, frame, mirrored, 1 - k * k * (3 - 2 * k)

    def fading(self, now=None):
        return self._fading(self._now if now is None else now) is not None

    def transform(self, now):
        sx = sy = 1.0
        rot = 0.0
        if self.motion:
            if self.mode == "drag":
                rot = 0.0 if self._grab and self._grab.get("boundary") else self.swing
            elif self.mode == "air":
                rot = max(-16.0, min(16.0, self.vx * 0.01))
            elif self.mode == "fly":
                # Bank with actual travel, including powered wingbeats and a
                # cursor chase. A stationary reply/reach stays upright.
                rot = max(-12.0, min(12.0, self._flight_vx / max(self.scale, 0.01) * 0.12))
        return {"sx": sx, "sy": sy, "rot": rot, "dy": self._hop_offset(now) + self._jolt_offset(now),
                "pivot": "hands" if self.clip == "dangle" else "feet"}

    def _jolt_offset(self, now):
        t = now - self.jolt_started
        if not self.motion or not 0 <= t < 0.26:
            return 0.0
        return -math.sin(t / 0.26 * math.pi) * self.figure * 0.06

    def _hop_offset(self, now):
        if self.motion and self.clip == "form_dragon_hop":
            fps = CLIPS[self.clip][2]
            phase = ((now - self.clip_started) * fps / 4) % 1.0 * 2 - 0.5   # airborne over frames 1-2
            return -4 * phase * (1 - phase) * self.figure * 0.12 if 0 <= phase <= 1 else 0.0
        if self.motion and self.clip == "form_dragon_flap":
            p = (now - self.clip_started - 0.75) / 1.5      # the flapping frames lift her, then she plops
            return -4 * p * (1 - p) * self.figure * 0.18 if 0 <= p <= 1 else 0.0
        if not self.motion or self.clip != "hop":
            return 0.0
        t = now - self.hop_started - 1 / 7      # airborne during frames 1-2
        if 0 <= t <= 2 / 7:
            p = t / (2 / 7)
            return -4 * p * (1 - p) * self.figure * 0.32
        return 0.0

    # ── planning ──
    def _end_posture(self):
        if self.plan:
            return CLIPS[self.plan[-1].clip][4]
        return CLIPS[self.step.clip][4] if self.step is not None else self.posture()

    def _queue(self, *steps, now, interrupt=False):
        """Append steps, inserting sit/stand/lie transitions so poses never jump."""
        interrupted_air_play = interrupt and self.air_play_active
        if interrupt and self.mode == "ground" and self.warp is not None:
            request = self._warp_request if any(s.clip == "warp_cast" for s in steps) else None
            self._cancel_warp(now)
            self._warp_request = request  # Cancel the old cast, not a newly requested destination.
        if interrupt and self.mode == "warp":
            # A spell in flight finishes first (she may be under the floor); the new steps follow it.
            self._chasing_prop = None
            self.plan.clear()
            interrupt = False
        if interrupt:
            if self._warp_request and self._warp_request.get("cause") == "prop" and not any(s.clip == "warp_cast" for s in steps):
                self._warp_request = None
            self._air_chase = None
            self._air_returning = interrupted_air_play and self.mode == "fly"
            self._chasing_prop = None
            if not any(step.hold == "sleep" for step in steps):
                self._sleep_cue_until = 0.0
            if self.mode == "ground":
                target = self._clamp_x(self.x, edge_side=0)
                if abs(target - self.x) > 0.001:
                    # 先走回普通安全边界，再做新动作；撤掉扒边标记不能让人物突然弹回。
                    side = -1 if self.x < target else 1
                    steps = (Step("walk", target_x=target, edge_side=side), *steps)
            elif self.mode in ("fly", "bottom"):
                descent = self._air_return_steps() if self.mode == "fly" else [Step(self.clip, target_y=self.floor)]
                steps = (*descent, *steps)
            self.plan.clear()
            self.step = None
        previous = self.plan[-1].clip if self.plan else self.step.clip if self.step else self.clip
        posture = self._end_posture()
        for step in steps:
            start = self.repertoire.actions.get(step.clip, {}).get(
                "starts_from", STARTS_FROM.get(step.clip, CLIPS[step.clip][4]))
            if step.clip == "lie_chin" and posture == "lie":
                start = "lie"
            bridges = BRIDGES.get((posture, start), [])
            if previous == "lie_chin" and start in ("sit", "stand"):
                bridges = ["sit_up"] + (["stand_up"] if start == "stand" else [])
            for bridge in bridges:
                self.plan.append(Step(bridge, edge_side=step.edge_side))
            self.plan.append(step)
            posture = CLIPS[step.clip][4]
            previous = step.clip
        if interrupt:
            self._advance(now)

    def _advance(self, now):
        self.step = self.plan.popleft() if self.plan else None
        self.step_started = now
        self._flight_vx = 0.0
        if self.mode == "ground" and not self.edge_side:
            self.x = self._clamp_x(self.x)
        if self.step is None:
            if self.posture() == "stand":
                self._set_clip("idle", now)
            return
        if self._role(self.step.clip) == "bottom":
            self.bottom_peek = True
        if self.step.hold == "air_return" and self.step.target_x is None and not self.bottom_peek:
            # A chase may have travelled since this return leg was planned.
            offset = self.figure * 0.55
            target = self._clamp_x(self.x + self.facing * offset, edge_side=0)
            if abs(target - self.x) < self.figure * 0.2:
                target = self._clamp_x(self.x - self.facing * offset, edge_side=0)
            self.step.target_x = target
        if self.step.target_y is not None:
            self.mode = "bottom" if self.bottom_peek else "fly"
            self._move_origin = (self.x, self.y)
            dx = (self.step.target_x - self.x) if self.step.target_x is not None else 0.0
            dy = self.step.target_y - self.y
            self._move_duration = max(0.35, math.hypot(dx, dy) / max(1.0, 85.0 * self.scale))
            if self._role(self.step.clip) == "air_play" and not CLIPS[self.step.clip][3]:
                # Pace the short route to the full performance, not the reverse.
                self._move_duration = len(CLIPS[self.step.clip][1]) / CLIPS[self.step.clip][2]
        if self.step.target_x is not None:
            self.facing = 1 if self.step.target_x >= self.x else -1
        elif self.step.edge_side and (self.step.clip == "edge_peek" or self._role(self.step.clip) == "edge"):
            self.facing = -self.step.edge_side
        keep_wingbeat = (self.mode == "fly" and self.step.clip == self.clip and CLIPS[self.clip][3]
                         and self.repertoire.actions.get(self.clip, {}).get("family") == "flap")
        self._set_clip(self.step.clip, now, restart=not keep_wingbeat)
        if self.step.clip in DRAGON_TRICKS:
            self._dragon_recent.append(self.step.clip)
        if self.step.clip == "warp_cast":
            self._begin_warp(now)
        elif self.step.clip == "pounce_leap":
            self._launch_pounce(now)
        elif self.step.clip == "conjure":
            self._burst(now, "sparkle", 0.42, -0.62, 5)
        if self.step.line and not self.chat_pending and (self.bubble is None or self.bubble.kind != "say"):
            self.say(self.step.line, now=now, seconds=2.4, kind="status")
        props = {"dragon_toy_ball": "ball", "dragon_treasure_find": "coin", "dragon_treasure_polish": "gem"}
        if (self.step.clip in props and self.motion and not self.quiet and not self.props.items
                and now - self._prop_at >= 180):
            self.props.emit(props[self.step.clip], self.x, self.y - self.figure * 0.35, now, self.scale)
            self._prop_at = now
        if (self.step.clip in self.repertoire.actions
                and (not self.repertoire.recent or self.repertoire.recent[-1] != self.step.clip)):
            self.repertoire.recent.append(self.step.clip)

    def _step_done(self, now):
        step = self.step
        if step is None:
            return True
        if step.hold == "chat":
            return not self.chat_pending
        if step.hold == "agent":
            role = {"laptop": "search", "drawing": "drawing", "sing": "voice"}.get(self.media)
            has_variants = any(a["role"] == role for a in self.repertoire.actions.values())
            return now >= self.agent_busy_until or (has_variants and now >= self._state_switch_at)
        if step.hold == "state":
            return now >= self.state_until or now >= self._state_switch_at
        if step.hold == "sleep":
            return now >= self._sleep_cue_until or now - self.step_started >= 40
        if step.target_x is not None or step.target_y is not None:
            reached = ((step.target_x is None or abs(self.x - step.target_x) < (0.001 if step.edge_side else 1.5))
                       and (step.target_y is None or abs(self.y - step.target_y) < 1.5))
            return reached and (self._role(step.clip) != "air_play" or CLIPS[step.clip][3] or self._clip_done(now))
        if step.duration is not None:
            return now - self.step_started >= step.duration
        return now - self.step_started >= 4.0 if CLIPS[step.clip][3] else self._clip_done(now)

    def _role(self, clip):
        return self.repertoire.actions.get(clip, {}).get("role")

    def _form(self, clip=None):
        """'dragon' while one of her dragon-form strips plays, else 'human'."""
        return self.repertoire.actions.get(clip or self.clip, {}).get("form", "human")

    def _head_clearance(self, clip=None):
        geometry = self.repertoire.actions.get(clip or self.clip, {}).get("geometry") or {}
        bounds = geometry.get("bounds")
        if isinstance(bounds, (list, tuple)) and len(bounds) == 4 and isinstance(bounds[1], (int, float)):
            return max(0.0, -bounds[1] * 0.5 * self.scale)
        return self.figure * 1.05

    def _bottom_target(self, clip=None):
        if clip is None:
            clip = self._hidden_clip if self.hidden_edge == "bottom" else self.step.clip if self.step else self.clip
        # Keep a usable head-sized region on screen across different sprite heights and scales.
        visible = max(60.0, min(80.0, self.figure * 0.45))
        return self.floor + max(0.0, self._head_clearance(clip) - visible)

    def _pick(self, role, fallback, **kwargs):
        return self.repertoire.choose(role, fallback=fallback, **kwargs)

    def _state_steps(self, name, fallback, now):
        role = {"reply": "reply", "listen": "listen", "inspect": "inspect",
                "checkup": "study", "handoff": "celebrate", "stuck": "curious"}.get(name)
        clip = fallback
        if name == "reply":
            fallback = STATE_CLIPS["reply"]
            sleepy = self.presence.circadian in NIGHT or self.presence.circadian == "午后有点困" or self.energy < 0.35
            if self.motion and not self.quiet and not self.attention and self.effective_mood > 0.3 and not sleepy:
                clip = self._pick("reply", fallback, posture="air", family="joyful")
            elif sleepy:
                clip = self._pick("reply", fallback, posture="sit", family="sleepy")
            else:
                posture, family = self.rng.choice((("sit", "calm"), ("stand", "bright"),
                                                   ("stand", "quiet")))
                clip = self._pick("reply", fallback, posture=posture, family=family)
        elif name == "inspect" and self.media == "laptop" and self._has("spell_tome"):
            clip = "tome"                 # 群里在查资料：翻魔法书，而不是举着照片端详
        elif name == "listen" and self.media == "sing":
            clip = "sing"
        elif role:
            clip = self._pick(role, fallback)
        self.state_clip = clip
        self._state_switch_at = now + self.rng.uniform(10, 16)
        if CLIPS[clip][4] == "air" and self.motion and self.mode == "ground":
            height = max(self.ceiling + self.figure, self.floor - self.figure * 1.2)
            prep = self._pick("fly", "hop", family="takeoff")
            flap = self._pick("fly", "fall", family="flap")
            land = self._powered_landing()
            return [Step(prep), Step(flap, target_y=height), Step(clip, hold="state"),
                    Step(flap, target_y=self.floor, hold="air_return"), Step(land), Step("idle", 1)]
        return [Step(clip, hold="state"), Step("idle", 1)]

    def _choose(self, now):
        """Pick a little routine, with long quiet poses between its small beats."""
        p = self.presence
        sleepy = p.circadian in NIGHT or self.energy < 0.25
        drowsy = p.circadian == "午后有点困"     # an afternoon nap now and then, not all afternoon
        offline = p.known and p.bot != "ready"
        engaged = not offline and now - self.last_agent_event < ENGAGED_SECONDS
        mood = self.effective_mood
        r = self.rng.uniform
        # 状态层最优先：系统此刻在做的事，比「刚才发生过什么」和随机卖萌都准。
        # 被打断（戳一下、拖一下、聊一句）之后还能回到这个动作，直到心跳停掉。
        if self.state_clip and now < self.state_until:
            return self._state_steps(self.state_name, self.state_clip, now)
        self.state_clip, self.state_name, self.state_source = None, None, None
        if self.media and now < self.agent_busy_until:
            # Back from a poke, a drag or a local chat: the drawing in QQ is still going.
            self._state_switch_at = now + self.rng.uniform(10, 16)
            return [Step(self._busy_clip(), hold="agent"), Step("idle", 1)]
        self.media = None
        if now < self._sleep_cue_until:
            return [Step(self._pick("sleep", "sleep"), hold="sleep")]
        if self._next_pending:
            self._next_pending = False
            return self._routine_steps(now, manual=True)
        if self.attention:
            return [Step("idle", 1.5)]
        chase = self._prop_routine(now)
        if chase:
            return chase
        weights = {
            "idle": 0.65, "wander": 1.1,
            "stretch": 0.25 + (1.2 if sleepy or drowsy or p.circadian == "刚睡醒" else 0.0),
            "tea_time": 1.1 + max(0.0, mood), "book": 1.2, "laptop": 0.65,
            "phone": 0.45 + min(1.4, p.chatter * 0.18),
            "nap": 0.0 if engaged or now - self._nap_at < 480 else (0.08 if sleepy or drowsy else 0.015),
            # 静态道具库只做穿插：整段动起来的法术、扑跃、散步才是主角。
            "library": 2.6 if self.repertoire.actions else 0.0,
            "dragon": 0.45 if self._can_dragon(now) and self.energy >= 0.45 else 0.0,
            "warp": 0.0,                 # A trip needs a toy, a caller or another concrete cause.
            "sneeze": (0.08 + (0.3 if p.circadian == "刚睡醒" else 0.0)) if self.motion and self._has("spell_sneeze") else 0.0,
            "flight": 0.35 if self.motion and not self.quiet and any(
                a["role"] == "fly" for a in self.repertoire.actions.values()) else 0.0,
            "bottom": 0.15 if self.motion and any(a["role"] == "bottom" for a in self.repertoire.actions.values()) else 0.0,
            "hop": 0.15 + max(0.0, mood) * 1.5, "sing": 0.12 + max(0.0, mood) * 1.1,
            "cheer": max(0.0, mood - 0.3) * 1.5, "sketch": 0.6,
            "observe": 0.75 + max(0.0, -mood - 0.2) * 1.2, "patrol": 0.6, "sit": 0.65,
            "headphones": 0.85, "gamepad": 0.7, "tail_care": 0.8, "lie_chin": 0.8,
            "popcorn": 0.0 if offline or p.chatter < 4 else 0.4 + min(3.2, p.chatter * 0.18),
            "edge": 0.65 if (now - self._edge_at >= EDGE_COOLDOWN
                              and min(self.x - self.left, self.right - self.x) <= self.figure * 2
                              and self.right - self.left > self.figure * 3) else 0.0,
        }
        if self.repertoire.actions:
            for name in ("headphones", "gamepad", "tail_care", "lie_chin", "popcorn", "sit",
                         "tea_time", "book", "laptop"):
                weights[name] *= 0.45
        if self.quiet or self.pointer.near:
            weights["wander"] = weights["patrol"] = weights["edge"] = weights["flight"] = weights["bottom"] = 0.0
            weights["warp"] = weights["dragon"] = 0.0
            if self.quiet:
                weights["hop"] = weights["cheer"] = 0.0
        if not self.motion:
            weights = {name: weights[name] for name in
                       ("idle", "book", "tea_time", "laptop", "sit", "nap", "phone",
                        "headphones", "tail_care", "lie_chin", "popcorn", "library")}
        # 只对日常选择去重：真实群聊、聊天、拖拽和用户点播都不受这张近期表影响。
        choices = {name: value for name, value in weights.items() if name not in self._ambient_recent}
        # 真到了屏幕边，就探出头看看；较远处仍只是偶尔挑中的小探险。
        if choices.get("edge", 0) > 0 and min(self.x - self.left, self.right - self.x) <= self.figure * 0.45:
            choice = "edge"
        else:
            choice = self._weighted(choices)
        if choice != "library":
            self._ambient_recent.append(choice)
        if choice == "library":
            return self._routine_steps(now)
        if choice == "warp":
            return self._prop_routine(now) or [Step("idle", 1.5)]
        if choice == "sneeze":
            return [Step("sneeze"), Step("idle", 1.5)]
        if choice == "dragon":
            return self._dragon_steps(now)
        if choice == "flight":
            return self._flight_steps(now)
        if choice == "bottom":
            clip = self._pick("bottom", "dangle")
            return [Step(clip, target_y=self._bottom_target(clip)), Step(clip, r(3, 6)),
                    Step(clip, target_y=self.floor)]
        if choice == "idle":
            return [Step("idle", r(3, 6))]
        if choice == "wander":
            flourish = self._pick("walk", "lookaround", family=self.rng.choice(("transition", "recovery")))
            return [*self._stroll(), Step(flourish), Step("peek"), Step("sit_swing", r(5, 9))]
        if choice == "edge":
            self._edge_at = now
            side = -1 if self.x - self.left <= self.right - self.x else 1
            target = self.left + self.figure * EDGE_PEEK if side == -1 else self.right - self.figure * EDGE_PEEK
            inside = self._clamp_x(target, edge_side=0)
            return [Step("walk", target_x=target, edge_side=side),
                    Step(self._pick("edge", "edge_peek"), r(8, 14), edge_side=side),
                    Step("walk", target_x=inside, edge_side=side),
                    Step("walk", target_x=inside - side * self.figure * 0.55),
                    Step("peek"), Step("sit_swing", r(5, 9))]
        if choice == "tea_time":
            # 自动吃点心只是一段生活动作，不调用 feed，不写照顾记录或好感。
            tea = self._pick("tea", "tea")
            return [Step(self._pick("snack", "eat")), *self._activity_steps(tea, r(8, 15)), Step("sit_swing", r(4, 7))]
        if choice == "book":
            reading = self._pick("study", "read", family="reading")
            steps = self._activity_steps(reading, r(24, 42))
            if not engaged and now - self._nap_at >= 480 and self.rng.random() < 0.04:
                self._nap_at = now
                steps += [Step("doze"), Step(self._pick("sleep", "sleep"), r(12, 18))]
            return [*steps, Step("sit_swing", r(4, 7))]
        if choice == "laptop":
            working = self._pick("study", "laptop", family="work")
            return [*self._activity_steps(working, r(8, 15)), Step("sit_swing", r(4, 7))]
        if choice == "sketch":
            if self._has("spell_paint"):
                activity = self._activity_steps("paint", r(9, 16))
                # A selected result already finishes the scene. A second reveal
                # would overwrite the cheek/rework outcome with the same boast.
                ending = [Step("paint_reveal"), Step("nod")] if len(activity) == 1 else []
                return [Step("lookaround"), Step("conjure"), *activity, *ending]
            return [Step("lookaround"), Step("think", r(4, 7)), *self._activity_steps("drawing", r(9, 16)),
                    Step("show_drawing", r(3, 5)), Step("nod")]
        if choice == "observe":
            return [Step("lookaround"), Step("peek"),
                    Step("shy" if self.rng.random() < 0.35 else "nod"), Step("sit_swing", r(5, 9))]
        if choice == "patrol":
            return [*self._stroll(), Step("lookaround"), Step("checkup", r(7, 12)),
                    Step("inspect", r(4, 8)), Step("nod")]
        if choice == "sit":
            return [Step("sit_swing", r(5, 9)), Step("tea", r(14, 24))]
        if choice == "nap":
            self._nap_at = now
            clip = self._pick("sleep", "sleep")
            return [Step(clip, r(12, 25)), Step("idle", 1)]
        if choice == "phone":
            return [Step(self._pick("curious", "phone"), r(8, 16)), Step("nod")]
        if choice == "headphones":
            return [Step("headphones", r(35, 75)), Step("sit_swing", r(5, 9))]
        if choice == "gamepad":
            return [*self._activity_steps("gamepad", r(28, 50)), Step("headphones", r(18, 30))]
        if choice == "tail_care":
            return [Step("tail_care", r(24, 42)), Step("lie_chin", r(25, 45)), Step("sit_swing", r(5, 9))]
        if choice == "lie_chin":
            return [Step("sit_swing", r(3, 6)), Step("lie_chin", r(40, 75)), Step("sit_swing", r(5, 9))]
        if choice == "popcorn":
            return [Step("popcorn", r(30, 60)), Step("tea", r(12, 20)), Step("sit_swing", r(5, 9))]
        if choice == "stretch":
            return [Step("stretch"), Step("wing_stretch"), Step("sit_swing", r(5, 9))]
        if choice == "sing":
            return [Step("sing", r(5, 9))]
        return [Step(choice)]

    def _flight_steps(self, now):
        clearance = self.floor - self.ceiling - self.figure * 1.1
        if clearance < self.figure * 0.4:
            return [Step("idle", 2)]
        height = self.floor - min(clearance, self.figure * self.rng.uniform(0.9, 1.6))
        direction = 1 if self.x < (self.left + self.right) / 2 else -1
        target = self._clamp_x(self.x + direction * self.rng.uniform(2.2, 3.4) * self.figure)
        travel = target - self.x
        if abs(travel) < self.figure * 0.5:
            return [Step("idle", 2)]
        self.facing = direction          # Look toward the route before taking off.
        prep = self._pick("fly", "hop", family="takeoff")
        flap = self._pick("fly", "fall", family="flap")
        land = self._powered_landing()
        cruise = flap                    # Old glide art has standing legs and tiny static wings.
        if (self._air_play_allowed(now) and self._has("air_play_fire")
                and now - self._air_fire_at >= AIR_FIRE_COOLDOWN and self.rng.random() < 0.2):
            self._air_fire_at = now
            cruise = "air_play_fire"
        return [Step(prep), Step(flap, target_x=self.x + travel * 0.32, target_y=height),
                Step(cruise, target_x=self.x + travel * 0.68,
                     target_y=max(self.ceiling + self.figure * 1.1, height - self.figure * 0.08)),
                Step(flap, target_x=target, target_y=self.floor, hold="air_return"), Step(land)]

    def _air_return_steps(self):
        return [Step(self._pick("fly", "fall", family="flap"), target_y=self.floor, hold="air_return"),
                Step(self._powered_landing())]

    def _height_scare(self, now, drop):
        if (not self.motion or self.quiet or drop < self.figure * HEIGHT_FEAR_DROP
                or self.chat_pending or self.media or now < self.agent_busy_until
                or (self.state_clip and now < self.state_until)       # 群里真有事时不演
                or now - self._height_fear_at < HEIGHT_FEAR_COOLDOWN
                or not (self._has("scene_height_nervous") and self._has("scene_height_wings"))
                or self.rng.random() >= 0.6):
            return False
        self._height_fear_at = now
        return True

    def _powered_landing(self):
        if self._has("motion_fly_approach") and self._has("motion_fly_settle"):
            return "motion_fly_settle"
        return self._pick("fly", "land", family="landing")

    def _stroll(self):
        """A few steps nearby, rather than a long trip across the whole screen."""
        if self.quiet or self.pointer.near or not self.motion:
            return []
        target = self._clamp_x(self.x + self.rng.uniform(-2.2, 2.2) * self.figure)
        return [Step(self._pick("walk", "walk", family="gait"), target_x=target)] if abs(target - self.x) >= self.figure * 0.4 else []

    def _routine_steps(self, now, *, manual=False):
        can_story = now - self._last_story_at >= 180 and self._rests_since_story >= 2
        # Asking for the next activity changes the choice, not its pacing.
        # Forcing story here replaced 45–90s accompaniment with a 3s one-shot.
        mode = "story" if can_story and self.rng.random() < 0.45 else "rest"
        quiet = self.quiet or not self.motion
        sleepy = self.presence.circadian in NIGHT or self.presence.circadian == "午后有点困" or self.energy < 0.5
        context = {"quiet": quiet, "mood": self.effective_mood, "energy": self.energy, "sleepy": sleepy,
                   "recent_clips": self.repertoire.recent}
        routine_context = {**context, "allow_furniture": manual or now - self._furniture_at >= FURNITURE_COOLDOWN}
        routine = choose_routine(self.rng, self.repertoire.actions, self._ambient_families,
                                 mode=mode, **routine_context)
        if routine is None and (mode == "story" or manual or can_story):
            routine = choose_routine(self.rng, self.repertoire.actions, self._ambient_families,
                                     mode="rest" if mode == "story" else "story", **routine_context)
        if routine is None:
            return [Step("peek"), Step("idle", 1)] if manual else [Step("idle", 2)]
        family, beats = routine
        self._ambient_families.append(family)
        if family in ("scene:chair", "scene:sofa", "home:chair", "home:sofa"):
            self._furniture_at = now
        if family.startswith("ambient:"):
            self._rests_since_story += 1
            name, duration = beats[0]
            spec = self.repertoire.actions[name]
            activity_context = {**context, "recent_clips": tuple(self.repertoire.recent)}
            name = self.repertoire.choose(spec["role"], family=spec["family"],
                                          posture=spec["posture"], loop=True, fallback=name)
            beats = activity_sequence(self.rng, self.repertoire.actions, name, duration,
                                      **activity_context)
        elif family.startswith("home:"):
            self._rests_since_story += 1
        else:
            self._last_story_at, self._rests_since_story = now, 0
        return [*(Step(clip, duration, line=BEAT_LINES.get(clip)
                       if not family.startswith("ambient:") or clip.startswith("scene_") else None)
                  for clip, duration in beats), Step("idle", 1)]

    def _activity_steps(self, clip, duration):
        beats = activity_sequence(self.rng, self.repertoire.actions, clip, duration,
                                  mood=self.effective_mood, energy=self.energy, quiet=self.quiet or not self.motion,
                                  sleepy=self.presence.circadian in NIGHT or self.presence.circadian == "午后有点困",
                                  recent_clips=self.repertoire.recent)
        return [Step(name, seconds, line=BEAT_LINES.get(name) if name.startswith("scene_") else None)
                for name, seconds in beats]

    def _furniture_exit(self):
        spec = self.repertoire.actions.get(self.clip, {})
        if spec.get("role") != "scene_furniture":
            return None
        branches = EPISODES.get(spec.get("family"), ())
        for branch in branches:
            if branch and branch[-1][0] in self.repertoire.actions:
                return branch[-1]
        return None

    def _queue_manual(self, steps, now):
        if self._warp_request and self._warp_request.get("cause") == "prop" and not any(s.clip == "warp_cast" for s in steps):
            self._warp_request, self._chasing_prop = None, None
        # Only these local activities own a prop-matched put-away. Real work
        # holds remain controlled by their actual chat/agent/state result.
        if self.step is None or self.step.hold is None:
            for source, endings in ACTIVITY_ENDINGS.items():
                cleanup = endings[-1]
                if self.clip not in (source, *endings) or cleanup not in self.repertoire.actions:
                    continue
                if self.clip in endings and self.step is not None:
                    # Let the short gesture finish at its original speed. More
                    # clicks replace the destination, never restart the hands.
                    self.plan.clear()
                    tail = [] if self.clip == cleanup else [Step(cleanup)]
                    self._queue(*tail, *steps, now=now)
                else:
                    self._queue(Step(cleanup), *steps, now=now, interrupt=True)
                return
        exit_beat = self._furniture_exit()
        if exit_beat and self.clip == exit_beat[0] and self.step is not None:
            self.plan.clear()
            self._queue(*steps, now=now)
        else:
            cleanup = [Step(*exit_beat)] if exit_beat and self.clip != exit_beat[0] else []
            self._queue(*cleanup, *steps, now=now, interrupt=True)

    def next_activity(self, now):
        if (self.chat_pending or self.game_active or self.media or now < self.agent_busy_until
                or (self.state_clip and now < self.state_until) or self.mode in ("drag", "air", "fly", "bottom", "top_exit", "warp")):
            return False
        if self.mode in ("hidden", "hiding") and self.hidden_edge:
            self._next_pending = True
            self._emerge(now)
        elif self.mode == "ground":
            self._queue_manual(self._routine_steps(now, manual=True), now)
        else:
            return False
        self.say("换一个～", now=now, seconds=1.6, kind="status")
        return True

    def _prop_destination(self, item):
        direction = 1 if item.x >= self.x else -1
        target = self._clamp_x(item.x - direction * (self.figure * 0.18 + item.radius))
        return (target, direction) if abs(item.x - target) <= self.figure * 0.3 + item.radius else None

    def _far_prop_candidates(self, now):
        return tuple(item for item in self.props.candidates(now)
                     if (destination := self._prop_destination(item)) is not None
                     and abs(destination[0] - self.x) >= self.figure * 3)

    def _prop_routine(self, now, *, manual=False):
        if (not self.motion or self.chat_pending or self.game_active or self.mode != "ground"
                or self.media or now < self.agent_busy_until or (self.state_clip and now < self.state_until)
                or self.clip in ASLEEP or (not manual and (self.quiet or self.pointer.near or self.attention
                or self._role(self.clip) == "scene_furniture" or self.clip.startswith("form_")
                or now - self._prop_chase_at < 120))):
            return None
        candidates = self._far_prop_candidates(now) if manual else self.props.candidates(now)
        if not candidates or (not manual and self.rng.random() >= 0.15):
            return None
        item = self.rng.choice(candidates)
        destination = self._prop_destination(item)
        if destination is None:
            return None
        target, direction = destination
        teleport = (self._can_warp() and abs(target - self.x) >= self.figure * 3
                    and (manual or now - self._warp_at >= WARP_COOLDOWN))
        if manual and not teleport:
            return None
        self._prop_chase_at = now
        self._chasing_prop = (item.id, item.kind, target, direction)
        if teleport:
            self._warp_request = {"x": target, "via": "floor", "cause": "prop", "prop": self._chasing_prop}
            return [Step("warp_cast", line="去够那边的球。" if item.kind == "ball" else "去拿那边的宝贝。"), Step("idle", 1.5)]
        return [Step(self._pick("walk", "walk", family="gait"), target_x=target)]

    def _chased_prop(self, now):
        if self._chasing_prop is None:
            return None
        prop_id, kind, target, _ = self._chasing_prop
        return next((item for item in self.props.candidates(now) if item.id == prop_id and item.kind == kind
                     and abs(item.x - target) <= self.figure * 0.3 + item.radius), None)

    def _finish_prop_routine(self, now):
        prop_id, kind, target, direction = self._chasing_prop
        item = self._chased_prop(now)
        self._chasing_prop = None
        if item is None or abs(self.x - target) > self.figure * 0.12:
            return False
        success = self.props.kick(prop_id, now, direction) if kind == "ball" else self.props.collect(prop_id, now)
        if success:
            self._prop_at = now           # A reaction must not immediately emit the collected object again.
            self.plan.clear()
            self.step = None
            # The object has already moved or been collected. Stay empty-handed
            # instead of pulling a different toy or treasure out for the reaction.
            self._queue(Step("cheer" if kind == "ball" else "nod"),
                        Step("idle", 1.5), now=now)
            self.say("踢一下～" if kind == "ball" else "找到了。", now=now, seconds=1.8, kind="status")
        return success

    def _retreating(self):
        step = self.step
        return bool(step is not None and step.edge_side and step.clip == "walk"
                    and step.target_x is not None
                    and self._clamp_x(step.target_x, edge_side=0) == step.target_x)

    def _weighted(self, weights):
        total = sum(max(0.0, v) for v in weights.values())
        pick = self.rng.uniform(0, total)
        for name, value in weights.items():
            pick -= max(0.0, value)
            if pick <= 0:
                return name
        return "idle"

    # ── per-frame update ──
    def tick(self, now):
        dt = max(0.0, min(0.1, now - self._now))
        self._now = now
        if self._air_chase is not None and self.mode == "ground" and not self._air_play_allowed(now, continuing=True):
            self._queue(Step("idle", 1.5), now=now, interrupt=True)
        self._decay_warmth(now)
        self.props.tick(now, self.left, self.right, self.ceiling, self.floor)
        asleep = self.clip in ASLEEP or self.hidden_asleep
        spec = self.repertoire.actions.get(self.clip, {})
        working = self.chat_pending or self.media or now < self.agent_busy_until or (self.state_clip and now < self.state_until)
        restful = spec.get("restful", False) and CLIPS[self.clip][3] and self.mode == "ground" and not working
        self.energy = max(0.0, min(1.0, self.energy + (dt / 240 if asleep else dt / 600 if restful else -dt / 2400)))
        if self.mode == "air":
            self._fall(now, dt)
        elif self.mode == "drag":
            self._hang(dt)
        elif self.mode == "hiding":
            self._hide_motion(now, dt)
        elif self.mode == "top_exit":
            self._top_exit_motion(now, dt)
        elif self.mode == "hidden":
            self._hidden_tick(now)
        elif self.mode == "edge_exit":
            self._edge_exit_motion(now, dt)
        elif self.mode == "warp":
            self._warp(now, dt)
        elif self.mode in ("fly", "bottom"):
            self._fly(now, dt)
        else:
            self._ground(now, dt)
        if self.clip == "idle" and now >= self.blink_at:
            self.blink_started = now
            self.blink_at = now + (0.3 if self.rng.random() < 0.15 else self.rng.uniform(2.4, 6.0))
        self._particles(now, dt)
        self.portals = [portal for portal in self.portals if not portal.gone(now)]
        self._track_frame(now)
        if self.game_active and self.game_next_at and now >= self.game_next_at:
            self.game_next_at = 0.0
            if self.mode == "ground" and not self.chat_pending:
                self._game_round(now)
            else:
                self.cancel_game(now)
        if self.game_until and now >= self.game_until:
            self.game_until = 0.0
            if self.chat_pending:
                self.cancel_game(now)      # 聊天抢走了点击，不算你失误
            else:
                self._game_miss(now)       # 停手了：结算这一轮
        if self.bubble is not None and now >= self.bubble.until:
            self.bubble = None

    def _ground(self, now, dt):
        self.y = self.floor
        if self.warp is not None:
            if self.warp["phase"] == "cast" and self.clip == "warp_cast" and self._cast(now):
                return
            if self.warp is not None and self.warp["phase"] == "cast" and self.clip == "warp_cast":
                self._look(now)
                return
        # Long accompaniment can outlast a toy. Notice settled toys while resting,
        # rather than waiting for the current 45–90s activity to finish.
        if (self.props.items and not self._chasing_prop and now >= self._prop_check_at
                and now - self.last_interaction >= 8
                and self.step is not None and self.step.hold is None):
            self._prop_check_at = now + 2
            chase = self._prop_routine(now)
            if chase:
                target = self._chasing_prop
                self._queue(*chase, now=now, interrupt=True)
                self._chasing_prop = target
        step = self.step
        if self._chasing_prop and not any(p.id == self._chasing_prop[0] for p in self.props.candidates(now)):
            self._queue(Step("idle", 1), now=now, interrupt=True)
            step = self.step
        if step is not None and step.target_x is not None and (
                self.clip == "walk" or self._role(self.clip) == "walk" or self.clip == "form_dragon_hop"):
            mood = self.effective_mood
            speed = WALK_SPEED * self.scale * (1.15 if mood > 0.3
                                               else 0.85 if mood < -0.3 else 1.0)
            if self._role(self.clip) == "walk":
                speed *= max(0.55, min(1.25, CLIPS[self.clip][2] / 5))
            delta = step.target_x - self.x
            self.x += max(-speed * dt, min(speed * dt, delta))
            self.facing = 1 if delta >= 0 else -1
            clamped = self._clamp_x(self.x)
            if clamped != self.x:
                self.x = step.target_x = clamped
            if (self.pointer.near or self.attention) and not self._retreating():
                self._queue(Step("idle", 1.5), now=now, interrupt=True)
        if self._step_done(now):
            if self._chasing_prop and step is not None and step.target_x is not None:
                self._finish_prop_routine(now)
            self._advance(now)
            if self.step is None:
                self._queue(*self._choose(now), now=now)
                self._advance(now)
        self._look(now)

    def _fly(self, now, dt):
        if self.air_play_active and not self._air_returning and not self._air_play_allowed(now, continuing=True):
            self._queue(Step("idle", 1.5), now=now, interrupt=True)
        step = self.step
        if step is None:
            self.mode = "air"
            return
        if not self.bottom_peek:
            # Screen and pet-size changes must not leave an unreachable old waypoint.
            if step.target_x is not None:
                step.target_x = self._clamp_x(step.target_x, edge_side=0)
            if step.target_y is not None:
                step.target_y = (self.floor if step.hold == "air_return" else
                                 min(self.floor, max(self.ceiling + self.figure, step.target_y)))
        previous_x = self.x
        if step.hold == "air_chase" and self._air_chase is not None:
            self._track_air_cursor(dt)
        elif step.target_x is not None or step.target_y is not None:
            progress = max(0.0, min(1.0, (now - self.step_started) / self._move_duration))
            eased = progress * progress * (3 - 2 * progress)
            for axis, origin, target in (("x", self._move_origin[0], step.target_x),
                                         ("y", self._move_origin[1], step.target_y)):
                if target is not None:
                    setattr(self, axis, origin + (target - origin) * eased)
        self.x = self._clamp_x(self.x)
        lower = self._bottom_target() if self.bottom_peek else self.floor
        self.y = max(min(lower, self.ceiling + self.figure), min(lower, self.y))
        self._flight_vx = (self.x - previous_x) / dt if dt > 0 else 0.0
        if (step.hold == "air_return" and not self.bottom_peek
                and 0 < self.floor - self.y <= self.figure * 0.18
                and self.plan and self.plan[0].clip == "motion_fly_settle"
                and self._has("motion_fly_approach") and self.clip != "motion_fly_approach"):
            # Change only the picture near contact. The return leg keeps its
            # original route and clock; its grounded sequel starts at frame 1.
            step.clip = "motion_fly_approach"
            self._set_clip(step.clip, now, restart=True)
        self._resolve_air_reach(now)
        step = self.step
        if self._step_done(now):
            if self.bottom_peek and self.y <= self.floor + 1.5 and step.target_y == self.floor:
                self.bottom_peek = False
                self.y, self.mode = self.floor, "ground"
            elif not self.bottom_peek and self.y >= self.floor - 1.5:
                self.y, self.mode = self.floor, "ground"
                self._air_chase, self._air_returning = None, False
            self._advance(now)
            if self.step is None and self.mode in ("fly", "bottom"):
                self.mode = "air"

    def _look(self, now):
        """Idle glances follow a nearby cursor; a lingering cursor gets a wave."""
        if self.step is None or self.step.clip != "idle":
            return
        if not (self.pointer.near and now - self.pointer.since > 0.35):
            if self.clip in ("look_left", "look_right"):
                self._set_clip("idle", now)
            return
        offset = self.pointer.x - self.x
        if abs(offset) > self.figure * 0.12:
            self.facing = 1 if offset > 0 else -1
            self._set_clip("look_right" if offset > 0 else "look_left", now)
        else:
            self._set_clip("idle", now)
        if (now - self.pointer.since > 2.5 and now - self._greeted_at > 120
                and now - self.last_interaction > 60 and self.motion and not self.chat_pending
                and not self.media and now >= self.agent_busy_until
                and not (self.state_clip and now < self.state_until)):
            self._greeted_at = now
            greeting = "scene_glass_hello" if self._has("scene_glass_hello") and self.rng.random() < 0.35 else "wave"
            self._queue(Step(greeting), Step("idle", 2.0), now=now, interrupt=True)
            lines = LINES["late"] if self.presence.circadian in NIGHT else LINES["greet"]
            self.say(self.rng.choice(lines), now=now, seconds=3.5)

    def _fall(self, now, dt):
        if self._ceiling_until and now >= self._ceiling_until:
            self._ceiling_until = 0.0
            self._set_clip(self._pick("fall", "fall"), now, restart=True)
        self.vy += GRAVITY * self.scale * dt
        self.x += self.vx * dt
        self.y += self.vy * dt
        self.vx *= 0.995
        if self._air_top is not None:
            self._air_top = min(self._air_top, self.y)
        clamped = self._clamp_x(self.x)
        if clamped != self.x:
            self.x, self.vx = clamped, -self.vx * 0.45
        if self.vy < 0 and self.y - self._head_clearance() < self.ceiling:
            # Flung upwards she bonks the top of the screen instead of flying out of sight.
            self.y, self.vy, self.vx = self.ceiling + self._head_clearance(), -self.vy * 0.25, self.vx * 0.8
            ceiling_clip = self._pick("ceiling", "fall")
            if ceiling_clip != "fall":
                self._set_clip(ceiling_clip, now, restart=True)
                self.y = max(self.y, self.ceiling + self._head_clearance())
                self._ceiling_until = now + 0.35
            self._emit("sparkle", now, 2)
            if self.rng.random() < 0.5:
                self.say(self.rng.choice(LINES["bonk"]), now=now, seconds=1.8)
        if self.y >= self.floor:
            impact = self.vy
            self.y, self.vx, self.vy = self.floor, 0.0, 0.0
            self.mode = "ground"
            self.swing = self.swing_v = 0.0
            self.plan.clear()
            self.step = None
            drop = self.floor - self._air_top if self._air_top is not None else 0.0
            self._air_top = None
            if self._landing:
                self._land_trick(now)
                return
            land = self._pick("land", "land")
            self._set_clip(land, now, restart=True)
            steps = [Step(land)]
            if self._height_scare(now, drop):
                # 怕高：自己飞很熟练，被拎上去再放手就心虚，腿软坐下，抱着翅膀壮胆再站起来。
                steps += [Step("scene_height_nervous", line=BEAT_LINES["scene_height_nervous"]),
                          Step("scene_height_wings", line=BEAT_LINES["scene_height_wings"])]
            elif impact > 1900 * self.scale:
                steps.append(Step("startle"))       # 摔狠了只哆嗦一下，不再跺脚瞪人
                self.say(self.rng.choice(LINES["thrown"]), now=now, seconds=3.0)
                self._emit("sweat", now)
            self._queue(*steps, Step("idle", 1.5), now=now, interrupt=True)

    def _hang(self, dt):
        grab = self._grab
        # Ease from where she was grabbed to hanging by her raised hands.
        k = min(1.0, dt * 14)
        grab["dx"] += (0.0 - grab["dx"]) * k
        grab["dy"] += (self.figure * 0.98 - grab["dy"]) * k
        self.x = self._clamp_x(grab["cx"] + grab["dx"])
        top = min(self.floor, self.ceiling + self._head_clearance())
        desired_y = grab["cy"] + grab["dy"]
        self.y = max(top, min(self.floor, desired_y))
        # Retain the selected reaction until the pointer moves clearly away from the edge.
        margin = self.figure * 0.05
        boundary = grab.get("boundary")
        if boundary == "ceiling" and desired_y < top + margin:
            next_boundary = boundary
        elif boundary == "floor" and desired_y > self.floor - margin:
            next_boundary = boundary
        else:
            next_boundary = "ceiling" if desired_y <= top else "floor" if desired_y >= self.floor else None
        if next_boundary != boundary:
            grab["boundary"] = next_boundary
            self._set_clip(self._pick(next_boundary or "drag", "dangle"), self._now, restart=True)
            self.y = max(self.y, min(self.floor, self.ceiling + self._head_clearance()))
        target = max(-26.0, min(26.0, self.vx * 0.03))
        self.swing_v += ((target - self.swing) * 60.0 - self.swing_v * 9.0) * dt
        self.swing += self.swing_v * dt
        self.vx *= max(0.0, 1 - dt * 6)     # stillness lets the pendulum settle

    def _hidden_target(self, *, edge=None, clip=None, top_depth=None):
        edge, clip = edge or self.hidden_edge, clip or self._hidden_clip
        side = -1 if edge == "left" else 1 if edge == "right" else 0
        target_x = (self.left + self.figure * EDGE_PEEK if side == -1 else
                    self.right - self.figure * EDGE_PEEK if side == 1 else self._clamp_x(self.x, edge_side=0))
        side_y = self.y if self._hidden_side_y is None else self._hidden_side_y
        side_y = max(min(self.floor, self.ceiling + self.figure), min(self.floor, side_y))
        target_y = (self._bottom_target(clip) if edge == "bottom" else
                    self.ceiling + self.figure * 0.45 if edge == "top" else side_y)
        geometry = self.repertoire.actions.get(clip, {}).get("geometry") or {}
        rest_y = geometry.get("rest_y")
        if edge in ("bottom", "top") and isinstance(rest_y, (int, float)):
            plane = self.floor if edge == "bottom" else self.ceiling
            target_y = plane - rest_y * self.figure / 320.0
        rest_x = geometry.get("rest_x")
        if side and isinstance(rest_x, (int, float)):
            plane = self.left if side == -1 else self.right
            target_x = plane - rest_x * self.figure / 320.0 * (-1 if self.facing < 0 else 1)
        if edge == "top":
            # The shoe contact belongs to a fully revealed hanging pose. Hidden
            # means only the inverted head peeks below the edge, not the body.
            depth = top_depth if top_depth is not None else 0.38 if self._role(clip) == "edge_sleep" else 0.45
            bottom = (geometry.get("bounds") or [0, 0, 0, 0])[3]
            target_y = self.ceiling + self.figure * depth - bottom * self.figure / 320.0
        return target_x, target_y

    def _hide_motion(self, now, dt):
        side = -1 if self.hidden_edge == "left" else 1 if self.hidden_edge == "right" else 0
        target_x, target_y = self._hidden_target()
        p = max(0.0, min(1.0, (now - self._hide_started) / self._hide_duration))
        eased = p * p * (3 - 2 * p)
        self.x = self._hide_origin[0] + (target_x - self._hide_origin[0]) * eased
        self.y = self._hide_origin[1] + (target_y - self._hide_origin[1]) * eased
        if abs(self.x - target_x) < 0.01 and abs(self.y - target_y) < 0.01:
            self.x, self.y, self.mode = target_x, target_y, "hidden"
            self._hidden_since = now
            sleepy = self.presence.circadian in NIGHT or self.presence.circadian == "午后有点困" or self.energy < 0.35
            self._hidden_sleep_at = now + self.rng.uniform(60, 85) if sleepy else now + self.rng.uniform(90, 120)
            self.hidden_asleep = False
            self._hidden_slip_started = None
            self._hidden_gaze = self._hidden_gaze_candidate = 1
            self._hidden_gaze_since = now
            self._hidden_blink_at = now + self.rng.uniform(4, 8)
            self._hidden_micro_at = now + self.rng.uniform(25, 40)
            if side:
                self.facing = -side
            if self._hidden_clip:
                self._hidden_awake_clip = self._hidden_clip
                self._set_clip(self._hidden_clip, now, restart=True)

    def _hidden_tick(self, now):
        if self.hidden_asleep:
            if self._hidden_slip_started is not None:
                p = max(0.0, min(1.0, (now - self._hidden_slip_started) / (4 / 6)))
                self.x, base_y = self._hidden_target()
                self.y = base_y + self.figure * 0.25 * math.sin(math.pi * p)
                if p >= 1:
                    self._hidden_slip_started = None
                    self._set_clip(self._hidden_clip, now, restart=True)
                    spec = self.repertoire.actions.get(self._hidden_clip, {})
                    if spec.get("entry_sequence") and spec.get("entry_fps"):
                        self.clip_started = now - len(spec["entry_sequence"]) / spec["entry_fps"]
            elif now >= self._hidden_slip_check_at:
                self._hidden_slip_check_at = now + 20
                slip = self.repertoire.actions.get(self._hidden_clip, {}).get("slip_clip")
                if (self.hidden_edge in ("left", "right") and self.motion and not self.quiet
                        and slip in CLIPS and now - self._hidden_sleep_started >= 90
                        and now - self._hidden_slip_at >= 600 and self.floor - self.y >= self.figure * 0.5
                        and self.rng.random() < 0.18):
                    self._hidden_slip_started = self._hidden_slip_at = now
                    self._set_clip(slip, now, restart=True)
            return
        if self._role(self.clip) == "edge_micro":
            if not self._clip_done(now):
                return
            self._hidden_clip = self._hidden_awake_clip
            self._set_clip(self._hidden_clip, now, restart=True)
            self.x, self.y = self._hidden_target()
            self._fade = None
        if (now < self._hidden_sleep_at - 4 and now >= self._hidden_micro_at
                and self.motion and not self.quiet):
            self._hidden_micro_at = now + self.rng.uniform(45, 70)
            family = "side" if self.hidden_edge in ("left", "right") else self.hidden_edge
            clip = self._pick("edge_micro", None, family=family)
            if clip in CLIPS:
                self._hidden_clip = clip
                self._set_clip(clip, now, restart=True)
                self.x, self.y = self._hidden_target()
                self._fade = None
                return
        if now < self._hidden_sleep_at:
            if self.pointer.seen:
                offset = self.pointer.x - self.x
                deadzone = self.figure * 0.35
                desired = 0 if offset < -deadzone else 2 if offset > deadzone else 1
                if desired != self._hidden_gaze_candidate:
                    self._hidden_gaze_candidate, self._hidden_gaze_since = desired, now
                elif now - self._hidden_gaze_since >= 0.25:
                    self._hidden_gaze = desired
            if self.motion and now >= self._hidden_blink_at:
                self._hidden_blink_started = now
                self._hidden_blink_at = now + self.rng.uniform(4, 8)
            return
        self.hidden_asleep = True
        self._hidden_sleep_started = now
        self._hidden_slip_check_at = now + 90
        family = "side" if self.hidden_edge in ("left", "right") else self.hidden_edge
        name = self._pick("edge_sleep", self._hidden_clip, family=family)
        if name in CLIPS and self._role(name) == "edge_sleep":
            self._hidden_clip = name
            self._set_clip(name, now, restart=True)
            # The entry frames already lower her cheek onto the support. Moving
            # the whole strip as well made her forearms slide through the edge.
            self.x, self.y = self._hidden_target()
            self._fade = None

    def _popout_steps(self):
        clip = "boundary_popout" if "boundary_popout" in CLIPS else "hop"
        return [Step(clip), Step("idle", 1.5)]

    def _emerge(self, now):
        edge, self.hidden_edge = self.hidden_edge, None
        waking, self.hidden_asleep = self.hidden_asleep, False
        self._hidden_slip_started = None
        self._fade = None
        self.mode = "edge_exit"
        self.plan.clear()
        self.step = None
        self._edge_exit = {"edge": edge, "phase": "wake" if waking else "prepare", "since": now}
        if waking:
            spec = self.repertoire.actions.get(self._hidden_clip, {})
            entry = spec.get("entry_sequence")
            if entry:
                # Open her eyes on the same pillow/forearms before pushing off.
                # A wake must not replay a left-looking idle or move its support.
                self._edge_exit["wake_sequence"] = list(reversed(entry))
                self._set_clip(self._hidden_clip, now, restart=True)
            else:
                self._hidden_clip = self._hidden_awake_clip or self._hidden_clip
                if self._hidden_clip:
                    self._set_clip(self._hidden_clip, now, restart=True)
            self.x, self.y = self._hidden_target(edge=edge)
            self.say("嗯？", now=now, seconds=1.2, kind="status")
        else:
            self._prepare_edge_exit(now)

    def _prepare_edge_exit(self, now):
        state = self._edge_exit
        edge = state["edge"]
        if edge == "top":
            bounds = (self.repertoire.actions.get(self.clip, {}).get("geometry") or {}).get("bounds") or [0, 0, 0, 0]
            state["peek_depth"] = (self.y + bounds[3] * self.figure / 320.0 - self.ceiling) / self.figure
        if state["phase"] == "wake":
            self._hidden_clip = self._hidden_awake_clip or self._hidden_clip
            if edge != "top":
                self.x, self.y = self._hidden_target(edge=edge)
        state.update(phase="prepare", since=now)
        if edge == "top" and self._can_warp():
            name = ("edge_daily_top_cast" if self._has("edge_daily_top_cast") else
                    self._hidden_awake_clip or "boundary_top_look")
        else:
            name = {"left": "boundary_side_launch", "right": "boundary_side_launch",
                    "bottom": "boundary_bottom_spring", "top": "boundary_top_return"}[edge]
        fallback = "boundary_popout" if edge != "top" else "fall"
        self._set_clip(name if name in CLIPS else fallback if fallback in CLIPS else "idle", now, restart=True)
        if edge == "top" and self._can_warp():
            self.x, self.y = self._hidden_target(edge=edge, clip=self.clip, top_depth=state["peek_depth"])
            # Open the route while only her head peeks out. Nothing slides down
            # to expose a hanging body before the spell has somewhere to lead.
            plane = min(self.floor - self.figure, self.ceiling + self.figure * 0.65)
            state["src"] = self._open_portal(self.x, plane, "floor", now)
            state["dst"] = self._open_portal(self.x, self.floor, "floor", now + 0.15)
        self._fade = None

    def _edge_exit_motion(self, now, dt):
        state = self._edge_exit
        edge, phase = state["edge"], state["phase"]
        if phase == "wake":
            self.x, self.y = self._hidden_target(edge=edge)
            duration = len(state["wake_sequence"]) / 5 if state.get("wake_sequence") else 0.3
            if now - state["since"] >= duration:
                self._prepare_edge_exit(now)
            return
        if phase == "prepare":
            top_route = edge == "top" and "src" in state and self._can_warp()
            duration = (len(CLIPS[self.clip][1]) / CLIPS[self.clip][2]
                        if self.clip == "edge_daily_top_cast" else
                        0.65 if edge == "top" and self.motion else 0.2 if self.motion else 0.1)
            if top_route:
                self.x, self.y = self._hidden_target(edge=edge, clip=self.clip, top_depth=state["peek_depth"])
                state["src"].x, state["src"].y = self.x, min(self.floor - self.figure, self.ceiling + self.figure * 0.65)
                state["dst"].x, state["dst"].y = self.x, self.floor
                state["src"].radius = state["dst"].radius = self.figure * PORTAL_RADIUS
            elif "src" in state:
                self._cancel_warp(now)
                state.pop("src")
                state.pop("dst")
            if now - state["since"] < duration:
                return
            if top_route:
                # Ground fear scenes end in sitting/standing poses. Decide here
                # while she is still attached, then open a route before release.
                self._begin_top_escape(now)
                return
            direction = 1 if edge == "left" else -1 if edge == "right" else (1 if self.x < (self.left + self.right) / 2 else -1)
            distance = self.figure * (0.9 if edge in ("left", "right") else 0.35 if edge == "top" else 0.2)
            target = self._clamp_x(self.x + direction * distance, edge_side=0)
            duration = (max(1.5, min(2.5, (self.floor - self.y) / max(1.0, self.figure * 2.4)))
                        if edge == "top" else 1.0) if self.motion else 0.35
            state.update(phase="flight", since=now, origin=(self.x, self.y), target=target, duration=duration)
            self.facing = direction
            clip = self._pick("fly", "fall", family="flap") if self.motion else "idle"
            state["flap"] = clip
            if edge == "top" or not self.motion or self._role(self.clip) != "edge_launch":
                self._set_clip(clip, now, restart=True)
            return
        if self._role(self.clip) == "edge_launch" and now - state["since"] >= 3 / 7:
            self._set_clip(state["flap"], now, restart=True)
        p = max(0.0, min(1.0, (now - state["since"]) / state["duration"]))
        eased = p * p * (3 - 2 * p)
        x0, y0 = state["origin"]
        self.x = x0 + (state["target"] - x0) * eased
        self.y = y0 + (self.floor - y0) * eased
        if edge != "top" and self.motion:
            self.y -= self.figure * 0.45 * 4 * p * (1 - p)
        if self.bottom_peek and self.y <= self.floor:
            self.bottom_peek = False
        if self.top_peek and self.y >= self.ceiling + self._head_clearance():
            self.top_peek = False
        if p >= 1:
            self.x, self.y, self.mode = state["target"], self.floor, "ground"
            self.top_peek = self.bottom_peek = False
            self._edge_exit = None
            clip = "boundary_land" if "boundary_land" in CLIPS else "land"
            self._queue(Step(clip), Step("idle", 1.5), now=now, interrupt=True)

    def _top_exit_motion(self, now, dt):
        p = max(0.0, min(1.0, (now - self._hide_started) / self._hide_duration))
        self.y = self._hide_origin[1] + (self.floor - self._hide_origin[1]) * p * p * (3 - 2 * p)
        if self.y >= self.floor:
            self.top_peek = False
            self.mode = "ground"
            self._queue(*self._popout_steps(), now=now, interrupt=True)

    # ── spells: magic circles, teleport, pounce, sneeze ──
    def _has(self, strip):
        """Spells only play with their own adopted sheets; the legacy fallback keeps the old staging."""
        return strip in self.repertoire.actions

    def _can_warp(self):
        return self.motion and self._has("spell_cast") and self._has("spell_dive") and self._has("spell_rise")

    def _busy_clip(self):
        """What she acts out while 小团团 is busy in QQ: a magic canvas, a floating tome, a song."""
        if self.media == "drawing" and self._has("spell_paint"):
            return "paint"
        if self.media == "laptop" and self._has("spell_tome"):
            return "tome"
        role = {"laptop": "search", "drawing": "drawing", "sing": "voice"}.get(self.media)
        return self._pick(role, self.media)

    def _open_portal(self, x, y, side, now):
        self._portal_serial += 1
        portal = Portal(self._portal_serial, x, y, side, self.figure * PORTAL_RADIUS, now)
        self.portals.append(portal)
        return portal

    def _begin_top_escape(self, now):
        """A short top-edge entry reuses the regular floor-portal exit."""
        state = self._edge_exit
        src, dst = state["src"], state["dst"]
        target = self._clamp_x(self.x, edge_side=0)
        release = "edge_daily_top_release" if self._has("edge_daily_top_release") else None
        if release:
            self._set_clip(release, now, restart=True)
            self.x, self.y = self._hidden_target(edge="top", clip=release, top_depth=state["peek_depth"])
        plane = src.y
        self.warp = {"phase": "top_release" if release else "top_sink", "since": now, "x": target, "via": "floor",
                     "cause": "top_edge", "src": src, "dst": dst, "origin_y": self.y, "plane": plane,
                     "release_y": max(self.y, self.ceiling + self._head_clearance(release) + 2) if release else self.y}
        self._warp_at = now
        self._edge_exit = None
        self.mode = "warp"
        self.portal_clip = (plane, "below")
        self.vx = self.vy = 0.0
        if not release:
            self._set_clip("warp_sink", now, restart=True)
        self._fade = None
        self.say("看准了，走这边。", now=now, seconds=1.8, kind="status")

    def _ceiling_plane(self):
        return self.ceiling + self.figure * PORTAL_RADIUS * PORTAL_TILT + 6

    def _warp_target(self, x=None):
        """A landing spot well away from her, or None when the screen is too narrow for a trip."""
        lo, hi = self._clamp_x(self.left, edge_side=0), self._clamp_x(self.right, edge_side=0)
        if x is not None:
            x = max(lo, min(hi, x))
            return x if abs(x - self.x) >= self.figure * 1.5 else None
        span = hi - lo
        if span < self.figure * 4:
            return None
        far = max(self.figure * 2.5, span * 0.35)
        options = [c for c in (self.rng.uniform(lo, hi) for _ in range(8)) if abs(c - self.x) >= far]
        if options:
            return options[0]
        return lo if self.x - lo > hi - self.x else hi

    def magic(self, now, kind=None):
        """A requested trick has a purpose; QA may select an exact available kind."""
        self.last_interaction = now
        if (self.chat_pending or self.mode != "ground" or not self.motion or self.game_active
                or self.media or now < self.agent_busy_until or (self.state_clip and now < self.state_until)):
            return False
        if self.clip in ASLEEP or self.clip in WAKING:
            self.poke(now)
            return False
        available = [name for name in DAILY_MAGIC if self._has(name)]
        if self._can_dragon(now, manual=True):
            available.insert(0, "dragon")
        if self._can_warp() and self._far_prop_candidates(now):
            available.append("prop")
        if not any(name in DAILY_MAGIC for name in available) and self._has("scene_fire_controlled"):
            available.append("scene_fire_controlled")
        if kind is not None:
            if kind not in available:
                return False
            chosen = kind
        elif "dragon" in available and not self._magic_dragon_shown:
            chosen = "dragon"
        else:
            fresh = [name for name in available if name not in self._magic_used]
            if not fresh:
                self._magic_used.clear()
                fresh = available
            if not fresh:
                return False
            chosen = self.rng.choice(fresh)
        if chosen == "dragon":
            steps = self._dragon_steps(now)
            self._magic_dragon_shown = True
        elif chosen == "prop":
            steps = self._prop_routine(now, manual=True)
            if not steps:
                return False
        else:
            steps = [Step(chosen), Step("idle", 1.5)]
        self._magic_used.add(chosen)
        self._queue_manual(steps, now)
        return True

    def _begin_warp(self, now, *, slip=False):
        request = self._warp_request or {"x": None, "via": None, "cause": "spell"}
        self._warp_request = None
        if request.get("cause") == "prop":
            self._chasing_prop = request.get("prop")
            if self._chased_prop(now) is None:
                self._chasing_prop = None
                self.step = Step("idle", 1)
                self._set_clip("idle", now, restart=True)
                return False
        target = self._warp_target(request.get("x"))
        if target is None or not self.motion:
            if not slip:
                clip = "idle" if request.get("cause") == "prop" else "conjure"
                self._chasing_prop = None
                self.step = Step(clip)
                self._set_clip(clip, now, restart=True)
            return False
        via = request.get("via")
        if via not in ("floor", "ceiling"):
            via = "ceiling" if self.floor - self.ceiling > self.figure * 3.2 and self.rng.random() < 0.35 else "floor"
        if via == "ceiling" and self.floor - self.ceiling <= self.figure * 3.2:
            via = "floor"
        self._warp_at = now
        self.vx = self.vy = 0.0
        self.warp = {"phase": "slip" if slip else "cast", "since": now, "x": target, "via": via,
                     "cause": request.get("cause", "spell"), "src": None, "dst": None}
        # Casting is an ordinary ground beat: a cue, a poke or a drag can still stop it and the
        # circles just close. Only the jump commits her to the spell.
        if slip:
            self.mode = "warp"
            self.warp["src"] = self._open_portal(self.x, self.floor, "floor", now)
            self.warp["dst"] = self._open_portal(target, self._ceiling_plane() if via == "ceiling" else self.floor,
                                                 via, now + 0.15)
            self._set_clip(self._pick("fall", "fall"), now, restart=True)
        return True

    def _cast(self, now):
        """The ground beat of a spell: draw the circle under her feet, then the one she will come out of."""
        w, t = self.warp, now - self.warp["since"]
        if w["cause"] == "prop" and self._chased_prop(now) is None:
            self._cancel_warp(now)
            self._queue(Step("idle", 1.5), now=now, interrupt=True)
            return True
        if w["src"] is None and t >= 0.45:
            w["src"] = self._open_portal(self.x, self.floor, "floor", now)
            self._burst(now, "sparkle", 0.0, -0.05, 4, spread=0.5)
        if w["dst"] is None and t >= 0.8:
            plane = self._ceiling_plane() if w["via"] == "ceiling" else self.floor
            w["dst"] = self._open_portal(w["x"], plane, w["via"], now)
        if w["dst"] is not None and self._clip_done(now):
            self.mode = "warp"
            self._warp_phase("crouch", "warp_crouch", now)
            return True
        return False

    def _warp_phase(self, phase, clip, now):
        self.warp["phase"], self.warp["since"] = phase, now
        if clip:
            self._set_clip(clip, now, restart=True)

    def _warp(self, now, dt):
        """Cast → crouch → leap → sink through the floor circle → (gone) → rise / drop → land."""
        w = self.warp
        if w is None:
            self.mode = "ground"
            return
        f, t = self.figure, now - w["since"]
        g = GRAVITY * self.scale * 0.8
        phase = w["phase"]
        sunk = self.floor + self._head_clearance("spell_dive") + 6
        if phase == "top_release":
            # The first pose still grips the ceiling. Once the shoes unhook,
            # descend during the horizontal pose so the upright head has room.
            beat = 1 / CLIPS[self.clip][2]
            p = max(0.0, min(1.0, (t - beat) / beat))
            self.y = w["origin_y"] + (w["release_y"] - w["origin_y"]) * p * p * (3 - 2 * p)
            if self._clip_done(now):
                w["origin_y"] = self.y
                self._warp_phase("top_sink", "warp_sink", now)
        elif phase == "top_sink":
            p = max(0.0, min(1.0, t / 0.6))
            target_y = w["plane"] + self._head_clearance("spell_dive") + 6
            self.y = w["origin_y"] + (target_y - w["origin_y"]) * p * p * (3 - 2 * p)
            if p >= 1:
                w["src"].closing = now
                self.top_peek = False
                self._warp_phase("gone", None, now)
        elif phase == "crouch":
            if t >= 0.2:
                self.vy = -math.sqrt(2 * g * f * 0.42)
                self._warp_phase("leap", "warp_leap", now)
        elif phase in ("leap", "slip"):
            self.vy += g * dt
            if self.y > self.floor:
                self.vy = min(self.vy, 620.0 * self.scale)     # the circle slows her like water
            self.y += self.vy * dt
            if phase == "leap" and self.vy > 0 and self.clip == "warp_leap":
                self._set_clip("warp_sink", now, restart=True)
            if self.portal_clip is None and self.y > self.floor - 1:
                self.portal_clip = (self.floor, "below")
                w["src"].flare = now
                self._burst(now, "sparkle", 0.0, -0.05, 5, spread=0.6)
            if self.y >= sunk:
                w["src"].closing = now
                self._warp_phase("gone", None, now)
        elif phase == "gone":
            if t >= 0.32:
                dst = w["dst"]
                self.x, self.vy = w["x"], 0.0
                self.facing = 1 if self.x < (self.left + self.right) / 2 else -1
                dst.flare = now
                if w["via"] == "floor":
                    self.y = sunk
                    self.portal_clip = (dst.y, "below")
                    self._warp_phase("rise", "warp_rise" if w["cause"] != "sneeze" else self._pick("fall", "fall"), now)
                else:
                    self.y = dst.y
                    self.portal_clip = (dst.y, "above")
                    self._warp_phase("drop", "warp_rise" if w["cause"] != "sneeze" else self._pick("fall", "fall"), now)
        elif phase == "rise":
            p = min(1.0, t / 0.5)
            top = self.floor - f * 0.45
            self.y = sunk + (top - sunk) * (1 - (1 - p) ** 3)
            if self.portal_clip and self.y < self.floor - 1:
                self.portal_clip = None
            if p >= 1:
                self.vy = 0.0
                self._warp_phase("fall", "warp_tada" if w["cause"] != "sneeze" else None, now)
                self._burst(now, "sparkle", 0.0, -0.6, 6, spread=0.7)
                w["dst"].closing = now
        elif phase in ("drop", "fall"):
            self.vy += g * dt
            self.y += self.vy * dt
            if self.portal_clip and self.portal_clip[1] == "above":
                if self.y - self._head_clearance(CLIPS[self.clip][0]) > self.portal_clip[0] + 2:
                    self.portal_clip = None
                    w["dst"].closing = now
                    if w["cause"] != "sneeze":
                        self._set_clip("warp_tada", now, restart=True)
            if self.y >= self.floor:
                self.y, self.vy = self.floor, 0.0
                self.portal_clip = None
                if w["dst"].closing is None:
                    w["dst"].closing = now
                self._burst(now, "sparkle", 0.0, -0.05, 4, spread=0.5)
                landing = "warp_land" if w["cause"] != "sneeze" else self._pick("land", "land")
                stumble = (w["cause"] != "sneeze" and self._has("edge_daily_warp_stumble")
                           and not self._warp_stumbled_last and self.rng.random() < 0.18)
                self._warp_stumbled_last = w["stumbled"] = stumble
                if stumble:
                    landing = "edge_daily_warp_stumble"
                self._warp_phase("land", landing, now)
        elif phase == "land" and self._clip_done(now):
            self._finish_warp(now)

    def _finish_warp(self, now):
        w, self.warp = self.warp, None
        self.mode, self.portal_clip = "ground", None
        self.y, self.vx, self.vy = self.floor, 0.0, 0.0
        for portal in self.portals:
            if portal.closing is None:
                portal.closing = now
        if w and w["cause"] == "prop" and self._chasing_prop is not None:
            busy = (self.chat_pending or self.media or now < self.agent_busy_until
                    or (self.state_clip and now < self.state_until))
            if busy:
                self._chasing_prop = None
            else:
                self._finish_prop_routine(now)
        if w and w["cause"] != "prop" and not self.chat_pending:
            key = {"sneeze": "sneeze_warp", "called": "warp_called"}.get(w["cause"])
            line = (self.rng.choice(LINES["sneeze_warp"]) if w["cause"] == "sneeze"
                    else self.rng.choice(LINES["warp_sky" if w["via"] == "ceiling" else "warp_in"]))
            if key != "warp_called":
                self.say("呼，站稳了。" if w.get("stumbled") else line, now=now, seconds=2.2, kind="status")
        if w and w["cause"] == "sneeze":
            self.plan.appendleft(Step("startle"))
        self._advance(now)
        if self.step is None:
            self._queue(Step("idle", 1.5), now=now)
            self._advance(now)

    def _cancel_warp(self, now):
        if ((self.warp and self.warp.get("cause") == "prop")
                or (self._warp_request and self._warp_request.get("cause") == "prop")):
            self._chasing_prop, self._warp_request = None, None
        if self.warp is None and not (self._edge_exit and "src" in self._edge_exit):
            return
        self.warp, self.portal_clip = None, None
        for portal in self.portals:
            if portal.closing is None:
                portal.closing = now

    def _air_play_allowed(self, now, *, continuing=False):
        return (self.motion and not self.quiet and not self.attention and not self.chat_pending
                and not self.game_active and not self.media and now >= self.agent_busy_until
                and not (self.state_clip and now < self.state_until)
                and self.mode in ("ground", "fly") and self._form() == "human"
                and self.warp is None and self.clip not in ASLEEP
                and (continuing or not self.air_play_active))

    def _air_cursor_check(self, now, x, y):
        if (not self._air_play_allowed(now) or not self.pointer.seen
                or not self._has("air_play_reach") or not self._has("air_play_miss")
                or not self._has("air_play_caught")
                or now - self._air_chase_at < AIR_CHASE_COOLDOWN
                or now - self._pounce_at < POUNCE_COOLDOWN
                or self.floor - self.ceiling < self.figure * 2.4
                or (self.mode == "ground" and self.posture() != "stand")):
            self._air_jiggles.clear()
            return
        f = self.figure
        dx, altitude = x - self.x, self.floor - y
        if self.mode == "ground":
            # A low wiggle in front of her still belongs to the ground pounce.
            low_pounce = f * 0.55 <= abs(dx) <= f * 2.4 and -f * 0.1 <= altitude <= f * 1.6
            nearby = not low_pounce and f * 1.15 <= altitude <= f * 3.2 and abs(dx) <= f * 2.4
        else:
            nearby = math.hypot(dx, y - (self.y - f * 0.65)) <= f * 1.8
        if not nearby or not self.left <= x <= self.right or y < self.ceiling + f * 0.35:
            self._air_jiggles.clear()
            return
        mx, my = x - self.pointer.x, y - self.pointer.y
        if abs(mx) + abs(my) < 2:
            return
        px, py = self._air_jiggle_dir
        if (px and mx and (px > 0) != (mx > 0)) or (py and my and (py > 0) != (my > 0)):
            self._air_jiggles.append(now)
        self._air_jiggle_dir = (mx or px, my or py)
        if sum(now - t < 1.0 for t in self._air_jiggles) >= 3:
            self._air_jiggles.clear()
            self._start_air_chase(now, x, y)

    def _air_target(self, x, y, origin):
        f = self.figure
        tx, ty = x - self.facing * f * AIR_HAND[0], y - f * AIR_HAND[1]
        dx, dy = tx - origin[0], ty - origin[1]
        distance = math.hypot(dx, dy)
        if distance > f * AIR_CHASE_RANGE:
            tx = origin[0] + dx * f * AIR_CHASE_RANGE / distance
            ty = origin[1] + dy * f * AIR_CHASE_RANGE / distance
        return self._clamp_x(tx, edge_side=0), max(self.ceiling + f, min(self.floor - f * 0.25, ty))

    def _start_air_chase(self, now, x=None, y=None):
        if (not self._air_play_allowed(now) or not self.pointer.seen
                or not self._has("air_play_reach") or not self._has("air_play_miss")
                or not self._has("air_play_caught")
                or now - self._air_chase_at < AIR_CHASE_COOLDOWN):
            return False
        x, y = self.pointer.x if x is None else x, self.pointer.y if y is None else y
        origin = (self.x, self.y)
        if abs(x - self.x) > self.figure * 0.15:
            self.facing = 1 if x > self.x else -1
        _, height = self._air_target(x, y, origin)
        flap = self._pick("fly", "fall", family="flap")
        steps = []
        if self.mode == "ground":
            steps = [Step(self._pick("fly", "hop", family="takeoff")), Step(flap, target_y=height)]
        steps += [Step(flap, AIR_CHASE_SECONDS, hold="air_chase"), Step("air_play_reach"),
                  *self._air_return_steps(), Step("idle", 1.5)]
        self._air_chase_at = self._pounce_at = now
        self._chasing_prop = None
        self.plan.clear()
        self.step = None
        self._queue(*steps, now=now)
        self._air_chase = {"origin": origin, "caught": None}
        self._advance(now)
        return True

    def _track_air_cursor(self, dt):
        if abs(self.pointer.x - self.x) > self.figure * 0.15:
            self.facing = 1 if self.pointer.x > self.x else -1
        tx, ty = self._air_target(self.pointer.x, self.pointer.y, self._air_chase["origin"])
        dx, dy = tx - self.x, ty - self.y
        distance = math.hypot(dx, dy)
        amount = min(1.0, 140 * self.scale * dt / distance) if distance else 0.0
        self.x, self.y = self.x + dx * amount, self.y + dy * amount

    def _air_hands(self):
        return self.x + self.facing * self.figure * AIR_HAND[0], self.y + self.figure * AIR_HAND[1]

    def _resolve_air_reach(self, now):
        if (self._air_chase is None or self._air_chase["caught"] is not None
                or self.clip != "air_play_reach" or self._frame(now)[1] < 2):
            return
        hx, hy = self._air_hands()
        caught = self.pointer.seen and math.hypot(self.pointer.x - hx, self.pointer.y - hy) <= self.figure * AIR_CATCH_RADIUS
        self._air_chase["caught"] = caught
        if caught:
            # A brief contact glint leaves the palms and face readable; rising
            # hearts used to cover both before the satisfied hover could play.
            self.particles.append(Particle("sparkle", AIR_HAND[0] * self.facing, AIR_HAND[1],
                                           self.facing * 0.06, -0.04, now, 0.4))
            # Show the closed palms for their full half-second, then acknowledge
            # contact instead of playing the final empty-handed inspection.
            self.step.duration = 1.5
            self.plan.appendleft(Step("air_play_caught"))
        else:
            self.plan.appendleft(Step("air_play_miss"))

    def _pounce_check(self, now, x, y):
        """Wiggle the cursor just in front of her and she crouches, wiggles back and pounces."""
        if (not self.motion or self.quiet or self.attention or self.chat_pending or self.game_active
                or not self._has("spell_pounce") or not self.pointer.seen
                or self.mode != "ground" or self.posture() != "stand" or self.clip in ASLEEP
                or now - self._pounce_at < POUNCE_COOLDOWN or self.media or now < self.agent_busy_until
                or (self.state_clip and now < self.state_until) or self.clip.startswith("pounce")):
            self._jiggles.clear()
            return
        f = self.figure
        dx, dy = x - self.x, self.floor - y
        if not (f * 0.55 <= abs(dx) <= f * 2.4 and -f * 0.1 <= dy <= f * 1.6):
            self._jiggles.clear()
            return
        px, py = self._jiggle_dir
        mx, my = x - self.pointer.x, y - self.pointer.y
        if abs(mx) + abs(my) < 2:
            return
        if (px and mx and (px > 0) != (mx > 0)) or (py and my and (py > 0) != (my > 0)):
            self._jiggles.append(now)
        self._jiggle_dir = (mx or px, my or py)
        if sum(1 for t in self._jiggles if now - t < 1.0) >= 3:
            self._jiggles.clear()
            self._pounce_at = now
            self._pounce_target = (x, y)
            self.facing = 1 if dx > 0 else -1
            self._queue(Step("pounce_crouch", self.rng.uniform(0.7, 1.2)), Step("pounce_leap"),
                        now=now, interrupt=True)
            self.facing = 1 if dx > 0 else -1

    def _launch_pounce(self, now):
        tx, ty = self._pounce_target or (self.pointer.x, self.pointer.y)
        f = self.figure
        target = self._clamp_x(self.x + max(-f * 1.8, min(f * 1.8, tx - self.x - self.facing * f * 0.2)))
        height = max(f * 0.25, min(f * 0.9, self.floor - ty - f * 0.55))
        g = GRAVITY * self.scale
        self.vy = -math.sqrt(2 * g * height)
        self.vx = (target - self.x) / max(0.2, 2 * -self.vy / g)
        self.facing = 1 if self.vx >= 0 else -1
        self.mode, self._landing = "air", "pounce_catch"
        self.plan.clear()
        self._emit("!", now)

    def _land_trick(self, now):
        clip, self._landing = self._landing, None
        self.plan.clear()
        self.step = None
        if clip == "pounce_catch":
            hands = (self.x + self.facing * self.figure * 0.25, self.floor - self.figure * 0.45)
            caught = self.pointer.seen and math.hypot(self.pointer.x - hands[0], self.pointer.y - hands[1]) < self.figure * 0.75
            if not caught and self._has("scene_pounce_miss"):
                clip = "scene_pounce_miss"
            self.say(self.rng.choice(LINES["pounce_caught" if caught else "pounce_missed"]), now=now, seconds=2)
            self._burst(now, "heart" if caught else "sweat", 0.25, -0.55, 3 if caught else 1)
        self._set_clip(clip, now, restart=True)
        self._queue(Step(clip, 1.1), Step("idle", 1.5), now=now, interrupt=True)

    def _burst(self, now, kind, x, y, count=1, *, spread=0.12):
        """Particles at a point given in figure heights from her feet (x points the way she faces)."""
        if not self.motion:
            return
        for _ in range(count):
            vx = self.rng.uniform(-0.12, 0.12) * (spread / 0.12) ** 0.5
            vy = -self.rng.uniform(0.15, 0.35)
            if kind == "ember":
                vx, vy = self.facing * self.rng.uniform(0.25, 0.55), self.rng.uniform(-0.3, 0.05)
            elif kind == "smoke":
                vx, vy = self.facing * self.rng.uniform(0.05, 0.15), -self.rng.uniform(0.12, 0.22)
            self.particles.append(Particle(
                kind, x * self.facing + self.rng.uniform(-spread, spread) * 0.25, y + self.rng.uniform(-0.03, 0.03),
                vx, vy, now, {"ember": 0.7, "smoke": 1.4, "paint": 1.1, "glyph": 1.6}.get(kind, 1.2)))

    def _can_dragon(self, now, *, manual=False):
        return (self.motion and (manual or not self.quiet and now - self._dragon_at >= DRAGON_COOLDOWN)
                and self._has("form_dragon") and self._has("form_recover")
                and any(self._has(name) for name in DRAGON_TRICKS))

    def _dragon_steps(self, now, *, trick=None):
        """Puff into her little dragon form, do one thing with it, puff back and tidy up.

        The swap itself is the puff in _set_clip; a cue, a poke or a drag in between
        brings her straight back to two legs the same way.
        """
        self._dragon_at = now
        tricks = [name for name in DRAGON_TRICKS if self._has(name)]
        fresh = [name for name in tricks if name not in self._dragon_recent]
        trick = trick if trick in tricks else self.rng.choice(fresh or tricks)
        start, end = DRAGON_TRICKS[trick]
        steps = [Step("form_dragon", self.rng.uniform(1.4, 2.2), line=self.rng.choice(LINES["dragon"]))]
        if self._has("form_dragon_hop") and self.rng.random() < 0.6:
            room = [x for x in (self.x - self.figure * 1.3, self.x + self.figure * 1.3)
                    if self.left + self.figure * 0.6 <= x <= self.right - self.figure * 0.6]
            if room:
                steps.append(Step("form_dragon_hop", target_x=self.rng.choice(room)))
        if trick == "form_dragon_guard":
            # Let her stay asleep at the end rather than replaying the whole
            # guarding-and-dozing entrance a second time.
            seconds = len(CLIPS[trick][1]) / CLIPS[trick][2]
            steps.append(Step(trick, seconds + self.rng.uniform(2.5, 4.5), line=start))
        else:
            steps += [Step(trick, line=start), Step("form_dragon", 1.2)]
        recovery = "form_recover" if trick == "form_dragon_reach" else "form_tidy"
        steps += [Step(recovery, line=end), Step("idle", 1.5)]
        return steps

    def _poof(self, now):
        """A cartoon puff of cloud over her whole body, dense at once and thinning out, plus a few sparkles."""
        if not self.motion:
            return
        self._poof_at = now
        for i in range(10):
            angle = i / 10 * math.tau + self.rng.uniform(-0.25, 0.25)
            x, y = math.cos(angle) * 0.2, -0.42 + math.sin(angle) * 0.3
            self.particles.append(Particle("cloud", x, y, x * 0.5, (y + 0.42) * 0.4 - 0.05, now, 0.85))
        self.particles.append(Particle("cloud", 0.0, -0.42, 0.0, -0.06, now, 0.75))
        self._burst(now, "sparkle", 0.0, -0.5, 4, spread=0.9)

    def _spell_particles(self, now):
        """Beat-timed effects: the sneeze's flame, paint flecks off the brush, glyphs off the tome."""
        if not self.motion:
            return
        elapsed = now - self.clip_started
        if self.clip == "sneeze" and elapsed >= 1.0 and self._sneezed_at < self.clip_started:
            self._sneezed_at = now
            self._burst(now, "ember", 0.32, -0.66, 7)
            self._burst(now, "smoke", 0.3, -0.7, 3)
            self.say(self.rng.choice(LINES["sneeze"][:2]), now=now, seconds=1.4)
            self._sneeze_slip = self._sneeze_warp or self.rng.random() < 0.18
            self._sneeze_warp = False
            return
        if self.clip == "sneeze" and elapsed >= 1.45 and self._sneeze_slip and self.mode == "ground":
            # The sneeze itself plays out first; then a circle pops open under her by accident.
            self._sneeze_slip = False
            self._warp_request = {"x": None, "via": "ceiling", "cause": "sneeze"}
            self.plan.clear()
            self._begin_warp(now, slip=True)
            return
        if now < self._spark_at:
            return
        strip, frame, _ = self._frame(now)
        if self.clip == "paint" and frame in (1, 3):
            self._spark_at = now + 0.4
            self._burst(now, "paint", 0.4, -0.5, 2, spread=0.08)
        elif self.clip == "tome":
            self._spark_at = now + 0.55
            self._burst(now, "glyph", 0.3, -0.72, 1, spread=0.35)
        elif self.clip == "paint_reveal" and frame in (2, 3):
            self._spark_at = now + 0.3
            self._burst(now, "sparkle", 0.05, -1.05, 1, spread=0.5)

    # ── particles and speech ──
    def _emit(self, kind, now, count=1):
        if not self.motion:
            return
        for _ in range(count):
            inward = kind == "z" and self.mode == "hidden" and self.hidden_asleep and self.hidden_edge == "right"
            top = kind == "z" and self.mode == "hidden" and self.hidden_asleep and self.hidden_edge == "top"
            self.particles.append(Particle(
                kind, (-0.2 if inward else 0.2) if kind == "z" else self.rng.uniform(-0.14, 0.14),
                0.15 if top else -0.95 if kind in ("z", "note", "heart", "sweat", "!", "puff") else -0.55,
                (-0.07 if inward else 0.07 if kind == "z" else 0.0) + self.rng.uniform(-0.04, 0.04),
                -self.rng.uniform(0.16, 0.3), now, 1.0 if kind in ("!", "puff") else 1.7))

    def _particles(self, now, dt):
        self._spell_particles(now)
        kind = {"sleep": "z", "sing": "note", "shy": "heart", "cheer": "sparkle",
                "hop": "sparkle", "eat": "crumb"}.get(self.clip)
        if self.mode == "hidden" and self.hidden_asleep:
            kind = "z"
        if kind and self.motion and now >= self._emit_at:
            self._emit(kind, now)
            self._emit_at = now + {"z": 1.3, "note": 0.7, "heart": 0.5}.get(kind, 0.45)
        alive = []
        for p in self.particles:
            if now - p.born < p.life:
                p.x += p.vx * dt
                p.y += p.vy * dt
                alive.append(p)
        self.particles = alive[-36:]

    def say(self, text, *, now, seconds=None, kind="say"):
        text = str(text).strip()
        if not text:
            return
        self._serial += 1
        seconds = seconds if seconds is not None else max(3.0, min(14.0, 2.2 + len(text) * 0.12))
        self.bubble = Bubble(text, kind, now + seconds, self._serial)

    def _status_line(self, key, now, *, gap=75.0, starts=False):
        """Narrate agent activity sparingly: per-kind gap, never over a reply or on top of another line.

        starts=True: she just started something long (searching, drawing, singing); that line may replace
        a status line from a moment ago ("（在群里回了一句）" for the "我去查查" right before it).
        """
        if (now - self._last_lines.get(key, -1000.0) < gap
                or (not starts and now - self._last_status_line < 6)
                or self.chat_pending or (self.bubble is not None and self.bubble.kind == "say")):
            return
        self._last_status_line = self._last_lines[key] = now
        self.say(self.rng.choice(LINES[key]), now=now, seconds=3.2, kind="status")

    # ── user input ──
    def hover(self, now, x, y, *, near):
        p = self.pointer
        if near and not p.near:
            p.since = now
        p.near = near
        if not near or self.mode == "fly":
            self._air_cursor_check(now, x, y)
        if not near:
            self._pounce_check(now, x, y)
        left, top, right, bottom = self.body_rect()
        over_head = near and left <= x <= right and top <= y <= top + (bottom - top) * 0.42
        dx = x - p.x
        if over_head and self._press is None and abs(dx) > 1.5:
            if p.last_dx and (dx > 0) != (p.last_dx > 0):
                p.reversals.append(now)
            p.last_dx = dx
            if sum(1 for t in p.reversals if now - t < 1.4) >= 4 and self.mode == "ground":
                p.reversals.clear()
                self._petted(now)
        p.x, p.y, p.seen = x, y, True

    def _petted(self, now):
        """Hovering back and forth over her head is a head pat; clicking the head does the same."""
        self.last_interaction = now
        if self.clip in ASLEEP:
            self.say("（翻了个身）", now=now, seconds=2, kind="status")
            return
        if self.clip == "pat" or self.chat_pending:
            return
        self._pat(now)

    def _pat(self, now):
        state = self._care(now, "pat")
        self._react(now, "pat", self._care_lines("pat", state), "heart",
                    seconds=2.4, emit=4 if state == "gain" else 1)
        # 被夸后忍不住露一手：小火苗或者变一下小龙，都是偶尔的。
        if (self.motion and not self.quiet and now - self._show_off_at >= SHOW_OFF_COOLDOWN
                and self.rng.random() < 0.2):
            trick = None
            if self._can_dragon(now) and self.rng.random() < 0.4:
                trick = self._dragon_steps(now, trick="form_dragon_flap")
            elif self._has("scene_fire_controlled"):
                trick = [Step("scene_fire_controlled", line=self.rng.choice(LINES["show_off"])), Step("idle", 1.5)]
            if trick:
                self._show_off_at = now
                self.plan.extend(trick)

    def press(self, now, x, y):
        self._press = (now, x, y)
        self.last_interaction = now

    def touch_prop(self, prop_id, now):
        kind = self.props.tap(prop_id, now)
        if kind and self.mode == "ground" and not self.chat_pending:
            role = "treasure" if kind in ("coin", "gem") else "play" if kind == "ball" else "celebrate"
            self._queue(Step(self._pick(role, "peek")), Step("idle", 1), now=now, interrupt=True)
        return kind

    def prop_dragged(self, prop_id, now):
        item = next((p for p in self.props.items if p.id == prop_id), None)
        if (item and item.kind in ("ball", "coin", "gem") and now - self._prop_voice_at >= 5
                and not self.chat_pending and not self.media and now >= self.agent_busy_until
                and not (self.state_clip and now < self.state_until)):
            self._prop_voice_at = now
            self.say("我的球～" if item.kind == "ball" else "我的宝贝～", now=now, seconds=1.8, kind="status")

    def return_prop(self, prop_id, now):
        kind = self.props.discard(prop_id, now)
        if kind and not self.chat_pending and not self.media and now >= self.agent_busy_until and not (self.state_clip and now < self.state_until):
            self.say("收好啦。", now=now, seconds=1.8, kind="status")
        return kind

    def drag(self, now, x, y):
        if self._press is None or self.mode == "warp":
            return                       # mid-spell she cannot be picked up out of the circle
        _, px, py = self._press
        if self.mode != "drag":
            if math.hypot(x - px, y - py) < 4:
                return
            self._cancel_warp(now)
            self._air_chase = None
            self._air_returning = False
            self.mode = "drag"
            self._landing = None
            self._edge_exit = None
            self.hidden_asleep = False
            self._hidden_slip_started = None
            self.hidden_edge = None
            self.top_peek = False
            self.bottom_peek = False
            self._grab = {"dx": self.x - x, "dy": self.y - y, "cx": x, "cy": y, "samples": deque(maxlen=6)}
            self.plan.clear()
            self.step = None
            self._set_clip(self._pick("drag", "dangle"), now, restart=True)
            self._emit("sweat", now)
            if self.rng.random() < 0.35:
                self.say(self.rng.choice(LINES["lifted"]), now=now, seconds=2.2)
        grab = self._grab
        grab["cx"], grab["cy"] = x, y
        grab["samples"].append((now, x, y))
        (t1, x1, y1), (t2, x2, y2) = grab["samples"][0], grab["samples"][-1]
        if t2 > t1:
            self.vx, self.vy = (x2 - x1) / (t2 - t1), (y2 - y1) / (t2 - t1)

    def release(self, now, x, y):
        """Return "drop" after a drag, "click" for a press without movement."""
        pressed, self._press = self._press, None
        self.last_interaction = now
        if self.mode == "drag":
            grab = self._grab
            fresh = [s for s in self._grab["samples"] if now - s[0] < 0.12]
            if len(fresh) < 2:
                self.vx = self.vy = 0.0
            self.vx = max(-2600.0, min(2600.0, self.vx))
            self.vy = max(-2200.0, min(1400.0, self.vy))
            edge = ("left" if grab["cx"] <= self.left + self.figure * EDGE else
                    "right" if grab["cx"] >= self.right - self.figure * EDGE else
                    "bottom" if grab.get("boundary") == "floor" else
                    "top" if grab.get("boundary") == "ceiling" else None)
            self._grab = None
            if edge:
                self.hidden_edge, self.mode = edge, "hiding"
                self._fade = None
                self._hidden_side_y = self.y if edge in ("left", "right") else None
                self.vx = self.vy = 0.0
                self.bottom_peek = edge == "bottom"
                self.top_peek = edge == "top"
                if edge in ("left", "right"):
                    self.facing = -1 if edge == "left" else 1
                preferred = (("boundary_top_look" if "boundary_top_look" in CLIPS else "boundary_top_peek") if edge == "top" else "motion_bottom_look" if edge == "bottom"
                             else "motion_edge_look")
                clip = preferred if preferred in CLIPS else self._pick(
                    "bottom" if edge == "bottom" else "edge", "dangle" if edge in ("bottom", "top") else "edge_peek")
                self._hidden_clip = clip
                self._hide_origin, self._hide_started = (self.x, self.y), now
                hx, hy = self._hidden_target()
                self._hide_duration = max(0.35, math.hypot(hx - self.x, hy - self.y) / max(1.0, self.figure * 1.2))
                entry = ("walk" if edge in ("left", "right") else
                         "boundary_tuck" if edge == "bottom" and "boundary_tuck" in CLIPS else clip)
                self._set_clip(entry, now, restart=True)
                return "drop"
            self.mode = "air"
            self._air_top = self.y
            self._set_clip(self._pick("fall", "fall"), now, restart=True)
            return "drop"
        return "click" if pressed is not None else None

    @property
    def effective_mood(self):
        """只读的群聊心情 + 被照顾出来的加成；presence 本身永远不改。"""
        base = self.presence.mood if self.presence.known else 0.0
        return max(-1.0, min(1.0, base + self.warmth))

    def _decay_warmth(self, now):
        """按真实经过的时间回落，而不是按帧数——时间跳跃也不会留下假心情。"""
        self.warmth = decay(self.warmth, self._warmth_at, now)
        self._warmth_at = now

    def _care(self, now, kind):
        """一次有意的照顾真的抬高心情，但有上限和各自独立的冷却。

        「到顶」看的是这次还能加多少，而不是 warmth 是否正好等于上限——
        它会一直衰减，照等号判断的话这句提醒永远不会出现。
        """
        if self.external_care:
            self.care_requests.append(kind)
            return "pending"
        self._decay_warmth(now)
        self.warmth, self._care_at[kind], result = change(self.warmth, self._care_at[kind], kind, now)
        return result

    def sync_care(self, care, now):
        if not isinstance(care, dict):
            return
        stamp = care.get("updated_at")
        if type(stamp) in (int, float) and math.isfinite(stamp):
            if stamp < self._care_snapshot_at:
                return
            self._care_snapshot_at = stamp
        warmth = care.get("warmth")
        if type(warmth) in (int, float) and math.isfinite(warmth):
            self.warmth = max(0.0, min(CARE_MAX, warmth))
            self._warmth_at = now

    def receive_care(self, now, event):
        """A panel-confirmed effect; displaying it never applies the action again."""
        if not isinstance(event, dict) or self.has_seen(event):
            return
        self._seen_events.append(self._event_key(event))
        action, result = event.get("action"), event.get("result")
        if action not in CARE_GAIN or result not in ("gain", "cooldown", "full", "fed_full"):
            return
        offered_here = action == "feed" and self._feed_waiting
        if action == "feed":
            self._feed_waiting = False
        self.last_interaction = now
        if self.mode != "ground" or self.chat_pending:
            return
        self.cancel_game(now)
        text = event.get("text")
        text = text[:120] if isinstance(text, str) and text else None
        if action == "feed" and result != "fed_full":
            # The click only anticipated a cookie while the panel was deciding.
            # Continue that same scene on receipt, without another random actor
            # or restarting the anticipation already on screen.
            waiting = self.clip == "feed_expect" or any(step.clip == "feed_expect" for step in self.plan)
            if waiting:
                while self.plan and self.plan[-1].clip != "feed_expect":
                    self.plan.pop()
                self._queue(*self._feed_steps(text), now=now)
            elif offered_here:
                self._queue(*self._feed_steps(text), now=now, interrupt=True)
            else:
                self._queue(Step("feed_expect"), *self._feed_steps(text), now=now, interrupt=True)
        else:
            clip = "peek" if result == "fed_full" else "pat"
            self._queue(Step(clip), Step("idle", 1.5), now=now, interrupt=True)
            if text:
                self.say(text, now=now, seconds=2.8)
        if result == "gain":
            self._emit("heart", now, 3)

    def _care_lines(self, kind, state):
        if state == "pending":
            return ("给我的？",) if kind == "feed" else ("（蹭了蹭你的手）",)
        if state == "gain":
            return CARE_LINES[kind]
        if state == "full":
            return CARE_FULL_LINES
        plain = PLAY["pat"][1] if kind == "pat" else LINES["fed"]
        return (*CARE_COOL_LINES[kind], *plain)

    def _tap_zone(self, x, y):
        """Head / body / feet of the figure in screen points; unknown coordinates count as the body."""
        if x is None or y is None:
            return "body"
        left, top, right, bottom = self.body_rect()
        if not (left <= x <= right and top <= y <= bottom):
            return "body"
        frac = (y - top) / max(1.0, bottom - top)
        return "head" if frac < 0.42 else "feet" if frac > 0.82 else "body"

    def _feed_steps(self, line=None):
        """Accept, bite, then respond with the same cookie and body throughout."""
        return [Step("feed_accept"), Step("feed_bite"), Step("feed_content", line=line), Step("idle", 1.5)]

    def _next_tap_clip(self):
        """洗牌袋：一轮里每个小幅动作都轮到一次，且不与上一次重复。"""
        if not self._tap_bag:
            self._tap_bag = list(TAP_POOL)
            self.rng.shuffle(self._tap_bag)
        index = len(self._tap_bag) - 1
        if self._tap_bag[index] == self._last_tap_clip and len(self._tap_bag) > 1:
            index = 0
        clip = self._tap_bag.pop(index)
        self._last_tap_clip = clip
        return clip

    def _react(self, now, clip, line, particle="sparkle", seconds=2.2, emit=1):
        self._queue(Step(clip), Step("idle", 1.5), now=now, interrupt=True)
        self._emit(particle, now, emit)
        self.say(self.rng.choice(line), now=now, seconds=seconds)

    def poke(self, now, x=None, y=None):
        """A click is a small greeting. Where you click picks the reaction.

        No foot stomping, no turning away, no accusing finger, no escalation into a sulk:
        consecutive taps simply rotate through a pool of near-static gestures.
        """
        self.pokes.append(now)
        self.last_interaction = now
        if self.mode in ("hiding", "hidden") and self.hidden_edge:
            self._emerge(now)
            return
        if self.game_active:
            # 一局进行中，所有点击都归游戏：既不会被双击聊天抢走，也不会
            # 顺手再放一个小动作盖掉命中演出。
            self._last_tap_at = now
            if self.game_until and now <= self.game_until and self.mode == "ground" and not self.chat_pending:
                self.game_until = 0.0
                self._game_hit(now)
            elif not self.chat_pending:
                self.say(self.rng.choice(PLAY_WAIT), now=now, seconds=1.2)
            return
        if self.chat_pending or self.mode != "ground" or now - self._last_tap_at < 0.30:
            return
        self._last_tap_at = now
        if self.clip in ASLEEP:
            # Waking stays a slow, seated stretch: stir, sit up, heavy-lidded look, stand.
            # Nodding off while still seated skips the lying frames.
            self.jolt_started = now
            lying = [Step("wake")] if self.posture() == "lie" else []
            self._queue(*lying, Step("grumble", 1.2), Step("get_up"), Step("idle", 2),
                        now=now, interrupt=True)
            self.say(self.rng.choice(LINES["woken"]), now=now, seconds=3.2)
            return
        if self.clip in WAKING:
            if self.bubble is None:
                self.say(self.rng.choice(LINES["waking"]), now=now, seconds=2)
            return
        if self.posture() in ("sit", "lie"):
            # 坐着看书喝茶、趴着托腮时应一声，不为了一个小动作站起来。
            if (self._tap_zone(x, y) == "body" and self.motion and not self.quiet and self._has("spell_sneeze")
                    and now - self._last_trick_at >= 120 and self.rng.random() < 0.08):
                self._last_trick_at = now
                self.jolt_started = now        # 坐着就地打个小喷嚏，不为一个小动作站起来
                self._burst(now, "ember", 0.2, -0.62, 4)
                self._burst(now, "smoke", 0.2, -0.64, 2)
                self.say(LINES["sneeze"][0], now=now, seconds=1.6)
                return
            self.jolt_started = now
            self.say(self.rng.choice(LINES["poke"]), now=now, seconds=2)
            return
        recent = sum(1 for t in self.pokes if now - t < 6)
        zone = self._tap_zone(x, y)
        if zone == "head":
            self._last_tap_clip = "pat"
            self._pat(now)
        elif zone == "feet":
            self._last_tap_clip = "peek"
            self._react(now, "peek", LINES["tapped_feet"], "sparkle")
        else:
            clip = self._next_tap_clip()
            lines = PLAY[clip][1]
            if (self.motion and not self.quiet and self._has("spell_sneeze")
                    and now - self._last_trick_at >= 120 and self.rng.random() < 0.08):
                self._last_trick_at = now
                if self.rng.random() < 0.5 and self._can_warp() and self._warp_target() is not None:
                    # 被戳烦了就开个阵溜到别处，再回头冲你眨眼。
                    self._warp_request = {"x": None, "via": None, "cause": "spell"}
                    self._queue(Step("warp_cast", line="抓不到我～"), Step("idle", 1.5), now=now, interrupt=True)
                else:
                    self._queue(Step("sneeze"), Step("idle", 1.5), now=now, interrupt=True)
                return
            self._react(now, clip, lines,
                        "heart" if clip in ("shy", "peek") else "sparkle")
            if recent >= 3 and self.rng.random() < 0.35:
                self.say(self.rng.choice(LINES["poked_more"]), now=now, seconds=2.2)

    def _game_round(self, now):
        """抬一次手，等你在窗口里点到她。"""
        window = max(PLAY_MIN_WINDOW, PLAY_WINDOW - PLAY_SPEEDUP * self.game_streak)
        self.game_until = now + window
        self._queue(Step("highfive", window), Step("idle", 1.5), now=now, interrupt=True)
        self._emit("sparkle", now, 2)
        prompt = PLAY_START if self.game_streak == 0 else PLAY_AGAIN
        self.say(self.rng.choice(prompt), now=now, seconds=window)

    def _game_hit(self, now):
        self.game_streak += 1
        record = self.game_streak > self.game_best
        self.game_best = max(self.game_best, self.game_streak)
        milestone = self.game_streak >= 3 and self.game_streak % 3 == 0
        if record and self.game_streak >= 3:
            text = self.rng.choice(PLAY_BEST).format(n=self.game_streak)
        elif self.game_streak >= 2:
            text = self.rng.choice(PLAY_STREAK).format(n=self.game_streak)
        else:
            text = self.rng.choice(PLAY_HIT)
        self._queue(Step("cheer" if milestone else "nod"), Step("idle", 2.0),
                    now=now, interrupt=True)
        self._emit("heart" if milestone else "sparkle", now, 5 if milestone else 2)
        self.say(text, now=now, seconds=2.6)
        if self.game_streak >= PLAY_MAX_STREAK:
            self._game_miss(now, done=True)       # 一口气接满，收工
        else:
            self.game_next_at = now + PLAY_GAP    # 自动抬下一手，不用再点圆盘

    def _game_miss(self, now, *, done=False):
        """停手 / 超时就结算这一轮；done=True 是接满收工，不算失手。"""
        streak, best = self.game_streak, self.game_best
        self.game_active = False
        self.game_until = self.game_next_at = 0.0
        self.game_streak = 0
        if streak >= 2:
            text = self.rng.choice(PLAY_OVER).format(n=streak, m=best)
        else:
            text = "手真快，收工啦。" if done else self.rng.choice(PLAY_MISS)
        if not done:
            self._queue(Step("peek"), Step("idle", 1.8), now=now, interrupt=True)
        self.say(text, now=now, seconds=2.8)

    def cancel_game(self, now):
        """聊天 / 语音 / 投喂抢走了点击：本局作废，但不算失手、不扣纪录。"""
        if not self.game_active:
            return
        self.game_active = False
        self.game_until = self.game_next_at = 0.0
        self.say(PLAY_CANCEL, now=now, seconds=2.0)

    def play(self, now, kind=None):
        """「玩一下」开一局击掌接龙：她反复抬手，你一直点，停手就结算。

        一局里所有点击都归游戏，所以双击弹聊天这个入口必须让开。聊天只走
        圆盘的「聊两句」和网页工作台；它们打开时会取消本局，但不算失手。
        """
        self.last_interaction = now
        if self.chat_pending or self.mode != "ground" or now - self._last_play_at < 0.35:
            return
        self._last_play_at = now
        if self.clip in ASLEEP or self.clip in WAKING:
            self.poke(now)
            return
        if self.game_active:
            return
        self.game_active = True
        self.game_streak = 0
        self._game_round(now)

    def feed(self, now):
        self.feeds.append(now)
        self.last_interaction = now
        if self.chat_pending or self.mode != "ground":
            return
        self.cancel_game(now)               # 投喂是有意切换，别让她一直举着手
        if not self.external_care and sum(1 for t in self.feeds if now - t < 600) > 4:
            # 吃不下也只是歪头看你，不再跺脚生气。
            self._queue(Step("peek"), Step("idle", 2), now=now, interrupt=True)
            self.say(self.rng.choice(LINES["full"]), now=now, seconds=2.6)
            return
        self.energy = min(1.0, self.energy + 0.15)
        state = self._care(now, "feed")
        self._feed_waiting = state == "pending"
        # Sharing remains a daily scene. A user-offered cookie does not swap
        # between differently drawn bodies, and a pending receipt never eats it.
        ending = ([Step("idle", 3.0)] if state == "pending" else
                  self._feed_steps(self.rng.choice(self._care_lines("feed", state))))
        self._queue(Step("feed_expect"), *ending, now=now, interrupt=True)
        self.say("给我的？", now=now, seconds=1.6)
        if state == "gain":
            self._emit("heart", now, 3)

    def enter(self, now, height):
        """Drop in from above the floor (startup entrance)."""
        if self.motion:
            self.y, self.mode = self.floor - height, "air"
            self._set_clip("fall", now, restart=True)

    def settle(self, now):
        """Stop walking where she is (e.g. the user just asked her to stay put)."""
        if self.mode == "ground" and (self.clip == "walk" or self.edge_side):
            self._queue(Step("idle", 1.5), now=now, interrupt=True)

    def set_attention(self, value, now):
        """Toolbar or chat open: stop wandering and face the user."""
        value = bool(value)
        if value and not self.attention and self.mode in ("fly", "bottom"):
            self._queue(Step("idle", 1.5), now=now, interrupt=True)
        if (value and not self.attention and self.mode == "ground"
                and (self.clip == "walk" or self.edge_side) and not self._retreating()):
            self._queue(Step("idle", 1.5), now=now, interrupt=True)
        self.attention = value

    # ── local chat ──
    def chat_sent(self, now):
        self.cancel_game(now)
        self.chat_pending = True
        self.set_state("think", now, source="local")
        self.last_interaction = now
        if self.mode == "ground":
            busy = "laptop" if self.posture() == "sit" else "think"
            self._queue(Step(busy, hold="chat"), now=now, interrupt=True)
        self.say("…", now=now, seconds=80, kind="think")

    def chat_reply(self, now, text, *, animate=True):
        self.chat_pending = False
        if animate:
            self.set_state("reply", now, source="local", seconds=max(2.5, min(8.0, len(text) * 0.09)))
        self.say(text, now=now, seconds=max(6.0, min(30.0, 4 + len(text) * 0.16)))

    def chat_failed(self, now, message):
        self.chat_pending = False
        self.clear_state(now, source="local")
        if self.mode == "ground":
            self._queue(Step("lookaround"), Step("idle", 2), now=now, interrupt=True)
        self.say(message, now=now, seconds=6, kind="status")

    # ── agent state (read-only, from the panel) ──
    def observe(self, now, data):
        """Apply one /companion/presence snapshot. Unknown fields are ignored."""
        if not isinstance(data, dict):
            return
        self.sync_care(data.get("care"), now)
        old = self.presence
        mood, chatter = data.get("mood"), data.get("chatter")
        new = Presence(
            available=data.get("available") is True, qq_connected=data.get("qq_connected") is True,
            bot=data.get("bot") if data.get("bot") in ("ready", "offline") else "unknown",
            mood=max(-1.0, min(1.0, float(mood))) if type(mood) in (int, float) and math.isfinite(mood) else 0.0,
            circadian=data.get("circadian") if isinstance(data.get("circadian"), str) else "",
            chatter=max(0, min(500, chatter)) if type(chatter) is int else 0, known=True)
        self.presence = new
        # 面板每次心跳都会带上「此刻」的状态；不带就是状态结束了，让它自然回落。
        state = data.get("state")
        if isinstance(state, str) and state in STATE_CLIPS:
            self.set_state(state, now)
        elif "state" in data and state is None and self.state_source == "panel":
            self.clear_state(now, source="panel")
        local_active = self.state_source == "local" and now < self.state_until
        if old.known and old.bot == "ready" and new.bot == "offline" and not local_active:
            self.clear_state(now)
            self._status_line("offline", now, gap=0)
            if self.mode == "ground" and not self.chat_pending and self.clip not in ASLEEP:
                self._queue(Step("stretch"), Step("idle", 2), now=now, interrupt=True)
        elif old.known and old.bot == "offline" and new.bot == "ready":
            self._status_line("online", now, gap=0)
            if self.mode == "ground" and self.clip in ASLEEP:
                self._queue(Step("wake"), Step("get_up"), now=now, interrupt=True)
        for event in data.get("events") or ():
            if not isinstance(event, dict) or self.has_seen(event):
                continue
            if event.get("kind") == "care":
                self.receive_care(now, event)
                continue
            self._seen_events.append(self._event_key(event))
            if self.mode == "hidden" and event.get("kind") in CALLS:
                self._emit("!", now)
            self._agent_event(now, event)

    @staticmethod
    def _event_key(event):
        return (event.get("kind"), event.get("ts"), event.get("id"))

    def has_seen(self, event):
        return self._event_key(event) in self._seen_events

    def set_state(self, name, now, *, seconds=None, source="panel"):
        """本地对话优先于群聊心跳；同名状态只续期，不重排动作。"""
        if source != "local" and self.state_source == "local" and now < self.state_until:
            return False
        clip = name if source == "local" and name in CLIPS else STATE_CLIPS.get(name)
        if clip is None:
            return False
        duration = math.inf if source == "local" else STATE_HEARTBEAT
        self.state_until = now + (duration if seconds is None else max(0.0, seconds))
        if self.state_name == name and self.state_source == source:
            return True
        self.state_name, self.state_clip, self.state_source = name, clip, source
        self.last_agent_event = now
        if self.mode == "ground" and not self.chat_pending:
            self._queue(*self._state_steps(name, clip, now), now=now, interrupt=True)
            if source != "local":
                self._status_line(name, now, gap=0)
        elif self.mode in ("fly", "bottom"):
            # Finish the airborne path before acting out the new state.
            self._queue(Step("idle", 1), now=now, interrupt=True)
        return True

    def clear_state(self, now, *, source=None):
        """状态结束：立刻回到日常，不要等一个假装还在忙的长超时。"""
        if source is not None and self.state_source != source:
            return
        had_state = self.state_name is not None
        was_active = self.step is not None and self.step.hold == "state"
        self.state_clip, self.state_name, self.state_until = None, None, 0.0
        self.state_source = None
        if was_active and self.mode == "ground":
            self._queue(Step("idle", 1), now=now, interrupt=True)
        elif had_state and self.mode in ("fly", "bottom"):
            self._queue(Step("idle", 1), now=now, interrupt=True)

    def _agent_event(self, now, event):
        kind = event.get("kind")
        if kind == "state":
            # 一次性的状态动作（交给 Agent、体检、没配好）：演一下就自己收。
            name = event.get("state")
            if isinstance(name, str) and name in STATE_CLIPS and self.mode == "ground" and not self.chat_pending:
                self.set_state(name, now, seconds=STATE_ONCE.get(name), source="event")
            return
        if kind in ("inspecting", "listening"):
            self.last_agent_event = now
            # 很短的任务可能在两次轮询间完成，演一下收到的真实事件；长任务仍由心跳续期。
            name = "inspect" if kind == "inspecting" else "listen"
            if self.mode == "ground" and not self.chat_pending and self.state_source != "local":
                if (not self.state_clip or now >= self.state_until) and not (self.media and now < self.agent_busy_until):
                    self.set_state(name, now, seconds=3.0, source="event")
                self._status_line(name, now, starts=True)
            return
        # What 小团团 is busy with in QQ is tracked even mid-drag or mid-chat, so she
        # picks the sketchbook back up afterwards (see _choose).
        finished = self.media
        if kind in MEDIA_HOLD:
            self.media = BUSY_CLIP[kind]
            self.agent_busy_until = now + MEDIA_HOLD[kind]
            restage = {"searching": "inspect", "singing": "listen"}.get(kind)
            if (restage and self.state_name == restage and self.state_clip not in ("tome", "sing")
                    and now < self.state_until and self.mode == "ground" and not self.chat_pending):
                self._queue(*self._state_steps(self.state_name, STATE_CLIPS[restage], now), now=now, interrupt=True)
        elif kind in ("drew", "voice", "media_end") or (kind in ("said", "chimed", "proactive")
                                                         and self.media == "laptop"):
            self.media, self.agent_busy_until = None, 0.0    # the answer after a search ends it
        busy = self.media is not None and kind not in MEDIA_HOLD
        if kind != "cue":
            self.last_agent_event = now
        if self.mode != "ground" or self.chat_pending:
            return
        # 状态层在演时，QQ 日志事件只记账（上面已经更新 media），不抢镜。
        # cue 是用户/Agent 的显式指令，仍然优先。
        if kind != "cue" and self.state_clip and now < self.state_until:
            return
        # Asleep is no excuse: whatever 小团团 does in QQ wakes her (the queue walks lie -> sit -> stand).
        if kind == "cue":
            action = event.get("action")
            text = event.get("text") if isinstance(event.get("text"), str) else ""
            if action in CUE_ACTIONS:
                if action == "sleep":
                    self._sleep_cue_until = now + 900
                    self._queue(Step(self._pick("sleep", "sleep"), hold="sleep"), now=now, interrupt=True)
                else:
                    hold = 6.0 if CLIPS[action][3] else None
                    self._queue(Step(action, hold), Step("idle", 2), now=now, interrupt=True)
            if text.strip():
                self.say(text[:120], now=now)
            return
        if kind in CALLS:
            self._emit("!", now)
            if not busy:      # mid-drawing she only glances up
                greet = Step(self._pick("greet", "phone"), 4)
                target = self.pointer.x if self.pointer.seen and self.left <= self.pointer.x <= self.right else None
                if (kind == "mention" and target is not None and self._can_warp() and not self.quiet
                        and self.clip not in ASLEEP and now - self._warp_at >= 90 and self.rng.random() < 0.7
                        and abs(target - self.x) >= self.figure * 3 and self._warp_target(target) is not None):
                    # 被 @ 了、你又离得远：开个阵传送到你手边再看消息。
                    self._warp_request = {"x": target, "via": "floor", "cause": "called"}
                    self._queue(Step("warp_cast", line=self.rng.choice(LINES["warp_called"])), greet,
                                Step("idle", 1), now=now, interrupt=True)
                    return
                stop = "motion_walk_message_stop" if self.clip.startswith("motion_walk_") and "motion_walk_message_stop" in self.repertoire.actions else None
                self._queue(*([Step(stop)] if stop else []), greet, Step("idle", 1), now=now, interrupt=True)
                self._status_line(kind, now, gap=40)
        elif kind == "replying" and not busy:
            self.agent_busy_until = now + 40
            clip = "laptop" if self.posture() == "sit" else "phone"
            self._queue(Step(clip, hold="agent"), Step("idle", 1), now=now, interrupt=True)
        elif kind in MEDIA_HOLD:
            # Wakes her if she dozed off: someone asked her to look something up, draw or sing.
            self._state_switch_at = now + self.rng.uniform(10, 16)
            busy_clip = self._busy_clip()
            opening = [Step("conjure")] if busy_clip in ("paint", "tome") else []
            self._queue(*opening, Step(busy_clip, hold="agent"), Step("idle", 1), now=now, interrupt=True)
            self._status_line(kind, now, gap=20, starts=True)
        elif kind == "media_end":
            if finished and self.step is not None and self.step.hold == "agent":
                self._queue(Step("idle", 1), now=now, interrupt=True)   # put it down; her QQ line follows
        elif kind in ("said", "chimed", "proactive") and busy:
            pass              # the "好，我画画看" before the picture: keep drawing
        elif kind in ("said", "chimed", "proactive", "drew", "voice"):
            self.agent_busy_until = 0.0
            if kind == "drew" and finished == "drawing":
                if self._has("spell_reveal"):
                    # 画布转过来给你看，举高，抱在怀里眨眼。
                    self._queue(Step("paint_reveal"), Step("idle", 1.5), now=now, interrupt=True)
                    self._burst(now, "sparkle", 0.1, -0.9, 5, spread=0.6)
                else:
                    self._queue(Step("show_drawing", 2.6), Step("idle", 1.5), now=now, interrupt=True)
                    self._emit("sparkle", now, 3)
            elif finished == "laptop" and kind in ("said", "chimed", "proactive") and self._has("spell_tome"):
                self._queue(Step("tome_found", 1.4), Step("idle", 1.5), now=now, interrupt=True)
                self._burst(now, "sparkle", 0.3, -0.8, 3, spread=0.3)
                if not self.chat_pending:
                    self._last_status_line = now
                    self.say(self.rng.choice(LINES["found"]), now=now, seconds=2.4, kind="status")
                return
            else:
                clip = self._pick({"drew": "drawing", "voice": "voice"}.get(kind, "celebrate"),
                                  {"drew": "draw", "voice": "sing"}.get(kind, "talk"))
                seconds = 1.6 if finished == "sing" else 3.0
                self._queue(Step(clip, seconds if clip in ("talk", "sing") else None), Step("idle", 1.5),
                            now=now, interrupt=True)
            self._status_line(kind, now, gap=20 if kind in ("drew", "voice") else 75)

    def snapshot(self):
        return {"clip": self.clip, "mode": self.mode, "x": round(self.x, 1), "y": round(self.y, 1),
                "facing": self.facing, "state": self.state_name,
                "plan": [s.clip for s in self.plan],
                "bubble": self.bubble.text if self.bubble else None}
