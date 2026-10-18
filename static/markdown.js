'use strict';

// Markdown → sanitized HTML (marked + DOMPurify), code blocks highlighted by highlight.js with a copy button.

marked.use({ gfm: true, breaks: true });
hljs.configure({ ignoreUnescapedHTML: true });

function renderMarkdown(target, text) {
  target.innerHTML = DOMPurify.sanitize(marked.parse(text || ''));
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
