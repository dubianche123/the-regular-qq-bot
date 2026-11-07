# 编码 Agent 的本机提示

把 Codex 或 Claude Code 的结束事件接给桌宠，一轮回复结束后，她就会提醒你回去看看；Claude Code 请求权限时也能提示你处理。提醒跟随客户端事件，具体结果以对应 Agent 的报告为准。

桌宠和本机面板运行时即可接收通知。接入命令读取事件类型，向本机桌宠发送固定文案，使用现有 token 和 CSRF 认证。客户端 hook 配置好并获信任后，提醒会自动触发；桌宠未运行时，Agent 可以照常结束回复。

先用实例目录手动试：

```bash
regular --directory ~/.the-regular pet-notify --tool codex --status finished
regular --directory ~/.the-regular pet-notify --tool claude --status attention
```

源码环境可以把 `regular` 换成 `.venv/bin/python -m regular`，也保留了 `tools/pet_agent_notify.py` 脚本入口。下面示例里的 `regular` 可执行文件与实例目录请填本机绝对路径；已有 hook 时合并配置，保留原有条目。

## Codex CLI 和桌面应用

[Codex 官方 Hooks 文档](https://learn.chatgpt.com/docs/hooks)说明 CLI 和支持的 Codex 客户端可在 `Stop` 事件运行本地命令；非托管 hook 必须先审阅并信任。下面是可放入用户级 `~/.codex/hooks.json` 的示例；若文件已有内容，请合并而不是覆盖。项目级 `.codex/hooks.json` 只在对应可信项目生效。脚本必须能在执行环境访问到，本机桌宠也必须能连接到该环境；远程环境不自动获得本机服务。

```json
{
  "hooks": {
    "Stop": [{"hooks": [{"type": "command", "command": "/absolute/path/to/regular --directory /absolute/path/to/instance pet-notify --tool codex", "timeout": 10}]}]
  }
}
```

在 Codex CLI 用 `/hooks` 审阅并信任新配置，桌面端按客户端的 hook 审阅提示操作。已有其他 Stop hook 时，它们会同时运行；同一事件保留一个桌宠提醒即可。

Codex CLI 也有另一种 [`notify` 机制](https://learn.chatgpt.com/docs/config-file/config-advanced#notifications)，以 JSON 参数发送 `agent-turn-complete`；本脚本支持该格式。但 `notify` 是用户级单个命令，替换它可能关闭已有通知，因此优先使用可并存的 Stop hook，不要直接覆盖现有设置。

## Claude Code

[Claude Code Hooks 文档](https://code.claude.com/docs/en/hooks)说明 `Stop` 在主 Agent 完成回复时触发；`Notification` 的 `permission_prompt` 表示需要用户授权。可将下面的片段合并进用户或项目的 Claude Code settings JSON：

```json
{
  "hooks": {
    "Stop": [{"hooks": [{"type": "command", "command": "/absolute/path/to/regular --directory /absolute/path/to/instance pet-notify --tool claude", "timeout": 10}]}],
    "Notification": [{"matcher": "permission_prompt", "hooks": [{"type": "command", "command": "/absolute/path/to/regular --directory /absolute/path/to/instance pet-notify --tool claude", "timeout": 10}]}]
  }
}
```

脚本只对上述事件和类型发提示，忽略其他通知；不会读取 transcript 或正文。自动接入是否运行取决于客户端 hook 配置及信任设置。客户端没有可用 hook 的情况下，只能通过上面的显式命令发提示。
