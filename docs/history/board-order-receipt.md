# Mozi receipt — Continuum main-board render order (interim, order-only)

Date: 2026-09-17 (EDT) | Role: stage-4 implementation | Scope: one render-order move

## Changed file (exactly one)
/Users/kethuda/.hermes/profiles/proteus/cache/session-project-indexer/build/dashboard/static/app.js
- md5 before write (re-checked immediately pre-edit): 84f4e9de15b2fb8acf183d2fc658698e (matches dispatch baseline — no conflict)
- md5 after write: 1b5ffc5f0b4bfbbea2a30652575046d0
- Lines 2518–2529 in render(); total lines 2597 and byte size 116491 unchanged (pure line move).

## Before → after render order (main board path)
Before: HealthStrip, MissionControl, CurrentState, **TaskHome**, attentionRail, Bands, body, Inbox, CompletenessReceipt, drawer
After:  HealthStrip, MissionControl, CurrentState, attentionRail, Bands, body, Inbox, CompletenessReceipt, **TaskHome**, drawer
renderTaskHome(data) is now last among ordinary panels, after renderCompletenessReceipt(data), before drawer — as briefed.

## Diff evidence (single hunk, patch tool)
-    renderTaskHome(data),      (removed from 4th slot)
+    renderTaskHome(data),      (inserted after renderCompletenessReceipt(data), before drawer)

## Checks run (all PASS)
1. Read-back of app.js lines 2516–2531 confirms the new order verbatim.
2. Inverse proof (/tmp/mozi_order_verify.py): reverting only this block move reproduces the
   baseline md5 84f4e9de15b2fb8acf183d2fc658698e exactly ⇒ the edit is provably order-only,
   zero collateral byte changes.
3. renderTaskHome occurrences still exactly 3 (definition :1254, call site :2527, export :2590);
   export list unchanged. Task Home markup/fields untouched (function body never edited).
4. node --check on /tmp/mozi_app_check.mjs (copy, ESM): syntax OK.
5. styles.css unchanged: md5 5d348b28a4468b55ed2ad6333f73d027 (== baseline), mtime 18:40:09.
6. Unchanged-source evidence: find -mmin -30 lists as modified only app.js (mine) plus server
   runtime artifacts (data/action_log.json 19:19, registry.db-wal 19:29 — both predate my 19:36
   write; pytest cache; plugin_api .pyc). registry.db mtime 17:24; task_home.py, config,
   plugin_api.py, HTML untouched. Server NOT stopped or restarted.
7. Live serving (read-only): curl http://127.0.0.1:18772/app.js md5 == on-disk
   1b5ffc5f0b4bfbbea2a30652575046d0; index HTTP 200. Order change is already live for
   Proteus's DOM proof.

## Design-spec deviation note
frida-design.md (712 lines, STABLE) states visual order is frozen by app.js. This order move is an
explicitly authorized interim deviation per the Proteus order-only brief (user goal: good panels
lead while v2 re-import owns the unnamed-row fix). No other aspect of the design spec was touched.

## Revert path
In render() (app.js ~2518–2529), move `renderTaskHome(data),` from its current position (after
`renderCompletenessReceipt(data),`) back up to directly after `renderCurrentState(data),`. That
single inverse move restores md5 84f4e9de15b2fb8acf183d2fc658698e (verified by inverse proof above).

Downstream: Proteus live DOM proof, then Shaka independent read-only QA. Shaka receipt not written here.

STABLE
