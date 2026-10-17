// map.js -- the schematic city map (hand-built SVG, no map tiles, no libraries).
//
// Coordinates from the backend are kilometres on a plain x/y canvas. A "view" {cx, cy, k} says which
// city point is at the centre of the screen and how many pixels one kilometre is. Every element is
// created once and then repositioned by _layout() whenever the view changes (pan / zoom / resize).

import { s, h, clear, badge, reducedMotion, COLORS } from './util.js';

const MIN_K = 14;
const MAX_K = 260;
const clamp = (v, a, b) => Math.min(b, Math.max(a, v));

export class TransitMap {
  /**
   * @param root   container element (position: relative)
   * @param city   payload of GET /api/city
   * @param hooks  { onClick({stop, point, clientX, clientY}), onHover(stop|null, clientX, clientY) }
   */
  constructor(root, city, hooks = {}) {
    this.root = root;
    this.city = city;
    this.hooks = hooks;
    this.size = city.size_km;
    this.stopById = new Map(city.stops.map((st) => [st.id, st]));
    this.lineByCode = new Map(city.lines.map((l) => [l.code, l]));
    this.view = { cx: this.size / 2, cy: this.size / 2, k: 40 };
    this.W = 800;
    this.H = 600;
    this.opts = { labels: true, walks: true };
    this.plan = null;
    this.endpoints = { from: null, to: null };
    this.replay = null;
    this._ptrs = new Map();
    this._fitted = false;
    this._build();
    new ResizeObserver(() => this._resize()).observe(root);
    this._resize();
  }

  // ---- projection ------------------------------------------------------------
  px(x) { return (x - this.view.cx) * this.view.k + this.W / 2; }
  py(y) { return this.H / 2 - (y - this.view.cy) * this.view.k; }
  pts(coords) { return coords.map(([x, y]) => `${this.px(x).toFixed(1)},${this.py(y).toFixed(1)}`).join(' '); }
  unproject(sx, sy) { return [(sx - this.W / 2) / this.view.k + this.view.cx, (this.H / 2 - sy) / this.view.k + this.view.cy]; }

  // ---- DOM ---------------------------------------------------------------------
  _build() {
    const svg = (this.svg = s('svg', { class: 'map-svg', role: 'img',
      'aria-label': 'Schematic map of the fictional city with metro lines, bus routes and stops' }));
    this.g = {};
    for (const name of ['bg', 'walks', 'lines', 'badges', 'explore', 'stops', 'labels', 'route', 'pins']) {
      this.g[name] = s('g', { class: `layer-${name}` });
      svg.append(this.g[name]);
    }

    // city boundary + 1 km grid
    this.cityRect = s('rect', { class: 'bg-city' });
    this.g.bg.append(this.cityRect);
    this.gridV = [];
    this.gridH = [];
    for (let i = 0; i <= Math.floor(this.size); i++) {
      const major = i % 5 === 0;
      const v = s('line', { class: major ? 'grid-major' : 'grid-minor' });
      const hz = s('line', { class: major ? 'grid-major' : 'grid-minor' });
      this.gridV.push([i, v]);
      this.gridH.push([i, hz]);
      this.g.bg.append(v, hz);
    }

    // walking links
    this.walkEls = this.city.walks.map((w) => {
      const el = s('polyline', { class: 'walk-link', fill: 'none' });
      el.append(s('title', {}, `Walking link, ${w.minutes.toFixed(1)} min`));
      this.g.walks.append(el);
      return { w, el };
    });

    // lines (white casing under a coloured stroke)
    this.lineEls = this.city.lines.map((l) => {
      const casing = s('polyline', { class: 'line-casing', fill: 'none' });
      const main = s('polyline', { class: `line-main ${l.mode}`, fill: 'none', stroke: l.color });
      main.append(s('title', {}, `${l.code} ${l.name}`));
      this.g.lines.append(casing, main);
      return { l, casing, main };
    });

    // line badges at the ends of every line (a second, non-colour way to tell lines apart)
    this.badgeEls = [];
    const perStop = new Map();
    for (const l of this.city.lines) {
      const ends = l.loop ? [l.stop_ids[0]] : [l.stop_ids[0], l.stop_ids[l.stop_ids.length - 1]];
      for (const sid of ends) {
        const n = perStop.get(sid) || 0;
        perStop.set(sid, n + 1);
        const g = s('g', { class: 'line-badge' },
          s('rect', { rx: 3, ry: 3, height: 16, width: 8 + 8 * l.code.length, fill: l.color }),
          s('text', { x: (8 + 8 * l.code.length) / 2, y: 12 }, l.code));
        this.g.badges.append(g);
        this.badgeEls.push({ g, sid, slot: n, w: 8 + 8 * l.code.length });
      }
    }

    // stops
    this.stopEls = this.city.stops.map((st) => {
      const type = st.interchange ? 'interchange' : st.metro ? 'metro' : 'bus';
      const c = s('circle', { class: `stop stop-${type}` });
      this.g.stops.append(c);
      const lbl = s('text', { class: `stop-lbl lbl-${type}` }, st.name);
      this.g.labels.append(lbl);
      return { st, type, c, lbl };
    });

    this.root.append(svg);
    this.scaleEl = h('div', { class: 'scalebar', 'aria-hidden': 'true' },
      h('div', { class: 'scalebar-line' }), h('span', {}, ''), h('span', { class: 'scalebar-note' }, 'Fictional city'));
    this.root.append(this.scaleEl);
    this._bindEvents();
  }

  // ---- layout (called on every view change) --------------------------------------
  _layout() {
    const { k } = this.view;
    const f = clamp(0.8 + (k - 40) / 200, 0.8, 1.6);          // stop size factor
    const fl = clamp(0.7 + (k - 40) / 300, 0.75, 1.5);        // line width factor

    this.cityRect.setAttribute('x', this.px(0));
    this.cityRect.setAttribute('y', this.py(this.size));
    this.cityRect.setAttribute('width', this.size * k);
    this.cityRect.setAttribute('height', this.size * k);
    for (const [i, el] of this.gridV) {
      el.setAttribute('x1', this.px(i)); el.setAttribute('x2', this.px(i));
      el.setAttribute('y1', this.py(0)); el.setAttribute('y2', this.py(this.size));
    }
    for (const [i, el] of this.gridH) {
      el.setAttribute('y1', this.py(i)); el.setAttribute('y2', this.py(i));
      el.setAttribute('x1', this.px(0)); el.setAttribute('x2', this.px(this.size));
    }

    for (const { w, el } of this.walkEls) {
      el.setAttribute('points', this.pts(w.coords));
      el.style.strokeWidth = (3 * fl).toFixed(1);
    }
    for (const { l, casing, main } of this.lineEls) {
      const p = this.pts(l.coords);
      const w = (l.mode === 'metro' ? 6 : 3.5) * fl;
      casing.setAttribute('points', p); main.setAttribute('points', p);
      casing.style.strokeWidth = (w + 3).toFixed(1); main.style.strokeWidth = w.toFixed(1);
    }
    for (const b of this.badgeEls) {
      const st = this.stopById.get(b.sid);
      b.g.setAttribute('transform', `translate(${(this.px(st.x) + 10).toFixed(1)},${(this.py(st.y) - 26 - b.slot * 19).toFixed(1)})`);
    }

    const onRoute = this._routeStops();
    const hasRoute = !!this.plan;
    this.svg.classList.toggle('has-route', hasRoute);
    for (const e of this.stopEls) {
      const r = { bus: 3.4, metro: 4.8, interchange: 6.6 }[e.type] * f;
      e.c.setAttribute('cx', this.px(e.st.x)); e.c.setAttribute('cy', this.py(e.st.y)); e.c.setAttribute('r', r.toFixed(1));
      e.c.classList.toggle('on-route', onRoute.has(e.st.id));
      const show = this.opts.labels && (hasRoute ? k >= 110 : e.type === 'interchange' ? k >= 34 : e.type === 'metro' ? k >= 52 : k >= 100);
      e.lbl.setAttribute('x', this.px(e.st.x) + r + 4); e.lbl.setAttribute('y', this.py(e.st.y) - r - 2);
      e.lbl.style.display = show ? '' : 'none';
    }
    this.g.walks.style.display = this.opts.walks ? '' : 'none';

    this._layoutRoute();
    this._layoutPins();
    this._layoutReplay();
    this._layoutScale();
  }

  _layoutScale() {
    const target = 120 / this.view.k;                      // km that fit ~120 px
    const nice = [0.25, 0.5, 1, 2, 5, 10].filter((v) => v <= target).pop() || 0.25;
    this.scaleEl.firstChild.style.width = `${nice * this.view.k}px`;
    this.scaleEl.children[1].textContent = `${nice} km`;
  }

  // ---- route -------------------------------------------------------------------
  _routeStops() {
    const set = new Set();
    if (!this.plan || !this.plan.found) return set;
    for (const l of this.plan.legs) if (l.kind === 'ride') l.stop_ids.forEach((id) => set.add(id));
    return set;
  }

  /** Show a planned journey (payload of GET /api/route) or clear it with null. */
  setRoute(plan, { fit = true, animate = true } = {}) {
    this.plan = plan && plan.found ? plan : null;
    clear(this.g.route);
    this.routeEls = [];
    if (this.plan) {
      const legs = this.plan.legs;
      legs.forEach((leg, i) => {
        if (leg.kind === 'walk') {
          const casing = s('polyline', { class: 'route-casing', fill: 'none' });
          const body = s('polyline', { class: 'route-walk', fill: 'none' });
          this.g.route.append(casing, body);
          this.routeEls.push({ leg, casing, body, i });
        } else {
          const casing = s('polyline', { class: 'route-casing', fill: 'none' });
          const body = s('polyline', { class: 'route-body', fill: 'none', stroke: leg.color });
          const chev = leg.coords.slice(1).map(() => s('path', { class: 'route-chevron', d: 'M-3.5,-4 L3,0 L-3.5,4', fill: 'none' }));
          const dots = leg.coords.map((_, j) => (j > 0 && j < leg.coords.length - 1) ? s('circle', { class: 'route-dot', stroke: leg.color }) : null);
          this.g.route.append(casing, body, ...chev, ...dots.filter(Boolean));
          this.routeEls.push({ leg, casing, body, chev, dots, i });
        }
      });
      // boarding / alighting markers and their names
      this.markEls = [];
      const seen = new Set();
      legs.forEach((leg, i) => {
        if (leg.kind !== 'ride') return;
        const ends = [[leg.coords[0], leg.stop_names[0], leg.stop_ids[0], i === 0 ? 'Board' : 'Change'],
                      [leg.coords[leg.coords.length - 1], leg.stop_names[leg.stop_names.length - 1], leg.stop_ids[leg.stop_ids.length - 1], 'Get off']];
        for (const [xy, name, id, role] of ends) {
          const key = `${id}`;
          const c = s('circle', { class: 'route-end' });
          this.g.route.append(c);
          let text = null;
          if (!seen.has(key)) {
            seen.add(key);
            text = s('text', { class: 'route-lbl' }, name);
            this.g.route.append(text);
          }
          this.markEls.push({ xy, c, text });
        }
      });
      if (animate && !reducedMotion()) this._drawIn();
    }
    this._layout();
    if (fit && this.plan) this.fitRoute();
  }

  _layoutRoute() {
    if (!this.plan) return;
    const { k } = this.view;
    const fl = clamp(0.8 + (k - 40) / 300, 0.85, 1.4);
    for (const e of this.routeEls) {
      const p = this.pts(e.leg.coords);
      e.casing.setAttribute('points', p); e.body.setAttribute('points', p);
      if (e.leg.kind === 'walk') {
        e.casing.style.strokeWidth = `${(8 * fl).toFixed(1)}px`; e.body.style.strokeWidth = `${(4 * fl).toFixed(1)}px`;
      } else {
        e.casing.style.strokeWidth = `${(14 * fl).toFixed(1)}px`; e.body.style.strokeWidth = `${(9 * fl).toFixed(1)}px`;
        e.chev.forEach((c, j) => {
          const [x0, y0] = e.leg.coords[j]; const [x1, y1] = e.leg.coords[j + 1];
          const ax = this.px(x0), ay = this.py(y0), bx = this.px(x1), by = this.py(y1);
          const long = Math.hypot(bx - ax, by - ay) > 26;
          c.style.display = long ? '' : 'none';
          c.setAttribute('transform', `translate(${((ax + bx) / 2).toFixed(1)},${((ay + by) / 2).toFixed(1)}) rotate(${(Math.atan2(by - ay, bx - ax) * 180 / Math.PI).toFixed(1)})`);
        });
        e.dots.forEach((d, j) => { if (d) { d.setAttribute('cx', this.px(e.leg.coords[j][0])); d.setAttribute('cy', this.py(e.leg.coords[j][1])); d.setAttribute('r', (3.6 * fl).toFixed(1)); } });
      }
    }
    for (const m of this.markEls) {
      m.c.setAttribute('cx', this.px(m.xy[0])); m.c.setAttribute('cy', this.py(m.xy[1])); m.c.setAttribute('r', (7 * fl).toFixed(1));
      if (m.text) { m.text.setAttribute('x', this.px(m.xy[0]) + 12); m.text.setAttribute('y', this.py(m.xy[1]) + 4); }
    }
  }

  _drawIn() {
    // one orchestrated moment: the route draws itself from start to finish
    requestAnimationFrame(() => {
      for (const e of this.routeEls) {
        if (e.leg.kind !== 'ride') continue;
        for (const el of [e.casing, e.body]) {
          const len = el.getTotalLength ? el.getTotalLength() : 0;
          if (!len) continue;
          el.style.strokeDasharray = len; el.style.strokeDashoffset = len;
          el.getBoundingClientRect();
          el.style.transition = 'stroke-dashoffset .8s cubic-bezier(.3,.7,.2,1)';
          el.style.strokeDashoffset = 0;
          setTimeout(() => { el.style.transition = ''; el.style.strokeDasharray = ''; el.style.strokeDashoffset = ''; }, 900);
        }
      }
    });
  }

  // ---- start / destination pins -----------------------------------------------------
  /** from / to: {x, y, name} or null */
  setEndpoints(from, to) {
    this.endpoints = { from, to };
    clear(this.g.pins);
    this.pinEls = [];
    for (const [key, label] of [['from', 'A'], ['to', 'B']]) {
      const p = this.endpoints[key];
      if (!p) continue;
      const g = s('g', { class: `pin pin-${key}` },
        s('circle', { r: 13 }), s('text', { y: 5 }, label));
      g.append(s('title', {}, `${key === 'from' ? 'Start' : 'Destination'}: ${p.name}`));
      this.g.pins.append(g);
      this.pinEls.push({ g, p });
    }
    this._layoutPins();
  }

  _layoutPins() {
    for (const { g, p } of this.pinEls || []) g.setAttribute('transform', `translate(${this.px(p.x).toFixed(1)},${this.py(p.y).toFixed(1)})`);
  }

  // ---- search replay overlay ----------------------------------------------------------
  /**
   * cfg: { trace: [[stopId, kind]...], step, color, otherStops: [ids] | null, otherColor } or null.
   * Draws one dot per stop expanded within the first `step` states (darker = expanded more than once).
   */
  setReplay(cfg) {
    this.replay = cfg;
    this._layoutReplay();
  }

  _layoutReplay() {
    clear(this.g.explore);
    const cfg = this.replay;
    if (!cfg) return;
    const f = clamp(0.8 + (this.view.k - 40) / 200, 0.8, 1.6);
    const origin = this.endpoints.from;
    const posOf = (id) => (id === 'O' ? (origin && [origin.x, origin.y]) : (this.stopById.get(id) && [this.stopById.get(id).x, this.stopById.get(id).y]));
    if (cfg.otherStops) {
      for (const id of new Set(cfg.otherStops)) {
        const p = posOf(id); if (!p) continue;
        this.g.explore.append(s('circle', { class: 'explore-other', cx: this.px(p[0]), cy: this.py(p[1]), r: (11 * f).toFixed(1), stroke: cfg.otherColor }));
      }
    }
    const counts = new Map();
    cfg.trace.slice(0, cfg.step).forEach(([id]) => counts.set(id, (counts.get(id) || 0) + 1));
    for (const [id, n] of counts) {
      const p = posOf(id); if (!p) continue;
      this.g.explore.append(s('circle', { class: 'explore-dot', cx: this.px(p[0]), cy: this.py(p[1]), r: (9 * f).toFixed(1),
        fill: cfg.color, 'fill-opacity': Math.min(0.28 + 0.22 * n, 0.85) }));
    }
    const last = cfg.step > 0 ? cfg.trace[cfg.step - 1] : null;
    const lp = last && posOf(last[0]);
    if (lp) this.g.explore.append(s('circle', { class: 'explore-now', cx: this.px(lp[0]), cy: this.py(lp[1]), r: (14 * f).toFixed(1), stroke: cfg.color }));
  }

  // ---- view control -----------------------------------------------------------------------
  fitBounds(minX, minY, maxX, maxY, pad = 56, animate = true) {
    const w = Math.max(maxX - minX, 0.5), hgt = Math.max(maxY - minY, 0.5);
    const k = clamp(Math.min((this.W - 2 * pad) / w, (this.H - 2 * pad) / hgt), MIN_K, MAX_K);
    this._animateTo({ cx: (minX + maxX) / 2, cy: (minY + maxY) / 2, k }, animate);
  }

  fitCity(animate = true) {
    const pad = 24;
    this.fitBounds(0, 0, this.size, this.size, pad, animate);
  }

  fitRoute() {
    if (!this.plan) return;
    const xs = [], ys = [];
    for (const l of this.plan.legs) for (const [x, y] of l.coords) { xs.push(x); ys.push(y); }
    this.fitBounds(Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys), Math.min(90, this.W / 6));
  }

  zoomBy(factor) { this._zoomAt(this.W / 2, this.H / 2, factor); }

  _zoomAt(sx, sy, factor) {
    const [x, y] = this.unproject(sx, sy);
    const k = clamp(this.view.k * factor, MIN_K, MAX_K);
    this.view = { k, cx: x - (sx - this.W / 2) / k, cy: y + (sy - this.H / 2) / k };
    this._layout();
  }

  _animateTo(target, animate) {
    cancelAnimationFrame(this._raf);
    if (!animate || reducedMotion()) { this.view = target; this._layout(); return; }
    const from = { ...this.view }, t0 = performance.now(), dur = 520;
    const step = (now) => {
      const t = Math.min(1, (now - t0) / dur), e = 1 - Math.pow(1 - t, 3);
      const kk = from.k * Math.pow(target.k / from.k, e);
      this.view = { cx: from.cx + (target.cx - from.cx) * e, cy: from.cy + (target.cy - from.cy) * e, k: kk };
      this._layout();
      if (t < 1) this._raf = requestAnimationFrame(step);
    };
    this._raf = requestAnimationFrame(step);
  }

  _resize() {
    const r = this.root.getBoundingClientRect();
    if (r.width < 10 || r.height < 10) return;
    this.W = r.width; this.H = r.height;
    this.svg.setAttribute('viewBox', `0 0 ${this.W} ${this.H}`);
    if (!this._fitted) { this._fitted = true; this.fitCity(false); if (this.plan) this.fitRoute(); }
    else this._layout();
  }

  setOption(name, value) { this.opts[name] = value; this._layout(); }

  // ---- interaction ---------------------------------------------------------------------------
  _local(e) { const r = this.svg.getBoundingClientRect(); return [e.clientX - r.left, e.clientY - r.top]; }

  _nearestStop(sx, sy, maxPx) {
    let best = null, bd = maxPx;
    for (const e of this.stopEls) {
      const d = Math.hypot(this.px(e.st.x) - sx, this.py(e.st.y) - sy);
      if (d <= bd) { bd = d; best = e.st; }
    }
    return best;
  }

  _bindEvents() {
    const svg = this.svg;
    svg.addEventListener('pointerdown', (e) => {
      svg.setPointerCapture(e.pointerId);
      this._ptrs.set(e.pointerId, { x: e.clientX, y: e.clientY, sx: e.clientX, sy: e.clientY, moved: false });
      this._pinch = null;
    });
    svg.addEventListener('pointermove', (e) => {
      const p = this._ptrs.get(e.pointerId);
      if (!p) {
        const [sx, sy] = this._local(e);
        const stop = this._nearestStop(sx, sy, 13);
        svg.style.cursor = stop ? 'pointer' : 'grab';
        this.hooks.onHover && this.hooks.onHover(stop, e.clientX, e.clientY);
        return;
      }
      this.hooks.onHover && this.hooks.onHover(null);
      if (this._ptrs.size === 2) {                                   // pinch to zoom
        const [a, b] = [...this._ptrs.values()];
        const before = Math.hypot(a.x - b.x, a.y - b.y);
        p.x = e.clientX; p.y = e.clientY;
        const after = Math.hypot(a.x - b.x, a.y - b.y);
        const r = svg.getBoundingClientRect();
        if (before > 0) this._zoomAt((a.x + b.x) / 2 - r.left, (a.y + b.y) / 2 - r.top, after / before);
        p.moved = true;
        return;
      }
      const dx = e.clientX - p.x, dy = e.clientY - p.y;
      if (!p.moved && Math.hypot(e.clientX - p.sx, e.clientY - p.sy) < 5) return;
      p.moved = true; p.x = e.clientX; p.y = e.clientY;
      svg.style.cursor = 'grabbing';
      this.view = { ...this.view, cx: this.view.cx - dx / this.view.k, cy: this.view.cy + dy / this.view.k };
      this._layout();
    });
    const end = (e) => {
      const p = this._ptrs.get(e.pointerId);
      this._ptrs.delete(e.pointerId);
      if (p && !p.moved && e.type === 'pointerup') {
        const [sx, sy] = this._local(e);
        const stop = this._nearestStop(sx, sy, 16);
        const [x, y] = this.unproject(sx, sy);
        this.hooks.onClick && this.hooks.onClick({ stop, point: { x, y }, clientX: e.clientX, clientY: e.clientY });
      }
      svg.style.cursor = 'grab';
    };
    svg.addEventListener('pointerup', end);
    svg.addEventListener('pointercancel', end);
    svg.addEventListener('pointerleave', () => this.hooks.onHover && this.hooks.onHover(null));
    svg.addEventListener('wheel', (e) => {
      e.preventDefault();
      const [sx, sy] = this._local(e);
      this._zoomAt(sx, sy, Math.exp(-e.deltaY * (e.ctrlKey ? 0.01 : 0.0016)));
    }, { passive: false });
    this.root.setAttribute('tabindex', '0');
    this.root.addEventListener('keydown', (e) => {
      const step = 60 / this.view.k;
      const moves = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, step], ArrowDown: [0, -step] };
      if (moves[e.key]) { e.preventDefault(); this.view = { ...this.view, cx: this.view.cx + moves[e.key][0], cy: this.view.cy + moves[e.key][1] }; this._layout(); }
      else if (e.key === '+' || e.key === '=') this.zoomBy(1.3);
      else if (e.key === '-' || e.key === '_') this.zoomBy(1 / 1.3);
      else if (e.key === '0') this.plan ? this.fitRoute() : this.fitCity();
    });
  }
}

/** HTML legend built from the real line data (so colours always match the map). */
export function buildLegend(city) {
  const metro = city.lines.filter((l) => l.mode === 'metro');
  const bus = city.lines.filter((l) => l.mode === 'bus');
  const row = (l) => h('li', { class: 'lg-line' }, badge(l),
    h('span', { class: 'lg-name' }, l.mode === 'metro' ? l.name : l.name.replace(/^Bus \d+\s*/, '') || l.name),
    h('span', { class: 'lg-meta' }, `every ${l.headway} min`));
  const sym = (cls, text, inner) => h('li', { class: 'lg-sym' }, h('span', { class: `lg-key ${cls}` }, inner), text);
  return h('div', { class: 'legend-body' },
    h('div', { class: 'lg-col' },
      h('h3', {}, 'Metro'), h('ul', {}, metro.map(row)),
      h('h3', {}, 'Bus'), h('ul', {}, bus.map(row))),
    h('div', { class: 'lg-col' },
      h('h3', {}, 'Map symbols'),
      h('ul', {},
        sym('k-bus', 'Bus stop'),
        sym('k-metro', 'Metro station'),
        sym('k-inter', 'Interchange (2 or more lines)'),
        sym('k-walk', 'Walking link between stops'),
        sym('k-pin', 'Start (A) and destination (B)', 'A'),
        sym('k-route', 'Selected route, arrows show direction'))));
}
