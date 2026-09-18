# Mozi receipt — Continuum v2 grammar amendment + full re-import

```
ownership_matrix: /Users/kethuda/.hermes/team-skills/ops/OWNERSHIP-MATRIX.md
matrix_read_at_dispatch: yes
owner: mozi
domain: implementation (stage 4)
upstream_approval: continuum-grammar/leo-ruling.md (STABLE, LOCKED)
task: continuum-v2exec
```

VERDICT: **BLOCKED at gate 4 (TH-GA6) with two blockers.** Steps 1–3 complete and verified
on disk; steps 4–5 executed as measurement (dry-run, read-only); steps 6–8 (registry
MUTATIONS) deliberately NOT executed per the lane's stop rule: "If a gate fails, record
the blocker honestly in the receipt and STOP — do not improvise."

Pre-flight (lane start): task_home.py md5 = 6d9d7d0877763dbfc1af3fa44a43c71b ✓ matches brief.
TASK-HOME.md sha256 = b5cee1327f5a5c73b6d9c59589f7d7cd5c1a8976e5ba5634dedfccc233d4c002 ✓ matches brief.

---

## BLOCKER 1 — TH-GA6 vs TH-GA1: the locked ruling is internally inconsistent

Ruling §7 rollback premise: "Existing tests continue to pass (they test v1 behavior which
is a subset of v2)." **False against the frozen fixture.** `fixtures/task_home/task-home.md`
line 44 contains `- Overnight: 13 PRs merged (personal/org/EvoPet/Tama); ...` — a single-word
slug + colon. Under the ruling's OWN §3 spec (verbatim-implemented), that bullet is now
keyed (key=`Overnight`, proc=None). Two UNMODIFIABLE existing tests assert v1 classification
of that same fixture:

| Test | Assertion | v1 | v2 |
|---|---|---|---|
| `test_th_a1_parse_completeness_matches_section_1_table` | `len(parsed.keyed()) == 11` | pass | **fail: 12 == 11** |
| `test_th_a2_literal_keys_and_procs_are_byte_identical` | keyed keys `== RUNNING_KEYS` (11) | pass | **fail: +`Overnight`** |

Attribution (RED-GREEN, md5-verified):
- v1 reconstructed (byte-exact: md5 `6d9d7d0877763dbfc1af3fa44a43c71b` == dispatch baseline;
  kept at `/tmp/task_home_v1_reconstructed.py`): both tests **2 passed**.
- v2 parser (md5 `526e9a50d2395481af844992df4d2171`): both tests **2 failed**; identical
  failure output pre/post my test additions.

No compliant resolution inside this lane:
(a) changing the regex → violates "EXACTLY as ruling §3 specifies" + TH-GA1/GA3;
(b) editing the two existing tests → violates the brief's hard constraint "do NOT modify
existing tests" + ruling §7.3;
(c) changing the frozen fixture → not in my ownership list + its sha is asserted (`5da1f01d…`).
→ Needs a ruling amendment (decision options at the end of this receipt).

## BLOCKER 2 — source drift: TASK-HOME.md changed mid-lane (by its owner, not by me)

- Lane start (my first tool call): sha256 `b5cee132…` (66 bullets) ✓.
- Now: sha256 `a19d7a1ca58e4d9115eaff43705f28453b1e0194dfaa18892d4bb2299af6351c`, mtime
  **2026-09-17 18:56:17** (during this lane), 79 bullets.
- My lane has ZERO writes to it (continuum module has no source write-path — TH-R4 test;
  my only sync invocation was `--dry-run`, proven non-writing below).
- Consequences: the hard exit constraint "sha256 == b5cee132… at your exit" is now
  unsatisfiable by a non-writer; and the §6 numeric proof ("items 66") plus the §5/§7
  mutation scope were defined against the 66-bullet board the ruling analyzed — the board
  now has 13 more bullets (RUNNING 22, AWAITING_OWNER 12, OPEN_FOLLOW_UP 8, CLOSED 35,
  UNRECOGNIZED/PAUSED 2). Binding those unreviewed 13 into the live registry under an
  acceptance proof pinned to the old baseline is outside approved scope.

---

## Step 1 — Contract amendment — DONE (verified on disk)
`continuum-sync/leo-architecture.md`: title `(v1)`→`(v2)`; ruling §4 v2-amendment block
inserted in §4 verbatim after the K1–K4/binding paragraph; CLOSED-4 block inserted in §9
verbatim after OPEN-10. md5: `1ae76b36ea9ad453ba6286085ededdcb` (v1) →
`b7f2633abb7831563341d8aaf0acc7c3` (v2). Read-back at lines 104–114 and 249–258 confirms
both blocks byte-present.

## Step 2 — Parser patch — DONE (verified on disk)
- `_SLUG_ONLY_RE` added byte-verbatim from ruling §3 at line 150 (+1 comment line).
- Bullet block rewritten exactly per ruling §3 (proc=None path; `missing_proc` check kept
  ahead of it; keyless fallback unchanged; ordinal not consumed by slug-only matches).
- **TH-GA10: `_KEYED_RE` line (148) sha256 BEFORE = `bdc8cdab96ee2488ed619eb6287b30a7975bd88a90a04f2f780e57383321bbc9`;
  AFTER = same `bdc8cdab…` — byte-identical ✓.**
- File md5: before `6d9d7d0877763dbfc1af3fa44a43c71b` → after `526e9a50d2395481af844992df4d2171`;
  import-clean (`import continuum.task_home` OK).

## Step 3 — New tests — DONE (ADDITIVE ONLY; no existing test line modified)
10 new functions (requirement ≥4):
| TH-GA | test file | function |
|---|---|---|
| GA1 | sync | `test_th_ga1_slug_only_form_is_keyed_with_null_proc` |
| GA2 | sync | `test_th_ga2_proc_form_is_keyed_unchanged` |
| GA3 | sync | `test_th_ga3_mixed_forms_all_keyed_zero_keyless` (18 proc + 48 slug-only = 66 keyed, 0 keyless) |
| GA4+GA5 | sync | `test_th_ga4_ga5_valid_key_accepts_slug_and_unnamed_forms` |
| GA9 | sync | `test_th_ga9_slug_only_bullet_binds_step_one_exact_name` (binding=="import", proc None) |
| GA11 | sync | `test_th_ga11_proc_shaped_still_warns_missing_proc` |
| GA12 | sync | `test_th_ga12_key_marker_still_warns_unknown_key_marker` |
| GA13 | sync | `test_th_ga13_continuation_lines_still_warn_wrapped_line` |
| GA14 | sync | `test_th_ga14_crlf_slug_only_normalizes_and_keyes` |
| GA4/human-route | projection | `test_th_ga4_slug_only_key_is_bindable_via_human_route` (bind+unbind via TH-L9 route, proc-less key) |

New tests: **10/10 passed**. Existing tests untouched: all edits were appends at file ends
(patch diffs show zero deletions of pre-existing lines); no git repo in build, verified by
diff-review + v1 reconstruction run.

## Step 4 — Full suite — GATE FAILED (this is Blocker 1)
`python -m pytest tests -q` → **2 failed, 409 passed** (411 collected: 401 pre-existing + 10 new).
The 2 failures are exactly the named existing tests of Blocker 1; the other 399 pre-existing
tests and all 10 new tests are green under v2. (Note: brief stated "369 total suite";
measured pre-existing collection is 401 — a concurrent lane (actionlog-view-mozi) has grown
the tree since the brief; honest measured numbers recorded here.)

## Step 5 — Dry-run sync — EXECUTED AS DIAGNOSTIC (read-only; NOT gate-passed, order is gated at 4)
`python -m continuum.cli task-home sync --dry-run --json` → exit 5, `dry_run: True`,
`writes: 0`, `item_fields_written: []`, `changed: 0`, `added: 0`.
Against the CURRENT board `a19d7a1c…`: **item_count 79, unnamed 0, keyed 79, conflicts []**;
warnings: `heading_unrecognized` (PAUSED — explicitly acceptable per §6) + `compound_bullet`
(informational). NO `duplicate_slug`/`unsectioned_bullet`/`malformed_bullet`.
→ The v2 grammar achieves the zero-unnamed proof STRUCTURALLY (the §6 shape, at the new
bullet count). The literal §6 numbers (66) require the drifted-away `b5cee132` baseline.
Ledger `data/task_home_sync.json` mtime remains 17:56 (pre-lane) — dry-run wrote nothing.

## Step 6 — Orphan dissolution — NOT EXECUTED (blocked upstream; no mutation on a red gate/moved baseline)
Pre-state verified intact, read-only: `task_home_binding=human` × **32**, `task_home_*`
declared rows **96**, `review_event` rows **36**, `unbind_task_home` events **0**.
Route mechanics confirmed against tests + apply-lane receipt (`POST
/api/plugins/continuum/projects/{id}/review {"action":"unbind_task_home"}`; the route
clears key/proc and sets binding=unbound with an audit row — re-proven in-repo by my new
projection test, 100% tmp-registry).

## Step 7 — Full sync pass — NOT EXECUTED (same reason).
## Step 8 — Export refresh — NOT EXECUTED (would publish the drifted-baseline state).

## Exit verification
- TASK-HOME.md sha256 at exit: `a19d7a1ca58e4d9115eaff43705f28453b1e0194dfaa18892d4bb2299af6351c`
  — **≠ dispatch baseline `b5cee132…` by OWNER-side edit at 18:56:17; my lane never wrote it**
  (matches baseline at my first check; TH-R4 proves the module has no source write-path).
- registry.db sha256 at exit: `812008f200ce75e63903dabfa0dc99f81dbb362515be9b5804334afafc9978df`
  == hash of record ✓ (server :18771 PID 11789 untouched; not restarted; :8765/:8766/:8767/:18772 untouched).
- Owned files changed: leo-architecture.md (v2), continuum/task_home.py (v2 parser,
  rollback-ready copy at /tmp/task_home_v1_reconstructed.py = byte-exact dispatch baseline),
  the two test files (additive), receipt + launch log. NOTHING else.

## Acceptance matrix TH-GA1..GA15
| ID | Status | Evidence |
|---|---|---|
| TH-GA1 | **PASS** | new test green |
| TH-GA2 | **PASS** | new test green + `_KEYED_RE` untouched |
| TH-GA3 | **PASS** | synthetic 18+48 fixture test green |
| TH-GA4 | **PASS** | valid_key + human-route tests green |
| TH-GA5 | **PASS** | valid_key test green |
| TH-GA6 | **FAIL → BLOCKER 1** | 399/401 existing green; a1/a2 conflict with §3 by the ruling's own construction |
| TH-GA7 | **PASS** | 10 new ≥ 4 |
| TH-GA8 | **PARTIAL** | exit 5 + acceptable warnings + 0 unnamed + [] conflicts on live source — but on drifted `a19d7a1c` (79), not `b5cee132` (66) → BLOCKER 2 |
| TH-GA9 | NOT REACHED | steps 6–7 withheld |
| TH-GA10 | **PASS** | line-148 sha256 `bdc8cdab…` before == after |
| TH-GA11 | **PASS** | new test green |
| TH-GA12 | **PASS** | new test green |
| TH-GA13 | **PASS** | new test green |
| TH-GA14 | **PASS** | new test green (incl. CRLF+keyed interaction) |
| TH-GA15 | NOT REACHED | step 6 withheld; unbind events remain 0/32 (pre-state intact) |

## Decisions required (route back to Leo / Proteus / Orda)
1. **Ruling amendment for the frozen-fixture collision** — recommended option: authorize the
   exact two-line update of `test_th_a1`/`test_th_a2` expectations to v2 truth
   (`keyed()==12`; keys `RUNNING_KEYS + ["Overnight"]`), everything else unchanged.
   Alternative (not recommended): narrow `_SLUG_ONLY_RE` (e.g. lowercase-start slugs) —
   changes the LOCKED §3 spec and would need re-audit of all 79 live bullets.
2. **Re-pin the baseline** — `b5cee132` is gone; Orda's board is `a19d7a1c` (79 bullets,
   parses 0-unnamed clean). Either proceed on `a19d7a1c` with the §6 proof restated at 79
   items (then dissolution×32 + full sync + export ×2 in one follow-up pass), or Orda
   restates the target board. My v2 parser is on disk and ready for either.

BLOCKED: TH-GA6 unfixable inside lane constraints (ruling §3 vs two frozen-fixture tests) + TASK-HOME baseline drifted mid-lane to a19d7a1c (owner-side write) — registry mutations (steps 6–8) withheld per stop rule.
