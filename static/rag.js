'use strict';

// Knowledge bases (RAG) and web search settings under Einstellungen, plus the knowledge base picker in the chat.
// Documents are uploaded like chat attachments (/api/uploads) and then indexed (/api/rag/<id>/documents).

let knowledgeBases = [];

async function loadKnowledgeBases() {
  try {
    knowledgeBases = await api('/api/rag');
  } catch (e) {
    $('rag-msg').textContent = `Fehler: ${e.message}`;
  }
  renderKnowledgeBases();
  renderKnowledgeSelect();
}

// Chat picker; a chat whose knowledge base was deleted shows it as missing.
function renderKnowledgeSelect() {
  const select = $('chat-knowledge');
  const chosen = chatState.current?.knowledge || '';
  select.replaceChildren(new Option('(keine)', ''));
  for (const kb of knowledgeBases) select.append(new Option(`${kb.name} (${kb.docs.length} Dok.)`, kb.id));
  if (chosen && !knowledgeBases.some((kb) => kb.id === chosen) && knowledgeBases.length) {
    select.append(new Option('(gelöscht)', chosen));
  }
  select.value = chosen;
}

function renderEmbedModels() {
  const select = $('rag-model');
  const configured = state.config.rag.embed_model;
  select.replaceChildren();
  const names = embeddingModelNames();
  for (const name of names) select.append(new Option(name, name));
  // Ollama lists "bge-m3" as "bge-m3:latest".
  const installed = names.find((n) => n === configured || n === `${configured}:latest`);
  if (configured && !installed) {
    select.append(new Option(`${configured} (nicht installiert: ollama pull ${configured})`, configured));
  }
  select.value = installed || configured;
}

function renderKnowledgeBases() {
  const list = $('rag-list');
  list.replaceChildren();
  if (!knowledgeBases.length) list.append(el('p', 'hint', 'Noch keine Wissensbasis.'));
  for (const kb of knowledgeBases) {
    const box = el('div', 'rag-kb');
    const head = el('div', 'rag-head');
    const add = el('button', 'small', 'Dokumente hinzufügen …');
    add.type = 'button';
    const input = el('input');
    Object.assign(input, { type: 'file', multiple: true, hidden: true });
    add.addEventListener('click', () => input.click());
    input.addEventListener('change', () => {
      addDocuments(kb, [...input.files], box);
      input.value = '';
    });
    const del = el('button', 'ghost small', 'Löschen');
    del.type = 'button';
    del.addEventListener('click', () => deleteKnowledgeBase(kb));
    head.append(el('strong', null, kb.name), el('span', 'hint', `${plural(kb.docs.length, 'Dokument', 'Dokumente')} · ${kb.embed_model}`), add);
    if (state.user.admin && state.config.code?.roots?.length) {
      const folder = el('button', 'ghost small', 'Code-Ordner …');
      folder.type = 'button';
      folder.title = 'Quelltext aus einem Ordner auf dem Server einlesen (aufgeteilt nach Funktionen)';
      folder.addEventListener('click', () => addCodeFolder(kb, box));
      head.append(folder);
    }
    head.append(del, input);
    box.append(head);
    for (const doc of kb.docs) {
      const row = el('div', 'rag-doc');
      const x = el('button', 'ghost attach-x', '×');
      x.type = 'button';
      x.title = 'Dokument entfernen';
      x.addEventListener('click', () => removeDocument(kb, doc));
      const details = [doc.pages && plural(doc.pages, 'Seite', 'Seiten'), plural(doc.chunks, 'Abschnitt', 'Abschnitte')]
        .filter(Boolean).join(', ');
      row.append(el('span', null, `📄 ${doc.name}`), el('span', 'hint', details), x);
      box.append(row);
    }
    list.append(box);
  }
}

// Uploads and indexes the files one after another, with progress per file.
async function addDocuments(kb, files, box) {
  for (const file of files) {
    const row = el('div', 'rag-doc hint', `${file.name}: wird hochgeladen …`);
    box.append(row);
    try {
      const meta = await uploadFile(file);
      await stream(`/api/rag/${kb.id}/documents`, { upload_id: meta.id }, (ev) => {
        if (ev.type === 'queue' && ev.position) row.textContent = `${file.name}: ${queueText(ev.position)}`;
        else if (ev.type === 'progress') row.textContent = `${file.name}: ${ev.done} von ${ev.total} Abschnitten eingelesen …`;
        else if (ev.type === 'error') throw new Error(ev.error);
      });
    } catch (e) {
      row.className = 'rag-doc error';
      row.textContent = `${file.name}: ${e.message}`;
      continue;
    }
    row.remove();
    await loadKnowledgeBases();
  }
}

async function removeDocument(kb, doc) {
  if (!confirm(`„${doc.name}“ aus „${kb.name}“ entfernen?`)) return;
  try {
    await api(`/api/rag/${kb.id}/documents/${doc.id}`, { method: 'DELETE' });
  } catch (e) {
    $('rag-msg').textContent = `Fehler: ${e.message}`;
  }
  loadKnowledgeBases();
}

async function deleteKnowledgeBase(kb) {
  if (!confirm(`Wissensbasis „${kb.name}“ mit allen eingelesenen Dokumenten löschen?`)) return;
  try {
    await api(`/api/rag/${kb.id}`, { method: 'DELETE' });
  } catch (e) {
    $('rag-msg').textContent = `Fehler: ${e.message}`;
  }
  loadKnowledgeBases();
}

$('rag-create').addEventListener('click', async () => {
  const name = $('rag-name').value.trim();
  if (!name) return void ($('rag-msg').textContent = 'Bitte einen Namen eingeben.');
  try {
    await api('/api/rag', { method: 'POST', body: { name } });
    $('rag-name').value = '';
    $('rag-msg').textContent = `„${name}“ angelegt. Jetzt Dokumente hinzufügen.`;
  } catch (e) {
    $('rag-msg').textContent = `Fehler: ${e.message}`;
  }
  loadKnowledgeBases();
});

$('web-test').addEventListener('click', async () => {
  const msg = $('web-msg');
  msg.textContent = 'teste …';
  try {
    const { count, first } = await api('/api/web/test', { method: 'POST' });
    msg.textContent = count ? `OK: ${count} Ergebnisse, z. B. „${first}“` : 'Verbunden, aber keine Ergebnisse.';
  } catch (e) {
    msg.textContent = `Fehler: ${e.message}`;
  }
  renderWebAvailability();
});

// The chat's "Websuche" box only makes sense with a SearXNG URL.
function renderWebAvailability() {
  const configured = !!state.config.web.searxng_url;
  $('chat-web').disabled = !configured;
  $('chat-web').closest('label').title = configured ? '' : 'Erst unter Einstellungen → Websuche eine SearXNG-URL eintragen.';
}

// Called from app.js once the config is loaded.
function initRag() {
  $('rag-topk').value = state.config.rag.top_k;
  $('web-url').value = state.config.web.searxng_url;
  renderEmbedModels();
  autosave($('rag-model'), 'rag', 'embed_model');
  autosave($('rag-topk'), 'rag', 'top_k', { parse: (v) => {
    const n = Number(v);
    if (!Number.isInteger(n) || n < 1 || n > 20) throw new Error('1 bis 20');
    return n;
  } });
  autosave($('web-url'), 'web', 'searxng_url', { parse: (v) => v.trim() });
  $('web-url').addEventListener('input', () => setTimeout(renderWebAvailability, 700));
  renderWebAvailability();
  loadKnowledgeBases();
}
