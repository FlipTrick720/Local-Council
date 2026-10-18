'use strict';

// Colour theme toggle: Auto (follows the system) → Hell → Dunkel. Stored per device in the browser.
// theme-init.js applies it before the first paint; this keeps it up to date.

const THEME_KEY = 'local-council.theme';
const THEMES = { auto: ['◐', 'Auto'], light: ['☀', 'Hell'], dark: ['☾', 'Dunkel'] };
const systemDark = matchMedia('(prefers-color-scheme: dark)');

function themeChoice() {
  try { return localStorage.getItem(THEME_KEY) || 'auto'; } catch { return 'auto'; }
}

function applyTheme() {
  const choice = themeChoice();
  const dark = choice === 'dark' || (choice === 'auto' && systemDark.matches);
  document.documentElement.dataset.theme = dark ? 'dark' : 'light';
  const [icon, label] = THEMES[choice];
  $('theme').replaceChildren(icon, el('span', 'label', ` ${label}`));
  $('theme').title = `Farbschema: ${label} (klicken zum Wechseln: Auto / Hell / Dunkel)`;
}

$('theme').addEventListener('click', () => {
  const order = Object.keys(THEMES);
  const next = order[(order.indexOf(themeChoice()) + 1) % order.length];
  try { localStorage.setItem(THEME_KEY, next); } catch { /* private mode: only for this page view */ }
  applyTheme();
});
systemDark.addEventListener('change', applyTheme);
applyTheme();
