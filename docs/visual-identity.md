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

`tools/pet_sprites.py` 记录全部提示词：`generate` 默认只打印计划，加 `--allow-paid` 才向官方 Images API 上传原创角色图与动作描述，每张一次请求、不自动重试，原始表只放仓库外目录；`build` 离线切帧、以头部重心和脚底对齐、按待机身高统一比例并导出 WebP。本轮共约 26 次生成，被拎起的第一版没画成悬空、已淘汰重做。生成图自带一圈浅色贴纸描边，在深色桌面上反而更醒目，保留。

网页插画的维护工具为 `tools/generate_mascot_poses.py` 和 `tools/mascot_expression.swift`。默认仅打印计划，明确加 `--allow-paid` 才向官方 Images API 上传原创参考图与动作描述，每项一次请求、不自动重试。试验以 GPT Image 2.6 Flare 为主，局部姿势补强使用 Sunburst；不上传群聊。候选先留临时目录，验看后只导出采用素材，失败帧不放入运行包。

本轮动作与情境扩展用了 8 次生成，保留自然且一致的结果；失败的腿部接缝、切翼和失真手臂候选已淘汰。完整角色与情境提示保存在生成工具中，下面是最初原画与横幅的提示。

保留生成提示，便于后续延续同一形象。横幅以角色图作为身份参考。

## 日常动作扩展（2026-11-07）

新增 `popcorn`、`headphones`、`gamepad`、`tail_care`、`lie_chin`、`wing_stretch`、`edge_peek` 七组，每组四帧，分别表现吃瓜、听歌、掌机、理尾巴、趴地托腮、展翼和扒边框。以原 Q 版定稿与既有动作表为参照，服装、脸和比例沿用现有角色；主体用 GPT Image 2.6 Flare 生成，扒边框定稿使用 Sunburst。

这批累计 10 次生成，采用 7 组，新增压缩动作素材约 0.58 MB。

提示词收在 `tools/pet_sprites.py` 的 `SHEETS` 中，共用 `GENTLE_LOOP` 约束：头和躯干位置稳定，手指、尾巴、眼睛和脚尖做可见的小动作。候选在仓库外处理，运行包保存切好并对齐的 WebP 动作条。日常动作链和慢速轮播由 `regular/pet_brain.py` 本地运行。

扒边框原图保留全身，固定为「身体在左、头向右探」的姿势；原生桌宠在左屏边使用原向，右屏边镜像，并按当前屏幕裁去藏身部分。画面不自带边框，位置随真实屏幕边缘变化。

## 丰富的桌宠动作库（2026-11-07）

桌宠在原有动作之外加入 236 组四帧动作：移动与状态 64 组、龙娘主题 72 组、日常生活 96 组、边缘过渡 4 组。包括不同心情下的回消息、睡觉、飞行、屏幕边缘、手作与小道具互动。角色仍是成年 Q 版小龙，保持银白淡紫头发、鼠尾草绿开衫和原有服装。当前运行素材清单共 279 条、1,110 个源帧，压缩素材约 32.5 MB。

本轮生成账本记录 253 次服务端成功并保存的图片，其中 Flare 236 次、Sunburst 17 次；正式采用 232 组动作。失败和跳过不计入成功次数。原始候选留在仓库外，运行包只放验收后的 WebP 和清单；动作提示与元数据保存在 `regular/pet_actions_*.py` 文件中。生成、验收及本机播放方式见[桌宠动作库](pet-repertoire.md)。

边缘过渡另生成 6 次，采用 4 组：藏起、跳出、倒挂探头和转正；多手候选已淘汰。后续统一按人物头部测量校准整条动作，并将透明画布扩至 512×512。举手、翅膀和道具不再决定人物缩放，帧内保留相同倍率；原始动作与采用版本分开保存，便于对照。

飞行打磨又用 Flare 重画并替换 5 组：慢速扑翼和 4 组空中回复。四帧明确分为上振、展平、下振、展平，逐帧核对头身比例，避免只有人物上下移动、翅膀却不动。运行时也去掉整张人物图的呼吸缩放、按压缩小和落地压扁；同一动作播放保持固定比例。

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
