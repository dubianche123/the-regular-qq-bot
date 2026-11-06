# Changelog

## 0.4.2 — 2026-11-06

- Separate settings into QQ identity and Models & capabilities. Show saved profiles
  and compact capability rows; reveal credentials and fields only when editing.
- Replace the chat model autocomplete with a real saved-model selector and custom
  entry. Preserve provider drafts and the fallback chain when promoting a model.
- Configure judgment beside chat, with explicit same-service credential reuse and
  blank-key preservation. Keep QQ identity changes isolated from model settings.
- Rebuild group knowledge around scoped entries, editing, preview, save and delete
  confirmation. Preserve unparsed source and reject stale previews before writing.
- Remove diagnostic pages from primary navigation and reduce developer-facing copy
  across the workspace and pet. Show readable group names and keep the companion
  dock compact so it does not cover settings.

## 0.4.1 — 2026-11-06

- Keep the overview available before setup, with one connection action and a
  first-run progress row that disappears when complete. Condense checkup findings
  into expandable scenes and the Agent handoff into one copy action.
- Keep native manager confirmation cards beside the pet until dismissed; serialize
  pending changes and report whether a saved switch reaches the current bot.
- Add a capabilities console with independent vision, image/GIF, incoming voice,
  transcription and speech controls. Report loaded models and whether the live
  process supports immediate switches; distinguish settings from actual readiness.
- Make speech model and voice configurable through the real synthesis path,
  use separate audio credentials, and restrict inherited image keys to the same API.
- Fix the clipped console avatar and share its icon with a real macOS application
  bundle named 老群友. Source launchers prepare and cache the local bundle; ordinary
  CLI/server installs keep the optional builder out of their requirements.
- Stop walking immediately when motion is disabled, and require a live matching
  process before desktop presence claims the QQ bot is online.

- Rebuild the desktop pet as a living chibi companion: 24 GPT Image 2.6 Flare
  sprite strips (walk, sit, tea, read, laptop, phone, sleep, dangle, fall, draw,
  sing, pout, shy, cheer, eat…) sliced and anchored by `tools/pet_sprites.py`,
  with blink via an eye-only masked edit.
- Add a pure, tested behaviour brain (`regular/pet_brain.py`): autonomous
  activities, posture transitions, gravity and landing, a drag pendulum,
  petting, poking, feeding and procedural breathing, squash and particles.
- Make the pet agent-first: read-only `/companion/presence` maps the running
  bot's status, persisted mood, circadian label and interaction log into
  content-free signals (mentioned, replying, said, drew, voice, chatter).
  `regular pet-cue` lets a coding agent make her act or say one line.
- Replace the chat/notification dialogs with a speech bubble, an input pill and
  an activity card; move the hover toolbar beside her and add feeding.
- Redesign the web “聊两句” page as her room: an animated scene with narration,
  status chips, a mood meter, a recent-activity feed and local-only pat/feed/wave
  buttons, beside a chat panel with suggestions and Enter to send. The dock uses
  the same strips and clip table; character actions are on by default and follow
  reduced motion.
- Pokes react on release with a squish and a small jolt, then escalate
  (startle, point, pout); a seated pet flinches in place.
- The bot logs a content-free `media` interaction row when it starts drawing or
  recording a voice reply and when one does not land, and delivery rows keep
  their `medium`. The pet now draws for the whole image generation and sings
  while a voice is recorded, instead of reacting only after delivery; voice
  deliveries were previously indistinguishable from text in the log.
- Fix “look it up, then draw”: a reply that follows a lookup (inline or in the
  background follow-up) may now draw, re-checking the quota when the answer is
  ready; previously its drawing marker was dropped and only the line promising
  a picture was sent. Lookup turns still do not use voice.
- The pet sits at her laptop while a lookup runs in the background (a
  content-free `lookup` row), and only says she “chimed in” for unprompted
  banter, not for a direct reply.
- The pet no longer picks a nap within three minutes of her own group activity,
  and anything 小王 does in QQ wakes her (she used to sleep through a drawing
  delivered right after an afternoon nap began).
- Redesign “能力与服务” into a one-screen list: shared services on top, one row
  per capability with a plain-language state and a switch that saves at once
  (only that flag); settings and an “advanced” fold open per row; one voice
  key shared by hearing and speaking. Section saves no longer flip switches.
- Reorder the sidebar by frequency of use; the one-time setup becomes
  “设置 → QQ 与模型”, reached from the overview while unfinished. The pet's
  toolbar opens the overview.
- The local chat becomes her manager: she may propose one switch change or a
  hand-off to a coding agent (fixed markers), shown as a confirmation card in
  the web chat and beside the desktop pet; only a click executes it. Each local
  turn carries the current switches and today's numbers-only activity summary.
  `regular manage` gives coding agents the same view and switches as JSON.
- The incoming log row records how she was called (`call`: at / name / quote);
  the pet says different things for an @, her name and a quoted line instead of
  claiming every name-call was an @.
- A pet flung upwards bonks the top of the screen and falls back instead of
  flying out of sight; the last typed characters of a speech bubble are painted
  (a feeding line stopped at “谢谢投”).
- Drop a reply's last line when the bookkeeping tag slot holds only a broken
  `<…>` fragment (gpt-6-luna sent `<отов>` to the group instead of
  `<CMD>{"aff": 0}</CMD>`).
- Exit the pet with `QApplication.exit()` so the launcher can stop the panel it
  started (macOS `quit()` terminated the process first). Remove the superseded
  pet keyframes and `pet-actions.json`.

## 0.4.0 — 2026-11-06

- Open the desktop companion first from the source launchers and the default CLI.
  Reveal chat, notifications and settings on proximity; keep the transparent
  character visible when switching applications. Reuse an authenticated local
  panel or start a bounded, local-only panel service without starting QQ.
- Preserve the original mascot with anchored PNG action frames and optional
  reduced motion. Add adjustable size and remembered local preferences; use
  transparent rounded controls and distinct setup, library and checkup art.
- Add explicit local text chat through the existing bot's event loop, session
  handling and call budget, with separate local history and no QQ delivery.
- Rewrite the bilingual README around everyday use and setup; preserve the
  technical README in architecture documents and add a copyable Agent install prompt.
- Make the selected chat service authoritative over an old CHAT_PROVIDER; show
  primary, fallback and running-versus-saved model choices. Add a knowledge entry
  browser and structured drafts with explicit preview and save.
- Make the voice-output process lock portable to Windows while retaining the
  POSIX lock and cancellation cleanup.

## 0.3.0 — 2026-11-05

- Make the source package a guided starting point: Windows, macOS and Linux
  launchers prepare a local environment and open the browser panel. Keep private
  instance data separate from the source workspace.
- Add browser configuration for QQ, the chat provider and a daily call limit.
  Preserve blank passwords, show environment overrides, and save without model
  requests. Explicitly start a bot from the panel and stop only its owned child;
  report connection readiness from the matching live process.
- Introduce an Agent handoff page: review findings, download a task brief, open
  the source workspace, and follow the bundled iteration skill. Recorded scenes,
  regression cases, bounded replay and independent judging support changes that
  the maintainer chooses to release.
- Give proactive speech a bounded chronological scene with people, relative
  times, image evidence and the bot's actual delivery outcomes. Persist recent
  outcomes and proactive cooldowns across restarts. Quote the selected older
  message, with a visible text reference when a native reference is unavailable.
- Distinguish delivered, failed and uncertain media outcomes; an uncertain voice
  receipt no longer becomes a successful voice delivery in conversation history.
- Include the intervening replay, checkup/recall strategy evaluation, reminder
  review and cancellation, precise reminder mentions, and still/GIF support.
  Proactive image/voice selection remains a separate follow-up.

## 0.2.0 — 2026-10-20

- Publish installable wheel and source archives in the GitHub Release. End users
  can install with uv, create a local instance and run without cloning source.
- Expose the existing launchd/systemd generator as `regular service` for installed
  packages, preserving the same resolved paths and installed Python environment.

- Rename the GitHub repository to `the-regular-qq-bot` for clearer QQ bot discovery;
  update source links while keeping the `regular` command and `the-regular` package.

- Make first launch actionable: stop rejected QQ authentication, bound failures
  before first login, retain recovery after login, and confirm readiness only after
  gateway authentication. Keep rejected token payloads out of error summaries.
- Validate required setup input immediately and treat optional aliases as optional.

- Use one bounded provider request policy and explicit OpenAI-compatible endpoints/model chains; keep vendor presets as optional setup shortcuts. Image reading inherits the main provider and first chat model by default, with optional independent provider/model overrides.
- Add installable `regular` CLI: offline demo, private configuration initialization,
  guided setup that fills missing settings and runs local checks, explicit metadata
  checks, foreground run and structured status. Keep configured values and hidden
  secret input; show actionable next steps for the selected instance directory.
- Generate portable macOS/Linux service definitions. No automatic process killing,
  power-policy changes or service startup. Add a single-container Compose path.
- Default to TLS verification. Silently save the first nonempty C2C sender as
  owner, with no claim phrase or extra notification; handle the message normally.
  Group messages cannot claim. Give new groups 10 minutes before appending one
  ownership reminder to a normal chat reply, marking it shown only after delivery.
  Keep cross-endpoint fallback explicitly opt-in.
- Keep runtime state, owner identity, logs, archives and summaries in `data/` by
  default, with an explicit deployment directory and data-path overrides.
- Commit actual delivered text after a positive QQ receipt; reuse the same sequence
  on retries and supersede unconfirmed older turns after a new turn or reset.
- Persist source-linked image descriptions and share in-flight/cache work across
  foreground and background paths. All physical model attempts use one daily budget.
- Advance digest archive offsets only for the processed prefix, preserving late and
  equal-timestamp arrivals; reject incomplete or invalid compression results.
- Make idle relation decay idempotent, restore persona/milestone fields, and keep
  stable promise identities and nag counts across renames.
- Record real background-task and gateway transitions; drain work before shutdown.
- Align configuration examples and bilingual onboarding; add offline CI, packaging,
  contributor guidance and issue forms. Runtime correctness fixes are described in
  the associated change review and regression tests.

Install the wheel or download the source archive from the
[v0.2.0 GitHub Release](https://github.com/dubianche123/the-regular-qq-bot/releases/tag/v0.2.0).
See the [provider configuration guide](docs/providers.md) for endpoint and image-model setup.
