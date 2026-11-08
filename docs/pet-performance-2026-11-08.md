# 桌宠演出补画与实机验收 · 2026-11-08

这轮继续解决三个具体问题：投喂跨旧素材时身体变短、边缘睡醒时支撑滑动，以及人物设定里的情绪选择只有文字、缺少身体表演。先收窄旧换皮候选，再把缺少的选择画出来；完整剧场沿用原来的低频门槛，不靠更频繁地演出来展示库存。

后续用户截图指出了上一轮验收漏掉的问题：顶部隐藏仍露出整个人，侧边蜷睡也把身体放在屏内。此前的“挂点稳定、醒来正常”结论只覆盖位置和状态衔接，没有验收“躲在边外、只探头”这件事。下面保留当时的记录，并单列此次曝光与手动换节目修正；旧测试数和旧录像不作为这些新修正的验收结果。

## 采用范围

15 张 `daily_*` 与 8 张 `edge_daily_*` 已全部采用。`sprites.json` 实测 360 条 WebP、1,434 帧、36,527,434 字节：43 条原有动作，加 317 个动作库定义。动作库另提供同源抱书睡和侧边打盹两个播放别名，共 319 个可用动作项；别名不另占 WebP。

本轮补入 23 张四帧表，共 92 个运行帧；`daily_book_sleep_hold` 复用 `daily_book_hug_sleep` 的末两帧，`boundary_side_doze` 复用 `motion_edge_look` 的闭眼帧，均不新增原画。动作定义与采用分离：未进入 `sprites.json` 的图不参与运行，缺少来源时播放别名也不可用。

日常提示与元数据在 `regular/pet_actions_daily.py`，边缘部分在 `regular/pet_actions_edge_daily.py`；编排在 `regular/pet_routines.py`，真实状态、投喂和边缘调度在 `regular/pet_brain.py`。以下“触发”只指本机代码已有的心情、精力、作息和交互状态。看向观众、发现观众忙等是写好的小戏，不读取屏幕内容或推断真实注意力。

## 原待画方向怎样落地

除投喂和安静循环外，下表均进入原有完整剧场选择：距上段至少 180 秒、经过两次长驻，再以 45% 概率进入。仍有近期剧目和已播结尾去重；没有提高旧剧场频率。

| 原先缺少的方向 | 新片段 | 可见的选择与情绪落点 | 触发条件 |
| --- | --- | --- | --- |
| 把书抱好安心睡，不补装精神 | `daily_book_hug_sleep` → `daily_book_sleep_hold` | 合书、抱到胸前、蜷腿闭眼；同源末两帧继续睡，安心结束 | `scene:sleepy_read`，困时可选；睡姿停留 35–60 秒 |
| 最后一块也愿意给你 | `daily_last_cookie` | 举到嘴边停住，完整饼干放到前方，空手退开；主动温柔 | `scene:sweet` 的一条独立分支 |
| 炫技改为护住一盏小灯 | `daily_lamp_shelter` | 收起大动作、蹲下护灯、小火点稳，坐在灯旁；把本事用好 | `scene:fire` 的成功分支；安静模式不选喷火类 |
| 高处抓稳后被新东西吸引 | `edge_daily_top_wonder` | 挂点和胸前双手固定，只用眼神跟随同一片绒羽；安静发现 | 顶边清醒隐藏的低频微动作 |
| 怕烫变成照顾别人 | `daily_second_cup` | 两杯从第一帧就存在；吹凉手中第二杯、检查再递出，不自己喝掉 | `scene:tea_kindness`，安静模式也可选 |
| 翻到忘记的小收藏，停下清点 | `daily_old_keepsake` | 打开盒子，拿起旧星饰，坐下端详；出神与珍惜 | `scene:keepsake`，安静模式也可选 |
| 对方忙时收回招呼，留一个位置 | `daily_quiet_space` | 手举到一半放下，坐下挪开尾巴，轻拍空位；不催促的亲近 | `scene:viewer` 的一条分支，安静模式也可选 |
| 收藏与分享：舍不得到真正松手 | `daily_gem_release` | 递出又收回同一颗宝石，放下后双手摊开；保留一点不舍 | `scene:collection`，独立四帧，不串旧宝石动作 |
| 逞能与被注意：先确认，再准备 | `daily_audience_prepare` → `scene_fire_controlled` | 看向观众、点头、站稳、准备，接小火成功；认真与自信 | `scene:fire`，两段素材都采用时才可选 |
| 创作与亲近：把画当礼物 | `daily_painting_gift` | 最后一笔、抱画、放到前方、空手退开；期待被珍惜 | `scene:gift`，同一画布从头到尾，不接旧画作 |
| 困倦与好胜：今天真的到这里 | `daily_game_set_down` | 输了肩翼垂下，掌机放到身旁，抱膝停留；真失落，不强补胜利 | `scene:tired_game`，困时或精力低于 0.5 |

新书、新杯、宝石和画布的小戏各自在同一张表内完成。没有拼接不同开本的漫画、不同花盆或纸星与纸鹤，也没有画不存在的收礼者手。被打断时未播出的结果直接放下，不在下一件事后补演。

## 具体情绪与投喂

| 片段 | 身体动作与落点 | 入口与停留 |
| --- | --- | --- |
| `daily_cookie_receive` | 抬眼伸出空掌 → 接住饼干 → 小口咬、鼓腮 → 吃完空掌、满足回应 | 明确投喂；`feed_expect/accept/bite/content` 各取同一图的第 0/1/2/3 帧，不进随机节目 |
| `daily_joy_tail_catch` | 尾尖先翘起，踮脚展翼，抱住自己的尾巴，再放开双手 | 心情至少 0.2 的 `scene:joy`；安静模式不选 |
| `daily_low_tail_comfort` | 坐下收拢膝盖，轻抚尾巴，脸靠手臂；情绪没有突然变开心 | 心情至多 −0.2 的 `scene:low`；安静模式可选 |
| `daily_curious_feather` | 蹲下吹动羽毛、托起观察、坐着看细节；好奇有停留 | `scene:wonder`；安静模式不选 |
| `daily_quiet_tail_rest` | 坐姿、头和肩固定，手搭尾巴，慢眨眼和尾尖微动 | 长驻 `ambient:quiet_company`，45–90 秒；唯一新增日常循环 |

投喂回执只确认当前这一份：早到时接在期待之后，迟到时继续接过、吃和满足，不重新期待、不重吃。重复事件不重新触发；吃饱仍走拒绝路径。本机心情账本与 QQ 好感账本保持原有边界，不为这些情绪演出新增模型调用。

## 边缘微动作与出口

| 片段 | 接触与动作 | 入口 |
| --- | --- | --- |
| `edge_daily_side_tail_greet` | 两手固定抓边，尾尖先伸出来招呼，眼神发现后带一点笑 | 左右边缘清醒隐藏；右边镜像 |
| `edge_daily_side_listen` | 抓握和身体固定，耳、发梢与眼神做倾听反应 | 左右边缘清醒隐藏 |
| `edge_daily_bottom_watch` | 双前臂固定撑在底边，低头靠近袖口、看回来 | 底边清醒隐藏 |
| `edge_daily_bottom_yawn` | 肘和前臂不离开支撑，小哈欠后重新睁眼 | 底边清醒隐藏，不直接转睡 |
| `edge_daily_top_wonder` | 藏住躯干腿脚，只露倒挂头与必要手部，眼神追绒羽 | 顶边清醒隐藏 |
| `edge_daily_top_cast` | 保持隐藏位置正常施法，近端入口与地面出口从零张开 | 顶边点击离开时先演，不先下移露身 |
| `edge_daily_top_release` | 首格仍只探头，随后松脚、侧转、斜身、正身悬空，进入已打开的阵 | 顶部施法结束之后，不先落到桌面再施法 |
| `edge_daily_warp_stumble` | 已落地才踉跄，扶稳后重新站好 | 非喷嚏传送落地后 18% 概率；不连续两次出现 |

清醒隐藏后首段微动作等 25–40 秒，之后隔 45–70 秒，距离原定入睡不足四秒就不再开始。安静模式或减弱动态时不演；播完恢复原探头，不重新计算入睡时间。入睡片段只播一次，再循环同源睡姿；点击逆序醒来后才进入出场。底边不再同时移动窗口下沉，顶部不再把倒挂姿态直接切成地面站姿。

## 后续更正：藏身曝光与换节目节奏

顶部隐藏的位置按可见范围决定：清醒与微动作的锚点在顶边下方 `0.45 × figure`，睡眠为 `0.38 × figure`，让躯干、腿脚留在屏外；不再拿鞋尾支撑 `rest_y` 决定隐藏位置。最初的曝光修正仍在 `prepare` 固定首格，用 0.4 秒移到支撑位置，随后才施法。用户继续指出这会变成“先下移再开阵”，因此取消了这段平移。

当前顺序是 `prepare` 保持隐藏位置并正常播放 `edge_daily_top_cast`，近端入口与地面出口先从零张开。施法结束进入 `edge_daily_top_release`，首格仍只探头，然后松脚、转身进入已开的门。静止隐藏与微动作仍只露头，不因出场流程改变。清醒点击和睡醒点击需要各自验证；新顺序的 18 秒原生录制分别归档于 `/private/tmp/regular-air-play-20261108/top-awake/top` 与 `top-wake/top`，实际结果和新测试记录见[空中互动记录](pet-air-play-2026-11-08.md)，不沿用下方旧阶段的通过结论。

左右打盹改用 `boundary_side_doze`：同源 `motion_edge_look` 的入睡帧序为 `[1, 1, 3]`，睡眠循环为 `[3, 3]`，继续保持抓边和探头的姿势。旧 `boundary_side_sleep` 全身蜷睡素材保留，设为 `automatic=False`；日常隐藏不再自动选它，也不自动进入睡滑。此轮没有改图、增加素材或调整人物缩放。

“换个节目”此前直接强制短剧，绕开了自然日常的选择门槛，看起来像动作突然加快。现 `next_activity` 共用自然入口的选择与时长逻辑。相同日间条件和初始历史下，seed 0–99 的排定节目时长中位数如下（含结尾 idle，不含姿态桥接）：

| 版本 | 自然入口 | 按钮入口 |
| --- | ---: | ---: |
| 修复前 | 66.29 秒 | 4.00 秒 |
| 修复后 | 66.29 秒 | 66.29 秒 |

修复后 100/100 个种子的选片和时长一致，自然入口本身 100/100 与修复前相同；没有改素材 FPS。seed 7 的两个入口以 60Hz 实际采样十秒，21 次可见帧变化也完全一致。定向回归为 7 passed、14 subtests passed；证据见[换节目对照记录](/private/tmp/regular-pet-followup-20261108/next-activity/comparison.md)。这些条件下没有抽到完整剧场，不表示按钮以后不会选到符合门槛的剧场。

### 上一阶段曝光更正的验证记录

`/private/tmp/regular-edge-peek-20261108/` 保留为上一阶段曝光验收历史。以下录像均用真实 Cocoa Qt 窗口与真实时钟，人工观看了隐藏、微动作、睡醒和当时的主动离场；顶部仍使用先露身再开阵的旧顺序，不能验证此后改为原位施法的流程。

| 场景 | 当时的原生录像 | 时长与捕获 | 当时的人工复核结果 |
| --- | --- | --- | --- |
| 顶边、左右边睡醒 | [顶边](/private/tmp/regular-edge-peek-20261108/native-sleep/top/continuous.webp)、[左边](/private/tmp/regular-edge-peek-20261108/native-sleep/left/continuous.webp)、[右边](/private/tmp/regular-edge-peek-20261108/native-sleep/right/continuous.webp) | 各约 18 秒、217 帧 | 静止与睡眠只露头和抓边部分，睁眼→闭眼→醒来，腿鞋在屏外；顶部主动离场先用 0.4 秒渐露再施法 |
| 顶部、底部微动作 | [顶部](/private/tmp/regular-edge-peek-20261108/native-comparison/top/continuous.webp)、[底部](/private/tmp/regular-edge-peek-20261108/native-comparison/bottom/continuous.webp) | 各约 16 秒、193 帧 | 顶部观察仍只露头、必要手部和羽毛；底部露头与前臂，腿脚在外；最后均回到 `ground` |
| 自然入口与真实按钮 | [自然入口](/private/tmp/regular-edge-peek-20261108/native-comparison/natural/continuous.webp)、[按钮入口](/private/tmp/regular-edge-peek-20261108/native-comparison/next/continuous.webp) | 各约 16 秒、193 帧 | 按钮通过实际 Qt `toolbar.play.click()` 触发，两个入口的排定节目和捕获到的 30 次可见帧变化序列一致 |

按钮与自然入口均排定 `dragon_magic_feather` 74.292051 秒，加结尾 `idle` 1 秒；录像比较的是开头 16 秒。该素材保持 2 FPS，捕获帧变化间隔中位数分别为 0.5106 / 0.4962 秒，差异来自 12 FPS 录制采样，不是播放速度改变。该批录像的原生窗口取整最大锚点误差约 0.5 像素，全部数据见 [summary.json](/private/tmp/regular-edge-peek-20261108/summary.json)。[曝光修正前后图](/private/tmp/regular-edge-peek-20261108/before-after.png)与[微动作曝光图](/private/tmp/regular-edge-peek-20261108/micro-exposure.jpg)也已人工复核。

该阶段完整重跑直接相关的六个测试文件：**275 passed、1061 subtests passed**，无失败；有两条已有的 Qt 退出 `disconnect` RuntimeWarning。此结果单独记录，不与前面的定向回归或旧阶段结果相加，也不作为此后顶部离场顺序修正的测试结果：

```bash
.venv/bin/python -m pytest -q \
  tests/test_pet_brain.py tests/test_pet_brain_edges.py tests/test_pet_repertoire.py \
  tests/test_pet_routines.py tests/test_pet_daily.py tests/test_desktop_pet.py
```

## 制作和采用

主要通过项目 OpenAI Images API `gpt-image-2.6-flare` 并行生成，每张一次请求，不自动重试。提示要求 1024×1024 的 2×2 表，每格 512×512、四肢完整、至少 30 像素透明留白；头、躯干和四肢比例与参考一致，身体不被锁死。人物身份参考与已采用风格参考由项目提供，不发送群聊。

`daily_last_cookie` 与 `edge_daily_side_tail_greet` 使用内置图像编辑精准修复饼干松手及固定双手。顶部多手、支撑和转身不清的候选另作重画与验看，不用字幕遮盖。`edge_daily_top_cast` 构建时采用源格 `[0, 1, 3, 3]`，末帧多停一拍；有多手的源格 2 不进入发行条。原始表、失败稿和请求账本保留于 `/private/tmp/regular-pet-followup-20261108/`，不进仓库；运行包只收最终 WebP 条与 `sprites.json`。播放别名和重复取帧不计为新原画。

## 前一阶段验证证据与边界

| 项目 | 当时完成的检查 | 当时结论 |
| --- | --- | --- |
| 投喂衔接 | 同源四拍；0.1 秒与 2 秒回执、重复事件、吃饱路径的定向回归；延迟回执原生录制 | 回执不会再触发第二遍吃；原生记录只出现一次四拍 |
| 日常调度 | 未采用素材不可选、同源抱书睡、情绪门槛、安静模式、独立道具与唯一新长驻循环的定向测试 | 新节目沿用原故事频率；书睡不随机插入别的书后面 |
| 固定支撑与中断 | `tests/test_pet_brain_edges.py` 覆盖 0.6/1/1.5 倍四边睡醒接触点、逆帧醒来、拖动打断、微动作后恢复和不推迟睡眠；四边清醒微动作原生录制 | 眼神、支撑与出口观看通过；动态锚点误差小于 0.5 像素 |
| 顶部路线与踉跄 | 支撑下开阵、转身后沉入、升出后才落地；两次连续传送回归 | 不接地面恐高片段；踉跄只在落地且不连两次 |
| 四边睡醒与离开 | 最终每边 16 秒原生录制，均 193 帧；醒来逆序 `2 → 1 → 0`，顶部转身 `0/1/2/3` 全部捕获 | 支撑、逆醒与离开观看通过，全部回到地面；最大捕获间隔 0.1083 秒，锚点误差小于 0.5 像素；底边实际使用枕头睡图 |
| 六段原生日常 | Cocoa 真实时钟，每段约 10 秒、121 帧；实际原生位置与脑内锚点核对 | 播放缩放均为 `(1, 1)`，最大锚点偏差 0.2 像素 |
| 图内比例 | 对照待机和逐帧人工审阅；留空位末帧再查虹膜间距 | `daily_quiet_space` 眼距帧间约差 1%，坐下的外轮廓变化不按人物缩放处理 |
| 气泡避让 | 使用 `SpriteBank.region` 缓存的实际 alpha 轮廓，并映射到与绘制相同的变换；四边 × 三种尺寸 × 三帧醒来的 36 个子测试直接检查像素区域与气泡不相交 | 已修复躺姿估算过矮和发梢外伸造成的遮挡；最终 Qt 测试和四边原生重录通过，头发、角和眼睛均无遮挡 |

上表与录像曾据此记录“支撑与出口观看通过”，但没有识别身体曝光过多。验证保留两次实际运行的结果，不把它们相加当作一次总跑。气泡最终修正前，相关九个测试文件为 **280 passed、939 subtests passed**，无失败；出现两条已有的 Qt 退出 `disconnect` RuntimeWarning：

```bash
.venv/bin/python -m pytest -q \
  tests/test_pet_brain.py tests/test_pet_brain_edges.py tests/test_pet_daily.py \
  tests/test_pet_routines.py tests/test_pet_repertoire.py tests/test_pet_sprite_pipeline.py \
  tests/test_desktop_pet.py tests/test_desktop_bundle.py tests/test_pet_care.py
```

气泡改用真实 alpha 轮廓后，完整重跑直接相关的 Qt 测试文件，结果为 **56 passed、62 subtests passed**，无失败，同样有两条已有的 Qt 退出 `disconnect` RuntimeWarning。其余八个测试文件对应模块没有再改，未重复运行：

```bash
.venv/bin/python -m pytest -q tests/test_desktop_pet.py
```

原生录制工具是 `tools/record_pet.py`。它创建真实 Cocoa Qt 窗口，按实际经过时间运行脑内调度，只抓取验收进程自己的 Qt 窗口，并按 `NSWindow` 的真实位置合成；没有录入其他应用，也没有连接线上服务。投喂使用延迟本地回执，日志中的 `blocked_requests: ["care"]` 表示请求被拦截并由合成回执替代。

六段日常记录位于 `/private/tmp/regular-pet-followup-20261108/final-native/`：

| 场景 | 连续画面 | 重点 |
| --- | --- | --- |
| 投喂 | [feed/continuous.webp](/private/tmp/regular-pet-followup-20261108/final-native/feed/continuous.webp) | 空手期待、同源吃饼干、延迟回执只确认一次 |
| 抱书睡 | [daily_book_hug_sleep/continuous.webp](/private/tmp/regular-pet-followup-20261108/final-native/daily_book_hug_sleep/continuous.webp) | 合书入睡后只留同源睡姿，不再合书一次 |
| 护灯 | [daily_lamp_shelter/continuous.webp](/private/tmp/regular-pet-followup-20261108/final-native/daily_lamp_shelter/continuous.webp) | 炫技转为照料，站到坐有身体变化 |
| 低落 | [daily_low_tail_comfort/continuous.webp](/private/tmp/regular-pet-followup-20261108/final-native/daily_low_tail_comfort/continuous.webp) | 抱尾巴自我安慰，情绪保留 |
| 欣喜 | [daily_joy_tail_catch/continuous.webp](/private/tmp/regular-pet-followup-20261108/final-native/daily_joy_tail_catch/continuous.webp) | 尾巴、翅膀、踮脚共同表达开心 |
| 留空位 | [daily_quiet_space/continuous.webp](/private/tmp/regular-pet-followup-20261108/final-native/daily_quiet_space/continuous.webp) | 收回招呼、坐下挪尾巴、轻拍空处 |

四边清醒微动作使用相同的原生录制方式，每段 16 秒，包含探头、微动作、恢复与点击离开。下列睡醒录像是气泡修复后的阶段版本，早于此次藏身曝光更正：

| 场景 | 连续画面 | 捕获帧数 |
| --- | --- | ---: |
| 左边 | [left/continuous.webp](/private/tmp/regular-pet-followup-20261108/final-native-edges/left/continuous.webp) | 193 |
| 右边 | [right/continuous.webp](/private/tmp/regular-pet-followup-20261108/final-native-edges/right/continuous.webp) | 186 |
| 底边 | [bottom/continuous.webp](/private/tmp/regular-pet-followup-20261108/final-native-edges/bottom/continuous.webp) | 191 |
| 顶边 | [top/continuous.webp](/private/tmp/regular-pet-followup-20261108/final-native-edges/top/continuous.webp) | 185 |
| 左边睡醒与离开 | [left/continuous.webp](/private/tmp/regular-pet-followup-20261108/final-native-wake-clear/left/continuous.webp) | 193 |
| 右边睡醒与离开 | [right/continuous.webp](/private/tmp/regular-pet-followup-20261108/final-native-wake-clear/right/continuous.webp) | 193 |
| 底边睡醒与离开 | [bottom/continuous.webp](/private/tmp/regular-pet-followup-20261108/final-native-wake-clear/bottom/continuous.webp) | 193 |
| 顶边睡醒与离开 | [top/continuous.webp](/private/tmp/regular-pet-followup-20261108/final-native-wake-clear/top/continuous.webp) | 193 |

气泡修复阶段睡醒记录的精确数据如下，原始汇总保存在 [summary.json](/private/tmp/regular-pet-followup-20261108/final-native-wake-clear/summary.json)：

| 边缘 | 实际时长（秒） | 最大锚点误差（像素） | 最大捕获间隔（秒） |
| --- | ---: | ---: | ---: |
| 左 | 16.0094 | 0.4846 | 0.0966 |
| 右 | 16.0049 | 0.4994 | 0.0998 |
| 底 | 16.0023 | 0.4752 | 0.0971 |
| 顶 | 16.0005 | 0.4903 | 0.1083 |

[四边各四个关键时刻的复核图](/private/tmp/regular-pet-followup-20261108/final-native-wake-clear/wake-clear-review.jpg)当时确认了气泡没有遮住头发、角和眼睛，逆醒后能回到地面。真实 alpha 轮廓避让修复了气泡遮发问题；该记录没有证明隐藏时只露头，不能覆盖此次用户截图指出的身体曝光问题。每段同目录的 `keyframes.jpg` 是关键帧速览，`frames.json` 保存时间、clip、strip、帧号、原生窗口位置、锚点误差和播放缩放。临时证据不会随运行包发布。

数字能检查卡住、重播、错位或放缩，不能证明可爱和耐看。前一阶段人工观看确认了这些片段的动作顺序与落点，却漏掉了隐藏曝光；这次反馈补上了检查范围。长期重复观看是否仍有趣，仍以实际使用反馈继续取舍，不以新增 23 张作为完成质量的证明。
