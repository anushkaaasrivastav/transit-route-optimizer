// experiments.js -- the experiments dashboard.
// Every number and every chart comes from /api/experiments, which reads results/*.csv (written by experiments.py).
// Nothing is typed in here. Sentences that quote a number build it from the data at draw time.

import { draw, baseLayout } from './charts.js';
import { h, clear, fmt, COLORS, COST_PARTS, ALGO } from './util.js';

const BUCKETS = [['all', 'All trips'], ['short', 'Short'], ['medium', 'Medium'], ['long', 'Long']];
const bucketName = Object.fromEntries(BUCKETS);

export function renderExperiments(root, data, city) {
  const { meta } = data;
  const nets = data.networks.map((n) => n.network);               // ['city_80', 'city_300', 'city_1000']
  const netLabel = Object.fromEntries(data.networks.map((n) => [n.network, `${fmt.int(n.stops)} stops`]));
  const netColor = (n) => COLORS.net[n];
  const rowsOf = (list, where) => list.filter((r) => Object.entries(where).every(([k, v]) => r[k] === v));
  const pairsTotal = Object.values(meta.pairs_per_network).reduce((a, b) => a + b, 0);

  // ---------- small building blocks -------------------------------------------------------------------------
  /** a segmented control; calls onChange(value) */
  function seg(options, value, onChange, label) {
    const wrap = h('div', { class: 'seg-sm', role: 'group', 'aria-label': label });
    const set = (v) => {
      for (const b of wrap.children) b.classList.toggle('on', b.dataset.v === String(v));
      for (const b of wrap.children) b.setAttribute('aria-pressed', b.dataset.v === String(v));
    };
    for (const [v, text] of options) {
      wrap.append(h('button', { type: 'button', 'data-v': v, onclick: () => { set(v); onChange(v); } }, text));
    }
    set(value);
    return wrap;
  }

  /** a table of the numbers behind a chart */
  function table(cols, rows) {
    return h('div', { class: 'data-table-wrap' }, h('table', { class: 'data' },
      h('thead', {}, h('tr', {}, cols.map((c) => h('th', {}, c.label)))),
      h('tbody', {}, rows.map((r) => h('tr', {}, cols.map((c) => h('td', {}, c.get(r))))))));
  }

  /**
   * One chart card: title, subtitle, optional filters, the chart, and a "show as table" switch.
   * spec.render(state) -> { data, layout } for Plotly;  spec.rows(state) -> { cols, rows } for the table.
   */
  function card(spec) {
    const state = { ...(spec.initial || {}) };
    const chart = h('div', { class: `chart ${spec.tall ? 'tall' : ''}`, role: 'img', 'aria-label': spec.alt || spec.title });
    const tbl = h('div', { hidden: true });
    const note = h('p', { class: 'note' });
    let asTable = false;
    const paint = () => {
      const r = spec.render(state);
      draw(chart, r.data, r.layout);
      const t = spec.rows(state);
      clear(tbl).append(table(t.cols, t.rows));
      note.replaceChildren(...(spec.note ? [].concat(spec.note(state)) : []));
      note.hidden = !spec.note;
    };
    const tools = h('div', { class: 'chart-tools' },
      h('div', { style: { display: 'flex', gap: '10px', flexWrap: 'wrap' } },
        (spec.filters || []).map((f) => seg(f.options, state[f.key], (v) => { state[f.key] = v; paint(); }, f.label))),
      h('button', { type: 'button', class: 'link-btn', 'aria-pressed': 'false', onclick: (e) => {
        asTable = !asTable;
        chart.hidden = asTable;
        tbl.hidden = !asTable;
        e.target.textContent = asTable ? 'Show chart' : 'Show as table';
        e.target.setAttribute('aria-pressed', asTable);
        if (!asTable) paint();
      } }, 'Show as table'));
    const el = h('div', { class: `chart-block ${spec.wide ? 'wide' : ''}` },
      h('h3', {}, spec.title), h('p', { class: 'chart-sub' }, spec.sub), tools, chart, tbl, note);
    paint();
    return el;
  }

  const section = (title, lede, ...children) => h('section', { class: 'exp-section' }, h('h2', {}, title), h('p', { class: 'lede' }, lede), ...children);
  const grid = (...cards) => h('div', { class: 'chart-grid' }, ...cards);
  const bucketFilter = { key: 'bucket', label: 'Trip length', options: BUCKETS };

  // ---------- 1. A* vs Dijkstra ------------------------------------------------------------------------------
  const compRow = (net, bucket) => rowsOf(data.comparison, { network: net, bucket })[0];

  const expandedCard = card({
    title: 'States expanded', wide: false, initial: { bucket: 'all' }, filters: [bucketFilter],
    sub: 'Median number of states each algorithm expanded per trip. Fewer means less searching.',
    alt: 'Grouped bar chart of median expanded states for A* and Dijkstra on each network size',
    render: ({ bucket }) => ({
      data: ['astar', 'dijkstra'].map((a) => ({
        type: 'bar', name: ALGO[a].label, x: nets.map((n) => netLabel[n]),
        y: nets.map((n) => compRow(n, bucket)[`median_expanded_${a}`]),
        marker: { color: ALGO[a].color, cornerradius: 4 }, text: nets.map((n) => compRow(n, bucket)[`median_expanded_${a}`]),
        textposition: 'outside', cliponaxis: false, textfont: { size: 12, color: COLORS.ink },
        hovertemplate: `${ALGO[a].label}<br>%{x}<br>median %{y} states<extra></extra>`,
      })),
      layout: baseLayout({ barmode: 'group', yaxis: { title: { text: 'Median states expanded' }, rangemode: 'tozero' } }),
    }),
    rows: ({ bucket }) => ({
      cols: [{ label: 'Network', get: (n) => netLabel[n] }, { label: 'A*', get: (n) => compRow(n, bucket).median_expanded_astar },
        { label: 'Dijkstra', get: (n) => compRow(n, bucket).median_expanded_dijkstra },
        { label: 'A* as % of Dijkstra', get: (n) => `${(compRow(n, bucket).median_expanded_ratio * 100).toFixed(0)}%` }],
      rows: nets,
    }),
    note: ({ bucket }) => {
      const parts = nets.map((n) => `${netLabel[n]}: ${(100 - compRow(n, bucket).median_expanded_ratio * 100).toFixed(0)}% fewer`);
      return [h('strong', {}, 'A* expands fewer states: '), `${parts.join(', ')} (${bucketName[bucket].toLowerCase()}, median).`];
    },
  });

  const timeCard = card({
    title: 'Execution time', initial: { bucket: 'all' }, filters: [bucketFilter],
    sub: 'Median time for one search, measured on this machine when the experiments were run. Lower is faster.',
    alt: 'Grouped bar chart of median execution time for A* and Dijkstra on each network size',
    render: ({ bucket }) => ({
      data: ['astar', 'dijkstra'].map((a) => ({
        type: 'bar', name: ALGO[a].label, x: nets.map((n) => netLabel[n]),
        y: nets.map((n) => compRow(n, bucket)[`median_time_${a}_us`]),
        marker: { color: ALGO[a].color, cornerradius: 4 },
        text: nets.map((n) => fmt.time(compRow(n, bucket)[`median_time_${a}_us`])), textposition: 'outside', cliponaxis: false,
        textfont: { size: 12, color: COLORS.ink },
        hovertemplate: `${ALGO[a].label}<br>%{x}<br>median %{text}<extra></extra>`,
      })),
      layout: baseLayout({ barmode: 'group', yaxis: { title: { text: 'Median time (µs)' }, rangemode: 'tozero' } }),
    }),
    rows: ({ bucket }) => ({
      cols: [{ label: 'Network', get: (n) => netLabel[n] }, { label: 'A*', get: (n) => fmt.time(compRow(n, bucket).median_time_astar_us) },
        { label: 'Dijkstra', get: (n) => fmt.time(compRow(n, bucket).median_time_dijkstra_us) },
        { label: 'A* time / Dijkstra time', get: (n) => `${compRow(n, bucket).median_time_ratio.toFixed(2)}×` }],
      rows: nets,
    }),
    note: ({ bucket }) => {
      const slower = nets.filter((n) => compRow(n, bucket).median_time_ratio > 1.0);
      const faster = nets.filter((n) => compRow(n, bucket).median_time_ratio <= 1.0);
      const out = [];
      if (faster.length) out.push(h('strong', {}, `A* was faster on ${faster.map((n) => netLabel[n]).join(' and ')}. `));
      if (slower.length) {
        out.push(h('strong', {}, `A* was slower on ${slower.map((n) => netLabel[n]).join(' and ')} `),
          `(${slower.map((n) => `${compRow(n, bucket).median_time_ratio.toFixed(2)}×`).join(', ')}). It expands fewer states, but each expansion also looks up the heuristic, so on a small graph that extra work can cost more than the saving. Absolute times depend on the machine; the ratios travel better.`);
      }
      return out;
    },
  });

  const ratioCard = card({
    title: 'How much search A* saves, by trip length', wide: true, tall: false, initial: { metric: 'median_expanded_ratio' },
    filters: [{ key: 'metric', label: 'Measure', options: [['median_expanded_ratio', 'States expanded'], ['median_time_ratio', 'Execution time']] }],
    sub: 'A* as a percentage of Dijkstra, per trip-length group. Below the 100% line, A* used less.',
    alt: 'Grouped bar chart of A* as a percentage of Dijkstra by trip length and network size',
    render: ({ metric }) => ({
      data: nets.map((n) => {
        const ys = BUCKETS.slice(1).map(([b]) => compRow(n, b)[metric] * 100);
        return { type: 'bar', name: netLabel[n], x: BUCKETS.slice(1).map(([, t]) => t), y: ys,
          marker: { color: netColor(n), cornerradius: 4 }, text: ys.map((v) => `${v.toFixed(0)}%`), textposition: 'outside', cliponaxis: false,
          textfont: { size: 12, color: COLORS.ink }, hovertemplate: `${netLabel[n]}<br>%{x}: %{y:.0f}% of Dijkstra<extra></extra>` };
      }),
      layout: baseLayout({ barmode: 'group', yaxis: { title: { text: '% of Dijkstra' }, rangemode: 'tozero', ticksuffix: '%' },
        shapes: [{ type: 'line', xref: 'paper', x0: 0, x1: 1, yref: 'y', y0: 100, y1: 100, line: { color: COLORS.ink, width: 1.5, dash: 'dot' } }] }),
    }),
    rows: ({ metric }) => ({
      cols: [{ label: 'Network', get: (r) => netLabel[r.network] }, { label: 'Trips', get: (r) => r.n }, { label: 'Length', get: (r) => bucketName[r.bucket] },
        { label: 'A* as % of Dijkstra', get: (r) => `${(r[metric] * 100).toFixed(0)}%` }],
      rows: data.comparison.filter((r) => r.bucket !== 'all'),
    }),
  });

  const scatterCard = card({
    title: 'Every trip: states expanded by each algorithm', wide: true, tall: true, initial: { bucket: 'all' }, filters: [bucketFilter],
    sub: `One dot per trip (${fmt.int(data.trips.length)} in total). Dots below the dashed line are trips where A* expanded fewer states than Dijkstra. Hover for details.`,
    alt: 'Scatter plot of states expanded by Dijkstra against A* for each trip, coloured by network size',
    render: ({ bucket }) => {
      const pts = (n) => data.trips.filter((t) => t.network === n && (bucket === 'all' || t.bucket === bucket));
      const max = Math.max(...data.trips.map((t) => t.exp_dijkstra));
      return {
        data: [{ type: 'scatter', mode: 'lines', x: [1, max], y: [1, max], line: { color: COLORS.track, width: 1.5, dash: 'dash' }, hoverinfo: 'skip', showlegend: false },
          ...nets.map((n) => ({ type: 'scatter', mode: 'markers', name: netLabel[n], x: pts(n).map((t) => t.exp_dijkstra), y: pts(n).map((t) => t.exp_astar),
            marker: { color: netColor(n), size: 7, opacity: 0.6, line: { color: '#fff', width: 1 } },
            customdata: pts(n).map((t) => [t.km, t.bucket, t.cost, t.transfers]),
            hovertemplate: `${netLabel[n]}<br>Dijkstra %{x} states, A* %{y} states<br>%{customdata[0]} km straight line, %{customdata[1]}<br>cost %{customdata[2]:.1f} min, %{customdata[3]} transfers<extra></extra>` }))],
        layout: baseLayout({ xaxis: { type: 'log', title: { text: 'Dijkstra: states expanded' }, showgrid: true, gridcolor: COLORS.kerbSoft },
          yaxis: { type: 'log', title: { text: 'A*: states expanded' } }, hovermode: 'closest' }),
      };
    },
    rows: ({ bucket }) => ({
      cols: [{ label: 'Network', get: (t) => netLabel[t.network] }, { label: 'Length', get: (t) => t.bucket }, { label: 'Straight km', get: (t) => t.km },
        { label: 'Cost (min)', get: (t) => fmt.min(t.cost) }, { label: 'A* states', get: (t) => t.exp_astar }, { label: 'Dijkstra states', get: (t) => t.exp_dijkstra }],
      rows: data.trips.filter((t) => bucket === 'all' || t.bucket === bucket).slice(0, 200),
    }),
    note: () => `The table shows the first 200 trips; the full list is in results/queries.csv.`,
  });

  // ---------- 2. transfer penalty sweep ---------------------------------------------------------------------------
  const sweepRows = (n) => rowsOf(data.sweep, { network: n }).sort((a, b) => a.penalty - b.penalty);
  const penalties = meta.penalties_swept;

  const sweepLine = (key, title, sub, ytitle, scale = 1, suffix = '', alt) => card({
    title, sub, alt,
    render: () => ({
      data: nets.map((n) => ({ type: 'scatter', mode: 'lines+markers', name: netLabel[n], x: sweepRows(n).map((r) => r.penalty), y: sweepRows(n).map((r) => r[key] * scale),
        line: { color: netColor(n), width: 2.5 }, marker: { color: netColor(n), size: 8, line: { color: '#fff', width: 2 } },
        hovertemplate: `${netLabel[n]}<br>penalty %{x} min: %{y:.2f}${suffix}<extra></extra>` })),
      layout: baseLayout({ xaxis: { title: { text: 'Transfer penalty (min)' }, tickvals: penalties, showgrid: false }, yaxis: { title: { text: ytitle }, rangemode: 'tozero', ticksuffix: suffix }, hovermode: 'x unified' }),
    }),
    rows: () => ({
      cols: [{ label: 'Penalty (min)', get: (p) => p }, ...nets.map((n) => ({ label: netLabel[n], get: (p) => (sweepRows(n).find((r) => r.penalty === p)[key] * scale).toFixed(2) + suffix }))],
      rows: penalties,
    }),
  });

  const transfersCard = sweepLine('mean_transfers', 'Transfers per trip', 'Average number of transfers in the best route, as the penalty grows.', 'Mean transfers', 1, '',
    'Line chart of mean transfers against transfer penalty for each network size');
  const changedCard = sweepLine('share_route_changed_vs_p0', 'Trips whose route changes', 'Share of trips whose best route differs from the route with no penalty.', 'Trips with a different route', 100, '%',
    'Line chart of the share of trips whose route differs from the zero-penalty route, against penalty');

  const partsCard = card({
    title: 'Where the cost goes', wide: true, initial: { net: nets[0] },
    filters: [{ key: 'net', label: 'Network', options: nets.map((n) => [n, netLabel[n]]) }],
    sub: 'Average cost per trip split into ride, wait, walk and transfer penalty, at each penalty setting.',
    alt: 'Stacked bar chart of average ride, wait, walk and transfer-penalty minutes at each penalty setting',
    render: ({ net }) => {
      const rows = sweepRows(net);
      const keys = { ride: 'mean_ride', wait: 'mean_wait', walk: 'mean_walk', penalty: 'mean_penalty_min' };
      return {
        data: COST_PARTS.map((p) => ({ type: 'bar', name: p.label, x: rows.map((r) => `${r.penalty}`), y: rows.map((r) => r[keys[p.key]]),
          marker: { color: p.color, line: { color: '#fff', width: 2 } }, hovertemplate: `${p.label}<br>penalty %{x} min: %{y:.1f} min<extra></extra>` })),
        layout: baseLayout({ barmode: 'stack', xaxis: { title: { text: 'Transfer penalty (min)' }, type: 'category' }, yaxis: { title: { text: 'Mean cost per trip (min)' } } }),
      };
    },
    rows: ({ net }) => ({
      cols: [{ label: 'Penalty (min)', get: (r) => r.penalty }, { label: 'Ride', get: (r) => r.mean_ride.toFixed(1) }, { label: 'Wait', get: (r) => r.mean_wait.toFixed(1) },
        { label: 'Walk', get: (r) => r.mean_walk.toFixed(1) }, { label: 'Penalty', get: (r) => r.mean_penalty_min.toFixed(1) }, { label: 'Total', get: (r) => r.mean_cost.toFixed(1) }],
      rows: sweepRows(net),
    }),
    note: ({ net }) => {
      const r0 = sweepRows(net)[0], rN = sweepRows(net).at(-1);
      return [h('strong', {}, 'Higher penalty, fewer transfers: '), `${netLabel[net]} trips average ${r0.mean_transfers.toFixed(2)} transfers at a penalty of ${r0.penalty} and ${rN.mean_transfers.toFixed(2)} at ${rN.penalty}. The penalty is a planning weight, not real time, so the chart shows it as its own part of the cost.`];
    },
  });

  // ---------- 3. shortest-distance baseline ----------------------------------------------------------------------------
  const baseRow = (n, b) => rowsOf(data.baseline, { network: n, bucket: b })[0];
  const METRICS = {
    mean_extra_min: { label: 'Extra minutes', fmt: (v) => `${v.toFixed(1)} min`, scale: 1, suffix: '', title: 'Mean extra cost (min)' },
    share_same_route: { label: 'Same route', fmt: (v) => `${v.toFixed(0)}%`, scale: 100, suffix: '%', title: 'Trips where both pick the same route' },
    p90_extra_pct: { label: 'Worst 10%', fmt: (v) => `${v.toFixed(0)}%`, scale: 1, suffix: '%', title: '90th percentile extra cost' },
  };
  const baselineCard = card({
    title: 'Shortest distance versus lowest cost', wide: true, initial: { metric: 'mean_extra_min' },
    filters: [{ key: 'metric', label: 'Measure', options: Object.entries(METRICS).map(([k, m]) => [k, m.label]) }],
    sub: 'The baseline always takes the route with the fewest kilometres, then pays the real wait, walk and transfer costs. Compared with the route A* finds.',
    alt: 'Grouped bar chart comparing the shortest-distance baseline with the lowest-cost route by trip length and network size',
    render: ({ metric }) => {
      const m = METRICS[metric];
      return {
        data: nets.map((n) => {
          const ys = BUCKETS.map(([b]) => baseRow(n, b)[metric] * m.scale);
          return { type: 'bar', name: netLabel[n], x: BUCKETS.map(([, t]) => t), y: ys, marker: { color: netColor(n), cornerradius: 4 },
            text: ys.map(m.fmt), textposition: 'outside', cliponaxis: false, textfont: { size: 12, color: COLORS.ink },
            hovertemplate: `${netLabel[n]}<br>%{x}: ${m.label.toLowerCase()} %{text}<extra></extra>` };
        }),
        layout: baseLayout({ barmode: 'group', yaxis: { title: { text: m.title }, rangemode: 'tozero', ticksuffix: m.suffix } }),
      };
    },
    rows: () => ({
      cols: [{ label: 'Network', get: (r) => netLabel[r.network] }, { label: 'Length', get: (r) => bucketName[r.bucket] }, { label: 'Trips', get: (r) => r.n },
        { label: 'Same route', get: (r) => `${(r.share_same_route * 100).toFixed(0)}%` }, { label: 'Mean extra (min)', get: (r) => r.mean_extra_min.toFixed(1) },
        { label: 'Median extra', get: (r) => `${r.median_extra_pct.toFixed(1)}%` }, { label: 'P90 extra', get: (r) => `${r.p90_extra_pct.toFixed(0)}%` },
        { label: 'Transfers (best / baseline)', get: (r) => `${r.mean_opt_transfers.toFixed(2)} / ${r.mean_base_transfers.toFixed(2)}` }],
      rows: data.baseline,
    }),
    note: () => {
      const all = nets.map((n) => baseRow(n, 'all'));
      return [h('strong', {}, 'Shortest is not cheapest: '),
        `across all trips the distance baseline costs ${all.map((r, i) => `${r.mean_extra_min.toFixed(1)} min more on the ${netLabel[nets[i]]} network`).join(', ')}, and picks the same route as the optimum for ${all.map((r) => `${(r.share_same_route * 100).toFixed(0)}%`).join(', ')} of trips.`];
    },
  });

  // ---------- 4. trips where the route flips ----------------------------------------------------------------------------
  const known = new Map(city.stops.map((s) => [s.id, s.name]));
  const flipRows = data.flips.filter((f) => known.has(f.origin) && known.has(f.dest)).slice(0, 15);
  const flipTable = h('div', { class: 'chart-block wide' },
    h('h3', {}, 'Trips where the best route changes with the penalty'),
    h('p', { class: 'chart-sub' }, `${fmt.int(meta.flip_trips_found_city_80)} of ${fmt.int(meta.flip_trips_searched_city_80)} trips searched on the ${netLabel.city_80} network have a penalty above which the route drops a transfer. The exact value is computed, then checked against A* on both sides. Showing the first ${flipRows.length}.`),
    h('div', { class: 'data-table-wrap' }, h('table', { class: 'data flip-table' },
      h('thead', {}, h('tr', {}, ['From', 'To', 'Straight km', 'Route changes at', 'Transfers', 'Extra time without transfer', ''].map((t) => h('th', {}, t)))),
      h('tbody', {}, flipRows.map((f) => h('tr', {},
        h('td', {}, known.get(f.origin)), h('td', {}, known.get(f.dest)), h('td', {}, f.straight_km.toFixed(1)),
        h('td', {}, `${f.first_flip_penalty.toFixed(1)} min`), h('td', {}, `${f.transfers_before} → ${f.transfers_after}`),
        h('td', {}, `${f.extra_minutes_without_transfers.toFixed(1)} min`),
        h('td', {}, h('a', { href: `#/planner?from=${f.origin}&to=${f.dest}&algo=astar&p=${Math.max(0, Math.floor(f.first_flip_penalty) - 2)}` }, 'Open in planner'))))))));

  // ---------- provenance --------------------------------------------------------------------------------------------------------
  const csvs = ['queries', 'summary_algorithms', 'summary_comparison', 'penalty_sweep_pairs', 'penalty_sweep_summary', 'penalty_flips', 'baseline_queries', 'baseline_summary', 'networks'];
  const prov = h('details', { class: 'prov' }, h('summary', {}, 'How these results were produced'),
    h('dl', {},
      h('dt', {}, 'Generated'), h('dd', {}, `${meta.generated_utc.slice(0, 10)}, ${meta.mode} run, ${meta.runtime_seconds ? `${Math.round(meta.runtime_seconds)} s` : ''}`),
      h('dt', {}, 'Seeds'), h('dd', {}, `city ${meta.city_seed}, trip pairs ${meta.pair_seed_base} (fixed, so a rerun gives the same costs and states)`),
      h('dt', {}, 'Trips per network'), h('dd', {}, nets.map((n) => `${netLabel[n]}: ${meta.pairs_per_network[n]}`).join(', ')),
      h('dt', {}, 'Trip length groups'), h('dd', {}, 'Each network’s trips are split into thirds by straight-line distance: short, medium, long.'),
      h('dt', {}, 'Timing'), h('dd', {}, meta.timing_statistic),
      h('dt', {}, 'Expanded states'), h('dd', {}, meta.expanded_definition),
      h('dt', {}, 'Penalties swept'), h('dd', {}, `${penalties.join(', ')} min`),
      h('dt', {}, 'Raw files'), h('dd', {}, csvs.map((c, i) => [i ? ', ' : '', h('a', { href: `/results/${c}.csv`, download: '' }, `${c}.csv`)]))));

  // ---------- page ---------------------------------------------------------------------------------------------------------------------
  const kpi = (v, label, extra) => h('div', { class: 'kpi' }, h('b', { class: extra || '' }, v), h('span', {}, label));
  clear(root).append(
    h('h1', {}, 'Experiments'),
    h('p', { class: 'lede' }, `A* and Dijkstra on three fictional cities of different sizes, with the same trips given to both. Every figure on this page is read from the CSV files the experiment runner wrote.`),
    h('div', { class: 'kpis' },
      kpi(fmt.int(pairsTotal), 'trips searched by both algorithms'),
      kpi(meta.total_astar_dijkstra_cost_mismatches === 0 ? 'None' : String(meta.total_astar_dijkstra_cost_mismatches), 'trips where A* and Dijkstra disagreed on the cost', meta.total_astar_dijkstra_cost_mismatches === 0 ? 'ok' : ''),
      kpi(data.networks.map((n) => fmt.int(n.stops)).join(' / '), 'stops in the three networks'),
      kpi(`${data.networks.map((n) => n.median_heuristic_tightness.toFixed(2)).join(' / ')}`, 'how close the heuristic is to the true cost (1.00 is perfect)')),
    section('A* against Dijkstra', 'Both algorithms find the same optimal route every time. The difference is how much of the network they search first.',
      grid(expandedCard, timeCard, ratioCard, scatterCard)),
    section('Changing the transfer penalty', `The planner adds a penalty each time you change vehicle. Sweeping it from ${penalties[0]} to ${penalties.at(-1)} minutes shows how strongly it steers people onto routes with fewer transfers.`,
      grid(transfersCard, changedCard, partsCard)),
    section('Is the shortest route the best route?', 'A planner that only minimised distance would ignore waiting and transfers. This compares it with the lowest-cost route.',
      grid(baselineCard), h('div', { style: { marginTop: '36px' } }, flipTable)),
    prov);
}
