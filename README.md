# protean-continuum

Continuum is the dashboard component of the [Protean System](https://github.com/aska-digital):
a kanban board over AI-agent session and project activity, plus an external-facing action-log
view with scope filtering. It is a single FastAPI/uvicorn app that serves a static browser shell
(`index.html` + `app.js` + a shared pure interaction module) and one JSON board route,
`/api/plugins/continuum/projects`, which carries both the board envelope (`items[]`) and the
action-log envelope (`view=action_log`, `actions[]`, default scope `external`). The runtime is a
byte-faithful consolidation of the live dashboard tree — this repo is the source of truth for
the code, the frozen design spec, the grammar ruling, and the implementation history.

## Run

Requires Python 3 with `fastapi`, `starlette`, `uvicorn`, and `pydantic` installed
(any interpreter that can import them; the live instance uses a dedicated venv).

```sh
cd server

# Board view  (live instance uses port 18771)
python3 -m dashboard.standalone --host 127.0.0.1 --port 18771

# Action-log view — the SAME app; its action-log default query scope is `external`
# in code (continuum/action_log.py SCOPES + the app.js toggle).
# Live instance runs a second process on port 18772.
python3 -m dashboard.standalone --host 127.0.0.1 --port 18772
```

Then open `http://127.0.0.1:18771/` (board) or `http://127.0.0.1:18772/` (action log).
The server binds loopback by default and never opens a network socket unless you override
`--host`. Launch cwd does not matter: all paths resolve module-relative from `server/`.

First boot auto-creates an empty `server/data/registry.db` (SQLite, WAL mode). To populate it
with your own Hermes profile/session corpus:

```sh
cd server && python3 -m continuum.cli        # scan surface (see --help for subcommands)
```

Both live servers share one `data/registry.db`; single-writer discipline is preserved via
WAL mode and the lock files in `data/` (generated at runtime, not committed).

### Machine-specific config (re-homing)

Code is byte-faithful to the live tree; deployment knobs live in `server/config.yaml`:

- `task_home_source_path` — absolute path to the task-home source document (read-only).
  Ships with the live instance's path; point it at your own source doc.
- `generic_paths` — context-only path prefixes for clustering (includes the operator's home).
- The scanner reads Hermes profiles under `~/.hermes` (override with the `HERMES_HOME` env).
- Registry/ledger paths are bundle-relative (`data/...`) and need no change.

## Repo map

```
server/            Runtime tree, byte-faithful mirror of the live bundle root.
  dashboard/       Browser entrypoint (standalone.py), API router (plugin_api.py),
                   manifest.json, static shell (index.html, app.js, styles.css).
  desktop/         kanban-interaction.js — shared pure interaction module, served at
                   /desktop/ and imported by app.js. plugin.js + plugin_build.py —
                   the Hermes-Desktop plugin surface (M8) and its single-file builder.
  review/          Offline review harnesses (ab_snapshot.py, mc_measure.py — no server,
                   no network) and the lane receipts that freeze their contracts.
  __init__.py      Bundle package marker (legacy `-m build.dashboard...` form).
  continuum/       Core package: service.py (singleton), config.py, registry.py,
                   scanner.py, classify.py, cluster.py, evidence.py, model.py,
                   audience.py, task_home.py (parser/sync/exporters), action_log.py
                   (ledger + external-view filter), cli.py.
  config.yaml      Bundle config (registry + task-home paths — see re-homing above).
  tests/           Full test suite incl. test_action_log_scope.py (19 scope tests).
  fixtures/        Frozen v2 task-home fixture + action-log parser fixtures.
spec/              Frozen dashboard design spec (frida-design.md), handoff receipt,
                   and 26 design-evidence screenshots (S1–S9 series).
grammar/           Task-home v2 grammar ruling (leo-ruling.md) + lane receipt —
                   the amendment contract the parser patch implements.
docs/              Architecture specs (continuum-sync, action-log) and history/
                   receipts documenting live-code state: v2 parser patch state
                   (v2-exec, apply), board-order move (revert hash
                   84f4e9de15b2fb8acf183d2fc658698e — move is LIVE), action-log
                   view-filter implementation + QA, external-only default view.
```

## Testing

```sh
cd server && python3 -m pytest tests -q
```

427/430 pass on a fresh clone (Python 3 + fastapi/starlette/uvicorn/pydantic, run with
`-p no:cacheprovider`). Three documented couplings account for the rest — none is a defect
in this tree, and the code is byte-faithful to the live instance, so they are carried,
not edited:

- `test_standalone.py::test_registry_path_is_preserved` asserts the bundle directory is
  *named* `build` (the live tree's name); here it is `server/`. The behavioural invariant it
  protects — the registry path stays bundle-relative and is never relocated — holds and is
  visible in the assertion diff.
- `test_task_home_sync.py::test_th_a1/th_a2` compare the frozen v2 fixture
  (`fixtures/task_home/task-home.md`, byte-identical to the live tree) against stale v1
  expectations. This is the v2 re-import blocker itself — see
  `docs/history/v2exec-mozi-receipt.md` — and fails identically on the live tree.
- Six `test_overview_modes.py` tests read the *live corpus* from `data/registry.db`. On an
  empty registry they fail by construction; provision the data first (run a scan, or take a
  read-only snapshot of an existing registry: `sqlite3 <src>.db ".backup data/registry.db"`).

Note: the action-log scope classification treats paths under `~/.hermes` as internal
(`continuum/action_log.py` `INTERNAL_PATH_ROOT`), and the test suite relies on the bundle
living there — run the suite from a clone under `~/.hermes`.

## Notes

- `data/` contents (registry.db, action-log ledger, sync ledger, locks) are runtime state and
  are gitignored; they are regenerated by scan/sync, and the action-log ledger also lives in
  `data/action_log.json`.
- Mid-amendment state (documented in docs/history/): `continuum/task_home.py` carries an
  un-gated v2 parser patch (steps 1–3 done, steps 4–8 blocked). This is intentional —
  consolidation, not refactor.
- License: MIT (see LICENSE).
