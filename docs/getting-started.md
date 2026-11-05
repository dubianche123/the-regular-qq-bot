# 第一次使用老群友

先让它在你的 QQ 群里接住一句话，再按需加画图、语音和提醒。之后，你可以把群聊反馈交给自己的编码 Agent，让它按仓库自带的流程找现场、修改并对比。

## 下载与安装

需要 Python 3.10 或以上，以及可以下载 Python 依赖的网络。接入 QQ 还需要你自己的 QQ 机器人和回复模型服务凭据。启动器会安装项目依赖，不会替你安装 Python。

从 [Releases](https://github.com/dubianche123/the-regular-qq-bot/releases/latest) 下载 **Source code (zip)** 并完整解压；不要在压缩包预览中直接运行。解压后的目录应有 `start.bat`、`start.command`、`start.sh` 和 `pyproject.toml`。熟悉 Git 也可以克隆：

```bash
git clone https://github.com/dubianche123/the-regular-qq-bot.git
cd the-regular-qq-bot
```

| 系统 | 打开方式 |
| --- | --- |
| Windows | 安装 Python 时启用 Python Launcher 或加入 PATH，然后双击 `start.bat` |
| macOS | 双击 `start.command`；系统阻止双击或文件没有执行权限时，在项目目录终端运行 `bash start.sh` |
| Linux | 在项目目录终端运行 `bash start.sh`；发行版可能还需要安装 `python3-venv` |

首次运行会在**源码目录**建立 `.venv`，用其中的 Python 执行 `pip install -e` 安装本项目和依赖；版本变化后再次安装。安装失败会停止，不会显示已经启动。看到安装器报错时先解决网络、Python 版本或目录写权限，再重开启动文件。

启动器随后打开浏览器。如果浏览器没打开，复制终端打印的完整面板网址；网址带本机访问 token，请自己保存。面板只监听 `127.0.0.1`，用运行它的这台电脑访问。

### 源码目录和实例目录

这两个目录用途不同：

| 目录 | 保存什么 | 升级时怎么处理 |
| --- | --- | --- |
| 下载/解压的源码目录 | 程序、文档、启动文件及本地 `.venv` | 可换成新版源码；新的源码目录首次启动会安装自己的环境 |
| 默认 `~/.the-regular` | 私有 `.env`、Key、聊天记录、状态与评测结果 | 保留，后续启动继续使用；不要上传到公开仓库 |

Windows 上 `~` 指你的用户目录。默认启动不会选择你原有的线上实例。需要另一个实例时，可用启动器参数：

```bash
# macOS / Linux；路径可以包含空格
python3 tools/start_regular.py --directory "$HOME/My Regular"
```

```powershell
# Windows PowerShell
py -3 tools/start_regular.py --directory "D:\My Regular"
```

也可设置 `REGULAR_INSTANCE_DIR` 环境变量；显式 `--directory` 优先。后续每次启动保持同一个实例路径，否则会打开另一个空实例。

## 在浏览器完成首次配置

空实例会自动进入「开始使用」（`/setup`）。先填这几项：

| 配置 | 从哪里取得/怎样填 |
| --- | --- |
| QQ AppID / AppSecret | 你的 QQ 开放平台机器人凭据 |
| 它的名字 | 群里喊它的称呼；多个名字用逗号分隔 |
| 回复模型服务 | 使用兼容网关时选择「OpenAI 兼容接口」；也可选择已有的智谱或 Gemini 服务 |
| 接口地址、模型 ID、API Key | 按所选服务提供的参数填写，模型 ID 必须是该服务实际可用的聊天模型 |
| 每日 AI 调用次数上限 | 次数上限，不是金额预算 |

点击「保存配置」不会请求模型或消耗模型调用额度。AppSecret、API Key 不回显，已经填过的密码框留空会保留旧值。基础配置在**下一次启动机器人**时读取，保存不会擅自重启已运行的机器人；如果提示有环境变量覆盖，需在启动面板的环境里修改对应值。

判断 API、画图、语音可以稍后配置。提醒的语义审核需要判断 API，没配好时不会保存新提醒。它们的凭据和服务要求见[供应商配置](providers.md)、[画图配置](image-generation.md)与[到点提醒](reminders.md)。

## 启动并完成 QQ 接入

1. 去「概览」，点击「启动机器人」。面板会启动这个实例的机器人进程；网页刚打开或配置刚保存时还没有启动。
2. 等实际状态变成「已连上 QQ」。如果只看到「进程已启动，等待 QQ 连接确认」，展开当前版本与启动日志，先检查 AppID、AppSecret 和 QQ 侧授权。
3. 在 QQ 开放平台启用私聊与需要的群消息事件，并配置预期维护者/测试成员或使用范围。平台授权需要你本人操作。
4. **由你给机器人发第一条非空私聊。** 这一步会静默认主，先完成再邀请其他人使用。
5. 把机器人加入群，在群设置里允许它获取群消息，用 @ 或配置的名字试一句。已连上网关但收不到事件时，继续检查 QQ 消息事件、使用范围与群授权。

保持启动面板的终端和电脑运行。**关网页不会停止机器人；退出面板服务（如在终端按 Ctrl+C）会停止它自己启动的机器人。** 面板只能停止自己持有的子进程；若这个实例已由其他终端、launchd 或 systemd 运行，面板只读状态，不接管或停止它。

## 以后如何再打开

再次运行同一个启动文件，沿用相同实例路径即可。配置会保留；面板打开后，按需要明确点击「启动机器人」。基础配置改过后，需要停止并重新启动机器人才能使用新值；外部服务管理的机器人请回到原启动方式操作。

也可以从已安装的环境直接开面板：

```bash
# macOS / Linux
.venv/bin/python -m regular --directory ~/.the-regular panel --open
```

```powershell
# Windows PowerShell
.venv\Scripts\python.exe -m regular --directory "$HOME\.the-regular" panel --open
```

准备常驻运行时，用 `regular service` 生成 launchd 或 systemd 定义，再由你安装和启动；命令不会自动安装服务。固定发布快照的部署需同时更新服务的代码来源，不能用一次重启代替代码升级。

只想先体验聊天链路，不接 QQ 或模型，可在安装完成后运行离线 demo：

```bash
# macOS / Linux；Windows 换成 .venv\Scripts\python.exe
.venv/bin/python -m regular demo
```

## 让你的 Agent 根据群聊反馈协助改进

面板「体检」（`/checkup`）只读本机记录，列出可疑片段；它提供线索，不代表每条都确实有问题。

「让 Agent 改进」（`/iterate`）给出流程和「下载交接说明」：内容只有源码位置、实例目录和维护步骤，不附聊天或密钥，也不会连接或控制你的编码 Agent。让 Agent 打开源码工作区，读 `AGENTS.md` 和 `.agents/skills/regular-iterate/SKILL.md`，再给它你观察到的问题或截图。

源码 ZIP 带流程文件，但**没有 Git 历史**。保留一份未修改的基线源码，与候选目录比较，例如 `eval --base /path/to/baseline --cand /path/to/candidate`。命令的数据目录仍指向原实例，真实群聊用例、报告和凭据继续留在实例中。wheel 是运行安装包，要改代码先获取独立源码工作区，不能把已安装的 wheel 当成可编辑仓库。

涉及模型回放时，先用 `--plan` 说明会把多少真实聊天发给哪些模型，获你授权后再付费评测。Agent 提交结论、退步和未覆盖项，由你决定提交、发布或部署。完整方法见[自迭代设计](self-iteration.md)。
