/* Local UI, explicit same-origin chat requests, and optional click feedback. */
"use strict";

const setup = document.querySelector("[data-setup-form]");
if (setup) {
  const options = [...setup.querySelectorAll("[data-provider-option]")];
  const drafts = new Map();
  const provider = setup.elements.namedItem("provider");
  const fields = ["base_url", "models", "fallback_models", "api_key"];
  const summary = setup.querySelector("[data-chat-choice-summary]");
  const modelOptions = setup.querySelector("#chat-model-options");
  function describeChoice() {
    const model = setup.elements.namedItem("models").value.trim();
    summary.querySelector("strong").textContent = `${provider.value} · ${model || "还没选模型"}`;
  }
  for (const option of options) {
    drafts.set(option.dataset.providerOption, {
      base_url: option.dataset.baseUrl,
      models: option.dataset.models,
      fallback_models: option.dataset.fallbackModels || "",
      api_key: "",
    });
    option.addEventListener("click", () => {
      const selected = option.dataset.providerOption;
      if (provider.value === selected) return;
      drafts.set(provider.value, Object.fromEntries(fields.map(name => [name, setup.elements.namedItem(name).value])));
      const draft = drafts.get(selected);
      for (const name of fields) setup.elements.namedItem(name).value = draft[name];
      setup.elements.namedItem("api_key").placeholder = option.dataset.keyPlaceholder;
      provider.value = selected;
      for (const item of options) {
        const active = item === option;
        item.classList.toggle("selected", active);
        item.setAttribute("aria-pressed", String(active));
      }
      setup.querySelector("[data-provider-status]").textContent = "已切换服务，其他输入已保留；尚未保存。";
      modelOptions.replaceChildren(...[draft.models, ...draft.fallback_models.split(",")].filter(Boolean).map(value => {
        const choice = document.createElement("option");
        choice.value = value.trim();
        return choice;
      }));
      describeChoice();
    });
  }
  setup.elements.namedItem("models").addEventListener("input", describeChoice);
}

// Compose a Markdown entry locally; it remains a draft until explicit Save.
const knowledgeForm = document.querySelector("[data-knowledge-form]");
if (knowledgeForm) {
  const title = knowledgeForm.querySelector("[data-knowledge-title]");
  const keywords = knowledgeForm.querySelector("[data-knowledge-keywords]");
  const body = knowledgeForm.querySelector("[data-knowledge-body]");
  const source = knowledgeForm.querySelector("[data-knowledge-source]");
  const status = knowledgeForm.querySelector("[data-knowledge-status]");
  knowledgeForm.querySelector("[data-knowledge-add]").addEventListener("click", () => {
    const heading = title.value.trim();
    const words = keywords.value.trim();
    const content = body.value.trim();
    if (!heading || !content || /[\r\n]/.test(heading + words) || /(^|\n)##\s/.test(content)) {
      status.textContent = "请写好单行标题和具体内容；多条内容请分别加入。";
      return;
    }
    const entry = `## ${heading}\n${words ? `关键词：${words}\n` : ""}${content}\n`;
    source.value = `${source.value.trimEnd()}${source.value.trim() ? "\n\n" : ""}${entry}`;
    title.value = keywords.value = body.value = "";
    status.textContent = "已加入草稿，尚未保存。请核对后点击「保存这份知识」。";
    source.focus();
  });
}

const copyButton = document.querySelector("[data-copy-brief]");
if (copyButton) {
  copyButton.addEventListener("click", async () => {
    const brief = document.querySelector("[data-agent-brief]");
    const status = document.querySelector("[data-copy-status]");
    try {
      await navigator.clipboard.writeText(brief.value);
      status.textContent = "已复制。带上你的反馈，一起发给 Agent。";
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

// Static poses carry state. Optional click feedback never runs on a loop.
const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
const poseStates = (document.body.dataset.poseStates || "").split(",");
let actionsEnabled = false;
try { actionsEnabled = localStorage.getItem("regular.mascotActions") === "on"; } catch { /* Private browsing can deny storage. */ }
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
  for (const button of document.querySelectorAll("[data-actions-toggle]")) {
    button.setAttribute("aria-pressed", String(enabled));
    button.textContent = reducedMotion.matches ? "动作：遵循系统减弱" : `角色动作：${actionsEnabled ? "开" : "关"}`;
    button.disabled = reducedMotion.matches;
  }
}
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
  let available = false;
  let sending = false;
  let settleTimer;

  function setState(state) {
    if (!portrait) return;
    portrait.dataset.mascotState = state;
    portrait.dataset.staticSrc = poseSource(state);
    portrait.setAttribute("src", portrait.dataset.staticSrc);
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
      if (!response.ok) throw new Error("无法读取本机连接，请刷新页面。");
      const result = await response.json();
      available = result.available === true;
      status.textContent = available ? `本机聊天已就绪${result.qq_connected ? " · QQ 已连接" : " · QQ 暂未连接"}` : `${result.reason || "机器人尚未提供本机聊天。"} 请由你更新并启动，页面不会自动操作。`;
      if (!available) setState("sleeping");
      else if (!sending) setState("idle");
    } catch {
      available = false;
      status.textContent = "本机连接暂不可用。请检查机器人运行状态，再手动刷新。";
      setState("sleeping");
    } finally {
      refresh.disabled = false;
      syncComposer();
    }
  }
  input.addEventListener("input", syncComposer);
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
      status.textContent = "我在。还想聊点什么？";
      setState("celebrate");
      settleTimer = setTimeout(() => setState("idle"), 2600);
    } catch (error) {
      const message = error.name === "TimeoutError" || error.name === "AbortError" ? "等待回复超时，结果尚未确认；本页没有自动重试。" : (error.message || "连接中断，结果尚未确认；本页没有自动重试。");
      appendMessage("error", "本机小窗", message);
      status.textContent = "这一句暂时没接上，可以先看看运行状态。";
      setState("idle");
    } finally {
      sending = false;
      syncComposer();
      input.focus();
    }
  });
  syncComposer();
  refreshStatus(); // One local, read-only check. There is no polling or automatic chat.
}
