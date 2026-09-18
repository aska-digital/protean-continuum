/**
 * kanban-interaction.js — Shared pure interaction logic for the Continuum kanban board.
 *
 * This module is the single source of truth for:
 *   - Constants (BOARD_COLUMNS, COLUMN_LABELS, NON_INBOX_PLACEMENTS)
 *   - Toast message builders (accept success, failure, undo)
 *   - Keyboard event classification
 *   - Arrow/Home/End navigation
 *   - Layout breakpoint decision (2164px)
 *   - Reduced-motion CSS
 *   - Drop-target rule (Inbox excluded)
 *   - Copy ID value contract
 *   - Close-menu focus target
 *   - Focus-after-move target
 *   - Menu item generation and highlight navigation
 *
 * Imported by:
 *   - desktop/plugin.js (shipped artifact)
 *   - tests/kanban_helpers.js → tests/test_kanban_helpers.mjs (Node behavioral tests)
 *
 * No React, no DOM, no JSX — pure ESM for both browser and Node.
 */

export const BOARD_COLUMNS = ['inbox', 'ongoing', 'blocked', 'waiting_on_you', 'paused', 'done', 'shipped', 'scrapped']
export const NON_INBOX_PLACEMENTS = ['ongoing', 'blocked', 'waiting_on_you', 'paused', 'done', 'shipped', 'scrapped']
export const COLUMN_LABELS = {
  inbox: 'Inbox',
  ongoing: 'Ongoing',
  blocked: 'Blocked',
  waiting_on_you: 'Waiting on you',
  paused: 'Paused',
  done: 'Done',
  shipped: 'Shipped',
  scrapped: 'Scrapped',
}

// ── KB-T1: Toast success message builder ──────────────────────────────
export function buildAcceptToast(name, target) {
  return 'Accepted \u201c' + (name || 'item') + '\u201d. It\u2019s in ' + COLUMN_LABELS[target] + '.'
}

// ── KB-T1: Toast action contract ──────────────────────────────────────
export function buildAcceptActions(auditId) {
  return { label: 'Undo', auditId }
}

// ── KB-T2: Toast failure message builder ──────────────────────────────
export function buildFailureToast(name, origin) {
  return 'Couldn\u2019t move \u201c' + (name || 'item') + '\u201d. It is in ' + COLUMN_LABELS[origin] + '.'
}

// ── KB-T2: Failure toast action contract ──────────────────────────────
export function buildFailureActions() {
  return { label: 'Retry' }
}

// ── KB-T2: Undo success message builder ───────────────────────────────
export function buildUndoToast(name) {
  return 'Undone. \u201c' + (name || 'item') + '\u201d is back in Inbox.'
}

// ── KB-T2: Undo toast has no action ───────────────────────────────────
export function buildUndoActions() {
  return null
}

// ── KB-K1/K2: Keyboard navigation logic ───────────────────────────────

export function navigateRight(laneItems, laneIdx, cardIdx, allByColumn) {
  let nextLane = laneIdx + 1
  while (nextLane >= 0 && nextLane < BOARD_COLUMNS.length) {
    const col = BOARD_COLUMNS[nextLane]
    const items = allByColumn[col] || []
    if (items.length > 0) {
      const nextIdx = Math.min(cardIdx, items.length - 1)
      return { focusedId: items[nextIdx].project_id, column: col }
    }
    nextLane++
  }
  return null
}

export function navigateLeft(laneItems, laneIdx, cardIdx, allByColumn) {
  let nextLane = laneIdx - 1
  while (nextLane >= 0 && nextLane < BOARD_COLUMNS.length) {
    const col = BOARD_COLUMNS[nextLane]
    const items = allByColumn[col] || []
    if (items.length > 0) {
      const nextIdx = Math.min(cardIdx, items.length - 1)
      return { focusedId: items[nextIdx].project_id, column: col }
    }
    nextLane--
  }
  return null
}

export function navigateDown(laneItems, cardIdx) {
  const nextIdx = cardIdx + 1
  if (nextIdx >= 0 && nextIdx < laneItems.length) {
    return { focusedId: laneItems[nextIdx].project_id }
  }
  return null
}

export function navigateUp(laneItems, cardIdx) {
  const nextIdx = cardIdx - 1
  if (nextIdx >= 0 && nextIdx < laneItems.length) {
    return { focusedId: laneItems[nextIdx].project_id }
  }
  return null
}

export function navigateHome(laneItems) {
  if (laneItems.length > 0) {
    return { focusedId: laneItems[0].project_id }
  }
  return null
}

export function navigateEnd(laneItems) {
  if (laneItems.length > 0) {
    return { focusedId: laneItems[laneItems.length - 1].project_id }
  }
  return null
}

// ── KB-K2: Enter → Detail, Space → Menu separation ────────────────────
export function classifyKeyEvent(key, menuOpenFor, cardProjectId) {
  if (key === 'Escape' && menuOpenFor === cardProjectId) {
    return { action: 'closeMenu' }
  }
  if (key === 'Enter' && !menuOpenFor) {
    return { action: 'openDetail' }
  }
  if (key === ' ') {
    return { action: menuOpenFor === cardProjectId ? 'closeMenu' : 'openMenu' }
  }
  if (key === 'ArrowLeft') return { action: 'navigateLeft' }
  if (key === 'ArrowRight') return { action: 'navigateRight' }
  if (key === 'ArrowUp') return { action: 'navigateUp' }
  if (key === 'ArrowDown') return { action: 'navigateDown' }
  if (key === 'Home') return { action: 'navigateHome' }
  if (key === 'End') return { action: 'navigateEnd' }
  return { action: 'none' }
}

// ── KB-K3: closeMenu restores focus ───────────────────────────────────
export function closeMenuFocusTarget(menuOpenFor) {
  return menuOpenFor
}

// ── KB-R1: Responsive layout decision ─────────────────────────────────
export function resolveLayout(windowWidth) {
  if (windowWidth <= 2164) {
    return {
      gridTemplateColumns: '1fr',
      overflowX: 'hidden',
      mode: 'stacked',
    }
  }
  return {
    gridTemplateColumns: 'repeat(8, minmax(260px, 1fr))',
    overflowX: 'auto',
    mode: 'grid',
  }
}

// ── KB-R1: Lane min-width is stripped in stacked mode ─────────────────
export function laneMinWidth(mode) {
  return mode === 'stacked' ? '0' : '260px'
}

// ── KB-RM1: Reduced-motion animation suppression ──────────────────────
export function reducedMotionCSS() {
  return {
    animation: 'none',
    transition: 'none',
    scrollBehavior: 'auto',
  }
}

// ── KB-R-Inbox: Inbox is not a drop target ────────────────────────────
export function canDropOnColumn(targetCol) {
  return targetCol !== 'inbox'
}

// ── KB-Focus: Focus follows moved card ────────────────────────────────
export function focusAfterMove(projectId) {
  return projectId
}

// ── KB-Copy: Copy ID uses bare session_id ─────────────────────────────
export function copyIdValue(card) {
  const primarySession = (card.sessions && card.sessions[0]) || null
  if (primarySession) {
    return { text: primarySession.session_id, label: 'Copy ID' }
  }
  return { text: card.project_id, label: 'Copy project ID' }
}

// ── KB-Menu: Menu item generation ─────────────────────────────────────
export function menuItemsFor(card) {
  const currentCol = card.column
  const targets = NON_INBOX_PLACEMENTS
    .filter(col => col !== currentCol)
    .map(col => ({ kind: 'target', target: col, disabled: false }))
  const cancel = { kind: 'cancel', target: null, disabled: false }
  return [...targets, cancel]
}

// ── KB-Menu: Initial highlight index ──────────────────────────────────
export function menuInitialHighlight(items) {
  for (let i = 0; i < items.length; i++) {
    if (!items[i].disabled) return i
  }
  return 0
}

// ── KB-Menu: Highlight navigation ─────────────────────────────────────
export function menuHighlightIndex(items, currentIdx, key) {
  if (items.length === 0) return currentIdx

  if (key === 'Home') {
    for (let i = 0; i < items.length; i++) {
      if (!items[i].disabled) return i
    }
    return currentIdx
  }

  if (key === 'End') {
    for (let i = items.length - 1; i >= 0; i--) {
      if (!items[i].disabled) return i
    }
    return currentIdx
  }

  if (key === 'ArrowDown') {
    for (let i = currentIdx + 1; i < items.length; i++) {
      if (!items[i].disabled) return i
    }
    return currentIdx
  }

  if (key === 'ArrowUp') {
    for (let i = currentIdx - 1; i >= 0; i--) {
      if (!items[i].disabled) return i
    }
    return currentIdx
  }

  return currentIdx
}

// ── KB-Menu: Activation action ────────────────────────────────────────
export function menuActivate(item) {
  if (item.disabled) return 'none'
  if (item.kind === 'cancel') return 'close'
  return 'move'
}

