# 鼠标靠近闪透明与魔法节奏 · 2026-11-08

用户在 `470de78` 部署后指出：鼠标靠近时人物会突然透明，魔法演出也太短。本轮处理这两项反馈。

## 输入切换不能隐藏角色

上轮动态调用 `QWidget.setWindowFlag(WindowTransparentForInput)` 会进入 `setWindowFlags → setParent`，隐藏整个窗口，再靠 `show()` 显示回来。鼠标跨过轮廓就可能触发隐藏与重显，是新引入的显示副作用。[Qt 文档](https://doc.qt.io/qt-6/qwidget.html#windowFlags-prop)明确说明修改这类窗口 flags 会隐藏窗口。

现在通过 `QWindow.setFlag` 原地同步 Qt 与原生输入位，再用 `overrideWindowFlags` 同步 QWidget 的状态，不调用 QWidget 的隐藏/重显路径。Cocoa 使用已有 PyObjC 桥保存并恢复原窗口层级与 Spaces 设置，确保三层输入状态一致。透明处继续穿透、实体继续接收，拖拽保持捕获。

曾验证仅设置 `NSWindow.ignoresMouseEvents` 的候选：显示稳定，但原生点击测试发现 QWindow 仍保留穿透位，人物实体收不到事件。该候选未采用；最终实现同步 QWindow，并分别复核显示和真实点击。

纯 brain 对照没有发现另一条“靠近就降低透明度”的路径：正常 hover 只改变视线，既有 fade 仍是旧图完全不透明、新图渐入。

固定同一 idle 素材与时钟，用真实鼠标交替跨越透明/实体 14 次，旧版与新版各捕获 98 帧 WindowServer 输出。旧版有 13 次 Hide、13 次 Show，窗口层级在 8/25 之间变化；alpha 总和最低降为 0，28 个样本没有 alpha 大于 220 的像素，既有整体变淡也有完全空帧。新版为 **0 Hide / 0 Show / 0 WinIdChange**，同一窗口、固定层级 25；98 帧 alpha 总和均为 9,143,814，不透明像素均为 35,442，未发生闪透明。

[对照画面](/private/tmp/regular-hover-opacity-20261108/hover-before-after.jpg)、[旧版实录](/private/tmp/regular-hover-opacity-20261108/before/hover-native.webp)、[新版实录](/private/tmp/regular-hover-opacity-20261108/after/hover-native.webp)与同目录 JSON 保留逐帧数据。此测试只观察自建验收窗口，结束恢复真实鼠标位置。

随后以 `CGEventPost` 做七种状态的空白/实体点击，共 **14/14 通过**：空白点击到自建底窗，实体点击到桌宠，三个输入状态一致。未点击其他应用；[点击证据](/private/tmp/regular-hover-opacity-20261108/hit/native-hit.json)。

## 修正显示验收的盲点

旧录制器用 `QWidget.grab()` 取画面。Qt 6.10 的该方法调用 render 并使用 `IgnoreMask`，可以掩盖窗口隐藏、空 backing store 或原生遮罩问题。旧录像能说明动作、素材与时序，不足以证明鼠标靠近时的窗口系统显示稳定。[Qt 6.10 源码](https://raw.githubusercontent.com/qt/qtbase/v6.10.0/src/widgets/kernel/qwidget.cpp)

`tools/record_pet.py` 已改为 `CGWindowListCreateImage(…IncludingWindow…)`，只读取验收进程自己窗口的 WindowServer 输出，不触发 QWidget 重绘，不录其他应用。缺少既有屏幕录制权限时明确报错。新窗口尚未向 WindowServer 交付画面时，保留空帧与缺失窗口记录，不用重绘补图；日常魔法会额外检查开始稳定后全程实体可见。

## 每一拍留出时间

五段日常魔法从统一 3.5 秒改为按动机安排停留，仍为 4 FPS、四个源帧、单次播放。收物件均为 1 秒。

| 魔法 | 起意 | 施法 | 结果 | 收尾 | 总时长 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 冰可乐 | 1.25s | 1.50s | 1.75s | 1s | 5.50s |
| 烤棉花糖 | 1.25s | 1.75s | 1.75s | 1s | 5.75s |
| 吹干围巾 | 1.50s | 1.75s | 1.75s | 1s | 6.00s |
| 梳尾巴 | 1.50s | 2.00s | 1.50s | 1s | 6.00s |
| 修书角 | 1.75s | 2.00s | 1.75s | 1s | 6.50s |

手动与自动故事使用同一序列。不提高自动频率，不改变其他魔法、投喂或飞行速度。

使用新的 WindowServer 捕获连续录制五段，各 10 秒、121 帧，共 605 帧。四拍完整、实体在稳定后全程可见、最后均回到地面 idle。可乐录制开始约 0.07 秒时新建窗口尚无画面，已如实保留空帧和缺失记录；其余采样无缺失，没有用主动重绘填补。采样间隔约 1/12 秒，逐拍观测时长相对定义存在该采样量级的误差。

[冰可乐](/private/tmp/regular-hover-opacity-20261108/magic/magic_daily_cola/continuous.webp)、[烤棉花糖](/private/tmp/regular-hover-opacity-20261108/magic/magic_daily_roast/continuous.webp)、[吹干围巾](/private/tmp/regular-hover-opacity-20261108/magic/magic_daily_scarf/continuous.webp)、[梳尾巴](/private/tmp/regular-hover-opacity-20261108/magic/magic_daily_brush/continuous.webp)、[修书角](/private/tmp/regular-hover-opacity-20261108/magic/magic_daily_mend/continuous.webp)及[实测时长汇总](/private/tmp/regular-hover-opacity-20261108/magic/summary.json)记录最终采用的节奏。

## 定向测试

`test_desktop_pet`、`test_pet_magic_purpose`、`test_pet_routines` 合计 **122 passed、145 subtests passed**，保留两条已知 Qt 退出 `disconnect` 警告。覆盖反复进出轮廓时无 Hide/Show/WinIdChange、同一窗口与绘制像素、Cocoa 输入位同步及原生属性恢复，以及手动/自动入口的四拍真实停留与返回待机。
