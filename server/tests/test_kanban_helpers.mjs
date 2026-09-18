/**
 * test_kanban_helpers.mjs — Behavioral tests for kanban interaction logic.
 *
 * These tests exercise the shared pure helpers from desktop/kanban-interaction.js.
 * For every export consumed by desktop/plugin.js, the tested artifact is the same
 * module the shipped artifact imports. Exports NOT consumed by plugin.js are
 * helper-unit supplements and carry no shipped-behavior claim.
 *
 * Run: node tests/test_kanban_helpers.mjs
 * Expected: all tests pass, exit 0
 */
import { strict as assert } from 'node:assert'
import { readFileSync } from 'fs'
import { resolve, dirname } from 'path'
import { fileURLToPath } from 'url'

const __dirname = dirname(fileURLToPath(import.meta.url))
const BUILD = resolve(__dirname, '..')
const pluginSrc = readFileSync(resolve(BUILD, 'desktop/plugin.js'), 'utf8')

import {
  BOARD_COLUMNS,
  COLUMN_LABELS,
  buildAcceptToast,
  buildAcceptActions,
  buildFailureToast,
  buildFailureActions,
  buildUndoToast,
  buildUndoActions,
  navigateRight,
  navigateLeft,
  navigateDown,
  navigateUp,
  navigateHome,
  navigateEnd,
  classifyKeyEvent,
  closeMenuFocusTarget,
  resolveLayout,
  laneMinWidth,
  reducedMotionCSS,
  canDropOnColumn,
  focusAfterMove,
  copyIdValue,
  menuItemsFor,
  menuInitialHighlight,
  menuHighlightIndex,
  menuActivate,
} from '../desktop/kanban-interaction.js'

let passed = 0
let failed = 0
const failures = []

function test(name, fn) {
  try {
    fn()
    passed++
    console.log(`  ✓ ${name}`)
  } catch (e) {
    failed++
    failures.push({ name, error: e.message })
    console.log(`  ✗ ${name}`)
    console.log(`    ${e.message}`)
  }
}

// ── KB-T1: Toast success — exact message, singular Undo action ────────
console.log('\nKB-T1: toast success')

test('accept toast contains name and COLUMN_LABELS destination', () => {
  const msg = buildAcceptToast('my-project', 'ongoing')
  assert.equal(msg, 'Accepted \u201cmy-project\u201d. It\u2019s in Ongoing.')
})

test('accept toast uses Unicode curly quotes and apostrophe', () => {
  const msg = buildAcceptToast('test', 'blocked')
  // \u201c = left double quote, \u201d = right double quote, \u2019 = right single quote
  assert.ok(msg.includes('\u201c'), 'must contain left curly double quote')
  assert.ok(msg.includes('\u201d'), 'must contain right curly double quote')
  assert.ok(msg.includes('\u2019'), 'must contain curly apostrophe')
})

test('accept toast has exactly one action: Undo', () => {
  const action = buildAcceptActions('audit-123')
  assert.equal(action.label, 'Undo')
  assert.equal(action.auditId, 'audit-123')
})

test('accept toast uses COLUMN_LABELS for destination, not raw column key', () => {
  for (const col of BOARD_COLUMNS) {
    if (col === 'inbox') continue
    const msg = buildAcceptToast('x', col)
    assert.ok(msg.includes(COLUMN_LABELS[col]),
      `toast for ${col} must include label "${COLUMN_LABELS[col]}"`)
    assert.ok(!msg.includes(col) || COLUMN_LABELS[col].includes(col),
      `toast for ${col} must not use raw key "${col}" as display`)
  }
})

test('accept toast with fallback name when name is empty', () => {
  const msg = buildAcceptToast('', 'ongoing')
  assert.ok(msg.includes('item'), 'empty name must fallback to "item"')
})

// ── KB-T2: Toast failure and undo messages ────────────────────────────
console.log('\nKB-T2: toast failure and undo')

test('failure toast contains name and origin COLUMN_LABELS', () => {
  const msg = buildFailureToast('my-project', 'ongoing')
  assert.equal(msg, 'Couldn\u2019t move \u201cmy-project\u201d. It is in Ongoing.')
})

test('failure toast action is Retry (no audit_id)', () => {
  const action = buildFailureActions()
  assert.equal(action.label, 'Retry')
  assert.equal(action.auditId, undefined, 'Retry must not carry audit_id')
})

test('undo success toast has no action', () => {
  const action = buildUndoActions()
  assert.equal(action, null, 'undo toast must have no action')
})

test('undo toast says "back in Inbox"', () => {
  const msg = buildUndoToast('my-project')
  assert.ok(msg.includes('Undone'), 'must start with Undone')
  assert.ok(msg.includes('back in Inbox'), 'must say back in Inbox')
})

test('undo toast contains the project name', () => {
  const msg = buildUndoToast('special-project')
  assert.ok(msg.includes('special-project'))
})

// ── KB-K1: Keyboard handlers — all required keys produce valid actions ─
console.log('\nKB-K1: keyboard handlers')

test('ArrowLeft produces navigateLeft action', () => {
  const r = classifyKeyEvent('ArrowLeft', null, 'p1')
  assert.equal(r.action, 'navigateLeft')
})

test('ArrowRight produces navigateRight action', () => {
  const r = classifyKeyEvent('ArrowRight', null, 'p1')
  assert.equal(r.action, 'navigateRight')
})

test('ArrowUp produces navigateUp action', () => {
  const r = classifyKeyEvent('ArrowUp', null, 'p1')
  assert.equal(r.action, 'navigateUp')
})

test('ArrowDown produces navigateDown action', () => {
  const r = classifyKeyEvent('ArrowDown', null, 'p1')
  assert.equal(r.action, 'navigateDown')
})

test('Home produces navigateHome action', () => {
  const r = classifyKeyEvent('Home', null, 'p1')
  assert.equal(r.action, 'navigateHome')
})

test('End produces navigateEnd action', () => {
  const r = classifyKeyEvent('End', null, 'p1')
  assert.equal(r.action, 'navigateEnd')
})

test('Escape with no menu open does nothing', () => {
  const r = classifyKeyEvent('Escape', null, 'p1')
  assert.equal(r.action, 'none')
})

// ── KB-K2: Enter → Detail, Space → Menu (Shayba C-1 split) ───────────
console.log('\nKB-K2: Enter→Detail, Space→Menu split')

test('Enter when menu closed → openDetail', () => {
  const r = classifyKeyEvent('Enter', null, 'p1')
  assert.equal(r.action, 'openDetail')
})

test('Enter when OTHER card menu open → none (menu blocks detail)', () => {
  const r = classifyKeyEvent('Enter', 'other-card', 'p1')
  assert.equal(r.action, 'none')
})

test('Space when menu closed → openMenu', () => {
  const r = classifyKeyEvent(' ', null, 'p1')
  assert.equal(r.action, 'openMenu')
})

test('Space when THIS card menu open → closeMenu', () => {
  const r = classifyKeyEvent(' ', 'p1', 'p1')
  assert.equal(r.action, 'closeMenu')
})

test('Space when OTHER card menu open → openMenu (toggles current card)', () => {
  const r = classifyKeyEvent(' ', 'other-card', 'p1')
  assert.equal(r.action, 'openMenu')
})

// ── KB-K3: Escape restores focus and no mutation ──────────────────────
console.log('\nKB-K3: Escape restores focus, no mutation')

test('closeMenu returns the pid that should receive focus', () => {
  assert.equal(closeMenuFocusTarget('p1'), 'p1')
  assert.equal(closeMenuFocusTarget('p2'), 'p2')
})

test('closeMenu with null returns null (no-op)', () => {
  assert.equal(closeMenuFocusTarget(null), null)
})

test('Escape on card with open menu → closeMenu action', () => {
  const r = classifyKeyEvent('Escape', 'p1', 'p1')
  assert.equal(r.action, 'closeMenu')
})

// ── KB-R1: Responsive layout decision ─────────────────────────────────
console.log('\nKB-R1 (helper-unit supplement): stacked reflow layout')

test('width <= 2164 → stacked mode with 1fr grid', () => {
  const layout = resolveLayout(2164)
  assert.equal(layout.mode, 'stacked')
  assert.equal(layout.gridTemplateColumns, '1fr')
  assert.equal(layout.overflowX, 'hidden')
})

test('width 1200 → stacked mode (old 1200px breakpoint subsumed)', () => {
  const layout = resolveLayout(1200)
  assert.equal(layout.mode, 'stacked')
})

test('width 800 → stacked mode', () => {
  assert.equal(resolveLayout(800).mode, 'stacked')
})

test('width > 2164 → grid mode with 8-column repeat', () => {
  const layout = resolveLayout(2165)
  assert.equal(layout.mode, 'grid')
  assert.equal(layout.gridTemplateColumns, 'repeat(8, minmax(260px, 1fr))')
  assert.equal(layout.overflowX, 'auto')
})

test('width 3000 → grid mode', () => {
  assert.equal(resolveLayout(3000).mode, 'grid')
})

test('stacked mode lane min-width is 0 (defeats min-width:260px)', () => {
  assert.equal(laneMinWidth('stacked'), '0')
})

test('grid mode lane min-width is 260px', () => {
  assert.equal(laneMinWidth('grid'), '260px')
})

// ── KB-RM1: Reduced motion ────────────────────────────────────────────
console.log('\nKB-RM1 (helper-unit supplement): prefers-reduced-motion')

test('reduced motion disables animation', () => {
  const css = reducedMotionCSS()
  assert.equal(css.animation, 'none')
})

test('reduced motion disables transitions', () => {
  const css = reducedMotionCSS()
  assert.equal(css.transition, 'none')
})

test('reduced motion sets scroll-behavior to auto', () => {
  const css = reducedMotionCSS()
  assert.equal(css.scrollBehavior, 'auto')
})

// ── KB-R-Inbox: Inbox is not a drop target ────────────────────────────
console.log('\nKB-R (helper-unit supplement): Inbox not drop target')

test('canDropOnColumn("inbox") returns false', () => {
  assert.equal(canDropOnColumn('inbox'), false)
})

test('canDropOnColumn("ongoing") returns true', () => {
  assert.equal(canDropOnColumn('ongoing'), true)
})

test('all non-inbox columns are valid drop targets', () => {
  for (const col of BOARD_COLUMNS) {
    if (col === 'inbox') {
      assert.equal(canDropOnColumn(col), false, `inbox must not be a drop target`)
    } else {
      assert.equal(canDropOnColumn(col), true, `${col} must be a drop target`)
    }
  }
})

// ── KB-Focus: Focus follows moved card ────────────────────────────────
console.log('\nKB-Focus (helper-unit supplement): focus follows moved card')

test('focusAfterMove returns the moved card project_id', () => {
  assert.equal(focusAfterMove('p1'), 'p1')
  assert.equal(focusAfterMove('p2'), 'p2')
})

// ── KB-Copy: Copy ID value contract ───────────────────────────────────
console.log('\nKB-Copy (helper-unit supplement): Copy ID value contract')

test('card with sessions: Copy ID uses bare session_id', () => {
  const card = {
    project_id: 'proj-1',
    sessions: [{ session_id: 'sess-abc', profile: 'alpha' }],
  }
  const result = copyIdValue(card)
  assert.equal(result.text, 'sess-abc')
  assert.equal(result.label, 'Copy ID')
})

test('card without sessions: Copy ID falls back to project_id', () => {
  const card = { project_id: 'proj-1', sessions: [] }
  const result = copyIdValue(card)
  assert.equal(result.text, 'proj-1')
  assert.equal(result.label, 'Copy project ID')
})

test('Copy ID text is bare session_id, not "profile/session_id"', () => {
  const card = {
    project_id: 'proj-1',
    sessions: [{ session_id: 'sess-abc', profile: 'alpha' }],
  }
  const result = copyIdValue(card)
  assert.ok(!result.text.includes('/'), 'must not contain slash')
  assert.ok(!result.text.includes('alpha'), 'must not contain profile name')
})

test('Copy ID never uses project_id when sessions exist', () => {
  const card = {
    project_id: 'proj-1',
    sessions: [{ session_id: 'sess-abc', profile: 'alpha' }],
  }
  const result = copyIdValue(card)
  assert.notEqual(result.text, card.project_id,
    'Copy ID must use session_id, not project_id')
})

// ── Equality guards: shared builder outputs vs plugin.js bindings ─────
console.log('\nEquality guards: builders match shipped bindings')

test('buildAcceptToast output matches plugin.js builder usage', () => {
  const msg = buildAcceptToast('TestName', 'ongoing')
  assert.ok(pluginSrc.includes('buildAcceptToast('), 'plugin.js must call buildAcceptToast')
  assert.ok(msg.startsWith('Accepted \u201c'), 'builder must start with Accepted + left quote')
  assert.ok(msg.includes('\u2019s in'), 'builder must contain curly apostrophe + s in')
  assert.ok(msg.endsWith('Ongoing.'), 'builder must end with label + period')
})

test('buildFailureToast output matches plugin.js builder usage', () => {
  const msg = buildFailureToast('TestName', 'blocked')
  assert.ok(pluginSrc.includes('buildFailureToast('), 'plugin.js must call buildFailureToast')
  assert.ok(msg.startsWith('Couldn\u2019t move \u201c'), 'builder must start with Couldnt move + left quote')
  assert.ok(msg.includes('It is in'), 'builder must contain It is in')
  assert.ok(msg.endsWith('Blocked.'), 'builder must end with label + period')
})

test('buildUndoToast output matches plugin.js builder usage', () => {
  const msg = buildUndoToast('TestName')
  assert.ok(pluginSrc.includes('buildUndoToast('), 'plugin.js must call buildUndoToast')
  assert.ok(msg.startsWith('Undone. \u201c'), 'builder must start with Undone + left quote')
  assert.ok(msg.includes('is back in Inbox'), 'builder must contain is back in Inbox')
})

test('buildAcceptActions produces singular Undo action (no View)', () => {
  const action = buildAcceptActions('audit-999')
  assert.equal(action.label, 'Undo')
  assert.equal(action.auditId, 'audit-999')
  // plugin.js must use buildAcceptActions and no viewAction
  assert.ok(pluginSrc.includes('buildAcceptActions('), 'plugin.js must use buildAcceptActions')
  assert.ok(!pluginSrc.includes('viewAction'), 'plugin.js must not have viewAction')
})

test('buildFailureActions produces Retry action', () => {
  const action = buildFailureActions()
  assert.equal(action.label, 'Retry')
  assert.ok(pluginSrc.includes('buildFailureActions('), 'plugin.js must use buildFailureActions')
})

test('closeMenuFocusTarget returns menuOpenFor value (identity)', () => {
  assert.equal(closeMenuFocusTarget('card-1'), 'card-1')
  assert.equal(closeMenuFocusTarget(null), null)
  // Verify plugin.js calls closeMenuFocusTarget (not inline prev=menuOpenFor)
  assert.ok(pluginSrc.includes('closeMenuFocusTarget('), 'plugin.js must call closeMenuFocusTarget')
})

// ── KB-Navigation: Arrow navigation through lanes ─────────────────────
console.log('\nKB-Navigation: arrow key navigation')

test('ArrowRight moves to next non-empty lane', () => {
  const allByColumn = {
    inbox: [{ project_id: 'p1' }],
    ongoing: [{ project_id: 'p2' }],
    blocked: [],
    waiting_on_you: [{ project_id: 'p3' }],
  }
  const result = navigateRight([], 0, 0, allByColumn)
  assert.equal(result.focusedId, 'p2')
  assert.equal(result.column, 'ongoing')
})

test('ArrowRight skips empty lanes', () => {
  const allByColumn = {
    inbox: [{ project_id: 'p1' }],
    ongoing: [],
    blocked: [],
    waiting_on_you: [{ project_id: 'p3' }],
  }
  const result = navigateRight([], 0, 0, allByColumn)
  assert.equal(result.focusedId, 'p3')
  assert.equal(result.column, 'waiting_on_you')
})

test('ArrowRight at last lane returns null', () => {
  const allByColumn = { scrapped: [{ project_id: 'p1' }] }
  const result = navigateRight([], 7, 0, allByColumn)
  assert.equal(result, null)
})

test('ArrowLeft moves to previous non-empty lane', () => {
  const allByColumn = {
    inbox: [{ project_id: 'p1' }],
    ongoing: [{ project_id: 'p2' }],
    blocked: [{ project_id: 'p3' }],
  }
  const result = navigateLeft([], 2, 0, allByColumn)
  assert.equal(result.focusedId, 'p2')
  assert.equal(result.column, 'ongoing')
})

test('ArrowLeft at first lane returns null', () => {
  const allByColumn = { inbox: [{ project_id: 'p1' }] }
  const result = navigateLeft([], 0, 0, allByColumn)
  assert.equal(result, null)
})

test('ArrowDown moves to next card in same lane', () => {
  const laneItems = [{ project_id: 'p1' }, { project_id: 'p2' }, { project_id: 'p3' }]
  const result = navigateDown(laneItems, 0)
  assert.equal(result.focusedId, 'p2')
})

test('ArrowDown at last card returns null', () => {
  const laneItems = [{ project_id: 'p1' }]
  const result = navigateDown(laneItems, 0)
  assert.equal(result, null)
})

test('ArrowUp moves to previous card in same lane', () => {
  const laneItems = [{ project_id: 'p1' }, { project_id: 'p2' }]
  const result = navigateUp(laneItems, 1)
  assert.equal(result.focusedId, 'p1')
})

test('ArrowUp at first card returns null', () => {
  const laneItems = [{ project_id: 'p1' }]
  const result = navigateUp(laneItems, 0)
  assert.equal(result, null)
})

test('Home moves to first card in lane', () => {
  const laneItems = [{ project_id: 'p1' }, { project_id: 'p2' }, { project_id: 'p3' }]
  const result = navigateHome(laneItems)
  assert.equal(result.focusedId, 'p1')
})

test('End moves to last card in lane', () => {
  const laneItems = [{ project_id: 'p1' }, { project_id: 'p2' }, { project_id: 'p3' }]
  const result = navigateEnd(laneItems)
  assert.equal(result.focusedId, 'p3')
})

test('Home on empty lane returns null', () => {
  assert.equal(navigateHome([]), null)
})

test('End on empty lane returns null', () => {
  assert.equal(navigateEnd([]), null)
})

test('ArrowRight clamps cardIdx at target lane length', () => {
  // cardIdx=5 but target lane has only 2 items → focus item at index 1
  const allByColumn = {
    inbox: [{ project_id: 'p1' }],
    ongoing: [{ project_id: 'p2' }, { project_id: 'p3' }],
  }
  const result = navigateRight([], 0, 5, allByColumn)
  assert.equal(result.focusedId, 'p3') // min(5, 2-1) = 1 → p3
})

// ── KB-Menu: Context menu item generation and keyboard navigation ─────
console.log('\nKB-Menu: context menu items and keyboard navigation')

test('menuItemsFor excludes the card current column and excludes inbox; appends Cancel last', () => {
  const card = { column: 'ongoing' }
  const items = menuItemsFor(card)
  const targets = items.map(i => i.target)
  assert.ok(!targets.includes('ongoing'), 'must not include own column')
  assert.ok(!targets.includes('inbox'), 'must not include inbox')
  assert.equal(items[items.length - 1].kind, 'cancel', 'last item must be Cancel')
})

test('menuInitialHighlight returns first enabled item', () => {
  const items = [
    { kind: 'move', target: 'blocked', disabled: false },
    { kind: 'move', target: 'waiting_on_you', disabled: false },
    { kind: 'cancel', target: null, disabled: false },
  ]
  assert.equal(menuInitialHighlight(items), 0)
})

test('ArrowDown moves to next enabled item, skipping disabled entries', () => {
  const items = [
    { kind: 'move', target: 'blocked', disabled: false },
    { kind: 'move', target: 'waiting_on_you', disabled: true },
    { kind: 'move', target: 'done', disabled: false },
    { kind: 'cancel', target: null, disabled: false },
  ]
  assert.equal(menuHighlightIndex(items, 0, 'ArrowDown'), 2)
})

test('ArrowUp moves to previous enabled item, symmetric to ArrowDown', () => {
  const items = [
    { kind: 'move', target: 'blocked', disabled: false },
    { kind: 'move', target: 'waiting_on_you', disabled: false },
    { kind: 'move', target: 'done', disabled: false },
    { kind: 'move', target: 'scrapped', disabled: true },
    { kind: 'cancel', target: null, disabled: false },
  ]
  assert.equal(menuHighlightIndex(items, 4, 'ArrowUp'), 2)
})

test('ArrowDown at last item CLAMPS (no wrap)', () => {
  const items = [
    { kind: 'move', target: 'blocked', disabled: false },
    { kind: 'cancel', target: null, disabled: false },
  ]
  assert.equal(menuHighlightIndex(items, 1, 'ArrowDown'), 1)
})

test('ArrowUp at first item CLAMPS (no wrap)', () => {
  const items = [
    { kind: 'move', target: 'blocked', disabled: false },
    { kind: 'cancel', target: null, disabled: false },
  ]
  assert.equal(menuHighlightIndex(items, 0, 'ArrowUp'), 0)
})

test('Home goes to first enabled; End goes to last enabled (Cancel)', () => {
  const items = [
    { kind: 'move', target: 'blocked', disabled: false },
    { kind: 'move', target: 'waiting_on_you', disabled: false },
    { kind: 'move', target: 'done', disabled: false },
    { kind: 'cancel', target: null, disabled: false },
  ]
  assert.equal(menuHighlightIndex(items, 2, 'Home'), 0)
  assert.equal(menuHighlightIndex(items, 0, 'End'), 3)
})

test('menuActivate returns none for disabled item, close for Cancel, move for enabled target', () => {
  const disabled = { kind: 'move', target: 'blocked', disabled: true }
  const cancel = { kind: 'cancel', target: null, disabled: false }
  const enabled = { kind: 'move', target: 'done', disabled: false }
  assert.equal(menuActivate(disabled), 'none')
  assert.equal(menuActivate(cancel), 'close')
  assert.equal(menuActivate(enabled), 'move')
})

test('menuItemsFor with card in ongoing produces 6 targets + Cancel = 7 items', () => {
  const card = { column: 'ongoing' }
  const items = menuItemsFor(card)
  assert.equal(items.length, 7)
})

test('menuItemsFor always has Cancel as last item regardless of card column', () => {
  const columns = ['inbox', 'ongoing', 'blocked', 'waiting_on_you', 'done', 'scrapped']
  for (const col of columns) {
    const items = menuItemsFor({ column: col })
    assert.equal(items[items.length - 1].kind, 'cancel',
      `Cancel must be last for card in ${col}`)
  }
})

test('menuHighlightIndex with all disabled items returns currentIdx unchanged', () => {
  const items = [
    { kind: 'move', target: 'blocked', disabled: true },
    { kind: 'move', target: 'done', disabled: true },
    { kind: 'cancel', target: null, disabled: true },
  ]
  assert.equal(menuHighlightIndex(items, 1, 'ArrowDown'), 1)
  assert.equal(menuHighlightIndex(items, 1, 'ArrowUp'), 1)
  assert.equal(menuHighlightIndex(items, 1, 'Home'), 1)
  assert.equal(menuHighlightIndex(items, 1, 'End'), 1)
})

test('menuActivate with unknown kind returns none (disabled)', () => {
  const item = { kind: 'unknown', disabled: true }
  assert.equal(menuActivate(item), 'none')
})

// ── Summary ───────────────────────────────────────────────────────────
console.log(`\n${'='.repeat(50)}`)
console.log(`Results: ${passed} passed, ${failed} failed`)
if (failures.length > 0) {
  console.log('\nFailures:')
  for (const f of failures) {
    console.log(`  ✗ ${f.name}: ${f.error}`)
  }
  process.exit(1)
} else {
  console.log('All tests passed.')
  process.exit(0)
}
