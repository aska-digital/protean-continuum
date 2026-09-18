# action-log receipt (build-tree copy)

STATUS: STABLE

The full Mozi receipt for the Orda action-log implementation lives at
`/Users/kethuda/.hermes/profiles/proteus/cache/delegation/action-log/mozi-receipt.md`
(written there per the brief's OUTPUT FORMAT; this file is the §7 MAY-WRITE location
`review/action-log-receipt.md` pointing at it).

Key facts repeated here so an auditor reading the build tree needs no cross-repo hop:

- ledger: `data/action_log.json` sha256 aad7491d932b273c47c535fa6b81705b4f3aae7cbd2f3ea25ed38c22d7f8a197
- events: `data/action_log_events.jsonl` sha256 0e8c680f4a69994d3f980457c6127081327461392e47e37072a4f06e6a69cc63 (8 events)
- lock: `data/action_log.lock`
- rows: 736 total, 736 active, 0 archived
  (by_kind dispatch 426, merge 98, retraction 77, direct-edit 54, post 45, close 36;
   by_operator agent 670, operator 66; by_source inflight 204, dispatch-ledger 222, receipt 310)
- suite: `pytest tests -q` → 401 passed, exit 0; AL tests 32 passed
- live: http://127.0.0.1:18772/?view=action_log (my own instance; 127.0.0.1:18771 untouched)
- deviations: see §7 of the full receipt (OPEN-1 keyword precedence + negation/compound guards;
  two frozen-guard test edits declared as out-of-boundary; named lane pr109015-evidence = 0 rows,
  its evidence sits outside the §3 source patterns).

STABLE
