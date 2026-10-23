'use strict';

// Benchmark tab (admins): Council vs. single model on a question set (/api/benchmark, see app/benchmark.py).
// Set up and start runs, edit the questions, follow the progress, read the results and vote yourself.

const CATEGORY_NAMES = { reasoning: 'Logik', factual: 'Wissen', math: 'Mathe', coding: 'Code', writing: 'Schreiben' };
const bench = { members: [], runs: [], open: null, questions: [], poll: null };

// ---------- Setup ----------

function renderBenchSetup() {
  if (!bench.members.length) {
    const names = modelNames();
    bench.members = [0, 1].map((i) => ({ model: names[i] || names[0] || '', role: '', system: '' }));
  }
  const box = $('bm-members');
  const redraw = () => renderBenchSetup();
  box.replaceChildren(...bench.members.map((m, i) => memberRow(bench.members, m, i, { changed: renderBenchWarning, redraw })));
  $('bm-add').disabled = bench.members.length >= MAX_MEMBERS;
  for (const [id, empty] of [['bm-baseline'], ['bm-synth', '(erstes Mitglied)'], ['bm-judge']]) {
    renderModelSelect($(id), $(id).value, empty);
  }
  renderBenchWarning();
}

function benchSetup() {
  const members = bench.members.filter((m) => m.model);
  return {
    baseline: $('bm-baseline').value, members, synthesizer: $('bm-synth').value || members[0]?.model || '',
    rounds: Number($('bm-rounds').value) || 0, parallel: $('bm-parallel').checked, judge: $('bm-judge').value,
  };
}

// Same check as the server: a judge that is also a contestant may prefer its own style.
function renderBenchWarning() {
  const s = benchSetup();
  const inCouncil = s.members.some((m) => m.model === s.judge) || s.synthesizer === s.judge;
  $('bm-warn').textContent = inCouncil ? `⚠ Der Richter ${s.judge} ist selbst im Council und könnte Antworten im eigenen Stil bevorzugen.`
    : s.judge === s.baseline ? `⚠ Der Richter ${s.judge} ist auch das Einzelmodell und könnte seine eigene Antwort bevorzugen.` : '';
}

$('bm-add').addEventListener('click', () => {
  bench.members.push({ model: bench.members.at(-1)?.model || '', role: '', system: '' });
  renderBenchSetup();
});
for (const id of ['bm-baseline', 'bm-synth', 'bm-judge']) $(id).addEventListener('change', renderBenchWarning);

$('bm-start').addEventListener('click', async () => {
  const s = benchSetup();
  if (s.members.length < 2) return void ($('bm-msg').textContent = 'Der Council braucht mindestens zwei Mitglieder.');
  try {
    const run = await api('/api/benchmark/runs', { method: 'POST', body: s });
    $('bm-msg').textContent = '';
    await loadRuns();
    openRun(run.id);
  } catch (e) {
    $('bm-msg').textContent = `Fehler: ${e.message}`;
  }
});

// ---------- Questions ----------

async function loadQuestions() {
  try {
    bench.questions = await api('/api/benchmark/questions');
  } catch (e) {
    $('bq-msg').textContent = `Fehler: ${e.message}`;
  }
  renderQuestions();
}

function renderQuestions() {
  $('bq-count').textContent = `(${plural(bench.questions.length, 'Frage', 'Fragen')})`;
  $('bq-list').replaceChildren(...bench.questions.map((q, i) => {
    const row = el('div', 'bq-row');
    const cat = el('select');
    for (const [k, v] of Object.entries(CATEGORY_NAMES)) cat.append(new Option(v, k));
    cat.value = q.category;
    cat.addEventListener('change', () => { q.category = cat.value; });
    const lang = el('select');
    lang.append(new Option('DE', 'de'), new Option('EN', 'en'));
    lang.value = q.lang;
    lang.addEventListener('change', () => { q.lang = lang.value; });
    const text = el('textarea');
    Object.assign(text, { rows: 2, value: q.question, placeholder: 'Frage' });
    text.addEventListener('input', () => { q.question = text.value; });
    const ref = el('input');
    Object.assign(ref, { value: q.reference || '', placeholder: 'Referenzantwort (optional)' });
    ref.addEventListener('input', () => { q.reference = ref.value; });
    const del = el('button', 'icon-btn ghost', '×');
    Object.assign(del, { type: 'button', title: 'Frage entfernen' });
    del.setAttribute('aria-label', 'Frage entfernen');
    del.addEventListener('click', () => { bench.questions.splice(i, 1); renderQuestions(); });
    row.append(cat, lang, del, text, ref);
    return row;
  }));
}

$('bq-add').addEventListener('click', () => {
  bench.questions.push({ category: 'reasoning', lang: 'de', question: '', reference: '' });
  renderQuestions();
  $('bq-list').lastElementChild?.querySelector('textarea')?.focus();
});
$('bq-save').addEventListener('click', (e) => flash(e.target, async () => {
  bench.questions = await api('/api/benchmark/questions', { method: 'PUT', body: bench.questions });
  renderQuestions();
}));

// ---------- Runs ----------

function pct(n, total) {
  return total ? `${Math.round((100 * n) / total)} %` : '–';
}

function runLabel(run) {
  const s = run.setup;
  return `${s.baseline} gegen ${s.members.map((m) => m.model + (m.role ? ` (${m.role})` : '')).join(' + ')}`;
}

function statusText(run) {
  const p = run.progress;
  if (run.status === 'running') return p.step || `Frage ${p.current}/${p.total}`;
  if (run.status === 'done') return `fertig · ${p.total} Fragen`;
  return `gestoppt bei ${p.done}/${p.total}${run.error ? ` · Fehler: ${run.error}` : ''}`;
}

async function loadRuns() {
  try {
    bench.runs = await api('/api/benchmark/runs');
  } catch (e) {
    $('br-msg').textContent = `Fehler: ${e.message}`;
    return;
  }
  renderRuns();
  if (bench.open) refreshOpenRun();
  // Follow a running benchmark without a reload; stop polling once nothing runs.
  const running = bench.runs.some((r) => r.status === 'running');
  clearTimeout(bench.poll);
  if (running) bench.poll = setTimeout(loadRuns, 2500);
}

function renderRuns() {
  const list = $('br-list');
  list.replaceChildren();
  if (!bench.runs.length) return list.append(el('p', 'empty', 'Noch keine Läufe. Oben einen Lauf starten.'));
  const table = el('table', 'bm-table');
  const head = el('tr');
  for (const h of ['Datum', 'Vergleich', 'Richter', 'Stand', 'Council gewinnt', '']) head.append(el('th', null, h));
  table.append(head);
  for (const run of bench.runs) {
    const tr = el('tr', bench.open?.id === run.id ? 'selected' : '');
    const when = new Date(run.created * 1000).toLocaleString('de-DE', { dateStyle: 'short', timeStyle: 'short' });
    const w = run.summary.wins;
    const actions = el('td', 'bm-actions');
    const open = el('button', 'small', 'Öffnen');
    open.addEventListener('click', () => openRun(run.id));
    actions.append(open);
    if (run.status === 'running') actions.append(runButton('Stoppen', () => api(`/api/benchmark/runs/${run.id}/stop`, { method: 'POST' })));
    else if (run.status !== 'done') actions.append(runButton('Fortsetzen', () => api(`/api/benchmark/runs/${run.id}/start`, { method: 'POST' })));
    actions.append(runButton('Löschen', async () => {
      if (!confirm('Diesen Lauf mit allen Antworten löschen?')) return;
      await api(`/api/benchmark/runs/${run.id}`, { method: 'DELETE' });
      if (bench.open?.id === run.id) closeRun();
    }, 'ghost'));
    tr.append(el('td', null, when), el('td', 'bm-wrap', runLabel(run)), el('td', null, run.setup.judge),
      el('td', run.status === 'running' ? 'busy-text' : null, statusText(run)),
      el('td', null, run.summary.judged ? `${pct(w.council, run.summary.judged)} (${w.council}:${w.baseline}, ${w.tie} unentsch.)` : '–'),
      actions);
    table.append(tr);
  }
  const wrap = el('div', 'table-wrap');
  wrap.append(table);
  list.append(wrap);
}

function runButton(label, task, kind = '') {
  const b = el('button', `small ${kind}`.trim(), label);
  b.addEventListener('click', async () => {
    b.disabled = true;
    try {
      await task();
      $('br-msg').textContent = '';
    } catch (e) {
      $('br-msg').textContent = `Fehler: ${e.message}`;
    }
    await loadRuns();
  });
  return b;
}

// ---------- One run: results and own votes ----------

async function openRun(id) {
  bench.open = { id };
  await refreshOpenRun();
  renderRuns();
  $('bm-result').scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function closeRun() {
  bench.open = null;
  $('bm-result').hidden = true;
}

async function refreshOpenRun() {
  try {
    bench.open = await api(`/api/benchmark/runs/${bench.open.id}`);
  } catch {
    return closeRun();
  }
  renderResult(bench.open);
}

// A row with a small bar: share of `value` in `max`.
function barCell(value, max, text) {
  const td = el('td', 'bar-cell');
  const bar = el('span', 'bar');
  const fill = el('span');
  fill.style.width = `${max ? Math.min(100, (100 * (value || 0)) / max) : 0}%`;
  bar.append(fill);
  td.append(el('span', null, text), bar);
  return td;
}

function table(headers, rows) {
  const t = el('table', 'bm-table');
  const head = el('tr');
  for (const h of headers) head.append(el('th', null, h));
  t.append(head);
  for (const cells of rows) {
    const tr = el('tr');
    for (const c of cells) tr.append(c instanceof Node ? c : el('td', null, c ?? '–'));
    t.append(tr);
  }
  const wrap = el('div', 'table-wrap');
  wrap.append(t);
  return wrap;
}

function renderResult(run) {
  const box = $('bm-result');
  box.hidden = false;
  const s = run.summary;
  const n = s.judged;
  box.replaceChildren(el('h2', null, `Ergebnis: ${runLabel(run)}`));
  box.append(el('p', 'hint', `Council: ${run.setup.rounds} Diskussionsrunde(n), finale Antwort durch ${run.setup.synthesizer}`
    + ` · Richter: ${run.setup.judge} · ${statusText(run)}`));
  if (run.setup.warning) box.append(el('p', 'warn-text', `⚠ ${run.setup.warning}`));
  box.append(el('p', 'note', 'Hinweis: Das Urteil eines kleinen Richter-Modells ist ein Anhaltspunkt, kein Beweis. '
    + 'Es schwankt, bevorzugt manchmal längere Antworten und irrt bei Fakten. Deine eigenen Stimmen unten zeigen, wie weit man ihm trauen kann.'));
  if (!n) return box.append(el('p', 'empty', 'Noch keine bewertete Frage.'));

  box.append(el('h3', null, 'Wer gewinnt?'));
  box.append(table(['', `Siege laut Richter (${n} Fragen)`], [
    ['Council', barCell(s.wins.council, n, `${s.wins.council} (${pct(s.wins.council, n)})`)],
    [`Einzelmodell ${run.setup.baseline}`, barCell(s.wins.baseline, n, `${s.wins.baseline} (${pct(s.wins.baseline, n)})`)],
    ['Unentschieden', barCell(s.wins.tie, n, `${s.wins.tie} (${pct(s.wins.tie, n)})`)],
  ]));

  box.append(el('h3', null, 'Ø Punkte des Richters (1–10) nach Kategorie'));
  box.append(table(['Kategorie', 'Fragen', 'Einzelmodell', 'Council'], Object.entries(s.categories).map(([cat, c]) => [
    CATEGORY_NAMES[cat] || cat, String(c.n), barCell(c.baseline, 10, fmt(c.baseline, '', 1)), barCell(c.council, 10, fmt(c.council, '', 1)),
  ])));

  const b = s.sides.baseline;
  const c = s.sides.council;
  box.append(el('h3', null, 'Aufwand pro Frage'));
  box.append(table(['', 'Einzelmodell', 'Council'], [
    ['Ø Zeit', fmt(b.seconds, ' s', 1), fmt(c.seconds, ' s', 1)],
    ['Ø erzeugte Tokens', fmt(b.tokens, '', 0), fmt(c.tokens, '', 0)],
    ['Ø Punkte', fmt(b.score, '', 2), fmt(c.score, '', 2)],
    ['Richtig laut Referenz', b.correct == null ? '–' : pct(b.correct, 1), c.correct == null ? '–' : pct(c.correct, 1)],
  ]));
  box.append(el('p', 'hint', s.time_factor ? `Der Council braucht im Schnitt ${s.time_factor}× so lange wie das Einzelmodell.` : ''));

  box.append(el('h3', null, 'Selbst bewerten'));
  box.append(el('p', 'hint', s.votes
    ? `${plural(s.votes, 'Frage', 'Fragen')} selbst bewertet · Übereinstimmung mit dem Richter: ${pct(s.agreement, 1)}`
      + ` · deine Stimmen: Council ${s.human_wins.council}, Einzelmodell ${s.human_wins.baseline}, unentschieden ${s.human_wins.tie}`
    : 'Bewerte eine Stichprobe selbst: Du siehst die Antworten genauso anonym wie der Richter.'));
  box.append(voteView(run));

  const details = el('details', 'bm-details');
  details.append(el('summary', null, 'Alle Fragen im Einzelnen'));
  for (const r of run.results.filter((x) => x.judge)) details.append(questionResult(r));
  box.append(details);
}

// The next unvoted question in a fixed random order (so the sample is not just the first questions).
function nextForVote(run) {
  const open = run.results.filter((r) => r.judge && !r.human);
  const key = (r) => [...(r.qid + run.id)].reduce((h, ch) => (h * 31 + ch.charCodeAt(0)) >>> 0, 7);
  return open.sort((a, b) => key(a) - key(b))[0];
}

function voteView(run) {
  const r = nextForVote(run);
  const box = el('div', 'vote');
  if (!r) return el('p', 'empty', 'Alle bewerteten Fragen hast du auch selbst bewertet.');
  box.append(el('p', null, r.question.question));
  if (r.question.reference) box.append(el('p', 'hint', `Referenz: ${r.question.reference}`));
  const grid = el('div', 'vote-grid');
  for (const label of ['A', 'B']) {
    const card = el('div', 'card');
    const side = r.judge.labels[label];
    card.append(el('strong', null, `Antwort ${label}`));
    const md = el('div', 'md');
    renderMarkdown(md, r[side]?.content || '(keine Antwort)');
    card.append(md);
    grid.append(card);
  }
  box.append(grid);
  const actions = el('div', 'actions');
  for (const [label, text] of [['A', 'A ist besser'], ['B', 'B ist besser'], ['tie', 'Gleich gut']]) {
    actions.append(runButton(text, async () => {
      await api(`/api/benchmark/runs/${run.id}/vote`, { method: 'POST', body: { qid: r.qid, label } });
    }, label === 'tie' ? 'ghost' : ''));
  }
  box.append(actions);
  return box;
}

function questionResult(r) {
  const j = r.judge;
  const who = { baseline: 'Einzelmodell', council: 'Council', tie: 'unentschieden' };
  const item = el('div', 'bm-q');
  item.append(el('strong', null, `${CATEGORY_NAMES[r.question.category] || ''} · ${r.question.question}`));
  item.append(el('div', 'hint', `Richter: ${who[j.winner]} (Punkte Einzelmodell ${j.scores.baseline ?? '–'}, Council ${j.scores.council ?? '–'})`
    + `${r.human ? ` · du: ${who[r.human.winner]}` : ''} · Zeit ${fmt(r.baseline.seconds, ' s', 1)} / ${fmt(r.council.seconds, ' s', 1)}`));
  if (j.reason) item.append(el('div', 'hint', `Begründung: ${j.reason}`));
  return item;
}

// Called when the tab is opened (admins only).
function initBenchmark() {
  if (bench.ready) return loadRuns();
  bench.ready = true;
  renderBenchSetup();
  loadQuestions();
  loadRuns();
}
