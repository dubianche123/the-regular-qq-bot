<div align="center">

<img src="docs/readme/hero.png" alt="The Regular's original dragon-girl mascot: hangs out on your desktop and joins your group chat" width="100%" />

# The Regular · 老群友

**A chibi dragon-girl desktop pet & Coding Agent companion on your screen—and the only one with a real social life in your QQ group.**

On your desktop, she strolls, flies, drops physics toys, chats hands-free via real-time voice, and cheers when your Claude Code / Codex finishes a task.
Connected to your group chat, she reads the room, joins inside jokes, draws looping GIFs, speaks aloud, and evolves both *when she speaks* and *how she replies* around your friends' feedback.

**No QQ bot required to start—let Codex / Claude Code put her on your desktop first, add an API key to chat locally, and connect your group anytime.** [Copy the install prompt ↓](#quick-start)

[![Agent-First](https://img.shields.io/badge/Agent--First-Codex%20%C2%B7%20Claude%20Code-7A5AF8)](docs/agent-setup.md) [![Desktop Pet](https://img.shields.io/badge/Desktop%20Pet-270%2B%20Actions%20%C2%B7%20Live%20Sync-2E90FA)](docs/companion.md) [![Triage](https://img.shields.io/badge/Triage-Decisions%20API%20(Jev)-E07A3F)](docs/judgment-layer.md) [![Self-Iteration](https://img.shields.io/badge/Self--Iteration-Replay%20%2B%20A%2FB-3A9F82)](docs/self-iteration.md) [![Python](https://img.shields.io/badge/Python-3.10%2B-5375B5)](requirements.txt) [![Tests](https://img.shields.io/badge/offline%20tests-2100%2B-3A9F82)](.github/workflows/ci.yml) [![License](https://img.shields.io/badge/License-MIT-3A9F82)](LICENSE)

[中文](README.md) · **English** · [Download Latest](https://github.com/dubianche123/the-regular-qq-bot/releases/latest) · [270+ Action Desktop Pet](#a-dragon-girl-companion-on-your-desktop) · [Live Group Social Life](#connect-your-group-a-desktop-pet-with-a-real-social-life) · [Self-Iteration](#make-it-your-groups-own) · [Quick Start](#quick-start) · [Under the Hood](#architecture-and-design)

</div>

Most desktop pets live on a lonely single-player island on your screen; most chat bots are headless background processes hidden inside a terminal window. **Tuantuan (`小团团`) bridges both worlds: she has a living body on your desktop and a real social life with your friends in group chat.**

- **Just want a lively AI desktop pet & Coding Agent buddy first?** Zero QQ bot setup needed. Launch her in one step and she lands at the bottom of your screen—strolling, taking flight, peeking from screen edges, roasting marshmallows with dragon fire, strumming a ukulele, and dropping interactive physics props you can bounce or collect. Pat her head, play high-five rally games, talk with her hands-free via real-time voice, or let Claude Code / Codex trigger celebratory animations when your build or eval finishes.
- **Want to bring her into your QQ group?** Add your QQ bot credentials anytime in the local workbench. With no slash commands required, she chimes in when called, catches implicit follow-ups, draws looping GIFs, sends expressive voice lines, and **mirrors everything she's doing in the group right on your desktop in real time**.

### ✨ Why Desktops (and Groups) Love Her

- **🐉 270+ Actions & Physics Toys—Play Standalone or Synced Live with Your Group**: Ships with **[270+ four-frame sprite strips](docs/pet-repertoire.md)** (wing & tail care, dragon-fire snacks, magic tricks, treasure counting, musical instruments, crafts, 12 sleeping poses, mid-screen flight, and screen-edge peeks) plus interactive **physics props** (`ball`, `coin`, `gem`, `star`, `feather`). Standalone, she's a rich desktop companion; connected to QQ, **when someone `@`-mentions her in the group, she pulls out her phone on your screen; when the chat gets lively, she grabs a bucket of popcorn; when someone asks for a drawing, she sketches until the image lands in the chat.**
- **🧠 Ultra-Cheap Turn-Taking via Decisions API (`Jev`)**: Group bots usually suffer from noisy regex triggers or burn tokens calling a full LLM just to output `SKIP`. The Regular uses the **Decisions API (`Jev` closed-set judgment endpoint)**—at roughly **0.1 USD / 1M tokens and a 0.71s median latency**—to decide *when to speak* before calling the main chat model: distinguishing direct questions from third-party mentions, catching implicit follow-ups, recalling old quotes, and respecting back-off cues.
- **🔄 Tailored to Your Vibe via Self-Iteration (Even for *When to Speak*)**: Every group has its own humor and conversational pace. When she feels too quiet, too chatty, or too literal, you don't need to dig through Python files—hand a screenshot to your Coding Agent. Both **the judgment layer (Decisions API triage questions & margins)** and **the generation layer (reply prompts)** can be replayed, tuned, and blind-evaluated on real turns.
- **🤖 Agent-First Zero-Friction Setup + One Key for Everything**: From downloading the release, preparing Python, and building the desktop pet, to running daily health checkups, tuning triage & reply rules, and making the desktop pet jump for joy when evals finish (`regular pet-cue cheer`), **Codex, Claude Code, or Gemini CLI** can handle the heavy lifting. Enter **one official OpenAI API key** (shared across chat, Jev judgment, vision, image/GIF generation, and voice with pre-selected models) to chat on your desktop right away—and connect QQ whenever you're ready.

> 💡 **Sitting on monthly ChatGPT Pro / Business API credits and wondering what to build?**
> Adopt a dragon-girl on your desktop first, then introduce her to your group ([OpenAI API credits guide](https://developers.openai.com/api/docs/guides/chatgpt-api-credits)). You can also plug in Zhipu GLM, Gemini, or any compatible model service.

---

## A Dragon-Girl Companion on Your Desktop

<p align="center"><img src="docs/readme/pet-demo.webp" width="720" alt="Ring-menu pet demo: hover to reveal five actions, play a high-five rally, pick her up and chat locally"></p>

**No QQ bot credentials required—launch her and start playing right away.** Tuantuan lives along the bottom edge of your screen (above your Dock or taskbar) and stays with you as you switch between apps:

- **270+ Action Repertoire & Mid-Screen Flight**: She has a rich daily life of her own—grooving with headphones, grinning at a handheld console, brushing her tail scales, breathing tiny flames, casting magic, counting treasure, playing instruments, and cycling through 12 cozy sleeping poses (hugging her tail, using her wings as a blanket, resting on a star pillow). When in high spirits, she even **spreads her wings to fly across your screen** or peeks out from the left, right, or bottom screen edges.
- **Interactive Physics Props & Pick-Up Physics**: When she plays, casts spells, or counts treasure, interactive **balls, coins, gems, stars, and feathers** spawn with gravity and bounce—click or drag to bounce the ball or collect coins. Pick her up to watch her dangle her legs, or toss her gently upward to bonk against the top of the screen.
- **Headpats, Snacks & High-Five Rallies**: Hover near her and she turns to look at you, revealing a five-button radial ring (`Play / Chat / Voice / Activity / More`). Patting her head or feeding her cookies genuinely boosts her mood and walking pep; click *Play* for a fast-paced multi-tap high-five combo game.
- **Hands-Free Real-Time Voice & Coding Agent Celebrations**: With a single OpenAI key configured, click *Voice* on the radial ring to **talk with her hands-free**—she detects when you finish, replies aloud, and lets you interrupt anytime without holding a button or connecting QQ. And while you code with Codex or Claude Code, your agent can run `regular pet-cue cheer --say "All tests passed!"` to make her celebrate on your desktop!

Launch her via the starter script or `regular --directory ~/.the-regular pet`. Size (`60%–150%`), position, stay-in-place toggle, and motion preferences persist automatically. [Desktop companion & local chat guide →](docs/companion.md) · [Explore the 270+ action repertoire →](docs/pet-repertoire.md)

---

## Connect Your Group: A Desktop Pet with a Real Social Life

Once you connect Tuantuan to a QQ group, she's no longer confined to a single screen—she becomes **a natural group regular who needs no `/commands` and acts out her group life right on your desktop**.

### Live Desktop × Group Sync

Your desktop pet mirrors what Tuantuan is doing in the group in real time (polling coarse local signals every 3 seconds without exposing raw chat text):
- **Someone `@`-mentions her or calls her name** → an exclamation mark pops over her head and she pulls out her phone;
- **She sends a reply or chimes in** → she talks on your desktop (and may even reply while hovering mid-air when in a great mood);
- **Friends send images or voice notes** → she holds up the picture to inspect it or leans in to listen;
- **Someone requests a drawing or voice line** → she grabs her sketchbook and draws (or sings into a microphone) until the media lands in the group, then holds up the finished work proudly;
- **The group chat gets super lively** → she sits down with a bucket of popcorn to watch the action.

### Natural Group Banter

With the default official OpenAI setup, chat, judgment, vision, drawing, voice, and reminders all share the same API key—toggle whatever you want in the local workbench:

| What your group says | How Tuantuan responds |
| --- | --- |
| “Tuantuan, what do you think?” | Replies using surrounding context—and catches follow-ups without needing an `@` on every message |
| “Draw an animated chicken playing basketball” | Fills in a short looping motion and sends the generated GIF straight to the group |
| “Say good morning like you just woke up” | Writes the line in character, then delivers it as expressive synthesized speech |
| “Remind me about the 9 AM meeting tomorrow” | Clarifies what's needed, confirms and logs the reminder locally, and `@`s you right on time |
| “That wasn't a question for you” | Steps back immediately—and logs the moment as feedback for future tuning |

<details>
<summary>🎬 See an illustrated group conversation</summary>

![Synthetic group conversation: replies, memory and actions](docs/readme/conversation.svg)

</details>

Three things shape the everyday group experience:
- **Conversation with room for others.** No slash commands required. She considers conversational turn-taking before chiming in, and respects quiet time whenever someone asks her to chill.
- **Continuity between conversations.** Local deterministic code tracks nicknames, rapport scores, and source-backed quotes alongside her own delivered messages—linking back to original lines when revisiting older topics.
- **A built-in loop to fix what feels off.** Your Coding Agent can turn *"why did she miss that?"* or *"why did she answer the wrong person?"* into a replayable regression case and benchmark a proposed fix against the current version.

---

## Make It Your Group's Own

Every friend group has its own inside jokes and rhythm. When Tuantuan replies too literally, chimes in at the wrong moment, misses an implicit follow-up, or needs tuning to fit your group's style, **you never have to dig through code or hand-edit prompts**—just hand a screenshot or note to your Coding Agent:

> *“Look at Tuantuan's last reply. Why did she answer the wrong person? Follow the repository's self-iteration workflow, try a fix, and compare it with the current version.”*

```text
Keep live triage & reply scenes → Agent diagnoses Judgment (Decisions) vs. Reply layer → Paired replay & blind A/B judging → You decide what goes live
```

**Tune both *how she speaks* and *when she speaks*:**
- **Triage Layer (`Decisions / Jev`) Iteration**: If she chimes in too often or stays too quiet in your group, your agent can replay historical triage logs (`regular eval --layer triage`) to refine the closed-set decision questions, option phrasing, or confidence margins.
- **Generation Layer Iteration**: If her tone feels stiff or repetitive, your agent can replay real conversation windows, run independent blind A/B judging, and prune redundant prompt rules with `--ablate`.

The repository ships with replay tools and **[`.agents/skills/regular-iterate/SKILL.md`](.agents/skills/regular-iterate/SKILL.md)**. Open **“让 Agent 改进” (Improve with your agent)** in the local workbench to download a ready-to-use handoff brief telling Codex, Claude Code, or Gemini CLI exactly where the source and data live and how to begin.

<details>
<summary>🛠️ See how the workbench hands feedback to your Coding Agent</summary>

![Feedback and agent handoff in the local panel](docs/readme/iterate.jpg)

</details>

[Walk through a complete self-iteration cycle →](docs/self-iteration.md#iteration-example) · [See all 8 customizable surfaces →](docs/self-iteration.md#iteration-surfaces)

---

## Quick Start

You can **adopt Tuantuan on your desktop first** (zero credentials needed to play with the 270+ action pet, or 1 API key for local text & real-time voice chat) and **connect her to your QQ group whenever you're ready**—or set up both at once.

### Option 1: Let Your Coding Agent Install & Open Everything (Recommended)

Copy and paste this into **Codex, Claude Code, Gemini CLI**, or any local coding agent:

```text
Install and open The Regular on this computer:
https://github.com/dubianche123/the-regular-qq-bot

Read the repository's docs/agent-setup.md, then download its latest stable source
release. Prepare Python, install dependencies, build the pet, and launch it.
Reuse my existing instance and settings if present. Verify that the pet is
visible and the local workspace opens; resolve ordinary installation errors.
Use the project's recommended OpenAI model settings. I will enter my official
OpenAI key locally (and my QQ AppID / AppSecret when I want to connect a QQ group).
Handle the remaining setup for me,
apart from account actions or system permissions that require my input.
If I have entered QQ credentials and completed authorization, start the bot
and verify its connection.
```

**Your agent handles download, Python setup, desktop pet build, and launch; you simply enter an API key locally to chat on your desktop, or add QQ credentials anytime to join a group.** [Full agent installation workflow →](docs/agent-setup.md)

---

### Option 2: Do It Yourself in 3 Steps

#### 1. Download & Meet Her on Your Desktop (Zero Credentials Needed)

[**Download the latest release →**](https://github.com/dubianche123/the-regular-qq-bot/releases/latest) Choose **Source code (zip)**, extract it, and run your OS launcher:

| Windows | macOS | Linux |
| --- | --- | --- |
| Double-click `start.bat` | Double-click `start.command` (or `bash start.sh`) | Run `bash start.sh` in the project directory |

The first launch installs dependencies and places the chibi dragon-girl on your desktop. **Even before entering any API key, you can hover near her to explore 270+ animations, pat her head, feed her cookies, toss physics props, and play high-five rallies!**

#### 2. Add One API Key for Local Voice/Text Chat (Optional: Connect QQ)

Choose **“更多 → 打开工作台” (More → Open workspace)** from the radial ring:
- **“模型与能力” (Models and capabilities)**: Connect your official OpenAI key at the top to unlock all recommended chat, judgment, and multimodal presets at once—you can now chat with her on your desktop via text or hands-free real-time voice (or expand *Chat* to pick Zhipu GLM, Gemini, etc.);
- **“QQ 连接与身份” (QQ connection and identity)** *(when connecting to QQ)*: Enter your QQ bot AppID / AppSecret and give her a group name (e.g., *Tuantuan* / *小团团*).

![QQ connection and identity setup](docs/readme/setup.jpg)

#### 3. Connect Your Group & Watch Desktop Sync Come Alive

On **“我的群友” (My companion)**, click **“启动机器人” (Start bot)** and wait for **“已连上 QQ” (Connected to QQ)**. Send her a first private message (C2C) to automatically claim owner permissions, then invite her to your group and say hi!

> Private settings and group memories live in **`~/.the-regular`** and persist across updates. [Full setup & troubleshooting →](docs/getting-started.md) · [Services & keys →](docs/providers.md) · [Config example →](.env.example)

<details>
<summary>🧪 Want to test message routing 100% offline in the terminal?</summary>

After installing dependencies, run:

```bash
# macOS / Linux; on Windows use .venv\Scripts\python.exe
.venv/bin/python -m regular demo
```

Runs 100% offline with zero model calls so you can explore message routing, rapport ledgers, and local commands.

</details>

---

## One Key, with Capabilities You Can Switch On or Off

With the default official OpenAI setup, **local desktop chat, group chat, judgment, vision, images, and voice all share the single key you entered at the start—and models are already pre-selected.** Just choose which capabilities to enable and set their daily call limits.

Open **“模型与能力” (Models and capabilities)** to view each row's status, model, and daily quota at a glance. Toggles save immediately; expand any row when you want to customize its model or limit. See the [settings guide](docs/capabilities.md).

| What you want | How the default setup works |
| --- | --- |
| **🗣️ Continuous voice / text chat with the pet** | Open *Voice* or *Chat* from the desktop pet's ring; independent of QQ settings and **needs no QQ connection** |
| **👀 Understand pictures & memes** | Enabled by default using the chat model; toggle off or pick a dedicated model under *Vision* |
| **🎨 Draw still art or looping GIFs** | Shares the OpenAI connection; adjust switches, GIF animation, and daily limits under *Images* |
| **🎧 Transcribe QQ voice notes** | Enabled by default; the preset transcription model fills in whenever QQ omits text |
| **🎙️ Send voice replies in QQ** | Off by default; ask your install agent to prepare local audio dependencies, then enable *Speech* |
| **⏰ Set timed reminders** | Judgment model is pre-configured; ask for a reminder and it saves after clarification and review |
| **📒 Group rules & inside jokes** | Add, preview, and save lore entries under *Group knowledge* with zero extra services |

<details>
<summary>🎛️ Preview the Models & Capabilities workbench</summary>

![Models and capabilities in the local panel](docs/readme/capabilities.jpg)

</details>

<details>
<summary>📖 Preview the Group Lore & Knowledge editor</summary>

![Group lore and shared conventions](docs/readme/knowledge.jpg)

</details>

<details>
<summary>🔌 Want different models or another provider?</summary>

Expand *Chat* to choose Zhipu GLM, Gemini, or another compatible endpoint; capabilities on the official OpenAI connection keep sharing your original key. Existing custom models, dedicated providers, and disabled capabilities are preserved.

[Models and services](docs/providers.md) · [Images](docs/image-generation.md) · [QQ voice and audio components](docs/getting-started.md#语音单独开启) · [Media behavior](docs/media-replies.md) · [Reminders](docs/reminders.md)

</details>

---

## Architecture and Design

Beneath the 270+ action desktop dragon-girl and friendly group regular is a compact **Agent Harness** engineered around deterministic state boundaries, prefix-cache economics, and regression-proof self-evolution:

- **⚡ 0.71s Closed-Set Judgment Layer (`Decisions API / Jev`)**: Replaces brittle regex rules and costly full-LLM `SKIP` calls with ultra-cheap (~0.1 USD / 1M tokens) closed-set Decisions API checks for intent triage, back-off detection, memory recall, and reminder screening while the chat model focuses on writing replies; [908 historical triage calls recorded a 0.71-second median latency](docs/judgment-layer.md).
- **🧱 Four-Layer Context Layout for Prefix Caching**: Structured strictly as `Stable Instructions → Daily Memory → Session History → Current-Turn Facts`. Speaker- and turn-specific data always goes last, preserving a stable prefix for [prefix caching](docs/DESIGN_NOTES_EN.md) (75.6%–98% measured hit rates).
- **🔄 Controlled Self-Evolution with Blind Judging & Rule Ablation**: Combines zero-token daily health scans (`regular checkup`), [generation / triage / retrieval / message replays](docs/self-iteration.md), independent blind pairwise judging, and single-rule ablation (`--ablate`) so weak candidates are rejected and obsolete prompt rules get pruned.
- **🛡️ 2,100+ Offline Regression Cases Protect State Boundaries**: The pure-Python desktop pet brain (`pet_brain.py`), physics props (`pet_props.py`), rapport scores, nicknames, promises, and daily quotas live strictly in deterministic local Python code. [CI](.github/workflows/ci.yml) covers Python 3.10 / 3.12 / 3.13 and desktop audio pipelines across Windows and macOS.

Explore the full message flow diagrams, Decisions calibration data, self-iteration walkthroughs, and design notes in **[Architecture and Design (`docs/architecture-en.md`)](docs/architecture-en.md)**.

### Practical Notes

- **Predictable quotas**: Set independent daily call limits per capability in the workbench so banter never burns through your budget.
- **Local-first privacy**: Credentials, state ledgers, and logs stay on your machine under `~/.the-regular` with the workbench bound to `127.0.0.1`. For 24/7 operation, see [persistent operation](docs/getting-started.md#以后如何再打开).
- **Customizable identity**: The original dragon-girl mascot, name, persona, and sprite repertoire can be customized freely ([Visual identity & prompts](docs/visual-identity.md) · [Pet repertoire guide](docs/pet-repertoire.md)).

[Contributing](CONTRIBUTING.md) · [MIT License](LICENSE)

---

<p align="center">
  Whether you want a chibi dragon-girl who flies, drops toys, and keeps you company while coding, or a witty friend for your group chat who knows when to chime in, give her a try—and drop a <b>⭐ Star</b> to help more people meet her!
</p>
