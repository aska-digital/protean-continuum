/** review.js — Owner review queue renderer (read-only, fixtures-only at page load). */
const QUEUE_URL_LIVE = '/api/plugins/continuum/review-queue'
const QUEUE_URL_FIXTURE = './review-queue.json'
const SNAPSHOT_URL = './snapshot.review-queue.json'

const GROUP_META = {
  github_drafts: { label: 'GitHub drafts waiting to go out', hint: 'a draft that needs approval to post' },
  mechanics: { label: 'Mechanics and adjustments waiting to be implemented', hint: 'proposals, consent lists, new procedures' },
  staging_promotion: { label: 'Site version waiting to be judged for promotion to live', hint: 'which tree, what changed, where served, what promotes it' },
  other_owner_actions: { label: 'Anything else waiting on the owner', hint: 'owner action outside our hands, decision with no default' },
}
const GROUP_ORDER = ['github_drafts', 'mechanics', 'staging_promotion', 'other_owner_actions']

function h(tag, attrs, children) {
  const el = document.createElement(tag)
  if (attrs) for (const [k,v] of Object.entries(attrs)) {
    if (k === 'text') el.textContent = String(v)
    else if (k === 'html') el.innerHTML = String(v)
    else if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2).toLowerCase(), v)
    else if (v !== null && v !== false) el.setAttribute(k, String(v))
  }
  if (children) for (const c of [].concat(children)) if (c) el.appendChild(typeof c==='string'?document.createTextNode(c):c)
  return el
}
function fmtUtc(iso){ try{ return new Date(iso).toISOString().replace('.000Z','Z') } catch(e){ return String(iso) } }

async function fetchQueue() {
  // Try live API first, then static fixture neighbours (fixtures-only: no credential, no live API in static build)
  const candidates = [QUEUE_URL_LIVE, SNAPSHOT_URL, QUEUE_URL_FIXTURE]
  let lastErr = null
  for (const url of candidates) {
    try {
      const res = await fetch(url, {headers:{'Accept':'application/json'}})
      if (!res.ok) { lastErr = new Error('HTTP '+res.status+' at '+url); continue }
      const data = await res.json()
      if (data && data.groups) { data._url = url; return data }
      lastErr = new Error('bad shape at '+url)
    } catch(e){ lastErr = e; }
  }
  throw lastErr || new Error('queue unavailable')
}

function renderFreshness(data) {
  const el = document.getElementById('freshness')
  const gen = data.generated_utc || 'unknown'
  const rev = data.source_revision != null ? 'rev '+String(data.source_revision) : 'rev unknown'
  const src = data.source_paths ? Object.values(data.source_paths).join(' + ') : 'sources: brief.json + DRAFT-REGISTRY.md + consent + staging'
  // stale: generated older than 24h now, or payload says is_stale
  let stale = !!data.is_stale
  let staleNote = data.stale_reason || ''
  if (!stale) {
    try {
      const genMs = Date.parse(gen)
      if (!isNaN(genMs)) {
        const ageH = (Date.now() - genMs)/3600000
        if (ageH > 24) { stale = true; staleNote = `generated ${gen} is ${ageH.toFixed(1)}h old -> stale (never present as live)` }
      }
    } catch(e){}
  }
  el.className = 'c-freshness' + (stale ? ' is-stale' : '')
  el.replaceChildren(
    h('div', {}, [
      h('strong', {text: stale ? 'STALE — not live: ' : 'Fresh: '}),
      document.createTextNode(`queue generated ${fmtUtc(gen)} UTC · ${rev} · source ${src}`)
    ]),
    stale ? h('div', {text: staleNote}) : null
  )
}

function renderGroup(key, items) {
  const meta = GROUP_META[key] || {label:key, hint:''}
  const head = h('div', {class:'c-group-head'}, [
    h('div', {}, [
      h('h2', {class:'c-group-title', text: meta.label}),
      meta.hint ? h('div', {class:'c-review-subtitle', text: meta.hint}) : null,
    ]),
    h('span', {class:'c-group-count', text: String(items.length) + ' waiting'}),
  ])
  let body
  if (!items || items.length === 0) {
    body = h('div', {class:'c-group-empty', text: 'Nothing in this group in this snapshot.'})
  } else {
    body = h('div', {}, items.map((row) => {
      const where = row.where || {}
      const href = where.href || ''
      const label = where.label || href || '—'
      // Link if looks like URL or path; otherwise plain text
      let whereNode
      if (/^https?:\/\//.test(href)) {
        whereNode = h('a', {href:href, target:'_blank', rel:'noopener', text: label})
      } else if (href && href.length > 0 && href !== label) {
        // show label + href as code path
        whereNode = h('span', {}, [
          href.startsWith('/') || href.startsWith('~') || href.startsWith('eldunari')
            ? h('code', {text: href}) : document.createTextNode(label),
          where.label && where.href && where.label !== where.href ? document.createTextNode(' — '+where.label) : null
        ])
      } else {
        whereNode = h('code', {text: label})
      }
      return h('div', {class:'c-review-row'}, [
        h('dl', {class:'c-q'}, [
          h('dt', {text:'What it is'}), h('dd', {text: row.what || '—'}),
          h('dt', {text:'If you say yes'}), h('dd', {text: row.what_if_yes || '—'}),
          h('dt', {text:'If you say nothing'}), h('dd', {text: row.what_if_nothing || '—'}),
          h('dt', {text:'Where the real thing lives'}), h('dd', {}, [whereNode]),
        ])
      ])
    }))
  }
  return h('section', {class:'c-group', 'aria-label': meta.label}, [head, body])
}

async function render() {
  const root = document.getElementById('review-app')
  root.replaceChildren(h('div', {class:'c-skeleton'}), h('div', {class:'c-skeleton'}))
  try {
    const data = await fetchQueue()
    renderFreshness(data)
    const groups = data.groups || {}
    const nodes = GROUP_ORDER.map(k => renderGroup(k, groups[k] || []))
    root.replaceChildren(...nodes)
  } catch(e) {
    const el = document.getElementById('freshness')
    el.className = 'c-freshness is-stale'
    el.textContent = 'Queue unavailable: ' + String(e && e.message || e) + ' — nothing invents, nothing guessed.'
    root.replaceChildren(h('div', {class:'c-group-empty', text: 'The queue could not be loaded from any source (live API or snapshot). No item can be shown without reading its source. See browser console for the attempted URLs: ' + [QUEUE_URL_LIVE, SNAPSHOT_URL, QUEUE_URL_FIXTURE].join(', ') + '.'}))
  }
}

document.getElementById('refresh').addEventListener('click', render)
render()
