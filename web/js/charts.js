// charts.js -- shared Plotly setup so every chart in the app looks like part of one system.
// Plotly is vendored in web/vendor/ (no internet needed) and loaded the first time a chart is drawn.

import { COLORS } from './util.js';

let plotlyPromise = null;

export function ensurePlotly() {
  if (window.Plotly) return Promise.resolve(window.Plotly);
  if (!plotlyPromise) {
    plotlyPromise = new Promise((resolve, reject) => {
      const tag = document.createElement('script');
      tag.src = '/vendor/plotly.min.js';
      tag.onload = () => resolve(window.Plotly);
      tag.onerror = () => reject(new Error('Could not load the charting library.'));
      document.head.append(tag);
    });
  }
  return plotlyPromise;
}

const FONT = "Barlow, system-ui, -apple-system, 'Segoe UI', sans-serif";

/** Quiet, recessive chart furniture: thin hairline grid, no axis lines, text in ink tones, never series colour. */
export function baseLayout(extra = {}) {
  const axis = {
    gridcolor: COLORS.kerbSoft, gridwidth: 1, zeroline: false, linecolor: COLORS.kerb, ticks: '',
    tickfont: { size: 12, color: COLORS.track }, title: { font: { size: 12.5, color: COLORS.track }, standoff: 10 },
    automargin: true,
  };
  const { xaxis, yaxis, ...rest } = extra;
  return {
    font: { family: FONT, size: 13, color: COLORS.ink },
    paper_bgcolor: 'rgba(0,0,0,0)', plot_bgcolor: 'rgba(0,0,0,0)',
    margin: { l: 52, r: 16, t: 28, b: 44 },
    legend: { orientation: 'h', x: 0, y: 1.14, xanchor: 'left', yanchor: 'bottom', font: { size: 12.5, color: COLORS.ink },
      itemsizing: 'constant', tracegroupgap: 4 },
    hoverlabel: { bgcolor: COLORS.ink, bordercolor: COLORS.ink, font: { family: FONT, size: 12.5, color: '#fff' }, align: 'left' },
    bargap: 0.32, bargroupgap: 0.08,
    xaxis: { ...axis, showgrid: false, ...(xaxis || {}) },
    yaxis: { ...axis, ...(yaxis || {}) },
    ...rest,
  };
}

export const baseConfig = { displayModeBar: false, responsive: true, scrollZoom: false };

export async function draw(el, data, layout) {
  const Plotly = await ensurePlotly();
  return Plotly.react(el, data, layout, baseConfig);
}
