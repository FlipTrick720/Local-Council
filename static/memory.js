'use strict';

// Memory: facts about the user that go into the system prompt (/api/memory, see app/memory.py). Settings page with
// the list, a global switch and a switch per chat (chat.memory). The model only proposes facts
// (msg.memory_proposals); they are stored when the user clicks "Merken".

let memoryState = { enabled: true, items: [], prompt_chars: 0, facts_in_prompt: 0, max_chars: 2000 };

async function loadMemory() {
  try {
    memoryState = await api('/api/memory');
  } catch (e) {
    $('mem-msg').textContent = `Fehler: ${e.message}`;
  }
  renderMemory();
  scheduleContextMeter();
}

function memoryInUse(chat) {
  return memoryState.enabled && chat?.memory !== false;
}

// Tokens the memory adds to each request of this chat (for the context meter).
function memoryTokens(chat) {
  return memoryInUse(chat) ? estimateTokens(memoryState.prompt_chars) : 0;
}

function renderMemory() {
  $('mem-enabled').checked = memoryState.enabled;
  const n = memoryState.items.length;
  $('mem-state').textContent = !n ? 'Noch keine Erinnerungen.'
    : `${plural(n, 'Erinnerung', 'Erinnerungen')}; ${memoryState.facts_in_prompt} davon pass${memoryState.facts_in_prompt === 1 ? 't' : 'en'} ins Limit von `
      + `${memoryState.max_chars} Zeichen (~${kTokens(estimateTokens(memoryState.prompt_chars))} Tokens pro Anfrage).`
      + (memoryState.facts_in_prompt < n ? ' Die ältesten bleiben außen vor.' : '');
  const list = $('mem-list');
  list.replaceChildren();
  for (const item of [...memoryState.items].reverse()) list.append(memoryRow(item));
}

function memoryRow(item) {
  const row = el('div', 'rag-head');
  const text = el('span', 'mem-text', item.text);
  const edit = el('button', 'ghost small', 'Bearbeiten');
  edit.type = 'button';
  edit.addEventListener('click', async () => {
    const next = prompt('Erinnerung bearbeiten:', item.text)?.trim();
    if (!next || next === item.text) return;
    await memoryCall(() => api(`/api/memory/${item.id}`, { method: 'PATCH', body: { text: next } }));
  });
  const del = el('button', 'ghost small', 'Löschen');
  del.type = 'button';
  del.addEventListener('click', async () => {
    if (confirm(`„${item.text}“ vergessen?`)) await memoryCall(() => api(`/api/memory/${item.id}`, { method: 'DELETE' }));
  });
  row.append(text, edit, del);
  return row;
}

async function memoryCall(task) {
  try {
    await task();
    $('mem-msg').textContent = '';
  } catch (e) {
    $('mem-msg').textContent = `Fehler: ${e.message}`;
  }
  await loadMemory();
}

$('mem-enabled').addEventListener('change', () => memoryCall(() => api('/api/memory', {
  method: 'PUT', body: { enabled: $('mem-enabled').checked },
})));

$('mem-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const text = $('mem-text').value.trim();
  if (!text) return;
  await memoryCall(() => api('/api/memory', { method: 'POST', body: { text } }));
  $('mem-text').value = '';
});

// ---------- Proposals under an answer ----------

// While streaming, the <<remember: …>> lines are hidden; the server removes them from the finished answer.
function withoutMemoryMarkers(text) {
  return text.replace(/^[ \t>*_-]*<<[^\n]*$/gm, '').trimEnd();
}

function renderProposals(box, msg, chat) {
  const items = msg.memory_proposals || [];
  box.hidden = !items.length;
  box.replaceChildren();
  for (const p of items) {
    const row = el('div', 'mem-proposal');
    row.append(el('span', null, `💡 Merken? „${p.text}“`));
    if (p.state === 'saved') row.append(el('span', 'hint', ' · gemerkt'));
    else if (p.state === 'dismissed') row.append(el('span', 'hint', ' · verworfen'));
    else {
      const save = el('button', 'small', 'Merken');
      const drop = el('button', 'ghost small', 'Verwerfen');
      save.type = drop.type = 'button';
      save.addEventListener('click', async () => {
        try {
          await api('/api/memory', { method: 'POST', body: { text: p.text } });
          p.state = 'saved';
          loadMemory();
        } catch (e) {
          setChatStatus(`Merken fehlgeschlagen: ${e.message}`, 'err');
          return;
        }
        renderProposals(box, msg, chat);
        persist(chat);
      });
      drop.addEventListener('click', () => {
        p.state = 'dismissed';
        renderProposals(box, msg, chat);
        persist(chat);
      });
      row.append(save, drop);
    }
    box.append(row);
  }
}

// Called from app.js once the config is loaded.
function initMemory() {
  loadMemory();
}
