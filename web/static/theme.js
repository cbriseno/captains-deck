/* Origin-scoped visual preference; independent of the server language. */
'use strict';
(() => {
  const system = matchMedia('(prefers-color-scheme: dark)');
  const choices = new Set(['light', 'dark', 'system']);
  let preference = 'system';
  try { const saved = localStorage.getItem('deck-theme'); if (choices.has(saved)) preference = saved; } catch { /* Storage may be disabled. */ }
  const apply = () => {
    const dark = preference === 'dark' || (preference === 'system' && system.matches);
    document.documentElement.dataset.theme = dark ? 'dark' : 'light';
    document.querySelector('meta[name="theme-color"]')?.setAttribute('content', dark ? '#121a29' : '#f4f6fa');
  };
  apply();
  system.addEventListener('change', apply);
  document.addEventListener('DOMContentLoaded', () => {
    const select = document.getElementById('theme');
    select.value = preference;
    select.addEventListener('change', () => {
      preference = choices.has(select.value) ? select.value : 'system';
      try { localStorage.setItem('deck-theme', preference); } catch { /* In-memory selection still works. */ }
      apply();
    });
  });
})();
