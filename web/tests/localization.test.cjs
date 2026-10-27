/* Execute the browser helpers against their DOM/storage interfaces. No packages. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '..');

async function locale(language, failed = false) {
  const messages = JSON.parse(fs.readFileSync(path.join(root, 'locales', language.split('-')[0] + '.json'), 'utf8'));
  const text = {dataset: {i18n: 'Board'}, textContent: ''};
  const attr = {setAttribute(key, value) {this[key] = value;}, getAttribute() {return 'Search board';}};
  const document = {documentElement: {}, querySelectorAll(selector) {
    return selector === '[data-i18n]' ? [text] : selector === '[data-i18n-aria-label]' ? [attr] : [];
  }};
  const context = vm.createContext({document, Intl, Date, fetch: async () => {
    if (failed) throw new Error('offline');
    return {ok: true, json: async () => ({locale: language, messages})};
  }});
  const code = fs.readFileSync(path.join(root, 'static/i18n.js'), 'utf8');
  vm.runInContext(code + '\nthis.ready = DeckI18n.ready;', context);
  return {api: await context.ready, document, text, attr};
}

function theme(saved, systemDark, storageFails = false) {
  const callbacks = {}, storage = new Map(saved ? [['deck-theme', saved]] : []);
  const select = {addEventListener(type, fn) {callbacks[type] = fn;}};
  const media = {matches: systemDark, addEventListener(type, fn) {callbacks.media = fn;}};
  const meta = {setAttribute(key, value) {this[key] = value;}};
  const document = {documentElement: {dataset: {}},
    querySelector() {return meta;}, getElementById() {return select;},
    addEventListener(type, fn) {callbacks[type] = fn;}};
  const localStorage = {getItem(key) {if (storageFails) throw new Error('denied'); return storage.get(key);},
    setItem(key, value) {if (storageFails) throw new Error('denied'); storage.set(key, value);}};
  vm.runInNewContext(fs.readFileSync(path.join(root, 'static/theme.js'), 'utf8'),
    {document, localStorage, matchMedia: () => media});
  callbacks.DOMContentLoaded();
  return {document, media, meta, select, storage, change(value) {select.value = value; callbacks.change();}, system(value) {media.matches = value; callbacks.media();}};
}

(async () => {
  const en = await locale('en-GB'), es = await locale('es-MX'), fallback = await locale('en', true);
  assert.equal(en.text.textContent, 'Board'); assert.equal(es.text.textContent, 'Tablero');
  assert.equal(es.attr['aria-label'], 'Buscar en el tablero');
  assert.equal(en.document.documentElement.lang, 'en-GB');
  assert.equal(es.document.documentElement.lang, 'es-MX');
  assert.equal(fallback.api.locale, 'en'); assert.equal(fallback.text.textContent, 'Board');
  assert.equal(es.api.t(undefined), '');
  const payload = '<script>{scope}</script> café';
  assert.equal(es.api.t('{label}: {path}\n{text}', {label: 'x', path: 'url', text: payload}), 'x: url\n' + payload);
  const now = Date.UTC(2026, 9, 26, 12) / 1000;
  assert.match(en.api.relative(now - 3600, now), /1 hour ago/);
  assert.match(es.api.relative(now - 3600, now), /hace 1 hora/);
  assert.equal(en.api.relative(now - 3599, now), '1 hour ago');
  assert.equal(en.api.relative(now - 86399, now), 'yesterday');
  assert.equal(en.api.relative(now - 1500, now), '25 minutes ago');
  assert.equal(en.api.relative(now - 59, now), 'now');
  assert.equal(en.api.relative(now + 3599, now), 'in 1 hour');
  assert.equal(en.api.relative(now - 1801, now), '30 minutes ago');
  assert.equal(en.api.relative(now + 1801, now), 'in 30 minutes');
  assert.equal(en.api.relative(now - 43201, now), '12 hours ago');
  assert.equal(en.api.relative(now + 43201, now), 'in 12 hours');
  assert.equal(en.api.relative(now - 86400, now), 'yesterday');
  assert.equal(en.api.relative(now + 86399, now), 'tomorrow');
  assert.equal(en.api.relative(now - 172800, now), '2 days ago');
  assert.equal(es.api.relative(now - 1801, now), 'hace 30 minutos');
  assert.equal(es.api.relative(now + 1801, now), 'dentro de 30 minutos');
  assert.equal(es.api.relative(now - 43201, now), 'hace 12 horas');
  assert.equal(es.api.relative(now + 43201, now), 'dentro de 12 horas');
  assert.equal(es.api.relative(now - 3599, now), 'hace 1 hora');
  assert.equal(es.api.relative(now - 86399, now), 'ayer');
  assert.equal(es.api.relative(now + 3599, now), 'dentro de 1 hora');
  for (const {api} of [en, es]) {
    const event = {label: 'Delivered', date: '2026-10-24', epoch: null};
    const before = JSON.stringify(event);
    const expected = new Intl.DateTimeFormat(api.locale, {dateStyle: 'medium', timeZone: 'UTC'}).format(new Date('2026-10-24T00:00:00Z'));
    assert.ok(api.exactTime(event).includes(expected));
    assert.ok(!api.eventTime(event, now).includes(expected));
    assert.ok(api.eventTime(event, now).includes(api.t('no time recorded')));
    assert.equal(JSON.stringify(event), before);
    const exact = api.eventTime({label: 'Updated', epoch: now - 3600}, now);
    assert.ok(!exact.includes(api.dateFmt.format(new Date((now - 3600) * 1000))));
    assert.ok(exact.includes(api.relative(now - 3600, now)));
    assert.equal(api.exactTime({epoch: now - 3600}), api.dateFmt.format(new Date((now - 3600) * 1000)));
    assert.ok(api.exactTime(event).includes(expected));
    assert.ok(api.exactTime(event).includes(api.t('no time recorded')));
    assert.notEqual(api.eventTime({label: 'Updated', epoch: now - 59}, now), api.eventTime({label: 'Updated', epoch: now - 59}, now + 61));
    assert.ok(!api.eventTime({epoch: NaN}, now).includes('NaN'));
    assert.ok(api.eventTime({label: 'Updated', epoch: now + 60}, now).includes(api.t('future date; check the clock')));
    assert.equal(api.eventTime(null), api.t('Time not recorded'));
  }
  assert.equal(en.api.relative(now - 14 * 86400, now), '2 weeks ago');
  assert.equal(es.api.relative(now - 14 * 86400, now), 'hace 2 semanas');
  assert.equal(en.api.relative(now - 90 * 86400, now), '3 months ago');
  assert.equal(en.api.relative(now - 730 * 86400, now), '2 years ago');
  const iso = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
  const base = new Date(2026, 0, 15, 12), clock = base.getTime() / 1000;
  const dayAgo = (n, from = base) => new Date(from.getFullYear(), from.getMonth(), from.getDate() - n, 12);
  assert.ok(en.api.eventTime({label: 'Updated', date: iso(base)}, clock).includes('today'));
  assert.ok(en.api.eventTime({label: 'Updated', date: iso(dayAgo(1))}, clock).includes('yesterday'));
  assert.ok(en.api.eventTime({label: 'Updated', date: iso(dayAgo(3))}, clock).includes('3 days ago'));
  assert.ok(en.api.eventTime({label: 'Updated', date: '2025-01-15'}, clock).includes('last year'));
  assert.ok(es.api.eventTime({label: 'Updated', date: iso(base)}, clock).includes('hoy'));
  const dst = new Date(2026, 2, 9, 12);
  assert.ok(en.api.eventTime({label: 'Updated', date: iso(dayAgo(1, dst))}, dst.getTime() / 1000).includes('yesterday'));
  const dateOnly = en.api.eventTime({label: 'Updated', date: iso(dayAgo(3))}, clock);
  assert.ok(!/minute|hour/.test(dateOnly));
  assert.ok(dateOnly.includes(en.api.t('no time recorded')));
  assert.ok(en.api.eventTime({label: 'Updated', date: 'not-a-date'}, clock).includes(en.api.t('time unavailable')));
  assert.ok(en.api.exactTime({date: '2026-01-10'}).includes('2026'));
  assert.ok(en.api.exactTime({date: 'not-a-date', epoch: clock}).includes(en.api.dateFmt.format(new Date(clock * 1000))));
  const a = theme(null, true), b = theme('light', true);
  assert.equal(a.document.documentElement.dataset.theme, 'dark');
  assert.equal(b.document.documentElement.dataset.theme, 'light');
  a.change('light'); a.system(true); assert.equal(a.document.documentElement.dataset.theme, 'light');
  a.change('dark'); a.system(false); assert.equal(a.document.documentElement.dataset.theme, 'dark');
  assert.equal(a.storage.get('deck-theme'), 'dark');
  assert.equal(theme(a.storage.get('deck-theme'), false).document.documentElement.dataset.theme, 'dark');
  a.change('system'); a.system(false); assert.equal(a.document.documentElement.dataset.theme, 'light');
  a.system(true); assert.equal(a.document.documentElement.dataset.theme, 'dark');
  assert.equal(a.meta.content, '#121a29');
  assert.equal(b.document.documentElement.dataset.theme, 'light');
  const denied = theme('light', true, true); denied.change('light');
  assert.equal(denied.document.documentElement.dataset.theme, 'light');
  assert.equal(theme('unrecognized', false).select.value, 'system');
  console.log('Localization, date-only/exact/relative dates, text safety and independent themes passed.');
})().catch(error => {console.error(error); process.exitCode = 1;});
