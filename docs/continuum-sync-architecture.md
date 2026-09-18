# Continuum ↔ Orda TASK-HOME Sync Contract (v2) — Architecture Lock

STATUS: STABLE

owner: Leo (solution/software architecture)
domain: architecture (OWNERSHIP-MATRIX.md read at dispatch: yes)
project: Continuum / Raptora dashboard ↔ Orda TASK-HOME bidirectional sync
task: continuum-sync (leo-architecture)
workspace: /Users/kethuda/.hermes/profiles/proteus/cache/delegation/continuum-sync
profile/session/provider at dispatch: leo / 20260917_105742_768477 / opencode-go · deepseek-v4.1-flash
  (read from /Users/kethuda/.hermes/profiles/leo/config.yaml immediately before launch: model.provider=opencode-go, model.default=deepseek-v4.1-flash)
upstream (read on disk this pass, not re-derived):
  - /Users/kethuda/.hermes/profiles/orda/TASK-HOME.md (sha256 5da1f01d21a9a7d94f032756ffb0815c6ab98d40e5ad2a4b37a167443ba9b14e, 46 lines)
  - installed plugin /Users/kethuda/.hermes/plugins/continuum/ (older, pre-mission-control copy)
  - source build /Users/kethuda/.hermes/profiles/proteus/cache/session-project-indexer/build/ (Continuum 2 / mission control, registry SCHEMA_VERSION=3)
  - served demo evidence: status/continuum-2-proteus-integration.status (URI/process/read-back), handoffs/continuum-2-leo-architecture.md (STABLE, MC-L1..MC-L16), handoffs/continuum-2-shaka-qa.md (PASS + limits), build/review/mc-mission-control-receipt.md, demo/*.png
downstream: Mozi (implementation of §7 boundary) then Shaka (QA gates TH-G1..TH-G3)
method: every row is anchored to a named on-disk artifact, a file:line, or a measurement taken this pass. Nothing is stated from memory. No measurement here is a QA verdict.
scope: sync contract, section→panel mapping, identity keys, import/export payload shape, conflict precedence, v1 mechanism, implementation file boundary, acceptance criteria.
non-claims: this artifact writes no build file, no TASK-HOME line, no registry row; installs/enables/deploys nothing; issues no QA verdict; closes no OPEN item. The desktop plugin (desktop/plugin.js) is out of scope (MC-L8).

---

## 1. Ground truth measured this pass

TASK-HOME.md (parse run this pass, deterministic script over the literal file):

| section heading (verbatim) | bullets | keyed `slug (proc_id):` | has `proc_` | `RECEIPT SAYS` | compound (`; + `) |
|---|---|---|---|---|---|
| `RUNNING (background workers live)` | 11 | 11 | 11 | 7 | 7 |
| `AWAITING OWNER (needs input, no worker running)` | 11 | 0 | 0 | 0 | 5 |
| `OPEN FOLLOW-UPS (tracked, no lane yet)` | 6 | 0 | 0 | 0 | 0 |
| `CLOSED (2026-09-17)` | 6 | 0 | 0 | 0 | 4 |
| **total** | **34** | 11 | 11 | 7 | 16 |

Line anchors: RUNNING L7–L17, AWAITING OWNER L20–L30, OPEN FOLLOW-UPS L33–L38, CLOSED L41–L46.
Consequences that drive §3 and §9: only RUNNING carries explicit keys; 23 of 34 bullets are keyless prose; 16 bullets bundle more than one ask on one line; 7 RUNNING bullets already contradict their own section with `RECEIPT SAYS CLOSED/COMPLETE`.

Dashboard state (measured):
- Sources are read-only (`mode=ro`, `PRAGMA query_only=ON`); the registry (`build/data/registry.db`, v3) is the only writable store; no TASK-HOME reference exists anywhere in the build tree or the installed plugin (grep `TASK-HOME` → 0 hits in both).
- Existing wire: routes `/projects`, `/projects/{id}`, `/candidates`, `/attention`, `/staleness`, `/noise`, `/scan/status`, `POST /scan`, `POST /projects/{id}/review`, `POST /review/undo`, `/overview`, `/events`. Playground `/projects` query vocabulary is `view∈{today,all}`, `sort`, `direction`, `lane∈BOARD_COLUMNS`, `lifecycle∈LS-1..LS-9`, `attention`, `band`, `profile`, `q`, `page`, `page_size` (service.py:1123-1211).
- Surface set: attention rail, eight-lane board (`inbox, ongoing, blocked, waiting_on_you, paused, done, shipped, scrapped`), dense grid, staleness view, Recovery Inbox (MC-L1: one set, five projections).
- Served demo: `http://127.0.0.1:8767/` (loopback; PID 29428 alive at read time, `python -m dashboard.standalone --host 127.0.0.1 --port 8767`), serving the SOURCE BUILD; installed plugin is older and is a separate reviewed copy step (MC-L14).

---

## 2. Decision table — LOCKED

| ID | Lock | Rationale | Anchor |
|---|---|---|---|
| TH-L1 | TASK-HOME.md is READ-ONLY to this system, in both directions. The sync reads it and exports a proposal for Orda; no code path, CLI flag, or route may write it. | TASK-HOME writes are outside this worker's authority and outside the dashboard's domain. | brief; OWNERSHIP-MATRIX (architecture: no canonical writes outside its own artifact) |
| TH-L2 | The four sections map onto ONE new panel group set (5 groups, §3) plus the existing lane vocabulary. NO new lane, NO new lifecycle value, NO new attention state. | MC-L1/MC-L15 and RG-9 freeze lanes/lifecycles; the existing `_DERIVED_TO_PLACEMENT` table already expresses every status we need. | service.py:53 (BOARD_COLUMNS), service.py:38-45 (`_DERIVED_TO_PLACEMENT`) |
| TH-L3 | Identity key is the literal RUNNING slug (`task_home_key`), byte-verbatim; `proc_*` is a secondary cross-check, never the primary key. | 11/11 RUNNING bullets are keyed; keys are stable across text edits; proc ids rotate and are absent on 23/34 bullets. | §1 table; TASK-HOME L7–L17 |
| TH-L4 | Keyless bullets get a deterministic fallback key `unnamed:<SECTION>:<ordinal>` plus `content_sha1`; re-ordering re-binds by `content_sha1`, an edit emits `keyless_binding_shift` and never silently re-points. | 23/34 bullets have no key; a line-hash alone is not stable under edits and an ordinal alone is not stable under re-ordering. | §1 table (keyless counts) |
| TH-L5 | Import writes ONLY the reserved declared-field namespace `task_home_*` (source=`task-home-sync`) plus its own ledger. It never writes `placement`, `urgent`, `lifecycle_override`, `accepted`, `project_name`, `dismissed`, or `merged_into`. | Those are human-owned fields with their own review actions; MC-L13/INV-MC-2 require every user-visible mutation to stay audited and reversible. | registry.py:603 (`set_declared`), service.py review actions |
| TH-L6 | Precedence: (1) lane receipts outrank everything on FACTUAL disputes; (2) TASK-HOME outranks the dashboard on STATUS; (3) the dashboard is a projection, never a second status authority. Local human flags are preserved verbatim and shown as shadowed values with a conflict row. | Explicit requirement; matches MC-L1 and INV-MC-2 (no silent overwrite of human decisions). | brief req. 4; MC-L1 |
| TH-L7 | v1 mechanism is an explicit, idempotent SYNC PASS invoked by CLI. No daemon, no watcher, no background thread, no write on any GET path, no scan-time coupling. Polling on dashboard load was REJECTED as a write-on-read. | Brief ("no new always-on daemon without owner approval") + MC-L5 ("no page load starts a scan") + existing read-only invariants. | brief req. 5; mc-mission-control-receipt MG-5 (`scans_during_page_load=0`) |
| TH-L8 | Idempotency is content-gated: a pass compares `source_sha256` and per-bullet `content_sha1`; unchanged input produces zero registry writes. The export body carries no wall-clock value (run metadata goes to a sidecar). | Requirement 3 ("repeated sync updates existing rows") + byte-stability of the round-trip. | §5, §8 TH-I* |
| TH-L9 | Human binding changes (bind/unbind) go through the EXISTING `POST /projects/{id}/review` route as new actions `bind_task_home` / `unbind_task_home`, so they inherit the append-only audit + `undo`. No new route. | MC-L1/CP-8 (no new route while an additive path exists); registry.snapshot/restore already covers `declared_field`, so undo works with no new persistence. | plugin_api.py:123-137; registry.py:712-760 |
| TH-L10 | TASK-HOME-only keys (no Continuum project) live in the Task Home panel only. They never enter the continuity set (`items[]` of board/inbox/attention/staleness) and never change `counts.total`. Continuity stays exactly the cluster-derived set. | INV-MC-4 (deterministic identity over clusters) and MC-L1 (one continuity set); keeps board semantics and frozen counts intact. | MC-L1; service.py:1117-1119 |
| TH-L11 | Wire additions are additive and confined to the PLAYGROUND `/projects` path (`view=today|all`). The bare legacy `/projects` envelope key set and `_card()`'s key set stay byte-compatible. | CP-T13/KB-N1 freeze both; MC-S4 set the precedent (`counts.staleness_bands` on the playground path only). | mc-mission-control-receipt §0.1 |
| TH-L12 | The panel's visual/interaction/copy spec belongs to Frida. This artifact locks the DATA contract only and requires Mozi to render server fields without inventing labels. | Domain boundary (design vs architecture). | OWNERSHIP-MATRIX; doctrine §2 |
| TH-L13 | Freshness is server-computed: `synced_at`, `age_seconds`, `stale`, `source_sha256` are published; the browser derives none of them. | INV-MC-10 (client may not compute what the server owns). | leo-architecture §2 INV-MC-10 |
| TH-L14 | Export is a generated artifact under `build/review/out/` for a human to apply. Nothing in the sync applies it. | Brief req. 6 ("without editing TASK-HOME"). | brief |
| TH-L15 | No registry DDL. `SCHEMA_VERSION` stays 3. All import state rides existing `declared_field` rows + the sync's own ledger file. | MC-L13/RG-9. | registry.py:23 |
| TH-L16 | Out of scope, untouched: install/enable/deploy, TASK-HOME edits, desktop plugin, mobile 390px defect, CP-18 virtualization, Raptora, HUD/merge lanes. | MC-L8/MC-L14/MC-L16. | leo-architecture §0 |

Locked-together note: TH-L1/TH-L5/TH-L7 keep the direction of writing one-way (TASK-HOME → registry) with a human step for the reverse direction. TH-L3/TH-L4/TH-L8 make repeated passes update existing rows instead of duplicating them. TH-L6 is the single precedence order.

---

## 3. Section → panel / lane mapping (locked)

Panel groups (new `home` vocabulary, six values, no lane added):

| panel group code | panel label (Frida owns final copy) | source | board lane (`column`) | quiet? |
|---|---|---|---|---|
| `RUNNING` | Running | TASK-HOME section 1 | `ongoing` | no |
| `AWAITING_OWNER` | Awaiting owner | TASK-HOME section 2 | `waiting_on_you` | no |
| `OPEN_FOLLOW_UP` | Open follow-ups | TASK-HOME section 3 | `paused` | yes |
| `CLOSED` | Closed | TASK-HOME section 4 | `done` | yes |
| `DASHBOARD_ONLY` | Dashboard only | bound-less Continuum projects | unchanged (existing resolution) | unchanged |
| `ABSENT` | Not in TASK-HOME | previously bound, no longer in source | pre-import lane (override withdrawn) | unchanged |

Rules:
1. The mapping is a READ-TIME override of `column` for bound items (TH-L5: nothing persisted for it). `lane=` filtering honours the overridden value, so the panel and the board cannot disagree.
2. `lifecycle`, `derived_lifecycle`, `attention_state`, `confidence*`, `evidence_tier(s)` are NOT rewritten by the import. The panel shows `task_home.section` as the status label next to the cluster-derived lifecycle with `state_source="task_home"`.
3. Attention participation in v1 = NONE. The rail keeps deriving from grounded conditions (INV-MC-6); `AWAITING_OWNER` is counted in `counts.task_home.AWAITING_OWNER` and rendered by the panel. Rail participation is OPEN-2.
4. `DASHBOARD_ONLY` is the required home representation for dashboard-origin items: every Continuum project with no TASK-HOME line appears exactly once, in this group, carrying its existing lane and a proposed export line.
5. Receipt overrides (TH-L6 P1): a bound RUNNING item whose line contains `RECEIPT SAYS CLOSED|COMPLETE` stays in `RUNNING` and gains `receipt_section_mismatch`; it is listed in the export's confirm-then-close list. The sync never moves it and never closes it.

---

## 4. Identity keys

| key | value | who sets it | stability |
|---|---|---|---|
| K1 `task_home_key` | literal slug, byte-verbatim | TASK-HOME author (Orda) | stable across text edits |
| K2 `task_home_proc` | literal `proc_<hex>` from the same line | TASK-HOME author | rotates per run; absent on 23/34 |
| K3 `task_home_content_sha1` | sha1 of the verbatim bullet (after LF normalisation) | sync | stable under re-ordering, changes on edit |
| K4 `project_id` | Continuum uuid5 (cluster signature → member overlap) | model.py (consumed unchanged) | deterministic (INV-MC-4) |

Binding = at most one (K1 → K4) and one (K4 → K1). Enforced by the sync ledger; violations emit `ambiguous_binding` and are reported, never auto-resolved.

> **v2 amendment (2026-09-17, continuum-grammar ruling):**
> The parser recognizes two keyed forms:
> 1. `- slug (proc_hex): text` — keyed, key=slug, proc=proc_hex (original v1 form)
> 2. `- slug: text` — keyed, key=slug, proc=None (v2 addition)
>
> Both are keyed; neither falls to the TH-L4 unnamed fallback. A slug-only bullet carries
> proc=None and participates in the §4 binding fallback order starting at step 1 (exact slug match).
> The unnamed fallback remains for bullets that have no slug at all.
>
> TH-L3 is unchanged: slug is the primary identity key; proc is a secondary cross-check.

Legacy / deterministic fallback order (applies to pre-existing dashboard rows and keyless bullets):
1. exact `slug ==` Continuum project `name` (case-sensitive byte compare of the normalised name);
2. `task_home_proc` found verbatim in a member session `title` or in the project's `next_action` text;
3. `keyless`: bind by `task_home_content_sha1` within the same section;
4. `keyless` first sight: bind by `unnamed:<SECTION>:<ordinal>`;
5. none of the above → `DASHBOARD_ONLY` (dashboard side) or `source_only` (TASK-HOME side).
No fuzzy, substring, token-score or embedding matching anywhere (TH-L4; INV-MC-4 forbids non-deterministic identity).

---

## 5. v1 mechanism, CLI, endpoints, write boundary

Mechanism: one explicit idempotent pass. `cli task-home sync` reads TASK-HOME → parses → keys → binds → writes the `task_home_*` declared fields + ledger → prints a JSON report. Exit codes: 0 = changed+x, 3 = `SYNC_LOCKED` (a concurrent pass holds the lock), 4 = `SOURCE_MISSING`, 5 = `PARSE_DEGRADED` (warnings, no crash).

Rejected alternatives (recorded, not silent): file-watch needs a long-lived process (owner approval required; note the standalone server IS long-lived, so an in-process watch would add no NEW daemon but would add a write path to a running server — OPEN-6); import-on-dashboard-load writes on a GET and contradicts MC-L5; scan-time coupling would tie a read-only scan to an external file.

CLI (existing `continuum/cli.py` gains one subcommand; no new entrypoint):
```
python -m continuum.cli task-home sync   [--source PATH] [--dry-run] [--json]
python -m continuum.cli task-home show   [--json]      # read-only: ledger + bindings + conflicts
python -m continuum.cli task-home export [--out DIR] [--json]
```
Read endpoint (additive query on the EXISTING route):
```
GET /api/plugins/continuum/projects?view=all[&home=RUNNING|AWAITING_OWNER|OPEN_FOLLOW_UP|CLOSED|DASHBOARD_ONLY|ABSENT]
```
`home` is repeatable, OR-within, AND-across with `lane|lifecycle|attention|band|profile|q`; invalid value → HTTP 400 on the existing ValueError path. No new route.
Human binding endpoint (existing route, new actions):
```
POST /api/plugins/continuum/projects/{project_id}/review
  {"action":"bind_task_home","payload":{"key":"<slug>","proc":"<proc_x|null>"}}
  {"action":"unbind_task_home"}
```

Write boundary of the sync pass (exhaustive):
- registry `declared_field` rows for the reserved namespace only: `task_home_key`, `task_home_proc`, `task_home_section`, `task_home_section_label`, `task_home_line`, `task_home_line_no`, `task_home_content_sha1`, `task_home_receipt`, `task_home_receipt_text`, `task_home_binding`, `task_home_source_sha256`, `task_home_synced_at`, `task_home_run_id` (writes inside one `registry.transaction()`);
- `build/data/task_home_sync.json` (ledger) + `build/data/task_home_sync.lock`, written atomically (tmp + `os.replace`) under an exclusive lock;
- `build/review/out/task-home-export.{md,json}` + `.meta.json`.
Everything else is forbidden (see §7). The pass must not touch `project`, `project_session`, `evidence`, `next_action`, `review_event`, `scan_run`, or the `placement`/`urgent`/`lifecycle_override`/`accepted` fields; a pre/post content hash of those tables proves it.

Ledger shape (`build/data/task_home_sync.json`, schema 1):
```
{"schema":1,"source_path":...,"source_sha256":...,"last_run_id":...,"last_synced_at":...,
 "items":[{"key","proc","section","section_label","line","line_no","content_sha1",
           "project_id"|null,"binding":"import|human|keyless|<null>","warnings":[...]}],
 "conflicts":[{"key","kind","task_home_value","dashboard_value","winner","line_no"}],
 "runs":[{"run_id","started_at","ended_at","source_sha256","added","changed","unchanged",
          "absent","warnings"}]}
```

Export payload (dashboard → TASK-HOME), generated, never applied:
```
{"schema":1,"source_sha256":<the sha the proposal was computed against>,
 "sections":[{"section","label","lines":[{"key","proposed":true|false,"existing_line_no"|null,
   "lane","derived":{"confidence_band","evidence_tier","session_count","last_subject_activity"},
   "proposed_line":"- ..."}]}],
 "proposed_new_section":{"label":"DASHBOARD-DERIVED (Continuum)","reason":...},
 "confirm_then_close":[{"key","receipt_text","line_no"}],
 "unbound_projects":[...], "conflicts":[...]}
```
The `.md` rendering is the reviewable block. Deterministic ordering: sections in source order, lines by `project_id` then `key`; LF endings; no wall-clock inside the body (run metadata in `.meta.json`).

---

## 6. Additive wire shape (playground path only)

Envelope additions (`GET /projects?view=...`): `counts.task_home = {RUNNING, AWAITING_OWNER, OPEN_FOLLOW_UP, CLOSED, DASHBOARD_ONLY, ABSENT, source_only, conflicts}` and one `task_home` object:
```
"task_home": {"enabled":true,"source_path":...,"source_sha256":...,"source_mtime":...,
  "synced_at":<float|null>,"age_seconds":<float|null>,"stale":<bool>,"stale_after_seconds":<int>,
  "run_id":<str|null>,"warnings":[...],"conflicts":[...],
  "items":[{"key","proc","section","lane","line","line_no","project_id"|null,
            "binding","receipt","warnings":[...]}]}
```
Playground card addition (`_playground_card` only): `card.task_home = {key, proc, section, section_label, lane, line, line_no, receipt, receipt_text, content_sha1, binding, synced_at, age_seconds, stale, conflicts[]}` or `null`.
`_card()` (legacy, attention, staleness) gains NOTHING (KB-N1/CARD_KEYS stay green). Never synced yet → `enabled:false`, `synced_at:null`; the panel shows the exact CLI command from `task_home.source_path`.

---

## 7. Implementation boundary (exact files for Mozi)

All paths are in the SOURCE BUILD `/Users/kethuda/.hermes/profiles/proteus/cache/session-project-indexer/build/`.
MAY WRITE: `continuum/task_home.py` (NEW: parser, keyer, mapper, sync engine, export renderer; pure functions, injected clock/paths, no HTTP, no network); `continuum/cli.py` (one `task-home` subcommand); `continuum/service.py` (additive: `home=` in `_parse_board_query`/`_matches_query`, `counts.task_home`, playground-card `task_home`, `task_home` envelope, `bind_task_home`/`unbind_task_home` review actions, `_task_home_projection()` reader); `continuum/config.py` + `config.yaml` (additive non-secret keys `task_home_enabled:false`, `task_home_source_path`, `task_home_stale_after_seconds:900`, with fail-loud validation matching the existing `_validate_bundle` style); `dashboard/plugin_api.py` (pass-through of `home=` only); `dashboard/static/app.js` + `dashboard/static/styles.css` (Task Home panel rendering server fields per Frida's spec); `tests/test_task_home_sync.py` (NEW), `tests/test_task_home_projection.py` (NEW), `fixtures/task_home/*.md` (NEW frozen copies); `review/out/task-home-export.*` (generated); `data/task_home_sync.json`, `data/task_home_sync.lock` (local, gitignored); `review/task-home-sync-receipt.md` (Mozi receipt).
MUST NOT WRITE: `TASK-HOME.md` (§2 TH-L1); any Hermes `<profile>/state.db`; the installed tree `/Users/kethuda/.hermes/plugins/continuum/**`; `continuum/registry.py` DDL or `SCHEMA_VERSION`; `continuum/{scanner,cluster,evidence,classify,model}.py`; `desktop/plugin.js`; `build/data/registry.db` by hand; `project`/`project_session`/`evidence`/`next_action`/`review_event` rows outside the audited review path; Frida/Orda/Shaka artifacts; anything public; no install/enable/deploy/PR/cron/daemon.

---

## 8. Acceptance IDs

Mozi (implementation, each an executed test or a read-back command):
- TH-A1 parse completeness: 34 items, section counts 11/11/6/6 on the frozen fixture; no bullet dropped or split.
- TH-A2 literal keys: the 11 RUNNING keys and proc ids are byte-identical to the source line; no case folding, no dedupe.
- TH-A3 keyless determinism: reordered keyless lines re-bind by `content_sha1`; an edited keyless line emits `keyless_binding_shift` and does not rebind silently.
- TH-A4 fallback order: steps 1→5 of §4 exercised on a legacy fixture; zero fuzzy matches.
- TH-A5 binding uniqueness: K1→K4 and K4→K1 both ≤1; a violation reports `ambiguous_binding`.
- TH-A6 section identity: heading matched on its leading words; a `(date)`/parenthetical change orphans nothing.
- TH-A7 mapping: panel group and `column` for each bound item equal §3; `lane=` filter returns the same set as the panel group.
- TH-A8 no pollution: `items[]` and `counts.total` of board/inbox/attention/staleness are identical before and after a sync pass.
- TH-A9 precedence: with a conflicting local `placement`, the rendered lane is TASK-HOME's; the `declared_field` row for `placement` is byte-unchanged (before/after dump).
- TH-A10 conflicts: `counts.task_home.conflicts` equals the number of conflict entries; every entry names both values and the winner.
- TH-A11 receipt flag: `RECEIPT SAYS CLOSED|COMPLETE` sets `receipt`+`receipt_text` verbatim; the section is unchanged; `receipt_section_mismatch` present.
- TH-A12 write boundary: pre/post sha256 of `project`, `project_session`, `evidence`, `next_action`, `review_event`, `scan_run` unchanged; only `task_home_*` declared rows differ.
- TH-A13 schema: `SCHEMA_VERSION == 3`; the full suite is green with the counted total unchanged.
- TH-A14 frozen shapes: bare `GET /projects` key set unchanged; `_card()` key set unchanged (KB-N1/CP-T13 green).
- TH-I1 idempotency: second pass on unchanged source reports `changed=0` and the `declared_field` table hash is byte-identical before/after.
- TH-I2 idempotency under edit: a text edit changes exactly the `task_home_line`/`task_home_content_sha1` values and adds no binding.
- TH-I3 export stability: two exports from one unchanged source are byte-identical; the body contains no timestamp.
- TH-I4 concurrency: a second concurrent pass exits 3 with `SYNC_LOCKED` and the ledger hash is unchanged.
- TH-R1 round-trip fixed point: `parse(render(parse(fixture)))` equals `parse(fixture)` on items, sections, lanes (34 items).
- TH-R2 export completeness: every Continuum project appears in exactly one bucket; `counts.total == bound + DASHBOARD_ONLY`.
- TH-R3 additions marked: proposed lines/section are flagged separately; no existing line is modified in the export.
- TH-R4 no auto-write: after `export`, the fixture source sha256 is unchanged.
- TH-C1 receipt outranks: a RUNNING bullet with a CLOSED receipt stays `RUNNING` in panel and lane and appears in `confirm_then_close`.
- TH-C2 dashboard outranks nothing: the shadowed local value is present in `conflicts[]` and the rendered status is TASK-HOME's.
- TH-C3 derived claims quarantined: confidence/tier/session counts appear only inside the export's `derived` block, never as status or next-action text.

Shaka (independent QA):
- TH-G1 re-run TH-A/TH-I/TH-R on the delivered tree and re-measure the counts (§1) from the fixture, not the live drifting file.
- TH-G2 adversarial parse: malformed heading, duplicate slug, missing proc, reordered keyless lines, CRLF file, bullet with no `:` separator, empty section. Each yields a defined warning; none crashes or silently drops.
- TH-G3 boundary audit: sha/mtime of TASK-HOME.md, every source DB, and the installed tree; full-suite no-regression; zero forbidden-file writes.

---

## 9. OPEN (not invented — needs a decision)

- OPEN-1 panel copy/labels/empty states: Frida's spec (this artifact locks the data contract only).
- OPEN-2 attention-rail participation for `AWAITING_OWNER` (recommend: yes in v2, via an explicit `task_home_awaiting` attention condition that keeps INV-MC-6 grounding).
- OPEN-3 exact new-section heading for dashboard-origin lines in TASK-HOME (recommended `## DASHBOARD-DERIVED (Continuum)`); Orda's edit decision.
- OPEN-4 a stable key marker on keyless bullets (recommended `[key: <slug>]` at line end) — removes the TH-L4 fallback entirely.
- OPEN-5 wrapped/continuation bullet lines: parser currently treats one bullet per `- ` line (recommended: keep single-line bullets and add a warning when a line exceeds N chars).
- OPEN-6 watch mode / in-process watcher (`task_home_enabled` + explicit user approval).
- OPEN-7 an explicit "Import TASK-HOME" button (needs a route decision; the CLI pass is the v1 path).
- OPEN-8 whether bindings may be created by import without a human confirm (recommended: `task_home_binding="import"`, human confirm only for overrides).
- OPEN-9 compound bullets (`; +`, 16/34) splitting into separate items; v1 keeps line granularity and warns.
- OPEN-10 CLOSED retention/pruning of ledger history (v1 keeps all runs).

> **CLOSED-4:** A stable key marker on keyless bullets. Superseded by the v2 grammar amendment:
> `- slug: text` is now keyed directly; no marker syntax is needed for bullets that already carry a
> slug. OPEN-4 remains open for genuinely keyless bullets (those without any slug) if a future
> decision wants to adopt `[key: <slug>]` for them.

---

## 10. Handoff to Mozi

Read: this artifact (§2 locks, §4 keys, §5 mechanism/write boundary, §7 files, §8 ids) plus the frozen fixture copies you create from TASK-HOME (sha256 5da1f01d…). Do not write TASK-HOME; do not write outside §7. Build order: `continuum/task_home.py` parser+keyer (TH-A1..TH-A6) → ledger+sync (TH-I1..TH-I4, TH-A12) → service projection + `home=` (TH-A7..TH-A11, TH-A13, TH-A14) → CLI → export (TH-R1..TH-R4) → panel rendering to Frida's spec (OPEN-1). Report the ledger path, the export path, and the pre/post table hashes in `review/task-home-sync-receipt.md`. Any deviation is recorded as a deviation, not silently absorbed.
