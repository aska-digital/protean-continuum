# INFLIGHT — live writer leases (FROZEN fixture for action-log tests)

## Columns (fixed)
| column | meaning |
|---|---|
| claim id | stable claim id assigned by the writer |
| session id | hermes session id of the writer |
| profile | profile that owns the lease |
| provider/model | verified from the profile config at claim time |
| tree / worktree / branch | the working tree the claim covers |
| owned files | files the claim covers |
| claim time | ISO-8601 UTC |
| lease expiry | ISO-8601 UTC |
| release time | ISO-8601 UTC when released |
| conflict result | conflict check outcome |
| status | live lease state |

## Lease rows

| claim id | session id | profile | provider/model | tree / worktree / branch | owned files | claim time | lease expiry | release time | conflict result | status |
|---|---|---|---|---|---|---|---|---|---|---|
| (empty state) | — | — | — | — | — | — | — | — | — | empty — no live writer |
| inf-20260918-fixture-01 | 20260918_101010_aaaaaa | mozi | nous / fixture-model — read from profiles/mozi/config.yaml at claim time | `~/.hermes/profiles/mozi/cache/delegation/fixture-lane` | `fixtures/action_log/one.md` (create) | 2026-09-18T10:10:10Z | 2026-09-18T12:10:10Z | | none — fixture baseline md5 | active — writer holds lease |

| inf-20260918-fixture-02 | 20260918_111111_bbbbbb | mozi | nous / fixture-model — read from profiles/mozi/config.yaml at claim time | `~/.hermes/profiles/mozi/cache/delegation/fixture-lane-2` | `fixtures/action_log/two.md` (create) | 2026-09-18T11:11:11Z | 2026-09-18T13:11:11Z | 2026-09-18T11:40:00Z | none — fixture baseline md5 | released — writer done |
| inf-20260918-fixture-03 | 20260918_121212_cccccc mozi session, direct records action | mozi | nous / fixture-model — read from profiles/mozi/config.yaml at claim time | `~/.hermes/profiles/mozi/cache/delegation/fixture-lane-3` | `fixtures/action_log/three.md` (create) | 2026-09-18T12:12:12Z | 2026-09-18T14:12:12Z | 2026-09-18T12:30:00Z | none — fixture baseline md5 | released — operator action |
