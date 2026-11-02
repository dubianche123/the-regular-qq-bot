<div align="center">

<img src="docs/readme/cover.svg" alt="The Regular: a QQ group companion that remembers shared stories" width="100%" />

# The Regular · 老群友

**An AI companion for QQ group chats: it gets to know the people, remembers shared stories, and can join in—or leave room for others.**

[![Python](https://img.shields.io/badge/Python-3.10%2B-5375B5)](requirements.txt) [![QQ](https://img.shields.io/badge/QQ-Group%20%2B%20C2C-B48543)](bot.py) [![License](https://img.shields.io/badge/License-MIT-3A9F82)](LICENSE)

[中文](README.md) · **English** · [Quick start](#quick-start) · [Learn more](#learn-more)

</div>

A group chat is more than a stream of questions. People tease each other, send a thought across several messages, correct each other, and sometimes want the bot to stop talking. The Regular is an experiment in being part of that conversation over time, rather than waiting for commands.

It can chat, understand images and voice messages, search for information, recall relevant group history, draw, and reply with voice when the corresponding services are configured. These are abilities, not guarantees: the bot can misunderstand a conversation, and external services can fail.

## Life in the group

<table>
<tr>
<td width="50%" valign="top"><strong>💬 Follow the conversation</strong><br>Ask a question in one message and call the bot in the next; it can use the surrounding conversation to understand.</td>
<td width="50%" valign="top"><strong>🤝 Take a joke or correction</strong><br>It can pick up on a correction, play along with a joke, and step back when someone wants quiet.</td>
</tr>
<tr>
<td width="50%" valign="top"><strong>🌿 Be part of the group</strong><br>It may join a conversation or stay quiet, while remembering names, relationships and shared experiences.</td>
<td width="50%" valign="top"><strong>🎨 More than text</strong><br>Ask naturally for a picture, a voice reply, a search or an old story. Daily limits apply to search and drawing.</td>
</tr>
</table>

![Illustrative group-chat interactions: a split request, a correction, and an image request](docs/readme/conversation.svg)

These examples illustrate the intended experience; individual replies can differ.

## How it works

![The Regular architecture: local rules, closed-choice judgments, conversational decisions, and bounded actions](docs/readme/flow.svg)

A group companion has to do more than write a good sentence: **is this message meant for me, should I answer, and what kind of response fits?** The Regular combines three roles:

- **Closed-choice judgments** help interpret selected situations, such as whether a name mention is addressing the bot, whether someone is responding to its previous message, or whether a reply needs a lookup. Applicable questions can be grouped in one call. The result is a choice, not a plan or permission to execute tools.
- **The conversation model** uses the surrounding chat to decide whether to respond when nobody explicitly calls it. When it does reply, it writes the response and can indicate that an image or voice reply would fit. It can also choose to say nothing.
- **Local code** manages names, relationships, memory, budgets and delivery. It combines judgments with rules, checks whether a requested action is available and permitted, and executes the bounded lookup, image or voice task. It records the actual result so subsequent conversation has the right context.

### How Decisions is used

For a message relevant to the bot, local code selects the applicable questions and can send them together to Decisions. The answers help the code choose a response or a lookup; the decision service does not execute tools or update state.

![Decisions in use: select applicable questions, make closed-choice judgments, and handle the results in code](docs/readme/decision.svg)

The bot does not need a ReAct-style autonomous planning and repeated tool-use loop. Decisions are shared between the judgment interface, the conversation model and local rules; the code retains control of state and execution. This describes the bot's own orchestration, not the internal workings of external search services.

The project currently uses **OpenAI's Decisions API** for closed-choice judgments. [Jev](https://www.jev-llm.com/) is a separate product; the project draws on the idea of fixed-choice judgments but does not use Jev as that API. Judgments can be uncertain or wrong, and different situations have different fallbacks. See [judgment-layer design](docs/judgment-layer.md) and [decision calibration](docs/decision-calibration.md).

## Memory, privacy and cost

- **Local state:** Names, relationships and quotas are maintained by code rather than written directly by the chat model. Proposed personal memories are checked against local source messages before being saved and used selectively in context.
- **Bounded resources:** Search and image generation have daily limits. An image that is confirmed not to have been sent does not consume the member's personal allowance. Slow searches may receive a local waiting message and a follow-up when ready.
- **Online services:** Local storage does not mean the chat stays entirely on the device. Relevant messages and context may be sent to configured conversation and decision services; enabled image, voice and search features also send the content they need to their providers. Group and user identifiers are removed from decision requests locally. Review [provider configuration](docs/providers.md) before using the bot in a group.
- **Costs depend on use:** The bot may call the conversation model to decide whether to respond even when it ultimately stays quiet. Context is arranged to make prefix caching possible where supported, but actual cache hits and charges depend on the provider and workload.

## Quick start

You need Python 3.10 or later, a QQ bot with the required permissions, and a compatible model service. Optional features such as decisions, search, images and voice require the corresponding service access and configuration.

```bash
git clone https://github.com/dubianche123/the-regular-qq-bot.git
cd the-regular-qq-bot
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
regular demo
```

The terminal demo runs without QQ credentials or an API key. To connect a QQ bot, prepare its AppID and AppSecret and your model-service credentials, then run:

```bash
regular --directory regular-bot setup
regular --directory regular-bot run
```

Enable C2C and group events in the QQ Open Platform console. **The first private message to the running bot claims ownership**; send it yourself before inviting others. After adding the bot to a group, allow it to receive all group messages in the group settings. You can address it with an @ mention or a configured name.

For optional capabilities and settings, see [provider configuration](docs/providers.md) and [.env.example](.env.example). The local panel can configure the decision endpoint separately:

```bash
regular --directory regular-bot panel
```

## Learn more

- [Judgment layer](docs/judgment-layer.md) · [When to reply without being called](docs/when-to-reply.md)
- [Image and voice replies](docs/media-replies.md) · [Web lookup and follow-ups](docs/slow-work.md)
- [Personal memory](docs/personal-memory.md) · [Design notes](docs/DESIGN_NOTES_EN.md)

[MIT License](LICENSE)
