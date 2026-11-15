"""她和屏幕外的世界：天气、屏幕边、路过的光标，以及一次像样的起飞。

2026-11-15 群主要的几件事：
  · 天气联动：控制台选城市，下雨撑翅膀当伞、下雪接雪花、太热就瘫着再冰一杯可乐、太冷就呵火暖手、
    晴天像小龙一样晒太阳；
  · 屏幕边偶尔有点别出心裁的小动作，但次数要低：靠着屏幕边站一会儿、敲敲屏幕边听听、倒挂在屏幕顶上；
  · 光标路过时不只会扑：拿尾巴扫一下、踮脚去够、凑过去戳一戳、光标绕着她转会转晕；
  · 起飞要有预备：屈膝、展翼、蹬地，离地后第一下拍翼。
这些都不进日常随机池（automatic=False），由 pet_brain 按天气、位置和光标挑。
特效（雨、雪、热气、火苗）由引擎按 effects 发粒子，不画进图里。
"""

_ANATOMY = (
    "Image 1 is the original adult chibi dragon-woman; Image 2 is her exact shipped body. "
    "COPY its head diameter, neck-to-dress-hem length, hip position, thigh and shin lengths, "
    "hands and shoes in every cell: never a larger head on a shorter baby body. "
    "Silver-lavender hair, lilac horns, green flower clip, sage cardigan, cream dress, lavender "
    "scarf, white socks and sneakers, two small bat wings and one scaled tail. Exactly TWO arms, "
    "TWO connected hands and TWO legs. Fixed camera and character scale. Clear, simple "
    "silhouettes that read at a small desktop size. Consecutive frames are in-betweens of one "
    "continuous motion: small clear changes, never unrelated poses. No background, floor, cast "
    "shadow, text, numbers, borders, speed lines, rain, snow, flames, steam or sparkles: those "
    "effects are added later by the program. Real alpha transparency; every figure fully inside "
    "its own cell with transparent margins."
)


def _header(frames):
    grid = ("Transparent 1024x1024 sprite sheet, exactly FOUR complete character frames in a 2x2 grid"
            if frames == 4 else
            "Transparent 1536x1024 sprite sheet, exactly SIX complete character frames in a 3x2 grid")
    return grid + ", each cell 512x512, reading left to right, top row first. " + _ANATOMY


def _spec(title, prompt, *, frames=4, style="idle", posture="stand", starts_from=None, role, family,
          loop, fps, sequence=None, duration=None, effects=(), line=None, **extra):
    sequence = list(sequence or range(frames))
    spec = {
        "title": title, "prompt_header": _header(frames), "prompt": prompt, "style": style,
        "posture": posture, "starts_from": starts_from or posture, "role": role, "family": family,
        "automatic": False, "loop": loop, "fps": fps, "sequence": sequence,
        "duration": duration or round(len(sequence) / fps, 3), "frame_blend": 0.14, "drop_marks": 0.02,
        "effects": list(effects), "scale": {"sit": 0.8, "lie": 0.7}.get(posture, 1.0),
        "fixed_ground": posture != "air", "calibration_reference": "idle", "calibration_frames": [0],
        **({"line": line} if line else {}), **extra,
    }
    if frames == 6:
        spec.update(size="1536x1024", cells=[0, 1, 2, 3, 4, 5])
    return spec


ACTIONS = {
    # ── 起飞：屈膝、展翼、蹬地，离地，收腿，第一下拍翼 ──
    "motion_fly_launch": _spec(
        "蹬地起飞",
        "A real dragon-girl takeoff, front three-quarter view. Frame 1: standing as Image 2, knees "
        "just starting to bend, both small bat wings beginning to open. Frame 2: deep crouch, knees "
        "bent, arms swung back, wings raised high and fully spread above her shoulders, eyes looking "
        "up. Frame 3: pushing off: legs straightening, toes still on the ground line, wings sweeping "
        "powerfully DOWN, arms swinging forward. Frame 4: airborne a little above the ground line, "
        "body stretched upward, legs extended below, wings fully down. Frame 5: higher, knees tucked "
        "up softly, wings sweeping up again, hair lifting. Frame 6: hovering with the first relaxed "
        "wing beat, wings half open, legs dangling gently, happy determined face.",
        frames=6, posture="air", starts_from="stand", role="fly", family="launch", loop=False, fps=7,
        calibration_frames=[0]),

    # ── 天气 ──
    "weather_wing_umbrella": _spec(
        "翅膀当伞",
        "Standing in the rain under her OWN wing. She has EXACTLY TWO bat wings, both growing from "
        "her back between the shoulder blades, never held in a hand. The wing on the viewer's RIGHT "
        "stretches UP from her back and ARCHES FORWARD over the top of her head like an umbrella "
        "canopy, its membrane spread above her hair; the wing on the viewer's LEFT stays folded "
        "behind her. Both hands are empty: one hand is held out to the side palm up, feeling for "
        "raindrops, the other rests at her chest. Gentle four-frame loop: 1 looking up at the wing "
        "canopy, calm; 2 the outstretched palm tilts, a small curious smile; 3 she blinks and glances "
        "at the viewer; 4 settles back toward frame 1. The wing canopy and body stay in the same place.",
        role="weather", family="rain", loop=True, fps=1.4, line="下雨了，翅膀借我挡一挡。",
        # The raised wing defeats head matching (confidence ~0.43, read as 0.99); measured against
        # idle by shoe span and side-by-side, the figure needs 0.97 of the wing-inclusive fit.
        scale=0.97, head_calibration=False,
        effects=[{"frames": [0, 1, 2, 3], "kind": "rain", "zone": [-0.95, -0.42, 0.5, 0.95],
                  "y": -1.35, "every": 0.022},
                 {"frames": [0, 1, 2, 3], "kind": "drip", "at": [0.48, -0.74], "toward": [0.52, -0.05],
                  "every": 0.45}]),       # rain running off the wing tip
    "weather_snow_catch": _spec(
        "接雪花",
        "Snow is falling: she tilts her face up, eyes sparkling, and catches snowflakes on her "
        "tongue and in her cupped hands. Gentle four-frame loop: 1 face up, tongue just out, hands "
        "cupped at chest; 2 eyes closed happily, a little giggle; 3 she looks into her cupped palms "
        "with wonder; 4 face up again toward frame 1. Wings folded, tail swaying slightly.",
        role="weather", family="snow", loop=True, fps=1.3, line="下雪啦！",
        effects=[{"frames": [0, 1, 2, 3], "kind": "snow", "zone": [-0.95, 0.95],
                  "y": -1.4, "every": 0.12}]),
    "weather_warm_hands": _spec(
        "呵火暖手",
        "It is very cold. Frame 1: she shivers, hugging her own arms, shoulders raised, wings wrapped "
        "close. Frame 2: she rubs her two palms together in front of her chest. Frame 3: she cups "
        "both hands close to her mouth and breathes in, cheeks puffed. Frame 4: lips pursed blowing "
        "softly into her cupped hands (no flame drawn), eyes closed. Frame 5: she opens her warm "
        "hands, relieved, a small happy smile. Frame 6: she presses the warm palms to her own cheeks, "
        "content, shoulders relaxed.",
        frames=6, role="weather", family="cold", loop=False, fps=4,
        sequence=[0, 0, 0, 1, 1, 1, 2, 2, 3, 3, 3, 3, 4, 4, 4, 5, 5, 5, 5], line="好冷……呵一口火。",
        effects=[{"frames": [3], "kind": "flame", "at": [0.08, -0.72], "toward": [0.16, -0.6], "every": 0.05},
                 {"frames": [4, 5], "kind": "heat", "at": [0.12, -0.6], "toward": [0.12, -0.95], "every": 0.18}]),
    "weather_heat_melt": _spec(
        "热化了",
        "A very hot day: she sits slumped on the floor exactly in the seated layout of Image 2's "
        "seated sheet, legs out, shoulders drooping, wings hanging limp, tongue slightly out, "
        "fanning her face weakly with one hand. Gentle four-frame loop: 1 slumped, fanning; 2 hand "
        "drops, a long tired sigh; 3 fanning again, eyes half closed; 4 back toward frame 1. "
        "The seated body keeps its normal torso length, just relaxed.",
        style="tea", posture="sit", role="weather", family="hot", loop=True, fps=1.2, line="要化了……",
        effects=[{"frames": [0, 1, 2, 3], "kind": "sweat", "at": [0.12, -0.85], "every": 1.4},
                 {"frames": [0, 1, 2, 3], "kind": "heat", "zone": [-0.6, 0.6], "y": -0.1, "every": 0.45}]),
    "weather_sunbask": _spec(
        "晒太阳",
        "A sunny day: like a little dragon basking on a warm rock, she sits on the floor in the "
        "seated layout of Image 2's seated sheet, leaning back on both hands, face tilted up to the "
        "sun with eyes closed, both bat wings spread wide open to soak up warmth, tail stretched out. "
        "Gentle four-frame loop: 1 basking, blissful; 2 wings spread a little wider, a contented "
        "sigh; 3 one eye peeks open at the viewer, a lazy smile; 4 back toward frame 1.",
        style="tea", posture="sit", role="weather", family="sun", loop=True, fps=1.0, line="晒晒太阳……",
        effects=[{"frames": [0, 1, 2, 3], "kind": "sun", "zone": [-0.7, 0.7], "y": -1.2, "every": 0.7}]),

    # ── 屏幕边的小时刻（很少出现） ──
    "edge_wall_lean": _spec(
        "靠着屏幕边",
        "She leans her back against an IMAGINARY vertical wall on the LEFT side of the cell (the wall "
        "is not drawn): shoulder blades and folded wings touch it, arms crossed loosely, one foot "
        "crossed over the other, facing toward the viewer's right, relaxed and a bit cool. Gentle "
        "four-frame loop: 1 relaxed lean, glancing right; 2 a slow blink; 3 she glances at the viewer "
        "with a small smile, tail tip flicks; 4 back toward frame 1. Body stays in place.",
        role="edge_moment", family="lean", loop=True, fps=1.0),
    "edge_knock": _spec(
        "敲敲屏幕边",
        "She faces an IMAGINARY vertical wall on the RIGHT side of the cell (the wall is not drawn), "
        "standing close to it. Frame 1: she raises one hand and knocks on the invisible wall with her "
        "knuckles, curious; Frame 2: knocks again, leaning closer; Frame 3: she presses one ear to "
        "the invisible wall, listening, eyes wide; Frame 4: she steps back half a step and shrugs "
        "with a puzzled little smile.",
        role="edge_moment", family="knock", loop=False, fps=2,
        sequence=[0, 1, 0, 1, 2, 2, 2, 2, 3, 3, 3], line="咚咚……外面有人吗？"),
    "edge_bat_hang": _spec(
        "倒挂在屏幕顶",
        "Like a little bat, she hangs UPSIDE DOWN from an imaginary horizontal bar at the TOP of the "
        "cell (the bar is not drawn): both knees hooked over it near the top of the cell, her head "
        "and long hair hanging down toward the bottom, both bat wings wrapped around her body like a "
        "cloak, only her face and hands peeking out, cheeky grin. Gentle four-frame loop: 1 hanging, "
        "grinning; 2 swinging very slightly, a blink; 3 she peeks one hand out of the wings and waves; "
        "4 back toward frame 1. Her shoes stay at the top of the cell in every frame.",
        posture="air", role="edge_moment", family="hang", loop=True, fps=1.2, line="倒过来看也挺好看的～"),

    # ── 光标路过 ──
    "cursor_tail_swat": _spec(
        "尾巴扫一下",
        "Something passes behind her. Frame 1: she glances back over her shoulder, eyes narrowed "
        "playfully; Frame 2: her scaled tail swings out wide to the side, sweeping like a cat's; "
        "Frame 3: the tail tip snaps and curls at the end of the swat; Frame 4: she looks forward "
        "again with a smug little smile, tail settling.",
        role="cursor", family="swat", loop=False, fps=5, sequence=[0, 0, 1, 2, 2, 3, 3, 3, 3]),
    "cursor_reach_up": _spec(
        "踮脚够光标",
        "Something floats just above her head. Frame 1: she looks straight up, curious, rising onto "
        "tiptoes with both hands reaching up; Frame 2: a small hop, both feet just off the ground, "
        "hands closing together above her head as if catching something; Frame 3: landed, she holds "
        "her cupped hands in front of her chest; Frame 4: she peeks into her cupped hands, they are "
        "empty, a puzzled pout.",
        role="cursor", family="reach", loop=False, fps=4, sequence=[0, 0, 1, 1, 2, 3, 3, 3, 3]),
    "cursor_poke": _spec(
        "凑过去戳一戳",
        "Something small hovers right in front of her face. Frame 1: she leans forward, curious, "
        "sniffing at it; Frame 2: she reaches out ONE fingertip and pokes the air in front of her; "
        "Frame 3: she pulls the finger back with a surprised little 'oh'; Frame 4: she giggles behind "
        "her hand.",
        role="cursor", family="poke", loop=False, fps=3, sequence=[0, 0, 1, 1, 2, 3, 3, 3]),
    "cursor_dizzy": _spec(
        "转晕了",
        "She has been following something going round and round her: dizzy. Gentle four-frame loop: "
        "1 swirly spiral eyes, swaying to the left, arms out for balance; 2 swaying to the right, "
        "wobbly smile; 3 swaying left again, wings slightly open for balance; 4 back toward frame 1. "
        "Her feet stay planted; only the upper body sways a little.",
        role="cursor", family="dizzy", loop=True, fps=2.2, line="转、转晕了……"),
}

# The takeoff splits around the moment her soles leave the ground.
PLAYBACK_ALIASES = {
    "motion_fly_launch_crouch": {
        "strip": "motion_fly_launch", "title": "起飞前屈膝展翼", "starts_from": "stand", "posture": "stand",
        "role": "fly", "family": "launch_crouch", "fps": 6, "loop": False, "sequence": [0, 1, 1, 1, 2],
        "duration": 5 / 6, "automatic": False, "frame_blend": 0.08,
    },
    "motion_fly_launch_lift": {
        "strip": "motion_fly_launch", "title": "蹬地离地", "starts_from": "stand", "posture": "air",
        "role": "fly", "family": "launch_lift", "fps": 7, "loop": False, "sequence": [3, 4, 5],
        "duration": 3 / 7, "automatic": False, "frame_blend": 0.06,
    },
}
