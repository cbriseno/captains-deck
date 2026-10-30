/* Measure the real board in headless Chrome at 1440, 1024, 390 and 360 px.
   Needs Node.js 22 (global WebSocket) and Chrome; skips without Chrome.
   Set CD_TEST_CHROME to choose the browser binary. */
const assert = require('node:assert/strict');
const {spawn} = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const test = require('node:test');

const chrome = [process.env.CD_TEST_CHROME, '/usr/bin/google-chrome', '/usr/bin/google-chrome-stable', '/usr/bin/chromium',
  '/usr/bin/chromium-browser', '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'].find(p => p && fs.existsSync(p));

function started(child, pattern) {
  return new Promise((resolve, reject) => {
    let text = '';
    const timer = setTimeout(() => reject(new Error('Timed out waiting for ' + pattern + '\n' + text)), 20000);
    const read = (chunk) => { text += chunk; const m = text.match(pattern); if (m) { clearTimeout(timer); resolve(m[1]); } };
    child.stdout.on('data', read); child.stderr.on('data', read);
    child.on('exit', (code) => reject(new Error('Exited ' + code + '\n' + text)));
  });
}

async function devtools(target) {
  const socket = new WebSocket(target);
  await new Promise((resolve, reject) => { socket.onopen = resolve; socket.onerror = reject; });
  let id = 0; const waiting = new Map();
  socket.onmessage = (event) => { const m = JSON.parse(event.data); if (waiting.has(m.id)) { waiting.get(m.id)(m); waiting.delete(m.id); } };
  const send = (method, params = {}) => new Promise((resolve, reject) => {
    waiting.set(++id, (m) => m.error ? reject(new Error(method + ': ' + m.error.message)) : resolve(m.result));
    socket.send(JSON.stringify({id, method, params}));
  });
  const evaluate = async (expression) => {
    const result = await send('Runtime.evaluate', {expression, awaitPromise: true, returnByValue: true});
    if (result.exceptionDetails) throw new Error(result.exceptionDetails.exception?.description || result.exceptionDetails.text);
    return result.result.value;
  };
  return {send, evaluate, close: () => socket.close()};
}

// Runs inside the page: no text in a card escapes its box or is cut.
const measure = `(async () => {
  const wait = (ms) => new Promise(r => setTimeout(r, ms));
  for (let i = 0; i < 100 && !document.querySelector('.task-card'); i++) await wait(100);
  await wait(300);
  const shown = (n) => n.getClientRects().length > 0;
  const cards = [...document.querySelectorAll('.task-card')].filter(shown);
  const problems = [];
  for (const card of cards) {
    const box = card.getBoundingClientRect();
    for (const n of card.querySelectorAll('.card-open, .card-open *')) {
      const cs = getComputedStyle(n), r = n.getBoundingClientRect();
      if (n.scrollWidth > n.clientWidth + 1 && n.clientWidth) problems.push('scrolls: ' + n.className + ' ' + n.textContent.slice(0, 30));
      if (r.width && (r.right > box.right + 0.5 || r.left < box.left - 0.5)) problems.push('outside: ' + n.className + ' ' + n.textContent.slice(0, 30));
      if (cs.textOverflow === 'ellipsis' || (cs.webkitLineClamp && cs.webkitLineClamp !== 'none')) problems.push('clamped: ' + n.className);
      if (n.matches('.t') && n.scrollHeight > n.clientHeight + 1) problems.push('title hidden: ' + n.textContent.slice(0, 30));
    }
    if (/…/.test(card.querySelector('.card-open').textContent)) problems.push('ellipsis: ' + card.dataset.key);
  }
  const rects = () => cards.map(c => { const r = c.getBoundingClientRect(); return [r.left, r.top + scrollY, r.width, r.height].map(Math.round).join(','); }).join('|');
  const visible = [...document.querySelectorAll('.column')].filter(shown);
  const columns = visible.map(c => Math.round(c.getBoundingClientRect().width));
  const across = new Set(visible.map(c => Math.round(c.getBoundingClientRect().left))).size;
  return {problems, cards: cards.length, columns, across, firstTop: Math.round(cards[0].getBoundingClientRect().top + scrollY),
    pageOverflow: document.documentElement.scrollWidth > innerWidth, rects: rects(),
    order: [...document.querySelectorAll('.column')].map(c => c.dataset.kind), tabs: [...document.querySelectorAll('#columns-nav button')].map(b => b.dataset.kind),
    active: document.querySelector('#columns-nav button[aria-pressed=true]')?.dataset.kind, later: laterLayout()};
  // Later sits under the cards ready to start, inside Up next, and its heading fits.
  function laterLayout() {
    const up = document.querySelector('.column[data-kind=charted]'), block = up?.querySelector('.later');
    if (!block) return null;
    if (matchMedia('(max-width: 639px)').matches) document.querySelector('#columns-nav button[data-kind=charted]').click();
    const ready = [...up.querySelector('.cards').children].map(n => n.getBoundingClientRect().bottom);
    const heading = block.querySelector('.later-heading');
    return {cards: block.querySelectorAll('.task-card').length, below: block.getBoundingClientRect().top >= Math.max(0, ...ready),
      inside: block.getBoundingClientRect().right <= up.getBoundingClientRect().right + 0.5, fits: heading.scrollWidth <= heading.clientWidth + 1};
  }
})()`;

// Opening or closing any detail moves no card on a wide screen.
const steady = `(async () => {
  const wait = (ms) => new Promise(r => setTimeout(r, ms));
  const rects = () => [...document.querySelectorAll('.task-card')].map(c => { const r = c.getBoundingClientRect(); return [r.left, r.top + scrollY, r.width, r.height].map(Math.round).join(','); }).join('|');
  const before = rects(), moved = [];
  const kinds = ['captains_call', 'underway', 'charted', 'landed'].map(kind => '.column[data-kind=' + kind + '] .card-open');
  for (const selector of [...kinds, '.column[data-kind=charted] .later .card-open']) {
    document.querySelector(selector).click(); await wait(900);
    if (rects() !== before) moved.push('open ' + selector);
  }
  document.querySelector('.column-heading').click(); await wait(300);
  if (rects() !== before) moved.push('close');
  return {moved, open: document.getElementById('detail').dataset.open, dialogs: document.querySelectorAll('dialog').length};
})()`;

// On a phone a call opens inside its card with the recommended choice in view.
const phoneCall = `(async () => {
  const wait = (ms) => new Promise(r => setTimeout(r, ms));
  document.querySelector('#columns-nav button[data-kind=captains_call]').click(); await wait(200);
  const keys = () => [...document.querySelectorAll('.column[data-kind=captains_call] .task-card')].map(c => c.dataset.key).join(',');
  const before = keys();
  const card = document.querySelector('.column[data-kind=captains_call] .task-card');
  card.querySelector('.card-open').click(); await wait(1500);
  const rec = card.querySelector('.recommended-choice')?.getBoundingClientRect();
  return {inCard: card.contains(document.getElementById('detail')), recBottom: rec && Math.round(rec.bottom), height: innerHeight,
    sameOrder: keys() === before, texts: [...card.querySelectorAll('.t, .question, .card-line')].filter(n => n.getClientRects().length).map(n => n.textContent.trim())};
})()`;

// History: one line per delivery, newest first, nothing cut, no web address.
const history = `(async () => {
  const wait = (ms) => new Promise(r => setTimeout(r, ms));
  for (let i = 0; i < 100 && !document.querySelector('.task-card'); i++) await wait(100);
  document.getElementById('history-view').click();
  for (let i = 0; i < 100 && !document.querySelector('#history-results .task-card'); i++) await wait(100);
  await wait(300);
  const rows = [...document.querySelectorAll('#history-results .task-card')], problems = [];
  for (const row of rows) {
    const box = row.getBoundingClientRect();
    for (const n of row.querySelectorAll('.card-open, .card-open *')) {
      const r = n.getBoundingClientRect();
      if (n.scrollWidth > n.clientWidth + 1 && n.clientWidth) problems.push('scrolls: ' + n.textContent.slice(0, 30));
      if (r.width && (r.right > box.right + 0.5 || r.left < box.left - 0.5)) problems.push('outside: ' + n.textContent.slice(0, 30));
      if (getComputedStyle(n).textOverflow === 'ellipsis') problems.push('clamped: ' + n.className);
      if (n.matches('.t') && n.scrollHeight > n.clientHeight + 1) problems.push('title hidden: ' + n.textContent.slice(0, 30));
    }
  }
  // Newest first by the time each row shows; a day without a time follows
  // that day's timed rows, and rows without any time come last.
  const key = (event) => {
    if (typeof event?.epoch === 'number') { const d = new Date(event.epoch * 1000); return [1, d.getFullYear() * 10000 + (d.getMonth() + 1) * 100 + d.getDate(), 1, event.epoch]; }
    if (event?.date) return [1, Number(event.date.replaceAll('-', '')), 0, 0];
    return [0, 0, 0, 0];
  };
  const keys = rows.map(r => key(JSON.parse(r.querySelector('.card-ago').dataset.event)));
  const later = (a, b) => { for (let i = 0; i < 4; i++) if (a[i] !== b[i]) return a[i] > b[i]; return false; };
  const disorder = keys.findIndex((k, i) => i && later(k, keys[i - 1]));
  const face = rows[0].querySelector('.card-open'); face.click(); await wait(900);
  return {rows: rows.length, height: Math.round(document.getElementById('history-results').getBoundingClientRect().height), problems, disorder,
    urls: rows.filter(r => /https?:\\/\\//.test(r.textContent)).length, pageOverflow: document.documentElement.scrollWidth > innerWidth,
    open: document.getElementById('detail').dataset.open, links: [...document.querySelectorAll('#detail-content a.report-link')].length};
})()`;

test('the board and history fit, never cut text and keep cards still at 1440, 1024, 390 and 360 px', {skip: !chrome && 'no Chrome found; set CD_TEST_CHROME'}, async (t) => {
  const scratch = fs.mkdtempSync(path.join(os.tmpdir(), 'deck-layout-'));
  const server = spawn('python3', [path.join(__dirname, 'layout_fixture.py'), scratch, 'es'], {stdio: ['ignore', 'pipe', 'pipe']});
  const browser = spawn(chrome, ['--headless=new', '--remote-debugging-port=0', '--user-data-dir=' + path.join(scratch, 'chrome'),
    '--no-first-run', '--no-default-browser-check', '--disable-gpu', '--hide-scrollbars', 'about:blank'], {stdio: ['ignore', 'pipe', 'pipe']});
  process.once('exit', () => { server.kill(); browser.kill(); });
  const url = await started(server, /URL (\S+)/);
  const endpoint = await started(browser, /DevTools listening on (ws:\/\/\S+)/);
  const pages = await (await fetch(endpoint.replace(/^ws:\/\/([^/]+).*/, 'http://$1/json/list'))).json();
  const page = await devtools(pages.find(p => p.type === 'page').webSocketDebuggerUrl);
  // Close Chrome itself so its profile is flushed before the scratch folder goes.
  t.after(async () => { page.close(); const root = await devtools(endpoint); await root.send('Browser.close').catch(() => {}); root.close(); });
  const gone = (child) => child.exitCode !== null || child.signalCode ? Promise.resolve() : new Promise(r => child.once('exit', r));
  t.after(async () => {
    server.kill(); await Promise.race([gone(browser), new Promise(r => setTimeout(r, 5000))]); browser.kill();
    await Promise.all([gone(server), gone(browser)]);
    fs.rmSync(scratch, {recursive: true, force: true, maxRetries: 10, retryDelay: 200});
  });
  const order = ['captains_call', 'underway', 'charted', 'landed'];
  for (const [width, height] of [[1440, 900], [1024, 800], [390, 844], [360, 740]]) {
    await page.send('Emulation.setDeviceMetricsOverride', {width, height, deviceScaleFactor: 1, mobile: width < 640});
    await page.send('Page.navigate', {url});
    await new Promise(r => setTimeout(r, 300));
    const m = await page.evaluate(measure);
    assert.deepEqual(m.problems, [], width + ' px');
    assert.equal(m.pageOverflow, false, width + ' px');
    assert.deepEqual(m.order, order); assert.deepEqual(m.tabs, order);
    assert.ok(m.firstTop <= (width < 640 ? 200 : 120), `${width} px: first card at ${m.firstTop}`);
    assert.deepEqual(m.later, {cards: 2, below: true, inside: true, fits: true}, width + ' px');
    if (width >= 900) {
      assert.ok(m.columns.every(w => w >= 250), `${width} px: columns ${m.columns}`);
      // Four across only when each keeps about 250 px beside the panel.
      assert.equal(m.across, width >= 1440 ? 4 : 2, `${width} px`);
      const s = await page.evaluate(steady);
      assert.deepEqual(s.moved, [], width + ' px'); assert.equal(s.open, 'false'); assert.equal(s.dialogs, 0);
    } else {
      // The fixture has questions waiting, so the phone opens on Needs you.
      assert.equal(m.active, 'captains_call', width + ' px');
      const call = await page.evaluate(phoneCall);
      assert.ok(call.inCard); assert.ok(call.sameOrder);
      assert.ok(call.recBottom <= call.height, `recommended ends at ${call.recBottom} of ${call.height}`);
      // The question shows once: as the title or as its own line, never both.
      assert.equal(new Set(call.texts).size, call.texts.length, call.texts.join(' | '));
    }
    await page.send('Page.navigate', {url});
    const h = await page.evaluate(history);
    assert.equal(h.rows, 87); assert.deepEqual(h.problems, [], width + ' px history');
    assert.equal(h.disorder, -1, width + ' px history order'); assert.equal(h.urls, 0); assert.equal(h.pageOverflow, false);
    assert.equal(h.open, 'true', width + ' px: a row opens its detail');
    // The captain's 87 deliveries took 8 400 px at 1440; narrower, long titles wrap.
    if (width >= 1440) assert.ok(h.height <= 2500, `${width} px: history is ${h.height} px tall`);
  }
});
