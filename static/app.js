'use strict';

const $ = (id) => document.getElementById(id);
const state = { config: null, models: [] };

// ---------- API ----------

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
    body: options.body && JSON.stringify(options.body),
  });
  if (!res.ok) throw new Error(await errorText(res));
  return res.json();
}

async function errorText(res) {
  try { return (await res.json()).detail || res.statusText; } catch { return res.statusText; }
}

// POSTs `body` and calls onEvent for each NDJSON line of the streamed response.
async function stream(path, body, onEvent, signal) {
  const res = await fetch(path, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), signal,
  });
  if (!res.ok) throw new Error(await errorText(res));
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let i;
    while ((i = buffer.indexOf('\n')) >= 0) {
      const line = buffer.slice(0, i).trim();
      buffer = buffer.slice(i + 1);
      if (line) onEvent(JSON.parse(line));
    }
  }
}

async function saveConfig() {
  state.config = await api('/api/config', { method: 'PUT', body: state.config });
}

// ---------- UI helpers ----------

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  return node;
}

function modelNames() {
  return state.models.map((m) => m.name);
}

// Checkbox list of installed models; configured-but-missing models are shown struck through.
function renderModelChecks(container, selected) {
  container.replaceChildren();
  const names = [...new Set([...modelNames(), ...selected])];
  if (!names.length) container.append(el('span', 'hint', 'Keine Modelle gefunden. Läuft Ollama?'));
  for (const name of names) {
    const label = el('label');
    if (!modelNames().includes(name)) label.classList.add('missing');
    const box = el('input');
    box.type = 'checkbox';
    box.value = name;
    box.checked = selected.includes(name);
    label.append(box, ' ', name);
    container.append(label);
  }
}

function checkedModels(container) {
  return [...container.querySelectorAll('input:checked')].map((b) => b.value);
}

function renderModelSelect(select, selected, emptyLabel) {
  select.replaceChildren();
  if (emptyLabel) select.append(new Option(emptyLabel, ''));
  const names = [...new Set([...modelNames(), ...(selected ? [selected] : [])])];
  for (const name of names) select.append(new Option(name, name));
  select.value = selected || (emptyLabel ? '' : names[0] || '');
}

function fmt(value, unit = '', digits = 2) {
  return value == null ? '–' : `${Number(value).toFixed(digits)}${unit}`;
}

function statsLine(s) {
  if (!s) return '';
  return `${fmt(s.tps, ' tok/s', 1)} · ${s.tokens} Tokens · erstes Token ${fmt(s.ttft, ' s')} · gesamt ${fmt(s.wall_time, ' s')}`;
}

// A card that shows one model's streamed output.
function makeCard(parent, title, subtitle = '') {
  const card = el('div', 'card');
  const head = el('div', 'card-head');
  const badge = el('span', 'badge', 'läuft…');
  head.append(el('strong', null, title), el('span', 'sub', subtitle), badge);
  const thinking = el('details', 'thinking');
  thinking.hidden = true;
  const thinkText = el('div', 'think-text');
  thinking.append(el('summary', null, 'Gedankengang'), thinkText);
  const out = el('div', 'out');
  const stats = el('div', 'stats');
  card.append(head, thinking, out, stats);
  parent.append(card);
  return {
    card,
    token(kind, text) {
      if (kind === 'thinking') {
        thinking.hidden = false;
        thinkText.textContent += text;
      } else {
        out.textContent += text;
      }
    },
    done(s) {
      badge.textContent = s?.tps != null ? `${fmt(s.tps, ' tok/s', 1)}` : 'fertig';
      stats.textContent = statsLine(s);
    },
    error(message) {
      badge.textContent = 'Fehler';
      badge.classList.add('err');
      out.append(el('div', 'error', message));
    },
    stopped() {
      if (badge.textContent === 'läuft…') badge.textContent = 'abgebrochen';
    },
  };
}

// Wires a run/stop button pair around an async task that receives an AbortSignal.
function runner(runBtn, stopBtn, task) {
  let controller = null;
  stopBtn.addEventListener('click', () => controller?.abort());
  return async () => {
    controller = new AbortController();
    runBtn.disabled = true;
    stopBtn.disabled = false;
    try {
      await task(controller.signal);
    } catch (e) {
      if (e.name !== 'AbortError') alertBox(e.message);
    } finally {
      runBtn.disabled = false;
      stopBtn.disabled = true;
      controller = null;
    }
  };
}

function alertBox(message) {
  const status = $('status');
  status.textContent = message;
  status.className = 'status err';
}

async function flash(button, task) {
  const label = button.textContent;
  try {
    await task();
    button.textContent = 'Gespeichert ✓';
  } catch (e) {
    button.textContent = 'Fehler: ' + e.message;
  }
  setTimeout(() => { button.textContent = label; }, 1500);
}

// ---------- Speed-Test ----------

function renderSpeedTable(rows) {
  const wrap = $('speed-table');
  wrap.replaceChildren();
  if (!rows.length) return;
  rows.sort((a, b) => (b.stats?.tps ?? -1) - (a.stats?.tps ?? -1));
  const table = el('table');
  const head = el('tr');
  for (const h of ['Modell', 'Tokens/s', 'Erstes Token', 'Gesamtzeit', 'Tokens', 'Prompt tok/s', 'Ladezeit'])
    head.append(el('th', null, h));
  table.append(head);
  rows.forEach((r, i) => {
    const s = r.stats || {};
    const tr = el('tr', i === 0 && s.tps != null ? 'best' : '');
    const cells = r.error
      ? [r.model, 'Fehler', '', '', '', '', '']
      : [r.model, fmt(s.tps, '', 1), fmt(s.ttft, ' s'), fmt(s.wall_time, ' s'), s.tokens ?? '–',
         fmt(s.prompt_tps, '', 1), fmt(s.load_time, ' s')];
    for (const c of cells) tr.append(el('td', null, c));
    table.append(tr);
  });
  const div = el('div', 'table-wrap');
  div.append(table);
  wrap.append(div);
}

const runSpeed = runner($('speed-run'), $('speed-stop'), async (signal) => {
  const models = checkedModels($('speed-models'));
  if (!models.length) throw new Error('Bitte mindestens ein Modell auswählen.');
  const cardsEl = $('speed-cards');
  cardsEl.replaceChildren();
  renderSpeedTable([]);
  const cards = Object.fromEntries(models.map((m) => [m, makeCard(cardsEl, m)]));
  const rows = [];
  try {
    await stream('/api/speedtest', {
      prompt: $('speed-prompt').value,
      models,
      parallel: $('speed-parallel').checked,
    }, (ev) => {
      const card = cards[ev.model];
      if (ev.type === 'token') card.token(ev.kind, ev.text);
      else if (ev.type === 'done') { card.done(ev.stats); rows.push(ev); renderSpeedTable(rows); }
      else if (ev.type === 'error' && card) { card.error(ev.error); rows.push(ev); renderSpeedTable(rows); }
      else if (ev.type === 'error') throw new Error(ev.error);
    }, signal);
  } finally {
    Object.values(cards).forEach((c) => c.stopped());
  }
});

$('speed-run').addEventListener('click', runSpeed);
$('speed-save').addEventListener('click', (e) => flash(e.target, () => {
  Object.assign(state.config.speedtest, {
    models: checkedModels($('speed-models')),
    parallel: $('speed-parallel').checked,
    prompt: $('speed-prompt').value,
  });
  return saveConfig();
}));

// ---------- Council ----------

function phaseTitle(ev, rounds) {
  if (ev.stage === 'initial') return 'Erste Antworten';
  if (ev.stage === 'discussion') return `Diskussionsrunde ${ev.round} von ${rounds}`;
  return 'Finale Antwort des Rats';
}

const runCouncil = runner($('council-run'), $('council-stop'), async (signal) => {
  const members = checkedModels($('council-members'));
  if (members.length < 2) throw new Error('Bitte mindestens zwei Ratsmitglieder auswählen.');
  if (!$('council-question').value.trim()) throw new Error('Bitte eine Frage eingeben.');
  const out = $('council-out');
  out.replaceChildren();
  const cards = {};
  let labels = {};
  let rounds = 0;
  try {
    await stream('/api/council', {
      question: $('council-question').value,
      members,
      synthesizer: $('council-synth').value || null,
      rounds: Number($('council-rounds').value),
      parallel: $('council-parallel').checked,
    }, (ev) => {
      const key = `${ev.stage}-${ev.round}-${ev.model}`;
      if (ev.type === 'start') { labels = ev.labels; rounds = ev.rounds; }
      else if (ev.type === 'phase') {
        out.append(el('h2', null, phaseTitle(ev, rounds)));
        const grid = el('div', 'cards');
        out.append(grid);
        for (const m of ev.models) {
          const card = makeCard(grid, m, ev.stage === 'synthesis' ? 'fasst zusammen' : labels[m]);
          if (ev.stage === 'synthesis') { card.card.classList.add('final'); grid.style.gridTemplateColumns = '1fr'; }
          cards[`${ev.stage}-${ev.round}-${m}`] = card;
        }
      }
      else if (ev.type === 'token') cards[key].token(ev.kind, ev.text);
      else if (ev.type === 'done') cards[key].done(ev.stats);
      else if (ev.type === 'error' && cards[key]) cards[key].error(ev.error);
      else if (ev.type === 'error') out.append(el('p', 'error', ev.error));
    }, signal);
  } finally {
    Object.values(cards).forEach((c) => c.stopped());
  }
});

$('council-run').addEventListener('click', runCouncil);
$('council-save').addEventListener('click', (e) => flash(e.target, () => {
  Object.assign(state.config.council, {
    members: checkedModels($('council-members')),
    synthesizer: $('council-synth').value,
    rounds: Number($('council-rounds').value),
    parallel: $('council-parallel').checked,
  });
  return saveConfig();
}));

// ---------- Chat ----------

let chatHistory = [];

function chatBubble(role, title) {
  const card = makeCard($('chat-log'), title);
  card.card.classList.add(role);
  return card;
}

const runChat = runner($('chat-send'), $('chat-stop'), async (signal) => {
  const text = $('chat-text').value.trim();
  const model = $('chat-model').value;
  if (!text) return;
  if (!model) throw new Error('Bitte ein Modell auswählen.');
  $('chat-text').value = '';
  const user = chatBubble('user', 'Du');
  user.token('content', text);
  user.done(null);
  chatHistory.push({ role: 'user', content: text });

  const reply = chatBubble('assistant', model);
  let answer = '';
  try {
    await stream('/api/chat', { model, messages: chatHistory, system: $('chat-system').value }, (ev) => {
      if (ev.type === 'token') {
        reply.token(ev.kind, ev.text);
        if (ev.kind === 'content') answer += ev.text;
        window.scrollTo(0, document.body.scrollHeight);
      } else if (ev.type === 'done') reply.done(ev.stats);
      else if (ev.type === 'error') reply.error(ev.error);
    }, signal);
  } finally {
    reply.stopped();
    if (answer) chatHistory.push({ role: 'assistant', content: answer });
    else chatHistory.pop();
  }
});

$('chat-form').addEventListener('submit', (e) => { e.preventDefault(); runChat(); });
$('chat-text').addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey && !$('chat-send').disabled) { e.preventDefault(); runChat(); }
});
$('chat-new').addEventListener('click', () => { chatHistory = []; $('chat-log').replaceChildren(); });
$('chat-save').addEventListener('click', (e) => flash(e.target, () => {
  Object.assign(state.config.chat, { model: $('chat-model').value, system: $('chat-system').value });
  return saveConfig();
}));

// ---------- Einstellungen ----------

function renderSettings() {
  const { ollama, server } = state.config;
  $('set-url').value = ollama.url;
  $('set-keepalive').value = ollama.keep_alive ?? '';
  $('set-timeout').value = ollama.timeout;
  $('set-options').value = Object.keys(ollama.options || {}).length ? JSON.stringify(ollama.options, null, 2) : '';
  $('set-server').textContent =
    `Server lauscht auf ${server.host}:${server.port}. Änderungen daran in config.json greifen erst nach einem Neustart.`;
  const list = $('set-models');
  list.replaceChildren();
  for (const m of state.models) {
    const details = [m.parameter_size, m.quantization, m.size && `${(m.size / 1e9).toFixed(1)} GB`].filter(Boolean);
    list.append(el('div', null, `${m.name}  (${details.join(', ')})`));
  }
  if (!state.models.length) list.textContent = 'Keine Modelle gefunden.';
}

$('set-save').addEventListener('click', async () => {
  const msg = $('set-msg');
  try {
    const raw = $('set-options').value.trim();
    Object.assign(state.config.ollama, {
      url: $('set-url').value.trim(),
      keep_alive: $('set-keepalive').value.trim() || null,
      timeout: Number($('set-timeout').value) || 600,
      options: raw ? JSON.parse(raw) : {},
    });
    await saveConfig();
    msg.textContent = 'Gespeichert.';
    await loadModels();
    renderAll();
  } catch (e) {
    msg.textContent = 'Fehler: ' + e.message;
  }
});

// ---------- Init ----------

function renderAll() {
  const { speedtest, council, chat } = state.config;
  renderModelChecks($('speed-models'), speedtest.models);
  renderModelChecks($('council-members'), council.members);
  renderModelSelect($('council-synth'), council.synthesizer, '(erstes Ratsmitglied)');
  renderModelSelect($('chat-model'), chat.model);
  renderSettings();
}

async function loadModels() {
  const status = $('status');
  try {
    state.models = await api('/api/models');
    status.textContent = `Ollama verbunden · ${state.models.length} Modelle`;
    status.className = 'status ok';
  } catch (e) {
    state.models = [];
    status.textContent = e.message;
    status.className = 'status err';
  }
}

document.querySelectorAll('nav button').forEach((btn) => btn.addEventListener('click', () => {
  document.querySelectorAll('nav button').forEach((b) => b.classList.toggle('active', b === btn));
  document.querySelectorAll('.tab').forEach((t) => { t.hidden = t.id !== `tab-${btn.dataset.tab}`; });
}));

(async () => {
  state.config = await api('/api/config');
  const { speedtest, council, chat } = state.config;
  $('speed-prompt').value = speedtest.prompt;
  $('speed-parallel').checked = speedtest.parallel;
  $('council-rounds').value = council.rounds;
  $('council-parallel').checked = council.parallel;
  $('chat-system').value = chat.system;
  await loadModels();
  renderAll();
})();
