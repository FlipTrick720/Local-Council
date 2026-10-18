'use strict';

// Markdown → sanitized HTML (marked + DOMPurify), code blocks highlighted by highlight.js with a copy button,
// formulas rendered by KaTeX (see math.js).

marked.use({ gfm: true, breaks: true });
hljs.configure({ ignoreUnescapedHTML: true });

// `streaming`: the text is still arriving, so an unfinished formula at the end is shown as raw TeX for now.
function renderMarkdown(target, text, { streaming = false } = {}) {
  const { text: markdown, formulas } = extractMath(text || '', streaming);
  target.innerHTML = DOMPurify.sanitize(marked.parse(markdown));
  insertMath(target, formulas);
  for (const a of target.querySelectorAll('a[href]')) {
    a.target = '_blank';
    a.rel = 'noopener noreferrer';
  }
  for (const code of target.querySelectorAll('pre > code')) {
    const lang = [...code.classList].find((c) => c.startsWith('language-'))?.slice(9);
    // Unknown languages stay plain; without a language hint highlight.js guesses.
    if (!lang || hljs.getLanguage(lang)) hljs.highlightElement(code);
    const pre = code.parentElement;
    const block = el('div', 'code-block');
    const bar = el('div', 'code-bar');
    bar.append(el('span', null, lang || 'Code'), copyButton(() => code.textContent));
    pre.replaceWith(block);
    block.append(bar, pre);
  }
}
