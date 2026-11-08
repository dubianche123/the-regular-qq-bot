# 老群友的视觉形象

这位银白发、淡紫眼睛、穿鼠尾草绿外套的小龙群友，是项目的原创 AI 生成吉祥物。形象受维护者提供的龙娘参考启发，使用独立服装与表情设计；它代表本项目，不代表 OpenAI、ChatGPT 或 QQ 官方形象。角色用于欢迎与介绍，不改变机器人原有名字或聊天人设。

- README 横幅：`docs/readme/hero.png`
- 透明角色图：`regular/assets/mascot.png`
- 生成方式：内置 imagegen，参考图生成，角色图保留透明 alpha；2026-11-06。
- 页面配色：暖纸白、墨绿、鼠尾草绿，少量淡紫。

## 页面和桌宠怎样共用她

角色设计保持一致，页面插画按用途更换姿势：

| 用途 | 最终文件 |
| --- | --- |
| 角色原画、桌宠基准 | `regular/assets/mascot.png` |
| 配置与聊天欢迎：捧茶坐下 | `regular/assets/mascot-welcome.png` |
| 知识库：整理笔记 | `regular/assets/mascot-knowledge.png` |
| 体检与改进：放大镜和记录板 | `regular/assets/mascot-checkup.png` |
| 网页点击反馈 | `mascot-blink.png`、`mascot-wave.png` |
| 桌宠与网页小助手（Q 版动作） | `regular/assets/pet/*.webp` + `sprites.json` |

网页插画统一为 512×768 的透明 PNG；另外保留原始高清角色与 README 横幅。情境插画允许换姿势和道具。

## 桌宠的 Q 版动作

桌宠用的是同一角色的 Q 版（约 3 头身、成年角色）。2026-11-06 以 `mascot.png` 为身份参考，用 GPT Image 2.6 Flare 生成定稿（Sunburst 版本对比后差别不大，未采用），再以定稿和走路表为参照生成整张动作表：待机/张望、走路 6 帧、挥手、跳、坐下、睡着、被拎起、掉落与落地、托腮/看手机、说话、伸懒腰、看书、画画、唱歌、生气、害羞、被戳、庆祝、喝茶、东张西望、吃饼干、敲电脑。同一张表里的几帧由模型一次画出，脸、服装和比例一致，远好于逐帧重画；眨眼只编辑待机帧的眼睛椭圆并羽化合回，身体像素不变。

`tools/pet_sprites.py` 记录全部提示词：`generate` 默认只打印计划，加 `--allow-paid` 才向官方 Images API 上传原创角色图与动作描述，每张一次请求、不自动重试，原始表只放仓库外目录；`build` 离线切帧、按姿态对齐脚底或支撑，校准时以待机头部为比例参照并导出 WebP，不把坐姿和站姿拉成相同高度。最初这轮共约 26 次生成，被拎起的第一版没画成悬空、已淘汰重做。生成图自带一圈浅色贴纸描边，在深色桌面上反而更醒目，保留。

网页插画的维护工具为 `tools/generate_mascot_poses.py` 和 `tools/mascot_expression.swift`。默认仅打印计划，明确加 `--allow-paid` 才向官方 Images API 上传原创参考图与动作描述，每项一次请求、不自动重试。试验以 GPT Image 2.6 Flare 为主，局部姿势补强使用 Sunburst；不上传群聊。候选先留临时目录，验看后只导出采用素材，失败帧不放入运行包。

本轮动作与情境扩展用了 8 次生成，保留自然且一致的结果；失败的腿部接缝、切翼和失真手臂候选已淘汰。完整角色与情境提示保存在生成工具中，下面是最初原画与横幅的提示。

保留生成提示，便于后续延续同一形象。横幅以角色图作为身份参考。

## 日常动作扩展（2026-11-07）

新增 `popcorn`、`headphones`、`gamepad`、`tail_care`、`lie_chin`、`wing_stretch`、`edge_peek` 七组，每组四帧，分别表现吃瓜、听歌、掌机、理尾巴、趴地托腮、展翼和扒边框。以原 Q 版定稿与既有动作表为参照，服装、脸和比例沿用现有角色；主体用 GPT Image 2.6 Flare 生成，扒边框定稿使用 Sunburst。

这批累计 10 次生成，采用 7 组，新增压缩动作素材约 0.58 MB。

提示词收在 `tools/pet_sprites.py` 的 `SHEETS` 中，共用 `GENTLE_LOOP` 约束：头和躯干位置稳定，手指、尾巴、眼睛和脚尖做可见的小动作。候选在仓库外处理，运行包保存切好并对齐的 WebP 动作条。日常动作链和慢速轮播由 `regular/pet_brain.py` 本地运行。

扒边框原图保留全身，固定为「身体在左、头向右探」的姿势；原生桌宠在左屏边使用原向，右屏边镜像，并按当前屏幕裁去藏身部分。画面不自带边框，位置随真实屏幕边缘变化。

## 丰富的桌宠动作库（2026-11-07）

桌宠第一次扩展加入 236 组四帧动作：移动与状态 64 组、龙娘主题 72 组、日常生活 96 组、边缘过渡 4 组。后续增加 8 组法术表演、9 组边缘跃出／眼神／打盹、34 组角色场景和 7 组小龙形态。角色仍是成年 Q 版小龙，保持银白淡紫头发、鼠尾草绿开衫和原有服装。下述情绪与互动补画之前，运行素材清单为 337 条 WebP、1,342 帧、34,589,330 字节，包括 43 条原有素材和 294 条动作库定义；边缘动作共 13 组。

本轮生成账本记录 253 次服务端成功并保存的图片，其中 Flare 236 次、Sunburst 17 次；正式采用 232 组动作。失败和跳过不计入成功次数。原始候选留在仓库外，运行包只放验收后的 WebP 和清单；动作提示与元数据保存在 `regular/pet_actions_*.py` 文件中。生成、验收及本机播放方式见[桌宠动作库](pet-repertoire.md)。

边缘过渡另生成 6 次，采用 4 组：藏起、跳出、倒挂探头和转正；多手候选已淘汰。后续把透明画布扩至 512×512，并按头部大小校准整条动作。最早按肤色和发宽估头宽，手、刘海和歪头都会带偏，实际各组相差 0.7–1.3 倍：悬停、挥手时大约变大两成，落地、坐下、睡觉时又变小。现在 `tools/pet_sprites.py calibrate` 直接拿待机帧的头部去比对（多尺度归一化互相关，含镜像，躺姿再试旋转），每组取能看清脸的帧的中位数；脸被枕头、翅膀或屏幕边挡住时，沿用同一批生成的同组动作的倍率。举手、翅膀和道具不作为人物缩放依据；同一动作需要时逐帧按头部校正，人龙混合动作则按每帧对应形态的参考校正。家具入场、长驻与退场复用同源帧，保持道具一致。原始动作与采用版本分开保存，便于对照。边缘探头原图里供她抓握的品红竖杆已在采用版本中抹掉，不再只靠屏幕边挡住。

飞行打磨又用 Flare 重画并替换 5 组：慢速扑翼和 4 组空中回复。四帧明确分为上振、展平、下振、展平，逐帧核对头身比例，避免只有人物上下移动、翅膀却不动。运行时也去掉整张人物图的呼吸缩放、按压缩小和落地压扁；播放时不额外动态缩放；贴近屏幕打招呼的透视前探则是有意的变化。

小龙形态（2026-11-08 重画）是同一张脸和头发长在圆滚滚的 Q 版小龙身上：鬃毛带呆毛和刘海，角、尖耳、紫眼、绿发夹、小白花和围巾都保留，奶白身体、四条短腿、小蝙蝠翼和原来的鳞尾，高约人形六成、头与人形一样大。设计稿 Flare、Sunburst 各试画，采用 Sunburst 一张（身体更像小龙）；蹦跶、试飞、拆糖、护宝、追尾巴五组以它为参照用 Flare 画成，共 13 次生成。Sunburst 的动作候选有不透明底或地面线，未采用。变身不画中间帧，由运行时的一团云遮住切换。早先的写实四足龙、人龙过渡帧和搬椅子等 6 组已移除。

2026-11-08 为四边隐藏补画 9 组：侧边扑翼跃出、下沿蹦出、收翼落地、侧边蜷睡、枕手趴睡、枕头趴睡、顶部裹翼挂睡、倒挂看鼠标和睡滑抓稳。共 15 次成功生图；多手起跳和站姿闭眼的睡觉候选均淘汰。试画与眼神用 Flare，跃出和支撑睡姿用 Sunburst 补强；顶部另以蜷卧姿势为参照重画，保留弯腿抱枕的蝙蝠轮廓。提示词、入睡帧序、循环帧序与支撑坐标在 `regular/pet_actions_boundary.py`；候选原图不进运行目录。

## 情绪、日常互动与边缘微动作（2026-11-08）

这一批按[角色设定](character.md)中未画出的选择补素材：15 张日常表和 8 张边缘表，每张 2×2 四帧。日常包括完整投喂、抱书安心睡、最后饼干、护灯、第二杯茶、旧收藏、留空位、宝石放手、确认观众、画作礼物、放下掌机，以及欣喜、低落、发现和安静陪伴。边缘包括侧边招呼与倾听、底边观看与哈欠、顶部发现、开阵、转身和出口踉跄。数量只记录运行包内容，不作为演出质量的判断。

生成通过项目已有的 `tools/pet_sprites.py generate --stage library --group daily --parallel 6 --allow-paid` 等显式命令，主要使用 OpenAI Images API 的 `gpt-image-2.6-flare`；角色定稿与已采用动作表作为参考，不发送群聊。每次请求四格整表、固定 1024×1024 输出，每格 512×512，要求完整四肢、留白、透明底和一致的头身比例。动机、变化、落点写在逐帧提示中，镜头固定，身体可以重心转移、坐下或蹲下。

`daily_last_cookie` 用内置图像编辑修复“放下完整饼干后空手退开”的因果；`edge_daily_side_tail_greet` 用内置精准编辑保持两只手和抓握位置，尾尖做招呼。顶部发现保留两手在胸前，只让眼神追随绒羽；顶部开阵同样限制为胸前双掌，随后用独立转身图进入法阵。开阵表有多手的源格不入运行条，采用 `[0, 1, 3, 3]`，让最后姿势多停一拍。原始表、失败候选与请求账本留在 `/private/tmp/regular-pet-followup-20261108/` 的工作目录；运行目录只纳入采用的 WebP 和清单。23 组全部采用后，最终清单为 360 条、1,434 帧、36,527,434 字节；逐项核对见[本轮演出记录](pet-performance-2026-11-08.md)。

尺寸核对同时看头部和身体比例。旧饼干动作的头大小接近待机，但躯干与腿更短，单按头部放大缩小不能修复；投喂四拍改用同一新表，不再切到旧吃饼干或分享图。`daily_quiet_space` 坐下后的外轮廓变化较大，虹膜间距复核约差 1%，保留自然坐姿，不按全身包围盒把人物放大。底边支撑不叠加窗口下滑，醒来反播同一睡图的入睡帧。实际窗口、接触点和比例的验收状态另记，不把提示词要求当作验证结果。

本轮已完成六段 Cocoa 原生、真实时钟的 10 秒日常录制，包含延迟投喂回执；只抓取验收进程的 Qt 窗口，按真实原生位置合成，不访问服务或其他窗口。四边清醒微动作各录 16 秒，眼神、支撑和出口观看通过，动态锚点误差小于 0.5 像素；四边睡醒单独录制，支撑与逆序醒来观看通过，顶部转身四帧均已捕获。气泡现复用 `SpriteBank.region` 的真实 alpha 轮廓，按角色绘制时的相同变换避让，修复躺姿高度估算和发梢外伸造成的遮挡；四边、三尺寸、三帧醒来的 36 个子测试直接检查实际像素不相交，完整 Qt 测试通过。气泡修复后的四边原生重录均已通过，头发、角和眼睛无遮挡，逆醒与离开正常，最后全部回到地面。素材图与记录路径、具体可见行为见[本轮演出记录](pet-performance-2026-11-08.md)。

## 角色图提示

```text
Use case: stylized-concept.
Asset type: original cute mascot cutout for the README and local setup UI of "The Regular / 老群友", a cozy QQ group companion.
Primary request: Design ONE charming original feminine dragon companion, clearly an adult character in a soft stylized chibi illustration (about 3.5-head proportions), full body, with a truly transparent background. She should look like a witty longtime friend who is about to add a playful comment in a group chat.
Input image: the attached white-haired dragon woman is a visual reference ONLY for the silver hair, little pale dragon horns, pointed ears, lavender eyes and dragon tail motif. Create a distinct new costume and face design. Do not reproduce the reference's logos.
Subject: fluffy silver-white shoulder-length hair with a tiny loose ahoge, two small rounded pearly dragon horns, warm lavender eyes, an amused gentle smile. Cozy oversized sage-green cardigan over a simple ivory knee-length casual dress, lavender scarf ribbon, clean white sneakers. A short pale lavender dragon tail curls beside her; very small folded dragon wings optional if visually uncluttered. One hand waves casually; the other holds a small blank speech bubble-shaped cushion. Her posture is relaxed, inviting, cheeky and companionable.
Style/medium: polished Japanese anime character illustration, delicate confident ink lines, soft pastel cel shading, subtle hand-painted texture, beautiful face and expressive eyes, premium indie open-source mascot aesthetic. Charming rather than glossy mobile-game poster.
Color palette: pearl white, pale lavender, muted sage green, warm cream; small dark forest-green accents. Broad readable silhouette at 200px height.
Composition: one centered full character, small breathing room on all sides, no feet or horns clipped, no extra character portraits. No scene, no typography or UI.
Constraints: actual transparent alpha background, modest everyday clothes, adult character, original design, no OpenAI/ChatGPT/QQ official emblems or any logos, no knot emblems, no watermark, no text, no panels, no checkerboard baked into the pixels.
```

## 横幅提示

```text
Create a beautiful landscape GitHub README hero banner, aspect ratio 2.5:1. Use the supplied original mascot as the exact identity reference: friendly silver-white bob-haired anime dragon woman, small pearly horns, lavender eyes, sage cardigan, ivory dress, purple scarf, pale tail. On the right 40% she waves while sitting casually on a warm ivory speech-bubble seat. On the left spacious elegant dark forest-green typography: exactly 'The Regular' then '老群友' then the smaller line '群里，多一个有趣的朋友。'. No other words. Background warm ivory paper, muted sage and lavender decorative shapes, tiny subtle chat bubbles and a microphone and picture icon. Polished delicate anime illustration, cozy expressive face, clean editorial layout, generous safe margins, exceptionally correct legible Chinese typography. A refined inviting indie project cover, playful but uncluttered. Preserve the reference character and outfit. No official OpenAI or QQ logos, no watermark, no extra people, no architecture diagrams or metrics. All text and character within frame.
```
