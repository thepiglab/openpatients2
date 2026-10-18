'use strict';
/* Overview: the catalog as a few charts. Every mark links into the viewer with the matching filter.
   DOM + SVG built by hand (no innerHTML, no inline style attributes) for the strict CSP. */

// Old links pointed at the viewer on this address; send them on.
if (/(^|[#&])article=/.test(location.hash)) location.replace('viewer.html' + location.hash);

const $ = id => document.getElementById(id);
const SVG = 'http://www.w3.org/2000/svg';
const LABELS = {
  symptoms_function: 'Symptoms & function', procedures_devices: 'Procedures & devices',
  family_genetics: 'Family & genetics', reproductive_perinatal: 'Reproductive & perinatal',
  social_exposures: 'Social & exposures', care_plans: 'Care plans', ent: 'ENT',
};
const pretty = k => LABELS[k] || String(k).replaceAll(/[_-]/g, ' ').replace(/^\w/, c => c.toUpperCase());
const fmt = n => n.toLocaleString();
const pct = (n, d) => d ? `${Math.round(100 * n / d)}%` : '–';
const view = params => 'viewer.html#' + new URLSearchParams(params);

function h(tag, props, ...kids) {
  const n = document.createElement(tag);
  if (typeof props === 'string') n.className = props;
  else for (const [k, v] of Object.entries(props || {})) if (v != null && v !== false) n.setAttribute(k, v);
  for (const k of kids.flat(Infinity)) if (k != null && k !== false && k !== '') n.append(k instanceof Node ? k : String(k));
  return n;
}
function s(tag, attrs) {
  const n = document.createElementNS(SVG, tag);
  for (const [k, v] of Object.entries(attrs || {})) n.setAttribute(k, v);
  return n;
}

/* ---------- tooltip ---------- */

function tip(node, text) {
  const show = e => {
    const t = $('tip');
    t.textContent = text; t.hidden = false;
    const x = Math.min(e.clientX + 14, innerWidth - t.offsetWidth - 8), y = Math.max(8, e.clientY - t.offsetHeight - 10);
    t.style.left = x + 'px'; t.style.top = y + 'px';
  };
  node.addEventListener('pointermove', show);
  node.addEventListener('pointerleave', () => { $('tip').hidden = true; });
}

/* ---------- marks ---------- */

/* Donut for part-to-whole (<= 5 parts), with a legend that carries the numbers. */
function donut(items, {center, sub}) {
  const total = items.reduce((a, b) => a + b.n, 0);
  const R = 52, W = 16, C = 2 * Math.PI * R, gap = items.filter(i => i.n).length > 1 ? 2 : 0;
  const svg = s('svg', {viewBox: '0 0 132 132', class: 'donut', role: 'img', 'aria-label': items.map(i => `${i.label} ${i.n}`).join(', ')});
  svg.append(s('circle', {cx: 66, cy: 66, r: R, class: 'donut-track', 'stroke-width': W, fill: 'none'}));
  let at = 0;
  items.forEach((it, i) => {
    if (!it.n) return;
    const len = C * it.n / total;
    const arc = s('circle', {cx: 66, cy: 66, r: R, fill: 'none', 'stroke-width': W, class: `seg ${it.cls || 'c' + (i + 1)}`,
      'stroke-dasharray': `${Math.max(0, len - gap)} ${C}`, 'stroke-dashoffset': -at, transform: 'rotate(-90 66 66)'});
    tip(arc, `${it.label}: ${fmt(it.n)} (${pct(it.n, total)})`);
    if (it.href) arc.addEventListener('click', () => { location.href = it.href; });
    svg.append(arc);
    at += len;
  });
  const mid = s('text', {x: 66, y: 64, 'text-anchor': 'middle', class: 'donut-num'}); mid.textContent = center;
  const lab = s('text', {x: 66, y: 82, 'text-anchor': 'middle', class: 'donut-sub'}); lab.textContent = sub;
  svg.append(mid, lab);
  const legend = h('ul', 'legend', items.map((it, i) => h('li', null,
    h('span', `swatch ${it.cls || 'c' + (i + 1)}`),
    it.href ? h('a', {href: it.href}, it.label) : h('span', null, it.label),
    h('span', 'legend-n', fmt(it.n)), h('span', 'legend-p', pct(it.n, total)))));
  return h('div', 'donut-wrap', svg, legend);
}

/* Horizontal bars for magnitudes; one hue, value labels at the end. */
function bars(items, {note} = {}) {
  const max = Math.max(...items.map(i => i.n), 1);
  return h('div', 'hbars', items.map(it => {
    const fill = h('span', 'hbar-fill'); fill.dataset.w = it.n / max;
    const row = h('div', 'hbar', h('span', 'hbar-label', it.label), h('span', 'hbar-track', fill), h('span', 'hbar-n', fmt(it.n)));
    tip(row, note ? note(it) : `${it.label}: ${fmt(it.n)}`);
    return row;
  }));
}

/* Meter: a share of a known total, on a track of the same hue. */
function meter(label, n, of, href) {
  const fill = h('span', 'meter-fill'); fill.dataset.w = of ? n / of : 0;
  const el = h(href ? 'a' : 'div', {class: 'meter', href},
    h('span', 'meter-label', label), h('span', 'meter-n', `${fmt(n)} `, h('span', 'muted', `/ ${fmt(of)}`)),
    h('span', 'meter-track', fill));
  tip(el, `${label}: ${fmt(n)} of ${fmt(of)} (${pct(n, of)})`);
  return el;
}

/* Themes: one card per group; each lane is a bar of its articles, plus its completion.
   Completion is the export checklist's when the server has one, else the full-schema review status. */
function themes(all, scheme) {
  const done = r => r.completed ?? r.status === 'schema_reviewed';
  const tally = rows => ({n: rows.length, done: rows.filter(done).length});
  const lanes = {}, tagged = {};
  for (const r of all) {
    if (!r.theme) continue;
    (lanes[r.theme.lane] ||= []).push(r);
    for (const t of r.theme.tags || []) if (t !== r.theme.lane) tagged[t] = (tagged[t] || 0) + 1;
  }
  const max = Math.max(1, ...Object.values(lanes).map(rows => rows.length));
  const key = h('p', 'theme-key muted', h('span', 'swatch c1'), 'Articles', h('span', 'swatch done'), 'Completed');
  const cards = scheme.map(g => {
    const rows = g.lanes.flatMap(l => lanes[l.lane] || []);
    if (!rows.length) return null;
    const t = tally(rows);
    return h('div', 'theme-card',
      h('a', {class: 'theme-head', href: view({theme: g.group}), title: g.description},
        h('span', 'theme-name', g.name), h('span', 'theme-n', fmt(t.n)),
        h('span', 'theme-sub', `${pct(t.done, t.n)} completed`)),
      g.lanes.filter(l => lanes[l.lane]).map(l => {
        const x = tally(lanes[l.lane]);
        const fill = (cls, w) => { const f = h('span', cls); f.dataset.w = w; return f; };
        const row = h('a', {class: 'theme-lane', href: view({theme: l.lane})},
          h('span', 'theme-lane-name', l.name),
          h('span', 'theme-track', fill('theme-seg c1', x.n / max)),
          h('span', 'theme-lane-n', fmt(x.n)),
          h('span', 'theme-done', fill('theme-done-fill', x.done / x.n)));
        tip(row, `${l.name}: ${fmt(x.n)} articles · ` +
          `${fmt(x.done)} completed · ${fmt(tagged[l.lane] || 0)} more carry it as a secondary tag`);
        return row;
      }));
  });
  return h('div', null, key, h('div', 'theme-grid', cards));
}

function stat(id, label, value, sub, href) {
  $(id).replaceChildren(h(href ? 'a' : 'div', {class: 'stat-body', href},
    h('span', 'stat-label', label), h('span', 'stat-value', value), sub && h('span', 'stat-sub', sub)));
}

function paint() {
  // Widths go through the CSSOM: inline style attributes are blocked by the security policy.
  for (const f of document.querySelectorAll('[data-w]')) f.style.width = (100 * Number(f.dataset.w)).toFixed(1) + '%';
}

/* ---------- the page ---------- */

function render(c) {
  const all = c.articles, reviewed = all.filter(r => r.status === 'schema_reviewed');
  const count = (rows, f) => rows.filter(f).length;
  const sum = (rows, f) => rows.reduce((a, r) => a + (f(r) || 0), 0);
  const patients = sum(reviewed, r => r.patient_count);
  const facts = {}, having = {};
  for (const r of reviewed) {
    for (const [k, v] of Object.entries(r.section_facts || {})) facts[k] = (facts[k] || 0) + v;
    for (const [k, v] of Object.entries(r.section_patients || {})) having[k] = (having[k] || 0) + v;
  }
  const factTotal = Object.values(facts).reduce((a, b) => a + b, 0);

  stat('t-articles', 'Articles', fmt(all.length), 'open-access case reports');
  stat('t-reviewed', 'Reviewed records', fmt(reviewed.length), `${pct(reviewed.length, all.length)} of articles`, view({status: 'schema_reviewed'}));
  stat('t-patients', 'Patients', fmt(patients), 'individual records');
  stat('t-facts', 'Extracted facts', fmt(factTotal), `${fmt(sum(reviewed, r => r.event_count))} timeline events`);

  if (c.themes) $('c-themes').replaceChildren(themes(all, c.themes));

  const source = count(all, r => r.status === 'source_reviewed');
  $('c-status').replaceChildren(donut([
    {label: 'Reviewed', n: reviewed.length, href: view({status: 'schema_reviewed'})},
    {label: 'Source reviewed', n: source, href: view({status: 'source_reviewed'}), cls: 'c4'},
    {label: 'Not yet', n: all.length - reviewed.length - source, href: view({status: 'pending'}), cls: 'c5'},
  ], {center: pct(reviewed.length, all.length), sub: 'reviewed'}));

  const sizes = [['1 patient', r => r.patient_count === 1, '1'], ['2–4', r => r.patient_count >= 2 && r.patient_count <= 4, '2-4'],
    ['5 or more', r => r.patient_count >= 5, '5+'], ['Series only', r => !r.patient_count, 'aggregate']];
  $('c-size').replaceChildren(donut(sizes.map(([label, f, v]) =>
    ({label, n: count(reviewed, f), href: view({status: 'schema_reviewed', patients: v})})),
  {center: fmt(reviewed.length), sub: 'reviewed'}));

  const types = {};
  for (const r of all) types[r.article_type || 'unspecified'] = (types[r.article_type || 'unspecified'] || 0) + 1;
  const ranked = Object.entries(types).sort((a, b) => b[1] - a[1]);
  const top = ranked.slice(0, 4).map(([t, n]) => ({label: pretty(t), n, href: view({article_type: t})}));
  const rest = ranked.slice(4).reduce((a, [, n]) => a + n, 0);
  $('c-type').replaceChildren(donut([...top, ...(rest ? [{label: 'Other', n: rest, cls: 'c5'}] : [])], {center: fmt(all.length), sub: 'articles'}));

  $('c-sections').replaceChildren(bars(Object.entries(facts).filter(([, n]) => n).sort((a, b) => b[1] - a[1])
    .map(([k, n]) => ({label: pretty(k), n, key: k})),
  {note: it => `${it.label}: ${fmt(it.n)} facts · ${fmt(having[it.key] || 0)} of ${fmt(patients)} patients have any`}));

  const figs = all.filter(r => r.figure_count > 0), supps = all.filter(r => r.supplement_count > 0);
  $('c-media').replaceChildren(h('div', 'meters',
    meter('Articles with figures', figs.length, all.length, view({figures: 'with'})),
    meter('Figures AI-described', count(figs, r => r.figure_reviewed), figs.length, view({figure_review: 'described'})),
    meter('Figures EHR-labeled', count(figs, r => r.figure_ehr_labeled), figs.length, view({figure_review: 'ehr'})),
    meter('Articles with supplements', supps.length, all.length, view({supplements: 'with'})),
    meter('Supplements extracted', count(supps, r => r.supplements_extracted > 0), supps.length, view({supplements: 'extracted'}))));

  const strata = {};
  for (const r of all) {
    const x = strata[r.primary_stratum || 'other'] ||= {n: 0, done: 0};
    x.n++; x.done += r.status === 'schema_reviewed';
  }
  $('c-specialty').replaceChildren(h('div', 'meters grid', Object.entries(strata)
    .sort((a, b) => a[0].localeCompare(b[0])).map(([k, x]) => meter(pretty(k), x.done, x.n, view({specialty: k})))));

  const licenses = {};
  for (const r of all) licenses[r.license || 'Unknown'] = (licenses[r.license || 'Unknown'] || 0) + 1;
  $('foot').replaceChildren(
    `AI reference annotations, not physician-adjudicated · Licenses: `,
    Object.entries(licenses).sort((a, b) => b[1] - a[1]).map(([l, n]) => `${l} ${fmt(n)}`).join(' · '),
    ` · Updated ${new Date(c.updated_at).toLocaleString([], {dateStyle: 'medium', timeStyle: 'short'})}`);
  paint();
}

fetch('api/catalog.json', {cache: 'no-store'})
  .then(r => r.ok ? r.json() : Promise.reject(Error(`Could not load the catalog (${r.status}).`)))
  .then(render)
  .catch(e => stat('t-articles', 'Error', '–', e.message));
