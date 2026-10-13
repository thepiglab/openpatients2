'use strict';
/* Clinical Article Library: read the source article beside its extracted patient record.
   Built with DOM nodes + textContent only (no innerHTML, no inline style attributes), so it
   runs under the server's strict Content-Security-Policy. */

const $ = id => document.getElementById(id);
const SVG = 'http://www.w3.org/2000/svg';
const LABELS = {
  symptoms_function: 'Symptoms & function', procedures_devices: 'Procedures & devices',
  family_genetics: 'Family & genetics', reproductive_perinatal: 'Reproductive & perinatal',
  social_exposures: 'Social & exposures', source_facts: 'Source facts', raw: 'Raw data', supplements: 'Supplements', ent: 'ENT',
};
const TITLE_KEYS = ['name', 'attribute', 'event', 'substance', 'action_text', 'description', 'text',
  'clinical_statement', 'label', 'event_id', 'figure_id'];
const EVIDENCE_KEYS = new Set(['evidence', 'documentation_evidence', 'attribution_evidence']);
const ICONS = {
  chevron: 'M6 4l4 4-4 4',
  check: 'M3.5 8.5l3 3 6-7',
  locate: 'M8 13.5A5.5 5.5 0 1 0 8 2.5a5.5 5.5 0 0 0 0 11zM8 10a2 2 0 1 0 0-4 2 2 0 0 0 0 4z',
};

const state = {
  catalog: [], catalogRevision: null,
  filter: {q: '', status: '', specialty: '', advanced: {}},
  article: null, aid: null, pid: null, tab: 'summary', revision: null,
  generation: 0, refreshing: false,
  expanded: new Set(),
  active: null,  // {key, items: [{id, quote}], index}: the evidence currently highlighted in the article
  segments: new Map(), find: {query: '', marks: [], index: -1},
  floats: {figures: new Map(), tables: new Map()}, references: new Map(),
};

/* ---------- helpers ---------- */

function h(tag, props, ...kids) {
  const n = document.createElement(tag);
  if (typeof props === 'string') n.className = props;
  else if (props) {
    for (const [k, v] of Object.entries(props)) {
      if (v === undefined || v === null || v === false) continue;
      if (k === 'class') n.className = v;
      else if (k.startsWith('on')) n.addEventListener(k.slice(2), v);
      else n.setAttribute(k, v === true ? '' : v);
    }
  }
  for (const k of kids.flat(Infinity)) {
    if (k === null || k === undefined || k === false || k === '') continue;
    n.append(k instanceof Node ? k : document.createTextNode(String(k)));
  }
  return n;
}
function icon(name) {
  const s = document.createElementNS(SVG, 'svg');
  s.setAttribute('viewBox', '0 0 16 16'); s.setAttribute('class', 'icon'); s.setAttribute('aria-hidden', 'true');
  const p = document.createElementNS(SVG, 'path'); p.setAttribute('d', ICONS[name]); s.append(p);
  return s;
}
const pretty = key => LABELS[key] || String(key).replaceAll('_', ' ').replace(/^\w/, c => c.toUpperCase());
const words = v => typeof v === 'string' ? v.replaceAll('_', ' ') : v;
const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`;
const isObject = v => v !== null && typeof v === 'object' && !Array.isArray(v);
const smooth = () => matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth';
const store = (k, v) => { try { localStorage.setItem('cal.' + k, v); } catch { /* unavailable */ } };
const load = k => { try { return localStorage.getItem('cal.' + k); } catch { return null; } };
function isBlank(v) {
  return v === null || v === undefined || v === '' || v === 'unknown' || (Array.isArray(v) && !v.length)
    || (isObject(v) && Object.values(v).every(isBlank));
}
function safeURL(url) {
  try { const u = new URL(url); return ['https:', 'http:'].includes(u.protocol) ? u.href : null; } catch { return null; }
}
function link(text, url) {
  const safe = safeURL(url);
  return safe ? h('a', {href: safe, target: '_blank', rel: 'noopener noreferrer'}, text) : null;
}
async function get(path) {
  const r = await fetch(path, {cache: 'no-store'});
  if (!r.ok) throw Error(r.status === 503 ? 'Data is updating; retrying shortly.' : `Could not load data (${r.status}).`);
  return r.json();
}
function restart(node, cls) { node.classList.remove(cls); void node.offsetWidth; node.classList.add(cls); }
/* ---------- routing ---------- */

function route() {
  const p = new URLSearchParams(location.hash.slice(1));
  return {aid: p.get('article'), pid: p.get('patient'), tab: p.get('section') || 'summary'};
}
function saveRoute(push = false) {
  if (!state.aid) return;
  const p = new URLSearchParams({article: state.aid, section: state.tab});
  if (state.pid) p.set('patient', state.pid);
  if ('#' + p === location.hash) return;
  if (push) history.pushState(null, '', '#' + p); else history.replaceState(null, '', '#' + p);
}

/* ---------- article list ---------- */

const titleOf = r => r.title || r.article_id;

const range = (n, v) => v === '1' ? n === 1 : v === '2-4' ? n >= 2 && n <= 4 : n >= 5;
const has = (n, v) => v === 'with' ? n > 0 : !n;
const ADVANCED = {
  patients: (r, v) => r.status === 'schema_reviewed' && (v === 'aggregate' ? !r.patient_count : range(r.patient_count || 0, v)),
  figures: (r, v) => has(r.figure_count, v),
  tables: (r, v) => has(r.has_tables ? 1 : 0, v),
  supplements: (r, v) => v === 'extracted' ? r.supplements_extracted > 0 : has(r.supplement_count, v),
  figure_review: (r, v) => ({described: r.figure_reviewed, ehr: r.figure_ehr_labeled, privacy: r.privacy_concern,
    none: r.figure_count > 0 && !r.figure_reviewed})[v],
  article_type: (r, v) => r.article_type === v,
  license: (r, v) => r.license === v,
};
const advancedMatch = r => Object.entries(state.filter.advanced).every(([k, v]) => !v || !ADVANCED[k] || ADVANCED[k](r, v));

function filteredCatalog() {
  const {q, status, specialty} = state.filter;
  const terms = q.toLowerCase().split(/\s+/).filter(Boolean);
  const rank = {schema_reviewed: 0, source_reviewed: 1, updating: 2, pending: 3};
  return state.catalog
    .filter(r => (!status || r.status === status) && (!specialty || r.primary_stratum === specialty) && advancedMatch(r)
      && terms.every(t => [r.title, r.article_id, r.primary_stratum].join(' ').toLowerCase().includes(t)))
    .sort((a, b) => (rank[a.status] ?? 4) - (rank[b.status] ?? 4) || titleOf(a).localeCompare(titleOf(b)));
}

function renderCatalog() {
  const rows = filteredCatalog();
  const reviewed = state.catalog.filter(r => r.status === 'schema_reviewed').length;
  $('counts').textContent = rows.length === state.catalog.length
    ? `${state.catalog.length} articles · ${reviewed} reviewed`
    : `${rows.length} of ${state.catalog.length} articles`;
  $('articles').replaceChildren(...rows.map(r => {
    const meta = [r.primary_stratum && pretty(r.primary_stratum),
      r.status === 'schema_reviewed' ? (r.patient_count ? plural(r.patient_count, 'patient') : 'aggregate series only') : r.status === 'updating' ? 'updating…' : 'not reviewed yet'];
    return h('button', {
      type: 'button', class: 'article-row' + (r.status === 'schema_reviewed' ? '' : ' unreviewed') + (r.article_id === state.aid ? ' active' : ''),
      'aria-current': r.article_id === state.aid ? 'true' : null, 'data-id': r.article_id,
      onclick: () => openArticle(r.article_id),
    }, h('span', 'row-title', titleOf(r)), h('span', 'row-meta', meta.filter(Boolean).join(' · ')));
  }));
  if (!rows.length) $('articles').append(h('p', 'empty', 'No matching articles.'));
}

function markActiveRow() {
  for (const b of $('articles').querySelectorAll('.article-row')) {
    b.classList.toggle('active', b.dataset.id === state.aid);
    if (b.dataset.id === state.aid) b.setAttribute('aria-current', 'true'); else b.removeAttribute('aria-current');
  }
}

function renderSpecialties() {
  const select = $('specialty');
  const values = [...new Set(state.catalog.map(r => r.primary_stratum).filter(Boolean))].sort();
  select.replaceChildren(h('option', {value: ''}, 'Any specialty'), ...values.map(v => h('option', {value: v}, pretty(v))));
  select.value = values.includes(state.filter.specialty) ? state.filter.specialty : '';
  state.filter.specialty = select.value;
  for (const [key, field, label] of [['article_type', 'article_type', words], ['license', 'license', v => v]]) {
    const el = document.querySelector(`[data-filter="${key}"]`);
    const opts = [...new Set(state.catalog.map(r => r[field]).filter(Boolean))].sort();
    el.replaceChildren(h('option', {value: ''}, 'Any'), ...opts.map(v => h('option', {value: v}, label(v))));
  }
  syncAdvanced();
}

/* Advanced filters persist per browser; stale values (e.g. a license no longer present) are dropped. */
function syncAdvanced() {
  for (const el of document.querySelectorAll('[data-filter]')) {
    const v = state.filter.advanced[el.dataset.filter] || '';
    el.value = [...el.options].some(o => o.value === v) ? v : '';
    state.filter.advanced[el.dataset.filter] = el.value;
  }
  const active = Object.values(state.filter.advanced).filter(Boolean).length;
  $('advanced-toggle').textContent = active ? `Filters (${active})` : 'Filters';
  $('advanced-toggle').classList.toggle('on', active > 0);
}
function setAdvanced(next) {
  state.filter.advanced = next;
  store('filters', JSON.stringify(next));
  syncAdvanced(); renderCatalog();
}

function stepArticle(delta) {
  const rows = filteredCatalog();
  const i = rows.findIndex(r => r.article_id === state.aid);
  const next = rows[Math.min(rows.length - 1, Math.max(0, i + delta))];
  if (next && next.article_id !== state.aid) {
    openArticle(next.article_id);
    $('articles').querySelector(`[data-id="${CSS.escape(next.article_id)}"]`)?.scrollIntoView({block: 'nearest'});
  }
}

async function refresh() {
  if (state.refreshing) return;
  state.refreshing = true;
  try {
    const c = await get('api/catalog.json');
    if (c.revision !== state.catalogRevision) {
      state.catalog = c.articles; state.catalogRevision = c.revision;
      renderSpecialties(); renderCatalog();
    }
    $('sync').textContent = 'Updated ' + new Date().toLocaleTimeString([], {hour: 'numeric', minute: '2-digit'});
    $('sync').classList.remove('error');
    if (!state.aid && c.articles.length) {
      const r = route();
      state.pid = r.pid; state.tab = r.tab;
      const shown = filteredCatalog();
      await loadArticle(r.aid || shown.find(x => x.status === 'schema_reviewed')?.article_id || shown[0]?.article_id
        || c.articles.find(x => x.status === 'schema_reviewed')?.article_id || c.articles[0].article_id);
      syncTabbar();
      $('articles').querySelector('.active')?.scrollIntoView({block: 'center'});
    } else {
      const row = c.articles.find(r => r.article_id === state.aid);
      if (row && row.revision !== state.revision) await loadArticle(state.aid, {preserve: true});
    }
  } catch (e) {
    $('sync').textContent = e.message;
    $('sync').classList.add('error');
  } finally {
    state.refreshing = false;
  }
}

/* ---------- loading an article ---------- */

const patientId = p => p.source?.patient_target?.patient_id || p.source?.record_id || p.patient_id;
const patientList = (a = state.article) => a?.patients?.length ? a.patients : a?.annotation?.patients || [];
const patient = () => state.article?.patients.find(p => patientId(p) === state.pid);
const annotatedPatient = () => state.article?.annotation?.patients?.find(x => x.patient_id === state.pid);
const timelineOf = p => p?.companions?.timeline_v2 || p?.relative_timeline || {};
function patientLabel(p) {
  const rp = state.article.roster?.patients?.find(r => r.patient_id === patientId(p));
  return rp?.label || p.label || p.sections?.case_context?.index_subject_description || 'Patient';
}

function openArticle(aid) {
  document.body.classList.remove('library-open');
  syncTabbar();
  if (aid === state.aid) return;
  state.pid = null;
  loadArticle(aid, {push: true});
}

async function loadArticle(aid, {preserve = false, push = false} = {}) {
  const seq = ++state.generation;
  const switching = aid !== state.aid;
  const scrolls = [$('source-scroll').scrollTop, $('record').scrollTop];
  state.aid = aid;
  markActiveRow();
  document.body.classList.add('loading');
  try {
    const a = await get(`api/articles/${encodeURIComponent(aid)}.json`);
    if (seq !== state.generation) return;
    state.article = a; state.revision = a.revision;
    const ids = patientList(a).map(patientId);
    if (!ids.includes(state.pid)) state.pid = ids[0] || null;
    if (switching) { state.expanded.clear(); state.active = null; closePeek(); }
    renderHeading();
    renderSource();
    renderPatients();
    saveRoute(push);
    if (preserve) { $('source-scroll').scrollTop = scrolls[0]; $('record').scrollTop = scrolls[1]; }
    else if (switching) {
      $('source-scroll').scrollTop = 0; $('record').scrollTop = 0;
      restart($('reader'), 'enter');
    }
  } catch (e) {
    if (seq !== state.generation) return;
    $('article-heading').append(h('p', 'notice', e.message));
  } finally {
    if (seq === state.generation) document.body.classList.remove('loading');
  }
}

function renderHeading() {
  const a = state.article, m = a.metadata;
  const authors = (m.authors || []).map(v => typeof v === 'string' ? v : v.name || '').filter(Boolean);
  const byline = authors.length > 3 ? `${authors.slice(0, 3).join(', ')} et al.` : authors.join(', ');
  const eyebrow = [m.primary_stratum && pretty(m.primary_stratum), m.article_type && words(m.article_type), m.pmcid || a.article_id];
  const links = [link('PubMed Central ↗', m.article_url),
    h('a', {href: `api/articles/${encodeURIComponent(a.article_id)}.json`, download: a.article_id + '.json'}, 'Download JSON')].filter(Boolean);
  $('article-heading').replaceChildren(
    h('p', 'eyebrow', eyebrow.filter(Boolean).join('  ·  ')),
    h('h1', null, m.title || a.article_id),
    byline && h('p', 'byline', byline),
    h('p', 'article-links', links),
    ...(a.notices || []).map(n => h('p', 'notice', n)));
}

/* ---------- article text, with figures and tables in place ---------- */

function figuresOf(a = state.article) {
  return [...(a.visual?.figures || []), ...(a.supplement_visuals || []).map(s =>
    ({figure_id: s.supplement_id, analysis: s.analysis, assets: s.assets}))];
}
const figureNumber = label => Number((String(label || '').match(/(\d+)/) || [])[1]) || null;

/* Figure numbers a paragraph refers to: "Fig. 2a and b", "Figures 1–3", "Figs. 2, 4". */
function mentionedFigures(text) {
  const found = new Set();
  for (const m of text.matchAll(/\bfig(?:ure)?s?\.?\s*((?:\d+\s*[a-z]?\b(?:\s*(?:,|and|&|[-–])\s*)?)+)/gi)) {
    const nums = [...m[1].matchAll(/\d+/g)].map(x => Number(x[0]));
    if (nums.length === 2 && /[-–]/.test(m[1])) for (let n = nums[0]; n <= nums[1]; n++) found.add(n);
    else nums.forEach(n => found.add(n));
  }
  return found;
}

function register(seg, node, parts) {
  state.segments.set(seg.segment_id, {seg, node, parts});
}

/* ---------- inline markup ----------
   The XML-to-text step leaves a few conventions behind: ^(18) superscripts, _(max) subscripts,
   10^9, spaces inside "( Fig. 1A, B )", and empty ^(,) / ^([]) where citation numbers were dropped.
   These are rendered for display only; character offsets into the raw text are preserved so
   evidence quotes and find-in-article still line up. */

const INLINE = new RegExp([
  String.raw`(?<open>\(\s+)`,
  String.raw`(?<close>\s+\))`,
  String.raw`(?<punct>\s+(?=[,;](?:\s|$)))`,
  String.raw`(?<bracket>\[\s+(?=\^\())`,
  String.raw`\^\((?<sup>[^()]*)\)`,
  String.raw`\^(?<bare>[-−]?\d+)\b`,
  String.raw`\s?_\((?<sub>[^()]*)\)(?:\s(?=:))?`,
  String.raw`(?<ref>\b(?:Fig(?:ure)?s?\.?|Tables?)\s?\d+[A-Za-z]?\b)`,
  String.raw`(?<![A-Za-z0-9)\]])\[(?<cite>\d+(?:\s*[,–-]\s*\d+)*)\]`,
].join('|'), 'g');

function inlineTokens(text, links) {
  const out = [];
  INLINE.lastIndex = 0;
  for (let m; (m = INLINE.exec(text));) {
    const g = m.groups, s = m.index;
    let e = s + m[0].length, node;
    if (g.open !== undefined) node = '(';
    else if (g.close !== undefined) node = ')';
    else if (g.punct !== undefined) node = '';
    else if (g.bracket !== undefined) node = '[';
    else if (g.sup !== undefined) {
      if (/^[\s\[\],;–\-]*$/.test(g.sup)) node = '';  // citation numbers lost upstream
      else {
        node = h('sup', null, g.sup);
        // "[ ^(18) F]F-FDG" → "[¹⁸F]F-FDG"
        if (/^\d+$/.test(g.sup) && text[e] === ' ' && /[A-Z]/.test(text[e + 1] || '')) INLINE.lastIndex = ++e;
      }
    } else if (g.bare !== undefined) node = h('sup', null, g.bare);
    else if (g.sub !== undefined) node = h('sub', null, g.sub);
    else if (g.ref !== undefined) node = links ? floatLink(g.ref) : null;
    else if (g.cite !== undefined) node = links ? referenceLink(m[0], g.cite) : null;
    if (node !== null) out.push({s, e, node});
  }
  return out;
}

/* Render raw text with inline markup; ranges ({s, e, cls, key?, q?}) become <mark>s. */
function rich(text, ranges = [], {links = true} = {}) {
  const out = [];
  const wrap = (node, s, e) => {
    const cover = ranges.filter(r => r.s < e && r.e > s);
    if (!cover.length) return node;
    const m = h('mark', [...new Set(cover.map(r => r.cls))].join(' '), node);
    const hit = cover.find(r => r.key);
    if (hit) m.dataset.hit = hit.key;
    const quote = cover.find(r => r.q !== undefined);
    if (quote) m.dataset.q = quote.q;
    return m;
  };
  const plain = (s, e) => {
    const cuts = [...new Set([s, e, ...ranges.flatMap(r => [r.s, r.e]).filter(x => x > s && x < e)])].sort((a, b) => a - b);
    for (let i = 0; i < cuts.length - 1; i++) out.push(wrap(text.slice(cuts[i], cuts[i + 1]), cuts[i], cuts[i + 1]));
  };
  let at = 0;
  for (const t of inlineTokens(text, links)) {
    if (t.s > at) plain(at, t.s);
    if (t.node !== '') out.push(wrap(t.node, t.s, t.e));
    at = t.e;
  }
  if (at < text.length) plain(at, text.length);
  // Neighbouring marks of the same kind (split only by inline markup) become one mark.
  const merged = [];
  for (const n of out) {
    const prev = merged.at(-1);
    if (n instanceof HTMLElement && n.tagName === 'MARK' && prev instanceof HTMLElement && prev.tagName === 'MARK'
      && prev.className === n.className && prev.dataset.q === n.dataset.q && prev.dataset.hit === n.dataset.hit) prev.append(...n.childNodes);
    else merged.push(n);
  }
  return merged;
}
const richText = text => rich(String(text ?? ''), [], {links: false});

function floatLink(label) {
  const n = Number(label.match(/\d+/)[0]);
  const kind = /^tab/i.test(label) ? 'tables' : 'figures';
  if (!state.floats[kind].has(n)) return null;
  return h('button', {type: 'button', class: 'xref', onclick: () => {
    const node = document.getElementById(state.floats[kind].get(n));
    if (!node) return;
    node.scrollIntoView({block: 'center', behavior: smooth()});
    restart(node, 'flash');
  }}, label);
}

function referenceLink(raw, numbers) {
  const n = Number(numbers.match(/\d+/)[0]);
  const ref = state.references.get(n);
  if (!ref) return null;
  return h('button', {type: 'button', class: 'xref', title: ref, onclick: () => {
    const block = $('references'), li = document.getElementById('ref-' + n);
    if (!block || !li) return;
    block.open = true;
    li.scrollIntoView({block: 'center', behavior: smooth()});
    restart(li, 'flash');
  }}, raw);
}

function renderSource() {
  const a = state.article;
  const segments = a.source?.segments || [];
  const sourceFigures = a.source?.figures || [];
  const references = a.source?.references || [];
  const visuals = new Map(figuresOf(a).map(f => [f.figure_id, f]));
  state.segments = new Map();

  // Pair caption segments with their figure, then move each one after the first paragraph that cites it.
  const figureFor = new Map();
  for (const s of segments) {
    if (s.kind !== 'figure_caption') continue;
    const f = sourceFigures.find(f => (f.figure_key || f.figure_id) === s.jats_id)
      || sourceFigures.find(f => f.label && (s.text || '').startsWith(f.label));
    if (f) figureFor.set(s.segment_id, f);
  }
  const after = new Map(), moved = new Set();
  for (const [sid, f] of figureFor) {
    const n = figureNumber(f.label);
    const target = n && segments.find(p => p.kind === 'paragraph' && mentionedFigures(p.text || '').has(n));
    if (!target) continue;
    if (!after.has(target.segment_id)) after.set(target.segment_id, []);
    after.get(target.segment_id).push(segments.find(s => s.segment_id === sid));
    moved.add(sid);
  }

  // Know every link target before any paragraph renders, so "Fig. 2" and "[8]" can link forward.
  state.floats = {figures: new Map(), tables: new Map()};
  for (const [sid, f] of figureFor) if (figureNumber(f.label)) state.floats.figures.set(figureNumber(f.label), 'segment-' + sid);
  for (const s of segments) {
    if (s.kind !== 'table_row') continue;
    const n = figureNumber((s.text || '').split('\n')[0]);
    if (n && !state.floats.tables.has(n)) state.floats.tables.set(n, 'segment-' + s.segment_id);
  }
  state.references = new Map();
  const refRows = references.map((r, i) => {
    const raw = String(r.citation_text || '');
    const n = Number(raw.match(/^(\d+)[.\s]/)?.[1]) || i + 1;
    const text = raw.replace(/^\d+\.?\s*/, '');
    state.references.set(n, text);
    return {n, text, ids: r.identifiers || {}};
  });

  const box = $('source');
  box.replaceChildren();
  let top = null, sub = null;
  for (let i = 0; i < segments.length; i++) {
    const s = segments[i];
    if (moved.has(s.segment_id)) continue;
    const [head, ...rest] = (s.heading || '').split(' / ');
    if (head && head !== top) { top = head; sub = null; box.append(h('h2', 'source-h2', richText(head))); }
    if (rest.length && rest.join(' / ') !== sub) { sub = rest.join(' / '); box.append(h('h3', 'source-h3', richText(sub))); }

    if (s.kind === 'table_row') {
      const rows = [s];
      while (segments[i + 1]?.kind === 'table_row' && segments[i + 1].table_id === s.table_id) rows.push(segments[++i]);
      const notes = [];
      while (segments[i + 1]?.kind === 'table_footnote' && segments[i + 1].table_id === s.table_id) notes.push(segments[++i]);
      box.append(tableBlock(rows, notes));
    } else if (s.kind === 'figure_caption' && figureFor.has(s.segment_id)) {
      box.append(figureBlock(s, figureFor.get(s.segment_id), visuals));
    } else {
      const p = h('p', {class: 'para' + (s.kind === 'paragraph' ? '' : ' minor'), id: 'segment-' + s.segment_id});
      register(s, p, [{el: p, text: s.text || ''}]);
      box.append(p);
    }
    for (const cap of after.get(s.segment_id) || []) box.append(figureBlock(cap, figureFor.get(cap.segment_id), visuals));
  }
  if (!segments.length) box.append(h('p', 'empty', 'The article text is not installed here. Use the PubMed Central link above.'));
  if (refRows.length) {
    box.append(h('details', {class: 'references', id: 'references'},
      h('summary', null, `References (${refRows.length})`),
      h('ol', null, refRows.map(r => h('li', {id: 'ref-' + r.n, value: r.n}, r.text,
        r.ids.pmid?.[0] && [' ', link('PubMed', `https://pubmed.ncbi.nlm.nih.gov/${encodeURIComponent(r.ids.pmid[0])}/`)],
        r.ids.doi?.[0] && [' ', link('DOI', 'https://doi.org/' + encodeURI(r.ids.doi[0]))])))));
  }
  runFind(false);
  for (const it of state.active?.items || []) state.segments.get(it.id)?.node.classList.add('active');
}

function tableBlock(rows, notes) {
  const lines = rows.map(r => (r.text || '').split('\n'));
  const first = lines[0];
  if (first.length < 3) {  // not in the expected "title / header / row" shape; show as text
    return h('div', null, rows.concat(notes).map(r => {
      const p = h('p', {class: 'para minor', id: 'segment-' + r.segment_id});
      register(r, p, [{el: p, text: r.text || ''}]);
      return p;
    }));
  }
  const title = first.slice(0, -2);
  const header = first.at(-2).split(' | ');
  const body = rows.map((r, k) => {
    const texts = lines[k].at(-1).split(' | ');
    const cells = texts.map(() => h('td'));
    const tr = h('tr', {id: 'segment-' + r.segment_id}, cells);
    register(r, tr, cells.map((td, j) => ({el: td, text: texts[j]})));
    return tr;
  });
  const noteNodes = notes.map(n => {
    const p = h('p', {class: 'table-note', id: 'segment-' + n.segment_id});
    register(n, p, [{el: p, text: n.text || ''}]);
    return p;
  });
  return h('figure', 'table-block',
    h('figcaption', null, h('strong', null, richText(title[0])), title.length > 1 ? [' ', richText(title.slice(1).join(' '))] : ''),
    h('div', 'table-scroll', h('table', null, h('thead', null, h('tr', null, header.map(c => h('th', null, richText(c))))), h('tbody', null, body))),
    noteNodes);
}

/* Reviewed EHR category and privacy flags, one line per panel. */
function figureReview(figureId) {
  const a = state.article;
  const labels = (a.figure_ehr_labels?.figures || []).find(f => f.figure_id === figureId);
  const privacy = (a.figure_privacy_flags?.figures || []).find(f => f.figure_id === figureId);
  if (!labels && !privacy) return null;
  const keys = [...new Set([...(labels?.panels || []), ...(privacy?.panels || [])].map(p => p.panel ?? ''))];
  const rows = (keys.length ? keys : ['']).map(key => {
    const label = labels?.panels?.find(p => (p.panel ?? '') === key)?.label || (keys.length <= 1 ? labels?.figure_label : null);
    const flags = privacy?.panels?.find(p => (p.panel ?? '') === key);
    const out = [];
    if (label) out.push([words(label.ehr_category), label.ehr_includable ? 'blue' : 'violet'],
      [label.ehr_includable ? 'EHR includable' : 'not EHR includable', label.ehr_includable ? 'blue' : 'violet']);
    if (flags?.burned_in_identifiers && flags.burned_in_identifiers !== 'none')
      out.push([words(flags.burned_in_identifiers), flags.burned_in_identifiers === 'direct_identifiers' ? 'red' : 'amber']);
    if (flags?.identifiable_face) out.push(['identifiable face', 'red']);
    if (flags?.sensitive_anatomy) out.push(['sensitive anatomy', 'red']);
    const why = [label?.rationale, flags?.notes].filter(Boolean).join(' ');
    return h('p', {title: why || null}, key && h('strong', null, `Panel ${key} `),
      out.map(([text, tone], i) => [i ? ' · ' : '', h('span', 'flag ' + tone, text)]));
  });
  return h('div', 'figure-review', rows);
}

function figureBlock(seg, fig, visuals) {
  const visual = visuals.get(fig.figure_key || fig.figure_id);
  const media = visual?.assets?.length ? visual.assets : fig.media || [];
  const label = fig.label || '';
  const text = seg.text || fig.caption || '';
  const caption = label && text.startsWith(label) ? text.slice(label.length).trim() : text;
  const captionSpan = h('span');
  const description = visual?.analysis?.visual?.general_description;
  const plainCaption = richText(caption).map(n => typeof n === 'string' ? n : n.textContent).join('');
  const node = h('figure', {class: 'article-figure', id: 'segment-' + seg.segment_id},
    media.map(m => safeURL(m.url)).filter(Boolean).map(src => {
      const img = h('img', {src, alt: plainCaption || label || 'Figure', loading: 'lazy', referrerpolicy: 'no-referrer'});
      const btn = h('button', {type: 'button', class: 'figure-zoom', 'aria-label': 'Enlarge ' + (label || 'figure'), onclick: () => openLightbox(src, `${label} ${plainCaption}`)}, img);
      img.addEventListener('error', () => btn.remove());
      return btn;
    }),
    h('figcaption', null, label && h('strong', null, label + ' '), captionSpan),
    figureReview(fig.figure_key || fig.figure_id),
    description && h('details', 'figure-ai', h('summary', null, 'AI image description'), h('p', null, richText(description))));
  register(seg, node, [{el: captionSpan, text: caption}]);
  return node;
}

/* Render one segment, highlighting find matches and any active evidence quotes. */
function paintSegment(id) {
  const entry = state.segments.get(id);
  if (!entry) return;
  const q = state.find.query;
  const quotes = (state.active?.items || []).map((it, i) => ({...it, i})).filter(it => it.id === id && it.quote);
  entry.parts.forEach((part, p) => {
    const text = part.text, lower = text.toLowerCase();
    // Quotes that touch (or are separated only by spaces/punctuation) become one highlighter stroke.
    const found = quotes.map(it => ({at: text.indexOf(it.quote), it})).filter(x => x.at >= 0)
      .map(({at, it}) => ({s: at, e: at + it.quote.length, q: [it.i]})).sort((a, b) => a.s - b.s);
    const ranges = [];
    for (const r of found) {
      const last = ranges.at(-1);
      if (last && (r.s <= last.e || /^[\s,.;:()\-–]*$/.test(text.slice(last.e, r.s)))) {
        last.e = Math.max(last.e, r.e); last.q.push(...r.q);
      } else ranges.push({...r, cls: 'quote'});
    }
    for (const r of ranges) r.q = r.q.join(' ');
    if (q) for (let i = lower.indexOf(q); i !== -1; i = lower.indexOf(q, i + q.length)) ranges.push({s: i, e: i + q.length, cls: 'hit', key: `${id}/${p}/${i}`});
    part.el.replaceChildren(...rich(text, ranges));
  });
}

function runFind(scroll = true) {
  const raw = $('text-search').value.trim();
  state.find.query = raw.length >= 2 ? raw.toLowerCase() : '';
  for (const id of state.segments.keys()) paintSegment(id);
  const groups = new Map();
  for (const m of $('source').querySelectorAll('mark.hit')) {
    if (!groups.has(m.dataset.hit)) groups.set(m.dataset.hit, []);
    groups.get(m.dataset.hit).push(m);
  }
  state.find.marks = [...groups.values()];
  state.find.index = state.find.marks.length ? 0 : -1;
  showMatch(scroll);
  markNow();
}
function showMatch(scroll = true) {
  const {marks, index, query} = state.find;
  for (const m of $('source').querySelectorAll('mark.current')) m.classList.remove('current');
  $('find-count').textContent = !query ? '' : marks.length ? `${index + 1}/${marks.length}` : '0';
  if (index < 0) return;
  for (const m of marks[index]) m.classList.add('current');
  if (scroll) marks[index][0].scrollIntoView({block: 'center', behavior: smooth()});
}
function stepMatch(delta) {
  const n = state.find.marks.length;
  if (!n) return;
  state.find.index = (state.find.index + delta + n) % n;
  showMatch();
}

/* ---------- evidence: record → article ---------- */

function resolveSegment(e) {
  const id = e.segment_id || e.source_section;
  if (id && state.segments.has(id)) return id;
  if (e.quote) for (const [sid, entry] of state.segments) if ((entry.seg.text || '').includes(e.quote)) return sid;
  return null;
}
function evidenceItems(evidence) {
  const out = [], seen = new Set();
  for (const e of evidence || []) {
    const id = resolveSegment(e);
    const key = id + '|' + (e.quote || '');
    if (id && !seen.has(key)) { seen.add(key); out.push({id, quote: e.quote}); }
  }
  return out;
}

/* Highlight every quote behind one fact; clicking the same fact again steps to its next quote. */
function locate(key, evidence) {
  const items = evidenceItems(evidence);
  if (!items.length) return;
  const again = state.active?.key === key;
  const before = state.active?.items || [];
  state.active = {key, items, index: again ? (state.active.index + 1) % items.length : 0};
  if (!again) {
    for (const id of new Set([...before.map(x => x.id), ...items.map(x => x.id)])) {
      paintSegment(id);
      state.segments.get(id)?.node.classList.toggle('active', items.some(x => x.id === id));
    }
  }
  markNow();
  markSelected();
  // On a phone, keep the reader in the record and show the quote in a sheet instead of jumping away.
  if (narrow() && $('reading-grid').classList.contains('show-record')) openPeek();
  else jumpToActive();
}
function jumpToActive() {
  if (!state.active) return;
  closePeek();
  showPane('source');
  const it = state.active.items[state.active.index];
  const target = $('source').querySelector(`mark.quote[data-q~="${state.active.index}"]`) || state.segments.get(it.id)?.node;
  target?.scrollIntoView({block: 'center', behavior: smooth()});
}

/* ---------- evidence peek (phones) ---------- */

/* The quote with a little of the surrounding text, cut at sentence or word boundaries. */
function quoteInContext(text, quote, room = 240) {
  const at = quote ? text.indexOf(quote) : -1;
  if (at < 0) return [text.length > room * 2 ? text.slice(0, room * 2).replace(/\s+\S*$/, '') + ' …' : text, '', ''];
  let from = Math.max(0, at - room), to = Math.min(text.length, at + quote.length + room);
  if (from > 0) { const cut = text.slice(from, at).search(/[.!?]\s+[A-Z(]/); from = cut >= 0 ? from + cut + 2 : text.indexOf(' ', from) + 1; }
  if (to < text.length) { const end = text.slice(at + quote.length, to).search(/[.!?](\s|$)(?!.*[.!?](\s|$))/); to = end >= 0 ? at + quote.length + end + 1 : text.lastIndexOf(' ', to); }
  return [(from > 0 ? '… ' : '') + text.slice(from, at), quote, text.slice(at + quote.length, to) + (to < text.length ? ' …' : '')];
}
function openPeek() {
  const {items, index} = state.active;
  const it = items[index];
  const seg = state.segments.get(it.id)?.seg || {};
  const [before, quote, after] = quoteInContext(seg.text || seg.caption || '', it.quote);
  $('peek-body').replaceChildren(
    seg.heading && h('p', 'peek-section', seg.heading.split(' / ').join(' › ')),
    h('p', 'peek-quote', richText(before), quote && h('mark', 'quote now', richText(quote)), richText(after)));
  $('peek-body').scrollTop = 0;
  $('peek-count').textContent = items.length > 1 ? `${index + 1} of ${items.length}` : '';
  $('peek-prev').hidden = $('peek-next').hidden = items.length < 2;
  $('peek').style.removeProperty('--drag');
  $('peek').hidden = false;
}
function closePeek() { $('peek').hidden = true; }
function stepPeek(delta) {
  if (!state.active) return;
  const n = state.active.items.length;
  state.active.index = (state.active.index + delta + n) % n;
  markNow(); markSelected(); openPeek();
}

/* Drag a sheet down to dismiss it. */
function dragToDismiss(handle, sheet, close) {
  let start = null;
  handle.addEventListener('pointerdown', e => {
    if (e.target.closest('button')) return;
    start = e.clientY; sheet.classList.add('dragging'); handle.setPointerCapture(e.pointerId);
  });
  handle.addEventListener('pointermove', e => {
    if (start !== null) sheet.style.setProperty('--drag', Math.max(0, e.clientY - start) + 'px');
  });
  const end = e => {
    if (start === null) return;
    const dy = e.clientY - start; start = null;
    sheet.classList.remove('dragging');
    sheet.style.removeProperty('--drag');
    if (dy > 70) close();
  };
  handle.addEventListener('pointerup', end);
  handle.addEventListener('pointercancel', end);
}
function markNow() {
  for (const m of $('source').querySelectorAll('mark.quote')) m.classList.toggle('now', m.dataset.q.split(' ').includes(String(state.active?.index)));
}
function markSelected() {
  for (const n of $('record').querySelectorAll('[data-key]')) {
    const on = n.dataset.key === state.active?.key;
    n.classList.toggle('selected', on);
    const where = n.querySelector('.where');
    if (where) where.textContent = on && state.active.items.length > 1 ? `${state.active.index + 1} of ${state.active.items.length}` : '';
  }
}

/* ---------- record ---------- */

function sectionGroups(section) {
  return Object.entries(section || {}).filter(([k, v]) =>
    !EVIDENCE_KEYS.has(k) && k !== 'limitations' && Array.isArray(v) && v.length && v.every(isObject));
}
function sectionHasContent(section) {
  if (!section || section.documentation_status === 'not_documented') return false;
  return sectionGroups(section).length > 0 || Object.entries(section).some(([k, v]) =>
    !['coverage', 'limitations', 'documentation_status'].includes(k) && !EVIDENCE_KEYS.has(k) && !Array.isArray(v) && !isBlank(v));
}

function tabs() {
  const p = patient();
  const supp = supplementList().length ? ['supplements'] : [];
  if (p) return ['summary', 'timeline', ...Object.keys(p.sections || {}).filter(k => sectionHasContent(p.sections[k])), ...supp, 'raw'];
  if (state.article.annotation) return ['source_facts', ...supp, 'raw'];
  return ['summary', ...supp, 'raw'];
}

function renderPatients() {
  const list = patientList();
  $('tab-record-badge').textContent = list.length > 1 ? list.length : '';
  $('patients').replaceChildren(...(list.length > 1 ? list.map((p, i) => h('button', {
    type: 'button', role: 'tab', class: 'patient' + (patientId(p) === state.pid ? ' active' : ''),
    'aria-selected': String(patientId(p) === state.pid), title: patientLabel(p),
    onclick: () => selectPatient(patientId(p)),
  }, `Patient ${i + 1}`)) : []));
  renderSections();
  renderRecord();
}

function selectPatient(pid) {
  if (pid === state.pid) return;
  closePeek();
  state.pid = pid;
  renderPatients(); saveRoute();
  $('record').scrollTop = 0;
}

function renderSections() {
  const list = tabs();
  if (state.tab === 'raw_json' || state.tab === 'article_details') state.tab = 'raw';
  if (!list.includes(state.tab)) state.tab = list[0];
  $('sections').replaceChildren(...list.map(t => h('button', {
    type: 'button', class: 'section-link' + (t === state.tab ? ' active' : '') + (t === 'raw' ? ' quiet' : ''),
    'aria-current': t === state.tab ? 'page' : null, onclick: () => selectTab(t),
  }, pretty(t))));
  $('sections').querySelector('.active')?.scrollIntoView({block: 'nearest', inline: 'nearest'});
}

function selectTab(tab) {
  if (tab === state.tab) return;
  closePeek();
  state.tab = tab;
  renderSections(); renderRecord(); saveRoute();
  $('record').scrollTop = 0;
  showPane('record');
}
function stepTab(delta) {
  const list = tabs();
  selectTab(list[(list.indexOf(state.tab) + delta + list.length) % list.length]);
}

function renderRecord() {
  const r = $('record'), p = patient();
  r.replaceChildren();
  restart(r, 'enter');
  if (state.tab === 'raw') renderRaw(r);
  else if (state.tab === 'source_facts') renderSourceFacts(r);
  else if (state.tab === 'supplements') renderSupplements(r);
  else if (!p) {
    r.append(h('p', 'empty', 'This article has not been reviewed yet, so there is no extracted record. You can still read the article and download what exists as JSON.'));
  } else if (state.tab === 'summary') renderSummary(r, p);
  else if (state.tab === 'timeline') renderTimeline(r, p);
  else renderSection(r, p.sections[state.tab] || {});
  markSelected();
}

function factTitle(o, index) {
  for (const k of TITLE_KEYS) if (typeof o[k] === 'string' && o[k].trim()) return o[k];
  return o.task ? pretty(o.task) : `Item ${index + 1}`;
}
function formatTime(t) {
  if (!isObject(t) || !(t.text || t.date_iso)) return null;
  return [t.relation && t.relation !== 'unknown' ? words(t.relation) : null, t.text || t.date_iso].filter(Boolean).join(' ');
}
function factValue(o, title, {withTime = false} = {}) {
  let v = o.text_value || o.dose_text || o.amount_text || o.finding || o.outcome || o.reaction || null;
  if (!v && typeof o.numeric_value === 'number') v = String(o.numeric_value);
  if (typeof v !== 'string' || v === title) v = null;
  // A bare number gets its unit ("3.6" → "3.6 mmol/L"); a missing result says why.
  if (v && typeof o.unit === 'string' && o.unit && !/[a-z%]/i.test(v) && !v.includes(o.unit)) v += ' ' + o.unit;
  if (!v && typeof o.result_absent_reason === 'string' && o.result_absent_reason !== 'unknown') v = words(o.result_absent_reason);
  const when = formatTime(o.time);
  if (!v) return when;
  return withTime && when ? `${v} · ${when}` : v;
}
/* The few qualifiers that change what a fact means, as plain words. */
function qualifiers(o) {
  const out = [];
  if (o.subject && !['index_patient', 'unknown'].includes(o.subject)) out.push([words(o.subject), 'violet']);
  if (o.assertion === 'absent') out.push(['absent', 'red']);
  else if (['possible', 'conditional', 'hypothetical'].includes(o.assertion)) out.push([o.assertion, 'amber']);
  if (o.verification_status === 'refuted') out.push(['ruled out', 'red']);
  else if (['provisional', 'differential'].includes(o.verification_status)) out.push([o.verification_status, 'amber']);
  if (['high', 'low', 'abnormal', 'critical', 'positive'].includes(o.flag)) out.push([o.flag, o.flag === 'low' ? 'blue' : 'red']);
  return out;
}

/* One row per fact. Click it to see its source in the article; the chevron opens its details. */
function factRow(o, index, key, title = factTitle(o, index), {withTime = false} = {}) {
  const value = factValue(o, title, {withTime});
  const linked = evidenceItems(o.evidence).length > 0;
  const row = h('div', {class: 'fact', 'data-key': key});
  const more = h('button', {type: 'button', class: 'fact-more', 'aria-expanded': 'false', 'aria-label': 'Details', title: 'Details',
    onclick: () => toggleFact(row, o, key, title)}, icon('chevron'));
  const main = h('button', {type: 'button', class: 'fact-main', title: linked ? 'Show in article' : 'Not found in the article text',
    onclick: () => linked ? locate(key, o.evidence) : toggleFact(row, o, key, title)},
  h('span', 'fact-text',
    h('span', 'fact-title', richText(title)),
    value && h('span', 'fact-value', richText(value)),
    qualifiers(o).map(([text, tone]) => h('span', 'flag ' + tone, text))),
  linked ? h('span', 'fact-locate', h('span', 'where'), icon('locate')) : null);
  row.append(h('div', 'fact-head', main, more));
  if (state.expanded.has(key)) toggleFact(row, o, key, title, true);
  return row;
}

/* Source facts an item points at by id (timeline events list the facts they summarize). */
function linkedFacts(o) {
  if (!Array.isArray(o.fact_ids) || !o.fact_ids.length) return [];
  const byId = new Map((state.article?.annotation?.patients || []).flatMap(p => p.facts || []).map(f => [f.fact_id, f]));
  return o.fact_ids.map(id => byId.get(id)).filter(Boolean);
}

function toggleFact(row, o, key, title, instant = false) {
  const open = !row.classList.contains('open');
  if (open && !row.querySelector('.fact-body')) {
    const missing = (o.evidence || []).filter(e => !resolveSegment(e));
    const linked = linkedFacts(o);
    const rest = linked.length === o.fact_ids?.length ? Object.fromEntries(Object.entries(o).filter(([k]) => k !== 'fact_ids')) : o;
    row.append(h('div', 'fact-body', h('div', 'fact-inner',
      linked.length ? h('div', 'linked-facts', h('p', 'linked-title', 'Recorded values'),
        h('div', 'facts', linked.map((f, i) => factRow({...(isObject(f.fields) ? f.fields : {}), task: f.task, evidence: f.evidence},
          i, `${key}/fact/${f.fact_id}`, undefined, {withTime: true})))) : null,
      details(rest, title) || (linked.length ? null : h('p', 'muted', 'No further details.')),
      missing.length ? h('div', 'unlinked', h('p', 'muted', 'Quoted but not found in the article text:'), missing.map(e => h('blockquote', null, richText(e.quote)))) : null)));
  }
  row.querySelector('.fact-more').setAttribute('aria-expanded', String(open));
  if (open) state.expanded.add(key); else state.expanded.delete(key);
  if (open && !instant) void row.offsetHeight;
  row.classList.toggle('open', open);
}

function details(o, title) {
  const rows = [];
  for (const [k, v] of Object.entries(o)) {
    if (EVIDENCE_KEYS.has(k) || isBlank(v) || v === title) continue;
    if ((k === 'subject' && v === 'index_patient') || (k === 'assertion' && v === 'present')) continue;
    let shown;
    if (k === 'time') shown = formatTime(v);
    else if (typeof v === 'boolean') shown = v ? 'yes' : 'no';
    else if (typeof v === 'string' || typeof v === 'number') shown = String(words(v));
    else if (Array.isArray(v) && v.every(x => typeof x !== 'object')) shown = v.join(', ');
    else if (isObject(v) && Object.values(v).every(x => typeof x !== 'object' || x === null)) {
      shown = Object.entries(v).filter(([, x]) => !isBlank(x)).map(([a, b]) => `${words(a)}: ${words(b)}`).join(' · ');
    }
    if (shown) rows.push(h('dt', null, pretty(k)), h('dd', null, richText(shown)));
  }
  return rows.length ? h('dl', 'details', rows) : null;
}

function viewHeader(title, sub) {
  return h('header', 'view-head', h('h2', null, title), sub && h('p', null, sub));
}

function renderSummary(r, p) {
  const cc = p.sections?.case_context || {};
  const context = [cc.care_setting, cc.chief_complaint && `presenting with ${cc.chief_complaint}`].filter(x => !isBlank(x));
  r.append(viewHeader(cc.index_subject_description || 'Summary', context.join(', ')));
  const claims = p.companions?.summary?.claims || [];
  if (claims.length) r.append(h('p', 'hint', 'Click any sentence or fact to see where it comes from in the article.'));
  r.append(h('div', 'claims', claims.map((c, i) => {
    const key = `summary/${i}`;
    const linked = evidenceItems(c.evidence).length > 0;
    return h('button', {type: 'button', class: 'claim', 'data-key': key, disabled: !linked,
      onclick: () => locate(key, c.evidence)}, richText(c.text), linked ? h('span', 'where') : null);
  })));
  if (!claims.length) r.append(h('p', 'empty', 'No summary was written for this patient.'));
  const missing = Object.entries(p.sections || {}).filter(([, s]) => s.documentation_status === 'not_documented').map(([k]) => pretty(k).toLowerCase());
  if (missing.length) r.append(h('p', 'footnote', `Not documented in the article: ${missing.join(', ')}. This does not mean they were absent.`));
}

function renderSection(r, section) {
  r.append(viewHeader(pretty(state.tab)));
  if (section.limitations?.length) r.append(h('p', 'footnote top', section.limitations.map(l => typeof l === 'string' ? l : JSON.stringify(l)).join(' ')));
  const groups = sectionGroups(section);
  const scalars = Object.fromEntries(Object.entries(section).filter(([k]) =>
    !['coverage', 'limitations', 'documentation_status', ...groups.map(([g]) => g)].includes(k)));
  const d = details(scalars);
  if (d) {
    d.classList.add('stacked');
    r.append(d);
    const evidence = [...(section.evidence || []), ...(section.documentation_evidence || [])];
    const key = `${state.tab}/section`;
    if (evidenceItems(evidence).length) {
      r.append(h('button', {type: 'button', class: 'text-btn', 'data-key': key, onclick: () => locate(key, evidence)},
        icon('locate'), 'Show in article', h('span', 'where')));
    }
  }
  for (const [group, items] of groups) {
    if (groups.length > 1 || group !== 'items') r.append(h('h3', 'group-title', pretty(group)));
    r.append(h('div', 'facts', items.map((it, i) => factRow(it, i, `${state.tab}/${group}/${i}`))));
  }
}

function renderTimeline(r, p) {
  const events = timelineOf(p).events || [];
  r.append(viewHeader('Timeline', events.length ? 'Shown in extraction order; the exact ordering relations are in the raw data.' : ''));
  if (!events.length) { r.append(h('p', 'empty', 'No timeline events were extracted.')); return; }
  r.append(h('ol', 'timeline', events.map((e, i) =>
    h('li', null, factRow(e, i, `timeline/${i}`, e.description || e.event_id)))));
}

function renderSourceFacts(r) {
  const facts = annotatedPatient()?.facts || [];
  r.append(viewHeader('Source facts'));
  r.append(h('div', 'facts', facts.map((f, i) => {
    const o = {...(isObject(f.fields) ? f.fields : {}), task: f.task, evidence: f.evidence};
    return factRow(o, i, `source_facts/${i}`);
  })));
  if (!facts.length) r.append(h('p', 'empty', 'No source facts for this patient.'));
}

/* ---------- supplementary material ---------- */

/* One entry per supplementary file: what the article lists, the reviewed catalogue entry, and any extraction. */
function supplementList(a = state.article) {
  const byId = rows => new Map((rows || []).map(x => [x.supplement_id, x]));
  const manifest = byId(a?.supplement_manifest?.files), docs = byId(a?.supplement_documents), visuals = byId(a?.supplement_visuals);
  const ids = [...new Set([...(a?.source?.supplements || []).map(x => x.supplement_id), ...manifest.keys(), ...docs.keys(), ...visuals.keys()])];
  return ids.map(id => ({id, source: (a.source?.supplements || []).find(x => x.supplement_id === id) || {},
    review: manifest.get(id)?.review, doc: docs.get(id), visual: visuals.get(id)}));
}

/* Rebuild reviewer transcriptions as tables: cell-level (row × column labels) or row-level ("a | b | c"). */
function transcriptionTables(transcriptions) {
  const groups = new Map();
  for (const t of transcriptions || []) {
    const key = t.table_label || (t.page ? `Page ${t.page}` : 'Transcribed text');
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(t);
  }
  return [...groups].map(([label, items]) => {
    const notes = [], cellLevel = items.some(t => t.kind === 'clinical_data' && t.column_label);
    let head = null, body = [];
    if (cellLevel) {
      const cols = [...new Set(items.filter(t => t.column_label && t.kind === 'clinical_data').map(t => t.column_label))];
      const rows = new Map();
      for (const t of items) {
        if (t.kind !== 'clinical_data' || !t.column_label) { notes.push(t.text); continue; }
        const r = t.row_label || '';
        if (!rows.has(r)) rows.set(r, new Map());
        rows.get(r).set(t.column_label, t.text);
      }
      head = ['', ...cols];
      body = [...rows].map(([r, cells]) => [r, ...cols.map(c => cells.get(c) ?? '')]);
    } else {
      for (const t of items) {
        const cells = (t.text || '').split(' | ');
        if (cells.length < 2) notes.push(t.text);
        else if (!head && t.kind === 'context' && !body.length) head = cells;
        else body.push(cells);
      }
    }
    const shown = body.slice(0, 300);
    return h('figure', 'table-block supp-table',
      h('figcaption', null, h('strong', null, label)),
      notes.filter(Boolean).map(n => h('p', 'table-note', richText(n))),
      shown.length ? h('div', 'table-scroll', h('table', null,
        head && h('thead', null, h('tr', null, head.map(c => h('th', null, richText(c))))),
        h('tbody', null, shown.map(row => h('tr', null, row.map(c => h('td', null, richText(c)))))))) : null,
      body.length > shown.length ? h('p', 'table-note', `Showing ${shown.length} of ${body.length} rows; the rest are in Raw data.`) : null);
  });
}

/* Short patient names matching the patient tabs: "Patient 2", or "this patient" when there is one. */
function shortPatient(id) {
  const ids = patientList().map(patientId), i = ids.indexOf(id);
  return i < 0 ? id : ids.length > 1 ? `Patient ${i + 1}` : 'this patient';
}

const SUPP_LABEL = {clinical_case_table: 'Patient data table', literature_table: 'Literature table', clinical_data: 'Clinical data'};

function renderSupplements(r) {
  const list = supplementList();
  const extracted = list.filter(s => s.doc || s.visual).length;
  r.append(viewHeader('Supplementary material',
    `${plural(list.length, 'file')} listed with the article · ${extracted ? `${extracted} with extracted content` : 'nothing extracted yet'}`));
  for (const s of list) {
    const assets = s.doc?.assets?.length ? s.doc.assets : s.visual?.assets?.length ? s.visual.assets : s.source.assets || [];
    const name = assets.map(x => x.filename).filter(Boolean).join(', ') || s.id;
    const kind = s.review?.content_class || s.doc?.document_classification;
    const status = s.doc || s.visual ? ['Extracted', 'blue'] : s.review ? ['Catalogued', 'violet'] : ['Not reviewed yet', 'amber'];
    const facts = s.doc?.clinical_data?.facts || s.visual?.clinical_data?.facts || [];
    const mine = facts.filter(f => f.patient_ids?.length && (!state.pid || f.patient_ids.includes(state.pid)));
    const unassigned = facts.filter(f => !f.patient_ids?.length);
    const factList = (rows, group) => h('div', 'facts', rows.map((f, i) => {
      const fields = isObject(f.fields) ? f.fields : {};
      const where = (f.evidence || []).map(e => e.quote).filter(q => q && q.trim()).join(' · ');
      return factRow({...fields, task: f.task, from_supplement: where}, i, `supplements/${s.id}/${group}/${i}`, undefined, {withTime: true});
    }));
    const scope = s.review && (s.review.patient_scope === 'aggregate' ? 'Aggregate data, no individual patient'
      : s.review.patient_ids?.length ? 'Belongs to ' + s.review.patient_ids.map(shortPatient).join(', ') : null);
    const summary = s.doc?.contents_summary || s.visual?.analysis?.visual?.general_description
      || s.review?.content_class_rationale || s.source.description;
    const images = assets.filter(x => /\.(jpe?g|png|gif|webp)(\?|$)/i.test(x.url || x.filename || '')).map(x => safeURL(x.url)).filter(Boolean);
    const card = h('section', 'supp',
      h('header', 'supp-head',
        h('div', 'supp-name', h('strong', null, s.id), h('span', null, name)),
        h('span', 'flag ' + status[1], status[0])),
      h('p', 'supp-meta', [kind && (SUPP_LABEL[kind] || words(kind)), scope,
        s.review && (s.review.ehr_includable ? 'EHR includable' : 'not EHR includable')].filter(Boolean).join(' · ')),
      images.map(src => h('button', {type: 'button', class: 'figure-zoom', 'aria-label': 'Enlarge ' + s.id, onclick: () => openLightbox(src, `${s.id} ${summary || ''}`)},
        h('img', {src, alt: summary || s.id, loading: 'lazy', referrerpolicy: 'no-referrer'}))),
      summary && h('p', 'supp-summary', richText(summary)),
      h('p', 'supp-links', assets.map(x => safeURL(x.url) && h('a', {href: safeURL(x.url), target: '_blank', rel: 'noopener noreferrer'}, `Open ${x.filename || 'file'} ↗`))),
      mine.length ? [h('h3', 'group-title', `Extracted for ${state.pid ? shortPatient(state.pid) : 'this article'} (${mine.length})`), factList(mine, 'mine')] : null,
      unassigned.length ? [h('h3', 'group-title', `Not assigned to a patient (${unassigned.length})`),
        h('p', 'footnote top', 'The reviewer could not tell which patient these belong to, so they are kept out of every record.'),
        factList(unassigned, 'unassigned')] : null,
      facts.length > mine.length + unassigned.length
        ? h('p', 'footnote', `${plural(facts.length - mine.length - unassigned.length, 'more fact')} in this file belong to other patients.`) : null,
      (s.doc?.clinical_data?.transcriptions || []).length ? h('details', 'supp-transcript',
        h('summary', null, `Transcribed content (${s.doc.clinical_data.transcriptions.length} entries)`),
        transcriptionTables(s.doc.clinical_data.transcriptions)) : null,
      [s.review?.notes, ...(s.doc?.limitations || []), ...(s.review?.limitations || [])].filter(x => typeof x === 'string').length
        ? h('p', 'footnote', [s.review?.notes, ...(s.review?.limitations || []), ...(s.doc?.limitations || [])].filter(x => typeof x === 'string').join(' ')) : null);
    r.append(card);
  }
  r.append(h('p', 'footnote', 'Supplementary files sit outside the article text, so their facts cannot be highlighted in the article. Open the file to check them.'));
}

function renderRaw(r) {
  const a = state.article;
  r.append(viewHeader('Raw data', 'Everything the server has for this article, including review receipts and provenance.'),
    h('p', null, h('a', {href: `api/articles/${encodeURIComponent(a.article_id)}.json`, download: a.article_id + '.json'}, 'Download JSON')),
    h('pre', 'json', JSON.stringify(a, null, 2)));
}

/* ---------- layout ---------- */

const narrow = () => matchMedia('(max-width: 900px)').matches;
function showPane(which) {
  if (!narrow()) return;
  const grid = $('reading-grid');
  const was = grid.classList.contains('show-record') ? 'record' : 'source';
  grid.classList.toggle('show-record', which === 'record');
  $('show-source').classList.toggle('active', which !== 'record');
  $('show-record').classList.toggle('active', which === 'record');
  if (which !== 'record') closePeek();
  if (was !== which) {
    // Slide the new sheet in from the side it lives on: Article on the left, Record on the right.
    restart(grid.querySelector(which === 'record' ? '.record-sheet' : '.source-sheet'), which === 'record' ? 'swipe-in-left' : 'swipe-in-right');
    document.body.classList.remove('chrome-hidden');
  }
  syncTabbar();
}
function toggleLibrary() {
  if (narrow()) {
    const list = $('articles'), row = list.querySelector('.active');
    // Centre the current article; measured relative to the list so it works mid-slide.
    if (document.body.classList.toggle('library-open') && row)
      list.scrollTop += row.getBoundingClientRect().top - list.getBoundingClientRect().top - (list.clientHeight - row.offsetHeight) / 2;
  } else store('libraryHidden', document.body.classList.toggle('library-hidden') ? '1' : '');
  syncTabbar();
}
function syncTabbar() {
  const library = document.body.classList.contains('library-open');
  const record = $('reading-grid').classList.contains('show-record');
  $('tab-library').classList.toggle('active', library);
  $('tab-source').classList.toggle('active', !library && !record);
  $('tab-record').classList.toggle('active', !library && record);
  for (const id of ['tab-library', 'tab-source', 'tab-record'])
    $(id).setAttribute('aria-current', $(id).classList.contains('active') ? 'page' : 'false');
  if (library) closePeek();
}

/* Phones: swipe sideways between Article and Record (not inside things that scroll sideways). */
function initSwipe() {
  let t = null;
  $('reading-grid').addEventListener('touchstart', e => {
    const x = e.touches[0];
    t = e.touches.length === 1 && !e.target.closest('.table-scroll, .sections, .patients, .json, input, .find')
      ? {x: x.clientX, y: x.clientY, at: Date.now()} : null;
  }, {passive: true});
  $('reading-grid').addEventListener('touchend', e => {
    if (!t || !narrow()) return;
    const x = e.changedTouches[0], dx = x.clientX - t.x, dy = x.clientY - t.y, quick = Date.now() - t.at < 600;
    t = null;
    if (!quick || Math.abs(dx) < 70 || Math.abs(dx) < Math.abs(dy) * 1.8) return;
    if (window.getSelection()?.toString()) return;
    showPane(dx < 0 ? 'record' : 'source');
  }, {passive: true});
}

/* Phones: tuck the top bar away while reading down, bring it back on the way up; track reading progress. */
function initScrollChrome() {
  for (const el of [$('source-scroll'), $('record')]) {
    let last = 0;
    el.addEventListener('scroll', () => {
      const y = el.scrollTop;
      if (el === $('source-scroll')) {
        const max = el.scrollHeight - el.clientHeight;
        $('progress').style.setProperty('--read', max > 0 ? Math.min(1, y / max).toFixed(4) : 0);
      }
      if (!narrow() || Math.abs(y - last) < 6) return;
      document.body.classList.toggle('chrome-hidden', y > last && y > 80);
      last = y;
    }, {passive: true});
  }
}

function initSplitter() {
  const grid = $('reading-grid'), bar = $('splitter');
  let split = Number(load('split2')) || 52;
  const set = pct => { split = Math.min(72, Math.max(32, pct)); grid.style.setProperty('--split', split + '%'); store('split2', split); };
  if (load('split2')) set(split);
  bar.addEventListener('pointerdown', e => {
    e.preventDefault();
    bar.setPointerCapture(e.pointerId);
    document.body.classList.add('resizing');
    const rect = grid.getBoundingClientRect();
    const move = ev => set((ev.clientX - rect.left) / rect.width * 100);
    const up = () => { bar.removeEventListener('pointermove', move); bar.removeEventListener('pointerup', up); document.body.classList.remove('resizing'); };
    bar.addEventListener('pointermove', move);
    bar.addEventListener('pointerup', up);
  });
  bar.addEventListener('keydown', e => {
    if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') { e.preventDefault(); set(split + (e.key === 'ArrowLeft' ? -2 : 2)); }
  });
  bar.addEventListener('dblclick', () => set(52));
}

/* The new theme spreads out as a circle from the button (View Transitions); instant where unsupported. */
function toggleTheme(e) {
  const root = document.documentElement;
  const dark = root.dataset.theme ? root.dataset.theme === 'dark' : matchMedia('(prefers-color-scheme: dark)').matches;
  const apply = () => { root.dataset.theme = dark ? 'light' : 'dark'; store('theme', root.dataset.theme); };
  if (!document.startViewTransition || matchMedia('(prefers-reduced-motion: reduce)').matches) return apply();
  const r = e?.currentTarget?.getBoundingClientRect?.();
  const x = r ? r.left + r.width / 2 : innerWidth - 40, y = r ? r.top + r.height / 2 : 28;
  // Percentages of the page, not pixels: some webviews scale pixel clip-paths by the device pixel ratio.
  const W = innerWidth, H = innerHeight, at = `at ${(100 * x / W).toFixed(3)}% ${(100 * y / H).toFixed(3)}%`;
  const radius = 100 * Math.hypot(Math.max(x, W - x), Math.max(y, H - y)) / (Math.hypot(W, H) / Math.SQRT2);
  document.startViewTransition(apply).ready.then(() => root.animate(
    {clipPath: [`circle(0% ${at})`, `circle(${radius.toFixed(3)}% ${at})`]},
    {duration: 550, easing: 'cubic-bezier(.4, 0, .2, 1)', pseudoElement: '::view-transition-new(root)'}));
}

/* ---------- dropdowns ----------
   The native <select> stays the source of truth (value, options, change events). With a mouse or trackpad,
   a button + listbox stands in for the OS popup so the menu drops below the field; touch keeps the native picker. */
const SELECT_VALUE = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value');
let openDropdown = null;

function enhanceSelect(select) {
  if (!matchMedia('(hover: hover) and (pointer: fine)').matches || select.dataset.enhanced) return;
  select.dataset.enhanced = '1';
  const label = select.getAttribute('aria-label') || select.closest('label')?.firstChild?.textContent?.trim() || '';
  const value = h('span', 'dd-value');
  const trigger = h('button', {type: 'button', class: 'dd-trigger', 'aria-haspopup': 'listbox', 'aria-expanded': 'false', 'aria-label': label}, value);
  const list = h('ul', {class: 'dd-list', role: 'listbox', tabindex: '-1', 'aria-label': label, hidden: true});
  // The trigger comes first so a wrapping <label> activates it rather than the hidden select.
  select.before(trigger);
  select.classList.add('dd-native');
  select.tabIndex = -1;
  select.setAttribute('aria-hidden', 'true');
  document.body.append(list);
  let active = -1, typed = '', typedAt = 0;

  const sync = () => { value.textContent = select.selectedOptions[0]?.textContent || ''; };
  Object.defineProperty(select, 'value', {configurable: true, get() { return SELECT_VALUE.get.call(this); },
    set(v) { SELECT_VALUE.set.call(this, v); sync(); }});
  sync();

  const items = () => [...list.children];
  const highlight = i => {
    const all = items();
    active = Math.max(0, Math.min(all.length - 1, i));
    all.forEach((li, j) => li.classList.toggle('active', j === active));
    if (all[active]) { list.setAttribute('aria-activedescendant', all[active].id); all[active].scrollIntoView({block: 'nearest'}); }
  };
  const place = () => {
    const r = trigger.getBoundingClientRect(), below = innerHeight - r.bottom - 12, above = r.top - 12;
    const up = below < 220 && above > below;
    list.classList.toggle('up', up);
    list.style.minWidth = r.width + 'px';
    list.style.maxHeight = Math.min(340, up ? above : below) + 'px';
    list.style.left = Math.min(r.left, innerWidth - list.offsetWidth - 8) + 'px';
    list.style.top = up ? '' : (r.bottom + 6) + 'px';
    list.style.bottom = up ? (innerHeight - r.top + 6) + 'px' : '';
  };
  const close = (focus = true) => {
    if (list.hidden) return;
    list.hidden = true; trigger.setAttribute('aria-expanded', 'false');
    openDropdown = null;
    if (focus) trigger.focus();
  };
  const choose = i => {
    const opt = select.options[i];
    if (opt && select.value !== opt.value) { select.value = opt.value; select.dispatchEvent(new Event('change', {bubbles: true})); }
    close();
  };
  const fill = () => list.replaceChildren(...[...select.options].map((o, i) => {
      const li = h('li', {role: 'option', id: `${label.replace(/\W+/g, '-')}-opt-${i}`, 'aria-selected': String(o.selected), class: o.selected ? 'selected' : null},
        h('span', 'dd-check', o.selected ? icon('check') : null), o.textContent);
      li.addEventListener('pointermove', () => { if (active !== i) highlight(i); });
      li.addEventListener('click', () => choose(i));
      return li;
    }));
  const open = () => {
    if (openDropdown && openDropdown !== close) openDropdown(false);
    fill();
    list.hidden = false; trigger.setAttribute('aria-expanded', 'true');
    openDropdown = close;
    place();
    highlight(select.selectedIndex);
    list.focus({preventScroll: true});
    restart(list, 'dd-in');
  };

  // Options can arrive or change while the menu is open (e.g. specialties once the catalog loads): rebuild it.
  new MutationObserver(() => {
    sync();
    if (!list.hidden) { fill(); place(); highlight(select.selectedIndex); }
  }).observe(select, {childList: true, subtree: true});

  trigger.addEventListener('click', () => list.hidden ? open() : close());
  trigger.addEventListener('keydown', e => {
    if (['ArrowDown', 'ArrowUp', 'Enter', ' '].includes(e.key)) { e.preventDefault(); open(); }
  });
  list.addEventListener('keydown', e => {
    const n = items().length;
    const keys = {ArrowDown: () => highlight(active + 1), ArrowUp: () => highlight(active - 1),
      Home: () => highlight(0), End: () => highlight(n - 1), PageDown: () => highlight(active + 8), PageUp: () => highlight(active - 8),
      Enter: () => choose(active), ' ': () => choose(active), Escape: () => close(), Tab: () => close(false)};
    if (keys[e.key]) { if (e.key !== 'Tab') e.preventDefault(); e.stopPropagation(); keys[e.key](); return; }
    if (e.key.length === 1 && !e.metaKey && !e.ctrlKey) {
      // Type-ahead: keep typing to narrow, pause to start over.
      typed = (Date.now() - typedAt > 700 ? '' : typed) + e.key.toLowerCase(); typedAt = Date.now();
      const all = items(), from = typed.length === 1 ? active + 1 : active;
      const hit = [...all.slice(from), ...all.slice(0, from)].find(li => li.textContent.trim().toLowerCase().startsWith(typed));
      if (hit) highlight(all.indexOf(hit));
      e.stopPropagation();
    }
  });
  list.addEventListener('blur', e => { if (!list.contains(e.relatedTarget) && e.relatedTarget !== trigger) close(false); });
  document.addEventListener('pointerdown', e => { if (!list.hidden && !list.contains(e.target) && !trigger.contains(e.target)) close(false); }, true);
  addEventListener('resize', () => close(false));
  document.addEventListener('scroll', e => { if (!list.hidden && !list.contains(e.target)) close(false); }, true);
}

function initLightboxSwipe() {
  const box = $('lightbox');
  let start = null;
  box.addEventListener('touchstart', e => { start = e.touches.length === 1 ? e.touches[0].clientY : null; }, {passive: true});
  box.addEventListener('touchmove', e => {
    if (start !== null && e.touches.length === 1) $('lightbox-img').style.setProperty('--drag', Math.max(0, e.touches[0].clientY - start) + 'px');
  }, {passive: true});
  box.addEventListener('touchend', e => {
    const dy = start === null ? 0 : e.changedTouches[0].clientY - start;
    start = null;
    $('lightbox-img').style.removeProperty('--drag');
    if (dy > 90) box.close();
  });
}

function openLightbox(src, caption) {
  $('lightbox-img').src = src;
  $('lightbox-img').alt = caption;
  $('lightbox-caption').textContent = caption;
  $('lightbox').showModal();
}

/* ---------- wiring ---------- */

function debounce(fn, ms) { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; }

if (load('theme')) document.documentElement.dataset.theme = load('theme');
if (load('libraryHidden')) document.body.classList.add('library-hidden');
initSplitter();
document.querySelectorAll('select').forEach(enhanceSelect);
initSwipe();
initScrollChrome();
initLightboxSwipe();
dragToDismiss($('peek-grip'), $('peek'), closePeek);
dragToDismiss($('peek').querySelector('.peek-head'), $('peek'), closePeek);

$('search').addEventListener('input', debounce(() => { state.filter.q = $('search').value; renderCatalog(); }, 80));
$('status').addEventListener('change', () => { state.filter.status = $('status').value; renderCatalog(); });
try { state.filter.advanced = JSON.parse(load('filters') || '{}') || {}; } catch { state.filter.advanced = {}; }
for (const el of document.querySelectorAll('[data-filter]'))
  el.addEventListener('change', () => setAdvanced({...state.filter.advanced, [el.dataset.filter]: el.value}));
$('advanced-reset').onclick = () => setAdvanced({});
$('advanced-toggle').onclick = () => {
  const open = $('advanced').hidden;
  $('advanced').hidden = !open;
  $('advanced-toggle').setAttribute('aria-expanded', String(open));
};
/* Links from the overview carry filters in the hash (e.g. #specialty=cardiac&patients=5+); they replace saved ones. */
{
  const p = new URLSearchParams(location.hash.slice(1));
  const keys = Object.keys(ADVANCED);
  if (!p.has('article') && [...p.keys()].some(k => k === 'specialty' || k === 'status' || keys.includes(k))) {
    state.filter.specialty = p.get('specialty') || '';
    state.filter.status = $('status').value = p.get('status') || '';
    state.filter.advanced = Object.fromEntries(keys.map(k => [k, p.get(k) || '']).filter(([, v]) => v));
    store('filters', JSON.stringify(state.filter.advanced));
    if (narrow()) document.body.classList.add('library-open');
  }
}
syncAdvanced();
$('specialty').addEventListener('change', () => { state.filter.specialty = $('specialty').value; renderCatalog(); });
$('text-search').addEventListener('input', debounce(() => runFind(), 120));
$('text-search').addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); stepMatch(e.shiftKey ? -1 : 1); } });
$('theme').onclick = toggleTheme;
$('toggle-library').onclick = toggleLibrary;
$('scrim').onclick = () => { document.body.classList.remove('library-open'); syncTabbar(); };
$('show-source').onclick = () => showPane('source');
$('show-record').onclick = () => showPane('record');
$('tab-library').onclick = () => { if (!document.body.classList.contains('library-open')) toggleLibrary(); };
$('tab-source').onclick = () => { document.body.classList.remove('library-open'); showPane('source'); };
$('tab-record').onclick = () => { document.body.classList.remove('library-open'); showPane('record'); };
$('peek-close').onclick = closePeek;
$('peek-open').onclick = jumpToActive;
$('peek-prev').onclick = () => stepPeek(-1);
$('peek-next').onclick = () => stepPeek(1);
// The toad hops; only the name goes back to the overview page.
$('toad').addEventListener('click', () => { hop(); boing(); });
$('lightbox').addEventListener('click', e => { if (e.target === $('lightbox')) $('lightbox').close(); });

document.addEventListener('keydown', e => {
  if (document.querySelector('dialog[open]')) return;
  const typing = e.target.closest?.('input, select, textarea, [role="listbox"]');
  if (e.key === 'Escape') {
    if (typing) e.target.blur();
    else if (!$('peek').hidden) closePeek();
    else { document.body.classList.remove('library-open'); syncTabbar(); }
    return;
  }
  if (typing || e.metaKey || e.ctrlKey || e.altKey) return;
  const keys = {
    '/': () => $('search').focus(),
    f: () => { showPane('source'); $('text-search').focus(); },
    j: () => stepArticle(1), k: () => stepArticle(-1),
    ']': () => stepTab(1), '[': () => stepTab(-1),
  };
  if (keys[e.key]) { e.preventDefault(); keys[e.key](); }
});

function followRoute() {
  const r = route();
  if (!r.aid) return;
  state.pid = r.pid; state.tab = r.tab;
  if (r.aid !== state.aid) loadArticle(r.aid);
  else if (state.article) {
    if (!patientList().map(patientId).includes(state.pid)) state.pid = patientList()[0] ? patientId(patientList()[0]) : null;
    renderPatients();
  }
}
window.addEventListener('popstate', followRoute);
window.addEventListener('hashchange', followRoute);

refresh();
setInterval(() => { if (!document.hidden) refresh(); }, 15000);
document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
