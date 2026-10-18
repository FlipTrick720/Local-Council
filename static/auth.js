'use strict';

// Optional password: login dialog, logout, and setting/removing the password under Einstellungen.
// Without a password (the default) nothing here shows up.

let authEnabled = false;

// Called first on start-up. Resolves only when logged in; otherwise the login dialog reloads the page.
async function ensureLogin() {
  let status = { enabled: false, logged_in: true };
  try {
    status = await (await fetch('/api/auth')).json();
  } catch { /* server unreachable: the normal start-up shows the error */ }
  authEnabled = status.enabled;
  $('logout').hidden = !authEnabled;
  renderPasswordState();
  if (!status.logged_in) {
    showLogin();
    await new Promise(() => {});
  }
}

function showLogin() {
  $('login').hidden = false;
  $('login-password').focus();
}

function renderPasswordState() {
  $('pw-state').textContent = authEnabled
    ? 'Die App ist mit einem Passwort geschützt.'
    : 'Kein Passwort gesetzt: Jeder im Heimnetz kann die App benutzen.';
  $('pw-remove').hidden = !authEnabled;
  $('pw-set').textContent = authEnabled ? 'Passwort ändern' : 'Passwort setzen';
}

async function setPassword(password) {
  const res = await api('/api/auth/password', { method: 'PUT', body: { password } });
  authEnabled = res.enabled;
  $('logout').hidden = !authEnabled;
  renderPasswordState();
}

$('login-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const msg = $('login-msg');
  msg.textContent = '';
  const res = await fetch('/api/login', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ password: $('login-password').value }),
  }).catch(() => null);
  if (res?.ok) location.reload();
  else msg.textContent = res ? await errorText(res) : 'Server nicht erreichbar.';
});

$('logout').addEventListener('click', async () => {
  await fetch('/api/logout', { method: 'POST' });
  location.reload();
});

// Enter in one of the password fields sets the password instead of submitting (and reloading) the page.
$('pw-form').addEventListener('submit', (e) => {
  e.preventDefault();
  $('pw-set').click();
});

$('pw-set').addEventListener('click', async () => {
  const msg = $('pw-msg');
  const password = $('pw-new').value;
  if (!password) return void (msg.textContent = 'Bitte ein Passwort eingeben.');
  if (password !== $('pw-repeat').value) return void (msg.textContent = 'Die Passwörter sind nicht gleich.');
  try {
    await setPassword(password);
    $('pw-new').value = $('pw-repeat').value = '';
    msg.textContent = 'Gespeichert. Andere Geräte müssen sich neu anmelden.';
  } catch (e) {
    msg.textContent = `Fehler: ${e.message}`;
  }
});

$('pw-remove').addEventListener('click', async () => {
  if (!confirm('Passwortschutz entfernen? Danach kann jeder im Heimnetz die App benutzen.')) return;
  try {
    await setPassword('');
    $('pw-msg').textContent = 'Passwortschutz ist aus.';
  } catch (e) {
    $('pw-msg').textContent = `Fehler: ${e.message}`;
  }
});
