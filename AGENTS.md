# The Regular · 老群友 — Agent Instructions

本仓库是 **The Regular · 老群友**，一个常驻 QQ 群的陪伴型机器人，不是通用 Agent 平台，也不是 Agent Harness。

## 执行效率与问题处理（优先遵守）

- **遵循最新指令**：以用户最新的目标、授权和约束为准，不用自行推测的审计或复审流程给执行加门槛。
- **按影响验证**：改完先跑直接相关的测试和必要的 smoke；只有用户要求、明确的跨模块影响或定向验证暴露的新问题才扩大范围。全量测试先说明理由，不无故重复已通过的验证。
- **不做自证审计**：默认只有当前 agent 修改本任务代码。不要为排查假想的他人改动去算校验值、反复比对文件或做归因审计。
- **直接解决问题**：负责定位、修复、重跑相关验证并继续；不追责，不证明“不是我改坏的”。避免无必要的防御性编程和无关检查。
- **授权后持续推进**：用户说“继续”或已授权修复后，不再反复确认；按约定持续执行，进度汇报不等于任务结束。
- **仅因实际阻塞暂停**：只有无法自行解决的具体阻塞，或下一步超出已有授权时才暂停，并说明所需输入；继续完成不受影响的部分。

## 仓库与 Git

- 默认在本地 `main` 开发，不创建、切换、删除或合并其他分支，除非用户明确要求。
- 不擅自 `reset`、`rebase`、强制推送或覆盖已有提交；不把未跟踪的用户文件当成垃圾清理。
- 只提交当前任务直接修改的文件；用户没有明确要求提交时，不要自己 commit。
- 按功能点拆分 commit；不要把多个无关功能、格式化或临时改动混在一起。

## 产品边界（硬约束）

- **不做通用 Agent Harness**：不操作电脑/手机，不执行 shell，不做自主多步工具循环、后台规划、插件市场、多平台适配或向量数据库。
- **工具单步、显式、可审计**：一次请求最多做一次本地查询/一次显式搜索；结果只能作为证据进入本轮动态层，不能变成自主循环。新增工具必须走稳定接口和配置，不要把一次性分支写死进核心流程。
- **不要用正则/枚举硬凑自然语言**：能用命令语法、固定前缀、配置或组合接口解决，就不要堆一串同义触发词。搜索只认一个明确命令（`搜索 关键词`），旧账召回靠明确线索 + 内容词，不靠无限扩充短语表。
- **代码持有状态，模型只负责说人话**：好感、称呼、承诺、摘要、心情、激活门槛都由本地代码维护；模型不能写账本，也不能覆盖主称呼。
- **四层提示词顺序**：稳定头 → 每日记忆 → 会话历史 → 本轮动态。任何随发言人或轮次变化的内容只能进最末动态层；改这里必须跑 `tests/test_harness.py`。
- **成本边界**：SDK 自动重试关闭；显式候选、尝试上限、单次/整轮超时和日预算。思考模型的 reasoning token 也占 `*_MAX_TOKENS`，截断时调上限而不是盲目重试。
- **隐私与安全**：openid、群号、机器编号不进入模型上下文或群回复；记忆/归档/知识库出口都过敏感词检查；知识库只允许维护者改文件或本机面板，不开放群聊写入。
- **搜索边界**：只读 DuckDuckGo 的标题/摘要/链接，不打开结果页、不跑 JS、不自动连续搜索；结果必须以“不可信外部资料”包装，默认直连，不继承网关代理。
- **线上进程**：不要在未获用户明确要求时重启/停止正在运行的 bot；验证重启使用 launchd，并检查 `bot.log` 启动行。

## 工程原则

1. **优先复用**：先找现有模块、函数、协议和测试，不重复造轮子。
2. **保持简洁**：选直接、清晰、可验证的实现；无必要不引入正则、枚举、额外抽象层或新依赖。
3. **面向扩展**：通过稳定接口、配置或组合扩展，避免一次性的核心分支。
4. **重视维护**：减少隐式行为和重复逻辑；改动有清晰边界，并补直接相关的测试或验证。
5. **中文回复**：技术名称、代码标识和命令保留原文。

## 常用命令

```bash
# 离线全量测试（不需要网络和 Key）
.venv/bin/python -m pytest -q

# 部署体检 / 状态（只读）
regular --directory . doctor
regular --directory . status

# 本机面板（只绑定 127.0.0.1，启动时打印带 token 的地址）
regular --directory . panel

# 前台运行
regular --directory . run

# 仅在用户明确要求时重启线上实例
launchctl kickstart -k gui/$(id -u)/com.qqbot.xiaowang
```

## 代码地图

| 文件 | 职责 |
| --- | --- |
| `bot.py` | QQ 事件路由、本地 0-token 指令、四层提示词组装、发送 |
| `config.py` | 配置默认值与路径；`.env` 覆盖 |
| `prompts.py` | 人设、共享硬规则、动态注入块 |
| `storage.py` / `runtime.py` | 状态持久化、会话锁、单例组装 |
| `relations.py` / `affinity.py` | 关系档案、分级、衰减、称呼台账 |
| `digest.py` | 滚动摘要、承诺账本、表达学习 |
| `archive.py` | 原始消息 JSONL 与每日 Markdown |
| `recall.py` | 按需旧账召回 |
| `knowledge.py` | 维护者可编辑知识库 |
| `search.py` | 按需 DuckDuckGo 搜索 |
| `panel.py` | 本机维护面板（`aiohttp`，127.0.0.1） |
| `providers.py` | 显式候选、按任务路由、预算/超时边界 |
| `turns.py` | 每轮生成与分诊输入的原样 JSONL 记录（`data/turns/`） |
| `regular/checkup.py` | 每日只读体检信号、上下文提取与人工准确率统计 |
| `regular/cases.py` / `regular/eval.py` | 事故用例库、双版本子进程回放、盲评、整天回放、窄口子措辞搜索 |
| `regular/rules.py` | `prompts.py` 规则账本解析与单条消融（`--ablate`） |
| `tests/` | 离线回归测试 |

修改任何模块前先读它和它的直接调用方；不确定的边界先查看 `docs/DESIGN_NOTES.md` 和对应测试。

## 自我迭代标准流程（见 `docs/self-iteration.md`）

代理（Claude Code / CodeX）排查线上问题与迭代提示词/代码时，统一通过以下只读和评测命令操作，**严禁自动上线或重启线上机器人**：

**在代码仓库里执行，数据目录指向部署目录。** 下面的 `$DEPLOY` 是部署目录（线上 `.env` 和 `data/` 所在，
Pro 上是 `~/Code_repository/regular-bot`）。仓库自己的 `data/` 和 `.env` 都不是线上那份，
写成 `--directory .` 体检什么都看不见，评测用的也不是线上配置。`--base` / `--cand` 指的才是代码：
`.` 是当前工作区，`HEAD` 之类的 git 版本会临时检出。命令统一用 `.venv/bin/python -m regular`
（`.venv/bin/regular` 可能是旧安装，没有这些子命令）。

```bash
DEPLOY=~/Code_repository/regular-bot
```

1. **读每日体检，挑最严重的候选**：
   ```bash
   .venv/bin/python -m regular --directory "$DEPLOY" checkup --day today
   .venv/bin/python -m regular --directory "$DEPLOY" checkup --label <finding_id> yes|no
   ```
2. **定位原因（先分清是代码问题还是提示词问题）**：
   对照部署目录下的 `data/turns/YYYY-MM-DD.jsonl`（按 `turn_id` 查当轮原样入参）、`data/interactions/YYYY-MM-DD.jsonl` 和 `data/archive/YYYY-MM-DD.jsonl` 定位根因。代码能解决的（如 `@` 识别、引用拼接、冷却边界）不往提示词里堆刹车。
3. **提两种范围的改法（Harness² 分范围对比）**：
   - 通用改法（不绑特定话题/句式）；
   - 针对本群与人设的改法；
   若改 `prompts.py`，在对应字符串前挂 `# rule: <id> | <date> | <doc> | cases: <pattern>`（可用 `.venv/bin/python -m regular rules` 查看账本与稳定头字数；日期写这段措辞第一次进仓库的那天，`git log -S` 可查）。
4. **存成回归用例，跑全量评测与整天回放**：
   ```bash
   # 从体检输出的 turn_id 存成用例（或初始化种子用例）；分诊用例要用 --choice / --choice-not 写明该判成什么
   .venv/bin/python -m regular --directory "$DEPLOY" case add --turn <turn_id> --id <case_id> --incident docs/xxx.md --must-not "刚醒|洗衣"
   .venv/bin/python -m regular --directory "$DEPLOY" case seed

   # 对比旧版本与当前改法（全量用例 + 整天回放 + 规则消融）
   .venv/bin/python -m regular --directory "$DEPLOY" eval --base HEAD --cand . --cases all -n 4 --allow-paid
   .venv/bin/python -m regular --directory "$DEPLOY" eval --base HEAD --cand . --day today --allow-paid
   .venv/bin/python -m regular --directory "$DEPLOY" eval --ablate <rule_id> --allow-paid
   ```
5. **写报告与事故记录，交由人决定上不上线**：
   检查 `$DEPLOY/data/evals/` 下的报告（退步用例排最前，上线前任何退步都必须交代原因；「噪声内下降」加大 `-n` 只跑那几条再看；规则消融的结论分「可删除 / 仍需保留 / 说不准 / 判断不了」四种；稳定头字数不许无故只增不减），补充 `docs/` 事故记录与离线测试。上线永远由维护者手动执行。
