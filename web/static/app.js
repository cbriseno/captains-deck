/* English source messages are catalog keys. Never translate task data. */
'use strict';
DeckI18n.ready.then(({t, locale, dateFmt, relative, eventTime, exactTime}) => {
/* User content is text only. Polling reads; only explicit buttons can POST. */
const $ = (id) => document.getElementById(id);
const S = { board: null, home: '', column: 'underway', online: false, view: 'board', query: '',
  busy: false, csrf: '', modal: null, opener: null, generation: 0, historyGeneration: 0,
  lastRead: 0, receipt: null, drafts: new Map(), historyTimer: null, linkHandled: false };
const labels = { charted: t("Up next"), underway: t("Underway"), captains_call: t("Your decision"), landed: t("Delivered") };
const notes = { charted: t("The next step"), underway: t("Work started"), captains_call: t("Your answer is needed"), landed: t("Recent deliveries") };
const phaseLabels = { human: t("Waiting for your answer"), dependency: t("Waiting for a dependency"), integration: t("Integration pending"), technical: t("Technical hold"), active: t("Declared activity"), unknown: t("Unconfirmed state"), waiting: t("Declared wait"), reported: t("Work reported") };
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
  for (const line of (text || t("No content is retained for this view.")).split('\n')) {
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
function detailMeta(owner, event) { $('detail-meta').replaceChildren(node('span', '', t('Coordinated by {owner}', {owner})), timeView(event)); }
function refreshTimes() {
  for (const n of document.querySelectorAll('.relative-time')) {
    const text = eventTime(JSON.parse(n.dataset.event));
    if (n.textContent !== text) n.textContent = text;
  }
}
function executorText(card) {
  const p = card.presentation || {};
  const harness = p.executor?.harness;
  return harness ? t('Registered executor: {name}', {name: {codex: 'Codex', claude: 'Claude Code', pi: 'Pi'}[harness] || harness}) : t('Executor not recorded');
}
function summaryText(card, compact = false) { return card.presentation?.summary || t(compact ? 'Summary pending from Firstmate.' : 'A human summary is missing. Firstmate needs to record the result or current situation.'); }
function phaseText(card) {
  if (card.answered) return t('Answer recorded');
  if (card.bucket === 'landed') return t('Retained delivery');
  if (card.bucket === 'charted' && card.context.phase === 'unknown') return t('Queued');
  const run = card.presentation?.run_step;
  if (run) return {running: t('Validation running'), fixing: t('Validation fixes in progress'), passed: t('Validation passed'), failed: t('Validation failed')}[run.state];
  return card.context.label || phaseLabels[card.context.phase];
}
function dependencyText(card) {
  return card.dependencies.map(d => (d.title || d.id) + ' · ' + (d.owner === '(main)' ? card.owner : d.owner || t('owner not recorded')) +
    (d.reconciliation_required ? t(' · needs reconciliation') : d.unresolved === false ? t(' · resolved') : '')).join('; ');
}
function notice(text, error = false) { $('notice').hidden = !text; $('notice').className = 'notice' + (error ? ' error' : ''); $('notice').textContent = text; }
function syncSubmit() {
  const submit = $('answer-submit'), call = S.modal?.ticket?.call;
  if (!submit || !call) return;
  const choice = document.querySelector('input[name="answer"]:checked')?.value;
  const hasAnswer = !!choice || (call.freeform && !!$('answer-note')?.value.trim());
  submit.disabled = S.busy || !S.online || !!call.error || !!S.receipt || !!S.modal.draftNeedsReview || !hasAnswer;
}
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
    if (err.name === 'AbortError') throw new Error(t('Request timed out. Your draft is preserved.'));
    if (err instanceof TypeError) throw new Error(t('Could not connect to Deck. Your draft is preserved.'));
    if (err instanceof SyntaxError) throw new Error(t('Deck returned an unreadable response. Your draft is preserved.'));
    throw err;
  } finally { clearTimeout(timer); }
}
function renderUpdated(at) {
  const updated = $('updated');
  if (!at) { if (updated.dataset.epoch) { updated.replaceChildren(t("First read pending.")); delete updated.dataset.epoch; } return; }
  if (updated.dataset.epoch === String(at)) return;
  const focused = updated.contains(document.activeElement), open = updated.querySelector('details')?.open;
  const view = timeView({label: 'Source read', epoch: at});
  view.open = !!open;
  updated.replaceChildren(view); updated.dataset.epoch = String(at);
  if (focused) view.querySelector('summary').focus({preventScroll: true});
}
function sourceStatus() {
  if (!S.board) return;
  const sources = S.board.sources || [];
  const latest = sources.map(x => x.received_at).filter(x => x > 0);
  const at = latest.length ? Math.min(...latest) : 0;
  const stale = at && Date.now() / 1000 - at > Math.max(60, S.board.source_ttl * 2);
  const failed = S.board.error || sources.some(s => s.error);
  const disconnected = !S.online || Date.now() - S.lastRead > 30000;
  const connectionText = disconnected ? t("Offline") : failed ? t("Source error") : stale ? t("Stale data") : S.board.loading ? t("Loading sources") : t("Connected to Deck");
  if ($('connection').textContent !== connectionText) $('connection').textContent = connectionText;
  $('connection').dataset.level = disconnected || failed ? 'error' : stale ? 'warning' : 'ok';
  renderUpdated(S.lastRead / 1000);
  if (disconnected) notice(t("Deck is offline. The last view and your draft are preserved. Review the question before sending after reconnecting."));
  else if (failed) notice(t("A source could not be refreshed. Retained data may be stale; check the sources below."));
  else if (stale) notice(t("The Firstmate read is old. The current state is not yet confirmed."));
  else notice('');
  syncSubmit();
}
function selectColumn(column, scroll = false) {
  S.column = column;
  for (const section of $('board').children) section.dataset.selected = String(section.dataset.kind === column);
  for (const b of $('columns-nav').children) b.setAttribute('aria-pressed', String(b.dataset.kind === column));
  if (scroll && matchMedia('(max-width: 639px)').matches) $('columns-nav').scrollIntoView({ block: 'start', behavior: 'smooth' });
}
function renderBoard() {
  if (!S.board) return;
  const columns = S.board.columns;
  const counts = Object.fromEntries(columns.map(c => [c.id, c.cards.filter(x => !x.answered).length]));
  const attention = `${counts.captains_call || 0} ${counts.captains_call === 1 ? t("pending decision") : t("pending decisions")}`;
  const spaces = `${S.board.homes.length} ${S.board.homes.length === 1 ? t("space") : t("spaces")}`;
  $('overview').textContent = matchMedia('(max-width: 639px)').matches ? attention + ' · ' + spaces : `${attention} · ${t('{count} tasks started', {count: counts.underway || 0})} · ${spaces}`;
  const metrics = [], nav = [];
  for (const id of ['captains_call', 'underway', 'charted', 'landed']) {
    const b = button('', () => { setView('board'); selectColumn(id, true); }, 'metric'); b.dataset.kind = id;
    append(b, node('span', 'metric-number', String(counts[id] || 0)), append(node('span', 'metric-label', labels[id]), node('span', 'metric-note', notes[id]))); metrics.push(b);
  }
  for (const c of columns) {
    const b = button('', () => selectColumn(c.id)); b.dataset.kind = c.id; b.setAttribute('aria-pressed', String(S.column === c.id));
    append(b, node('span', '', String(counts[c.id] || 0)), document.createTextNode(labels[c.id] || c.title)); nav.push(b);
  }
  const navIdentity = JSON.stringify(counts);
  if ($('columns-nav').dataset.signature !== navIdentity) {
    const focusedKind = document.activeElement.dataset.kind;
    const parent = document.activeElement.parentElement?.id;
    $('metrics').replaceChildren(...metrics); $('columns-nav').replaceChildren(...nav);
    $('columns-nav').dataset.signature = navIdentity;
    if (['metrics', 'columns-nav'].includes(parent)) [...$(parent).children].find(n => n.dataset.kind === focusedKind)?.focus({preventScroll: true});
  }
  const existing = new Map([...$('board').children].map(n => [n.dataset.kind, n]));
  for (const c of columns) {
    let section = existing.get(c.id);
    if (!section) { section = node('section', 'column'); section.dataset.kind = c.id; $('board').append(section); }
    const cards = c.cards.filter(card => (S.query === '' || [card.title, card.context.reason, card.owner, card.task, card.presentation?.summary, card.presentation?.next_action, card.presentation?.waiting_for, card.presentation?.executor?.harness,
      ...card.dependencies.map(d => d.title || d.id)].join(' ').toLocaleLowerCase(locale).includes(S.query)));
    const heading = append(node('div', 'column-heading'), node('span', 'column-dot'), node('h2', '', labels[c.id] || c.title), node('span', 'column-count', String(cards.length)));
    let list = section.querySelector('.cards');
    if (!list) { list = node('div', 'cards'); section.append(heading, list); }
    else section.firstChild.replaceWith(heading);
    const keys = new Set(cards.map(card => card.key));
    const retained = new Map();
    for (const n of [...list.children]) if (keys.has(n.dataset.key)) retained.set(n.dataset.key, n); else n.remove();
    for (const [index, card] of cards.entries()) {
      // Source freshness is independent of card progress. Retain unchanged
      // controls, disclosure state and focus across polls.
      const signature = JSON.stringify({...card, supervisor: null, context: {...card.context, firstmate_action: null}});
      let item = retained.get(card.key); retained.delete(card.key);
      if (!item || item.dataset.signature !== signature) {
        const old = item, focused = old?.contains(document.activeElement);
        const dateFocused = focused && !!document.activeElement.closest('.event-time');
        item = renderCard(card); item.dataset.signature = signature;
        if (old?.querySelector('.event-time')?.open) item.querySelector('.event-time').open = true;
        if (old) old.replaceWith(item);
        if (focused) (dateFocused ? item.querySelector('summary') : item.querySelector('.card-open')).focus({preventScroll: true});
      }
      if (list.children[index] !== item) {
        const focused = item.contains(document.activeElement) ? document.activeElement : null;
        list.insertBefore(item, list.children[index] || null);
        focused?.focus({preventScroll: true});
      }
    }
    if (!cards.length) list.replaceChildren(node('p', 'empty', S.query ? t("No tasks match your search.") : c.id === 'captains_call' ? t("No pending questions. Firstmate is keeping course.") : c.id === 'landed' ? t("No deliveries in this recent view. Search history for retained deliveries.") : t("No tasks in this state.")));
  }
  for (const n of [...$('board').children]) if (!n.dataset.kind) n.remove();
  selectColumn(S.column); renderSupervisors(); sourceStatus();
}
function renderCard(card) {
  const cardNode = node('article', 'task-card'); cardNode.dataset.key = card.key;
  const b = button('', () => card.bucket === 'landed' && card.home ? openDelivery(card.home, card.task, b) : openTicket(card.key, b), 'card-open'); b.dataset.key = card.key;
  const phase = node('span', 'phase', phaseText(card)); phase.dataset.phase = card.bucket === 'landed' ? 'delivered' : card.presentation?.run_step ? 'waiting' : card.context.phase;
  const p = card.presentation || {};
  append(b, node('div', 'card-top', card.bucket === 'landed' ? t('Coordinated by {owner}', {owner: card.owner}) : executorText(card)), node('h3', '', card.title), phase,
    node('p', 'card-reason', card.bucket === 'landed' ? card.summary || t('Open the delivery to read the retained result.') : summaryText(card, true)));
  if (card.dependencies.length) b.append(node('p', 'dependency', t('Depends on: {dependency}', {dependency: dependencyText(card)})));
  else if (p.waiting_for) b.append(node('p', 'dependency', t('Waiting: ') + p.waiting_for));
  if (card.bucket !== 'landed' && p.next_action && p.next_action !== p.waiting_for && p.next_action !== p.summary) b.append(node('p', 'card-next', t('Next: {action}', {action: p.next_action})));
  if (card.bucket === 'underway') b.append(node('p', 'activity-note', t('Live activity unverified.')));
  b.append(node('span', 'card-link', card.bucket === 'captains_call' ? t('View question →') : card.bucket === 'landed' ? t('Read delivery →') : t('View context →')));
  return append(cardNode, b, timeView(card.time));
}
function supervisorText(s) {
  if (!s || !s.observed_at) return t("Current activity is unknown. There is no supervisor declaration.");
  const epoch = Date.parse(s.observed_at) / 1000;
  const ttl = Number(s.ttl || s.ttl_seconds || 0);
  const stale = s.freshness === 'stale' || (ttl > 0 && Date.now() / 1000 - epoch > ttl);
  const state = { coordinating: t("coordination"), waiting: t("waiting"), idle: t("idle") }[s.state] || t("unknown state");
  const unknown = !Number.isFinite(epoch) || ttl <= 0 || s.freshness === 'unknown' || epoch > Date.now() / 1000 + 5;
  return t('{declaration} {summary} {time} Current model activity is unknown.', {declaration: stale ? t("Declaration expired; current activity is unknown.") : unknown ? t("Freshness unconfirmed; current activity is unknown.") : t("Declared ") + state + '.', summary: s.summary || t("No purpose recorded."), time: Number.isFinite(epoch) ? dateFmt.format(new Date(epoch * 1000)) + ' · ' + relative(epoch) + '.' : t("Time cannot be verified.")});
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
    const receipt = source.error ? node('small', '', t(source.error)) : source.received_at ? timeView({label: 'Source received', epoch: source.received_at}) : node('small', '', t('No confirmed read'));
    const title = append(node('div'), node('strong', '', source.label), receipt);
    const status = !s?.observed_at ? t('Current activity is unknown. There is no supervisor declaration.') : expired ? t('Declaration expired; current activity is unknown.') : t('Coordination is declared separately from worker activity.');
    append(row, title, append(node('div'), node('p', '', status), disclosure(t('Source details'), prose(supervisorText(s)), timeView({label: 'Snapshot generated', epoch: Date.parse(source.generated) / 1000}))));
    if (old) {
      const focusedIndex = old.contains(document.activeElement) ? [...old.querySelectorAll('summary')].indexOf(document.activeElement) : -1;
      const openStates = [...old.querySelectorAll('details')].map(d => d.open);
      [...row.querySelectorAll('details')].forEach((d, i) => { d.open = !!openStates[i]; });
      old.replaceWith(row);
      if (focusedIndex >= 0) row.querySelectorAll('summary')[focusedIndex]?.focus({preventScroll: true});
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
  $('board-view').setAttribute('aria-pressed', String(view === 'board')); $('history-view').setAttribute('aria-pressed', String(view === 'history'));
  if (view === 'history') loadHistory();
}
async function loadHistory() {
  const generation = ++S.historyGeneration;
  $('history-status').textContent = t("Searching retained records…");
  try {
    const query = new URLSearchParams({ home: S.home, q: $('history-search').value });
    const data = await api('api/history?' + query);
    if (generation !== S.historyGeneration) return;
    $('history-status').textContent = t('{count} deliveries{omitted}. {scope}', {count: data.records.length, omitted: data.omitted ? t(' · {count} more results; refine your search', {count: data.omitted}) : '', scope: data.scope});
    $('history-results').replaceChildren(...data.records.map(row => {
      const b = button('', () => openDelivery(row.home, row.task, b), 'card-open');
      append(b, node('p', 'card-top', t('Coordinated by {owner}', {owner: row.owner})), node('h3', '', row.title), node('span', 'card-link', t("Read delivery →")));
      return append(node('article', 'task-card'), b, timeView(row.time));
    }));
    if (!data.records.length) $('history-results').append(node('p', 'empty', t("No retained deliveries match your search.")));
    $('history-sources').replaceChildren(...data.details.map(d => contextRow(d.home, d.source)));
  } catch (err) { if (generation === S.historyGeneration) $('history-status').textContent = t("Could not refresh history. ") + err.message; }
}
function showDetail(title, meta, kicker, opener) {
  S.opener = opener || document.activeElement; S.receipt = null;
  S.openerKey = S.opener?.dataset?.key;
  $('detail-title').textContent = title; $('detail-meta').textContent = meta; $('detail-kicker').textContent = kicker;
  $('detail-content').replaceChildren(node('p', 'muted', t("Loading context…")));
  if (!$('detail').open) $('detail').showModal();
  $('detail-title').focus();
}
function readDraft(key) { try { return JSON.parse(sessionStorage.getItem('deck-draft-' + key)) || {}; } catch { return S.drafts.get(key) || {}; } }
function saveDraft(event) {
  if (S.modal?.type !== 'call' || !$('answer-note') || S.receipt?.saved) return;
  if (event && ['input', 'change'].includes(event.type)) S.modal.draftNeedsReview = false;
  const draft = { note: $('answer-note').value, value: document.querySelector('input[name="answer"]:checked')?.value || '', request: S.modal.request || crypto.randomUUID().replaceAll('-', ''), occurrence: S.modal.draftNeedsReview ? S.modal.previousOccurrence : S.modal.ticket.call.occurrence };
  S.modal.request = draft.request; S.drafts.set(S.modal.key, draft);
  try { sessionStorage.setItem('deck-draft-' + S.modal.key, JSON.stringify(draft)); } catch { /* In-memory draft remains available. */ }
  syncSubmit();
}
async function openTicket(key, opener) {
  const gen = ++S.generation;
  showDetail(t("Task context"), '', t("DECLARED STATE"), opener);
  S.modal = { type: 'loading', key };
  try {
    const ticket = await api('api/ticket/' + encodeURIComponent(key));
    if (gen !== S.generation || !$('detail').open) return;
    $('detail-title').textContent = ticket.title; detailMeta(ticket.owner, ticket.time);
    if (ticket.call) renderCall(ticket);
    else {
      S.modal = { type: 'task', key, ticket };
      const c = ticket.context, p = ticket.presentation || {};
      $('detail-kicker').textContent = phaseText(ticket);
      $('detail-content').replaceChildren(prose(summaryText(ticket)), contextRow(t('Who is involved'), executorText(ticket)));
      if (ticket.bucket === 'underway') $('detail-content').append(node('p', 'activity-note', t('Live worker activity is not verified.')));
      if (ticket.dependencies.length) $('detail-content').append(contextRow(t('Dependencies'), dependencyText(ticket)));
      if (p.waiting_for || ['waiting', 'technical', 'dependency', 'unknown'].includes(c.phase)) $('detail-content').append(contextRow(t('What it is waiting for'), p.waiting_for || t('The condition for resuming is missing. Firstmate needs to record what will allow this task to continue.')));
      $('detail-content').append(contextRow(t('Next step'), p.next_action || t('No concrete next step is recorded. Firstmate needs to update the task.')));
      addTechnical(ticket); addHerdr(ticket);
    }
  } catch (err) { if (gen === S.generation) $('detail-content').replaceChildren(node('p', 'notice error', err.message)); }
}
function addTechnical(ticket, ...extra) {
  const evidence = node('pre', 'technical-evidence', JSON.stringify({task: ticket.task, ...ticket.evidence, native: ticket.native, context: ticket.context}, null, 2));
  const report = ticket.report?.text ? contextRow(t('Full recorded report'), ticket.report.text) : null;
  const details = disclosure(t('Technical details and original report'), report,
    contextRow(t('Recorded agent'), ticket.agent || t('Not recorded.')),
    contextRow(t('Firstmate declaration'), supervisorText(ticket.supervisor)),
    contextRow(t('Date provenance'), ticket.time?.detail || t('Time not recorded')),
    ticket.report?.text ? contextRow(t('Report provenance'), ticket.report.time?.detail) : null,
    evidence, ...extra);
  if (ticket.original_title !== ticket.title) details.append(contextRow(t('Retained original title'), ticket.original_title));
  $('detail-content').append(details);
}
function addHerdr(ticket) { if (!ticket.herdr_url) return; const a = node('a', 'herdr-link', t("Open this agent in Herdr Web ↗")); a.href = ticket.herdr_url; a.target = '_blank'; a.rel = 'noopener noreferrer'; $('detail-content').append(a); }
function addStableLink(home, task, view) {
  if (!home) return;
  const url = new URL(location.href); url.search = new URLSearchParams({ home, task, view }).toString(); url.hash = '';
  const a = node('a', 'herdr-link', t("Permanent link to this task ↗")); a.href = url.href;
  $('detail-content').append(a);
}
async function openStableLink(params) {
  const home = params.get('home') || '', task = params.get('task') || '', view = params.get('view') || '';
  showDetail(t("Opening task"), '', t("FIRSTMATE LINK"));
  const gen = ++S.generation;
  try {
    const result = await api('api/resolve?' + new URLSearchParams({ home, task, view, item: params.get('item') || '' }));
    if (gen !== S.generation || !$('detail').open) return;
    if (result.view === 'call') { await openTicket(result.key, S.opener); return; }
    if (result.view === 'delivery') { await openDelivery(home, task, S.opener); if (S.modal?.type === 'delivery' && result.notice) { S.modal.linkNotice = result.notice; renderDelivery(); } return; }
    S.modal = { type: 'task' };
    $('detail-title').textContent = t("The question from this notification"); $('detail-meta').textContent = result.owner + ' · ' + result.task;
    $('detail-content').replaceChildren(prose(result.message));
    for (const receipt of result.receipts || []) $('detail-content').append(receiptView(receipt));
    if (result.current_key) $('detail-content').append(button(t("View the current question"), async () => { await openTicket(result.current_key, S.opener); $('detail-kicker').textContent = t("CURRENT QUESTION"); }));
    $('detail-content').append(button(t("Find this task in history"), () => { $('history-search').value = task; $('detail').close(); setView('history'); }));
  } catch (err) { if (gen === S.generation) $('detail-content').replaceChildren(node('p', 'notice error', err.message)); }
}
function renderCall(ticket, draftOverride) {
  const key = ticket.key, c = ticket.call, draft = draftOverride || readDraft(key);
  const staleDraft = !!draft.occurrence && draft.occurrence !== c.occurrence && !!(draft.note || draft.value);
  S.modal = { type: 'call', key, ticket, request: draft.request || crypto.randomUUID().replaceAll('-', ''), draftNeedsReview: staleDraft, previousOccurrence: draft.occurrence };
  $('detail-kicker').textContent = t('CAPTAIN’S CALL'); $('detail-title').textContent = c.title || ticket.title;
  const content = $('detail-content'); content.replaceChildren();
  if (ticket.answered) {
    $('detail-kicker').textContent = t('Answer recorded');
    content.append(prose(c.presentation?.decide || c.decide), contextRow(t('What happens next'), t('The recorded answer is waiting for owner handling. This does not confirm that work resumed.')),
      button(t('Check receipts'), async () => { await loadReceipts(); $('receipts-panel').open = true; $('detail').close(); }));
    addTechnical(ticket, c.detail ? contextRow(t('Full question context'), c.detail) : null); addHerdr(ticket); addStableLink(ticket.home, ticket.task, 'call'); return;
  }
  const error = node('p', 'notice', c.error || (staleDraft ? t("This draft belongs to an earlier question. It is preserved here: select a current option or edit your answer to enable sending.") : '')); error.id = 'call-change'; error.hidden = !c.error && !staleDraft; content.append(error);
  if (c.presentation?.about || c.about) content.append(prose(c.presentation?.about || c.about));
  content.append(node('p', 'question', (c.presentation?.decide || c.decide)));
  const form = node('fieldset', 'option-list'); form.append(node('legend', '', t("Choose an option or answer in your own words")));
  for (const option of c.options) {
    const choice = node('label', 'choice'), input = node('input'); input.type = 'radio'; input.name = 'answer'; input.value = option.value; input.checked = !staleDraft && draft.value === option.value;
    const text = append(node('span'), node('strong', '', c.presentation?.options?.find(o => o.value === option.value)?.label || option.label));
    if (option.value === c.recommend) text.append(node('span', 'recommended', t("Suggested")));
    if (option.hint) text.append(node('small', '', c.presentation?.options?.find(o => o.value === option.value)?.hint || option.hint));
    input.addEventListener('change', saveDraft); append(choice, input, text); form.append(choice);
  }
  if (c.freeform) {
    const choice = node('label', 'choice'), input = node('input'); input.type = 'radio'; input.name = 'answer'; input.value = ''; input.checked = !staleDraft && !draft.value;
    input.addEventListener('change', saveDraft); append(choice, input, append(node('span'), node('strong', '', t("Answer in my own words")), node('small', '', c.presentation?.freeform_hint || c.freeform_hint))); form.append(choice);
  }
  content.append(form);
  const noteLabel = node('label', 'answer-label', c.freeform ? t("Your answer or additional context") : t("Additional context (optional)"));
  const note = node('textarea'); note.id = 'answer-note'; note.rows = 3; note.value = draft.note || ''; note.placeholder = t("Write here…"); note.addEventListener('input', saveDraft); noteLabel.append(note); content.append(noteLabel);
  content.append(node('p', 'answer-help', t("Maximum 512 bytes, one line. Selecting or typing saves the draft; only Send submits it.")));
  const state = node('div'); state.id = 'answer-status'; state.setAttribute('role', 'status'); content.append(state);
  const actions = node('div', 'action-bar');
  append(actions, node('p', '', c.close === 'release' ? t("Firstmate will save your answer and release this hold. This does not confirm that the agent has resumed.") : t("Firstmate will record your selection through this task’s intake. Reconcile requests a review; it does not resolve the call.")), button(t("Send answer"), submitAnswer, 'primary'));
  actions.lastChild.id = 'answer-submit'; actions.lastChild.disabled = !!c.error || !S.online;
  content.append(actions); addTechnical(ticket, c.detail ? contextRow(t('Full question context'), c.detail) : null); addHerdr(ticket); addStableLink(ticket.home, ticket.task, 'call'); syncSubmit();
}
async function checkCall() {
  const modal = S.modal;
  if (!modal || modal.checking) return;
  modal.checking = true;
  try {
    const latest = await api('api/ticket/' + encodeURIComponent(modal.key));
    if (S.modal !== modal || S.busy) return;
    if (!latest.call || latest.call.occurrence !== modal.ticket.call.occurrence || latest.call.error) {
      const warning = $('call-change'); if (!warning) return;
      warning.hidden = false; warning.replaceChildren(document.createTextNode(latest.call?.error || t("The question changed. Your draft is still here.")));
      $('answer-submit').disabled = true;
      if (latest.call) warning.append(button(t("Review current question"), () => { saveDraft(); renderCall(latest); }));
      modal.ticket.call.error = t("Review the current question before sending.");
    }
  } catch (err) { if (S.modal === modal && $('call-change')) { $('call-change').hidden = false; $('call-change').textContent = t("Could not verify the question. Your draft is preserved."); $('answer-submit').disabled = true; } }
  finally { modal.checking = false; }
}
function receiptView(receipt) {
  const box = node('div', 'receipt'); box.setAttribute('role', 'status');
  append(box, node('h3', '', receipt.saved ? t("Answer recorded") : receipt.saved === false ? t("Answer not sent") : t("Submission needs verification")), node('p', '', t(receipt.message)));
  const dl = node('dl');
  for (const [label, text] of [[t("Saved"), receipt.saved === true ? t("Confirmed by intake") : t("Unconfirmed")], [t("Owner notification"), receipt.notice === 'queued' ? t("Queued") : t("Unconfirmed")], [t("Owner ACK"), receipt.ack === 'acknowledged' ? t("Received") : t("Unconfirmed")], [t("Answer consumption"), receipt.consumed === 'consumed' ? t("Confirmed") : t("Unconfirmed")]]) append(dl, node('dt', '', label), node('dd', '', text));
  append(box, dl, node('p', '', t("Saving and ACK are separate facts. This receipt does not confirm that the agent resumed.")));
  if (receipt.detail) append(box, append(node('details', 'source-details'), node('summary', '', t("Receipt details")), prose(receipt.detail)));
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
  S.busy = true; $('answer-submit').disabled = true; $('answer-submit').textContent = t("Sending…");
  try {
    const receipt = await api('api/ticket/' + encodeURIComponent(modal.key) + '/answer', { value: draft.value || '', note: draft.note || '', occurrence: modal.ticket.call.occurrence, request_id: modal.request });
    if (S.modal !== modal) return;
    S.receipt = receipt; $('answer-status').replaceChildren(receiptView(receipt));
    $('answer-submit').textContent = receipt.saved ? t("Recorded") : receipt.saved === false ? t("Not sent") : t("Unconfirmed");
    $('answer-status').append(button(t("Check receipts"), async () => {
      await loadReceipts(); $('receipts-panel').open = true; $('detail').close();
      $('receipts-panel').scrollIntoView({ block: 'start' });
    }));
    if (receipt.state === 'conflict') $('answer-status').append(button(t("Review current question"), () => openTicket(modal.key, S.opener)));
    if (receipt.saved) { S.drafts.delete(modal.key); try { sessionStorage.removeItem('deck-draft-' + modal.key); } catch {} }
  } catch (err) {
    if (S.modal !== modal) return;
    $('answer-status').replaceChildren(node('p', 'notice error', err.message + t(" Your draft is preserved.")));
    $('answer-submit').textContent = t("Retry the same submission");
  } finally { S.busy = false; syncSubmit(); }
}
async function openDelivery(home, task, opener) {
  const gen = ++S.generation;
  showDetail(t("Crew delivery"), '', t("RETAINED DELIVERY"), opener);
  S.modal = { type: 'loading' };
  try {
    const data = await api('api/delivery/' + encodeURIComponent(home) + '/' + encodeURIComponent(task));
    if (gen !== S.generation || !$('detail').open) return;
    S.modal = { type: 'delivery', home, task, data, tab: 'summary' };
    $('detail-title').textContent = data.title; detailMeta(data.owner, data.time);
    renderDelivery(); refreshExplanation();
  } catch (err) { if (gen === S.generation) $('detail-content').replaceChildren(node('p', 'notice error', err.message)); }
}
function renderDelivery() {
  const modal = S.modal, data = modal.data, content = $('detail-content'); content.replaceChildren();
  if (modal.linkNotice) content.append(node('p', 'notice', modal.linkNotice));
  const tabs = node('nav', 'detail-tabs'); tabs.setAttribute('aria-label', t("Delivery content"));
  for (const [id, label] of [['summary', t("Summary")], ['report', t("Report")]]) { const b = button(label, () => { modal.tab = id; renderDelivery(); [...content.querySelectorAll('.detail-tabs button')].find(n => n.getAttribute('aria-pressed') === 'true')?.focus({preventScroll: true}); }); b.setAttribute('aria-pressed', String(modal.tab === id)); tabs.append(b); }
  append(content, tabs, prose(data[modal.tab]));
  content.append(disclosure(t('Technical details and sources'), prose(data.details), contextRow(t('History sources'), data.sources), contextRow(t('Date provenance'), data.time?.detail)));
  const actions = node('div', 'explain-actions');
  const ask = button(t("Ask Firstmate for an explanation"), requestExplanation); ask.id = 'explain-request';
  const status = button(t("Check status"), refreshExplanation);
  const text = node('p', 'explain-status', t("Checking does not send a request.")); text.id = 'explain-status'; text.setAttribute('role', 'status');
  append(actions, ask, status, text); content.append(actions);
  if (modal.explanation) explanationView(modal.explanation);
  addStableLink(modal.home, modal.task, 'delivery');
}
function explanationView(data) {
  if (!$('explain-status')) return;
  const meanings = { absent: t("No explanation has been requested yet."), queued: t("Request saved and notification queued. Firstmate has not acknowledged receipt yet."), saved_unannounced: t("Request saved; notification to Firstmate is unconfirmed."), acknowledged: t("Firstmate acknowledged receipt (ACK). This does not confirm that a reply is being prepared."), answered: t("Firstmate left a reply."), unsupported: t("This home does not offer the explanation intake yet."), error: t("Could not verify the request status.") };
  const reply = data.receipt?.reply;
  const text = (meanings[data.state] || t("State unconfirmed.")) + (data.message ? '\n' + data.message : '') + (reply?.body ? t('\n\nReply from Firstmate:\n') + reply.body + (reply.at ? '\n' + dateFmt.format(new Date(reply.at)) : '') : '');
  if ($('explain-status').textContent !== text) $('explain-status').textContent = text;
  $('explain-request').disabled = S.busy || !S.online || ['queued', 'acknowledged', 'answered', 'unsupported'].includes(data.state);
}
async function refreshExplanation() {
  const modal = S.modal; if (modal?.type !== 'delivery' || modal.readingExplanation) return;
  modal.readingExplanation = true;
  try { const data = await api(`api/delivery/${encodeURIComponent(modal.home)}/${encodeURIComponent(modal.task)}/explanation`); if (S.modal === modal) { modal.explanation = data; explanationView(data); } }
  catch (err) { if (S.modal === modal && $('explain-status')) $('explain-status').textContent = err.message; }
  finally { modal.readingExplanation = false; }
}
async function requestExplanation() {
  const modal = S.modal; if (modal?.type !== 'delivery' || S.busy || !S.online) return;
  S.busy = true; $('explain-request').disabled = true;
  try { const data = await api(`api/delivery/${encodeURIComponent(modal.home)}/${encodeURIComponent(modal.task)}/explanation`, {}); if (S.modal === modal) { modal.explanation = data; explanationView(data); } }
  catch (err) { if (S.modal === modal && $('explain-status')) $('explain-status').textContent = err.message; }
  finally { S.busy = false; if (S.modal === modal && modal.explanation) explanationView(modal.explanation); }
}
$('detail-close').addEventListener('click', () => { saveDraft(); $('detail').close(); });
$('detail').addEventListener('cancel', saveDraft);
$('detail').addEventListener('close', () => {
  // A queued close event must not cancel a detail opened immediately after it.
  if ($('detail').open) return;
  saveDraft(); S.modal = null; S.generation++;
  const current = S.opener?.isConnected && S.opener.tagName !== 'BODY' ? S.opener : [...document.querySelectorAll('.card-open')].find(n => n.dataset.key === S.openerKey) || $('board-view');
  current.focus({ preventScroll: true });
});
$('crew').addEventListener('change', () => { S.home = $('crew').value; S.board = null; $('board').replaceChildren(node('p', 'placeholder', t("Loading this space…"))); poll(); if (S.view === 'history') loadHistory(); });
$('board-view').addEventListener('click', () => setView('board'));
$('history-view').addEventListener('click', () => setView('history'));
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
