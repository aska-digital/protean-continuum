# Task Home ↔ Continuum sync — implementation receipt

STATUS: IMPLEMENTED — all applicable TH-A/TH-I/TH-R/TH-C ids executed green; ready for Shaka
TH-G1..G3. This is an implementation receipt, not a QA verdict, not an install/deploy claim.

    owner:    mozi (implementation, stage 4) · task: continuum-sync · matrix_read_at_dispatch: yes
    contract: profiles/proteus/cache/delegation/continuum-sync/leo-architecture.md (STATUS: STABLE;
              §2/§3/§4/§5/§6/§7/§8 read on disk and consumed as locked, not re-derived)
    tree:     SOURCE BUILD ONLY — /Users/kethuda/.hermes/profiles/proteus/cache/session-project-indexer/build/
    provider/model (read from /Users/kethuda/.hermes/profiles/mozi/config.yaml this run):
              opencode-go · deepseek-v4.1-flash
    date:     2026-09-17 (original session 20260917_110435_7f4466 + recovery continuation; 15:54Z)

## 0. Pre-flight (measured)

- TASK-HOME.md sha256 (live, read-only): 5da1f01d21a9a7d94f032756ffb0815c6ab98d40e5ad2a4b37a167443ba9b14e
  (46 lines) — equal to the architecture's anchor; re-measured after all work: UNCHANGED.
- frozen fixture copy fixtures/task_home/task-home.md: same sha256 (cp -p).
- baseline full suite BEFORE any edit: `1 failed, 316 passed`. The one failure is
  tests/test_scanner_readonly::test_every_profile_opens_readonly_and_counts_reconcile
  (live-corpus drift; pre-existing, untouched by this milestone, still failing — reported,
  not claimed green).

## 1. Files

Written (all inside §7 MAY WRITE):
    continuum/task_home.py                NEW — parser, keyer, mapper, sync engine, ledger,
                                          flock-guarded lock, export renderer, projection
                                          reader; pure functions, injected clock/paths,
                                          no HTTP, no network, no thread
    continuum/cli.py                      one `task-home` command: sync | show | export
                                          (existing entrypoint; exit 0/3/4/5 per §5)
    continuum/service.py                  home= parse/validate/echo/match; counts.task_home;
                                          playground-card task_home + home + state_source +
                                          placement_shadowed with the §3 read-time lane
                                          override applied LAST; task_home envelope;
                                          bind_task_home / unbind_task_home as new review
                                          actions on the EXISTING route (audit + undo)
    continuum/config.py + config.yaml     task_home_enabled:false (§7 literal shipped value),
                                          task_home_source_path, task_home_ledger_path,
                                          task_home_export_dir, task_home_stale_after_seconds:900;
                                          fail-loud in the existing _validate_bundle style
                                          (bool/path/int checks verified to raise)
    dashboard/plugin_api.py               home= pass-through on the existing /projects route;
                                          no new route (router still exactly 12 paths)
    dashboard/static/app.js               HOME_GROUPS/HOME_LABELS (§3 locked labels), home=
                                          repeatable filter + URL persistence + clearQuery +
                                          filter echo; renderTaskHome renders ONLY server
                                          fields (TH-L13); never-synced shows the exact CLI
                                          command from task_home.source_path (§6)
    dashboard/static/styles.css           .c-task-home block (existing token language only)
    tests/test_task_home_sync.py          NEW — 33 executed tests
    tests/test_task_home_projection.py    NEW — 19 executed tests
    fixtures/task_home/task-home.md       NEW frozen copy (sha above)
    data/task_home_sync.json (+ .lock)    generated ledger/lock (local)
    review/out/task-home-export.{md,json,meta.json}  generated export
    review/task-home-sync-receipt.md      this file

NOT written: TASK-HOME.md; the installed tree /Users/kethuda/.hermes/plugins/continuum/**
(verified: `find … -type f -newermt "2026-09-17 11:00"` -> 0 files); any Hermes state.db;
registry DDL / SCHEMA_VERSION (still 3); continuum/{scanner,cluster,evidence,classify,model}.py;
desktop/plugin.js; Frida/Orda/Shaka artifacts; nothing public.

## 2. Commands + real outputs (frozen fixture as source)

Parse (executed): items=34; sections {'RUNNING': 11, 'AWAITING_OWNER': 11, 'OPEN_FOLLOW_UP': 6,
'CLOSED': 6}; keyed=11; receipts=7; compound=16; line anchors 7–17 / 20–30 / 33–38 / 41–46 —
all equal §1's table. Round-trip: render(parse(source)) reproduces the fixture byte-for-byte;
parse(render(parse(x))) == parse(x) on items/sections/lanes/line numbers.

Real CLI (from build root):
    python3 -m continuum.cli task-home sync --source fixtures/task_home/task-home.md
      -> run b1fdc49c6bd149e29349187994fcfc51: items=34, added=0 changed=0 writes=0, exit 0
         (no real-registry project matches a RUNNING slug byte-exactly and no proc_ token
          appears in any member session title/next_action -> all 34 keys are source_only;
          a name match was exercised for real on the scratch registry below)
    (same command again) -> added=0 changed=0 unchanged=0 writes=0          (zero-write idempotency)
    python3 -m continuum.cli task-home show -> ledger present=True, source_sha256=5da1f01d…,
      34 items, 0 conflicts
    python3 -m continuum.cli task-home export --source fixtures/task_home/task-home.md
      -> review/out/task-home-export.{md,json,meta.json}
         counts={"bound":0,"confirm_then_close":7,"conflicts":0,"dashboard_only":32,"items":34}
    two consecutive exports -> .md and .json sha256 identical (EXPORT byte-stable; timestamps
      live only in the .meta.json sidecar)

Bound-case CLI proof on a scratch registry (/tmp/th_demo_registry.db, test-only artifact,
project 'continuum-sync' + placement 'blocked'):
    sync #1: added=1 changed=1 writes=13 (all 13 reserved fields, source='task-home-sync')
             + conflict [placement_conflict] continuum-sync: task_home=ongoing vs
               dashboard=blocked (winner task_home)
    sync #2: added=0 changed=0 unchanged=1 writes=0; declared_field hash byte-identical

Test runs (real exit codes):
    python3 -m pytest tests/test_task_home_sync.py tests/test_task_home_projection.py -q
      -> 52 passed
    python3 -m pytest -q
      -> 368 passed, 1 failed (the pre-existing baseline failure of §0; 316+52=368)
    node --check app.js (as .mjs) -> OK (ESM valid)

## 3. Acceptance log (every row is an executed test unless labelled structural)

    TH-A1  34 items, 11/11/6/6, anchors, no drop/split                    PASS test_th_a1_*
    TH-A2  11 keys + procs byte-verbatim, no fold/dedupe                  PASS test_th_a2_*
    TH-A3  reorder re-binds by content_sha1; edit -> keyless_binding_shift
           with binding kept, never silently re-pointed                   PASS test_th_a3_* (x2)
    TH-A4  §4 steps 1 (exact name), 2 (proc in member title AND declared
           next_action), 5 (source_only); steps 3–4 exercised by the
           seeded-ledger TH-A3 pair; zero fuzzy (near-miss slug unbound)  PASS test_th_a4_*
    TH-A5  K1->K4 and K4->K1 <=1; violation -> ambiguous_binding,
           reported, never auto-resolved                                  PASS test_th_a5_*
    TH-A6  heading = leading words; (date)/parenthetical change orphans
           nothing; unknown heading warns, bullets kept, exit 5           PASS test_th_a6_*
    TH-A7  §3 group + column for all four sections; lane= set == home= set PASS test_th_a7_*
    TH-A8  board/inbox/attention/staleness items[] + counts.total identical PASS test_th_a8_*
           before/after sync; source-only keys never enter items[]/total  PASS  (2 tests)
    TH-A9  conflicting human placement: rendered lane is TASK-HOME's; PASS test_th_a9_*
           placement declared row byte-unchanged (row-dump before==after)  (2 tests)
    TH-A10 counts.task_home.conflicts == published conflict entries; PASS test_th_a10_*
           every entry names both values + winner 'task_home'             PASS
    TH-A11 7 receipts verbatim text+status; section stays RUNNING; PASS test_th_a11_*
           receipt_section_mismatch present on all 7                      PASS
    TH-A12 guarded-table hashes unchanged; only task_home_* declared PASS test_th_a12_* (2)
           rows differ; source='task-home-sync'; placement/urgent/… untouched
    TH-A13 SCHEMA_VERSION == 3; sync never migrates; full-suite total PASS test_th_a13_* (2)
           consistent (368+1 == 316 baseline + 52 new + 1 baseline fail)
    TH-A14 bare /projects envelope key set + nested _card() KB-N1 set PASS test_th_a14_* (4)
           unchanged; playground additions exactly §6 key sets; 12 routes
    TH-I1  second pass changed=0 + declared_field hash identical; dry-run PASS test_th_i1_* (2)
           writes nothing at all
    TH-I2  edit moves exactly task_home_line + task_home_content_sha1; PASS test_th_i2_*
           row count unchanged, no new binding
    TH-I3  two exports at different injected clocks byte-identical; PASS test_th_i3_*
           no timestamp/run-id in either body; sidecar carries them
    TH-I4  held flock -> SyncLocked -> exit 3, ledger hash unchanged; PASS test_th_i4_* (2)
           missing source -> exit 4
    TH-R1  parse(render(parse(fixture))) == parse(fixture) on items, PASS test_th_r1_*
           sections, lanes (34 items) — and renderer reproduces source bytes
    TH-R2  every project exactly one bucket; bound+dashboard_only == PASS test_th_r2_*
           total projects (counts asserted against ledger + registry)
    TH-R3  proposed lines flagged separately (proposed:true, line_no PASS test_th_r3_*
           null); every existing line verbatim == source line at its line_no
    TH-R4  after sync+export, fixture sha unchanged; live TASK-HOME sha PASS test_th_r4_* (2)
           unchanged; structural: source opened 'rb' only, lock opened a+
           but never written through (labelled structural supplement)
    TH-C1  receipt-claiming RUNNING bullet stays RUNNING + ongoing and PASS test_th_c1_*
           appears in confirm_then_close (all 7, ordered by line_no)
    TH-C2  shadowed local value present in conflicts[] (both values + PASS test_th_a10_* +
           winner); rendered status is TASK-HOME's; flag itself intact  test_th_a9_*
    TH-C3  confidence/tier/session-count appear ONLY in the export's PASS test_th_c3_* (2)
           derived{} block; proposed lines carry no derived claim;
           lifecycle/attention fields not rewritten by the import

Extra adversarial coverage (complements Shaka TH-G2): malformed bullet, wrapped line,
duplicate slug, missing proc, unknown heading, empty section, CRLF — each yields a defined
warning, none crashes or silently drops (test_adversarial_parse_*, test_crlf_*,
test_duplicate_slug_*). Bind/unbind route tests incl. payload validation, undo reversibility,
human-binding preservation across passes (4 tests).

## 4. Pre/post hashes (real build registry data/registry.db)

Pre captured before ANY task-home write; post after the final canonical CLI runs. All EQUAL:

    table            pre == post   hash (sha256, first 16 … last 8)
    project          True          ff8ef193f3874e18 … feaaf8f9
    project_session  True          9fb88b8b367fcbe1 … 5a2d1d99
    evidence         True          c92100ac2d204a6b … 099c1d7c
    next_action      True          e3b0c44298fc1c14 … 7852b855 (table empty)
    review_event     True          77cf7ec6c0c4d6cc … 67ada910
    scan_run         True          80c3fe79c3295ef1 … 3f7f485b
    declared_field   True          5b523b23ce30f9b2 … 693415b7   (task_home_* rows now: 0)

Sources: TASK-HOME.md 5da1f01d…b14e before == after; fixture identical; installed tree 0 files
modified since session start. state.db usage: NONE — the sync/export/projection paths never
open any Hermes profile state.db (grep 'state.db' continuum/task_home.py -> 0 hits; and
test_th_a12_no_source_database_is_touched verifies fixture source-db stat unchanged).
Ledger: build/data/task_home_sync.json — schema 1, source_sha256 5da1f01d…, 34 items,
0 conflicts, runs appended (TH-L5/§5 shape).

## 5. Deviations, limits, OPENs (recorded, not silently absorbed)

1. Reserved declared rows are written by task_home.py with raw SQL inside
   registry.transaction() instead of Registry.set_declared(), because (a) set_declared
   hardcodes source='declared' while TH-L5 requires source='task-home-sync', (b) it bumps
   project.declared_rev while TH-A12 requires the project table byte-unchanged, and (c)
   registry.py is outside §7. Rationale documented at the call site.
2. Cleanup performed this session: a pre-fix CLI pass (shared default ledger + swapped
   --registry during the bound-case demo) re-created a stale ledger binding as 13 orphan
   task_home_* declared rows for a nonexistent project in the REAL registry. They were
   removed with a scoped DELETE touching only project_id='th-demo-1' AND field LIKE
   'task_home_%' (post-delete declared_field hash == pre-work hash, §4). The carry-over
   branch now requires the pid to exist in the current registry (regression test
   test_ledger_binding_to_a_missing_project_is_never_recreated). No human row was touched.
3. §7 lists `task_home_enabled:false` as the shipped value, so the config default ships FALSE;
   the panel surfaces only when the operator flips the switch. The tests exercise the ON state
   explicitly; never-synced and disabled states both render (§6).
4. Attention-rail participation of AWAITING_OWNER remains OPEN-2 (v1 = NONE, §3 rule 3).
   Panel final copy/labels/empty states remain OPEN-1 (Frida); the panel renders §3's locked
   group labels and server values only.
5. Line granularity kept; compound bullets ('+' marker, 16/34 exactly per §1) warn
   `compound_bullet` and do NOT split (OPEN-9). Wrapped/continuation lines warn
   `wrapped_line` attached to the previous bullet (OPEN-5); a >300-char bullet additionally
   warns `long_line` (constant LINE_LENGTH_WARN_CHARS).
6. Wire conflicts[] on the envelope and on cards are the CURRENT read-time lane/placement
   conflicts (both values + winner, §5 shape). Sync-time ledger conflict rows (placement +
   ambiguous_binding) are published by `task-home show` and the ledger file.
   counts.task_home.conflicts counts the read-time entries.
7. PARSE_DEGRADED exit 5 covers degraded resolve-time warnings (e.g. keyless_binding_shift,
   ambiguous_binding) as well as parse warnings — same degraded class, same §5 exit path;
   §5 names parse warnings explicitly, this is the minimal consistent extension.
8. CLI form: `task-home` is a command choice with subcommand positional
   (`continuum.cli task-home sync|show|export`), matching the existing single-command argparse
   design; --source/--dry-run/--out/--json per §5.
9. data/task_home_sync.{json,lock} are not added to .gitignore (§7 lists the files as "local";
   .gitignore itself is not in MAY WRITE; the tree is not a git repo anyway; review/out/ is
   already ignored).
10. The canonical CLI sync against the real registry bound 0/34 keys (exact-match-only
    matching per §4: no real project is named after a RUNNING slug yet). First real bindings
    happen via the export proposal round-trip (human applies lines / names projects) or via
    bind_task_home — this is the designed TH-L1/TH-L14 direction.
11. Install/enable/deploy NOT done (forbidden + not claimed). Desktop plugin untouched
    (TH-L16). No QA verdict issued here; Shaka re-measures from this tree.
