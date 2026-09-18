# Continuum — Standalone Browser Dashboard Implementation Receipt (KodeKoot)

```
task_id:      continuum-kodekoot-standalone-browser-implementation
owner:        kodekoot (implementation)
domain:       implementation
upstream:     handoffs/architecture-continuum-standalone-browser.md (Azaraki, DECIDED D-SB-1..D-SB-7)
authority:    OWNERSHIP-MATRIX.md — "Implementation | KodeKoot | code and tests from a stable
              approved design; efficient implementation"
date:         2026-09-13
state:        IMPLEMENTED — awaiting Halakukhan independent QA gate
correction:   continuum-kodekoot-standalone-threading-fix — standalone transport
              made safe for FastAPI's threaded execution; see §8 for the exact
              fix, the reproduced failure, and the live post-fix evidence.
interpreter:  /Users/kethuda/.hermes/hermes-agent/venv/bin/python (3.11.15) — carries fastapi 0.133.1,
              uvicorn 0.41.0, httpx 0.28.1; node v22.17.1
```

## 1. Outcome

A local browser dashboard now runs Continuum without Hermes Desktop:

```
python3 -m build.dashboard.standalone        # binds 127.0.0.1:8765 by default
```

The app serves the static shell, mounts the EXISTING `dashboard.plugin_api.router`
unchanged under `/api/plugins/continuum`, and the browser board re-expresses the approved
eight-lane kanban interaction set with no SDK and no Raptora data. Raptora was used as a
read-only visual reference (CSS token language only); no file under
`/Users/kethuda/Documents/team6/raptora/wrk/rpt6` was created, edited, or deleted.

## 2. Files

| file | state | lines | sha256 (prefix) |
|------|-------|-------|-----------------|
| `build/dashboard/standalone.py` | new (+ threading-fix adapter, §8) | 179 | `037ff082` |
| `build/dashboard/static/index.html` | new | 41 | — |
| `build/dashboard/static/app.js` | new | 773 | `bd75364e` |
| `build/dashboard/static/styles.css` | new | 238 | `8ed092ef` |
| `build/desktop/plugin_build.py` | new (build step — see §4) | 138 | `0469bdc5` |
| `build/tests/test_standalone.py` | new (+ 3 threading-safety tests, §8) | 344 | `4d775ae1` |
| `build/review/standalone-browser-receipt.md` | new (this file) | — | — |
| `build/desktop/plugin.js` | UNCHANGED (guard-bound source — see §4) | 1079 | byte-identical to pre-task |

`build/data/registry.db` was not modified (mtime unchanged: Sep 12 18:02). No file under
`~/.hermes/plugins/continuum` or `~/.hermes/desktop-plugins/continuum` was touched. No Hermes
source DB was opened for writing.

## 3. Architecture contract delivered

* **Entrypoint / bind (D-SB-1).** `python3 -m build.dashboard.standalone` launches FastAPI via
  uvicorn. `standalone.parse_args([])` → host `127.0.0.1`, port `8765`. `--host`/`--port` exist
  but only affect the run when explicitly passed. No TLS, no auth — the loopback bind is the
  boundary.
* **Static surface.** `GET /` → `static/index.html`; `GET /app.js`; `GET /styles.css`;
  `GET /desktop/kanban-interaction.js` (the shared pure module the browser imports).
* **API boundary (D-SB-3).** `plugin_api.router` mounted unchanged at `/api/plugins/continuum`.
  Test asserts the mounted route set equals `{PREFIX + r.path for r in plugin_api.router.routes}`
  — no route or mutation duplicated.
* **Browser app (D-SB-4).** `app.js` imports `../desktop/kanban-interaction.js` (legal pure ESM
  in a browser) and reuses `BOARD_COLUMNS`, `COLUMN_LABELS`, `NON_INBOX_PLACEMENTS`,
  `classifyKeyEvent`, `canDropOnColumn`, `closeMenuFocusTarget`, `copyIdValue`,
  `menuItemsFor/menuInitialHighlight/menuHighlightIndex/menuActivate`, the toast builders and
  the navigate helpers. Re-expressed, not redesigned: eight fixed lanes; Inbox exit = one atomic
  `accept` carrying `{lifecycle:'LS-1', placement}`; later movement = `set_placement`; undo by
  `audit_id`; detail/session identity; bare Copy ID; `hermes -p <profile> --resume <session_id>`;
  Space opens Move-to and focuses the highlighted item; arrows/Home/End navigate; Enter activates;
  Escape restores focus with no mutation; accepted-lane drop once; Inbox rejected; drag state
  clears on every exit; loading/empty/error/responsive/reduced-motion states.
* **Refresh / scan (D-SB-5).** `/events` polled every 15 s (`POLL_MS = 15000`, parity with the
  desktop surface) plus an explicit "Scan now" `POST /scan` button. No profile picker; the
  service scans whatever profiles exist under `HERMES_HOME`. Registry stays bundle-relative.
* **Visual language (D-SB-2).** `styles.css` carries the dark token block
  (`--bg/--card/--line/--fg/--muted/--gold/--cyan/--rose/--emerald/--violet`) and the
  card/chip/banner vocabulary only. A test asserts none of `raptora`, `rpt6`, `ventures.json`,
  `gen_ventures`, `run_daily_checks`, or the Raptora path appears in any served file or in
  `standalone.py` — no runtime dependency on the Raptora workspace.

## 4. Desktop fix (D-SB-6) — delivered as a build step, not a literal source inline — READ THIS

**Requirement.** The desktop plugin runtime permits only `@hermes/plugin-sdk`, `react`,
`react/jsx-runtime`. `desktop/plugin.js:41` imports `./kanban-interaction.js` — the failure.

**Finding (measured, not argued).** Applying the inline directly to `build/desktop/plugin.js`
provably destroys the locked correction-004/005/006 guard suite, because those guards bind the
*tested* pure module to the *shipped* file:

| guard | breaks under a direct inline | measured result |
|-------|------------------------------|-----------------|
| `tests/test_plugin_source.py::test_plugin_imported_symbols_are_actually_used` | asserts the shared import line exists | FAIL |
| `tests/test_kanban_bounded.py` P1 parity guard | asserts `"kanban-interaction.js"` in `plugin.js` | (comment-dependent) |
| `tests/mutation_evidence.js` structural probes P1–P4 | assert the import + no local redefinition | 4 missed (17→13) |
| `tests/mutation_evidence.js` drift probes | mutate a call site and expect the guard to catch it; once the *definitions* live in `plugin.js`, `classifyKeyEvent(`/`navigateRight(`/`closeMenuFocusControl`/`canDropOnColumn(` still match the definition | **8/13 missed** |

The 8/13 drift misses are the decisive point: inlining removes the falsifiability that
architecture correction-004 mandated precisely to prevent replica/plugin drift. A direct inline
therefore cannot satisfy both D-SB-6 and BC-SB-3 "no regression".

**Resolution.** D-SB-6 explicitly allows the alternative — *"or a build step producing the
same"*. Implemented as `build/desktop/plugin_build.py`:

```
python3 build/desktop/plugin_build.py --check                 # validate purity
python3 build/desktop/plugin_build.py --out <install>/plugin.js  # emit SDK-pure artifact
```

It emits a copy whose imports are exactly `{@hermes/plugin-sdk, react, react/jsx-runtime}` with
the consumed interaction exports inlined verbatim (`--check` → `SDK-pure: OK`, `node --check`
→ valid ESM). It never mutates the repository source, so the canonical pure module
(`desktop/kanban-interaction.js`) stays the single source of truth the behaviour/parity/drift
guards bind to. The inlined artifact is what ships to the desktop-plugin install location.

**Open item for the architect / Halakukhan.** `build/desktop/plugin_build.py` is one file
outside the dispatch's exact allowed list. It is required to satisfy D-SB-6 without regressing
BC-SB-3 / the correction-004 drift mandate, and it touches nothing forbidden. If the intended
reading was a literal inline of `plugin.js`, that requires authorizing re-baseline of
`mutation_evidence.js` P1–P4 + the drift guards and
`test_plugin_source.py::test_plugin_imported_symbols_are_actually_used` — a QA/architecture
decision, not an implementation one. Flagging rather than silently regressing the guard suite.
`build/desktop/plugin.js` is left byte-identical to its pre-task state.

## 5. Gates (measured)

| gate | result |
|------|--------|
| Full Python suite | **173 passed** (151 baseline + 22 `test_standalone.py`) |
| Bounded suite (`test_kanban_bounded.py`) | **52 passed** |
| Node behavioral (`test_kanban_helpers.mjs`) | **75 passed** |
| Shipped paths (`test_kanban_shipped_paths.mjs`) | **17 passed** |
| Structural/parity probes (`mutation_evidence.js` baseline) | **17 passed, 0 missed** |
| Drift (`mutation_evidence.js`) | **12 drift cases caught, 30 total passed, 0 missed** |
| `node --check` all JS | exit 0 — plugin.js, kanban-interaction.js, app.js, kanban_helpers.js, mutation_evidence.js, both `.mjs` |
| Plugin validation | `hermes plugins validate build` → **Validation passed**, exit 0 |
| New standalone tests | **22 passed** |

### BC-SB-1 — loopback-only standalone server (live, evidence)

```
$ lsof -iTCP:8765 -sTCP:LISTEN -n -P
python  54991 kethuda 10u IPv4 ... TCP 127.0.0.1:8765 (LISTEN)     # 127.0.0.1, NOT *:8765
$ curl -s -o /dev/null -w "%{http_code} %{content_type}\n" http://127.0.0.1:8765/
200 text/html; charset=utf-8                                       # shell contains src="/app.js"
$ curl .../app.js                       -> 200 text/javascript
$ curl .../styles.css                   -> 200 text/css
$ curl .../desktop/kanban-interaction.js -> 200 text/javascript
$ curl .../api/plugins/continuum/projects -> 200 {"items":[...board JSON...]}
```

The default run binds only `127.0.0.1`; `--host 0.0.0.0` is reachable only by explicit flag
(asserted in `test_default_bind_is_loopback_only` / `test_host_and_port_override_only_when_requested`).

### BC-SB-2 — semantic parity with the approved kanban contract (isolated fixture data)

Drove the exact payloads `app.js` sends through the mounted API over a throw-away fixture
home + temp registry:

```
scan (explicit)              -> run_id 51b50716, mode incremental
board items: 2 | inbox: 2
accept {lifecycle:'LS-1', placement:'ongoing'}
   -> exactly 1 new review_event ['accept']; placement overlay row {field:'placement', value:'ongoing'}
   -> column after accept: ongoing (source: human)
set_placement {placement:'blocked'}  -> column after: blocked
undo {audit_id:<accept>}             -> column after: inbox        # reverses the acceptance
set_urgent {value:true}              -> urgent: true               # durable overlay
BC-SB-2: PASS
```

### BC-SB-3 — no regression

Full suite, bounded suite, Node behavioral, shipped paths, structural/parity and drift all
pass unchanged in count (see §5). `test_plugin_source.py` and `test_kanban_helpers.mjs`
pass; `plugin.js` is byte-identical to its pre-task state, so the shared-module binding is
intact.

## 6. No install / enable / deploy / publication / readiness claim

This is an implementation receipt only. No install, enablement, deployment, publication, PR,
or readiness claim is made or authorized. Nothing was installed into Hermes Desktop or the
plugin trees. Halakukhan's independent gate is the next step.

## 7. Safety affirmations

* No Hermes source `state.db` was written (tests use temporary fixtures; the live server only
  issues GETs).
* `build/data/registry.db` is byte-unchanged; `standalone.py` never relocates or writes it.
* `/scan` cannot be triggered by a page load — a GET `/` + GET `/projects` fires zero scans
  (asserted live against a spy); only an explicit button click POSTs `/scan`.
* No Raptora runtime dependency in any served file or entrypoint.
* No architecture, service, registry, scanner, model, config, schema, `plugin_api.py`, or
  deployment file was modified.

## 8. Correction — standalone transport threading fix

Task: `continuum-kodekoot-standalone-threading-fix` (upstream: architecture-continuum-standalone-browser.md
D-SB-1..D-SB-7). The board GET worked on the implementation host but failed on the live server:
FastAPI runs every synchronous `def` endpoint on AnyIO's shared worker-thread pool, and that pool
hands successive requests to different threads. The cached `Service`/`Registry`
(`dashboard.plugin_api` caches it in `_SERVICE`) owns one SQLite connection bound at creation to
the thread that created it, so a later request on a second worker thread raised:

```
sqlite3.ProgrammingError: SQLite objects created in a thread can only be used in that same thread.
  The object was created in thread id 6146011136 and this is thread id 6162837504.
  continuum/registry.py:408  list(self.conn.execute("SELECT * FROM project ORDER BY project_id"))
  dashboard/plugin_api.py:54  return get_service().board()
```

**Reproduced before the fix (live uvicorn, real tree):**

```
$ python3 -m build.dashboard.standalone --port 8791     # single GET OK (same thread reused)
$ curl -s .../api/plugins/continuum/projects            -> 200
$ seq 1 20 | xargs -P 20 -I{} curl -s -o /dev/null -w "%{http_code}\n" .../projects | sort | uniq -c
   2 200
  18 500          # 18/20 concurrent GETs 500 with the traceback above
```

**The fix (narrowest implementation-safe route-execution strategy).** No service, registry,
schema, registry-path, route, or desktop change. All of the fix lives in
`build/dashboard/standalone.py`: a single, never-exiting `ThreadPoolExecutor(max_workers=1)`
(`thread_name_prefix="continuum-api"`) and a drop-in `_run_in_threadpool(func, *args, **kwargs)`
that runs every synchronous endpoint on that one dedicated thread via
`loop.run_in_executor(...)`. `create_app()` installs it (`_pin_sync_routes_to_one_thread()`)
BEFORE mounting the router, for both `fastapi.routing.run_in_threadpool` and
`starlette.concurrency.run_in_threadpool`. Every request — and so every access to the cached
Service and its connection — therefore runs on the same thread that created the connection.
`ThreadPoolExecutor`'s single worker blocks on its work queue for the life of the process rather
than timing out like AnyIO's recyclable pool, so the creating thread is stable across idle gaps
(the 15 s `/events` poll). This is confined to the standalone process; the desktop surface and
the Hermes plugin host are untouched. No forbidden file was edited; no architecture escalation
was required.

**RED/GREEN (pre-fix failure vs. post-fix pass) — post-fix live uvicorn, real tree:**

```
$ python3 -m build.dashboard.standalone            # default 127.0.0.1:8765
GET /                -> 200 text/html
GET /app.js          -> 200 text/javascript
GET /styles.css      -> 200 text/css
GET /projects        -> 200 application/json
seq 1 40 | xargs -P 40 ... /projects | sort | uniq -c  ->  40 200        # 0 errors in the log
GET /projects/<id>   -> 200      GET /events -> 200 {"scan":"idle","data_as_of":...}
lsof -iTCP:8765 -sTCP:LISTEN -n -P  ->  TCP 127.0.0.1:8765 (LISTEN)       # loopback only
```

**Explicit POSTs work through the standalone server (isolated temp clone + fixture home, temp
registry — never the real registry):**

```
scan/status before any POST            -> phase "idle"
GET / + GET /projects (page load)      -> phase "idle" (no scan; 0 POSTs in log)
POST /scan                             -> 200 {run_id c7d875eb..., mode incremental}
POST /projects/<id>/review accept {lifecycle:'LS-1', placement:'ongoing'} -> 200 audit 57e148d9... ; column ongoing (src=human)
POST /projects/<id>/review set_placement {placement:'blocked'}            -> 200 audit 87257857... ; column blocked
POST /review/undo {audit_id 57e148d9...}                                  -> 200 ; column inbox (src=candidate)
POST /projects/<id>/review set_urgent {value:true}                        -> 200 ; urgent=true
seq 1 40 | xargs -P 40 ... /projects  -> 40 200                           # 0 errors
```

**Tests added (`build/tests/test_standalone.py` §8, 3 new; suite 19 → 22):**

* `test_sync_route_execution_is_pinned_to_one_thread` — the adapter runs 8 routed calls on one
  thread id (fails if the pin is removed).
* `test_create_app_installs_the_single_thread_adapter` — `create_app` installs
  `_run_in_threadpool` for both FastAPI and Starlette.
* `test_concurrent_board_requests_do_not_cross_threads` — 48 overlapping board GETs via a
  16-worker pool over an isolated fixture Service; all must be 200 (reproduces the cross-thread
  500 if the pin is removed — independently confirmed by reverting to AnyIO's pool:
  `Exception ... sqlite3.ProgrammingError ... registry.py:408`).

**Regression gates (measured after the fix):** full Python 173 passed; bounded 52 passed;
Node behavioral 75; shipped paths 17; structural/parity 17 passed / 0 missed; drift 12 cases
caught / 30 total passed / 0 missed; `node --check` clean on all JS; `hermes plugins validate
build` → Validation passed.

**Data safety:** `build/data/registry.db` byte-unchanged across the entire live evidence run
(sha256 `94787bcc…f43c`, mtime `Sep 12 18:02:43 2026`, identical before and after; the live
server only issued GETs, and every explicit POST was driven against an isolated temp clone whose
registry lives under a throw-away directory). No Hermes source `state.db` was opened for writing.

**No install / enable / deploy / publication / readiness claim.** This correction is an
implementation receipt for the threading fix only; Halakukhan's independent gate remains the
next step.
