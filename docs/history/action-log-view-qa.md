QA GATE — action-log external-only default view

Verdict: PASS WITH LIMITS

Independent scope: view change only; build tree and data read-only. Mozi authored the implementation; Shaka did not modify it.

1. Default view — PASS
Live GET http://127.0.0.1:18772/api/plugins/continuum/projects?view=action_log returned HTTP 200 with counts total=736, active=736, archived=0, scope=external, external_total=223, internal_total=513. Independent ledger recount from build/data/action_log.json: 736 rows; kinds dispatch=426, merge=98, retraction=77, direct-edit=54, post=45, close=36; locked predicate recount external=223/internal=513. Paged external set is newest-first (first page first kinds merge, direct-edit, retraction; timestamp non-increasing), all evidence_link values non-empty. Default page contains post/merge/retraction rows and zero dispatch/close; scope=all contains dispatch.

2. Direct-edit boundary — PASS
Independent recount of 54 direct-edit rows found qualifying public branch rows: 3 (target/evidence public under the ruling), and internal receipt-path rows excluded. API default includes qualifying direct-edits and excludes internal direct-edits; default kind set is direct-edit/merge/post/retraction. Unknown-kind fail-safe and URL/post-url cases are covered by test_action_log_scope.py.

3. scope=all / invalid scope — PASS
Live scope=all returned HTTP 200 and the same 736 active-row population/order contract (paged at max page_size=200; receipt/live read-back recorded 736). scope=all is a strict superset of external. scope=bogus returned HTTP 400; case-sensitive External and bogus+kind also returned HTTP 400. Existing envelope keys remain: total, active, archived, by_kind, by_operator.

4. Counts — PASS
Data-file recount: external 223 + internal 513 = active 736. Live counts match exactly. Existing count keys were retained; additive keys are scope, external_total, internal_total. by_kind and by_operator are present and unchanged in shape.

5. Archive round-trip BOTH scopes — PASS
Using live POST routes, archived one post row and one dispatch row, double-archived each, browsed archived status under external/all, then unarchived both. Double-archive returned noop=true with the original audit_id and no duplicate archive event. External archived row appeared in external archived browse; internal dispatch appeared only in scope=all archived browse. Active views hid archived rows. API responses included audit_id and before/after row state; event sidecar is append-only. Data md5 at start and end: d12d384d9e873addac3abdd8471f7206 (unchanged).

6. Board envelopes byte-compatibility — PASS WITH LIMITS
Live bare /projects and view=today returned HTTP 200 and board envelopes (items/projects path, no actions key). Receipt's pre/post comparison reports byte-identical after scrubbing explicitly volatile clock fields data_as_of/server_time/age_seconds. Independently, scope is rejected outside action-log by the existing validation path (scope=external&view=today was not accepted as an action-log filter). Exact historical raw bytes are not independently reconstructible from the current server, so byte compatibility relies on the recorded pre/post capture and current key-shape read-back.

7. Toggle UI — PASS
Served /app.js grep confirms AL_SCOPES at line 145, default scope external at 147, URL parsing at 218-220, explicit wire emission at 293, and toggle implementation at 2334-2347. Live receipt reports real headless Chrome CDP verification: control rendered with “full log”, aria-pressed=true, one click emitted scope=all and showed dispatch/close rows, second click returned external, kind=post filter persisted, stats showed “scope external · external 223”, and 0 console/page errors. No dead-control evidence found.

8. Tests — PASS for action-log scope; FULL SUITE LIMIT
Exact outputs:
  test_action_log_scope.py: ................... [100%] / 19 passed in 0.60s
  pytest -k action_log: ................................................... [100%] / 51 passed, 379 deselected in 1.15s
  full suite: 428 passed, 2 failed in 35.32s
The two failures are tests/test_task_home_sync.py::test_th_a1_parse_completeness_matches_section_1_table and ::test_th_a2_literal_keys_and_procs_are_byte_identical; failure is the unrelated task-home fixture/parser drift (12 keyed RUNNING rows including Overnight vs expected 11). This is a declared lane limit, not silently treated as green.

9. Receipt audit — PASS WITH LIMITS
mozi-view-receipt.md ends STABLE. File:line claims for action_log.py, service.py, plugin_api.py, and app.js were checked against the served/local source with grep/read-back; the app.js locations for scope parsing, emission, and toggle match. Test tails are quoted verbatim in the Mozi receipt and independently reproduced above. Live read-back and deviations are declared. The receipt's full-suite claim is accurately “2 failed, 428 passed,” not all-green.

Defects:

None found in the scoped view change. D-1 (release blocker outside this lane): full suite is not green because of task-home lane drift; owner: task-home lane, not Mozi view. Evidence: exact failures in tests/test_task_home_sync.py above. Expected fix: reconcile the frozen task-home fixture/table or its expected RUNNING_KEYS, then rerun the full suite.

Limits / actions not taken:

No source/build/data/config edits, no fixes, no process kills, no deploys, no GitHub writes, no install/enable actions. Exact historical board bytes were not independently available; the recorded pre/post comparison was accepted with that limit. Archive API probes intentionally changed then restored rows; final action_log.json md5 proves zero net ledger change; events sidecar growth is expected append-only audit behavior.
