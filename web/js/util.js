// util.js -- tiny DOM helpers, number formatting and the colour tokens shared by map and charts.

export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

const SVG_NS = 'http://www.w3.org/2000/svg';

/** h('div', {class: 'x', onclick: fn, dataset: {a: 1}}, 'text', childEl) -> HTMLElement (text is always escaped) */
export function h(tag, attrs = {}, ...children) {
  return build(document.createElement(tag), attrs, children);
}

/** s('circle', {cx: 1, cy: 2, r: 3}) -> SVGElement */
export function s(tag, attrs = {}, ...children) {
  return build(document.createElementNS(SVG_NS, tag), attrs, children);
}

function build(el, attrs, children) {
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k === 'class') el.setAttribute('class', v);
    else if (k === 'style' && typeof v === 'object') Object.assign(el.style, v);
    else if (k === 'dataset') Object.assign(el.dataset, v);
    else if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? '' : v);
  }
  for (const c of children.flat()) {
    if (c === null || c === undefined || c === false) continue;
    el.append(c.nodeType ? c : document.createTextNode(String(c)));
  }
  return el;
}

export function clear(el) {
  while (el.firstChild) el.removeChild(el.firstChild);
  return el;
}

// ---- number formatting ----------------------------------------------------
export const fmt = {
  min: (v, d = 1) => (v === null || v === undefined ? '–' : Number(v).toFixed(d)),
  km: (v, d = 1) => `${Number(v).toFixed(d)} km`,
  int: (v) => Number(v).toLocaleString('en-GB'),
  pct: (v, d = 0) => `${(v * 100).toFixed(d)}%`,
  // microseconds -> "123 µs" or "2.4 ms"
  time: (us) => (us < 1000 ? `${Math.round(us)} µs` : `${(us / 1000).toFixed(us < 10000 ? 2 : 1)} ms`),
};

export const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;

// ---- colour tokens (kept in sync with css/styles.css) -----------------------
export const COLORS = {
  ink: '#1e2a32', track: '#5f6e78', kerb: '#d5ddda', kerbSoft: '#e6ecea', surface: '#ffffff', flag: '#ffd23f',
  astar: '#6250d6', dijkstra: '#1baf7a', baseline: '#eb6834',
  ride: '#2a78d6', wait: '#eda100', walk: '#6b7a86', penalty: '#e34948',
  // network size: one hue, three steps (small -> large)
  net: { city_80: '#a99ff0', city_300: '#6250d6', city_1000: '#33257f' },
};

export const COST_PARTS = [
  { key: 'ride', label: 'Ride', color: COLORS.ride },
  { key: 'wait', label: 'Wait', color: COLORS.wait },
  { key: 'walk', label: 'Walk', color: COLORS.walk },
  { key: 'penalty', label: 'Transfer penalty', color: COLORS.penalty },
];

export const ALGO = {
  astar: { label: 'A*', color: COLORS.astar },
  dijkstra: { label: 'Dijkstra', color: COLORS.dijkstra },
};

/** line badge element: coloured rounded rectangle with the line code ("M1", "B3") */
export function badge(line) {
  return h('span', { class: `badge ${line.mode}`, style: { background: line.color }, title: line.name }, line.code);
}

export function debounce(fn, ms) {
  let t;
  return (...a) => {
    clearTimeout(t);
    t = setTimeout(() => fn(...a), ms);
  };
}

export const reducedMotion = () => window.matchMedia('(prefers-reduced-motion: reduce)').matches;
