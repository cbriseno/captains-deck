/* Catalogs contain plain text. Task content and decision payloads stay intact. */
'use strict';
const DeckI18n = {
  async load() {
    let data = {locale: 'en', messages: {}};
    try {
      const response = await fetch('api/locale', {cache: 'no-store', credentials: 'same-origin'});
      if (response.ok) data = await response.json();
    } catch { /* The English document and message keys are the safe fallback. */ }
    let locale = data.locale || 'en';
    try {
      // Keep unsupported runtimes from silently selecting their OS locale.
      if (!Intl.DateTimeFormat.supportedLocalesOf([locale]).length ||
          !Intl.RelativeTimeFormat.supportedLocalesOf([locale]).length) locale = 'en';
    } catch { locale = 'en'; }
    const messages = data.messages || {};
    const t = (key, values = {}) => {
      const source = String(key ?? '');
      return (Object.hasOwn(messages, source) ? messages[source] : source)
        .replace(/\{(\w+)\}/g, (match, name) => Object.hasOwn(values, name) ? String(values[name]) : match);
    };
    document.documentElement.lang = locale;
    for (const el of document.querySelectorAll('[data-i18n]')) el.textContent = t(el.dataset.i18n);
    for (const attr of ['aria-label', 'placeholder']) {
      for (const el of document.querySelectorAll(`[data-i18n-${attr}]`)) el.setAttribute(attr, t(el.getAttribute(`data-i18n-${attr}`)));
    }
    const dateFmt = new Intl.DateTimeFormat(locale, {dateStyle: 'medium', timeStyle: 'short'});
    const dayFmt = new Intl.DateTimeFormat(locale, {dateStyle: 'medium', timeZone: 'UTC'});
    const relativeFmt = new Intl.RelativeTimeFormat(locale, {numeric: 'auto'});
    const relative = (epoch, now = Date.now() / 1000) => {
      const seconds = epoch - now;
      for (const [size, unit] of [[86400, 'day'], [3600, 'hour'], [60, 'minute']]) {
        if (Math.abs(seconds) >= size) return relativeFmt.format(Math.round(seconds / size), unit);
      }
      return t('now');
    };
    const eventTime = (event, now = Date.now() / 1000) => {
      if (!event) return t('Time not recorded');
      const label = t(event.label || 'Event');
      if (event.date) return `${label} · ${dayFmt.format(new Date(event.date + 'T00:00:00Z'))} · ${t('no time recorded')}`;
      if (typeof event.epoch === 'number') return `${label} · ${dateFmt.format(new Date(event.epoch * 1000))} · ${event.epoch > now + 5 ? t('future date; check the clock') : relative(event.epoch, now)}`;
      return `${label} · ${t(event.problem === 'not recorded' ? 'time not recorded' : 'time unavailable')}`;
    };
    return {t, locale, dateFmt, relative, eventTime};
  }
};
DeckI18n.ready = DeckI18n.load();
