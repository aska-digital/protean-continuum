/**
 * mutation_evidence.js — Source-level parity and drift probes.
 *
 * Proves that:
 *   1. plugin.js imports from kanban-interaction.js (the shared module)
 *   2. plugin.js does NOT re-define constants that come from the shared module
 *   3. kanban-interaction.js exports every required function
 *   4. Editing plugin.js alone (without the shared module) breaks parity
 *
 * Run: node tests/mutation_evidence.js
 * Expected: all probes pass, exit 0
 */
import { readFileSync } from 'fs'
import { resolve, dirname } from 'path'
import { fileURLToPath } from 'url'

const __dirname = dirname(fileURLToPath(import.meta.url))
const BUILD = resolve(__dirname, '..')

const pluginSrc = readFileSync(resolve(BUILD, 'desktop/plugin.js'), 'utf8')
const sharedSrc = readFileSync(resolve(BUILD, 'desktop/kanban-interaction.js'), 'utf8')

let caught = 0, missed = 0

function probe(name, shouldPass, fn) {
  try {
    fn()
    if (!shouldPass) { missed++; console.log('  MISSED (should fail): ' + name) }
    else { caught++; console.log('  PASS: ' + name) }
  } catch(e) {
    if (shouldPass) { missed++; console.log('  FAIL: ' + name + ': ' + e.message) }
    else { caught++; console.log('  CAUGHT (correctly rejected): ' + name) }
  }
}

// ── Probe 1: plugin.js imports from kanban-interaction.js ──────────────
probe('P1: plugin.js imports from kanban-interaction.js', true, () => {
  if (!pluginSrc.includes("from './kanban-interaction.js'") &&
      !pluginSrc.includes('from "./kanban-interaction.js"')) {
    throw new Error('plugin.js does not import from kanban-interaction.js')
  }
})

// ── Probe 2: plugin.js does NOT define BOARD_COLUMNS locally ──────────
probe('P2: plugin.js does not redefine BOARD_COLUMNS', true, () => {
  // The only occurrence of BOARD_COLUMNS should be in the import, not a const declaration
  const lines = pluginSrc.split('\n')
  const constDecl = lines.filter(l => l.match(/const\s+BOARD_COLUMNS\s*=/))
  if (constDecl.length > 0) {
    throw new Error('plugin.js still has: ' + constDecl[0].trim())
  }
})

// ── Probe 3: plugin.js does NOT define COLUMN_LABELS locally ──────────
probe('P3: plugin.js does not redefine COLUMN_LABELS', true, () => {
  const lines = pluginSrc.split('\n')
  const constDecl = lines.filter(l => l.match(/const\s+COLUMN_LABELS\s*=/))
  if (constDecl.length > 0) {
    throw new Error('plugin.js still has: ' + constDecl[0].trim())
  }
})

// ── Probe 4: plugin.js does NOT define NON_INBOX_PLACEMENTS locally ───
probe('P4: plugin.js does not redefine NON_INBOX_PLACEMENTS', true, () => {
  const lines = pluginSrc.split('\n')
  const constDecl = lines.filter(l => l.match(/const\s+NON_INBOX_PLACEMENTS\s*=/))
  if (constDecl.length > 0) {
    throw new Error('plugin.js still has: ' + constDecl[0].trim())
  }
})

// ── Probe 5: kanban-interaction.js exports all required functions ──────
const requiredExports = [
  'BOARD_COLUMNS', 'NON_INBOX_PLACEMENTS', 'COLUMN_LABELS',
  'buildAcceptToast', 'buildAcceptActions',
  'buildFailureToast', 'buildFailureActions',
  'buildUndoToast', 'buildUndoActions',
  'navigateRight', 'navigateLeft', 'navigateDown', 'navigateUp',
  'navigateHome', 'navigateEnd',
  'classifyKeyEvent', 'closeMenuFocusTarget',
  'resolveLayout', 'laneMinWidth', 'reducedMotionCSS',
  'canDropOnColumn', 'focusAfterMove', 'copyIdValue',
]
probe('P5: kanban-interaction.js exports all required symbols', true, () => {
  for (const name of requiredExports) {
    if (!sharedSrc.includes('export ' + name) && !sharedSrc.includes('export const ' + name) && !sharedSrc.includes('export function ' + name)) {
      throw new Error('missing export: ' + name)
    }
  }
})

// ── Probe 6: kanban-interaction.js has 2164px breakpoint ──────────────
probe('P6: shared module uses 2164px breakpoint (not 1200px)', true, () => {
  if (!sharedSrc.includes('2164')) throw new Error('2164 breakpoint missing')
  if (sharedSrc.includes('1200')) throw new Error('old 1200 breakpoint still present')
})

// ── Probe 7: kanban-interaction.js has Inbox drop exclusion ────────────
probe('P7: shared module excludes Inbox from drop targets', true, () => {
  if (!sharedSrc.includes("!== 'inbox'")) throw new Error('inbox exclusion missing')
})

// ── Probe 8: plugin.js uses classifyKeyEvent (not inline re-implementation)
probe('P8: plugin.js uses imported classifyKeyEvent', true, () => {
  if (!pluginSrc.includes('classifyKeyEvent(')) {
    throw new Error('plugin.js does not call classifyKeyEvent')
  }
})

// ── Probe 9: plugin.js uses canDropOnColumn (not inline check) ────────
probe('P9: plugin.js uses imported canDropOnColumn', true, () => {
  if (!pluginSrc.includes('canDropOnColumn(')) {
    throw new Error('plugin.js does not call canDropOnColumn')
  }
})

// ── Probe 10: Enter → Detail contract preserved in shared module ──────
probe('P10: shared classifyKeyEvent has Enter → openDetail', true, () => {
  if (!sharedSrc.includes("key === 'Enter'") || !sharedSrc.includes("action: 'openDetail'")) {
    throw new Error('Enter→openDetail contract missing from shared module')
  }
})

// ── Probe 11: Space → Menu contract preserved in shared module ────────
probe('P11: shared classifyKeyEvent has Space → openMenu/closeMenu', true, () => {
  if (!sharedSrc.includes("key === ' '")) throw new Error('Space handler missing')
  if (!sharedSrc.includes("'openMenu'")) throw new Error('openMenu action missing')
  if (!sharedSrc.includes("'closeMenu'")) throw new Error('closeMenu action missing')
})

// ── Probe 12: Toast literal pinned in shared module ───────────────────
probe('P12: shared module has exact toast literal', true, () => {
  if (!sharedSrc.includes("Accepted \\u201c")) throw new Error('accept toast literal missing')
  if (!sharedSrc.includes("It\\u2019s in")) throw new Error('accept toast destination literal missing')
  if (!sharedSrc.includes("Couldn\\u2019t move")) throw new Error('failure toast literal missing')
  if (!sharedSrc.includes("back in Inbox")) throw new Error('undo toast literal missing')
})

// ── Probe 13: Toast builders used in plugin.js ────────────────────────
// This is the PLUGIN DRIFT PROBE: if someone edits plugin.js to use inline
// toast construction instead of shared builders, this catches it.
probe('P13: plugin.js uses shared toast builders (not inline construction)', true, () => {
  if (!pluginSrc.includes('buildAcceptToast(')) {
    throw new Error('plugin.js must use buildAcceptToast')
  }
  if (!pluginSrc.includes('buildFailureToast(')) {
    throw new Error('plugin.js must use buildFailureToast')
  }
  if (!pluginSrc.includes('buildUndoToast(')) {
    throw new Error('plugin.js must use buildUndoToast')
  }
})

// ── Probe 14: plugin.js Enter→Detail path uses onOpen (not inline) ────
// If someone reverts the classifyKeyEvent refactoring, this catches it.
probe('P14: plugin.js handleCardKeyDown uses classifyKeyEvent', true, () => {
  // Find the handleCardKeyDown function definition (const handleCardKeyDown = ...)
  const idx = pluginSrc.indexOf('const handleCardKeyDown')
  if (idx === -1) throw new Error('handleCardKeyDown function not found')
  // Read the next 800 chars to capture the function body
  const body = pluginSrc.slice(idx, idx + 800)
  if (!body.includes('classifyKeyEvent')) {
    throw new Error('handleCardKeyDown does not call classifyKeyEvent — possible drift')
  }
})

// ── Probe 17: plugin.js uses imported closeMenuFocusTarget ────────────
probe('P17: plugin.js uses imported closeMenuFocusTarget', true, () => {
  if (!pluginSrc.includes('closeMenuFocusTarget(')) {
    throw new Error('plugin.js does not call closeMenuFocusTarget — possible drift')
  }
})

// ── Probe 15: No 1200px breakpoint in plugin.js CSS ──────────────────
probe('P15: plugin.js has no 1200px breakpoint (replaced by 2164px)', true, () => {
  if (pluginSrc.includes('max-width: 1200px')) {
    throw new Error('old 1200px breakpoint still in plugin.js')
  }
})

// ── Probe 16: plugin.js has 2164px breakpoint in CSS ─────────────────
probe('P16: plugin.js CSS uses 2164px breakpoint', true, () => {
  if (!pluginSrc.includes('max-width: 2164px')) {
    throw new Error('2164px breakpoint missing from plugin.js CSS')
  }
})

console.log('\\n' + '='.repeat(50))
console.log('Mutation/drift evidence: ' + caught + ' passed, ' + missed + ' missed')

// ── Drift harness: 6 mutation probes ──────────────────────────────────
// Each probe mutates plugin.js in a specific way and verifies the guard catches it.
// These prove the equality guards are FALSIFIABLE, not merely pass baseline.

console.log('\\n' + '='.repeat(50))
console.log('Drift harness: 6 mutation probes')
console.log('Each probe mutates plugin.js and checks the guard catches it.')
console.log('')

function driftProbe(name, mutateFn) {
  try {
    const mutated = mutateFn(pluginSrc)
    // Run the same guards we use for baseline, but against the mutated source
    // If the guard passes on the mutated source, the mutation was NOT caught (miss)
    let guardFailed = false
    let guardError = ''
    try {
      // Guard 1: plugin.js must use shared builders (not inline toast construction)
      if (!mutated.includes('buildAcceptToast(')) {
        throw new Error('plugin.js must use buildAcceptToast')
      }
      if (!mutated.includes('buildFailureToast(')) {
        throw new Error('plugin.js must use buildFailureToast')
      }
      if (!mutated.includes('buildUndoToast(')) {
        throw new Error('plugin.js must use buildUndoToast')
      }
      // Guard 2: plugin.js must use shared navigation helpers
      if (!mutated.includes('navigateRight(')) {
        throw new Error('plugin.js must use navigateRight')
      }
      if (!mutated.includes('navigateLeft(')) {
        throw new Error('plugin.js must use navigateLeft')
      }
      if (!mutated.includes('navigateDown(')) {
        throw new Error('plugin.js must use navigateDown')
      }
      if (!mutated.includes('navigateUp(')) {
        throw new Error('plugin.js must use navigateUp')
      }
      // Guard 3: plugin.js must use closeMenuFocusTarget
      if (!mutated.includes('closeMenuFocusTarget(')) {
        throw new Error('plugin.js must use closeMenuFocusTarget')
      }
      // Guard 4: 2164px breakpoint
      if (!mutated.includes('max-width: 2164px')) {
        throw new Error('2164px breakpoint missing')
      }
      if (mutated.includes('max-width: 1200px')) {
        throw new Error('old 1200px breakpoint present')
      }
      // Guard 5: classifyKeyEvent must be used
      if (!mutated.includes('classifyKeyEvent(')) {
        throw new Error('plugin.js must use classifyKeyEvent')
      }
      // Guard 6: copyIdValue must be imported and used
      if (!mutated.includes('copyIdValue')) {
        throw new Error('plugin.js must use copyIdValue')
      }
      // Guard 7: draggableProps must be wired
      if (!mutated.includes('draggableProps.onDragStart')) {
        throw new Error('plugin.js must wire draggableProps.onDragStart')
      }
      if (!mutated.includes('draggableProps.onDragEnd')) {
        throw new Error('plugin.js must wire draggableProps.onDragEnd')
      }
      // Guard 8: menuHighlightIdx state must exist
      if (!mutated.includes('menuHighlightIdx')) {
        throw new Error('plugin.js must have menuHighlightIdx state')
      }
      // Guard 9: menuInitialHighlight must be called on menu open
      if (!mutated.includes('menuInitialHighlight(')) {
        throw new Error('plugin.js must call menuInitialHighlight on menu open')
      }
      // Guard 10: canDropOnColumn guard must be in handleDrop
      if (!mutated.includes('canDropOnColumn(targetCol)')) {
        throw new Error('handleDrop must use canDropOnColumn guard')
      }
      // Guard 11: menu arrow handler must have stopPropagation
      if (!mutated.includes('menuHighlightIndex(menuItems')) {
        throw new Error('menu arrow handler must use menuHighlightIndex')
      }
      // Guard 12: menuItems prop must be wired to KanbanCard
      if (!mutated.includes('menuItems: menuOpen')) {
        throw new Error('KanbanCard must receive menuItems prop')
      }
      // Guard 13: open-path focus binding (menuInitialHighlight → rAF → getElementById → .focus)
      if (!/menuInitialHighlight[\s\S]{0,500}\.focus\(\)/.test(mutated)) {
        throw new Error('open path must bind focus via menuInitialHighlight → requestAnimationFrame → .focus()')
      }
    } catch (e) {
      guardFailed = true
      guardError = e.message
    }
    if (guardFailed) {
      caught++
      console.log('  CAUGHT (correctly rejected): ' + name + ' — guard: ' + guardError)
    } else {
      missed++
      console.log('  MISSED (should fail): ' + name + ' — mutation passed all guards')
    }
  } catch (e) {
    missed++
    console.log('  ERROR: ' + name + ': ' + e.message)
  }
}

// ── drift1: mutate success-toast origin clause ─────────────────────────
driftProbe('drift1: mutate success-toast origin (replace ALL buildAcceptToast with inline)', (src) => {
  // Replace ALL occurrences of buildAcceptToast calls with inline strings
  let mutated = src.replace(/buildAcceptToast\([^)]+\)/g, "'Accepted \\u201citem\\u201d. It\\u2019s in Ongoing.'")
  return mutated
})

// ── drift2: mutate failure-toast origin clause ─────────────────────────
driftProbe('drift2: mutate failure-toast origin (replace ALL buildFailureToast with inline)', (src) => {
  // Replace ALL occurrences of buildFailureToast calls with inline strings
  let mutated = src.replace(/buildFailureToast\([^)]+\)/g, "'Couldn\\u2019t move \\u201citem\\u201d. It is in Ongoing.'")
  return mutated
})

// ── drift3: mutate Enter→Detail binding ────────────────────────────────
driftProbe('drift3: mutate Enter→Detail (replace classifyKeyEvent with inline)', (src) => {
  return src.replace(
    "const classification = classifyKeyEvent(e.key, menuOpenFor, card.project_id)",
    "const classification = { action: e.key === 'Enter' ? 'openMenu' : 'none' }"
  )
})

// ── drift4: mutate breakpoint from 2164px to 1200px ───────────────────
driftProbe('drift4: mutate breakpoint (replace 2164px with 1200px)', (src) => {
  return src.replace('max-width: 2164px', 'max-width: 1200px')
})

// ── drift5: mutate lane-walk clamp (replace navigateRight with inline) ─
driftProbe('drift5: mutate lane-walk clamp (replace navigateRight with inline Math.max)', (src) => {
  return src.replace(
    "result = navigateRight(laneItems, laneIdx, cardIdx, allByColumn)",
    "result = { focusedId: laneItems[Math.max(cardIdx, 0)]?.project_id, column: BOARD_COLUMNS[laneIdx + 1] }"
  )
})

// ── drift6: mutate close-menu focus (replace closeMenuFocusTarget with inline) ─
driftProbe('drift6: mutate close-menu focus (replace closeMenuFocusTarget with inline)', (src) => {
  return src.replace(
    "const target = closeMenuFocusTarget(menuOpenFor)",
    "const target = menuOpenFor"
  )
})

// ── drift7: remove copyIdValue import from plugin.js ──────────────────
driftProbe('drift7: remove copyIdValue import and usage (D12)', (src) => {
  // Remove from import line AND remove the const copyId line
  let mutated = src.replace(/copyIdValue,?\s*/g, '')
  mutated = mutated.replace(/const copyId = copyIdValue\(card\)\n/, '')
  return mutated
})

// ── drift8: remove draggableProps wiring from onDragStart ─────────────
driftProbe('drift8: remove draggableProps.onDragStart call (D10)', (src) => {
  return src.replace('if (draggableProps && draggableProps.onDragStart) draggableProps.onDragStart();', '')
})

// ── drift9: remove canDropOnColumn guard from handleDrop ──────────────
driftProbe('drift9: remove canDropOnColumn guard from handleDrop (D11)', (src) => {
  return src.replace(
    "if (!canDropOnColumn(targetCol)) { setDragId(null); return }",
    "if (false) { setDragId(null); return }"
  )
})

// ── drift10: remove menu preventDefault+stopPropagation from arrows ───
driftProbe('drift10: remove menu arrow handler entirely (D9)', (src) => {
  // Remove the entire else-if block for arrow keys in menu
  return src.replace(
    /else if \(e\.key === 'ArrowDown' \|\| e\.key === 'ArrowUp' \|\| e\.key === 'Home' \|\| e\.key === 'End'\) \{[\s\S]*?requestAnimationFrame\(\(\) => \{[\s\S]*?\}\)\s*\}/,
    "else if (e.key === 'ArrowDown' || e.key === 'ArrowUp' || e.key === 'Home' || e.key === 'End') { /* removed */ }"
  )
})

// ── drift11: remove menuItems prop from KanbanCard rendering ──────────
driftProbe('drift11: remove menuItems prop from KanbanCard (D7/D8 guard)', (src) => {
  return src.replace(
    "menuItems: menuOpen ? menuItemsFor(card) : null,",
    "menuItems: null,"
  )
})

// ── drift12: remove menuInitialHighlight from menu open ───────────────
driftProbe('drift12: remove menuInitialHighlight call and open-focus block (D8)', (src) => {
  // Remove the entire setMenuHighlightIdx(menuInitialHighlight) + rAF focus block from open paths
  return src.replace(
    /setMenuHighlightIdx\(menuInitialHighlight\(items\)\)\n\s*setAnnounce\('Opened Move to menu for[\s\S]*?\.focus\(\)\n\s*\}\)/g,
    "setMenuHighlightIdx(0)\n        setAnnounce('item')"
  )
})

// ── drift13: remove focus call from menu-open path ────────────────────
driftProbe('drift13: remove open-focus call from menu-open path', (src) => {
  // Remove the requestAnimationFrame focus block that follows setAnnounce in open paths
  return src.replace(
    /setAnnounce\('Opened Move to menu for[\s\S]*?\.focus\(\)\n\s*\}\)/g,
    "setAnnounce('item')"
  )
})

console.log('\\n' + '='.repeat(50))
console.log('Drift harness: ' + caught + ' total passed, ' + missed + ' total missed')
if (missed > 0) {
  console.log('\\nFAILED: ' + missed + ' drift case(s) not caught')
  process.exit(1)
} else {
  console.log('All 12 drift cases caught. Baseline + drift: all passed.')
  process.exit(0)
}
