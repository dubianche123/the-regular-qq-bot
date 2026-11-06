<div align="center">

<img src="docs/readme/hero.png" alt="The Regular's original dragon-girl mascot, welcoming a new friend to the group" width="100%" />

# The Regular · 老群友

**One more friend in your QQ group: joins the jokes, remembers you, draws pictures, and speaks.**

Let your coding agent help it fit your group better, guided by your group's feedback.

[![Python](https://img.shields.io/badge/Python-3.10%2B-5375B5)](requirements.txt) [![QQ](https://img.shields.io/badge/QQ-Group%20%2B%20C2C-B48543)](bot.py) [![License](https://img.shields.io/badge/License-MIT-3A9F82)](LICENSE)

[中文](README.md) · **English** · [Download](https://github.com/dubianche123/the-regular-qq-bot/releases/latest) · [Quick start](#quick-start) · [Make it your group's own](#make-it-your-groups-own)

</div>

The Regular hangs out where your group chats. Call its name and it joins in; leave it unmentioned and it considers whether there is something worth adding. It follows jokes, takes corrections, and gives people space when asked. Names, shared stories, and what it actually said before give the next conversation some continuity.

Want something fun? Ask for a chicken playing basketball—even a short animated one. Want to hear it? Ask it to say a line aloud. **Choose its name and personality, then find the rhythm that suits your group.** The everyday bot experience is primarily designed for Chinese QQ groups; the local panel is in Chinese.

> **Claimed ChatGPT Pro API credits and wondering what to build?** Give your group a new companion. Eligible Pro / Business subscriptions must claim the benefit and link an API organization; the credits expire with the applicable billing cycle. They are not automatically included with every subscription. Check [OpenAI's current eligibility rules](https://developers.openai.com/api/docs/guides/chatgpt-api-credits) and your account benefits. Other reply model services can be configured too.

## A little life in the group

These are **illustrative use cases, not real chat transcripts**. Images, voice and reminders require their corresponding services to be configured first.

| What you ask | What it can do |
| --- | --- |
| “Xiaowang, what do you think?” | Reply using the surrounding conversation; a follow-up need not repeat the @ mention every time |
| “Draw an animated chicken playing basketball” | Fill in a short looping motion and send the generated GIF to the group |
| “Say good morning like you just woke up” | Write the line, then deliver it as synthesized speech |
| “Remind me about the meeting at nine tomorrow” | Clarify what is needed, confirm the saved reminder, and @ its creator when due |
| “That wasn't a question for you” | Back off, while leaving feedback that can guide a later improvement |

<details>
<summary>See an illustrated group conversation</summary>

![Synthetic group conversation: replies, memory and actions](docs/readme/conversation.svg)

</details>

Three things shape the experience:

- **Conversation with room for others.** Messages need not all be commands. It considers context before joining in; corrections and quiet time help when it gets that wrong.
- **Continuity between conversations.** It keeps names, relationships and selected source-backed quotes, along with its own delivered messages. When returning to an older line, it can link the original.
- **A way to improve what feels off.** Your coding agent can turn “why did it miss that?” or “why did it answer the wrong person?” into a replayable case and compare a proposed fix with the previous version.

## Quick start

Prepare **Python 3.10+, your own QQ Open Platform bot's AppID / AppSecret, and a reply model service's API key, base URL and model ID**. Creating the QQ bot, subscribing to events and setting its access scope still require your action. This project uses QQ's bot interface; it does not log in to a personal QQ account.

### 1. Download and meet it on your desktop

[**Download the latest version →**](https://github.com/dubianche123/the-regular-qq-bot/releases/latest) Choose **Source code (zip)**, extract it completely, then use your system's launcher:

| Windows | macOS | Linux |
| --- | --- | --- |
| Double-click `start.bat` | Double-click `start.command` | Run `bash start.sh` in the project directory |

The first launch downloads dependencies and places the transparent character on your desktop. Move the pointer near her to reveal chat, snack, activity and settings. It stays visible when you switch applications. If macOS blocks the double-click, run `bash start.sh` instead. A browser entry is available when the native desktop component is unavailable. You can also give your coding agent the [installation prompt](docs/agent-setup.md) (Chinese).

### 2. Give it a name and connect the services

Choose “工作台” (Workspace) beside the character, then follow the overview to “QQ 与模型” (QQ and models). Fill in QQ credentials, bot names, the reply model, and a daily call-count limit. **Opening the pet and saving settings do not call a model.** Keys stay on your computer; blank password fields preserve saved values.

![Setup panel: connecting QQ and a reply model (Chinese interface)](docs/readme/setup.jpg)

### 3. Start it and say hello

On “我的群友” (My companion), click “启动机器人” (Start bot) and wait for the actual **“已连上 QQ” (Connected to QQ)** status. Send its first nonempty private message yourself to claim ownership, then add it to your group, allow group messages, and try an @ mention or its name.

Keep the computer and panel terminal running. Closing the browser tab leaves the bot running; exiting the panel service stops the bot it started. Private data defaults to **`~/.the-regular`** and is reused on later launches.

[Full setup and troubleshooting](docs/getting-started.md) · [Services and keys](docs/providers.md) · [Configuration example](.env.example)

<details>
<summary>No QQ bot or API key yet? Try the offline message flow</summary>

After dependency installation, run this in the project directory:

```bash
# macOS / Linux; on Windows use .venv\Scripts\python.exe
.venv/bin/python -m regular demo
```

This terminal demo uses no QQ connection or model calls. It demonstrates message handling and local commands, rather than real model conversation quality.

</details>

## Add more things to do

The local panel's “能力与服务” (Capabilities and services) page has separate controls for vision, images/GIFs, incoming voice and speech output. Choose their models and credentials, set call limits, or disable them independently. Incoming voice can use only QQ-provided transcripts. See the [capability controls guide](docs/capabilities.md) (Chinese).

<details>
<summary>Shared services and four simple capability switches</summary>

![Capabilities page in a demo instance with no API keys](docs/readme/capabilities.jpg)

</details>

Start with chat, then add the capabilities you want. **A key that supports chat does not necessarily grant image, voice or Decisions API access.**

| Capability | What else it needs |
| --- | --- |
| Understand pictures | An image-capable reply model or a separate vision model; [vision setup](docs/providers.md#识图默认继承也可独立配置) |
| Draw still images or looping GIFs | An Images API with the required output support and quota, plus the pre-drawing decision check; [image setup](docs/image-generation.md) |
| Hear and send voice | Corresponding speech services and local audio dependencies; [voice setup](docs/getting-started.md#语音单独开启), [media behavior](docs/media-replies.md) |
| Reminders and more semantic judgments | Native Decisions configured in the panel's “判断 API” page; reminders are saved only after review; [reminders](docs/reminders.md) |

Text conversation, requested images and voice replies are implemented. **Choosing images or voice for spontaneous scheduled messages is still deferred.** Speech output is synthesized speaking, not singing; animations are short looping GIFs, not long videos.

## Meet at your desktop, too

<p align="center"><img src="docs/readme/pet-demo.webp" width="720" alt="Desktop pet demo: she lands, wanders, has tea, checks her phone when mentioned, talks when she replies in the group, sketches when she sends a picture, dangles when picked up, answers local chat in a bubble and naps at night"></p>

The desktop character is the default entry: **a chibi dragon who lives on the bottom edge of your screen** — she walks, sits down for tea or a book, naps when sleepy, dangles when you pick her up and falls back when you let go. More importantly, she acts out what the bot is really doing: she checks her phone when someone @-mentions her in the group, talks when she replies, sketches when she sends a picture, and dozes off when the bot stops. Hover to make her look at you and reveal chat, snack, activity and settings; pet her head and she blushes, poke too often and she pouts. Motion can be reduced.

Use the source launcher, or run `regular --directory ~/.the-regular pet`; it prepares the local panel when needed. The panel's “聊两句” page provides the same text conversation in a browser. Microphone input and desktop voice chat are not implemented yet.

**She can also help manage her capabilities.** Say “stop drawing for now” and she can offer a confirmation card; only your click changes the switch. Ask what she did today for a summary based on local activity counts. Code changes lead to a handoff brief you can copy to your coding agent.

Resize from the character's context menu; size, position and motion preference are remembered. A coding agent can cue her too, e.g. `regular pet-cue cheer --say "eval finished"`. Settings explicitly choose the chat API and primary model shared by local and QQ replies. The knowledge library provides entry previews and drafts before saving group agreements or recurring context.

[Desktop companion and local chat guide (Chinese) →](docs/companion.md)

## Make it your group's own

Every group has its own humor. When a reply is too literal, an interjection misses the target, or you want a different way of getting along, hand a screenshot or feedback to your coding agent:

> “Look at this reply. Why did it answer the wrong person? Use the repository's iteration workflow to try a fix and compare it with the previous version.”

**Keep the scene → the agent diagnoses and edits → replay the same examples → you decide what goes live.**

The repository includes replay tools and an [iteration skill](.agents/skills/regular-iterate/SKILL.md). The panel's “让 Agent 改进” (Improve with your agent) page downloads a handoff explaining where the source and data live and how to begin. Codex, Claude Code and Gemini CLI have workflow entry points.

Improvement comes from your feedback and changes you can inspect. **The bot does not edit its own code or deploy itself.** You can start with conversation and bring your agent in when something feels off.

<details>
<summary>Hand feedback to your agent from the panel</summary>

![Feedback and agent handoff in the local panel](docs/readme/iterate.jpg)

</details>

[Walk through an improvement](docs/self-iteration.md#iteration-example) · [What can change](docs/self-iteration.md#iteration-surfaces)

## A few practical details

- **Bring your own service quota.** The daily limit counts calls, not money; costs depend on models and usage.
- **Chat content goes to the model services you configure.** Settings, state and records are saved locally. Explain this to your group, and keep the panel's token-bearing URL private.
- **It will make mistakes.** Memory is selective and services can fail. Existing measurements describe particular test conditions.
- **Keep a host running.** It cannot reply while the computer sleeps or its service is stopped. See [later launches and persistent operation](docs/getting-started.md#以后如何再打开).

The cover is an AI-generated original mascot for The Regular; bot names and personalities are configurable. [Assets and generation prompts](docs/visual-identity.md). This project is not affiliated with OpenAI or QQ.

## Architecture and design

The previous technical README is preserved in [Architecture and design](docs/architecture-en.md), including message flow, Decisions, iteration, measurements and the design document index.

[Contributing](CONTRIBUTING.md) · [MIT License](LICENSE)

If you would like a friend like this in your group, give it a try. A ⭐ helps other groups find it too.
