'use strict';

// Chat tab, per-chat settings: system prompt with presets, generation options and the thinking switch.
// The values live on the chat object (chat.system, chat.options, chat.think) and are stored with it.
// Presets and the defaults for new chats are kept in config.json (chat.presets, chat.system, …).

// [Ollama option, label, step, min, max]
const GEN_OPTIONS = [
  ['temperature', 'Temperatur', 0.05, 0, 2],
  ['top_p', 'top_p', 0.05, 0, 1],
  ['top_k', 'top_k', 1, 0, 1000],
  ['repeat_penalty', 'repeat_penalty', 0.05, 0, 3],
  ['num_ctx', 'Kontextlänge (Tokens)', 1024, 512, 262144],
  ['num_predict', 'Max. Antwortlänge (Tokens)', 64, -1, 131072],
  ['seed', 'Seed', 1, null, null],
];

let settingsTimer = null;

function buildGenInputs() {
  const box = $('chat-gen');
  for (const [key, label, step, min, max] of GEN_OPTIONS) {
    const input = el('input');
    Object.assign(input, { type: 'number', id: `opt-${key}`, step, placeholder: 'Standard' });
    if (min != null) input.min = min;
    if (max != null) input.max = max;
    const wrap = el('label', null, label);
    wrap.append(input);
    box.insertBefore(wrap, $('chat-think').closest('label'));
    input.addEventListener('input', settingsChanged);
  }
}

// Empty or invalid fields are left out, so the configured or the model's own default applies.
function readOptions() {
  const options = {};
  for (const [key] of GEN_OPTIONS) {
    const text = $(`opt-${key}`).value.trim();
    if (text !== '' && Number.isFinite(Number(text))) options[key] = Number(text);
  }
  return options;
}

function parseThink(value) {
  return value === '' ? null : value === 'true';
}

function renderChatSettings(chat) {
  $('chat-system').value = chat.system || '';
  for (const [key] of GEN_OPTIONS) $(`opt-${key}`).value = chat.options?.[key] ?? '';
  $('chat-think').value = chat.think == null ? '' : String(chat.think);
  $('chat-tools').checked = !!chat.tools;
  $('chat-web').checked = !!chat.web;
  renderKnowledgeSelect();
  $('chat-settings-msg').textContent = '';
  renderPresets();
  summarizeSettings(chat);
}

// The <summary> line tells what is set without opening the panel.
function summarizeSettings(chat) {
  const parts = [];
  const preset = presets().find((p) => p.system === chat.system);
  if (preset) parts.push(`Vorlage „${preset.name}“`);
  else if (chat.system?.trim()) parts.push('eigener System-Prompt');
  const n = Object.keys(chat.options || {}).length;
  if (n) parts.push(`${n} Einstellung${n > 1 ? 'en' : ''}`);
  if (chat.think != null) parts.push(chat.think ? 'Denken an' : 'Denken aus');
  const kb = knowledgeBases.find((k) => k.id === chat.knowledge);
  if (kb) parts.push(`Wissen „${kb.name}“`);
  if (chat.tools) parts.push('Werkzeuge');
  if (chat.web) parts.push('Websuche');
  $('chat-settings-sum').textContent = parts.length ? `(${parts.join(', ')})` : '';
}

function settingsChanged() {
  const chat = chatState.current;
  chat.system = $('chat-system').value;
  chat.options = readOptions();
  chat.think = parseThink($('chat-think').value);
  chat.knowledge = $('chat-knowledge').value || null;
  chat.tools = $('chat-tools').checked;
  chat.web = $('chat-web').checked;
  syncPresetSelect();
  summarizeSettings(chat);
  scheduleContextMeter();
  const msg = $('chat-settings-msg');
  clearTimeout(settingsTimer);
  if (!chat.messages.length) {
    msg.textContent = ' · gilt für diesen Chat';
    return;
  }
  msg.textContent = '';
  settingsTimer = setTimeout(async () => {
    await persist(chat);
    msg.textContent = ' · gespeichert';
  }, 600);
}

// ---------- Presets ----------

function presets() {
  return state.config.chat.presets || [];
}

function renderPresets() {
  const select = $('chat-preset');
  select.replaceChildren(new Option('– eigener Text –', ''));
  for (const p of presets()) select.append(new Option(p.name, p.name));
  syncPresetSelect();
}

function syncPresetSelect() {
  const match = presets().find((p) => p.system === $('chat-system').value);
  $('chat-preset').value = match ? match.name : '';
  $('preset-delete').disabled = !match;
}

async function storePresets(list) {
  await api('/api/config', { method: 'PUT', body: { chat: { presets: list } } });
  state.config.chat.presets = list;
  renderPresets();
  summarizeSettings(chatState.current);
}

$('chat-preset').addEventListener('change', () => {
  const preset = presets().find((p) => p.name === $('chat-preset').value);
  if (!preset) return;
  $('chat-system').value = preset.system;
  settingsChanged();
});

$('preset-save').addEventListener('click', async () => {
  const text = $('chat-system').value;
  if (!text.trim()) return alert('Der System-Prompt ist leer.');
  const name = prompt('Name der Vorlage (ein vorhandener Name wird überschrieben):', $('chat-preset').value)?.trim();
  if (!name) return;
  const list = presets().some((p) => p.name === name)
    ? presets().map((p) => (p.name === name ? { name, system: text } : p))
    : [...presets(), { name, system: text }];
  try {
    await storePresets(list);
    $('chat-settings-msg').textContent = ` · Vorlage „${name}“ gespeichert`;
  } catch (e) {
    $('chat-settings-msg').textContent = ` · Fehler: ${e.message}`;
  }
});

$('preset-delete').addEventListener('click', async () => {
  const name = $('chat-preset').value;
  if (!name || !confirm(`Vorlage „${name}“ löschen? Der Text im Chat bleibt erhalten.`)) return;
  try {
    await storePresets(presets().filter((p) => p.name !== name));
  } catch (e) {
    $('chat-settings-msg').textContent = ` · Fehler: ${e.message}`;
  }
});

// ---------- Defaults for new chats ----------

$('chat-save').addEventListener('click', (e) => flash(e.target, async () => {
  const c = chatState.current;
  const defaults = {
    model: $('chat-model').value, system: c.system || '', options: c.options || {}, think: c.think ?? null,
    knowledge: c.knowledge || null, tools: !!c.tools, web: !!c.web,
  };
  await api('/api/config', { method: 'PUT', body: { chat: defaults } });
  Object.assign(state.config.chat, defaults);
}));

buildGenInputs();
$('chat-system').addEventListener('input', settingsChanged);
for (const id of ['chat-think', 'chat-knowledge', 'chat-tools', 'chat-web']) $(id).addEventListener('change', settingsChanged);
