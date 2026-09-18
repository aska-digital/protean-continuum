# QA Remediation Implementation Receipt (KodeKoot)

```
task_id:          continuum-azaraki-kanban-qa-remediation
owner:            kodekoot (implementation)
upstream:         handoffs/architecture-continuum-kanban-qa-remediation.md (Azaraki, DECIDED)
date:             2026-09-13
state:            implemented — awaiting Halakukhan re-gate
scope:            build/desktop/plugin.js, build/desktop/kanban-interaction.js,
                  build/tests/test_kanban_helpers.mjs, build/tests/test_kanban_shipped_paths.mjs,
                  build/tests/mutation_evidence.js, build/tests/test_kanban_bounded.py,
                  build/review/qa-remediation-receipt.md
```

## wc line counts (post-implementation)

| file | lines |
|------|-------|
| desktop/plugin.js | 1079 |
| desktop/kanban-interaction.js | 260 |
| tests/test_kanban_helpers.mjs | 617 |
| tests/test_kanban_shipped_paths.mjs | 191 |
| tests/mutation_evidence.js | 405 |
| tests/test_kanban_bounded.py | 1050 |

## Exact anchors (re-derived from final files)

| anchor | location |
|--------|----------|
| import copyIdValue | plugin.js:41 |
| const copyId = copyIdValue(card) | plugin.js:163 |
| CopyButton text: copyId.text (primary) | plugin.js:224 |
| CopyButton text: copyId.text (fallback) | plugin.js:235 |
| KanbanCard draggableProps prop | plugin.js:147 |
| onDragStart draggableProps.onDragStart() | plugin.js:171 |
| onDragEnd draggableProps.onDragEnd() | plugin.js:172 |
| handleDrop canDropOnColumn guard | plugin.js:702 |
| handleDrop setDragId(null) on Inbox reject | plugin.js:703 |
| menuHighlightIdx state | plugin.js:473 |
| menuInitialHighlight on Space open | plugin.js:598 |
| open-focus: Space rAF focus call | plugin.js:601-605 |
| menuInitialHighlight on click open | plugin.js:776 |
| open-focus: click rAF focus call | plugin.js:779-783 |
| menuItemsFor(card) in KanbanCard render | plugin.js:781 |
| menu arrow preventDefault+stopPropagation | plugin.js:286 |
| menu Enter preventDefault+stopPropagation | plugin.js:278 |
| menu Space preventDefault+stopPropagation | plugin.js:295 |
| getElementById kanban-menu- focus (arrow) | plugin.js:290 |
| menuItemsFor defined | kanban-interaction.js:202 |
| menuInitialHighlight defined | kanban-interaction.js:212 |
| menuHighlightIndex defined | kanban-interaction.js:220 |
| menuActivate defined | kanban-interaction.js:255 |

## Scoped helper claim (per architecture §2.3)

"These tests exercise the shared pure helpers from desktop/kanban-interaction.js.
For every export consumed by desktop/plugin.js, the tested artifact is the same
module the shipped artifact imports. Exports NOT consumed by plugin.js are
helper-unit supplements and carry no shipped-behavior claim."

This claim appears verbatim in test_kanban_helpers.mjs header (line 4).
The unscoped "identical by construction" phrase has been superseded and does not
appear in any current receipt or test header.

## D-A / QA-3 implementation

### Bind: copyIdValue
- copyIdValue imported at plugin.js:41
- const copyId = copyIdValue(card) computed once per KanbanCard render (plugin.js:163)
- Both CopyButton sites use copyId.text / copyId.label (plugin.js:224, 235)
- Behavioral equivalence preserved: with primary session → bare session_id + "Copy ID"; without → project_id + "Copy project ID"

### Do NOT bind: resolveLayout, laneMinWidth, reducedMotionCSS, focusAfterMove
These four exports remain as helper-unit supplements in test_kanban_helpers.mjs.
Their Node sections are relabeled "(helper-unit supplement)": KB-R1, KB-RM1, KB-Focus, KB-Copy.
The shipped CSS media queries and inline focus logic are preserved exactly.

### Claim rewrite
- Header claim in test_kanban_helpers.mjs replaced with scoped claim (§2.3)
- "identical by construction" does not appear in any current file

### Python supplements renamed (QA-3b)
1. test_kb_k1_keyboard_handlers_behavioral → test_kb_k1_keyboard_handlers_service_supplement
2. test_kb_k2_roving_tabindex_behavioral → test_kb_k2_roving_tabindex_service_supplement
3. test_kb_k3_escape_behavioral → test_kb_k3_escape_service_supplement
4. test_kb_k4_no_capture_mode_behavioral → test_kb_k4_no_capture_mode_service_supplement
5. test_kb_r1_stacked_reflow_behavioral → test_kb_r1_stacked_reflow_service_supplement
6. test_kb_rm1_reduced_motion_behavioral → test_kb_rm1_reduced_motion_service_supplement

All docstrings updated to: "Service-data supplement. Behavioral proof for the shipped
interaction lives in tests/test_kanban_helpers.mjs and tests/test_kanban_shipped_paths.mjs."
Assertions unchanged; pytest totals 151/52 unchanged.

## D-B / QA-1 implementation

### New pure exports in kanban-interaction.js
- menuItemsFor(card): excludes card.column and inbox; appends enabled Cancel last
- menuInitialHighlight(items): first enabled index; 0 if none
- menuHighlightIndex(items, currentIdx, key): skip-disabled, clamp at ends, no wrap
- menuActivate(item): 'move' for enabled target, 'close' for Cancel, 'none' for disabled

### BoardSurface state
- menuHighlightIdx alongside menuOpenFor (plugin.js:473)
- Reset via menuInitialHighlight on every menu open (Space + click)
- Reset to 0 on closeMenu (plugin.js:489)

### Key handling invariants
- ArrowUp/ArrowDown/Home/End: preventDefault + stopPropagation (plugin.js:285-286)
- DOM focus moves to highlighted item via getElementById + requestAnimationFrame (plugin.js:289-292)
- Arrows never reach handleBoardKeyDown while menu is open
- Enter: preventDefault + stopPropagation, activation via menuActivate → same doMove closure (plugin.js:278-283)
- Space: preventDefault + stopPropagation (reserved for card-level toggle) (plugin.js:295)
- Escape: closeMenu — restores originating card focus, no mutation (plugin.js:275)

### Open-focus fix (QA-1-OPEN-FOCUS)
On every menu-open path (Space/card toggle and Move-to click), DOM focus moves to the
highlighted menu item after menu render via requestAnimationFrame + getElementById:
- Space open path: plugin.js:601-605 — requestAnimationFrame(() => { const openIdx = menuInitialHighlight(items); ... .focus() })
- Click open path: plugin.js:779-783 — same pattern with pid
This ensures Space → Enter works without an intermediate arrow press.

## D-C / QA-2 implementation

### KanbanCard draggableProps
- KanbanCard destructures draggableProps (plugin.js:147)
- onDragStart: sets dataTransfer + calls draggableProps.onDragStart() (plugin.js:171)
- onDragEnd: calls draggableProps.onDragEnd() (plugin.js:172)

### handleDrop ordering
1. e.preventDefault()
2. if !canDropOnColumn(targetCol) → setDragId(null), return (plugin.js:703)
3. if !dragId → return (plugin.js:704)
4. defense-in-depth: dataTransfer text vs dragId mismatch → setDragId(null), return (plugin.js:705-706)
5. resolve card by dragId; if absent or same column → setDragId(null), return (plugin.js:707-708)
6. doMove(card, targetCol) exactly once (plugin.js:709)
7. setDragId(null) (plugin.js:710)

### Inbox
- onDragOver does NOT preventDefault for inbox (unchanged)
- No Inbox highlight (isDropTarget = dragId !== null && col !== 'inbox')
- handleDrop(e, 'inbox') clears dragId and performs no move

## Test totals (measured)

| harness | count | note |
|---------|-------|------|
| Python full suite | 151 passed | UNCHANGED from baseline |
| Python bounded (KB-*) | 52 passed | UNCHANGED from baseline |
| Node behavioral (test_kanban_helpers.mjs) | 75 passed | 63 baseline + 12 KB-Menu |
| Shipped-path (test_kanban_shipped_paths.mjs) | 17 passed | SP-1..SP-9 (multiple assertions per SP, SP-8 strengthened) |
| Drift harness (mutation_evidence.js) | 30 total | 17 baseline + 13 drift |
| Drift caught | 13/13 | 0 missed |
| Node syntax (node --check plugin.js) | exit 0 | valid ESM |

## Drift probes (all CAUGHT)

| drift | mutation | guard |
|-------|----------|-------|
| drift1 | replace buildAcceptToast with inline | P13: buildAcceptToast( in plugin.js |
| drift2 | replace buildFailureToast with inline | P13: buildFailureToast( in plugin.js |
| drift3 | replace classifyKeyEvent with inline | P14: classifyKeyEvent( in handleCardKeyDown |
| drift4 | replace 2164px with 1200px | P15/P16: 2164px present, 1200px absent |
| drift5 | replace navigateRight with inline | navigateRight( in handleBoardKeyDown |
| drift6 | replace closeMenuFocusTarget with inline | P17: closeMenuFocusTarget( in closeMenu |
| drift7 | remove copyIdValue import and usage | copyIdValue in plugin.js |
| drift8 | remove draggableProps.onDragStart call | draggableProps.onDragStart in plugin.js |
| drift9 | remove canDropOnColumn guard from handleDrop | canDropOnColumn(targetCol) in handleDrop |
| drift10 | remove menu arrow handler entirely | menuHighlightIndex(menuItems in plugin.js |
| drift11 | remove menuItems prop from KanbanCard | menuItems: menuOpen in plugin.js |
| drift12 | remove menuInitialHighlight + open-focus block | menuInitialHighlight( in plugin.js |
| drift13 | remove open-focus call from menu-open path | open-path focus binding: menuInitialHighlight → .focus() |

## No readiness/install claim

This receipt is an implementation receipt only. It does NOT advance the pipeline.
Halakukhan's independent re-gate is required before any installation, enablement,
deployment, publication, or PR. No forbidden files were modified.
