# 让你的 Agent 帮你安装

先下载并解压源码，或让 Agent 克隆本仓库。让 Codex、Claude Code、Gemini CLI 等编码 Agent 打开项目目录，复制下面这段话即可。安装过程需要下载 Python 依赖；QQ 开放平台的授权仍由你本人完成。

```text
请帮我安装 The Regular · 老群友：
https://github.com/dubianche123/the-regular-qq-bot

先读仓库 AGENTS.md、docs/getting-started.md 和 docs/companion.md。
检查本机 Python 3.10+、图形桌面和依赖下载条件；使用项目自带的
tools/start_regular.py / start.command / start.bat / start.sh，默认从桌宠进入。
不要把我的已有实例或线上机器人替换成空配置，不重启其他机器人。

私有配置和数据放在 ~/.the-regular；如果我已经有实例，先沿用那个目录。
首次打开应看到 Q 版人物从上方落到屏幕底边，靠近后侧边出现快捷栏，从「工作台」首页进入「QQ 与模型」。
无桌面环境使用 regular panel，原生组件不可用时说明网页替代入口。

请告诉我在本机设置页填写 QQ AppID、AppSecret、模型服务地址、模型 ID、
API Key 和每日调用次数上限。不要让我把密钥贴到聊天里，不要打印密钥。
不要替我登录 QQ 平台；告诉我需要亲自完成的事件订阅和使用范围设置。
保存配置不调用模型，也不要自动启动机器人。

在「能力与服务」逐项说明读图、画图/动图、听语音、发语音的状态；只启用我需要的能力。
语音 Key 在共用服务中填写一次；不要把聊天 API 权限当成语音或生图权限。
需要本地读状态或开关时用 regular --directory <实例目录> manage，不执行自主工具循环。

我完成配置并明确要求启动后，再检查实际 QQ 连接状态；由我先发第一条
非空 QQ 私聊完成认主。若要做真实模型测试，先说清发送什么、发给谁。
最后告诉我如何再次打开、关闭人物和停止本次启动的服务。
```

安装以后，遇到接话不合拍的问题，可以在本机工作台「让 Agent 帮忙」复制交接说明，再附上你的反馈。安装引导负责把它跑起来；群聊回放与自迭代另走仓库自带的 [regular-iterate skill](../.agents/skills/regular-iterate/SKILL.md)。
