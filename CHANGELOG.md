# Changelog

## 0.6.0 — 2026-11-08

### Highlights

- The desktop pet has a daily life of her own. Activities end the way real ones do:
  she laughs at a comic or slips in a bookmark, wins or loses at her handheld and
  sometimes asks for a rematch, finishes a painting or redoes a stroke, then puts the
  laptop or instrument away. Character scenes appear between quiet stays instead of
  behind a menu: she shares half a cookie, shows off a small flame after a head pat,
  burns her tongue on tea she heated with her own fire, dozes over a book, pulls out
  a folding chair or sofa, or greets you through the "glass".
- She can turn into a round chibi dragon with her own face, hair, horns and scarf. A
  puff of cloud hides the change; she hops, tries to fly and flops, cannot unwrap a
  candy with her paws, curls around her treasure or chases her tail, then puffs back.
  It is a rare natural visit; when she is awake and free, More → 变个魔法 prioritises
  it on the first successful request.
- Her dragon abilities solve small wishes: frosty breath for a warm cola, a tiny
  flame for a marshmallow, warm breath for a damp scarf, a floating brush for the
  tail she cannot reach, gold thread for a torn book corner. Requested portals take
  her to a real toy ball or treasure across the screen. She also uses portals to
  answer a call near a far-away cursor or leave the top edge; a sneeze can still
  send her through one by accident.
- Flight takes off on a diagonal, flaps the whole way, banks with its speed and
  reaches for the floor before touching down. In the air she may breathe a small
  flame or chase a cursor wiggling near her.
- Released at any of the four screen edges she hides her body and peeks in, with
  small moments while hidden: her tail waves first, she listens, yawns or dozes.
  Lift her high and let go, and she sometimes goes weak at the knees after landing
  and hugs her wings for courage: she can fly, but heights still get to her.
- Transparent pixels pass clicks to the window underneath. Her visible body remains
  interactive during ordinary activities; cursor chases pass clicks through as well.
  The window no longer flickers when the pointer crosses her outline.
- Codex and Claude Code hooks (`regular pet-notify`) let her call you when an agent
  finishes a turn; Claude Code permission requests can notify her too.

### Details

- Library: 377 WebP strips / 1,502 frames (37.9 MB), 334 repertoire definitions and
  339 playable items. Strips are decoded on demand with bounded caches.
- New and revised strips are calibrated against the idle head and checked side by
  side; edge-gripping strips scale about the grip so her hand stays on
  the edge. Falls, landings and the berry snack were redrawn where the body, not the
  head, had been drawn too short.
- Character bible in `docs/character.md`: how to recognise her in both forms, her
  traits, which trigger shows which trait, pacing, and the rules for drawing new art.
- Pacing: one-shot scenes hold their key poses (about three seconds per beat) instead
  of freezing on the last frame. Whole scenes stay occasional (at least 180 seconds
  and two quiet stays apart); furniture comes out about every half hour; the dragon
  has at least 25 minutes between automatic appearances, waits through the first
  ten minutes after launch, and stays human with "待在原地" on or low energy.
  These are eligibility gates, not a fixed schedule; manual magic can request the
  dragon sooner. Recent endings are avoided rather than shown more often.
- "换个节目" (the ring's first button, which replaced the high-five game) picks the
  next activity with the same rules as natural selection. It first puts away the
  current laptop, instrument or furniture, once, even under repeated clicks.
- Feeding is one continuous strip: she looks up, takes the cookie, bites and answers;
  a delayed care receipt continues it instead of replaying it. Sharing her own cookie
  or leaving the last one for you is a separate everyday scene.
- Edges: only the head and necessary hands stay visible while hidden, including sleep
  and micro-performances. A click wakes her by reversing the same sleep frames. From
  the top she opens both portals while still peeking, then lets go and turns into
  them. Full-body side sleep is retired.
- Group work is staged in three parts: drawing in QQ conjures a floating canvas that
  she turns round when the picture is delivered; a search opens a floating tome that
  ends on "找到了"; image reading and voice recognition have their own actions.
- Speech bubbles avoid the sprite's actual outline, including lying poses and hair.
- macOS: the pet stays above ordinary windows across Spaces and below the menu bar,
  and portals keep their layer order. Input pass-through is changed in place, so the
  window is never hidden and re-shown.
- Fix: a reshuffled long stay could swap a loop for a one-shot that then froze for
  45–90 seconds. Fix: returning from the dragon no longer invents a candy in her hand.
- Generation prompts for every adopted strip live next to its spec in
  `regular/pet_actions_*.py`; see the dated [performance](docs/pet-performance-2026-11-08.md),
  [flight](docs/pet-air-play-2026-11-08.md), [activity endings](docs/pet-activity-endings-2026-11-08.md),
  [everyday magic](docs/pet-purpose-magic-2026-11-08.md), and
  [hover visibility](docs/pet-hover-visibility-2026-11-08.md) review notes.

## 0.5.1 — 2026-11-07

- Feeding and head pats now change the same temporary local mood in the browser
  and desktop pet, with shared cooldowns, a cap and decay. Local chat receives
  the recent care as read-only facts; QQ relationship scores stay separate.
- Keep long conversations inside their scrolling area. Constrain hidden speaker
  labels, preserve the composer position and fit the character to her scene.
- An official OpenAI key now has maintained chat, vision and judgment defaults.
  The first-run page opens one key field; custom models, disabled capabilities
  and separately configured services retain their choices.
- Put a copyable install prompt before manual download in both READMEs. The
  agent handles downloading, dependencies, desktop startup and verification;
  the user enters credentials locally and completes QQ authorization.

## 0.5.0 — 2026-11-07

### Highlights

- A five-button ring beside the desktop pet brings play, chat, continuous voice,
  activity and settings together. Small reactions, a high-five game and gentler
  posture changes make her feel more at home on the desktop.
- Continuous voice keeps its single mute control and spoken interruptions;
  listening, thinking and speaking now have matching character actions, even
  when QQ disconnects.
- The console follows everyday tasks, with compact model and capability rows,
  a separate shared OpenAI connection, clearer saved states and group knowledge.
- Image feedback can be traced back to its generation. Repeated vague image
  corrections lead to one concrete question, backed by synthetic replay and
  independent blind judging; explicit edits still execute directly.

### Details

- Retire the oversized "stomp / turn away / point accusingly" reaction. It used to
  be reachable from four paths at once (repeated pokes, waking, overfeeding and a
  hard landing). `pout` and `point` are no longer triggered by any interaction,
  ambient choice or panel cue, and `startle` keeps only its two small flinch
  frames. Waking still plays its acted wake/getup sequence; overfeeding, a hard
  landing and a bad mood now use a curious tilt or a small flinch instead.
- Add four new near-static interaction strips (pat, highfive, peek, nod) generated
  with the repo's GPT Image 2.6 chain. Clicking her head, body or feet now gives
  three different small reactions drawn from an eight-gesture shuffled bag, so a
  round never repeats. Seated pets answer in place instead of standing up for a
  small gesture. The shipped-sprite budget follows the four new strips from 3.0 MB
  to 3.5 MB. The ring's "play" button is **not** another gesture draw: it starts a
  multi-tap high-five rally — she keeps raising her hand, the reaction window
  shrinks with the streak, milestones and records are called out, stopping wraps
  up the round, and eight in a row ends it. While a rally runs every click belongs
  to the game.
- Remove the double-click chat shortcut. A double-click used to open the chat bar
  while the rally and ordinary tapping need those same clicks, so the two were
  inherently in conflict. Text chat now has one explicit entry point: the ring's
  "chat" button (plus the browser companion page). Opening chat, voice or feeding
  cancels a running rally without counting it as a loss, and the best streak is
  kept.
- Fix the image flicker: a dissolve used to start on *every* frame change and drew
  both frames at partial opacity, which made the whole figure turn translucent
  (opaque pixels dropped from ~79% to 0% four times during one gesture). Dissolves
  now happen only on posture changes and the slow waking/sleeping performance;
  ordinary animation frames cut crisply. During a dissolve the outgoing frame
  stays opaque underneath and only the incoming one fades in, so overall opacity
  no longer dips.
- Make two console pages show the data the bot has actually collected. 让她更好
  checks the latest available logs before choosing a day; an active day stays
  selected even with zero findings, while a genuinely empty day can fall back to
  an earlier report. Uncomputed dates are marked pending. It also adds a
  recent-days strip and a link back to today. 群史记
  replaces a raw markdown dump and a file dropdown with an index of dated pages
  (summary, topics, best lines) plus a formatted full-page view.
- Repair the browser-script test harness: its `document` stubs lacked
  `addEventListener`, so the outside-click handlers added earlier made four Node
  smoke tests fail.
- Make the activity ("notifications") card and the "More" settings panel transient:
  an app-wide click filter closes them on any outside click, switching away from
  the app closes them too, opening one closes the other, and clicking the same
  button again toggles it shut. The console's floating helper bubble also closes
  on an outside click or Escape.
- Use five buttons (play / chat / voice / activity / more) in a real radial
  layout, with the continuous voice controls accessible beside the character.
- Rework 模型与能力 so a model and its daily quota live in the same place. Every
  row now shows 模型 / 每天 / 每人 chips without opening 「设置」, and the daily
  limits move out of 「更多设置」 into the main form next to the model field
  (chat's daily call budget and the desktop voice round limit included). The
  shared OpenAI connection has its own folding card: saving a key or address
  preserves the selected chat service and model. Its status reflects OpenAI
  credentials rather than any chat key, and explains compatible-service limits.
  Judgment settings distinguish a usable shared key from a saved connection;
  old `/decision` links open the settings directly.
- Fix the capability row layout: the switch stays in the header when settings
  expand, and 状态 joins the model/quota chips under the title. Folding controls
  remain usable on a second click instead of replaying their open animation.
- Keep local pet conversations independent of QQ connection state. Voice
  animations follow listening, thinking, playback and mute; QQ heartbeats cannot
  send an active local conversation to sleep. Real Qt double-click sequences
  count both taps, and hovering pats share the existing warmth cooldown.
- Tell the reply model which media actions this turn can execute. A blocked
  drawing marker produces an honest non-execution message instead of retaining
  its drawing promise. Image deliveries carry generation IDs and descriptions;
  checkups can also trace older image feedback through the original request.
- Clarify a concrete difference after repeated vague image dissatisfaction,
  while executing explicit edits and respecting stop requests. Eight synthetic
  scenarios passed the paired comparison with independent Kimi blind judging;
  the cases are retained as regression seeds.
- Make petting and feeding actually matter. `presence.mood` stays read-only
  (it is the group's statistics); a separate `warmth` layer lifts
  `effective_mood`, which drives ambient choices and walking speed. Head pats
  and biscuits have independent cooldowns (45 s / 90 s), a shared +0.6 cap and
  a ~5-minute decay, and her bubble says whether it worked, is cooling down or
  has hit the cap. Overfeeding and body/feet taps never lift it.

### Console and desktop foundations

- Reframe the console as a status panel instead of a list of backend modules.
  The sidebar has two scenario groups and no catch-all drawer: 我的群友 / 聊两句
  / 让她更好 / 群知识, then 设置 (模型与能力 / QQ 连接与身份). The daily checkup
  and the coding-agent hand-off are one page (让她更好, with a pending badge in
  the sidebar; /iterate lands on its hand-off block); group memory is a tab of
  群知识. The homepage is one status card (state, the one action, first-run
  progress, "需要你看一眼" exceptions) plus today's numbers-only activity.
  Judgment and search stay independent underneath but move into one advanced
  section; their visible module rows are gone. Settings forms and hot switches
  are unchanged.
- Replace the native Qt context menu and the vertical five-button toolbar with
  one compact self-drawn ring (play / chat / voice / activity / more;
  "more" opens beside the ring) and a rounded
  "More" panel. The five "open workbench" submenu shortcuts are removed; size,
  quiet, motion, feed, refresh and quit now live in "More". The continuous
  desktop voice conversation from 0.4.4 is untouched and still uses one button.
- Add an event-driven state layer to the pet, with six new sprite strips:
  reply, listen, inspect, checkup, handoff and stuck. Presence heartbeats keep
  the current state alive, one-shot cues trigger handoff / checkup / stuck, and
  local voice follows its actual listening, thinking, playback and mute stages.
  Seated states use
  the existing posture bridges. A state ends when its heartbeat stops, not on
  a long fake "still busy" timeout.
- Make waking up an acted sequence instead of a pose jump. Two new strips
  (wake, getup): she stirs with one eye open, sits up rubbing an eye, gives a
  heavy-lidded look when poked awake, pulls her knees in, rises on one knee,
  scratches her hair and yawns. Poking her while she nods off seated skips the
  lying frames; poking again while she gets up no longer restarts the sequence.
  Slow and posture-changing frames now dissolve into each other (blinks, walks,
  hops and flinches stay crisp; reduced motion turns it off).
  `tools/pet_sprites.py build --only` rebuilds single strips and keeps strips it
  does not own in `sprites.json`.
- Remove 13 README diagrams no page links to any more and `tools/doctor.py`,
  which `regular doctor` replaced.
- Add `tests/test_design_state_layer.py` covering the state layer, shipped
  strips, navigation folding, and removal of the native menu.
- Share one OpenAI connection where the service endpoints match. Its independent
  console card preserves the selected chat provider when changing the shared
  key; other model services keep their own credentials. Judgment is no
  longer a copied credential: `decision_api.json` stores only endpoint/model
  plus `credential: "shared"`, and `capabilities.key_for_endpoint()` resolves
  the live key per request, so changing the shared key takes effect immediately.
  Vision, images, voice and the desktop companion can share the same key. The key
  is handed out only when the target endpoint matches `OPENAI_BASE_URL`;
  third-party endpoints keep their dedicated-key escape hatch. Old
  `decision_api.json` files with their own key still load as dedicated.
- Add `tests/test_shared_openai_key.py` for dynamic binding, endpoint scoping
  and legacy-file compatibility.

## 0.4.4 — 2026-11-06

- Open desktop voice as a continuous conversation: automatic turn detection,
  spoken interruptions, one mute control and close to end. Keep listening when
  switching apps, and retain the existing session and daily reply limits.
- Add WebRTC AEC3 to the native full-duplex audio stream, using actual speaker
  output as its reference. Keep the playback buffer and actual DAC progress.
- Update one subtitle bubble as text arrives, without restarting the typewriter
  or replaying the final sentence. Ordinary text chat keeps its typing effect.
- Add macOS and Windows CI coverage for the audio wheel and native interface.

## 0.4.3 — 2026-11-06

- Add native desktop push-to-talk using the configured OpenAI realtime speech
  model and voice. Stream microphone PCM and generated audio; interrupt playback
  and truncate unheard speech when starting a new turn. QQ need not be running.
- Add a compact voice card that connects when opened, with one rounded push-to-talk
  button and a close control. Capture only while held; release commits, leaving the
  button or losing focus cancels the turn, and closing ends audio and transport.
  Handle microphone permission and include the macOS bundle usage description.
- Buffer bursty voice packets before playback and use a steadier soundcard latency
  to avoid inserting silence into speech during brief network gaps.
- Keep API keys in the local panel, with CSRF-issued single-use tickets, one active
  voice session and no automatic reconnect. Bound sessions, audio and daily turns
  separately from QQ voice messages; configure the daily limit under Voice service.
- Reuse capability confirmation cards and numeric activity summaries for voice.
  No recording files, QQ message delivery, raw group history or direct model writes.

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
  and anything 小团团 does in QQ wakes her (she used to sleep through a drawing
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
