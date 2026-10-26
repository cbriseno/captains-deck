/* English source messages are catalog keys. Never translate task data. */
'use strict';
DeckI18n.ready.then(({t, locale, dateFmt, relative, eventTime}) => {
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
  let lines = [];
  const flush = () => { if (lines.join('\n').trim()) root.append(node('p', '', lines.join('\n').trim())); lines = []; };
  for (const line of (text || t("No content is retained for this view.")).split('\n')) {
    if (headings.has(line.trim())) { flush(); root.append(node('h3', '', line.trim())); }
    else lines.push(line);
  }
  flush(); return root;
}
function contextRow(title, text) { return append(node('div', 'context-row'), node('h3', '', title), node('p', '', text || t("Not recorded."))); }
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
function sourceStatus() {
  if (!S.board) return;
  const sources = S.board.sources || [];
  const latest = sources.map(x => x.received_at).filter(x => x > 0);
  const at = latest.length ? Math.min(...latest) : 0;
  const stale = at && Date.now() / 1000 - at > Math.max(60, S.board.source_ttl * 2);
  const failed = S.board.error || sources.some(s => s.error);
  const disconnected = !S.online || Date.now() - S.lastRead > 30000;
  $('connection').textContent = disconnected ? t("Offline") : failed ? t("Source error") : stale ? t("Stale data") : S.board.loading ? t("Loading sources") : t("Connected to Deck");
  $('connection').dataset.level = disconnected || failed ? 'error' : stale ? 'warning' : 'ok';
  $('updated').textContent = at ? t('Firstmate read: {date} · {age}', {date: dateFmt.format(new Date(at * 1000)), age: relative(at)}) : t("First read pending.");
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
  $('metrics').replaceChildren(...metrics); $('columns-nav').replaceChildren(...nav);
  const existing = new Map([...$('board').children].map(n => [n.dataset.kind, n]));
  for (const c of columns) {
    let section = existing.get(c.id);
    if (!section) { section = node('section', 'column'); section.dataset.kind = c.id; $('board').append(section); }
    const cards = c.cards.filter(card => (S.query === '' || [card.title, card.context.reason, card.owner, card.task,
      ...card.dependencies.map(d => d.title || d.id)].join(' ').toLocaleLowerCase(locale).includes(S.query)));
    const heading = append(node('div', 'column-heading'), node('span', 'column-dot'), node('h2', '', labels[c.id] || c.title), node('span', 'column-count', String(cards.length)));
    const list = node('div', 'cards');
    for (const card of cards) list.append(renderCard(card));
    if (!cards.length) list.append(node('p', 'empty', S.query ? t("No tasks match your search.") : c.id === 'captains_call' ? t("No pending questions. Firstmate is keeping course.") : c.id === 'landed' ? t("No deliveries in this recent view. Search history for retained deliveries.") : t("No tasks in this state.")));
    // Preserve focus when a polling update replaces the selected card.
    const focusedKey = section.contains(document.activeElement) ? document.activeElement.dataset.key : '';
    section.replaceChildren(heading, list);
    if (focusedKey) [...list.children].find(n => n.dataset.key === focusedKey)?.focus({ preventScroll: true });
  }
  for (const n of [...$('board').children]) if (!n.dataset.kind) n.remove();
  selectColumn(S.column); renderSupervisors(); sourceStatus();
}
function renderCard(card) {
  const b = button('', () => card.bucket === 'landed' && card.home ? openDelivery(card.home, card.task, b) : openTicket(card.key, b), 'task-card'); b.dataset.key = card.key;
  const phase = node('span', 'phase', card.answered ? t("Answer recorded") : card.bucket === 'landed' ? t("Retained delivery") : card.bucket === 'charted' && card.context.phase === 'unknown' ? t("Queued") : card.context.label || phaseLabels[card.context.phase]); phase.dataset.phase = card.context.phase;
  const dependencyWait = card.context.waiting_for || (card.dependencies.length ? t("Waiting for ") + card.dependencies.map(d => d.title || d.id).join('; ') : card.context.reason);
  const waitNamesDependency = card.context.phase === 'dependency' && card.dependencies.some(d => dependencyWait.trim() === (d.title || d.id));
  const dependencyOwner = waitNamesDependency ? ' · ' + card.dependencies.map(d => d.owner === '(main)' ? card.owner : d.owner || t("owner not recorded")).join('; ') : '';
  const reason = card.context.phase === 'dependency' ? t("Waiting: ") + dependencyWait + dependencyOwner : card.context.phase === 'unknown' ? t("Last report: ") + card.context.reason : card.context.reason;
  append(b, append(node('div', 'card-top'), node('span', 'owner', card.owner)), node('h3', '', card.title), phase,
    node('p', 'card-reason', card.bucket === 'landed' ? card.summary || t("Open the delivery to read the retained result.") : reason));
  if (card.dependencies.length && !waitNamesDependency) b.append(node('p', 'dependency', '↳ ' + card.dependencies.map(d => (d.title || d.id) + ' · ' + (d.owner === '(main)' ? card.owner : d.owner || t("owner not recorded")) + (d.unresolved === false ? t(" · resolved") : '')).join('; ')));
  if (card.bucket === 'underway') {
    if (card.context.waiting_for) b.append(node('p', 'dependency', t("Condition: ") + card.context.waiting_for));
    b.append(node('p', 'dependency', 'Firstmate: ' + card.context.next_action));
    b.append(node('p', 'dependency', card.context.native_status));
    if (card.report?.state) b.append(node('p', 'dependency', t("Report: ") + eventTime(card.report.time)));
  }
  const time = node('span', '', eventTime(card.time)); time.title = card.time.detail;
  append(b, append(node('div', 'card-time'), time, node('span', '', card.bucket === 'captains_call' ? t("View question →") : card.bucket === 'landed' ? t("Read delivery →") : t("View context →"))));
  return b;
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
  $('supervisors').replaceChildren(...(S.board.sources || []).map(source => {
    const row = node('div', 'supervisor');
    const title = append(node('div'), node('strong', '', source.label), node('small', '', source.error || (source.received_at ? t("Source read ") + relative(source.received_at) : t("No confirmed read"))));
    if (source.generated && Number.isFinite(Date.parse(source.generated))) title.append(node('small', '', t('Export: ') + dateFmt.format(new Date(source.generated))));
    append(row, title, node('p', '', supervisorText(source.supervisor))); return row;
  }));
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
      const b = button('', () => openDelivery(row.home, row.task, b), 'task-card');
      append(b, node('p', 'card-top', row.owner), node('h3', '', row.title), node('p', 'card-time', eventTime(row.time)), node('span', 'herdr-link', t("Read delivery →"))); return b;
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
    $('detail-title').textContent = ticket.title; $('detail-meta').textContent = ticket.owner + ' · ' + eventTime(ticket.time);
    if (ticket.call) renderCall(ticket);
    else {
      S.modal = { type: 'task', key, ticket };
      const c = ticket.context;
      const reasonLabel = c.phase === 'dependency' || c.phase === 'unknown' ? t("Last recorded report") : c.label;
      $('detail-content').replaceChildren(contextRow(reasonLabel, c.reason), contextRow(t("Condition for resuming"), c.waiting_for || (ticket.dependencies.length ? t("Waiting for ") + ticket.dependencies.map(d => d.title || d.id).join('; ') : t("No additional condition is recorded."))), contextRow(t("Next step"), c.next_action), contextRow(t("Owner"), ticket.owner));
      if (ticket.report?.state) $('detail-content').append(contextRow(t("Durable report · ") + ticket.report.state, ticket.report.text), contextRow(t("Report date"), eventTime(ticket.report.time)), contextRow(t("Report provenance"), ticket.report.time.detail));
      $('detail-content').append(contextRow(t("Native state"), c.native_status), contextRow(t("Original Firstmate observation"), ticket.native?.detail));
      for (const d of ticket.dependencies) $('detail-content').append(contextRow(t("Dependency · ") + (d.title || d.id), t('Owner: {owner}. State: {state}.{resolved}{reconciliation}', {owner: d.owner === '(main)' ? ticket.owner : d.owner || t('not recorded'), state: d.state || t('unknown'), resolved: d.unresolved === false ? t(' Resolved.') : '', reconciliation: d.reconciliation_required ? t(' The link needs reconciliation by Firstmate.') : ''})));
      $('detail-content').append(contextRow(t("Firstmate declaration"), supervisorText(ticket.supervisor)), contextRow(t("Recorded agent"), ticket.agent || t("Not recorded.")));
      addTimeDetails(ticket.time); addHerdr(ticket);
      if (ticket.original_title !== ticket.title) $('detail-content').append(append(node('details', 'source-details'), node('summary', '', t("Retained original title")), prose(ticket.original_title)));
    }
  } catch (err) { if (gen === S.generation) $('detail-content').replaceChildren(node('p', 'notice error', err.message)); }
}
function addTimeDetails(time) { const details = node('details', 'source-details'); append(details, node('summary', '', t("Exact date and provenance")), prose(time?.detail || t("Time not recorded."))); $('detail-content').append(details); }
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
  $('detail-kicker').textContent = 'CAPTAIN’S CALL'; $('detail-title').textContent = c.title || ticket.title;
  const content = $('detail-content'); content.replaceChildren();
  const error = node('p', 'notice', c.error || (staleDraft ? t("This draft belongs to an earlier question. It is preserved here: select a current option or edit your answer to enable sending.") : '')); error.id = 'call-change'; error.hidden = !c.error && !staleDraft; content.append(error);
  if (c.presentation?.about || c.about) content.append(prose(c.presentation?.about || c.about));
  content.append(node('p', 'question', (c.presentation?.decide || c.decide)));
  // Canonical human hold reason remains explicit even with a composed card.
  if (ticket.context.reason && ticket.context.reason !== c.about) content.append(contextRow(t("Reason for waiting"), ticket.context.reason));
  if (c.detail) { const d = node('details', 'source-details'); append(d, node('summary', '', t("Full question context")), prose(c.detail)); content.append(d); }
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
  content.append(actions); addTimeDetails(ticket.time); addHerdr(ticket); addStableLink(ticket.home, ticket.task, 'call'); syncSubmit();
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
    $('receipts').replaceChildren(...data.receipts.map(r => {
      const detail = node('details', 'source-details');
      append(detail, node('summary', '', (r.title || r.task) + ' · ' + dateFmt.format(new Date(r.at * 1000))), receiptView(r));
      return detail;
    }));
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
    $('detail-title').textContent = data.title; $('detail-meta').textContent = data.owner + ' · ' + eventTime(data.time);
    renderDelivery(); refreshExplanation();
  } catch (err) { if (gen === S.generation) $('detail-content').replaceChildren(node('p', 'notice error', err.message)); }
}
function renderDelivery() {
  const modal = S.modal, data = modal.data, content = $('detail-content'); content.replaceChildren();
  if (modal.linkNotice) content.append(node('p', 'notice', modal.linkNotice));
  const tabs = node('nav', 'detail-tabs'); tabs.setAttribute('aria-label', t("Delivery content"));
  for (const [id, label] of [['summary', t("Summary")], ['report', t("Report")], ['details', t("Details")]]) { const b = button(label, () => { modal.tab = id; renderDelivery(); }); b.setAttribute('aria-pressed', String(modal.tab === id)); tabs.append(b); }
  append(content, tabs, prose(data[modal.tab]));
  if (modal.tab === 'details') { content.append(contextRow(t("History sources"), data.sources)); addTimeDetails(data.time); }
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
  $('explain-status').textContent = (meanings[data.state] || t("State unconfirmed.")) + (data.message ? '\n' + data.message : '') + (reply?.body ? t('\n\nReply from Firstmate:\n') + reply.body + (reply.at ? '\n' + dateFmt.format(new Date(reply.at)) : '') : '');
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
  saveDraft(); S.modal = null; S.generation++;
  const current = S.opener?.isConnected && S.opener.tagName !== 'BODY' ? S.opener : [...document.querySelectorAll('.task-card')].find(n => n.dataset.key === S.openerKey) || $('board-view');
  current.focus({ preventScroll: true });
});
$('crew').addEventListener('change', () => { S.home = $('crew').value; S.board = null; $('board').replaceChildren(node('p', 'placeholder', t("Loading this crew…"))); poll(); if (S.view === 'history') loadHistory(); });
$('board-view').addEventListener('click', () => setView('board'));
$('history-view').addEventListener('click', () => setView('history'));
$('refresh').addEventListener('click', () => { poll(); if (S.view === 'history') loadHistory(); });
$('board-search').addEventListener('input', () => { S.query = $('board-search').value.trim().toLocaleLowerCase(locale); renderBoard(); });
$('history-search').addEventListener('input', () => { clearTimeout(S.historyTimer); S.historyTimer = setTimeout(loadHistory, 250); });
window.addEventListener('offline', () => { S.online = false; sourceStatus(); });
window.addEventListener('online', poll);
matchMedia('(max-width: 639px)').addEventListener('change', renderBoard);
document.addEventListener('visibilitychange', () => { if (!document.hidden) poll(); });
setInterval(poll, 4000); setInterval(() => { sourceStatus(); if (S.board) renderSupervisors(); }, 1000);
// Retire an old Deck push/offline worker. It must never intercept actions or
// show stale API success; offline honesty is handled by the visible live view.
if ('serviceWorker' in navigator) navigator.serviceWorker.getRegistrations().then(regs => { for (const reg of regs) if (reg.scope === new URL('./', location.href).href) reg.unregister(); }).catch(() => {});
poll();

});
