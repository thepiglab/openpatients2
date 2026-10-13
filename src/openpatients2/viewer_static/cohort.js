'use strict';
/* Cohort builder: filter reviewed patients by specialty, age, sex, course, conditions and medications,
   open any of them in the viewer, and export the full records (JSONL) or the table (CSV).
   The filters live in the URL hash, so a cohort is a link you can bookmark or share. */

const $ = id => document.getElementById(id);
const fmt = n => n.toLocaleString();
const pretty = k => ({ent: 'ENT'})[k] || String(k).replaceAll(/[_-]/g, ' ').replace(/^\w/, c => c.toUpperCase());
const PAGE = 100;

function h(tag, props, ...kids) {
  const n = document.createElement(tag);
  if (typeof props === 'string') n.className = props;
  else for (const [k, v] of Object.entries(props || {})) if (v != null && v !== false) n.setAttribute(k, v === true ? '' : v);
  for (const k of kids.flat(Infinity)) if (k != null && k !== false && k !== '') n.append(k instanceof Node ? k : String(k));
  return n;
}

/* Choice groups: OR within a group, AND across groups. */
const AGE = [['neonate', 'Neonate (<28 d)'], ['infant', 'Infant (<1 y)'], ['child', 'Child (1–12)'], ['adolescent', 'Adolescent (13–17)'],
  ['adult', 'Adult (18–64)'], ['older_adult', '65 and over'], ['unknown', 'Not stated']];
const SEX = [['female', 'Female'], ['male', 'Male'], ['unknown', 'Not documented']];
const COURSE = [['living_at_presentation', 'Alive at presentation'], ['deceased_during_course', 'Died during course'], ['unknown', 'Not stated']];
const ARTICLE = [['figures', 'Has figures'], ['multi', 'Multi-patient article'], ['supplements', 'Has supplements']];
const GROUPS = {
  specialty: {test: (p, v) => p.specialty === v},
  age: {options: AGE, test: (p, v) => p.age_group === v},
  sex: {options: SEX, test: (p, v) => (p.sex || 'unknown') === v},
  course: {options: COURSE, test: (p, v) => (p.subject_state || 'unknown') === v},
  article: {options: ARTICLE, all: true, test: (p, v) => ({figures: p.figure_count > 0, multi: p.patient_count > 1, supplements: p.supplement_count > 0})[v]},
};

const state = {patients: [], chosen: {}, cond: '', med: '', sort: ['specialty', 1], shown: PAGE, exportLive: false};

/* ---------- URL <-> filters ---------- */

function readHash() {
  const p = new URLSearchParams(location.hash.slice(1));
  state.chosen = Object.fromEntries(Object.keys(GROUPS).map(g => [g, new Set((p.get(g) || '').split(',').filter(Boolean))]));
  state.cond = p.get('condition') || ''; state.med = p.get('medication') || '';
  $('cond').value = state.cond; $('med').value = state.med;
}
function writeHash() {
  const p = new URLSearchParams();
  for (const [g, set] of Object.entries(state.chosen)) if (set.size) p.set(g, [...set].join(','));
  if (state.cond) p.set('condition', state.cond);
  if (state.med) p.set('medication', state.med);
  history.replaceState(null, '', p.toString() ? '#' + p : location.pathname);
}

/* ---------- matching ---------- */

const terms = text => text.toLowerCase().split(',').map(t => t.trim()).filter(Boolean);
// Each comma-separated term must match some name on the patient's list (substring, case-insensitive).
const named = (list, text) => terms(text).every(t => list.some(name => name.toLowerCase().includes(t)));

function matches(p, skip) {
  for (const [g, set] of Object.entries(state.chosen)) {
    if (g === skip || !set.size) continue;
    const test = v => GROUPS[g].test(p, v);
    if (GROUPS[g].all ? ![...set].every(test) : ![...set].some(test)) return false;
  }
  return named(p.conditions, state.cond) && named(p.medications, state.med);
}

/* ---------- rendering ---------- */

function renderFilters() {
  for (const [g, def] of Object.entries(GROUPS)) {
    const options = def.options || [...new Set(state.patients.map(p => p.specialty).filter(Boolean))].sort().map(v => [v, pretty(v)]);
    const pool = state.patients.filter(p => matches(p, g));  // counts reflect every other filter
    const box = document.querySelector(`[data-group="${g}"] .chips`);
    box.replaceChildren(...options.map(([v, label]) => {
      const n = pool.filter(p => def.test(p, v)).length, on = state.chosen[g].has(v);
      const chip = h('button', {type: 'button', class: 'chip' + (on ? ' on' : ''), 'aria-pressed': String(on), disabled: !n && !on},
        label, h('span', 'chip-n', fmt(n)));
      chip.addEventListener('click', () => {
        state.chosen[g][on ? 'delete' : 'add'](v);
        update();
      });
      return chip;
    }));
  }
}

function median(values) {
  const v = values.filter(x => x != null).sort((a, b) => a - b);
  return v.length ? (v.length % 2 ? v[(v.length - 1) / 2] : (v[v.length / 2 - 1] + v[v.length / 2]) / 2) : null;
}
const ageText = y => y == null ? '—' : y < 1 / 12 ? `${Math.round(y * 365.25)} d` : y < 2 ? `${Math.round(y * 12)} mo` : `${Math.floor(y)}`;

function selected() {
  const [key, dir] = state.sort;
  const val = p => p[key] ?? (dir > 0 ? '￿' : '');
  return state.patients.filter(p => matches(p))
    .sort((a, b) => (typeof val(a) === 'number' && typeof val(b) === 'number' ? val(a) - val(b) : String(val(a)).localeCompare(String(val(b)))) * dir
      || a.article_id.localeCompare(b.article_id) || a.patient_id.localeCompare(b.patient_id));
}

function renderResults() {
  const rows = selected(), articles = new Set(rows.map(p => p.article_id)).size;
  $('count').textContent = `${fmt(rows.length)} patient${rows.length === 1 ? '' : 's'} · ${fmt(articles)} article${articles === 1 ? '' : 's'}`;
  const female = rows.filter(p => p.sex === 'female').length, male = rows.filter(p => p.sex === 'male').length;
  const med = median(rows.map(p => p.age_years));
  $('summary').textContent = rows.length
    ? `${fmt(female)} female · ${fmt(male)} male · median age ${med == null ? '—' : ageText(med)} · ${fmt(rows.reduce((a, p) => a + p.facts, 0))} facts`
    : 'No patients match. Remove a filter to widen the cohort.';
  $('export').disabled = $('csv').disabled = !rows.length;
  $('export').textContent = `Export JSONL (${fmt(rows.length)})`;
  $('rows').replaceChildren(...rows.slice(0, state.shown).map(p => {
    const href = 'viewer.html#' + new URLSearchParams({article: p.article_id, patient: p.patient_id, section: 'summary'});
    const tr = h('tr', null,
      h('td', null, h('a', {href, class: 'patient-link'}, p.description || p.patient_id), h('span', 'row-article', p.title)),
      h('td', 'num', ageText(p.age_years)),
      h('td', null, p.sex ? pretty(p.sex) : '—'),
      h('td', null, pretty(p.specialty || '—')),
      h('td', 'conds', p.conditions.slice(0, 3).join(' · '), p.conditions.length > 3 ? h('span', 'muted', ` +${p.conditions.length - 3}`) : null),
      h('td', 'num', fmt(p.facts)));
    tr.addEventListener('click', e => { if (!e.target.closest('a')) location.href = href; });
    return tr;
  }));
  $('more').hidden = rows.length <= state.shown;
  $('more').textContent = `Show ${fmt(Math.min(PAGE, rows.length - state.shown))} more`;
  for (const b of document.querySelectorAll('[data-sort]'))
    b.dataset.dir = b.dataset.sort === state.sort[0] ? (state.sort[1] > 0 ? 'asc' : 'desc') : '';
}

function update({keepPage = false} = {}) {
  if (!keepPage) state.shown = PAGE;
  writeHash(); renderFilters(); renderResults();
}

/* ---------- export ---------- */

function download(name, type, text) {
  const a = h('a', {href: URL.createObjectURL(new Blob([text], {type})), download: name});
  document.body.append(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 4000);
}
const stamp = () => new Date().toISOString().slice(0, 19).replace(/[-:T]/g, '').replace(/(\d{8})/, '$1-');

async function exportJsonl() {
  const rows = selected(), button = $('export');
  if (state.exportLive) {
    // The server streams full records straight to the file; nothing large is held in the page.
    const a = h('a', {href: 'api/export.jsonl?patients=' + encodeURIComponent(rows.map(p => `${p.article_id}:${p.patient_id}`).join(',')), download: ''});
    document.body.append(a); a.click(); a.remove();
    return;
  }
  // Static site: assemble the rows from the per-article files.
  button.disabled = true;
  const byArticle = Map.groupBy(rows, p => p.article_id), lines = [];
  let done = 0;
  for (const [aid, wanted] of byArticle) {
    button.textContent = `Exporting ${fmt(++done)} / ${fmt(byArticle.size)}…`;
    const a = await (await fetch(`api/articles/${encodeURIComponent(aid)}.json`)).json();
    const ids = new Set(wanted.map(p => p.patient_id));
    for (const rec of a.patients) {
      const pid = rec.source?.patient_target?.patient_id || rec.source?.record_id || rec.patient_id;
      if (!ids.has(pid)) continue;
      const m = a.metadata;
      lines.push(JSON.stringify({article_id: aid, patient_id: pid, record_id: rec.source?.record_id, pmcid: m.pmcid, title: m.title,
        specialty: m.primary_stratum, license: m.source_license?.code, source_url: m.article_url, review_status: m.status, record: rec}));
    }
  }
  download(`cohort-${stamp()}.jsonl`, 'application/x-ndjson', lines.join('\n') + '\n');
  renderResults();
}

function exportCsv() {
  const cols = ['article_id', 'patient_id', 'title', 'specialty', 'description', 'age_years', 'age_group', 'sex', 'subject_state',
    'conditions', 'medications', 'facts', 'events'];
  const cell = v => { const s = Array.isArray(v) ? v.join('; ') : v ?? ''; return /[",\n]/.test(s) ? `"${String(s).replaceAll('"', '""')}"` : String(s); };
  download(`cohort-${stamp()}.csv`, 'text/csv', [cols.join(','), ...selected().map(p => cols.map(c => cell(p[c])).join(','))].join('\n') + '\n');
}

/* ---------- wiring ---------- */

let typing;
for (const [id, key] of [['cond', 'cond'], ['med', 'med']])
  $(id).addEventListener('input', () => { clearTimeout(typing); typing = setTimeout(() => { state[key] = $(id).value.trim(); update(); }, 150); });
$('reset').onclick = () => { history.replaceState(null, '', location.pathname); readHash(); update(); };
$('more').onclick = () => { state.shown += PAGE; renderResults(); };
$('export').onclick = exportJsonl;
$('csv').onclick = exportCsv;
for (const b of document.querySelectorAll('[data-sort]')) b.addEventListener('click', () => {
  const k = b.dataset.sort;
  state.sort = [k, state.sort[0] === k ? -state.sort[1] : (k === 'facts' ? -1 : 1)];
  renderResults();
});
addEventListener('hashchange', () => { readHash(); update(); });

readHash();
fetch('api/catalog.json', {cache: 'no-store'})
  .then(r => r.ok ? r.json() : Promise.reject(Error(`Could not load the catalog (${r.status}).`)))
  .then(c => {
    state.exportLive = !!c.export;
    state.patients = c.articles.filter(r => r.status === 'schema_reviewed').flatMap(r => (r.cohort || []).map(p => ({...p,
      article_id: r.article_id, title: r.title, specialty: r.primary_stratum, patient_count: r.patient_count,
      figure_count: r.figure_count, supplement_count: r.supplement_count})));
    update({keepPage: true});
  })
  .catch(e => { $('count').textContent = e.message; });
