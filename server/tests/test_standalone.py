"""Standalone browser dashboard — implementation tests (KodeKoot, D-SB-1..D-SB-7).

Covers, per the dispatch/gate list:

* app import + route mounting (static shell + existing API prefix)
* default loopback host/port (never exposed by default)
* static shell response
* browser app imports the shared pure interaction module and reuses the API prefix
* no Raptora runtime dependency
* explicit scan only — a GET / page load never triggers a scan
* source DB read-only contract + registry path preservation
* the D-SB-6 desktop build step produces an SDK-pure plugin without mutating the
  guard-bound source

Everything here is offline: no network, no real profile DB write, temp registries.
"""
from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import os
import re
import shutil
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from dashboard import plugin_api
from dashboard import standalone
from continuum.config import load_config, PLUGIN_DIR
from continuum.service import Service
from fixtures.make_fixture import build_fixture_tree

BUILD_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC_DIR = os.path.join(BUILD_ROOT, "dashboard", "static")
PLUGIN_JS = os.path.join(BUILD_ROOT, "desktop", "plugin.js")
SHARED_JS = os.path.join(BUILD_ROOT, "desktop", "kanban-interaction.js")
PLUGIN_BUILD = os.path.join(BUILD_ROOT, "desktop", "plugin_build.py")
STANDALONE_PY = os.path.join(BUILD_ROOT, "dashboard", "standalone.py")
REGISTRY_DB = os.path.join(str(PLUGIN_DIR), "data", "registry.db")

SDK_ALLOWED = {"@hermes/plugin-sdk", "react", "react/jsx-runtime"}


def _read(path):
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def _file_state(path):
    if not os.path.exists(path):
        return None
    with open(path, "rb") as fh:
        return (os.stat(path).st_size, hashlib.sha256(fh.read()).hexdigest())


@pytest.fixture()
def isolated_service(tmp_path, monkeypatch):
    """A Service over a throw-away fixture home + temp registry, with a scan spy.

    The Service is built lazily on first use so its SQLite connection is created
    in the same thread that serves the request (TestClient runs the app in a
    worker thread; a connection created in the test thread cannot be reused).
    """
    home = build_fixture_tree(str(tmp_path / "hermes_home"))
    cfg = load_config(hermes_home=home)
    cfg.bundle["registry_path"] = str(tmp_path / "registry.db")
    holder = {"svc": None}
    calls = {"scan": 0}

    def factory():
        if holder["svc"] is None:
            svc = Service(cfg)
            original = svc.scan

            def spy(*args, **kwargs):
                calls["scan"] += 1
                return original(*args, **kwargs)

            svc.scan = spy
            holder["svc"] = svc
        return holder["svc"]

    monkeypatch.setattr(plugin_api, "get_service", factory)
    yield {"factory": factory, "holder": holder, "cfg": cfg, "calls": calls}
    monkeypatch.delenv("HERMES_HOME", raising=False)


# ── 1. app import + route mounting ─────────────────────────────────────
def test_app_imports_and_mounts_routes():
    app = standalone.create_app()
    paths = {getattr(r, "path", None) for r in app.routes}
    for expected in ("/", "/app.js", "/styles.css", "/desktop/kanban-interaction.js"):
        assert expected in paths, "missing static route {}".format(expected)
    # existing router mounted unchanged at the proven prefix
    assert "{}/projects".format(standalone.API_PREFIX) in paths
    assert "{}/projects/{{project_id}}".format(standalone.API_PREFIX) in paths
    assert "{}/review/undo".format(standalone.API_PREFIX) in paths


def test_existing_api_prefix_is_reused_not_redefined():
    app = standalone.create_app()
    paths = {getattr(r, "path", None) for r in app.routes}
    for suffix in ("/scan", "/events", "/overview", "/attention", "/staleness", "/noise"):
        assert standalone.API_PREFIX + suffix in paths
    # the mounted routes are exactly plugin_api.router's (no duplicated handlers)
    api_paths = {r.path for r in app.routes if getattr(r, "path", "").startswith(standalone.API_PREFIX)}
    router_paths = {standalone.API_PREFIX + r.path for r in plugin_api.router.routes}
    assert api_paths == router_paths


# ── 2. default loopback host/port ──────────────────────────────────────
def test_default_bind_is_loopback_only():
    args = standalone.parse_args([])
    assert args.host == "127.0.0.1"
    assert args.port == 8765
    assert standalone.DEFAULT_HOST == "127.0.0.1"
    assert standalone.DEFAULT_PORT == 8765


def test_host_and_port_override_only_when_requested():
    assert standalone.parse_args([]).host == "127.0.0.1"
    over = standalone.parse_args(["--host", "0.0.0.0", "--port", "9000"])
    assert over.host == "0.0.0.0"
    assert over.port == 9000


# ── 3. static shell response ───────────────────────────────────────────
def test_static_shell_response():
    client = TestClient(standalone.create_app())
    res = client.get("/")
    assert res.status_code == 200
    assert "text/html" in res.headers["content-type"]
    body = res.text
    assert "app.js" in body
    assert 'type="module"' in body
    # no external CDN
    assert "http://" not in body.replace("http://127.0.0.1", "")
    assert "cdn" not in body.lower()


def test_served_static_files_match_source():
    client = TestClient(standalone.create_app())
    for route, filename, ctype in (
        ("/app.js", "app.js", "javascript"),
        ("/styles.css", "styles.css", "css"),
        ("/desktop/kanban-interaction.js", None, "javascript"),
    ):
        res = client.get(route)
        assert res.status_code == 200, route
        assert ctype in res.headers["content-type"], route
    # the shared route serves the desktop module verbatim
    shared = client.get("/desktop/kanban-interaction.js")
    assert shared.text == _read(SHARED_JS)


def test_browser_app_imports_shared_interaction_module():
    src = _read(os.path.join(STATIC_DIR, "app.js"))
    assert "from '../desktop/kanban-interaction.js'" in src
    for symbol in ("BOARD_COLUMNS", "COLUMN_LABELS", "classifyKeyEvent", "canDropOnColumn",
                   "copyIdValue", "menuItemsFor", "menuInitialHighlight"):
        assert symbol in src, "app.js must reuse shared symbol {}".format(symbol)
    # polling interval parity with the shipped desktop surface
    assert "POLL_MS = 15000" in src
    assert "/events" in src


# ── 4. no Raptora runtime dependency ───────────────────────────────────
def test_no_raptora_runtime_dependency():
    forbidden = ("raptora", "rpt6", "ventures.json", "gen_ventures", "run_daily_checks")
    sources = {
        "standalone.py": _read(STANDALONE_PY),
        "app.js": _read(os.path.join(STATIC_DIR, "app.js")),
        "styles.css": _read(os.path.join(STATIC_DIR, "styles.css")),
        "index.html": _read(os.path.join(STATIC_DIR, "index.html")),
    }
    for name, src in sources.items():
        low = src.lower()
        for token in forbidden:
            assert token not in low, "{} references Raptora token {!r}".format(name, token)
        assert "/Users/kethuda/Documents/team6/raptora" not in src


# ── 5. explicit scan only ──────────────────────────────────────────────
def test_no_scan_on_page_load(isolated_service):
    calls = isolated_service["calls"]
    client = TestClient(standalone.create_app())
    assert client.get("/").status_code == 200
    assert client.get("/app.js").status_code == 200
    r = client.get(standalone.API_PREFIX + "/projects")
    assert r.status_code == 200
    assert "items" in r.json()
    assert calls["scan"] == 0, "a GET / page load must never trigger a scan"


def test_scan_runs_only_via_explicit_post(isolated_service):
    calls = isolated_service["calls"]
    client = TestClient(standalone.create_app())
    r = client.post(standalone.API_PREFIX + "/scan")
    assert r.status_code == 200
    assert calls["scan"] == 1


def test_appjs_scan_is_bound_to_the_scan_button_only():
    src = _read(os.path.join(STATIC_DIR, "app.js"))
    assert "API + '/scan'" in src
    # exactly one fetch of /scan, inside the scanNow() function
    assert src.count("API + '/scan'") == 1
    # the button click is the only binding for scanNow
    assert "els.scan.addEventListener('click', scanNow)" in src
    # boot initialises via refresh(), never scanNow()
    assert "refresh().then(" in src
    assert "boot()" in src


# ── 6. source DB read-only + registry path preservation ────────────────
def test_registry_path_is_preserved():
    cfg = load_config(os.path.expanduser("~/.hermes"))
    # PLUGIN_DIR is server/ (continuum/config.py: PLUGIN_DIR = Path(__file__).parent.parent)
    # so registry_path "data/registry.db" resolves to server/data/registry.db
    assert cfg.registry_path_resolved().endswith(os.path.join("data", "registry.db"))
    assert cfg.registry_path_resolved() == REGISTRY_DB


def test_standalone_never_relocates_or_writes_the_registry():
    src = _read(STANDALONE_PY)
    # the standalone module must not override the registry location
    assert "registry_path" not in src
    assert "sqlite3" not in src
    assert "os.replace" not in src and "shutil.move" not in src


def test_standalone_does_not_touch_source_databases():
    src = _read(STANDALONE_PY)
    for banned in ("state.db", "sqlite3", "mode=rw", "INSERT", "UPDATE ", "DELETE"):
        assert banned not in src, "standalone.py must not contain {!r}".format(banned)


def test_importing_and_serving_does_not_mutate_the_registry(isolated_service):
    """Importing the app and serving reads must not change build/data/registry.db."""
    before = _file_state(REGISTRY_DB)
    app = standalone.create_app()
    client = TestClient(app)
    client.get("/")
    client.get("/app.js")
    client.get(standalone.API_PREFIX + "/projects")  # patched isolated service
    after = _file_state(REGISTRY_DB)
    assert before == after, "build/data/registry.db must be byte-unchanged by a read-only serve"


def test_isolated_scan_writes_only_the_temp_registry(tmp_path, isolated_service):
    svc = isolated_service["factory"]()
    reg = str(tmp_path / "registry.db")
    assert svc.cfg.registry_path_resolved() == reg
    svc.scan()
    assert os.path.exists(reg)
    # source fixtures under the temp home remain present and reachable
    for prof in ("alpha", "beta"):
        db = os.path.join(str(tmp_path / "hermes_home"), "profiles", prof, "state.db")
        assert _file_state(db) is not None


# ── 7. D-SB-6 desktop build step ───────────────────────────────────────
def _load_plugin_build():
    spec = importlib.util.spec_from_file_location("continuum_plugin_build", PLUGIN_BUILD)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_desktop_source_stays_guard_bound():
    """The repo source keeps the shared-module import the parity/drift guards bind to."""
    assert "from './kanban-interaction.js'" in _read(PLUGIN_JS)


def test_build_step_produces_sdk_pure_plugin(tmp_path):
    mod = _load_plugin_build()
    out = mod.build_plugin(_read(PLUGIN_JS), _read(SHARED_JS))
    specs = mod.import_specifiers(out)
    assert specs, "expected imports"
    assert specs <= SDK_ALLOWED, "non-SDK imports remain: {}".format(specs - SDK_ALLOWED)
    assert "./kanban-interaction.js" not in specs
    # consumed helpers are inlined and used
    for name in ("BOARD_COLUMNS", "classifyKeyEvent", "copyIdValue"):
        assert name in out
    if shutil.which("node"):
        target = tmp_path / "plugin_dist.mjs"
        target.write_text(out, encoding="utf-8")
        proc = subprocess.run(["node", "--check", str(target)], capture_output=True, text=True)
        assert proc.returncode == 0, proc.stderr


def test_build_step_does_not_mutate_the_source():
    before = _read(PLUGIN_JS)
    mod = _load_plugin_build()
    mod.build_plugin(before, _read(SHARED_JS))
    assert _read(PLUGIN_JS) == before


# ── 8. threaded FastAPI transport safety ───────────────────────────────
# The live board GET failed on the standalone server with a cross-thread SQLite
# error because FastAPI runs sync endpoints on AnyIO's shared worker pool while
# the cached Service owns a single connection bound to its creating thread.  The
# adapter pins every sync endpoint to one dedicated thread; these tests bind
# that invariant to behaviour (and reproduce the failure if it is removed).
def test_sync_route_execution_is_pinned_to_one_thread():
    """Every routed sync call must run on the same dedicated thread."""
    def probe():
        return threading.get_ident()

    async def run():
        return [await standalone._run_in_threadpool(probe) for _ in range(8)]

    ids = asyncio.run(run())
    assert len(set(ids)) == 1, "sync routes must execute on a single thread"


def test_create_app_installs_the_single_thread_adapter():
    import fastapi.routing as fastapi_routing
    import starlette.concurrency as starlette_concurrency

    standalone.create_app()
    assert fastapi_routing.run_in_threadpool is standalone._run_in_threadpool
    assert starlette_concurrency.run_in_threadpool is standalone._run_in_threadpool


def test_concurrent_board_requests_do_not_cross_threads(isolated_service):
    """A burst of overlapping board GETs must all succeed.

    Without the single-thread adapter the cached Service's connection is created
    on one worker thread and reused on another, raising the cross-thread SQLite
    error and 500-ing the request.
    """
    client = TestClient(standalone.create_app())

    def hit(_):
        return client.get(standalone.API_PREFIX + "/projects").status_code

    with ThreadPoolExecutor(max_workers=16) as pool:
        codes = list(pool.map(hit, range(48)))
    assert set(codes) == {200}, codes
    # exactly one Service instance (the cached singleton) served every request
    assert isolated_service["factory"]() is isolated_service["holder"]["svc"]


# ── 9. card summary + vertical spacing contract (D-CS-1..D-CS-6) ───────
# Renderer-only copy/rhythm assertions. These are STATIC/served-file string
# checks on the same bytes the standalone server hands the browser — they bind
# the labels, the tier selection, the fallback patterns and the spacing rules;
# they make no behavioral/DOM claim (that is the QA gate's job).
APP_JS = os.path.join(STATIC_DIR, "app.js")
STYLES_CSS = os.path.join(STATIC_DIR, "styles.css")

# words/theory that must never leak into a fallback rendered by a card
FORBIDDEN_NARRATIVE = (" next ", " should ", " plan ", " must ", " need to ")


def _css_rule(src, selector):
    """Return the declaration body of the first exact ``selector { ... }`` rule."""
    m = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", src)
    assert m, "missing CSS rule {!r}".format(selector)
    return m.group(1)


def _card_render_region():
    """Source slice owning card rendering (left-off helpers + renderCard)."""
    src = _read(APP_JS)
    start = src.index("function primarySessionFor(card) {")
    end = src.index("function renderBoard(")
    return src[start:end]


def _string_literals(region):
    out = []
    for a, b in re.findall(r"'([^'\\]*(?:\\.[^'\\]*)*)'" + r'|"([^"\\]*(?:\\.[^"\\]*)*)"', region):
        out.append(a or b)
    return out


def test_state_row_uses_state_label():
    src = _read(APP_JS)
    assert "'State: '" in src
    # the semantically wrong "Waiting on:" prefix is gone from the card renderer
    assert "Waiting on: " not in src


def test_card_contract_labels_present():
    src = _read(APP_JS)
    assert "'ABOUT THIS'" in src
    assert "'LEFT OFF · DECLARED'" in src
    assert "'LEFT OFF · DERIVED'" in src
    assert "'Nothing recorded yet.'" in src
    # the stale chip text is the literal `stale`
    assert "text: 'stale'" in src


def test_about_block_is_guarded_by_summary_text_only():
    src = _read(APP_JS)
    assert "if (card.summary_text) {" in src
    region = _card_render_region().lower()
    # no invented placeholder stands in for an absent summary
    assert "no summary" not in region
    assert "not yet described" not in region


def test_left_off_primary_selection_is_role_aware_with_fallback():
    src = _read(APP_JS)
    assert "s.role === 'primary'" in src  # first sessions[] entry marked primary
    assert "sessions[0]" in src            # fallback to the first entry


def test_left_off_fallback_title_pattern():
    src = _read(APP_JS)
    assert "'untitled · ' + sid.slice(0, 12)" in src


def test_declared_stale_chip_is_conditional():
    src = _read(APP_JS)
    assert "card.declared_stale ? h('span', { class: 'c-chip chip-stale'" in src
    assert ".c-chip.chip-stale" in _read(STYLES_CSS)


def test_omitted_sessions_suffix_uses_existing_field():
    src = _read(APP_JS)
    assert "card.sessions_omitted" in src
    assert "text: '+' + omitted + ' more'" in src


def test_card_aria_extends_with_left_off_text():
    src = _read(APP_JS)
    assert ("cardAria = name + ' · ' + (urgent ? 'urgent · ' : '') + lifecycleName"
            " + ' · ' + stallLabel + ' · ' + leftOff.text") in src


def test_left_off_tiers_introduce_no_narrative_prose():
    """A card with no declared next action must yield no narrative fallback."""
    region = _card_render_region().replace("next_action", "")
    blob = " " + " ".join(_string_literals(region)).lower() + " "
    for word in FORBIDDEN_NARRATIVE:
        assert word not in blob, "card render literal must not contain {!r}".format(word)


def test_no_backend_surface_change_from_card_render():
    src = _read(APP_JS)
    # read-only GETs + the existing audited mutation routes, nothing else.
    # M4 adds exactly ONE read: the lazy, detail-only session-pane GET
    # (`/projects/{id}?pane=...`, D-SP-3).
    # AL (Orda action log, AL-L5/§4) adds exactly ONE read that rides the existing apiGet
    # (`GET /projects?view=action_log`) and ONE audited mutation route that the browser must
    # reach for the per-row archive button of the action-log page
    # (`POST /action-log/{id}/archive|unarchive`). Board/route shapes are unchanged: no
    # existing path gained a parameter and no existing mutation route changed.
    assert src.count("fetch(") == 8
    assert src.count("method: 'POST'") == 4
    assert "'/evidence'" not in src
    assert "API + '/scan'" in src and "API + '/review/undo'" in src
    assert "API + '/action-log/'" in src
    assert "id: 'kanban-card-' + card.project_id" in src


def test_card_vertical_rhythm_css():
    css = _read(STYLES_CSS)
    card = _css_rule(css, ".c-card")
    assert "padding: 12px" in card
    assert "margin-bottom: 12px" in card
    block = _css_rule(css, ".c-block")
    assert "margin-top: 8px" in block
    assert "padding-top: 8px" in block
    assert "border-top: 1px solid var(--line)" in block
    assert "margin-top: 6px" in _css_rule(css, ".c-row + .c-row")
    label = _css_rule(css, ".c-block-label")
    assert "font-size: 10px" in label
    assert "font-weight: 700" in label
    assert "letter-spacing" in label
    assert "text-transform: uppercase" in label
    assert "color: var(--dim)" in label


def test_card_line_clamps_css():
    css = _read(STYLES_CSS)
    assert "-webkit-line-clamp: 3" in _css_rule(css, ".c-summary")
    assert "-webkit-line-clamp: 2" in _css_rule(css, ".c-leftoff")
    link = _css_rule(css, ".c-link")
    assert "flex: 1" in link
    assert "min-width: 0" in link
    assert "-webkit-line-clamp: 2" in link


def test_lane_dimensions_unchanged():
    css = _read(STYLES_CSS)
    assert "--lane-min: 260px" in css
    assert "min-height: 220px" in _css_rule(css, ".c-lane")


def test_served_card_assets_match_source_and_carry_labels():
    client = TestClient(standalone.create_app())
    served_js = client.get("/app.js").text
    served_css = client.get("/styles.css").text
    assert served_js == _read(APP_JS), "served /app.js must equal the build file bytes"
    assert served_css == _read(STYLES_CSS), "served /styles.css must equal the build file bytes"
    for label in ("ABOUT THIS", "LEFT OFF · DECLARED", "LEFT OFF · DERIVED", "Nothing recorded yet."):
        assert label in served_js, "served app.js missing {!r}".format(label)
    assert "padding: 12px" in served_css
    assert "-webkit-line-clamp: 3" in served_css


def test_standalone_detail_presentation_uses_existing_identity_and_resume_payloads():
    src = _read(APP_JS)
    assert "session.profile || session.profile_name" in src
    assert "sessionProfile(primary)" in src
    assert "d.resume_links || []" in src
    assert "text: 'Resume'" in src
    assert "copyText(command, 'Resume command')" in src


def test_standalone_detail_and_pane_failures_are_honest_and_retryable():
    src = _read(APP_JS)
    assert "Message capture is unavailable for this session." in src
    assert "Message capture could not be read for this session." in src
    assert "pane.reason === 'source_unreadable'" in src
    assert "Showing cached detail · stale" in src
    assert "text: 'Retry'" in src
    # Refresh failures retain the candidate detail instead of blanking it.
    assert "if (!sameDetail) state.detail = null" in src


# ── 10. M4 Continuity Playground route contract (serve-level, no readiness claim) ──
def test_m4_projects_route_compat_and_query_echo(isolated_service):
    client = TestClient(standalone.create_app())
    # omitted parameters -> the legacy envelope (existing clients keep working)
    legacy = client.get(standalone.API_PREFIX + "/projects")
    assert legacy.status_code == 200
    assert set(legacy.json().keys()) == {"items", "page", "total", "counts", "data_as_of",
                                         "scan_state"}
    # view=today -> the additive query and playground metadata
    today = client.get(standalone.API_PREFIX + "/projects?view=today")
    assert today.status_code == 200
    body = today.json()
    assert {"page_size", "query", "playground"} <= set(body.keys())
    assert body["query"]["view"] == "today"
    assert body["playground"]["mode"] == "today"
    # repeatable filters bind as query parameters (not a request body)
    filtered = client.get(standalone.API_PREFIX
                          + "/projects?view=all&lane=inbox&lane=ongoing&sort=name&direction=asc")
    assert filtered.status_code == 200
    assert filtered.json()["query"]["lanes"] == ["inbox", "ongoing"]


def test_m4_invalid_query_values_are_a_real_400(isolated_service):
    client = TestClient(standalone.create_app())
    for bad in ("?view=nope", "?sort=nope", "?direction=sideways", "?lane=nope",
                "?lifecycle=LS-99", "?attention=nope", "?page=0", "?page_size=0",
                "?page_size=999"):
        res = client.get(standalone.API_PREFIX + "/projects" + bad)
        assert res.status_code == 400, (bad, res.status_code)


def test_m4_pane_is_an_additive_query_on_the_existing_detail_route(isolated_service):
    client = TestClient(standalone.create_app())
    # the isolated service starts empty; one explicit scan seeds the registry
    assert client.post(standalone.API_PREFIX + "/scan").status_code == 200
    listing = client.get(standalone.API_PREFIX + "/projects?view=all").json()
    pid = sorted(c["project_id"] for c in listing["items"])[0]
    plain = client.get(standalone.API_PREFIX + "/projects/" + pid)
    assert plain.status_code == 200
    assert "session_pane" not in plain.json()
    refs = plain.json().get("source_sessions") or []
    assert refs, "the fixture must link at least one session"
    ref = "{}/{}".format(refs[0]["profile"], refs[0]["session_id"])
    pane = client.get(standalone.API_PREFIX + "/projects/" + pid + "?pane=" + ref)
    assert pane.status_code == 200
    payload = pane.json()["session_pane"]
    assert payload["profile"] == refs[0]["profile"]
    assert payload["session_id"] == refs[0]["session_id"]
    # a malformed pane reference is a 400, never a silent empty transcript
    assert client.get(standalone.API_PREFIX + "/projects/" + pid
                      + "?pane=not-a-ref").status_code == 400


def test_m4_browser_defaults_to_today_and_persists_the_query_in_the_url():
    src = _read(APP_JS)
    assert "view: 'today'" in src
    assert "replaceState" in src
    assert "parseUrlState" in src and "serializeUrlState" in src
    assert "queryString" in src
    # a reload must retain EVERY selected value of a repeatable filter: parse uses the
    # URLSearchParams.getAll-equivalent read, and the request replays repeated params.
    assert "params.getAll(key)" in src
    assert "for (const value of splitList(query[key])) params.append(key, value)" in src
    assert "key in REPEATABLE_FILTERS" in src
    # the browser renders all eight lanes and never sorts locally
    assert "BOARD_COLUMNS.map((col)" in src
    assert "SORT_FIELDS" in src
