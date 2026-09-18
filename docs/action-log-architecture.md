# Orda Action-Log Page — Architecture Lock (v1)

STATUS: STABLE

owner: Leo (solution/software architecture)
domain: architecture
project: Continuum / Orda action-log page + archive
task: action-log (leo-architecture)
workspace: /Users/kethuda/.hermes/profiles/proteus/cache/delegation/action-log
profile/session/provider at dispatch: leo / 20260917_... / nous · qwen/qwen3-coder-next
  (read from /Users/kethuda/.hermes/profiles/leo/config.yaml at 2026-09-17T21:23Z:
   model.provider=nous, model.default=qwen/qwen3-coder-next)
upstream (read on disk this pass, not re-derived):
  - INFLIGHT.md (team-skills/ops/INFLIGHT.md, 247 lines, ~130 claim rows)
  - DISPATCH-LEDGER.md (team-skills/ops/DISPATCH-LEDGER.md, 429+ lines, ~20+ dispatch rows)
  - delegation directories: 300+ under profiles/proteus/cache/delegation/ + ~50 across other profiles
  - source build: /Users/kethuda/.hermes/profiles/proteus/cache/session-project-indexer/build/
  - prior architecture of record: ../continuum-sync/leo-architecture.md (LOCKED, TH-L1..TH-L16)
  - review route code: service.py:2812-2996 (review()), service.py:2979-2996 (_undo())
  - registry audit: registry.py ReviewEvent dataclass
  - task_home_sync.json: pattern reference for JSON ledger store
downstream: Mozi (implementation of §7 boundary) then Shaka (QA gates AL-G1..AL-G4)
method: every decision is anchored to a named on-disk artifact, a file:line, or a measurement
  taken this pass. Nothing is stated from memory.
scope: row schema, evidence sources, sync mechanism, archive/unarchive, distinct layout,
  wire shape, implementation file boundary, acceptance criteria.
non-claims: this artifact writes no build file, no TASK-HOME line, no registry row;
  installs/enables/deploys nothing; issues no QA verdict; closes no OPEN item.

---

## 0. Ground truth measured this pass

Evidence source inventory (read-only):

| source | path | rows/items | anchor |
|---|---|---|---|
| INFLIGHT.md | team-skills/ops/INFLIGHT.md | ~130 claim rows (247 lines total) | measured: file read, rows counted |
| DISPATCH-LEDGER.md | team-skills/ops/DISPATCH-LEDGER.md | ~20+ dispatch rows (429+ lines) | measured: file read |
| Delegation receipts | profiles/*/cache/delegation/**/*-receipt.md | hundreds | measured: directory listing |
| Delegation directories | profiles/*/cache/delegation/ | 300+ | measured: ls output |

Existing dashboard state (measured):
- Source build at build/ (READ-ONLY this stage).
- Existing wire: routes /projects, /projects/{id}, /candidates, /attention, /staleness, /noise,
  /scan/status, POST /scan, POST /projects/{id}/review, POST /review/undo, /overview, /events.
- Playground /projects query vocabulary: view∈{today,all}, sort, direction, lane∈BOARD_COLUMNS,
  lifecycle∈LS-1..LS-9, attention, band, profile, q, page, page_size (service.py:1123-1211).
- Served demo: http://127.0.0.1:8767/ (loopback, serving the SOURCE BUILD).
- Active apply claim: inf-20260917-2115-continuum-apply-mozi (owns config.yaml, task_home_sync.*,
  task-home-export.*, :8771). Lease to 23:15Z.

Review route code (read-only this pass, used as pattern reference):
- service.py:2812 — `def review(self, action, target_id, payload, actor)`: entry point.
- service.py:2853 — `before = registry.snapshot(...)`: snapshot before mutation.
- service.py:2856 — `with registry.transaction():`: atomic mutation block.
- service.py:2968-2971 — `ev = ReviewEvent(...); registry.append_review_event(ev)`: audit append.
- service.py:2979 — `def _undo(self, audit_id, actor)`: undo entry point.
- service.py:2987 — `self.registry.restore(before)`: restore from snapshot.
- registry.py — `ReviewEvent` dataclass: event_id, ts, actor, action, target_type, target_id,
  before_json, after_json, rev.

---

## 1. Decision table — LOCKED

| ID | Lock | Rationale | Anchor |
|---|---|---|---|
| AL-L1 | Action log is a PROJECTION of evidence sources — never a second authority. Every row links its evidence source (receipt path, post URL, or commit SHA). Rows may never carry facts that contradict their evidence source. The log is rebuildable from sources. | Same principle as TH-L1 (projection-of-receipts). The action log reads evidence; it does not create facts. | brief ("the action log is a PROJECTION of receipts"); TH-L1 |
| AL-L2 | Store: `build/data/action_log.json` (JSON ledger, schema 1) + `build/data/action_log_events.jsonl` (append-only audit) + `build/data/action_log.lock` (exclusive lock). No registry DDL. SCHEMA_VERSION stays 3. All action-log state rides the JSON ledger and its events sidecar. | Follows task_home_sync.json pattern (TH-L2/TH-L15). Keeps registry untouched. Disjoint file set from apply lane. | TH-L2; TH-L15; task_home_sync.json pattern |
| AL-L3 | Row schema locked (§2). Fields: action_id (string, deterministic), timestamp (float, Unix UTC), kind (enum 6 values), target (string), target_type (enum 6 values), evidence_type (enum 3 values), evidence_link (string), operator_flag (enum 2 values), source (enum 5 values), source_row_id (string\|null), status (enum 2 values), archived_at (float\|null), archive_audit_id (string\|null). 13 fields total. | Every field serves the owner's review use case. The kind/target/evidence triple describes the action; operator_flag distinguishes who acted; source/source_row_id provide provenance; status/archived_at/archive_audit_id manage the archive lifecycle. | brief ("Row schema: time, kind, target, evidence link, operator/manual flag"); §2 |
| AL-L4 | Action-id is deterministic, derived from the source row id when available: INFLIGHT-sourced → `"al-" + claim_id`; DISPATCH-LEDGER-sourced → `"al-" + task_id`; receipt-sourced → `"al-" + sha256(receipt_path + "\n" + kind)[:16]`; git-log-sourced → `"al-" + commit_sha[:16]`. Max 64 chars. | Re-scanning must produce the same ids (idempotency requirement). Source-row ids are stable across re-scans: INFLIGHT claim ids and DISPATCH-LEDGER task ids don't change; receipt paths + kind are deterministic. | §3; idempotency requirement; AL-L9 |
| AL-L5 | Archive/unarchive follows the EXISTING review-route pattern (service.py:2812-2996): (1) snapshot row state before mutation; (2) mutate row in an atomic file-write block (tmp + os.replace); (3) append audit event to events.jsonl matching ReviewEvent schema; (4) undo via event replay (find event → restore before_json → append undo event). Implemented as new method `action_log_review()` in service.py, NOT as a new action on POST /projects/{id}/review — because action-log rows are not Continuum projects and have no project_id (service.py:2846 requires project_id). | The existing review route requires target_id to be a project_id (service.py:2846: `registry.get_project(target_id) is None` raises ValueError). Action-log rows are independent entities. The pattern (snapshot + atomic + audit + undo) is preserved; the endpoint differs. | brief ("EXISTING review-route patterns"); service.py:2812-2996; service.py:2846 |
| AL-L6 | The action-log page is a DISTINCT layout from the board projection. Board = project-centric kanban (8 columns, attention rail, staleness bands). Action-log = action-centric flat table (filterable by kind/date/operator/status, with archive button per row). Different entity (project vs action), different wire shape (cards vs rows), different query vocabulary. They share the HTTP server (service.py) but NOT the same projection code, query parsing, or wire envelope. | Brief requirement: "Distinct layout from the board projection — the action-log page must not be a variant of the board projection." | brief; service.py board projection (L39-117) vs new action-log projection |
| AL-L7 | Wire additions are additive and confined to the PLAYGROUND path. `GET /projects?view=action_log` returns a new envelope shape with `actions[]` instead of `projects[]`. The bare `/projects` envelope key set and `_card()`'s key set stay byte-compatible. | Same approach as TH-L11. Existing wire shapes are frozen (KB-N1/CARD_KEYS). | TH-L11; mc-mission-control-receipt §0.1 |
| AL-L8 | v1 mechanism is an explicit, idempotent SYNC PASS invoked by CLI. No daemon, no watcher, no background thread, no write on any GET path, no scan-time coupling. Exit codes: 0 = changed, 1 = no changes, 3 = LOCKED, 4 = SOURCE_MISSING, 5 = PARSE_DEGRADED. | Same approach as TH-L7. No write-on-read. The action log is populated by an explicit human-initiated pass. | TH-L7; brief ("progress never blocks on input") |
| AL-L9 | Idempotency is content-gated: a pass compares action-ids and content hashes; unchanged sources produce zero ledger writes. The ledger body carries no wall-clock value (run metadata goes to events.jsonl sidecar entries). | Same approach as TH-L8. Repeated passes update existing rows instead of duplicating. | TH-L8 |
| AL-L10 | No config.yaml write until the continuum-apply lane releases (inf-20260917-2115-continuum-apply-mozi, lease to 23:15Z, pid 96978). v1 uses runtime defaults and CLI flags. Config keys (`action_log_enabled: false`, `action_log_source_paths`, `action_log_stale_after_seconds: 900`) are added AFTER apply-lane release, during Mozi's implementation. | The apply lane exclusively owns config.yaml (one-line flip). Serialization requirement from brief. | brief (collision constraint); INFLIGHT row inf-20260917-2115 |
| AL-L11 | Implementation file boundary locked (§7). Mozi writes ONLY within the listed files. Everything else is forbidden. | Prevents scope creep and file-set collision. | §7 |
| AL-L12 | The action-log never writes to TASK-HOME.md, the installed tree /Users/kethuda/.hermes/plugins/continuum/**, any Hermes `<profile>/state.db`, or any source database. | Same boundary as TH-L1. The action log is a read-projection with its own audited store. | TH-L1 |
| AL-L13 | Query vocabulary for view=action_log: `kind=` (repeatable, OR-within), `operator=` (operator\|agent), `status=` (active\|archived), `date_from=` (epoch float), `date_to=` (epoch float), `page=` (int, 1-indexed), `page_size=` (int, default 50, max 200). Invalid value → HTTP 400 on the existing ValueError path. | Defines the owner's search surface. All filters are server-side over the in-memory JSON ledger. | brief ("owner reviews on demand"); §5 |
| AL-L14 | Default view: active rows only, sorted by timestamp desc. Archived rows leave the main view; browsable via `status=archived`. Total = active + archived. | Owner reviews on demand; archived rows don't clutter the default view. | brief ("archived rows leave the main view into a browsable archived section") |
| AL-L15 | Visual/interaction/copy spec belongs to Frida. This artifact locks the DATA contract only and requires Mozi to render server fields without inventing labels. | Domain boundary (design vs architecture). | OWNERSHIP-MATRIX; doctrine §2 |
| AL-L16 | Out of scope, untouched: install/enable/deploy, TASK-HOME edits, desktop plugin, mobile 390px defect, Raptora, HUD, TASK-HOME sync (TH-L* locks). | Keeps scope bounded. | brief; TH-L16 |

Locked-together note: AL-L1/AL-L5/AL-L12 keep the action log as a read-projection with its own
audited store (never a second authority, never writes to the registry). AL-L4/AL-L9 make repeated
passes update existing rows instead of duplicating. AL-L6/AL-L7 keep the action-log wire distinct
from the board wire. AL-L10 serializes against the apply lane.

---

## 2. Row schema (exact fields + types)

| field | type | required | who sets | description |
|---|---|---|---|---|
| action_id | string | yes | sync | Deterministic id (AL-L4). Prefix `al-`. Max 64 chars. |
| timestamp | float | yes | sync | Unix epoch UTC of the action. Derived from INFLIGHT claim_time, DISPATCH-LEDGER launch session, or receipt mtime. |
| kind | string enum | yes | sync | One of: `dispatch`, `direct-edit`, `post`, `merge`, `close`, `retraction`. |
| target | string | yes | sync | Human-readable target. Lane name for dispatch, file path for direct-edit, URL for post, commit SHA or PR URL for merge, lane/file for close, claim id for retraction. Max 512 chars. |
| target_type | string enum | yes | sync | One of: `delegation`, `file`, `url`, `commit`, `pr`, `task-home`. |
| evidence_type | string enum | yes | sync | One of: `receipt-path`, `post-url`, `commit-sha`. |
| evidence_link | string | yes | sync | The actual evidence: path to a receipt file, URL to a post/comment, or commit SHA. Non-empty. Max 1024 chars. |
| operator_flag | string enum | yes | sync | `operator` (human-initiated) or `agent` (agent-executed). See §3 for derivation rules. |
| source | string enum | yes | sync | One of: `inflight`, `dispatch-ledger`, `receipt`, `task-home`, `direct-log`. Which evidence source produced this row. |
| source_row_id | string \| null | no | sync | ID of the source row: INFLIGHT claim_id, DISPATCH-LEDGER task_id, or receipt file path. Null when no stable source id exists. |
| status | string enum | yes | archive | `active` (default, visible in main view) or `archived` (hidden from main view, visible in archived section). |
| archived_at | float \| null | yes | archive | Unix epoch when archived. Null when active. |
| archive_audit_id | string \| null | yes | archive | Event ID (uuid hex) of the archive action in events.jsonl. Null when active. |

Ledger shape (build/data/action_log.json, schema 1):
```
{"schema": 1, "last_sync_at": <float|null>,
 "last_source_hashes": {"inflight_md5": "<hex>", "dispatch_ledger_md5": "<hex>"},
 "items": [<row objects per §2>]}
```

Events shape (build/data/action_log_events.jsonl, one JSON object per line):
```
{"event_id": "<uuid hex>", "ts": <float>, "actor": "<string>",
 "action": "archive_action_log|unarchive_action_log|undo",
 "target_id": "<action_id>",
 "before_json": "<JSON string of row state before mutation>",
 "after_json": "<JSON string of row state after mutation>",
 "rev": <int>}
```

---

## 3. Evidence sources and collection

The sync pass scans these sources (read-only) and projects them into action-log rows:

| source | path pattern | row derivation | kind mapping |
|---|---|---|---|
| INFLIGHT.md | team-skills/ops/INFLIGHT.md | One row per claim (columns: claim_id, session_id, profile, provider/model, tree, owned_files, claim_time, ...). Each claim = a dispatch action. Released + crashed + active rows are all projected. | `dispatch` |
| DISPATCH-LEDGER.md | team-skills/ops/DISPATCH-LEDGER.md | One row per task dispatch (columns: task_id, owner_profile, model, domain, ...). Overlaps with INFLIGHT but INFLIGHT is session-bound and more granular. | `dispatch` |
| Receipt files | profiles/*/cache/delegation/**/*-receipt.md | Parsed for action keywords in content: "post" / "posted" / "comment" → `post`, "merge" / "merged" → `merge`, "close" / "closed" / "COMPLETE" → `close`, "retract" / "retraction" / "BLOCKED" → `retraction`, "edit" / "patch" / "write" → `direct-edit`. | varies |
| Git log (optional v1+) | build tree git log, delegation repo logs | Merge commits → `merge`. Config/auth changes → `direct-edit`. Deferred to v2 if receipts cover cases. | `merge`, `direct-edit` |

Source precedence: INFLIGHT supersedes DISPATCH-LEDGER for the same action (INFLIGHT is
session-bound and more precise — it records the actual session, claim time, and release time).
Receipts add detail that INFLIGHT doesn't capture (post URLs, merge SHAs, close confirmations).

Operator flag derivation:
- INFLIGHT rows: action type contains "direct-records-action" → `operator`. All others → `agent`.
  (Grounding: INFLIGHT rows like `inf-20260917-2121-pr98616-close-proteus` have the string
  "proteus session, direct records action" in their session id column.)
- DISPATCH-LEDGER rows: all `agent` (Proteus dispatches workers).
- Receipt-sourced rows: check receipt for "operator" or "manual" keyword → `operator`.
  Default → `agent`.

---

## 4. Mechanism — sync pass, CLI, endpoints

Mechanism: one explicit idempotent pass. `cli action-log sync` reads evidence sources →
parses → assigns action-ids → writes the ledger → prints a JSON report.

CLI (existing continuum/cli.py gains one subcommand group; no new entrypoint):
```
python -m continuum.cli action-log sync    [--dry-run] [--json]
python -m continuum.cli action-log show    [--json]   # read-only: ledger + events + stats
python -m continuum.cli action-log archive <action_id> [--actor NAME] [--json]
python -m continuum.cli action-log unarchive <action_id> [--actor NAME] [--json]
```

Read endpoint (additive query on the EXISTING playground path):
```
GET /api/plugins/continuum/projects?view=action_log
  [&kind=dispatch|direct-edit|post|merge|close|retraction]
  [&operator=operator|agent]
  [&status=active|archived]
  [&date_from=<epoch>]
  [&date_to=<epoch>]
  [&page=<n>&page_size=<n>]
```
`view=action_log` is repeatable-or-defaultable with the existing `view=today|all`. Invalid
view value → HTTP 400 on the existing ValueError path.

Archive/unarchive endpoint (new route, same pattern as review — service.py:2812-2996):
```
POST /api/plugins/continuum/action-log/{action_id}/archive
POST /api/plugins/continuum/action-log/{action_id}/unarchive
  body: {"actor": "<string>"}
```
Returns: `{"ok": true, "audit_id": "<hex>", "action": "archive_action_log", "target_id": "<action_id>"}`.

Internal implementation: service.py gains `action_log_review(action, action_id, actor)` method
following the EXACT pattern of `review()` (service.py:2812-2996):
1. Load ledger, find the row by action_id.
2. Snapshot the row state → before_json.
3. Mutate: set status / archived_at / archive_audit_id.
4. Append audit event to events.jsonl (matching ReviewEvent schema: event_id, ts, actor,
   action, target_id, before_json, after_json, rev).
5. Write ledger atomically (tmp + os.replace under exclusive lock).
6. Return {"ok": True, "audit_id": ..., "action": ..., "target_id": ...}.

Undo: service.py gains `_action_log_undo(audit_id, actor)` following `_undo()` (service.py:2979-2996):
1. Find event in events.jsonl by event_id.
2. Parse before_json.
3. Restore row to before state.
4. Append undo event to events.jsonl.
5. Return undo result.

Write boundary of the sync pass (exhaustive):
- `build/data/action_log.json` (ledger, tmp + os.replace)
- `build/data/action_log_events.jsonl` (append-only)
- `build/data/action_log.lock` (exclusive lock file)
Everything else is forbidden (see §7).

---

## 5. Wire shape (playground path addition)

Envelope for view=action_log:
```
{"view": "action_log",
 "counts": {"total": <int>, "active": <int>, "archived": <int>,
            "by_kind": {"dispatch": <int>, "direct-edit": <int>, "post": <int>,
                        "merge": <int>, "close": <int>, "retraction": <int>},
            "by_operator": {"operator": <int>, "agent": <int>}},
 "actions": [<row objects per §2>],
 "page": <int>, "page_size": <int>, "has_more": <bool>}
```

The `projects` key is ABSENT when view=action_log. The `actions` key is ABSENT when
view=today|all. This is an additive extension — existing views are byte-unchanged.

Default sort: timestamp desc. Additional sorts: kind (asc), target (asc), operator_flag (asc).

---

## 6. Distinct layout vs board projection

| dimension | board projection | action-log projection |
|---|---|---|
| entity | Continuum project | Orda action |
| identity key | project_id (uuid5, deterministic from cluster signature) | action_id (deterministic, source-derived, AL-L4) |
| layout | 8-column kanban (inbox, ongoing, blocked, waiting_on_you, paused, done, shipped, scrapped) + attention rail + staleness bands | flat table + filter bar (kind, date, operator, status) + archive button per row + archived section |
| wire shape | project cards: name, placement, derived_lifecycle, attention_state, confidence, confidence_band, evidence_tier(s), source_sessions[] | action rows: action_id, timestamp, kind, target, evidence_link, operator_flag, status |
| query vocabulary | view, sort, direction, lane, lifecycle, attention, band, profile, q, page, page_size | view, kind, operator, status, date_from, date_to, page, page_size |
| mutations | accept, set_placement, merge, split, set_urgent, set_lifecycle, park, etc. via POST /projects/{id}/review (service.py:2812) | archive, unarchive via POST /action-log/{id}/archive (new route, same pattern) |
| data source | registry (SQLite, build/data/registry.db) + scan results | JSON ledger (build/data/action_log.json) rebuilt from evidence sources |
| lane vocabulary | BOARD_COLUMNS = (inbox, ongoing, blocked, waiting_on_you, paused, done, shipped, scrapped) | none — flat table, no lanes |

The two projections share the same HTTP server (service.py) and authentication/authorization,
but they do NOT share projection code, query parsing, or wire envelope. The action-log is
NOT a filtered/sorted variant of the board.

---

## 7. Implementation boundary (exact files for Mozi)

All paths are in the SOURCE BUILD
`/Users/kethuda/.hermes/profiles/proteus/cache/session-project-indexer/build/`.

MAY WRITE:
- `continuum/action_log.py` (NEW: evidence collector, parser, id-assigner, sync engine,
  archive/unarchive logic; pure functions, injected clock/paths, no HTTP, no network).
- `continuum/cli.py` (additive: one `action-log` subcommand group: sync, show, archive, unarchive).
- `continuum/service.py` (additive: `action_log_review()` method, `_action_log_undo()` method,
  `view=action_log` in `_parse_board_query`/`_matches_query`, action-log envelope construction,
  `POST /action-log/{id}/archive|unarchive` route wiring).
- `continuum/config.py` + `config.yaml` (additive non-secret keys: `action_log_enabled: false`,
  `action_log_source_paths: [...]`, `action_log_stale_after_seconds: 900`; added AFTER
  apply-lane release per AL-L10, with fail-loud validation matching existing `_validate_bundle`).
- `dashboard/plugin_api.py` (pass-through of `view=action_log` and `/action-log/` POST routes).
- `dashboard/static/app.js` + `dashboard/static/styles.css` (action-log panel rendering,
  server fields per Frida's spec — AL-L15).
- `tests/test_action_log_sync.py` (NEW).
- `tests/test_action_log_archive.py` (NEW).
- `fixtures/action_log/*.md` (NEW frozen copies of evidence sources for testing).
- `build/data/action_log.json`, `build/data/action_log_events.jsonl`,
  `build/data/action_log.lock` (local, gitignored).
- `review/action-log-receipt.md` (Mozi receipt).

MUST NOT WRITE:
- TASK-HOME.md (AL-L12; TH-L1).
- Any Hermes `<profile>/state.db`.
- The installed tree `/Users/kethuda/.hermes/plugins/continuum/**`.
- `continuum/registry.py` DDL or `SCHEMA_VERSION` (stays 3, AL-L2).
- `continuum/{scanner,cluster,evidence,classify,model}.py`.
- `continuum/task_home.py` (owned by TH-L* locks).
- `desktop/plugin.js`.
- `build/data/registry.db` by hand.
- `build/config.yaml` until apply-lane release (AL-L10).
- `build/data/task_home_sync.{json,lock}` (owned by continuum-apply).
- `build/review/out/task-home-export.*` (owned by continuum-apply).
- Frida/Orda/Shaka artifacts.
- Anything public; no install/enable/deploy/PR/cron/daemon.

---

## 8. Serialization vs continuum-apply lane

The continuum-apply claim (inf-20260917-2115-continuum-apply-mozi, lease to 23:15Z, pid 96978)
exclusively owns:
- `build/config.yaml` (one-line flip)
- registry `task_home_*` declared rows via the audited review route
- `data/task_home_sync.{json,lock}`
- `review/out/task-home-export.*`
- live serve :8771

The action-log uses its own file set (`data/action_log.*`), which is DISJOINT from the apply
lane's files. No write collision on the data files. However, shared files require serialization:

| shared file | apply lane usage | action-log usage | collision? |
|---|---|---|---|
| config.yaml | one-line flip | add action_log_* keys | YES — serialize |
| service.py | task_home methods | action_log methods | NO — additive, different methods |
| plugin_api.py | task_home pass-through | action_log pass-through | NO — additive, different routes |
| cli.py | task-home subcommand | action-log subcommand | NO — additive, different subcommands |

Serialization requirement: Mozi MUST NOT modify config.yaml until the apply lane releases.
service.py, plugin_api.py, and cli.py additions are additive (new methods, new routes,
new subcommands) and can proceed without collision, but the brief requires full serialization
(Proteus gates the handoff). Mozi's implementation begins AFTER the apply lane releases.

Where the action-log and task-home sync share mechanisms (review-route pattern, serve pattern),
the action-log references the shared mechanism and notes the serialization requirement instead
of re-designing it:
- `action_log_review()` follows the same pattern as `review()` (service.py:2812) but operates
  on a different store (JSON ledger vs registry). No shared code path.
- The action-log page is served by the same HTTP server but at a different view. No shared
  projection code.
- Both use the same CLI entry point (continuum/cli.py) but different subcommands. No shared
  argument parsing.

---

## 9. Acceptance IDs

Mozi (implementation, each an executed test or a read-back command):

- AL-A1 sync completeness: all INFLIGHT rows (~130) and DISPATCH-LEDGER rows (~20) are
  projected into action-log rows. Count matches measured totals ±5%.
- AL-A2 row schema: every field present with correct type. kind ∈ 6 values. evidence_link
  non-empty. operator_flag ∈ {operator, agent}.
- AL-A3 deterministic ids: re-scanning produces byte-identical action-ids. No random ids.
  INFLIGHT claim ids map 1:1 to action-ids.
- AL-A4 idempotency: second pass on unchanged sources reports changed=0 and the ledger
  hash is byte-identical before/after.
- AL-A5 archive: archive sets status=archived, archived_at (float, non-null), archive_audit_id
  (string, non-null). Audit event appended to events.jsonl with before_json and after_json.
- AL-A6 unarchive: unarchive clears archived_at (null) and archive_audit_id (null), sets
  status=active. Audit event appended.
- AL-A7 undo: undo of archive restores the row to active state. The restored state is
  byte-identical to the before_json. Audit event appended with action=undo.
- AL-A8 distinct layout: view=action_log returns `actions[]` and `counts` (no `projects[]`).
  view=today returns `projects[]` and `counts` (no `actions[]`). Key sets are disjoint.
- AL-A9 filters: kind=, operator=, status=, date_from=, date_to= all return correct subsets.
  Invalid value → HTTP 400.
- AL-A10 archived section: default view (no status filter) returns only status=active rows.
  status=archived returns only archived rows. Total across both = sum of active + archived.
- AL-A11 no pollution: board/inbox/attention/staleness views are byte-identical before and
  after action-log sync. items[] and counts.total unchanged.
- AL-A12 write boundary: pre/post sha256 of project, project_session, evidence, next_action,
  review_event, scan_run tables unchanged; task_home_sync.json hash unchanged. Only
  action_log.* files differ.
- AL-A13 schema: SCHEMA_VERSION == 3. Full suite green with counted totals unchanged.
- AL-A14 frozen shapes: bare GET /projects key set unchanged; _card() key set unchanged
  (KB-N1/CP-T13 green).
- AL-I1 idempotency (sync): second pass on unchanged sources reports changed=0. Ledger
  hash byte-identical before/after.
- AL-I2 idempotency (archive): double-archive is a no-op (status already archived). Returns
  ok=true with existing audit_id. No duplicate event appended.
- AL-I3 events stability: events.jsonl grows monotonically (append-only). No event is ever
  deleted or modified. Line count after N operations == N.
- AL-I4 concurrency: a second concurrent sync pass exits 3 with LOCKED and the ledger
  hash is unchanged.

Shaka (independent QA):

- AL-G1 re-run AL-A/AL-I on the delivered tree and re-measure the counts from frozen
  fixtures, not the live drifting file.
- AL-G2 adversarial sync: empty INFLIGHT, malformed receipt (no keyword), duplicate claim id,
  CRLF file, receipt with no `:` separator, empty delegation directory. Each yields a defined
  warning; none crashes or silently drops.
- AL-G3 boundary audit: sha/mtime of TASK-HOME.md, every source DB, task_home_sync.*, and
  the installed tree unchanged; full-suite no-regression; zero forbidden-file writes.
- AL-G4 archive audit: archive → undo round-trip preserves the original row state exactly
  (byte-identical before_json and restored state). events.jsonl has exactly 2 events after
  the round-trip.

---

## 10. OPEN (not invented — needs a decision)

- OPEN-1 receipt parsing rules: exact keywords/regex for extracting kind from receipt content.
  v1 uses simple keyword matching (§3). NLP extraction is out of scope.
- OPEN-2 git-log integration: whether v1 includes git-log scanning. Recommended: defer to v2,
  receipts cover most cases.
- OPEN-3 retention/pruning of archived rows. Recommended: keep all, no pruning in v1.
- OPEN-4 whether the action-log should auto-populate from new INFLIGHT rows. Recommended: no,
  sync is explicit per AL-L8.
- OPEN-5 the exact URL path for the action-log page in the dashboard UI. Recommended: view=action_log
  query parameter on the existing playground path (AL-L7).
- OPEN-6 visual design: table layout, column widths, colors, empty states. Frida's spec (AL-L15).
- OPEN-7 whether operator_flag derivation needs a more sophisticated heuristic. Recommended:
  simple keyword match for v1 (§3).
- OPEN-8 batch archive: whether to support selecting multiple rows and archiving in one action.
  Recommended: defer to v2.

---

## 11. Handoff to Mozi

Read: this artifact (§1 locks, §2 schema, §3 sources, §4 mechanism/write boundary, §7 files,
§9 ids) plus the continuum-sync architecture (../continuum-sync/leo-architecture.md,
TH-L1..TH-L16) for pattern reference. Do not write outside §7.

Build order:
1. `continuum/action_log.py` — evidence collector + parser + id-assigner (AL-A1..AL-A4)
2. Ledger + sync engine (AL-I1..AL-I4, AL-A12)
3. Archive/unarchive + audit events (AL-A5..AL-A7)
4. service.py projection + view=action_log (AL-A8..AL-A11, AL-A13, AL-A14)
5. CLI (action-log sync/show/archive/unarchive)
6. Endpoint (POST /action-log/{id}/archive|unarchive)
7. Panel rendering to Frida's spec (OPEN-6)
8. Tests + fixtures

Report the ledger path, the events path, and the pre/post file hashes in
`review/action-log-receipt.md`. Any deviation is recorded as a deviation, not silently absorbed.

Sequence: implementation begins AFTER the continuum-apply lane releases. Proteus gates that handoff.

LOCKED
