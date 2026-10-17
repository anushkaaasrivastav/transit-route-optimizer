// planner.js -- the trip planner: state, sidebar UI, and the glue between the map and the backend.
// The browser never computes a route. It sends (start, destination, algorithm, penalty) to /api/route
// and draws exactly what comes back.

import { api } from './api.js';
import { TransitMap, buildLegend } from './map.js';
import { draw } from './charts.js';
import { $, h, clear, badge, fmt, plural, debounce, COLORS, COST_PARTS, ALGO } from './util.js';

const ALGO_TEXT = {
  astar: 'Uses a straight-line time estimate to head toward the destination.',
  dijkstra: 'Explores outward in every direction. It is A* with the estimate switched off.',
};

export class Planner {
  constructor(city) {
    this.city = city;
    this.lines = new Map(city.lines.map((l) => [l.code, l]));
    this.stops = new Map(city.stops.map((st) => [st.id, st]));
    this.state = { from: null, to: null, algo: 'astar', penalty: 5 };
    this.plan = null;
    this.sweepCache = new Map();
    this.flips = [];
    this.replay = { open: false, trace: null, other: null, step: 0, playing: false, both: false, timer: null };
    this._abort = null;
    this._lastSummary = null;
  }

  // ---- start up -----------------------------------------------------------------------------
  mount() {
    this.rail = $('#rail');
    this.mapwrap = $('#mapwrap');
    this.map = new TransitMap($('#map'), this.city, {
      onClick: (info) => this.openPopover(info),
      onHover: (stop, x, y) => this.showTip(stop, x, y),
    });
    $('#legend-body').append(buildLegend(this.city));
    this._buildEndpointFields();
    this._bindOptions();
    this._bindMapControls();
    this._bindReplay();
    this.renderEmpty();
    this.loadFlips();
    document.addEventListener('pointerdown', (e) => {
      if (!e.target.closest('#popover') && !e.target.closest('.map-svg')) this.closePopover();
    });
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape') this.closePopover(); });
  }

  /** read from / to / algo / p out of a URL hash query */
  applyQuery(params) {
    const st = this.state;
    const parse = (v) => {
      if (!v) return null;
      if (this.stops.has(v)) return { kind: 'stop', id: v };
      const m = /^(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)$/.exec(v);
      return m ? { kind: 'point', x: +m[1], y: +m[2] } : null;
    };
    st.from = parse(params.get('from'));
    st.to = parse(params.get('to'));
    if (['astar', 'dijkstra'].includes(params.get('algo'))) st.algo = params.get('algo');
    const p = parseFloat(params.get('p'));
    if (!Number.isNaN(p)) st.penalty = Math.min(30, Math.max(0, p));
    this._syncControls();
    this.refresh({ fit: true });
  }

  // ---- helpers ------------------------------------------------------------------------------------
  spec(ep) { return ep.kind === 'stop' ? ep.id : `${ep.x.toFixed(3)},${ep.y.toFixed(3)}`; }

  describe(ep) {
    if (!ep) return null;
    if (ep.kind === 'stop') { const st = this.stops.get(ep.id); return { x: st.x, y: st.y, name: st.name }; }
    return { x: ep.x, y: ep.y, name: `Point (${ep.x.toFixed(1)}, ${ep.y.toFixed(1)} km)` };
  }

  syncHash() {
    const st = this.state, q = new URLSearchParams();
    if (st.from) q.set('from', this.spec(st.from));
    if (st.to) q.set('to', this.spec(st.to));
    q.set('algo', st.algo);
    q.set('p', st.penalty);
    history.replaceState(null, '', `#/planner?${q}`);
  }

  toast(msg, ms = 4200) {
    const t = $('#toast');
    t.textContent = msg;
    t.classList.add('show');
    clearTimeout(this._toastT);
    this._toastT = setTimeout(() => t.classList.remove('show'), ms);
  }

  // ---- from / to fields -------------------------------------------------------------------------------
  _buildEndpointFields() {
    this.fields = {};
    for (const key of ['from', 'to']) {
      this.fields[key] = new Combobox($(`#cb-${key}`), {
        id: `cb-${key}-input`, label: key === 'from' ? 'Start' : 'Destination',
        placeholder: key === 'from' ? 'Start: stop name or code' : 'Destination: stop name or code',
        stops: this.city.stops, lines: this.lines,
        onPick: (stop) => { this.state[key] = { kind: 'stop', id: stop.id }; this.refresh({ fit: true }); },
      });
    }
    $('#swap').addEventListener('click', () => {
      const st = this.state;
      [st.from, st.to] = [st.to, st.from];
      this._syncControls();
      this.refresh({ fit: true });
    });
  }

  _syncControls() {
    const st = this.state;
    for (const key of ['from', 'to']) this.fields[key].setText(st[key] ? this.describe(st[key]).name : '');
    $('#penalty').value = st.penalty;
    this._renderPenalty();
    for (const b of document.querySelectorAll('[data-algo]')) {
      const on = b.dataset.algo === st.algo;
      b.setAttribute('aria-checked', on);
      b.classList.toggle('on', on);
    }
    $('#algo-desc').textContent = ALGO_TEXT[st.algo];
  }

  // ---- options: algorithm + penalty ----------------------------------------------------------------------
  _bindOptions() {
    for (const b of document.querySelectorAll('[data-algo]')) {
      b.addEventListener('click', () => { this.state.algo = b.dataset.algo; this._syncControls(); this.refresh({ fit: false }); });
    }
    const slider = $('#penalty');
    const go = debounce(() => this.refresh({ fit: false }), 140);
    slider.addEventListener('input', () => { this.state.penalty = parseFloat(slider.value); this._renderPenalty(); this.updateSweepMarker(); go(); });
    for (const b of document.querySelectorAll('[data-preset]')) {
      b.addEventListener('click', () => { this.state.penalty = parseFloat(b.dataset.preset); slider.value = this.state.penalty; this._renderPenalty(); this.refresh({ fit: false }); });
    }
    this._syncControls();
  }

  _renderPenalty() {
    const p = this.state.penalty;
    const slider = $('#penalty');
    $('#penalty-out').textContent = `${Number.isInteger(p) ? p : p.toFixed(1)} min`;
    slider.style.setProperty('--fill', `${(p / 30) * 100}%`);
    for (const b of document.querySelectorAll('[data-preset]')) b.classList.toggle('on', parseFloat(b.dataset.preset) === p);
  }

  // ---- map overlays ---------------------------------------------------------------------------------------
  _bindMapControls() {
    $('#zoom-in').addEventListener('click', () => this.map.zoomBy(1.4));
    $('#zoom-out').addEventListener('click', () => this.map.zoomBy(1 / 1.4));
    $('#zoom-fit').addEventListener('click', () => (this.plan && this.plan.found ? this.map.fitRoute() : this.map.fitCity()));
    $('#opt-labels').addEventListener('change', (e) => this.map.setOption('labels', e.target.checked));
    $('#opt-walks').addEventListener('change', (e) => this.map.setOption('walks', e.target.checked));
    if (window.matchMedia('(max-width: 900px)').matches) $('#legend').removeAttribute('open');
  }

  showTip(stop, x, y) {
    const tip = $('#tip');
    if (!stop) { tip.hidden = true; return; }
    clear(tip).append(h('strong', {}, stop.name), h('span', { class: 'tip-id' }, stop.id),
      h('div', { class: 'tip-lines' }, stop.lines.map((c) => badge(this.lines.get(c)))),
      h('div', { class: 'tip-hint' }, 'Click to plan from or to here'));
    tip.hidden = false;
    const r = this.mapwrap.getBoundingClientRect();
    const tw = tip.offsetWidth, th = tip.offsetHeight;
    tip.style.left = `${Math.min(Math.max(8, x - r.left + 14), r.width - tw - 8)}px`;
    tip.style.top = `${Math.min(Math.max(8, y - r.top + 14), r.height - th - 8)}px`;
  }

  openPopover({ stop, point, clientX, clientY }) {
    const pop = $('#popover');
    $('#tip').hidden = true;
    const ep = stop ? { kind: 'stop', id: stop.id } : { kind: 'point', x: point.x, y: point.y };
    const inCity = point.x >= -0.2 && point.x <= this.city.size_km + 0.2 && point.y >= -0.2 && point.y <= this.city.size_km + 0.2;
    if (!stop && !inCity) { this.closePopover(); return; }
    clear(pop).append(
      h('strong', {}, stop ? stop.name : `Point (${point.x.toFixed(1)}, ${point.y.toFixed(1)} km)`),
      stop ? h('div', { class: 'tip-lines' }, stop.lines.map((c) => badge(this.lines.get(c))))
           : h('p', { class: 'pop-note' }, 'Not at a stop. You walk to the nearest stop within 600 m.'),
      h('div', { class: 'pop-actions' },
        h('button', { class: 'btn', onclick: () => this._pick('from', ep) }, 'Start here'),
        h('button', { class: 'btn ghost', onclick: () => this._pick('to', ep) }, 'Go here')));
    pop.hidden = false;
    const r = this.mapwrap.getBoundingClientRect();
    pop.style.left = `${Math.min(Math.max(8, clientX - r.left + 10), r.width - pop.offsetWidth - 8)}px`;
    pop.style.top = `${Math.min(Math.max(8, clientY - r.top + 10), r.height - pop.offsetHeight - 8)}px`;
  }

  closePopover() { $('#popover').hidden = true; }

  _pick(key, ep) {
    this.closePopover();
    this.state[key] = ep;
    this._syncControls();
    this.refresh({ fit: true });
  }

  // ---- routing ---------------------------------------------------------------------------------------------
  async refresh({ fit = true } = {}) {
    const st = this.state;
    this.syncHash();
    this.map.setEndpoints(this.describe(st.from), this.describe(st.to));
    if (!st.from || !st.to) { this.plan = null; this.map.setRoute(null); this.renderEmpty(); this._hideExtras(); return; }
    if (this.spec(st.from) === this.spec(st.to)) { this.plan = null; this.map.setRoute(null); this.renderMessage('Choose two different places.'); this._hideExtras(); return; }
    if (this._abort) this._abort.abort();
    const ctl = (this._abort = new AbortController());
    $('#result').classList.add('loading');
    try {
      const plan = await api.route({ origin: this.spec(st.from), dest: this.spec(st.to), algo: st.algo, penalty: st.penalty }, ctl.signal);
      this.plan = plan;
      const changed = plan.found && plan.summary !== this._lastSummary;
      this._lastSummary = plan.found ? plan.summary : null;
      this.renderResult(plan);
      this.map.setRoute(plan, { fit, animate: fit || changed });
      this.renderCompare(plan);
      this.loadSweep();
      if (this.replay.open) this.loadReplay();
    } catch (e) {
      if (e.name === 'AbortError') return;
      this.plan = null;
      this.map.setRoute(null);
      this.renderMessage(e.message, true);
      this._hideExtras();
    } finally {
      if (this._abort === ctl) $('#result').classList.remove('loading');
    }
  }

  _hideExtras() {
    $('#compare').hidden = true;
    $('#sweep-sec').hidden = true;
    $('#replay-open').hidden = true;
    this.closeReplay();
  }

  // ---- result panel -----------------------------------------------------------------------------------------
  renderEmpty() {
    const R = clear($('#result'));
    R.append(h('h2', {}, 'Journey'),
      h('p', { class: 'empty' }, 'Choose a start and a destination to see the best route. You can also click stops on the map.'),
      h('button', { class: 'btn ghost', onclick: () => this.tryExample() }, 'Try an example trip'));
  }

  tryExample() {
    const ex = this.flips[0];
    if (ex) this.applyFlip(ex);
    else {
      const ids = this.city.stops.map((x) => x.id);
      this.state.from = { kind: 'stop', id: ids[3] };
      this.state.to = { kind: 'stop', id: ids[ids.length - 4] };
      this._syncControls();
      this.refresh({ fit: true });
    }
  }

  renderMessage(msg, isError = false) {
    clear($('#result')).append(h('h2', {}, 'Journey'), h('p', { class: isError ? 'notice error' : 'notice' }, msg));
  }

  renderResult(plan) {
    const R = clear($('#result'));
    if (!plan.found) {
      R.append(h('h2', {}, 'Journey'), h('p', { class: 'notice warn' }, plan.message));
      $('#replay-open').hidden = true;
      return;
    }
    const bd = plan.breakdown;
    const clock = bd.ride + bd.wait + bd.walk;
    const chips = plan.legs.filter((l) => l.kind === 'ride').map((l) => this.lines.get(l.line));
    R.append(
      h('h2', {}, 'Journey'),
      h('div', { class: 'headline' },
        h('span', { class: 'big' }, fmt.min(plan.cost)), h('span', { class: 'unit' }, 'min total cost'),
        h('span', { class: 'chips' }, chips.length ? chips.flatMap((c, i) => [i ? h('span', { class: 'then', 'aria-hidden': 'true' }, '›') : null, badge(c)]) : h('span', { class: 'walk-only' }, 'Walk only'))),
      h('p', { class: 'sub' }, `${fmt.min(clock)} min on the clock`, bd.penalty > 0 ? ` plus ${fmt.min(bd.penalty)} min transfer penalty` : '',
        h('span', { class: 'help', tabindex: '0', title: 'Total cost = ride + wait + walk + transfer penalty. The penalty is not real time: it is how strongly the planner avoids changing vehicle.' }, ' (what is this?)')),
      this._fare(plan), this._stats(plan), this._diagram(plan));
    const btn = $('#replay-open');
    btn.hidden = false;
    if (!this._legendClosed) { this._legendClosed = true; $('#legend').removeAttribute('open'); }   // get the legend out of the way of the first route
  }

  _fare(plan) {
    const bd = plan.breakdown;
    const total = plan.cost || 1;
    const bar = h('div', { class: 'fare', role: 'img', 'aria-label': COST_PARTS.map((p) => `${p.label} ${fmt.min(bd[p.key])} minutes`).join(', ') },
      COST_PARTS.filter((p) => bd[p.key] > 0.0001).map((p) =>
        h('span', { class: 'fare-seg', style: { flexGrow: bd[p.key], background: p.color }, title: `${p.label}: ${fmt.min(bd[p.key])} min` })));
    const list = h('dl', { class: 'fare-list' },
      COST_PARTS.map((p) => h('div', { class: bd[p.key] > 0.0001 ? '' : 'zero' },
        h('dt', {}, h('i', { style: { background: p.color } }), p.label),
        h('dd', {}, `${fmt.min(bd[p.key])} min`, h('span', { class: 'pct' }, `${Math.round((bd[p.key] / total) * 100)}%`)))));
    return h('div', { class: 'fare-wrap' }, bar, list);
  }

  _stats(plan) {
    const st = plan.stats;
    const cell = (v, label, sub, tip) => h('div', { class: 'stat', title: tip || null }, h('b', {}, v), h('span', {}, label), sub ? h('em', {}, sub) : null);
    return h('div', { class: 'stats' },
      cell(plan.transfers, plan.transfers === 1 ? 'transfer' : 'transfers'),
      cell(plan.vehicle_stops, 'stops ridden', null, 'Stop-to-stop segments spent inside vehicles'),
      cell(fmt.km(plan.distance_km), 'distance', 'straight-line legs'),
      cell(fmt.int(st.expanded), 'expanded states', `of ${fmt.int(plan.graph.nodes)} in the graph`, 'States taken off the open list and expanded by the search'),
      cell(fmt.int(st.generated), 'generated states', null, 'States pushed onto the open list'),
      cell(fmt.time(st.time_us), 'execution time', 'median of 15 runs', 'Measured on the server around the search function only'));
  }

  _diagram(plan) {
    const A = this.describe(this.state.from), B = this.describe(this.state.to);
    const rows = [h('li', { class: 'dg-end' }, h('i', { class: 'dg-pin' }, 'A'), h('div', {}, h('strong', {}, A.name), h('small', {}, 'Start')))];
    plan.legs.forEach((leg, i) => {
      if (leg.kind === 'walk') {
        rows.push(h('li', { class: 'dg-walk' }, h('div', { class: 'dg-track' }), h('div', { class: 'dg-body' },
          h('strong', {}, `Walk ${fmt.min(leg.walk_min)} min`), h('small', {}, `${leg.km.toFixed(2)} km to ${leg.to_name}`))));
        return;
      }
      const line = this.lines.get(leg.line);
      const n = leg.stop_ids.length - 1;
      const mid = leg.stop_names.slice(1, -1);
      const waitText = `Wait about ${fmt.min(leg.wait_min)} min (every ${leg.headway} min)`;
      rows.push(h('li', { class: 'dg-ride', style: { '--c': leg.color } },
        h('div', { class: 'dg-track' }),
        h('div', { class: 'dg-body' },
          h('div', { class: 'dg-title' }, badge(line), h('strong', {}, leg.stop_names[0])),
          h('small', {}, i > 0 && leg.penalty_min > 0 ? `${waitText} · +${fmt.min(leg.penalty_min)} min transfer penalty` : waitText),
          mid.length
            ? h('details', {}, h('summary', {}, `Ride ${fmt.min(leg.ride_min)} min · ${plural(n, 'stop', 'stops')}`),
                h('ol', { class: 'dg-stops' }, mid.map((m) => h('li', {}, m))))
            : h('p', { class: 'dg-ride-note' }, `Ride ${fmt.min(leg.ride_min)} min · ${plural(n, 'stop', 'stops')}`),
          h('div', { class: 'dg-title dg-last' }, h('span', { class: 'dg-getoff' }, 'Get off'), h('strong', {}, leg.stop_names[leg.stop_names.length - 1])))));
    });
    rows.push(h('li', { class: 'dg-end' }, h('i', { class: 'dg-pin' }, 'B'), h('div', {}, h('strong', {}, B.name), h('small', {}, 'Destination'))));
    return h('ol', { class: 'diagram', 'aria-label': 'Journey steps' }, rows);
  }

  // ---- A* vs Dijkstra on this trip -----------------------------------------------------------------------------
  renderCompare(plan) {
    const sec = clear($('#compare'));
    if (!plan.found) { sec.hidden = true; return; }
    sec.hidden = false;
    const c = plan.compare;
    const row = (title, key, format) => {
      const vals = { astar: c.astar[key], dijkstra: c.dijkstra[key] };
      const max = Math.max(vals.astar, vals.dijkstra) || 1;
      return h('div', { class: 'cmp-row' }, h('div', { class: 'cmp-title' }, title),
        ['astar', 'dijkstra'].map((a) => h('div', { class: `cmp-bar ${plan.algo === a ? 'sel' : ''}` },
          h('span', { class: 'cmp-name' }, ALGO[a].label),
          h('span', { class: 'cmp-track' }, h('span', { class: 'cmp-fill', style: { width: `${Math.max(2, (vals[a] / max) * 100)}%`, background: ALGO[a].color } })),
          h('span', { class: 'cmp-val' }, format(vals[a])))));
    };
    const saved = c.dijkstra.expanded ? 1 - c.astar.expanded / c.dijkstra.expanded : 0;
    sec.append(
      h('h2', {}, 'A* and Dijkstra on this trip'),
      c.same_cost
        ? h('p', { class: 'ok-line' }, h('span', { class: 'tick', 'aria-hidden': 'true' }, '✓'), `Both found the same optimal cost: ${fmt.min(c.astar.cost, 2)} min.`)
        : h('p', { class: 'notice error' }, 'The two algorithms disagree on cost. This should never happen; please report it.'),
      row('Expanded states', 'expanded', fmt.int),
      row('Execution time', 'time_us', fmt.time),
      h('p', { class: 'hint' }, saved > 0.005
        ? `A* expanded ${Math.round(saved * 100)}% fewer states here.`
        : 'On this trip A* expanded about as many states as Dijkstra.',
        ' Expanded states are exact; times are server measurements and vary a little between runs.'));
  }

  // ---- how the penalty changes this trip (a live sweep through the real A*) ------------------------------------------
  async loadSweep() {
    const st = this.state;
    if (!st.from || !st.to) return;
    const key = `${this.spec(st.from)}|${this.spec(st.to)}`;
    $('#sweep-sec').hidden = false;
    try {
      if (!this.sweepCache.has(key)) this.sweepCache.set(key, await api.sweep(this.spec(st.from), this.spec(st.to)));
      if (key !== `${this.spec(this.state.from)}|${this.spec(this.state.to)}`) return;
      this.sweep = this.sweepCache.get(key);
      await this.drawSweep();
    } catch (e) {
      if (e.name !== 'AbortError') $('#sweep-note').textContent = e.message;
    }
  }

  updateSweepMarker() { if (this.sweep) this.drawSweep(); }

  async drawSweep() {
    const sw = this.sweep;
    const sec = $('#sweep-sec');
    if (!sw || !sw.found) { sec.hidden = true; return; }
    sec.hidden = false;
    const pts = sw.points;
    const maxT = Math.max(...pts.map((p) => p.transfers));
    // runs of identical routes -> shaded bands labelled with the route
    const runs = [];
    for (const p of pts) {
      const last = runs[runs.length - 1];
      if (last && last.summary === p.summary) last.end = p.penalty; else runs.push({ summary: p.summary, start: p.penalty, end: p.penalty, transfers: p.transfers });
    }
    runs.forEach((r, i) => { r.x0 = i === 0 ? 0 : (runs[i - 1].end + r.start) / 2; });
    runs.forEach((r, i) => { r.x1 = i === runs.length - 1 ? 30 : runs[i + 1].x0; });
    const shapes = runs.map((r, i) => ({ type: 'rect', xref: 'x', yref: 'paper', x0: r.x0, x1: r.x1, y0: 0, y1: 1, line: { width: 0 },
      fillcolor: i % 2 ? 'rgba(98,80,214,0.10)' : 'rgba(98,80,214,0.04)', layer: 'below' }));
    shapes.push({ type: 'line', xref: 'x', yref: 'paper', x0: this.state.penalty, x1: this.state.penalty, y0: 0, y1: 1, line: { color: COLORS.ink, width: 2, dash: 'dot' } });
    const annotations = runs.map((r) => ({ x: (r.x0 + r.x1) / 2, y: 1, xref: 'x', yref: 'paper', yanchor: 'bottom', showarrow: false,
      text: r.summary, font: { size: 12, color: COLORS.ink } }));
    const el = $('#sweep-chart');
    await draw(el, [{
      x: pts.map((p) => p.penalty), y: pts.map((p) => p.transfers), type: 'scatter', mode: 'lines+markers', line: { color: COLORS.astar, width: 2.5, shape: 'hv' },
      marker: { size: 5, color: COLORS.astar }, hovertemplate: '%{customdata[0]}<br>penalty %{x} min: %{y} transfers, total %{customdata[1]} min<extra></extra>',
      customdata: pts.map((p) => [p.summary, p.cost.toFixed(1)]),
    }], Object.assign(
      { height: 190, shapes, annotations, showlegend: false },
      { margin: { l: 44, r: 12, t: 28, b: 40 } },
      {
        font: { family: 'Barlow, system-ui, sans-serif', size: 12, color: COLORS.ink },
        paper_bgcolor: 'rgba(0,0,0,0)', plot_bgcolor: 'rgba(0,0,0,0)',
        hoverlabel: { bgcolor: COLORS.ink, font: { color: '#fff', size: 12 } },
        xaxis: { range: [0, 30], title: { text: 'Transfer penalty (min)', font: { size: 12, color: COLORS.track } }, gridcolor: COLORS.kerbSoft, zeroline: false, tickfont: { size: 11.5, color: COLORS.track } },
        yaxis: { range: [-0.35, maxT + 0.5], dtick: 1, title: { text: 'Transfers', font: { size: 12, color: COLORS.track } }, gridcolor: COLORS.kerbSoft, zeroline: false, tickfont: { size: 11.5, color: COLORS.track } },
      }));
    const changes = [];
    for (let i = 1; i < runs.length; i++) changes.push(`${runs[i - 1].summary} → ${runs[i].summary} at ${runs[i].start} min`);
    $('#sweep-note').textContent = runs.length > 1
      ? `Route changes: ${changes.join('; ')}. Dotted line marks your current penalty.`
      : 'The best route stays the same for every penalty from 0 to 30 minutes on this trip.';
  }

  // ---- trips where the route changes (from the experiment results) --------------------------------------------------------
  async loadFlips() {
    try {
      this.flips = await api.flips();
    } catch (_) {
      $('#flips-sec').hidden = true;
      return;
    }
    const ul = clear($('#flips'));
    for (const f of this.flips) {
      ul.append(h('li', {}, h('button', { class: 'flip', onclick: () => this.applyFlip(f) },
        h('strong', {}, `${f.origin_name} to ${f.dest_name}`),
        h('small', {}, `${f.transfers_before} → ${f.transfers_after} transfers once the penalty passes ${f.flip_penalty} min`))));
    }
    $('#flips-sec').hidden = !this.flips.length;
  }

  applyFlip(f) {
    this.state.from = { kind: 'stop', id: f.origin };
    this.state.to = { kind: 'stop', id: f.dest };
    this.state.penalty = Math.max(0, Math.floor(f.flip_penalty) - 2);
    this._syncControls();
    this.refresh({ fit: true });
    this.toast(`Raise the penalty above ${f.flip_penalty} min and the route drops to ${f.transfers_after} ${f.transfers_after === 1 ? 'transfer' : 'transfers'}.`, 6500);
  }

  // ---- search replay ---------------------------------------------------------------------------------------------------------------------------------------
  _bindReplay() {
    $('#replay-open').addEventListener('click', () => this.openReplay());
    $('#replay-close').addEventListener('click', () => this.closeReplay());
    $('#replay-play').addEventListener('click', () => this.togglePlay());
    $('#replay-step').addEventListener('input', (e) => { this.pause(); this.replay.step = +e.target.value; this._drawReplay(); });
    $('#replay-both').addEventListener('change', (e) => { this.replay.both = e.target.checked; this.loadReplay(); });
  }

  async openReplay() {
    this.replay.open = true;
    $('#replay').hidden = false;
    await this.loadReplay();
    this.replay.step = 0;
    this._drawReplay();
    this.play();
  }

  closeReplay() {
    this.pause();
    this.replay.open = false;
    this.replay.trace = null;
    $('#replay').hidden = true;
    if (this.map) this.map.setReplay(null);
  }

  async loadReplay() {
    const st = this.state;
    if (!st.from || !st.to) return;
    const base = { origin: this.spec(st.from), dest: this.spec(st.to), penalty: st.penalty, replay: true };
    const otherAlgo = st.algo === 'astar' ? 'dijkstra' : 'astar';
    const [mine, other] = await Promise.all([
      api.route({ ...base, algo: st.algo }),
      this.replay.both ? api.route({ ...base, algo: otherAlgo }) : Promise.resolve(null),
    ]);
    this.replay.trace = mine.trace || [];
    this.replay.other = other ? (other.trace || []).map((t) => t[0]) : null;
    this.replay.otherAlgo = otherAlgo;
    this.replay.step = Math.min(this.replay.step, this.replay.trace.length);
    const slider = $('#replay-step');
    slider.max = this.replay.trace.length;
    this._drawReplay();
  }

  _drawReplay() {
    const r = this.replay;
    if (!r.open || !r.trace) return;
    const a = ALGO[this.state.algo];
    $('#replay-step').value = r.step;
    $('#replay-step').style.setProperty('--fill', `${r.trace.length ? (r.step / r.trace.length) * 100 : 0}%`);
    $('#replay-label').textContent = `${a.label}: ${r.step} of ${r.trace.length} states expanded`;
    $('#replay-play').textContent = r.playing ? 'Pause' : (r.step >= r.trace.length ? 'Replay' : 'Play');
    $('#replay-key').replaceChildren(...[
      h('span', { class: 'rk' }, h('i', { style: { background: a.color } }), `${a.label} expanded`),
      r.both ? h('span', { class: 'rk' }, h('i', { class: 'ring', style: { borderColor: ALGO[r.otherAlgo].color } }), `${ALGO[r.otherAlgo].label} expanded`) : null,
    ].filter(Boolean));
    this.map.setReplay({ trace: r.trace, step: r.step, color: a.color, otherStops: r.other, otherColor: r.otherAlgo ? ALGO[r.otherAlgo].color : null });
  }

  play() {
    const r = this.replay;
    if (!r.trace) return;
    if (r.step >= r.trace.length) r.step = 0;
    r.playing = true;
    clearInterval(r.timer);
    const inc = Math.max(1, Math.ceil(r.trace.length / 90));
    r.timer = setInterval(() => {
      r.step = Math.min(r.trace.length, r.step + inc);
      if (r.step >= r.trace.length) this.pause();
      this._drawReplay();
    }, 45);
    this._drawReplay();
  }

  pause() { clearInterval(this.replay.timer); this.replay.playing = false; if (this.replay.trace) this._drawReplay(); }
  togglePlay() { this.replay.playing ? this.pause() : this.play(); }
}

// ---- searchable stop picker -------------------------------------------------------------------------------------------------------------------------------------
class Combobox {
  constructor(root, opts) {
    this.opts = opts;
    this.listId = `${opts.id}-list`;
    this.input = h('input', { id: opts.id, type: 'text', role: 'combobox', autocomplete: 'off', spellcheck: 'false', placeholder: opts.placeholder,
      'aria-label': opts.label, 'aria-expanded': 'false', 'aria-controls': this.listId, 'aria-autocomplete': 'list' });
    this.list = h('ul', { id: this.listId, class: 'cb-list', role: 'listbox', hidden: true });
    root.append(this.input, this.list);
    this.items = [];
    this.active = -1;
    this.committed = '';
    this.input.addEventListener('focus', () => { this.input.select(); this.render(''); });
    this.input.addEventListener('input', () => this.render(this.input.value));
    this.input.addEventListener('keydown', (e) => this.key(e));
    this.input.addEventListener('blur', () => setTimeout(() => { this.close(); this.input.value = this.committed; }, 120));
  }

  setText(t) { this.committed = t; this.input.value = t; }

  render(q) {
    const n = q.trim().toLowerCase();
    const stops = this.opts.stops.filter((s) => !n || s.name.toLowerCase().includes(n) || s.id.toLowerCase().includes(n) || s.lines.some((c) => c.toLowerCase() === n));
    this.items = stops.slice(0, 40);
    clear(this.list);
    this.items.forEach((st, i) => this.list.append(h('li', { role: 'option', id: `${this.listId}-${i}`, 'aria-selected': 'false',
      onmousedown: (e) => { e.preventDefault(); this.choose(i); } },
      h('span', { class: 'cb-name' }, st.name), h('span', { class: 'cb-id' }, st.id),
      h('span', { class: 'cb-lines' }, st.lines.map((c) => badge(this.opts.lines.get(c)))))));
    if (!this.items.length) this.list.append(h('li', { class: 'cb-none', role: 'option', 'aria-disabled': 'true' }, 'No matching stop'));
    this.active = this.items.length ? 0 : -1;
    this.mark();
    this.list.hidden = false;
    this.input.setAttribute('aria-expanded', 'true');
  }

  mark() {
    [...this.list.children].forEach((li, i) => { li.classList.toggle('active', i === this.active); li.setAttribute('aria-selected', i === this.active); });
    if (this.active >= 0) { this.input.setAttribute('aria-activedescendant', `${this.listId}-${this.active}`); this.list.children[this.active].scrollIntoView({ block: 'nearest' }); }
  }

  key(e) {
    if (e.key === 'ArrowDown') { e.preventDefault(); if (this.list.hidden) this.render(this.input.value); else { this.active = Math.min(this.items.length - 1, this.active + 1); this.mark(); } }
    else if (e.key === 'ArrowUp') { e.preventDefault(); this.active = Math.max(0, this.active - 1); this.mark(); }
    else if (e.key === 'Enter') { e.preventDefault(); if (this.active >= 0) this.choose(this.active); }
    else if (e.key === 'Escape') { this.close(); this.input.value = this.committed; }
  }

  choose(i) {
    const st = this.items[i];
    if (!st) return;
    this.setText(st.name);
    this.close();
    this.input.blur();
    this.opts.onPick(st);
  }

  close() { this.list.hidden = true; this.input.setAttribute('aria-expanded', 'false'); }
}
