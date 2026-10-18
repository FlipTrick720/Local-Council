# Vendored libraries

Copied unchanged from npm (via jsdelivr), so the app works without internet access.
Licenses are in the header of each file (KaTeX: `katex/LICENSE`).

| File | Package | License |
|---|---|---|
| `marked.umd.js` | marked 15.0.12 | MIT |
| `purify.min.js` | dompurify 3.2.6 | Apache-2.0 / MPL-2.0 |
| `highlight.min.js` | @highlightjs/cdn-assets 11.11.1 (common languages) | BSD-3-Clause |
| `katex/` | katex 0.16.22: `katex.min.js`, `katex.min.css`, `fonts/*.woff2` | MIT (`katex/LICENSE`) |

KaTeX ships fonts as woff2, woff and ttf; only woff2 is included, which all current browsers load first.
