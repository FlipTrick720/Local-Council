'use strict';

// Chat tab: conversations stored on the server (data/chats/*.json), streamed replies rendered as Markdown,
// stop/regenerate, and a model per chat. Uses helpers from core.js and renderMarkdown from markdown.js.

const chatState = { current: null, list: [], controller: null, views: [], search: null };
const LAST_CHAT_KEY = 'local-council.chat';

function newChatId() {
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
}

// New chats start with the defaults from config.json (see "Als Standard für neue Chats").
function blankChat() {
  const d = state.config.chat;
  return {
    id: newChatId(), title: '', model: d.model || $('chat-model').value,
    system: d.system || '', options: { ...d.options }, think: d.think ?? null,
    knowledge: d.knowledge || null, tools: !!d.tools, web: !!d.web, memory: d.memory !== false, code: !!d.code,
    messages: [],
  };
}

// Which chat was open last is a per-device convenience, so it lives in the browser (per account).
function rememberChat(id) {
  try { localStorage.setItem(`${LAST_CHAT_KEY}.${state.user.name}`, id); } catch { /* private mode */ }
}

function rememberedChat() {
  try { return localStorage.getItem(`${LAST_CHAT_KEY}.${state.user.name}`); } catch { return null; }
}

function setChatStatus(text, kind = '') {
  const status = $('chat-status');
  status.textContent = text;
  status.className = `chat-status ${kind}`;
}

function shortDate(iso) {
  if (!iso) return '';
  return new Date(iso).toLocaleString('de-DE', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
}

function untitled(chat) {
  return chat.kind === 'council' ? 'Neuer Council' : 'Neuer Chat';
}

function titleFrom(chat) {
  const first = chat.messages.find((m) => m.role === 'user');
  const line = first?.content.split('\n')[0].trim() || first?.attachments?.[0]?.name || '';
  return line.length > 60 ? `${line.slice(0, 57)}…` : line;
}

// ---------- Chat list ----------

async function loadChatList() {
  try {
    chatState.list = await api('/api/chats');
  } catch (e) {
    setChatStatus(`Chats konnten nicht geladen werden: ${e.message}`, 'err');
  }
  if (chatState.search) await runSearch();
  else renderChatList();
}

// The list shows all chats, or the search results (with snippets) while the search box has text.
function renderChatList() {
  const list = $('chat-list');
  list.replaceChildren();
  const current = chatState.current;
  const search = chatState.search;
  let items;
  if (search) {
    items = search.results;
    if (!items.length) list.append(el('p', 'hint', `Nichts gefunden für „${search.q}“.`));
  } else {
    items = [...chatState.list];
    // A new chat is only stored with its first message; until then it is listed here anyway.
    if (current && !items.some((c) => c.id === current.id)) {
      items.unshift({ id: current.id, title: current.title || untitled(current), model: current.model, updated: '', kind: current.kind });
    }
  }
  if (!items.length && !search) list.append(el('p', 'empty', 'Noch keine Chats.'));
  for (const c of items) list.append(chatListItem(c, search?.q));
  renderActive();
}

// The one place that marks what is open: the entry in the chat list, the "+ Neuer Chat/Council" button while an
// unsaved new one is open, and the panel title. Everything that changes chatState.current or the list ends here
// (showChat → renderChatList → renderActive), so they cannot disagree.
function renderActive() {
  const current = chatState.current;
  for (const item of $('chat-list').querySelectorAll('.chat-item')) {
    const on = item.dataset.id === current?.id;
    item.classList.toggle('active', on);
    if (on) item.querySelector('.chat-open').setAttribute('aria-current', 'true');
    else item.querySelector('.chat-open').removeAttribute('aria-current');
  }
  const fresh = !!current && !current.messages.length && !chatState.list.some((c) => c.id === current.id);
  $('chat-new').classList.toggle('is-active', fresh && current.kind !== 'council');
  $('council-new').classList.toggle('is-active', fresh && current.kind === 'council');
  if (current) renderChatHeader(current);
}

function chatListItem(c, q) {
  const item = el('div', 'chat-item');
  item.dataset.id = c.id;
  const open = el('button', 'chat-open');
  open.type = 'button';
  open.append(el('span', 'chat-title', `${c.kind === 'council' ? '🏛 ' : ''}${c.title}`),
    el('span', 'hint', [c.model, shortDate(c.updated)].filter(Boolean).join(' · ')));
  for (const hit of c.hits || []) open.append(highlighted(hit.snippet, q));
  if (c.hit_count > 3) open.append(el('span', 'hint', `… und ${c.hit_count - 3} weitere Treffer`));
  open.addEventListener('click', () => openChat(c.id, c.hits?.[0]?.index));
  const acts = el('span', 'chat-acts');
  for (const [icon, label, action] of [['✎', 'Umbenennen', renameChat], ['×', 'Löschen', deleteChat]]) {
    const b = el('button', 'icon-btn ghost', icon);
    Object.assign(b, { type: 'button', title: `${label}: ${c.title}` });
    b.setAttribute('aria-label', `${label}: ${c.title}`);
    b.addEventListener('click', () => action(c));
    acts.append(b);
  }
  item.append(open, acts);
  return item;
}

// `index`: message to scroll to (search hit).
async function openChat(id, index) {
  closeDrawer();
  if (chatState.current?.id !== id) {
    stopChat();
    try {
      showChat(await api(`/api/chats/${id}`));
    } catch (e) {
      setChatStatus(`Chat konnte nicht geladen werden: ${e.message}`, 'err');
      return;
    }
  }
  if (index != null) revealMessage(index);
}

function newChat() {
  closeDrawer();
  stopChat();
  showChat(blankChat());
  $('chat-text').focus();
}

function showChat(chat) {
  chatState.current = chat;
  rememberChat(chat.id);
  const council = chat.kind === 'council';
  $('chat-model-row').hidden = council;
  $('chat-system-box').hidden = council;
  $('council-setup').hidden = !council;
  $('chat-save').hidden = council;
  if (council) renderCouncilSetup(chat);
  renderModelSelect($('chat-model'), chat.model || state.config.chat.model);
  if (chat.model && $('chat-model').value !== chat.model && !council) {
    setChatStatus(`Das Modell „${chat.model}“ dieses Chats ist nicht installiert. Bitte ein anderes wählen.`, 'err');
  } else {
    setChatStatus('');
  }
  renderChatSettings(chat);
  renderChatMemory();
  renderChatLog();
  renderChatList();
  scheduleContextMeter();
}

async function deleteChat(c) {
  if (!confirm(`Chat „${c.title}“ löschen?`)) return;
  const stored = chatState.list.some((x) => x.id === c.id);
  if (chatState.current?.id === c.id) {
    chatState.current.deleted = true;  // a reply still streaming must not store it again
    stopChat();
    showChat(blankChat());
  }
  if (stored) {
    try {
      await api(`/api/chats/${c.id}`, { method: 'DELETE' });
    } catch (e) {
      setChatStatus(`Löschen fehlgeschlagen: ${e.message}`, 'err');
    }
  }
  await loadChatList();
}

async function persist(chat) {
  if (chat.deleted) return;
  try {
    const saved = await api(`/api/chats/${chat.id}`, { method: 'PUT', body: chat });
    Object.assign(chat, { created: saved.created, updated: saved.updated });
  } catch (e) {
    if (chatState.current === chat) setChatStatus(`Chat konnte nicht gespeichert werden: ${e.message}`, 'err');
  }
  await loadChatList();
}

// ---------- Messages ----------

// Builds one message and returns it with update(), which redraws it from the (mutable) message object.
function messageView(msg) {
  const card = el('div', `card msg ${msg.role}`);
  const head = el('div', 'card-head');
  head.append(el('strong', null, msg.role === 'user' ? 'Du' : msg.council ? `🏛 Council · ${msg.model}` : msg.model || 'Assistent'));
  const thinking = el('details', 'thinking');
  const thinkSummary = el('summary');
  const thinkText = el('div', 'think-text');
  thinking.append(thinkSummary, thinkText);
  const toolBox = el('details', 'tools');
  const sourceBox = el('details', 'sources');
  const proposals = el('div', 'mem-proposals');
  const body = el('div', msg.role === 'user' ? 'plain' : 'md');
  const error = el('div', 'error');
  const foot = el('div', 'msg-foot');
  const stats = el('span', 'stats');
  const actions = el('span', 'msg-actions');
  foot.append(stats, actions);
  card.append(head, toolBox, thinking, body, proposals, sourceBox, error, foot);
  const council = msg.council ? councilView(msg) : null;
  const finalLabel = el('div', 'final-label', 'Finale Antwort des Councils');
  if (council) {
    card.classList.add('council-msg');
    toolBox.before(council.node);
    body.before(finalLabel);
    sourceBox.before(council.judge);
  }
  if (msg.attachments?.length) {
    const files = el('div', 'attach-list');
    for (const a of msg.attachments) files.append(attachmentChip(a));
    body.after(files);
  }

  const update = (streaming = false) => {
    renderToolCalls(toolBox, msg.tools);
    renderSources(sourceBox, msg.sources);
    thinking.hidden = !msg.thinking;
    thinkSummary.textContent = thinkingLabel(msg);
    thinkText.textContent = msg.thinking || '';
    if (thinking.open) thinkText.scrollTop = thinkText.scrollHeight;
    if (msg.role === 'user') body.textContent = msg.content;
    else renderMarkdown(body, streaming ? withoutMemoryMarkers(msg.content) : msg.content, { streaming });
    if (msg.role === 'assistant' && !streaming) attachRuns(body, msg, chatState.current);
    if (msg.role === 'assistant') renderProposals(proposals, msg, chatState.current);
    body.hidden = !msg.content;
    error.hidden = !msg.error;
    error.textContent = msg.error || '';
    stats.textContent = msg.stats ? chatStatsLine(msg.stats) : msg.stopped ? 'abgebrochen' : '';
    finalLabel.hidden = !msg.content;
    council?.update(streaming);
  };
  update();
  return { node: card, update, actions, thinking, council };
}

function renderToolCalls(box, calls) {
  box.hidden = !calls?.length;
  if (box.hidden) return;
  box.replaceChildren(el('summary', null, `🔧 ${calls.length} Werkzeug-Aufruf${calls.length > 1 ? 'e' : ''}: ${[...new Set(calls.map((c) => c.name))].join(', ')}`));
  for (const call of calls) {
    const args = Object.entries(call.arguments || {}).map(([k, v]) => `${k}: ${typeof v === 'string' ? v : JSON.stringify(v)}`).join(', ');
    const row = el('div', 'tool-call');
    row.append(el('code', null, `${call.name}(${args})`), el('div', 'tool-result', call.result ?? '… läuft'));
    box.append(row);
  }
}

// Knowledge base excerpts and web results the answer was based on.
function renderSources(box, sources) {
  box.hidden = !sources?.length;
  if (box.hidden) return;
  const open = box.open;
  box.replaceChildren(el('summary', null, `Quellen (${sources.length})`));
  for (const s of sources) {
    const row = el('div', 'source');
    const label = el('strong', null, `[${s.n}] `);
    row.append(label);
    if (s.kind === 'web') {
      const a = el('a', null, s.name || s.url);
      Object.assign(a, { href: s.url, target: '_blank', rel: 'noopener noreferrer' });
      row.append(a, el('span', 'hint', ` · ${s.date ? new Date(s.date).toLocaleDateString('de-DE') : 'ohne Datum'}`));
      if (s.name && s.name !== s.url) row.append(el('div', 'source-url', s.url));
    } else {
      row.append(`${s.name}${s.page ? `, S. ${s.page}` : ''}`, el('span', 'hint', ` · Ähnlichkeit ${s.score}`));
    }
    row.append(el('div', 'source-text', s.text.length > 300 ? `${s.text.slice(0, 300)} …` : s.text));
    box.append(row);
  }
  box.open = open;
}

function thinkingLabel(msg) {
  if (msg.thinking_time == null) return msg.content || msg.stopped || msg.error ? 'Gedankengang' : 'Denkt nach …';
  return `Gedankengang (${fmt(msg.thinking_time, ' s', 1)})`;
}

function chatStatsLine(s) {
  const parts = [
    s.tps != null && `${fmt(s.tps, ' tok/s', 1)}`,
    `${s.tokens} Tokens`,
    `erstes Token nach ${fmt(s.ttft, ' s', 1)}`,
    `gesamt ${fmt(s.wall_time, ' s', 1)}`,
    s.prompt_tokens && `Kontext ${s.prompt_tokens + s.tokens} Tokens`,
    s.load_time >= 0.5 && `Ladezeit ${fmt(s.load_time, ' s', 1)}`,
  ];
  return parts.filter(Boolean).join(' · ');
}

function renderChatLog() {
  const log = $('chat-log');
  const messages = chatState.current.messages;
  log.replaceChildren();
  chatState.views = messages.map((msg, i) => {
    const view = messageView(msg);
    if (msg.content) view.actions.append(copyButton(() => msg.content));
    if (i === messages.length - 1) {
      const again = el('button', 'ghost needs-idle',
        msg.role === 'user' ? 'Antwort generieren' : msg.error ? 'Erneut versuchen' : 'Neu generieren');
      again.type = 'button';
      again.title = msg.role === 'user' ? 'Mit dem oben gewählten Modell antworten'
        : 'Diese Antwort verwerfen und neu erzeugen (mit dem oben gewählten Modell)';
      again.disabled = !!chatState.controller;
      again.addEventListener('click', regenerate);
      view.actions.append(again);
    }
    log.append(view.node);
    return view;
  });
  if (!messages.length) {
    log.append(el('p', 'hint chat-empty', chatState.current.kind === 'council'
      ? 'Neuer Council: Mitglieder, Rollen und Runden wählen, dann die Frage stellen.' : 'Neuer Chat: Modell wählen und lostippen.'));
  }
}

// Send and Stop share one slot in the composer: only the usable one is shown.
function setChatBusy(busy) {
  $('chat-send').disabled = busy;
  $('chat-send').hidden = busy;
  $('chat-stop').hidden = !busy;
  document.querySelectorAll('#tab-chat .needs-idle').forEach((b) => { b.disabled = busy; });
}

function nearBottom() {
  return window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 150;
}

function stopChat() {
  chatState.controller?.abort();
}

// What Ollama gets: the conversation without failed (empty) replies. Attachments go as upload ids; Council
// answers also carry the members' final positions.
function apiMessages(chat) {
  return chat.messages
    .filter((m) => m.content || m.attachments?.length)
    .map(({ role, content, attachments, members }) => ({
      role, content, attachments: (attachments || []).map((a) => a.id),
      members: (members || []).map(({ label, role: r, content: c }) => ({ label, role: r, content: c })),
    }));
}

function respond(chat) {
  return chat.kind === 'council' ? generateCouncil(chat) : generate(chat);
}

// Streams a new assistant reply for the end of `chat` and stores the chat afterwards, also on stop/error.
async function generate(chat) {
  const model = $('chat-model').value;
  chat.model = model;
  const messages = apiMessages(chat);
  // Store the question right away, so a reload or closed tab during the reply does not lose it.
  if (!chat.title) chat.title = titleFrom(chat);
  persist(chat);
  $('chat-settings-msg').textContent = '';
  const msg = { role: 'assistant', model, content: '', thinking: '' };
  chat.messages.push(msg);
  renderChatLog();
  const view = chatState.views.at(-1);
  window.scrollTo(0, document.documentElement.scrollHeight);

  const controller = new AbortController();
  chatState.controller = controller;
  setChatBusy(true);
  const visible = () => chatState.current === chat;

  // Redraw at most every 80 ms; re-parsing Markdown for every token would be wasteful.
  let redraw = null;
  const scheduleRedraw = () => {
    redraw ??= setTimeout(() => {
      redraw = null;
      const follow = nearBottom();
      view.update(true);
      if (follow) window.scrollTo(0, document.documentElement.scrollHeight);
    }, 80);
  };

  const started = Date.now();
  let phase = 'wait';
  let chunks = 0;
  let firstToken = null;
  let thinkStart = null;
  let statusText = null;  // from the server: knowledge base / web search / tool in progress
  // Live speed: Ollama sends roughly one token per chunk, so chunks per second ≈ tokens per second.
  const tick = () => {
    if (!visible()) return;
    const now = Date.now();
    const s = Math.round((now - started) / 1000);
    const tps = firstToken && now - firstToken > 500 ? ` · ~${(chunks / ((now - firstToken) / 1000)).toFixed(1)} tok/s` : '';
    const text = statusText ? `${statusText} ${s} s`
      : phase === 'wait' ? `Warte auf ${model} … ${s} s (wird ggf. erst in den Speicher geladen)`
      : phase === 'thinking' ? `${model} denkt nach … ${s} s · ${chunks} Tokens${tps}`
      : `${model} schreibt … ${s} s · ${chunks} Tokens${tps}`;
    setChatStatus(text, 'busy');
  };
  tick();
  const timer = setInterval(tick, 1000);

  try {
    const body = {
      model, messages, system: chat.system || '', options: chat.options || {}, think: chat.think ?? null,
      knowledge: chat.knowledge || null, tools: !!chat.tools, web: !!chat.web, memory: memoryInUse(chat),
      code: !!chat.code,
    };
    await stream('/api/chat', body, (ev) => {
      if (ev.type === 'token') {
        firstToken ??= Date.now();
        statusText = null;
        chunks++;
        if (ev.kind === 'thinking') {
          if (phase !== 'thinking') {
            thinkStart = Date.now();
            view.thinking.open = true;  // show the reasoning live …
          }
          msg.thinking += ev.text;
          phase = 'thinking';
        } else {
          if (phase === 'thinking') {
            msg.thinking_time = (Date.now() - thinkStart) / 1000;
            view.thinking.open = false;  // … and fold it away once the answer starts
          }
          msg.content += ev.text;
          phase = 'content';
        }
        scheduleRedraw();
      } else if (ev.type === 'status') {
        statusText = ev.text;
        tick();
      } else if (ev.type === 'queue') {
        statusText = queueText(ev.position) || null;
        tick();
      } else if (ev.type === 'sources') {
        msg.sources = ev.sources;
        scheduleRedraw();
      } else if (ev.type === 'tool_call') {
        (msg.tools ??= []).push({ name: ev.name, arguments: ev.arguments });
        statusText = `Werkzeug: ${ev.name} …`;
        tick();
        scheduleRedraw();
      } else if (ev.type === 'tool_result') {
        const call = msg.tools?.findLast((c) => c.name === ev.name && c.result == null);
        if (call) call.result = ev.result;
        statusText = null;
        scheduleRedraw();
      } else if (ev.type === 'done') {
        msg.stats = ev.stats;
        msg.content = ev.content ?? msg.content;  // without <<remember: …>> lines
      } else if (ev.type === 'memory_proposals') {
        msg.memory_proposals = ev.items.map((text) => ({ text, state: 'open' }));
      } else if (ev.type === 'error') {
        msg.error = ev.error;
        if (/does not support thinking/.test(ev.error)) {
          msg.error += ' – Dieses Modell kann nicht „denken“. Unter Einstellungen „Denken“ auf „Modell-Standard“ stellen.';
        }
      }
    }, controller.signal);
    if (!msg.stats && !msg.error) msg.error = 'Antwort unvollständig: Die Verbindung wurde unterbrochen.';
  } catch (e) {
    if (e.name === 'AbortError') msg.stopped = true;
    else msg.error = e instanceof TypeError ? 'Server nicht erreichbar. Läuft die App noch?' : e.message;
  } finally {
    clearInterval(timer);
    clearTimeout(redraw);
    chatState.controller = null;
    setChatBusy(false);
    if (!msg.thinking) delete msg.thinking;
    if (msg.thinking && msg.thinking_time == null && thinkStart) msg.thinking_time = (Date.now() - thinkStart) / 1000;
    if (!chat.title) chat.title = titleFrom(chat);
    if (visible()) {
      renderChatLog();
      if (msg.error) setChatStatus(`Fehler: ${msg.error}`, 'err');
      else if (msg.stopped) setChatStatus('Abgebrochen. Die Teilantwort wurde gespeichert.');
      else setChatStatus(`Fertig · ${chatStatsLine(msg.stats)}`, 'ok');
    }
    refreshLoaded();
    await persist(chat);
    scheduleContextMeter();
    autoTitle(chat, msg);
  }
}

function checkModel() {
  if (chatState.current.kind === 'council' || $('chat-model').value) return true;
  setChatStatus('Bitte oben ein Modell auswählen. Läuft Ollama?', 'err');
  return false;
}

async function sendChat() {
  const text = $('chat-text').value.trim();
  if ((!text && !pendingFiles.length) || chatState.controller || !checkModel()) return;
  let attachments;
  try {
    attachments = await takePending();
  } catch (e) {
    setChatStatus(e.message, 'err');
    return;
  }
  $('chat-text').value = '';
  const msg = { role: 'user', content: text };
  if (attachments.length) msg.attachments = attachments;
  chatState.current.messages.push(msg);
  await respond(chatState.current);
}

async function regenerate() {
  const chat = chatState.current;
  if (chatState.controller || !checkModel()) return;
  if (chat.messages.at(-1)?.role === 'assistant') chat.messages.pop();
  if (chat.messages.length) await respond(chat);
}

// ---------- Wiring ----------

$('chat-form').addEventListener('submit', (e) => { e.preventDefault(); sendChat(); });
if (matchMedia('(max-width: 760px)').matches) $('chat-text').placeholder = 'Nachricht …';  // the long hint does not fit
$('chat-text').addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); sendChat(); }
  if (e.key === 'Escape') stopChat();
});
$('chat-stop').addEventListener('click', stopChat);
$('chat-new').addEventListener('click', newChat);
$('chat-model').addEventListener('change', () => {
  const chat = chatState.current;
  chat.model = $('chat-model').value;
  setChatStatus(`Die nächsten Antworten in diesem Chat kommen von ${chat.model}.`);
  if (chat.messages.length) persist(chat);
  else renderChatList();
});

// Called once from app.js after config and models are loaded: reopens the chat that was open last.
async function initChat() {
  await loadChatList();
  const last = rememberedChat();
  if (last && chatState.list.some((c) => c.id === last)) await openChat(last);
  else showChat(blankChat());
}
