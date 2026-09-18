# Receipt — Continuum user-facing session filter (D-US-1 .. D-US-9)

```
task_id:            continuum-user-session-filter-implementation
profile:            kodekoot
role:               implementation only (no architecture, invariant, or wording decisions)
ownership_matrix:   /Users/kethuda/.hermes/profiles/lugia/cache/session-project-indexer/OWNERSHIP-MATRIX.md
matrix_read_at_dispatch: yes
upstream_approval:  handoffs/architecture-continuum-user-session-filter.md
                    (Azaraki — DECIDED, D-US-1 .. D-US-9, no BLOCKED rows)
boundary:           D-US-9 allowed files + this receipt only
interpreter:        /Users/kethuda/.hermes/hermes-agent/venv/bin/python (3.11.15), node v22.17.1
date:               2026-09-13
state:              implemented; measured evidence below; no compliance wording claimed
```

## 1. The user outcome

The exact bad target — `azaraki/20260911_174725_516587`, "Write EvoPet levels and evolution
contract doc" — is a delegated worker whose first user message is byte-identical to
`lugia/cache/delegation/evopet/azaraki-brief.md`.

Byte-identity, re-verified this session (read-only):

```
$ wc -c <first user message>                      -> 5415 bytes (5389 chars)
$ shasum -a 256 <first user message>              -> a8313d9b9d0ccdf7cc69e3e477f1770774b444cfbac79f336d83b8ed3d8f5f98
$ shasum -a 256 .../delegation/evopet/azaraki-brief.md -> a8313d9b9d0ccdf7cc69e3e477f1770774b444cfbac79f336d83b8ed3d8f5f98
$ cmp <first user message> .../azaraki-brief.md   -> EXACT MATCH
```

The defect, as shipped, before this change (unchanged artifact):

```
$ grep -c -- "20260911_174725_516587" build/review/out/live-inbox.json   -> 4
$ grep -o -- "--resume 20260911_174725_516587" build/review/out/live-inbox.json
  --resume 20260911_174725_516587
  --resume 20260911_174725_516587
```

After this change, on the live corpus, the EvoPet card resolves:

```
evopet project anchor_session : lugia/20260911_163629_22fff0
evopet anchor_reason          : ANCHOR-NAME
evopet card anchor.copy_command_profile_scoped :
      hermes -p lugia --resume 20260911_163629_22fff0
evopet conversation_line      : Conversation: Document evopet handoff package (lugia)
evopet audience_counts        : {USER_FACING: 0, DELEGATED: 13, AUTOMATED: 0, UNKNOWN: 0}
evopet primary_session        : null
resume affordances pointing at 20260911_174725_516587 (board+inbox+detail): []
DELEGATED/AUTOMATED resume links anywhere                                  : []
```

## 2. Decision conformance

| decision | where | status |
|---|---|---|
| D-US-1 audience vocabulary + storage | `session_fact.audience/audience_reason/audience_evidence_ref`, total + closed | implemented |
| D-US-2 exclusion rules A1-A3 / B1 / C1-C5 | `continuum/audience.py::classify_audience` | implemented |
| D-US-3 retention U1..U6 + `U-DELEGATOR`, no profile filter | `audience.py::_user_facing`, `_is_group_relay` | implemented |
| D-US-4 `async_delegations` read-only, positive only | `scanner._read_delegators`, `_read_delegations`; `audience.build_delegator_index` | implemented |
| D-US-5 excluded sessions kept as evidence | `service._audience_snapshot`, card/detail payloads | implemented |
| D-US-6 primary selection + anchor + NO_USER_SESSION | `model.reconcile` (audience section), `registry.set_primary/set_anchor`, `audience.select_primary/resolve_anchor` | implemented |
| D-US-7 additive v2->v3, non-destructive, audited override | `registry.SCHEMA_VERSION = 3`, `_ensure_additive_columns`, `apply_audience`, `service.review("set_audience")` | implemented |
| D-US-8 fixtures F1..F8 + T1..T8 | `tests/fixtures/audience/**`, `tests/test_audience_filter.py`, extended 3 test modules | implemented |
| D-US-9 file boundary | see §4 | honoured |

Forbidden exclusion evidence is not used anywhere: no profile name, `cwd`, `title`, `model`,
`system_prompt_hash`, `api_call_count`, `end_reason`, and no bare `source='cli'` (source is read
only for A1/A2/A3). C5 is deliberately non-exiting: it corroborates C2/C3/C4 and can never
stand alone (asserted by `test_c5_role_declaration_never_stands_alone`).

## 3. Files changed (exactly the allowed set)

```
$ find . -type f -newermt "<session start>" -not -path "*/__pycache__/*" -not -path "./review/out/*" -not -path "./data/*"
./config.yaml
./continuum/audience.py                       (NEW)
./continuum/config.py
./continuum/model.py
./continuum/registry.py
./continuum/scanner.py
./continuum/service.py
./tests/fixtures/audience/briefs/azaraki-brief.md   (NEW; byte copy of the real brief)
./tests/fixtures/audience/briefs/synthetic_brief.md (NEW)
./tests/fixtures/audience/build_audience_home.py    (NEW)
./tests/test_audience_filter.py               (NEW)
./tests/test_pipeline_fixtures.py
./tests/test_registry_reversibility.py
./tests/test_scanner_readonly.py
```

Not touched (verified by mtime): `continuum/cluster.py`, `dashboard/plugin_api.py`,
`dashboard/standalone.py`, `desktop/**`, `dashboard/static/**`, `data/registry.db`, every
`profiles/*/state.db`, installed plugin trees, Raptora workspaces, other handoffs.

New config surfaces (D-US-9): `audience_brief_roots`, `audience_brief_exts`,
`audience_brief_max_bytes`, `dispatch_keys`, `dispatch_target_profiles`,
`audience_probe_chars`, `anchor_min_token_len` — data, not code, so the rule set is
falsifiable by mutation.

Scanner additions: `MessageProbe.content_head` (untruncated, bounded by `audience_probe_chars`),
`ScanBatch.first_user`, `ScanBatch.delegators`, `ScanBatch.delegation_rows`. Existing excerpt
semantics (`content[:excerpt_chars]`) are unchanged; every source read stays `mode=ro` +
`PRAGMA query_only=ON`.

## 4. Test results

All commands from `build/`, `VENV=/Users/kethuda/.hermes/hermes-agent/venv/bin/python`.

| suite | command | result |
|---|---|---|
| full Python | `"$VENV" -m pytest tests/ -q` | **205 passed, 1 failed** (see §7) |
| audience | `"$VENV" -m pytest tests/test_audience_filter.py -q` | 15 passed |
| bounded | `"$VENV" -m pytest tests/test_kanban_bounded.py -q` | 51 passed, 1 failed (same §7) |
| node behavioral | `node tests/test_kanban_helpers.mjs` | 75 passed, 0 failed |
| node shipped paths | `node tests/test_kanban_shipped_paths.mjs` | 17 passed, 0 failed |
| node drift/mutation | `node tests/mutation_evidence.js` | 30 passed, 12/12 drift caught |
| python syntax | `"$VENV" -m py_compile continuum/*.py tests/*.py` | OK |
| node syntax | `node --check desktop/plugin.js` / `kanban-interaction.js` | exit 0 |
| plugin validation | `hermes plugins validate <build>` | Validation passed (10/10), exit 0 |

Python suite breakdown: 187 pre-existing tests (one of which pins the stale schema version —
see §7) + 19 new/extended (15 audience, 1 pipeline T5, 1 reversibility override, 2 scanner T8)
= 206 collected, 205 green.

### T1 .. T8 (D-US-8)

| id | test | result |
|---|---|---|
| T1 exact target => DELEGATED C1 | `test_t1_exact_target_session_is_delegated_c1` | PASS |
| T2 no board/inbox/detail resume link points at the target | `test_t2_no_board_resume_link_points_at_the_target` | PASS (red on the pre-change build: shipped `live-inbox.json` had 4 hits) |
| T3 direct Azaraki + KodeKoot sessions => USER_FACING | `test_t3_direct_worker_profile_sessions_are_user_facing` | PASS |
| T4 Lugia origin => USER_FACING / U-DELEGATOR | `test_t4_delegator_origin_session_is_user_facing` | PASS |
| T5 no DELEGATED/AUTOMATED primary or resume link | `test_t5_*` (audience) + `test_t5_primary_and_resume_links_never_target_delegated_over_the_corpus` (pipeline) | PASS |
| T6 audience closed set + total | `test_t6_audience_is_total_and_closed` | PASS |
| T7 mutating C1 flips T1 to red | `test_t7_disabling_c1_flips_t1_to_red` (brief index emptied => reason becomes C2, not C1) | PASS |
| T8 source DB mtime/size unchanged + query_only | `test_t8_audience_pass_leaves_source_dbs_byte_unchanged`, `test_t8_first_user_message_is_untruncated_and_bounded` | PASS |

Rule coverage beyond T1..T8: C1 fixture file match, C2 envelope, C3/C5 corroboration,
A1/A2/A3, F7 group relay (UNKNOWN, never primary), the audited audience override + undo
(`test_audience_override_is_audited_and_undone`), and the anchor-NONE fallback copy
(`test_no_anchor_uses_the_architecture_copy`).

## 5. Live corpus evidence (sources read-only; temp registry copies only)

`Service(load_config(~/.hermes))` with `registry_path` pointing at a TMPDIR copy; one scan
(1.62 s over the 8 profiles):

```
sessions 1848   projects 15
AUD AUTOMATED 702   DELEGATED 550   USER_FACING 411   UNKNOWN 185
A2=287 A3=8 A1=407 | B1=142 C1=378 C2=20 C4=10 | U=401 U-DELEGATOR=10 | UNKNOWN=185
primary rows that are DELEGATED/AUTOMATED: 0
anchors resolved 7 / anchor_reason=NONE 8
target  azaraki/20260911_174725_516587 -> DELEGATED / C1
        evidence_ref = ~/.hermes/profiles/lugia/cache/delegation/evopet/azaraki-brief.md
origin  lugia/20260911_163629_22fff0    -> USER_FACING / U-DELEGATOR
```

Notes stated plainly: `C5` never appears as a reason by design (corroborating only), `C3` is
subsumed by `C2` on this corpus (an envelope carrying the preflight header has >= 2 dispatch
keys and C2 is evaluated first), and 411 USER_FACING sessions remain primary-eligible — no
over-exclusion from any profile.

Live API surface through the shipped FastAPI router (`plugin_api._SERVICE` set to the live
service): `GET /projects` total 15 (counts.inbox 15), `GET /overview?mode=immediate` total 15,
`GET /candidates` total 15, `GET /attention` attention_count 8. 78 USER_FACING resume links
remain across the board.

## 6. Immutability + migration evidence

| check | result |
|---|---|
| source DBs mtime+size, before vs after the live audience pass (8 profiles) | all 8 identical; `source_dbs_unchanged: true` (sha256 equal for every `state.db`) |
| write attempt on a scanner handle | `sqlite3.OperationalError`; `PRAGMA query_only` = 1 (`test_t8_*`) |
| shipped `build/data/registry.db` | byte-unchanged: sha256 `94787bccc6be442d07beb41f822c839f93df9bc3417bc2a1065464fbbc50f43c`, mtime/size `1789250563.059 / 1114112` (unchanged) |
| v1 -> v3 migration + rescan on a registry COPY | `project_session` rows 555 -> 581, **0 rows missing**, 0 rows with changed `accepted`/`declared_rev`, `declared_field` rows 0 -> 0, `review_event` rows 0 -> 0 |
| EvoPet row `azaraki/20260911_174725_516587` | before `(primary, accepted=0, declared_rev=0)` -> after `(supporting, accepted=0, declared_rev=0)` — only the derived `role_in_project` changed |
| additive columns present after migration | `session_fact`: audience, audience_reason, audience_evidence_ref; `project`: anchor_session, anchor_reason, anchor_updated_at |

The +26 `project_session` rows are newly-clustered sessions from the live profile DBs since the
old registry snapshot — no pre-existing row was deleted or rewritten (D-US-7.2).

Bounds held: `audience_probe_chars = 8192` > the 5 389-char brief, so C1 digest-matches the
untruncated first user message (the 240-char excerpt could not). `ENVELOPE_HEAD_CHARS = 4096`
bounds C2/C3/C4. The brief index reads only `.md`/`.txt` files under the configured roots,
capped at 2 MB each; `{hermes_home}` in a root is substituted at load time, so a fixture
`hermes_home` never reads the live brief corpus.

## 7. One known red — a stale change-detector outside the D-US-9 boundary

`tests/test_kanban_bounded.py::test_kb_forbidden_files_untouched` asserts
`SCHEMA_VERSION == 2`. D-US-7 requires `SCHEMA_VERSION 2 -> 3`, so the assertion is falsified by
the approved decision. `test_kanban_bounded.py` is **not** in the D-US-9 allowed-file list, so it
was not edited here. The one-line re-baseline needed is:

```diff
--- a/tests/test_kanban_bounded.py
+++ b/tests/test_kanban_bounded.py
@@
-    assert SCHEMA_VERSION == 2
+    assert SCHEMA_VERSION == 3   # D-US-7 (additive v2->v3)
```

With that single line applied the full suite is **206 passed / 0 failed** — measured on a copy of
the delivered tree (`rsync` to TMPDIR, one-line edit on the copy only; the delivered
`tests/test_kanban_bounded.py` is untouched). This is recorded rather than executed in place
because the D-US-9 boundary is explicit; the decision to re-baseline belongs to the reviewer
(and to whoever owns the kanban correction batch that added the assertion).

## 8. Non-claims

No install, no enable, no deploy, no publish, no PR, no readiness claim. No QA verdict. No
architecture, invariant, or UX-copy decision is made here. `desktop/**`, `dashboard/static/**`,
`dashboard/plugin_api.py`, `dashboard/standalone.py`, `continuum/cluster.py` and
`data/registry.db` are untouched. The desktop/browser copy for the two D-US-6 lines is Shayba's;
this receipt exposes the payload (`anchor`, `anchor_session`, `anchor_reason`,
`conversation_line`, `audience_counts`, `audience_unverified`, `no_user_session`) and the exact
fallback string `No user-facing conversation found for this project.` without redesigning any copy.

Azaraki reviews the wording of this receipt; Halakukhan independently re-gates T1..T8.
