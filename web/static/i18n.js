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
    for (const attr of ['aria-label', 'placeholder', 'title']) {
      for (const el of document.querySelectorAll(`[data-i18n-${attr}]`)) el.setAttribute(attr, t(el.getAttribute(`data-i18n-${attr}`)));
    }
    const dateFmt = new Intl.DateTimeFormat(locale, {dateStyle: 'medium', timeStyle: 'short'});
    const dayFmt = new Intl.DateTimeFormat(locale, {dateStyle: 'medium', timeZone: 'UTC'});
    const relativeFmt = new Intl.RelativeTimeFormat(locale, {numeric: 'auto'});
    const relative = (epoch, now = Date.now() / 1000) => {
      const seconds = epoch - now;
      if (Math.abs(seconds) < 60) return t('now');
      const minutes = Math.round(seconds / 60);
      if (Math.abs(minutes) < 60) return relativeFmt.format(minutes, 'minute');
      const hours = Math.round(seconds / 3600);
      if (Math.abs(hours) < 24) return relativeFmt.format(hours, 'hour');
      return span(Math.round(seconds / 86400));
    };
    const span = (days) => {
      if (Math.abs(days) < 7) return relativeFmt.format(days, 'day');
      if (Math.abs(days) < 30) return relativeFmt.format(Math.round(days / 7), 'week');
      if (Math.abs(days) < 365) return relativeFmt.format(Math.round(days / 30.4375), 'month');
      return relativeFmt.format(Math.round(days / 365.25), 'year');
    };
    const calendarDay = (date) => {
      const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(date || '');
      if (!match) return null;
      const [year, month, day] = [+match[1], +match[2], +match[3]];
      const ms = Date.UTC(year, month - 1, day), check = new Date(ms);
      return check.getUTCFullYear() === year && check.getUTCMonth() === month - 1 && check.getUTCDate() === day ? ms : null;
    };
    const dateRelative = (day, now) => {
      const today = new Date(now * 1000);
      return span(Math.round((day - Date.UTC(today.getFullYear(), today.getMonth(), today.getDate())) / 86400000));
    };
    const eventTime = (event, now = Date.now() / 1000) => {
      if (!event) return t('Time not recorded');
      const label = t(event.label || 'Event');
      const day = calendarDay(event.date);
      if (day !== null) return `${label} · ${dateRelative(day, now)} · ${t('no time recorded')}`;
      if (typeof event.epoch === 'number' && Number.isFinite(event.epoch)) return `${label} · ${event.epoch > now + 5 ? t('future date; check the clock') : relative(event.epoch, now)}`;
      return `${label} · ${t(event.problem === 'not recorded' ? 'time not recorded' : 'time unavailable')}`;
    };
    const exactTime = (event) => {
      const day = calendarDay(event?.date);
      if (day !== null) return `${dayFmt.format(new Date(day))} · ${t('no time recorded')}`;
      if (typeof event?.epoch === 'number' && Number.isFinite(event.epoch)) return dateFmt.format(new Date(event.epoch * 1000));
      return t('Time not recorded');
    };
    // Card age, the captain's compact format: now · 5 min · 3 h · 2 d · 3 wk · +1 mo.
    // Units floor, so a card never looks newer than it is. A date-only event
    // counts calendar days; no recorded time, or one in the future, is a dash.
    const compactDays = (days) => days < 7 ? t('{count} d', {count: days}) : days < 30 ? t('{count} wk', {count: Math.floor(days / 7)}) : t('+1 mo');
    const ago = (event, now = Date.now() / 1000) => {
      const day = calendarDay(event?.date);
      if (day !== null) {
        const today = new Date(now * 1000);
        const days = Math.floor((Date.UTC(today.getFullYear(), today.getMonth(), today.getDate()) - day) / 86400000);
        return days < 0 ? '—' : days === 0 ? t('today') : compactDays(days);
      }
      if (typeof event?.epoch !== 'number' || !Number.isFinite(event.epoch)) return '—';
      const seconds = now - event.epoch;
      if (seconds < -5) return '—';
      if (seconds < 60) return t('now');
      if (seconds < 3600) return t('{count} min', {count: Math.floor(seconds / 60)});
      if (seconds < 86400) return t('{count} h', {count: Math.floor(seconds / 3600)});
      return compactDays(Math.floor(seconds / 86400));
    };
    // A return date on a card: "Oct 31"; the year only when it isn't this one.
    const shortDate = (value, now = Date.now()) => {
      const day = calendarDay(value), at = day !== null ? day : /^\d{4}-\d{2}-\d{2}T/.test(value || '') ? Date.parse(value) : NaN;
      if (!Number.isFinite(at)) return '';
      const zone = day !== null ? {timeZone: 'UTC'} : {};
      const sameYear = new Date(at).getFullYear() === new Date(now).getFullYear();
      return new Intl.DateTimeFormat(locale, {day: 'numeric', month: 'short', ...(sameYear ? {} : {year: 'numeric'}), ...zone}).format(new Date(at));
    };
    // An exported hold/pause end: a date-only day stays that calendar day.
    const untilText = (value) => {
      const day = calendarDay(value);
      if (day !== null) return dayFmt.format(new Date(day));
      const at = /^\d{4}-\d{2}-\d{2}T/.test(value || '') ? Date.parse(value) : NaN;
      return Number.isFinite(at) ? dateFmt.format(new Date(at)) : '';
    };
    return {t, locale, dateFmt, relative, eventTime, exactTime, untilText, ago, shortDate};
  }
};
DeckI18n.ready = DeckI18n.load();
