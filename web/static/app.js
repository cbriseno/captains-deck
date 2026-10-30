/* English source messages are catalog keys. Never translate task data. */
'use strict';
DeckI18n.ready.then(({t, locale, dateFmt, relative, eventTime, exactTime, untilText, ago, shortDate}) => {
/* User content is text only. Polling reads; only explicit buttons can POST. */
const $ = (id) => document.getElementById(id);
const S = { board: null, home: '', column: 'underway', columnPicked: false, online: false, view: 'board', query: '',
  busy: false, csrf: '', modal: null, opener: null, generation: 0, historyGeneration: 0,
  lastRead: 0, receipt: null, drafts: new Map(), historyTimer: null, linkHandled: false, density: 'normal', openStacks: new Set() };
try { if (localStorage.getItem('deck-density') === 'compact') S.density = 'compact'; } catch { /* Normal is the default. */ }
const labels = { charted: t("Up next"), underway: t("Underway"), captains_call: t("Needs you"), landed: t("Delivered") };
const phaseLabels = { human: t("Waiting for your answer"), dependency: t("Waiting on another task"), integration: t("Finishing up"), technical: t("On hold"), active: t("In progress"), unknown: t("Status unclear"), waiting: t("Waiting"), reported: t("Reported done") };
const runLabels = { running: t('Checks running'), fixing: t('Fixing failed checks'), passed: t('Checks passed'), failed: t('Checks failed') };
// Firstmate's crew state and endpoint probe, as projected by the bridge.
const agentLines = { working: t('Agent is working'), validation: t('Checks are running'), open: t('Agent is open'), present: t('Agent is open but idle'), none: t('No agent running'), unknown: t('Agent status unknown') };
// The same states after the agent's name: "Claude Code · working now".
const agentStates = { working: t('working now'), validation: t('running checks'), open: t('open'), present: t('open, not working'), none: t('not running'), unknown: '' };
const staleDraftNote = t("The question changed since you started. Your draft is still here: pick an option or edit it to send.");
const gapLines = { hold_until: t('No end time given for this hold.'), wait_until: t('No end time given for this pause.') };
const zone = Intl.DateTimeFormat().resolvedOptions().timeZone;
$('timezone').textContent = t("Times in ") + zone;
function node(tag, cls, text) { const n = document.createElement(tag); if (cls) n.className = cls; if (text !== undefined) n.textContent = text; return n; }
function append(parent, ...children) { parent.append(...children.filter(Boolean)); return parent; }
function button(text, action, cls = '') { const b = node('button', cls, text); b.type = 'button'; b.addEventListener('click', action); return b; }
function prose(text) {
  const root = node('div', 'prose');
  const headings = new Set(['Result', 'Scope', 'Why', 'Evidence', 'Pending', 'Checks', 'Limits', 'What changed', 'Next step', 'Resultado', 'Alcance', 'Por qué', 'Evidencia', 'Pendiente', 'Comprobaciones', 'Límites', 'Qué cambió', t("Next step")]);
  let lines = [], list = null;
  const flush = () => { if (lines.join('\n').trim()) root.append(node('p', '', lines.join('\n').trim())); lines = []; };
  for (const line of (text || t("Nothing to show here yet.")).split('\n')) {
    const heading = line.match(/^#{1,6}\s+(.+)/), item = line.match(/^\s*(?:[-*]|\d+[.)])\s+(.+)/);
    if (heading || headings.has(line.trim())) { flush(); list = null; root.append(node('h3', '', heading ? heading[1] : line.trim())); }
    else if (item) {
      flush(); const tag = /^\s*\d/.test(line) ? 'ol' : 'ul';
      if (!list || list.tagName.toLowerCase() !== tag) { list = node(tag); root.append(list); }
      list.append(node('li', '', item[1]));
    }
    else { list = null; if (!line.trim()) flush(); else lines.push(line); }
  }
  flush(); return root;
}
function contextRow(title, text) { return append(node('div', 'context-row'), node('h3', '', title), node('p', '', text || t("Not recorded."))); }
function disclosure(title, ...children) { return append(node('details', 'source-details'), node('summary', '', title), ...children); }
function timeView(event) {
  const time = node('span', 'relative-time', eventTime(event));
  time.dataset.event = JSON.stringify(event || null); time.setAttribute('aria-live', 'off');
  return append(node('details', 'event-time'), append(node('summary'), time, node('span', 'sr-only', t(' · Show exact local date'))), node('p', 'exact-time', exactTime(event)));
}
// One coordinating space is not news; it is named only when there are several.
const manyHomes = () => (S.board?.homes || []).length > 1;
// Without a recorded time there is nothing to show; Technical details say why.
const timed = (event) => !!event && (event.date || Number.isFinite(event.epoch));
function detailMeta(owner, event, project) { $('detail-meta').replaceChildren(projectTag(project) || '', manyHomes() ? node('span', '', t('Coordinated by {owner}', {owner})) : '', timed(event) ? timeView(event) : ''); }
// A stable small number for a text, so one project keeps one color and one
// stack keeps one element id across reads and views.
function hashOf(text) { let h = 2166136261; for (const ch of String(text)) { h ^= ch.codePointAt(0); h = Math.imul(h, 16777619); } return h >>> 0; }
// The project Firstmate recorded for a task, in its own color everywhere.
function projectTag(project) {
  if (!project?.label) return null;
  const n = node('span', 'project-tag', project.label); n.dataset.hue = String(hashOf(project.id) % 8);
  return n;
}
// A card's age: compact text, the exact date on hover and in the detail.
function agoView(event) {
  const n = node('span', 'card-ago', ago(event));
  n.dataset.event = JSON.stringify(event || null); n.title = t(event?.label || 'Event') + ' · ' + exactTime(event);
  return n;
}
function refreshTimes() {
  for (const n of document.querySelectorAll('.relative-time')) {
    const text = eventTime(JSON.parse(n.dataset.event));
    if (n.textContent !== text) n.textContent = text;
  }
  for (const n of document.querySelectorAll('.card-ago')) {
    const text = ago(JSON.parse(n.dataset.event));
    if (n.textContent !== text) n.textContent = text;
  }
}
// Who's on it, in one line: the agent and, while underway, what it is doing.
function whoText(card) {
  const harness = card.presentation?.executor?.harness, agent = card.activity?.agent || 'unknown';
  const name = harness ? {codex: 'Codex', claude: 'Claude Code', pi: 'Pi'}[harness] || harness : '';
  if (card.bucket !== 'underway') return name;
  // A pane's presence does not establish current agent activity.
  if (activityUnconfirmed(card)) return [name, t('Activity unconfirmed')].filter(Boolean).join(' · ');
  return name ? [name, agentStates[agent]].filter(Boolean).join(' · ') : agent === 'unknown' ? '' : agentLines[agent] || '';
}
// On a card the return date is its own tag, so the chip drops it (short).
function activityText(card, short = false) {
  const a = card.activity || {}, until = a.until && !short ? untilText(a.until) : '';
  if (a.kind === 'paused') return until ? t('Paused until {date}', {date: until}) : t('Paused');
  if (a.kind === 'deferred') return until ? t('Deferred until {date}', {date: until}) : t('Deferred');
  if (a.kind === 'decision') return card.bucket === 'underway' ? t('Waiting for your answer') : '';
  if (a.kind === 'last_seen_working') return t('Working when last seen') + (a.seen_at ? ' · ' + relative(a.seen_at) : '');
  if (a.kind === 'blocked' || card.context?.phase === 'dependency') {
    const deps = card.dependencies || [];
    if (deps.some(d => d.unresolved !== false && !d.reconciliation_required && d.state !== 'done')) return t('Waiting on another task');
    if (deps.length) return t('Dependencies need a check');
    return t('Status unclear');
  }
  if (a.kind === 'unknown' && ['unknown', 'active'].includes(card.context?.phase)) return t('Activity unconfirmed');
  return { working: t('Working now'), validating: t('Checks running'), validation_gate: t('Checks need approval'), blocked: t('Waiting on another task'),
    held: t('On hold'), finished: t('Done, not closed yet'), stopped: t('Stopped'), ready: t('Ready to start'), queued: t('Queued') }[a.kind] || '';
}
function phaseText(card, short = false) {
  if (card.answered) return t('Answer saved');
  if (card.bucket === 'landed') return t('Delivered');
  const run = card.presentation?.run_step;
  if (run) return runLabels[run.state];
  return activityText(card, short) || (card.bucket === 'charted' && card.context.phase === 'unknown' ? t('Queued') : card.context.label || phaseLabels[card.context.phase]);
}
// A task's workflow and the observation of its agent are separate facts.
// Unknown Codex stays unknown even when a pane exists or the last report says working.
function activityUnconfirmed(card) {
  if (card.native && 'unconfirmed' in card.native) return card.native.unconfirmed !== false || !!card.native.retained_at ||
    !['pane', 'current_state.pane'].includes(card.native.source);
  return !card.activity || ['unknown', 'last_seen_working'].includes(card.activity.kind);
}
function workingText(cards, short = false) {
  const live = cards.filter(c => !c.answered);
  const working = live.filter(c => c.activity?.kind === 'working').length;
  const unknown = live.filter(c => c.activity?.kind !== 'working' && activityUnconfirmed(c)).length;
  if (unknown && !working) return t('Activity unconfirmed: {count}', {count: unknown});
  const text = short || unknown ? t('{count} working', {count: working}) : t('{count} working now', {count: working});
  return unknown ? text + ' · ' + t('{count} unconfirmed', {count: unknown}) : text;
}
function activityCount(columns, id, kind) { return (columns.find(c => c.id === id)?.cards || []).filter(x => !x.answered && x.activity?.kind === kind).length; }
function dependencyText(card) {
  // The crew is named only when there are several.
  const crew = (d) => manyHomes() ? ' · ' + (d.owner === '(main)' ? card.owner : d.owner || t('crew not recorded')) : '';
  return card.dependencies.map(d => (d.title || d.id) + crew(d) +
    (d.reconciliation_required ? t(' · needs a check') : d.unresolved === false ? t(' · resolved') : '')).join('; ');
}
function notice(text, error = false) { $('notice').hidden = !text; $('notice').className = 'notice' + (error ? ' error' : ''); $('notice').textContent = text; }
function syncSubmit() {
  const call = S.modal?.type === 'call' ? S.modal.ticket?.call : null;
  if (!call) return;
  const blocked = S.busy || !S.online || !!S.modal.checkFailed || !!S.receipt;
  for (const b of $('detail-content').querySelectorAll('.option-choice')) {
    b.disabled = blocked;
    b.setAttribute('aria-pressed', String(b.dataset.value === S.modal.value));
  }
  const submit = $('answer-submit');
  if (submit) submit.disabled = blocked || !!S.modal.draftNeedsReview || !$('answer-note')?.value.trim();
}
const retryable = (message) => Object.assign(new Error(message), {retry: true});
async function api(path, body) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), body ? 100000 : 20000);
  try {
    const response = await fetch(path, { method: body ? 'POST' : 'GET', cache: 'no-store', credentials: 'same-origin',
      headers: body ? { 'Content-Type': 'application/json', 'X-Deck-CSRF': S.csrf } : {},
      body: body ? JSON.stringify(body) : undefined, signal: controller.signal });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || t('Request failed ({status}).', {status: response.status}));
    return data;
  } catch (err) {
    if (err.name === 'AbortError') throw retryable(t('Deck took too long to answer.'));
    if (err instanceof TypeError) throw retryable(t('Couldn’t reach Deck.'));
    if (err instanceof SyntaxError) throw retryable(t('Couldn’t read Deck’s reply.'));
    throw err;
  } finally { clearTimeout(timer); }
}
function sourceStatus() {
  if (!S.board) return;
  const sources = S.board.sources || [];
  const latest = sources.map(x => x.received_at).filter(x => x > 0);
  const at = latest.length ? Math.min(...latest) : 0;
  const stale = at && Date.now() / 1000 - at > Math.max(60, S.board.source_ttl * 2);
  const failed = S.board.error || sources.some(s => s.error);
  const disconnected = !S.online || Date.now() - S.lastRead > 30000;
  // Freshness is one short phrase; the exact read time is on hover.
  const read = {label: 'Source read', epoch: S.lastRead / 1000};
  const connectionText = disconnected ? t("Offline") : failed ? t("Update failed") : stale ? t("Out of date") : S.board.loading ? t("Loading…") : t('updated {ago}', {ago: ago(read)});
  if ($('connection').textContent !== connectionText) $('connection').textContent = connectionText;
  $('connection').dataset.level = disconnected || failed ? 'error' : stale ? 'warning' : 'ok';
  $('connection').title = S.lastRead ? t('Source read') + ' · ' + exactTime(read) : '';
  if (disconnected) notice(t("You’re offline. Your draft is safe. Check the question again once you’re back."));
  else if (failed) notice(t("Couldn’t refresh one source. Some cards may be out of date."));
  else if (stale) notice(t("This view is a bit old. Waiting for a fresh update."));
  else notice('');
  syncSubmit();
}
function selectColumn(column, scroll = false) {
  S.column = column;
  for (const section of $('board').children) section.dataset.selected = String(section.dataset.kind === column);
  for (const b of $('columns-nav').children) b.setAttribute('aria-pressed', String(b.dataset.kind === column));
  if (scroll && matchMedia('(max-width: 639px)').matches) $('columns-nav').scrollIntoView({ block: 'start', behavior: 'smooth' });
}
// Up next work held until a later date; it never counts as ready to start.
const isLater = (card) => card.bucket === 'charted' && card.activity?.kind === 'deferred';
function syncCards(list, cards, retained, stacks = new Map()) {
  for (const [index, card] of cards.entries()) {
    if (card.stack) { place(list, index, syncStack(card, retained, stacks)); continue; }
    // Source freshness is independent of card progress. Retain unchanged
    // controls, an open detail and focus across polls.
    const signature = JSON.stringify({...card, supervisor: null, context: {...card.context, firstmate_action: null}, homes: manyHomes()});
    let item = retained.get(card.key); retained.delete(card.key);
    if (!item || item.dataset.signature !== signature) {
      const old = item, fresh = renderCard(card), focused = !!old?.firstChild?.contains(document.activeElement);
      // A card holding the open detail swaps only its face, so a draft and
      // its focus never leave the page.
      if (old?.contains($('detail'))) { old.firstChild.replaceWith(fresh.firstChild); item = old; }
      else { item = fresh; old?.replaceWith(item); }
      item.dataset.signature = signature;
      if (focused) item.querySelector('.card-open').focus({preventScroll: true});
    }
    place(list, index, item);
  }
}
function place(list, index, item) {
  if (list.children[index] === item) return;
  const focused = item.contains(document.activeElement) ? document.activeElement : null;
  list.insertBefore(item, list.children[index] || null);
  focused?.focus({preventScroll: true});
}
// Two or more questions from one project wait together in one stack, where
// the first of them stands in the column.
function stacked(cards) {
  const groups = new Map();
  for (const card of cards) if (card.project) groups.set(card.project.id, [...groups.get(card.project.id) || [], card]);
  const out = [], placed = new Set();
  for (const card of cards) {
    const members = card.project ? groups.get(card.project.id) : null;
    if (!members || members.length < 2) out.push(card);
    else if (!placed.has(card.project.id)) { placed.add(card.project.id); out.push({stack: card.project, cards: members}); }
  }
  return out;
}
function stackFace(entry) {
  const waiting = entry.cards.filter(card => !card.answered);
  const project = entry.stack.label;
  const title = !waiting.length ? t('{project}: all answered', {project})
    : waiting.length === 1 ? t('1 question from {project}', {project}) : t('{count} questions from {project}', {count: waiting.length, project});
  // Its age is the newest question's; the line previews the first one.
  const newest = entry.cards.map(card => card.time).reduce((a, b) => (b?.epoch ?? -Infinity) > (a?.epoch ?? -Infinity) ? b : a);
  const first = waiting[0], rest = waiting.length - 1;
  const line = first ? leadLine(first.title) + (rest ? ' ' + t('and {count} more', {count: rest}) : '') : '';
  return {title, newest, line};
}
function syncStack(entry, retained, stacks) {
  const key = 'stack:' + entry.stack.id;
  let item = stacks.get(key); stacks.delete(key);
  if (!item) {
    item = node('article', 'call-stack'); item.dataset.key = key;
    const face = button('', () => toggleStack(key), 'stack-open'); face.dataset.key = key;
    const more = node('div', 'stack-more'); more.id = 'stack-' + hashOf(entry.stack.id).toString(36);
    face.setAttribute('aria-controls', more.id);
    append(item, face, append(more, node('div', 'cards stack-cards')));
  }
  const face = item.firstChild, {title, newest, line} = stackFace(entry);
  const signature = JSON.stringify({project: entry.stack, title, newest, line});
  if (face.dataset.signature !== signature) {
    face.replaceChildren(append(node('div', 'card-meta'), projectTag(entry.stack), agoView(newest)), node('h3', 't', title), line && node('p', 'card-line', line));
    face.dataset.signature = signature;
  }
  // A stack that holds the open detail stays open.
  if (detailOpen() && entry.cards.some(card => card.key === S.openerKey)) S.openStacks.add(key);
  showStack(item, S.openStacks.has(key));
  syncCards(item.querySelector('.stack-cards'), entry.cards, retained);
  return item;
}
function showStack(item, open) {
  item.dataset.open = String(open); item.firstChild.setAttribute('aria-expanded', String(open));
  // Closed, its questions stay out of reach of Tab and screen readers.
  item.lastChild.inert = !open;
}
function toggleStack(key) {
  const item = [...$('board').querySelectorAll('.call-stack')].find(n => n.dataset.key === key);
  if (!item) return;
  const open = !S.openStacks.has(key);
  if (open) S.openStacks.add(key); else S.openStacks.delete(key);
  // Folding a stack also closes the detail of a question inside it.
  if (!open && detailOpen() && [...item.querySelectorAll('.task-card')].some(n => n.dataset.key === S.openerKey)) closeDetail(false);
  showStack(item, open);
}
function renderBoard() {
  if (!S.board) return;
  const columns = S.board.columns;
  const counts = Object.fromEntries(columns.map(c => [c.id, c.cards.filter(x => !x.answered && !isLater(x)).length]));
  // Underway holds every open task; only these agents are working right now.
  const working = activityCount(columns, 'underway', 'working'), ready = activityCount(columns, 'charted', 'ready');
  const calls = counts.captains_call || 0;
  // A phone opens on Needs you when something waits for an answer, else on
  // Underway. Chosen once, on the first full read; then only a tap changes it.
  if (!S.columnPicked && !S.board.loading) { S.columnPicked = true; S.column = calls ? 'captains_call' : 'underway'; }
  const overview = (calls === 1 ? t('{count} needs you', {count: calls}) : t('{count} need you', {count: calls})) + ' · ' + workingText(columns.find(c => c.id === 'underway')?.cards || [], true) + ' · ';
  if ($('overview').textContent !== overview) $('overview').textContent = overview;
  const nav = [];
  for (const c of columns) {
    const b = button('', () => { S.columnPicked = true; selectColumn(c.id); }); b.dataset.kind = c.id; b.setAttribute('aria-pressed', String(S.column === c.id));
    append(b, node('span', '', String(counts[c.id] || 0)), document.createTextNode(labels[c.id] || c.title)); nav.push(b);
  }
  const navIdentity = JSON.stringify({counts, working, ready, order: columns.map(c => c.id)});
  if ($('columns-nav').dataset.signature !== navIdentity) {
    const focusedKind = $('columns-nav').contains(document.activeElement) ? document.activeElement.dataset.kind : null;
    $('columns-nav').replaceChildren(...nav);
    $('columns-nav').dataset.signature = navIdentity;
    if (focusedKind) [...$('columns-nav').children].find(n => n.dataset.kind === focusedKind)?.focus({preventScroll: true});
  }
  const existing = new Map([...$('board').children].map(n => [n.dataset.kind, n]));
  for (const c of columns) {
    let section = existing.get(c.id);
    if (!section) { section = node('section', 'column'); section.dataset.kind = c.id; $('board').append(section); }
    const shown = c.cards.filter(card => (S.query === '' || [card.project?.label, card.title, card.question, card.context.reason, card.owner, card.task, card.presentation?.status, card.presentation?.summary, card.presentation?.next_action, card.presentation?.waiting_for, card.presentation?.executor?.harness,
      ...card.dependencies.map(d => d.title || d.id)].join(' ').toLocaleLowerCase(locale).includes(S.query)));
    const cards = shown.filter(card => !isLater(card)), later = shown.filter(isLater);
    const sub = c.id === 'underway' ? workingText(cards)
      : c.id === 'charted' ? t('{count} ready to start', {count: activityCount([{id: c.id, cards}], c.id, 'ready')}) : '';
    const heading = append(node('div', 'column-heading'), node('span', 'column-dot'), node('h2', '', labels[c.id] || c.title), sub && node('span', 'column-sub', sub), node('span', 'column-count', String(cards.length)));
    let list = section.querySelector('.cards');
    if (!list) { list = node('div', 'cards'); section.append(heading, list); }
    else section.firstChild.replaceWith(heading);
    // The ready list comes first, Later after it; a card can move between
    // them, so both share one pool.
    const keys = new Set(shown.map(card => card.key));
    const retained = new Map();
    for (const n of section.querySelectorAll('.task-card')) if (keys.has(n.dataset.key)) retained.set(n.dataset.key, n); else { rescueDetail(n); n.remove(); }
    for (const n of [...list.children]) if (!n.dataset.key) n.remove();
    const stacks = new Map([...section.querySelectorAll('.call-stack')].map(n => [n.dataset.key, n]));
    syncCards(list, c.id === 'captains_call' ? stacked(cards) : cards, retained, stacks);
    for (const n of stacks.values()) { rescueDetail(n); n.remove(); }
    // Postponed work waits below the cards that are ready, with its return date.
    let block = section.querySelector('.later');
    if (later.length) {
      if (!block) { block = append(node('div', 'later'), node('div', 'later-heading'), node('div', 'cards')); section.append(block); }
      block.firstChild.replaceChildren(node('h3', '', t('Later')), node('span', 'column-count', String(later.length)));
      syncCards(block.querySelector('.cards'), later, retained);
    } else if (block) block.remove();
    if (!cards.length && !later.length) list.replaceChildren(node('p', 'empty', S.query ? t("No tasks match your search.") : c.id === 'captains_call' ? t("Nothing needs you right now.") : c.id === 'landed' ? t("Nothing finished recently. Try History for older work.") : t("Nothing here.")));
  }
  for (const n of [...$('board').children]) if (!n.dataset.kind) n.remove();
  selectColumn(S.column); refreshTimes(); renderSupervisors(); sourceStatus();
}
const flatten = (text) => String(text || '').replace(/\s+/g, ' ').trim().toLocaleLowerCase(locale);
// One line of what is happening: authored text only, never a placeholder.
// A long text leads with its first full sentence; the title is never cut.
function leadLine(text) {
  const flat = String(text || '').replace(/\s+/g, ' ').trim();
  if (flat.length <= 140) return flat;
  for (const end of flat.matchAll(/[.!?](?=\s+[\p{Lu}¿¡]|$)/gu))
    if (end.index >= 40 && !abbreviation(flat.slice(0, end.index))) return flat.slice(0, end.index + 1);
  return flat;
}
function cardLine(card) {
  const p = card.presentation || {};
  if (card.bucket === 'landed') return leadLine(card.summary);
  // A call's line is its question, unless the title already asks it.
  const same = (text) => flatten(text) === flatten(card.title);
  if (card.bucket === 'captains_call') return leadLine([card.question, p.status, p.summary].find(text => text && !same(text)));
  // What is happening now, as Firstmate wrote it for the card, comes first.
  const waiting = repeatsDependencies(card, p.waiting_for) ? '' : p.waiting_for;
  const after = card.dependencies.length ? t('After: {dependency}', {dependency: card.dependencies.map(d => d.title || d.id).join(', ')}) : '';
  return leadLine([p.status, p.summary, waiting || after, p.next_action].find(text => text && !same(text)));
}
// A wait that only names the tasks it depends on says nothing new.
function repeatsDependencies(card, text) {
  if (!text || !card.dependencies.length) return false;
  const names = card.dependencies.map(d => flatten(d.title || d.id));
  return flatten(text).split(/\s*[,;]\s*/).every(part => names.includes(part)) || flatten(text) === names.join(', ');
}
function renderCard(card) {
  const cardNode = node('article', 'task-card'); cardNode.dataset.key = card.key;
  const b = button('', () => openCard(card, b), 'card-open'); b.dataset.key = card.key;
  b.setAttribute('aria-expanded', 'false'); b.setAttribute('aria-controls', 'detail');
  // A fixed top row (project, state, crew, return date, age), then the full title.
  const meta = node('div', 'card-meta');
  const tag = projectTag(card.project); if (tag) meta.append(tag);
  // Under Later the section and the return date already say it.
  const chip = card.bucket === 'landed' || (card.bucket === 'captains_call' && !card.answered) || isLater(card) ? '' : phaseText(card, true);
  if (chip) {
    const phase = node('span', 'phase', chip); phase.dataset.phase = card.presentation?.run_step ? 'waiting' : card.context.phase;
    phase.dataset.activity = card.answered ? '' : card.activity?.kind || ''; meta.append(phase);
  }
  if (manyHomes()) meta.append(node('span', 'card-tag', card.owner));
  const until = card.activity?.until ? shortDate(card.activity.until) : '';
  if (until) meta.append(node('span', 'card-date', until));
  meta.append(agoView(card.time));
  const line = cardLine(card);
  append(b, meta, node('h3', 't', card.title), line && node('p', 'card-line', line));
  // On a phone the detail opens inside its card; only that card grows.
  const more = append(node('div', 'card-more'), node('div', 'card-more-inner'));
  if (detailOpen() && S.openerKey === card.key) { b.setAttribute('aria-expanded', 'true'); cardNode.dataset.current = 'true'; }
  return append(cardNode, b, more);
}
function openCard(card, opener) {
  if (detailOpen() && S.openerKey === card.key) { closeDetail(); return; }
  if (card.bucket === 'landed' && card.home) openDelivery(card.home, card.task, opener); else openTicket(card.key, opener);
}
function supervisorText(s) {
  if (!s || !s.observed_at) return t("Firstmate hasn’t said what it’s doing.");
  const epoch = Date.parse(s.observed_at) / 1000;
  const ttl = Number(s.ttl || s.ttl_seconds || 0);
  const stale = s.freshness === 'stale' || (ttl > 0 && Date.now() / 1000 - epoch > ttl);
  const state = { coordinating: t("coordination"), waiting: t("waiting"), idle: t("idle") }[s.state] || t("unknown state");
  const unknown = !Number.isFinite(epoch) || ttl <= 0 || s.freshness === 'unknown' || epoch > Date.now() / 1000 + 5;
  return t('{declaration} {summary} {time} Current model activity is unknown.', {declaration: stale ? t("Firstmate’s last update expired.") : unknown ? t("Freshness unconfirmed; current activity is unknown.") : t("Declared ") + state + '.', summary: s.summary || t("No purpose recorded."), time: Number.isFinite(epoch) ? dateFmt.format(new Date(epoch * 1000)) + ' · ' + relative(epoch) + '.' : t("Time cannot be verified.")});
}
function renderSupervisors() {
  const current = new Map([...$('supervisors').children].map(n => [n.dataset.home, n]));
  for (const source of S.board.sources || []) {
    const s = source.supervisor, observed = Date.parse(s?.observed_at) / 1000, ttl = Number(s?.ttl || s?.ttl_seconds || 0);
    const expired = s?.freshness === 'stale' || (ttl > 0 && Date.now() / 1000 - observed > ttl);
    const signature = JSON.stringify({supervisor: s, error: source.error, expired, received: source.received_at, generated: source.generated});
    const old = current.get(source.home); current.delete(source.home);
    if (old?.dataset.signature === signature) continue;
    const row = node('div', 'supervisor');
    row.dataset.home = source.home; row.dataset.signature = signature;
    // One phrase: the board's own freshness is in the top bar, so this says
    // only what Firstmate last told, and when; the read times are in details.
    const status = source.error ? t(source.error) : !source.received_at ? t('No confirmed read')
      : !s?.observed_at ? t('Firstmate hasn’t said what it’s doing.')
      : expired ? t('Firstmate’s last update was {time}.', {time: relative(observed)})
      : (s.summary ? s.summary + ' · ' + relative(observed) : t('Firstmate posted an update {time}.', {time: relative(observed)}));
    const receipt = source.received_at ? timeView({label: 'Source received', epoch: source.received_at}) : null;
    if (receipt) receipt.dataset.role = 'receipt';
    const snapshot = timeView({label: 'Data generated', epoch: Date.parse(source.generated) / 1000});
    snapshot.dataset.role = 'snapshot';
    const detailsView = disclosure(t('Source details'), prose(supervisorText(s)), receipt, snapshot);
    detailsView.dataset.role = 'source';
    const line = node('p', '', status); line.dataset.role = 'status';
    append(row, node('strong', '', source.label), append(node('div'), line, detailsView));
    if (old) {
      const focusedRole = old.contains(document.activeElement) ? document.activeElement.closest('details')?.dataset.role : null;
      const openByRole = Object.fromEntries([...old.querySelectorAll('details')].map(d => [d.dataset.role, d.open]));
      const views = [...row.querySelectorAll('details')];
      for (const d of views) d.open = !!openByRole[d.dataset.role];
      old.replaceWith(row);
      const target = views.find(d => d.dataset.role === focusedRole) || views.find(d => d.dataset.role === 'source');
      if (focusedRole) target.querySelector('summary').focus({preventScroll: true});
    } else $('supervisors').append(row);
  }
  for (const old of current.values()) old.remove();
}
async function poll() {
  if (document.hidden) return;
  const selected = S.home;
  try {
    const data = await api('api/board' + (selected ? '?home=' + encodeURIComponent(selected) : ''));
    if (selected !== S.home) return;
    S.online = true; S.lastRead = Date.now(); S.csrf = data.csrf;
    // Failed collection must not erase an already useful board.
    if (!(data.error && !data.columns.some(c => c.cards.length) && S.board)) S.board = data;
    else { S.board.error = data.error; S.board.sources = data.sources; }
    const identity = JSON.stringify(data.homes);
    if ($('crew').dataset.homes !== identity) {
      $('crew').dataset.homes = identity;
      $('crew').replaceChildren(new Option(t("All"), ''), ...data.homes.map(h => new Option(h.label, h.id)));
      $('crew').value = S.home;
    }
    // A single coordinating space needs no picker.
    $('crew').parentElement && ($('crew').parentElement.hidden = data.homes.length < 2 && !S.home);
    renderBoard();
    loadReceipts();
    if (!S.linkHandled && data.homes.length && !data.loading) {
      S.linkHandled = true;
      const params = new URLSearchParams(location.search);
      if (params.has('task')) openStableLink(params);
    }
    if (S.modal?.type === 'call' && !S.busy && !S.receipt) checkCall();
    if (S.modal?.type === 'delivery' && S.modal.explanation) refreshExplanation();
  } catch (err) { S.online = false; sourceStatus(); if (!S.board) { $('board').replaceChildren(node('p', 'empty', t("Could not load the board. ") + err.message)); notice(t("No read has been confirmed. Try Refresh."), true); $('connection').textContent = t("Unavailable"); } }
}
function setView(view) {
  S.view = view; $('board-view-panel').hidden = view !== 'board'; $('history-panel').hidden = view !== 'history';
  // History has its own search; the top one filters the board.
  $('board-search').hidden = view !== 'board';
  $('history-view').setAttribute('aria-pressed', String(view === 'history'));
  if (view === 'history') loadHistory();
}
async function loadHistory() {
  const generation = ++S.historyGeneration;
  $('history-status').textContent = t("Searching…");
  try {
    const query = new URLSearchParams({ home: S.home, q: $('history-search').value });
    const data = await api('api/history?' + query);
    if (generation !== S.historyGeneration) return;
    const count = data.records.length === 1 ? t('1 delivery') : t('{count} deliveries', {count: data.records.length});
    $('history-status').textContent = count + (data.omitted ? t(' · {count} more results; refine your search', {count: data.omitted}) : '');
    rescueDetail($('history-results'));
    // One line per delivery, newest first as the bridge sorts it: project,
    // crew tag when there are several, the full title, then how long ago.
    $('history-results').replaceChildren(...data.records.map(row => {
      const key = 'history:' + row.home + ':' + row.task;
      const b = button('', () => detailOpen() && S.openerKey === key ? closeDetail() : openDelivery(row.home, row.task, b), 'card-open history-row');
      b.dataset.key = key; b.setAttribute('aria-expanded', 'false'); b.setAttribute('aria-controls', 'detail');
      append(b, projectTag(row.project), manyHomes() ? node('span', 'card-tag', row.owner) : null, node('h3', 't', row.title), agoView(row.time));
      const article = append(node('article', 'task-card'), b, append(node('div', 'card-more'), node('div', 'card-more-inner')));
      article.dataset.key = key; return article;
    }));
    if (!data.records.length) $('history-results').append(node('p', 'empty', t("No matches.")));
    $('history-sources').replaceChildren(node('p', 'muted', data.scope), ...data.details.map(d => contextRow(d.home, d.source)));
  } catch (err) { if (generation === S.historyGeneration) $('history-status').textContent = t("Could not refresh history. ") + err.message; }
}
// The detail is never a dialog. On a wide screen it fills the panel kept
// beside the board, so no card moves; on a phone it opens inside its card.
const phoneLayout = matchMedia('(max-width: 899px)');
function detailOpen() { return $('detail').dataset.open === 'true'; }
function hostCard() { return $('detail').parentElement?.parentElement?.closest?.('.task-card') || null; }
const cardFaces = () => [...$('board').querySelectorAll('.card-open'), ...$('history-results').querySelectorAll('.card-open')];
function markCurrent() {
  for (const b of cardFaces()) {
    const current = detailOpen() && b.dataset.key === S.openerKey;
    b.setAttribute('aria-expanded', String(current));
    const card = b.parentElement; if (card) card.dataset.current = String(current);
  }
}
function parkDetail() {
  const detail = $('detail'), old = hostCard();
  if (old) old.dataset.open = 'false';
  if (detail.parentElement !== $('detail-panel')) $('detail-panel').append(detail);
  detail.hidden = !detailOpen(); $('detail-empty').hidden = detailOpen();
  // A phone shows the panel only while it holds a detail with no card.
  $('detail-panel').dataset.active = String(detailOpen());
}
function placeDetail(opener) {
  const detail = $('detail'), card = phoneLayout.matches ? opener?.closest?.('.task-card') : null;
  const old = hostCard();
  if (!card) { parkDetail(); return; }
  if (old && old !== card) old.dataset.open = 'false';
  detail.hidden = false; $('detail-empty').hidden = false; $('detail-panel').dataset.active = 'false';
  const slot = card.querySelector('.card-more-inner');
  if (detail.parentElement !== slot) { slot.append(detail); void card.offsetHeight; }
  card.dataset.open = 'true';
  const top = card.getBoundingClientRect?.().top;
  if (top > window.innerHeight * 0.6) card.scrollIntoView({block: 'start', behavior: 'smooth'});
}
// A card that leaves the board hands its detail back to the panel, so a
// receipt is never lost.
function rescueDetail(n) { if (n.contains($('detail'))) parkDetail(); }
function showDetail(title, meta, kicker, opener) {
  S.opener = opener || document.activeElement; S.receipt = null;
  S.openerKey = S.opener?.dataset?.key;
  $('detail-title').textContent = title; $('detail-meta').textContent = meta; $('detail-kicker').textContent = kicker;
  $('detail-content').replaceChildren(node('p', 'muted', t("Loading…")));
  $('detail').dataset.open = 'true';
  placeDetail(S.opener); markCurrent();
  // In a card, focus stays on the card and the detail follows it.
  if (!hostCard()) $('detail-title').focus({preventScroll: true});
}
function closeDetail(restoreFocus = true) {
  if (!detailOpen()) return;
  saveDraft(); S.modal = null; S.generation++;
  const card = hostCard();
  $('detail').dataset.open = 'false'; markCurrent();
  if (card) { card.dataset.open = 'false'; setTimeout(() => { if (!detailOpen()) parkDetail(); }, 220); }
  else parkDetail();
  if (!restoreFocus) return;
  const current = S.opener?.isConnected && S.opener.tagName !== 'BODY' ? S.opener : cardFaces().find(n => n.dataset.key === S.openerKey) || $('refresh');
  current.focus({ preventScroll: true });
}
function readDraft(key) { try { return JSON.parse(sessionStorage.getItem('deck-draft-' + key)) || {}; } catch { return S.drafts.get(key) || {}; } }
function saveDraft(event) {
  if (S.modal?.type !== 'call' || !$('answer-note') || S.receipt?.saved) return;
  if (event && ['input', 'change'].includes(event.type)) S.modal.draftNeedsReview = false;
  const draft = { note: $('answer-note').value, value: S.modal.value || '', request: S.modal.request || crypto.randomUUID().replaceAll('-', ''), occurrence: S.modal.draftNeedsReview ? S.modal.previousOccurrence : S.modal.ticket.call.occurrence };
  S.modal.request = draft.request; S.drafts.set(S.modal.key, draft);
  try { sessionStorage.setItem('deck-draft-' + S.modal.key, JSON.stringify(draft)); } catch { /* In-memory draft remains available. */ }
  syncSubmit();
}
async function openTicket(key, opener) {
  const gen = ++S.generation;
  showDetail(t("Task"), '', t("DETAILS"), opener);
  S.modal = { type: 'loading', key };
  try {
    const ticket = await api('api/ticket/' + encodeURIComponent(key));
    if (gen !== S.generation || !detailOpen()) return;
    $('detail-title').textContent = ticket.title; detailMeta(ticket.owner, ticket.time, ticket.project);
    if (ticket.call) renderCall(ticket);
    else {
      S.modal = { type: 'task', key, ticket };
      const c = ticket.context, p = ticket.presentation || {};
      $('detail-kicker').textContent = phaseText(ticket);
      // Only what is recorded: a missing fact is no row, never a placeholder.
      const lead = [p.status, p.summary].filter((text, i, all) => text && all.indexOf(text) === i && flatten(text) !== flatten(ticket.title));
      $('detail-content').replaceChildren(...lead.map(text => prose(text)));
      const who = whoText(ticket);
      if (who) $('detail-content').append(contextRow(t('Who’s on it'), who));
      const a = ticket.activity || {}, until = a.until ? untilText(a.until) : '';
      if (ticket.dependencies.length) $('detail-content').append(contextRow(t('Dependencies'), dependencyText(ticket)));
      const waitingFor = (repeatsDependencies(ticket, p.waiting_for) ? '' : p.waiting_for) || (until ? t('Until {date}', {date: until}) : a.kind === 'validation_gate' ? t('Someone to approve its checks.') : '');
      if (!['ready', 'queued'].includes(a.kind) && waitingFor) $('detail-content').append(contextRow(t('Waiting for'), waitingFor));
      if (gapLines[a.gap]) $('detail-content').append(node('p', 'activity-note', gapLines[a.gap]));
      if (p.next_action) $('detail-content').append(contextRow(t('Next step'), p.next_action));
      // Work that waits for the captain outside a Captain's Call is answered in Firstmate.
      if (a.kind === 'decision') $('detail-content').append(node('p', 'activity-note', t('You can’t answer this one from the web. Answer it in Firstmate.')));
      addTechnical(ticket); addHerdr(ticket);
    }
  } catch (err) { if (gen === S.generation) $('detail-content').replaceChildren(node('p', 'notice error', err.message)); }
}
function addTechnical(ticket, ...extra) {
  const evidence = node('pre', 'technical-evidence', JSON.stringify({task: ticket.task, native: ticket.native, activity: ticket.activity, dependencies: ticket.dependencies, context: ticket.context}, null, 2));
  const report = ticket.report?.text ? contextRow(t('Full recorded report'), ticket.report.text) : null;
  const details = disclosure(t('Technical details'), report,
    contextRow(t('Recorded agent'), ticket.agent || t('Not recorded.')),
    contextRow(t('Firstmate declaration'), supervisorText(ticket.supervisor)),
    contextRow(t('Date provenance'), ticket.time?.detail || t('Time not recorded')),
    ticket.report?.text ? contextRow(t('Report provenance'), ticket.report.time?.detail) : null,
    evidence, ...extra);
  if (ticket.original_title !== ticket.title) details.append(contextRow(t('Original title'), ticket.original_title));
  $('detail-content').append(details);
}
function addHerdr(ticket) { if (!ticket.herdr_url) return; const a = node('a', 'herdr-link', t("Open this agent in Herdr Web ↗")); a.href = ticket.herdr_url; a.target = '_blank'; a.rel = 'noopener noreferrer'; $('detail-content').append(a); }
function addStableLink(home, task, view) {
  if (!home) return;
  const url = new URL(location.href); url.search = new URLSearchParams({ home, task, view }).toString(); url.hash = '';
  const a = node('a', 'herdr-link', t("Link to this task ↗")); a.href = url.href;
  $('detail-content').append(a);
}
async function openStableLink(params) {
  const home = params.get('home') || '', task = params.get('task') || '', view = params.get('view') || '';
  showDetail(t("Opening task"), '', t("FIRSTMATE LINK"));
  const gen = ++S.generation;
  try {
    const result = await api('api/resolve?' + new URLSearchParams({ home, task, view, item: params.get('item') || '' }));
    if (gen !== S.generation || !detailOpen()) return;
    if (result.view === 'call') { await openTicket(result.key, S.opener); return; }
    if (result.view === 'delivery') { await openDelivery(home, task, S.opener); if (S.modal?.type === 'delivery' && result.notice) { S.modal.linkNotice = result.notice; renderDelivery(); } return; }
    S.modal = { type: 'task' };
    $('detail-title').textContent = t("The question from this notification"); $('detail-meta').textContent = result.owner + ' · ' + result.task;
    $('detail-content').replaceChildren(prose(result.message));
    for (const receipt of result.receipts || []) $('detail-content').append(receiptView(receipt));
    if (result.current_key) $('detail-content').append(button(t("View the current question"), async () => { await openTicket(result.current_key, S.opener); $('detail-kicker').textContent = t("CURRENT QUESTION"); }));
    $('detail-content').append(button(t("Find this task in history"), () => { $('history-search').value = task; closeDetail(false); setView('history'); }));
  } catch (err) { if (gen === S.generation) $('detail-content').replaceChildren(node('p', 'notice error', err.message)); }
}
// Long authored text leads with one short line; the rest opens on demand.
const abbreviation = (text) => /(?:^|[^\p{L}.])(?:vs|e\.g|i\.e|etc|approx|aprox|mrs?|dra?|sra?|p\. ?ej)$/iu.test(text) || /(?:^|[^\p{L}.])No$/u.test(text);
function shortLine(text, limit = 140) {
  const flat = String(text || '').replace(/\s+/g, ' ').trim();
  if (flat.length <= limit) return flat;
  for (const end of flat.matchAll(/[.!?](?=\s+[\p{Lu}¿¡]|$)/gu)) {
    if (end.index >= limit) break;
    if (end.index >= 40 && !abbreviation(flat.slice(0, end.index))) return flat.slice(0, end.index + 1);
  }
  return flat.slice(0, limit - 1).replace(/\s+\S*$/, '') + '…';
}
function leadText(cls, text) {
  const lead = shortLine(text), full = String(text || '').trim();
  if (lead === full.replace(/\s+/g, ' ')) return node('p', cls, full);
  return append(node('div', cls), node('p', '', lead), disclosure(t('Read more'), prose(full)));
}
// A web report opens in a new tab; a report file in the crew's folder opens
// right here, read through Deck like a delivery report.
function reportLink(link, key) {
  if (!link) return null;
  if (/^https?:\/\//i.test(link)) {
    const a = node('a', 'report-link', t('See the full report ↗')); a.href = link; a.target = '_blank'; a.rel = 'noopener noreferrer';
    return a;
  }
  const box = node('div', 'call-report'), view = node('div', 'call-report-text'); view.hidden = true;
  const open = button(t('See the full report'), async () => {
    const show = view.hidden; view.hidden = !show; open.setAttribute('aria-expanded', String(show));
    if (!show || view.dataset.loaded) return;
    view.replaceChildren(node('p', 'muted', t('Loading…')));
    try { const data = await api('api/ticket/' + encodeURIComponent(key) + '/report'); view.replaceChildren(prose(data.text)); view.dataset.loaded = 'true'; }
    catch (err) { view.replaceChildren(node('p', 'notice error', err.message)); }
  }, 'report-link');
  open.setAttribute('aria-expanded', 'false');
  return append(box, open, view);
}
function choose(value) {
  if (S.busy || S.receipt || S.modal?.type !== 'call') return;
  S.modal.value = value; S.modal.draftNeedsReview = false; $('call-change').hidden = true;
  saveDraft(); submitAnswer();
}
function renderCall(ticket, draftOverride) {
  const key = ticket.key, c = ticket.call, draft = draftOverride || readDraft(key);
  const staleDraft = !!draft.occurrence && draft.occurrence !== c.occurrence && !!(draft.note || draft.value);
  S.modal = { type: 'call', key, ticket, value: staleDraft ? '' : draft.value || '', request: draft.request || crypto.randomUUID().replaceAll('-', ''), draftNeedsReview: staleDraft, previousOccurrence: draft.occurrence, checkFailed: !!c.error };
  $('detail-kicker').textContent = t('Needs you'); $('detail-title').textContent = ticket.title;
  const content = $('detail-content'); content.replaceChildren();
  // The question appears once; the report is its link, not a path in the text.
  const howTo = c.link ? (c.detail || '').split(c.link).join('').replace(/\s+([.,;:])/g, '$1').replace(/\s{2,}/g, ' ').trim() : c.detail;
  const instructions = howTo ? contextRow(t('How to answer'), howTo) : null;
  if (instructions) instructions.className += ' call-instructions';
  const technical = [contextRow(t('Answer limits'), t('One line, up to 512 bytes. Typing only saves a draft. Picking an option sends it right away; Send sends a written answer.'))];
  if (c.background) technical.unshift(contextRow(t('Original question text'), c.background));
  if (ticket.answered) {
    $('detail-kicker').textContent = t('Answer saved');
    content.append(prose(c.presentation?.decide || c.decide), contextRow(t('What happens next'), t('Firstmate has your answer and will pick it up next.')),
      button(t('See answer status'), async () => { await loadReceipts(); $('receipts-panel').open = true; closeDetail(false); $('receipts-panel').scrollIntoView({ block: 'start' }); }));
    if (instructions) content.append(instructions);
    const link = reportLink(c.link, key); if (link) content.append(link);
    addTechnical(ticket, ...technical); addHerdr(ticket); addStableLink(ticket.home, ticket.task, 'call'); return;
  }
  const error = node('p', 'notice', c.error || (staleDraft ? staleDraftNote : '')); error.id = 'call-change'; error.hidden = !c.error && !staleDraft; content.append(error);
  const about = c.presentation?.about || c.about, question = c.presentation?.decide || c.decide;
  // The title already asks it when the two read the same.
  if (flatten(question) !== flatten(ticket.title)) content.append(node('p', 'question', question));
  if (about) content.append(leadText('call-context', about));
  const label = (option) => c.presentation?.options?.find(o => o.value === option.value) || option;
  const choices = c.options.filter(o => o.value !== 'reconcile'), recheck = c.options.find(o => o.value === 'reconcile');
  if (choices.length) {
    const list = node('div', 'option-list'); list.setAttribute('role', 'group'); list.setAttribute('aria-label', t('Pick one'));
    for (const option of choices) {
      const b = button('', () => choose(option.value), 'option-choice'); b.dataset.value = option.value;
      const text = append(node('span', 'option-text'), node('strong', '', label(option).label));
      if (option.value === c.recommend) { b.className += ' recommended-choice'; text.append(' ', node('span', 'recommended', t('Recommended'))); }
      if (option.hint) text.append(node('small', '', label(option).hint || option.hint));
      b.append(text); list.append(b);
    }
    content.append(list);
  }
  // How-to text follows the choices, so the recommended one stays in view.
  if (instructions) content.append(instructions);
  const link = reportLink(c.link, key);
  const note = node('textarea'); note.id = 'answer-note'; note.rows = 3; note.value = draft.note || ''; note.placeholder = t('Write here…'); note.addEventListener('input', saveDraft);
  const noteLabel = append(node('label', 'answer-label', c.freeform ? t('Your answer') : t('Add a note (optional)')), note);
  const send = c.freeform ? button(t('Send'), () => { S.modal.value = ''; saveDraft(); submitAnswer(); }, 'primary') : null;
  if (send) send.id = 'answer-submit';
  const ownWords = append(node('div', 'own-words'), noteLabel, send);
  if (choices.length) {
    const fold = disclosure(c.freeform ? t('Answer in my own words') : t('Add a note first (optional)'), ownWords);
    fold.className += ' own-words-fold'; fold.open = !!draft.note; content.append(fold);
  } else content.append(ownWords);
  if (recheck) {
    // The re-check carries its own one-line meaning.
    const b = button('', () => choose('reconcile'), 'option-choice secondary-choice'); b.dataset.value = 'reconcile';
    append(b, node('strong', '', t('Check again first')), node('small', '', t('Nothing gets decided yet.')));
    content.append(append(node('div', 'recheck'), b));
  }
  if (link) content.append(link);
  const state = node('div'); state.id = 'answer-status'; state.setAttribute('role', 'status'); content.append(state);
  addTechnical(ticket, ...technical); addHerdr(ticket); addStableLink(ticket.home, ticket.task, 'call'); syncSubmit();
}
async function checkCall() {
  const modal = S.modal;
  if (!modal || modal.checking) return;
  modal.checking = true;
  try {
    const latest = await api('api/ticket/' + encodeURIComponent(modal.key));
    const warning = $('call-change');
    if (S.modal !== modal || S.busy || !warning) return;
    const changed = !latest.call || latest.call.occurrence !== modal.ticket.call.occurrence;
    if (changed || latest.call.error) {
      warning.hidden = false; warning.replaceChildren(document.createTextNode(latest.call?.error || t("The question changed. Your draft is still here.")));
      if (changed && latest.call && !latest.call.error) warning.append(button(t("See the new question"), () => { saveDraft(); renderCall(latest); }));
      modal.checkFailed = true;
    } else if (modal.checkFailed) {
      modal.checkFailed = false; warning.hidden = !modal.draftNeedsReview; warning.textContent = modal.draftNeedsReview ? staleDraftNote : '';
    }
    syncSubmit();
  } catch (err) { if (S.modal === modal && $('call-change')) { $('call-change').hidden = false; $('call-change').textContent = t("Couldn’t check the question. Your draft is safe."); modal.checkFailed = true; syncSubmit(); } }
  finally { modal.checking = false; }
}
function receiptView(receipt) {
  const box = node('div', 'receipt'); box.setAttribute('role', 'status');
  append(box, node('h3', '', receipt.saved ? t("Answer saved") : receipt.saved === false ? t("Answer not sent") : t("Not sure it was sent")), node('p', '', t(receipt.message)));
  if (receipt.primary === 'unreachable') append(box, node('p', 'notice', t("Couldn’t reach Firstmate right now. It will see your answer on its next check.")));
  // Each step is a separate fact; none of them proves the agent resumed.
  const dl = node('dl'), no = t("Not yet");
  for (const [label, text] of [[t("Saved"), receipt.saved === true ? t("Yes") : no], [t("Firstmate told"), receipt.primary === 'sent' ? t("Sent to Firstmate") : receipt.notice === 'queued' ? t("Queued") : no], [t("Firstmate got it"), receipt.ack === 'acknowledged' ? t("Yes") : no], [t("Firstmate used it"), receipt.consumed === 'consumed' ? t("Yes") : no]]) append(dl, node('dt', '', label), node('dd', '', text));
  append(box, disclosure(t("Details"), dl, node('p', '', t("Saved doesn’t mean the agent has started again.")), receipt.detail ? prose(receipt.detail) : null));
  return box;
}
async function loadReceipts() {
  try {
    const data = await api('api/receipts');
    $('receipts-panel').hidden = !data.receipts.length;
    const signature = JSON.stringify(data.receipts);
    if ($('receipts').dataset.signature === signature) return;
    const expanded = [...$('receipts').querySelectorAll('details[open]')].map(d => d.dataset.request);
    const focused = $('receipts').contains(document.activeElement) ? document.activeElement.closest('[data-request]')?.dataset.request : '';
    $('receipts').replaceChildren(...data.receipts.map(r => {
      const detail = node('details', 'source-details');
      detail.dataset.request = r.request_id; detail.open = expanded.includes(r.request_id);
      append(detail, node('summary', '', (r.title || r.task) + ' · ' + dateFmt.format(new Date(r.at * 1000))), receiptView(r));
      return detail;
    }));
    $('receipts').dataset.signature = signature;
    if (focused) [...$('receipts').children].find(d => d.dataset.request === focused)?.querySelector('summary')?.focus({preventScroll: true});
  } catch { /* The board remains usable; each submit also returns its receipt. */ }
}
async function submitAnswer() {
  if (S.busy || !S.online || S.receipt || S.modal?.type !== 'call') return;
  saveDraft(); const modal = S.modal, draft = readDraft(modal.key);
  S.busy = true; syncSubmit(); $('answer-status').replaceChildren(node('p', 'muted', t("Sending…")));
  try {
    const receipt = await api('api/ticket/' + encodeURIComponent(modal.key) + '/answer', { value: draft.value || '', note: draft.note || '', occurrence: modal.ticket.call.occurrence, request_id: modal.request });
    if (S.modal !== modal) return;
    S.receipt = receipt; $('answer-status').replaceChildren(receiptView(receipt));
    $('answer-status').append(button(t("See answer status"), async () => {
      await loadReceipts(); $('receipts-panel').open = true; closeDetail(false);
      $('receipts-panel').scrollIntoView({ block: 'start' });
    }));
    if (receipt.state === 'conflict') $('answer-status').append(button(t("See the new question"), () => openTicket(modal.key, S.opener)));
    if (receipt.saved) { S.drafts.delete(modal.key); try { sessionStorage.removeItem('deck-draft-' + modal.key); } catch {} }
  } catch (err) {
    if (S.modal !== modal) return;
    // The same request id goes out again, so a retry can never answer twice.
    $('answer-status').replaceChildren(node('p', 'notice error', err.message + (err.retry ? t(" Your draft is safe. Try the same answer again.") : t(" Your draft is safe."))));
  } finally { S.busy = false; syncSubmit(); }
}
async function openDelivery(home, task, opener) {
  const gen = ++S.generation;
  showDetail(t("Delivered work"), '', t("DELIVERED"), opener);
  S.modal = { type: 'loading' };
  try {
    const data = await api('api/delivery/' + encodeURIComponent(home) + '/' + encodeURIComponent(task));
    if (gen !== S.generation || !detailOpen()) return;
    S.modal = { type: 'delivery', home, task, data, tab: 'summary' };
    $('detail-title').textContent = data.title; detailMeta(data.owner, data.time, data.project);
    renderDelivery(); refreshExplanation();
  } catch (err) { if (gen === S.generation) $('detail-content').replaceChildren(node('p', 'notice error', err.message)); }
}
function renderDelivery() {
  const modal = S.modal, data = modal.data, content = $('detail-content'); content.replaceChildren();
  if (modal.linkNotice) content.append(node('p', 'notice', modal.linkNotice));
  if (data.notice) content.append(node('p', 'notice', data.notice));
  // What changed comes first; without a summary the report opens right away.
  const views = [['summary', t("Summary")], ['report', t("Report")]].filter(([id]) => data[id]);
  if (!views.some(([id]) => id === modal.tab)) modal.tab = views[0]?.[0] || 'summary';
  if (views.length > 1) {
    const tabs = node('nav', 'detail-tabs'); tabs.setAttribute('aria-label', t("Delivery content"));
    for (const [id, label] of views) { const b = button(label, () => { modal.tab = id; renderDelivery(); [...content.querySelectorAll('.detail-tabs button')].find(n => n.getAttribute('aria-pressed') === 'true')?.focus({preventScroll: true}); }); b.setAttribute('aria-pressed', String(modal.tab === id)); tabs.append(b); }
    content.append(tabs);
  }
  if (!views.length) content.append(node('p', 'detail-empty', t("No summary or report for this one.")));
  else if (modal.tab === 'summary') content.append(prose(data.summary));
  else content.append(markdown(data.report_text || data.report), data.report_lead ? disclosure(t("Branch, commits and paths"), markdown(data.report_lead)) : '');
  // A PR address left the title; it opens from here instead.
  for (const link of data.links || []) {
    const a = node('a', 'report-link', link.pr ? t('See PR #{number} ↗', {number: link.pr}) : t('Open link ↗'));
    a.href = link.url; a.target = '_blank'; a.rel = 'noopener noreferrer'; content.append(a);
  }
  // One control, one line of where the request stands.
  const actions = node('div', 'explain-actions');
  const ask = button(t("Ask Firstmate to explain it"), requestExplanation); ask.id = 'explain-request';
  const text = node('span', 'explain-status'); text.id = 'explain-status'; text.setAttribute('role', 'status');
  const reply = node('div', 'explain-reply'); reply.id = 'explain-reply'; reply.hidden = true;
  append(actions, ask, text); content.append(actions, reply);
  content.append(disclosure(t('Technical details'), prose(data.details), contextRow(t('History sources'), data.sources), contextRow(t('Date provenance'), data.time?.detail),
    data.original_title && data.original_title !== data.title ? contextRow(t('Original title'), data.original_title) : null));
  if (modal.explanation) explanationView(modal.explanation);
  addStableLink(modal.home, modal.task, 'delivery');
}
// A report's Markdown as plain page elements: nothing in it runs or loads,
// and only a web address becomes a link.
function inline(target, text) {
  let at = 0;
  for (const m of String(text).matchAll(/\*\*([^*\n]+)\*\*|__([^_\n]+)__|`([^`\n]+)`|\[([^\]\n]+)\]\(([^)\s]+)\)|(https?:\/\/[^\s<>()\[\]]*[^\s<>()\[\].,;:!?'"»])/g)) {
    target.append(text.slice(at, m.index)); at = m.index + m[0].length;
    if (m[1] || m[2]) target.append(node('strong', '', m[1] || m[2]));
    else if (m[3]) target.append(node('code', '', m[3]));
    else {
      const url = m[5] || m[6], label = m[4] || m[6];
      if (!/^https?:\/\//i.test(url)) { target.append(label); continue; }
      const a = node('a', '', label); a.href = url; a.target = '_blank'; a.rel = 'noopener noreferrer'; target.append(a);
    }
  }
  target.append(String(text).slice(at)); return target;
}
function markdown(text) {
  const root = node('div', 'prose report');
  let lines = [], list = null, fence = null, table = null;
  const flush = () => { if (lines.join('\n').trim()) root.append(inline(node('p'), lines.join('\n').trim())); lines = []; };
  for (const line of String(text || '').split('\n')) {
    if (fence) { if (/^\s*(```|~~~)/.test(line)) { root.append(append(node('pre'), node('code', '', fence.join('\n')))); fence = null; } else fence.push(line); continue; }
    const heading = line.match(/^\s{0,3}(#{1,6})\s+(.+?)[\s#]*$/), item = line.match(/^\s*([-*+]|\d+[.)])\s+(.+)/);
    if (!/^\s*\|/.test(line)) table = null;
    if (/^\s*(```|~~~)/.test(line)) { flush(); list = null; fence = []; }
    else if (/^\s*\|/.test(line)) {
      flush(); list = null;
      const cells = line.trim().replace(/^\||\|$/g, '').split('|').map(cell => cell.trim());
      if (cells.every(cell => /^:?-{2,}:?$/.test(cell))) continue;
      if (!table) { table = node('table'); root.append(append(node('div', 'table-scroll'), table)); }
      table.append(append(node('tr'), ...cells.map(cell => inline(node(table.children.length ? 'td' : 'th'), cell))));
    }
    else if (heading) { flush(); list = null; root.append(inline(node(heading[1].length <= 2 ? 'h3' : 'h4'), heading[2])); }
    else if (item) {
      flush(); const tag = /\d/.test(item[1]) ? 'ol' : 'ul';
      if (!list || list.tagName.toLowerCase() !== tag) { list = node(tag); root.append(list); }
      list.append(inline(node('li'), item[2]));
    }
    else if (!line.trim()) { flush(); list = null; }
    // An indented line goes on with the item above it.
    else if (list && /^\s{2,}\S/.test(line)) inline(list.lastChild, ' ' + line.trim());
    else { list = null; lines.push(line.replace(/^\s*>\s?/, '')); }
  }
  if (fence) root.append(append(node('pre'), node('code', '', fence.join('\n'))));
  flush(); return root;
}
function explanationView(data) {
  if (!$('explain-status')) return;
  const lines = { queued: t("Asked. Firstmate hasn’t seen it yet."), saved_unannounced: t("Asked, but not sure Firstmate was told."), acknowledged: t("Firstmate is on it."), answered: t("Firstmate replied below."), unsupported: data.message || t("This space can’t take explanation requests yet."), error: t("Couldn’t check the request.") };
  const text = lines[data.state] || '';
  if ($('explain-status').textContent !== text) $('explain-status').textContent = text;
  $('explain-status').title = data.state === 'error' ? data.message || '' : '';
  const reply = data.receipt?.reply, box = $('explain-reply');
  box.hidden = !reply?.body;
  if (reply?.body && box.dataset.body !== reply.body + (reply.at || '')) {
    box.dataset.body = reply.body + (reply.at || '');
    box.replaceChildren(markdown(reply.body), reply.at ? node('p', 'muted', dateFmt.format(new Date(reply.at))) : '');
  }
  const ask = $('explain-request');
  ask.hidden = data.state === 'unsupported';
  ask.disabled = S.busy || !S.online || ['queued', 'acknowledged', 'answered'].includes(data.state);
}
async function refreshExplanation() {
  const modal = S.modal; if (modal?.type !== 'delivery' || modal.readingExplanation) return;
  modal.readingExplanation = true;
  try { const data = await api(`api/delivery/${encodeURIComponent(modal.home)}/${encodeURIComponent(modal.task)}/explanation`); if (S.modal === modal) { modal.explanation = data; explanationView(data); } }
  catch (err) { if (S.modal === modal && $('explain-status')) { $('explain-status').textContent = t("Couldn’t check the request."); $('explain-status').title = err.message; } }
  finally { modal.readingExplanation = false; }
}
async function requestExplanation() {
  const modal = S.modal; if (modal?.type !== 'delivery' || S.busy || !S.online) return;
  S.busy = true; $('explain-request').disabled = true;
  try { const data = await api(`api/delivery/${encodeURIComponent(modal.home)}/${encodeURIComponent(modal.task)}/explanation`, {}); if (S.modal === modal) { modal.explanation = data; explanationView(data); } }
  catch (err) { if (S.modal === modal && $('explain-status')) $('explain-status').textContent = err.message; }
  finally { S.busy = false; if (S.modal === modal && modal.explanation) explanationView(modal.explanation); }
}
$('detail-close').addEventListener('click', () => closeDetail());
// Esc or a tap anywhere outside the detail and the cards closes it.
document.addEventListener('keydown', (event) => {
  if (event.key !== 'Escape') return;
  if (detailOpen()) { event.preventDefault(); closeDetail(); return; }
  // Without an open detail, Esc folds the stack the focus is in.
  const stack = document.activeElement?.closest?.('.call-stack');
  if (stack?.dataset.open === 'true') { event.preventDefault(); toggleStack(stack.dataset.key); stack.firstChild.focus({preventScroll: true}); }
});
document.addEventListener('click', (event) => {
  if (!detailOpen()) return;
  const path = event.composedPath?.() || [];
  if (path.includes($('detail')) || path.includes($('detail-panel')) || path.some(n => n.classList?.contains('card-open'))) return;
  closeDetail(false);
});
phoneLayout.addEventListener('change', () => { if (detailOpen()) { placeDetail(S.opener); markCurrent(); } });
function setDensity(density) {
  S.density = density; $('workspace').dataset.density = density;
  $('density').setAttribute('aria-pressed', String(density === 'compact'));
}
setDensity(S.density);
$('density').addEventListener('click', () => {
  setDensity(S.density === 'compact' ? 'normal' : 'compact');
  try { localStorage.setItem('deck-density', S.density); } catch { /* Works for this page only. */ }
});
$('crew').addEventListener('change', () => { S.home = $('crew').value; S.board = null; closeDetail(false); $('board').replaceChildren(node('p', 'placeholder', t("Loading this space…"))); poll(); if (S.view === 'history') loadHistory(); });
$('history-view').addEventListener('click', () => setView(S.view === 'history' ? 'board' : 'history'));
$('refresh').addEventListener('click', () => { poll(); if (S.view === 'history') loadHistory(); });
$('board-search').addEventListener('input', () => { S.query = $('board-search').value.trim().toLocaleLowerCase(locale); renderBoard(); });
$('history-search').addEventListener('input', () => { clearTimeout(S.historyTimer); S.historyTimer = setTimeout(loadHistory, 250); });
window.addEventListener('offline', () => { S.online = false; sourceStatus(); });
window.addEventListener('online', poll);
matchMedia('(max-width: 639px)').addEventListener('change', renderBoard);
document.addEventListener('visibilitychange', () => { if (!document.hidden) poll(); });
setInterval(poll, 4000); setInterval(() => { sourceStatus(); refreshTimes(); }, 15000);
// Retire an old Deck push/offline worker. It must never intercept actions or
// show stale API success; offline honesty is handled by the visible live view.
if ('serviceWorker' in navigator) navigator.serviceWorker.getRegistrations().then(regs => { for (const reg of regs) if (reg.scope === new URL('./', location.href).href) reg.unregister(); }).catch(() => {});
poll();

});
