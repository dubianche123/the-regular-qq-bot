<div align="center">

<img src="docs/readme/hero.png" alt="The Regular's original dragon-girl mascot, welcoming a new friend to the group" width="100%" />

# The Regular · 老群友

**One more friend in your QQ group: joins the jokes, remembers you, draws pictures, and speaks.**

Let your coding agent help it fit your group better, guided by your group's feedback.

[![Python](https://img.shields.io/badge/Python-3.10%2B-5375B5)](requirements.txt) [![QQ](https://img.shields.io/badge/QQ-Group%20%2B%20C2C-B48543)](bot.py) [![License](https://img.shields.io/badge/License-MIT-3A9F82)](LICENSE)

[中文](README.md) · **English** · [Download](https://github.com/dubianche123/the-regular-qq-bot/releases/latest) · [Quick start](#quick-start) · [Make it your group's own](#make-it-your-groups-own) · [Under the hood](#architecture-and-design)

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

### Let your agent install and open it

Copy this into Codex, Claude Code, Gemini CLI, or another coding agent with access to this computer:

```text
Install and open The Regular on this computer:
https://github.com/dubianche123/the-regular-qq-bot

Read the repository's docs/agent-setup.md, then download its latest stable source
release. Prepare Python, install dependencies, build the pet, and launch it.
Reuse my existing instance and settings if present. Verify that the pet is
visible and the local workspace opens; resolve ordinary installation errors.
Use the project's recommended OpenAI model settings. I will enter my key
locally and complete QQ authorization. Handle the remaining setup for me,
apart from account actions or system permissions that require my input.
Once I confirm configuration and QQ authorization are complete, start the bot
and verify its connection.
```

**Your agent handles download, installation and launch; you provide your own key and QQ authorization.** [Full installation workflow (Chinese) →](docs/agent-setup.md)

Prefer doing it yourself? Follow the three steps below. Prepare **Python 3.10+, a model service's API key, and your own QQ bot's AppID / AppSecret**. Official OpenAI can use the project's model presets; other services need their corresponding endpoint and model. Creating the QQ bot and setting its event subscriptions and access scope remain your account actions.

### 1. Download and meet it on your desktop

[**Download the latest version →**](https://github.com/dubianche123/the-regular-qq-bot/releases/latest) Choose **Source code (zip)**, extract it completely, then use your system's launcher:

| Windows | macOS | Linux |
| --- | --- | --- |
| Double-click `start.bat` | Double-click `start.command` | Run `bash start.sh` in the project directory |

The first launch downloads dependencies and places the transparent character on your desktop. Move the pointer near her to reveal a ring with play, chat, voice, activity and more. It stays visible when you switch applications. If macOS blocks the double-click, run `bash start.sh` instead. A browser entry is available when the native desktop component is unavailable.

### 2. Give it a name and connect the services

Choose **“更多 → 打开工作台” (More → Open workspace)** from the ring. The overview guides you through two connections: **“QQ 连接与身份” (QQ connection and identity)** for QQ credentials and its name, then **“模型与能力” (Models and capabilities)** to connect your official OpenAI key at the top and use the project's chat and judgment presets. For another service, expand Chat to choose its provider and model. Existing choices are preserved; expand a row when you want to change it.

![QQ connection and identity: connection status and an edit entry (Chinese interface)](docs/readme/setup.jpg)

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

**“模型与能力” (Models and capabilities)** lists chat, vision, images, incoming voice and speech in editable rows. Switch off capabilities you do not need. The separate OpenAI connection at the top lets you update the shared key without switching an existing chat service. Capabilities using the same official service can share credentials; other addresses or services need their own credentials. Official OpenAI has a default judgment model; “高级” (Advanced) lets you choose another. See the [settings guide](docs/capabilities.md) (Chinese).

<details>
<summary>Choose models by purpose, with settings folded until needed</summary>

![Models and capabilities in the local panel](docs/readme/capabilities.jpg)

</details>

Start with chat, then add the capabilities you want. **A key that supports chat does not necessarily grant image, voice or Decisions API access.**

| Capability | What else it needs |
| --- | --- |
| Understand pictures | An image-capable reply model or a separate vision model; [vision setup](docs/providers.md#识图默认继承也可独立配置) |
| Draw still images or looping GIFs | An Images API with the required output support and quota, plus the pre-drawing decision check; [image setup](docs/image-generation.md) |
| Hear and send voice | Corresponding speech services and local audio dependencies; [voice setup](docs/getting-started.md#语音单独开启), [media behavior](docs/media-replies.md) |
| Reminders and more semantic judgments | Connect the judgment service under “模型与能力 → 判断”; reminders are saved only after review; [reminders](docs/reminders.md) |

Text conversation, requested images and voice replies are implemented. **Choosing images or voice for spontaneous scheduled messages is still deferred.** Speech output is synthesized speaking, not singing; animations are short looping GIFs, not long videos.

## Meet at your desktop, too

<p align="center"><img src="docs/readme/pet-demo.webp" width="720" alt="Ring-menu pet demo: hover to reveal five actions, play a high-five rally, pick her up and chat locally using preset dialogue"></p>

Ring-menu and local-interaction demo; chat uses preset dialogue.

The desktop character is the default entry: **a chibi dragon who lives on the bottom edge of your screen** — she walks, sits down for tea or a book, naps when sleepy, dangles when you pick her up and falls back when you let go. More importantly, she acts out what the bot is really doing: she checks her phone when someone @-mentions her in the group, talks when she replies, sketches when she sends a picture, and dozes off when the bot stops. Hover to make her look at you and reveal a five-button ring (play, chat, voice, activity and more). Click her head, body or feet for different small reactions; "play" is a multi-tap high-five rally — she keeps raising her hand, you keep tapping, and stopping wraps up the streak. Text chat lives on the ring's "chat" button: double-clicking her no longer steals the clicks. Poking her repeatedly never turns into an angry routine. Notifications and settings close as soon as you click elsewhere. Motion can be reduced.

Use the source launcher, or run `regular --directory ~/.the-regular pet`; it prepares the local panel when needed. The panel's “聊两句” page provides text conversation in a browser. For voice, open “语音” beside the native pet and speak naturally: it detects when you finish, replies aloud, and lets you interrupt without holding a button. Mute when needed; close the card to end. Connect official OpenAI using the separate card at the top of Models and capabilities; credentials can be shared with capabilities using the same service. Choose the realtime speech model and voice under “发语音” and its daily turn limit under “桌宠语音”. QQ does not need to be running.

**She can also help manage her capabilities.** Say “stop drawing for now” and she can offer a confirmation card; only your click changes the switch. Ask what she did today for a summary based on local activity counts. Code changes lead to a handoff brief you can copy to your coding agent.

Cycle her size from the "More" panel; size, position and motion preference are remembered. A coding agent can cue her too, e.g. `regular pet-cue cheer --say "eval finished"`. Local and QQ replies share the chat model selected under Models and capabilities. Group knowledge is an entry list: choose its group, add or edit one entry, preview, then save. Deleting an entry asks for confirmation.

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

- **Separate fixed judgments from open-ended replies.** Intent triage and reminder checks use closed-set Decisions API questions; the chat model writes replies. [908 historical triage calls had a 0.71-second median latency](docs/judgment-layer.md).
- **Four context layers preserve a reusable prefix.** Stable instructions → daily memory → conversation history → current-turn facts. Speaker- and turn-specific information goes last, keeping stable input available for [prefix caching](docs/DESIGN_NOTES_EN.md).
- **Compare changes, and reject weak candidates.** [Generation, triage, retrieval and message replays](docs/self-iteration.md), independent model blind judging, and single-rule ablation with `--ablate` help decide whether a change is worth shipping.
- **2,100+ offline regression cases protect state boundaries.** Relationships, names, tasks and quotas stay in local code. [CI](.github/workflows/ci.yml) covers Python 3.10 / 3.12 / 3.13 and desktop audio paths on Windows / macOS.

The previous technical README is preserved in [Architecture and design](docs/architecture-en.md), including message flow, Decisions, iteration, measurements and the design document index.

[Contributing](CONTRIBUTING.md) · [MIT License](LICENSE)

If you would like a friend like this in your group, give it a try. A ⭐ helps other groups find it too.
