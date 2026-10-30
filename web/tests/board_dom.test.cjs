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
  get classList() { return {contains: (name) => this.className.split(/\s+/).includes(name)}; }
  get firstChild() { return this.children[0] || null; }
  get lastChild() { return this.children.at(-1) || null; }
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
  matches(selector) { const m = selector.match(/^([a-z0-9]*)((?:\.[\w-]+)*)$/); if (!m) throw new Error('Unsupported selector ' + selector);
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
  const descendants = (node) => [node, ...node.children.flatMap(descendants)];
  doc.getElementById = (id) => {
    for (const root of doc.byId.values()) { const found = descendants(root).find(n => n.id === id); if (found) return found; }
    const el = new Element('div', doc); el.id = id; doc.byId.set(id, el); return el;
  };
  doc.querySelectorAll = () => [];
  doc.querySelector = (selector) => {
    const nodes = [...doc.byId.values()].flatMap(descendants);
    if (selector === 'input[name="answer"]:checked') return nodes.find(n => n.tagName === 'INPUT' && n.name === 'answer' && n.checked) || null;
    return nodes.find(n => n.matches(selector)) || null;
  };
  doc.listeners = {};
  doc.addEventListener = (type, fn) => { (doc.listeners[type] ||= []).push(fn); };
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

async function mountBoard(initial, clock = Date.UTC(2026, 0, 15, 12), tickets = {}, messages = {}, setup = {}) {
  const doc = makeDom(), timers = [], requests = [], storage = new Map(), local = setup.local || new Map();
  class FixedDate extends Date { constructor(...args) { args.length ? super(...args) : super(clock); } static now() { return clock; } }
  let current = initial;
  const fetch = async (url, options = {}) => {
    requests.push({url, method: options.method || 'GET', body: options.body ? JSON.parse(options.body) : null});
    if (url.startsWith('api/locale')) return { ok: true, json: async () => ({ locale: Object.keys(messages).length ? 'es' : 'en', messages }) };
    if (url.startsWith('api/board')) return { ok: true, json: async () => JSON.parse(JSON.stringify(current)) };
    if (url.startsWith('api/history')) return { ok: true, json: async () => JSON.parse(JSON.stringify(setup.history || {records: [], details: [], omitted: 0, scope: ''})) };
    if (setup.delivery && url.startsWith('api/delivery/') && !url.endsWith('/explanation')) return { ok: true, json: async () => JSON.parse(JSON.stringify(setup.delivery)) };
    if (url.startsWith('api/ticket/')) {
      const entry = tickets[decodeURIComponent(url.slice('api/ticket/'.length))];
      return typeof entry === 'function' ? entry() : { ok: true, json: async () => JSON.parse(JSON.stringify(entry)) };
    }
    return { ok: true, json: async () => ({ receipts: [] }) };
  };
  const context = vm.createContext({ document: doc, fetch, navigator: {}, location: { href: 'http://localhost/', search: '' },
    matchMedia: (query) => ({ matches: !!setup.phone && /max-width/.test(query), addEventListener() {} }), setInterval: (fn) => timers.push(fn), setTimeout, clearTimeout,
    localStorage: { getItem(key) { return local.get(key) ?? null; }, setItem(key, value) { local.set(key, value); } },
    AbortController, URL, URLSearchParams, Intl, Date: FixedDate, JSON, Map, Set, Promise, crypto: require('node:crypto').webcrypto, Option: function Option(text, value) { return Object.assign(new Element('option', doc), { text, value }); },
    sessionStorage: { getItem(key) { return storage.get(key) || null; }, setItem(key, value) { storage.set(key, value); } } });
  context.window = context;
  context.addEventListener = () => {};
  vm.runInContext(fs.readFileSync(path.join(root, 'static/i18n.js'), 'utf8') + '\nthis.DeckI18n = DeckI18n;', context);
  vm.runInContext(fs.readFileSync(path.join(root, 'static/app.js'), 'utf8'), context);
  const flush = async () => { for (let i = 0; i < 20; i++) await new Promise(resolve => setImmediate(resolve)); };
  await flush();
  return { doc, flush, requests, local, poll: timers[0], setBoard(next) { current = next; } };
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

test('board counts agents working now and names what every other card waits for', async () => {
  const act = (key, bucket, kind, agent = '', until = '') => ({...card(key, 'Summary ' + key), bucket,
    activity: {kind, agent, until, gap: ''}, context: {reason: '', phase: 'unknown', firstmate_action: null}});
  const data = board([act('a', 'underway', 'working', 'working'), act('b', 'underway', 'working', 'working'),
    act('v', 'underway', 'validating', 'validation'), act('p', 'underway', 'paused', 'none', '2026-01-20T15:00:00Z'),
    act('f', 'underway', 'finished', 'present'), act('u', 'underway', 'unknown', 'unknown'), act('o', 'underway', 'unknown', 'open'),
    {...act('l', 'underway', 'last_seen_working', 'open'), activity: {kind: 'last_seen_working', agent: 'open', until: '', gap: '', seen_at: Date.UTC(2026, 0, 15, 10) / 1000}}]);
  data.columns[0].cards = [act('r', 'charted', 'ready'), act('d', 'charted', 'deferred', '', '2026-01-22'),
    act('h', 'charted', 'held'), act('w', 'charted', 'blocked')];
  const view = await mountBoard(data);
  const section = (kind) => view.doc.getElementById('board').children.find(s => s.dataset.kind === kind);
  const chips = (kind) => Object.fromEntries(cardsIn(section(kind).querySelector('.cards')).map(c => [c.dataset.key, c.querySelector('.phase').textContent]));
  // The top line says what needs you and who works; the columns say the rest.
  assert.equal(view.doc.getElementById('overview').textContent, '0 need you · 2 working · ');
  assert.equal(section('underway').querySelector('.column-sub').textContent, '2 working now');
  assert.equal(section('charted').querySelector('.column-sub').textContent, '1 ready to start');
  const underway = chips('underway');
  assert.equal(underway.a, 'Working now'); assert.equal(underway.v, 'Checks running');
  assert.equal(underway.f, 'Done, not closed yet');
  assert.equal(underway.l, 'Working when last seen · 2 hours ago');
  // A return date is its own short tag beside the state.
  assert.equal(underway.p, 'Paused');
  // Deferred work leaves Up next for its Later section.
  assert.deepEqual(chips('charted'), {r: 'Ready to start', h: 'On hold', w: 'Waiting on another task'});
  const dates = (kind) => Object.fromEntries(cardsIn(section(kind).querySelector('.cards')).filter(c => c.querySelector('.card-date')).map(c => [c.dataset.key, c.querySelector('.card-date').textContent]));
  assert.deepEqual(dates('underway'), {p: 'Jan 20'}); assert.deepEqual(dates('charted'), {});
  // The agent line lives in the detail, not on every card.
  assert.equal(descendants(view.doc.getElementById('board')).some(n => n.className === 'activity-note'), false);
  assert.equal(descendants(view.doc.getElementById('board')).some(n => n.textContent === 'Live activity unverified.'), false);
});

test('updated card keeps focus on its open control across polls', async () => {
  const view = await mountBoard(board([card('k1', 'First summary')]));
  const opener = underwayList(view.doc).querySelector('.card-open');
  opener.focus();
  view.setBoard(board([card('k1', 'Changed summary')]));
  await view.poll(); await view.flush();
  const focused = view.doc.activeElement;
  assert.equal(focused.className, 'card-open');
  assert.notEqual(focused, opener);
  assert.ok(underwayList(view.doc).contains(focused));
  assert.equal(cardsIn(underwayList(view.doc)).length, 1);
});

test('removing an earlier card keeps the focused card in place', async () => {
  const view = await mountBoard(board([card('a', 'A'), card('b', 'B'), card('c', 'C')]));
  const opener = underwayList(view.doc).children[1].querySelector('.card-open');
  opener.focus();
  view.setBoard(board([card('b', 'B'), card('c', 'C')]));
  await view.poll(); await view.flush();
  const list = underwayList(view.doc);
  assert.deepEqual(cardsIn(list).map(c => c.dataset.key), ['b', 'c']);
  assert.equal(view.doc.activeElement, opener);
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

test('source read, server receipt and snapshot time keep distinct events with exact dates on demand', async () => {
  const clock = Date.UTC(2026, 0, 15, 12), received = clock / 1000 - 3600, generated = Date.UTC(2026, 0, 15, 9);
  const sources = [{home: '/home/example/space', label: 'example', received_at: received, generated: new Date(generated).toISOString(), supervisor: null, error: null}];
  const view = await mountBoard({...board([]), sources}, clock);
  const exact = (ms) => new Intl.DateTimeFormat('en', {dateStyle: 'medium', timeStyle: 'short'}).format(new Date(ms));
  // The top line keeps freshness short; the exact read time is on hover.
  const header = view.doc.getElementById('connection');
  // This source was last received an hour ago, so the phrase says so.
  assert.equal(header.textContent, 'Out of date'); assert.equal(header.title, 'Source read · ' + exact(clock));
  const row = view.doc.getElementById('supervisors').children[0];
  const times = row.querySelectorAll('.relative-time');
  assert.ok(times[0].textContent.includes('1 hour ago'));
  assert.equal(row.querySelectorAll('.exact-time')[0].textContent, exact(received * 1000));
  assert.equal(row.querySelectorAll('.exact-time')[1].textContent, exact(generated));
  const summary = row.querySelectorAll('summary')[0];
  row.querySelectorAll('details')[0].open = true;
  row.querySelectorAll('details')[2].open = true;
  summary.focus();
  const moved = [{...sources[0], received_at: received + 30}];
  view.setBoard({...board([]), sources: moved});
  await view.poll(); await view.flush();
  const rowAgain = view.doc.getElementById('supervisors').children[0];
  assert.equal(rowAgain.querySelectorAll('.exact-time')[0].textContent, exact((received + 30) * 1000));
  assert.equal(rowAgain.querySelectorAll('details')[0].open, true);
  assert.equal(rowAgain.querySelectorAll('details')[1].open, false);
  assert.equal(rowAgain.querySelectorAll('details')[2].open, true);
  assert.equal(view.doc.activeElement, rowAgain.querySelectorAll('summary')[0]);
});

test('supervisor disclosures keep their own state when the row changes shape', async () => {
  const clock = Date.UTC(2026, 0, 15, 12), received = clock / 1000 - 3600;
  const row = (view) => view.doc.getElementById('supervisors').children[0];
  const byRole = (r, role) => [...r.querySelectorAll('details')].find(d => d.dataset.role === role);
  const failing = [{home: '/home/example/space', label: 'example', received_at: received, error: 'Source unavailable', generated: '', supervisor: null}];
  const recovered = [{home: '/home/example/space', label: 'example', received_at: received, error: null, generated: new Date(clock).toISOString(), supervisor: null}];
  const view = await mountBoard({...board([]), sources: failing}, clock);
  byRole(row(view), 'source').open = true;
  byRole(row(view), 'source').querySelector('summary').focus();
  view.setBoard({...board([]), sources: recovered});
  await view.poll(); await view.flush();
  const after = row(view);
  assert.equal(byRole(after, 'receipt').open, false);
  assert.equal(byRole(after, 'source').open, true);
  assert.equal(byRole(after, 'snapshot').open, false);
  assert.equal(view.doc.activeElement, byRole(after, 'source').querySelector('summary'));
});

const supervisorClock = Date.UTC(2026, 0, 15, 12);
const supervisorRow = (view) => view.doc.getElementById('supervisors').children[0];
const disclosure = (row, role) => [...row.querySelectorAll('details')].find(d => d.dataset.role === role);
const sourceSnapshot = (overrides = {}) => [{home: '/home/example/space', label: 'example', received_at: supervisorClock / 1000 - 3600,
  error: null, generated: new Date(supervisorClock - 7200000).toISOString(), supervisor: null, ...overrides}];

test('recovered source falling into error keeps the outer and snapshot disclosures open', async () => {
  const view = await mountBoard({...board([]), sources: sourceSnapshot()}, supervisorClock);
  disclosure(supervisorRow(view), 'source').open = true;
  disclosure(supervisorRow(view), 'snapshot').open = true;
  view.setBoard({...board([]), sources: sourceSnapshot({error: 'Source unavailable', received_at: 0})});
  await view.poll(); await view.flush();
  const row = supervisorRow(view);
  assert.equal(disclosure(row, 'receipt'), undefined);
  assert.equal(disclosure(row, 'source').open, true);
  assert.equal(disclosure(row, 'snapshot').open, true);
});

test('a newly present receipt does not inherit another disclosure state', async () => {
  const view = await mountBoard({...board([]), sources: sourceSnapshot({error: 'Source unavailable', received_at: 0})}, supervisorClock);
  disclosure(supervisorRow(view), 'snapshot').open = true;
  view.setBoard({...board([]), sources: sourceSnapshot()});
  await view.poll(); await view.flush();
  const row = supervisorRow(view);
  assert.equal(disclosure(row, 'receipt').open, false);
  assert.equal(disclosure(row, 'source').open, false);
  assert.equal(disclosure(row, 'snapshot').open, true);
});

test('an unknown snapshot time that becomes known keeps its open state and shows the exact instant', async () => {
  const view = await mountBoard({...board([]), sources: sourceSnapshot({generated: ''})}, supervisorClock);
  disclosure(supervisorRow(view), 'snapshot').open = true;
  const known = new Date(supervisorClock - 1800000).toISOString();
  view.setBoard({...board([]), sources: sourceSnapshot({generated: known})});
  await view.poll(); await view.flush();
  const snapshot = disclosure(supervisorRow(view), 'snapshot');
  assert.equal(snapshot.open, true);
  assert.equal(snapshot.querySelector('.exact-time').textContent, new Intl.DateTimeFormat('en', {dateStyle: 'medium', timeStyle: 'short'}).format(new Date(supervisorClock - 1800000)));
});

test('a focused receipt that disappears moves focus to the source summary without carrying its state', async () => {
  const view = await mountBoard({...board([]), sources: sourceSnapshot()}, supervisorClock);
  disclosure(supervisorRow(view), 'receipt').open = true;
  disclosure(supervisorRow(view), 'receipt').querySelector('summary').focus();
  view.setBoard({...board([]), sources: sourceSnapshot({error: 'Source unavailable', received_at: 0})});
  await view.poll(); await view.flush();
  const row = supervisorRow(view);
  assert.equal(disclosure(row, 'source').open, false);
  assert.equal(view.doc.activeElement, disclosure(row, 'source').querySelector('summary'));
});

test('unchanged-shape polls keep independent disclosure states and focus', async () => {
  const view = await mountBoard({...board([]), sources: sourceSnapshot()}, supervisorClock);
  disclosure(supervisorRow(view), 'receipt').open = true;
  disclosure(supervisorRow(view), 'snapshot').open = true;
  disclosure(supervisorRow(view), 'snapshot').querySelector('summary').focus();
  view.setBoard({...board([]), sources: sourceSnapshot({received_at: supervisorClock / 1000 - 1800})});
  await view.poll(); await view.flush();
  const row = supervisorRow(view);
  assert.equal(disclosure(row, 'receipt').open, true);
  assert.equal(disclosure(row, 'source').open, false);
  assert.equal(disclosure(row, 'snapshot').open, true);
  assert.equal(view.doc.activeElement, disclosure(row, 'snapshot').querySelector('summary'));
});

test('drawer shows the full recorded report and the registered executor without the raw evidence bundle', async () => {
  const report = 'Informe completo\n\n' + 'Detalle de evidencia sintética. '.repeat(40) + 'FIN_DEL_INFORME';
  const ticket = {key: 'web', task: 'web', title: 'Synthetic task web', bucket: 'underway', owner: 'Example home', home: '/home/example/space',
    context: {phase: 'active', reason: '', firstmate_action: null}, presentation: {summary: 'Short synthetic summary.', waiting_for: '', next_action: 'Next step', executor: {harness: 'codex'}},
    dependencies: [], time: null, report: {state: 'durable', text: report, time: null}, native: {state: 'working', detail: 'Synthetic', herdr: 'working'},
    agent: 'Codex', supervisor: null, original_title: 'Synthetic task web', herdr_url: ''};
  const view = await mountBoard({...board([card('web', 'Short synthetic summary.')])}, undefined, {web: ticket});
  view.doc.getElementById('detail').showModal = function () { this.open = true; };
  underwayList(view.doc).querySelector('.card-open').listeners.click[0]();
  await view.flush();
  const content = view.doc.getElementById('detail-content');
  const all = (n) => [n, ...n.children.flatMap(all)];
  assert.ok(all(content).some(e => e.tagName === 'P' && e.textContent === report));
  assert.ok(all(content).some(e => e.tagName === 'P' && e.textContent === 'Agent: Codex'));
  const evidence = JSON.parse(all(content).find(e => e.className === 'technical-evidence').textContent);
  assert.deepEqual(Object.keys(evidence).sort(), ['context', 'native', 'task']);
});

test('a ready or queued card detail does not claim a missing resume condition', async () => {
  const gate = (key, kind) => ({...card(key, 'Summary ' + key), bucket: 'charted', activity: {kind, agent: '', until: '', gap: ''},
    context: {phase: 'unknown', reason: '', firstmate_action: null}, presentation: {summary: 'Summary ' + key, waiting_for: '', next_action: null, executor: {harness: ''}},
    native: {}, report: null, agent: '', herdr_url: ''});
  const tickets = {r: gate('r', 'ready'), q: gate('q', 'queued'), h: gate('h', 'held')};
  const data = board([]); data.columns[0].cards = Object.values(tickets);
  const view = await mountBoard(data, undefined, tickets);
  const detail = async (key) => {
    const list = view.doc.getElementById('board').children.find(s => s.dataset.kind === 'charted').querySelector('.cards');
    list.children.find(n => n.dataset.key === key).querySelector('.card-open').listeners.click[0]();
    await view.flush();
    const content = view.doc.getElementById('detail-content'), kicker = view.doc.getElementById('detail-kicker').textContent;
    const rows = Object.fromEntries(descendants(content).filter(n => n.className === 'context-row').map(n => [n.children[0].textContent, n.children[1].textContent]));
    view.doc.getElementById('detail-close').listeners.click[0]();
    return [kicker, rows, content.textContent];
  };
  const missing = 'Not recorded. Firstmate hasn’t said what it needs to continue.';
  for (const [key, chip] of [['r', 'Ready to start'], ['q', 'Queued']]) {
    const [kicker, rows, text] = await detail(key);
    assert.equal(kicker, chip);
    assert.equal('Waiting for' in rows, false, key);
    assert.equal(text.includes(missing), false, key);
  }
  const [kicker, rows] = await detail('h');
  assert.equal(kicker, 'On hold');
  assert.ok(rows['Waiting for'].startsWith(missing));
});

const descendants = (node) => [node, ...node.children.flatMap(descendants)];
function questionTicket(key, title, question, detail) {
  return {...card(key, ''), title, original_title: title, bucket: 'captains_call', agent: 'Codex',
    context: {phase: 'human'}, native: {}, call: {title: question, about: 'A sample decision is needed.',
      decide: 'Choose a sample option.', detail, options: [{value: 'sample', label: 'Sample option'}],
      freeform: true, freeform_hint: 'Sample context', close: 'release', identity: 'synthetic-hold-' + key,
      occurrence: 'synthetic-occurrence-' + key, error: ''}};
}
async function questionView(tickets, messages) {
  const data = board([]); data.columns.find(c => c.id === 'captains_call').cards = Object.values(tickets);
  const view = await mountBoard(data, undefined, tickets, messages);
  view.open = async (key) => {
    const list = view.doc.getElementById('board').children.find(n => n.dataset.kind === 'captains_call').querySelector('.cards');
    list.children.find(n => n.dataset.key === key).querySelector('.card-open').listeners.click[0]();
    await view.flush();
  };
  return view;
}

test('question context retains each work title and complete safe instructions after its options', async () => {
  const tickets = {
    alpha: questionTicket('alpha', 'Prepare sample release', 'Confirm sample workspace', 'Run sample-cli status --scope example.\n' + 'Keep the sample output. '.repeat(40) + '<script>sample()</script>'),
    beta: questionTicket('beta', 'Review sample guide', 'Choose sample format', 'Read docs/example.md, then choose a format.')
  };
  const view = await questionView(tickets);
  for (const ticket of Object.values(tickets)) {
    await view.open(ticket.key);
    assert.equal(view.requests.filter(r => r.url.startsWith('api/ticket/')).at(-1).url, 'api/ticket/' + ticket.key);
    assert.equal(view.doc.getElementById('detail-title').textContent, ticket.title);
    const content = view.doc.getElementById('detail-content'), nodes = descendants(content);
    assert.equal(JSON.parse(nodes.find(n => n.className === 'technical-evidence').textContent).task, ticket.task);
    // The question shows once; the call's own short title is not a second heading.
    assert.equal(nodes.filter(n => n.textContent === ticket.call.decide && n.tagName === 'P').length, 1);
    assert.equal(nodes.some(n => n.textContent === ticket.call.title), false);
    const instructions = nodes.find(n => n.tagName === 'P' && n.textContent === ticket.call.detail);
    assert.ok(instructions);
    assert.equal(instructions.closest('details'), null);
    // How-to text follows the choices, so the recommended one stays in view.
    assert.ok(nodes.indexOf(instructions) > nodes.findIndex(n => n.className === 'option-list'));
    assert.equal(nodes.filter(n => n.tagName === 'P' && n.textContent === ticket.call.detail).length, 1);
    assert.equal(nodes.some(n => n.tagName === 'SCRIPT'), false);
    view.doc.getElementById('detail-close').listeners.click[0]();
  }
  assert.ok(view.requests.every(r => r.method === 'GET'));
});

test('question context avoids a duplicate equal title and permits absent instructions', async () => {
  const ticket = questionTicket('same', 'Choose sample scope', 'Choose sample scope', '');
  const view = await questionView({same: ticket}); await view.open('same');
  assert.equal(view.doc.getElementById('detail-title').textContent, ticket.title);
  assert.equal(descendants(view.doc.getElementById('detail-content')).some(n => n.textContent === ticket.title), false);
  assert.ok(descendants(view.doc.getElementById('detail-content')).some(n => n.className === 'option-list'), view.doc.getElementById('detail-content').textContent);
});

test('question context never adds the call title as a second question', async () => {
  const tickets = {
    markdown: {...questionTicket('markdown', 'Decide on deploy target', 'Decide on `deploy` target', ''), original_title: 'Decide on `deploy` target'},
    report: {...questionTicket('report', 'Choose release scope', 'Choose release scope data/reports/sample.md', ''), original_title: 'Choose release scope data/reports/sample.md'},
    authored: {...questionTicket('authored', 'Deploy sample: pick a target', 'Deploy sample', '')},
    distinct: {...questionTicket('distinct', 'Prepare sample release', 'Confirm sample workspace', ''), original_title: 'Prepare sample release data/reports/sample.md'},
  };
  const view = await questionView(tickets);
  for (const ticket of Object.values(tickets)) {
    await view.open(ticket.key);
    assert.equal(view.doc.getElementById('detail-title').textContent, ticket.title);
    const nodes = descendants(view.doc.getElementById('detail-content'));
    assert.equal(nodes.some(n => n.className === 'call-question-title'), false, ticket.key);
    assert.equal(nodes.filter(n => n.className === 'question').map(n => n.textContent).join('|'), ticket.call.decide, ticket.key);
    view.doc.getElementById('detail-close').listeners.click[0]();
  }
});

test('question draft and occurrence guard survive refreshed question context without sending', async () => {
  const ticket = questionTicket('draft', 'Prepare sample release', 'Confirm sample workspace', 'Read the sample instructions.');
  const view = await questionView({draft: ticket}); await view.open('draft');
  const note = view.doc.getElementById('answer-note'); note.value = 'Retain my sample draft';
  note.listeners.input[0]({type: 'input'});
  assert.equal(view.doc.getElementById('answer-submit').disabled, false);
  await view.poll(); await view.flush();
  assert.equal(view.doc.getElementById('answer-note'), note);
  ticket.call.occurrence = 'synthetic-new-occurrence'; ticket.call.identity = 'synthetic-new-hold';
  await view.poll(); await view.flush();
  assert.equal(view.doc.getElementById('answer-submit').disabled, true);
  assert.equal(note.value, 'Retain my sample draft');
  assert.equal(view.doc.getElementById('call-change').hidden, false);
  assert.ok(view.requests.every(r => r.method === 'GET'));
});

test('answer receipt says whether Firstmate was pointed at the answer or sees it next turn', async () => {
  const note = 'Couldn’t reach Firstmate right now. It will see your answer on its next check.';
  const answer = async (primary) => {
    const tickets = {ask: questionTicket('ask', 'Prepare sample release', 'Confirm sample workspace', '')};
    const view = await questionView(tickets); await view.open('ask');
    tickets['ask/answer'] = {saved: true, state: 'saved', message: 'Answer saved by Firstmate.', notice: 'unknown',
      ack: 'unknown', consumed: 'unknown', ...(primary ? {primary} : {})};
    const field = view.doc.getElementById('answer-note'); field.value = 'Sample answer';
    field.listeners.input[0]({type: 'input'});
    const send = descendants(view.doc.getElementById('detail-content')).find(n => n.id === 'answer-submit');
    send.listeners.click[0](); await view.flush();
    assert.ok(view.requests.some(r => r.url === 'api/ticket/ask/answer' && r.method === 'POST'));
    return view.doc.getElementById('answer-status').textContent;
  };
  const unreachable = await answer('unreachable');
  assert.ok(unreachable.includes('Answer saved')); assert.ok(unreachable.includes(note));
  const sent = await answer('sent');
  assert.ok(sent.includes('Sent to Firstmate')); assert.ok(!sent.includes(note));
  assert.ok(!(await answer('')).includes(note));
});

function structuredTicket(key, options, extra = {}) {
  const ticket = questionTicket(key, 'Pick the sample rollout', 'Pick the sample rollout', '');
  Object.assign(ticket.call, {about: 'The sample flag is ready.', decide: 'Which rollout should ship?', options, recommend: 'staged', close: '', ...extra});
  return ticket;
}
const reconcile = {value: 'reconcile', label: 'Check again first', hint: ''};

test('structured options are one-click choices with the recommended one marked', async () => {
  const tickets = {pick: structuredTicket('pick', [{value: 'staged', label: 'Staged rollout', hint: 'Ten percent first'},
    {value: 'all', label: 'Everyone at once', hint: ''}, reconcile], {link: 'https://example.com/report'})};
  const view = await questionView(tickets); await view.open('pick');
  const nodes = descendants(view.doc.getElementById('detail-content'));
  const list = nodes.find(n => n.className === 'option-list');
  assert.equal(list.getAttribute('role'), 'group');
  const choices = descendants(list).filter(n => n.tagName === 'BUTTON');
  assert.deepEqual(choices.map(b => b.dataset.value), ['staged', 'all']);
  assert.deepEqual(choices.map(b => b.querySelector('strong').textContent), ['Staged rollout', 'Everyone at once']);
  assert.equal(choices[0].querySelector('small').textContent, 'Ten percent first');
  assert.equal(choices[1].querySelector('small'), null);
  assert.deepEqual(choices.map(b => b.querySelector('.recommended')?.textContent || ''), ['Recommended', '']);
  assert.ok(choices[0].className.includes('recommended-choice'));
  // Re-check stays available, below the real choices and out of the primary list.
  const recheck = nodes.find(n => n.tagName === 'BUTTON' && n.dataset.value === 'reconcile');
  // Its one-line meaning sits on the button itself, not in a loose note.
  assert.equal(recheck.querySelector('strong').textContent, 'Check again first');
  assert.equal(recheck.querySelector('small').textContent, 'Nothing gets decided yet.'); assert.equal(list.contains(recheck), false);
  assert.equal(nodes.some(n => n.className === 'answer-help'), false);
  // Free text is the secondary path: folded away until the captain opens it.
  const fold = nodes.find(n => n.tagName === 'DETAILS' && n.className.includes('own-words-fold'));
  assert.equal(fold.open, false); assert.ok(fold.contains(view.doc.getElementById('answer-note')));
  const link = nodes.find(n => n.tagName === 'A' && n.textContent === 'See the full report ↗');
  assert.equal(link.href, 'https://example.com/report'); assert.equal(link.rel, 'noopener noreferrer');
  assert.ok(view.requests.every(r => r.method === 'GET'));
  tickets['pick/answer'] = {saved: true, state: 'saved', message: 'Answer saved by Firstmate.', notice: 'unknown', ack: 'unknown', consumed: 'unknown'};
  choices[1].listeners.click[0](); await view.flush();
  const posts = view.requests.filter(r => r.method === 'POST');
  assert.equal(posts.length, 1);
  assert.equal(posts[0].url, 'api/ticket/pick/answer');
  assert.equal(posts[0].body.value, 'all'); assert.equal(posts[0].body.occurrence, tickets.pick.call.occurrence);
  assert.ok(view.doc.getElementById('answer-status').textContent.includes('Answer saved'));
  assert.ok(choices.every(b => b.disabled));
  choices[0].listeners.click[0](); await view.flush();
  assert.equal(view.requests.filter(r => r.method === 'POST').length, 1);
});

test('a failed send offers a retry only when the request itself failed', async () => {
  const tickets = {pick: structuredTicket('pick', [{value: 'staged', label: 'Staged rollout', hint: ''}, reconcile])};
  const view = await questionView(tickets); await view.open('pick');
  const nodes = descendants(view.doc.getElementById('detail-content'));
  const staged = nodes.find(n => n.tagName === 'BUTTON' && n.dataset.value === 'staged');
  const status = () => view.doc.getElementById('answer-status').textContent;
  tickets['pick/answer'] = () => { throw Object.assign(new Error('aborted'), {name: 'AbortError'}); };
  staged.listeners.click[0](); await view.flush();
  assert.equal(status(), 'Deck took too long to answer. Your draft is safe. Try the same answer again.');
  assert.equal(staged.disabled, false);
  const refusal = 'That answer is too long. Shorten it and send again.';
  tickets['pick/answer'] = () => ({ok: false, status: 400, json: async () => ({error: refusal})});
  staged.listeners.click[0](); await view.flush();
  assert.equal(status(), refusal + ' Your draft is safe.');
  const posts = view.requests.filter(r => r.method === 'POST');
  assert.equal(posts.length, 2); assert.equal(posts[0].body.request_id, posts[1].body.request_id);
});

test('a call without options keeps the plain own-words flow and the re-check choice', async () => {
  const tickets = {free: structuredTicket('free', [reconcile], {recommend: ''})};
  const view = await questionView(tickets); await view.open('free');
  const nodes = descendants(view.doc.getElementById('detail-content'));
  assert.equal(nodes.some(n => n.className === 'option-list'), false);
  assert.equal(nodes.some(n => n.className === 'recommended'), false);
  assert.equal(nodes.some(n => n.tagName === 'DETAILS' && n.className.includes('own-words-fold')), false);
  const note = view.doc.getElementById('answer-note'), send = view.doc.getElementById('answer-submit');
  assert.equal(note.closest('details'), null);
  assert.equal(send.disabled, true);
  note.value = 'Ship it next week'; note.listeners.input[0]({type: 'input'});
  assert.equal(send.disabled, false);
  tickets['free/answer'] = {saved: true, state: 'saved', message: 'Answer saved by Firstmate.', notice: 'unknown', ack: 'unknown', consumed: 'unknown'};
  send.listeners.click[0](); await view.flush();
  const post = view.requests.find(r => r.method === 'POST');
  assert.deepEqual([post.body.value, post.body.note], ['', 'Ship it next week']);
  assert.ok(nodes.some(n => n.tagName === 'BUTTON' && n.dataset.value === 'reconcile'));
  // Byte limits and draft mechanics stay in the technical details, not the primary copy.
  const technical = nodes.find(n => n.tagName === 'DETAILS' && n.className === 'source-details');
  const primary = nodes.filter(n => n.tagName === 'P' && !technical.contains(n)).map(n => n.textContent).join(' ');
  assert.equal(/512|bytes|intake|reconcil/i.test(primary), false, primary);
  assert.ok(technical.textContent.includes('512 bytes'));
});

test('long context leads with one short line; a card shows its whole title and never cuts its line', async () => {
  const long = 'The sample migration touches three services. ' + 'It also needs a long explanation of the trade-offs. '.repeat(8);
  const ticket = structuredTicket('long', [{value: 'staged', label: 'Staged', hint: ''}, reconcile], {about: long});
  const view = await questionView({long: ticket}); await view.open('long');
  const context = descendants(view.doc.getElementById('detail-content')).find(n => n.className === 'call-context');
  assert.equal(context.children[0].textContent, 'The sample migration touches three services.');
  assert.equal(context.children[1].tagName, 'DETAILS'); assert.ok(context.children[1].textContent.includes('trade-offs'));
  const data = board([{...card('wordy', long), title: 'A sample title that keeps going '.repeat(6), presentation: {summary: long.replace(/\./g, ',')}}]);
  const boardView = await mountBoard(data);
  const shown = cardsIn(underwayList(boardView.doc))[0];
  assert.equal(descendants(shown).find(n => n.tagName === 'H3').textContent, data.columns[1].cards[0].title);
  assert.equal(shown.querySelector('.card-line').textContent, long.replace(/\./g, ',').trim());
  assert.equal(/…/.test(shown.textContent), false);
});

test('a lead line does not stop at an abbreviation', async () => {
  const lead = 'Upgrade the CI image to Node 20 vs. Node 18, e.g. for the newer test runner.';
  const text = lead + ' Then rerun every sample check and compare the timings with the previous image.'.repeat(3);
  const view = await mountBoard(board([{...card('abbr', ''), presentation: {summary: text}}]));
  assert.equal(cardsIn(underwayList(view.doc))[0].querySelector('.card-line').textContent, lead);
  const call = structuredTicket('abbr', [{value: 'staged', label: 'Staged', hint: ''}, reconcile], {about: text});
  const callView = await questionView({abbr: call}); await callView.open('abbr');
  const context = descendants(callView.doc.getElementById('detail-content')).find(n => n.className === 'call-context');
  assert.equal(context.children[0].textContent, lead);
});

test('a lead line skips abbreviations anywhere and otherwise cuts at a word', async () => {
  const lead = 'We need to pick the database for the new billing service, Postgres vs. MySQL, before the schema freeze on Friday.';
  const safe = lead + ' Postgres has the better tooling for the sample reports and the team already knows it well.';
  const unsafe = 'Pick the cache for the sample service, Redis vs. Memcached, e.g. for session data, i.e. The login tokens, approx. Ten thousand keys per node, as Dr. Example said.';
  const view = await mountBoard(board([{...card('safe', ''), presentation: {summary: safe}}, {...card('unsafe', ''), presentation: {summary: unsafe}}]));
  const reasons = Object.fromEntries(cardsIn(underwayList(view.doc)).map(c => [c.dataset.key, c.querySelector('.card-line').textContent]));
  assert.equal(reasons.safe, lead);
  // No safe sentence end: the whole line stays rather than a cut one.
  assert.equal(reasons.unsafe, unsafe);
});

test('a plain “no.” ends a lead sentence while the number abbreviation “No.” does not', async () => {
  const lead = 'La migración del esquema ya terminó en staging, pero el despliegue a producción todavía no.';
  const spanish = lead + ' Falta que el equipo de datos apruebe los cambios y revise los índices nuevos esta semana.';
  const number = 'Before the release we still have to close the sample ticket No. Twelve for the billing service. ' +
    'The rollback ticket can wait until the next sample review next week.';
  const view = await mountBoard(board([{...card('es', ''), presentation: {summary: spanish}}, {...card('number', ''), presentation: {summary: number}}]));
  const reasons = Object.fromEntries(cardsIn(underwayList(view.doc)).map(c => [c.dataset.key, c.querySelector('.card-line').textContent]));
  assert.equal(reasons.es, lead);
  assert.equal(reasons.number, 'Before the release we still have to close the sample ticket No. Twelve for the billing service.');
});

test('a card status keeps the server’s state-specific label over the generic phase name', async () => {
  const at = (key, phase, label) => ({...card(key, 'Summary ' + key), activity: {kind: 'unknown', agent: 'unknown', until: '', gap: ''},
    context: {reason: '', phase, label, firstmate_action: null}, native: {}, report: null, agent: '', herdr_url: ''});
  const tickets = {last: at('last', 'reported', 'Last report'), ready: at('ready', 'integration', 'Ready for integration'), bare: at('bare', 'reported', '')};
  const view = await mountBoard(board(Object.values(tickets)), undefined, tickets);
  const chips = Object.fromEntries(cardsIn(underwayList(view.doc)).map(c => [c.dataset.key, c.querySelector('.phase').textContent]));
  assert.deepEqual(chips, {last: 'Last report', ready: 'Ready for integration', bare: 'Reported done'});
  cardsIn(underwayList(view.doc)).find(c => c.dataset.key === 'last').querySelector('.card-open').listeners.click[0]();
  await view.flush();
  assert.equal(view.doc.getElementById('detail-kicker').textContent, 'Last report');
});

test('a failed question check blocks sending only until a later check confirms the same question', async () => {
  const tickets = {flaky: structuredTicket('flaky', [{value: 'staged', label: 'Staged rollout', hint: ''}, reconcile])};
  const view = await questionView(tickets); await view.open('flaky');
  const content = view.doc.getElementById('detail-content'), warning = view.doc.getElementById('call-change');
  const choices = descendants(content).filter(n => n.tagName === 'BUTTON' && n.className.includes('option-choice'));
  const note = view.doc.getElementById('answer-note'), send = view.doc.getElementById('answer-submit');
  note.value = 'Ship it next week'; note.listeners.input[0]({type: 'input'});
  assert.deepEqual(choices.map(b => b.dataset.value), ['staged', 'reconcile']);
  assert.ok(choices.every(b => !b.disabled)); assert.equal(send.disabled, false);
  const ticket = tickets.flaky; delete tickets.flaky;
  await view.poll(); await view.flush();
  assert.equal(warning.hidden, false); assert.equal(warning.textContent, 'Couldn’t check the question. Your draft is safe.');
  assert.ok(choices.every(b => b.disabled)); assert.equal(send.disabled, true);
  tickets.flaky = ticket;
  await view.poll(); await view.flush();
  assert.equal(warning.hidden, true);
  assert.ok(choices.every(b => !b.disabled)); assert.equal(send.disabled, false);
  assert.equal(view.doc.getElementById('answer-note'), note); assert.equal(note.value, 'Ship it next week');
  ticket.call.occurrence = 'synthetic-new-occurrence';
  await view.poll(); await view.flush();
  assert.equal(warning.hidden, false); assert.ok(warning.textContent.startsWith('The question changed.'));
  assert.ok(choices.every(b => b.disabled)); assert.equal(send.disabled, true);
  assert.ok(view.requests.every(r => r.method === 'GET'));
});

test('a server error on the same question blocks sending only until a clean check', async () => {
  const error = 'This Firstmate can’t take answers from the web yet. Update Firstmate or answer there.';
  const tickets = {guard: structuredTicket('guard', [{value: 'staged', label: 'Staged rollout', hint: ''}, reconcile], {error})};
  const ticket = tickets.guard;
  const view = await questionView(tickets); await view.open('guard');
  const content = view.doc.getElementById('detail-content'), warning = view.doc.getElementById('call-change');
  const choices = descendants(content).filter(n => n.tagName === 'BUTTON' && n.className.includes('option-choice'));
  const note = view.doc.getElementById('answer-note'), send = view.doc.getElementById('answer-submit');
  note.value = 'Ship it next week'; note.listeners.input[0]({type: 'input'});
  const locked = () => choices.every(b => b.disabled) && send.disabled;
  const open = () => choices.every(b => !b.disabled) && !send.disabled;
  assert.equal(warning.hidden, false); assert.equal(warning.textContent, error); assert.ok(locked());
  ticket.call.error = '';
  await view.poll(); await view.flush();
  assert.equal(warning.hidden, true); assert.ok(open());
  ticket.call.error = error;
  await view.poll(); await view.flush();
  assert.equal(warning.hidden, false); assert.equal(warning.textContent, error); assert.ok(locked());
  assert.equal(descendants(warning).some(n => n.tagName === 'BUTTON'), false);
  ticket.call.error = '';
  await view.poll(); await view.flush();
  assert.equal(warning.hidden, true); assert.ok(open());
  assert.equal(view.doc.getElementById('answer-note'), note); assert.equal(note.value, 'Ship it next week');
  assert.ok(view.requests.every(r => r.method === 'GET'));
});

test('the Captain’s Call dialog reads naturally in Spanish', async () => {
  const messages = JSON.parse(fs.readFileSync(path.join(root, 'locales/es.json'), 'utf8'));
  const tickets = {es: structuredTicket('es', [{value: 'staged', label: 'Por etapas', hint: ''}, reconcile])};
  const view = await questionView(tickets, messages); await view.open('es');
  const text = view.doc.getElementById('detail-content').textContent;
  for (const expected of ['Recomendada', 'Responder con mis palabras', 'Revisar otra vez primero', 'Aún no decides nada.'])
    assert.ok(text.includes(expected), expected);
  assert.equal(view.doc.getElementById('detail-kicker').textContent, 'Te necesita');
  // Authored option text is never translated.
  assert.ok(text.includes('Por etapas'));
  assert.equal(/Recommended|Answer in my own words|Check again first|CAPTAIN/.test(text), false);
});

// ---- U1-U4: one top line, even cards, no dialogs, answer in place ----
const columnOf = (view, kind) => view.doc.getElementById('board').children.find(s => s.dataset.kind === kind);
const fullBoard = (extra = {}) => {
  const data = board([card('w', 'Working summary.')]);
  data.columns = [['captains_call', [{...questionTicket('q', 'Pick the sample scope', 'Pick the sample scope', ''), question: 'Which scope ships first?',
      time: {label: 'Asked', epoch: Date.UTC(2026, 0, 15, 9) / 1000, date: null}}]],
    ['underway', data.columns[1].cards], ['charted', [{...card('later', ''), bucket: 'charted', title: 'Filed without a summary',
      time: {label: 'Filed', epoch: null, date: '2026-01-14'}}]], ['landed', [{...card('done', ''), bucket: 'landed', summary: 'Shipped it.'}]]]
    .map(([id, cards]) => ({id, title: id, cards}));
  return Object.assign(data, extra);
};

test('columns run Needs you, Underway, Up next, Delivered on the board and the phone tabs', async () => {
  const view = await mountBoard(fullBoard());
  const order = ['captains_call', 'underway', 'charted', 'landed'];
  assert.deepEqual(view.doc.getElementById('board').children.map(s => s.dataset.kind), order);
  assert.deepEqual(view.doc.getElementById('columns-nav').children.map(b => b.dataset.kind), order);
  assert.equal(view.doc.getElementById('overview').textContent, '1 needs you · 0 working · ');
});

test('every card has the same top row, its whole title and never a placeholder', async () => {
  const long = 'A sample title that keeps going and going '.repeat(5).trim();
  const data = fullBoard(); data.columns[1].cards.push({...card('long', ''), title: long, time: {label: 'Last report', epoch: Date.UTC(2026, 0, 15, 11, 55) / 1000, date: null}});
  const view = await mountBoard(data);
  const all = view.doc.getElementById('board').children.flatMap(s => cardsIn(s.querySelector('.cards')));
  assert.equal(all.length, 5);
  for (const item of all) {
    const face = item.querySelector('.card-open');
    assert.equal(face.children[0].className, 'card-meta', item.dataset.key);
    assert.equal(face.children[1].tagName, 'H3'); assert.equal(face.children[1].className, 't');
    assert.ok(face.children.length <= 3, item.dataset.key);
    assert.ok(face.children[0].lastChild.className === 'card-ago', item.dataset.key);
    assert.equal(/…|No summary yet|no time recorded|Coordinated by|＋/.test(face.textContent), false, face.textContent);
  }
  const ages = Object.fromEntries(all.map(c => [c.dataset.key, c.querySelector('.card-ago').textContent]));
  assert.deepEqual(ages, {q: '3 h', w: '—', later: '1 d', done: '—', long: '5 min'});
  assert.equal(all.find(c => c.dataset.key === 'long').querySelector('h3').textContent, long);
  // Ages follow the clock on every read of the board.
  assert.ok(all.find(c => c.dataset.key === 'q').querySelector('.card-ago').title.startsWith('Asked · '));
  // Delivered cards carry no state chip; a call shows its question, not its title twice.
  assert.equal(all.find(c => c.dataset.key === 'done').querySelector('.phase'), null);
  assert.equal(all.find(c => c.dataset.key === 'q').querySelector('.card-line').textContent, 'Which scope ships first?');
  assert.equal(all.find(c => c.dataset.key === 'later').querySelector('.card-line'), null);
});

test('the coordinating space is named only when there are several', async () => {
  const one = await mountBoard(fullBoard());
  assert.equal(descendants(one.doc.getElementById('board')).some(n => n.className === 'card-tag'), false);
  assert.equal(one.doc.getElementById('crew').parentElement, null);
  const two = await mountBoard(fullBoard({homes: [{id: 'a', label: 'example'}, {id: 'b', label: 'other'}]}));
  assert.ok(descendants(two.doc.getElementById('board')).filter(n => n.className === 'card-tag').every(n => n.textContent === 'Coordinator'));
});

test('compact mode drops only the line, and the choice is remembered', async () => {
  const view = await mountBoard(fullBoard());
  const density = view.doc.getElementById('density');
  assert.equal(view.doc.getElementById('workspace').dataset.density, 'normal');
  assert.equal(density.getAttribute('aria-pressed'), 'false');
  const before = view.doc.getElementById('board').children.map(s => s.dataset.kind + ':' + cardsIn(s.querySelector('.cards')).map(c => c.dataset.key).join(','));
  density.listeners.click[0]();
  assert.equal(view.doc.getElementById('workspace').dataset.density, 'compact');
  assert.equal(density.getAttribute('aria-pressed'), 'true'); assert.equal(view.local.get('deck-density'), 'compact');
  assert.deepEqual(view.doc.getElementById('board').children.map(s => s.dataset.kind + ':' + cardsIn(s.querySelector('.cards')).map(c => c.dataset.key).join(',')), before);
  const again = await mountBoard(fullBoard(), undefined, {}, {}, {local: view.local});
  assert.equal(again.doc.getElementById('workspace').dataset.density, 'compact');
});

test('no dialog remains: the source has no modal and a wide screen uses the side panel', async () => {
  assert.equal(/showModal|<dialog/.test(fs.readFileSync(path.join(root, 'static/app.js'), 'utf8') + fs.readFileSync(path.join(root, 'static/index.html'), 'utf8')), false);
  const ticket = {...card('w', 'Working summary.'), context: {phase: 'active', reason: '', firstmate_action: null}, native: {}, report: null, agent: '', herdr_url: ''};
  const view = await mountBoard(fullBoard(), undefined, {w: ticket});
  const detail = view.doc.getElementById('detail'), face = () => cardsIn(columnOf(view, 'underway').querySelector('.cards'))[0].querySelector('.card-open');
  face().listeners.click[0](); await view.flush();
  assert.equal(detail.dataset.open, 'true'); assert.equal(detail.hidden, false);
  assert.equal(detail.parentElement, view.doc.getElementById('detail-panel'));
  assert.equal(face().getAttribute('aria-expanded'), 'true'); assert.equal(face().parentElement.dataset.current, 'true');
  assert.equal(view.doc.getElementById('detail-empty').hidden, true);
  // Pressing the same card again closes it and focus returns to the card.
  face().listeners.click[0](); await view.flush();
  assert.equal(detail.dataset.open, 'false'); assert.equal(face().getAttribute('aria-expanded'), 'false');
  assert.equal(view.doc.activeElement, face()); assert.equal(view.doc.getElementById('detail-empty').hidden, false);
  // Esc closes.
  face().listeners.click[0](); await view.flush();
  let prevented = false;
  for (const fn of view.doc.listeners.keydown) fn({key: 'Escape', preventDefault() { prevented = true; }});
  assert.equal(detail.dataset.open, 'false'); assert.ok(prevented); assert.equal(view.doc.activeElement, face());
  // A click inside the detail keeps it; a click anywhere else closes it.
  face().listeners.click[0](); await view.flush();
  const click = (...nodes) => { for (const fn of view.doc.listeners.click) fn({composedPath: () => nodes}); };
  click(view.doc.getElementById('detail-content'), detail); assert.equal(detail.dataset.open, 'true');
  click(face()); assert.equal(detail.dataset.open, 'true');
  click(view.doc.getElementById('board')); assert.equal(detail.dataset.open, 'false');
});

test('on a phone the detail opens inside its card, keeps a draft across reads and never reorders cards', async () => {
  const tickets = {q: structuredTicket('q', [{value: 'staged', label: 'Staged rollout', hint: ''}, reconcile])};
  const data = board([]); data.columns.find(c => c.id === 'captains_call').cards = [tickets.q, {...questionTicket('r', 'Second call', 'Second call', '')}];
  const view = await mountBoard(data, undefined, tickets, {}, {phone: true});
  const list = () => columnOf(view, 'captains_call').querySelector('.cards');
  const keys = () => cardsIn(list()).map(c => c.dataset.key);
  const before = keys();
  cardsIn(list())[0].querySelector('.card-open').listeners.click[0](); await view.flush();
  const host = cardsIn(list())[0], detail = view.doc.getElementById('detail');
  assert.equal(host.dataset.open, 'true'); assert.equal(detail.parentElement, host.querySelector('.card-more-inner'));
  assert.equal(view.doc.getElementById('detail-panel').dataset.active, 'false');
  assert.deepEqual(keys(), before);
  const note = view.doc.getElementById('answer-note'); note.value = 'Keep my phone draft'; note.listeners.input[0]({type: 'input'});
  // A changed read redraws the card face only; the open detail and its draft stay.
  data.columns.find(c => c.id === 'captains_call').cards[0] = {...tickets.q, question: 'A reworded short question?'};
  view.setBoard(data); await view.poll(); await view.flush();
  assert.equal(cardsIn(list())[0], host); assert.equal(host.dataset.open, 'true');
  assert.equal(host.querySelector('.card-line').textContent, 'A reworded short question?');
  assert.equal(view.doc.getElementById('answer-note').value, 'Keep my phone draft');
  // When the card leaves the board, its detail moves to the panel instead of vanishing.
  data.columns.find(c => c.id === 'captains_call').cards = [data.columns.find(c => c.id === 'captains_call').cards[1]];
  view.setBoard(data); await view.poll(); await view.flush();
  assert.equal(detail.parentElement, view.doc.getElementById('detail-panel'));
  assert.equal(view.doc.getElementById('detail-panel').dataset.active, 'true'); assert.equal(detail.dataset.open, 'true');
});

test('a report path on a call becomes a link that opens the report here, not a path in the how-to', async () => {
  const link = 'data/sample-task/report.md';
  const tickets = {rep: structuredTicket('rep', [{value: 'staged', label: 'Staged rollout', hint: ''}, reconcile], {link, detail: 'Read ' + link + ' first, then pick.'})};
  tickets['rep/report'] = {path: link, text: '# Sample report\n\nEverything checked.'};
  const view = await questionView(tickets); await view.open('rep');
  const nodes = descendants(view.doc.getElementById('detail-content'));
  assert.equal(nodes.some(n => n.textContent.includes(link)), false);
  assert.ok(nodes.some(n => n.tagName === 'P' && n.textContent === 'Read first, then pick.'));
  const open = nodes.find(n => n.tagName === 'BUTTON' && n.textContent === 'See the full report');
  assert.equal(open.getAttribute('aria-expanded'), 'false');
  await open.listeners.click[0](); await view.flush();
  assert.equal(open.getAttribute('aria-expanded'), 'true');
  assert.ok(view.requests.some(r => r.url === 'api/ticket/rep/report' && r.method === 'GET'));
  assert.ok(descendants(view.doc.getElementById('detail-content')).some(n => n.textContent === 'Everything checked.'));
  assert.ok(view.requests.every(r => r.method === 'GET'));
});

test('a Spanish call shows no English interface text outside its technical details', async () => {
  const en = JSON.parse(fs.readFileSync(path.join(root, 'locales/en.json'), 'utf8'));
  const es = JSON.parse(fs.readFileSync(path.join(root, 'locales/es.json'), 'utf8'));
  const english = new Set(Object.keys(en).filter(k => es[k] !== en[k]).map(k => en[k].trim()).filter(text => text.length > 2));
  const tickets = {es: structuredTicket('es', [{value: 'staged', label: 'Por etapas', hint: 'Primero el diez por ciento'}, reconcile], {link: 'https://example.com/informe'})};
  const view = await questionView(tickets, es); await view.open('es');
  const content = view.doc.getElementById('detail-content');
  const technical = descendants(content).find(n => n.tagName === 'DETAILS' && n.className === 'source-details');
  const shown = descendants(content).filter(n => !technical.contains(n) && !n.children.length).map(n => n.textContent.trim()).filter(Boolean);
  assert.deepEqual(shown.filter(text => english.has(text)), []);
  assert.equal(view.doc.getElementById('detail-kicker').textContent, 'Te necesita');
  assert.ok(shown.includes('Ver informe completo ↗'));
});

test('history is one line per delivery: title then a compact age, no crew line', async () => {
  const history = {records: [{task: 'old', title: 'Old sample delivery', home: 'h', owner: 'example', time: {label: 'Delivered', epoch: null, date: '2026-01-01'}, closed: '2026-01-01'}], details: [], omitted: 0, scope: 'Everything kept.'};
  const delivery = {task: 'old', home: 'h', owner: 'example', title: 'Old sample delivery', summary: 'Done.', report: '', details: '', sources: '',
    time: history.records[0].time, links: [{url: 'https://github.com/example/deck/pull/8', pr: 8}, {url: 'https://example.com/notes', pr: null}]};
  const view = await mountBoard(fullBoard(), undefined, {}, {}, {history, delivery});
  view.doc.getElementById('history-view').listeners.click[0](); await view.flush();
  const row = view.doc.getElementById('history-results').children[0];
  const face = row.querySelector('.card-open');
  assert.equal(face.className, 'card-open history-row');
  assert.deepEqual(face.children.map(n => n.className), ['t', 'card-ago']);
  assert.equal(row.querySelector('.card-ago').textContent, '2 wk');
  assert.equal(row.querySelector('h3').textContent, 'Old sample delivery');
  assert.equal(/Coordinated by|example/.test(row.textContent), false);
  assert.equal(view.doc.getElementById('history-status').textContent, '1 delivery');
  assert.equal(view.doc.getElementById('history-sources').children[0].textContent, 'Everything kept.');
  // Opening a row survives the page's own click, which closes only outside clicks.
  face.listeners.click[0](); await view.flush();
  for (const fn of view.doc.listeners.click) fn({composedPath: () => [face, row]});
  assert.equal(view.doc.getElementById('detail').dataset.open, 'true');
  // The PR address that left the title opens from the detail.
  const links = descendants(view.doc.getElementById('detail-content')).filter(n => n.tagName === 'A' && n.className === 'report-link');
  assert.deepEqual(links.map(a => [a.textContent, a.href, a.rel]), [['See PR #8 ↗', 'https://github.com/example/deck/pull/8', 'noopener noreferrer'],
    ['Open link ↗', 'https://example.com/notes', 'noopener noreferrer']]);
  assert.equal(view.doc.getElementById('board-search').hidden, true);
  view.doc.getElementById('history-view').listeners.click[0](); await view.flush();
  assert.equal(view.doc.getElementById('board-search').hidden, false);
});

test('postponed work waits under Later with its return date and leaves Up next’s count', async () => {
  const charted = (key, kind, until = '') => ({...card(key, 'Summary ' + key), bucket: 'charted', title: 'Task ' + key,
    activity: {kind, agent: '', until, gap: ''}, context: {reason: '', phase: 'unknown', firstmate_action: null},
    time: {label: 'Filed', epoch: Date.UTC(2026, 0, 12, 12) / 1000, date: null}});
  const data = board([]);
  data.columns[0].cards = [charted('r', 'ready'), charted('d1', 'deferred', '2026-01-22'), charted('q', 'queued'),
    charted('h', 'held'), charted('d2', 'deferred', '2026-01-31')];
  const view = await mountBoard(data);
  const section = () => view.doc.getElementById('board').children.find(s => s.dataset.kind === 'charted');
  const later = () => section().querySelector('.later');
  const keys = (list) => cardsIn(list).map(c => c.dataset.key);
  assert.deepEqual(keys(section().querySelector('.cards')), ['r', 'q', 'h']);
  assert.deepEqual(keys(later().querySelector('.cards')), ['d1', 'd2']);
  // Up next counts only what can start; Later carries its own count.
  assert.equal(section().querySelector('.column-heading').querySelector('.column-count').textContent, '3');
  assert.equal(section().querySelector('.column-sub').textContent, '1 ready to start');
  const tab = view.doc.getElementById('columns-nav').children.find(b => b.dataset.kind === 'charted');
  assert.equal(tab.firstChild.textContent, '3');
  assert.equal(later().querySelector('.later-heading').textContent, 'Later2');
  // A postponed card keeps the same top row: its return date, then its age; the section says the rest.
  for (const item of cardsIn(later().querySelector('.cards'))) {
    const meta = item.querySelector('.card-meta');
    assert.equal(item.querySelector('.phase'), null);
    assert.deepEqual(meta.children.map(n => n.className), ['card-date', 'card-ago']);
    assert.equal(meta.lastChild.textContent, '3 d');
  }
  assert.deepEqual(cardsIn(later().querySelector('.cards')).map(c => c.querySelector('.card-date').textContent), ['Jan 22', 'Jan 31']);

  // When a hold ends the card returns to Up next, and an empty Later goes away.
  data.columns[0].cards = [charted('r', 'ready'), charted('d1', 'ready'), charted('q', 'queued'), charted('h', 'held'), charted('d2', 'deferred', '2026-01-31')];
  view.setBoard(data); await view.poll(); await view.flush();
  assert.deepEqual(keys(section().querySelector('.cards')), ['r', 'd1', 'q', 'h']);
  assert.deepEqual(keys(later().querySelector('.cards')), ['d2']);
  data.columns[0].cards = [charted('d2', 'deferred', '2026-01-31')];
  view.setBoard(data); await view.poll(); await view.flush();
  // Only postponed work: no empty box above Later.
  assert.equal(section().querySelector('.cards').children.length, 0);
  assert.deepEqual(keys(later().querySelector('.cards')), ['d2']);
  data.columns[0].cards = [];
  view.setBoard(data); await view.poll(); await view.flush();
  assert.equal(later(), null);
  assert.equal(placeholders(section().querySelector('.cards')).length, 1);
});

test('Later reads Más adelante in Spanish', async () => {
  const es = JSON.parse(fs.readFileSync(path.join(root, 'locales/es.json'), 'utf8'));
  const data = board([]);
  data.columns[0].cards = [{...card('d', ''), bucket: 'charted', activity: {kind: 'deferred', agent: '', until: '2026-10-31', gap: ''},
    context: {reason: '', phase: 'unknown', firstmate_action: null}}];
  const view = await mountBoard(data, undefined, {}, es);
  const section = view.doc.getElementById('board').children.find(s => s.dataset.kind === 'charted');
  assert.equal(section.querySelector('.later-heading').firstChild.textContent, 'Más adelante');
  assert.equal(section.querySelector('.column-heading').querySelector('.column-count').textContent, '0');
});

// ---- U11: the phone opens where the captain is needed ----
const pressedTab = (view) => view.doc.getElementById('columns-nav').children.filter(b => b.getAttribute('aria-pressed') === 'true').map(b => b.dataset.kind);
const selectedColumns = (view) => view.doc.getElementById('board').children.filter(s => s.dataset.selected === 'true').map(s => s.dataset.kind);

test('the phone opens on Needs you when a question waits, else on Underway', async () => {
  const calls = await mountBoard(fullBoard(), undefined, {}, {}, {phone: true});
  assert.deepEqual(pressedTab(calls), ['captains_call']); assert.deepEqual(selectedColumns(calls), ['captains_call']);
  const none = fullBoard(); none.columns[0].cards = [];
  const quiet = await mountBoard(none, undefined, {}, {}, {phone: true});
  assert.deepEqual(pressedTab(quiet), ['underway']); assert.deepEqual(selectedColumns(quiet), ['underway']);
  // An answered question no longer needs the captain.
  const answered = fullBoard(); answered.columns[0].cards[0].answered = true;
  assert.deepEqual(pressedTab(await mountBoard(answered, undefined, {}, {}, {phone: true})), ['underway']);
});

test('the opening tab waits for the first full read, then only a tap changes it', async () => {
  const empty = fullBoard({loading: true}); empty.columns[0].cards = [];
  const view = await mountBoard(empty, undefined, {}, {}, {phone: true});
  view.setBoard(fullBoard()); await view.poll(); await view.flush();
  assert.deepEqual(pressedTab(view), ['captains_call']);
  // A tap sticks across reads, even while questions keep waiting.
  view.doc.getElementById('columns-nav').children.find(b => b.dataset.kind === 'charted').listeners.click[0]();
  await view.poll(); await view.flush();
  assert.deepEqual(pressedTab(view), ['charted']); assert.deepEqual(selectedColumns(view), ['charted']);

  // A question that arrives after the board opened does not pull the tab away.
  const none = fullBoard(); none.columns[0].cards = [];
  const later = await mountBoard(none, undefined, {}, {}, {phone: true});
  later.setBoard(fullBoard()); await later.poll(); await later.flush();
  assert.deepEqual(pressedTab(later), ['underway']);
});

// ---- U6: a project tag on every card, and questions from one project in one stack ----
const callsSection = (view) => view.doc.getElementById('board').children.find(s => s.dataset.kind === 'captains_call');
const relay = {id: 'relay', label: 'Relay'}, garden = {id: 'garden app', label: 'Garden app'};
function projectCall(key, title, project, extra = {}) {
  const ticket = structuredTicket(key, [{value: 'yes', label: 'Yes', hint: ''}, {value: 'no', label: 'No', hint: ''}]);
  return Object.assign(ticket, {title, original_title: title, project, question: '', time: {label: 'Asked', epoch: Date.UTC(2026, 0, 15, 9) / 1000, date: null}}, extra);
}
function stackBoard(calls) {
  const data = fullBoard(); data.columns[0].cards = calls;
  data.columns[1].cards = [{...card('w', 'Working summary.'), project: relay}];
  return data;
}
const gardenCalls = () => [projectCall('r1', 'Start relay phase one?', relay), projectCall('g1', 'Animate moving tasks?', garden),
  projectCall('solo', 'Pick the beta scope', null), projectCall('g2', 'One group or several per task?', garden),
  projectCall('g3', 'How many lines per task?', garden, {time: {label: 'Asked', epoch: Date.UTC(2026, 0, 15, 11) / 1000, date: null}})];

test('a project tag leads the top row, with one color per project, and no tag without a project', async () => {
  const view = await mountBoard(stackBoard(gardenCalls()));
  const list = callsSection(view).querySelector('.cards');
  const r1 = list.children.find(n => n.dataset.key === 'r1'), solo = list.children.find(n => n.dataset.key === 'solo');
  const working = view.doc.getElementById('board').children.find(s => s.dataset.kind === 'underway').querySelector('.task-card');
  assert.equal(r1.querySelector('.card-meta').firstChild.className, 'project-tag');
  assert.equal(r1.querySelector('.project-tag').textContent, 'Relay');
  // The same project has the same color in every column.
  assert.equal(working.querySelector('.project-tag').dataset.hue, r1.querySelector('.project-tag').dataset.hue);
  assert.match(r1.querySelector('.project-tag').dataset.hue, /^[0-7]$/);
  assert.equal(solo.querySelector('.project-tag'), null);
  // The project name is searchable.
  view.doc.getElementById('board-search').value = 'garden';
  view.doc.getElementById('board-search').listeners.input[0]();
  assert.deepEqual(callsSection(view).querySelectorAll('.task-card').map(n => n.dataset.key), ['g1', 'g2', 'g3']);
});

test('two or more questions from one project wait in one stack where the first stood', async () => {
  const view = await mountBoard(stackBoard(gardenCalls()));
  const list = callsSection(view).querySelector('.cards');
  assert.deepEqual(list.children.map(n => n.dataset.key), ['r1', 'stack:garden app', 'solo']);
  const stack = list.children[1], face = stack.querySelector('.stack-open');
  assert.equal(stack.className, 'call-stack');
  assert.deepEqual(stack.querySelector('.stack-cards').children.map(n => n.dataset.key), ['g1', 'g2', 'g3']);
  assert.equal(face.querySelector('.project-tag').textContent, 'Garden app');
  assert.equal(face.querySelector('.t').textContent, '3 questions from Garden app');
  assert.equal(face.querySelector('.card-line').textContent, 'Animate moving tasks? and 2 more');
  // Its age is the newest question's.
  assert.equal(face.querySelector('.card-ago').textContent, '1 h');
  // Closed until tapped, with its questions out of reach.
  assert.equal(stack.dataset.open, 'false'); assert.equal(face.getAttribute('aria-expanded'), 'false');
  assert.equal(stack.lastChild.inert, true); assert.equal(face.getAttribute('aria-controls'), stack.lastChild.id);
  // Needs you still counts questions, not stacks.
  assert.equal(callsSection(view).querySelector('.column-count').textContent, '5');
  assert.equal(view.doc.getElementById('overview').textContent, '5 need you · 0 working · ');
  // Only Needs you stacks; one question of a project stays a plain card.
  const lone = await mountBoard(stackBoard([projectCall('g1', 'Animate moving tasks?', garden), projectCall('r1', 'Start relay phase one?', relay)]));
  assert.deepEqual(callsSection(lone).querySelector('.cards').children.map(n => n.dataset.key), ['g1', 'r1']);
});

test('answering a question in a stack keeps the stack and its other questions open', async () => {
  const calls = gardenCalls(), tickets = Object.fromEntries(calls.map(c => [c.key, c]));
  const data = stackBoard(calls), view = await mountBoard(data, undefined, tickets);
  const stack = () => callsSection(view).querySelector('.call-stack');
  stack().querySelector('.stack-open').listeners.click[0]();
  assert.equal(stack().dataset.open, 'true'); assert.equal(stack().lastChild.inert, false);
  const member = (key) => stack().querySelector('.stack-cards').children.find(n => n.dataset.key === key);
  member('g2').querySelector('.card-open').listeners.click[0](); await view.flush();
  assert.equal(view.doc.getElementById('detail').dataset.open, 'true');
  tickets['g2/answer'] = {saved: true, state: 'saved', message: 'Answer saved by Firstmate.', notice: 'unknown', ack: 'unknown', consumed: 'unknown'};
  descendants(view.doc.getElementById('detail-content')).find(n => n.tagName === 'BUTTON' && n.dataset.value === 'yes').listeners.click[0]();
  await view.flush();
  assert.equal(view.requests.filter(r => r.method === 'POST').map(r => r.url).join(), 'api/ticket/g2/answer');
  // The next read shows it answered; nothing else in the stack moves or closes.
  data.columns[0].cards.find(c => c.key === 'g2').answered = true;
  view.setBoard(data); await view.poll(); await view.flush();
  assert.equal(stack().dataset.open, 'true');
  assert.deepEqual(stack().querySelector('.stack-cards').children.map(n => n.dataset.key), ['g1', 'g2', 'g3']);
  assert.equal(stack().querySelector('.stack-open').querySelector('.t').textContent, '2 questions from Garden app');
  assert.equal(view.doc.getElementById('detail').dataset.open, 'true');
  assert.ok(view.doc.getElementById('answer-status').textContent.includes('Answer saved'));
  // Another question in the same stack still opens with its choices.
  member('g3').querySelector('.card-open').listeners.click[0](); await view.flush();
  assert.ok(descendants(view.doc.getElementById('detail-content')).some(n => n.tagName === 'BUTTON' && n.dataset.value === 'no'));
  assert.equal(stack().dataset.open, 'true');
});

test('a stack that holds the open detail opens; folding it or Esc closes it', async () => {
  const calls = gardenCalls(), tickets = Object.fromEntries(calls.map(c => [c.key, c]));
  // g1 is open on its own, then a second Garden question arrives and they stack.
  const data = stackBoard([calls[0], calls[1]]), view = await mountBoard(data, undefined, tickets);
  callsSection(view).querySelector('.cards').children.find(n => n.dataset.key === 'g1').querySelector('.card-open').listeners.click[0]();
  await view.flush();
  data.columns[0].cards = calls; view.setBoard(data); await view.poll(); await view.flush();
  const stack = () => callsSection(view).querySelector('.call-stack');
  assert.equal(stack().dataset.open, 'true'); assert.equal(view.doc.getElementById('detail').dataset.open, 'true');
  // Folding the stack also closes the question open inside it.
  stack().querySelector('.stack-open').listeners.click[0]();
  assert.equal(stack().dataset.open, 'false'); assert.equal(view.doc.getElementById('detail').dataset.open, 'false');
  // Esc folds the stack the focus is in when no detail is open.
  stack().querySelector('.stack-open').listeners.click[0]();
  const inner = stack().querySelector('.stack-cards').children[0].querySelector('.card-open'); inner.focus();
  for (const fn of view.doc.listeners.keydown) fn({key: 'Escape', preventDefault() {}});
  assert.equal(stack().dataset.open, 'false'); assert.equal(view.doc.activeElement, stack().querySelector('.stack-open'));
  // When the project's questions drop below two, the stack goes away.
  data.columns[0].cards = [calls[0], calls[1]]; view.setBoard(data); await view.poll(); await view.flush();
  assert.equal(stack(), null);
  assert.deepEqual(callsSection(view).querySelector('.cards').children.map(n => n.dataset.key), ['r1', 'g1']);
});

test('a stack reads naturally in Spanish', async () => {
  const es = JSON.parse(fs.readFileSync(path.join(root, 'locales/es.json'), 'utf8'));
  const calls = gardenCalls(); calls.forEach(c => { if (c.project === garden) c.answered = true; });
  calls[3].answered = false;
  const view = await mountBoard(stackBoard(calls), undefined, {}, es);
  const face = callsSection(view).querySelector('.stack-open');
  assert.equal(face.querySelector('.t').textContent, '1 pregunta de Garden app');
  assert.equal(face.querySelector('.card-line').textContent, 'One group or several per task?');
  calls[3].answered = true;
  const done = await mountBoard(stackBoard(calls), undefined, {}, es);
  assert.equal(callsSection(done).querySelector('.stack-open').querySelector('.t').textContent, 'Garden app: todas respondidas');
  calls.forEach(c => { c.answered = false; });
  const all = await mountBoard(stackBoard(calls), undefined, {}, es);
  assert.equal(callsSection(all).querySelector('.stack-open').querySelector('.card-line').textContent, 'Animate moving tasks? y 2 más');
});

test('history and the detail panel carry the project tag in the same color', async () => {
  const history = {records: [{task: 'old', title: 'Old sample delivery', home: 'h', owner: 'example', project: relay,
    time: {label: 'Delivered', epoch: null, date: '2026-01-01'}, closed: '2026-01-01'}], details: [], omitted: 0, scope: ''};
  const delivery = {task: 'old', home: 'h', owner: 'example', title: 'Old sample delivery', summary: 'Done.', report: '', details: '', sources: '',
    time: history.records[0].time, links: [], project: relay};
  const view = await mountBoard(stackBoard(gardenCalls()), undefined, {}, {}, {history, delivery});
  const boardHue = view.doc.getElementById('board').children.find(s => s.dataset.kind === 'underway').querySelector('.project-tag').dataset.hue;
  view.doc.getElementById('history-view').listeners.click[0](); await view.flush();
  const face = view.doc.getElementById('history-results').children[0].querySelector('.card-open');
  assert.deepEqual(face.children.map(n => n.className), ['project-tag', 't', 'card-ago']);
  assert.equal(face.firstChild.dataset.hue, boardHue);
  face.listeners.click[0](); await view.flush();
  const tag = view.doc.getElementById('detail-meta').children.find(n => n.className === 'project-tag');
  assert.equal(tag.textContent, 'Relay'); assert.equal(tag.dataset.hue, boardHue);
});
