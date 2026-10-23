'use strict';

// Code mode: Python blocks in answers get a "▶ Ausführen" button; the code runs in the server's Docker sandbox
// (/api/code/run, see app/sandbox.py) and the output and plots appear below the block. Results are stored with the
// message (msg.runs, by block number). Also the admin settings for the sandbox and code folders for knowledge bases.

let codeState = { allowed: false, available: false, reason: '', model: '' };

async function loadCodeStatus() {
  try {
    codeState = await api('/api/code');
  } catch {
    codeState = { allowed: false, available: false, reason: '' };
  }
  $('chat-code').closest('label').hidden = !codeState.allowed;
  $('code-state').textContent = codeState.available ? '✓ Sandbox bereit.' : `✗ ${codeState.reason}`;
  $('code-state').className = codeState.available ? 'hint' : 'error';
}

const PYTHON = ['python', 'py', 'python3'];

// Adds run buttons (and earlier results) to the Python blocks of an answer in a chat with code mode.
function attachRuns(body, msg, chat) {
  if (!codeState.allowed || !(chat?.code || msg.runs)) return;
  body.querySelectorAll('.code-block').forEach((block, i) => {
    const lang = block.querySelector('.code-bar span')?.textContent.toLowerCase();
    if (!PYTHON.includes(lang)) return;
    const output = el('div', 'run-result');
    block.after(output);
    if (msg.runs?.[i]) renderRun(output, msg.runs[i], i);
    const button = el('button', 'small', '▶ Ausführen');
    button.type = 'button';
    button.title = codeState.available ? 'In der Sandbox ausführen (ohne Internet, mit Zeit- und Speicherlimit)' : codeState.reason;
    button.disabled = !codeState.available;
    button.addEventListener('click', async () => {
      button.disabled = true;
      button.textContent = 'läuft …';
      output.replaceChildren(el('span', 'hint', 'Startet die Sandbox …'));
      try {
        const result = await api('/api/code/run', { method: 'POST', body: { code: block.querySelector('code').textContent } });
        msg.runs = { ...msg.runs, [i]: result };
        renderRun(output, result, i);
        persist(chat);
      } catch (e) {
        output.replaceChildren(el('div', 'error', `Ausführen fehlgeschlagen: ${e.message}`));
      } finally {
        button.disabled = false;
        button.textContent = '▶ Ausführen';
      }
    });
    block.querySelector('.code-bar').append(button);
  });
}

function renderRun(box, r, i) {
  box.replaceChildren();
  const head = el('div', 'run-head');
  const ok = !r.error && !r.timed_out;
  head.append(el('strong', null, ok ? 'Ausgabe' : 'Ausgabe (mit Fehler)'), el('span', 'hint', ` · ${fmt(r.seconds, ' s', 1)}`));
  const send = el('button', 'ghost small', 'In den Chat');
  send.type = 'button';
  send.title = 'Die Ausgabe ins Eingabefeld übernehmen, um sie dem Modell zu schicken';
  send.addEventListener('click', () => {
    const text = [r.stdout, r.stderr, r.error].filter((t) => t?.trim()).join('\n').slice(0, 4000);
    $('chat-text').value = `Ausgabe von Codeblock ${i + 1}${r.images?.length ? ` (plus ${r.images.length} Bild(er))` : ''}:\n\`\`\`\n${text || '(keine Ausgabe)'}\n\`\`\``;
    $('chat-text').focus();
    scheduleContextMeter();
  });
  head.append(send);
  box.append(head);
  if (r.stdout) box.append(el('pre', 'run-out', r.stdout));
  if (r.stderr) box.append(el('pre', 'run-err', r.stderr));
  if (r.error) box.append(el('pre', 'run-err error', r.error));
  for (const png of r.images || []) {
    const img = el('img', 'run-img');
    img.src = `data:image/png;base64,${png}`;
    img.alt = 'Diagramm aus dem Code';
    box.append(img);
  }
  if (!r.stdout && !r.stderr && !r.error && !r.images?.length) box.append(el('span', 'hint', '(keine Ausgabe)'));
}

// ---------- Admin settings ----------

function initCode() {
  loadCodeStatus();
  if (!state.user.admin) return;
  const c = state.config.code;
  $('code-users').checked = !!c.allow_users;
  $('code-timeout').value = c.timeout;
  $('code-memory').value = c.memory_mb;
  $('code-roots').value = (c.roots || []).join('\n');
  const int = (min, max) => (v) => {
    const n = Number(v);
    if (!Number.isInteger(n) || n < min || n > max) throw new Error(`${min} bis ${max}`);
    return n;
  };
  autosave($('code-users'), 'code', 'allow_users');
  autosave($('code-timeout'), 'code', 'timeout', { parse: int(5, 600) });
  autosave($('code-memory'), 'code', 'memory_mb', { parse: int(64, 8192) });
  autosave($('code-roots'), 'code', 'roots', { parse: (v) => v.split('\n').map((x) => x.trim()).filter(Boolean) });
  $('code-check').addEventListener('click', loadCodeStatus);
}

// A code folder on the server as a knowledge base document (admins; folders below code.roots).
async function addCodeFolder(kb, box) {
  const roots = state.config.code?.roots || [];
  const path = prompt(`Ordner auf dem Server (unterhalb von ${roots.join(', ')}):`, roots[0] || '')?.trim();
  if (!path) return;
  const row = el('div', 'rag-doc hint', `${path}: wird eingelesen …`);
  box.append(row);
  try {
    await stream(`/api/rag/${kb.id}/folder`, { path }, (ev) => {
      if (ev.type === 'queue' && ev.position) row.textContent = `${path}: ${queueText(ev.position)}`;
      else if (ev.type === 'progress') row.textContent = `${path}: ${ev.done} von ${ev.total} Abschnitten eingelesen …`;
      else if (ev.type === 'error') throw new Error(ev.error);
    });
  } catch (e) {
    row.className = 'rag-doc error';
    row.textContent = `${path}: ${e.message}`;
    return;
  }
  row.remove();
  await loadKnowledgeBases();
}
