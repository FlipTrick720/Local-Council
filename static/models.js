'use strict';

// Which models Ollama holds in memory right now (/api/models/loaded), load/unload for the chat model,
// and the list under Einstellungen.

let loadedModels = [];  // null while Ollama cannot be asked

// Capabilities ("vision", "thinking", "tools") and trained context length per model, asked once per page load.
const modelInfoCache = {};

function modelInfo(model) {
  if (!model) return Promise.resolve(null);
  modelInfoCache[model] ??= api('/api/models/show', { method: 'POST', body: { model } }).catch(() => {
    delete modelInfoCache[model];
    return null;
  });
  return modelInfoCache[model];
}

function gb(bytes) {
  return `${(bytes / 1e9).toFixed(1)} GB`;
}

function memoryText(m) {
  const where = !m.size_vram ? 'nur im RAM (CPU)'
    : m.size_vram >= m.size ? 'ganz auf der GPU'
    : `davon ${gb(m.size_vram)} auf der GPU`;
  const until = new Date(m.expires_at);
  // keep_alive -1 shows up as an expiry date centuries ahead.
  const keep = Number.isNaN(until.getTime()) ? ''
    : until.getFullYear() > 2100 ? ', bleibt geladen'
    : `, bis ${until.toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit' })}`;
  return `${gb(m.size)}, ${where}${keep}`;
}

async function refreshLoaded() {
  try {
    loadedModels = await api('/api/models/loaded');
  } catch {
    loadedModels = null;
  }
  renderChatMemory();
  renderLoadedList();
  scheduleContextMeter();
}

function renderChatMemory() {
  const model = $('chat-model').value;
  const entry = loadedModels?.find((m) => m.name === model);
  $('chat-mem').textContent = !model ? ''
    : loadedModels == null ? 'Speicherstatus unbekannt'
    : entry ? `Im Speicher: ${memoryText(entry)}`
    : 'Nicht im Speicher, wird bei der ersten Nachricht geladen';
  $('chat-load').hidden = !model || !!entry || loadedModels == null;
  $('chat-unload').hidden = !entry || !state.user.admin;  // unloading affects everyone
}

function renderLoadedList() {
  const list = $('set-loaded');
  list.replaceChildren();
  if (loadedModels == null) return list.append(el('span', 'hint', 'Ollama nicht erreichbar.'));
  if (!loadedModels.length) return list.append(el('span', 'hint', 'Kein Modell geladen.'));
  for (const m of loadedModels) {
    const row = el('div', 'loaded-row');
    row.append(el('strong', null, m.name), el('span', 'hint', memoryText(m)));
    if (state.user.admin) {
      const unload = el('button', 'ghost', 'Entladen');
      unload.type = 'button';
      unload.addEventListener('click', () => setLoaded(m.name, false, unload, $('set-loaded-msg')));
      row.append(unload);
    }
    list.append(row);
  }
}

// Loading can take a while (large models); the button shows that it is busy.
async function setLoaded(model, load, button, msg) {
  const label = button.textContent;
  button.disabled = true;
  button.textContent = load ? 'Lädt …' : 'Entlädt …';
  msg.textContent = '';
  try {
    loadedModels = await api(`/api/models/${load ? 'load' : 'unload'}`, { method: 'POST', body: { model } });
  } catch (e) {
    msg.textContent = `${load ? 'Laden' : 'Entladen'} fehlgeschlagen: ${e.message}`;
  } finally {
    button.disabled = false;
    button.textContent = label;
    renderChatMemory();
    renderLoadedList();
  }
  // Ollama may list a model for a moment after unloading it.
  if (!load) setTimeout(refreshLoaded, 1000);
}

function tabVisible(name) {
  return !$(`tab-${name}`).hidden;
}

$('chat-load').addEventListener('click', (e) => setLoaded($('chat-model').value, true, e.target, $('chat-mem-msg')));
$('chat-unload').addEventListener('click', (e) => setLoaded($('chat-model').value, false, e.target, $('chat-mem-msg')));
$('chat-model').addEventListener('change', renderChatMemory);
$('set-loaded-refresh').addEventListener('click', refreshLoaded);
document.querySelectorAll('nav button').forEach((b) => b.addEventListener('click', () => {
  if (['chat', 'settings'].includes(b.dataset.tab)) refreshLoaded();
}));
// keep_alive expiries and models loaded by other tabs/devices show up without a reload.
setInterval(() => {
  if (document.visibilityState === 'visible' && (tabVisible('chat') || tabVisible('settings'))) refreshLoaded();
}, 15000);
