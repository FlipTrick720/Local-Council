'use strict';

// Speed-Test, Einstellungen and start-up. Loaded last, after core.js and the chat modules.

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
      if (ev.type === 'queue') $('speed-queue').textContent = queueText(ev.position);
      else if (ev.type === 'token') card.token(ev.kind, ev.text);
      else if (ev.type === 'done') { card.done(ev.stats); onResult(ev); }
      else if (ev.type === 'error' && card) { card.error(ev.error); onResult(ev); }
      else if (ev.type === 'error') throw new Error(ev.error);
    }, signal);
  } finally {
    Object.values(cards).forEach((c) => c.stopped());
    $('speed-queue').textContent = '';
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
  return saveConfig('speedtest');
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
    await saveConfig('ollama');
    msg.textContent = 'Gespeichert.';
    await refreshModels();
  } catch (e) {
    msg.textContent = 'Fehler: ' + e.message;
  }
});

// ---------- Init ----------

function renderPickers(sel) {
  renderModelChecks($('speed-models'), sel.speed);
  renderModelSelect($('chat-model'), sel.chat);
  renderModelSelect($('set-titlemodel'), sel.titleModel, '(Modell des Chats)');
  if (state.config.rag) renderEmbedModels();
  if (chatState.current?.kind === 'council') renderCouncilSetup(chatState.current);
  renderSettings();
}

function configSelection() {
  const { speedtest, chat } = state.config;
  return { speed: speedtest.models, chat: chat.model, titleModel: chat.title_model };
}

// What is currently ticked/selected on the page (possibly unsaved).
function pageSelection() {
  return {
    speed: checkedModels($('speed-models')),
    chat: $('chat-model').value,
    titleModel: $('set-titlemodel').value,
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

function showTab(name) {
  document.querySelectorAll('nav button').forEach((b) => b.classList.toggle('active', b.dataset.tab === name));
  document.querySelectorAll('.tab').forEach((t) => { t.hidden = t.id !== `tab-${name}`; });
  if (name === 'bench') initBenchmark();
}

document.querySelectorAll('nav button').forEach((btn) => btn.addEventListener('click', () => showTab(btn.dataset.tab)));

(async () => {
  await ensureLogin();
  showTab(state.user.admin ? 'speed' : 'chat');  // the Speed-Test is for admins only
  state.config = await api('/api/config');
  const { speedtest, ollama } = state.config;
  $('speed-prompt').value = speedtest.prompt;
  $('speed-parallel').checked = speedtest.parallel;
  $('set-url').value = ollama.url;
  $('set-keepalive').value = ollama.keep_alive ?? '';
  $('set-timeout').value = ollama.timeout;
  $('set-options').value = Object.keys(ollama.options || {}).length ? JSON.stringify(ollama.options, null, 2) : '';
  $('set-parallel').value = state.config.queue.parallel;

  $('bench-runs').value = speedtest.bench_runs;
  $('bench-warmup').checked = speedtest.bench_warmup;

  autosave($('speed-prompt'), 'speedtest', 'prompt');
  autosave($('bench-runs'), 'speedtest', 'bench_runs', { parse: parseRuns });
  autosave($('bench-warmup'), 'speedtest', 'bench_warmup');
  autosave($('set-keepalive'), 'ollama', 'keep_alive', { parse: (v) => v.trim() || null });
  autosave($('set-timeout'), 'ollama', 'timeout', { parse: parseTimeout });
  autosave($('set-options'), 'ollama', 'options', { parse: parseOptions });
  autosave($('set-parallel'), 'queue', 'parallel', { parse: (v) => {
    const n = Number(v);
    if (!Number.isInteger(n) || n < 1 || n > 8) throw new Error('1 bis 8');
    return n;
  } });
  $('set-autotitle').checked = state.config.chat.auto_title !== false;
  autosave($('set-autotitle'), 'chat', 'auto_title');
  autosave($('set-titlemodel'), 'chat', 'title_model');
  await loadModels();
  lastRefresh = Date.now();
  renderPickers(configSelection());
  if (state.user.admin) loadSpeedHistory();
  initRag();
  initAccount();
  initMemory();
  initCode();
  initChat();
})();
