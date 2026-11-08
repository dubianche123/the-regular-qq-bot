# 工作与音乐尾声 · 2026-11-08

本轮给已有电脑、尤克里里和拇指琴补了六种结果、三段收纳。没有增加长驻循环或提高完整剧场频率。重点是同一件物品从正在使用、停手到收好都能接上，情绪也不全部回到“失手后装没事”。

## 采用的九组素材

| 原活动 | 结果一 | 结果二 | 共用收纳 |
| --- | --- | --- | --- |
| `life_laptop_type` / `life_laptop_think` | `ending_work_clear`：理顺一点，轻轻松口气 | `ending_work_pause`：仍没想清楚，停手歇一歇 | `ending_work_put_away`：合盖、移到臀尾后、空手 |
| `dragon_music_ukulele` | `ending_ukulele_resolve`：满意地收住和弦 | `ending_ukulele_sour`：弹错后低头认真听，不追加成功 | `ending_ukulele_put_away`：放低、移到身侧、遮住后空手 |
| `dragon_music_kalimba` | `ending_kalimba_linger`：安静听余音 | `ending_kalimba_drowsy`：困了，慢慢停下拇指 | `ending_kalimba_put_away`：从膝上移到身侧、藏到臀后、空手 |

两段工作图使用同一台灰绿电脑和花形标记，保持盘腿坐姿。尤克里里保持站姿与同一把棕色琴；拇指琴保持并膝坐姿与原有银色琴键。两琴虽然同属 `strings`，尾声按完整动作名匹配，不按家族互换。结果最后仍持原物件，收纳最后空手；没有增加包或琴盒，也不使用道具淡出。

每组四格，真实透明背景，4 FPS，播放顺序为 `[0,0,1,1,2,2,3,3,3,3]`，单次 2.5 秒。新片段均为 `activity_ending`、`automatic=False`，只接在原活动之后。全库现为 **372 条 WebP、1482 帧、329 个动作定义、332 个可用动作项**；普通长驻仍是 34 条。

使用项目现有 `tools/pet_sprites.py` 和已授权的 OpenAI Images API `gpt-image-2.6-flare`。共 13 次成功请求，无自动重试：九张首稿、三张收纳重画，以及拇指琴的一次局部修手。先画结果，再让收纳参考实际结果末格。

首稿电脑收纳的闭眼笑会覆盖“没理顺”的情绪，改为平静睁眼、闭嘴。尤克里里首稿移到身侧的过程不清楚，重画后保留完整琴、半遮挡、空手三个状态。拇指琴第二稿出现多余的胸前拳头，局部编辑去掉；保留全部四个移动格，没有用跳帧掩盖问题。

最终 36 帧相对各自原活动的头部测量比值为 **0.9733–1.0509**，匹配分数 0.5649–0.7875，构建没有裁切。测量用于发现比例问题，不代替画面审查；同尺度对照和实录未见明显尺寸跳变。原画、完整请求与弃稿原因见[素材审核说明](/private/tmp/regular-activity-endings-20261108/review/asset-review.md)，制作规格在 `regular/pet_actions_endings.py`，逐帧数据在[头部检查](/private/tmp/regular-activity-endings-20261108/review/head-check.json)。

## 调度与打断

长驻主段仍为 45–90 秒；独立电脑入口仍为 8–15 秒，二者都接同一套尾声。原主段 FPS 不变。

至少 72% 的普通结束直接安静收纳，安静模式至少 82%；其余才尝试一个情绪结果。工作按心情、精力和困意变化；走音只在非安静模式且低精力或心情偏低时少量出现；打瞌睡只在困倦或低精力时出现。概率与条件先于去重，抽中近期重复结果就直接收纳，不强迫罕见情绪出场。缺结果图时安静收纳，缺对应收纳图时保留原活动。

主活动中点“换个节目”跳过未播结果，直接收好当前物件。已经在结果或收纳中时保留当前动作、起始时间和播放帧，只替换后续节目；结果完成后仍只收纳一次。真实 QQ 工作、面板状态、本机聊天继续优先，`hold=agent/state/chat` 不接虚构完成或失败结果。拖拽、戳和真实事件打断仍丢弃未播日常结果。

## 实机录制

`tools/record_pet.py` 在实际 Cocoa 窗口、真实单调时钟上驱动 `PetWidget.step`，约 12 FPS 捕获自己的角色、气泡与特效窗口。没有录其他应用，没有访问线上服务。分支被固定以便逐种检查；实际选择器、队列、姿态桥接、原图 FPS 和 2.5 秒尾声不变。换节目用真实 Qt 工具栏信号，后续目的地固定为待机，便于识别重播和道具重新出现。

十二段各约 18 秒、217 帧，主循环在这些定向样本中设为 8 秒：六种结果、`life_laptop_think` 的兼容衔接、三个直接收纳，以及两段连续换节目。它们都展示了尾声的四个源帧，只收纳一次，没有播放时钟回退，最后回到地面待机；原生脚锚点取整误差最大 0.2 个逻辑像素。坐到站最初几帧沿用既有的短暂淡入，多轮廓不是原画额外肢体。

另录一段 **55 秒、661 帧** 的完整拇指琴演出，主段保留 **45.3 秒**，在旧循环源帧 2 接困倦结果和收纳。约第 46.76 秒进入结果、第 49.25 秒收琴、第 52.18 秒回到待机；四格完整、没有时钟重置，头身和琴的衔接可读。[长驻完整实录](/private/tmp/regular-activity-endings-20261108/native-long/ending_kalimba_drowsy/continuous.webp)、[接片关键帧](/private/tmp/regular-activity-endings-20261108/native-long/ending_kalimba_drowsy/tail-beats.jpg)、[数据汇总](/private/tmp/regular-activity-endings-20261108/native-long/summary.json)。十三段总计 3265 个捕获帧。

| 检查 | 连续实录 |
| --- | --- |
| 电脑理顺 / 暂停 | [理顺](/private/tmp/regular-activity-endings-20261108/native/ending_work_clear/continuous.webp)、[暂停](/private/tmp/regular-activity-endings-20261108/native/ending_work_pause/continuous.webp) |
| 尤克里里满意 / 走音 | [满意](/private/tmp/regular-activity-endings-20261108/native/ending_ukulele_resolve/continuous.webp)、[走音](/private/tmp/regular-activity-endings-20261108/native/ending_ukulele_sour/continuous.webp) |
| 拇指琴余音 / 困倦 | [余音](/private/tmp/regular-activity-endings-20261108/native/ending_kalimba_linger/continuous.webp)、[困倦](/private/tmp/regular-activity-endings-20261108/native/ending_kalimba_drowsy/continuous.webp) |
| 另一段电脑主活动 | [思考后理顺](/private/tmp/regular-activity-endings-20261108/native/work-think-clear/continuous.webp) |
| 直接安静收纳 | [电脑](/private/tmp/regular-activity-endings-20261108/native/work-quiet/continuous.webp)、[尤克里里](/private/tmp/regular-activity-endings-20261108/native/ukulele-quiet/continuous.webp)、[拇指琴](/private/tmp/regular-activity-endings-20261108/native/kalimba-quiet/continuous.webp) |
| 主活动中换节目，再两次点击收纳中途 | [电脑连续换节目](/private/tmp/regular-activity-endings-20261108/native/work-next/continuous.webp) |
| 结果中换节目，再两次点击收纳中途 | [尤克里里连续换节目](/private/tmp/regular-activity-endings-20261108/native/ukulele-next-result/continuous.webp) |

每段目录还保存 `frames.json`、`keyframes.jpg` 和放大检查的 `tail-beats.jpg`。[十二段汇总](/private/tmp/regular-activity-endings-20261108/native/summary.json)记录动作区间、四格覆盖、换节目事件和锚点误差。实录可以判断这次衔接是否清楚，不能证明连续几天观看后的耐看程度。

例如在本机重录长驻样本：

```bash
.venv/bin/python tools/record_pet.py --source "$PWD" \
  --out /private/tmp/regular-activity-endings-repeat \
  --scenes ending_kalimba_drowsy --activity-seconds 45.3 --seconds 55
```

## 定向验证

以下六个直接相关测试文件共 **259 passed、1112 subtests passed**。两条既有 Qt 退出 `disconnect` 警告仍在；本轮没有扩大到全量测试或付费群聊评测。

```bash
.venv/bin/python -m pytest -q \
  tests/test_pet_activity_endings.py tests/test_pet_ending_flow.py \
  tests/test_pet_routines.py tests/test_pet_repertoire.py \
  tests/test_pet_brain.py tests/test_desktop_pet.py
```

覆盖精确物件与姿势、两种电脑入口、缺素材回退、状态门槛、概率后去重、主段时长、主段/结果/收纳中的连续换节目，以及真实状态优先打断。
