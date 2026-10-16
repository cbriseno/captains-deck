/* Captain's Deck PWA shell.
 *
 * Renders what /api/board returns and posts answers to /api/ticket/<key>/answer.
 * All card and ticket text is untrusted data: it reaches the DOM through
 * textContent, never through innerHTML.
 *
 * Columns keep their DOM across board updates, so a reader keeps the place
 * they scrolled to while the deck refreshes underneath.
 */

const ACTIVE_COLUMNS = ["charted", "underway", "captains_call", "awaiting_merge", "landed"];
const THEME_KEY = "deck.theme";
const SHOW_ANSWERED_KEY = "deck.showAnswered";
const THEME_MODES = ["auto", "dark", "light"];

/** ANSI-16 colour codes the TUI uses, mapped to theme colours. */
const BADGE_COLORS = {
  31: "var(--bad)",
  32: "var(--ok)",
  33: "var(--warn)",
  35: "var(--decide)",
  36: "var(--review)",
  90: "var(--dim)",
};

const state = {
  board: null,
  home: "",
  socket: null,
  retry: 0,
  openKey: "",
  ticket: null,
  choice: "",
  optionRows: [],
  submitting: false,
  sections: new Map(),
  order: [],
  page: 0,
  raf: 0,
  themeMode: "auto",
  showAnswered: false,
};

const el = {
  board: document.querySelector("#board"),
  homes: document.querySelector("#homes"),
  dots: document.querySelector("#dots"),
  conn: document.querySelector("#conn"),
  stamp: document.querySelector("#stamp"),
  error: document.querySelector("#error"),
  refresh: document.querySelector("#refresh"),
  sheet: document.querySelector("#sheet"),
  sheetTitle: document.querySelector("#sheet-title"),
  sheetBadge: document.querySelector("#sheet-badge"),
  sheetRepo: document.querySelector("#sheet-repo"),
  sheetAbout: document.querySelector("#sheet-about"),
  sheetDecide: document.querySelector("#sheet-decide"),
  sheetDetailBox: document.querySelector("#sheet-detail-box"),
  sheetDetail: document.querySelector("#sheet-detail"),
  sheetOptions: document.querySelector("#sheet-options"),
  sheetNote: document.querySelector("#sheet-note"),
  sheetState: document.querySelector("#sheet-state"),
  sheetSubmit: document.querySelector("#sheet-submit"),
  sheetClose: document.querySelector("#sheet-close"),
  debugButton: document.querySelector("#debug-button"),
  settings: document.querySelector("#settings"),
  settingsButton: document.querySelector("#settings-button"),
  settingsClose: document.querySelector("#settings-close"),
  pushState: document.querySelector("#push-state"),
  pushOn: document.querySelector("#push-on"),
  pushOff: document.querySelector("#push-off"),
  pushTest: document.querySelector("#push-test"),
  insetRow: document.querySelector("#inset-row"),
  insetValue: document.querySelector("#inset-value"),
  insetPlus: document.querySelector("#inset-plus"),
  insetMinus: document.querySelector("#inset-minus"),
  showAnsweredOn: document.querySelector("#show-answered-on"),
  showAnsweredOff: document.querySelector("#show-answered-off"),
};

function node(tag, className, text) {
  const n = document.createElement(tag);
  if (className) n.className = className;
  if (text !== undefined && text !== null && text !== "") n.textContent = text;
  return n;
}

function fmtElapsed(seconds) {
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return s + "s";
  const m = Math.floor(s / 60);
  if (m < 60) return m + "m" + String(s % 60).padStart(2, "0") + "s";
  const h = Math.floor(m / 60);
  return h + "h" + String(m % 60).padStart(2, "0") + "m";
}

function setConn(name, title) {
  el.conn.dataset.state = name;
  el.conn.title = title || name;
}

// --------------------------------------------------------------------------
// Theme
// --------------------------------------------------------------------------

function systemPrefersLight() {
  return window.matchMedia("(prefers-color-scheme: light)").matches;
}

function resolvedTheme(mode) {
  if (mode === "light" || mode === "dark") return mode;
  return systemPrefersLight() ? "light" : "dark";
}

function storedTheme() {
  try {
    return localStorage.getItem(THEME_KEY) || "auto";
  } catch {
    return "auto"; // private mode: the deck still works, the choice just will not stick
  }
}

function storeTheme(mode) {
  try {
    localStorage.setItem(THEME_KEY, mode);
  } catch {
    /* the choice is session-only when storage is unavailable */
  }
}

function applyTheme(mode) {
  state.themeMode = THEME_MODES.includes(mode) ? mode : "auto";
  const resolved = resolvedTheme(state.themeMode);
  document.documentElement.dataset.theme = resolved;
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.setAttribute("content", resolved === "light" ? "#f5f7fb" : "#0b1220");
  for (const button of document.querySelectorAll("[data-theme-mode]")) {
    button.dataset.selected = String(button.dataset.themeMode === state.themeMode);
  }
}

// --------------------------------------------------------------------------
// Safe area
// --------------------------------------------------------------------------

function isStandalone() {
  return (
    window.matchMedia("(display-mode: standalone)").matches ||
    window.navigator.standalone === true
  );
}

function isIOS() {
  // iPadOS calls itself a Mac, so the touch count tells the two apart.
  return (
    /iPad|iPhone|iPod/.test(navigator.userAgent) ||
    (/Macintosh/.test(navigator.userAgent) && navigator.maxTouchPoints > 1)
  );
}

function measuredInset(property) {
  const probe = document.createElement("div");
  probe.style.cssText =
    "position:fixed;left:0;top:0;width:0;visibility:hidden;pointer-events:none;height:env(" +
    property +
    ", 0px)";
  document.body.append(probe);
  const height = probe.getBoundingClientRect().height;
  probe.remove();
  return height;
}

/* iOS 26 draws a glass bar over an installed web app and reports a zero top
   inset while it does. A measured zero on an installed iOS app therefore
   means "reserve the space here", not "there is no bar". The bar on the phone
   that reported this was about 110 px tall; the settings sheet has a control
   for the last few pixels. */
const IOS_BAR_TOP = 118;
const IOS_BAR_BOTTOM = 34;
const INSET_KEY = "deck.topExtra";

let topExtra = storedTopExtra();

function storedTopExtra() {
  try {
    const raw = Number(localStorage.getItem(INSET_KEY));
    return Number.isFinite(raw) ? raw : 0;
  } catch {
    return 0;
  }
}

function storeTopExtra(value) {
  try {
    localStorage.setItem(INSET_KEY, String(value));
  } catch {
    /* the nudge lasts this session only */
  }
}

function applySafeArea() {
  const installedIOS = isStandalone() && isIOS();
  const measuredTop = measuredInset("safe-area-inset-top");
  const base = measuredTop || (installedIOS ? IOS_BAR_TOP : 0);
  const top = Math.max(0, base + (installedIOS ? topExtra : 0));
  const bottom =
    measuredInset("safe-area-inset-bottom") || (installedIOS ? IOS_BAR_BOTTOM : 0);
  document.documentElement.style.setProperty("--safe-top", top + "px");
  document.documentElement.style.setProperty("--safe-bottom", bottom + "px");
  if (el.insetRow) el.insetRow.hidden = !installedIOS;
  if (el.insetValue) el.insetValue.textContent = Math.round(top) + " px";
}

function nudgeInset(step) {
  topExtra += step;
  storeTopExtra(topExtra);
  applySafeArea();
}

// --------------------------------------------------------------------------
// Debug readout (?debug)
// --------------------------------------------------------------------------

function debugEnabled() {
  return location.search.includes("debug");
}

async function pushLog() {
  if (!("caches" in window)) return null;
  try {
    const cache = await caches.open("deck-push-log");
    const hit = await cache.match("/log");
    if (!hit) return null;
    const data = await hit.json();
    return data && typeof data === "object" ? data : null;
  } catch {
    return null;
  }
}

async function debugReport() {
  const root = getComputedStyle(document.documentElement);
  const lines = [
    "standalone: " + isStandalone(),
    "ios: " + isIOS(),
    "env top: " + measuredInset("safe-area-inset-top") + "px",
    "env bottom: " + measuredInset("safe-area-inset-bottom") + "px",
    "safe-top: " + root.getPropertyValue("--safe-top").trim(),
    "safe-bottom: " + root.getPropertyValue("--safe-bottom").trim(),
    "innerHeight: " + window.innerHeight,
    "visualViewport: " +
      (window.visualViewport ? Math.round(window.visualViewport.height) : "-"),
    "body: " + document.body.clientHeight,
    "screen: " + window.screen.width + "x" + window.screen.height,
    "dpr: " + window.devicePixelRatio,
    "theme: " + document.documentElement.dataset.theme,
    "permission: " + (window.Notification ? Notification.permission : "none"),
  ];
  try {
    const registration = await navigator.serviceWorker.ready;
    const subscription = await registration.pushManager.getSubscription();
    lines.push(
      "push endpoint: " + (subscription ? "…" + subscription.endpoint.slice(-12) : "none")
    );
  } catch (err) {
    lines.push("push endpoint: " + String(err.message || err));
  }
  const log = await pushLog();
  lines.push(
    "pushes received: " +
      (log ? log.count + " (last " + new Date(log.at).toLocaleTimeString() + ")" : "none")
  );
  if (log && log.last) lines.push("last payload: " + JSON.stringify(log.last));
  return lines.join("\n");
}

let debugTimer = 0;

async function toggleDebug() {
  const shown = document.querySelector(".debug");
  if (shown) {
    shown.remove();
    clearInterval(debugTimer);
    return;
  }
  const box = node("pre", "debug", await debugReport());
  document.body.append(box);
  clearInterval(debugTimer);
  debugTimer = setInterval(async () => {
    const live = document.querySelector(".debug");
    if (live) live.textContent = await debugReport();
  }, 2000);
}

// --------------------------------------------------------------------------
// Rendering
// --------------------------------------------------------------------------

function render() {
  const board = state.board;
  if (!board) return;
  el.stamp.textContent = board.loading ? "loading…" : stampText(board);
  renderError(board);
  renderHomes(board);
  renderColumns(board);
}

function stampText(board) {
  const age = Math.max(0, Math.round(Date.now() / 1000 - board.collected_at));
  return age <= 1 ? "now" : age + "s ago";
}

function renderError(board) {
  el.error.hidden = !board.error;
  el.error.textContent = board.error || "";
}

function renderHomes(board) {
  el.homes.replaceChildren();
  const active = board.active_home || state.home;
  for (const home of board.homes) {
    const tab = node("button", "home");
    tab.type = "button";
    tab.dataset.active = String(home.label === active);
    tab.append(node("span", "home-label", home.label));
    if (home.count) tab.append(node("span", "home-count", String(home.count)));
    if (home.activity === "working") tab.append(node("span", "dot working", "●"));
    else if (home.activity === "blocked") tab.append(node("span", "dot blocked", "●"));
    else if (home.offline_since) tab.append(node("span", "dot offline", "●"));
    tab.addEventListener("click", () => selectHome(home.label));
    el.homes.append(tab);
  }
}

function selectHome(label) {
  state.home = label;
  if (state.socket && state.socket.readyState === WebSocket.OPEN) {
    state.socket.send(JSON.stringify({ type: "home", label }));
  } else {
    load();
  }
}

function cardSignature(card) {
  return [
    card.id,
    card.title,
    card.badge,
    card.badge_color,
    card.doing,
    card.owner,
    card.agent,
    card.model,
    card.effort,
    card.run_elapsed,
    card.run_tokens,
    card.status_text,
    card.live_status,
  ].join("\u0001");
}

function ensureSection(column) {
  const known = state.sections.get(column.id);
  if (known) return known;
  const section = node("section", "column");
  section.dataset.column = column.id;
  const head = node("button", "column-head");
  head.type = "button";
  const note = node("span", "column-note");
  note.hidden = true;
  const count = node("span", "column-count", "0");
  head.append(node("span", "column-title", column.title), note, count);
  const list = node("div", "cards");
  const entry = {
    id: column.id,
    title: column.title,
    section,
    head,
    note,
    count,
    list,
    sig: "",
    collapsed: column.id === "landed",
  };
  if (entry.collapsed) section.classList.add("collapsed");
  head.addEventListener("click", () => {
    entry.collapsed = !entry.collapsed;
    section.classList.toggle("collapsed", entry.collapsed);
  });
  section.append(head, list);
  state.sections.set(column.id, entry);
  return entry;
}

function updateColumn(entry, column) {
  entry.title = column.title;
  const cards = visibleCards(column);
  const hidden = (column.cards || []).length - cards.length;
  entry.count.textContent = String(cards.length);
  entry.note.textContent = hidden ? hidden + " answered" : "";
  entry.note.hidden = !hidden;
  // The switch changes what the column holds, so it belongs in the signature.
  const signature =
    String(state.showAnswered) +
    "\u0002" +
    column.title +
    "\u0002" +
    cards.map(cardSignature).join("\u0001");
  if (signature === entry.sig) return;
  // Rebuilding a column must not move the reader: the page scroll is restored.
  const keepTop = entry.section.scrollTop;
  entry.list.replaceChildren();
  if (!cards.length) entry.list.append(node("p", "column-empty", "nothing here"));
  for (const card of cards) entry.list.append(cardNode(card, column.id));
  entry.sig = signature;
  if (keepTop) entry.section.scrollTop = keepTop;
}

/* An answered call stays in the data: the phone decides whether to show it,
   the same way the TUI's A key does. */
function visibleCards(column) {
  const cards = column.cards || [];
  if (state.showAnswered) return cards;
  return cards.filter((card) => !card.answered_hidden);
}

function storedShowAnswered() {
  try {
    return localStorage.getItem(SHOW_ANSWERED_KEY) === "1";
  } catch {
    return false;
  }
}

function applyShowAnswered(value) {
  state.showAnswered = Boolean(value);
  try {
    localStorage.setItem(SHOW_ANSWERED_KEY, state.showAnswered ? "1" : "0");
  } catch {
    /* the choice lasts this session only */
  }
  if (el.showAnsweredOn) el.showAnsweredOn.dataset.selected = String(state.showAnswered);
  if (el.showAnsweredOff) {
    el.showAnsweredOff.dataset.selected = String(!state.showAnswered);
  }
  // Every column keeps its signature, so the switch has to force a rebuild.
  for (const entry of state.sections.values()) entry.sig = "";
  render();
}

function renderColumns(board) {
  const wanted = [];
  for (const column of board.columns) {
    if (ACTIVE_COLUMNS.includes(column.id)) wanted.push(column);
  }
  const others = board.others || [];
  if (others.length) wanted.push({ id: "others", title: "Other Agents", cards: others });

  if (!wanted.length) return;
  if (!state.sections.size) el.board.replaceChildren();

  for (const column of wanted) updateColumn(ensureSection(column), column);

  for (const [id, entry] of [...state.sections]) {
    if (!wanted.some((column) => column.id === id)) {
      entry.section.remove();
      state.sections.delete(id);
    }
  }

  state.order = wanted.map((column) => column.id);
  state.order.forEach((id, index) => {
    const entry = state.sections.get(id);
    if (el.board.children[index] !== entry.section) {
      el.board.insertBefore(entry.section, el.board.children[index] || null);
    }
  });

  renderDots();
  updateActivePage();
}

// --------------------------------------------------------------------------
// Phone pager: one column per screen
// --------------------------------------------------------------------------

function renderDots() {
  if (el.dots.children.length !== state.order.length) {
    el.dots.replaceChildren();
    for (let index = 0; index < state.order.length; index += 1) {
      const dot = node("button", "page-dot");
      dot.type = "button";
      dot.addEventListener("click", () => scrollToPage(index));
      el.dots.append(dot);
    }
  }
  [...el.dots.children].forEach((dot, index) => {
    const entry = state.sections.get(state.order[index]);
    const title = entry ? entry.title : "";
    dot.dataset.active = String(index === state.page);
    dot.title = title;
    dot.setAttribute("aria-label", "Show " + (title || "column " + (index + 1)));
  });
}

function updateActivePage() {
  const width = el.board.clientWidth;
  if (!width || !state.order.length) return;
  const index = Math.max(
    0,
    Math.min(state.order.length - 1, Math.round(el.board.scrollLeft / width))
  );
  if (index === state.page && el.dots.children.length === state.order.length) return;
  state.page = index;
  renderDots();
}

function scrollToPage(index) {
  const entry = state.sections.get(state.order[index]);
  if (!entry) return;
  entry.section.scrollIntoView({ behavior: "smooth", inline: "start", block: "nearest" });
  state.page = index;
  renderDots();
}

function cardNode(card, columnId) {
  const answerable = columnId === "captains_call";
  const item = document.createElement(answerable ? "button" : "div");
  item.className = "card";
  if (answerable) {
    item.type = "button";
    item.classList.add("answerable");
  }

  const top = node("div", "card-top");
  top.append(node("span", "card-title", card.title || card.task));
  if (card.badge) {
    const badge = node("span", "badge", card.badge);
    const color = BADGE_COLORS[Number(card.badge_color)];
    if (color) badge.style.color = color;
    top.append(badge);
  }
  item.append(top);

  if (card.answered_hidden) {
    item.append(node("p", "card-answered", "✓ answered, waiting for its owner"));
  }

  // A card whose status repeats its doing line must not show the paragraph
  // twice. The doing text is truncated with an ellipsis, so the comparison
  // uses the head of it.
  function repeatsDoing(text) {
    if (!card.doing || !text) return false;
    const head = card.doing.replace(/\u2026$/, "").trim();
    return head.length > 12 && (text.startsWith(head) || card.doing.startsWith(text));
  }

  const statusRepeats = repeatsDoing(card.status_text);
  const doingText =
    statusRepeats && card.status_text.length > card.doing.length ? card.status_text : card.doing;
  if (doingText) item.append(node("p", "card-doing", doingText));

  const meta = [];
  if (card.owner && card.owner !== "(main)") meta.push(card.owner);
  const agent = [card.agent, card.model, card.effort].filter(Boolean).join(" ");
  if (agent) meta.push(agent);
  if (card.run_elapsed) {
    meta.push(
      typeof card.run_elapsed === "number" ? fmtElapsed(card.run_elapsed) : card.run_elapsed
    );
  }
  if (card.run_tokens) meta.push(card.run_tokens);
  if (card.status_text && !statusRepeats) meta.push(card.status_text);
  if (meta.length) item.append(node("p", "card-meta", meta.join(" · ")));

  if (answerable) {
    item.append(node("span", "card-cta", "answer →"));
    item.addEventListener("click", () => openTicket(card.task));
  }
  return item;
}

// --------------------------------------------------------------------------
// Ticket sheet
// --------------------------------------------------------------------------

async function openTicket(key) {
  state.openKey = key;
  state.choice = "";
  state.ticket = null;
  el.sheetState.textContent = "";
  el.sheetState.dataset.tone = "";
  el.sheet.hidden = false;
  el.sheet.classList.remove("closing");
  el.sheetTitle.textContent = "Loading…";
  el.sheetBadge.textContent = "";
  el.sheetRepo.textContent = "";
  el.sheetAbout.textContent = "";
  el.sheetDecide.textContent = "";
  el.sheetDetailBox.hidden = true;
  el.sheetOptions.replaceChildren();
  state.optionRows = [];
  el.sheetNote.value = "";
  el.sheetSubmit.disabled = true;
  try {
    const response = await fetch("/api/ticket/" + encodeURIComponent(key));
    if (!response.ok) throw new Error("ticket " + response.status);
    state.ticket = await response.json();
    drawTicket(state.ticket);
  } catch (err) {
    el.sheetTitle.textContent = "Cannot open this ticket";
    el.sheetState.textContent = String(err.message || err);
  }
}

function drawTicket(ticket) {
  el.sheetTitle.textContent = ticket.title || ticket.key;
  el.sheetBadge.textContent = ticket.type ? ticket.type.toUpperCase() : "";
  el.sheetRepo.textContent = ticket.repo || "";
  el.sheetAbout.textContent = ticket.about || "";
  el.sheetDecide.textContent = ticket.decide || "";
  if (ticket.detail) {
    el.sheetDetail.textContent = ticket.detail;
    el.sheetDetailBox.hidden = false;
  }
  el.sheetOptions.replaceChildren();
  state.optionRows = [];
  for (const option of ticket.options || []) {
    const row = node("button", "option");
    row.type = "button";
    row.dataset.value = option.value;
    row.dataset.recommended = String(option.value === ticket.recommend);
    row.append(node("span", "option-label", option.label));
    if (option.value === ticket.recommend) row.append(node("span", "rec", "recommended"));
    if (option.hint) row.append(node("span", "option-hint", option.hint));
    row.addEventListener("click", () => chooseOption(option.value));
    el.sheetOptions.append(row);
    state.optionRows.push(row);
  }
  el.sheetNote.placeholder = ticket.freeform_hint || "or answer in your own words…";
  el.sheetNote.disabled = ticket.freeform === false;
  if (ticket.recommend) chooseOption(ticket.recommend);
}

function chooseOption(value) {
  state.choice = state.choice === value ? "" : value;
  for (const row of state.optionRows) {
    row.dataset.selected = String(row.dataset.value === state.choice);
  }
  updateSubmit();
}

function updateSubmit() {
  if (!state.ticket) return;
  const note = el.sheetNote.value.trim();
  el.sheetSubmit.disabled = state.submitting || (!state.choice && !note);
}

async function submit() {
  const ticket = state.ticket;
  if (!ticket || state.submitting) return;
  state.submitting = true;
  el.sheetSubmit.disabled = true;
  // A running circle in the button reads as "in progress" better than a word.
  el.sheetSubmit.dataset.busy = "true";
  el.sheetState.dataset.tone = "";
  el.sheetState.textContent = "";
  try {
    const response = await fetch("/api/ticket/" + encodeURIComponent(ticket.key) + "/answer", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ value: state.choice, note: el.sheetNote.value }),
    });
    const result = await response.json();
    if (result.ok) {
      el.sheetState.textContent = "Recorded: " + (result.answer || "");
      el.sheetState.dataset.tone = "ok";
      // The sheet closes on its own, and the button stays busy until it does,
      // so a second tap cannot record a second answer.
      setTimeout(closeTicket, 1100);
      return;
    }
    el.sheetState.textContent = result.detail || result.error || "Refused";
    el.sheetState.dataset.tone = "bad";
  } catch (err) {
    el.sheetState.textContent = String(err.message || err);
    el.sheetState.dataset.tone = "bad";
  }
  state.submitting = false;
  el.sheetSubmit.dataset.busy = "false";
  updateSubmit();
}

function closeTicket() {
  // Dropping focus closes the keyboard, and with it any zoom iOS applied to
  // the field.
  if (el.sheetNote && document.activeElement === el.sheetNote) el.sheetNote.blur();
  state.openKey = "";
  state.ticket = null;
  state.submitting = false;
  el.sheetSubmit.dataset.busy = "false";
  // The panel leaves the way it arrived, and the sheet stops taking taps only
  // once it is out of sight.
  if (el.sheet.hidden) return;
  el.sheet.classList.add("closing");
  setTimeout(() => {
    el.sheet.hidden = true;
    el.sheet.classList.remove("closing");
    updateSubmit();
  }, 190);
}

// --------------------------------------------------------------------------
// Settings sheet
// --------------------------------------------------------------------------

function openSettings() {
  applyTheme(state.themeMode);
  refreshPushState();
  el.settings.hidden = false;
}

function closeSettings() {
  el.settings.hidden = true;
}

// --------------------------------------------------------------------------
// Notifications (Web Push)
// --------------------------------------------------------------------------

const PUSH = {
  supported: false,
  enabled: false,
  permission: "default",
  devices: 0,
  busy: false,
};

function pushSupported() {
  return "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;
}

function urlBase64ToUint8Array(base64) {
  const padded = (base64 + "=".repeat((4 - (base64.length % 4)) % 4))
    .replace(/-/g, "+")
    .replace(/_/g, "/");
  const raw = atob(padded);
  const bytes = new Uint8Array(raw.length);
  for (let index = 0; index < raw.length; index += 1) bytes[index] = raw.charCodeAt(index);
  return bytes;
}

function deviceLabel() {
  const match = navigator.userAgent.match(/\(([^)]+)\)/);
  return (match ? match[1] : "device").slice(0, 60);
}

function showButton(button, visible) {
  if (button) button.hidden = !visible;
}

function renderPush(message) {
  if (!el.pushState) return;
  const standalone = isStandalone();
  const usable = PUSH.supported && (standalone || !isIOS());
  if (message) {
    el.pushState.dataset.tone = message.tone || "";
    el.pushState.textContent = message.text;
  } else if (!PUSH.supported) {
    el.pushState.dataset.tone = "";
    el.pushState.textContent = "This browser cannot show notifications.";
  } else if (!standalone && isIOS()) {
    el.pushState.dataset.tone = "";
    el.pushState.textContent =
      "Add the deck to the Home Screen first. iOS sends notifications only to an installed web app.";
  } else if (PUSH.permission === "denied") {
    el.pushState.dataset.tone = "bad";
    el.pushState.textContent = "Notifications are blocked for this app in iOS Settings.";
  } else if (PUSH.enabled) {
    el.pushState.dataset.tone = "ok";
    el.pushState.textContent =
      PUSH.devices > 1
        ? "On, this device and " + (PUSH.devices - 1) + " more."
        : "On for this device.";
  } else {
    el.pushState.dataset.tone = "";
    el.pushState.textContent = "Off.";
  }
  // The two states carry the same mark the theme options use.
  if (el.pushOn) {
    el.pushOn.dataset.selected = String(PUSH.enabled);
    el.pushOn.disabled = !usable || PUSH.enabled || PUSH.permission === "denied";
  }
  if (el.pushOff) {
    el.pushOff.dataset.selected = String(!PUSH.enabled);
    el.pushOff.disabled = !usable || !PUSH.enabled;
  }
  showButton(el.pushTest, PUSH.enabled);
}

async function refreshPushState() {
  PUSH.supported = pushSupported();
  if (!PUSH.supported) {
    renderPush();
    return;
  }
  PUSH.permission = Notification.permission;
  try {
    const registration = await navigator.serviceWorker.ready;
    const subscription = await registration.pushManager.getSubscription();
    PUSH.enabled = Boolean(subscription);
    const response = await fetch("/api/push/status");
    const status = await response.json();
    PUSH.devices = status.devices || 0;
  } catch {
    PUSH.enabled = false;
  }
  renderPush();
}

/* The bridge has to hold the endpoint the app actually has. When the app is
   removed from the Home Screen and added again, iOS issues a new endpoint, and
   the old entry stays in the store answering 201 while the phone shows
   nothing. Registering on every load keeps the store honest and asks the
   captain for nothing. */
async function registerSubscription(silent) {
  if (!pushSupported() || Notification.permission !== "granted") return false;
  try {
    const registration = await navigator.serviceWorker.ready;
    const subscription = await registration.pushManager.getSubscription();
    if (!subscription) return false;
    const response = await fetch("/api/push/subscribe", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ subscription: subscription.toJSON(), device: deviceLabel() }),
    });
    if (!response.ok) throw new Error("the bridge refused the subscription");
    const result = await response.json();
    PUSH.enabled = true;
    PUSH.devices = result.devices || 1;
    return true;
  } catch (err) {
    if (!silent) renderPush({ text: String(err.message || err), tone: "bad" });
    return false;
  }
}

async function enablePush() {
  if (PUSH.busy || !el.pushOn) return;
  PUSH.busy = true;
  renderPush({ text: "Asking iOS…" });
  try {
    // The prompt must answer the tap, so it is the first thing this handler awaits.
    const permission = await Notification.requestPermission();
    PUSH.permission = permission;
    if (permission !== "granted") {
      renderPush();
      return;
    }
    const registration = await navigator.serviceWorker.ready;
    if (!(await registration.pushManager.getSubscription())) {
      const keyResponse = await fetch("/api/push/key");
      const key = await keyResponse.json();
      await registration.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: urlBase64ToUint8Array(key.public_key),
      });
    }
    if (!(await registerSubscription(false))) {
      throw new Error("the bridge did not accept the subscription");
    }
    renderPush();
  } catch (err) {
    renderPush({ text: String(err.message || err), tone: "bad" });
  } finally {
    PUSH.busy = false;
  }
}

async function disablePush() {
  if (PUSH.busy) return;
  PUSH.busy = true;
  try {
    const registration = await navigator.serviceWorker.ready;
    const subscription = await registration.pushManager.getSubscription();
    if (subscription) {
      await fetch("/api/push/unsubscribe", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ endpoint: subscription.endpoint }),
      });
      await subscription.unsubscribe();
    }
    PUSH.enabled = false;
  } catch (err) {
    renderPush({ text: String(err.message || err), tone: "bad" });
  } finally {
    PUSH.busy = false;
  }
  await refreshPushState();
}

async function testPush() {
  renderPush({ text: "Sending…" });
  try {
    const response = await fetch("/api/push/test", { method: "POST" });
    const result = await response.json();
    renderPush(
      result.sent
        ? { text: "Sent to " + result.sent + " device(s).", tone: "ok" }
        : { text: "The bridge has no device to send to.", tone: "bad" }
    );
  } catch (err) {
    renderPush({ text: String(err.message || err), tone: "bad" });
  }
}

function queryParam(name) {
  const match = location.search.match(new RegExp("[?&]" + name + "=([^&]*)"));
  return match ? decodeURIComponent(match[1]) : "";
}

// --------------------------------------------------------------------------
// Data
// --------------------------------------------------------------------------

async function load() {
  try {
    const query = state.home ? "?home=" + encodeURIComponent(state.home) : "";
    const response = await fetch("/api/board" + query);
    state.board = await response.json();
    render();
  } catch {
    setConn("down", "Cannot reach the bridge");
  }
}

function connect() {
  const scheme = location.protocol === "https:" ? "wss:" : "ws:";
  const socket = new WebSocket(scheme + "//" + location.host + "/ws");
  state.socket = socket;
  setConn("idle", "Connecting…");

  socket.addEventListener("open", () => {
    state.retry = 0;
    setConn("live", "Live");
    if (state.home) socket.send(JSON.stringify({ type: "home", label: state.home }));
  });
  socket.addEventListener("message", (event) => {
    let data;
    try {
      data = JSON.parse(event.data);
    } catch {
      return; // a frame the bridge did not write: ignoring it beats crashing the board
    }
    if (data.type === "board") {
      state.board = data.board;
      render();
    } else if (data.type === "ticket" && data.key === state.openKey && data.ticket) {
      state.ticket = data.ticket;
      drawTicket(data.ticket);
    }
  });
  socket.addEventListener("close", () => {
    setConn("down", "Disconnected, retrying");
    const wait = Math.min(15000, 1000 * 2 ** state.retry++);
    setTimeout(connect, wait);
  });
  socket.addEventListener("error", () => setConn("down", "Connection error"));
}

el.refresh.addEventListener("click", () => {
  if (state.socket && state.socket.readyState === WebSocket.OPEN) {
    state.socket.send(JSON.stringify({ type: "refresh" }));
    el.stamp.textContent = "refreshing…";
  } else {
    load();
  }
});
el.board.addEventListener(
  "scroll",
  () => {
    if (state.raf) return;
    state.raf = requestAnimationFrame(() => {
      state.raf = 0;
      updateActivePage();
    });
  },
  { passive: true }
);
window.addEventListener("resize", updateActivePage);
window.addEventListener("resize", applySafeArea);
window.addEventListener("orientationchange", applySafeArea);
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) applySafeArea();
});
el.sheetClose.addEventListener("click", closeTicket);
el.sheet.addEventListener("click", (event) => {
  if (event.target === el.sheet) closeTicket();
});
el.settingsButton.addEventListener("click", openSettings);
el.settingsClose.addEventListener("click", closeSettings);
el.settings.addEventListener("click", (event) => {
  if (event.target === el.settings) closeSettings();
});
if (el.debugButton) el.debugButton.addEventListener("click", toggleDebug);
if (el.pushOn) el.pushOn.addEventListener("click", enablePush);
if (el.pushTest) el.pushTest.addEventListener("click", testPush);
if (el.pushOff) el.pushOff.addEventListener("click", disablePush);
if (el.insetPlus) el.insetPlus.addEventListener("click", () => nudgeInset(6));
if (el.insetMinus) el.insetMinus.addEventListener("click", () => nudgeInset(-6));
if (el.showAnsweredOn) {
  el.showAnsweredOn.addEventListener("click", () => applyShowAnswered(true));
}
if (el.showAnsweredOff) {
  el.showAnsweredOff.addEventListener("click", () => applyShowAnswered(false));
}
for (const button of document.querySelectorAll("[data-theme-mode]")) {
  button.addEventListener("click", () => {
    storeTheme(button.dataset.themeMode);
    applyTheme(button.dataset.themeMode);
  });
}
el.sheetNote.addEventListener("input", updateSubmit);
el.sheetSubmit.addEventListener("click", submit);
document.addEventListener("keydown", (event) => {
  if (event.key !== "Escape") return;
  if (!el.sheet.hidden) closeTicket();
  else if (!el.settings.hidden) closeSettings();
});

window.matchMedia("(prefers-color-scheme: light)").addEventListener("change", () => {
  if (state.themeMode === "auto") applyTheme("auto");
});

if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js").catch((err) => {
      console.error("service worker not registered:", err);
    });
  });
}

applyTheme(storedTheme());
applyShowAnswered(storedShowAnswered());
applySafeArea();
// iOS can report the insets only after the first frames settle.
setTimeout(applySafeArea, 400);
setTimeout(applySafeArea, 1600);
if (debugEnabled()) toggleDebug();
load().then(connect);
// Silent: a device that already agreed keeps the bridge pointed at itself.
registerSubscription(true);
const deepTicket = queryParam("ticket");
if (deepTicket) openTicket(deepTicket);
setInterval(() => {
  if (state.board) el.stamp.textContent = state.board.loading ? "loading…" : stampText(state.board);
}, 5000);
