# mozi-apply-receipt — Continuum TASK-HOME sync APPLY (bounded recovery pass)

- Goal: apply Continuum sync live in the SOURCE BUILD (owner-approved `apply` lane).
- Owner/route: Proteus (integration) → Mozi (implementation / stage 4). Recovery = bounded restart #1
  after `KeyboardInterrupt` in the first pass; inflight claim reused: `inf-20260917-2115-continuum-apply-mozi`.
- Briefs: `briefs/mozi-apply.md` (authoritative) + `briefs/mozi-apply-recovery.md` (this pass).
- Source build (only writable tree): `/Users/kethuda/.hermes/profiles/proteus/cache/session-project-indexer/build`
- Interpreter: `/Users/kethuda/.hermes/hermes-agent/venv/bin/python`
- Receipt written: 2026-09-17T21:36:58Z (17:36 EDT).

## 0. Scope of THIS pass — and what was deliberately NOT re-run

Steps 1–4 of the original brief were already done and verified before the interrupt. Per the recovery
brief they were NOT redone; they are carried forward here **by reference to their on-disk evidence**,
which I re-read this session (read-back, not memory):

| Step | State | Evidence file (read this session) |
|---|---|---|
| 1 Config flip | DONE, not re-edited | config.yaml line 225 = `task_home_enabled: true`; sha256 `1d166f82cb22de7f2b351d44e93d9402435149a9a7850203720a094efa369f7c` |
| 2 Sync #1, #2 | DONE, not re-run | `launch-logs/sync1.json`, `launch-logs/sync2.json` |
| 3 32 binds | DONE, `apply_binds.py` NOT re-run (a second pass would duplicate audit events) | `launch-logs/binds-result.json`, `launch-logs/bind-events.json` |
| 4 Live server | ALREADY RUNNING, not restarted/duplicated | PID 11789 `python -m dashboard.standalone --host 127.0.0.1 --port 18771` (alive, elapsed 12:37 at final check) |

Executed this pass: original-brief steps 5, 6, 7, 8, 9 (recovery-brief steps 5–9). The 7-close
presentation (original step 8) is re-checked against the live source below.

## 1. (Step 5) Sync pass #3 — post-bind re-check

Command (from build root, no `--source`; reads the live configured source):

    HERMES_HOME=/Users/kethuda/.hermes \
      /Users/kethuda/.hermes/hermes-agent/venv/bin/python -m continuum.cli task-home sync --json

Real result (full JSON: `launch-logs/sync3.json`; stderr empty `launch-logs/sync3.err`):

| field | value |
|---|---|
| run_id | `0d96e373604c467b9f08055735c01773` |
| exit_code | **5** (PARSE_DEGRADED — LOCKED behavior for a heading outside the 4-section contract; EXPECTED, not a failure) |
| source_path | `/Users/kethuda/.hermes/profiles/orda/TASK-HOME.md` |
| source_sha256 | `f9e49814bfa0ed4eaf814fc108e3d2d8d5120891f8a1ad612392ca8abad2df1c` (== baseline, == post-pass) |
| item_count | 49 |
| added / changed / unchanged | 0 / 0 / 0 |
| writes | **0** |
| absent / absent_new | 0 / 0 |
| item_fields_written | [] |
| conflicts | [] |
| warnings | `compound_bullet` (informational), `heading_unrecognized` (degraded) |
| section_counts | RUNNING 7 / AWAITING_OWNER 13 / OPEN_FOLLOW_UP 6 / CLOSED 21 / UNRECOGNIZED 2 |

Hash proof inside the pass — `table_hashes_before` == `table_hashes_after`, byte-identical:

    project         fa2bbb9dd3e214a728f66d18f10afb4f27f71ead749402f3ec45e11f56555553
    project_session 9fb88b8b367fcbe15a36892dc8638947a5b9958de0bd0a4378da47795a2d1d99
    evidence        c92100ac2d204a6b441bd5fe8c9b63aff07acd8d10f2a99e711fa339099c1d7c
    next_action     e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855
    review_event    cc02e80bac2ff5d03c9f1f4f4df17ec935819d1c140b67e384bcf9a119318951
    scan_run        80c3fe79c3295ef159444fd2079cded78da14482756851f833e499383f7f485b
    declared_field  5fc15da176f204b6d6960a2e533ad274e20b91024b3aac4683ec05e4ae5f5a7c

Human bindings are PRESERVED: the 6 keyed RUNNING slugs still parse as `binding: null` /
`project_id: null` (they were never bound — human decision), and the 32 new human bindings carry no
TASK-HOME line, so no ledger item references them and `writes=0`.
No sha drift occurred mid-pass, so the "re-run once at the end" clause did NOT trigger: TASK-HOME
sha256 before == after == `f9e49814…`; registry file sha256 before == after ==
`812008f200ce75e63903dabfa0dc99f81dbb362515be9b5804334afafc9978df`.

Ledger after the pass (`build/data/task_home_sync.json`, sha256
`fdd8b95134a0aa5a3620067cb159e2f90c99df9dc3cafa6121e7c8f9c4b64f37`): schema 1, items 49,
runs 7 (4 pre-existing + `2b472ecd…` #1, `5aef28f8…` #2, `0d96e373…` #3, all `writes: 0`),
`last_run_id = 0d96e373604c467b9f08055735c01773`, `last_synced_at = 1789680895.906275`,
`conflicts = []`.

Sync run ids, all three passes: #1 `2b472ecd0caf40bca0d1e9293e2425ea`, #2 `5aef28f810cc4b26a4267252799ced49`,
#3 `0d96e373604c467b9f08055735c01773` — each exit **5**, writes **0**, item_count **49**.

## 2. (Step 6) Export refresh ×2 — byte-stable

Command (build root), run twice:

    HERMES_HOME=/Users/kethuda/.hermes \
      /Users/kethuda/.hermes/hermes-agent/venv/bin/python -m continuum.cli task-home export --json

| pass | run_id | exit | .md sha256 | .json sha256 | .meta.json sha256 |
|---|---|---|---|---|---|
| #1 | `58b4e129cab44ff88bed47c0167e764c` | 0 | `3e2808f5dc15d42c519a808ada48fdbb90af181ba1693bfd4f97e7624ad09bfc` | `0b102547d402620a280b928bb957fe40df03c59f64e17613c72fdd93cc3862d6` | `6841e054480312b9b5f740b5cc9f70433aad7cdb1b8e5c2c74336ad8417d0264` |
| #2 | `b229cc4cded0479e8fbfa5a554270ca1` | 0 | `3e2808f5dc15d42c519a808ada48fdbb90af181ba1693bfd4f97e7624ad09bfc` | `0b102547d402620a280b928bb957fe40df03c59f64e17613c72fdd93cc3862d6` | `5eb9f2310b00148c577ff83cfa906d03727af314996abc3192855da202c4226b` |

- `.md` and `.json` are byte-identical across the two runs (`cmp` clean) — byte-stable.
- `.meta.json` differs ONLY in `run_id` + `generated_at` (`diff` output: lines 2–3). Timestamps are
  confined to `.meta.json`, as designed. Export run ids: `58b4e129…`, `b229cc4c…`.
- Exported counts (both passes): `{"sections": 4, "items": 49, "bound": 0, "dashboard_only": 32,
  "confirm_then_close": 0, "conflicts": 0}` — `confirm_then_close = 0` (no `RECEIPT SAYS` lines remain
  in RUNNING), `dashboard_only = 32`, `conflicts = 0`. Matches the brief's expectation (items follows the
  live count 49, not the brief prose 50).
- `source_sha256` in both payloads: `f9e49814…`.

## 3. (Step 7) Zero-write proof — 5 repeated GETs

`GET http://127.0.0.1:18771/api/plugins/continuum/projects?view=all` × 5
(script `launch-logs/zero_write_proof.py`, results `launch-logs/zero-write-proof.json` +
`launch-logs/zero-write-runs.json`). After EACH GET the declared_field hash was recomputed from a
READ-ONLY sqlite connection (`continuum.task_home.table_hashes(conn, declared_only=True)`).

| GET | HTTP | declared_field hash | task_home_* declared rows | review_event rows | registry file sha256 |
|---|---|---|---|---|---|
| 1 | 200 | `5fc15da176f204b6d6960a2e533ad274e20b91024b3aac4683ec05e4ae5f5a7c` | 96 | 34 | `812008f2…c9978df` |
| 2 | 200 | `5fc15da176f204b6d6960a2e533ad274e20b91024b3aac4683ec05e4ae5f5a7c` | 96 | 34 | `812008f2…c9978df` |
| 3 | 200 | `5fc15da176f204b6d6960a2e533ad274e20b91024b3aac4683ec05e4ae5f5a7c` | 96 | 34 | `812008f2…c9978df` |
| 4 | 200 | `5fc15da176f204b6d6960a2e533ad274e20b91024b3aac4683ec05e4ae5f5a7c` | 96 | 34 | `812008f2…c9978df` |
| 5 | 200 | `5fc15da176f204b6d6960a2e533ad274e20b91024b3aac4683ec05e4ae5f5a7c` | 96 | 34 | `812008f2…c9978df` |

**Recorded declared_field hash (byte-identical across all 5 GETs):
`5fc15da176f204b6d6960a2e533ad274e20b91024b3aac4683ec05e4ae5f5a7c`** — read is zero-write.
Registry file sha256 identical across all 5; declared/review row counts identical across all 5.

Body-level detail (measured, extra 2-GET diff): the served JSON is NOT byte-identical between two GETs —
it differs in exactly two volatile clock fields, `.server_time` and `.task_home.age_seconds`. Everything
else is identical, including the whole `items[]` array (sha256
`01c8bcefb0543c49cb098852e74c5b8f6851dd7fd07cabd4f9322595fea4c4c3`, equal across GETs). So the body
difference is timing metadata only, with no data change — the brief's criterion (declared_field hash
byte-identical across all 5) holds.

## 4. (Recovered step 3) The 32 audited binds — read-back, not re-run

`apply_binds.py` was NOT re-executed (a second pass would duplicate audit events). Read-back this
session from `launch-logs/binds-result.json` (32 rows) and `launch-logs/bind-events.json` (32 rows):
all 32 `http 200`, all `ok:true`, all `action=bind_task_home`, `target_id` == project_id; audit_ids in
`binds-result.json` are the same 32 ids as `bind-events.json::event_id` (set equality verified);
duplicate-name pairs are faithful to the export's own proposal (`gc1` ×2, `team6-kit` ×2,
`team6-kit-pr` ×2 → 6 projects share 3 keys, as flagged in the original brief step 4d).
`bind-events.json` ts range: 1789680242.496 → 1789680243.076.

| # | audit_id (event_id) | project_id | key | http |
|---|---|---|---|---|
| 1 | `5ec23951dfa8475391323b076f94e9ae` | `005b1d2c-2c6d-539d-af85-eeb5d8a27431` | `azarakiws` | 200 |
| 2 | `445d47469dd844d4b4475aecdbf565d4` | `00cedda4-4c3a-50ac-ade3-d8416a6d144e` | `team6-kit` | 200 |
| 3 | `b4449b86b23c40ea829e2405f18c375a` | `040e5799-36f5-5862-a4df-57e9f7111480` | `gc1` | 200 |
| 4 | `2adc382be0534f9ab198216d33a48bc2` | `0b057475-0fc6-5561-b105-acdd75292d20` | `hermes-agent` | 200 |
| 5 | `3778845ad3cd49cba3da3727f732cb48` | `0f7a6476-fae4-5f1d-83ed-736f09857ecc` | `team6-kit-sitecopy-1789328240` | 200 |
| 6 | `6362018f24254f499ddfc22b0f77d8d4` | `16ada560-11ff-530b-8772-5f0c805314be` | `team6-pr7-merge` | 200 |
| 7 | `02dc15152d3d41a19f442b58837783d4` | `1d0c7aca-04c2-5ec5-aa42-b692f3a0cc36` | `TamaHermes` | 200 |
| 8 | `077d2e7df7a94c87937e41094693dfd1` | `1df29a7b-47d6-5237-83b7-aca1b242290c` | `dashboard-1df29a7b-47d` | 200 |
| 9 | `19f921d8a79242ae927a9e87d3674d70` | `21901fd6-8a90-5ecd-af07-d484cf4a4f10` | `team6-kit-pr` | 200 |
| 10 | `1fb1253c91474dbf8356a3cf7ff3d53b` | `244b2082-43f9-5c56-ac7f-31d9358d4937` | `evopet-pet` | 200 |
| 11 | `d9d12cf21d4b46e480f054e7a9070f32` | `2471013c-37b9-5e94-b071-0bfe9b6da30a` | `command` | 200 |
| 12 | `e0001d919020437e8c466a36114bb75f` | `32991ccc-f397-5be4-ba24-4065732a52bd` | `vtt6` | 200 |
| 13 | `eda9e4225fde48c6b40c64194c20f3fd` | `36dbb243-8067-5333-9d59-eeea79b44753` | `team6-kit-flywheel` | 200 |
| 14 | `2ff70079b39243f5b338d77b1e4922cf` | `37e5379e-75ad-50e1-b992-8b5767b9d2d0` | `team6-kit` | 200 |
| 15 | `e982c6dbfdac4a1cb360e080b47bd548` | `42bfd0f5-61a8-537d-9f2b-e5e0a70ce5c3` | `team-skills` | 200 |
| 16 | `0d919157187643ef9b6b8e8e907a1587` | `48a43e05-ffd3-5a1f-9ac5-7962826e1517` | `dashboard-48a43e05-ffd` | 200 |
| 17 | `6950d34635c24643a76d0b49c2c72229` | `54b3fc2d-3123-5887-9837-bd50a316f7d5` | `pr109015-evidence` | 200 |
| 18 | `905d1f60f8c84a119424dba04f861879` | `55c2d743-e3bc-5aaa-8fc2-bf831b70a7d2` | `gc1` | 200 |
| 19 | `69d4f8b94c1741f387d068ea0719c830` | `5eb0d91b-56a7-542b-92d0-3d33f6fbb47b` | `team6-kit-memwiki` | 200 |
| 20 | `cc86b877200a451288fec344c9a276e3` | `6a991f7e-0f6d-5f8c-896f-202442b42207` | `protean-lcm` | 200 |
| 21 | `98fa21f427634329b45bda39d8de729c` | `7aaa4caf-dd03-57d2-9b51-d04a1157f756` | `team6-kit-wrk` | 200 |
| 22 | `bb391380433b4d0caecc13d90bae9ac8` | `82a903b7-dad5-5041-a415-f16fd1733f2b` | `tmt6` | 200 |
| 23 | `a5a1d54cb959487c8ffbbc7de632992f` | `82c806c8-fc13-5c3f-b0b5-0b24d2b9c5c4` | `macos` | 200 |
| 24 | `70af3b2766004bd8bee5c768845c30cf` | `8ccb6137-59c9-563d-926b-a9d3eefc8d46` | `EvoPet-site-revert` | 200 |
| 25 | `740ad2b9981744c8b444a633c0ab2317` | `a902ff7b-22a6-5e46-9b82-77ed2c9a875e` | `team6-kit-pr` | 200 |
| 26 | `bcade908389542839d80a117e04de259` | `bd969115-cade-5915-8b44-5c9b73eef17a` | `team6-kit-audit` | 200 |
| 27 | `841c62c1ab114d229fbaf9fcfd3e6fa0` | `d1b54382-dc70-5add-857e-737186d590f4` | `team6-site-pr9` | 200 |
| 28 | `87f861885a5a4b539a80c2fdb03be397` | `dd4d2bf9-2e8e-5535-8005-bfa74f372448` | `verify-team6-kit` | 200 |
| 29 | `9f1684e87416461ab84c20b42d6e8167` | `e6bbabcc-f754-5a2e-9d93-9386646c17f3` | `hermes-agent-pr106742-current` | 200 |
| 30 | `b8a1cbff98154a32801cd3c73539d5c6` | `ee61f116-0280-5d0d-bbc5-02d96a6fc462` | `dbt6` | 200 |
| 31 | `3d4cc027fe784ee5ac141e5e9da6958a` | `f476e3d2-7de7-549b-a444-3022d4599dd4` | `pot6` | 200 |
| 32 | `e7ce1cde88634a7b8376aa0754889ef2` | `f939833e-6de2-56ab-b13d-c2f5dbd83e0a` | `EvoPet-site-audit` | 200 |

Fallback path used for the binds (recorded in STATE.md pre-flight): `:8767` (PID 29428) was started
before the task-home code existed and does not carry the task-home envelope, so original-brief step 4c's
fallback applied — binds went through the lane's own standalone server on `127.0.0.1:18771` (same
standalone module, cwd = build, same registry.db). That server is still the live one (PID 11789).

Post-bind registry read-back (this session, read-only):

    SELECT COUNT(*) FROM declared_field WHERE field LIKE 'task_home_%'      -> 96
      by field: task_home_binding 32 | task_home_key 32 | task_home_proc 32
      task_home_binding values: {'human': 32}
    SELECT COUNT(*) FROM review_event                                       -> 34   (baseline 2 + 32)
    review_event rows whose event_id is one of the 32 bind audit_ids        -> 32
    SELECT COUNT(*) FROM project                                            -> 32

## 5. (Step 8) The 7 confirm-then-close items — PRESENTED, nothing closed

Presentation only: Proteus performs the owner confirmations; Orda owns the source file; Mozi closed
nothing, moved nothing, bound nothing beyond the 32. Keys are the 7 old RUNNING keys from the frozen
fixture `build/fixtures/task_home/task-home.md`, whose sha256 is unchanged:
`5da1f01d21a9a7d94f032756ffb0815c6ab98d40e5ad2a4b37a167443ba9b14e`.

Status re-check against the LIVE source (this session, `grep`-equivalent over
`profiles/orda/TASK-HOME.md`, sha `f9e49814…`): **none of the 7 keys occurs anywhere in the live file**
(0 occurrences, so a fortiori none in RUNNING — RUNNING is bullets L7–L13 and contains only the 6 keyed
slugs plus the keyless `continuum-sync follow-up`).

| # | key | original receipt claim (verbatim) | key in live RUNNING? | live-source close evidence | tracked follow-up that rides along |
|---|---|---|---|---|---|
| 1 | `evopet-floater-verify` | "RECEIPT SAYS CLOSED (floater 126 = HUD 126). Confirm lane dir, then close." | no | CLOSED L44: "- EvoPet uncap (pet 126 live) + floater 126 verified; EvoPet #25 + #26 merged; preview-snapshot spam guarded." | — |
| 2 | `upstream-rebase` | "RECEIPT SAYS COMPLETE (4 PRs mergeable, pushes read back). Confirm lane dir, then close." | no | CLOSED L46: "- Upstream 106742 fulfilled-by-maintainer + covered; 4 conflict PRs rebased mergeable." | — |
| 3 | `kit-about` | "RECEIPT SAYS COMPLETE with 2 flags (homepage DNS, README six-vs-seven). Confirm, then close." | no | CLOSED L48: "- Positioning 8/8 merged; kit About live; bounce SOP law + org teams; TypeMon published + playable." | homepage DNS → AWAITING L19 ("- Homepage URL: proteus.askaconsult.com does not resolve (kit About points there). Give correct URL → correction lane."); README six-vs-seven → OPEN FOLLOW-UPS L35 |
| 4 | `typemon-publish` | "RECEIPT SAYS COMPLETE (published + playable). One re-probe pending, then close." | no | CLOSED L48: "…TypeMon published + playable." | re-probe → OPEN FOLLOW-UPS L39 ("- TypeMon Pages: one stability re-probe post-publish.") |
| 5 | `positioning-merge` | "RECEIPT SAYS 8/8 merged. Verify zero-open, then close." | no | CLOSED L48: "- Positioning 8/8 merged; …" | — |
| 6 | `bounce-sop-finish` | "RECEIPT SAYS CLOSED (SOP law + org teams live). Confirm, then close." | no | CLOSED L48: "…bounce SOP law + org teams; …" | platform blockers → AWAITING L25 |
| 7 | `evopet-repo-verify` | "RECEIPT SAYS zero-PR proven. Confirm, then close." | no | no CLOSED line (closed by lane evidence, not by an Orda bullet): `/Users/kethuda/.hermes/profiles/orda/cache/evopet-repo-verify-output.log` — "Verified. Hazen's zero-PR claim is correct." / "Open PRs: TamaHermes [], EvoPet []." / "Code lane closed." | — |

All 7 statuses match the pre-dispatch evidence recorded in `journal.md` (21:40Z entry, Proteus's
individual confirmations against live sha `f9e49814…`): 6/7 already carry a CLOSED-section line in the
live source; `evopet-repo-verify` is proven by its lane log. **These are presentations, not closes.**

## 6. Wire read-back (live serve) — value list

Server: PID **11789**, `python -m dashboard.standalone --host 127.0.0.1 --port 18771`, cwd = source
build. **URL: http://127.0.0.1:18771** (left running; not killed, not duplicated). Started in the
pre-interrupt pass — alive and serving this session (verified by `ps` at final check).

`GET /` → **200**. `GET /api/plugins/continuum/projects?view=all` → 200; envelope:

    task_home.enabled        = true
    task_home.source_path    = /Users/kethuda/.hermes/profiles/orda/TASK-HOME.md
    task_home.source_sha256  = f9e49814bfa0ed4eaf814fc108e3d2d8d5120891f8a1ad612392ca8abad2df1c
    task_home.synced_at      = 1789680895.906275      (age_seconds 31.6 at read; stale_after_seconds 900)
    task_home.stale          = false
    task_home.run_id         = 0d96e373604c467b9f08055735c01773   (sync pass #3)
    task_home.warnings       = ["compound_bullet"]      task_home.conflicts = []
    items (task_home rows)   = 49
    counts.task_home         = {RUNNING:0, AWAITING_OWNER:0, OPEN_FOLLOW_UP:0, CLOSED:0,
                                DASHBOARD_ONLY:32, ABSENT:0, source_only:47, conflicts:0}

`source_only = 47` = 49 items − 0 bound − 2 UNRECOGNIZED (UNRECOGNIZED is outside SECTION_ORDER by
design). `DASHBOARD_ONLY = 32` = the 32 projects now carrying human bindings but no TASK-HOME line.
The 6 keyed RUNNING rows are **byte-verbatim** against the live source lines L7–L12 (compared
programmatically, not from memory):

    VERBATIM evopet-pointer-fix   line 7   RUNNING
    VERBATIM input-sovereignty    line 8   RUNNING
    VERBATIM verify-methodology   line 9   RUNNING
    VERBATIM kit-pointer          line 10  RUNNING
    VERBATIM kit-issues           line 11  RUNNING
    VERBATIM idle-watchdog-review line 12  RUNNING

Keyless rows carry `unnamed:<SECTION>:<ordinal>` keys (e.g. `unnamed:RUNNING:1`); the 2 UNRECOGNIZED
rows carry keys `unnamed:UNRECOGNIZED:1` / `:2` (live source lines 31–32, the PAUSED bullets).
Note the envelope re-read the ledger live: an earlier read this session showed `run_id = 5aef28f8…`
(sync #2) and the final read shows `0d96e373…` (sync #3) — no restart needed.

## 7. Config change (done pre-interrupt; one line, re-read this session)

    build/config.yaml line 225:  task_home_enabled: true
    sha256 before (baseline.json): 3d0fd9ff29b12858ababa6bd026f883211a46d5e68c0bfff492ecf7252752728
    sha256 after  (this session):  1d166f82cb22de7f2b351d44e93d9402435149a9a7850203720a094efa369f7c
    → exactly one line flipped false → true; config.yaml was NOT edited again in this pass.

## 8. Guarded-table hashes — baseline vs post-state

`table_hashes(declared_only=True)`, baseline.json → this session's read-back:

| table | baseline | post-state | verdict |
|---|---|---|---|
| project_session | `9fb88b8b367fcbe1…` | `9fb88b8b367fcbe1…` | SAME |
| evidence | `c92100ac2d204a6b…` | `c92100ac2d204a6b…` | SAME |
| next_action | `e3b0c44298fc1c14…` | `e3b0c44298fc1c14…` | SAME |
| scan_run | `80c3fe79c3295ef1…` | `80c3fe79c3295ef1…` | SAME |
| declared_field | `5b523b23ce30f9b2…` | `5fc15da176f204b6…` | CHANGED — by design: +96 task_home_* declared rows (32 × {key, proc, binding}) |
| review_event | `77cf7ec6c0c4d6cc…` | `cc02e80bac2ff5d0…` | CHANGED — by design: +32 audited review_events (2 → 34) |
| project | `ff8ef193f3874e18…` | `fa2bbb9dd3e214a7…` | CHANGED — see W-2 below (documented deviation, `declared_rev + 1`) |

Registry file: baseline size 1302528 → now 4075520 bytes; sha256
`812008f200ce75e63903dabfa0dc99f81dbb362515be9b5804334afafc9978df` (stable across sync #3 and all 5
proof GETs). Schema/SCHEMA_VERSION untouched (3); no DDL run.

## 9. Warnings, observations, open decisions

- **W-1 (needs a Proteus decision) — PAUSED section is source drift beyond the locked 4-section
  contract.** The live source has 6 headings; the contract locks 4. The sync handles it as designed:
  exit **5 = PARSE_DEGRADED**, `degraded: ["heading_unrecognized"]`, and the 2 PAUSED bullets land in
  the UNRECOGNIZED section (`section_counts.UNRECOGNIZED = 2`) rather than being dropped. This is why
  3 consecutive passes exit 5 — it is the LOCKED behavior, not a failure. Orda's file is read-only to
  this lane (TH-L1): the decision (accept PAUSED as a 5th section, or have Orda move it inside the
  contract) belongs to Proteus/the owner.
- **W-2 (documented deviation) — the `project` table hash changed.** Pre-flight finding, recorded in
  `STATE.md`: `registry.set_declared` (registry.py:612-613) executes
  `UPDATE project SET declared_rev = declared_rev + 1`. The brief's mandated bind route
  (`POST …/review {action: bind_task_home}`) calls it, so 32 rows' `declared_rev` incremented.
  Raw-SQL bypass of the audited route is forbidden and registry.py is outside the write boundary, so
  this is correct-route behavior, recorded rather than "fixed". `project_session`, `evidence`,
  `next_action`, `scan_run` are untouched.
- **W-3 (informational) — `compound_bullet`.** 17 of 49 items warn `compound_bullet` (bullets holding
  more than one clause). Informational only; it is not in `degraded`, does not affect exit code, and
  does not block anything.
- **W-4 (wire-shape observation, no action permitted).** The brief expected the 2 UNRECOGNIZED rows to
  carry the `heading_unrecognized` warning on the wire. Measured wire shape: those rows carry
  `warnings: []` and `section_label: null`, and the top-level `task_home.warnings` is
  `["compound_bullet"]` only; `heading_unrecognized` appears in the sync report's
  `warnings`/`degraded` (and is why exit is 5), not in the served envelope. The wire still surfaces the
  rows correctly as section `UNRECOGNIZED`, with keys `unnamed:UNRECOGNIZED:1|2`. No code edit is in
  scope for this lane (`continuum/*.py` and `dashboard/*.py` are FORBIDDEN) — flagged for Leo/Proteus so
  the contract and the wire agree.
- **W-5 (data observation, not "fixed") — 3 duplicate name pairs.** `gc1` ×2, `team6-kit` ×2,
  `team6-kit-pr` ×2 → 6 projects share 3 keys, faithful to the export's own `proposed_key` (brief step
  4d). The sync would only report `ambiguous_binding` if TASK-HOME ever contained those keys; it never
  auto-resolves, and no ledger item currently references them.
- **W-6 (count reconciliation) — brief prose vs file.** The brief's prose said "50 bullets, AWAITING 14";
  the live file has 49 bullets and AWAITING 13. The file sha equalled the dispatch baseline, so the
  prose count was stale, not a drift; all counts follow the file at sync time (49).

## 10. Exit codes (all passes)

| action | exit | meaning |
|---|---|---|
| sync #1 `2b472ecd…` | 5 | PARSE_DEGRADED — EXPECTED (PAUSED heading outside the 4-section contract); writes 0 |
| sync #2 `5aef28f8…` | 5 | same; writes 0; hashes before==after |
| sync #3 `0d96e373…` | 5 | same; writes 0; hashes before==after |
| export #1 `58b4e129…` | 0 | byte-stable body; dashboard_only 32, confirm_then_close 0, conflicts 0 |
| export #2 `b229cc4c…` | 0 | byte-identical body to #1 |
| 32 binds (recovered) | 200 ×32 | `ok:true` ×32, one audit_id each, `apply_binds.py` not re-run |
| `apply_binds.py` (this pass) | not run | deliberately — re-running would duplicate audit events |
| 5 × GET (proof) | 200 ×5 | declared_field hash byte-identical ×5; zero writes |

## 11. Final invariants (step 9)

- **TASK-HOME.md sha256, final: `f9e49814bfa0ed4eaf814fc108e3d2d8d5120891f8a1ad612392ca8abad2df1c`**
  — identical to the dispatch baseline; 5676 bytes; mtime 1789679108; 63 lines / 49 bullets.
  Read-only in both directions throughout (TH-L1 held); no Orda drift occurred during this pass, so no
  drift re-run and no second sha to report.
- Frozen fixture `build/fixtures/task_home/task-home.md`: `5da1f01d21a9a7d94f032756ffb0815c6ab98d40e5ad2a4b37a167443ba9b14e` — UNCHANGED.
- Installed tree `/Users/kethuda/.hermes/plugins/continuum/**`: `find -newermt "2026-09-17 21:00:00"`
  → **0 files** modified.
- Servers untouched: `:8765` PID 82257, `:8766` PID 99100, `:8767` PID 29428 (all alive, same PIDs).
  `:18771` PID 11789 = the lane's standalone serve, left running. Nothing listening on `:8771`
  (`lsof -iTCP:8771 -sTCP:LISTEN` empty) — no extra daemon.
- Source build is not a git repo (`git status` → "not a git repository"), so no commits were possible
  or made.
- Writes landed only in: `build/config.yaml` (the one flip, pre-interrupt), `build/data/registry.db`
  task_home_* declared rows + review_events via the audited review route, `build/data/task_home_sync.*`,
  `build/review/out/task-home-export.{md,json,meta.json}`, and this lane's logs/receipt.
- No new binds, no unbinds, no closes, no TASK-HOME writes, no code edits, no installs, no kills.

## 12. Files written (this pass)

    launch-logs/sync3.json, sync3.err
    launch-logs/export1.json, export1.err, export2.json, export2.err,
                export1-body.md, export1-body.json, export1-meta.json
    launch-logs/wire_check.py, wire_env_check.json
    launch-logs/zero_write_proof.py, zero-write-proof.json, zero-write-runs.json
    launch-logs/receipt_evidence.py, receipt-evidence.json
    mozi-apply-receipt.md   (this file)
    STATE.md                (updated)

STABLE
