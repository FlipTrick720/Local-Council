'use strict';

// Chat tab extras: search across chats, rename, export, automatic titles, and the chat list drawer on phones.

// ---------- Search ----------

let searchTimer = null;

async function runSearch() {
  const q = $('chat-search').value.trim();
  if (!q) {
    chatState.search = null;
  } else {
    try {
      chatState.search = { q, results: await api(`/api/chats/search?q=${encodeURIComponent(q)}`) };
    } catch (e) {
      setChatStatus(`Suche fehlgeschlagen: ${e.message}`, 'err');
      return;
    }
  }
  renderChatList();
}

// The snippet as text with the matches marked (no innerHTML: chat text is untrusted).
function highlighted(text, q) {
  const out = el('span', 'snippet');
  const lower = text.toLowerCase();
  const needle = q.toLowerCase();
  let pos = 0;
  for (let i = lower.indexOf(needle); i >= 0 && needle; i = lower.indexOf(needle, pos)) {
    out.append(text.slice(pos, i), el('mark', null, text.slice(i, i + needle.length)));
    pos = i + needle.length;
  }
  out.append(text.slice(pos));
  return out;
}

// Scrolls to a message (from a search hit) and lights it up briefly.
function revealMessage(index) {
  const node = chatState.views[index]?.node;
  if (!node) return;
  node.scrollIntoView({ block: 'center' });
  node.classList.add('flash');
  setTimeout(() => node.classList.remove('flash'), 2000);
}

$('chat-search').addEventListener('input', () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(runSearch, 250);
});
$('chat-search').addEventListener('keydown', (e) => {
  if (e.key === 'Escape') {
    $('chat-search').value = '';
    runSearch();
  }
});

// ---------- Rename ----------

async function renameChat(c) {
  const title = prompt('Neuer Titel:', c.title)?.trim();
  if (!title) return;
  const current = chatState.current;
  if (current?.id === c.id) Object.assign(current, { title, title_source: 'manual' });
  if (chatState.list.some((x) => x.id === c.id)) {
    try {
      await api(`/api/chats/${c.id}`, { method: 'PATCH', body: { title, title_source: 'manual' } });
    } catch (e) {
      setChatStatus(`Umbenennen fehlgeschlagen: ${e.message}`, 'err');
    }
  }
  await loadChatList();
}

$('chat-rename').addEventListener('click', () => renameChat(chatState.current));
$('chat-title').addEventListener('dblclick', () => renameChat(chatState.current));

// ---------- Header with title and export ----------

function renderChatHeader(chat) {
  $('chat-title').textContent = chat.title || untitled(chat);
  const stored = chatState.list.some((c) => c.id === chat.id);
  for (const format of ['md', 'json']) {
    const link = $(`chat-export-${format}`);
    link.hidden = !stored;
    link.href = `/api/chats/${chat.id}/export?format=${format}`;
    link.title = `Diesen Chat als ${format === 'md' ? 'Markdown' : 'JSON'} herunterladen`;
  }
  $('chat-rename').hidden = !chat.messages.length;
}

$('chat-export-all').addEventListener('click', () => { location.href = '/api/chats/export'; });

// ---------- Automatic titles ----------

// After the first good answer a model writes a short title (config: chat.auto_title, chat.title_model).
// Until then, and if that fails, the start of the first message is the title.
async function autoTitle(chat, msg) {
  if (state.config.chat.auto_title === false || chat.title_source || msg.error || !msg.content) return;
  const question = chat.messages.find((m) => m.role === 'user');
  const text = question?.content || (question?.attachments || []).map((a) => a.name).join(', ');
  try {
    const { title } = await api('/api/chats/title', {
      method: 'POST', body: { model: msg.model, question: text, answer: msg.content },
    });
    if (!title || chat.deleted || chat.title_source) return;
    Object.assign(chat, { title, title_source: 'auto' });
    await persist(chat);
  } catch {
    // keep the provisional title
  }
}

// ---------- Drawer (phones) ----------

function closeDrawer() {
  $('chat-side').classList.remove('open');
  $('chat-backdrop').hidden = true;
}

$('chat-drawer').addEventListener('click', () => {
  $('chat-side').classList.add('open');
  $('chat-backdrop').hidden = false;
});
$('chat-backdrop').addEventListener('click', closeDrawer);
