"""实际浏览器脚本的无依赖交互回归；未装 Node 的环境跳过。"""
from pathlib import Path
import shutil
import subprocess
import unittest


@unittest.skipUnless(shutil.which('node'), 'Node is optional; browser script smoke requires it')
class PanelClientTest(unittest.TestCase):
    def test_static_poses_and_optional_click_feedback_keep_chat_explicit_and_literal(self):
        script = r'''
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
class Element {
  constructor(value = '') { this.value = value; this.dataset = {}; this.attrs = {}; this.events = {}; this.children = []; this.textContent = ''; }
  addEventListener(name, callback) { this.events[name] = callback; }
  setAttribute(name, value) { this.attrs[name] = value; }
  getAttribute(name) { return this.attrs[name]; }
  append(...children) { this.children.push(...children); }
  focus() {}
  set innerHTML(value) { throw new Error('Chat must use textContent, never innerHTML'); }
}
const input = new Element();
const submit = new Element();
const status = new Element();
const refresh = new Element();
const history = new Element();
const counter = new Element();
const portrait = new Element();
portrait.dataset = { staticSrc: '/assets/mascot.png', mascotState: 'idle' };
portrait.setAttribute('src', '/assets/mascot.png');
const motion = new Element();
const form = new Element();
const fields = { '[data-chat-input]': input, '[data-chat-submit]': submit, '[data-chat-count]': counter };
form.querySelector = selector => fields[selector];
form.elements = { namedItem: () => ({value: 'synthetic-csrf'}) };
const nodes = { '[data-chat-form]': form, '[data-chat-status]': status, '[data-chat-refresh]': refresh, '[data-chat-history]': history, '[data-chat-mascot]': portrait };
const preference = new Map();
const reduced = new Element(); reduced.matches = false;
const requests = [];
const timers = [];
let resolveChat;
const document = { body: {dataset: {poseStates: 'blink,thinking,celebrate,sleeping'}}, querySelector: selector => nodes[selector] || null, querySelectorAll: selector => selector === 'img[data-static-src]' ? [portrait] : selector === '[data-actions-toggle]' ? [motion] : [], createElement: () => new Element() };
const context = { document, window: {matchMedia: () => reduced}, localStorage: { getItem: key => preference.get(key), setItem: (key, value) => preference.set(key, value) }, AbortSignal: {timeout: () => ({})}, FormData: class extends Map {}, setTimeout: callback => { timers.push(callback); return timers.length; }, clearTimeout() {}, fetch: (url, options) => { requests.push({url, options}); if (url === '/companion/status') return Promise.resolve({ok:true, json: async () => ({available:true, qq_connected:false})}); return new Promise(resolve => {resolveChat = resolve;}); } };
vm.runInNewContext(fs.readFileSync(process.argv[1], 'utf8'), context);
const drain = () => new Promise(resolve => setImmediate(resolve));
(async () => {
  await drain();
  assert.deepEqual(requests.map(x => x.url), ['/companion/status']);
  assert.equal(portrait.getAttribute('src'), '/assets/mascot.png');
  assert.equal(submit.disabled, true);
  assert.equal(motion.attrs['aria-pressed'], 'true');           // character actions are on by default
  portrait.events.click();
  assert.equal(portrait.getAttribute('src'), '/assets/mascot-blink.png');
  timers.shift()();
  assert.equal(portrait.getAttribute('src'), '/assets/mascot.png');
  motion.events.click();                                          // the owner can turn them off
  assert.equal(preference.get('regular.mascotActions'), 'off');
  assert.equal(motion.attrs['aria-pressed'], 'false');
  portrait.events.click();
  assert.equal(portrait.getAttribute('src'), '/assets/mascot.png');
  assert.equal(timers.length, 0);
  reduced.matches = true; reduced.events.change();
  assert.equal(portrait.getAttribute('src'), '/assets/mascot.png');
  assert.equal(motion.disabled, true);
  assert.deepEqual([...preference.keys()], ['regular.mascotActions']);
  input.value = '<b>你好</b>'; input.events.input();
  assert.equal(submit.disabled, false);
  const first = form.events.submit({preventDefault() {}});
  input.value = 'second attempt';
  await form.events.submit({preventDefault() {}});
  assert.equal(requests.filter(x => x.url === '/companion/chat').length, 1);
  assert.equal(input.readOnly, true);
  assert.equal(portrait.getAttribute('src'), '/assets/mascot-thinking.png');
  assert.equal(submit.disabled, true);
  assert.equal(requests[1].options.body.get('csrf'), 'synthetic-csrf');
  assert.equal(requests[1].options.body.get('text'), '<b>你好</b>');
  const literal = '<img src=x onerror=alert(1)> 只是文字';
  resolveChat({ok:true, json:async () => ({text:literal})});
  await first;
  assert.equal(history.children[0].children[1].textContent, '<b>你好</b>');
  assert.equal(history.children[1].children[1].textContent, literal);
  assert.equal(input.readOnly, false);
  assert.equal(requests.filter(x => x.url === '/companion/chat').length, 1);
  assert.equal(portrait.getAttribute('src'), '/assets/mascot-celebrate.png');
  timers.shift()();
  assert.equal(portrait.getAttribute('src'), '/assets/mascot.png');
  console.log('explicit send, no reentry, literal text, static state poses and optional blink: passed');
})().catch(error => { process.stderr.write(String(error.stack)); process.exitCode = 1; });
'''
        source = Path(__file__).resolve().parents[1] / 'regular/panel.js'
        result = subprocess.run([shutil.which('node'), '-e', script, str(source)], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_sprite_player_animates_reacts_and_follows_agent_presence(self):
        script = r'''
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
class Element {
  constructor() { this.dataset = {}; this.attrs = {}; this.events = {}; this.children = []; this.style = {}; this.classList = { set: new Set(), add: c => this.classList.set.add(c), toggle: (c, on) => on ? this.classList.set.add(c) : this.classList.set.delete(c) }; }
  addEventListener(name, callback) { this.events[name] = callback; }
  setAttribute(name, value) { this.attrs[name] = value; }
  getAttribute(name) { return this.attrs[name]; }
  append(...children) { this.children.push(...children); }
  querySelector() { return this.frame; }
  closest(selector) { return selector === '.room-scene' ? this : null; }
  focus() {}
}
const clips = JSON.parse(process.argv[2]);
const sprite = new Element(); sprite.frame = new Element(); sprite.dataset.clip = 'idle';
const input = new Element(), submit = new Element(), status = new Element(), refresh = new Element(), history = new Element(), counter = new Element(), form = new Element(); input.value = '';
const fields = { '[data-chat-input]': input, '[data-chat-submit]': submit, '[data-chat-count]': counter };
form.querySelector = selector => fields[selector];
form.elements = { namedItem: () => ({value: 'synthetic-csrf'}) };
const nodes = { '[data-chat-form]': form, '[data-chat-status]': status, '[data-chat-refresh]': refresh, '[data-chat-history]': history };
let now = 1000; const frames = []; const intervals = []; const requests = [];
const manifest = {schema: 2, frame: [360, 360], actions: {}};
for (const [name, [strip, seq]] of Object.entries(clips)) manifest.actions[strip] = {file: strip + '.webp', frames: Math.max(manifest.actions[strip]?.frames || 0, Math.max(...seq) + 1, strip === 'idle' ? 3 : 1)};
let presence = {bot: 'ready', events: []};
const respond = body => Promise.resolve({ok: true, json: async () => body});
const document = { visibilityState: 'visible', body: {dataset: {poseStates: '', petClips: JSON.stringify(clips)}}, querySelector: s => nodes[s] || null,
  querySelectorAll: s => s === '[data-pet-sprite]' ? [sprite] : [], createElement: () => new Element() };
const context = { document, window: {matchMedia: () => ({matches: false, addEventListener() {}})}, localStorage: {getItem: () => null, setItem() {}},
  performance: {now: () => now}, requestAnimationFrame: cb => frames.push(cb), setInterval: cb => intervals.push(cb), setTimeout: () => 0, clearTimeout() {},
  AbortSignal: {timeout: () => ({})}, FormData: class extends Map {}, Math,
  fetch: url => { requests.push(url); if (url === '/assets/pet/sprites.json') return respond(manifest);
    if (url === '/companion/status') return respond({available: true}); if (url.startsWith('/companion/presence')) return respond(presence); throw new Error('unexpected ' + url); } };
vm.runInNewContext(fs.readFileSync(process.argv[1], 'utf8'), context);
const drain = () => new Promise(resolve => setImmediate(resolve));
const tick = ms => { now += ms; const pending = frames.splice(0); pending.forEach(cb => cb(now)); };
(async () => {
  await drain(); await drain();
  assert.ok(sprite.classList.set.has('ready'));
  assert.match(sprite.frame.style.backgroundImage, /idle\.webp/);
  const seen = new Set();
  for (let i = 0; i < 400; i++) { tick(33); seen.add(sprite.frame.style.backgroundPosition); }
  assert.ok(seen.size >= 2, 'idle blinks');
  sprite.events.click(); tick(16);
  assert.match(sprite.frame.style.backgroundImage, /startle\.webp/);
  for (let i = 0; i < 60; i++) tick(50);
  assert.match(sprite.frame.style.backgroundImage, /idle\.webp/);
  presence = {bot: 'ready', events: [{kind: 'drew', ts: 5}]};
  intervals[0](); await drain(); await drain(); tick(16);
  assert.match(sprite.frame.style.backgroundImage, /draw\.webp/);
  assert.ok(requests.includes('/companion/presence'));
  presence = {bot: 'offline', events: []};
  for (let i = 0; i < 80; i++) tick(50);
  intervals[0](); await drain(); await drain(); tick(16);
  assert.ok(requests.includes('/companion/presence?since=5'));
  assert.match(sprite.frame.style.backgroundImage, /sleep\.webp/);
  assert.ok(!requests.includes('/companion/chat'));
  console.log('sprite player: passed');
})().catch(error => { process.stderr.write(String(error.stack)); process.exitCode = 1; });
'''
        import json
        from regular.pet_brain import CLIPS
        clips = json.dumps({k: [v[0], v[1], v[2], v[3]] for k, v in CLIPS.items()})
        source = Path(__file__).resolve().parents[1] / 'regular/panel.js'
        result = subprocess.run([shutil.which('node'), '-e', script, str(source), clips],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_provider_drafts_and_knowledge_composer_make_no_requests(self):
        script = r'''
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
class Element {
  constructor(value='') { this.value=value; this.dataset={}; this.events={}; this.attrs={}; this.classList={toggle(){}}; }
  addEventListener(key, callback) { this.events[key]=callback; }
  setAttribute(key,value) { this.attrs[key]=value; }
  replaceChildren(...items) { this.children=items; }
  focus() { this.focused=true; }
}
const provider = new Element('openai');
const fields = {provider, base_url:new Element('https://openai.example/v1'), models:new Element('openai-edited'), fallback_models:new Element('backup-edited'), api_key:new Element('UNSAVED_PRIVATE_KEY')};
const options = ['openai','zhipu'].map(name => {
  const item=new Element();
  item.dataset={providerOption:name,baseUrl:`https://${name}.example/v1`,models:`${name}-primary`,fallbackModels:`${name}-backup`,keyPlaceholder:'local only'};
  return item;
});
const summary = new Element(); const summaryStrong = new Element(); summary.querySelector=()=>summaryStrong;
const modelOptions = new Element(); const providerStatus = new Element();
const setup = new Element(); setup.elements={namedItem:name=>fields[name]};
setup.querySelectorAll=()=>options;
setup.querySelector=key=>({'[data-chat-choice-summary]':summary,'#chat-model-options':modelOptions,'[data-provider-status]':providerStatus})[key];
const title=new Element('奶茶局'), keywords=new Element('奶茶、报名'), body=new Element('周五回复群公告。'), source=new Element('## 原有条目\n关键词：原有\n原有内容。\n'), add=new Element(), status=new Element();
const knowledge=new Element();
knowledge.querySelector=key=>({'[data-knowledge-title]':title,'[data-knowledge-keywords]':keywords,'[data-knowledge-body]':body,'[data-knowledge-source]':source,'[data-knowledge-add]':add,'[data-knowledge-status]':status})[key];
const document={body:{dataset:{}},querySelector:key=>({'[data-setup-form]':setup,'[data-knowledge-form]':knowledge})[key]||null,querySelectorAll:()=>[],createElement:()=>new Element()};
const reduced=new Element(); reduced.matches=false;
const context={document,window:{matchMedia:()=>reduced},localStorage:{getItem:()=>null},setTimeout(){},clearTimeout(){},fetch(){throw new Error('Selection/composition must stay local');}};
vm.runInNewContext(fs.readFileSync(process.argv[1],'utf8'),context);
options[1].events.click();
assert.equal(fields.models.value,'zhipu-primary');
assert.equal(fields.fallback_models.value,'zhipu-backup');
assert.equal(fields.api_key.value,'');
fields.models.value='zhipu-edited'; fields.fallback_models.value='zhipu-b2';
options[0].events.click();
assert.equal(fields.models.value,'openai-edited');
assert.equal(fields.fallback_models.value,'backup-edited');
assert.equal(fields.api_key.value,'UNSAVED_PRIVATE_KEY');
assert.equal(summaryStrong.textContent,'openai · openai-edited');
options[1].events.click(); assert.equal(fields.models.value,'zhipu-edited');
add.events.click();
assert.equal(source.value,'## 原有条目\n关键词：原有\n原有内容。\n\n## 奶茶局\n关键词：奶茶、报名\n周五回复群公告。\n');
assert.match(status.textContent,/尚未保存/);
assert.equal(title.value,'');
title.value='第二条'; body.value='内容\n## 不应偷偷增加新条目';
const before=source.value; add.events.click(); assert.equal(source.value,before);
console.log('provider draft isolation, explicit chat choice, local knowledge composition: passed');
'''
        source = Path(__file__).resolve().parents[1] / 'regular/panel.js'
        result = subprocess.run([shutil.which('node'), '-e', script, str(source)], capture_output=True,
                                text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


@unittest.skipUnless(shutil.which('node'), 'Node is optional; browser script smoke requires it')
class ManagerProposalTest(unittest.TestCase):
    def test_a_proposal_is_a_card_and_only_a_click_saves_the_switch(self):
        script = r'''
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
class Element {
  constructor() { this.value = ''; this.dataset = {}; this.attrs = {}; this.events = {}; this.children = []; this.textContent = ''; this.classList = { add: name => (this.added = name) }; }
  addEventListener(name, callback) { this.events[name] = callback; }
  setAttribute(name, value) { this.attrs[name] = value; }
  getAttribute(name) { return this.attrs[name]; }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  focus() {}
  set innerHTML(value) { throw new Error('Chat must use textContent, never innerHTML'); }
}
const [input, submit, status, refresh, history, counter, form] = Array.from({length: 7}, () => new Element());
const fields = { '[data-chat-input]': input, '[data-chat-submit]': submit, '[data-chat-count]': counter };
form.querySelector = selector => fields[selector];
form.elements = { namedItem: () => ({value: 'synthetic-csrf'}) };
const nodes = { '[data-chat-form]': form, '[data-chat-status]': status, '[data-chat-refresh]': refresh, '[data-chat-history]': history };
const requests = [];
const document = { body: {dataset: {}}, querySelector: selector => nodes[selector] || null, querySelectorAll: () => [], createElement: () => new Element() };
const context = { document, window: {matchMedia: () => ({matches: false, addEventListener() {}}), location: {href: ''}}, localStorage: { getItem() {}, setItem() {} },
  AbortSignal: {timeout: () => ({})}, FormData: class extends Map {}, setTimeout: () => 0, clearTimeout() {},
  fetch: (url, options) => { requests.push({url, options});
    if (url === '/companion/status') return Promise.resolve({ok: true, json: async () => ({available: true})});
    if (url === '/companion/chat') return Promise.resolve({ok: true, json: async () => ({text: '画图先关一下？', action: {kind: 'toggle', flag: 'image', label: '画图', enabled: false}})});
    return Promise.resolve({ok: true, json: async () => ({ok: true, timing: '对新消息生效。'})}); } };
context.window.location = context.window.location; context.location = context.window.location;
vm.runInNewContext(fs.readFileSync(process.argv[1], 'utf8'), context);
const drain = () => new Promise(resolve => setImmediate(resolve));
const find = (node, test) => test(node) ? node : node.children.map(child => find(child, test)).find(Boolean);
(async () => {
  await drain();
  input.value = '把画图关了吧'; input.events.input();
  await form.events.submit({preventDefault() {}});
  const card = history.children.find(child => child.className === 'chat-proposal');
  assert.ok(card, 'the proposal is shown as a card');
  assert.equal(card.children[0].textContent, '关掉「画图」？');
  assert.ok(!requests.some(x => x.url === '/capabilities/toggle'), 'nothing is saved before the click');
  const yes = card.children[1].children[0];
  await yes.events.click();
  const saved = requests.filter(x => x.url === '/capabilities/toggle');
  assert.equal(saved.length, 1);
  assert.equal(saved[0].options.body.get('flag'), 'image');
  assert.equal(saved[0].options.body.get('enabled'), 'false');
  assert.equal(saved[0].options.body.get('csrf'), 'synthetic-csrf');
  assert.match(card.children[0].textContent, /已关掉「画图」/);
  console.log('proposal card, explicit confirm: passed');
})().catch(error => { process.stderr.write(String(error.stack)); process.exitCode = 1; });
'''
        source = Path(__file__).resolve().parents[1] / 'regular/panel.js'
        result = subprocess.run([shutil.which('node'), '-e', script, str(source)], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
