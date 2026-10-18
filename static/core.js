'use strict';

// Shared helpers for all tabs: API access, NDJSON streaming, small DOM helpers, autosave.

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

// A 401 means the password session ran out (or the password was changed on another device).
async function errorText(res) {
  if (res.status === 401 && typeof showLogin === 'function') showLogin();
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

// The Clipboard API only exists on https/localhost. Over http in the LAN the old execCommand route is used.
async function copyText(text) {
  if (navigator.clipboard && window.isSecureContext) return navigator.clipboard.writeText(text);
  const area = el('textarea');
  area.value = text;
  area.style.cssText = 'position:fixed;opacity:0';
  document.body.append(area);
  area.select();
  const ok = document.execCommand('copy');
  area.remove();
  if (!ok) throw new Error('Kopieren nicht möglich');
}

function copyButton(getText, label = 'Kopieren') {
  const button = el('button', 'copy ghost', label);
  button.type = 'button';
  button.addEventListener('click', async () => {
    try {
      await copyText(getText());
      button.textContent = 'Kopiert ✓';
    } catch {
      button.textContent = 'Fehler';
    }
    setTimeout(() => { button.textContent = label; }, 1200);
  });
  return button;
}

// Models that can chat. Embedding-only models (for knowledge bases) are left out; unknown ones stay in.
function modelNames() {
  return state.models.filter((m) => !m.capabilities || m.capabilities.includes('completion')).map((m) => m.name);
}

function embeddingModelNames() {
  return state.models.filter((m) => !m.capabilities || m.capabilities.includes('embedding')).map((m) => m.name);
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

function plural(n, one, many) {
  return `${n} ${n === 1 ? one : many}`;
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

