'use strict';

// Applies the colour theme before the first paint, so dark mode does not flash white. Loaded in <head>;
// the toggle itself lives in theme.js.
(() => {
  let theme = 'auto';
  try { theme = localStorage.getItem('local-council.theme') || 'auto'; } catch { /* private mode */ }
  const dark = theme === 'dark' || (theme === 'auto' && matchMedia('(prefers-color-scheme: dark)').matches);
  document.documentElement.dataset.theme = dark ? 'dark' : 'light';
})();
