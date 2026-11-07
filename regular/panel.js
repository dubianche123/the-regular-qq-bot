/* Local UI, explicit same-origin chat requests, and optional click feedback. */
"use strict";

const setup = document.querySelector("[data-setup-form]");
if (setup) {
  const options = [...setup.querySelectorAll("[data-provider-option]")];
  const provider = setup.elements.namedItem("provider");
  const choice = setup.querySelector("[data-model-choice]");
  const custom = setup.querySelector("[data-custom-model-field]");
  const input = setup.elements.namedItem("models");
  const fallback = setup.elements.namedItem("fallback_models");
  const fields = ["base_url", "models", "fallback_models", "api_key"];
  const drafts = new Map();
  let fallbackEdited = false;
  let customValue = choice.value === "__custom__" ? input.value : "";
  for (const option of options) {
    const models = JSON.parse(option.dataset.modelOptions || "[]");
    drafts.set(option.dataset.providerOption, {
      base_url: option.dataset.baseUrl,
      models: option.dataset.models,
      fallback_models: option.dataset.fallbackModels || "",
      api_key: "", options: models,
      choice: models.includes(option.dataset.models) ? option.dataset.models : "__custom__",
      customValue: "", fallbackEdited: false,
    });
  }
  function revealCustom() {
    const shown = choice.value === "__custom__";
    custom.hidden = !shown;
    input.required = shown;
  }
  choice.addEventListener("change", () => {
    const draft = drafts.get(provider.value);
    if (choice.value === "__custom__") {
      input.value = customValue;
      revealCustom();
      input.focus();
    } else {
      input.value = choice.value;
      if (!fallbackEdited) fallback.value = draft.options.filter(model => model !== choice.value).join(", ");
      revealCustom();
    }
  });
  input.addEventListener("input", () => { customValue = input.value; });
  fallback.addEventListener("input", () => { fallbackEdited = true; });
  for (const option of options) {
    option.addEventListener("click", () => {
      const selected = option.dataset.providerOption;
      if (provider.value === selected) return;
      const current = drafts.get(provider.value);
      drafts.set(provider.value, {
        ...current, ...Object.fromEntries(fields.map(name => [name, setup.elements.namedItem(name).value])),
        choice: choice.value, customValue, fallbackEdited,
      });
      const draft = drafts.get(selected);
      for (const name of fields) setup.elements.namedItem(name).value = draft[name];
      setup.elements.namedItem("api_key").placeholder = option.dataset.keyPlaceholder;
      choice.replaceChildren(...[...draft.options, "__custom__"].map(value => {
        const item = document.createElement("option");
        item.value = value;
        item.textContent = value === "__custom__" ? "填写其他模型…" : value;
        return item;
      }));
      choice.value = draft.choice;
      customValue = draft.customValue;
      fallbackEdited = draft.fallbackEdited;
      provider.value = selected;
      revealCustom();
      for (const item of options) {
        const active = item === option;
        item.classList.toggle("selected", active);
        item.setAttribute("aria-pressed", String(active));
      }
      setup.querySelector("[data-provider-status]").textContent = "已切换，保存后生效。";
    });
  }
  revealCustom();
}

// Judgment and media reuse the explicit OpenAI connection; choosing another
// chat provider does not move that credential to a different service.

// Tell the desktop pet what is happening right now. A failed signal must never
// break the action the user actually asked for, so this stays best-effort.
async function signalState(action) {
  try {
    const token = await fetch("/companion/status", { credentials: "same-origin" })
      .then(response => response.ok ? response.json() : null);
    if (!token?.csrf) return;
    const body = new FormData();
    body.set("csrf", token.csrf);
    body.set("action", action);
    await fetch("/companion/cue", { method: "POST", body, credentials: "same-origin",
      headers: { Accept: "application/json" } });
  } catch { /* the pet is decorative here */ }
}
if (globalThis.location?.pathname === "/checkup") void signalState("checkup");

// Capabilities: a switch is saved the moment it flips; settings forms wait for their own Save.
const capChildren = { image: "gif", voice_input: "transcription" };
function applyCapabilities(data) {
  for (const [flag, on] of Object.entries(data.flags || {})) {
    const button = document.querySelector(`[data-cap-toggle] input[name="flag"][value="${flag}"]`)?.form.querySelector("button");
    if (!button) continue;
    button.setAttribute("aria-checked", on ? "true" : "false");
    button.value = on ? "false" : "true";
  }
  for (const [parent, child] of Object.entries(capChildren)) {
    const button = document.querySelector(`[data-cap-sub="${child}"] button`);
    if (button && !button.title) button.disabled = !data.flags?.[parent];
  }
  for (const [name, state] of Object.entries(data.states || {})) {
    const item = document.querySelector(`[data-cap="${name}"]`);
    if (!item) continue;
    item.dataset.tone = state.tone;
    // The row now shows model/quota chips; keep the state word short so they fit.
    const label = item.querySelector("[data-cap-state]");
    if (label) label.textContent = ({ ok: "已开启", off: "已关闭", todo: "待补齐" })[state.tone] || state.text;
  }
  const runtime = document.querySelector("[data-cap-runtime]");
  if (runtime && data.timing) runtime.textContent = data.timing;
}
for (const form of document.querySelectorAll("[data-cap-toggle]")) {
  form.addEventListener("submit", async event => {
    event.preventDefault();
    const button = event.submitter || form.querySelector("button");
    if (button.disabled || button.getAttribute("aria-busy") === "true") return;
    const body = new FormData(form);
    body.set("enabled", button.value);
    button.setAttribute("aria-busy", "true");
    try {
      const response = await fetch(form.action, { method: "POST", body, credentials: "same-origin", headers: { Accept: "application/json" } });
      if (!response.ok) throw new Error(String(response.status));
      applyCapabilities(await response.json());
    } catch {
      const item = form.closest("[data-cap]");
      const state = item?.querySelector("[data-cap-state]");
      if (state) state.textContent = "没保存上，刷新后再试";
      void signalState("stuck");
    } finally {
      button.removeAttribute("aria-busy");
    }
  });
}
for (const form of document.querySelectorAll("[data-cap-form]")) {
  form.addEventListener("input", () => {
    form.querySelector("[data-cap-form-status]").textContent = "有未保存的更改。";
  });
}

// A scope switch navigates; editing and previewing remain explicit form actions.
for (const scope of document.querySelectorAll("[data-kb-scope]")) {
  const select = scope.querySelector("select");
  const button = scope.querySelector("button");
  select.addEventListener("change", () => scope.requestSubmit());
  button.hidden = true;
}

const copyButton = document.querySelector("[data-copy-brief]");
if (copyButton) {
  copyButton.addEventListener("click", async () => {
    const brief = document.querySelector("[data-agent-brief]");
    const status = document.querySelector("[data-copy-status]");
    try {
      await navigator.clipboard.writeText(brief.value);
      status.textContent = "已复制。带上你的反馈，一起发给 Agent。";
      void signalState("handoff");
    } catch {
      brief.closest("details").open = true;
      brief.focus();
      brief.select();
      status.textContent = "浏览器未允许自动复制，已选中说明，请手动复制。";
    }
  });
}

// The dock shares the character-to-toolbar hover region, with a keyboard path.
for (const dock of document.querySelectorAll("[data-companion-dock]")) {
  let hideTimer;
  const reveal = () => { clearTimeout(hideTimer); dock.open = true; };
  const dismiss = () => {
    clearTimeout(hideTimer);
    hideTimer = setTimeout(() => {
      if (!dock.matches(":hover") && !dock.contains(document.activeElement)) dock.open = false;
    }, 600);
  };
  dock.addEventListener("pointerenter", reveal);
  dock.addEventListener("pointerleave", dismiss);
  dock.addEventListener("focusin", reveal);
  dock.addEventListener("focusout", dismiss);
}

// A click anywhere else closes the floating helper bubble right away; Escape does too.
// Hover still reveals it, but it no longer lingers after the user moved on to another page.
document.addEventListener("pointerdown", event => {
  for (const dock of document.querySelectorAll("[data-companion-dock][open]")) {
    if (!dock.contains(event.target)) dock.open = false;
  }
}, true);
document.addEventListener("keydown", event => {
  if (event.key !== "Escape") return;
  for (const dock of document.querySelectorAll("[data-companion-dock][open]")) dock.open = false;
  for (const details of document.querySelectorAll("details.cap-more[open]")) details.open = false;
});

// Only one settings row stays open, and clicking outside that row closes it.
// External row triggers are handled by the click handler below; otherwise
// pointerdown would close the row just before click reopens it.
document.addEventListener("pointerdown", event => {
  if (event.target instanceof Element && event.target.closest("[data-open-cap]")) return;
  for (const details of document.querySelectorAll("details.cap-more[open]")) {
    const row = details.closest(".cap-item") || details;
    if (!row.contains(event.target)) details.open = false;
  }
}, true);

// Explicit external row triggers open settings rather than only scrolling to
// a collapsed details element. Their ?edit=... URLs also work without JS.
document.addEventListener("click", event => {
  const trigger = event.target instanceof Element
    ? event.target.closest("[data-open-cap]") : null;
  if (!trigger) return;
  const row = document.getElementById(trigger.dataset.openCap);
  const details = row?.querySelector("details.cap-more");
  if (!details) return;
  event.preventDefault();
  if (details.open) {
    details.open = false;
    trigger.setAttribute("aria-expanded", "false");
    return;
  }
  for (const other of document.querySelectorAll("details.cap-more[open]")) {
    if (other !== details) other.open = false;
  }
  details.open = true;
  trigger.setAttribute("aria-expanded", "true");
  row.classList.add("cap-row-flash");
  setTimeout(() => row.classList.remove("cap-row-flash"), 1400);
  row.scrollIntoView({ behavior: "smooth", block: "center" });
  const focus = details.querySelector("input[type=password], input:not([type=hidden]), select");
  if (focus) setTimeout(() => focus.focus({ preventScroll: true }), 280);
});

// Static poses carry state. Optional click feedback never runs on a loop.
const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
const poseStates = (document.body.dataset.poseStates || "").split(",");
let actionsEnabled = true;
try { actionsEnabled = localStorage.getItem("regular.mascotActions") !== "off"; } catch { /* Private browsing can deny storage. */ }
function poseSource(state) {
  return poseStates.includes(state) ? `/assets/mascot-${state}.png` : "/assets/mascot.png";
}
function updateActionPreference() {
  const enabled = actionsEnabled && !reducedMotion.matches;
  if (!enabled) {
    for (const image of document.querySelectorAll("img[data-static-src]")) {
      if (image.getAttribute("src") !== image.dataset.staticSrc) image.setAttribute("src", image.dataset.staticSrc);
    }
  }
  for (const sprite of sprites) sprite.setMotion(enabled);
  for (const button of document.querySelectorAll("[data-actions-toggle]")) {
    button.setAttribute("aria-pressed", String(enabled));
    button.textContent = reducedMotion.matches ? "动作：遵循系统减弱" : `角色动作：${actionsEnabled ? "开" : "关"}`;
    button.disabled = reducedMotion.matches;
  }
}
// Animated chibi: the same strips and clip table as the native pet (regular/pet_brain.py).
const petClips = (() => { try { return JSON.parse(document.body.dataset.petClips || "{}"); } catch { return {}; } })();
let petManifest;
function loadPetManifest() {
  petManifest ??= fetch("/assets/pet/sprites.json", { credentials: "same-origin" })
    .then(response => (response.ok ? response.json() : null)).catch(() => null);
  return petManifest;
}
class SpritePlayer {
  constructor(root) {
    this.root = root;
    this.frame = root.querySelector(".pet-frame");
    this.base = root.dataset.clip || "idle";
    this.clip = this.base;
    this.started = performance.now();
    this.until = 0;
    this.blinkAt = this.started + 1800;
    this.motion = true;
    this.lastWave = 0;
    loadPetManifest().then(manifest => {
      if (!manifest || !manifest.actions) return;
      this.manifest = manifest;
      root.classList.add("ready");
      this.render(performance.now());
      requestAnimationFrame(now => this.loop(now));
    });
    root.addEventListener("pointerenter", () => {
      if (this.clip === this.base && performance.now() - this.lastWave > 8000) { this.lastWave = performance.now(); this.play("wave"); }
    });
    root.addEventListener("click", () => this.play(this.base === "sleep" ? "wake" : "startle"));
  }
  setMotion(enabled) { this.motion = enabled; this.root.classList.toggle("still", !enabled); if (this.manifest) this.render(performance.now()); }
  rest(clip) { this.base = clip; if (!this.until) this.play(clip, 0); }
  play(clip, seconds) {
    if (!petClips[clip]) return;
    this.clip = clip;
    this.started = performance.now();
    const [, sequence, fps, loop] = petClips[clip];
    this.until = seconds === 0 ? 0 : this.started + 1000 * (seconds ?? (loop ? 4 : sequence.length / fps));
  }
  loop(now) {
    if (this.until && now >= this.until) { this.until = 0; this.clip = this.base; this.started = now; }
    this.render(now);
    requestAnimationFrame(next => this.loop(next));
  }
  render(now) {
    const clip = this.motion ? this.clip : this.base;
    const spec = petClips[clip] || petClips.idle;
    const [strip, sequence, fps, loop] = spec;
    const action = this.manifest.actions[strip];
    if (!action) return;
    let index = Math.floor(Math.max(0, now - this.started) / 1000 * fps);
    index = loop ? index % sequence.length : Math.min(index, sequence.length - 1);
    let frame = this.motion ? sequence[index] : sequence[0];
    if (this.motion && clip === "idle") {
      const t = now - this.blinkAt;
      if (t > 200) this.blinkAt = now + 2400 + Math.random() * 3600;
      else if (t >= 0) frame = t < 50 || t >= 130 ? 1 : 2;
    }
    const key = `${strip}:${frame}`;
    if (key === this.key) return;
    this.key = key;
    this.frame.style.backgroundImage = `url("/assets/pet/${action.file}")`;
    this.frame.style.backgroundSize = `${action.frames * 100}% 100%`;
    this.frame.style.backgroundPosition = `${action.frames > 1 ? (frame / (action.frames - 1)) * 100 : 0}% 0`;
    this.root.dataset.posture = ["sit", "laptop", "read", "tea", "sleep", "doze", "wake", "sit_swing"].includes(clip) ? "low" : "stand";
  }
}
const sprites = [...document.querySelectorAll("[data-pet-sprite]")].map(root => new SpritePlayer(root));

for (const image of document.querySelectorAll("img[data-static-src]")) {
  image.addEventListener("error", () => {
    image.dataset.staticSrc = "/assets/mascot.png";
    if (image.getAttribute("src") !== image.dataset.staticSrc) image.setAttribute("src", image.dataset.staticSrc);
  });
  let lastAction = 0;
  image.addEventListener("click", () => {
    if (!actionsEnabled || reducedMotion.matches || !["idle", "wave"].includes(image.dataset.mascotState)
        || Date.now() - lastAction < 2000) return;
    lastAction = Date.now();
    const original = image.dataset.staticSrc;
    if (poseStates.includes("wave")) {
      // A short authored arm pose, anchored to the original canvas. No rocking.
      const wave = poseSource("wave");
      image.setAttribute("src", wave);
      setTimeout(() => {
        if (image.getAttribute("src") === wave && image.dataset.staticSrc === original) image.setAttribute("src", original);
      }, 320);
      return;
    }
    if (!poseStates.includes("blink")) return;
    const blink = poseSource("blink");
    image.setAttribute("src", blink);
    setTimeout(() => {
      if (image.getAttribute("src") === blink) image.setAttribute("src", original);
    }, 160);
  });
}
for (const button of document.querySelectorAll("[data-actions-toggle]")) {
  button.addEventListener("click", () => {
    actionsEnabled = !actionsEnabled;
    try { localStorage.setItem("regular.mascotActions", actionsEnabled ? "on" : "off"); } catch { /* Memory-only preference still works. */ }
    updateActionPreference();
  });
}
reducedMotion.addEventListener("change", updateActionPreference);
updateActionPreference();

const chatForm = document.querySelector("[data-chat-form]");
if (chatForm) {
  const input = chatForm.querySelector("[data-chat-input]");
  const submit = chatForm.querySelector("[data-chat-submit]");
  const status = document.querySelector("[data-chat-status]");
  const refresh = document.querySelector("[data-chat-refresh]");
  const history = document.querySelector("[data-chat-history]");
  const counter = chatForm.querySelector("[data-chat-count]");
  const portrait = document.querySelector("[data-chat-mascot]");
  const stage = sprites.find(sprite => sprite.root.closest(".room-scene"));
  const sceneBubble = document.querySelector("[data-scene-bubble]");
  const chips = document.querySelector("[data-presence-chips]");
  const moodMeter = document.querySelector("[data-mood-meter]");
  const feed = document.querySelector("[data-presence-feed]");
  const suggestions = document.querySelector("[data-chat-suggestions]");
  let bubbleTimer;
  function say(text, seconds = 4) {
    if (!sceneBubble) return;
    clearTimeout(bubbleTimer);
    sceneBubble.textContent = text;
    sceneBubble.hidden = false;
    bubbleTimer = setTimeout(() => { sceneBubble.hidden = true; }, seconds * 1000);
  }
  let available = false;
  let sending = false;
  let settleTimer;
  const spriteFor = { idle: ["idle", 0], sleeping: ["sleep", 0], thinking: ["think", 0], celebrate: ["talk", 3.2] };

  function setState(state) {
    if (stage) {
      const [clip, seconds] = spriteFor[state] || spriteFor.idle;
      if (seconds) stage.play(clip, seconds); else stage.rest(clip);
      return;
    }
    if (!portrait) return;
    portrait.dataset.mascotState = state;
    portrait.dataset.staticSrc = poseSource(state);
    portrait.setAttribute("src", portrait.dataset.staticSrc);
  }
  // Read-only, content-free agent state: the character mirrors what she is doing in QQ.
  let presenceSince;
  const reactions = { mention: ["phone", 4], named: ["phone", 4], quoted: ["phone", 4], called: ["phone", 4], replying: ["phone", 6], said: ["talk", 3], chimed: ["talk", 3], searching: ["laptop", 300], proactive: ["talk", 3], drawing: ["drawing", 200], drew: ["show_drawing", 2.6], singing: ["sing", 90], voice: ["sing", 1.6], media_end: ["idle"], cue: ["cheer"] };
  const narration = { mention: "群里有人@我，我去看看。", named: "嗯？好像有人在说我。", quoted: "有人引用了我刚才那句。", called: "有人在群里叫我！", said: "（在群里回了一句）", chimed: "（刚在群里插了句嘴）", searching: "我去查查～", proactive: "（在群里开了个话头）", drawing: "群里有人要画，我画画看～", drew: "画好啦，发群里了！", singing: "我去群里唱两句。", voice: "（在群里发了条语音）" };
  const feedText = { mention: "群里有人@了她", named: "群里有人喊了她的名字", quoted: "有人引用了她的话", called: "群里有人叫她", replying: "她在琢磨怎么接话", said: "她在群里回了一句", chimed: "她在群里插了句嘴", searching: "她在帮群里查资料", proactive: "她主动开了个话头", drawing: "她开始给群里画图", drew: "她给群里画了张图", singing: "她在录一段语音", voice: "她在群里发了条语音", media_end: "这次没发出去" };
  function chip(text, tone) {
    const item = document.createElement("li");
    item.className = `chip ${tone || ""}`;
    const dot = document.createElement("span");
    dot.className = "dot";
    item.append(dot, document.createTextNode(text));
    return item;
  }
  function renderPresence(state) {
    if (chips) {
      const items = [chip(state.bot === "ready" ? "在线" : "还没启动", state.bot === "ready" ? "good" : "warn")];
      if (state.bot === "ready") items.push(chip(state.gateway_connected ? "QQ 已连接" : "QQ 未连接", state.gateway_connected ? "good" : "warn"));
      if (state.circadian) items.push(chip(state.circadian, "lilac"));
      if (state.chatter > 0) items.push(chip(`群里 5 分钟 ${state.chatter} 条`));
      chips.replaceChildren(...items);
    }
    if (moodMeter && typeof state.mood === "number") {
      const mood = Math.max(-1, Math.min(1, state.mood));
      moodMeter.querySelector("i").style.left = `${(mood + 1) * 50}%`;
      moodMeter.querySelector("[data-mood-word]").textContent = mood >= 0.3 ? "不错" : mood <= -0.7 ? "烦透了" : mood <= -0.3 ? "有点烦" : "平稳";
    }
  }
  function remember(event) {
    const text = event.kind === "cue" && typeof event.text === "string" && event.text ? `Agent：${event.text}` : feedText[event.kind];
    if (!feed || !text) return;
    feed.querySelector(".empty")?.remove();
    const item = document.createElement("li");
    const time = document.createElement("time");
    const stamp = new Date(event.ts * 1000);
    time.textContent = `${String(stamp.getHours()).padStart(2, "0")}:${String(stamp.getMinutes()).padStart(2, "0")}`;
    item.append(time, document.createTextNode(text));
    feed.prepend(item);
    while (feed.children.length > 5) feed.lastElementChild.remove();
  }
  let mediaUntil = 0;
  async function pollPresence() {
    if (document.visibilityState !== "visible" || sending) return;
    try {
      const query = presenceSince === undefined ? "" : `?since=${presenceSince}`;
      const response = await fetch(`/companion/presence${query}`, { credentials: "same-origin", signal: AbortSignal.timeout(4000) });
      if (!response.ok) return;
      const state = await response.json();
      renderPresence(state);
      for (const event of state.events || []) {
        if (typeof event.ts !== "number" || event.ts <= (presenceSince ?? 0)) continue;
        presenceSince = event.ts;
        remember(event);
        // While 小王 is drawing or recording in QQ, her other lines don't take the sketchbook away.
        if (event.kind === "drawing" || event.kind === "singing") mediaUntil = performance.now() + 1000 * reactions[event.kind][1];
        else if (["drew", "voice", "media_end"].includes(event.kind)) mediaUntil = 0;
        if (performance.now() < mediaUntil && ["mention", "named", "quoted", "called", "replying", "said", "chimed", "proactive"].includes(event.kind)) continue;
        const [clip, seconds] = reactions[event.kind] || [];
        if (clip && stage) stage.play(event.kind === "cue" && petClips[event.action] ? event.action : clip, seconds);
        const line = event.kind === "cue" ? event.text : narration[event.kind];
        if (line) say(line);
      }
      if (stage && !sending) stage.rest(state.bot === "ready" ? "idle" : "sleep");
    } catch { /* The next poll tries again; nothing is sent. */ }
  }
  if (stage) setInterval(pollPresence, 5000);
  const roomLines = { shy: ["嘿嘿。", "再摸一下也行。"], eat: ["好吃！", "谢谢投喂～"], wave: ["嗨～", "我在这儿哦。"] };
  for (const button of document.querySelectorAll("[data-room-action]")) {
    button.addEventListener("click", () => {
      const clip = button.dataset.roomAction;
      if (stage) stage.play(clip);
      const lines = roomLines[clip] || [];
      if (lines.length) say(lines[Math.floor(Math.random() * lines.length)], 2.6);
    });
  }
  for (const button of document.querySelectorAll("[data-suggestion]")) {
    button.addEventListener("click", () => { input.value = button.textContent; syncComposer(); input.focus(); });
  }
  function syncComposer() {
    counter.textContent = `${input.value.length} / 2000`;
    submit.disabled = sending || !available || !input.value.trim() || input.value.length > 2000;
    submit.textContent = sending ? "在听…" : "发送 ↑";
    input.readOnly = sending;
  }
  function appendMessage(kind, label, text) {
    const message = document.createElement("div");
    message.className = `chat-message ${kind}`;
    const speaker = document.createElement("span");
    speaker.className = "chat-speaker";
    speaker.textContent = label;
    const content = document.createElement("p");
    content.textContent = text;
    message.append(speaker, content);
    history.append(message);
    history.scrollTop = history.scrollHeight;
  }
  async function refreshStatus() {
    refresh.disabled = true;
    try {
      const response = await fetch("/companion/status", { credentials: "same-origin", signal: AbortSignal.timeout(5000) });
      if (!response.ok) throw new Error("暂时连不上，请刷新页面。");
      const result = await response.json();
      available = result.available === true;
      status.textContent = available ? `可以聊天${result.qq_connected ? " · QQ 已连接" : " · QQ 暂未连接"}` : "还没连上，先去首页看看。";
      if (stage) pollPresence();             // the bot's own state decides her resting pose
      else if (!available) setState("sleeping");
      else if (!sending) setState("idle");
    } catch {
      available = false;
      status.textContent = "暂时连不上，去首页看看运行状态。";
      if (!stage) setState("sleeping");
    } finally {
      refresh.disabled = false;
      syncComposer();
    }
  }
  input.addEventListener("input", syncComposer);
  input.addEventListener("keydown", event => {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      if (!submit.disabled) chatForm.requestSubmit?.();
    }
  });
  // The manager may propose one thing; nothing changes until the maintainer confirms it here.
  function proposeAction(action) {
    const card = document.createElement("div");
    card.className = "chat-proposal";
    const words = document.createElement("p");
    const yes = document.createElement("button");
    const no = document.createElement("button");
    yes.type = no.type = "button";
    no.className = "ghost";
    no.textContent = "算了";
    if (action.kind === "toggle") {
      words.textContent = `${action.enabled ? "打开" : "关掉"}「${action.label}」？`;
      yes.textContent = action.enabled ? "打开" : "关掉";
    } else {
      words.textContent = "这件事需要 Agent 帮忙，打开交接页？";
      yes.textContent = "打开交接页";
    }
    const done = text => { card.replaceChildren(Object.assign(document.createElement("p"), { textContent: text })); card.classList.add("settled"); };
    no.addEventListener("click", () => done("好，先不动。"));
    yes.addEventListener("click", async () => {
      if (action.kind !== "toggle") { window.location.href = "/iterate"; return; }
      yes.disabled = no.disabled = true;
      try {
        const body = new FormData();
        body.set("csrf", chatForm.elements.namedItem("csrf").value);
        body.set("flag", action.flag);
        body.set("enabled", action.enabled ? "true" : "false");
        const response = await fetch("/capabilities/toggle", { method: "POST", body, credentials: "same-origin", headers: { Accept: "application/json" } });
        if (!response.ok) throw new Error();
        const data = await response.json();
        done(`已保存「${action.label}」${action.enabled ? "开启" : "关闭"}。${data.timing || ""}`);
      } catch {
        done("没保存上，可以去「模型与能力」里再试。");
      }
    });
    const row = document.createElement("div");
    row.append(yes, no);
    card.append(words, row);
    history.append(card);
    history.scrollTop = history.scrollHeight;
  }
  refresh.addEventListener("click", refreshStatus);
  chatForm.addEventListener("submit", async event => {
    event.preventDefault();
    const text = input.value.trim();
    if (sending || !available || !text || text.length > 2000) return;
    sending = true;
    clearTimeout(settleTimer);
    input.value = "";
    syncComposer();
    setState("thinking");
    appendMessage("user", "你", text);
    if (suggestions) suggestions.hidden = true;
    status.textContent = "正在听你说…";
    try {
      const form = new FormData();
      form.set("csrf", chatForm.elements.namedItem("csrf").value);
      form.set("text", text);
      const response = await fetch("/companion/chat", { method: "POST", body: form, credentials: "same-origin", signal: AbortSignal.timeout(85000) });
      let result;
      try { result = await response.json(); }
      catch { throw new Error("页面授权或连接已失效，请刷新后再试；本页没有自动重试。"); }
      if (!response.ok) throw new Error(result.error || "这一句暂时没有收到回复。");
      if (typeof result.text !== "string" || !result.text.trim()) throw new Error("没有收到有效回复；本页没有自动重试。");
      appendMessage("assistant", "老群友", result.text);
      if (result.action) proposeAction(result.action);
      say(result.text.length > 60 ? `${result.text.slice(0, 58)}…` : result.text, 6);
      status.textContent = "我在。还想聊点什么？";
      setState("celebrate");
      settleTimer = setTimeout(() => setState("idle"), 2600);
    } catch (error) {
      const message = error.name === "TimeoutError" || error.name === "AbortError" ? "等待回复超时，结果尚未确认；本页没有自动重试。" : (error.message || "连接中断，结果尚未确认；本页没有自动重试。");
      appendMessage("error", "提示", message);
      status.textContent = "这句没接上，去首页看看。";
      setState("idle");
    } finally {
      sending = false;
      syncComposer();
      input.focus();
    }
  });
  syncComposer();
  refreshStatus(); // One local, read-only check; presence polling above only reads. No automatic chat.
}
