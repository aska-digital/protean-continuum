/**
 * Continuum — desktop plugin (M8) — Hybrid Kanban board.
 *
 * Five surfaces: Overview · Board · Attention · Staleness · Noise Controls,
 * with Project Detail as a drill-down (never a tab).
 * Board is the eight-lane kanban (Inbox + 7 placements) per
 * ux-continuum-kanban-hybrid + architecture-continuum-kanban D-KB-1..D-KB-14.
 *
 * Hard constraints honoured here (architecture section 11, ux-contract section 1):
 *   - single uncompiled ESM file, NO build step, NO JSX syntax -> jsx() calls only
 *   - imports ONLY '@hermes/plugin-sdk', 'react', 'react/jsx-runtime'
 *   - no scanner/clustering logic in JS; data arrives as M7 JSON from /api/plugins/continuum
 *   - theme variables only (no #hex / rgb()), live updates are an enhancement ON TOP of
 *     mandatory refetchInterval polling (ctx.socket is a no-op on OAuth remotes)
 *   - every card renders the single provenance chip T{evidence_tier}·{confidence_band}
 *     with unknown/T0 first-class and tier-explained tooltip (ux revision section 2 + approval B2.1-B2.3)
 *
 * `defaultEnabled: false` -> opt-in: it inventories in Settings -> Plugins, off until enabled.
 */
import { useState, useRef, useEffect } from 'react'
import { jsx } from 'react/jsx-runtime'
import {
  host,
  ROUTES_AREA,
  SIDEBAR_NAV_AREA,
  PALETTE_AREA,
  useQuery,
  useMutation,
  useQueryClient,
  Badge,
  Button,
  CopyButton,
  EmptyState,
  ErrorState,
  SearchField,
  ScrollArea,
  Separator,
  Skeleton,
  Codicon,
} from '@hermes/plugin-sdk'
import { BOARD_COLUMNS, COLUMN_LABELS, NON_INBOX_PLACEMENTS, classifyKeyEvent, canDropOnColumn, closeMenuFocusTarget, buildAcceptToast, buildAcceptActions, buildFailureToast, buildFailureActions, buildUndoToast, navigateRight, navigateLeft, navigateDown, navigateUp, navigateHome, navigateEnd, copyIdValue, menuItemsFor, menuInitialHighlight, menuHighlightIndex, menuActivate } from './kanban-interaction.js'

const API = '/api/plugins/continuum'
const POLL_MS = 15000
let pluginCtx = null

async function api(path, options) {
  if (!pluginCtx || typeof pluginCtx.rest !== 'function') throw new Error('Continuum backend transport unavailable')
  const request = { ...(options || {}) }
  if (typeof request.body === 'string') {
    try { request.body = JSON.parse(request.body) } catch (e) {}
  }
  const data = await pluginCtx.rest(path, request)
  return {
    ok: true,
    status: 200,
    json: async () => data,
    text: async () => typeof data === 'string' ? data : JSON.stringify(data),
  }
}

const PAGE = '/continuum'

const SURFACES = [
  { id: 'overview', label: 'Overview', path: PAGE + '?view=overview' },
  { id: 'board', label: 'Board', path: PAGE + '?view=board' },
  { id: 'attention', label: 'Attention', path: PAGE + '?view=attention' },
  { id: 'stale', label: 'Staleness', path: PAGE + '?view=stale' },
  { id: 'noise', label: 'Noise', path: PAGE + '?view=noise' },
]

const COLUMN_LABEL_LIST = ['Inbox', 'Ongoing', 'Blocked', 'Waiting on you', 'Paused', 'Done', 'Shipped', 'Scrapped']
const EMPTY_COPY = {
  inbox: 'Inbox is empty.',
  ongoing: 'Nothing in progress yet.',
  blocked: 'Nothing is blocked.',
  waiting_on_you: 'Nothing is waiting on you.',
  paused: 'Nothing paused.',
  done: 'Nothing finished yet.',
  shipped: 'Nothing shipped yet.',
  scrapped: 'Nothing scrapped.',
}

// stacked reflow and reduced-motion styles (UX ruling F-5 / A7 A8) — breakpoint is lane-width condition 8*260+7*12=2164
if (typeof document !== 'undefined' && !document.getElementById('continuum-kanban-styles')) {
  const s = document.createElement('style')
  s.id = 'continuum-kanban-styles'
  s.textContent = `
.continuum-board-grid { display: grid; grid-template-columns: repeat(8, minmax(260px, 1fr)); gap: 12px; overflow-x: auto; }
.continuum-board-grid .kanban-lane { min-width: 260px; }
.continuum-board-grid .kanban-lane-header { position: sticky; top: 0; background: var(--ui-bg); z-index: 1; }
@media (max-width: 2164px) {
  .continuum-board-grid { grid-template-columns: 1fr; overflow-x: hidden; }
  .continuum-board-grid .kanban-lane { min-width: 0; }
}
@media (prefers-reduced-motion: reduce) {
  .continuum-board-grid * { animation: none !important; transition: none !important; scroll-behavior: auto !important; }
  .kanban-card { animation: none !important; transition: none !important; }
}
/* roving tabindex focus frame */
.kanban-card:focus { outline: 2px solid var(--ui-accent); outline-offset: 2px; }
.kanban-card:focus-visible { outline: 2px solid var(--ui-accent); }
`
  document.head.appendChild(s)
}

function fmtAge(days) {
  if (days === null || days === undefined) return 'quiet unknown'
  if (days < 1) return 'today'
  if (days < 2) return '1d quiet'
  return Math.round(days) + 'd quiet'
}

function fmtTime(epochSeconds) {
  if (!epochSeconds) return 'unknown'
  try {
    return new Date(epochSeconds * 1000).toLocaleString()
  } catch (e) {
    return 'unknown'
  }
}

function tierExplanation(tier) {
  if (tier === 0 || tier === '0') return 'T0 — deterministic metadata'
  if (tier === 1 || tier === '1') return 'T1 — rule-extracted'
  if (tier === 2 || tier === '2') return 'T2 — model-inferred'
  return 'T? — unknown tier'
}

function ProvenanceChip({ confidenceBand, evidenceTier, confidence }) {
  const band = confidenceBand || 'unknown'
  const tier = evidenceTier === null || evidenceTier === undefined ? '0' : String(evidenceTier)
  const label = 'T' + tier + ' \u00b7 ' + band
  const tip = tierExplanation(tier) + (confidence !== null && confidence !== undefined ? ' \u00b7 confidence ' + confidence : '') + ' \u00b7 ' + band
  return jsx(Badge, {
    variant: 'muted',
    title: tip,
    'aria-label': tip,
    children: label,
  })
}

function ResumeLink({ link, title }) {
  const command = link.copy_command_profile_scoped || link.copy_command
  const profile = link.profile_name || link.profile || 'unknown profile'
  const sessionId = link.session_id || 'unknown session'
  return jsx('span', {
    style: { display: 'inline-flex', gap: '6px', alignItems: 'center', flexWrap: 'wrap' },
    children: [
      jsx('button', {
        key: 'go',
        type: 'button',
        style: { background: 'transparent', border: 'none', color: 'var(--ui-accent)', cursor: link.route ? 'pointer' : 'not-allowed', padding: 0, font: 'inherit' },
        disabled: !link.route,
        title: link.route ? 'Open the source session' : 'Route pending verification — use the copy command',
        onClick: () => { if (link.route) host.navigate(link.route) },
        children: title || ('untitled · ' + sessionId.slice(0, 12)),
      }),
      jsx(CopyButton, { key: 'copy', text: command, label: 'resume cmd' }),
      jsx('code', { key: 'id', style: { color: 'var(--ui-text-quaternary)', fontSize: '11px' }, children: profile + '/' + sessionId }),
    ],
  })
}

function KanbanCard({ card, onOpen, onMove, onToggleUrgent, isPending, isFocused, tabIndex, onCardFocus, onCardKeyDown, menuOpen, onMenuToggle, onMenuSelect, onMenuClose, draggableProps, menuItems, menuHighlightIdx, onMenuHighlightChange }) {
  const quietLabel = card.quiet_days === null || card.quiet_days === undefined ? 'quiet unknown' : fmtAge(card.quiet_days)
  const stallLabel = card.stall_age_days !== null && card.stall_age_days !== undefined ? fmtAge(card.stall_age_days) : quietLabel
  const lifecycleName = card.lifecycle_name || card.derived_lifecycle_name || 'unknown'
  const derivedName = card.derived_lifecycle_name || lifecycleName
  const showDerivedHint = card.column && card.derived_lifecycle && card.column !== 'inbox' && card.derived_lifecycle_name && card.placement_source === 'human'
  const urgent = !!card.urgent
  const name = card.name || '(untitled cluster)'
  const cardAria = name + ' \u00b7 ' + (urgent ? 'urgent \u00b7 ' : '') + lifecycleName + ' \u00b7 ' + stallLabel + ' \u00b7 ' + (card.review_status_label || card.placement_source || '')
  const primarySession = (card.sessions && card.sessions[0]) || null
  const summaryText = card.summary_text || ''
  const hasPrimary = !!primarySession
  const reviewBadgeVariant = card.review_status === 'accepted' ? 'success' : 'default'
  const reviewBadgeLabel = card.review_status === 'accepted' ? 'Accepted' : 'Candidate'
  const needsReview = !!card.needs_review
  const cardId = 'kanban-card-' + card.project_id
  const copyId = copyIdValue(card)
  return jsx('div', {
    id: cardId,
    role: 'listitem',
    tabIndex: tabIndex,
    'aria-label': cardAria,
    'aria-busy': isPending ? 'true' : undefined,
    className: 'kanban-card',
    draggable: true,
    onDragStart: (e) => { e.dataTransfer.setData('text/plain', card.project_id); if (draggableProps && draggableProps.onDragStart) draggableProps.onDragStart(); if (onCardFocus) onCardFocus(card.project_id) },
    onDragEnd: () => { if (draggableProps && draggableProps.onDragEnd) draggableProps.onDragEnd() },
    onFocus: () => { if (onCardFocus) onCardFocus(card.project_id) },
    onKeyDown: (e) => { if (onCardKeyDown) onCardKeyDown(e, card) },
    onClick: (e) => { /* Enter on focused card opens Detail via onKeyDown; click on card background also opens detail */ },
    style: {
      border: '1px solid var(--ui-stroke-secondary)',
      borderRadius: '6px',
      padding: '10px',
      marginBottom: '10px',
      opacity: isPending ? 0.6 : 1,
      cursor: 'grab',
      background: 'var(--ui-bg)',
    },
    children: [
      jsx('div', { key: 'r1', style: { display: 'flex', gap: '8px', alignItems: 'center', flexWrap: 'wrap' }, children: [
        jsx('button', {
          key: 'name',
          type: 'button',
          style: { background: 'transparent', border: 'none', color: 'var(--ui-text)', cursor: 'pointer', textAlign: 'left', font: 'inherit', fontSize: '14px', fontWeight: 600 },
          onClick: () => onOpen(card.project_id),
          title: name,
          'aria-label': name,
          children: name,
        }),
        jsx(Badge, { key: 'review', variant: reviewBadgeVariant, title: card.review_status_label || '', 'aria-label': card.review_status_label || reviewBadgeLabel, children: reviewBadgeLabel }),
        needsReview ? jsx(Badge, { key: 'needs', variant: 'warn', title: 'Needs review', children: 'Needs review' }) : null,
        urgent ? jsx(Badge, { key: 'urgchip', variant: 'warn', children: 'Urgent' }) : null,
        jsx('span', { key: 'spacer', style: { flex: 1 } }),
        jsx(ProvenanceChip, { key: 'prov', confidenceBand: card.confidence_band, evidenceTier: card.evidence_tier, confidence: card.confidence }),
      ]}),
      jsx('div', { key: 'r2', style: { color: 'var(--ui-text-secondary)', fontSize: '12px', paddingTop: '4px' }, children: [
        'Waiting on: ' + lifecycleName + ' \u00b7 ' + stallLabel,
        showDerivedHint ? ' \u00b7 derived: ' + derivedName : '',
      ].join('')}),
      jsx('div', {
        key: 'r3',
        title: summaryText,
        'aria-label': summaryText,
        style: { paddingTop: '4px', display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden', fontSize: '13px', lineHeight: '1.4' },
        children: summaryText,
      }),
      hasPrimary
        ? jsx('div', { key: 'r4', style: { display: 'flex', gap: '8px', alignItems: 'center', flexWrap: 'wrap', paddingTop: '6px' }, children: [
            jsx('button', {
              key: 'sess',
              type: 'button',
              style: { background: 'transparent', border: 'none', color: 'var(--ui-accent)', cursor: 'pointer', padding: 0, font: 'inherit', fontSize: '13px' },
              onClick: () => onOpen(card.project_id),
              title: primarySession.title || primarySession.session_id,
              children: (primarySession.title || ('untitled \u00b7 ' + primarySession.session_id.slice(0, 12))) + ' \u25b8',
            }),
            jsx('code', { key: 'id', style: { color: 'var(--ui-text-quaternary)', fontSize: '11px' }, children: primarySession.profile + '/' + primarySession.session_id }),
            jsx(CopyButton, { key: 'copy', text: copyId.text, label: copyId.label }),
            card.sessions_omitted ? jsx('button', {
              key: 'omit',
              type: 'button',
              style: { background: 'transparent', border: 'none', color: 'var(--ui-text-quaternary)', cursor: 'pointer', padding: 0, font: 'inherit', fontSize: '11px' },
              onClick: () => onOpen(card.project_id),
              children: '+ ' + card.sessions_omitted + ' more sessions \u25b8',
            }) : null,
          ]})
        : jsx('div', { key: 'r4', style: { display: 'flex', gap: '8px', alignItems: 'center', flexWrap: 'wrap', paddingTop: '6px' }, children: [
            card.session_count !== undefined ? jsx('span', { key: 'sc', style: { color: 'var(--ui-text-quaternary)', fontSize: '11px' }, children: card.session_count + ' sessions' }) : null,
            card.project_id ? jsx(CopyButton, { key: 'copy', text: copyId.text, label: copyId.label }) : null,
            !card.session_count && !card.project_id ? jsx('span', { key: 'nolink', style: { color: 'var(--ui-text-secondary)', fontSize: '12px' }, children: 'no linked session' }) : null,
          ]}),
      jsx('div', { key: 'move', style: { paddingTop: '6px', display: 'flex', gap: '4px', flexWrap: 'wrap', alignItems: 'center' }, children: [
        jsx('button', {
          key: 'movetrigger',
          type: 'button',
          'aria-label': 'Move to…',
          'aria-expanded': menuOpen ? 'true' : 'false',
          'aria-haspopup': 'menu',
          disabled: isPending,
          onClick: () => onMenuToggle(card.project_id),
          style: { background: 'transparent', border: '1px solid var(--ui-stroke-secondary)', borderRadius: '4px', padding: '2px 8px', cursor: 'pointer', fontSize: '11px', color: 'var(--ui-text-secondary)' },
          children: 'Move to…',
        }),
        jsx('button', {
          key: 'urg',
          type: 'button',
          'aria-pressed': urgent ? 'true' : 'false',
          'aria-label': urgent ? 'Remove urgent' : 'Mark urgent',
          title: urgent ? 'Remove urgent' : 'Mark urgent',
          onClick: () => onToggleUrgent(card),
          style: { background: 'transparent', border: '1px solid var(--ui-stroke-secondary)', borderRadius: '4px', padding: '2px 6px', cursor: 'pointer', fontSize: '11px', color: urgent ? 'var(--ui-accent)' : 'var(--ui-text-secondary)' },
          children: urgent ? 'Urgent \u2713' : 'Mark urgent',
        }),
      ]}),
      menuOpen && menuItems ? jsx('div', { key: 'menu', role: 'menu', 'aria-label': 'Move to…', style: { border: '1px solid var(--ui-stroke-secondary)', borderRadius: '6px', padding: '4px', marginTop: '6px', background: 'var(--ui-bg)', display: 'flex', flexDirection: 'column', gap: '2px' }, children: menuItems.map((item, idx) => {
        const isHighlighted = idx === menuHighlightIdx
        const menuId = 'kanban-menu-' + card.project_id + '-' + (item.kind === 'cancel' ? 'cancel' : item.target)
        return jsx('button', {
          key: item.kind === 'cancel' ? 'cancel' : item.target,
          id: menuId,
          type: 'button',
          role: 'menuitem',
          tabIndex: isHighlighted ? 0 : -1,
          'aria-checked': item.kind === 'target' && card.column === item.target ? 'true' : 'false',
          onClick: () => {
            const action = menuActivate(item)
            if (action === 'move') { onMenuSelect(card, item.target); onMenuClose() }
            else if (action === 'close') { onMenuClose() }
          },
          onKeyDown: (e) => {
            if (e.key === 'Escape') { e.stopPropagation(); onMenuClose() }
            else if (e.key === 'Enter') {
              e.preventDefault(); e.stopPropagation()
              const action = menuActivate(item)
              if (action === 'move') { onMenuSelect(card, item.target); onMenuClose() }
              else if (action === 'close') { onMenuClose() }
            }
            else if (e.key === 'ArrowDown' || e.key === 'ArrowUp' || e.key === 'Home' || e.key === 'End') {
              e.preventDefault(); e.stopPropagation()
              const newIdx = menuHighlightIndex(menuItems, menuHighlightIdx, e.key)
              onMenuHighlightChange(newIdx)
              requestAnimationFrame(() => {
                const el = document.getElementById('kanban-menu-' + card.project_id + '-' + (menuItems[newIdx].kind === 'cancel' ? 'cancel' : menuItems[newIdx].target))
                if (el) el.focus()
              })
            }
            else if (e.key === ' ') {
              e.preventDefault(); e.stopPropagation()
            }
          },
          style: { background: isHighlighted ? 'var(--ui-accent)' : 'transparent', color: isHighlighted ? 'var(--ui-text-on-accent)' : 'var(--ui-text)', border: 'none', borderRadius: '4px', padding: '6px 8px', cursor: 'pointer', fontSize: '12px', textAlign: 'left' },
          children: item.kind === 'cancel' ? 'Cancel (Esc)' : (card.column === item.target ? '\\u2713 ' : '') + COLUMN_LABELS[item.target],
        })
      }) }) : null,
    ],
  })
}

function CompactCard({ it, onOpen, duplicateNames }) {
  const primary = (it.sessions && it.sessions[0]) || null
  const quietLabel = it.quiet_days === null || it.quiet_days === undefined ? 'quiet unknown' : fmtAge(it.quiet_days)
  const quietTitle = it.quiet_since ? new Date(it.quiet_since * 1000).toLocaleString() : 'quiet unknown'
  const needsReview = !!it.needs_review
  const showContext = duplicateNames && duplicateNames.has(it.name) && it.context_line
  const cardAria = (it.name || '(untitled)') + ' \u00b7 ' + (it.lifecycle_name || 'unknown') + ' \u00b7 ' + quietLabel + ' \u00b7 ' + (it.review_status_label || '') + ' \u00b7 ' + (it.inclusion_basis || '')
  const badgeVariant = it.review_status === 'accepted' ? 'success' : 'default'
  const badgeLabel = it.review_status === 'accepted' ? 'Accepted' : 'Candidate'
  const summaryText = it.summary_text || ''
  return jsx('div', {
    role: 'listitem',
    'aria-label': cardAria,
    style: { border: '1px solid var(--ui-stroke-secondary)', borderRadius: '6px', padding: '10px', marginBottom: '10px' },
    children: [
      jsx('div', { key: 'r1', style: { display: 'flex', gap: '8px', alignItems: 'center', flexWrap: 'wrap' }, children: [
        jsx('button', {
          key: 'name',
          type: 'button',
          style: { background: 'transparent', border: 'none', color: 'var(--ui-text)', cursor: 'pointer', textAlign: 'left', font: 'inherit', fontSize: '15px', fontWeight: 600 },
          onClick: () => onOpen(it.project_id),
          title: it.name,
          'aria-label': it.name,
          children: it.name || '(untitled cluster)',
        }),
        jsx(Badge, { key: 'badge', variant: badgeVariant, title: it.review_status_label, 'aria-label': it.review_status_label, children: badgeLabel }),
        needsReview ? jsx(Badge, { key: 'nr', variant: 'warn', children: 'Needs review' }) : null,
        jsx('span', { key: 'spacer', style: { flex: 1 } }),
        jsx(ProvenanceChip, { key: 'prov', confidenceBand: it.confidence_band, evidenceTier: it.evidence_tier, confidence: it.confidence }),
      ]}),
      showContext ? jsx('div', { key: 'ctx', style: { color: 'var(--ui-text-secondary)', fontSize: '12px', paddingTop: '2px' }, children: it.context_line }) : null,
      jsx('div', { key: 'r2', style: { color: 'var(--ui-text-secondary)', fontSize: '12px', paddingTop: '4px' }, children: [
        'Waiting on: ' + (it.lifecycle_name || 'unknown'),
        ' \u00b7 ',
        it.quiet_since
          ? jsx('time', { key: 't', dateTime: new Date(it.quiet_since * 1000).toISOString(), title: quietTitle, children: quietLabel })
          : quietLabel,
      ]}),
      jsx('div', {
        key: 'r3',
        title: summaryText,
        'aria-label': summaryText,
        style: { paddingTop: '4px', display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden', fontSize: '13px', lineHeight: '1.4' },
        children: summaryText,
      }),
      primary
        ? jsx('div', { key: 'r4', style: { display: 'flex', gap: '8px', alignItems: 'center', flexWrap: 'wrap', paddingTop: '6px' }, children: [
            jsx('button', {
              key: 'sess',
              type: 'button',
              style: { background: 'transparent', border: 'none', color: 'var(--ui-accent)', cursor: 'pointer', padding: 0, font: 'inherit', fontSize: '13px' },
              onClick: () => onOpen(it.project_id),
              title: primary.title || primary.session_id,
              children: (primary.title || ('untitled \u00b7 ' + primary.session_id.slice(0, 12))) + ' \u25b8',
            }),
            jsx('code', { key: 'id', style: { color: 'var(--ui-text-quaternary)', fontSize: '11px' }, children: primary.profile + '/' + primary.session_id }),
            jsx(CopyButton, { key: 'copy', text: primary.session_id, label: 'Copy ID' }),
            it.sessions_omitted ? jsx('button', {
              key: 'omit',
              type: 'button',
              style: { background: 'transparent', border: 'none', color: 'var(--ui-text-quaternary)', cursor: 'pointer', padding: 0, font: 'inherit', fontSize: '11px' },
              onClick: () => onOpen(it.project_id),
              children: '+ ' + it.sessions_omitted + ' more sessions \u25b8',
            }) : null,
          ]})
        : jsx('div', { key: 'r4', style: { color: 'var(--ui-text-secondary)', fontSize: '12px', paddingTop: '6px' }, children: 'no linked session' }),
    ],
  })
}

function ProjectRow({ card, onOpen }) {
  return jsx('div', {
    role: 'row',
    style: {
      display: 'grid',
      gridTemplateColumns: '2fr 1fr 1fr 1fr auto',
      gap: '10px',
      alignItems: 'center',
      padding: '8px 10px',
      borderBottom: '1px solid var(--ui-stroke-secondary)',
    },
    children: [
      jsx('button', {
        key: 'name',
        type: 'button',
        style: { background: 'transparent', border: 'none', color: 'var(--ui-text)', cursor: 'pointer', textAlign: 'left', font: 'inherit', fontSize: '15px' },
        onClick: () => onOpen(card.project_id),
        children: card.name,
      }),
      jsx(ProvenanceChip, { key: 'prov', confidenceBand: card.confidence_band, evidenceTier: card.evidence_tier, confidence: card.confidence }),
      jsx('span', { key: 'life', style: { color: 'var(--ui-text-secondary)' }, children: card.lifecycle_name }),
      jsx('span', { key: 'stall', style: { color: 'var(--ui-text)' }, children: fmtAge(card.stall_age_days) }),
      jsx('span', { key: 'na', style: { color: 'var(--ui-text-secondary)', fontSize: '12px' }, children: card.next_action ? card.next_action.text + ' (' + card.next_action.source + ')' : 'no next action' }),
    ],
  })
}

function useContinum(path) {
  const q = useQuery({
    queryKey: ['continuum', path],
    queryFn: async () => (await api(path)).json(),
    refetchInterval: POLL_MS,
  })
  useEffect(() => {
    if (!pluginCtx || typeof pluginCtx.socket !== 'function') return undefined
    return pluginCtx.socket('/events', () => { q.refetch() })
  }, [path])
  return q
}

function DataAsOf({ data }) {
  if (!data || !data.data_as_of) return null
  return jsx('div', {
    style: { color: 'var(--ui-text-quaternary)', fontSize: '11px', padding: '6px 0' },
    children: [
      'data as of ',
      jsx('time', { key: 't', dateTime: new Date(data.data_as_of * 1000).toISOString(), children: fmtTime(data.data_as_of) }),
    ],
  })
}

function AttentionSurface({ onOpen }) {
  const q = useContinum('/attention')
  if (q.isLoading) return jsx('div', { children: Array.from({ length: 4 }, (_, i) => jsx(Skeleton, { key: i, className: 'h-16 w-full mb-2' })) })
  if (q.error) return jsx(ErrorState, { title: 'Failed to load', description: String(q.error.message || q.error), children: jsx(Button, { onClick: () => q.refetch(), children: 'Retry' }) })
  const groups = (q.data && q.data.groups) || {}
  const order = ['awaiting_user', 'blocked', 'stale_active']
  const total = order.reduce((n, k) => n + ((groups[k] || []).length), 0)
  if (total === 0) {
    return jsx(EmptyState, { title: 'Nothing is waiting on you.', description: 'No project is awaiting your input, blocked, or stale-and-drive-expected.' })
  }
  return jsx('div', {
    children: [...order.map((key) => {
      const items = groups[key] || []
      if (items.length === 0) return null
      return jsx('div', {
        key: key,
        style: { marginBottom: '16px' },
        children: [
          jsx('div', { key: 'h', style: { fontWeight: 600, padding: '6px 0' }, children: key.replace('_', ' ') + ' \u00b7 ' + items.length }),
          ...items.map((it) => jsx('div', {
            key: it.project_id,
            style: { display: 'flex', gap: '10px', alignItems: 'center', padding: '6px 0', borderBottom: '1px solid var(--ui-stroke-secondary)' },
            children: [
              jsx(Badge, { key: 'r', variant: 'warn', children: it.reason }),
              jsx('button', {
                key: 'n', type: 'button',
                style: { background: 'transparent', border: 'none', color: 'var(--ui-text)', cursor: 'pointer', font: 'inherit' },
                onClick: () => onOpen(it.project_id),
                children: it.card.name,
              }),
              jsx(ProvenanceChip, { key: 'p', confidenceBand: it.confidence_band, evidenceTier: it.card.evidence_tier, confidence: it.card.confidence }),
            ],
          })),
        ],
      })
    }),
      jsx(DataAsOf, { key: 'asof', data: q.data }),
    ],
  })
}

function BoardSurface({ onOpen }) {
  const q = useContinum('/projects')
  const queryClient = useQueryClient()
  const [pending, setPending] = useState({})
  const [dragId, setDragId] = useState(null)
  const [announce, setAnnounce] = useState('')
  const [focusedId, setFocusedId] = useState(null)
  const [menuOpenFor, setMenuOpenFor] = useState(null)
  const [menuHighlightIdx, setMenuHighlightIdx] = useState(0)
  const isLoadingFirst = q.isLoading && !q.data
  const isRefreshing = q.isFetching && !!q.data

  const focusCard = (pid) => {
    setFocusedId(pid)
    requestAnimationFrame(() => {
      const el = typeof document !== 'undefined' ? document.getElementById('kanban-card-' + pid) : null
      if (el) el.focus({ preventScroll: false })
    })
  }

  const closeMenu = () => {
    const target = closeMenuFocusTarget(menuOpenFor)
    setMenuOpenFor(null)
    setMenuHighlightIdx(0)
    if (target) focusCard(target)
  }

  const doMove = async (card, target) => {
    if (card.column === target) return
    if (target === 'inbox') return
    const pid = card.project_id
    const origin = card.column
    const isInboxExit = origin === 'inbox'
    setPending((m) => ({ ...m, [pid]: target }))
    setAnnounce('Moving \u201c' + (card.name || pid) + '\u201d to ' + COLUMN_LABELS[target] + '.')
    try {
      const action = isInboxExit ? 'accept' : 'set_placement'
      const payload = isInboxExit ? { lifecycle: 'LS-1', placement: target } : { placement: target }
      const res = await api('/projects/' + pid + '/review', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: action, payload: payload }),
      })
      if (!res.ok) {
        const txt = await res.text().catch(() => '')
        throw new Error(txt || 'move failed: HTTP ' + res.status)
      }
      const body = await res.json()
      queryClient.invalidateQueries({ queryKey: ['continuum'] })
      setMenuOpenFor(null)
      if (isInboxExit) {
        host.notify({ kind: 'success', message: buildAcceptToast(card.name || pid, target), action: { label: buildAcceptActions(body.audit_id).label, onClick: async () => {
          try {
            const ures = await api('/review/undo', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ audit_id: body.audit_id }) })
            if (!ures.ok) throw new Error('undo failed')
            queryClient.invalidateQueries({ queryKey: ['continuum'] })
            host.notify({ kind: 'info', message: buildUndoToast(card.name || pid) })
            focusCard(pid)
            setAnnounce('Undone. \u201c' + (card.name || pid) + '\u201d is back in Inbox.')
          } catch (e) { host.notify({ kind: 'error', message: String(e.message || e) }) }
        } } })
        setAnnounce('Moved \u201c' + (card.name || pid) + '\u201d from ' + COLUMN_LABELS[origin] + ' to ' + COLUMN_LABELS[target] + '.')
      } else {
        setAnnounce('Moved \u201c' + (card.name || pid) + '\u201d from ' + COLUMN_LABELS[origin] + ' to ' + COLUMN_LABELS[target] + '.')
      }
      // focus follows the moved card in its new lane
      setTimeout(() => focusCard(pid), 50)
    } catch (err) {
      host.notify({ kind: 'error', message: buildFailureToast(card.name || pid, origin), action: { label: buildFailureActions().label, onClick: () => doMove(card, target) } })
      setAnnounce('Couldn\u2019t move \u201c' + (card.name || pid) + '\u201d.')
      focusCard(pid)
    } finally {
      setPending((m) => { const n = { ...m }; delete n[pid]; return n })
    }
  }

  const doToggleUrgent = async (card) => {
    const pid = card.project_id
    const nextVal = !card.urgent
    setPending((m) => ({ ...m, [pid + ':urgent']: true }))
    try {
      const res = await api('/projects/' + pid + '/review', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'set_urgent', payload: { value: nextVal } }),
      })
      if (!res.ok) throw new Error('urgent toggle failed: HTTP ' + res.status)
      const body = await res.json()
      queryClient.invalidateQueries({ queryKey: ['continuum'] })
      setAnnounce(nextVal ? 'Marked \u201c' + (card.name || pid) + '\u201d urgent.' : 'Removed urgent from \u201c' + (card.name || pid) + '\u201d.')
      host.notify({ kind: 'info', message: nextVal ? '\u201c' + (card.name || pid) + '\u201d marked urgent.' : 'Urgent removed from \u201c' + (card.name || pid) + '\u201d.', action: { label: 'Undo', onClick: async () => {
        try { await api('/review/undo', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ audit_id: body.audit_id }) }); queryClient.invalidateQueries({ queryKey: ['continuum'] }) } catch (e) {}
      }}})
    } catch (err) {
      host.notify({ kind: 'error', message: String(err.message || err) })
    } finally {
      setPending((m) => { const n = { ...m }; delete n[pid + ':urgent']; return n })
    }
  }

  const handleBoardKeyDown = (e, laneItems, laneIdx, cardIdx, allByColumn) => {
    // roving tabindex and board keyboard behavior — delegates to shared helpers
    // Enter → Detail is handled in handleCardKeyDown (Shayba C-1 supersession)
    let result = null
    if (e.key === 'ArrowRight') {
      e.preventDefault()
      result = navigateRight(laneItems, laneIdx, cardIdx, allByColumn)
    } else if (e.key === 'ArrowLeft') {
      e.preventDefault()
      result = navigateLeft(laneItems, laneIdx, cardIdx, allByColumn)
    } else if (e.key === 'ArrowDown') {
      e.preventDefault()
      result = navigateDown(laneItems, cardIdx)
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      result = navigateUp(laneItems, cardIdx)
    } else if (e.key === 'Home') {
      e.preventDefault()
      result = navigateHome(laneItems)
    } else if (e.key === 'End') {
      e.preventDefault()
      result = navigateEnd(laneItems)
    } else if (e.key === ' ') {
      // Space on focused card opens Move-to menu (Shayba ruling: Enter → Detail, Space → Menu)
      e.preventDefault()
      const card = laneItems[cardIdx]
      if (!card) return
      if (menuOpenFor === card.project_id) {
        closeMenu()
      } else {
        const items = menuItemsFor(card)
        setMenuOpenFor(card.project_id)
        setMenuHighlightIdx(menuInitialHighlight(items))
        setAnnounce('Opened Move to menu for \u201c' + (card.name || card.project_id) + '\u201d.')
        requestAnimationFrame(() => {
          const openIdx = menuInitialHighlight(items)
          const el = document.getElementById('kanban-menu-' + card.project_id + '-' + (items[openIdx].kind === 'cancel' ? 'cancel' : items[openIdx].target))
          if (el) el.focus()
        })
      }
    } else if (e.key === 'Escape') {
      if (menuOpenFor) {
        e.preventDefault()
        e.stopPropagation()
        closeMenu()
        setAnnounce('Closed menu.')
      }
    }
    if (result) {
      focusCard(result.focusedId)
      if (result.column) {
        setAnnounce('Moved focus to ' + COLUMN_LABELS[result.column] + ' column.')
      }
    }
  }

  const handleCardKeyDown = (e, card, laneItems, laneIdx, cardIdx, allByColumn) => {
    // Delegate to shared classifyKeyEvent for pure key → action mapping
    const classification = classifyKeyEvent(e.key, menuOpenFor, card.project_id)
    switch (classification.action) {
      case 'closeMenu':
        e.preventDefault()
        e.stopPropagation()
        closeMenu()
        return
      case 'openDetail':
        e.preventDefault()
        onOpen(card.project_id)
        return
      case 'openMenu':
        // Space on a card — open the menu for this card (handled by board via menuOpenFor)
        // fall through to board handler
        break
      case 'none':
        // Enter when menu is open, or unhandled key — pass to board
        break
      default:
        // Arrow keys, Home, End — pass to board handler
        break
    }
    handleBoardKeyDown(e, laneItems, laneIdx, cardIdx, allByColumn)
  }

  // accept hint announcement: once per Inbox card
  useEffect(() => {
    if (q.data && q.data.items) {
      const inboxCards = q.data.items.filter((c) => c.column === 'inbox')
      if (inboxCards.length > 0) {
        // aria-live announcement for Inbox hint is handled via per-card announcement on focus
      }
    }
  }, [q.data])

  if (isLoadingFirst) {
    return jsx('div', { children: [
      jsx('div', { key: 'live', 'aria-live': 'polite', style: { position: 'absolute', left: '-9999px' }, children: announce }),
      jsx('div', { key: 'lanes', className: 'continuum-board-grid', children: BOARD_COLUMNS.map((col) => jsx('div', { key: col, role: 'region', 'aria-label': COLUMN_LABELS[col] + ' column, loading', className: 'kanban-lane', style: { border: '1px solid var(--ui-stroke-secondary)', borderRadius: '6px', padding: '8px', minHeight: '120px' }, children: [
        jsx('div', { key: 'h', className: 'kanban-lane-header', style: { fontWeight: 600, fontSize: '12px', textTransform: 'uppercase', letterSpacing: '0.04em', color: 'var(--ui-text-secondary)', paddingBottom: '6px' }, children: COLUMN_LABELS[col] }),
        ...Array.from({ length: 3 }, (_, i) => jsx(Skeleton, { key: i, className: 'h-24 w-full mb-2' })),
      ]}))}),
    ]})
  }
  if (q.error) {
    return jsx('div', { children: [
      jsx(ErrorState, { key: 'e', title: 'Failed to load', description: String(q.error.message || q.error), children: jsx(Button, { onClick: () => q.refetch(), children: 'Retry' }) }),
      q.data ? jsx(DataAsOf, { key: 'asof', data: q.data }) : null,
      jsx('div', { key: 'stale', style: { color: 'var(--ui-text-quaternary)', fontSize: '11px' }, children: q.data && q.data.data_as_of ? 'last good data as of ' + fmtTime(q.data.data_as_of) + ' (stale)' : '' }),
    ]})
  }
  const data = q.data || {}
  const items = data.items || []
  const counts = data.counts || {}
  if (items.length === 0) {
    return jsx('div', { children: [
      jsx(EmptyState, { key: 'e', title: 'Nothing on the board yet.', description: 'Run a scan to find candidate projects. Sessions are read-only — nothing is modified.' }),
      jsx('div', { key: 'scan', style: { paddingTop: '8px' }, children: jsx(Button, { onClick: () => { api('/scan', { method: 'POST' }).then(() => queryClient.invalidateQueries({ queryKey: ['continuum'] })) }, children: 'Run scan now' }) }),
      jsx(DataAsOf, { key: 'd', data: data }),
    ]})
  }
  const byColumn = {}
  for (const col of BOARD_COLUMNS) byColumn[col] = []
  for (const card of items) {
    const col = card.column || 'inbox'
    if (!byColumn[col]) byColumn[col] = []
    const pTarget = pending[card.project_id]
    const displayCol = pTarget || col
    if (!byColumn[displayCol]) byColumn[displayCol] = []
    byColumn[displayCol].push({ ...card, _displayPending: !!pTarget })
  }

  // roving tabindex: one tab stop per lane (first card or last focused)
  const tabStops = {}
  for (const col of BOARD_COLUMNS) {
    const lane = byColumn[col] || []
    if (lane.length === 0) continue
    const focusedInLane = lane.find((c) => c.project_id === focusedId)
    tabStops[col] = focusedInLane ? focusedInLane.project_id : lane[0].project_id
  }

  const handleDrop = (e, targetCol) => {
    e.preventDefault()
    if (!canDropOnColumn(targetCol)) { setDragId(null); return }
    if (!dragId) return
    const dtText = e.dataTransfer.getData('text/plain')
    if (dtText && dtText !== dragId) { setDragId(null); return }
    const card = items.find((c) => c.project_id === dragId)
    if (!card || card.column === targetCol) { setDragId(null); return }
    doMove(card, targetCol)
    setDragId(null)
  }

  return jsx('div', {
    style: isRefreshing ? { opacity: 0.7 } : undefined,
    children: [
      jsx('div', { key: 'live', 'aria-live': 'polite', style: { position: 'absolute', left: '-9999px', width: '1px', height: '1px', overflow: 'hidden' }, children: announce }),
      jsx('div', { key: 'hint', 'aria-live': 'polite', style: { position: 'absolute', left: '-9999px', width: '1px', height: '1px', overflow: 'hidden' }, children: items.filter((c) => c.column === 'inbox').length > 0 ? 'Accept \u201c' + (items.find((c) => c.column === 'inbox')?.name || 'item') + '\u201d by moving it out of Inbox.' : '' }),
      jsx('div', { key: 'grid', className: 'continuum-board-grid', children: BOARD_COLUMNS.map((col, idx) => {
        const laneItems = byColumn[col] || []
        const count = counts[col] !== undefined ? counts[col] : laneItems.length
        const ariaLabel = COLUMN_LABELS[col] + ' column, ' + count + ' cards'
        const isDropTarget = dragId !== null && col !== 'inbox'
        return jsx('div', {
          key: col,
          role: 'region',
          'aria-label': ariaLabel,
          className: 'kanban-lane',
          onDragOver: (e) => { if (col !== 'inbox') e.preventDefault() },
          onDrop: (e) => handleDrop(e, col),
          style: {
            border: isDropTarget ? '2px dashed var(--ui-stroke-secondary)' : '1px solid var(--ui-stroke-secondary)',
            borderRadius: '6px',
            padding: '8px',
            minHeight: '200px',
            display: 'flex',
            flexDirection: 'column',
            gap: '0',
            background: 'var(--ui-bg)',
          },
          children: [
            jsx('div', { key: 'h', className: 'kanban-lane-header', style: { fontWeight: 600, fontSize: '12px', textTransform: 'uppercase', letterSpacing: '0.04em', color: 'var(--ui-text-secondary)', paddingBottom: '6px', borderBottom: '1px solid var(--ui-stroke-secondary)', display: 'flex', justifyContent: 'space-between', alignItems: 'center', position: 'sticky', top: '0' }, children: [
              jsx('span', { key: 'label', children: COLUMN_LABELS[col] }),
              count > 0 ? jsx('span', { key: 'cnt', style: { color: 'var(--ui-text-quaternary)', fontSize: '11px' }, children: '(' + count + ')' }) : null,
            ]}),
            idx === 5 ? jsx(Separator, { key: 'sep' }) : null,
            laneItems.length === 0
              ? jsx('div', { key: 'empty', style: { color: 'var(--ui-text-quaternary)', fontSize: '12px', padding: '12px 4px', textAlign: 'center' }, children: EMPTY_COPY[col] || 'Empty' })
              : jsx('div', { key: 'list', role: 'list', style: { display: 'flex', flexDirection: 'column', paddingTop: '6px' }, children: laneItems.map((card, cardIdx) => {
                  const isFocused = tabStops[col] === card.project_id
                  const tabIdx = isFocused ? 0 : -1
                  const menuOpen = menuOpenFor === card.project_id
                  return jsx(KanbanCard, {
                  key: card.project_id,
                  card: card,
                  onOpen: onOpen,
                  onMove: doMove,
                  onToggleUrgent: doToggleUrgent,
                  isPending: !!pending[card.project_id],
                  isFocused: isFocused,
                  tabIndex: tabIdx,
                  onCardFocus: (pid) => setFocusedId(pid),
                  onCardKeyDown: (e, c) => handleCardKeyDown(e, c, laneItems, idx, cardIdx, byColumn),
                  menuOpen: menuOpen,
                  onMenuToggle: (pid) => {
                    if (menuOpenFor === pid) {
                      setMenuOpenFor(null)
                      setMenuHighlightIdx(0)
                      focusCard(pid)
                    } else {
                      const items = menuItemsFor(card)
                      setMenuOpenFor(pid)
                      setMenuHighlightIdx(menuInitialHighlight(items))
                      setAnnounce('Opened Move to menu for \u201c' + (card.name || pid) + '\u201d.')
                      requestAnimationFrame(() => {
                        const openIdx = menuInitialHighlight(items)
                        const el = document.getElementById('kanban-menu-' + pid + '-' + (items[openIdx].kind === 'cancel' ? 'cancel' : items[openIdx].target))
                        if (el) el.focus()
                      })
                    }
                  },
                  onMenuSelect: doMove,
                  onMenuClose: closeMenu,
                  draggableProps: {
                    onDragStart: () => setDragId(card.project_id),
                    onDragEnd: () => setDragId(null),
                  },
                  menuItems: menuOpen ? menuItemsFor(card) : null,
                  menuHighlightIdx: menuHighlightIdx,
                  onMenuHighlightChange: setMenuHighlightIdx,
                })})}),
          ],
        })
      })}),
      jsx(DataAsOf, { key: 'asof', data: data }),
      jsx('div', { key: 'counts', style: { color: 'var(--ui-text-quaternary)', fontSize: '11px', paddingTop: '4px' }, children: 'total ' + (counts.total || items.length) + (counts.unplaced_accepted ? ' \u00b7 unplaced ' + counts.unplaced_accepted : '') }),
    ],
  })
}

function StalenessSurface({ onOpen }) {
  const q = useContinum('/staleness')
  if (q.isLoading) return jsx('div', { children: Array.from({ length: 5 }, (_, i) => jsx(Skeleton, { key: i, className: 'h-16 w-full mb-2' })) })
  if (q.error) return jsx(ErrorState, { title: 'Failed to load', description: String(q.error.message || q.error), children: jsx(Button, { onClick: () => q.refetch(), children: 'Retry' }) })
  const buckets = (q.data && q.data.buckets) || []
  const parked = (q.data && q.data.parked) || []
  return jsx('div', {
    children: [
      jsx('div', { key: 'note', style: { color: 'var(--ui-text-secondary)', padding: '6px 0' }, children: 'Quiet is not the same as abandoned. Age is information, not failure.' }),
      ...buckets.map((b) => jsx('div', { key: b.band, style: { marginBottom: '12px' }, children: [
        jsx('div', { key: 'h', style: { fontWeight: 600, paddingBottom: '4px' }, children: b.band + ' \u00b7 ' + b.items.length }),
        ...b.items.map((c) => jsx(ProjectRow, { key: c.project_id, card: c, onOpen: onOpen })),
      ] })),
      jsx('div', { key: 'parked', style: { marginTop: '16px' }, children: [
        jsx('div', { key: 'h', style: { fontWeight: 600, color: 'var(--ui-text-secondary)' }, children: 'Not alerting \u00b7 parked/hiatus \u00b7 ' + parked.length }),
        ...parked.map((c) => jsx(ProjectRow, { key: c.project_id, card: c, onOpen: onOpen })),
      ] }),
      jsx(DataAsOf, { key: 'asof', data: q.data }),
    ],
  })
}

function NoiseSurface() {
  const q = useContinum('/noise')
  if (q.isLoading) return jsx('div', { children: Array.from({ length: 4 }, (_, i) => jsx(Skeleton, { key: i, className: 'h-16 w-full mb-2' })) })
  if (q.error) return jsx(ErrorState, { title: 'Failed to load', description: String(q.error.message || q.error), children: jsx(Button, { onClick: () => q.refetch(), children: 'Retry' }) })
  const data = q.data || { suppressions: [], counts: {} }
  return jsx('div', {
    children: [
      jsx('div', { key: 'rule', style: { color: 'var(--ui-text-secondary)', padding: '6px 0' }, children: 'Hiding is a dashboard decision only. No Hermes session is ever renamed, archived, pinned or deleted.' }),
      jsx('div', { key: 'counts', style: { padding: '6px 0' }, children: 'suppressed sessions: ' + (data.counts.suppressed_sessions || 0) + ' \u00b7 visible projects: ' + (data.counts.visible_projects || 0) + ' \u00b7 suppressed projects: ' + (data.counts.suppressed_projects || 0) }),
      ...(data.suppressions || []).map((s, i) => jsx('div', {
        key: i,
        style: { display: 'grid', gridTemplateColumns: '1fr 1fr 1fr auto', gap: '8px', padding: '6px 0', borderBottom: '1px solid var(--ui-stroke-secondary)' },
        children: [
          jsx('span', { key: 'c', children: s.noise_class || 'unclassified' }),
          jsx('span', { key: 's', style: { color: 'var(--ui-text-secondary)' }, children: s.source }),
          jsx('span', { key: 'n', style: { color: 'var(--ui-text-secondary)' }, children: String(s.count) }),
          jsx('span', { key: 'r', style: { color: 'var(--ui-text-quaternary)' }, children: 'reversible' }),
        ],
      })),
      jsx(DataAsOf, { key: 'asof', data: q.data }),
    ],
  })
}

function OverviewSurface({ onOpen }) {
  const q = useContinum('/overview')
  const isLoadingFirst = q.isLoading && !q.data
  const isRefreshing = q.isFetching && !!q.data
  const data = q.data || {}
  const items = data.items || []
  const label = data.variant_label || 'Overview'
  const dataAsOf = data.data_as_of
  const dupNames = (() => {
    const counts = {}
    for (const it of items) counts[it.name] = (counts[it.name] || 0) + 1
    const s = new Set()
    for (const [name, n] of Object.entries(counts)) if (n > 1) s.add(name)
    return s
  })()
  if (isLoadingFirst) {
    return jsx('div', { children: [
      jsx('div', { key: 'sk', children: Array.from({ length: 6 }, (_, i) => jsx(Skeleton, { key: i, className: 'h-24 w-full mb-2' })) }),
    ]})
  }
  if (q.error) {
    return jsx('div', { children: [
      jsx(ErrorState, { key: 'e', title: 'Failed to load', description: String(q.error.message || q.error), children: jsx(Button, { onClick: () => q.refetch(), children: 'Retry' }) }),
      dataAsOf ? jsx('div', { key: 'stale', style: { color: 'var(--ui-text-quaternary)', fontSize: '11px' }, children: 'last good data as of ' + fmtTime(dataAsOf) + ' (stale)' }) : null,
    ]})
  }
  if (items.length === 0) {
    return jsx('div', { children: [
      jsx('div', { key: 'label', style: { color: 'var(--ui-text-secondary)', fontSize: '12px', paddingBottom: '6px' }, children: label }),
      jsx(EmptyState, { key: 'e', title: "Continuum hasn't scanned yet.", description: 'Run a scan to find candidate projects. Sessions are read-only \u2014 nothing is modified.' }),
      jsx(DataAsOf, { key: 'd', data: data }),
    ]})
  }
  return jsx('div', {
    style: isRefreshing ? { opacity: 0.7 } : undefined,
    children: [
      jsx('div', { key: 'label', style: { color: 'var(--ui-text-secondary)', fontSize: '12px', paddingBottom: '6px' }, children: label }),
      jsx('div', { key: 'list', role: 'list', children: items.map((it) => jsx(CompactCard, {
        key: it.project_id,
        it: it,
        onOpen: onOpen,
        duplicateNames: dupNames,
      }))}),
      jsx(DataAsOf, { key: 'd', data: data }),
    ],
  })
}

function DetailSurface({ projectId, onBack }) {
  const q = useContinum('/projects/' + projectId)
  const queryClient = useQueryClient()
  const [movePending, setMovePending] = useState(false)
  if (q.isLoading) return jsx('div', { children: Array.from({ length: 6 }, (_, i) => jsx(Skeleton, { key: i, className: 'h-16 w-full mb-2' })) })
  if (q.error) {
    const cached = q.data || {}
    const candidate = cached.project || {}
    return jsx('div', { children: [
      jsx(ErrorState, { key: 'error', title: 'Couldn’t refresh this project.', description: String(q.error.message || q.error), children: jsx(Button, { onClick: () => q.refetch(), children: 'Retry' }) }),
      candidate.name ? jsx('div', { key: 'cached', style: { color: 'var(--ui-text-secondary)', padding: '8px 0' }, children: [
        'Showing the last successful refresh for ', candidate.name, ' · stale data',
      ] }) : null,
      jsx(Button, { key: 'back', onClick: onBack, children: 'Back to board' }),
    ] })
  }
  const d = q.data || {}
  const p = d.project || {}
  const profileName = p.profile_name || p.primary_profile || p.owner_profile || 'profile unavailable'
  const col = p.column || 'inbox'
  const handleDetailMove = async (target) => {
    if (target === col) return
    if (target === 'inbox') return
    setMovePending(true)
    try {
      const isInboxExit = col === 'inbox'
      const action = isInboxExit ? 'accept' : 'set_placement'
      const payload = isInboxExit ? { lifecycle: 'LS-1', placement: target } : { placement: target }
      const res = await api('/projects/' + projectId + '/review', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ action: action, payload: payload }) })
      if (!res.ok) throw new Error('move failed')
      const body = await res.json()
      queryClient.invalidateQueries({ queryKey: ['continuum'] })
      q.refetch()
      if (isInboxExit) {
        host.notify({ kind: 'success', message: buildAcceptToast(p.name || projectId, target), action: { label: buildAcceptActions(body.audit_id).label, onClick: async () => { try { await api('/review/undo', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ audit_id: body.audit_id }) }); queryClient.invalidateQueries({ queryKey: ['continuum'] }); q.refetch(); host.notify({ kind: 'info', message: buildUndoToast(p.name || projectId) }) } catch (e) {} } } })
      }
    } catch (e) { host.notify({ kind: 'error', message: buildFailureToast(p.name || projectId, col), action: { label: buildFailureActions().label, onClick: () => handleDetailMove(target) } }) }
    finally { setMovePending(false) }
  }
  const handleUrgent = async () => {
    const nextVal = !p.urgent
    setMovePending(true)
    try {
      const res = await api('/projects/' + projectId + '/review', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ action: 'set_urgent', payload: { value: nextVal } }) })
      if (!res.ok) throw new Error('urgent failed')
      queryClient.invalidateQueries({ queryKey: ['continuum'] })
      q.refetch()
    } catch (e) { host.notify({ kind: 'error', message: String(e.message || e) }) }
    finally { setMovePending(false) }
  }
  return jsx('div', {
    children: [
      jsx('div', { key: 'bc', style: { paddingBottom: '8px' }, children: jsx(Button, { onClick: onBack, children: '\u2190 back' }) }),
      jsx('div', { key: 'h', style: { display: 'flex', gap: '10px', alignItems: 'center', flexWrap: 'wrap' }, children: [
        jsx('span', { key: 'n', style: { fontSize: '18px', fontWeight: 600 }, children: p.name || '(untitled)' }),
        jsx(Badge, { key: 'l', variant: 'default', children: p.lifecycle_name || p.derived_lifecycle_name || 'unknown' }),
        p.derived_lifecycle && p.column && p.derived_lifecycle !== p.lifecycle ? jsx('span', { key: 'dl', style: { color: 'var(--ui-text-secondary)', fontSize: '12px' }, children: 'derived: ' + p.derived_lifecycle_name }) : null,
        p.placement_source ? jsx(Badge, { key: 'ps', variant: 'muted', children: p.placement_source }) : null,
        p.urgent ? jsx(Badge, { key: 'urg', variant: 'warn', children: 'Urgent' }) : null,
        jsx('span', { key: 'owner', style: { color: 'var(--ui-text-secondary)', fontSize: '12px' }, children: 'Profile: ' + profileName }),
        p.review_status ? jsx(Badge, { key: 'rs', variant: p.review_status === 'accepted' ? 'success' : 'default', title: p.review_status_label, children: p.review_status === 'accepted' ? 'Accepted' : 'Candidate' }) : null,
        p.needs_review ? jsx(Badge, { key: 'nr', variant: 'warn', children: 'Needs review' }) : null,
        jsx(ProvenanceChip, { key: 'p', confidenceBand: p.confidence_band, evidenceTier: p.evidence_tier, confidence: p.confidence }),
      ] }),
      jsx('div', { key: 'col', style: { padding: '8px 0', display: 'flex', gap: '6px', alignItems: 'center', flexWrap: 'wrap' }, children: [
        jsx('span', { key: 'lbl', style: { color: 'var(--ui-text-secondary)', fontSize: '12px' }, children: 'Column: ' + COLUMN_LABELS[col] + ' \u00b7 ' + (p.placement_source || '') }),
        ...NON_INBOX_PLACEMENTS.map((t) => jsx('button', { key: t, type: 'button', disabled: t === col || movePending, onClick: () => handleDetailMove(t), style: { background: t === col ? 'var(--ui-accent)' : 'transparent', color: t === col ? 'var(--ui-text-on-accent)' : 'var(--ui-text-secondary)', border: '1px solid var(--ui-stroke-secondary)', borderRadius: '4px', padding: '2px 6px', cursor: t === col ? 'default' : 'pointer', fontSize: '11px' }, children: (t === col ? '\u2713 ' : '') + COLUMN_LABELS[t] })),
      ]}),
      jsx('div', { key: 'urg', style: { padding: '4px 0' }, children: jsx('button', { key: 'b', type: 'button', disabled: movePending, 'aria-pressed': p.urgent ? 'true' : 'false', onClick: handleUrgent, style: { background: 'transparent', border: '1px solid var(--ui-stroke-secondary)', borderRadius: '4px', padding: '4px 8px', cursor: 'pointer', fontSize: '12px' }, children: p.urgent ? 'Remove urgent' : 'Mark urgent' })}),
      jsx('div', { key: 'next', style: { padding: '8px 0' }, children: 'next action: ' + (p.next_action ? p.next_action.text + ' (' + p.next_action.source + ')' : 'none declared') }),
      jsx(Separator, { key: 's1' }),
      jsx('div', { key: 'lh', style: { fontWeight: 600, padding: '6px 0' }, children: 'Linked sessions' }),
      jsx('div', { key: 'links', style: { display: 'flex', flexDirection: 'column', gap: '4px' }, children: (d.resume_links || []).map((l) => jsx(ResumeLink, { key: l.profile + l.session_id, link: l })) }),
      jsx(Separator, { key: 's2' }),
      jsx('div', { key: 'eh', style: { fontWeight: 600, padding: '6px 0' }, children: 'Evidence excerpts (tier-labelled, read-only)' }),
      jsx('div', { key: 'ev', children: (d.evidence || []).slice(0, 20).map((e, i) => jsx('div', { key: i, style: { fontSize: '12px', color: 'var(--ui-text-secondary)', padding: '3px 0' }, children: 'T' + e.tier + ' \u00b7 ' + e.kind + ' \u00b7 ' + (e.excerpt || '') })) }),
      jsx(Separator, { key: 's3' }),
      jsx('div', { key: 'ah', style: { fontWeight: 600, padding: '6px 0' }, children: 'Decision history (audit, reversible)' }),
      jsx('div', { key: 'au', children: (d.audit || []).map((a, i) => jsx('div', { key: i, style: { fontSize: '12px', color: 'var(--ui-text-quaternary)', padding: '2px 0' }, children: a.action + ' \u00b7 ' + fmtTime(a.ts) })) }),
    ],
  })
}

function ContinuumPage({ ctx }) {
  const queryClient = useQueryClient()
  const [surface, setSurface] = useState('overview')
  const [detailId, setDetailId] = useState(null)
  const [search, setSearch] = useState('')

  const openDetail = (id) => setDetailId(id)

  const body = detailId
    ? jsx(DetailSurface, { projectId: detailId, onBack: () => setDetailId(null) })
    : jsx('div', {
        children: [
          jsx('div', { key: 'tabs', role: 'tablist', style: { display: 'flex', gap: '4px', paddingBottom: '8px' }, children: SURFACES.map((s) => {
            const isActive = surface === s.id
            return jsx('button', {
              key: s.id,
              type: 'button',
              role: 'tab',
              'aria-selected': isActive ? 'true' : 'false',
              'aria-label': s.label,
              onClick: () => { setSurface(s.id); host.navigate(s.path) },
              onKeyDown: (e) => {
                if (e.key === 'ArrowRight' || e.key === 'ArrowLeft') {
                  const idx = SURFACES.findIndex((x) => x.id === s.id)
                  const next = e.key === 'ArrowRight' ? (idx + 1) % SURFACES.length : (idx - 1 + SURFACES.length) % SURFACES.length
                  setSurface(SURFACES[next].id)
                  host.navigate(SURFACES[next].path)
                }
              },
              style: {
                background: isActive ? 'var(--ui-accent)' : 'transparent',
                color: isActive ? 'var(--ui-text-on-accent)' : 'var(--ui-text-secondary)',
                border: '1px solid var(--ui-stroke-secondary)',
                borderRadius: '4px',
                padding: '4px 10px',
                cursor: 'pointer',
                font: 'inherit',
                fontSize: '13px',
              },
              children: s.label,
            })
          } )}),
          jsx('div', { key: 'bar', style: { display: 'flex', gap: '8px', alignItems: 'center', paddingBottom: '8px' }, children: [
            jsx(SearchField, { key: 'q', value: search, onChange: setSearch, placeholder: 'filter (names, ids)', 'aria-label': 'filter projects' }),
            jsx(Button, { key: 's', onClick: async () => { await api('/scan', { method: 'POST' }); queryClient.invalidateQueries({ queryKey: ['continuum'] }) }, children: 'Run scan now' }),
          ] }),
          surface === 'overview' ? jsx(OverviewSurface, { key: 'o', onOpen: openDetail }) : null,
          surface === 'board' ? jsx(BoardSurface, { key: 'b', onOpen: openDetail }) : null,
          surface === 'attention' ? jsx(AttentionSurface, { key: 'a', onOpen: openDetail }) : null,
          surface === 'stale' ? jsx(StalenessSurface, { key: 's', onOpen: openDetail }) : null,
          surface === 'noise' ? jsx(NoiseSurface, { key: 'n' }) : null,
        ],
      })

  return jsx(ScrollArea, { style: { height: '100%', padding: '12px' }, children: body })
}

export default {
  id: 'continuum',
  name: 'Continuum',
  defaultEnabled: false,

  register(ctx) {
    pluginCtx = ctx
    ctx.register({
      id: 'continuum.page',
      area: ROUTES_AREA,
      data: { path: PAGE },
      render: () => jsx(ContinuumPage, { ctx }),
    })

    ctx.register({
      id: 'continuum.nav',
      area: SIDEBAR_NAV_AREA,
      data: { path: PAGE, label: 'Continuum', codicon: 'project' },
    })

    ctx.register({
      id: 'continuum.palette.open',
      area: PALETTE_AREA,
      data: {
        label: 'Continuum: Open dashboard',
        codicon: 'project',
        callback: () => host.navigate(PAGE),
      },
    })

    ctx.register({
      id: 'continuum.palette.board',
      area: PALETTE_AREA,
      data: {
        label: 'Continuum: Open board',
        codicon: 'project',
        callback: () => host.navigate(PAGE + '?view=board'),
      },
    })

    ctx.register({
      id: 'continuum.palette.scan',
      area: PALETTE_AREA,
      data: {
        label: 'Continuum: Run scan now',
        codicon: 'refresh',
        callback: async () => {
          const res = await api('/scan', { method: 'POST' })
          host.notify({ kind: res.ok ? 'info' : 'error', message: res.ok ? 'Continuum: scan started.' : 'Continuum: scan failed to start.' })
        },
      },
    })
  },
}
