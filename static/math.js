'use strict';

// Formulas in answers: $…$, $$…$$, \(…\) and \[…\] are rendered with KaTeX (static/vendor/katex).
// The formulas are taken out of the Markdown before marked sees it (marked would eat the backslashes of \( and
// break formulas containing _ * or |), replaced by placeholders, and put back as KaTeX output after sanitizing.
// Code blocks and inline code are left alone.

const MATH_MARK = /KATEXPH(\d+)X/;
const mathCache = new Map();

function atLineStart(src, i) {
  return i === 0 || src[i - 1] === '\n';
}

// Returns the Markdown with placeholders and the list of formulas. With `streaming`, a formula whose closing
// delimiter has not arrived yet is marked pending (shown as raw TeX) instead of flipping between text and math.
function extractMath(src, streaming = false) {
  const formulas = [];
  let out = '';
  let i = 0;
  const put = (tex, display, pending = false) => {
    formulas.push({ tex, display, pending });
    return `KATEXPH${formulas.length - 1}X`;
  };
  while (i < src.length) {
    // Fenced code block: copy up to the closing fence (or the end, while it is still streaming in).
    if (atLineStart(src, i)) {
      const fence = /^ {0,3}(`{3,}|~{3,})/.exec(src.slice(i, i + 40));
      if (fence) {
        const close = new RegExp(`\\n {0,3}${fence[1][0] === '`' ? '`' : '~'}{${fence[1].length},}[ \\t]*(?=\\n|$)`, 'g');
        close.lastIndex = i + fence[0].length;
        const m = close.exec(src);
        const end = m ? m.index + m[0].length : src.length;
        out += src.slice(i, end);
        i = end;
        continue;
      }
    }
    const c = src[i];
    if (c === '`') {
      const run = /^`+/.exec(src.slice(i))[0];
      const close = src.indexOf(run, i + run.length);
      const end = close < 0 ? i + run.length : close + run.length;
      out += src.slice(i, end);
      i = end;
      continue;
    }
    if (c === '\\') {
      const next = src[i + 1];
      if (next === '(' || next === '[') {
        const closer = next === '(' ? '\\)' : '\\]';
        const end = src.indexOf(closer, i + 2);
        if (end >= 0) {
          out += put(src.slice(i + 2, end), next === '[');
          i = end + 2;
          continue;
        }
        if (streaming) {
          out += put(src.slice(i), next === '[', true);
          break;
        }
      }
      out += src.slice(i, i + 2);  // keep other escapes (\$, \*, \\ …) for marked
      i += 2;
      continue;
    }
    if (c === '$' && src[i + 1] === '$') {
      const end = src.indexOf('$$', i + 2);
      if (end >= 0) {
        out += put(src.slice(i + 2, end), true);
        i = end + 2;
        continue;
      }
      if (streaming) {
        out += put(src.slice(i), true, true);
        break;
      }
      out += '$$';
      i += 2;
      continue;
    }
    if (c === '$') {
      // Inline: on one line, no space after the opening $, none before the closing $, and no digit right after
      // it – so "costs $5 and $10" stays text.
      if (src[i + 1] && !/\s/.test(src[i + 1])) {
        let j = i + 1;
        let end = -1;
        while (j < src.length && src[j] !== '\n') {
          if (src[j] === '\\') { j += 2; continue; }
          if (src[j] === '$') {
            if (!/\s/.test(src[j - 1]) && !/\d/.test(src[j + 1] || '')) end = j;
            break;
          }
          j++;
        }
        if (end > 0) {
          out += put(src.slice(i + 1, end), false);
          i = end + 1;
          continue;
        }
        if (streaming && j >= src.length) {
          out += put(src.slice(i), false, true);
          break;
        }
      }
      out += '$';
      i++;
      continue;
    }
    out += c;
    i++;
  }
  return { text: out, formulas };
}

function mathNode({ tex, display, pending }) {
  const node = el('span', `math${display ? ' math-display' : ''}`);
  if (pending) {
    node.classList.add('math-pending');
    node.textContent = tex;
    return node;
  }
  // Rendering is cached: while streaming, the same formulas are drawn again every few frames.
  const key = `${display ? 'D' : 'I'}${tex}`;
  let html = mathCache.get(key);
  if (html === undefined) {
    html = katex.renderToString(tex, { displayMode: display, throwOnError: false, output: 'htmlAndMathml' });
    mathCache.set(key, html);
    if (mathCache.size > 500) mathCache.delete(mathCache.keys().next().value);
  }
  node.innerHTML = html;
  return node;
}

// Replaces the placeholders in the (already sanitized) DOM by the rendered formulas.
function insertMath(root, formulas) {
  if (!formulas.length) return;
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  const nodes = [];
  while (walker.nextNode()) if (MATH_MARK.test(walker.currentNode.nodeValue)) nodes.push(walker.currentNode);
  for (const node of nodes) {
    const parts = node.nodeValue.split(/KATEXPH(\d+)X/);
    const fragment = document.createDocumentFragment();
    parts.forEach((part, k) => {
      if (k % 2) fragment.append(formulas[Number(part)] ? mathNode(formulas[Number(part)]) : part);
      else if (part) fragment.append(part);
    });
    node.replaceWith(fragment);
  }
}
