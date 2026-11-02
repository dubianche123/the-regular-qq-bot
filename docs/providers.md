# 供应商配置与路由 / Provider configuration

共享版直接使用现有 OpenAI Python SDK。`openai` 表示 OpenAI-compatible 协议入口；它不限定服务商，URL、模型和凭据由部署者明确提供。智谱与 Gemini 是可修改的便捷预设，预设中的模型名不代表任何账号具备额度或视觉能力。

## 配置一个通用端点

```dotenv
AI_PROVIDER=openai
OPENAI_BASE_URL=https://your-endpoint.example/v1
OPENAI_API_KEY=your-private-key
OPENAI_MODELS=chat-model
# 可选；省略时复用该端点的对话模型
OPENAI_MODELS_DIGEST=digest-model
OPENAI_MODELS_JUDGE=review-model
FALLBACK_PROVIDER=
AI_MAX_ATTEMPTS=3
AI_TIMEOUT_SECONDS=20
AI_TOTAL_TIMEOUT=30
```

`*_MODELS` 接受逗号分隔的有序列表，重复名称自动去重。只填自己确认可用的模型。`FALLBACK_PROVIDER` 可显式选择 `openai`、`zhipu` 或 `gemini`；留空关闭跨端点回退。主端点的候选用完或该端点需要跳过时，才进入备用端点，且共同受尝试上限约束。使用其他 endpoint 或 key pool 可运行自己已有的兼容网关，项目不另建网关框架。

智谱或 Gemini 可选 `AI_PROVIDER=zhipu` / `gemini`，并填写对应 `*_API_KEY`；`*_BASE_URL` 和 `*_MODELS*` 可覆盖预设。需要按端点管理多个 Key 时，可在部署目录使用 `api_keys_<provider>.txt`，每行一个 Key。默认 `AI_PROVIDER=openai`；`regular doctor` 展示当前有效端点和任务模型。

## 每次调用的边界

- 一次 `call_model` 最多 `AI_MAX_ATTEMPTS` 次物理模型请求；默认 3 次。候选顺序来自当前任务配置，摘要不会悄悄切到另一套对话模型。
- 每次请求最多 `AI_TIMEOUT_SECONDS`，整次调用最多 `AI_TOTAL_TIMEOUT`，默认分别 20 秒和 30 秒。
- SDK 设为 `max_retries=0`。聊天、摘要、审核和读图消费同一个日预算；原生判断另记一本（`DECISION_DAILY_BUDGET`），两本互不挤占。失败、超时和调用方显式重试也计数。原生判断的独立边界见下文。
- 不重复提交同一候选。失败只可能进入下一个明确配置的模型/端点。没有最终 `content` 时，不把 `reasoning_content` 作为答复。

分类复用 SDK 的异常类型和 HTTP 状态，不识别厂商错误文案：

| 结果 | 行为 |
| --- | --- |
| 401 / 403 | 本进程停用该端点，不盲试同端点其他 Key/模型；只允许显式备用。修好配置后重启。 |
| 429 | 按 `Retry-After` 暂停该端点；没有有效响应头时短暂停 1 秒。只走显式备用，不在请求里睡眠。 |
| 带 `Retry-After` 的 503 | 同样按响应头暂停端点。支持秒数和 HTTP 日期。 |
| 404、408、409、其余 5xx | 尝试下一个明确候选，仍受总尝试数和超时约束。 |
| SDK 连接错误、超时 | 本轮跳过该端点，允许显式备用；下一次调用可正常重试。 |
| 其他 4xx 或未知异常 | 停止本次调用，避免把错误输入继续提交到别处。 |

冷却中的端点不会被“最后一搏”强行调用。候选多于尝试上限时，后面的候选未必会被访问；需要给备用留机会时，应缩短前面的候选列表。`regular doctor` 展示有效链、尝试上限、超时和已配置能力，默认不发网络请求。

## 识图默认继承，也可独立配置

识图默认开启，未设置视觉覆盖时跟随 `AI_PROVIDER` 及其首个对话模型。因此主模型支持图片时无需再配一遍：

```dotenv
AI_PROVIDER=openai
OPENAI_BASE_URL=https://your-endpoint.example/v1
OPENAI_API_KEY=your-private-key
OPENAI_MODELS=your-multimodal-model
```

主模型是纯文本，或需要为读图选择更合适的模型时，独立覆盖即可：

```dotenv
AI_PROVIDER=openai
OPENAI_MODELS=your-text-model
# 同一端点使用另一款模型
VISION_MODEL=your-image-capable-model
# 也可选另一个已配置凭据的端点
# VISION_PROVIDER=gemini
# VISION_MODEL=your-gemini-image-model
```

`VISION_PROVIDER` 可选任一配置端点；跨端点时须同时填写 `VISION_MODEL`，不会猜测另一端点的默认视觉模型。凭据与 URL 复用该端点的配置，不另建一套客户端或路由。`VISION_ENABLED=false` 可关闭识图。`regular doctor` 展示启用状态、有效视觉端点与模型。继承配置不能保证模型支持图片，也不能从模型名或 `/models` 列表可靠推断；需要选用自己确认支持图片输入的模型。不支持或调用失败时会明确报告未读到图片，不编造描述。

默认 `VISION_INPUT_MODE=url` 将图片 URL 直接传给模型。若 QQ 图床只能由本机访问，可显式设 `VISION_INPUT_MODE=download`：先下载，再以 base64 发送一次模型请求。没有“报某段错误后换输入再提交”的自动重试。同图前后台共用在途任务与已保存描述，不重复占图片名额；前台新图在全局预算只剩一次时跳过读图，保留一次正文调用机会。

## 思考模型的输出上限

`AI_MAX_TOKENS`、`AI_MAX_TOKENS_BANTER`、`JUDGE_MAX_TOKENS`、`DIGEST_MAX_TOKENS`、`VISION_MAX_TOKENS` 都是**输出上限**，不是目标长度。思考模型的 reasoning token 同样计入上限：上限太小会先把额度花在思考上，留下空正文；称呼审核拿不到 `OK/NG` 时会 fail-open。

实测 `glm-5.3-flash` 一句普通回复约思考 90–140 token，默认值按此留余量：聊天 768、接话 320、称呼审核 400、摘要 1200、读图 300。摘要正文与 `<DIGEST>` JSON 共用 1200；读图描述最多保留 120 字符。显式设置 `AI_TEMPERATURE` 才覆盖供应商预设（智谱 0.7），不设置则沿用预设。

每个成功应答记录 `in/out/cached/thinking/finish`。出现 `finish=length` 或“正文为空”时，先提高对应任务的上限或压缩输入，不要靠重试解决。单模型全包时，一个支持图片的多模态模型可同时承担聊天、摘要、审核和读图；主模型只支持文本时再设 `VISION_MODEL`。

## 原生判断帮手

运行 `regular --directory . panel`，打开启动时打印的本机地址，在「判断 API」填写 Endpoint、模型和 API Key。它独立于聊天模型配置，不修改 `.env`；保存只写本机私有配置，不发网络请求、不重启 bot。密钥不会回显到页面或写入日志、仓库。

默认 Endpoint 为 `https://api.openai.com/v1/decisions`，模型为 `gpt-6-luna`。模型名属于 Luna 家族，但请求走专用的 `input + questions → answers` 协议，不是让普通聊天生成 JSON。判断请求不参加上面的聊天模型候选轮换，不回退到 `/responses` 或 `/chat/completions`。

判断怎么用、什么不交给它、失败往哪边退，总的设计见 [判断层](judgment-layer.md)。
落到产品上是这几处（没叫它的消息接不接，2026-11-02 起由小王自己定，见 [没叫它的时候](when-to-reply.md)）：

- **跟机器人有关的消息一次分诊**：本机先看已知事实（有没有叫到它、它刚说过话没有、
  要不要起名），挂上用得着的题，一次调用问完（群号、openid、消息 ID 在本机抹掉，
  命中敏感词表的整条不发）。跟它无关、也不在反馈窗口里的消息不分诊 —— 那句直接交给
  小王的回复调用，它自己定接不接。私聊不分诊。
- **叫到它 ≠ 欠它一句**：平台 @ 一定回；正文里出现名字时判断 answer / quip / let_it_pass，
  不可用就照答。生成不再用 `SKIP` 决定说不说。
- **自然反馈**：看最近确认送达的 bot 原话与前后语境，分清笑骂、纠正、嫌烦和骂的是别人。先检查对象，再执行动作。明确喊停优先；默认暂停三分钟的主动发言，包括没叫它时的接话、定时话题和催债，明确叫到仍可回答。纠正可以接一句，不需要重新 @；没点名的追问由小王自己认。另一位群友来纠正或追问时，接话模型也能看到此前实际发出的那句话，不靠猜测。这条即时反馈路径不扣好感，也不建立永久的“讨厌机器人”标签。
- **自发话题**只在原有作息、活跃度和间隔门槛全过后让小王写一次，它没什么想说的就写〔不接〕
  （2026-11-02 以前先问一次插嘴判断；那一题和本地节奏先验 `opening.py` 都随骰子一起删了）。催债仍按本地账本执行，不另加一轮判断；什么时候催由定时任务（`scheduler.py`）按账本定闹钟。
- **一次回忆机会**：没叫它的那一轮，本地话题闸先挑出本人一条已核实原话，再判断此刻
  端出来是「居然记得」还是「翻档案」。只有 `bring_it_up` 才注入，并换用开了例外的
  那段说明；判断不可用一律不端出（新增的惊喜不靠失败兜底），判断说「先别」不消耗额度。
  每人冷却 `DECISION_CALLBACK_COOLDOWN_SECONDS`、每群每天最多
  `DECISION_CALLBACK_MAX_PER_DAY` 次（它写〔不接〕就不算用掉）；对方正在喊停时一律不端。
  这一题单独问，因为它要把本人的旧原话送出去。
- **翻旧账与起名**：分诊判断「这句的回答要不要靠本群旧账」，说 look_up 才查归档；
  本地起名模式提出的候选，判断只能否决（诱饵 / 误触发），不能单独批准。
- **个人原话去留**：摘要先提出并核实最多六条新原话，再一次批量判断类型、保留或撤下。临时近况默认两天，明确偏好、事实和共同经历默认最多 90 天，均按原始发言时间算。更正只能撤下对应本人、对应来源的旧条目；未被本次摘要重复提起的有效长期条目仍可保留。每群最多 24 条，接话时仍只按本人、当前话题取最多两条。

自然反馈还有**跨天**的那一层（`receptive.py`，不调 API）：被嫌烦 / 被接着逗会
折进每人一个按半衰期衰减的接纳度标量，向本群群体层收缩；只有**跨成员置换检验**
（把正负标签在群里所有人的证据之间打乱）说这个人确实与众不同、而且是不爱被搭话时，
没叫它的那一轮给小王交代一句「TA 不太爱被你搭话」；否则什么都不说。它不进数字、不碰好感度，
也不建立永久标签。见 [没叫它的时候](when-to-reply.md)。

返回里的 `confidence` / `probabilities` 会落进本地观察日志的
`decision_scores` 行（见 [交互观察](interaction-observations.md)），
并且参与一件事：**弃答**。2026-10-31 用带客观标签的真实任务实测过这些概率值不值得信：
排序能用（AUC 0.64），**刻度不能用**（ECE 0.21、Platt 斜率 0.35 [0.13, 0.58]，
即 logit 需压缩约三到五倍），Brier 上还输给常数基线。所以不按绝对概率设阈值，改用无标度的
**边际**（被选中项比第二名高多少；二选一时恰好等于官方 `confidence`，多选时不等）：低于
`DECISION_MIN_MARGIN` 的那一题作废，调用方按「失败往哪边退」那张表处理
（欠人家的回答照答，多出来的东西不加，会留下来的写入照旧走底线）。
一份返回里某一题坏了只作废那一题。地板默认 0.25；它对选项多的题偏严（五选一的
`next_move` 约四成被丢），该按题用真实标签定，见 [判断层](judgment-layer.md)。
完整测量与给 Norn 研究线的建议见
[判断概率值得信到什么程度](decision-calibration.md)。

这借鉴了 [Jev 的记忆写入裁决](https://www.jev-llm.com/use-cases/agent-memory-control/) 和 [轮次决策](https://www.jev-llm.com/use-cases/voice-turn-taking/) 的固定选项思路，不引入其协议、工具循环或规划框架。[官方 Decisions 文档](https://developers.openai.com/api/docs/guides/decisions) 描述原生问题与答案格式。

| 配置 | 默认值 | 作用 |
| --- | --- | --- |
| `DECISION_ENABLED` | `true` | 关闭后不发原生判断请求 |
| `DECISION_TIMEOUT_SECONDS` | `6` | 一次请求总时限；只尝试一次，无自动重试 |
| `DECISION_DAILY_BUDGET` | `5000` | 判断请求每日次数上限，和聊天的 `DAILY_BUDGET` 分开记 |
| `DECISION_FEEDBACK_WINDOW_SECONDS` | `120` | 最近 bot 发言可用于反馈解释的时间窗，秒 |
| `DECISION_QUIET_SECONDS` | `180` | 嫌烦后的主动插话暂停，秒 |
| `PEOPLE_DURABLE_MAX_AGE_DAYS` | `90` | 经来源核对并分类的长期原话上限，天 |
| `PEOPLE_MEMORY_MAX_ITEMS` | `24` | 每群原话上限 |
| `DECISION_CALLBACK_ENABLED` | `true` | 关闭后没叫它的那几轮一律不端旧话 |
| `DECISION_CALLBACK_COOLDOWN_SECONDS` | `21600` | 同一个人两次回忆的最小间隔，秒 |
| `DECISION_CALLBACK_MAX_PER_DAY` | `2` | 每群每天的回忆次数上限 |
| `DECISION_MIN_MARGIN` | `0.25` | 弃答地板：被选中项比第二名高出不到这么多，这一题即作废 |

未配置、超时、拒答或无效返回时，使用原来的普通接话与门控；新原话保持短时效，不凭失败结果删除旧条目。`PERSONAL_MEMORY_ENABLED=false` 只关闭接话中的个人原话注入；`DECISION_ENABLED=false` 才关闭判断调用，关闭调用也不会抹掉已保存的有效记忆。

会话类判断请求（反馈、插话仲裁、回忆）带一个 `bot_is_addressed_as` 字段，
列出机器人在群里的叫法（`BOT_NAMES` + 平台昵称，见 `naming.bot_names()`）。
判断据此知道「用这些名字称呼的那句话是冲着它说的」。原话裁决不带这个字段 ——
它只看单条引语，不判在跟谁说话。

这是**加固**，不是修线上故障。2026-10-31 真实语料实测：触发词配好时
（含名字的消息必然 `is_at`，于是 `latest_is_directed=true`），声明与不声明都能
把「小王闭嘴」判成冲着机器人、都会装上暂停窗，声明只是把置信度从 0.72 推到 0.96。
真正会出事的是触发词**没**配的部署：那时同一句话被判成在说第三个人
（`another_person`），暂停窗装不上。声明叫法让这件事不再依赖 `BOT_NAMES`
恰好配对，也给插话仲裁和回忆判断补上它们唯一的「在跟谁说话」线索
（那两个 payload 没有 `directed` 字段）。完整 A/B 与边界见
[何时介入](when-to-intervene.md)。

声明叫法不放宽脱敏：这些名字本来就在群友的原话里，而群号、openid、消息 ID
照旧逐个抹掉。

本项目只接收预设选项，不让判断模型生成昵称、改账本、发消息或编造记忆。状态变更由代码验证并执行；原始标识留在本地，判断模型只见临时成员代号与限量语境。确认送达的 AI 群发也能成为自然反馈对象，不虚构一位“被回复的人”；静态播报、排队和未确认发送不算。机器人自己的话和判断日志也不会混成人类原话来源。

## 复用了什么

[OpenAI Python SDK 的异常和重试接口](https://github.com/openai/openai-python#handling-errors) 已提供 typed errors、状态码、超时及关闭自动重试的配置，本项目只保留与日预算和任务候选相关的薄边界。[LiteLLM 的重试和回退](https://docs.litellm.ai/docs/completion/reliable_completions) 适合需要更广供应商协议及集中路由的场景；当前统一兼容协议无需再引入第二套路由和额外依赖。此实现没有借用私有 SDK 重试函数。

## English configuration summary

Use `AI_PROVIDER=openai` with an explicit `OPENAI_BASE_URL`, `OPENAI_API_KEY`, and ordered `OPENAI_MODELS`. Task overrides are optional. Zhipu/Gemini are optional presets with their own endpoint, key and model variables. `FALLBACK_PROVIDER` is empty by default and only explicitly configured endpoints receive fallback prompts.

Image reading is enabled by default. Without overrides, it inherits `AI_PROVIDER` and its first chat model. Set `VISION_MODEL` to override the model; when choosing another endpoint with `VISION_PROVIDER`, also provide `VISION_MODEL`. Set an independent image-capable model when chat uses a text-only model, or disable image reading with `VISION_ENABLED=false`. Configuration inheritance does not guarantee image support; failures produce an explicit unavailable-image result, never a guessed caption.

Token caps are output ceilings, and reasoning tokens count against them on thinking models; undersized caps yield empty visible content. Keep headroom, watch `finish=length`, and raise the task cap instead of retrying. An explicit `AI_TEMPERATURE` overrides the provider preset.

The runtime uses one bounded policy: at most three physical attempts by default, a 20-second per-attempt timeout, and a 30-second total deadline. SDK retries are disabled; failed physical attempts consume the same persistent daily budget. Routing uses SDK exception types, HTTP status and `Retry-After`, with only explicitly configured candidates.
