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
  portrait.events.click();
  assert.equal(portrait.getAttribute('src'), '/assets/mascot.png');
  assert.equal(timers.length, 0);
  motion.events.click();
  assert.equal(portrait.getAttribute('src'), '/assets/mascot.png');
  portrait.events.click();
  assert.equal(portrait.getAttribute('src'), '/assets/mascot-blink.png');
  timers.shift()();
  assert.equal(portrait.getAttribute('src'), '/assets/mascot.png');
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
