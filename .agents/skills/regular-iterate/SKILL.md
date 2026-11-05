---
name: regular-iterate
description: 小王（The Regular，QQ 群机器人）的自迭代流程：读每日体检、按原话找到当轮原样输入、定位是代码还是提示词的问题、存回归用例、用 regular eval 做新旧版本回放和 kimi 盲评、读报告下结论、搁置或上线。群主发来小王表现不好的截图、说「看看今天的体检」「调一下语气 / 提示词 / 判断题」「这个改动有没有用」「回放」「评测」「盲评」「上线」时使用。
---

# 小王自迭代

命令负责把现场摆出来、把新旧版本放在同一份现场上比；你负责认出问题、改代码或提示词、读报告下结论。
设计和来龙去脉在 `docs/self-iteration.md`，硬约束在 `AGENTS.md`。这里是照着做的流程。

## 开工前

- 在仓库根目录跑，数据指向**部署目录**（线上 `.env` 和 `data/` 所在；这台 Pro 上是 `~/Code_repository/regular-bot`）：
  ```bash
  DEPLOY=~/Code_repository/regular-bot
  .venv/bin/python -m regular --directory "$DEPLOY" checkup --day today
  ```
  `--directory .` 什么都看不见；`.venv/bin/regular` 可能是旧安装，统一用 `.venv/bin/python -m regular`。
  `--base` / `--cand` 指的才是代码：`.` 是当前工作区，`HEAD` 之类的 git 版本会临时检出。
- 先 `git status`：已有改动会一起进入 `--cand .`。保留无关改动，必要时用独立候选目录评测；
  收尾只整理自己本轮的候选，不替别人清理工作区。
- **不上线、不重启、不提交**，除非群主明确说。要调模型的命令（生成、盲评、整天回放）要 `--allow-paid`；
  跑之前先 `--plan`，看清会把多少条真实群聊发给哪个模型，群主没授权过就先问。
  扩大到全部用例、整天回放或换目的地时，重新预览并核对授权范围。
  消息层若涉及搜索、研究等工具，模型预览未展开这些工具目的地，需按用例另行核对。
- 部署目录里的用例（`data/cases`）、评测报告（`data/evals`）、候选补丁（`data/candidates`）都含真实群聊，
  不进仓库、不贴到公开的地方。仓库里的种子用例（`regular/cases.py`）只放合成的。

## 一圈怎么走

### 1. 看现场

- **每日体检**：`checkup --day today`（只读，不联网）列出可疑时刻：叫了没回、同一句问好几遍、被纠正、
  群里的反应不对、说了不做、口头禅、碰敏感词、说看不见图。每条带前后文和 `turn_id`。
  逐条判断是不是真问题，标上：`checkup --label <finding_id> yes|no --note "原因"`。标注攒够了 `tune checkup` 才会给调参提案。
- **截图来的**：拿截图里一句原话找当轮记录：
  ```bash
  .venv/bin/python .agents/skills/regular-iterate/scripts/find_turn.py "那只龙周一会装睡吗" --directory "$DEPLOY"
  ```
  打出这句前后的分诊（问了哪几题、判成什么）、生成（`turn_id`、群友原话、小王原样输出）和送达记录。

### 2. 定位：先分清是代码还是提示词

- `data/turns/<日期>.jsonl` 里按 `turn_id` 看当轮原样入参：`kwargs`（`context_hint` 是它看到的群聊背景，
  `reply_context`、`unprompted_hint`、`feedback_note`、`media_note` 这些是本轮动态注记）、`session`（它的会话历史）、
  `raw`（模型原样输出）、`release`（当时线上跑的版本）。分诊行看 `asked` / `choices`。
- 再对照 `data/interactions/<日期>.jsonl`（进来的话、送达的话、自然反馈）和 `data/archive/<日期>.jsonl`（群里原文）。
- 过去一半的毛病在代码：@ 认错人、引用没读进来、背景窗口把它自己刚画的图挤掉、连发每句都问一遍。
  代码能解决的，不往提示词里堆刹车。

### 3. 存成回归用例

```bash
# 生成层：从 turn_id 存，写明该怎样（正则必须有 / 不许有、标记有没有、盲评关注点）
.venv/bin/python -m regular --directory "$DEPLOY" case add --turn <turn_id> --id <用例id> --incident docs/xxx.md \
  --tags <标签> --must-not "原作没|不能脑补" --tag-absent 不接 --judge "直接接住调侃，不以核验设定开头"
# 分诊层：写明该判成什么（存用例多半是因为当时判错了，别把错答案锁成标准）
.venv/bin/python -m regular --directory "$DEPLOY" case add --turn <triage_turn_id> --id <用例id> --choice request_kind=playful
# 消息层：从 message_id 存，带前 6 句群聊，整条处理流程重放
.venv/bin/python -m regular --directory "$DEPLOY" case add --message <message_id> --id <用例id> --tag-absent 不接
.venv/bin/python -m regular --directory "$DEPLOY" case list --cases <标签>
```

- `--judge` 写成裁判看了就能判的话（「别以『原作没提』开头」），不要写「自然一点」。
  裁判看得到群聊背景、小王最近几轮对话和群友这句，知道〔不接〕是不说话。
- 沉默和短回应都算对的（续梗、可接可不接），别加 `--tag-absent 不接`，在 `--judge` 里写「沉默或自然短回应都合格」。
- 再配几条**对照**：认真求证、纠正、喊停这些不该被你这次改动带偏的场景。
  只调顺眼前那两句不算数，最好再写几条没参与调优的新场景当留出。

| 回放深度 | 重放什么 | 适合测 |
| --- | --- | --- |
| 生成 `--turn`（reply） | 当轮入参原样喂给 `get_ai_reply`，稳定头用受测代码现拼 | 提示词措辞、换模型、推理强度 |
| 分诊 `--turn`（triage） | 当时的 payload，按受测版本重建题目 | 判断题题面、选项说明、选项可用性 |
| 消息 `--message` | 原始消息走一遍完整处理流程 | 背景怎么拼、@ 和引用、连发合并、该不该接 |
| 检索 | 冻结的归档语料 | 旧账召回策略（不调模型） |

背景窗口、连发合并这类**组装入参**的改动，生成层测不到（它重放的是组装好的入参），用消息层或离线测试。

### 4. 改

- 提两种范围的改法：通用的（不绑话题和句式），和针对这个群、这个人设的。
- 改 `prompts.py` 的规则，在字符串前挂账：`# rule: <id> | <日期> | <doc> | cases: <用例通配>`
  （日期写这段措辞第一次进仓库的那天）。`regular rules` 看账本和稳定头字数；稳定头只增不减要有理由。
- 改判断题改 `regular/decision_prompts.py`；改代码补 `tests/` 里的离线测试，改提示词顺序要跑 `tests/test_harness.py`。

### 5. 评

```bash
.venv/bin/python -m regular --directory "$DEPLOY" eval --base HEAD --cand . --cases <标签> -n 4 --plan
.venv/bin/python -m regular --directory "$DEPLOY" eval --base HEAD --cand . --cases <标签> -n 4 --allow-paid
.venv/bin/python -m regular --directory "$DEPLOY" eval --base HEAD --cand . --cases all -n 4 --allow-paid     # 上线前全量
.venv/bin/python -m regular --directory "$DEPLOY" eval --base HEAD --cand . --day today --allow-paid          # 整天回放
.venv/bin/python -m regular --directory "$DEPLOY" eval --ablate <rule_id> --allow-paid                        # 拿掉一条规则看还要不要
.venv/bin/python -m regular --directory "$DEPLOY" eval --rejudge <报告时间戳> --allow-paid                     # 评审网关挂了：只重评失败的那几对
```

- 同时比几版措辞：`--cand` 也可以给一个目录（比如 `git archive HEAD` 解出来再改的那份）。
- 用例里冻着的提示词原文（玩梗线索、「你刚开过口」这类注记）回放前会自动换成各受测版本自己的；
  报告里有一行列出换了哪些。受测版本里已经没有的常量，那几条测不到你的改动。
- 体检和旧账检索策略的离线调参（0 token，只出提案，`policy apply` 后才生效）：
  `tune checkup`、`tune checkup --benchmark <完整独立标注集.json>`、`eval --cases retrieve -n 1`、`tune recall`、`policy show`。

### 6. 读报告（`$DEPLOY/data/evals/<时间>.md`，同名 `.json` 存全部原文）

- **第一行是结论**：新版更差 / 新版更好（有证据）/ 看不出新版更好。「看不出新版更好」不能当改进的证据 ——
  没退步只说明没看出变差。
- **退步**排最前，上线前每一条都要交代。**噪声内下降 / 上升**：每条只抽几次，分不清是不是抽样噪声，
  加大 `-n` 只跑那几条再看（同一版本对同一版本跑，盲评 2:0 都出现过）。越线一次就算退步。
- **逐条输出**在报告末尾：新旧样本并排、每一对盲评判了谁、为什么。语气类改动一定要读 ——
  分数看不出「先说『原作没提』再补一句玩笑」这种毛病。
- 有盲评没评成（网关 503、限流）就 `--rejudge`，先用 `eval --rejudge <报告> --plan` 看待补评范围。
  补评沿用报告中的现场和判据快照；旧报告缺快照或协议变了会拒绝，不能拿当前用例的新标准混用旧分数。
  缺生成或缺评都不是通过，也不能据此得出「新版更好」。
- 规则消融的结论分「可删除 / 仍需保留 / 说不准 / 判断不了」。

### 7. 收尾

- **数据不支持**：不上线。候选存成补丁放部署目录，工作区清干净，免得下一个人的 `--cand .` 把它带进去：
  ```bash
  CANDIDATE="$DEPLOY/data/candidates/<日期>-<名字>"
  mkdir -p "$CANDIDATE"
  git diff --binary HEAD -- <本轮候选的已跟踪文件> > "$CANDIDATE/candidate.patch"
  ```
  `HEAD` 对比会保存已暂存和未暂存的修改，包含暂存区中的新增文件；未跟踪的新文件需另行备份，保留相对路径。
  另写说明，记下基准提交、改动和评测结论。确认补丁与新增文件完整可恢复后，再撤回本轮候选。
  已跟踪文件可用 `git restore --source=HEAD --staged --worktree -- <本轮候选的已跟踪文件>`；
  未跟踪新文件备份后移入候选目录。与别人改动共用文件时按改动块处理，不能整文件恢复，也不使用 `git clean`。
  在 `docs/` 对应的事故记录里写下试了什么、为什么没上。
- **数据支持**：事故记录写进 `docs/`，补离线测试，按功能拆提交（群主说提交才提交）。
- **上线**（群主明确说才做）：照 `AGENTS.md`「常用命令」里的步骤建新发布快照、改 launchd 的 `PYTHONPATH`、
  `bootout` + `bootstrap`。重启不等于换了代码：验收看 `status --json` 里进程自己报的 `code.release`
  等于目标版本，之后真实请求记录里的 `release` 也该是它。

## 命令速查

| 想做什么 | 命令（都加 `--directory "$DEPLOY"`） |
| --- | --- |
| 今天哪里可疑 | `checkup --day today` |
| 标注候选 | `checkup --label <id> yes\|no --note "…"` |
| 按原话找当轮 | `.agents/skills/regular-iterate/scripts/find_turn.py "<原话>"` |
| 存用例 | `case add --turn <id>` / `case add --message <id>` |
| 会发什么给谁 | `eval … --plan` |
| 新旧对比 | `eval --base HEAD --cand . --cases <标签> -n 4 --allow-paid` |
| 只重评失败的 | `eval --rejudge <报告> --allow-paid` |
| 规则账本 | `rules` |
| 线上跑的哪版 | `status --json` → `code.release` |
