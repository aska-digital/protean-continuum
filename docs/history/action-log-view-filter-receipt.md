# mozi-view-receipt — action-log external-only default view (+ bounded-recovery completion)

owner: mozi (implementation) | dispatched: Proteus brief mozi-view-brief.md + mozi-view-recovery.md
(bounded restart) | completed: 2026-09-17T23:12Z | supersedes nothing; AL-L14 default-content
clause superseded by Orda directive 2026-09-17 only.

## 1. What changed (file:line)

Recovery note: sections 1a-1d were written by the SIGINT'd run and verified in place by this
run; the NEW work this run is section 1e (test file), the test/restart/read-back/probe sequence,
and this receipt + journal. Nothing already on disk was redone.

a) build/continuum/action_log.py
   - L66-79: `SCOPES=("external","all")`, `DEFAULT_SCOPE="external"`, `EXTERNAL_KINDS`,
     `INTERNAL_KINDS`, `INTERNAL_PATH_ROOT` constants (docstring cites the Orda directive).
   - L978-985: `_target_is_public(target)` — URL target or path outside `~/.hermes/`.
   - L988-1005: `is_external_action(row)` — the ONE named audit helper (clause 4), fail-safe
     for unknown/future kinds.
   - L1046-1055 + L1092: `parse_query` — additive `scope=`; absent/empty -> external; any other
     value -> ValueError (existing 400 path); scope in returned query dict.
   - L1107-1110: `_matches` — external filtering; missing scope key fail-safes to the default.
   - L1130-1136 + L1153-1157: `view()` — additive `counts.scope/external_total/internal_total`
     computed ledger-wide over ACTIVE rows; existing counts keys untouched in name/type/position.
b) build/continuum/service.py
   - L87-88: `scope` added to `ACTION_LOG_ONLY_KEYS` (action-log-only filter; board views
     reject it with the existing ValueError). Query pass-through already flows via
     `_parse_action_log_query` (L1247) -> `action_log.parse_query`.
c) build/dashboard/plugin_api.py
   - L76: `scope: Optional[str] = None` parameter; L90: pass-through into `board(**provided)`.
d) build/dashboard/static/app.js
   - L145 `AL_SCOPES`; L147 default query `scope:'external'`; L218-220 URL read (absent/invalid
     -> external); L293 always explicit `scope=` on the wire; L2332-2347 the ONE toggle control
     (patches only scope + page, preserving other filters); L2442-2451 counts-bar renders the
     server's `scope external/all` + `external <n>` fields as returned (AL-L15).
   - styles.css NOT touched by this run (toggle reuses existing c-btn/c-al-filters classes;
     verified sufficient).
e) build/tests/test_action_log_scope.py — NEW, 19 tests (this run), covering the brief's
   (a)-(f): default==external (a), scope=all==previous default incl. paging/status browse (b),
   direct-edit public/internal boundary + unknown-kind fail-safe (c: 6 helper tests + query
   vocabulary + constructed 11-row ledger partition + fixture internal direct-edit), invalid
   scope ValueError + HTTP 400 incl. case-sensitivity and filter-composition combos (d),
   archive round-trip under BOTH scopes at service level (external row, internal row) and over
   the real HTTP POST routes (e), counts additive-key reconciliation incl. external_total
   tracking the archive lifecycle (f). Fixture env copies the archive lane's HTTP-fixture
   pattern (frozen sources, lazy Service, TestClient over standalone.create_app).

## 2. Filter ruling as implemented

scope=external (the DEFAULT when absent): kind ∈ {post, merge, retraction} — all rows; plus
direct-edit rows ONLY when target starts http(s)://, OR target path is outside
/Users/kethuda/.hermes/, OR evidence_type == 'post-url'. dispatch/close/internal direct-edits
excluded (kind check precedes evidence check: a close row with URL evidence stays internal,
clause 3 absolute). Unknown future kinds fail-safe to internal unless their evidence says
public (clause 4). scope=all = previous default exactly (all active rows, archived via
status=). Ordering stays timestamp desc. Archive behaviour identical under both scopes; the
archived browse itself respects scope (internal archived rows never surface under external).
counts extended additively: total/active/archived/by_kind/by_operator unchanged, plus
scope/external_total/internal_total with external_total + internal_total == active.

## 3. Test output (verbatim tails)

Targeted trio (scope + sync + archive), venv python from build dir:
    ...................................................                      [100%]
    51 passed in 1.47s
New file alone:
    19 passed in 0.94s
FULL suite (same invocation as previous lane: python -m pytest tests -q):
    FAILED tests/test_task_home_sync.py::test_th_a1_parse_completeness_matches_section_1_table
    FAILED tests/test_task_home_sync.py::test_th_a2_literal_keys_and_procs_are_byte_identical
    2 failed, 428 passed in 34.82s
Both failures are the task-home lane's OWN frozen-guard drift (12th keyed RUNNING row
'Overnight' in fixtures/task_home vs RUNNING_KEYS=11): they import only continuum.task_home +
task-home fixtures, zero action-log surface. That lane is LIVE mid-edit:
continuum/task_home.py mtime 2026-09-17T23:02:33Z (4 min before the full run), its test files
mtime 18:56/18:57 EDT — AFTER the 22:55Z full-401 green. Not fixable by this lane without
touching another lane's in-flight files (forbidden; see deviations 1).

## 4. Live read-back — before vs after restart (own standalone, port 18772)

Before (old code, PID 96786, started 18:01:30):
    GET /?view=action_log                                   -> 200
    GET /api/.../projects?view=action_log                    -> 200  counts keys
        [active, archived, by_kind, by_operator, total]  total=736 active=736 archived=0
        first row kind=dispatch (the old full-log default; NO scope/external_total keys)
    ...&scope=all -> 200 (ignored, 736 rows)   ...&scope=bogus -> 200 (ignored, 736 rows)
Restart: `kill 96786` only; relaunch `/Users/kethuda/.hermes/hermes-agent/venv/bin/python -m
dashboard.standalone --port 18772`, cwd build, log /tmp/actionlog-view-logs/standalone.log.
No other process touched (verified: :18771/:8767/:8766 standalones still up, untouched).
After (new code, PID 89325):
    GET /?view=action_log -> 200
    API default (scope absent)  -> 200, counts: total 736 active 736 archived 0
        scope=external external_total=223 internal_total=513  (223+513==736 reconciles)
        paged full default view: 223 rows = post 45 + merge 98 + retraction 77 + 3 qualifying
        direct-edit; ZERO dispatch, ZERO close; timestamps non-increasing; every row carries a
        rendered evidence node. Matches Proteus's smoke anchor (223/513) exactly.
    API scope=all -> 200, 736 active rows newest-first, strict superset of the default set
    API scope=bogus|External|bogus+kind -> 400, 400, 400
    Archive round-trip via API under BOTH scopes: external(post) row and internal(dispatch)
    row — archive -> absent from active views in both scopes -> double-archive noop (same
    audit_id, no extra event) -> archived browse shows it under scope=all and correctly under
    external scope only for the external row -> unarchive -> both scopes restored 223/736.
    Board envelopes: bare /projects and view=today JSON byte-identical pre/post restart
    (volatile clock fields data_as_of/server_time/age_seconds scrubbed; captured to
    /tmp/actionlog-view-logs/before_*.json and re-fetched after).
    UI sanity (toggle was NOT visually verified before this restart): driven in real headless
    Chrome over CDP on http://127.0.0.1:18772/?view=action_log — toggle renders ("full log",
    aria-pressed=true, title correct), ONE click flips to scope=all in URL + dispatch/close
    rows appear, second click flips back; kind=post filter PRESERVED across the toggle; stats
    bar shows "scope external · external 223"; 0 console/page errors. Screenshots:
    /tmp/actionlog-view-logs/view-default-external.png, view-toggled-all.png. (Interactive
    browser tool refuses private addresses; headless Chrome CDP used instead.)

## 5. Deviations

1. FULL-suite all-green NOT attainable at run time: 2 failing tests belong to the live
   task-home lane (continuum/task_home.py edited 23:02Z by that lane; frozen TH-A1/A2
   literals not yet re-synced by its owner). Zero relation to this lane's surface (evidence in
   §3). Declared, not fixed — another lane's in-flight files are forbidden here. Every test
   touching action-log is green (51 targeted + the remaining 428).
2. During the FIRST live round-trip pass my read-back script asserted archived-browse
   visibility wrongly for INTERNAL rows (ruling clause 3 hides them from the external default
   — the shipped code was right, the throwaway script was wrong). The script aborted between
   archive and unarchive, leaving row al-inf-20260917-2205-kit-issues-leo archived for ~2
   minutes; it was unarchived via the API and the ledger verified byte-restored (md5 back to
   the locked value). Events sidecar is now 20 lines (append-only growth from the QA
   round-trips is AL-I3 by design; action_log.json itself is byte-unchanged).
3. QA screenshots/CDP used the headless system Chrome (browser tool blocks private URLs) —
   read-back still executed against the real served page on :18772.
4. styles.css untouched (recovery file said "adjust only if needed" — it wasn't).

## 6. Data-file md5

Before tests:        d12d384d9e873addac3abdd8471f7206
Before restart:     d12d384d9e873addac3abdd8471f7206
After restart + full live read-back incl. two API archive round-trips:
                    d12d384d9e873addac3abdd8471f7206   (UNCHANGED; byte-compared to a
                    pre-roundtrip copy: identical)

STABLE
