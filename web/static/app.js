/* User content is text only. Polling reads; only explicit buttons can POST. */
'use strict';
const $ = (id) => document.getElementById(id);
const S = { board: null, home: '', column: 'underway', online: false, view: 'board', query: '',
  busy: false, csrf: '', modal: null, opener: null, generation: 0, historyGeneration: 0,
  lastRead: 0, receipt: null, drafts: new Map(), historyTimer: null, linkHandled: false };
const labels = { charted: 'Por comenzar', underway: 'En curso', captains_call: 'Tu decisión', landed: 'Entregado' };
const notes = { charted: 'El siguiente paso', underway: 'Trabajo iniciado', captains_call: 'Necesitan tu respuesta', landed: 'Entregas recientes' };
const phaseLabels = { human: 'Espera tu respuesta', dependency: 'Espera dependencia', integration: 'Integración pendiente', technical: 'Retención técnica', active: 'Actividad declarada', unknown: 'Estado sin confirmar', waiting: 'Espera declarada', reported: 'Trabajo informado' };
const timeLabels = { Filed: 'Registrada', Queued: 'En cola', Asked: 'Pregunta', Updated: 'Actualización', Ready: 'Lista', 'Done report': 'Trabajo informado', Delivered: 'Entrega' };
const zone = Intl.DateTimeFormat().resolvedOptions().timeZone;
$('timezone').textContent = 'Horas en ' + zone;
const dateFmt = new Intl.DateTimeFormat('es', { dateStyle: 'medium', timeStyle: 'short' });
const relativeFmt = new Intl.RelativeTimeFormat('es', { numeric: 'auto' });
function node(tag, cls, text) { const n = document.createElement(tag); if (cls) n.className = cls; if (text !== undefined) n.textContent = text; return n; }
function append(parent, ...children) { parent.append(...children.filter(Boolean)); return parent; }
function button(text, action, cls = '') { const b = node('button', cls, text); b.type = 'button'; b.addEventListener('click', action); return b; }
function relative(epoch) { const seconds = epoch - Date.now() / 1000; for (const [size, unit] of [[86400, 'day'], [3600, 'hour'], [60, 'minute']]) { if (Math.abs(seconds) >= size) return relativeFmt.format(Math.round(seconds / size), unit); } return 'ahora'; }
function eventTime(t) {
  if (!t) return 'Hora no registrada';
  const label = timeLabels[t.label] || t.label || 'Evento';
  if (t.date) { const [y, m, d] = t.date.split('-'); return `${label} · ${d}/${m}/${y} · sin hora registrada`; }
  if (typeof t.epoch === 'number') { return `${label} · ${dateFmt.format(new Date(t.epoch * 1000))} · ${t.epoch > Date.now() / 1000 + 5 ? 'fecha futura; revisa el reloj' : relative(t.epoch)}`; }
  return `${label} · ${t.problem === 'not recorded' ? 'hora no registrada' : 'hora no disponible'}`;
}
function prose(text) {
  const root = node('div', 'prose');
  const headings = new Set(['Resultado', 'Alcance', 'Por qué', 'Evidencia', 'Pendiente', 'Comprobaciones', 'Límites', 'Qué cambió', 'Siguiente paso']);
  let lines = [];
  const flush = () => { if (lines.join('\n').trim()) root.append(node('p', '', lines.join('\n').trim())); lines = []; };
  for (const line of (text || 'No se conserva contenido para esta vista.').split('\n')) {
    if (headings.has(line.trim())) { flush(); root.append(node('h3', '', line.trim())); }
    else lines.push(line);
  }
  flush(); return root;
}
function contextRow(title, text) { return append(node('div', 'context-row'), node('h3', '', title), node('p', '', text || 'No registrado.')); }
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
    if (!response.ok) throw new Error(data.error || `La consulta falló (${response.status}).`);
    return data;
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
  $('connection').textContent = disconnected ? 'Sin conexión' : failed ? 'Fuente con error' : stale ? 'Datos desactualizados' : S.board.loading ? 'Cargando fuentes' : 'Conectado al Deck';
  $('connection').dataset.level = disconnected || failed ? 'error' : stale ? 'warning' : 'ok';
  $('updated').textContent = at ? `Lectura de Firstmate: ${dateFmt.format(new Date(at * 1000))} · ${relative(at)}` : 'Primera lectura pendiente.';
  if (disconnected) notice('Sin conexión con el Deck. Conservamos la última vista y tu borrador. Al volver, verifica la pregunta antes de enviar.');
  else if (failed) notice('Una fuente no se pudo actualizar. Los datos conservados pueden estar desactualizados; consulta las fuentes abajo.');
  else if (stale) notice('La lectura de Firstmate es antigua. El estado actual todavía no está confirmado.');
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
  const attention = `${counts.captains_call || 0} ${counts.captains_call === 1 ? 'decisión pendiente' : 'decisiones pendientes'}`;
  const spaces = `${S.board.homes.length} ${S.board.homes.length === 1 ? 'espacio' : 'espacios'}`;
  $('overview').textContent = matchMedia('(max-width: 639px)').matches ? attention + ' · ' + spaces : `${attention} · ${counts.underway || 0} tareas iniciadas · ${spaces}`;
  const metrics = [], nav = [];
  for (const id of ['captains_call', 'underway', 'charted', 'landed']) {
    const b = button('', () => { setView('board'); selectColumn(id, true); }, 'metric'); b.dataset.kind = id;
    append(b, node('span', 'metric-number', String(counts[id] || 0)), append(node('span', 'metric-label', labels[id]), node('span', 'metric-note', notes[id]))); metrics.push(b);
  }
  for (const c of columns) {
    const b = button('', () => selectColumn(c.id)); b.dataset.kind = c.id; b.setAttribute('aria-pressed', String(S.column === c.id));
    append(b, node('span', '', String(counts[c.id] || 0)), document.createTextNode(c.title)); nav.push(b);
  }
  $('metrics').replaceChildren(...metrics); $('columns-nav').replaceChildren(...nav);
  const existing = new Map([...$('board').children].map(n => [n.dataset.kind, n]));
  for (const c of columns) {
    let section = existing.get(c.id);
    if (!section) { section = node('section', 'column'); section.dataset.kind = c.id; $('board').append(section); }
    const cards = c.cards.filter(card => (S.query === '' || [card.title, card.context.reason, card.owner, card.task,
      ...card.dependencies.map(d => d.title || d.id)].join(' ').toLocaleLowerCase().includes(S.query)));
    const heading = append(node('div', 'column-heading'), node('span', 'column-dot'), node('h2', '', c.title), node('span', 'column-count', String(cards.length)));
    const list = node('div', 'cards');
    for (const card of cards) list.append(renderCard(card));
    if (!cards.length) list.append(node('p', 'empty', S.query ? 'No hay tareas que coincidan con la búsqueda.' : c.id === 'captains_call' ? 'Sin preguntas pendientes. Firstmate conserva el rumbo.' : c.id === 'landed' ? 'No hay entregas en esta vista reciente. Puedes buscar en el historial.' : 'No hay tareas en este estado.'));
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
  const phase = node('span', 'phase', card.answered ? 'Respuesta registrada' : card.bucket === 'landed' ? 'Entrega retenida' : card.bucket === 'charted' && card.context.phase === 'unknown' ? 'En cola' : card.context.label || phaseLabels[card.context.phase]); phase.dataset.phase = card.context.phase;
  const dependencyWait = card.context.waiting_for || (card.dependencies.length ? 'Pendiente de ' + card.dependencies.map(d => d.title || d.id).join('; ') : card.context.reason);
  const waitNamesDependency = card.context.phase === 'dependency' && card.dependencies.some(d => dependencyWait.trim() === (d.title || d.id));
  const dependencyOwner = waitNamesDependency ? ' · ' + card.dependencies.map(d => d.owner === '(main)' ? card.owner : d.owner || 'responsable sin registrar').join('; ') : '';
  const reason = card.context.phase === 'dependency' ? 'Espera: ' + dependencyWait + dependencyOwner : card.context.phase === 'unknown' ? 'Último reporte: ' + card.context.reason : card.context.reason;
  append(b, append(node('div', 'card-top'), node('span', 'owner', card.owner)), node('h3', '', card.title), phase,
    node('p', 'card-reason', card.bucket === 'landed' ? card.summary || 'Abre la entrega para leer el resultado conservado.' : reason));
  if (card.dependencies.length && !waitNamesDependency) b.append(node('p', 'dependency', '↳ ' + card.dependencies.map(d => (d.title || d.id) + ' · ' + (d.owner === '(main)' ? card.owner : d.owner || 'responsable sin registrar') + (d.unresolved === false ? ' · resuelta' : '')).join('; ')));
  if (card.bucket === 'underway') {
    if (card.context.waiting_for) b.append(node('p', 'dependency', 'Condición: ' + card.context.waiting_for));
    b.append(node('p', 'dependency', 'Firstmate: ' + card.context.next_action));
    b.append(node('p', 'dependency', card.context.native_status));
    if (card.report?.state) b.append(node('p', 'dependency', 'Reporte: ' + eventTime(card.report.time)));
  }
  const time = node('span', '', eventTime(card.time)); time.title = card.time.detail;
  append(b, append(node('div', 'card-time'), time, node('span', '', card.bucket === 'captains_call' ? 'Ver pregunta →' : card.bucket === 'landed' ? 'Leer entrega →' : 'Ver contexto →')));
  return b;
}
function supervisorText(s) {
  if (!s || !s.observed_at) return 'Actividad actual desconocida. No hay una declaración del supervisor.';
  const epoch = Date.parse(s.observed_at) / 1000;
  const ttl = Number(s.ttl || s.ttl_seconds || 0);
  const stale = s.freshness === 'stale' || (ttl > 0 && Date.now() / 1000 - epoch > ttl);
  const state = { coordinating: 'coordinación', waiting: 'espera', idle: 'inactividad' }[s.state] || 'estado desconocido';
  const unknown = !Number.isFinite(epoch) || ttl <= 0 || s.freshness === 'unknown' || epoch > Date.now() / 1000 + 5;
  return `${stale ? 'Declaración vencida; actividad actual desconocida.' : unknown ? 'Vigencia sin confirmar; actividad actual desconocida.' : 'Declaró ' + state + '.'} ${s.summary || 'Sin propósito registrado.'} ${Number.isFinite(epoch) ? dateFmt.format(new Date(epoch * 1000)) + ' · ' + relative(epoch) + '.' : 'Hora no verificable.'} La actividad actual del modelo no se conoce.`;
}
function renderSupervisors() {
  $('supervisors').replaceChildren(...(S.board.sources || []).map(source => {
    const row = node('div', 'supervisor');
    const title = append(node('div'), node('strong', '', source.label), node('small', '', source.error || (source.received_at ? 'Fuente leída ' + relative(source.received_at) : 'Sin lectura confirmada')));
    if (source.generated && Number.isFinite(Date.parse(source.generated))) title.append(node('small', '', 'Export: ' + dateFmt.format(new Date(source.generated))));
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
      $('crew').replaceChildren(new Option('Todas', ''), ...data.homes.map(h => new Option(h.label, h.id)));
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
  } catch (err) { S.online = false; sourceStatus(); if (!S.board) { $('board').replaceChildren(node('p', 'empty', 'No se pudo cargar el tablero. ' + err.message)); notice('No se ha confirmado ninguna lectura. Reintenta con Actualizar.', true); $('connection').textContent = 'No disponible'; } }
}
function setView(view) {
  S.view = view; $('board-view-panel').hidden = view !== 'board'; $('history-panel').hidden = view !== 'history';
  $('board-view').setAttribute('aria-pressed', String(view === 'board')); $('history-view').setAttribute('aria-pressed', String(view === 'history'));
  if (view === 'history') loadHistory();
}
async function loadHistory() {
  const generation = ++S.historyGeneration;
  $('history-status').textContent = 'Buscando en los registros retenidos…';
  try {
    const query = new URLSearchParams({ home: S.home, q: $('history-search').value });
    const data = await api('api/history?' + query);
    if (generation !== S.historyGeneration) return;
    $('history-status').textContent = `${data.records.length} entregas${data.omitted ? ' · ' + data.omitted + ' resultados adicionales; afina la búsqueda' : ''}. ${data.scope}`;
    $('history-results').replaceChildren(...data.records.map(row => {
      const b = button('', () => openDelivery(row.home, row.task, b), 'task-card');
      append(b, node('p', 'card-top', row.owner), node('h3', '', row.title), node('p', 'card-time', eventTime(row.time)), node('span', 'herdr-link', 'Leer entrega →')); return b;
    }));
    if (!data.records.length) $('history-results').append(node('p', 'empty', 'No se encontraron entregas retenidas para esta búsqueda.'));
    $('history-sources').replaceChildren(...data.details.map(d => contextRow(d.home, d.source)));
  } catch (err) { if (generation === S.historyGeneration) $('history-status').textContent = 'No se pudo actualizar el historial. ' + err.message; }
}
function showDetail(title, meta, kicker, opener) {
  S.opener = opener || document.activeElement; S.receipt = null;
  S.openerKey = S.opener?.dataset?.key;
  $('detail-title').textContent = title; $('detail-meta').textContent = meta; $('detail-kicker').textContent = kicker;
  $('detail-content').replaceChildren(node('p', 'muted', 'Cargando el contexto…'));
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
  showDetail('Contexto de la tarea', '', 'ESTADO DECLARADO', opener);
  S.modal = { type: 'loading', key };
  try {
    const ticket = await api('api/ticket/' + encodeURIComponent(key));
    if (gen !== S.generation || !$('detail').open) return;
    $('detail-title').textContent = ticket.title; $('detail-meta').textContent = ticket.owner + ' · ' + eventTime(ticket.time);
    if (ticket.call) renderCall(ticket);
    else {
      S.modal = { type: 'task', key, ticket };
      const c = ticket.context;
      const reasonLabel = c.phase === 'dependency' || c.phase === 'unknown' ? 'Último reporte registrado' : c.label;
      $('detail-content').replaceChildren(contextRow(reasonLabel, c.reason), contextRow('Condición para retomar', c.waiting_for || (ticket.dependencies.length ? 'Pendiente de ' + ticket.dependencies.map(d => d.title || d.id).join('; ') : 'No hay una condición adicional registrada.')), contextRow('Siguiente paso', c.next_action), contextRow('Responsable', ticket.owner));
      if (ticket.report?.state) $('detail-content').append(contextRow('Reporte durable · ' + ticket.report.state, ticket.report.text), contextRow('Fecha del reporte', eventTime(ticket.report.time)), contextRow('Procedencia del reporte', ticket.report.time.detail));
      $('detail-content').append(contextRow('Estado nativo', c.native_status), contextRow('Observación original de Firstmate', ticket.native?.detail));
      for (const d of ticket.dependencies) $('detail-content').append(contextRow('Dependencia · ' + (d.title || d.id), `Responsable: ${d.owner === '(main)' ? ticket.owner : d.owner || 'no registrado'}. Estado: ${d.state || 'desconocido'}.${d.unresolved === false ? ' Resuelta.' : ''}${d.reconciliation_required ? ' El vínculo necesita conciliación de Firstmate.' : ''}`));
      $('detail-content').append(contextRow('Declaración de Firstmate', supervisorText(ticket.supervisor)), contextRow('Agente registrado', ticket.agent || 'No registrado.'));
      addTimeDetails(ticket.time); addHerdr(ticket);
      if (ticket.original_title !== ticket.title) $('detail-content').append(append(node('details', 'source-details'), node('summary', '', 'Título original conservado'), prose(ticket.original_title)));
    }
  } catch (err) { if (gen === S.generation) $('detail-content').replaceChildren(node('p', 'notice error', err.message)); }
}
function addTimeDetails(time) { const details = node('details', 'source-details'); append(details, node('summary', '', 'Fecha exacta y procedencia'), prose(time?.detail || 'Hora no registrada.')); $('detail-content').append(details); }
function addHerdr(ticket) { if (!ticket.herdr_url) return; const a = node('a', 'herdr-link', 'Abrir este agente en Herdr Web ↗'); a.href = ticket.herdr_url; a.target = '_blank'; a.rel = 'noopener noreferrer'; $('detail-content').append(a); }
function addStableLink(home, task, view) {
  if (!home) return;
  const url = new URL(location.href); url.search = new URLSearchParams({ home, task, view }).toString(); url.hash = '';
  const a = node('a', 'herdr-link', 'Enlace permanente a esta tarea ↗'); a.href = url.href;
  $('detail-content').append(a);
}
async function openStableLink(params) {
  const home = params.get('home') || '', task = params.get('task') || '', view = params.get('view') || '';
  showDetail('Abriendo la tarea', '', 'ENLACE DE FIRSTMATE');
  const gen = ++S.generation;
  try {
    const result = await api('api/resolve?' + new URLSearchParams({ home, task, view, item: params.get('item') || '' }));
    if (gen !== S.generation || !$('detail').open) return;
    if (result.view === 'call') { await openTicket(result.key, S.opener); return; }
    if (result.view === 'delivery') { await openDelivery(home, task, S.opener); if (S.modal?.type === 'delivery' && result.notice) { S.modal.linkNotice = result.notice; renderDelivery(); } return; }
    S.modal = { type: 'task' };
    $('detail-title').textContent = 'La pregunta de este aviso'; $('detail-meta').textContent = result.owner + ' · ' + result.task;
    $('detail-content').replaceChildren(prose(result.message));
    for (const receipt of result.receipts || []) $('detail-content').append(receiptView(receipt));
    if (result.current_key) $('detail-content').append(button('Ver la pregunta actual', async () => { await openTicket(result.current_key, S.opener); $('detail-kicker').textContent = 'PREGUNTA ACTUAL'; }));
    $('detail-content').append(button('Buscar esta tarea en el historial', () => { $('history-search').value = task; $('detail').close(); setView('history'); }));
  } catch (err) { if (gen === S.generation) $('detail-content').replaceChildren(node('p', 'notice error', err.message)); }
}
function renderCall(ticket, draftOverride) {
  const key = ticket.key, c = ticket.call, draft = draftOverride || readDraft(key);
  const staleDraft = !!draft.occurrence && draft.occurrence !== c.occurrence && !!(draft.note || draft.value);
  S.modal = { type: 'call', key, ticket, request: draft.request || crypto.randomUUID().replaceAll('-', ''), draftNeedsReview: staleDraft, previousOccurrence: draft.occurrence };
  $('detail-kicker').textContent = 'CAPTAIN’S CALL'; $('detail-title').textContent = c.title || ticket.title;
  const content = $('detail-content'); content.replaceChildren();
  const error = node('p', 'notice', c.error || (staleDraft ? 'Este borrador pertenece a una pregunta anterior. Se conserva aquí: selecciona una opción actual o edita tu respuesta para habilitar el envío.' : '')); error.id = 'call-change'; error.hidden = !c.error && !staleDraft; content.append(error);
  if (c.about) content.append(prose(c.about));
  content.append(node('p', 'question', c.decide));
  // Canonical human hold reason remains explicit even with a composed card.
  if (ticket.context.reason && ticket.context.reason !== c.about) content.append(contextRow('Motivo de la espera', ticket.context.reason));
  if (c.detail) { const d = node('details', 'source-details'); append(d, node('summary', '', 'Contexto completo de la pregunta'), prose(c.detail)); content.append(d); }
  const form = node('fieldset', 'option-list'); form.append(node('legend', '', 'Elige una opción o responde con tus palabras'));
  for (const option of c.options) {
    const choice = node('label', 'choice'), input = node('input'); input.type = 'radio'; input.name = 'answer'; input.value = option.value; input.checked = !staleDraft && draft.value === option.value;
    const text = append(node('span'), node('strong', '', option.label));
    if (option.value === c.recommend) text.append(node('span', 'recommended', 'Sugerida'));
    if (option.hint) text.append(node('small', '', option.hint));
    input.addEventListener('change', saveDraft); append(choice, input, text); form.append(choice);
  }
  if (c.freeform) {
    const choice = node('label', 'choice'), input = node('input'); input.type = 'radio'; input.name = 'answer'; input.value = ''; input.checked = !staleDraft && !draft.value;
    input.addEventListener('change', saveDraft); append(choice, input, append(node('span'), node('strong', '', 'Responder con mis palabras'), node('small', '', c.freeform_hint))); form.append(choice);
  }
  content.append(form);
  const noteLabel = node('label', 'answer-label', c.freeform ? 'Tu respuesta o contexto adicional' : 'Contexto adicional (opcional)');
  const note = node('textarea'); note.id = 'answer-note'; note.rows = 3; note.value = draft.note || ''; note.placeholder = 'Escribe aquí…'; note.addEventListener('input', saveDraft); noteLabel.append(note); content.append(noteLabel);
  content.append(node('p', 'answer-help', 'Máximo 512 bytes, una línea. Elegir una opción o escribir conserva el borrador; sólo Enviar lo comunica.'));
  const state = node('div'); state.id = 'answer-status'; state.setAttribute('role', 'status'); content.append(state);
  const actions = node('div', 'action-bar');
  append(actions, node('p', '', c.close === 'release' ? 'Firstmate guardará tu respuesta y liberará esta retención. Esto no confirma que el agente se haya reanudado.' : 'Firstmate registrará tu selección mediante el intake de esta tarea. Reconciliar pide revisión; no resuelve la llamada.'), button('Enviar respuesta', submitAnswer, 'primary'));
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
      warning.hidden = false; warning.replaceChildren(document.createTextNode(latest.call?.error || 'La pregunta cambió. Tu borrador sigue aquí.'));
      $('answer-submit').disabled = true;
      if (latest.call) warning.append(button('Revisar pregunta actual', () => { saveDraft(); renderCall(latest); }));
      modal.ticket.call.error = 'Revisa la pregunta actual antes de enviar.';
    }
  } catch (err) { if (S.modal === modal && $('call-change')) { $('call-change').hidden = false; $('call-change').textContent = 'No se pudo verificar la pregunta. Tu borrador se conserva.'; $('answer-submit').disabled = true; } }
  finally { modal.checking = false; }
}
function receiptView(receipt) {
  const box = node('div', 'receipt'); box.setAttribute('role', 'status');
  append(box, node('h3', '', receipt.saved ? 'Respuesta registrada' : receipt.saved === false ? 'Respuesta sin enviar' : 'Envío por verificar'), node('p', '', receipt.message));
  const dl = node('dl');
  for (const [label, text] of [['Guardado', receipt.saved === true ? 'Confirmado por intake' : 'Sin confirmar'], ['Aviso al responsable', receipt.notice === 'queued' ? 'En cola' : 'Sin confirmar'], ['ACK del responsable', receipt.ack === 'acknowledged' ? 'Recibido' : 'Sin confirmar'], ['Consumo de la respuesta', receipt.consumed === 'consumed' ? 'Confirmado' : 'Sin confirmar']]) append(dl, node('dt', '', label), node('dd', '', text));
  append(box, dl, node('p', '', 'Guardado y ACK son hechos distintos. Este recibo no confirma una reanudación del agente.'));
  if (receipt.detail) append(box, append(node('details', 'source-details'), node('summary', '', 'Detalle del recibo'), prose(receipt.detail)));
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
  S.busy = true; $('answer-submit').disabled = true; $('answer-submit').textContent = 'Enviando…';
  try {
    const receipt = await api('api/ticket/' + encodeURIComponent(modal.key) + '/answer', { value: draft.value || '', note: draft.note || '', occurrence: modal.ticket.call.occurrence, request_id: modal.request });
    if (S.modal !== modal) return;
    S.receipt = receipt; $('answer-status').replaceChildren(receiptView(receipt));
    $('answer-submit').textContent = receipt.saved ? 'Registrada' : receipt.saved === false ? 'Sin enviar' : 'Sin confirmar';
    $('answer-status').append(button('Consultar recibos', async () => {
      await loadReceipts(); $('receipts-panel').open = true; $('detail').close();
      $('receipts-panel').scrollIntoView({ block: 'start' });
    }));
    if (receipt.state === 'conflict') $('answer-status').append(button('Revisar pregunta actual', () => openTicket(modal.key, S.opener)));
    if (receipt.saved) { S.drafts.delete(modal.key); try { sessionStorage.removeItem('deck-draft-' + modal.key); } catch {} }
  } catch (err) {
    if (S.modal !== modal) return;
    $('answer-status').replaceChildren(node('p', 'notice error', err.message + ' Tu borrador se conserva.'));
    $('answer-submit').textContent = 'Reintentar el mismo envío';
  } finally { S.busy = false; syncSubmit(); }
}
async function openDelivery(home, task, opener) {
  const gen = ++S.generation;
  showDetail('Entrega de la tripulación', '', 'ENTREGA RETENIDA', opener);
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
  const tabs = node('nav', 'detail-tabs'); tabs.setAttribute('aria-label', 'Contenido de la entrega');
  for (const [id, label] of [['summary', 'Resumen'], ['report', 'Informe'], ['details', 'Detalles']]) { const b = button(label, () => { modal.tab = id; renderDelivery(); }); b.setAttribute('aria-pressed', String(modal.tab === id)); tabs.append(b); }
  append(content, tabs, prose(data[modal.tab]));
  if (modal.tab === 'details') { content.append(contextRow('Fuentes del historial', data.sources)); addTimeDetails(data.time); }
  const actions = node('div', 'explain-actions');
  const ask = button('Pedir explicación a Firstmate', requestExplanation); ask.id = 'explain-request';
  const status = button('Consultar estado', refreshExplanation);
  const text = node('p', 'explain-status', 'Consultar no envía una solicitud.'); text.id = 'explain-status'; text.setAttribute('role', 'status');
  append(actions, ask, status, text); content.append(actions);
  if (modal.explanation) explanationView(modal.explanation);
  addStableLink(modal.home, modal.task, 'delivery');
}
function explanationView(data) {
  if (!$('explain-status')) return;
  const meanings = { absent: 'Todavía no se ha solicitado una explicación.', queued: 'Petición guardada y aviso en cola. Firstmate aún no confirmó la recepción.', saved_unannounced: 'Petición guardada; no se confirmó el aviso a Firstmate.', acknowledged: 'Firstmate confirmó la recepción (ACK). Eso no confirma que esté preparando una respuesta.', answered: 'Firstmate dejó una respuesta.', unsupported: 'Este home aún no ofrece el intake de explicación.', error: 'No se pudo verificar el estado de la petición.' };
  const reply = data.receipt?.reply;
  $('explain-status').textContent = (meanings[data.state] || 'Estado no confirmado.') + (data.message ? '\n' + data.message : '') + (reply?.body ? '\n\nRespuesta de Firstmate:\n' + reply.body + (reply.at ? '\n' + dateFmt.format(new Date(reply.at)) : '') : '');
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
$('crew').addEventListener('change', () => { S.home = $('crew').value; S.board = null; $('board').replaceChildren(node('p', 'placeholder', 'Cargando esta tripulación…')); poll(); if (S.view === 'history') loadHistory(); });
$('board-view').addEventListener('click', () => setView('board'));
$('history-view').addEventListener('click', () => setView('history'));
$('refresh').addEventListener('click', () => { poll(); if (S.view === 'history') loadHistory(); });
$('board-search').addEventListener('input', () => { S.query = $('board-search').value.trim().toLocaleLowerCase(); renderBoard(); });
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
