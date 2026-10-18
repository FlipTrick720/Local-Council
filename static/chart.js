'use strict';

// Minimal SVG line chart with legend, direct labels and a crosshair tooltip. No dependencies.
//
// lineChart(container, {series, xHeaders, format, empty})
//   series:   [{name, color, dash?, segments: [[{x, y, note?}, ...], ...]}]
//             x must be an integer; each segment is drawn as its own line (gaps between segments).
//   xHeaders: {x: "tooltip heading"}
//   format:   (value) => string
//   empty:    text shown when there is no data

const SVG_NS = 'http://www.w3.org/2000/svg';

function svgEl(tag, attrs, parent) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  parent?.append(node);
  return node;
}

function niceMax(value) {
  if (!(value > 0)) return 1;
  const p = 10 ** Math.floor(Math.log10(value));
  return [1, 2, 2.5, 5, 10].map((m) => m * p).find((v) => v >= value);
}

function lineKey(color, dash) {
  const key = svgEl('svg', { width: 16, height: 8, class: 'key' });
  const line = svgEl('line', { x1: 0, x2: 16, y1: 4, y2: 4, 'stroke-width': 2 }, key);
  line.style.stroke = color;
  if (dash) line.setAttribute('stroke-dasharray', dash);
  return key;
}

function lineChart(container, { series, xHeaders, format, empty }) {
  container.replaceChildren();
  const points = series.flatMap((s) => s.segments.flat());
  if (!points.length) {
    const p = document.createElement('p');
    p.className = 'hint';
    p.textContent = empty;
    container.append(p);
    return;
  }

  const legend = document.createElement('div');
  legend.className = 'legend';
  for (const s of series) {
    const item = document.createElement('span');
    item.append(lineKey(s.color, s.dash), document.createTextNode(s.name));
    legend.append(item);
  }
  container.append(legend);

  const width = Math.max(container.clientWidth, 280);
  const height = 240;
  const directLabels = series.length <= 4 && width >= 480;
  const labelWidth = directLabels ? Math.min(150, 14 + 7 * Math.max(...series.map((s) => s.name.length))) : 0;
  const m = { top: 12, right: 16 + labelWidth, bottom: 26, left: 46 };
  const pw = width - m.left - m.right;
  const ph = height - m.top - m.bottom;

  const xs = points.map((p) => p.x);
  const xMin = Math.min(...xs);
  const xMax = Math.max(...xs);
  const yMax = niceMax(Math.max(...points.map((p) => p.y)));
  const sx = (x) => m.left + (xMax === xMin ? pw / 2 : ((x - xMin) / (xMax - xMin)) * pw);
  const sy = (y) => m.top + ph - (y / yMax) * ph;

  const root = svgEl('svg', { viewBox: `0 0 ${width} ${height}`, width: '100%', class: 'chart' });

  for (let i = 0; i <= 4; i++) {
    const v = (yMax * i) / 4;
    svgEl('line', { x1: m.left, x2: m.left + pw, y1: sy(v), y2: sy(v), class: i ? 'grid' : 'axis' }, root);
    svgEl('text', { x: m.left - 6, y: sy(v) + 4, 'text-anchor': 'end', class: 'tick' }, root).textContent = format(v);
  }
  const step = Math.max(1, Math.ceil((xMax - xMin + 1) / Math.max(2, Math.floor(pw / 48))));
  for (let x = xMin; x <= xMax; x += step) {
    svgEl('text', { x: sx(x), y: height - 6, 'text-anchor': 'middle', class: 'tick' }, root).textContent = `#${x}`;
  }

  for (const s of series) {
    for (const seg of s.segments) {
      if (seg.length > 1) {
        const path = svgEl('path', {
          d: seg.map((p, i) => `${i ? 'L' : 'M'}${sx(p.x)},${sy(p.y)}`).join(''),
          fill: 'none', 'stroke-width': 2, 'stroke-linejoin': 'round',
        }, root);
        path.style.stroke = s.color;
        if (s.dash) path.setAttribute('stroke-dasharray', s.dash);
      }
      for (const p of seg) {
        const dot = svgEl('circle', { cx: sx(p.x), cy: sy(p.y), r: 4, class: 'dot' }, root);
        dot.style.fill = s.color;
      }
    }
  }

  if (directLabels) {
    const labels = series.map((s) => {
      const last = s.segments.at(-1).at(-1);
      return { s, y: sy(last.y) };
    }).sort((a, b) => a.y - b.y);
    for (let i = 1; i < labels.length; i++) labels[i].y = Math.max(labels[i].y, labels[i - 1].y + 14);
    for (const { s, y } of labels) {
      const key = svgEl('line', { x1: m.left + pw + 8, x2: m.left + pw + 16, y1: y, y2: y, 'stroke-width': 2 }, root);
      key.style.stroke = s.color;
      svgEl('text', { x: m.left + pw + 20, y: y + 4, class: 'label' }, root).textContent = s.name;
    }
  }

  const hair = svgEl('line', { y1: m.top, y2: m.top + ph, class: 'hair', visibility: 'hidden' }, root);
  const hit = svgEl('rect', { x: m.left - 12, y: 0, width: pw + 24, height, fill: 'transparent' }, root);
  const tip = document.createElement('div');
  tip.className = 'tooltip';
  tip.hidden = true;

  const hide = () => { tip.hidden = true; hair.setAttribute('visibility', 'hidden'); };
  hit.addEventListener('pointerleave', hide);
  hit.addEventListener('pointermove', (e) => {
    const box = root.getBoundingClientRect();
    const px = ((e.clientX - box.left) * width) / box.width;
    const x = Math.min(xMax, Math.max(xMin, Math.round(xMin + ((px - m.left) / pw) * (xMax - xMin || 1))));
    const rows = series
      .map((s) => ({ s, p: s.segments.flat().find((p) => p.x === x) }))
      .filter((r) => r.p)
      .sort((a, b) => b.p.y - a.p.y);
    if (!rows.length) return hide();

    tip.replaceChildren();
    const head = document.createElement('div');
    head.className = 'tip-head';
    head.textContent = xHeaders[x] ?? `#${x}`;
    tip.append(head);
    for (const { s, p } of rows) {
      const row = document.createElement('div');
      const value = document.createElement('strong');
      value.textContent = format(p.y);
      row.append(lineKey(s.color, s.dash), value, document.createTextNode(` ${s.name}`));
      if (p.note) {
        const note = document.createElement('span');
        note.className = 'hint';
        note.textContent = ` · ${p.note}`;
        row.append(note);
      }
      tip.append(row);
    }

    hair.setAttribute('x1', sx(x));
    hair.setAttribute('x2', sx(x));
    hair.setAttribute('visibility', 'visible');
    tip.hidden = false;
    const left = (sx(x) / width) * box.width;
    const flip = left > box.width / 2;
    tip.style.left = flip ? '' : `${left + 12}px`;
    tip.style.right = flip ? `${box.width - left + 12}px` : '';
  });

  const wrap = document.createElement('div');
  wrap.className = 'chart-wrap';
  wrap.append(root, tip);
  container.append(wrap);
}
