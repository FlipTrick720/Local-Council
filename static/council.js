'use strict';

// Council sessions in the chat tab: several models (members, each with an optional role) answer, discuss each
// other's answers and one model writes the final answer (/api/council). A session is a chat with kind "council":
// its setup is stored in chat.council, each answer keeps the rounds in msg.council, the members' final positions in
// msg.members (sent back as context for follow-up questions) and the judge's scores in msg.judge.

const ROLE_PRESETS = [
  { name: 'Kritiker', system: 'Suche gezielt nach Fehlern, Lücken und schwachen Argumenten und benenne sie klar.' },
  { name: 'Skeptiker', system: 'Hinterfrage Annahmen und Behauptungen. Frag, was dagegen spricht, und verlange Belege.' },
  { name: 'Faktenprüfer', system: 'Prüfe Fakten, Zahlen und Daten. Nutze die Websuche, wenn verfügbar, und nenne Quellen.' },
  { name: 'Praktiker', system: 'Achte auf praktische Umsetzbarkeit, Aufwand, Risiken und konkrete nächste Schritte.' },
  { name: 'Zusammenfasser', system: 'Fasse die Positionen knapp zusammen und arbeite Konsens und Dissens heraus.' },
];
const MAX_MEMBERS = 6;
const JUDGE_CRITERIA = { correctness: 'Korrektheit', completeness: 'Vollständigkeit', clarity: 'Klarheit', usefulness: 'Nützlichkeit' };

let councilRun = null;  // id of the running Council, for "skip to the final answer"
let councilTimer = null;

// ---------- Setup ----------

function councilDefaults() {
  const c = state.config.council || {};
  const members = (c.members || []).map((m) => (typeof m === 'string' ? { model: m, role: '', system: '' } : { ...m }));
  const names = modelNames();
  while (members.length < 2) members.push({ model: names[members.length] || names[0] || '', role: '', system: '' });
  return {
    members, synthesizer: c.synthesizer || '', rounds: c.rounds ?? 2, parallel: !!c.parallel,
    tool_steps: c.tool_steps ?? 3, judge: c.judge || '',
  };
}

function blankCouncil() {
  return { ...blankChat(), kind: 'council', system: '', council: councilDefaults() };
}

function newCouncil() {
  closeDrawer();
  stopChat();
  showChat(blankCouncil());
  $('chat-text').focus();
}

function renderCouncilSetup(chat) {
  const cfg = chat.council;
  const box = $('cs-members');
  box.replaceChildren(...cfg.members.map((m, i) => memberRow(chat, m, i)));
  $('cs-add').disabled = cfg.members.length >= MAX_MEMBERS;
  renderModelSelect($('cs-synth'), cfg.synthesizer, '(erstes Mitglied)');
  renderModelSelect($('cs-judge'), cfg.judge, '(keine Bewertung)');
  $('cs-rounds').value = cfg.rounds;
  $('cs-steps').value = cfg.tool_steps;
  $('cs-parallel').checked = cfg.parallel;
}

function memberRow(chat, m, i) {
  const row = el('div', 'cs-member');
  const model = el('select');
  renderModelSelect(model, m.model);
  m.model = model.value;
  model.addEventListener('change', () => { m.model = model.value; councilChanged(chat); });

  const role = el('select');
  role.append(new Option('(keine Rolle)', ''));
  for (const p of ROLE_PRESETS) role.append(new Option(p.name, p.name));
  role.append(new Option('Eigene Rolle …', '*'));
  const preset = ROLE_PRESETS.find((p) => p.name === m.role && p.system === m.system);
  role.value = !m.role && !m.system ? '' : preset ? preset.name : '*';

  const custom = el('div', 'cs-custom');
  custom.hidden = role.value !== '*';
  const name = el('input');
  Object.assign(name, { placeholder: 'Rolle, z. B. Jurist', value: m.role, maxLength: 60 });
  const system = el('textarea');
  Object.assign(system, { rows: 2, placeholder: 'Anweisung für dieses Mitglied', value: m.system });
  custom.append(name, system);
  role.addEventListener('change', () => {
    const p = ROLE_PRESETS.find((x) => x.name === role.value);
    if (p) Object.assign(m, { role: p.name, system: p.system });
    else if (role.value === '') Object.assign(m, { role: '', system: '' });
    name.value = m.role;
    system.value = m.system;
    custom.hidden = role.value !== '*';
    councilChanged(chat);
  });
  name.addEventListener('input', () => { m.role = name.value; councilChanged(chat); });
  system.addEventListener('input', () => { m.system = system.value; councilChanged(chat); });

  const del = el('button', 'ghost small', '×');
  Object.assign(del, { type: 'button', title: 'Mitglied entfernen', disabled: chat.council.members.length <= 2 });
  del.addEventListener('click', () => {
    chat.council.members.splice(i, 1);
    renderCouncilSetup(chat);
    councilChanged(chat);
  });
  row.append(el('strong', null, String.fromCharCode(65 + i)), model, role, del, custom);
  return row;
}

// Stores the setup with the chat (once it has messages) shortly after the last change.
function councilChanged(chat) {
  scheduleContextMeter();
  clearTimeout(councilTimer);
  if (!chat.messages.length) return;
  councilTimer = setTimeout(() => persist(chat), 600);
}

function bindCouncilField(id, key, read) {
  $(id).addEventListener('change', () => {
    const chat = chatState.current;
    if (chat?.kind !== 'council') return;
    chat.council[key] = read($(id));
    councilChanged(chat);
  });
}

bindCouncilField('cs-synth', 'synthesizer', (e) => e.value);
bindCouncilField('cs-judge', 'judge', (e) => e.value);
bindCouncilField('cs-rounds', 'rounds', (e) => Math.max(0, Math.min(5, Math.round(Number(e.value) || 0))));
bindCouncilField('cs-steps', 'tool_steps', (e) => Math.max(1, Math.min(5, Math.round(Number(e.value) || 3))));
bindCouncilField('cs-parallel', 'parallel', (e) => e.checked);

$('cs-add').addEventListener('click', () => {
  const chat = chatState.current;
  if (chat.council.members.length >= MAX_MEMBERS) return;
  chat.council.members.push({ model: chat.council.members.at(-1)?.model || modelNames()[0] || '', role: '', system: '' });
  renderCouncilSetup(chat);
  councilChanged(chat);
});

$('cs-save').addEventListener('click', (e) => flash(e.target, async () => {
  const council = structuredClone(chatState.current.council);
  await api('/api/config', { method: 'PUT', body: { council } });
  Object.assign(state.config.council, council);
}));

$('council-new').addEventListener('click', newCouncil);

// ---------- Display of the rounds ----------

function stageTitle(st, rounds) {
  return st.stage === 'initial' ? 'Erste Antworten' : `Diskussionsrunde ${st.round} von ${rounds}`;
}

function answerStats(s) {
  if (!s) return '';
  return [s.tps != null && fmt(s.tps, ' tok/s', 1), fmt(s.wall_time, ' s', 1)].filter(Boolean).join(' · ');
}

// One member's answer in one round; redrawn only when something changed.
function memberCard(member) {
  const card = el('div', 'card');
  const head = el('div', 'card-head');
  const badge = el('span', 'badge', 'läuft …');
  head.append(el('strong', null, `Mitglied ${member.label}`),
    el('span', 'sub', [member.role, member.model].filter(Boolean).join(' · ')), badge);
  const tools = el('details', 'tools');
  const thinking = el('details', 'thinking');
  const thinkText = el('div', 'think-text');
  thinking.append(el('summary', null, 'Gedankengang'), thinkText);
  const body = el('div', 'md');
  const error = el('div', 'error');
  card.append(head, tools, thinking, body, error);
  let last = '';
  return {
    node: card,
    update(a, streaming) {
      const sig = `${a.content.length}|${a.thinking.length}|${a.tools?.length}|${a.tools?.at(-1)?.result != null}|${a.error}|${!!a.stats}`;
      if (sig === last) return;
      last = sig;
      renderToolCalls(tools, a.tools);
      thinking.hidden = !a.thinking;
      thinkText.textContent = a.thinking;
      renderMarkdown(body, a.content, { streaming: streaming && !a.stats });
      error.hidden = !a.error;
      error.textContent = a.error || '';
      badge.textContent = a.error ? 'Fehler' : a.stats ? answerStats(a.stats) : streaming ? 'läuft …' : 'abgebrochen';
      badge.classList.toggle('err', !!a.error);
    },
  };
}

// The collapsible "Beratung" box of a Council answer, plus the judge's table below the final answer.
function councilView(msg) {
  const box = el('details', 'council-box');
  const summary = el('summary');
  const warnings = el('div');
  const stages = el('div');
  box.append(summary, warnings, stages);
  const judgeBox = el('div', 'judge-table');
  const stageNodes = new Map();
  const cards = new Map();
  const update = (streaming = false) => {
    const c = msg.council;
    const discussed = c.stages.filter((s) => s.stage === 'discussion').length;
    summary.textContent = `Beratung: ${plural(c.members.length, 'Mitglied', 'Mitglieder')}, `
      + `${plural(discussed, 'Diskussionsrunde', 'Diskussionsrunden')}${c.skipped ? ' (abgekürzt)' : ''}`;
    warnings.replaceChildren(...c.warnings.map((w) => el('div', 'council-warn', w)));
    for (const st of c.stages) {
      const key = `${st.stage}-${st.round}`;
      if (!stageNodes.has(key)) {
        const node = el('div', 'council-stage');
        const grid = el('div', 'cards');
        node.append(el('h4', null, stageTitle(st, c.rounds)), grid);
        stages.append(node);
        stageNodes.set(key, grid);
      }
      for (const [label, a] of Object.entries(st.answers)) {
        const cardKey = `${key}-${label}`;
        if (!cards.has(cardKey)) {
          const member = c.members.find((m) => m.label === label) || { label, model: a.model };
          cards.set(cardKey, memberCard(member));
          stageNodes.get(key).append(cards.get(cardKey).node);
        }
        cards.get(cardKey).update(a, streaming);
      }
    }
    renderJudge(judgeBox, msg);
  };
  return { node: box, judge: judgeBox, update };
}

function renderJudge(box, msg) {
  const j = msg.judge;
  box.hidden = !j;
  if (!j) return;
  const names = (label) => (label === 'final' ? `Finale Antwort (${j.synthesizer || msg.model})`
    : `Mitglied ${label} allein (${j.members?.[label] || '?'})`);
  const table = el('table');
  const head = el('tr');
  for (const h of ['Antwort', ...Object.values(JUDGE_CRITERIA), 'Ø']) head.append(el('th', null, h));
  table.append(head);
  const rows = Object.entries(j.scores).sort((a, b) => (b[1].total ?? 0) - (a[1].total ?? 0));
  for (const [label, s] of rows) {
    const tr = el('tr');
    tr.append(el('td', label === j.best ? 'best' : null, `${names(label)}${label === j.best ? ' ★' : ''}`));
    for (const c of Object.keys(JUDGE_CRITERIA)) tr.append(el('td', null, s[c] ?? '–'));
    tr.append(el('td', null, s.total ?? '–'));
    table.append(tr);
  }
  const wrap = el('div', 'table-wrap');
  wrap.append(table);
  box.replaceChildren(el('strong', null, `Bewertung durch ${j.model}`), wrap);
  if (j.comment) box.append(el('p', 'hint', j.comment));
}

// ---------- Asking the Council ----------

function councilStage(msg, ev) {
  return msg.council.stages.find((s) => s.stage === ev.stage && s.round === ev.round);
}

async function generateCouncil(chat) {
  const cfg = chat.council;
  const members = cfg.members.filter((m) => m.model);
  if (members.length < 2) return setChatStatus('Ein Council braucht mindestens zwei Mitglieder mit Modell.', 'err');
  const synthesizer = cfg.synthesizer || members[0].model;
  chat.model = synthesizer;
  const messages = apiMessages(chat);
  if (!chat.title) chat.title = titleFrom(chat);
  persist(chat);
  const msg = {
    role: 'assistant', model: synthesizer, content: '', thinking: '',
    council: { members: [], stages: [], warnings: [], rounds: cfg.rounds },
  };
  chat.messages.push(msg);
  renderChatLog();
  const view = chatState.views.at(-1);
  view.council.node.open = true;  // show the discussion live; it can be folded away
  window.scrollTo(0, document.documentElement.scrollHeight);

  const controller = new AbortController();
  chatState.controller = controller;
  setChatBusy(true);
  $('council-skip').hidden = false;
  $('council-skip').disabled = true;
  const visible = () => chatState.current === chat;
  let redraw = null;
  const scheduleRedraw = () => {
    redraw ??= setTimeout(() => {
      redraw = null;
      const follow = nearBottom();
      view.update(true);
      if (follow) window.scrollTo(0, document.documentElement.scrollHeight);
    }, 120);
  };
  const started = Date.now();
  let phase = 'Wartet';
  let statusText = null;
  let thinkStart = null;
  const tick = () => {
    if (visible()) setChatStatus(`${statusText || phase} … ${Math.round((Date.now() - started) / 1000)} s`, 'busy');
  };
  tick();
  const timer = setInterval(tick, 1000);

  try {
    const body = {
      messages, members, synthesizer, rounds: cfg.rounds, parallel: cfg.parallel, tool_steps: cfg.tool_steps,
      judge: cfg.judge || null, options: chat.options || {}, think: chat.think ?? null,
      knowledge: chat.knowledge || null, tools: !!chat.tools, web: !!chat.web, memory: memoryInUse(chat), chat_id: chat.id,
    };
    await stream('/api/council', body, (ev) => {
      const c = msg.council;
      if (ev.type === 'queue') {
        statusText = queueText(ev.position) || null;
      } else if (ev.type === 'start') {
        councilRun = ev.run;
        c.members = ev.members;
        $('council-skip').disabled = false;
      } else if (ev.type === 'phase') {
        statusText = null;
        if (ev.stage === 'synthesis') {
          phase = `${synthesizer} schreibt die finale Antwort`;
          $('council-skip').disabled = true;
        } else if (ev.stage === 'judge') {
          phase = `${cfg.judge} bewertet die Antworten`;
        } else {
          phase = ev.stage === 'initial' ? 'Erste Antworten' : `Diskussionsrunde ${ev.round} von ${cfg.rounds}`;
          const answers = {};
          for (const label of ev.members) {
            answers[label] = { model: c.members.find((m) => m.label === label)?.model, content: '', thinking: '' };
          }
          c.stages.push({ stage: ev.stage, round: ev.round, answers });
        }
      } else if (ev.type === 'token' && ev.member === 'final') {
        if (ev.kind === 'thinking') {
          thinkStart ??= Date.now();
          msg.thinking += ev.text;
        } else {
          if (thinkStart && msg.thinking_time == null) msg.thinking_time = (Date.now() - thinkStart) / 1000;
          msg.content += ev.text;
        }
      } else if (ev.type === 'token' && ev.member !== 'judge') {
        const a = councilStage(msg, ev)?.answers[ev.member];
        if (a) a[ev.kind === 'thinking' ? 'thinking' : 'content'] += ev.text;
      } else if (ev.type === 'tool_call') {
        const a = councilStage(msg, ev)?.answers[ev.member];
        if (a) (a.tools ??= []).push({ name: ev.name, arguments: ev.arguments });
        statusText = `Mitglied ${ev.member}: ${ev.name}`;
      } else if (ev.type === 'tool_result') {
        const call = councilStage(msg, ev)?.answers[ev.member]?.tools?.findLast((t) => t.name === ev.name && t.result == null);
        if (call) call.result = ev.result;
        statusText = null;
      } else if (ev.type === 'sources') {
        msg.sources = ev.sources;
      } else if (ev.type === 'status') {
        statusText = ev.member ? `Mitglied ${ev.member}: ${ev.text}` : ev.text;
      } else if (ev.type === 'warning') {
        if (!c.warnings.includes(ev.text)) c.warnings.push(ev.text);
      } else if (ev.type === 'skipped') {
        c.skipped = true;
      } else if (ev.type === 'positions') {
        msg.members = ev.members;
      } else if (ev.type === 'done') {
        if (ev.member === 'final') Object.assign(msg, { stats: ev.stats, content: ev.content });
        const a = councilStage(msg, ev)?.answers[ev.member];
        if (a) Object.assign(a, { content: ev.content, stats: ev.stats });
      } else if (ev.type === 'memory_proposals') {
        msg.memory_proposals = ev.items.map((text) => ({ text, state: 'open' }));
      } else if (ev.type === 'judge') {
        msg.judge = { model: ev.model, scores: ev.scores, best: ev.best, comment: ev.comment, members: ev.members,
          synthesizer: ev.synthesizer };
      } else if (ev.type === 'error') {
        const a = councilStage(msg, ev)?.answers[ev.member];
        if (a) a.error = ev.error;
        else if (ev.member === 'judge') c.warnings.push(ev.error);
        else msg.error = ev.error;
      }
      tick();
      scheduleRedraw();
    }, controller.signal);
    if (!msg.stats && !msg.error) msg.error = 'Antwort unvollständig: Die Verbindung wurde unterbrochen.';
  } catch (e) {
    if (e.name === 'AbortError') msg.stopped = true;
    else msg.error = e instanceof TypeError ? 'Server nicht erreichbar. Läuft die App noch?' : e.message;
  } finally {
    clearInterval(timer);
    clearTimeout(redraw);
    councilRun = null;
    $('council-skip').hidden = true;
    chatState.controller = null;
    setChatBusy(false);
    if (!msg.thinking) delete msg.thinking;
    if (!chat.title) chat.title = titleFrom(chat);
    if (visible()) {
      renderChatLog();
      if (msg.error) setChatStatus(`Fehler: ${msg.error}`, 'err');
      else if (msg.stopped) setChatStatus('Abgebrochen. Was bis dahin kam, wurde gespeichert.');
      else setChatStatus(`Fertig · finale Antwort: ${chatStatsLine(msg.stats)}`, 'ok');
    }
    refreshLoaded();
    await persist(chat);
    scheduleContextMeter();
    autoTitle(chat, msg);
  }
}

$('council-skip').addEventListener('click', async () => {
  if (!councilRun) return;
  $('council-skip').disabled = true;
  try {
    await api(`/api/council/${councilRun}/skip`, { method: 'POST' });
    setChatStatus('Die Diskussion wird beendet, die finale Antwort folgt …', 'busy');
  } catch (e) {
    setChatStatus(`Überspringen nicht möglich: ${e.message}`, 'err');
  }
});

// Context estimate for the meter: earlier questions, final answers and positions (as the server shortens them),
// the new question, and the other members' answers that discussion rounds add.
function councilUsage(chat) {
  let chars = 0;
  for (const m of chat.messages) {
    chars += Math.min(m.content?.length || 0, 3000);
    for (const p of m.members || []) chars += Math.min(p.content?.length || 0, 1500);
  }
  let tokens = estimateTokens(chars) + messageTokens({ content: $('chat-text').value, attachments: pendingMetas() });
  for (const m of chat.messages) {
    for (const a of m.attachments || []) tokens += a.kind === 'image' ? IMAGE_TOKENS : Math.min(estimateTokens(a.chars || 0), 860);
  }
  if (chat.council.rounds) tokens += chat.council.members.length * 600;
  return { tokens: tokens + memoryTokens(chat), cutBefore: false };
}
