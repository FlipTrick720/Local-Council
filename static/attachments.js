'use strict';

// Chat attachments: file button, paste and drag & drop. Files are uploaded right away (/api/uploads) and the
// message only carries their ids. Text files and PDFs become text for the model, images go to vision models.

const pendingFiles = [];  // {name, uploading} → {name, meta} or {name, error}
const MAX_IMAGE_SIDE = 1536;
const PASTE_AS_FILE = 8000;  // pasted text longer than this becomes an attachment instead of filling the box

// Big photos are scaled down in the browser: vision models work at low resolution anyway.
async function prepareImage(file) {
  const bitmap = await createImageBitmap(file).catch(() => null);
  if (!bitmap) return file;
  const scale = Math.min(1, MAX_IMAGE_SIDE / Math.max(bitmap.width, bitmap.height));
  if (scale === 1 && ['image/jpeg', 'image/png'].includes(file.type) && file.size < 3e6) return file;
  const canvas = document.createElement('canvas');
  canvas.width = Math.round(bitmap.width * scale);
  canvas.height = Math.round(bitmap.height * scale);
  const ctx = canvas.getContext('2d');
  ctx.fillStyle = '#fff';
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  ctx.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
  const blob = await new Promise((resolve) => canvas.toBlob(resolve, 'image/jpeg', 0.9));
  return new File([blob], `${file.name.replace(/\.[^.]*$/, '') || 'bild'}.jpg`, { type: 'image/jpeg' });
}

async function uploadFile(file) {
  const res = await fetch(`/api/uploads?name=${encodeURIComponent(file.name)}`, {
    method: 'POST', headers: { 'Content-Type': file.type || 'application/octet-stream' }, body: file,
  });
  if (!res.ok) throw new Error(await errorText(res));
  return res.json();
}

async function addFiles(files) {
  await Promise.all([...files].map(async (file) => {
    const item = { name: file.name || 'Datei', uploading: true };
    pendingFiles.push(item);
    renderPending();
    try {
      item.meta = await uploadFile(file.type.startsWith('image/') ? await prepareImage(file) : file);
    } catch (e) {
      item.error = e instanceof TypeError ? 'Server nicht erreichbar' : e.message;
    }
    item.uploading = false;
    renderPending();
  }));
}

function attachmentDetails(meta) {
  if (meta.kind === 'image') return 'Bild';
  const parts = [];
  if (meta.pages) parts.push(`${meta.pages} Seite${meta.pages > 1 ? 'n' : ''}`);
  parts.push(`~${kTokens(estimateTokens(meta.chars || 0))} Tokens`);
  if (meta.truncated) parts.push('gekürzt');
  return parts.join(', ');
}

// A chip for an attachment: thumbnail for images, name and size for text. Opens the file in a new tab.
function attachmentChip(meta, onRemove) {
  const chip = el('span', `attach ${meta.kind}`);
  const link = el('a');
  link.href = `/api/uploads/${meta.id}`;
  link.target = '_blank';
  link.title = meta.name;
  if (meta.kind === 'image') {
    const img = el('img');
    img.src = link.href;
    img.alt = meta.name;
    link.append(img);
  } else {
    link.append(el('span', null, `📄 ${meta.name}`), el('small', null, ` ${attachmentDetails(meta)}`));
  }
  chip.append(link);
  if (onRemove) chip.append(removeButton(onRemove));
  return chip;
}

function removeButton(onRemove) {
  const x = el('button', 'ghost attach-x', '×');
  x.type = 'button';
  x.title = 'Entfernen';
  x.addEventListener('click', onRemove);
  return x;
}

function renderPending() {
  const box = $('chat-attach');
  box.replaceChildren();
  for (const item of pendingFiles) {
    const remove = () => {
      pendingFiles.splice(pendingFiles.indexOf(item), 1);
      renderPending();
    };
    if (item.meta) box.append(attachmentChip(item.meta, remove));
    else {
      const chip = el('span', `attach ${item.error ? 'failed' : 'busy'}`,
        item.error ? `${item.name}: ${item.error}` : `${item.name} wird hochgeladen …`);
      if (item.error) chip.append(removeButton(remove));
      box.append(chip);
    }
  }
  checkVision();
  scheduleContextMeter();
}

function pendingMetas() {
  return pendingFiles.filter((f) => f.meta).map((f) => f.meta);
}

// Warns while an image is attached but the chat model cannot see images.
async function checkVision() {
  const msg = $('chat-attach-msg');
  const model = $('chat-model').value;
  const ok = await canSeeImages(model);
  msg.textContent = ok ? '' : `„${model}“ kann keine Bilder verarbeiten. Bitte ein Vision-Modell wählen (z. B. gemma3, llava, qwen2.5vl).`;
  msg.hidden = ok;
  return ok;
}

async function canSeeImages(model) {
  if (!pendingMetas().some((m) => m.kind === 'image')) return true;
  const info = await modelInfo(model);
  return !info || info.capabilities.includes('vision');  // unknown: let Ollama decide
}

// Hands the finished uploads to a new message and empties the list. Throws while uploads are still running.
async function takePending() {
  if (pendingFiles.some((f) => f.uploading)) throw new Error('Dateien werden noch hochgeladen …');
  if (!(await checkVision())) throw new Error('Nicht gesendet, siehe Hinweis über der Eingabe.');
  const metas = pendingMetas().map(({ id, name, kind, size, chars, pages, truncated }) => ({ id, name, kind, size, chars, pages, truncated }));
  pendingFiles.length = 0;
  renderPending();
  return metas;
}

// ---------- Wiring: button, paste, drag & drop ----------

$('chat-file').addEventListener('click', () => $('chat-file-input').click());
$('chat-file-input').addEventListener('change', (e) => {
  addFiles(e.target.files);
  e.target.value = '';
});

$('chat-text').addEventListener('paste', (e) => {
  const files = e.clipboardData?.files;
  if (files?.length) {
    e.preventDefault();
    addFiles(files);
    return;
  }
  const text = e.clipboardData?.getData('text/plain') || '';
  if (text.length > PASTE_AS_FILE) {
    e.preventDefault();
    addFiles([new File([text], 'Eingefügter Text.txt', { type: 'text/plain' })]);
  }
});

let dragDepth = 0;
const chatMain = document.querySelector('#tab-chat .chat-main');
const hasFiles = (e) => [...(e.dataTransfer?.types || [])].includes('Files');
chatMain.addEventListener('dragenter', (e) => {
  if (!hasFiles(e)) return;
  e.preventDefault();
  dragDepth++;
  chatMain.classList.add('dropping');
});
chatMain.addEventListener('dragover', (e) => { if (hasFiles(e)) e.preventDefault(); });
chatMain.addEventListener('dragleave', () => {
  if (--dragDepth <= 0) {
    dragDepth = 0;
    chatMain.classList.remove('dropping');
  }
});
chatMain.addEventListener('drop', (e) => {
  if (!hasFiles(e)) return;
  e.preventDefault();
  dragDepth = 0;
  chatMain.classList.remove('dropping');
  addFiles(e.dataTransfer.files);
});
$('chat-model').addEventListener('change', checkVision);
