<div align="center">

<img src="docs/readme/cover.svg" alt="The Regular · 老群友：一个陪你聊天、记得群里的事的 QQ 群友" width="100%" />

# The Regular · 老群友

**一个住在 QQ 群里的 AI 群友：认识大家、记得群里的事，也知道什么时候该接话，什么时候该安静。**

[![Python](https://img.shields.io/badge/Python-3.10%2B-5375B5)](requirements.txt) [![QQ](https://img.shields.io/badge/QQ-Group%20%2B%20C2C-B48543)](bot.py) [![Tests](https://img.shields.io/badge/offline%20tests-1446-3A9F82)](tests) [![License](https://img.shields.io/badge/License-MIT-3A9F82)](LICENSE)

**中文** · [English](README_EN.md) · [信息流](#一句话进来之后) · [Decisions 输入](#decisions-究竟看到了什么) · [自己迭代](#自己迭代把这一圈做成命令) · [快速开始](#开始使用) · [设计文档](#设计文档)

</div>

老群友听群友聊天，被叫到时接话，没被叫到时判断这句是不是冲它来的；能接玩笑和纠正，也会在被嫌烦时退一退。看图、听语音、查资料、画图、发语音、到点提醒，都要落到代码确认的实际结果。

同一套系统有两条相连的链路。**线上负责把眼前这句话接好，离线改进负责让出过的问题变成下一次改动的证据。** 记录把两者连接起来，改法由编码代理提出，是否上线由维护者决定。

| 链路 | 怎样走 |
| --- | --- |
| 线上群友 | 消息 → 本地整理 → 按需判断与回复 → 执行、送达、记下实际结果 |
| 离线改进 | 现场记录 → 体检与用例 → 改代码或提示词 → 新旧回放 → 人决定上线 |

[群聊互动示意](docs/readme/conversation-cn.svg) · [已有测量与限制](#已有的测量与限制) · [实现与回放设计](docs/self-iteration.md)

<a id="设计亮点"></a>
<a id="架构总览"></a>
<a id="谁说话谁决定"></a>

## 一句话进来之后

代码先去重、认出 @ 和引用，准备图片或语音里的信息。本地规则可以直接结束一轮，不必调模型；需要理解意图时才问 Decisions。回复模型结合上下文决定怎么说，代码负责检查和执行，最后记下真正送达的内容。

![同一信息流中的线上接话、慢任务返回和离线改进](docs/readme/flow-cn.svg)

这里有三个不同的决定：**本地代码选择路径，Decisions 给出有边界的语义判断，回复模型决定说什么、是否回应，以及要不要提出动作。** 平台 @ 与只喊名字走不同路径；没点名的消息由回复模型自己选择接话或 `〔不接〕`。`〔画〕`、`〔语音〕`、`〔提醒 …〕` 是动作意图，服务、格式、词表、额度和执行结果都由代码核对。模型下一轮看到的是实际结果。

这些决定和当轮输入一起留下来，供[离线改进](#自己迭代把这一圈做成命令)重放。要看模块细节，可展开[详细架构图](docs/readme/architecture-cn.svg)，或读[画图与语音](docs/media-replies.md)、[引用与 @](docs/quotes.md)。

<a id="什么时候开口"></a>
<a id="闭集判断只信排序不信刻度"></a>

## Decisions 究竟看到了什么

Decisions 收到的是**现场 `input` + 一组 `questions`**。`input` 装当前消息、前后文和机器人上一句；每道题有 `instructions`、固定选项及各自的 `description`。它不使用回复模型的「稳定头 → 记忆 → 历史 → 本轮动态」四层格式。

看一个合成场景：群友先问「这个东西是什么？」，机器人阿团答「我不确定这是什么。」；群友没有平台 @，随后说「阿团，我觉得像个茶杯。」。这句点了名字、也在补充上一轮，`addressed` 的期望是 `answer`：接住补充，不等于认定它一定是茶杯。

![Decisions 输入封装：现场、判断题与闭集选项，返回后交代码处理](docs/readme/decision-cn.svg)

图中的 `input` 展示的是解码后内容，实际 HTTP 请求会把它编码成 JSON 字符串。`questions` 则另带这样的判断题（下面只展示一道题，题面按原意缩写；内容是合成示例）：

```json
{
  "type": "choice",
  "name": "addressed",
  "instructions": "结合前后文，判断是否轮到机器人接话。（示意缩写）",
  "choices": [
    {"value": "answer", "description": "在向机器人提问、补充或纠正，期待回应"},
    {"value": "quip", "description": "逗它或提起它，短接一句也可以"},
    {"value": "let_it_pass", "description": "群友互聊，或回应会打扰、重复"}
  ]
}
```

题面、选项说明和说明里的语义例子在 [`regular/decision_prompts.py`](regular/decision_prompts.py)，属于可迭代对象。**改题面用分诊回放；改它能看到的字段、裁剪或前后文构造，要用消息回放。** 题目按场景组合进一次调用，排序和有把握的结果才用于动作，概率刻度另做[校准验证](docs/decision-calibration.md)。完整封装与覆盖范围见 [Decisions 输入说明](docs/self-iteration.md#decisions-input)。

<a id="慢的事先回一句做完再跟进"></a>

## 慢任务怎样回到群里

读图、联网查询和深度研究可能来不及当场完成。只有走慢活路径的任务才进入下面的等待与跟进流程，代码管理任务把手和后续送达。

| 时机 | 群里发生什么 | 代码留下什么 |
| --- | --- | --- |
| 当场能完成 | 直接结合结果回复 | 结果与实际送达 |
| 超过线上等待时间（当前 6 秒） | 先回一句本地进度说明 | 在途任务或可恢复的把手 |
| 后台完成 | 跟进原任务；被动窗口过了尝试主动发言，再不行捎在后续消息上 | 完成状态、关联关系与投递结果 |

到点提醒是同一张任务表上的另一个用途：模型写固定时间标记，代码解析、校验并登记；一人一条，新的顶掉旧的。任务状态和额度随本地状态保存，重启后恢复。协议、读图时序和提醒格式分别见[慢活](docs/slow-work.md)、[先读图再回话](docs/image-before-reply.md)、[到点提醒](docs/reminders.md)。

<a id="怎么迭代的事故--回放--改--回归"></a>

## 自己迭代：把这一圈做成命令

一次迭代会产出**候选改动、新的回归用例和新旧对比报告**。线上留下输入、判断与实际送达；`checkup` 只读这些记录、列出可疑片段，独立评审模型另行标注。外接编码代理据此定位原因并修改，评测命令负责重放与比较，维护者决定是否发布。

![自迭代闭环：独立完整标注、训练与验证、新旧对照、误报漏报，以及代码部署与策略人工生效](docs/readme/iteration-cn.svg)

沿用前面的茶杯合成场景，**假设**「阿团，我觉得像个茶杯」被判成 `let_it_pass`，最后没接住。可以沿着这条错误看完整个流程：

| 这一轮 | 具体留下什么、比较什么 |
| --- | --- |
| 固定问题 | 保存现场，写明期望 `addressed=answer`；原来的错误选择只作参照 |
| 找到改动位置 | 若题面误解了补充句，改题面并跑分诊回放；若上一句没进入输入，修输入构造并跑消息回放 |
| 同时看修好和改坏 | 新旧版本跑同一份现场，也跑「群友只是提到阿团」等旧用例，防止变成逢名必答 |
| 根据报告再走一圈 | 原例改善但旧例退步，就回到修改；噪声内下降就补抽样；把结果和未覆盖项交给维护者决定 |

每次修复留下的用例会加入后续回归，事故记录和规则出处也供下次修改查阅。**积累的是代码、提示词、用例和改动依据**；发布后的新反馈再进入同一条链。当前维护流程由人触发编码代理：代码改动要跑相关离线测试，回放报告中的退步要交代原因，覆盖缺口要列明，再由维护者决定是否手动上线。完整走读见[一次迭代怎样走完](docs/self-iteration.md#iteration-example)。

可改的不只有聊天措辞。选择什么回放，要看改动发生在哪个边界：

| 可迭代对象 | 主要入口 | 怎样验证 |
| --- | --- | --- |
| 回复人设、语气、输出规则与动作说明 | [`prompts.py`](prompts.py) | 生成层回放；有账本的规则可单条消融 |
| Decisions 的题面、选项描述与语义例子 | [`regular/decision_prompts.py`](regular/decision_prompts.py) | 分诊层按待测版本重新取题，检查期望选项 |
| 分诊阈值与选项处理 | [`regular/decisions.py`](regular/decisions.py) | 分诊层检查有效选择；收敛等后续状态用消息层 |
| 输入字段、上下文裁剪、@ / 引用与路由 | [`regular/decisions.py`](regular/decisions.py)、[`bot.py`](bot.py)、[`storage.py`](storage.py)、[`quotes.py`](quotes.py) | 消息层回放与直接相关的离线测试 |
| 本地体检的信号、阈值与时间窗 | [`regular/checkup.py`](regular/checkup.py)、[`regular/tune.py`](regular/tune.py) | 冻结日志和独立正负标注，对比误报、漏报；训练与验证分开 |
| 旧账检索的匹配、排序与时效 | [`recall.py`](recall.py)、[`regular/tune.py`](regular/tune.py) | 检索层重跑冻结语料，对照完整相关消息集合；承诺另用专门判据 |
| 记忆与其他资料的选择、注入 | [`digest.py`](digest.py)、[`knowledge.py`](knowledge.py) | 消息层与定向测试；摘要生成和真实工具执行另做验证 |
| 模型、推理参数和任务配置 | [`config.py`](config.py)、[`providers.py`](providers.py) | 控制实验配置做对照，回放与任务探针各有覆盖范围 |

四种回放分别是：**生成层**重跑记下来的完整入参，**分诊层**重跑当时的输入与待测题目，**检索层**在冻结归档与承诺快照上重选证据，**消息层**把归档消息交给真实入口、使用假发送。整天回放把当天记录过的回复逐条重新生成，用来找口头禅等整体模式；它不会模拟群友听到新回复后会怎样反应。

**体检器和旧账检索器本身也接受考核。** 新旧策略在同一份独立完整标注上比较：误报是多报了问题或选入无关证据，漏报是没找到已标明的问题或相关证据。报告给出 TP / FP / FN / TN、查准率与召回率，并列出具体错误。只给已检出候选标 `yes/no`、只指定某条证据必须取到，都不足以计算总体漏报率；未标注、快照不全和分母为空会单列，避免把“没有证据”写成“零漏报”。

`tune checkup` 和 `tune recall` 在本地搜索有界的策略参数，0 token；评测与搜索使用的标注保持独立，验证集用于核对改动效果。它们只出报告与提案，维护者执行 `policy apply` 后策略才生效，旧策略保留供回退。范围和数据格式见[本地策略评测](docs/self-iteration.md#local-strategy-evaluation)。

消息用例若预置了 `triage_choices`，会固定那些选择；验证分诊整条链要使用没有预置答案的用例。消息回放也替换了部分资料渲染和媒体执行，不能代替真实服务的 smoke。

内置的自动措辞搜索目前只有 `remind` 和 `banter` 两个目标；后者改的是聊天生成的接话说明，Decisions 题面需要编码代理修改后再评测。安全、隐私、状态归属和调用预算仍是代码守住的硬约束。详细边界见[可迭代对象](docs/self-iteration.md#iteration-surfaces)。

[Harness² 的八类组件](docs/self-iteration.md#harness2-components)与这里的四层提示词、四种回放是不同维度。按机器人运行时严格对应，系统提示、约束、记忆、工具说明四类有直接对应；脚本和插件的部分职责由固定本地代码承担，不代表开放了模型执行脚本或插件循环；机器人内部没有再放一套子代理与技能系统。

<details>
<summary>维护者：回放命令、统计判定与已有验证</summary>

在代码仓库中执行，`DEPLOY` 指向线上 `.env` 和 `data/` 所在；`--base` / `--cand` 选择代码版本。

```bash
DEPLOY=/path/to/regular-bot
.venv/bin/python -m regular --directory "$DEPLOY" checkup --day today
.venv/bin/python -m regular --directory "$DEPLOY" case add --turn <turn_id> --id <case_id> --must-not "刚醒|洗衣"
.venv/bin/python -m regular --directory "$DEPLOY" eval --base HEAD --cand . --cases all -n 4 --allow-paid
.venv/bin/python -m regular --directory "$DEPLOY" eval --base HEAD --cand . --day today --allow-paid

# 本地策略评测和调参：不请求模型
.venv/bin/python -m regular --directory "$DEPLOY" tune checkup --benchmark docs/examples/checkup-benchmark.example.json
.venv/bin/python -m regular --directory "$DEPLOY" eval --base HEAD --cand . --cases retrieve -n 1
.venv/bin/python -m regular --directory "$DEPLOY" tune recall
.venv/bin/python -m regular --directory "$DEPLOY" policy show
```

没有 `turn_id` 时可用 `case add --message <message_id> --id <case_id>`；分诊用例要用 `--choice addressed=answer` 这类判据写明应该怎样。`checkup` 只读本地日志、不调模型；标注另由独立评审模型完成。生成、分诊、盲评等会调用模型的 `eval` 需要 `--allow-paid`；仅含本地检索且没有模型评审的评测无需授权付费。上面的体检示例是合成数据，用来体验命令，不代表线上效果。

生成与盲评的下降要过单侧检验才算退步，够不上的单列「噪声内下降」，可加大 `-n` 再看；越线一次就算退步。检索是确定性计算，判据通过率下降或完整标注下 FP / FN 增加直接记退步，不用重复抽样消噪。规则消融给出「可删除 / 仍需保留 / 说不准 / 判断不了」四种结论，缺用例或没跑完都不能建议删除。人设和规则的修改不会自动上线。

已有小规模验证：同一版本对同一版本（4 条用例各抽 4 次），0 条退步、2 条噪声内下降；第一次体检真实一天，顺着「被纠正」找到当天 4 条记账标签泄漏并修复。完整历史用例集与更长时间的线上观察仍待验收。设计借鉴 [Dream-RSI](https://arxiv.org/abs/2609.14858) 的历史回放和 [Harness²](https://arxiv.org/abs/2610.14224) 的新旧对比，完整流程见[自迭代设计](docs/self-iteration.md)。

</details>

<a id="四层提示词按稳定度排吃满前缀缓存"></a>
<a id="状态与记忆代码持有模型只负责说人话"></a>
<a id="成本隐私与可靠性"></a>

## 状态、成本与隐私的边界

| 边界 | 代码怎样守住 |
| --- | --- |
| 状态有来源 | 称呼、关系、承诺、心情、额度与任务由本地代码维护；个人原话逐字绑回归档，最多按需带入两条 |
| 上下文有顺序 | 稳定头 → 每日记忆 → 会话历史 → 本轮动态；随人和轮次变化的内容只放最后 |
| 调用有上限 | SDK 自动重试关闭；显式候选、尝试上限、单次与整轮超时，以及全局和各能力日预算 |
| 资料有边界 | 工具单步、显式，搜索只读结果标题 / 摘要 / 链接；外部资料按不可信证据包装 |
| 隐私与出口 | openid、群号不进模型上下文和群回复；记忆、归档、知识库出口过敏感词；维护面板只绑定 127.0.0.1 |

判断和回复所需的群聊内容会发给相应模型服务，部署前应结合群成员预期选择服务。机器人定位是常驻群友，具备固定接口的能力；改代码和工具链由外接编码代理在维护流程里完成。具体取舍见[交情与个人证据](docs/personal-memory.md)、[判断层隐私说明](docs/judgment-layer.md)和[供应商与成本](docs/providers.md)。

## 已有的测量与限制

| 已有证据 | 测量范围 |
| --- | --- |
| 1446 项离线回归测试 | 验证代码行为；不等于每句话都能接对 |
| 旧账检索 8 / 8 用例判据通过，相关证据取回 7 / 9 | [合成完整标注集](docs/self-iteration.md#local-strategy-evaluation)；新旧结果一致，预算约束造成的 2 条漏检如实计入 |
| 接住 17 / 20 条没带名字但冲它说的话，旧方案 3 / 20 | [141 条消息、9 种写法回放](docs/when-to-reply.md)；「该接 / 不该接」由独立评审模型标注 |
| 回复输入 token 的 75.6% 命中前缀缓存 | [线上一天 261 次调用](docs/providers.md)，与该模型和当时流量有关 |
| 闭集分诊中位延迟 0.71 秒 | [908 次判断](docs/judgment-layer.md)，不作为服务时延保证 |
| 到点提醒时间与对象 60 / 60 | [10 种说法各 6 次回放](docs/reminders.md)，覆盖的是该批样本 |

更早的迭代还包括[读图故障的三种原因](docs/image-before-reply.md)、[三家模型分工对比](docs/model-roles.md)和[1082 条请求的模型事后标注](docs/request-sincerity.md)。这些数据记录的是具体条件下的改进与代价，持续效果仍需后续未见样本和线上观察。

## 开始使用

需要 Python 3.10 或以上、QQ 机器人账号，以及可用的模型服务。可以先在终端体验，不需要 QQ 账号或 API Key：

```bash
git clone https://github.com/dubianche123/the-regular-qq-bot.git
cd the-regular-qq-bot
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
regular demo
```

要接入自己的 QQ 机器人，准备 `AppID`、`AppSecret` 和模型服务凭据，运行配置向导并启动：

```bash
regular --directory regular-bot setup
regular --directory regular-bot run
```

在 QQ 开放平台开启私聊和群消息事件。上线后第一个私聊机器人并发消息的人会被认作主人，请由你先完成这一步；把它拉进群，并在群设置中允许机器人获取群消息，就可以 @ 它或用配置的名字聊天。

| 命令 | 做什么 |
| --- | --- |
| `regular demo` | 离线体验，走的是生产的消息处理链路 |
| `regular setup` / `init` | 配置向导 / 只生成私有 `.env` |
| `regular doctor` / `status` | 本地体检 / 读运行时状态快照 |
| `regular panel` | 本机维护面板（只绑 127.0.0.1） |
| `regular service` | 生成 launchd / systemd 定义（不自动安装） |
| `regular coverage` / `calibration` | 只读统计：个人原话覆盖率、判断概率对照实际结果 |

判断接口、联网、识图、语音和画图需要对应服务和配置。配置方法见[供应商说明](docs/providers.md)、[示例配置](.env.example)和[画图说明](docs/image-generation.md)。

## 设计文档

| 主题 | 文档 |
| --- | --- |
| 判断交给谁 | [判断层](docs/judgment-layer.md) · [概率校准实测](docs/decision-calibration.md) · [叫到我不等于要我答](docs/addressing.md) · [引用和 @](docs/quotes.md) |
| 什么时候开口 | [没叫它的时候](docs/when-to-reply.md) · [何时介入（旧方案存档）](docs/when-to-intervene.md) · [真托付还是整活](docs/request-sincerity.md) · [岔开话题别换台](docs/deflection.md) |
| 能力与动作 | [画图与语音](docs/media-replies.md) · [图像生成配置](docs/image-generation.md) · [先读图再回话](docs/image-before-reply.md) · [画图前看一眼](docs/drawing-guard.md) · [到点提醒](docs/reminders.md) |
| 异步 | [慢活：先回一句，做完再跟进](docs/slow-work.md) |
| 记忆与观察 | [交情与个人证据](docs/personal-memory.md) · [互动观察](docs/interaction-observations.md) · [自己迭代](docs/self-iteration.md) |
| 模型与供应商 | [三家模型各司其职](docs/model-roles.md) · [供应商配置](docs/providers.md) |
| 总览 | [设计笔记](docs/DESIGN_NOTES.md) |

[MIT License](LICENSE)
