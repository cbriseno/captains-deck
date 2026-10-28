/* English source messages are catalog keys. Never translate task data. */
'use strict';
DeckI18n.ready.then(({t, locale, dateFmt, relative, eventTime, exactTime, untilText}) => {
/* User content is text only. Polling reads; only explicit buttons can POST. */
const $ = (id) => document.getElementById(id);
const S = { board: null, home: '', column: 'underway', online: false, view: 'board', query: '',
  busy: false, csrf: '', modal: null, opener: null, generation: 0, historyGeneration: 0,
  lastRead: 0, receipt: null, drafts: new Map(), historyTimer: null, linkHandled: false };
const labels = { charted: t("Up next"), underway: t("Underway"), captains_call: t("Needs you"), landed: t("Delivered") };
const notes = { captains_call: t("Waiting on your answer"), landed: t("Recently finished") };
const phaseLabels = { human: t("Waiting for your answer"), dependency: t("Waiting on another task"), integration: t("Finishing up"), technical: t("On hold"), active: t("In progress"), unknown: t("Status unclear"), waiting: t("Waiting"), reported: t("Reported done") };
const runLabels = { running: t('Checks running'), fixing: t('Fixing failed checks'), passed: t('Checks passed'), failed: t('Checks failed') };
// Firstmate's crew state and endpoint probe, as projected by the bridge.
const agentLines = { working: t('Agent is working'), validation: t('Checks are running'), open: t('Agent is open'), present: t('Agent is open but idle'), none: t('No agent running'), unknown: t('Agent status unknown') };
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
  return harness ? t('Agent: {name}', {name: {codex: 'Codex', claude: 'Claude Code', pi: 'Pi'}[harness] || harness}) : t('Agent not set');
}
function summaryText(card, compact = false) { return card.presentation?.summary || t(compact ? 'No summary yet.' : 'No summary yet. Firstmate hasn’t written one.'); }
function activityText(card) {
  const a = card.activity || {}, until = a.until ? untilText(a.until) : '';
  if (a.kind === 'paused') return until ? t('Paused until {date}', {date: until}) : t('Paused');
  if (a.kind === 'deferred') return until ? t('Deferred until {date}', {date: until}) : t('Deferred');
  if (a.kind === 'decision') return card.bucket === 'underway' ? t('Waiting on a decision') : '';
  if (a.kind === 'last_seen_working') return t('Working when last seen') + (a.seen_at ? ' · ' + relative(a.seen_at) : '');
  return { working: t('Working now'), validating: t('Checks running'), validation_gate: t('Checks need approval'), blocked: t('Waiting on another task'),
    held: t('On hold'), finished: t('Done, not closed yet'), stopped: t('Stopped'), ready: t('Ready to start'), queued: t('Queued') }[a.kind] || '';
}
function phaseText(card) {
  if (card.answered) return t('Answer saved');
  if (card.bucket === 'landed') return t('Delivered');
  const run = card.presentation?.run_step;
  if (run) return runLabels[run.state];
  return activityText(card) || (card.bucket === 'charted' && card.context.phase === 'unknown' ? t('Queued') : card.context.label || phaseLabels[card.context.phase]);
}
function agentLine(card) {
  const agent = card.activity?.agent || 'unknown', line = node('p', 'activity-note', agentLines[agent] || agentLines.unknown);
  line.dataset.agent = agent; return line;
}
function activityCount(columns, id, kind) { return (columns.find(c => c.id === id)?.cards || []).filter(x => !x.answered && x.activity?.kind === kind).length; }
function dependencyText(card) {
  return card.dependencies.map(d => (d.title || d.id) + ' · ' + (d.owner === '(main)' ? card.owner : d.owner || t('owner not recorded')) +
    (d.reconciliation_required ? t(' · needs a check') : d.unresolved === false ? t(' · resolved') : '')).join('; ');
}
function notice(text, error = false) { $('notice').hidden = !text; $('notice').className = 'notice' + (error ? ' error' : ''); $('notice').textContent = text; }
function syncSubmit() {
  const call = S.modal?.type === 'call' ? S.modal.ticket?.call : null;
  if (!call) return;
  const blocked = S.busy || !S.online || !!call.error || !!S.modal.checkFailed || !!S.receipt;
  for (const b of $('detail-content').querySelectorAll('.option-choice')) {
    b.disabled = blocked;
    b.setAttribute('aria-pressed', String(b.dataset.value === S.modal.value));
  }
  const submit = $('answer-submit');
  if (submit) submit.disabled = blocked || !!S.modal.draftNeedsReview || !$('answer-note')?.value.trim();
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
  const connectionText = disconnected ? t("Offline") : failed ? t("Update failed") : stale ? t("Out of date") : S.board.loading ? t("Loading…") : t("Connected to Deck");
  if ($('connection').textContent !== connectionText) $('connection').textContent = connectionText;
  $('connection').dataset.level = disconnected || failed ? 'error' : stale ? 'warning' : 'ok';
  renderUpdated(S.lastRead / 1000);
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
function renderBoard() {
  if (!S.board) return;
  const columns = S.board.columns;
  const counts = Object.fromEntries(columns.map(c => [c.id, c.cards.filter(x => !x.answered).length]));
  const attention = `${counts.captains_call || 0} ${counts.captains_call === 1 ? t("pending decision") : t("pending decisions")}`;
  const spaces = `${S.board.homes.length} ${S.board.homes.length === 1 ? t("space") : t("spaces")}`;
  // Underway holds every open task; only these agents are working right now.
  const working = activityCount(columns, 'underway', 'working'), ready = activityCount(columns, 'charted', 'ready');
  const now = t('{count} working now', {count: working});
  $('overview').textContent = matchMedia('(max-width: 639px)').matches ? attention + ' · ' + now : `${attention} · ${now} · ${t('{count} underway', {count: counts.underway || 0})} · ${spaces}`;
  const liveNotes = { ...notes, underway: now, charted: t('{count} ready to start', {count: ready}) };
  const metrics = [], nav = [];
  for (const id of ['captains_call', 'underway', 'charted', 'landed']) {
    const b = button('', () => { setView('board'); selectColumn(id, true); }, 'metric'); b.dataset.kind = id;
    append(b, node('span', 'metric-number', String(counts[id] || 0)), append(node('span', 'metric-label', labels[id]), node('span', 'metric-note', liveNotes[id]))); metrics.push(b);
  }
  for (const c of columns) {
    const b = button('', () => selectColumn(c.id)); b.dataset.kind = c.id; b.setAttribute('aria-pressed', String(S.column === c.id));
    append(b, node('span', '', String(counts[c.id] || 0)), document.createTextNode(labels[c.id] || c.title)); nav.push(b);
  }
  const navIdentity = JSON.stringify({counts, working, ready});
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
    const cards = c.cards.filter(card => (S.query === '' || [card.title, card.question, card.context.reason, card.owner, card.task, card.presentation?.summary, card.presentation?.next_action, card.presentation?.waiting_for, card.presentation?.executor?.harness,
      ...card.dependencies.map(d => d.title || d.id)].join(' ').toLocaleLowerCase(locale).includes(S.query)));
    const sub = c.id === 'underway' ? t('{count} working now', {count: activityCount([{id: c.id, cards}], c.id, 'working')})
      : c.id === 'charted' ? t('{count} ready to start', {count: activityCount([{id: c.id, cards}], c.id, 'ready')}) : '';
    const heading = append(node('div', 'column-heading'), node('span', 'column-dot'), node('h2', '', labels[c.id] || c.title), sub && node('span', 'column-sub', sub), node('span', 'column-count', String(cards.length)));
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
    if (!cards.length) list.replaceChildren(node('p', 'empty', S.query ? t("No tasks match your search.") : c.id === 'captains_call' ? t("Nothing needs you right now.") : c.id === 'landed' ? t("Nothing finished recently. Try History for older work.") : t("Nothing here.")));
  }
  for (const n of [...$('board').children]) if (!n.dataset.kind) n.remove();
  selectColumn(S.column); renderSupervisors(); sourceStatus();
}
function renderCard(card) {
  const cardNode = node('article', 'task-card'); cardNode.dataset.key = card.key;
  const b = button('', () => card.bucket === 'landed' && card.home ? openDelivery(card.home, card.task, b) : openTicket(card.key, b), 'card-open'); b.dataset.key = card.key;
  const phase = node('span', 'phase', phaseText(card)); phase.dataset.phase = card.bucket === 'landed' ? 'delivered' : card.presentation?.run_step ? 'waiting' : card.context.phase;
  phase.dataset.activity = card.answered ? '' : card.activity?.kind || '';
  const p = card.presentation || {};
  // A card leads with one short line; the full text is one click away.
  // An unknown agent is a drawer fact, not a line on every card.
  const top = card.bucket === 'landed' ? t('Coordinated by {owner}', {owner: card.owner}) : p.executor?.harness ? executorText(card) : '';
  append(b, top && node('div', 'card-top', top), node('h3', '', shortLine(card.title, 100)), phase,
    node('p', 'card-reason', shortLine(card.bucket === 'landed' ? card.summary || t('Open it to see what was delivered.') : card.question || summaryText(card, true))));
  if (card.dependencies.length) b.append(node('p', 'dependency', t('Waiting on: {dependency}', {dependency: dependencyText(card)})));
  else if (p.waiting_for) b.append(node('p', 'dependency', t('Waiting: ') + shortLine(p.waiting_for, 100)));
  if (card.bucket !== 'landed' && p.next_action && p.next_action !== p.waiting_for && p.next_action !== p.summary) b.append(node('p', 'card-next', t('Next: {action}', {action: shortLine(p.next_action, 100)})));
  if (card.bucket === 'underway') b.append(agentLine(card));
  b.append(node('span', 'card-link', card.bucket === 'captains_call' ? t('Answer →') : card.bucket === 'landed' ? t('See result →') : t('Details →')));
  return append(cardNode, b, timeView(card.time));
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
    const receipt = source.error ? node('small', '', t(source.error)) : source.received_at ? timeView({label: 'Source received', epoch: source.received_at}) : node('small', '', t('No confirmed read'));
    receipt.dataset.role = 'receipt';
    const title = append(node('div'), node('strong', '', source.label), receipt);
    const status = !s?.observed_at ? t('Firstmate hasn’t said what it’s doing.') : expired ? t('Firstmate’s last update expired.') : t('Firstmate posted an update.');
    const snapshot = timeView({label: 'Snapshot generated', epoch: Date.parse(source.generated) / 1000});
    snapshot.dataset.role = 'snapshot';
    const detailsView = disclosure(t('Source details'), prose(supervisorText(s)), snapshot);
    detailsView.dataset.role = 'source';
    append(row, title, append(node('div'), node('p', '', status), detailsView));
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
  $('history-status').textContent = t("Searching…");
  try {
    const query = new URLSearchParams({ home: S.home, q: $('history-search').value });
    const data = await api('api/history?' + query);
    if (generation !== S.historyGeneration) return;
    $('history-status').textContent = t('{count} deliveries{omitted}. {scope}', {count: data.records.length, omitted: data.omitted ? t(' · {count} more results; refine your search', {count: data.omitted}) : '', scope: data.scope});
    $('history-results').replaceChildren(...data.records.map(row => {
      const b = button('', () => openDelivery(row.home, row.task, b), 'card-open');
      append(b, node('p', 'card-top', t('Coordinated by {owner}', {owner: row.owner})), node('h3', '', row.title), node('span', 'card-link', t("See result →")));
      return append(node('article', 'task-card'), b, timeView(row.time));
    }));
    if (!data.records.length) $('history-results').append(node('p', 'empty', t("No matches.")));
    $('history-sources').replaceChildren(...data.details.map(d => contextRow(d.home, d.source)));
  } catch (err) { if (generation === S.historyGeneration) $('history-status').textContent = t("Could not refresh history. ") + err.message; }
}
function showDetail(title, meta, kicker, opener) {
  S.opener = opener || document.activeElement; S.receipt = null;
  S.openerKey = S.opener?.dataset?.key;
  $('detail-title').textContent = title; $('detail-meta').textContent = meta; $('detail-kicker').textContent = kicker;
  $('detail-content').replaceChildren(node('p', 'muted', t("Loading…")));
  if (!$('detail').open) $('detail').showModal();
  $('detail-title').focus();
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
    if (gen !== S.generation || !$('detail').open) return;
    $('detail-title').textContent = ticket.title; detailMeta(ticket.owner, ticket.time);
    if (ticket.call) renderCall(ticket);
    else {
      S.modal = { type: 'task', key, ticket };
      const c = ticket.context, p = ticket.presentation || {};
      $('detail-kicker').textContent = phaseText(ticket);
      $('detail-content').replaceChildren(prose(summaryText(ticket)), contextRow(t('Who’s on it'), executorText(ticket)));
      const a = ticket.activity || {}, until = a.until ? untilText(a.until) : '';
      if (ticket.bucket === 'underway') $('detail-content').append(contextRow(t('Agent'), agentLines[a.agent] || agentLines.unknown));
      if (ticket.dependencies.length) $('detail-content').append(contextRow(t('Waiting on'), dependencyText(ticket)));
      const waits = ['paused', 'deferred', 'held', 'blocked', 'validation_gate', 'stopped'].includes(a.kind);
      if (!['ready', 'queued'].includes(a.kind) && (p.waiting_for || waits || ['waiting', 'technical', 'dependency', 'unknown'].includes(c.phase))) $('detail-content').append(contextRow(t('Waiting for'), p.waiting_for || (until ? t('Until {date}', {date: until}) : a.kind === 'validation_gate' ? t('Someone to approve its checks.') : t('Not recorded. Firstmate hasn’t said what it needs to continue.'))));
      if (gapLines[a.gap]) $('detail-content').append(node('p', 'activity-note', gapLines[a.gap]));
      $('detail-content').append(contextRow(t('Next step'), p.next_action || t('No next step yet.')));
      addTechnical(ticket); addHerdr(ticket);
    }
  } catch (err) { if (gen === S.generation) $('detail-content').replaceChildren(node('p', 'notice error', err.message)); }
}
function addTechnical(ticket, ...extra) {
  const evidence = node('pre', 'technical-evidence', JSON.stringify({task: ticket.task, native: ticket.native, context: ticket.context}, null, 2));
  const report = ticket.report?.text ? contextRow(t('Full recorded report'), ticket.report.text) : null;
  const details = disclosure(t('Technical details'), report,
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
  const a = node('a', 'herdr-link', t("Link to this task ↗")); a.href = url.href;
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
// Long authored text leads with one short line; the rest opens on demand.
function shortLine(text, limit = 140) {
  const flat = String(text || '').replace(/\s+/g, ' ').trim();
  if (flat.length <= limit) return flat;
  const first = flat.match(/^.{40,}?[.!?](?=\s+[\p{Lu}¿¡]|$)/u)?.[0] || flat;
  return first.length <= limit ? first : first.slice(0, limit - 1).replace(/\s+\S*$/, '') + '…';
}
function leadText(cls, text) {
  const lead = shortLine(text), full = String(text || '').trim();
  if (lead === full.replace(/\s+/g, ' ')) return node('p', cls, full);
  return append(node('div', cls), node('p', '', lead), disclosure(t('Read more'), prose(full)));
}
function reportLink(link) {
  if (!link) return null;
  if (!/^https?:\/\//i.test(link)) return contextRow(t('Full report'), link);
  const a = node('a', 'herdr-link', t('Read the full report ↗')); a.href = link; a.target = '_blank'; a.rel = 'noopener noreferrer';
  return a;
}
function choose(value) {
  if (S.busy || S.receipt || S.modal?.type !== 'call') return;
  S.modal.value = value; S.modal.draftNeedsReview = false; $('call-change').hidden = !S.modal.ticket.call.error;
  saveDraft(); submitAnswer();
}
function renderCall(ticket, draftOverride) {
  const key = ticket.key, c = ticket.call, draft = draftOverride || readDraft(key);
  const staleDraft = !!draft.occurrence && draft.occurrence !== c.occurrence && !!(draft.note || draft.value);
  S.modal = { type: 'call', key, ticket, value: staleDraft ? '' : draft.value || '', request: draft.request || crypto.randomUUID().replaceAll('-', ''), draftNeedsReview: staleDraft, previousOccurrence: draft.occurrence };
  $('detail-kicker').textContent = t('CAPTAIN’S CALL'); $('detail-title').textContent = ticket.title;
  const content = $('detail-content'); content.replaceChildren();
  if (c.title && ![ticket.title, ticket.original_title].includes(c.title)) content.append(node('h3', 'call-question-title', c.title));
  const instructions = c.detail ? contextRow(t('How to answer'), c.detail) : null;
  if (instructions) instructions.className += ' call-instructions';
  const technical = [contextRow(t('Answer limits'), t('One line, up to 512 bytes. Typing saves a draft; only Send submits it.'))];
  if (c.background) technical.unshift(contextRow(t('Original question text'), c.background));
  if (ticket.answered) {
    $('detail-kicker').textContent = t('Answer saved');
    content.append(prose(c.presentation?.decide || c.decide), contextRow(t('What happens next'), t('Firstmate has your answer and will pick it up next.')),
      button(t('See answer status'), async () => { await loadReceipts(); $('receipts-panel').open = true; $('detail').close(); }));
    if (instructions) content.append(instructions);
    addTechnical(ticket, ...technical); addHerdr(ticket); addStableLink(ticket.home, ticket.task, 'call'); return;
  }
  const error = node('p', 'notice', c.error || (staleDraft ? staleDraftNote : '')); error.id = 'call-change'; error.hidden = !c.error && !staleDraft; content.append(error);
  const about = c.presentation?.about || c.about;
  if (about) content.append(leadText('call-context', about));
  content.append(node('p', 'question', (c.presentation?.decide || c.decide)));
  const link = reportLink(c.link); if (link) content.append(link);
  if (instructions) content.append(instructions);
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
    const b = button(t('Check again first'), () => choose('reconcile'), 'option-choice secondary-choice'); b.dataset.value = 'reconcile';
    content.append(append(node('div', 'recheck'), b, node('small', '', t('Firstmate looks at the latest state again. Nothing is decided yet.'))));
  }
  const state = node('div'); state.id = 'answer-status'; state.setAttribute('role', 'status'); content.append(state);
  content.append(node('p', 'answer-help', c.close === 'release' ? t('Firstmate saves your answer and lifts the hold.') : t('Firstmate saves your answer and takes it from there.')));
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
    if (!latest.call || latest.call.occurrence !== modal.ticket.call.occurrence || latest.call.error) {
      warning.hidden = false; warning.replaceChildren(document.createTextNode(latest.call?.error || t("The question changed. Your draft is still here.")));
      if (latest.call) warning.append(button(t("See the new question"), () => { saveDraft(); renderCall(latest); }));
      modal.ticket.call.error = t("Check the new question before sending.");
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
      await loadReceipts(); $('receipts-panel').open = true; $('detail').close();
      $('receipts-panel').scrollIntoView({ block: 'start' });
    }));
    if (receipt.state === 'conflict') $('answer-status').append(button(t("See the new question"), () => openTicket(modal.key, S.opener)));
    if (receipt.saved) { S.drafts.delete(modal.key); try { sessionStorage.removeItem('deck-draft-' + modal.key); } catch {} }
  } catch (err) {
    if (S.modal !== modal) return;
    // The same request id goes out again, so a retry can never answer twice.
    $('answer-status').replaceChildren(node('p', 'notice error', err.message + t(" Your draft is safe. Pick the same answer again to retry.")));
  } finally { S.busy = false; syncSubmit(); }
}
async function openDelivery(home, task, opener) {
  const gen = ++S.generation;
  showDetail(t("Delivered work"), '', t("DELIVERED"), opener);
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
  content.append(disclosure(t('Technical details'), prose(data.details), contextRow(t('History sources'), data.sources), contextRow(t('Date provenance'), data.time?.detail)));
  const actions = node('div', 'explain-actions');
  const ask = button(t("Ask Firstmate to explain"), requestExplanation); ask.id = 'explain-request';
  const status = button(t("Check status"), refreshExplanation);
  const text = node('p', 'explain-status', t("Checking doesn’t send anything.")); text.id = 'explain-status'; text.setAttribute('role', 'status');
  append(actions, ask, status, text); content.append(actions);
  if (modal.explanation) explanationView(modal.explanation);
  addStableLink(modal.home, modal.task, 'delivery');
}
function explanationView(data) {
  if (!$('explain-status')) return;
  const meanings = { absent: t("You haven’t asked yet."), queued: t("Asked. Firstmate hasn’t seen it yet."), saved_unannounced: t("Asked, but not sure Firstmate was told."), acknowledged: t("Firstmate got your request."), answered: t("Firstmate left a reply."), unsupported: t("This space can’t take explanation requests yet."), error: t("Couldn’t check the request.") };
  const reply = data.receipt?.reply;
  const text = (meanings[data.state] || t("Not sure yet.")) + (data.message ? '\n' + data.message : '') + (reply?.body ? t('\n\nReply from Firstmate:\n') + reply.body + (reply.at ? '\n' + dateFmt.format(new Date(reply.at)) : '') : '');
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
