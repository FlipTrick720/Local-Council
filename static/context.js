'use strict';

// Context meter under the chat input: estimates how much of the model's context window the next request
// fills, and warns before Ollama starts cutting off the beginning of the conversation.

const CHARS_PER_TOKEN = 3.5;  // rough average for German/English text
const IMAGE_TOKENS = 512;     // rough cost of one image for vision models
const DEFAULT_CTX = 4096;     // Ollama's default num_ctx unless OLLAMA_CONTEXT_LENGTH says otherwise

function estimateTokens(chars) {
  return Math.ceil(chars / CHARS_PER_TOKEN);
}

// Context sizes are usually powers of two (4096 → "4k", 131072 → "128k"); estimates are shown as 1.2k etc.
function kTokens(n) {
  if (n >= 1024 && n % 1024 === 0) return `${n / 1024}k`;
  return n >= 1000 ? `${(n / 1000).toFixed(n >= 10000 ? 0 : 1)}k` : String(n);
}

// Window size: chat setting > global model options > what Ollama reports for the loaded model > default.
function contextWindow(chat) {
  const set = chat.options?.num_ctx ?? state.config.ollama.options?.num_ctx;
  if (set) return { size: set, source: 'aus den Einstellungen' };
  if (chat.web && state.config.web?.num_ctx) return { size: state.config.web.num_ctx, source: 'für die Websuche (web.num_ctx)' };
  const loaded = loadedModels?.find((m) => m.name === $('chat-model').value);
  if (loaded?.context_length) return { size: loaded.context_length, source: 'von Ollama für das geladene Modell gemeldet' };
  return { size: DEFAULT_CTX, source: 'Ollama-Standard, geschätzt' };
}

function messageTokens(m) {
  const files = (m.attachments || []).reduce((n, a) => n + (a.kind === 'image' ? IMAGE_TOKENS : estimateTokens(a.chars || 0)), 0);
  return estimateTokens(m.content?.length || 0) + files + 4;
}

// The last answer's real numbers (prompt + reply tokens) cover everything up to it; only what came after
// is estimated.
function contextUsage(chat) {
  const msgs = chat.messages;
  let i = msgs.length - 1;
  while (i >= 0 && !(msgs[i].role === 'assistant' && msgs[i].stats)) i--;
  let tokens = i >= 0 ? msgs[i].stats.prompt_tokens + msgs[i].stats.tokens : estimateTokens((chat.system || '').length);
  for (const m of msgs.slice(i + 1)) tokens += messageTokens(m);
  tokens += messageTokens({ content: $('chat-text').value, attachments: pendingMetas() });
  return { tokens, cutBefore: i >= 0 && msgs[i].stats.prompt_tokens + msgs[i].stats.tokens >= 0.98 * contextWindow(chat).size };
}

let meterTimer = null;
function scheduleContextMeter() {
  clearTimeout(meterTimer);
  meterTimer = setTimeout(renderContextMeter, 250);
}

async function renderContextMeter() {
  const chat = chatState.current;
  if (!chat) return;
  const ctx = contextWindow(chat);
  const { tokens, cutBefore } = contextUsage(chat);
  const ratio = tokens / ctx.size;
  const level = ratio > 1 || cutBefore ? 'err' : ratio > 0.8 ? 'warn' : '';
  const meter = $('chat-ctx');
  meter.className = `ctx ${level}`;
  meter.title = `Kontextfenster ${ctx.size} Tokens (${ctx.source}). Belegung geschätzt.`;
  meter.replaceChildren(el('span', null, `Kontext ~${kTokens(tokens)} / ${kTokens(ctx.size)}`));
  const bar = el('span', 'ctx-bar');
  const fill = el('span');
  fill.style.width = `${Math.min(100, ratio * 100).toFixed(0)}%`;
  bar.append(fill);
  meter.append(bar);

  const warn = $('chat-ctx-warn');
  warn.hidden = !level;
  if (!level) return;
  const max = (await modelInfo($('chat-model').value))?.context_length;
  const hint = `Kontextlänge unter „System-Prompt & Einstellungen“ erhöhen${max && max > ctx.size ? ` (dieses Modell schafft bis ${kTokens(max)}, braucht dafür aber mehr Speicher)` : ''} oder einen neuen Chat beginnen.`;
  warn.textContent = ratio > 1 || cutBefore
    ? `⚠ Der Chat passt nicht mehr ins Kontextfenster (${kTokens(ctx.size)} Tokens). Ollama schneidet den Anfang ab, das Modell „vergisst“ ihn. ${hint}`
    : `⚠ Das Kontextfenster ist fast voll. ${hint}`;
}

$('chat-text').addEventListener('input', scheduleContextMeter);
$('chat-model').addEventListener('change', scheduleContextMeter);
