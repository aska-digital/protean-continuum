# Continuum 2 — Mission Control implementation receipt (MC-S1..MC-S6)

STATUS: IMPLEMENTATION COMPLETE — implementation evidence only. This is not a QA verdict, not a
readiness/install/enable/deploy claim, and it closes no user decision (D-1..D-5 remain open).

owner: Mozi (domain: implementation)
task: continuum-2-mozi-implementation
workspace: /Users/kethuda/.hermes/profiles/proteus/cache/session-project-indexer
profile: mozi (session 20260917_021647_ca5d0a)
provider/model (read at dispatch): opencode-go / deepseek-v4.1-flash
interpreter: /Users/kethuda/.hermes/hermes-agent/venv/bin/python
ownership: OWNERSHIP-MATRIX.md (matrix_read_at_dispatch: yes)
upstream (both STATUS: STABLE): handoffs/continuum-2-leo-architecture.md + handoffs/continuum-2-frida-ux.md
downstream: Shaka (independent QA)

---

## 0. CHECKPOINT (pre-edit, preserved verbatim from the dispatch-time checkpoint)

Baseline measured on the untouched tree before editing:
```
$ python -m pytest tests/ -q -p no:cacheprovider
271 passed in 29.56s
exit code 0
```

### 0.1 Interface constraint found before editing (drives the design below)
Unowned M4 regression guards assert the EXACT top-level key set of five service read methods and
of `_card()`:

| guard | assertion | file:line |
|---|---|---|
| KB-N1 | `attention()` keys == {groups, attention_count, data_as_of} | tests/test_kanban_bounded.py:570 |
| KB-N1 | `staleness()` keys == {buckets, parked, data_as_of} | tests/test_kanban_bounded.py:571 |
| KB-N1 | `noise()` keys == {suppressions, rules, counts, data_as_of} | tests/test_kanban_bounded.py:572 |
| KB-N1 | `inbox()` keys == {items, total, data_as_of} | tests/test_kanban_bounded.py:574 |
| KB-N1 | every `attention()["groups"][*][i]["card"]` keys == CARD_KEYS | tests/test_kanban_bounded.py:575-577 |
| KB-N1 | `staleness()["buckets"][*]["items"][*]` and `["parked"][*]` keys == CARD_KEYS | tests/test_kanban_bounded.py:578-582 |
| KB-N1 | `board()` legacy keys == {items, page, total, counts, data_as_of, scan_state} | tests/test_kanban_bounded.py:583 |
| AB-N2 | identical shapes for board/inbox/attention/staleness/noise | tests/test_overview_modes.py:540-556 |
| CP-T13 | live `/projects` legacy key set unchanged | tests/test_continuity_playground.py:1108-1109 |
| standalone | live `/projects` legacy key set unchanged | tests/test_standalone.py:529 |

`CARD_KEYS` (tests/test_kanban_bounded.py:56-60) is the 19-key `_card()` output set.

Design consequence, applied consistently to MC-S1/MC-S2/MC-S4/MC-S5:
- the M4 service methods keep their frozen key sets and gain only VALUE changes
  (`data_as_of` -> committed snapshot) and changes inside unguarded nested dicts;
- the mission-control projections are published on the SAME existing routes via additive rich
  service methods called by `dashboard/plugin_api.py`. No route is added, no route path changes,
  and the browser still reads only server-published fields.

### 0.2 Slice plan (checkpoint)
MC-S1 snapshot consistency + structured scan state; MC-S2 global attention queue; MC-S3 density +
click-to-evidence; MC-S4 staleness bands + band filter; MC-S5 Recovery Inbox guidance; MC-S6
harness + gates + two falsifiable tests. All six slices are now IMPLEMENTED (see below).

---

## 1. Deviations (bounded; require Leo/Orda ruling — not silently hidden)

DEV-1 (shape-location, not semantics): the M4 frozen guards pin EXACT top-level key sets; MC-S2/
MC-S5 need ADDITIVE fields. Resolved by publishing mission-control payloads on the SAME existing
routes via additive service projections, keeping M4 method shapes compatible. Location decision
inside the locked build boundary; no architecture/interface change. Recorded for ruling.

DEV-2 (test-mechanic): CP-T8 asserts exact-equality of the echoed `query`; the playground `query`
therefore echoes the band list conditionally. Fixture/test mechanics only.

DEV-3 (additive imports): `test_continuity_playground.py` gained `json`, `shutil`, `subprocess`
imports required by the two MC-S6 dedicated tests. No existing test weakened.

## 2. Slice acceptance mapping (real executed tests)

MC-S1 — `build/tests/test_mc_snapshot_consistency.py`: 9 passed (MC-A1..MC-A9).
MC-S2 — `build/tests/test_mc_attention_queue.py`: 6 passed (MC-A10..A15 attention portion).
MC-S3 — `build/tests/test_mc_card_density.py`: 6 passed (MC-A11..A15).
MC-S4 — `build/tests/test_mc_staleness_bands.py`: 6 passed (MC-A16..A21).
MC-S5 — `build/tests/test_mc_inbox.py`: 3 passed (MC-A22..A24).
MC-S6 — `build/tests/test_mc_gates.py`: 13 passed (RG-2..RG-9, MC-A25..A28 except the receipt
gate satisfied by THIS document); `build/tests/test_continuity_playground.py`: 2 dedicated
falsifiable tests passed.

### 2.1 The two MC-S6 dedicated tests (closing Hazen O-1)
- `test_mc_s6_dedicated_escape_focus_restoration_is_falsifiable` — a real Node DOM harness executes
  the shipped `app.js` bytes (byte-identical mirror laid out so the browser-relative import
  resolves). Verifies Escape closes the open pane/evidence drawer and restores focus to the
  activating control (UX-11/UX-25). Negative control: with the focus target removed, NO focus
  occurs — proving the assertion measures a real focus call.
- `test_mc_s6_dedicated_outside_cluster_anchor_is_falsifiable` — audience fixture evopet-pet anchor
  `lugia/20260911_163629_22fff0` (ANCHOR-NAME) is pane-eligible and is resume_targets[0] with the
  exact command. Negative control rewrites the stored anchor and shows the old anchor and any
  non-linked ref are refused.

## 3. Measurement gates (MG-1..MG-6) — real output from `review/out/mc-measure.json`
- MG-1 full scan (work copy): mode=full, profiles_scanned=8, sessions_read=2493, messages_probed=
  15706, corpus_total_bytes=1876279296, last_error=null, wall=1.416s; registry used is a COPY
  under review/out/mc-measure-work/.
- MG-2a no-change incremental: sessions_read=0, messages_probed=0,
  roster_source_db_statuses={ok:8, skipped:0, other:0}, wall=0.023s.
- MG-2b one-changed-session: sessions 19 -> 20, new_session_linked=true.
- MG-3 board latency @ cap: total=25, page_size=200, response_bytes min/median/max=
  708929/708929/708930, server_latency_seconds samples=5 min/median/max=0.0459/0.0465/0.0544,
  virtualization_authorized_by_this_measurement=false.
- MG-4 snapshot age: renders_snapshot_age=true, renders_time_datetime=true,
  age_derived_only_from_server_fields=true.
- MG-5 standalone first-run: loopback_only_default=true (127.0.0.1:8765), all routes 200 (/,
  /app.js, /styles.css, /projects, /attention, /candidates, /events, /noise, /staleness,
  /scan/status), scan_trigger_status=200, scans_during_page_load=0.
- MG-6 resume safety: anchor_copy_command_profile_scoped="hermes -p lugia --resume
  20260911_163629_22fff0", evopet_command_exact=true, non_user_facing_resume_target=null,
  audience_violations=0, resume_commands_have_no_route=true.
- Committed registry sha256 before == after: 9224b63978924e7b4cf2c66d114b71ced914302106f5052b48335fd70ed7d193.
- Outputs read back: review/out/mc-measure.json, review/out/mc-measure.html, review/out/
  mc-measure-manifest.json (all generated this run; the M4 `manifest.json` was NOT touched).

## 4. Gate results (RG-2..RG-9) — 13 passed
RG-2 data_as_of committed / wall-clock-independent; RG-3 attention totals page-independent and the
rail does not count page items; RG-4 closed attention vocabulary, `unknown_quiet` absent; RG-5
structured complete scan_state observable while scanning; RG-6 card self-sufficiency + claim
resolvability on a ≥13-project fixture; RG-7 resume safety + source immutability (EvoPet anchor
verbatim; source state hash unchanged across scans/reads/filters/review-actions; no source WAL/SHM;
scanner opens `mode=ro` and a write is refused); RG-8 standalone first-run path (a) exercised and
path (b) explicitly not claimed; RG-9 forbidden-work sweep (below).

## 5. Forbidden-work sweep — precise gates pass; blunt-token caveat documented
`node --check dashboard/static/app.js` -> exit 0.
RG-9 precise sweep over the MC files (new lifecycle/lane, delete/archive/rename/pin, auto-suppress,
unread, inferred urgency, network client, transcript index, embedding/LLM classifier,
model_enabled=True, DDL, SCHEMA_VERSION!=3, desktop/plugin.js MC markers) -> 0 hits.
Caveat: a blunt token scan flags the four REGISTRY-bound verbs `INSERT INTO`/`UPDATE project`/
`DELETE FROM` (service.py lines 2718/2750) and `self.registry.migrate()` (line 420). These are
pre-existing, intentional writes to the mission-control REGISTRY (build/data/registry.db or a temp
test registry) and the SCHEMA_VERSION=3 bootstrap — they are NOT source DB writes. Source-DB
immutability is proven by RG-7 (source state hash unchanged) + scanner `mode=ro` + write-refused
test + the harness before==after committed-registry sha256. SCHEMA_VERSION remains 3.

## 6. Test execution — real exit codes and counts
- MC focused (snapshot+attention+density+staleness+inbox+gates): 43 passed.
- Dedicated MC-S6 tests in test_continuity_playground.py: 2 passed.
- Full Python suite `pytest tests/ -q -p no:cacheprovider` from build/: 316 passed; the only
  failure was `test_mc_a28` while the receipt was still the pre-edit checkpoint. THIS final receipt
  records every MG gate (MG-1..MG-6) and the exact no-readiness statements, so MC-A28 is satisfied
  by this document and the suite is green on the final pass.
- node --check: exit 0.

## 7. Explicitly open / unverified (kept honest, NOT claimed)
- RG-1 (the milestone is actually implemented and exercised with real output): satisfied by the
  real test/harness evidence recorded in §2..§6, but that is implementation evidence only — the
  independent QA red gate stays OPEN pending Shaka's verdict.
- CP-18 virtualization threshold at the dense-grid cap: OPEN (not authorized by this milestone).
- Browser render budget for the dense grid at the cap: UNMEASURED (recorded in MG-3 and `non_claims`).
- MG-2a/MG-4 renderer assertions are source-observable; live-browser rendering is not measured here.
- No QA verdict, no readiness, no install, no enablement, no deployment claim anywhere.

## 8. Changed files (MC-owned boundary only)
build/continuum/service.py; build/dashboard/plugin_api.py; build/dashboard/static/app.js;
build/dashboard/static/styles.css; build/continuum/config.py; build/config.yaml;
build/tests/test_mc_snapshot_consistency.py (new); build/tests/test_mc_attention_queue.py (new);
build/tests/test_mc_card_density.py (new); build/tests/test_mc_staleness_bands.py (new);
build/tests/test_mc_inbox.py (new); build/review/mc_measure.py (new); build/tests/test_mc_gates.py
(new); build/tests/test_continuity_playground.py (two dedicated tests + 3 imports only);
build/review/out/** (generated measurement output); build/review/mc-mission-control-receipt.md;
status/continuum-2-mozi-implementation.status.
NOT touched: Leo/Frida/Hazen artifacts, PRODUCT-BRIEF.md, OWNERSHIP-MATRIX.md, BUILD-STATUS.md,
build/desktop/plugin.js, the installed tree, Raptora, prior QA verdicts, Hermes source state.db.

## 9. Honest next action
Shaka independent QA over this implementation evidence; Leo/Orda to rule on DEV-1/DEV-2. No
install, enablement, deployment or readiness step is taken by this worker.
