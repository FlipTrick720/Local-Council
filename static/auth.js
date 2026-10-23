'use strict';

// Accounts: login dialog, logout, changing one's own password, and (for admins) managing the users under
// Einstellungen. While the app is open (one account without password) no login shows up.

let authOpen = false;

// Called first on start-up. Resolves only when logged in; otherwise the login dialog reloads the page.
// Sets state.user ({name, admin, has_password}) and hides what only admins may use.
async function ensureLogin() {
  let status = { logged_in: false, open: false, user: null };
  try {
    status = await (await fetch('/api/auth')).json();
  } catch { /* server unreachable: the normal start-up shows the error */ }
  if (!status.logged_in) {
    showLogin();
    await new Promise(() => {});
  }
  authOpen = status.open;
  state.user = status.user;
  $('whoami').hidden = authOpen;
  $('whoami').textContent = `${state.user.name}${state.user.admin ? ' (Admin)' : ''}`;
  $('logout').hidden = authOpen;
  $('pw-user').value = state.user.name;
  document.querySelectorAll('[data-admin]').forEach((node) => { node.hidden = !state.user.admin; });
  renderPasswordState();
  if (state.user.admin) loadUsers();
}

function showLogin() {
  $('login').hidden = false;
  $('login-name').focus();
}

function renderPasswordState() {
  const has = state.user.has_password;
  $('pw-state').textContent = authOpen
    ? 'Kein Passwort gesetzt: Jeder im Heimnetz kann die App benutzen. Mit einem Passwort (und weiteren Benutzern unten) braucht es eine Anmeldung.'
    : `Angemeldet als ${state.user.name}.`;
  $('pw-current-label').hidden = !has;
  $('pw-set').textContent = has ? 'Passwort ändern' : 'Passwort setzen';
}

$('login-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const msg = $('login-msg');
  msg.textContent = '';
  const res = await fetch('/api/login', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username: $('login-name').value, password: $('login-password').value }),
  }).catch(() => null);
  if (res?.ok) location.reload();
  else msg.textContent = res ? await errorText(res) : 'Server nicht erreichbar.';
});

$('logout').addEventListener('click', async () => {
  await fetch('/api/logout', { method: 'POST' });
  location.reload();
});

// Enter in one of the password fields changes the password instead of submitting (and reloading) the page.
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
    const { user } = await api('/api/auth/password', { method: 'PUT', body: { current: $('pw-current').value, password } });
    state.user = user;
    $('pw-current').value = $('pw-new').value = $('pw-repeat').value = '';
    if (authOpen) return location.reload();  // from now on a login is needed
    renderPasswordState();
    msg.textContent = 'Gespeichert. Andere Geräte müssen sich neu anmelden.';
  } catch (e) {
    msg.textContent = `Fehler: ${e.message}`;
  }
});

// ---------- Benutzer (admins only) ----------

async function loadUsers() {
  try {
    renderUsers(await api('/api/users'));
  } catch (e) {
    $('users-msg').textContent = `Fehler: ${e.message}`;
  }
}

function renderUsers(list) {
  const box = $('users-list');
  box.replaceChildren();
  for (const u of list) {
    const row = el('div', 'rag-head');
    const me = u.name === state.user.name;
    const details = [u.admin ? 'Admin' : 'Benutzer', !u.has_password && 'ohne Passwort', me && 'du'].filter(Boolean);
    row.append(el('strong', null, u.name), el('span', 'hint', details.join(' · ')));
    if (!me) {
      row.append(
        userButton('Passwort neu …', () => resetPassword(u)),
        userButton(u.admin ? 'Admin entziehen' : 'Zum Admin machen', () => changeUser(u, { admin: !u.admin })),
        userButton('Löschen', () => deleteUser(u)),
      );
    }
    box.append(row);
  }
}

function userButton(label, onClick) {
  const button = el('button', 'ghost small', label);
  button.type = 'button';
  button.addEventListener('click', onClick);
  return button;
}

async function changeUser(u, body) {
  try {
    await api(`/api/users/${encodeURIComponent(u.name)}`, { method: 'PATCH', body });
    $('users-msg').textContent = '';
  } catch (e) {
    $('users-msg').textContent = `Fehler: ${e.message}`;
  }
  loadUsers();
}

async function resetPassword(u) {
  const password = prompt(`Neues Passwort für ${u.name} (meldet ${u.name} auf allen Geräten ab):`);
  if (password) await changeUser(u, { password });
}

async function deleteUser(u) {
  if (!confirm(`Konto „${u.name}“ mit allen Chats, Wissensbasen und Einstellungen löschen?`)) return;
  try {
    await api(`/api/users/${encodeURIComponent(u.name)}`, { method: 'DELETE' });
    $('users-msg').textContent = `„${u.name}“ gelöscht.`;
  } catch (e) {
    $('users-msg').textContent = `Fehler: ${e.message}`;
  }
  loadUsers();
}

$('user-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const msg = $('users-msg');
  try {
    const u = await api('/api/users', {
      method: 'POST',
      body: { name: $('user-name').value, password: $('user-password').value, admin: $('user-admin').checked },
    });
    $('user-name').value = $('user-password').value = '';
    $('user-admin').checked = false;
    msg.textContent = `„${u.name}“ angelegt.`;
  } catch (err) {
    msg.textContent = `Fehler: ${err.message}`;
  }
  loadUsers();
});
