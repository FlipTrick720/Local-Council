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

// Drops models that are no longer installed. If Ollama is unreachable (empty list), keeps everything.
function installedOnly(names) {
  return state.models.length ? names.filter((n) => modelNames().includes(n)) : names;
}

// Checkbox list of installed models. Selected models are only shown struck through while Ollama is unreachable.
function renderModelChecks(container, selected) {
  container.replaceChildren();
  const names = [...new Set([...modelNames(), ...installedOnly(selected)])];
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
  selected = installedOnly(selected ? [selected] : [])[0];
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
// runBtns may be one button or several that share the stop button (all are disabled while running).
function runner(runBtns, stopBtn, task) {
  const buttons = [].concat(runBtns);
  let controller = null;
  stopBtn.addEventListener('click', () => controller?.abort());
  return async () => {
    controller = new AbortController();
    buttons.forEach((b) => { b.disabled = true; });
    stopBtn.disabled = false;
    try {
      await task(controller.signal);
    } catch (e) {
      if (e.name !== 'AbortError') alertBox(e.message);
    } finally {
      buttons.forEach((b) => { b.disabled = false; });
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

// Saves a field to config.json shortly after typing stops, when the field loses focus, and on page
// reload/close. `section.key` is the config entry; `parse` turns the text into the value to store
// (or throws an Error whose message is shown instead of saving). Checkboxes store true/false.
const pendingSaves = new Set();

function autosave(input, section, key, { parse = (v) => v, msg } = {}) {
  if (!msg) {
    msg = el('span', 'hint');
    if (input.type === 'checkbox') input.closest('label').append(msg);
    else input.closest('label').insertBefore(msg, input);
  }
  let timer = null;
  const save = (keepalive = false) => {
    clearTimeout(timer);
    pendingSaves.delete(save);
    let value;
    try {
      value = input.type === 'checkbox' ? input.checked : parse(input.value);
    } catch (e) {
      msg.textContent = ` · ${e.message}`;
      return;
    }
    if (JSON.stringify(state.config[section][key]) === JSON.stringify(value)) return;
    state.config[section][key] = value;
    fetch('/api/config', {
      method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ [section]: { [key]: value } }), keepalive,
    }).then((res) => {
      msg.textContent = res.ok ? ' · gespeichert' : ' · Speichern fehlgeschlagen';
    }).catch(() => { msg.textContent = ' · Speichern fehlgeschlagen'; });
  };
  input.addEventListener('input', () => {
    msg.textContent = '';
    clearTimeout(timer);
    timer = setTimeout(save, 600);
    pendingSaves.add(save);
  });
  input.addEventListener('blur', () => save());
}

window.addEventListener('pagehide', () => pendingSaves.forEach((save) => save(true)));

function parseOptions(text) {
  if (!text.trim()) return {};
  let value;
  try {
    value = JSON.parse(text);
  } catch {
    throw new Error('ungültiges JSON, nicht gespeichert');
  }
  if (typeof value !== 'object' || value === null || Array.isArray(value)) {
    throw new Error('muss ein JSON-Objekt {…} sein');
  }
  return value;
}

function parseTimeout(text) {
  const n = Number(text);
  if (!(n >= 10)) throw new Error('mindestens 10 Sekunden');
  return n;
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

// Renders the results table above the cards. `rows` are arrays of cell texts; the first row is marked best.
function renderTable(caption, headers, rows) {
  const wrap = $('speed-table');
  wrap.replaceChildren();
  if (!rows.length) return;
  if (caption) wrap.append(el('h2', null, caption));
  const table = el('table');
  const head = el('tr');
  for (const h of headers) head.append(el('th', null, h));
  table.append(head);
  rows.forEach((cells, i) => {
    const tr = el('tr', i === 0 && cells[1] !== 'Fehler' ? 'best' : '');
    for (const c of cells) tr.append(el('td', null, c));
    table.append(tr);
  });
  const div = el('div', 'table-wrap');
  div.append(table);
  wrap.append(div);
}

function renderSpeedTable(events) {
  const sorted = [...events].sort((a, b) => (b.stats?.tps ?? -1) - (a.stats?.tps ?? -1));
  renderTable(null, ['Modell', 'Tokens/s', 'Erstes Token', 'Gesamtzeit', 'Tokens', 'Prompt tok/s', 'Ladezeit'],
    sorted.map((r) => {
      const s = r.stats || {};
      return r.error
        ? [r.model, 'Fehler', '', '', '', '', '']
        : [r.model, fmt(s.tps, '', 1), fmt(s.ttft, ' s'), fmt(s.wall_time, ' s'), s.tokens ?? '–',
           fmt(s.prompt_tps, '', 1), fmt(s.load_time, ' s')];
    }));
}

const mean = (xs) => (xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : null);

// Aggregates all recorded benchmark runs per model.
function renderBenchTable(events, models, done, total) {
  const rows = models.map((model) => {
    const stats = events.filter((e) => e.model === model && e.stats).map((e) => e.stats);
    const tps = stats.map((s) => s.tps).filter((v) => v != null);
    return {
      model,
      tps: mean(tps),
      cells: [model, fmt(mean(tps), '', 1), fmt(tps.length ? Math.min(...tps) : null, '', 1),
        fmt(tps.length ? Math.max(...tps) : null, '', 1), fmt(mean(stats.map((s) => s.ttft).filter((v) => v != null)), ' s'),
        fmt(mean(stats.map((s) => s.wall_time)), ' s'), String(events.filter((e) => e.model === model && e.error).length)],
    };
  }).sort((a, b) => (b.tps ?? -1) - (a.tps ?? -1));
  renderTable(`Benchmark: ${done} von ${total} Läufen`,
    ['Modell', 'Ø Tokens/s', 'Min', 'Max', 'Ø Erstes Token', 'Ø Gesamtzeit', 'Fehler'], rows.map((r) => r.cells));
}

function speedRequest() {
  const models = checkedModels($('speed-models'));
  if (!models.length) throw new Error('Bitte mindestens ein Modell auswählen.');
  return { prompt: $('speed-prompt').value, models, parallel: $('speed-parallel').checked };
}

// One speed-test run with live cards. Calls onResult for every finished (done/error) model.
async function speedRun(body, signal, onResult) {
  const cardsEl = $('speed-cards');
  cardsEl.replaceChildren();
  const cards = Object.fromEntries(body.models.map((m) => [m, makeCard(cardsEl, m)]));
  try {
    await stream('/api/speedtest', body, (ev) => {
      const card = cards[ev.model];
      if (ev.type === 'token') card.token(ev.kind, ev.text);
      else if (ev.type === 'done') { card.done(ev.stats); onResult(ev); }
      else if (ev.type === 'error' && card) { card.error(ev.error); onResult(ev); }
      else if (ev.type === 'error') throw new Error(ev.error);
    }, signal);
  } finally {
    Object.values(cards).forEach((c) => c.stopped());
  }
}

const speedButtons = [$('speed-run'), $('bench-run')];

const runSpeed = runner(speedButtons, $('speed-stop'), async (signal) => {
  const body = speedRequest();
  const events = [];
  renderSpeedTable([]);
  try {
    await speedRun(body, signal, (ev) => { events.push(ev); renderSpeedTable(events); });
  } finally {
    loadSpeedHistory();
  }
});

const runBench = runner(speedButtons, $('speed-stop'), async (signal) => {
  const body = speedRequest();
  const total = parseRuns($('bench-runs').value);
  const warmup = $('bench-warmup').checked;
  const msg = $('bench-msg');
  const events = [];
  let done = 0;
  renderTable(null, [], []);
  try {
    for (let i = warmup ? 0 : 1; i <= total; i++) {
      msg.textContent = i === 0 ? 'Aufwärmlauf …' : `Lauf ${i} von ${total} …`;
      await speedRun({ ...body, record: i > 0 }, signal, (ev) => {
        if (i > 0) events.push(ev);
      });
      if (i > 0) {
        done = i;
        renderBenchTable(events, body.models, done, total);
        loadSpeedHistory();
      }
    }
    msg.textContent = `Fertig: ${total} Läufe gespeichert.`;
  } catch (e) {
    msg.textContent = `Abgebrochen nach ${done} von ${total} Läufen.`;
    throw e;
  } finally {
    loadSpeedHistory();
  }
});

function parseRuns(text) {
  const n = Number(text);
  if (!Number.isInteger(n) || n < 1 || n > 100) throw new Error('Anzahl Läufe: 1 bis 100');
  return n;
}

const METRICS = {
  tps: (v) => v.toFixed(1),
  ttft: (v) => `${v.toFixed(2)} s`,
  wall_time: (v) => `${v.toFixed(1)} s`,
};
let speedHistory = [];

async function loadSpeedHistory() {
  try {
    speedHistory = await api('/api/speedtest/history');
  } catch {
    speedHistory = [];
  }
  renderSpeedHistory();
}

function renderSpeedHistory() {
  const metric = $('hist-metric').value;
  // Colors are assigned over all models ever measured, so a model keeps its color in both charts.
  const allModels = [...new Set(speedHistory.map((r) => r.model))].sort();
  for (const [mode, id] of [['sequential', 'hist-seq'], ['parallel', 'hist-par']]) {
    const rows = speedHistory.filter((r) => r.mode === mode && r[metric] != null);
    const runs = [...new Set(rows.map((r) => r.run_id))];
    const runIndex = Object.fromEntries(runs.map((run, i) => [run, i + 1]));
    const xHeaders = {};
    for (const r of rows) {
      const when = new Date(r.run_id).toLocaleString('de-DE', {
        day: '2-digit', month: '2-digit', year: '2-digit', hour: '2-digit', minute: '2-digit',
      });
      const extra = mode === 'parallel' ? ` · ${r.concurrency} gleichzeitig` : '';
      xHeaders[runIndex[r.run_id]] = `Lauf ${runIndex[r.run_id]} · ${when}${extra}`;
    }
    const series = allModels.filter((m) => rows.some((r) => r.model === m)).map((model) => {
      const i = allModels.indexOf(model);
      const segments = [];
      let version = null;
      for (const r of rows.filter((r) => r.model === model)) {
        const isNew = !segments.length || r.version !== version;
        if (isNew) segments.push([]);
        version = r.version;
        const short = r.version ? r.version.slice(0, 7) : '';
        const note = short && (isNew && segments.length > 1 ? `neue Version ${short}` : `Version ${short}`);
        segments.at(-1).push({ x: runIndex[r.run_id], y: r[metric], note });
      }
      // Past 8 models the colors repeat, so the second round is drawn dashed.
      return { name: model, color: `var(--series-${(i % 8) + 1})`, dash: i >= 8 ? '5 3' : null, segments };
    });
    lineChart($(id), { series, xHeaders, format: METRICS[metric], empty: 'Noch keine Läufe in diesem Modus.' });
  }
}

let resizeTimer;
window.addEventListener('resize', () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(renderSpeedHistory, 150);
});
$('hist-metric').addEventListener('change', renderSpeedHistory);
$('hist-prune').addEventListener('click', async () => {
  if (!confirm('Ältere Messungen löschen? Pro Modell bleiben die letzten 3 Läufe „nacheinander“ und die letzten 3 „gleichzeitig“ erhalten.')) return;
  try {
    const { removed } = await api('/api/speedtest/history/prune', { method: 'POST', body: { keep: 3 } });
    $('hist-msg').textContent = `${removed} Messungen gelöscht.`;
  } catch (e) {
    $('hist-msg').textContent = 'Fehler: ' + e.message;
  }
  loadSpeedHistory();
});
$('speed-run').addEventListener('click', runSpeed);
$('bench-run').addEventListener('click', runBench);
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
  const { server } = state.config;
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
    Object.assign(state.config.ollama, {
      url: $('set-url').value.trim(),
      keep_alive: $('set-keepalive').value.trim() || null,
      timeout: parseTimeout($('set-timeout').value),
      options: parseOptions($('set-options').value),
    });
    await saveConfig();
    msg.textContent = 'Gespeichert.';
    await refreshModels();
  } catch (e) {
    msg.textContent = 'Fehler: ' + e.message;
  }
});

// ---------- Init ----------

function renderPickers(sel) {
  renderModelChecks($('speed-models'), sel.speed);
  renderModelChecks($('council-members'), sel.council);
  renderModelSelect($('council-synth'), sel.synth, '(erstes Ratsmitglied)');
  renderModelSelect($('chat-model'), sel.chat);
  renderSettings();
}

function configSelection() {
  const { speedtest, council, chat } = state.config;
  return { speed: speedtest.models, council: council.members, synth: council.synthesizer, chat: chat.model };
}

// What is currently ticked/selected on the page (possibly unsaved).
function pageSelection() {
  return {
    speed: checkedModels($('speed-models')),
    council: checkedModels($('council-members')),
    synth: $('council-synth').value,
    chat: $('chat-model').value,
  };
}

// Re-reads the installed models from Ollama and keeps the current on-page selection.
let lastRefresh = 0;
async function refreshModels() {
  lastRefresh = Date.now();
  const sel = pageSelection();
  await loadModels();
  renderPickers(sel);
}

// Pick up `ollama pull` / `ollama rm` when the user comes back to the tab.
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'visible' && state.config && Date.now() - lastRefresh > 2000) refreshModels();
});

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
  const { speedtest, council, chat, ollama } = state.config;
  $('speed-prompt').value = speedtest.prompt;
  $('speed-parallel').checked = speedtest.parallel;
  $('council-question').value = council.question;
  $('council-rounds').value = council.rounds;
  $('council-parallel').checked = council.parallel;
  $('chat-system').value = chat.system;
  $('set-url').value = ollama.url;
  $('set-keepalive').value = ollama.keep_alive ?? '';
  $('set-timeout').value = ollama.timeout;
  $('set-options').value = Object.keys(ollama.options || {}).length ? JSON.stringify(ollama.options, null, 2) : '';

  $('bench-runs').value = speedtest.bench_runs;
  $('bench-warmup').checked = speedtest.bench_warmup;

  autosave($('speed-prompt'), 'speedtest', 'prompt');
  autosave($('bench-runs'), 'speedtest', 'bench_runs', { parse: parseRuns });
  autosave($('bench-warmup'), 'speedtest', 'bench_warmup');
  autosave($('council-question'), 'council', 'question');
  autosave($('chat-system'), 'chat', 'system', { msg: $('chat-system-msg') });
  autosave($('set-keepalive'), 'ollama', 'keep_alive', { parse: (v) => v.trim() || null });
  autosave($('set-timeout'), 'ollama', 'timeout', { parse: parseTimeout });
  autosave($('set-options'), 'ollama', 'options', { parse: parseOptions });
  await loadModels();
  lastRefresh = Date.now();
  renderPickers(configSelection());
  loadSpeedHistory();
})();
