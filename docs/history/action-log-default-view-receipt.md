# Proteus receipt — action-log external-only default view
1. STATUS: COMPLETE — default view is external-only; toggle to full log live.
2. Live URL verified: http://127.0.0.1:18772/?view=action_log (API default 200 external-only; scope=all 736; scope=bogus API 400; HTML shell ignores scope by design).
3. Default partition verified live: external_total 223 (post 45 + merge 98 + retraction 77 + 3 qualifying direct-edits) / internal_total 513 = active 736; newest-first; evidence_link on every row; zero dispatch/close in default.
4. Mozi bounded restart (session 20260917_185854_e2b87b) finished the SIGINT'd run: 19 new scope tests, targeted trio 51 passed, full suite 428 passed / 2 FAILED (foreign task-home lane drift, TH-A1/A2 — owner continuum-v2exec, outside this lane); data action_log.json md5 d12d384d9e873addac3abdd8471f7206 unchanged; board envelopes byte-compatible.
5. Shaka QA (session 20260917_191759_b2084e): PASS WITH LIMITS — 9-point checklist evidenced, archive round-trips green under both scopes, toggle verified; defect D-1 recorded against the task-home lane, not the view change.
6. Model gates: shaka config=state.db (gpt-5.6-luna-900k/openai-codex). mozi needs-decision: state.db primary nous/qwen3.8-flash fallback vs config opencode-go/deepseek-v4.1-flash — recorded, artifact verified independently by Proteus read-backs.
7. G-5/G-6/G-7 FAILs scanned this pass are all foreign-lane rows (kit-issues overlap, prior consecutive-mozi rows, 1 learning) — recorded, not routed around; no violation touches this lane's file set.
8. Records: ops INFLIGHT (crash classified + recovery released), DISPATCH-LEDGER (recovery DONE, shaka DONE), ROTATION-STATE (Mozi→Shaka boundary), action-log/journal.md appended. Limit: no re-audit of the pre-existing lane scope; task-home D-1 open with its owner.
STABLE
