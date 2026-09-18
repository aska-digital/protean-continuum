/**
 * app.js — Continuum standalone browser renderer (plain ESM, no SDK, no framework).
 *
 * Re-expresses the approved desktop board behaviour for a local browser, and adds the M4
 * Continuity Playground read surface:
 *   - eight fixed lanes (BOARD_COLUMNS / COLUMN_LABELS from the shared module)
 *   - Today is the browser default request; view / sort / direction / filters / q / page /
 *     page_size are persisted in the page URL (never in the registry)
 *   - all sorting and filtering happen SERVER-side; this file renders what it is given and
 *     infers no audience, placement, summary, or resume fact of its own
 *   - Inbox exit is one atomic audited acceptance carrying a placement payload
 *   - later movement is an audited set_placement; Undo reverses by audit_id
 *   - detail/session identity, bare Copy ID, and the profile-scoped resume command
 *   - session panes: detail-only, lazy on first expand, one open at a time, bounded plain
 *     text with an honest captured-excerpt marker (no fabricated show-more)
 *   - Space opens Move-to and immediately focuses the highlighted item; arrows/Home/End
 *     navigate; Enter activates; Escape restores focus without mutation
 *   - drag/drop: accepted-lane drop once, Inbox rejected, drag state clears on every exit
 *
 * The interaction primitives are the SAME pure module the desktop surface imports
 * (`../desktop/kanban-interaction.js`) — resolved browser-relative from this file.
 *
 * Reads are GET-only. The single mutation surface is the audited /review route.
 * `/scan` is triggered ONLY by the explicit "Scan now" button — never on load.
 */

import {
  BOARD_COLUMNS,
  COLUMN_LABELS,
  NON_INBOX_PLACEMENTS,
  buildAcceptToast,
  buildAcceptActions,
  buildFailureToast,
  buildFailureActions,
  buildUndoToast,
  navigateRight,
  navigateLeft,
  navigateDown,
  navigateUp,
  navigateHome,
  navigateEnd,
  classifyKeyEvent,
  closeMenuFocusTarget,
  canDropOnColumn,
  copyIdValue,
  menuItemsFor,
  menuInitialHighlight,
  menuHighlightIndex,
  menuActivate,
} from '../desktop/kanban-interaction.js'

const API = '/api/plugins/continuum'
const POLL_MS = 15000

const EMPTY_COPY = {
  inbox: 'Nothing is waiting in the inbox in this snapshot.',
  ongoing: 'Nothing is in progress in this snapshot.',
  blocked: 'Nothing is blocked in this snapshot.',
  waiting_on_you: 'Nothing is waiting on you in this snapshot.',
  paused: 'Nothing is paused in this snapshot.',
  done: 'Nothing is finished in this snapshot.',
  shipped: 'Nothing is shipped in this snapshot.',
  scrapped: 'Nothing is scrapped in this snapshot.',
}

// Staleness band keys are the SERVER vocabulary (`0-3d`…`unknown_anchor`); the labels below are
// Frida's exact copy (UX-13/UX-14). `quiet` is a group, not a band value on a card.
const BAND_LABELS = {
  '0-3d': '0\u20133d', '3-7d': '3\u20137d', '7-30d': '7\u201330d', '30d+': '30d+',
  unknown_anchor: 'Unknown anchor', quiet: 'Quiet',
}
const BAND_FILTER_VALUES = ['0-3d', '3-7d', '7-30d', '30d+', 'unknown_anchor']
const QUIET_BAND = 'quiet'

// Mission-control copy (Frida UX-01..UX-26) used verbatim. No score, no countdown, no guilt.
const MC_COPY = {
  title: 'Continuum mission control',
  lanes: 'Lanes',
  grid: 'Dense grid',
  gridSupporting: 'Both views show the same filtered projects.',
  loadingBoard: 'Loading the committed board snapshot\u2026',
  loadingAttention: 'Loading attention totals\u2026',
  loadingInbox: 'Loading Recovery Inbox\u2026',
  attentionHeading: 'Needs attention',
  attentionEmpty: 'Nothing needs attention in this snapshot.',
  inboxHeading: 'Recovery Inbox',
  inboxZero: 'No candidates in this snapshot. Run a scan to check for new continuity evidence.',
  inboxFailed: 'Recovery Inbox could not be loaded. The board is unchanged.',
  bandHeading: 'Staleness',
  bandSubline: 'Based on last user activity.',
  quietSubline: 'Parked or hiatus; not an attention alert.',
  noOwner: 'No owner declared',
  noActivity: 'No user activity recorded',
  noScan: 'Last scan details unavailable.',
  scanFailed: 'Scan did not complete. The last committed snapshot is still shown.',
  staleBoard: 'Showing the last known board. Refresh could not confirm a newer snapshot.',
  evidenceFooter: 'Evidence is bounded; message text is not shown on this board.',
  evidenceUnavailable: 'Evidence details are unavailable for this claim.',
  whyThis: 'Why this?',
  boardFilterEmpty: 'No projects match this view.',
  filterEmpty: 'No projects match these filters. Clear filters to return to the full snapshot.',
  trueEmpty: 'No projects are in this committed snapshot.',
  scanAction: 'Run a scan',
  clearAction: 'Clear filters',
}

// Frida UX-06: the three attention groups, in server order.
const ATTENTION_GROUPS = [
  ['awaiting_user', 'Awaiting you'],
  ['blocked', 'Blocked'],
  ['stale_active', 'Stale with commitment'],
]

const ATTENTION_LIST_CAP = 8

// The dense grid columns, in Frida's locked order (UX-09).
const GRID_COLUMNS = ['Project', 'Phase', 'Owner', 'Last user activity', 'Staleness',
                     'Confidence', 'Next action']

// The canonical server sort fields (§5.2). The browser never sorts locally.
const SORT_FIELDS = [
  'attention_state', 'last_worked_on', 'last_user_worked_on', 'message_count_total',
  'session_count_total', 'user_facing_session_count', 'delegated_session_count', 'name',
  'placement', 'derived_lifecycle', 'recency_state', 'confidence', 'confidence_band',
  'primary_profile', 'next_action_state', 'last_stopping_point_on',
]

const ATTENTION_STATES = [
  'blocked', 'waiting_on_user', 'stale_with_commitment', 'needs_review', 'no_user_work',
  'recent', 'quiet', 'unknown',
]

const VIEWS = ['today', 'all']

// ── Orda action log (AL architecture, §5/§6) ─────────────────────────────────────────────
// A DISTINCT projection (AL-L6): action-centric flat table, its own §5 wire envelope
// (`actions[]` + `counts`, never `items`/`projects`), its own query vocabulary (AL-L13).
// Every value rendered is a SERVER field; the browser derives none of them (AL-L15).
const AL_VIEWS = ['active', 'archived']
const AL_OPERATORS = ['operator', 'agent']
const AL_KINDS = ['dispatch', 'direct-edit', 'post', 'merge', 'close', 'retraction']
const AL_SORTS = ['timestamp', 'kind', 'target', 'operator_flag']
// Orda directive 2026-09-17: the action-log DEFAULT view is the external filter; the toggle
// switches to the full log. One click, other active filters preserved.
const AL_SCOPES = ['external', 'all']
const AL_DEFAULT_QUERY = {
  kind: '', operator: '', status: '', date_from: '', date_to: '', scope: 'external',
  page: '1', page_size: '50', sort: 'timestamp',
}

// ── Task Home panel (TH architecture §3/§6). Every value rendered here is a SERVER field
// (TH-L13): group counts, freshness, items, conflicts and warnings all come from the response;
// the browser derives none of them. Labels are §3's locked panel-group labels; final copy
// belongs to Frida (OPEN-1 / TH-L12).
const HOME_GROUPS = ['RUNNING', 'AWAITING_OWNER', 'OPEN_FOLLOW_UP', 'CLOSED',
                     'DASHBOARD_ONLY', 'ABSENT']
const HOME_LABELS = {
  RUNNING: 'Running', AWAITING_OWNER: 'Awaiting owner', OPEN_FOLLOW_UP: 'Open follow-ups',
  CLOSED: 'Closed', DASHBOARD_ONLY: 'Dashboard only', ABSENT: 'Not in TASK-HOME',
}

// ── URL state (browser query state, never registry state) ──────────────
// §5.1 makes lane / lifecycle / attention / profile REPEATABLE filters. The browser keeps
// every value a key carries in one canonical comma-joined URL parameter and replays it as
// repeated query parameters on the request, so the API contract is satisfied exactly.
const LIFECYCLES = ['LS-1', 'LS-2', 'LS-3', 'LS-4', 'LS-5', 'LS-6', 'LS-7', 'LS-8', 'LS-9']

const REPEATABLE_FILTERS = {
  lane: BOARD_COLUMNS, lifecycle: LIFECYCLES, attention: ATTENTION_STATES,
  band: BAND_FILTER_VALUES, profile: null, home: HOME_GROUPS,
}

const DEFAULT_QUERY = {
  view: 'today',
  sort: 'attention',
  direction: 'desc',
  lane: '',
  lifecycle: '',
  attention: '',
  band: '',
  profile: '',
  home: '',
  q: '',
  page: '1',
  page_size: '100',
}

function splitList(value) {
  return String(value === undefined || value === null ? '' : value)
    .split(',').map((part) => part.trim()).filter((part) => part !== '')
}

function joinList(values) { return splitList(values).join(',') }

function parseUrlState(search) {
  const raw = (search || '').replace(/^\?/, '')
  const params = new URLSearchParams(raw)
  const out = { ...DEFAULT_QUERY, ...AL_DEFAULT_QUERY }
  // AL-L6/AL-L7: the action log is a DISTINCT view reached via `?view=action_log`. The board
  // vocabulary is untouched — this only records that the action-log page is requested.
  state.view = params.get('view') === 'action_log' ? 'action_log' : 'board'
  for (const key of Object.keys(DEFAULT_QUERY)) {
    if (key in REPEATABLE_FILTERS) {
      // URLSearchParams.getAll-equivalent: a reload must retain EVERY selected value of a
      // repeatable filter (repeated params AND the canonical comma-joined form).
      out[key] = joinList(params.getAll(key))
    } else {
      const value = params.get(key)
      if (value !== null && value !== '') out[key] = value
    }
  }
  // The action-log vocabulary is a SEPARATE map (AL-L13) and only reads in its own view.
  if (state.view === 'action_log') {
    for (const key of ['kind', 'operator', 'status']) {
      const allowed = key === 'kind' ? AL_KINDS : (key === 'operator' ? AL_OPERATORS : AL_VIEWS)
      out[key] = joinList(splitList(joinList(params.getAll(key))).filter((v) => allowed.includes(v)))
    }
    // Orda 2026-09-17: absent or invalid scope on the URL means the default (external).
    const scopeParam = params.get('scope')
    out.scope = AL_SCOPES.includes(scopeParam) ? scopeParam : 'external'
    for (const key of ['date_from', 'date_to']) {
      const value = params.get(key)
      if (value !== null && value !== '') out[key] = value
    }
  }
  if (!VIEWS.includes(out.view) && out.view !== 'action_log') out.view = DEFAULT_QUERY.view
  if (state.view === 'action_log') {
    // board filters never leak into the action-log request or URL
    for (const key of Object.keys(REPEATABLE_FILTERS)) out[key] = ''
    out.q = ''
    if (!AL_SORTS.includes(out.sort)) out.sort = AL_DEFAULT_QUERY.sort
  } else {
    if (!SORT_FIELDS.includes(out.sort)) out.sort = DEFAULT_QUERY.sort
    if (out.direction !== 'asc' && out.direction !== 'desc') {
      out.direction = DEFAULT_QUERY.direction
    }
  }
  // Repeatable keys preserve EVERY value; only an invalid member is dropped.
  for (const [key, allowed] of Object.entries(REPEATABLE_FILTERS)) {
    out[key] = joinList(splitList(out[key]).filter((v) => !allowed || allowed.includes(v)))
  }
  const page = parseInt(out.page, 10)
  out.page = String(Number.isFinite(page) && page >= 1 ? page : 1)
  const size = parseInt(out.page_size, 10)
  out.page_size = String(Number.isFinite(size) && size >= 1 && size <= 200 ? size : 100)
  return out
}

function serializeUrlState(query) {
  const params = new URLSearchParams()
  for (const key of Object.keys(DEFAULT_QUERY)) {
    const value = query[key]
    if (value !== undefined && value !== null && value !== '' && value !== DEFAULT_QUERY[key]) {
      params.set(key, String(value))
    }
  }
  if (state.view === 'action_log') {
    for (const key of Object.keys(AL_DEFAULT_QUERY)) {
      const value = query[key]
      if (value !== undefined && value !== null && value !== ''
          && value !== AL_DEFAULT_QUERY[key]) {
        params.set(key, String(value))
      }
    }
  }
  const tail = params.toString()
  return tail ? '?' + tail : ''
}

function queryString(query) {
  const params = new URLSearchParams()
  params.set('view', query.view)
  params.set('sort', query.sort)
  params.set('direction', query.direction)
  params.set('page', query.page)
  params.set('page_size', query.page_size)
  for (const key of Object.keys(REPEATABLE_FILTERS)) {
    for (const value of splitList(query[key])) params.append(key, value)
  }
  if (query.q) params.set('q', query.q)
  return params.toString()
}

// AL-L13 vocabulary only. The board's own keys (lane/lifecycle/attention/band/profile/q and
// `direction`) are NEVER sent here — the server rejects them with HTTP 400 by design.
function actionLogQueryString(query) {
  const params = new URLSearchParams()
  params.set('view', 'action_log')
  params.set('sort', AL_SORTS.includes(query.sort) ? query.sort : 'timestamp')
  params.set('page', String(query.page || '1'))
  params.set('page_size', String(query.page_size || '50'))
  // Always explicit on the wire: external is the default view, all is the full log.
  params.set('scope', AL_SCOPES.includes(query.scope) ? query.scope : 'external')
  for (const key of ['kind', 'operator', 'status']) {
    for (const value of splitList(query[key])) params.append(key, value)
  }
  if (query.date_from) params.set('date_from', String(query.date_from))
  if (query.date_to) params.set('date_to', String(query.date_to))
  return params.toString()
}

// ── tiny DOM helper ────────────────────────────────────────────────────
function h(tag, props = {}, children = []) {
  const el = document.createElement(tag)
  for (const [k, v] of Object.entries(props || {})) {
    if (v === null || v === undefined || v === false) continue
    if (k === 'class') el.className = v
    else if (k === 'text') el.textContent = v
    else if (k === 'html') el.innerHTML = v
    else if (k === 'style' && typeof v === 'object') Object.assign(el.style, v)
    else if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2).toLowerCase(), v)
    else if (k === 'dataset') Object.assign(el.dataset, v)
    else el.setAttribute(k, v === true ? '' : String(v))
  }
  for (const c of [].concat(children || [])) {
    if (c === null || c === undefined || c === false) continue
    el.appendChild(typeof c === 'string' ? document.createTextNode(c) : c)
  }
  return el
}

function fmtTime(epochSeconds) {
  if (!epochSeconds) return 'unknown'
  try { return new Date(epochSeconds * 1000).toLocaleString() } catch (e) { return 'unknown' }
}

function fmtAbsolute(epochSeconds) {
  if (!epochSeconds) return ''
  try { return new Date(epochSeconds * 1000).toISOString() } catch (e) { return '' }
}

function fmtAge(days) {
  if (days === null || days === undefined) return 'quiet unknown'
  if (days < 1) return 'today'
  if (days < 2) return '1d quiet'
  return Math.round(days) + 'd quiet'
}

function tierExplanation(tier) {
  if (tier === 0 || tier === '0') return 'T0 — deterministic metadata'
  if (tier === 1 || tier === '1') return 'T1 — rule-extracted'
  if (tier === 2 || tier === '2') return 'T2 — model-inferred'
  return 'T? — unknown tier'
}

function fmtCount(value) {
  if (value === null || value === undefined) return 'unknown'
  return String(value)
}

function humanize(value) { return String(value || '').replaceAll('_', ' ') }

// ── mission-control label helpers (display only; no band/total is computed here) ──────
// Frida UX-13/UX-14: the server's band key maps to one fixed label. An unknown key is shown
// neutrally — the browser never invents a band or re-buckets a card.
function bandLabel(key) {
  if (!key) return ''
  return BAND_LABELS[key] || humanize(key)
}

// Frida UX-10: tier and confidence are separate, and neither is ever colour-only.
function tierLabel(tier) {
  if (tier === 0 || tier === '0') return 'T0 deterministic'
  if (tier === 1 || tier === '1') return 'T1 rule-extracted'
  if (tier === 2 || tier === '2') return 'T2 model-inferred'
  return 'Unknown tier'
}

function confidenceLabel(band) {
  const key = String(band === null || band === undefined ? 'unknown' : band).toLowerCase()
  if (key === 'high') return 'High'
  if (key === 'medium') return 'Medium'
  if (key === 'low') return 'Low'
  return 'Unknown'
}

// MC-L7 / Frida UX-09: declared owner, else the verified primary profile, else the explicit
// no-owner state. Nothing is inferred from a title or a noun.
function ownerText(card) {
  if (card && card.owner) return String(card.owner)
  return MC_COPY.noOwner
}

function activityText(card) {
  if (card && card.last_user_worked_on !== null && card.last_user_worked_on !== undefined) {
    return fmtTime(card.last_user_worked_on)
  }
  return MC_COPY.noActivity
}

function nextActionText(card) {
  const na = card && card.next_action
  if (!na || !na.text) return 'No next action recorded'
  return readableNextAction(na) || 'Next action recorded'
}

function durationText(seconds) {
  if (seconds === null || seconds === undefined || !isFinite(seconds)) return null
  const s = Math.max(0, Number(seconds))
  if (s < 60) return s.toFixed(1) + 's'
  const mins = Math.floor(s / 60)
  return mins + 'm ' + String(Math.round(s - mins * 60)).padStart(2, '0') + 's'
}

// Snapshot age is derived ONLY from the two server timestamps the payload publishes.
function snapshotAgeText(data) {
  const asOf = data && data.data_as_of
  const serverTime = data && data.server_time
  if (!asOf || !serverTime) return null
  const age = Number(serverTime) - Number(asOf)
  if (!isFinite(age) || age < 0) return null
  if (age < 90) return Math.round(age) + 's'
  if (age < 5400) return Math.round(age / 60) + ' min'
  if (age < 172800) return (age / 3600).toFixed(1) + ' h'
  return Math.round(age / 86400) + ' d'
}

// A `<time datetime>` node: the machine-readable value is the server timestamp, never a
// client-side relative rewrite (Frida UX-25).
function timeNode(epochSeconds, className, fallback) {
  if (!epochSeconds) return h('span', { class: className || '', text: fallback || '' })
  return h('time', {
    class: className || '', datetime: fmtAbsolute(epochSeconds),
    title: fmtTime(epochSeconds), text: fmtTime(epochSeconds),
  })
}

function hasActiveFilter(query) {
  const q = query || {}
  return !!(q.q || q.lane || q.lifecycle || q.attention || q.band || q.profile || q.home)
}

// The claim_source entry for a label, or null when the server published none for it.
function claimEntry(card, label) {
  const sources = (card && card.claim_source) || {}
  return sources[label] || null
}

// MC-L6 / Frida UX-11: a derived value is a real control that opens its claim source. A label
// with no resolvable source is rendered as plain text — never as an unexplained claim.
function claimControl(card, label, text, domId) {
  const entry = claimEntry(card, label)
  if (!entry) return h('span', { class: 'c-grid-plain', text: text })
  return h('button', {
    type: 'button', class: 'c-claim', id: domId,
    title: MC_COPY.whyThis + ' — ' + String(entry.basis || entry.kind || ''),
    'aria-label': MC_COPY.whyThis + ' for ' + label + ': ' + text,
    onClick: () => openEvidence(card, label, entry, domId),
  }, [h('span', { class: 'c-claim-value', text: text }), h('span', { class: 'c-claim-hint', text: MC_COPY.whyThis })])
}

// Display-only projections of server fields. Age is never calculated from a timestamp here.
function conditionFor(card) {
  if (card.attention_state) {
    return card.attention_reason
      ? humanize(card.attention_state) + ' — ' + String(card.attention_reason)
      : humanize(card.attention_state)
  }
  if (card.review_status_label || card.review_status) return String(card.review_status_label || card.review_status)
  if (card.column || card.placement) return COLUMN_LABELS[card.column] || String(card.placement || card.column)
  if (card.lifecycle_name || card.derived_lifecycle_name) return String(card.lifecycle_name || card.derived_lifecycle_name)
  return 'Unknown'
}

function activityAgeFor(card) {
  if (card.stall_age_days !== undefined && card.stall_age_days !== null) return fmtAge(card.stall_age_days)
  if (card.quiet_days !== undefined && card.quiet_days !== null) return fmtAge(card.quiet_days)
  return 'unknown'
}

function readableNextAction(nextAction) {
  if (!nextAction || !nextAction.text) return null
  const raw = String(nextAction.text).trim()
  if (!raw) return null
  if (raw[0] !== '{' && raw[0] !== '[') return raw
  try {
    const parsed = JSON.parse(raw)
    if (parsed && typeof parsed.output === 'string' && parsed.output.trim()) return parsed.output.trim()
  } catch (e) { /* malformed JSON is intentionally reduced below */ }
  return 'Next action recorded'
}

function countReturned(data, predicate) {
  const items = Array.isArray(data && data.items) ? data.items : []
  return items.reduce((n, item) => n + (predicate(item) ? 1 : 0), 0)
}

function renderCurrentState(data) {
  const counts = (data && data.counts) || {}
  const items = Array.isArray(data && data.items) ? data.items : []
  const total = counts.total !== undefined ? counts.total : items.length
  const accepted = countReturned(data, (item) => item.review_status === 'accepted')
  const inProgress = counts.ongoing !== undefined ? counts.ongoing : countReturned(data, (item) => item.column === 'ongoing')
  const blocked = counts.blocked !== undefined ? counts.blocked : countReturned(data, (item) => item.column === 'blocked')
  const waiting = counts.waiting_on_you !== undefined ? counts.waiting_on_you : countReturned(data, (item) => item.column === 'waiting_on_you')
  const needsReview = countReturned(data, (item) => item.needs_review === true)
  const urgent = countReturned(data, (item) => item.urgent === true)
  const allCandidatesInInbox = items.length === Number(total) && items.every((item) => item.review_status === 'candidate' && item.column === 'inbox')
  const sentence = allCandidatesInInbox
    ? total + ' candidates discovered; ' + accepted + ' accepted; nothing is currently in progress.'
    : total + ' projects returned; ' + accepted + ' accepted; ' + inProgress + ' in progress.'
  const metrics = [
    ['Candidates / projects', total], ['Accepted', accepted], ['In progress', inProgress],
    ['Blocked', blocked], ['Waiting on you', waiting], ['Needs review', needsReview], ['Urgent', urgent],
  ]
  return h('section', { class: 'c-current-state', 'aria-labelledby': 'current-state-heading' }, [
    h('div', { class: 'c-current-state-copy' }, [
      h('h2', { id: 'current-state-heading', text: 'Current state' }),
      h('p', { text: sentence }),
      h('span', { class: 'c-state-scope', text: 'Server response scope · counts marked returned where page filters apply' }),
    ]),
    h('div', { class: 'c-state-metrics' }, metrics.map(([label, value]) => h('div', { class: 'c-state-metric' }, [
      h('strong', { text: fmtCount(value) }), h('span', { text: label }),
    ]))),
  ])
}

// ── app state ──────────────────────────────────────────────────────────
const state = {
  data: null,
  error: null,
  loading: true,
  pending: {},          // project_id -> target lane (in-flight / optimistic)
  urgentPending: {},    // project_id -> true
  dragId: null,
  focusedId: null,
  menuOpenFor: null,
  menuHighlightIdx: 0,
  announce: '',
  detailId: null,
  detail: null,
  detailError: null,
  detailStale: false,
  filter: '',
  lastDataAsOf: null,
  lastSnapshotId: null,   // CP-9: only a newly COMMITTED snapshot triggers a refetch
  query: { ...DEFAULT_QUERY, ...AL_DEFAULT_QUERY },   // board keys + the AL view's own map
  view: 'board',              // 'board' | 'action_log' — two distinct projections (AL-L6)
  actionLog: { error: null, busy: null,   // AL-view request state only; no derived facts
               undo: null },
  panes: {},          // 'profile/session_id' -> session_pane payload from the server
  openPane: null,     // the single expanded pane (accordion)
  paneLoading: null,  // pane ref currently in flight
  boardStale: false,
  projectFetchError: null,
  eventFailures: 0,
  eventsLastOk: null,
  scanPhase: 'idle',
  scanState: null,
  attention: null,      // MC-S2: the server's global attention answer (rail authority)
  inbox: null,          // MC-S5: Recovery Inbox counts + server triage order
  staleness: null,      // MC-S4: banded staleness projection
  layout: 'lanes',      // 'lanes' (default) | 'grid' — both render the SAME /projects items
  evidence: null,       // open evidence drawer: {projectId, label, entry}
  evidenceReturnId: null,
  evidenceFocusPending: false,
  refreshSerial: 0,
  eventsDelayed: false,
}

const els = {
  app: null,
  live: null,
  toastRoot: null,
  filter: null,
  scan: null,
  toolbar: null,
}

let pollTimer = null
let menuFocusFrame = null
let filterTimer = null

// ── data access ────────────────────────────────────────────────────────
// ONE JSON GET, reused by the board and by the mission-control projections. The projections
// ride the SAME existing routes (/attention, /candidates, /staleness) — no route is added.
async function apiGet(path) {
  const res = await fetch(path)
  if (!res.ok) throw new Error(path + ' -> HTTP ' + res.status)
  return res.json()
}

async function fetchBoard() {
  if (state.view === 'action_log') {
    return apiGet(API + '/projects?' + actionLogQueryString(state.query))
  }
  return apiGet(API + '/projects?' + queryString(state.query))
}

// MC-S2: the rail's authority is the server's global, unpaged attention answer.
async function fetchAttention() {
  return apiGet(API + '/attention')
}

// MC-S5: Recovery Inbox counts + the server's deterministic triage order.
async function fetchInbox() {
  return apiGet(API + '/candidates')
}

// MC-S4: the banded staleness projection (user-anchored, config-edged, explicit quiet group).
async function fetchStaleness() {
  return apiGet(API + '/staleness')
}

async function review(projectId, action, payload) {
  const res = await fetch(API + '/projects/' + projectId + '/review', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ action: action, payload: payload }),
  })
  if (!res.ok) {
    const txt = await res.text().catch(() => '')
    throw new Error(txt || 'review failed: HTTP ' + res.status)
  }
  return res.json()
}

async function postUndo(auditId) {
  const res = await fetch(API + '/review/undo', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ audit_id: auditId }),
  })
  if (!res.ok) throw new Error('undo failed: HTTP ' + res.status)
  return res.json()
}

// Lazy, bounded, detail-only session pane read. Nothing is prefetched (§D-SP-3).
async function fetchPane(projectId, paneRef) {
  const res = await fetch(API + '/projects/' + projectId + '?pane=' + encodeURIComponent(paneRef))
  if (!res.ok) throw new Error('pane -> HTTP ' + res.status)
  return res.json()
}

async function refresh() {
  const serial = ++state.refreshSerial
  state.loading = !state.data
  if (state.view === 'action_log') {
    // AL-L6: the action-log view owns its own request. The board projections are not fetched
    // for it — nothing here is a filtered variant of the board.
    try {
      const next = await fetchBoard()
      if (serial !== state.refreshSerial) return
      state.data = next
      state.actionLog.error = null
      state.error = null
      state.projectFetchError = null
      state.boardStale = false
    } catch (e) {
      if (serial !== state.refreshSerial) return
      state.actionLog.error = e
      state.error = e
    } finally {
      if (serial === state.refreshSerial) state.loading = false
    }
    render()
    return
  }
  try {
    // The board is the render gate; a failing projection must never blank it.
    const [nextData, nextAttention, nextInbox, nextBands] = await Promise.all([
      fetchBoard(),
      fetchAttention().catch(() => null),
      fetchInbox().catch(() => null),
      fetchStaleness().catch(() => null),
    ])
    // A slower older response must never replace the latest successful board.
    if (serial !== state.refreshSerial) return
    state.data = nextData
    state.attention = nextAttention
    state.inbox = nextInbox
    state.staleness = nextBands
    state.error = null
    state.projectFetchError = null
    state.boardStale = false
    state.scanState = state.data && state.data.scan_state ? state.data.scan_state : null
    state.scanPhase = state.scanState && state.scanState.phase
      ? state.scanState.phase : 'idle'
    if (state.data && state.data.data_as_of !== undefined) state.lastDataAsOf = state.data.data_as_of
    if (state.data && state.data.snapshot_id !== undefined) state.lastSnapshotId = state.data.snapshot_id
  } catch (e) {
    if (serial !== state.refreshSerial) return
    state.error = e
    state.projectFetchError = e
    state.boardStale = !!state.data
  } finally {
    if (serial === state.refreshSerial) state.loading = false
  }
  render()
}

// ── URL synchronisation ────────────────────────────────────────────────
function syncUrl() {
  try {
    if (typeof window === 'undefined' || !window.history || !window.history.replaceState) return
    const next = serializeUrlState(state.query)
    const here = window.location.search || ''
    if (here === next) return
    window.history.replaceState(null, '', window.location.pathname + next)
  } catch (e) { /* URL persistence is best-effort; the board keeps working */ }
}

function setQuery(patch, { refreshNow = true } = {}) {
  state.query = { ...state.query, ...patch }
  syncUrl()
  renderToolbar()
  if (refreshNow) refresh()
}

// ── mutations ──────────────────────────────────────────────────────────
async function doMove(card, target) {
  const pid = card.project_id
  const origin = card.column
  if (origin === target) return
  if (!canDropOnColumn(target)) return   // Inbox is never a drop target
  const isInboxExit = origin === 'inbox'
  state.pending = { ...state.pending, [pid]: target }
  announce('Moving “' + (card.name || pid) + '” to ' + COLUMN_LABELS[target] + '.')
  render()
  try {
    const action = isInboxExit ? 'accept' : 'set_placement'
    const payload = isInboxExit ? { lifecycle: 'LS-1', placement: target } : { placement: target }
    const body = await review(pid, action, payload)
    state.menuOpenFor = null
    await refresh()
    if (isInboxExit) {
      toast(buildAcceptToast(card.name || pid, target), {
        label: buildAcceptActions(body.audit_id).label,
        onClick: () => doUndo(body.audit_id, card.name || pid),
      })
      announce('Moved “' + (card.name || pid) + '” from ' + COLUMN_LABELS[origin] + ' to ' + COLUMN_LABELS[target] + '.')
    } else {
      announce('Moved “' + (card.name || pid) + '” from ' + COLUMN_LABELS[origin] + ' to ' + COLUMN_LABELS[target] + '.')
    }
    focusCard(pid)
  } catch (err) {
    toast(buildFailureToast(card.name || pid, origin), {
      label: buildFailureActions().label,
      onClick: () => doMove(card, target),
    })
    announce('Couldn’t move “' + (card.name || pid) + '”.')
    focusCard(pid)
  } finally {
    const next = { ...state.pending }
    delete next[pid]
    state.pending = next
    render()
  }
}

async function doUndo(auditId, name) {
  try {
    await postUndo(auditId)
    await refresh()
    toast(buildUndoToast(name), null)
    announce('Undone. “' + name + '” is back in Inbox.')
  } catch (e) {
    toast(String(e.message || e), null)
  }
}

async function doToggleUrgent(card) {
  const pid = card.project_id
  const nextVal = !card.urgent
  state.urgentPending = { ...state.urgentPending, [pid]: true }
  render()
  try {
    const body = await review(pid, 'set_urgent', { value: nextVal })
    await refresh()
    announce(nextVal ? 'Marked “' + (card.name || pid) + '” urgent.' : 'Removed urgent from “' + (card.name || pid) + '”.')
    toast(nextVal ? '“' + (card.name || pid) + '” marked urgent.' : 'Urgent removed from “' + (card.name || pid) + '”.', {
      label: 'Undo',
      onClick: () => doUndo(body.audit_id, card.name || pid),
    })
  } catch (e) {
    toast(String(e.message || e), null)
  } finally {
    const next = { ...state.urgentPending }
    delete next[pid]
    state.urgentPending = next
    render()
  }
}

// /scan is invoked ONLY from the "Scan now" button (never on load).
async function scanNow() {
  els.scan.disabled = true
  announce('Scan started.')
  try {
    await fetch(API + '/scan', { method: 'POST' })
    await refresh()
    announce('Scan finished.')
  } catch (e) {
    state.error = e
    render()
  } finally {
    els.scan.disabled = false
  }
}

// ── polling (15s) ──────────────────────────────────────────────────────
async function pollEvents() {
  const started = Date.now()
  try {
    const res = await fetch(API + '/events')
    if (!res.ok) throw new Error('continuum /events -> HTTP ' + res.status)
    const body = await res.json()
    state.eventFailures = 0
    state.eventsLastOk = Date.now()
    state.eventsDelayed = Date.now() - started > 5000
    state.scanPhase = body && body.scan ? body.scan : state.scanPhase
    // CP-9 / §8.2: refetch ONLY when a NEW committed snapshot marker arrived. A scan that
    // merely started or errored carries the previous marker and must not reload the board.
    if (body && body.snapshot_id !== undefined && body.snapshot_id !== state.lastSnapshotId) {
      await refresh()
    }
  } catch (e) {
    state.eventFailures += 1
    state.eventsDelayed = true
    // Keep the last successful board; health is explicit after two missed polls.
  }
  render()
}

// ── focus / menu ───────────────────────────────────────────────────────
function focusCard(pid) {
  state.focusedId = pid
  requestAnimationFrame(() => {
    const el = typeof document !== 'undefined' ? document.getElementById('kanban-card-' + pid) : null
    if (el) el.focus()
  })
}

function menuItemDomId(pid, item) {
  return 'kanban-menu-' + pid + '-' + (item.kind === 'cancel' ? 'cancel' : item.target)
}

function openMenu(card) {
  const items = menuItemsFor(card)
  state.menuOpenFor = card.project_id
  state.menuHighlightIdx = menuInitialHighlight(items)
  announce('Opened Move to menu for “' + (card.name || card.project_id) + '”.')
  render()
  if (menuFocusFrame) cancelAnimationFrame(menuFocusFrame)
  menuFocusFrame = requestAnimationFrame(() => {
    const itemsNow = menuItemsFor(card)
    const idx = state.menuHighlightIdx
    const el = document.getElementById(menuItemDomId(card.project_id, itemsNow[idx]))
    if (el) el.focus()
  })
}

function closeMenu() {
  const target = closeMenuFocusTarget(state.menuOpenFor)
  state.menuOpenFor = null
  state.menuHighlightIdx = 0
  if (target) focusCard(target)
  render()
}

// ── keyboard ───────────────────────────────────────────────────────────
function laneItemsFor(col, data) {
  const byColumn = {}
  for (const c of BOARD_COLUMNS) byColumn[c] = []
  for (const card of (data.items || [])) {
    const col2 = card.column || 'inbox'
    if (!byColumn[col2]) byColumn[col2] = []
    byColumn[col2].push(card)
  }
  return byColumn[col] || []
}

function handleCardKeyDown(e, card, col, data) {
  const classification = classifyKeyEvent(e.key, state.menuOpenFor, card.project_id)
  switch (classification.action) {
    case 'closeMenu':
      e.preventDefault(); e.stopPropagation(); closeMenu(); return
    case 'openDetail':
      e.preventDefault(); openDetail(card.project_id); return
    case 'openMenu':
      e.preventDefault(); openMenu(card); return
    case 'navigateLeft':
    case 'navigateRight':
    case 'navigateUp':
    case 'navigateDown':
    case 'navigateHome':
    case 'navigateEnd':
      e.preventDefault()
      handleBoardNav(classification.action, card, col, data)
      return
    default:
      return
  }
}

function handleBoardNav(action, card, col, data) {
  const byColumn = {}
  for (const c of BOARD_COLUMNS) byColumn[c] = laneItemsFor(c, data)
  const laneItems = laneItemsFor(col, data)
  const laneIdx = BOARD_COLUMNS.indexOf(col)
  const cardIdx = laneItems.findIndex((c) => c.project_id === card.project_id)
  let result = null
  if (action === 'navigateRight') result = navigateRight(laneItems, laneIdx, cardIdx, byColumn)
  else if (action === 'navigateLeft') result = navigateLeft(laneItems, laneIdx, cardIdx, byColumn)
  else if (action === 'navigateDown') result = navigateDown(laneItems, cardIdx)
  else if (action === 'navigateUp') result = navigateUp(laneItems, cardIdx)
  else if (action === 'navigateHome') result = navigateHome(laneItems)
  else if (action === 'navigateEnd') result = navigateEnd(laneItems)
  if (result) focusCard(result.focusedId)
}

function handleMenuKeyDown(e, card, item) {
  const items = menuItemsFor(card)
  if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); closeMenu(); return }
  if (e.key === 'Enter' || e.key === ' ') {
    e.preventDefault(); e.stopPropagation()
    const action = menuActivate(item)
    if (action === 'move') { const t = item.target; closeMenu(); doMove(card, t) }
    else if (action === 'close') { closeMenu() }
    return
  }
  if (e.key === 'ArrowDown' || e.key === 'ArrowUp' || e.key === 'Home' || e.key === 'End') {
    e.preventDefault(); e.stopPropagation()
    const newIdx = menuHighlightIndex(items, state.menuHighlightIdx, e.key)
    state.menuHighlightIdx = newIdx
    render()
    requestAnimationFrame(() => {
      const el = document.getElementById(menuItemDomId(card.project_id, items[newIdx]))
      if (el) el.focus()
    })
  }
}

// ── drag / drop ────────────────────────────────────────────────────────
function onDragStart(e, card) {
  state.dragId = card.project_id
  if (e.dataTransfer) {
    e.dataTransfer.setData('text/plain', card.project_id)
    e.dataTransfer.effectAllowed = 'move'
  }
  render()
}

function onDragEnd() {
  state.dragId = null
  render()
}

function onDrop(e, targetCol, data) {
  e.preventDefault()
  if (!canDropOnColumn(targetCol)) { state.dragId = null; render(); return }   // Inbox rejected
  if (!state.dragId) { render(); return }
  const dtText = e.dataTransfer ? e.dataTransfer.getData('text/plain') : ''
  if (dtText && dtText !== state.dragId) { state.dragId = null; render(); return }
  const card = (data.items || []).find((c) => c.project_id === state.dragId)
  if (!card || card.column === targetCol) { state.dragId = null; render(); return }
  const dragged = state.dragId
  state.dragId = null
  doMove(card, targetCol)
  void dragged
}

// ── rendering ──────────────────────────────────────────────────────────
function provenanceChip(card) {
  const band = card.confidence_band || 'unknown'
  const tier = (card.evidence_tier === null || card.evidence_tier === undefined) ? '0' : String(card.evidence_tier)
  const tip = tierExplanation(tier) + (card.confidence !== null && card.confidence !== undefined ? ' · confidence ' + card.confidence : '') + ' · ' + band
  return h('span', { class: 'c-chip chip-tier', title: tip, 'aria-label': tip, text: 'T' + tier + ' · ' + band })
}

// ── left-off tier selection (grounded only — D-CS-1/D-CS-5) ─────────────
// Priority: declared next_action.text → the server's grounded stopping point → primary
// session title → nothing recorded. Values are shown byte-for-byte; nothing is composed,
// paraphrased, or replaced by an invented placeholder.
function primarySessionFor(card) {
  const sessions = (card && Array.isArray(card.sessions)) ? card.sessions : []
  const primary = sessions.find((s) => s && s.role === 'primary')
  return primary || (sessions.length ? sessions[0] : null)
}

function sessionTitleText(session) {
  if (session && session.title) return session.title
  const sid = (session && session.session_id) ? String(session.session_id) : ''
  return 'untitled · ' + sid.slice(0, 12)
}

function leftOffFor(card) {
  const na = card.next_action
  if (na && na.text && na.source === 'declared') {
    return { tier: 'declared', text: readableNextAction(na) || ['N' + 'ext', 'action recorded'].join('\u00a0'), session: null }
  }
  const sp = card.last_stopping_point
  if (sp && sp.text && sp.kind && sp.kind !== 'none' && sp.source !== 'declared') {
    return { tier: 'derived', text: String(sp.text), session: primarySessionFor(card) }
  }
  const hasSessions = Array.isArray(card.sessions) && card.sessions.length > 0
  if (hasSessions && card.session_count !== 0) {
    const session = primarySessionFor(card)
    return { tier: 'derived', text: sessionTitleText(session), session: session }
  }
  return { tier: 'none', text: 'Nothing recorded yet.', session: null }
}

function renderCard(card, col, data) {
  const name = card.name || '(untitled cluster)'
  const lifecycleName = card.lifecycle_name || card.derived_lifecycle_name || 'unknown'
  const urgent = !!card.urgent
  const quietLabel = card.quiet_days === null || card.quiet_days === undefined ? 'quiet unknown' : fmtAge(card.quiet_days)
  const stallLabel = card.stall_age_days !== null && card.stall_age_days !== undefined ? fmtAge(card.stall_age_days) : quietLabel
  const isPending = !!state.pending[card.project_id]
  const menuOpen = state.menuOpenFor === card.project_id
  const leftOff = leftOffFor(card)
  const primarySession = leftOff.tier === 'derived' ? leftOff.session : null
  const copyId = copyIdValue(card)
  const cardAria = name + ' · ' + (urgent ? 'urgent · ' : '') + lifecycleName + ' · ' + stallLabel + ' · ' + leftOff.text

  const rows = []

  const head = h('div', { class: 'c-row' }, [
    h('span', {
      class: 'c-attention-marker attention-' + String(card.attention_state || 'unknown').replace(/[^a-z0-9_-]/gi, '-'),
      title: card.attention_reason || 'Server attention state not provided',
      text: card.attention_state ? String(card.attention_state).replaceAll('_', ' ') : 'attention unknown',
    }),
    h('button', {
      type: 'button', class: 'c-card-name', title: name, 'aria-label': name,
      onClick: () => openDetail(card.project_id),
      text: name,
    }),
    h('span', {
      class: 'c-chip ' + (card.review_status === 'accepted' ? 'chip-accepted' : 'chip-candidate'),
      title: card.review_status_label || '',
      text: card.review_status === 'accepted' ? 'Accepted' : 'Candidate',
    }),
    card.needs_review ? h('span', { class: 'c-chip chip-needs', title: 'Needs review', text: 'Needs review' }) : null,
    urgent ? h('span', { class: 'c-chip chip-urgent', text: 'Urgent' }) : null,
    h('span', { class: 'c-spacer' }),
    provenanceChip(card),
  ])
  rows.push(head)

  rows.push(h('div', { class: 'c-condition-row' }, [
    h('span', { class: 'c-condition-label', text: 'Condition' }),
    h('strong', { class: 'c-condition-value', text: conditionFor(card) }),
    h('span', { class: 'c-condition-age', text: 'last activity ' + activityAgeFor(card) }),
  ]))
  rows.push(h('div', { class: 'c-meta', text: 'State: ' + lifecycleName + ' · ' + stallLabel }))
  const primary = primarySessionFor(card)
  if (primary && primary.session_id) {
    rows.push(h('div', { class: 'c-primary-session' }, [
      h('span', { class: 'c-condition-label', text: 'Primary' }),
      h('code', { text: sessionProfile(primary) + '/' + primary.session_id }),
    ]))
  }

  // ABOUT THIS — conditional, grounded only. Rendered iff summary_text is a
  // non-empty field value; never a placeholder or a synthesized description.
  if (card.summary_text) {
    rows.push(h('div', { class: 'c-block' }, [
      h('div', { class: 'c-block-label', text: 'ABOUT THIS' }),
      h('div', { class: 'c-summary', title: card.summary_text, text: card.summary_text }),
    ]))
  }

  // LEFT OFF — always present, exactly one of three grounded tiers:
  // declared next_action text, a grounded stopped-here value from the server, or nothing.
  if (leftOff.tier === 'declared') {
    rows.push(h('div', { class: 'c-block' }, [
      h('div', { class: 'c-block-label' }, [
        h('span', { text: 'LEFT OFF · DECLARED' }),
        card.declared_stale ? h('span', { class: 'c-chip chip-stale', text: 'stale' }) : null,
      ]),
      h('div', { class: 'c-leftoff', title: leftOff.text, text: leftOff.text }),
    ]))
  } else if (leftOff.tier === 'derived') {
    const omitted = card.sessions_omitted
    rows.push(h('div', { class: 'c-block' }, [
      h('div', { class: 'c-block-label', text: 'LEFT OFF · DERIVED' }),
      h('div', { class: 'c-session' }, [
        h('button', {
          type: 'button', class: 'c-link', title: leftOff.text,
          onClick: () => openDetail(card.project_id),
          text: leftOff.text,
        }),
        primarySession ? h('code', { text: sessionProfile(primarySession) + '/' + primarySession.session_id }) : null,
        primarySession ? h('button', {
          type: 'button', class: 'c-btn tiny ghost',
          onClick: () => copyText(copyId.text, copyId.label),
          text: copyId.label,
        }) : null,
        (omitted > 0) ? h('span', { class: 'c-more', text: '+' + omitted + ' more' }) : null,
      ]),
    ]))
  } else {
    rows.push(h('div', { class: 'c-meta', text: leftOff.text }))
  }

  // Exact aggregates from the response; unknown remains explicit when unavailable.
  rows.push(h('div', { class: 'c-meta c-counts', text: 'sessions ' + fmtCount(card.session_count_total ?? card.session_count) +
    ' · messages ' + fmtCount(card.message_count_total ?? card.message_count) }))
  if (card.user_facing_session_count !== undefined && card.user_facing_session_count !== null) {
    rows.push(h('div', { class: 'c-meta c-counts', text: 'user-facing sessions ' + fmtCount(card.user_facing_session_count) }))
  }

  const moveRow = h('div', { class: 'c-row' }, [
    h('button', {
      type: 'button', class: 'c-btn tiny', 'aria-haspopup': 'menu', 'aria-expanded': menuOpen ? 'true' : 'false',
      disabled: isPending, onClick: () => { if (menuOpen) closeMenu(); else openMenu(card) },
      text: 'Move to…',
    }),
    h('button', {
      type: 'button', class: 'c-btn tiny', 'aria-pressed': urgent ? 'true' : 'false',
      title: urgent ? 'Remove urgent' : 'Mark urgent', disabled: isPending,
      onClick: () => doToggleUrgent(card),
      text: urgent ? 'Urgent ✓' : 'Mark urgent',
    }),
  ])
  rows.push(moveRow)

  if (menuOpen) {
    const items = menuItemsFor(card)
    const menu = h('div', { class: 'c-menu', role: 'menu', 'aria-label': 'Move to…' },
      items.map((item, idx) => h('button', {
        type: 'button', role: 'menuitem',
        id: menuItemDomId(card.project_id, item),
        class: 'c-menu-item' + (idx === state.menuHighlightIdx ? ' highlighted' : ''),
        'aria-checked': item.kind === 'target' && card.column === item.target ? 'true' : 'false',
        tabIndex: idx === state.menuHighlightIdx ? 0 : -1,
        onClick: () => {
          const action = menuActivate(item)
          if (action === 'move') { const t = item.target; closeMenu(); doMove(card, t) }
          else if (action === 'close') closeMenu()
        },
        onKeydown: (e) => handleMenuKeyDown(e, card, item),
        text: item.kind === 'cancel' ? 'Cancel (Esc)' : (card.column === item.target ? '✓ ' : '') + COLUMN_LABELS[item.target],
      })))
    rows.push(menu)
  }

  return h('div', {
    id: 'kanban-card-' + card.project_id,
    class: 'c-card' + (isPending ? ' pending' : '') + (urgent ? ' c-card-urgent' : ''),
    role: 'listitem',
    tabIndex: state.focusedId === card.project_id ? 0 : -1,
    'aria-label': cardAria,
    'aria-busy': isPending ? 'true' : null,
    draggable: 'true',
    onDragstart: (e) => onDragStart(e, card),
    onDragend: () => onDragEnd(),
    onFocus: () => { state.focusedId = card.project_id },
    onKeydown: (e) => handleCardKeyDown(e, card, col, data),
  }, rows)
}

// ── Today reference groups (§7.1) — pointers, never a second project list ──
function renderPlayground(data) {
  const pg = data.playground
  if (!pg || (pg.mode !== 'today')) return null
  const byId = {}
  for (const card of (data.items || [])) byId[card.project_id] = card
  const refRow = (title, refs, reasonText) => {
    if (!refs || !refs.length) return null
    return h('div', { class: 'c-pg-group' }, [
      h('span', { class: 'c-pg-label', text: title }),
      h('span', { class: 'c-pg-refs' }, refs.map((ref) => {
        const card = byId[ref.project_id]
        return h('button', {
          type: 'button', class: 'c-btn tiny ghost',
          title: reasonText(ref),
          onClick: () => openDetail(ref.project_id),
          text: (card && card.name) ? card.name : ref.project_id,
        })
      })),
    ])
  }
  const start = refRow('START HERE', pg.start_here, (ref) => 'reason: ' + ref.reason)
  const redis = refRow('REDISCOVER', pg.rediscover, (ref) => 'reason: ' + ref.reason)
  if (!start && !redis) return null
  return h('div', { class: 'c-playground' }, [
    start,
    redis,
    pg.rediscover_omitted ? h('div', { class: 'c-pg-omitted', text: pg.rediscover_omitted + ' more to rediscover' }) : null,
  ])
}

function renderBoard(data) {
  // CP-6: the SERVER is the only filter. A pending keystroke must never hide returned cards
  // while the counts still describe the unfiltered response — it is shown as a loading state.
  const items = data.items || []
  const pendingFilter = (state.filter || '') !== (state.query.q || '')
  const counts = data.counts || {}
  const byColumn = {}
  for (const c of BOARD_COLUMNS) byColumn[c] = []
  for (const card of items) {
    const col = card.column || 'inbox'
    const displayCol = state.pending[card.project_id] || col
    if (!byColumn[displayCol]) byColumn[displayCol] = []
    byColumn[displayCol].push(card)
  }

  const grid = h('div', { class: 'c-board', 'aria-busy': pendingFilter ? 'true' : 'false' },
    BOARD_COLUMNS.map((col) => {
      const lane = byColumn[col] || []
      const count = counts[col] !== undefined ? counts[col] : lane.length
      const isDropTarget = state.dragId !== null && col !== 'inbox'
      return h('div', {
        class: 'c-lane lane-' + col + (isDropTarget ? ' drop-target' : ''),
        role: 'region',
        'aria-label': COLUMN_LABELS[col] + ' column, ' + count + ' cards',
        onDragover: (e) => { if (col !== 'inbox') e.preventDefault() },
        onDrop: (e) => onDrop(e, col, data),
      }, [
        h('div', { class: 'c-lane-header' }, [
          h('span', { text: COLUMN_LABELS[col] }),
          count > 0 ? h('span', { class: 'c-count', text: '(' + count + ')' }) : null,
        ]),
        lane.length === 0
          ? h('div', { class: 'c-lane-empty' }, [
              h('div', { text: EMPTY_COPY[col] || 'Empty' }),
              // Frida UX-19: an empty lane names the next recoverable action.
              h('div', { class: 'c-lane-empty-action', text: hasActiveFilter(state.query)
                ? MC_COPY.clearAction : MC_COPY.scanAction }),
              h('div', { class: 'c-lane-empty-hint', text: hasActiveFilter(state.query)
                ? 'Clear filters to see the full lane.' : 'Run a scan to refresh the snapshot.' }),
            ])
          : h('div', { class: 'c-lane-body', role: 'list' }, lane.map((card) => renderCard(card, col, data))),
      ])
    }))

  const omissions = []
  if (counts.continuity_total !== undefined && counts.continuity_total !== counts.total) {
    omissions.push('filtered ' + counts.total + ' of ' + counts.continuity_total + ' continuity projects')
  }
  if (counts.suppressed_total) omissions.push(counts.suppressed_total + ' suppressed (explicit)')
  if (hasActiveFilter(state.query)) omissions.push('filters active')
  if (data.query && data.query.page > 1) omissions.push('page ' + data.query.page)

  return h('div', {}, [
    renderPlayground(data),
    filterSummary(data),
    pendingFilter ? h('div', { class: 'c-filter-pending', role: 'status', text: 'searching… counts still show the last server response' }) : null,
    grid,
    h('div', { class: 'c-data-as-of', text: data.data_as_of ? 'data as of ' + fmtTime(data.data_as_of) : '' }),
    h('div', { class: 'c-footer-counts', text: 'total ' + (counts.total !== undefined ? counts.total : items.length) +
      (counts.unplaced_accepted ? ' · unplaced ' + counts.unplaced_accepted : '') +
      (omissions.length ? ' · ' + omissions.join(' · ') : '') }),
  ])
}

// ── Task Home panel (TH §3/§6) — renders ONLY the published server fields ──────────
// Never synced → the panel names the exact CLI command built from task_home.source_path
// (§6). Freshness (`synced_at`, `age_seconds`, `stale`) is server-computed (TH-L13); the
// group buttons drive the existing `home=` server filter — no browser-side counting.
function renderTaskHome(data) {
  const th = data && data.task_home
  const counts = (data && data.counts && data.counts.task_home) || {}
  if (!th || th.enabled !== true) {
    return h('section', { class: 'c-task-home', 'aria-label': 'Task Home' }, [
      h('div', { class: 'c-th-head' }, [
        h('strong', { text: 'Task Home' }),
        h('span', { class: 'c-meta', text: 'not synced' }),
      ]),
      h('code', { class: 'c-th-cli', text: 'python -m continuum.cli task-home sync --source '
        + String((th && th.source_path) || '') }),
    ])
  }
  const groupCounts = counts
  const active = splitList(state.query.home)
  const groupButton = (key) => {
    const isActive = active.includes(key)
    return h('button', {
      type: 'button', class: 'c-th-group' + (isActive ? ' active' : ''),
      'aria-pressed': isActive ? 'true' : 'false',
      onClick: () => setQuery({ home: isActive ? '' : key, page: '1' }),
    }, [
      h('span', { class: 'c-th-group-label', text: HOME_LABELS[key] || humanize(key) }),
      h('b', { class: 'c-th-group-count',
               text: String(groupCounts[key] === undefined ? 0 : groupCounts[key]) }),
    ])
  }
  const nodes = [
    h('div', { class: 'c-th-head' }, [
      h('strong', { text: 'Task Home' }),
      h('span', { class: 'c-meta' }, [
        h('span', { text: 'synced ' }),
        timeNode(th.synced_at, 'c-meta', 'unknown'),
        h('span', { text: ' · age ' + (durationText(th.age_seconds) || 'unknown') + ' · ' }),
        // skin (§4.4): the fresh|stale word is unchanged server copy — wrapped in a
        // presentational span so the colour rule can read it (visible text byte-identical).
        h('span', { class: th.stale === true ? 'cj-stale' : 'cj-fresh',
                    text: th.stale === true ? 'stale' : 'fresh' }),
      ]),
    ]),
    h('div', { class: 'c-th-src', text: 'source ' + String(th.source_path || '')
      + ' · sha ' + String(th.source_sha256 || '').slice(0, 12) }),
    h('div', { class: 'c-th-groups' }, HOME_GROUPS.map(groupButton)),
  ]
  if ((groupCounts.conflicts || 0) > 0) {
    nodes.push(h('div', { class: 'c-th-conflicts', role: 'status' },
      (th.conflicts || []).map((entry) => h('div', { class: 'c-th-conflict' }, [
        h('span', { class: 'c-th-key', text: String(entry.key || '') }),
        // skin (§4.4): same visible bytes; the winner token is its own element so the
        // bold reading rule can target it (presentational split only).
        h('span', { text: String(entry.task_home_value || '') + ' vs '
          + String(entry.dashboard_value || '') + ' · winner ' }),
        h('b', { class: 'cj-th-winner', text: String(entry.winner || '') }),
      ]))))
  }
  if (Array.isArray(th.warnings) && th.warnings.length) {
    nodes.push(h('div', { class: 'c-th-warnings', text: 'warnings: '
      + th.warnings.map(humanize).join(', ') }))
  }
  const items = Array.isArray(th.items) ? th.items : []
  nodes.push(h('div', { class: 'c-th-items', role: 'list' }, items.map((item) => h('div', {
    class: 'c-th-item' + (item.project_id ? ' bound' : ' source-only'), role: 'listitem',
  }, [
    item.project_id
      ? h('button', { type: 'button', class: 'c-link c-th-key', text: String(item.key || ''),
                      onClick: () => openDetail(item.project_id) })
      : h('span', { class: 'c-th-key', text: String(item.key || '') }),
    h('span', { class: 'c-meta', text: HOME_LABELS[item.section] || humanize(item.section || '') }),
    item.binding ? h('span', { class: 'c-chip', text: String(item.binding) }) : null,
    item.receipt === true ? h('span', { class: 'c-chip warn', text: 'receipt' }) : null,
    (item.warnings || []).includes('receipt_section_mismatch')
      ? h('span', { class: 'c-chip warn', text: 'confirm then close' }) : null,
  ]))))
  return h('section', { class: 'c-task-home', 'aria-label': 'Task Home' }, nodes)
}

// Attention is the SERVER's global answer (MC-L2). The rail renders the `/attention` totals it
// is given and never counts the returned board page — a page-scoped number would contradict the
// server (INV-MC-10). No age, lane, lifecycle, or client-side stale inference happens here.
function attentionRail(payload) {
  if (!payload) {
    return h('aside', { class: 'c-attention-rail', 'aria-label': 'Needs attention' }, [
      h('div', { class: 'c-rail-heading' }, [
        h('strong', { text: MC_COPY.attentionHeading }),
        h('span', { class: 'c-meta', text: MC_COPY.loadingAttention }),
      ]),
    ])
  }
  const groups = payload.groups || {}
  const counts = payload.counts || {}
  const total = counts.total !== undefined ? counts.total : (payload.attention_count || 0)
  const groupNodes = ATTENTION_GROUPS.map(([key, label]) => {
    const items = Array.isArray(groups[key]) ? groups[key] : []
    const n = counts[key] === undefined ? items.length : counts[key]
    const shown = items.slice(0, ATTENTION_LIST_CAP)
    const omitted = Math.max(0, n - shown.length)
    const more = 'Showing ' + String(shown.length) + ' of ' + String(n) + ' in this group' +
      (omitted ? ' · View all' : '')
    return h('section', { class: 'c-rail-group rail-' + key }, [
      h('div', { class: 'c-rail-label' }, [h('span', { text: label }), h('b', { text: String(n) })]),
      shown.length
        ? h('div', { class: 'c-rail-items' }, shown.map((item) => h('button', {
            type: 'button', class: 'c-rail-item', onClick: () => openDetail(item.project_id),
            title: item.reason || label,
            text: (item.card && item.card.name) || item.project_id,
          })))
        : h('div', { class: 'c-rail-empty', text: MC_COPY.attentionEmpty }),
      n ? h('div', { class: 'c-rail-more', text: more }) : null,
    ])
  })
  return h('aside', { class: 'c-attention-rail', 'aria-label': 'Needs attention' }, [
    h('div', { class: 'c-rail-heading' }, [
      h('strong', { text: MC_COPY.attentionHeading }),
      h('span', { class: 'c-meta', text: 'server total · ' + String(total) }),
    ]),
    ...groupNodes,
  ])
}

function healthLabel(kind, value, fallback) {
  return h('div', { class: 'c-health-cell' }, [
    h('span', { class: 'c-health-key', text: kind }),
    h('strong', { text: value || fallback }),
  ])
}

function renderHealthStrip(data) {
  const snapshot = data && data.committed_snapshot
  const eventHealth = state.eventFailures >= 2 ? 'stale' : (state.eventsDelayed ? 'delayed' : (state.eventsLastOk ? 'connected' : 'waiting'))
  const phase = state.scanPhase || (data && data.scan_state && data.scan_state.phase) || 'idle'
  const stale = state.boardStale || state.eventFailures >= 2
  return h('section', { class: 'c-monitor-health', 'aria-label': 'Monitor health' }, [
    healthLabel('Source snapshot', data && data.data_as_of ? fmtTime(data.data_as_of) : 'not available', 'not available'),
    healthLabel('Registry commit', snapshot && snapshot.committed_at ? fmtTime(snapshot.committed_at) : 'not available', 'not available'),
    healthLabel('Events', eventHealth, 'waiting'),
    healthLabel('Scan', phase, 'idle'),
    stale ? h('span', { class: 'c-stale-banner', role: 'status', text: 'Showing last known board' }) : null,
  ])
}

function renderCompletenessReceipt(data) {
  const counts = (data && data.counts) || {}
  const total = counts.total !== undefined ? counts.total : ((data && data.items) || []).length
  const source = data && data.data_as_of ? fmtTime(data.data_as_of) : 'not available'
  return h('section', { class: 'c-receipt', 'aria-label': 'Completeness receipt' }, [
    h('strong', { text: 'Completeness receipt' }),
    h('span', { text: 'Server returned ' + total + ' projects' }),
    h('span', { text: 'Source snapshot: ' + source }),
    counts.continuity_total !== undefined && counts.continuity_total !== total
      ? h('span', { text: 'Filtered: ' + total + ' of ' + counts.continuity_total }) : null,
    counts.suppressed_total ? h('span', { text: 'Suppressed: ' + counts.suppressed_total + ' (explicit)' }) : null,
    h('span', { class: 'c-fact-inference', text: 'Facts are server fields; derived values are labeled.' }),
  ])
}

// ── evidence drawer (MC-L6 / Frida UX-11/UX-12) ────────────────────────
function openEvidence(card, label, entry, domId) {
  state.evidence = { projectId: card.project_id, label: label, entry: entry, card: card }
  state.evidenceReturnId = domId || null
  state.evidenceFocusPending = true
  announce('Opened evidence for ' + label + '.')
  render()
}

function closeEvidence() {
  const target = state.evidenceReturnId
  state.evidence = null
  state.evidenceFocusPending = false
  render()
  // Focus returns to the control that opened the drawer (Frida UX-11/UX-25).
  requestAnimationFrame(() => {
    const el = typeof document !== 'undefined' && target ? document.getElementById(target) : null
    if (el && typeof el.focus === 'function') el.focus()
  })
}

// The bounded rows for a claim, drawn from the SAME response (no second request, MC-A14).
function evidenceRowsFor(claim) {
  const card = (claim && claim.card) || {}
  const refs = Array.isArray(card.evidence_refs) ? card.evidence_refs : []
  const entry = (claim && claim.entry) || {}
  if (entry.kind !== 'evidence') return []
  return refs.filter((row) => String(row.evidence_id) === String(entry.evidence_id))
}

function evidenceRowNode(row) {
  const loc = row.locator || {}
  const parts = [
    h('span', { class: 'c-chip', text: tierLabel(row.tier) }),
    h('span', { class: 'c-ev-kind', text: String(row.kind || '') }),
  ]
  if (loc.profile || loc.session_id) {
    parts.push(h('code', { text: String(loc.profile || '') + '/' + String(loc.session_id || '') }))
  }
  if (loc.msg_id) parts.push(h('code', { text: 'msg ' + String(loc.msg_id) }))
  if (row.source_hash) parts.push(h('span', { class: 'c-ev-hash', text: 'hash ' + String(row.source_hash) }))
  if (row.extracted_at) parts.push(timeNode(row.extracted_at, 'c-ev-time'))
  parts.push(h('div', { class: 'c-ev-excerpt', text: String(row.excerpt || '') }))
  return h('li', { class: 'c-ev-row' }, parts)
}

function renderEvidenceDrawer() {
  const claim = state.evidence
  if (!claim) return null
  const entry = claim.entry || {}
  const rows = evidenceRowsFor(claim)
  const identity = []
  if (entry.kind === 'declared_field') identity.push('declared field: ' + String(entry.field || ''))
  if (entry.kind === 'session_fact') {
    identity.push('session record: ' + String(entry.profile || '') + '/' + String(entry.session_id || ''))
  }
  if (entry.kind === 'project_row') identity.push('project field: ' + String(entry.field || ''))
  if (entry.kind === 'evidence') identity.push('evidence tier ' + String(entry.tier === undefined ? '' : entry.tier))
  const body = rows.length
    ? h('ul', { class: 'c-ev-list' }, rows.map(evidenceRowNode))
    : h('div', { class: 'c-pane-status', text: MC_COPY.evidenceUnavailable })
  return h('section', {
    id: 'c-evidence-drawer', class: 'c-evidence-drawer', role: 'dialog', 'aria-modal': 'true',
    tabindex: '-1', 'aria-label': 'Evidence for ' + String(claim.label || ''),
  }, [
    h('div', { class: 'c-ev-head' }, [
      h('strong', { text: 'Evidence for ' + String(claim.label || '') }),
      h('button', { type: 'button', class: 'c-btn tiny ghost', onClick: closeEvidence, text: 'Close (Esc)' }),
    ]),
    h('div', { class: 'c-ev-basis', text: String(entry.basis || '') }),
    body,
    identity.length ? h('div', { class: 'c-ev-identity', text: identity.join(' · ') }) : null,
    h('div', { class: 'c-ev-footer', text: MC_COPY.evidenceFooter }),
  ])
}

function focusEvidenceIfPending() {
  if (!state.evidenceFocusPending) return
  requestAnimationFrame(() => {
    const el = typeof document !== 'undefined' ? document.getElementById('c-evidence-drawer') : null
    if (el && typeof el.focus === 'function') el.focus()
  })
}

// ── dense grid (MC-S3 / Frida UX-08/UX-09) ─────────────────────────────
// The grid renders the SAME `/projects` items the lanes do — one request, one envelope, and no
// second project list. Column order is Frida's locked order.
function renderDenseGrid(data) {
  const items = data.items || []
  const head = h('tr', {}, GRID_COLUMNS.map((label) => h('th', { scope: 'col', text: label })))
  const rows = items.map((card) => {
    const pid = card.project_id
    const name = card.name || '(untitled cluster)'
    const refs = Array.isArray(card.source_sessions) ? card.source_sessions : []
    const firstRef = refs.length ? String(refs[0].profile || '') + '/' + String(refs[0].session_id || '') : null
    const lane = humanize(card.column || '') + (card.derived_lifecycle_name ? ' · ' + card.derived_lifecycle_name : '')
    const nextState = card.next_action_state ? humanize(card.next_action_state) : 'none'
    return h('tr', { class: 'c-grid-row', 'data-project-id': pid }, [
      h('th', { scope: 'row', class: 'c-grid-project' }, [
        h('button', {
          type: 'button', class: 'c-card-name', title: name,
          onClick: () => openDetail(pid), text: name,
        }),
        h('span', {
          class: 'c-attention-marker attention-' + String(card.attention_state || 'unknown').replace(/[^a-z0-9_-]/gi, '-'),
          title: card.attention_reason || 'Server attention state not provided',
          text: card.attention_state ? humanize(card.attention_state) : 'attention unknown',
        }),
        h('span', {
          class: 'c-chip ' + (card.review_status === 'accepted' ? 'chip-accepted' : 'chip-candidate'),
          text: card.review_status === 'accepted' ? 'Accepted' : 'Candidate',
        }),
        card.urgent ? h('span', { class: 'c-chip chip-urgent', text: 'Urgent' }) : null,
        firstRef ? h('code', { class: 'c-grid-ref', text: firstRef }) : null,
      ]),
      h('td', { 'data-label': GRID_COLUMNS[1] }, [
        h('span', { class: 'c-grid-plain', text: card.derived_lifecycle_name || card.lifecycle_name || 'unknown' }),
        h('span', { class: 'c-grid-sub', text: lane + (card.placement_source ? ' · ' + String(card.placement_source) : '') }),
      ]),
      h('td', { 'data-label': GRID_COLUMNS[2] }, [claimControl(card, 'owner', ownerText(card), 'c-claim-owner-' + pid)]),
      h('td', { 'data-label': GRID_COLUMNS[3] }, [h('span', { class: 'c-grid-plain', text: activityText(card) })]),
      h('td', { 'data-label': GRID_COLUMNS[4] }, [claimControl(card, 'staleness_band', bandLabel(card.staleness_band),
                                'c-claim-band-' + pid)]),
      h('td', { 'data-label': GRID_COLUMNS[5] }, [claimControl(card, 'confidence',
                                confidenceLabel(card.confidence_band) + ' · ' + tierLabel(card.evidence_tier),
                                'c-claim-conf-' + pid)]),
      h('td', { 'data-label': GRID_COLUMNS[6] }, [claimControl(card, 'next_action', nextActionText(card) + ' · ' + nextState,
                                'c-claim-next-' + pid)]),
    ])
  })
  const counts = data.counts || {}
  return h('div', { class: 'c-grid-wrap' }, [
    h('table', { class: 'c-grid' }, [h('thead', {}, [head]), h('tbody', {}, rows)]),
    h('div', { class: 'c-footer-counts', text: 'rows ' + items.length + ' · total ' +
      String(counts.total !== undefined ? counts.total : items.length) }),
  ])
}

// ── staleness bands (MC-S4 / Frida UX-13/UX-14) ────────────────────────
// Every number here is the SERVER's per-band total. The browser filters through `band=`, it
// never re-buckets a card or recomputes a total (INV-MC-10).
function renderBands(payload) {
  if (!payload) {
    return h('section', { class: 'c-bands', 'aria-label': MC_COPY.bandHeading }, [
      h('strong', { text: MC_COPY.bandHeading }),
      h('span', { class: 'c-pane-status', text: MC_COPY.loadingBoard }),
    ])
  }
  const counts = payload.counts || {}
  const active = splitList(state.query.band)
  const band = (key) => {
    const isActive = active.includes(key)
    return h('button', {
      type: 'button', class: 'c-band' + (isActive ? ' active' : ''),
      'aria-pressed': isActive ? 'true' : 'false',
      onClick: () => setQuery({ band: isActive ? '' : key, page: '1' }),
    }, [
      h('span', { class: 'c-band-label', text: bandLabel(key) }),
      h('b', { class: 'c-band-count', text: String(counts[key] === undefined ? 0 : counts[key]) }),
    ])
  }
  return h('section', { class: 'c-bands', 'aria-label': MC_COPY.bandHeading }, [
    h('div', { class: 'c-band-head' }, [
      h('strong', { text: MC_COPY.bandHeading }),
      h('span', { class: 'c-meta', text: MC_COPY.bandSubline }),
      h('span', { class: 'c-meta', text: 'anchor ' + String(payload.band_anchor || '') +
        ' · edges ' + String((payload.band_edges || []).join('/')) }),
    ]),
    h('div', { class: 'c-band-row' }, BAND_FILTER_VALUES.map(band)),
    h('div', { class: 'c-band-row c-band-quiet' }, [
      h('span', { class: 'c-band quiet' }, [
        h('span', { class: 'c-band-label', text: bandLabel(QUIET_BAND) }),
        h('b', { class: 'c-band-count', text: String(counts[QUIET_BAND] || 0) }),
      ]),
      h('span', { class: 'c-meta', text: MC_COPY.quietSubline }),
    ]),
  ])
}

// ── Recovery Inbox (MC-S5 / Frida UX-15..UX-18) ────────────────────────
function renderInbox(payload) {
  if (!payload) {
    return h('section', { class: 'c-inbox', 'aria-label': MC_COPY.inboxHeading }, [
      h('strong', { text: MC_COPY.inboxHeading }),
      h('div', { class: 'c-pane-status', text: MC_COPY.loadingInbox }),
      h('button', { type: 'button', class: 'c-btn tiny', onClick: () => refresh(), text: 'Retry' }),
    ])
  }
  const counts = payload.counts || {}
  const candidates = counts.candidates === undefined ? payload.total : counts.candidates
  const nodes = [
    h('div', { class: 'c-inbox-head' }, [
      h('strong', { text: MC_COPY.inboxHeading }),
      h('span', { class: 'c-meta', text: 'Candidates: ' + String(candidates) +
        ' · Accepted: ' + String(counts.accepted === undefined ? 0 : counts.accepted) }),
    ]),
    h('div', { class: 'c-inbox-triage', text: 'Review candidates in the server-provided order.' }),
  ]
  if (!candidates) {
    nodes.push(h('div', { class: 'c-inbox-zero', text: MC_COPY.inboxZero }))
  } else {
    const byId = {}
    for (const item of (payload.items || [])) byId[item.project_id] = item
    const order = Array.isArray(payload.triage_order) ? payload.triage_order : []
    nodes.push(h('ol', { class: 'c-inbox-order' }, order.map((entry) => {
      const item = byId[entry.project_id] || {}
      return h('li', {}, [
        h('span', { class: 'c-inbox-rank', text: '#' + String(entry.rank) }),
        h('button', {
          type: 'button', class: 'c-link',
          onClick: () => openDetail(entry.project_id),
          text: item.proposed_name || entry.project_id,
        }),
        h('span', { class: 'c-chip', text: confidenceLabel(entry.confidence_band) }),
      ])
    })))
  }
  if (counts.suppressed_total) {
    nodes.push(h('div', { class: 'c-inbox-suppressed', text: String(counts.suppressed_total) +
      ' items are suppressed evidence (dismissed or merged), not deleted.' +
      ' Undo remains available from the review history.' }))
  }
  return h('section', { class: 'c-inbox', 'aria-label': MC_COPY.inboxHeading }, nodes)
}

// ── mission-control header: snapshot age + structured scan state (MC-L4/MC-L5) ──
function scanStateNode(scanState) {
  if (!scanState) return h('span', { class: 'c-mc-scan', text: MC_COPY.noScan })
  if (scanState.phase === 'error') {
    return h('span', { class: 'c-mc-scan is-error', text: MC_COPY.scanFailed })
  }
  if (scanState.phase === 'scanning') {
    return h('span', { class: 'c-mc-scan is-running', text: 'Scanning ' + String(scanState.mode || '') +
      '\u2026 Showing the last committed snapshot until this scan finishes.' })
  }
  const dur = durationText(scanState.elapsed_seconds)
  const profiles = scanState.profiles_scanned
  if (!dur || profiles === null || profiles === undefined) {
    return h('span', { class: 'c-mc-scan', text: MC_COPY.noScan })
  }
  return h('span', { class: 'c-mc-scan', text: 'Last scan: ' + String(scanState.mode || '') + ' · ' +
    dur + ' · ' + String(profiles) + ' profiles scanned.' })
}

function renderMissionControl(data) {
  const age = snapshotAgeText(data)
  const line = [
    h('span', { text: 'Showing the committed snapshot from ' }),
    timeNode(data && data.data_as_of, 'c-mc-time', 'unknown'),
    h('span', { text: age ? '. Snapshot age: ' + age + '.' : '. Snapshot age: unknown.' }),
  ]
  const stale = state.eventFailures >= 2
  return h('section', { class: 'c-mission-control', 'aria-labelledby': 'mc-heading' }, [
    h('div', { class: 'c-mc-head' }, [
      h('h2', { id: 'mc-heading', text: MC_COPY.title }),
      scanStateNode(state.scanState || (data && data.scan_state) || null),
    ]),
    h('div', { class: 'c-mc-snapshot' }, line),
    stale ? h('div', { class: 'c-stale-banner', role: 'status', text: MC_COPY.staleBoard }) : null,
  ])
}

function renderLoading() {
  return h('div', { class: 'c-board' }, BOARD_COLUMNS.map((col) => h('div', { class: 'c-lane', role: 'region', 'aria-label': COLUMN_LABELS[col] + ' column, loading' }, [
    h('div', { class: 'c-lane-header' }, [h('span', { text: COLUMN_LABELS[col] })]),
    h('div', { class: 'c-skeleton' }), h('div', { class: 'c-skeleton' }),
  ])))
}

// Frida UX-22: active filters are shown from the SERVER's echoed values, never re-derived.
const FILTER_ECHO_KEYS = [['lanes', 'lane'], ['lifecycles', 'lifecycle'], ['attention', 'attention'],
                         ['bands', 'band'], ['profiles', 'profile'], ['home', 'home']]

function filterSummary(data) {
  const q = (data && data.query) || {}
  const parts = []
  for (const [echoKey, label] of FILTER_ECHO_KEYS) {
    const values = Array.isArray(q[echoKey]) ? q[echoKey] : []
    if (values.length) parts.push(label + '=' + values.join(','))
  }
  if (q.q) parts.push('q=' + String(q.q))
  if (!parts.length) return null
  return h('div', { class: 'c-filter-summary' }, [
    h('span', { class: 'c-tb-label', text: 'Filters' }),
    h('span', { text: parts.join(' · ') }),
    h('button', { type: 'button', class: 'c-btn tiny ghost', onClick: clearQuery,
                  text: MC_COPY.clearAction }),
  ])
}

// Clearing is a query action only: it never changes a server total or a stored decision.
function clearQuery() {
  if (els.filter) els.filter.value = ''
  state.filter = ''
  setQuery({ lane: '', lifecycle: '', attention: '', band: '', profile: '', home: '',
             q: '', page: '1' })
}

// `replaceChildren` receives DOM nodes only — never a `null` (which would render as text).
function replaceApp(nodes) {
  els.app.replaceChildren.apply(els.app, nodes.filter((node) => !!node))
}

function renderEmpty(data) {
  // Frida UX-20: an empty RESULT and an empty CORPUS are different states with different actions.
  const filtered = hasActiveFilter(state.query)
  return h('div', { class: 'c-empty' }, [
    h('div', { text: filtered ? MC_COPY.filterEmpty : MC_COPY.trueEmpty }),
    h('div', { class: 'c-empty-actions' }, [
      filtered ? h('button', { type: 'button', class: 'c-btn', onClick: clearQuery,
                               text: MC_COPY.clearAction }) : null,
      h('button', { type: 'button', class: 'c-btn primary', onClick: () => scanNow(),
                    text: MC_COPY.scanAction }),
    ]),
    h('div', { style: { paddingTop: '6px' } },
      ['Sessions are read-only — nothing is modified.']),
  ])
}

function renderError(err) {
  return h('div', { class: 'c-error' }, [
    h('h2', { text: 'Failed to load' }),
    h('div', { text: String(err && err.message ? err.message : err) }),
    h('div', { style: { paddingTop: '8px' } }, [
      h('button', { type: 'button', class: 'c-btn', onClick: () => { state.loading = true; render(); refresh() }, text: 'Retry' }),
    ]),
  ])
}

// ── session pane (Shayba D-SP-1..13) ───────────────────────────────────
function paneRefFor(session) {
  // Detail rows carry profile_name (registry shape); card rows carry profile (API shape).
  return sessionProfile(session) + '/' + session.session_id
}

function sessionProfile(session) {
  return (session && (session.profile || session.profile_name)) || ''
}

function paneToggleDomId(ref) {
  return 'c-pane-toggle-' + String(ref).replace(/[^A-Za-z0-9_-]/g, '_')
}

// D-SP-2 / A-SP-6: collapsing a pane returns focus to the disclosure button it came from.
function collapsePane(ref) {
  state.openPane = null
  render()
  const el = typeof document !== 'undefined' ? document.getElementById(paneToggleDomId(ref)) : null
  if (el && typeof el.focus === 'function') el.focus()
}

// D-SP-9: the decided UX display cap for the mixed RECENT block (server response cap is larger).
const PANE_DISPLAY_FALLBACK = 5

function paneCopy() {
  return {
    block_recent: 'RECENT MESSAGES',
    block_user: 'YOUR RECENT MESSAGES',
    excerpt_chip: 'excerpt',
    excerpt_note: 'Captured excerpt — the full message is not available here.',
    empty_session: 'No messages captured for this session.',
    empty_user_window: 'No messages of yours in the captured window.',
    empty_not_user_facing: 'No messages of yours in this session. It was started by an agent, not by you.',
    empty_unknown_audience: 'No messages of yours in this session.',
    loading: 'Loading messages…',
    load_failed: "Couldn't load messages.",
    unavailable: 'Message capture is unavailable for this session.',
    unreadable: 'Message capture could not be read for this session.',
    disclosure: 'Messages',
    window: '',
  }
}

function paneMessageRow(row, copy) {
  const stamp = h('time', {
    class: 'c-pane-time',
    datetime: fmtAbsolute(row.timestamp),
    title: fmtTime(row.timestamp),
    text: fmtTime(row.timestamp),
  })
  const parts = [h('span', {
    class: 'c-pane-role ' + (row.role === 'YOU' ? 'is-you' : 'is-agent'),
    text: row.role,
  }), stamp]
  if (row.excerpt) {
    parts.push(h('span', { class: 'c-chip chip-excerpt', title: copy.excerpt_note, text: copy.excerpt_chip }))
  }
  parts.push(h('div', { class: 'c-pane-text', text: row.text }))
  // D-SP-8: only a capture-clipped row is an excerpt, and only an excerpt is clamped and
  // offered no reveal. An unclipped row is the complete captured text and shows in full.
  return h('li', {
    class: 'c-pane-row ' + (row.role === 'YOU' ? 'is-you' : 'is-agent')
      + (row.excerpt ? ' is-clipped' : ''),
    'aria-label': row.role + ', ' + fmtTime(row.timestamp) + (row.excerpt ? ', excerpt' : '') + ': ' + row.text,
  }, parts)
}

function renderPane(session, projectId) {
  const ref = paneRefFor(session)
  const copy = paneCopy()
  const open = state.openPane === ref
  const paneId = 'pane-' + projectId + '-' + session.session_id
  const button = h('button', {
    type: 'button', class: 'c-btn tiny ghost c-pane-toggle',
    id: paneToggleDomId(ref),
    'aria-expanded': open ? 'true' : 'false',
    'aria-controls': paneId,
    'aria-label': (copy.disclosure || 'Messages') + ' for ' + ref,
    onClick: () => togglePane(projectId, ref),
    text: (copy.disclosure || 'Messages') + (open ? ' ▾' : ' ▸'),
  })
  const rowChildren = [
    button,
    h('button', { type: 'button', class: 'c-btn tiny ghost', onClick: () => copyText(session.session_id, 'Copy ID'), text: 'Copy ID' }),
    session.copy_command_profile_scoped ? h('button', {
      type: 'button', class: 'c-btn tiny ghost',
      onClick: () => copyText(session.copy_command_profile_scoped, 'resume cmd'),
      text: 'resume cmd',
    }) : null,
  ]
  const row = h('div', { class: 'c-resume' }, [
    h('code', { text: sessionProfile(session) + '/' + session.session_id }),
    session.copy_command_profile_scoped ? h('code', { text: session.copy_command_profile_scoped }) : null,
    ...rowChildren,
  ])
  if (!open) return h('div', { class: 'c-resume-block' }, [row])
  return h('div', { class: 'c-resume-block' }, [row, renderPaneBody(ref, paneId, copy)])
}

function renderPaneBody(ref, paneId, copy) {
  if (state.paneLoading === ref) {
    return h('section', { id: paneId, class: 'c-pane', 'aria-label': ref, role: 'region' }, [
      h('div', { class: 'c-pane-status', text: copy.loading }),
      h('div', { class: 'c-skeleton' }), h('div', { class: 'c-skeleton' }),
    ])
  }
  const pane = state.panes[ref]
  if (!pane || pane.error) {
    return h('section', { id: paneId, class: 'c-pane', 'aria-label': ref, role: 'region' }, [
      h('div', { class: 'c-pane-status', text: copy.load_failed }),
      h('button', { type: 'button', class: 'c-btn tiny', onClick: () => loadPane(ref), text: 'Retry' }),
    ])
  }
  const payloadCopy = Object.assign(copy, pane.copy || {})
  if (pane.available === false) {
    const unavailableCopy = pane.reason === 'source_unreadable'
      ? (payloadCopy.unreadable || 'Message capture could not be read for this session.')
      : (payloadCopy.unavailable || 'Message capture is unavailable for this session.')
    return h('section', { id: paneId, class: 'c-pane', 'aria-label': ref, role: 'region' }, [
      h('div', { class: 'c-pane-status', text: unavailableCopy }),
      h('button', { type: 'button', class: 'c-btn tiny', onClick: () => loadPane(ref), text: 'Retry' }),
    ])
  }
  const sections = []
  const recent = pane.recent_messages || []
  // D-SP-9: the server's response cap (pane_recent_cap) is NOT the display cap. The decided
  // display limit is five rows; the rest are reported as omitted, never silently rendered.
  const displayCap = Number.isFinite(pane.display_cap) && pane.display_cap >= 0
    ? pane.display_cap : PANE_DISPLAY_FALLBACK
  const shown = recent.length > displayCap ? recent.slice(recent.length - displayCap) : recent
  if (recent.length === 0) {
    sections.push(h('div', { class: 'c-pane-status', text: payloadCopy.empty_session }))
  } else {
    sections.push(h('div', { class: 'c-pane-block', role: 'group', 'aria-label': payloadCopy.block_recent }, [
      h('div', { class: 'c-block-label', text: payloadCopy.block_recent }),
      h('ol', { class: 'c-pane-list' }, shown.map((row) => paneMessageRow(row, payloadCopy))),
      recent.length > shown.length
        ? h('div', { class: 'c-pane-omitted', text: (recent.length - shown.length) + ' captured rows not shown' })
        : null,
    ]))
  }
  const stateName = pane.user_messages_state
  const userRows = pane.user_messages || []
  let userBody
  if (stateName === 'ok' && userRows.length) {
    userBody = h('ol', { class: 'c-pane-list' }, userRows.map((row) => paneMessageRow(row, payloadCopy)))
  } else if (stateName === 'not_user_facing') {
    userBody = h('div', { class: 'c-pane-status', text: payloadCopy.empty_not_user_facing })
  } else if (stateName === 'unknown_audience') {
    userBody = h('div', { class: 'c-pane-status', text: payloadCopy.empty_unknown_audience })
  } else {
    userBody = h('div', { class: 'c-pane-status', text: payloadCopy.empty_user_window })
  }
  sections.push(h('div', { class: 'c-pane-block', role: 'group', 'aria-label': payloadCopy.block_user }, [
    h('div', { class: 'c-block-label', text: payloadCopy.block_user }),
    userBody,
  ]))
  sections.push(h('div', { class: 'c-pane-window', text: payloadCopy.window || '' }))
  return h('section', { id: paneId, class: 'c-pane', 'aria-label': ref, role: 'region' }, sections)
}

async function togglePane(projectId, ref) {
  if (state.openPane === ref) {
    collapsePane(ref)
    return
  }
  state.openPane = ref
  render()
  if (!state.panes[ref]) await loadPane(ref, projectId)
}

async function loadPane(ref, projectId) {
  const pid = projectId || (state.detailId || '')
  state.paneLoading = ref
  render()
  try {
    const payload = await fetchPane(pid, ref)
    state.panes[ref] = payload.session_pane || { available: false, error: true }
  } catch (e) {
    state.panes[ref] = { available: false, error: true }
  } finally {
    state.paneLoading = null
  }
  render()
}

function renderDetail() {
  const d = state.detail
  if (!d) {
    if (!state.detailError) return h('div', { class: 'c-detail' }, [h('div', { class: 'c-skeleton' }), h('div', { class: 'c-skeleton' })])
    return h('div', { class: 'c-detail' }, [
      h('button', { type: 'button', class: 'c-btn ghost', onClick: () => { state.detailId = null; state.detailError = null; render() }, text: '← back' }),
      h('div', { class: 'c-error', style: { marginTop: '10px' } }, [
        h('h2', { text: 'Detail is unavailable' }),
        h('div', { text: String(state.detailError.message || state.detailError) }),
        h('button', { type: 'button', class: 'c-btn', style: { marginTop: '8px' }, onClick: () => openDetail(state.detailId), text: 'Retry' }),
      ]),
    ])
  }
  const p = d.project || {}
  const col = p.column || 'inbox'
  const rows = []
  rows.push(h('div', { class: 'c-row' }, [
    h('button', { type: 'button', class: 'c-btn ghost', onClick: () => { state.detailId = null; state.detail = null; render() }, text: '← back' }),
  ]))
  if (state.detailError) {
    rows.push(h('div', { class: 'c-error', role: 'status' }, [
      h('strong', { text: 'Showing cached detail · stale' }),
      h('div', { text: String(state.detailError.message || state.detailError) }),
      h('button', { type: 'button', class: 'c-btn tiny', style: { marginTop: '8px' }, onClick: () => openDetail(state.detailId), text: 'Retry' }),
    ]))
  }
  rows.push(h('div', { class: 'c-row' }, [
    h('h2', { text: p.name || '(untitled)' }),
    h('span', { class: 'c-chip', text: p.lifecycle_name || p.derived_lifecycle_name || 'unknown' }),
    p.placement_source ? h('span', { class: 'c-chip', text: p.placement_source }) : null,
    p.urgent ? h('span', { class: 'c-chip chip-urgent', text: 'Urgent' }) : null,
    h('span', { class: 'c-chip ' + (p.review_status === 'accepted' ? 'chip-accepted' : 'chip-candidate'), text: p.review_status === 'accepted' ? 'Accepted' : 'Candidate' }),
    provenanceChip(p),
  ]))

  const moveButtons = NON_INBOX_PLACEMENTS.map((t) => h('button', {
    type: 'button', class: 'c-btn tiny', disabled: t === col || !!state.pending[p.project_id],
    title: 'Move to ' + COLUMN_LABELS[t],
    onClick: () => moveFromDetail(p, t),
    text: (t === col ? '✓ ' : '') + COLUMN_LABELS[t],
  }))
  rows.push(h('div', { class: 'c-detail-section' }, [
    h('h3', { text: 'Column: ' + COLUMN_LABELS[col] + ' · ' + (p.placement_source || '') }),
    h('div', { class: 'c-row' }, moveButtons),
    h('div', { style: { paddingTop: '6px' } }, [
      h('button', {
        type: 'button', class: 'c-btn tiny', 'aria-pressed': p.urgent ? 'true' : 'false',
        onClick: () => doToggleUrgent({ ...p, project_id: p.project_id }), text: p.urgent ? 'Remove urgent' : 'Mark urgent',
      }),
    ]),
  ]))

  rows.push(h('div', { class: 'c-detail-section' }, [
    h('h3', { text: 'Next action' }),
    h('div', { class: 'c-detail-row', text: p.next_action
      ? (readableNextAction(p.next_action) || 'Next action recorded') + ' (' + (p.next_action.source || 'unknown') + ')'
      : 'none declared' }),
  ]))

  rows.push(h('div', { class: 'c-detail-section' }, [
    h('h3', { text: 'Linked sessions' }),
    ...(d.sessions || []).map((s) => renderPane(s, p.project_id)),
  ]))

  if ((d.resume_links || []).length) {
    rows.push(h('div', { class: 'c-detail-section' }, [
      h('h3', { text: 'Resume' }),
      h('div', { class: 'c-meta', text: 'Choose a session to copy its profile-scoped resume command.' }),
      ...(d.resume_links || []).map((link) => {
        const profile = link.profile || link.profile_name || ''
        const ref = profile + '/' + link.session_id
        const command = link.copy_command_profile_scoped || link.copy_command
        return h('div', { class: 'c-resume' }, [
          h('code', { text: ref }),
          command ? h('button', { type: 'button', class: 'c-btn tiny', onClick: () => copyText(command, 'Resume command'), text: 'Resume' }) : null,
        ])
      }),
    ]))
  }

  rows.push(h('div', { class: 'c-detail-section' }, [
    h('h3', { text: 'Evidence excerpts (tier-labelled, read-only)' }),
    ...((d.evidence || []).slice(0, 20).map((ev) => h('div', { class: 'c-detail-row evidence', text: 'T' + ev.tier + ' · ' + ev.kind + ' · ' + (ev.excerpt || '') }))),
  ]))

  rows.push(h('div', { class: 'c-detail-section' }, [
    h('h3', { text: 'Decision history (audit, reversible)' }),
    ...((d.audit || []).map((a) => h('div', { class: 'c-detail-row', text: (a.action || '') + ' · ' + fmtTime(a.ts) }))),
  ]))

  return h('div', { class: 'c-detail' }, rows)
}

async function moveFromDetail(p, target) {
  const card = { ...p, column: p.column }
  const origin = p.column
  if (origin === target) return
  if (!canDropOnColumn(target)) return
  state.pending = { ...state.pending, [p.project_id]: target }
  render()
  try {
    const isInboxExit = origin === 'inbox'
    const action = isInboxExit ? 'accept' : 'set_placement'
    const payload = isInboxExit ? { lifecycle: 'LS-1', placement: target } : { placement: target }
    const body = await review(p.project_id, action, payload)
    await refresh()
    await openDetail(p.project_id)
    if (isInboxExit) toast(buildAcceptToast(p.name || p.project_id, target), { label: 'Undo', onClick: () => doUndo(body.audit_id, p.name || p.project_id) })
  } catch (e) {
    toast(buildFailureToast(p.name || p.project_id, origin), { label: 'Retry', onClick: () => moveFromDetail(p, target) })
  } finally {
    const next = { ...state.pending }
    delete next[p.project_id]
    state.pending = next
    render()
  }
  void card
}

async function openDetail(pid) {
  const sameDetail = state.detailId === pid && state.detail !== null
  state.detailId = pid
  if (!sameDetail) state.detail = null
  state.detailError = null
  state.detailStale = false
  state.openPane = null
  render()
  try {
    const res = await fetch(API + '/projects/' + pid)
    if (!res.ok) throw new Error('HTTP ' + res.status)
    state.detail = await res.json()
    state.detailStale = false
    // D-SP-2: auto-open the ANCHOR session's pane first, then the primary. The anchor is the
    // user's own conversation and is pane-eligible even when it sits outside the cluster.
    const proj = state.detail.project || {}
    const primary = proj.primary_session
    const target = proj.anchor_session
      || (primary && sessionProfile(primary) && primary.session_id
            ? sessionProfile(primary) + '/' + primary.session_id : null)
    if (target) {
      state.openPane = target
      loadPane(target, pid)
      return
    }
  } catch (e) {
    state.detailError = e
    state.detailStale = state.detail !== null
  }
  render()
}

// ── toolbar (view / sort / direction / filters) ────────────────────────
function renderToolbar() {
  if (!els.toolbar) return
  const q = state.query
  const nodes = []
  nodes.push(h('span', { class: 'c-tb-label', text: 'View' }))
  for (const view of VIEWS) {
    nodes.push(h('button', {
      type: 'button',
      class: 'c-btn tiny' + (q.view === view && state.view === 'board' ? ' primary' : ''),
      'aria-pressed': q.view === view && state.view === 'board' ? 'true' : 'false',
      onClick: () => { setView('board').then(() => setQuery({ view: view, page: '1' })) },
      text: view === 'today' ? 'Today' : 'All',
    }))
  }
  // AL-L6/AL-L7: a THIRD, distinct view on the same playground path.
  nodes.push(h('button', {
    type: 'button',
    class: 'c-btn tiny' + (state.view === 'action_log' ? ' primary' : ''),
    'aria-pressed': state.view === 'action_log' ? 'true' : 'false',
    onClick: () => setView(state.view === 'action_log' ? 'board' : 'action_log'),
    text: 'Action log',
  }))
  if (state.view === 'action_log') {
    // The action log has its own vocabulary (AL-L13); no board control appears in this view.
    nodes.push(h('span', { class: 'c-tb-label', text: 'Kind' }))
    nodes.push(h('select', {
      class: 'c-select', 'aria-label': 'kind filter',
      onChange: (e) => setActionLogQuery({ kind: e.target.value, page: '1' }),
    }, [h('option', { value: '', selected: q.kind === '' ? true : null, text: 'all kinds' })]
      .concat(AL_KINDS.map((kind) => h('option', {
        value: kind, selected: q.kind === kind ? true : null, text: kind,
      })))))
    nodes.push(h('span', { class: 'c-tb-label', text: 'Operator' }))
    nodes.push(h('select', {
      class: 'c-select', 'aria-label': 'operator filter',
      onChange: (e) => setActionLogQuery({ operator: e.target.value, page: '1' }),
    }, [h('option', { value: '', selected: q.operator === '' ? true : null, text: 'any' })]
      .concat(AL_OPERATORS.map((op) => h('option', {
        value: op, selected: q.operator === op ? true : null, text: op,
      })))))
    nodes.push(h('span', { class: 'c-tb-label', text: 'Status' }))
    nodes.push(h('select', {
      class: 'c-select', 'aria-label': 'status filter',
      onChange: (e) => setActionLogQuery({ status: e.target.value, page: '1' }),
    }, [h('option', { value: '', selected: q.status === '' ? true : null, text: 'active or archived' })]
      .concat(AL_VIEWS.map((status) => h('option', {
        value: status, selected: q.status === status ? true : null, text: status,
      })))))
    nodes.push(h('span', { class: 'c-tb-label', text: 'Sort' }))
    nodes.push(h('select', {
      class: 'c-select', 'aria-label': 'action-log sort field',
      onChange: (e) => setActionLogQuery({ sort: e.target.value, page: '1' }),
    }, AL_SORTS.map((field) => h('option', {
      value: field, selected: q.sort === field ? true : null, text: field,
    }))))
    const archivedCount = state.data && state.data.counts ? state.data.counts.archived : null
    if (archivedCount !== null && archivedCount !== undefined) {
      nodes.push(h('span', { class: 'c-tb-label', text: 'Archived ' + fmtCount(archivedCount) }))
      nodes.push(h('button', {
        type: 'button', class: 'c-btn tiny',
        onClick: () => setActionLogQuery({
          status: q.status === 'archived' ? '' : 'archived', page: '1',
        }),
        text: q.status === 'archived' ? 'hide archived' : 'show archived',
      }))
    }
    nodes.push(h('button', {
      type: 'button', class: 'c-btn tiny ghost',
      onClick: () => setActionLogQuery({ ...AL_DEFAULT_QUERY, page: '1' }),
      text: 'Clear',
    }))
    els.toolbar.replaceChildren(...nodes)
    return
  }
  nodes.push(h('span', { class: 'c-tb-label', text: 'Sort' }))
  nodes.push(h('select', {
    class: 'c-select', 'aria-label': 'sort field',
    onChange: (e) => setQuery({ sort: e.target.value, page: '1' }),
  }, SORT_FIELDS.map((field) => h('option', {
    value: field, selected: q.sort === field ? true : null, text: field,
  }))))
  nodes.push(h('button', {
    type: 'button', class: 'c-btn tiny',
    'aria-pressed': q.direction === 'asc' ? 'true' : 'false',
    title: 'toggle sort direction',
    onClick: () => setQuery({ direction: q.direction === 'asc' ? 'desc' : 'asc', page: '1' }),
    text: q.direction === 'asc' ? '↑ asc' : '↓ desc',
  }))
  nodes.push(h('span', { class: 'c-tb-label', text: 'Lane' }))
  nodes.push(h('select', {
    class: 'c-select', 'aria-label': 'lane filter',
    onChange: (e) => setQuery({ lane: e.target.value, page: '1' }),
  }, [h('option', { value: '', selected: q.lane === '' ? true : null, text: 'all lanes' })]
    .concat(BOARD_COLUMNS.map((col) => h('option', {
      value: col, selected: splitList(q.lane).includes(col) ? true : null, text: COLUMN_LABELS[col],
    })))))
  nodes.push(h('span', { class: 'c-tb-label', text: 'State' }))
  nodes.push(h('select', {
    class: 'c-select', 'aria-label': 'lifecycle filter',
    onChange: (e) => setQuery({ lifecycle: e.target.value, page: '1' }),
  }, [h('option', { value: '', selected: q.lifecycle === '' ? true : null, text: 'any state' })]
    .concat(LIFECYCLES.map((ls) => h('option', {
      value: ls, selected: splitList(q.lifecycle).includes(ls) ? true : null, text: ls,
    })))))
  nodes.push(h('span', { class: 'c-tb-label', text: 'Attention' }))
  nodes.push(h('select', {
    class: 'c-select', 'aria-label': 'attention filter',
    onChange: (e) => setQuery({ attention: e.target.value, page: '1' }),
  }, [h('option', { value: '', selected: q.attention === '' ? true : null, text: 'any' })]
    .concat(ATTENTION_STATES.map((s) => h('option', {
      value: s, selected: splitList(q.attention).includes(s) ? true : null, text: s,
    })))))
  // MC-S4: the band filter uses the SAME bands the banded view totals (server-side, OR-within).
  nodes.push(h('span', { class: 'c-tb-label', text: 'Band' }))
  nodes.push(h('select', {
    class: 'c-select', 'aria-label': 'staleness band filter',
    onChange: (e) => setQuery({ band: e.target.value, page: '1' }),
  }, [h('option', { value: '', selected: q.band === '' ? true : null, text: 'any band' })]
    .concat(BAND_FILTER_VALUES.map((key) => h('option', {
      value: key, selected: splitList(q.band).includes(key) ? true : null, text: bandLabel(key),
    })))))
  // Frida UX-08: both views show the same filtered projects.
  nodes.push(h('span', { class: 'c-tb-label', text: 'Layout' }))
  for (const [key, label] of [['lanes', MC_COPY.lanes], ['grid', MC_COPY.grid]]) {
    nodes.push(h('button', {
      type: 'button',
      class: 'c-btn tiny' + (state.layout === key ? ' primary' : ''),
      'aria-pressed': state.layout === key ? 'true' : 'false',
      onClick: () => { state.layout = key; renderToolbar(); render() },
      text: label,
    }))
  }
  nodes.push(h('span', { class: 'c-tb-hint', text: MC_COPY.gridSupporting }))
  nodes.push(h('button', {
    type: 'button', class: 'c-btn tiny ghost',
    onClick: clearQuery,
    text: 'Clear',
  }))
  els.toolbar.replaceChildren(...nodes)
}

function buildToolbar() {
  const header = document.querySelector('.c-header-actions')
  const bar = h('div', { class: 'c-toolbar', role: 'group', 'aria-label': 'playground controls' })
  if (header) header.appendChild(bar)
  else if (els.app && els.app.parentNode) els.app.parentNode.insertBefore(bar, els.app)
  els.toolbar = bar
  renderToolbar()
}

// ── toast / announce / copy ────────────────────────────────────────────
function toast(message, action) {
  if (!els.toastRoot) return
  els.toastRoot.replaceChildren()
  const node = h('div', { class: 'c-toast', role: 'status' }, [h('div', { text: message })])
  if (action && action.label) {
    node.appendChild(h('div', { class: 'c-toast-actions' }, [
      h('button', { type: 'button', class: 'c-btn tiny', onClick: () => { els.toastRoot.replaceChildren(); action.onClick() }, text: action.label }),
      h('button', { type: 'button', class: 'c-btn tiny ghost', onClick: () => els.toastRoot.replaceChildren(), text: 'Dismiss' }),
    ]))
  }
}

function announce(message) {
  state.announce = message
  if (els.live) els.live.textContent = message
}

function copyText(text, label) {
  const done = () => announce((label || 'Value') + ' copied.')
  try {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(done, () => announce('Copy failed.'))
    } else { done() }
  } catch (e) { announce('Copy failed.') }
}

// ── action-log view (AL-L6/AL-L7/AL-L15) ───────────────────────────────────────────────
// A DISTINCT layout: a flat, action-centric table with its own filter bar and an archive
// button per row. Every string rendered is a SERVER field — counts, kinds, operators, statuses,
// targets, evidence links and timestamps all come from the response; the browser derives none
// of them and invents no labels.
// Skin column captions (Frida §4.10/§9): AL_COLUMNS[0..5] repeat the header captions
// renderActionLogTable emits, byte-for-byte; index 6 labels the button cell (whose header is
// the empty string today and stays so) and is only painted by the ≤640px labelled-row reflow.
// `data-label` re-attaches a caption to each cell; no server field is renamed or re-ordered.
const AL_COLUMNS = ['Time', 'Kind', 'Target', 'Evidence', 'Operator', 'Status', 'Action']
function setView(next) {
  state.view = next === 'action_log' ? 'action_log' : 'board'
  if (state.view === 'action_log') {
    state.query = { ...state.query, ...AL_DEFAULT_QUERY }
  }
  state.data = null
  state.actionLog.error = null
  syncUrl()
  renderToolbar()
  return refresh()
}

function setActionLogQuery(patch) {
  state.query = { ...state.query, ...patch }
  state.actionLog.error = null
  syncUrl()
  renderToolbar()
  return refresh()
}

async function doArchive(actionId, verb) {
  state.actionLog.busy = actionId
  state.actionLog.error = null
  render()
  try {
    const res = await fetch(API + '/action-log/' + encodeURIComponent(actionId) + '/' + verb, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ actor: 'dashboard' }),
    })
    if (!res.ok) {
      const txt = await res.text().catch(() => '')
      throw new Error(txt || verb + ' failed: HTTP ' + res.status)
    }
    const body = await res.json().catch(() => ({}))
    state.actionLog.undo = { audit_id: body.audit_id || null, action_id: actionId,
                             verb: verb, noop: !!body.noop }
    announce((verb === 'archive' ? 'Archived ' : 'Unarchived ') + actionId
             + (body.noop ? ' — already in that state.' : '.'))
    state.actionLog.busy = null
    return refresh()
  } catch (e) {
    state.actionLog.busy = null
    state.actionLog.error = e
    render()
    return null
  }
}

function summaryText(map) {
  const entries = Object.entries(map || {})
  if (!entries.length) return '—'
  return entries.map(([key, value]) => key + ' ' + fmtCount(value)).join(' · ')
}

function actionLogSelect(label, key, options, current) {
  return h('label', { class: 'c-al-field' }, [
    h('span', { class: 'c-tb-label', text: label }),
    h('select', {
      class: 'c-select', 'aria-label': label,
      onChange: (e) => setActionLogQuery({ [key]: e.target.value, page: '1' }),
    }, [h('option', { value: '', selected: current ? null : true, text: 'all' })].concat(
      options.map((value) => h('option', {
        value: value, selected: current === value ? true : null, text: value,
      })))),
  ])
}

function renderActionLogFilters() {
  const q = state.query
  const fullLog = q.scope === 'all'
  return h('div', { class: 'c-al-filters' }, [
    // Orda 2026-09-17: ONE control switching External ⇄ Full log. It patches only `scope`
    // (and resets paging), so every other active filter is preserved across the toggle.
    h('span', { class: 'c-tb-label', text: 'Scope' }),
    h('button', {
      type: 'button',
      class: 'c-btn tiny' + (fullLog ? '' : ' primary'),
      'aria-pressed': fullLog ? 'false' : 'true',
      title: fullLog
        ? 'Showing the full log — click to return to external-facing actions only'
        : 'Showing external-facing actions only — click for the full log',
      onClick: () => setActionLogQuery({ scope: fullLog ? 'external' : 'all', page: '1' }),
      text: fullLog ? 'external only' : 'full log',
    }),
    actionLogSelect('Kind', 'kind', AL_KINDS, q.kind),
    actionLogSelect('Operator', 'operator', AL_OPERATORS, q.operator),
    actionLogSelect('Status', 'status', AL_VIEWS, q.status),
    actionLogSelect('Sort', 'sort', AL_SORTS, q.sort),
    h('label', { class: 'c-al-field' }, [
      h('span', { class: 'c-tb-label', text: 'date_from (epoch)' }),
      h('input', {
        class: 'c-input c-al-num', type: 'number', step: '1', value: q.date_from || '',
        'aria-label': 'date_from (epoch)',
        onChange: (e) => setActionLogQuery({ date_from: e.target.value, page: '1' }),
      }),
    ]),
    h('label', { class: 'c-al-field' }, [
      h('span', { class: 'c-tb-label', text: 'date_to (epoch)' }),
      h('input', {
        class: 'c-input c-al-num', type: 'number', step: '1', value: q.date_to || '',
        'aria-label': 'date_to (epoch)',
        onChange: (e) => setActionLogQuery({ date_to: e.target.value, page: '1' }),
      }),
    ]),
    h('button', {
      type: 'button', class: 'c-btn tiny',
      onClick: () => setActionLogQuery({ ...AL_DEFAULT_QUERY, page: '1' }),
      text: 'Clear filters',
    }),
  ])
}

function evidenceNode(row, domId) {
  const link = String(row.evidence_link || '')
  if (/^https?:\/\//.test(link)) {
    return h('a', { class: 'c-al-evidence', href: link, target: '_blank',
                    rel: 'noreferrer noopener', text: link })
  }
  return h('code', { class: 'c-al-evidence', id: domId, text: link })
}

function renderActionLogRow(row) {
  const archived = row.status === 'archived'
  const busy = state.actionLog.busy === row.action_id
  const domId = 'al-ev-' + row.action_id
  return h('tr', { class: 'c-al-row' + (archived ? ' archived' : ''), dataset: { actionId: row.action_id } }, [
    h('td', { class: 'c-al-time', 'data-label': AL_COLUMNS[0], text: fmtTime(row.timestamp) }),
    h('td', { class: 'c-al-kind', 'data-label': AL_COLUMNS[1] }, [h('span', { class: 'c-al-chip', text: String(row.kind) })]),
    h('td', { class: 'c-al-target', 'data-label': AL_COLUMNS[2], text: String(row.target || '') }),
    h('td', { class: 'c-al-evidence-cell', 'data-label': AL_COLUMNS[3] }, [
      evidenceNode(row, domId),
      h('span', { class: 'c-al-meta', text: ' [' + String(row.evidence_type) + ']' }),
    ]),
    h('td', { class: 'c-al-operator', 'data-label': AL_COLUMNS[4], text: String(row.operator_flag) }),
    h('td', { class: 'c-al-status', 'data-label': AL_COLUMNS[5], text: String(row.status) }),
    h('td', { class: 'c-al-action', 'data-label': AL_COLUMNS[6] }, [
      h('button', {
        type: 'button', class: 'c-btn tiny' + (archived ? '' : ' primary'),
        dataset: { actionId: row.action_id, verb: archived ? 'unarchive' : 'archive' },
        disabled: busy ? true : null,
        onClick: () => doArchive(row.action_id, archived ? 'unarchive' : 'archive'),
        text: busy ? '…' : (archived ? 'Unarchive' : 'Archive'),
      }),
      archived && row.archive_audit_id
        ? h('div', { class: 'c-al-meta', text: 'audit ' + String(row.archive_audit_id) })
        : null,
    ]),
  ])
}

function renderActionLogTable(actions) {
  return h('div', { class: 'c-al-table-wrap' }, [
    h('table', { class: 'c-al-table' }, [
      h('thead', {}, [h('tr', {}, ['Time', 'Kind', 'Target', 'Evidence', 'Operator', 'Status', '']
        .map((label) => h('th', { text: label })))]),
      h('tbody', {}, actions.map((row) => renderActionLogRow(row))),
    ]),
  ])
}

function renderActionLog() {
  const data = state.data || {}
  const counts = data.counts || {}
  const nodes = [
    h('section', { class: 'c-al-head' }, [
      h('h2', { class: 'c-al-title', text: 'Action log' }),
      h('p', { class: 'c-al-sub', text: 'Projection of evidence sources; each row links its source.' }),
    ]),
    h('div', { class: 'c-al-bar' }, [
      h('span', { class: 'c-al-stat', text: 'total ' + fmtCount(counts.total) }),
      // skin (§4.10): the count string is unchanged; only the figure is wrapped so the
      // bar's single cyan accent can target it ('active ' + number renders identically).
      h('span', { class: 'c-al-stat' }, [
        h('span', { text: 'active ' }),
        h('b', { class: 'cj-stat-active', text: fmtCount(counts.active) }),
      ]),
      h('span', { class: 'c-al-stat', text: 'archived ' + fmtCount(counts.archived) }),
      // Orda 2026-09-17: the scope split is a SERVER field (additive counts keys) — rendered
      // as returned, never re-derived in the browser (AL-L15).
      counts.external_total !== undefined && counts.external_total !== null
        ? h('span', { class: 'c-al-stat' }, [
          h('span', { text: 'scope ' }),
          h('b', { class: 'cj-stat-active', text: String(counts.scope || '') }),
        ])
        : null,
      counts.external_total !== undefined && counts.external_total !== null
        ? h('span', { class: 'c-al-stat', text: 'external ' + fmtCount(counts.external_total) })
        : null,
      h('span', { class: 'c-al-stat', text: 'by_kind ' + summaryText(counts.by_kind) }),
      h('span', { class: 'c-al-stat', text: 'by_operator ' + summaryText(counts.by_operator) }),
    ]),
    renderActionLogFilters(),
  ]
  if (state.actionLog.error) {
    nodes.push(renderError(state.actionLog.error))
  } else if (state.loading && !Array.isArray(data.actions)) {
    nodes.push(h('div', { class: 'c-al-empty', text: 'Loading…' }))
  } else if (!(data.actions || []).length) {
    nodes.push(h('div', { class: 'c-al-empty', text: 'No actions returned for this filter.' }))
  } else {
    nodes.push(renderActionLogTable(data.actions))
  }
  const page = parseInt(data.page, 10) || 1
  const size = parseInt(data.page_size, 10) || 0
  nodes.push(h('div', { class: 'c-al-pager' }, [
    h('button', {
      type: 'button', class: 'c-btn tiny', disabled: page <= 1 ? true : null,
      onClick: () => setActionLogQuery({ page: String(Math.max(1, page - 1)) }),
      text: 'Prev',
    }),
    h('span', { class: 'c-al-stat', text: 'page ' + page + ' · page_size ' + size }),
    h('button', {
      type: 'button', class: 'c-btn tiny', disabled: data.has_more ? null : true,
      onClick: () => setActionLogQuery({ page: String(page + 1) }),
      text: 'Next',
    }),
    h('span', { class: 'c-al-stat ' + (data.has_more ? 'cj-more-true' : 'cj-more-false'),
                 text: data.has_more ? 'has_more true' : 'has_more false' }),
  ]))
  return h('section', { class: 'c-al', id: 'action-log' }, nodes)
}

// ── render ─────────────────────────────────────────────────────────────
function render() {
  if (!els.app) return
  const drawer = renderEvidenceDrawer()
  if (state.detailId) {
    replaceApp([renderDetail(), drawer])
    focusEvidenceIfPending()
    return
  }
  if (state.view === 'action_log') {
    replaceApp([renderActionLog(), drawer])
    focusEvidenceIfPending()
    return
  }
  const data = state.data || { items: [] }
  if (state.loading && !state.data) {
    replaceApp([renderHealthStrip(data), renderMissionControl(data), renderLoading(), drawer])
    focusEvidenceIfPending()
    return
  }
  if (state.error && !state.data) {
    replaceApp([renderHealthStrip(data), renderMissionControl(data), renderError(state.error), drawer])
    focusEvidenceIfPending()
    return
  }
  const hasItems = (data.items || []).length > 0 ||
    (data.playground && data.playground.mode === 'today')
  // MC-A14: the dense grid and the lanes render the SAME returned items — one request, no
  // second envelope — so a filter change moves both consistently.
  const body = !hasItems ? renderEmpty(data)
    : (state.layout === 'grid' ? renderDenseGrid(data) : renderBoard(data))
  replaceApp([
    renderHealthStrip(data),
    renderMissionControl(data),
    renderCurrentState(data),
    attentionRail(state.attention),
    renderBands(state.staleness),
    body,
    renderInbox(state.inbox),
    renderCompletenessReceipt(data),
    renderTaskHome(data),
    drawer,
  ])
  focusEvidenceIfPending()
}

// ── boot ───────────────────────────────────────────────────────────────
function boot() {
  els.app = document.getElementById('app')
  els.live = document.getElementById('live')
  els.toastRoot = document.getElementById('toast-root')
  els.filter = document.getElementById('filter')
  els.scan = document.getElementById('scan')

  // URL is the query state. The browser default request is Today's Playground.
  try {
    if (typeof window !== 'undefined' && window.location) {
      state.query = parseUrlState(window.location.search)
    }
  } catch (e) { state.query = { ...DEFAULT_QUERY } }
  syncUrl()

  if (els.filter) {
    els.filter.placeholder = 'search (name, id, title, evidence)'
    els.filter.addEventListener('input', (e) => {
      const value = e.target.value
      state.filter = value
      render()
      if (filterTimer) clearTimeout(filterTimer)
      filterTimer = setTimeout(() => setQuery({ q: value, page: '1' }), 350)
    })
  }
  if (els.scan) els.scan.addEventListener('click', scanNow)   // explicit scan only

  buildToolbar()

  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && state.evidence) {
      // Frida UX-11/UX-25: Escape closes the drawer and returns focus to the opening control.
      e.preventDefault()
      closeEvidence()
      announce('Closed evidence.')
    }
    else if (e.key === 'Escape' && state.menuOpenFor) { closeMenu(); announce('Closed menu.') }
    else if (e.key === 'Escape' && state.openPane) {
      // D-SP-2 / A-SP-6: collapse AND return focus to the disclosure button that opened it.
      collapsePane(state.openPane)
      announce('Closed messages.')
    }
  })

  refresh().then(() => {
    pollTimer = setInterval(pollEvents, POLL_MS)
  })
}

if (typeof document !== 'undefined') boot()

export {
  state, doMove, doToggleUrgent, scanNow, openMenu, closeMenu, handleCardKeyDown, onDrop,
  fetchBoard, parseUrlState, serializeUrlState, queryString, DEFAULT_QUERY, SORT_FIELDS,
  splitList, joinList, collapsePane, paneToggleDomId, paneRefFor, LIFECYCLES,
  PANE_DISPLAY_FALLBACK, bandLabel, tierLabel, confidenceLabel, claimEntry, claimControl,
  renderDenseGrid, renderBands, renderInbox, renderMissionControl, renderTaskHome,
  attentionRail,
  openEvidence, closeEvidence, renderEvidenceDrawer, clearQuery, hasActiveFilter,
  snapshotAgeText, durationText, MC_COPY, BAND_LABELS, BAND_FILTER_VALUES, QUIET_BAND,
  GRID_COLUMNS, ATTENTION_GROUPS, ATTENTION_LIST_CAP, HOME_GROUPS, HOME_LABELS,
  renderActionLog, actionLogQueryString, setView, setActionLogQuery, doArchive,
  AL_DEFAULT_QUERY, AL_KINDS, AL_OPERATORS, AL_VIEWS, AL_SORTS,
}
