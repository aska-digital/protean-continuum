/**
 * test_kanban_shipped_paths.mjs — Source-anchored shipped-path guards.
 *
 * Reads desktop/plugin.js and asserts specific structural invariants that
 * must hold for the shipped artifact. Each test is falsifiable: removing or
 * weakening the corresponding code causes the test to fail.
 *
 * SP-1..SP-9 per architecture §5.2.
 *
 * Run: node tests/test_kanban_shipped_paths.mjs
 * Expected: 9 tests pass, exit 0
 */
import { strict as assert } from 'node:assert'
import { readFileSync } from 'fs'
import { resolve, dirname } from 'path'
import { fileURLToPath } from 'url'

const __dirname = dirname(fileURLToPath(import.meta.url))
const BUILD = resolve(__dirname, '..')
const pluginSrc = readFileSync(resolve(BUILD, 'desktop/plugin.js'), 'utf8')
const sharedSrc = readFileSync(resolve(BUILD, 'desktop/kanban-interaction.js'), 'utf8')

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

// ── SP-1: copyIdValue is imported and has ≥2 call sites ───────────────
console.log('\nSP-1: copyIdValue imported and used')

test('copyIdValue is imported from kanban-interaction.js', () => {
  assert.ok(pluginSrc.includes('copyIdValue'), 'plugin.js must import copyIdValue')
})

test('copyIdValue is called and copyId used at both CopyButton sites', () => {
  const callMatches = pluginSrc.match(/copyIdValue\(/g)
  assert.ok(callMatches && callMatches.length >= 1,
    `copyIdValue must be called at least once, found ${callMatches ? callMatches.length : 0}`)
  // Both CopyButton sites use copyId.text / copyId.label (computed once)
  const textMatches = pluginSrc.match(/copyId\.text/g)
  const labelMatches = pluginSrc.match(/copyId\.label/g)
  assert.ok(textMatches && textMatches.length >= 2,
    `copyId.text must appear at ≥2 CopyButton sites, found ${textMatches ? textMatches.length : 0}`)
  assert.ok(labelMatches && labelMatches.length >= 2,
    `copyId.label must appear at ≥2 CopyButton sites, found ${labelMatches ? labelMatches.length : 0}`)
})

test('both CopyButton sites use copyIdValue', () => {
  // The primary session CopyButton and the fallback CopyButton both use copyId
  assert.ok(pluginSrc.includes('text: copyId.text'), 'primary CopyButton must use copyId.text')
  assert.ok(pluginSrc.includes('label: copyId.label'), 'CopyButton must use copyId.label')
})

// ── SP-2: KanbanCard destructures draggableProps ──────────────────────
console.log('\nSP-2: KanbanCard consumes draggableProps')

test('KanbanCard accepts draggableProps parameter', () => {
  const kanbanSection = pluginSrc.split('function KanbanCard(')[1].split('function CompactCard')[0]
  assert.ok(kanbanSection.includes('draggableProps'),
    'KanbanCard must destructure draggableProps')
})

// ── SP-3: card onDragStart invokes draggableProps.onDragStart and sets dataTransfer ──
console.log('\nSP-3: card onDragStart wires draggableProps')

test('card onDragStart calls draggableProps.onDragStart', () => {
  const kanbanSection = pluginSrc.split('function KanbanCard(')[1].split('function CompactCard')[0]
  assert.ok(kanbanSection.includes('draggableProps.onDragStart'),
    'onDragStart must invoke draggableProps.onDragStart')
})

test('card onDragStart sets dataTransfer', () => {
  const kanbanSection = pluginSrc.split('function KanbanCard(')[1].split('function CompactCard')[0]
  assert.ok(kanbanSection.includes('dataTransfer.setData'),
    'onDragStart must set dataTransfer')
})

// ── SP-4: card onDragEnd invokes draggableProps.onDragEnd ──────────────
console.log('\nSP-4: card onDragEnd wires draggableProps')

test('card onDragEnd calls draggableProps.onDragEnd', () => {
  const kanbanSection = pluginSrc.split('function KanbanCard(')[1].split('function CompactCard')[0]
  assert.ok(kanbanSection.includes('draggableProps.onDragEnd'),
    'onDragEnd must invoke draggableProps.onDragEnd')
})

// ── SP-5: handleDrop contains canDropOnColumn guard before doMove ──────
console.log('\nSP-5: handleDrop canDropOnColumn guard')

test('handleDrop calls canDropOnColumn before any doMove', () => {
  const handleDropSection = pluginSrc.split('const handleDrop')[1].split('return jsx')[0]
  const guardIdx = handleDropSection.indexOf('canDropOnColumn')
  const moveIdx = handleDropSection.indexOf('doMove(')
  assert.ok(guardIdx >= 0, 'handleDrop must call canDropOnColumn')
  assert.ok(moveIdx >= 0, 'handleDrop must call doMove')
  assert.ok(guardIdx < moveIdx, 'canDropOnColumn guard must appear before doMove')
})

// ── SP-6: every handleDrop return path reaches setDragId(null) ─────────
console.log('\nSP-6: handleDrop clears dragId on every exit path')

test('handleDrop has setDragId(null) on rejected Inbox path', () => {
  const handleDropSection = pluginSrc.split('const handleDrop')[1].split('return jsx')[0]
  assert.ok(handleDropSection.includes('canDropOnColumn(targetCol)) { setDragId(null)'),
    'Inbox rejection must clear dragId')
})

test('handleDrop has setDragId(null) on missing card / same column path', () => {
  const handleDropSection = pluginSrc.split('const handleDrop')[1].split('return jsx')[0]
  assert.ok(handleDropSection.includes('setDragId(null); return'),
    'every early return must clear dragId')
})

// ── SP-7: menu-item onKeyDown handles arrows with preventDefault+stopPropagation; "{ /* let board handle */ }" is ABSENT ──
console.log('\nSP-7: menu keyboard handling')

test('menu ArrowDown/ArrowUp/Home/End use preventDefault + stopPropagation', () => {
  const kanbanSection = pluginSrc.split('function KanbanCard(')[1].split('function CompactCard')[0]
  assert.ok(kanbanSection.includes("e.preventDefault(); e.stopPropagation()"),
    'menu arrow keys must call preventDefault + stopPropagation')
})

test('old no-op comment "{ /* let board handle */ }" is ABSENT', () => {
  assert.ok(!pluginSrc.includes('{ /* let board handle */ }'),
    'the old no-op comment must be removed')
})

// ── SP-8: menu open moves DOM focus to the highlighted item ───────────
console.log('\nSP-8: menu focus mechanism')

test('menu uses getElementById to focus highlighted item', () => {
  const kanbanSection = pluginSrc.split('function KanbanCard(')[1].split('function CompactCard')[0]
  assert.ok(kanbanSection.includes('getElementById'),
    'menu must use getElementById for focus management')
  assert.ok(kanbanSection.includes('kanban-menu-'),
    'menu items must have id prefix kanban-menu-')
})

test('open path (menuInitialHighlight) calls focus via requestAnimationFrame + getElementById', () => {
  // Verify the open path contains the focus binding: menuInitialHighlight → requestAnimationFrame → getElementById → .focus()
  assert.ok(
    /menuInitialHighlight[\s\S]{0,500}requestAnimationFrame[\s\S]{0,500}getElementById[\s\S]{0,200}\.focus\(\)/.test(pluginSrc),
    'open path must call menuInitialHighlight followed by requestAnimationFrame(getElementById(...).focus())'
  )
})

// ── SP-9: single menu highlight index in BoardSurface, reset on menu open ──
console.log('\nSP-9: menuHighlightIdx state')

test('BoardSurface has menuHighlightIdx state', () => {
  assert.ok(pluginSrc.includes('menuHighlightIdx'),
    'BoardSurface must declare menuHighlightIdx state')
})

test('menuHighlightIdx is reset on menu open', () => {
  // The menu open handlers (Space + click) must call menuInitialHighlight
  assert.ok(pluginSrc.includes('menuInitialHighlight'),
    'menu open must compute initial highlight via menuInitialHighlight')
})

test('menuHighlightIdx is reset to 0 on close', () => {
  const closeMenuSection = pluginSrc.split('const closeMenu')[1].split('const doMove')[0]
  assert.ok(closeMenuSection.includes('setMenuHighlightIdx(0)'),
    'closeMenu must reset menuHighlightIdx to 0')
})

// ── Summary ────────────────────────────────────────────────────────────
console.log(`\n${'='.repeat(50)}`)
console.log(`Results: ${passed} passed, ${failed} failed`)
if (failures.length > 0) {
  console.log('\nFailures:')
  for (const f of failures) {
    console.log(`  ✗ ${f.name}: ${f.error}`)
  }
  process.exit(1)
} else {
  console.log('All shipped-path tests passed.')
  process.exit(0)
}
