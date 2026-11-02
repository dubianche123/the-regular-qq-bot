<div align="center">

# The Regular · 老群友

**A QQ group companion that knows when to join in, when to listen, and when to lend a hand.**

[![Python](https://img.shields.io/badge/Python-3.10%2B-5375B5)](requirements.txt) [![Offline checks](https://github.com/dubianche123/the-regular-qq-bot/actions/workflows/ci.yml/badge.svg)](https://github.com/dubianche123/the-regular-qq-bot/actions/workflows/ci.yml) [![License](https://img.shields.io/badge/License-MIT-3A9F82)](LICENSE)

[中文](README.md) · **English** · [Releases](https://github.com/dubianche123/the-regular-qq-bot/releases)

</div>

A group chat is more than a stream of questions. People tease each other, change their minds, send a thought in several messages, and sometimes want the bot to stop talking. The Regular is an experiment in making a long-lived AI companion fit into that conversation: remembering shared context, responding to natural feedback, and leaving space for everyone else.

It can chat, read images, draw, look things up, and recall relevant past conversations. It is designed for a QQ group, not as a general-purpose agent or a computer-use assistant.

## What makes it different

The bot separates **deciding what kind of response is appropriate** from **writing the response**. Instead of asking the chat model to plan and repeatedly call tools in a ReAct loop, the normal group-message flow uses a closed-choice decision interface. It asks questions such as “is this person talking to the bot?”, “is this playful?”, and “would a search or image help?” The application combines those answers with local rules, context and quotas, then executes the appropriate bounded action. The chat model turns the available context and evidence into a natural reply.

![The Regular architecture: messages, closed-choice decisions, local execution, and a conversational reply](docs/readme/flow.svg)

The roles are deliberately distinct:

- **The decision layer interprets the situation.** It chooses among predefined options rather than writing a plan. Relevant questions are normally grouped into one triage call; some situations have separate decisions. A judgment can be uncertain or unavailable, so the application has fallback behavior.
- **Local code owns state and execution.** It manages relationships, names, memory, budgets and delivery, and decides whether to run a bounded lookup or image request. The decision endpoint does not execute tools or write the bot's ledgers.
- **Models create content.** The conversation model writes replies from the assembled context; other model calls may handle tasks such as image understanding, image generation and summaries. The chat model does not run an autonomous tool-use loop.

This is inspired by the idea of using fixed-choice judgments for agent decisions, including work by Jev. The implementation currently uses **OpenAI's Decisions API**; Jev is a separate product, not another name for that API. See the [judgment-layer design](docs/judgment-layer.md) and [comparison](docs/decision-calibration.md) for what was measured and where the judgments fall short.

## Life in the group

- **Join in without taking over.** The bot considers whether it was addressed, the conversation's rhythm and recent feedback. If someone wants it to stop, it can back off from unsolicited remarks while remaining available when addressed.
- **Follow the conversation.** It can use nearby messages to interpret a request split across several sends, or a correction to something it just said. These are judgments, not guarantees; ambiguous situations can still be misunderstood.
- **Remember with evidence.** Group summaries and relationships give continuity. Personal details drawn from chat are checked against source messages before they are saved, and relevant memories are used selectively. A maintainer can also provide a local knowledge base.
- **Help in natural language.** When configured, it can search for current information or draw a requested image. Slow requests can receive a brief local acknowledgment followed by the result. Search and drawing have daily limits; image limits can vary with the relationship, and a confirmed undelivered image does not consume the member's allowance.
- **Keep cost and control visible.** Local quotas and timeouts bound paid work. Prompts are arranged with stable context before turn-specific material to make prefix caching possible where supported; cache hits and costs depend on the provider and workload.

The bot uses online model services. With the decision layer enabled, group messages are sent to its configured endpoint in a de-identified form by default; group and user identifiers are removed locally, and messages caught by the sensitive-word filter are not sent for that decision. Other enabled model and search features also send the context they need to their configured providers. See [provider configuration](docs/providers.md) before using it in a group.

## Quick start

You need Python 3.10 or later, a QQ bot with the required permissions, and a compatible model service. Optional capabilities such as decisions, search and image generation need their corresponding service configuration and access.

### 1. Install

```bash
git clone https://github.com/dubianche123/the-regular-qq-bot.git
cd the-regular-qq-bot
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

To try the local terminal demo without QQ credentials or an API key, run `regular demo`.

### 2. Configure

Prepare the QQ bot's AppID and AppSecret, plus the model endpoint, API key and model name:

```bash
regular --directory regular-bot setup
```

The wizard creates `regular-bot` as a local instance directory and guides you through configuration. The decision endpoint can be configured separately through the local panel:

```bash
regular --directory regular-bot panel
```

See [provider configuration](docs/providers.md) and [.env.example](.env.example) for optional features and settings.

### 3. Start and invite the bot

Enable C2C and group events in the QQ Open Platform console, check the bot's [usage scope](https://bot.q.qq.com/wiki/#_10-%E4%BD%BF%E7%94%A8%E8%8C%83%E5%9B%B4%E4%B8%8E%E4%BA%BA%E5%91%98%E9%85%8D%E7%BD%AE), then run:

```bash
regular --directory regular-bot run
```

The **first private message to the running bot claims ownership**. Send it yourself before inviting others. After adding the bot to a group, set its group message scope to **获取群内全部消息** (receive all group messages). You can address it with an @ mention or a configured name.

## Read more

- [Judgment layer: what the decision interface does](docs/judgment-layer.md) and [how its judgments were evaluated](docs/decision-calibration.md)
- [When to join in](docs/when-to-intervene.md), [addressing the bot](docs/addressing.md), and [personal memory](docs/personal-memory.md)
- [Provider configuration](docs/providers.md), [web lookup and follow-ups](docs/slow-work.md), and [image generation](docs/image-generation.md)
- [Design notes](docs/DESIGN_NOTES_EN.md)

## License

[MIT](LICENSE)
