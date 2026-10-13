/* Decisions command centre: renders the needs-decision snapshot as tappable
 * option panels. Reads are plain GETs. Sync and act are POSTs that carry the
 * per-process session token, because act writes to GitHub. */
'use strict';

const API = '/api/plugins/continuum';
const state = { snap: null, token: null, filter: 'all', selected: {}, busy: false };

const $ = (id) => document.getElementById(id);

function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

function banner(msg) {
  const el = $('dec-banner');
  el.hidden = !msg;
  el.textContent = msg || '';
}

async function api(path, opts) {
  const res = await fetch(API + path, opts || {});
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || data.error || ('HTTP ' + res.status));
  return data;
}

async function authedPost(path, body) {
  if (!state.token) state.token = (await api('/decisions/session')).token;
  return api(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Continuum-Token': state.token },
    body: JSON.stringify(body || {}),
  });
}

function idempotencyKey(item, optionId, mode) {
  const raw = [item.id, item.fingerprint, optionId, mode].join('|');
  let h = 0;
  for (let i = 0; i < raw.length; i++) h = ((h * 31) + raw.charCodeAt(i)) >>> 0;
  return raw + '|' + h.toString(16);
}

function visibleItems() {
  const items = (state.snap && state.snap.items) || [];
  const f = state.filter;
  return items.filter((it) => {
    if (f === 'all') return true;
    if (f === 'waiting') return it.local_status === 'waiting';
    if (f === 'done') return it.local_status !== 'waiting';
    if (f === 'ion') return it.source === 'ion';
    return it.kind === f;
  });
}

function kindLabel(it) {
  if (it.source === 'ion') return 'Ion proposal';
  return it.kind === 'pull_request' ? 'Pull request' : 'Issue';
}

function modeChoices(it, option) {
  if (it.source === 'ion') return [{ id: 'record', label: 'Record decision (label)' }];
  const modes = (option && option.modes) || ['comment', 'dispatch'];
  const labels = { comment: 'Comment the ruling', dispatch: 'Dispatch an agent' };
  return modes.filter((m) => labels[m]).map((m) => ({ id: m, label: labels[m] }));
}

function renderMetrics() {
  const c = (state.snap && state.snap.counts) || {};
  const bits = [
    ['Waiting', c.waiting || 0], ['Total labelled', c.total || 0],
    ['Pull requests', c.pull_requests || 0], ['Issues', c.issues || 0],
    ['Ion proposals', c.ion || 0],
  ];
  $('dec-metrics').innerHTML = bits.map(([k, v]) =>
    '<div class="dec-metric"><span class="dec-metric-v">' + esc(v) +
    '</span><span class="dec-metric-k">' + esc(k) + '</span></div>').join('');
  $('dec-synced').textContent = state.snap && state.snap.generated_at
    ? 'synced ' + state.snap.generated_at : 'not synced yet';
  const degraded = (state.snap && state.snap.degraded) || [];
  banner(degraded.length ? 'Some items synced without full detail: ' + degraded.join('; ') : '');
}

function optionPanel(it, opt) {
  const sel = state.selected[it.id] && state.selected[it.id].optionId === opt.id;
  const tags = [];
  if (opt.recommended) tags.push('<span class="dec-tag dec-tag--rec">Recommended</span>');
  if (opt.worst) tags.push('<span class="dec-tag dec-tag--worst">Worst option</span>');
  if (opt.source === 'default') tags.push('<span class="dec-tag">Standard option</span>');
  return '<button type="button" class="dec-option' + (sel ? ' on' : '') +
    '" data-act="pick" data-item="' + esc(it.id) + '" data-option="' + esc(opt.id) +
    '" aria-pressed="' + sel + '">' +
    '<span class="dec-option-head"><span class="dec-option-key">' + esc(opt.key) +
    '</span> ' + esc(opt.label) + ' ' + tags.join(' ') + '</span>' +
    (opt.detail ? '<span class="dec-option-detail">' + esc(opt.detail) + '</span>' : '') +
    (opt.cost ? '<span class="dec-option-cost">Cost: ' + esc(opt.cost) + '</span>' : '') +
    '</button>';
}

function actionBar(it) {
  const sel = state.selected[it.id];
  if (!sel) return '';
  const opt = it.options.find((o) => o.id === sel.optionId);
  if (!opt) return '';
  const modes = modeChoices(it, opt);
  const modeBtns = modes.map((m) =>
    '<button type="button" class="dec-mode' + (sel.mode === m.id ? ' on' : '') +
    '" data-act="mode" data-item="' + esc(it.id) + '" data-mode="' + esc(m.id) +
    '" aria-pressed="' + (sel.mode === m.id) + '">' + esc(m.label) + '</button>').join('');
  return '<div class="dec-actionbar">' +
    '<div class="dec-modes">' + modeBtns + '</div>' +
    '<label class="dec-note-label">Note for the record (optional)' +
    '<textarea class="dec-note" data-note="' + esc(it.id) + '" rows="2" ' +
    'placeholder="Anything the author or agent must know">' + esc(sel.note || '') + '</textarea></label>' +
    '<button type="button" class="dec-apply" data-act="apply" data-item="' + esc(it.id) + '"' +
    (state.busy ? ' disabled' : '') + '>Apply: ' + esc(opt.label) + '</button>' +
    '<p class="dec-apply-hint">' +
    (it.source === 'ion'
      ? 'Records the matching ion label for the plan on display and clears needs decision.'
      : 'Comment posts the ruling on the item for its author. Dispatch writes an agent packet, announces it on the item, and clears needs decision.') +
    '</p></div>';
}

function itemCard(it) {
  const done = it.local_status !== 'waiting';
  const age = it.age_days >= 1 ? Math.round(it.age_days) + 'd waiting' : 'waiting today';
  const meta = [it.repo + '#' + it.number, kindLabel(it), age,
    it.author ? 'by ' + it.author : '',
    it.kind === 'pull_request' && it.changed_files
      ? it.changed_files + ' files, +' + it.additions + '/-' + it.deletions : '',
    it.draft ? 'draft' : ''].filter(Boolean).join(' · ');
  const decided = done && it.last_action
    ? '<p class="dec-decided">Decided: ' + esc(it.last_action.option_label || '') +
      ' (' + esc(it.last_action.mode || '') + ', ' + esc(it.local_status) + ')</p>' : '';
  return '<article class="dec-card' + (done ? ' done' : '') + '" data-item="' + esc(it.id) + '">' +
    '<header class="dec-card-head"><span class="dec-meta">' + esc(meta) + '</span>' +
    '<a class="dec-open" href="' + esc(it.url) + '" target="_blank" rel="noopener">Open on GitHub</a></header>' +
    '<h2 class="dec-card-title">' + esc(it.title) + '</h2>' +
    '<p class="dec-question">' + esc(it.question) + '</p>' +
    (it.context ? '<p class="dec-context">' + esc(it.context) + '</p>' : '') +
    decided +
    (done ? '' : '<div class="dec-options">' + it.options.map((o) => optionPanel(it, o)).join('') + '</div>' + actionBar(it)) +
    '</article>';
}

function render() {
  renderMetrics();
  const list = $('dec-list');
  const items = visibleItems();
  if (!state.snap || !state.snap.generated_at) {
    list.innerHTML = '<p class="dec-empty">No snapshot yet. Use Sync GitHub to pull the current needs-decision queue.</p>';
    return;
  }
  list.innerHTML = items.length
    ? items.map(itemCard).join('')
    : '<p class="dec-empty">Nothing is waiting under this filter. The queue is clear.</p>';
}

async function load() {
  try {
    state.snap = await api('/decisions');
    render();
  } catch (e) { banner(String(e.message || e)); }
}

async function syncNow() {
  if (state.busy) return;
  state.busy = true; render();
  try {
    state.snap = await authedPost('/decisions/sync');
    state.selected = {};
    banner('');
  } catch (e) { banner(String(e.message || e)); }
  state.busy = false; render();
}

async function apply(itemId) {
  const it = ((state.snap && state.snap.items) || []).find((x) => x.id === itemId);
  const sel = state.selected[itemId];
  if (!it || !sel || state.busy) return;
  const noteEl = document.querySelector('textarea[data-note="' + CSS.escape(itemId) + '"]');
  const note = noteEl ? noteEl.value : (sel.note || '');
  state.busy = true; render();
  try {
    const out = await authedPost('/decisions/' + encodeURIComponent(it.repo) + '/' + it.number + '/act', {
      option_id: sel.optionId, mode: sel.mode, note: note,
      expected_fingerprint: it.fingerprint,
      idempotency_key: idempotencyKey(it, sel.optionId, sel.mode),
    });
    state.snap = out.snapshot;
    delete state.selected[itemId];
    banner('');
  } catch (e) { banner(String(e.message || e)); }
  state.busy = false; render();
}

document.addEventListener('click', (ev) => {
  const chip = ev.target.closest('[data-filter]');
  if (chip) {
    state.filter = chip.getAttribute('data-filter');
    document.querySelectorAll('.dec-chip').forEach((c) => c.classList.toggle('on', c === chip));
    render();
    return;
  }
  const el = ev.target.closest('[data-act]');
  if (!el) return;
  const itemId = el.getAttribute('data-item');
  const it = ((state.snap && state.snap.items) || []).find((x) => x.id === itemId);
  if (!it) return;
  const act = el.getAttribute('data-act');
  if (act === 'pick') {
    const opt = it.options.find((o) => o.id === el.getAttribute('data-option'));
    const modes = modeChoices(it, opt);
    const prev = state.selected[itemId] || {};
    state.selected[itemId] = {
      optionId: opt.id,
      mode: modes.some((m) => m.id === prev.mode) ? prev.mode : modes[0].id,
      note: prev.note || '',
    };
    render();
  } else if (act === 'mode') {
    state.selected[itemId].mode = el.getAttribute('data-mode');
    render();
  } else if (act === 'apply') {
    apply(itemId);
  }
});

document.addEventListener('input', (ev) => {
  const noteEl = ev.target.closest('textarea[data-note]');
  if (noteEl && state.selected[noteEl.getAttribute('data-note')]) {
    state.selected[noteEl.getAttribute('data-note')].note = noteEl.value;
  }
});

$('dec-sync').addEventListener('click', syncNow);
load();

