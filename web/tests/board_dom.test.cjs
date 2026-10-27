/* Run the real board script against a minimal DOM. Only the board transitions are asserted. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const root = path.resolve(__dirname, '..');

class Element {
  constructor(tag, doc) { this.tagName = tag.toUpperCase(); this.ownerDocument = doc; this.children = []; this.parentElement = null;
    this.dataset = {}; this.attributes = {}; this.listeners = {}; this.className = ''; this.id = ''; this.value = ''; this.open = false; this.text = ''; }
  get firstChild() { return this.children[0] || null; }
  get textContent() { return this.children.length ? this.children.map(c => c.textContent).join('') : this.text; }
  set textContent(value) { this.replaceChildren(); this.text = String(value); }
  detach() { if (this.contains(this.ownerDocument.activeElement)) this.ownerDocument.activeElement = this.ownerDocument.body; if (this.parentElement) { const list = this.parentElement.children; list.splice(list.indexOf(this), 1); this.parentElement = null; } }
  insert(node, index) { node = typeof node === 'string' ? this.ownerDocument.createTextNode(node) : node; node.detach(); node.parentElement = this; this.children.splice(index, 0, node); }
  append(...nodes) { for (const n of nodes) this.insert(n, this.children.length); }
  replaceChildren(...nodes) { for (const c of this.children) c.parentElement = null; this.children = []; this.append(...nodes); }
  insertBefore(node, ref) { this.insert(node, ref ? this.children.indexOf(ref) : this.children.length); }
  replaceWith(node) { const parent = this.parentElement, index = parent.children.indexOf(this); this.detach(); parent.insert(node, index); }
  remove() { this.detach(); }
  contains(node) { for (let n = node; n; n = n.parentElement) if (n === this) return true; return false; }
  closest(selector) { for (let n = this; n; n = n.parentElement) if (n.matches(selector)) return n; return null; }
  matches(selector) { const m = selector.match(/^([a-z]*)((?:\.[\w-]+)*)$/); if (!m) throw new Error('Unsupported selector ' + selector);
    const classes = (m[2] || '').split('.').filter(Boolean); const own = this.className.split(/\s+/);
    return (!m[1] || this.tagName === m[1].toUpperCase()) && classes.every(c => own.includes(c)); }
  querySelectorAll(selector) {
    const direct = selector.startsWith(':scope > '), target = direct ? selector.slice(10) : selector, out = [];
    const walk = (node) => { for (const c of node.children) { if (c.matches(target) && (!direct || c.parentElement === this)) out.push(c); walk(c); } };
    walk(this); return out;
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
  setAttribute(key, value) { this.attributes[key] = String(value); }
  getAttribute(key) { return this.attributes[key] ?? null; }
  focus() { this.ownerDocument.activeElement = this; }
}

function makeDom() {
  const doc = { byId: new Map(), activeElement: null, hidden: false, documentElement: new Element('html', null) };
  doc.documentElement.ownerDocument = doc;
  doc.body = new Element('body', doc); doc.activeElement = doc.body;
  doc.createElement = (tag) => new Element(tag, doc);
  doc.createTextNode = (text) => { const n = new Element('#text', doc); n.text = String(text); return n; };
  doc.getElementById = (id) => { if (!doc.byId.has(id)) { const el = new Element('div', doc); el.id = id; doc.byId.set(id, el); } return doc.byId.get(id); };
  doc.querySelectorAll = () => [];
  doc.addEventListener = () => {};
  return doc;
}

function card(key, summary) {
  return { key, title: 'Synthetic task ' + key, bucket: 'underway', home: '/home/example/space', task: key, owner: 'Coordinator', answered: false,
    summary, context: { reason: '', phase: 'active', firstmate_action: null }, dependencies: [], presentation: null, supervisor: null, time: null };
}

function board(underway) {
  const columns = [['charted', []], ['underway', underway], ['captains_call', []], ['landed', []]].map(([id, cards]) => ({ id, title: id, cards }));
  return { homes: [{ id: '/home/example/space', label: 'example' }], columns, sources: [], source_ttl: 60, error: null, loading: false, csrf: 'synthetic' };
}

async function mountBoard(initial) {
  const doc = makeDom(), timers = [];
  let current = initial;
  const fetch = async (url) => {
    if (url.startsWith('api/locale')) return { ok: true, json: async () => ({ locale: 'en', messages: {} }) };
    if (url.startsWith('api/board')) return { ok: true, json: async () => JSON.parse(JSON.stringify(current)) };
    return { ok: true, json: async () => ({ receipts: [] }) };
  };
  const context = vm.createContext({ document: doc, fetch, navigator: {}, location: { href: 'http://localhost/', search: '' },
    matchMedia: () => ({ matches: false, addEventListener() {} }), setInterval: (fn) => timers.push(fn), setTimeout, clearTimeout,
    AbortController, URL, URLSearchParams, Intl, Date, JSON, Map, Set, Promise, Option: function Option(text, value) { return Object.assign(new Element('option', doc), { text, value }); },
    sessionStorage: { getItem() { return null; }, setItem() {} } });
  context.window = context;
  context.addEventListener = () => {};
  vm.runInContext(fs.readFileSync(path.join(root, 'static/i18n.js'), 'utf8') + '\nthis.DeckI18n = DeckI18n;', context);
  vm.runInContext(fs.readFileSync(path.join(root, 'static/app.js'), 'utf8'), context);
  const flush = async () => { for (let i = 0; i < 20; i++) await new Promise(resolve => setImmediate(resolve)); };
  await flush();
  return { doc, flush, poll: timers[0], setBoard(next) { current = next; } };
}

const underwayList = (doc) => doc.getElementById('board').children.find(s => s.dataset.kind === 'underway').querySelector('.cards');
const placeholders = (list) => list.children.filter(n => n.className === 'empty');
const cardsIn = (list) => list.children.filter(n => n.className === 'task-card');

test('empty column placeholder is replaced by cards and returns when emptied', async () => {
  const view = await mountBoard(board([]));
  let list = underwayList(view.doc);
  assert.equal(cardsIn(list).length, 0);
  assert.equal(placeholders(list).length, 1);

  view.setBoard(board([card('k1', 'First summary')]));
  await view.poll(); await view.flush();
  list = underwayList(view.doc);
  assert.deepEqual(cardsIn(list).map(c => c.dataset.key), ['k1']);
  assert.equal(placeholders(list).length, 0);

  view.setBoard(board([]));
  await view.poll(); await view.flush();
  list = underwayList(view.doc);
  assert.equal(cardsIn(list).length, 0);
  assert.equal(placeholders(list).length, 1);
});

test('updated card keeps focus on its open control across polls', async () => {
  const view = await mountBoard(board([card('k1', 'First summary')]));
  const opener = underwayList(view.doc).querySelector('.card-open');
  const disclosure = underwayList(view.doc).querySelector('.event-time');
  disclosure.open = true;
  opener.focus();
  view.setBoard(board([card('k1', 'Changed summary')]));
  await view.poll(); await view.flush();
  const focused = view.doc.activeElement;
  assert.equal(focused.className, 'card-open');
  assert.notEqual(focused, opener);
  assert.ok(underwayList(view.doc).contains(focused));
  assert.equal(cardsIn(underwayList(view.doc)).length, 1);
  assert.equal(underwayList(view.doc).querySelector('.event-time').open, true);
});

test('removing an earlier card keeps the focused card and its disclosure in place', async () => {
  const view = await mountBoard(board([card('a', 'A'), card('b', 'B'), card('c', 'C')]));
  const opener = underwayList(view.doc).children[1].querySelector('.card-open');
  underwayList(view.doc).children[1].querySelector('.event-time').open = true;
  opener.focus();
  view.setBoard(board([card('b', 'B'), card('c', 'C')]));
  await view.poll(); await view.flush();
  const list = underwayList(view.doc);
  assert.deepEqual(cardsIn(list).map(c => c.dataset.key), ['b', 'c']);
  assert.equal(view.doc.activeElement, opener);
  assert.equal(list.children[0].querySelector('.event-time').open, true);
});

test('reordering a focused card keeps the same focused control', async () => {
  const view = await mountBoard(board([card('a', 'A'), card('b', 'B')]));
  const list = () => underwayList(view.doc);
  const opener = list().children[1].querySelector('.card-open');
  opener.focus();
  view.setBoard(board([card('b', 'B'), card('a', 'A')]));
  await view.poll(); await view.flush();
  assert.deepEqual(cardsIn(list()).map(c => c.dataset.key), ['b', 'a']);
  assert.equal(view.doc.activeElement, opener);
});
