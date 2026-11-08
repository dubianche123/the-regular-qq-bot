# 比例、飞行与有目的的魔法 · 2026-11-08

后续更正：`470de78` 的 hover 闪透明与魔法时长反馈见[后续修复记录](pet-hover-visibility-2026-11-08.md)。本页保留当时素材和时序验收；使用 `QWidget.grab()` 的旧录像不能证明窗口系统没有隐藏/重显副作用，新录制已改为 WindowServer 输出。

用户实机指出空中和吃莓果时身体变小、透明处出现摸头手形并抢点击，以及飞行静翼、垂直平移。本轮同时处理素材与调度；用户在录制期间又指出降落不自然，因此补查接地前后的帧序。魔法改成先有愿望、再用能力解决一件小事，而非随机摆手或无目标传送。

## 比例不是只看头径

运行时 `scale` 及 `sx/sy` 没有因飞行或甩出而降低。旧下落稿和莓果稿的头接近 idle，但衣身、腿被画短：同尺度图里 idle 的领花至裙摆约 106px，旧 upward 约 65px、curl 约 69px、reach 约 80px。直接放大整图会把头也放大，不能解决这个问题。

替换八张四帧素材：`motion_fall_upward/flutter/curl/reach`、`motion_fly_land`、`motion_land_one_foot`、`motion_land_wing`、`dragon_snack_berry`。32 帧头径比为 0.9765–1.0232，人工衣身约 94–108px，构建无裁切。curl 多手和 fly_land 发梢裁切的首稿已弃用，共十次项目 Images API 请求。详见[资产审核](/private/tmp/regular-air-scale-20261108/review/asset-review.md)及[莓果前后对照](/private/tmp/regular-air-scale-20261108/review/berry-comparison.jpg)。

第二张用户截图准确对应 `life_comic_turn`。同尺度头径约 1.010–1.013，鞋底与露出大腿也与 idle 接近；身体坐下、书遮住腰部会降低总高度，本轮没有把正常坐姿整体放大。拖拽举手造成的自动头部匹配误差也未被当作缩小证据。保留的动作、依据和边界均在上述审核中。

## 从起飞到接地

原自然路线是垂直升高、站姿静翼滑行、再垂直落下。新路线斜向起飞、短程持续拍翼、斜向下降；同一拍翼片段跨路段沿用帧时钟。身体按实际横向速度前倾，最多 12°，不缩放角色。被打断返航也使用拍翼与斜向回位，空中喷火保持自身完整时长。

第一轮实录中，用户指出降落仍奇怪。逐帧发现：拍翼已把脚锚带到 floor，才开始播放 `motion_fly_land` 的空中伸腿首格，轮廓先向上约 4px、下一格又向下约 11px。问题是准备与接地的顺序，不是需要把整段飞行加速。

现在从同一条修正后的素材建立两个播放别名：`motion_fly_approach=[0]`、`motion_fly_settle=[1,2,3]`。下降距离地面不超过 `0.18 × figure` 时只切换成伸腿画面，保留原移动 Step 的起点、目标、时钟和时长；接地后直接播缓冲、收翼、站稳。自然飞行、被打断返航和空中互动统一使用，缺素材时回退；物理抛落、自由下落和法阵传送不借用这套拆分。两个别名不增加图片或帧库存，可用动作项共 339 个。

## 原生点击区域

旧实现遇到空中、旋转、跳起或淡入淡出就 `clearMask()`，整个透明窗口都可能接收鼠标。新轮廓使用与绘制一致的变换，并合并交叉淡入的两张图，再做四边和法阵裁切；不再额外扩张三像素。

Cocoa 的原生窗口输入还需随真实光标位置切换：透明处穿透并撤掉手形，实体处恢复接收；已按住或拖拽时保持捕获。空中抓鼠标仍全程透传，气泡独立处理。

实际 `CGEventPost` 点击验证覆盖七种状态的空白/实体，共 **14/14** 通过：空白点击到自建底窗，实体点击到桌宠，原生 `ignoresMouseEvents` 与预期一致。未点击其他应用，完成后恢复原鼠标位置。空白没有 OpenHand；实体处注入环境的系统 cursor 读数偶尔仍报 Arrow，因此不把 Qt/NSView 已配置 OpenHand 写成系统显示已完全验证。[原生证据与限制](/private/tmp/regular-air-scale-20261108/hit-native-window-cursor/review.md)。

## 能力服务日常

| 新片段 | 起因、动作、结果 |
| --- | --- |
| `magic_daily_cola` | 想喝凉的，对同一杯可乐吹冰息，喝一口，舒服地收杯 |
| `magic_daily_roast` | 馋棉花糖，从嘴里吐小火烤黄，咬下，再收起空签 |
| `magic_daily_scarf` | 围巾湿了，用暖息吹干，贴脸确认舒服，放回正常位置 |
| `magic_daily_brush` | 够不着尾巴一处，让小木刷悬浮过去，接回后收好 |
| `magic_daily_mend` | 发现书角破损，用指尖金线修复，轻轻检查，再收同一本书 |

五张各四帧、单次 3.5 秒，顺序 `[0,0,0,1,1,1,2,2,2,2,3,3,3,3]`，不加入长驻循环。自动编排共用一个 `daily_magic` 家族，保留既有剧场间隔、两次长驻与概率门槛。手动「变个魔法」首次优先变龙，之后从已采用节目中无重复轮换；自动变龙的稀有门槛不变，真实忙碌、睡眠、关闭动作优先。

普通无目标随机传送停止。远处真实静止的球、硬币、宝石才是主动追物的目标；施法与到达都核对同一件物品。拖走、移动、过期或打断不假报拿到。踢球/收集成功后只接空手 `cheer/nod`，避免又掏出不同物件。自动注意道具仍沿用原有 15% 检查与 120 秒追物冷却，自动传送还受 75 秒冷却约束。

新魔法使用项目已有生成流程与授权的 `gpt-image-2.6-flare`。五张首稿和五张定向修稿后，刷尾与修书再各修一次；刷尾编辑仍保留短衣身，因此弃用，最后从 idle 重新生成一张直背、正常髋位的版本。新魔法共十三次请求，连同八张替换共二十三次；没有 SDK 自动重试。最终采用 cola/roast/scarf v2、mend v3、brush v4。刷尾最终衣身约 105px，idle 约 106px。修书角是小尺寸下的细节，不能当作大幅炫技来描述。

[首轮独立审核](/private/tmp/regular-purpose-magic-20261108/independent-review/review.md)、[定向修稿记录](/private/tmp/regular-purpose-magic-20261108/review-v3/review.md)、[最终梳尾比例](/private/tmp/regular-purpose-magic-20261108/review-v4/review.md)分别保留采用与弃用依据；生产规格位于 `regular/pet_actions_magic_daily.py`。库存为 **377 条 WebP、1502 帧、334 个动作定义**，长驻循环仍为 34 条。

## 实机验收方式

`tools/record_pet.py` 使用真正的 Cocoa `PetWidget`、真实单调时钟及原图播放速度，只捕获验收进程自己的角色、道具、气泡与特效窗口。服务客户端被阻断，节目后的随机闲逛被固定为 idle。魔法使用真实 `brain.magic` 入口，首次变龙不指定 kind；追球只固定一个真实道具的起始位置，实际传送、碰撞和 kick 仍由生产代码执行。截图动作保留姿态过渡，甩出与松手使用真实 press/drag/release。

修图首稿、静态尺寸测量、合成回放与实机录制是不同证据，不能互相冒充。完整录制可以判断此次衔接，不能证明挂几天后的耐看程度。

最终录制覆盖自然飞行、打断返航、飞着喷火、抓到/抓空、抛出、松手下落、顶部开阵、两张用户截图、首次手动变龙、向真实球传送，以及五种日常魔法，共 **17 段、2873 帧、约 238 秒**。初次飞行取景范围没有覆盖新路线末端，已扩大到整个可用屏宽重录；原生桌宠窗口本身未被裁掉。五条动力飞行路径都验证了接地前 source 0、地面 source 1/2/3 和连续时钟，抓空分支另延长录制以覆盖其返回 idle。

| 实机检查 | 连续录制 |
| --- | --- |
| 自然飞行与接地逐拍 | [完整飞行](/private/tmp/regular-air-scale-20261108/after/air-flight/continuous.webp)、[接地逐拍](/private/tmp/regular-air-scale-20261108/after/air-flight/touchdown-beats.jpg) |
| 打断返航、空中喷火、抓到与抓空 | [打断](/private/tmp/regular-air-scale-20261108/after/flight-interrupt/continuous.webp)、[喷火](/private/tmp/regular-air-scale-20261108/after/air-fire/continuous.webp)、[抓到](/private/tmp/regular-air-scale-20261108/after/air-catch/continuous.webp)、[抓空](/private/tmp/regular-air-scale-20261108/after/air-miss/continuous.webp) |
| 抛出、松手下落、顶部离场 | [抛出](/private/tmp/regular-air-scale-20261108/after/throw/continuous.webp)、[下落](/private/tmp/regular-air-scale-20261108/after/drop/continuous.webp)、[顶部](/private/tmp/regular-air-scale-20261108/after/top/continuous.webp) |
| 两张用户截图对应动作 | [莓果碗](/private/tmp/regular-purpose-magic-20261108/native/inspect-dragon_snack_berry/continuous.webp)、[翻漫画](/private/tmp/regular-purpose-magic-20261108/native/inspect-life_comic_turn/continuous.webp) |
| 首次手动变龙与真实目标传送 | [小龙](/private/tmp/regular-purpose-magic-20261108/native/magic-dragon/continuous.webp)、[去踢球](/private/tmp/regular-purpose-magic-20261108/native/magic-prop/continuous.webp) |
| 五种日常魔法 | [可乐](/private/tmp/regular-purpose-magic-20261108/native/magic_daily_cola/continuous.webp)、[棉花糖](/private/tmp/regular-purpose-magic-20261108/native/magic_daily_roast/continuous.webp)、[围巾](/private/tmp/regular-purpose-magic-20261108/native/magic_daily_scarf/continuous.webp)、[梳尾](/private/tmp/regular-purpose-magic-20261108/native/magic_daily_brush/continuous.webp)、[书角](/private/tmp/regular-purpose-magic-20261108/native/magic_daily_mend/continuous.webp) |

[汇总数据](/private/tmp/regular-purpose-magic-20261108/final-native-summary.json)保留验证与采样间隔；[五段飞行独立核对](/private/tmp/regular-air-scale-20261108/after/final-native-independent-review.md)保留时钟、轨迹和抓取距离。全部配置比例为 1、绘制 `sx/sy=1`，原生锚点取整误差小于 0.5 个逻辑像素。可见飞行与抛落没有整窗无遮挡输入；完全藏入法阵时有九个空 Qt mask 样本，但原生输入均已透传，不是九次抢点击。五段日常完整展示 0/1/2/3 并回到 idle；真实球的 id、kick 返回值与实际位移见对应 `frames.json`。

## 定向验证

八个直接相关文件合跑 **310 passed、1108 subtests passed**：`test_pet_powered_flight`、`test_pet_magic_purpose`、`test_pet_brain`、`test_pet_air_play`、`test_pet_props`、`test_pet_routines`、`test_desktop_pet`、`test_pet_repertoire`。两条已有 Qt 退出 `disconnect` 警告仍在。

手动魔法与换节目共用收纳队列，因此额外跑 `test_pet_activity_endings`、`test_pet_ending_flow`：**37 passed、23 subtests passed**。合计 **347 passed、1131 subtests passed**。未运行无关全量测试或付费群聊评测。旧日常测试改验真实走动、飞行与日常魔法可达性，不再要求无目标传送，也不强求某一稀有家族必须在单个随机小时里出现。
