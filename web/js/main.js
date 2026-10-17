// main.js -- start-up and the tiny hash router (#/planner, #/experiments, #/about).
// Pages after the planner are loaded the first time they are opened, so the planner appears fast.

import { api } from './api.js';
import { Planner } from './planner.js';
import { $, $$, h, clear } from './util.js';

const VIEWS = ['planner', 'experiments', 'about'];
let city = null;
let planner = null;
const loaded = new Set();

function showError(root, err) {
  clear(root).append(h('div', { class: 'err-box' },
    h('h2', {}, 'Something went wrong'),
    h('p', { class: 'note' }, err.message || String(err))));
}

async function openPlanner(params) {
  if (!planner) {
    planner = new Planner(city);
    planner.mount();                    // the view is visible by now, so the map can measure itself
    planner.applyQuery(params);
  } else if (params.has('from') || params.has('to')) {
    planner.applyQuery(params);         // a link such as "Open in planner" from the experiments page
  }
}

async function openExperiments() {
  if (loaded.has('experiments')) return;
  loaded.add('experiments');
  const root = $('#experiments-root');
  try {
    const [{ renderExperiments }, data] = await Promise.all([import('./experiments.js'), api.experiments()]);
    renderExperiments(root, data, city);
  } catch (e) {
    loaded.delete('experiments');
    showError(root, e);
  }
}

async function openAbout() {
  if (loaded.has('about')) return;
  loaded.add('about');
  const { renderAbout } = await import('./about.js');
  renderAbout($('#about-root'), city);
}

function route() {
  const hash = location.hash.replace(/^#\/?/, '');
  const [name, query = ''] = hash.split('?');
  const view = VIEWS.includes(name) ? name : 'planner';
  for (const v of VIEWS) $(`#view-${v}`).hidden = v !== view;
  for (const a of $$('.tabs a')) {
    if (a.dataset.tab === view) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current');
  }
  document.title = { planner: 'Interchange: route planner', experiments: 'Experiments - Interchange', about: 'How it works - Interchange' }[view];
  window.scrollTo(0, 0);
  if (view === 'planner') openPlanner(new URLSearchParams(query));
  else if (view === 'experiments') openExperiments();
  else openAbout();
}

async function start() {
  try {
    city = await api.city();
  } catch (e) {
    for (const v of VIEWS) $(`#view-${v}`).hidden = v !== 'planner';
    const root = $('#view-planner');
    root.className = 'page';
    clear(root).append(h('div', { class: 'page-inner' }, h('div', { class: 'err-box' },
      h('h2', {}, 'The planner could not start'),
      h('p', { class: 'note' }, e.message),
      h('p', { class: 'note' }, 'Start the server with ', h('code', {}, 'python server.py'), ' and reload this page.'))));
    return;
  }
  window.addEventListener('hashchange', route);
  route();
}

start();
