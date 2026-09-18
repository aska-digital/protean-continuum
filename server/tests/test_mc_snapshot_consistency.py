"""MC-S1 — unified snapshot consistency + structured scan observability (MC-A1..MC-A5).

Behavioural tests over the real service and route surfaces with an isolated fixture home and a
temp registry. The frozen M4 surface guards live in tests/test_kanban_bounded.py and
tests/test_overview_modes.py; these tests ADD the mission-control acceptance and never touch
that baseline.

MC-L4: `data_as_of` has exactly ONE meaning on every read surface — the committed registry
snapshot timestamp. Wall clock is only ever published as `server_time`.
MC-L5: every read payload carries `scan_state` as an OBJECT, with honest nulls when no run
exists, and elapsed/mode/counts read from the existing `scan_run` row (no DDL).
"""
from __future__ import annotations

import json
import os
import time

import pytest

from continuum import service as service_mod
from continuum.config import load_config
from continuum.registry import SCHEMA_VERSION
from continuum.service import Service
from fixtures.make_fixture import build_fixture_tree

BUILD_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Every field MC-L5 requires on the structured scan_state object.
MC_L5_FIELDS = ("phase", "mode", "run_id", "started_at", "ended_at", "elapsed_seconds",
                "sessions_read", "messages_probed", "profiles_scanned", "last_error")


class _FarFutureClock:
    """A wall clock far from any committed snapshot.

    Only ``continuum.service``'s ``time`` seam is replaced, so nothing else in the process (or
    in pytest itself) sees a frozen clock.
    """

    def __init__(self, value: float = 4102444800.0):   # 2100-01-01
        self._value = value

    def time(self) -> float:
        return self._value


@pytest.fixture()
def home(tmp_path):
    return build_fixture_tree(str(tmp_path / "hermes_home"))


@pytest.fixture()
def svc(home):
    cfg = load_config(hermes_home=home)
    cfg.bundle["registry_path"] = os.path.join(home, "registry.db")
    service = Service(cfg)
    service.scan()
    return service


def _first_pid(svc) -> str:
    return sorted(c["project_id"] for c in svc.board(view="all", page_size=200)["items"])[0]


def _every_surface(svc, pid):
    """Every read surface MC-L4 names, plus the mission-control route projections."""
    return {
        "projects_legacy": svc.board(),
        "projects_playground": svc.board(view="all", page_size=200),
        "attention": svc.attention(),
        "attention_queue": svc.attention_queue(),
        "candidates": svc.inbox(),
        "recovery_inbox": svc.recovery_inbox(),
        "staleness": svc.staleness(),
        "staleness_view": svc.staleness_view(),
        "noise": svc.noise(),
        "scan_status": svc.scan_status(),
        "project_detail": svc.project_detail(pid),
    }


# --------------------------------------------------------------------------- MC-A1

def test_mc_a1_data_as_of_is_the_committed_snapshot_on_every_surface(svc):
    committed = svc.committed_snapshot()
    assert committed["run_id"] and committed["committed_at"] is not None
    detail = svc.project_detail(_first_pid(svc))
    for name, payload in _every_surface(svc, _first_pid(svc)).items():
        assert payload["data_as_of"] == committed["committed_at"], name
    # the card and the nested projections carry the SAME marker, never a wall clock
    for card in svc.board(view="all", page_size=200)["items"]:
        assert card["data_as_of"] == committed["committed_at"]
    for group in svc.attention()["groups"].values():
        for item in group:
            assert item["card"]["data_as_of"] == committed["committed_at"]
    for bucket in svc.staleness()["buckets"]:
        for card in bucket["items"]:
            assert card["data_as_of"] == committed["committed_at"]
    for card in svc.staleness()["parked"]:
        assert card["data_as_of"] == committed["committed_at"]
    assert detail["project"]["data_as_of"] == committed["committed_at"]
    # "now" travels in its own field and is never labelled data_as_of
    status = svc.scan_status()
    assert status["server_time"] is not None and status["server_time"] != status["data_as_of"]


def test_mc_a1_a_monkeypatched_wall_clock_changes_no_payload(svc, monkeypatch):
    """The kill site for MC-L4: the wall-clock call sites must no longer reach a payload."""
    committed = svc.committed_snapshot()["committed_at"]
    pid = _first_pid(svc)
    before = {name: payload["data_as_of"]
              for name, payload in _every_surface(svc, pid).items()}
    monkeypatch.setattr(service_mod, "time", _FarFutureClock())
    after = {name: payload["data_as_of"]
             for name, payload in _every_surface(svc, pid).items()}
    assert after == before
    assert set(after.values()) == {committed}
    assert svc.board(view="all", page_size=200)["items"][0]["data_as_of"] == committed


# --------------------------------------------------------------------------- MC-A2

def test_mc_a2_structured_scan_state_on_every_read_surface(svc):
    pid = _first_pid(svc)
    # Surfaces that carry the structured object directly.
    carriers = {
        "projects_legacy": svc.board(),
        "projects_playground": svc.board(view="all", page_size=200),
        "scan_status": svc.scan_status(),
        "attention_queue": svc.attention_queue(),
        "recovery_inbox": svc.recovery_inbox(),
        "staleness_view": svc.staleness_view(),
        "project_detail": svc.project_detail(pid),
        "overview": svc.overview(),
    }
    for name, payload in carriers.items():
        scan = payload.get("scan_state")
        assert isinstance(scan, dict), name
        for field in MC_L5_FIELDS:
            assert field in scan, (name, field)
        assert scan["phase"] in ("idle", "scanning", "error")
    # The four frozen M4 service methods keep their M4 key sets (unowned M4 guards), while the
    # SAME routes publish the mission-control object through the additive projections above.
    assert set(svc.attention().keys()) == {"groups", "attention_count", "data_as_of"}
    assert set(svc.inbox().keys()) == {"items", "total", "data_as_of"}
    assert set(svc.staleness().keys()) == {"buckets", "parked", "data_as_of"}
    assert set(svc.noise().keys()) == {"suppressions", "rules", "counts", "data_as_of"}
    assert svc.staleness()["buckets"][0]["items"] == svc.staleness()["buckets"][0]["items"]


def test_mc_a2_the_http_read_routes_publish_the_structured_object(home, monkeypatch):
    from fastapi.testclient import TestClient
    from dashboard import plugin_api, standalone

    cfg = load_config(hermes_home=home)
    cfg.bundle["registry_path"] = os.path.join(home, "registry.db")
    holder = {"svc": None}

    def factory():
        if holder["svc"] is None:
            service = Service(cfg)
            service.scan()
            holder["svc"] = service
        return holder["svc"]

    monkeypatch.setattr(plugin_api, "get_service", factory)
    client = TestClient(standalone.create_app())
    for route in ("/projects", "/projects?view=all", "/attention", "/candidates", "/staleness",
                  "/scan/status", "/events", "/overview"):
        payload = client.get(standalone.API_PREFIX + route).json()
        scan = payload.get("scan_state")
        assert isinstance(scan, dict), route
        for field in MC_L5_FIELDS:
            assert field in scan, (route, field)


def test_mc_a2_honest_nulls_when_no_run_exists(home):
    cfg = load_config(hermes_home=home)
    cfg.bundle["registry_path"] = os.path.join(home, "no-run-registry.db")
    fresh = Service(cfg)
    scan = fresh.board()["scan_state"]
    assert scan["phase"] == "idle"
    for field in ("mode", "run_id", "started_at", "ended_at", "elapsed_seconds",
                  "sessions_read", "messages_probed", "profiles_scanned", "last_error"):
        assert scan[field] is None, field


# --------------------------------------------------------------------------- MC-A3

def test_mc_a3_identical_snapshot_marker_on_projects_status_and_events(home, monkeypatch):
    from fastapi.testclient import TestClient
    from dashboard import plugin_api, standalone

    cfg = load_config(hermes_home=home)
    cfg.bundle["registry_path"] = os.path.join(home, "registry.db")
    holder = {"svc": None}

    def factory():
        if holder["svc"] is None:
            service = Service(cfg)
            service.scan()
            holder["svc"] = service
        return holder["svc"]

    monkeypatch.setattr(plugin_api, "get_service", factory)
    client = TestClient(standalone.create_app())
    legacy = client.get(standalone.API_PREFIX + "/projects").json()
    playground = client.get(standalone.API_PREFIX + "/projects?view=all").json()
    status = client.get(standalone.API_PREFIX + "/scan/status").json()
    events = client.get(standalone.API_PREFIX + "/events").json()
    markers = {
        (legacy["data_as_of"], legacy["scan_state"]["run_id"]),
        (playground["data_as_of"], playground["committed_snapshot"]["run_id"]),
        (status["data_as_of"], status["committed_snapshot"]["run_id"]),
        (events["data_as_of"], events["snapshot_id"]),
    }
    assert len(markers) == 1, markers
    (as_of, run_id), = markers
    assert as_of == status["committed_snapshot"]["committed_at"]
    assert run_id == status["committed_snapshot"]["run_id"]
    assert events["committed_at"] == as_of
    assert events["scan"] == status["phase"]
    assert isinstance(events["scan_state"], dict)


# --------------------------------------------------------------------------- MC-A4

def test_mc_a4_elapsed_seconds_equals_ended_minus_started_and_mode_is_locked(svc):
    svc.scan(full=True)
    full = svc.scan_state_object()
    assert full["mode"] == "full"
    assert full["ended_at"] is not None and full["started_at"] is not None
    assert full["elapsed_seconds"] == pytest.approx(full["ended_at"] - full["started_at"])
    assert full["elapsed_seconds"] >= 0
    svc.scan()
    incremental = svc.scan_state_object()
    assert incremental["mode"] == "incremental"
    assert incremental["elapsed_seconds"] == pytest.approx(
        incremental["ended_at"] - incremental["started_at"])
    # counts come from the stored scan_run row, never a computed guess
    row = svc.registry.conn.execute(
        "SELECT sessions_read, messages_probed, profiles_scanned FROM scan_run"
        " WHERE run_id=?", (incremental["run_id"],)).fetchone()
    assert incremental["sessions_read"] == row["sessions_read"]
    assert incremental["messages_probed"] == row["messages_probed"]
    assert incremental["profiles_scanned"] == row["profiles_scanned"]


def test_mc_a4_a_read_during_a_scan_is_observable_and_never_partial(home):
    from continuum import scanner

    cfg = load_config(hermes_home=home)
    cfg.bundle["registry_path"] = os.path.join(home, "registry.db")
    svc = Service(cfg)
    svc.scan(full=True)
    committed = svc.committed_snapshot()

    real_scan = scanner.scan
    observed = {}

    def spy(*args, **kwargs):
        observed["board"] = svc.board(view="all", page_size=200)
        observed["status"] = svc.scan_status()
        return real_scan(*args, **kwargs)

    scanner.scan = spy
    try:
        svc.scan()
    finally:
        scanner.scan = real_scan

    assert observed["board"]["scan_state"]["phase"] == "scanning"
    assert observed["status"]["scan_state"]["phase"] == "scanning"
    # the last COMMITTED snapshot is still what a reader sees
    assert observed["board"]["data_as_of"] == committed["committed_at"]
    assert observed["board"]["committed_snapshot"] == committed
    assert observed["board"]["scan_state"]["ended_at"] is None


# --------------------------------------------------------------------------- MC-A5

def test_mc_a5_legacy_envelope_compat_and_no_ddl(svc):
    legacy = svc.board()
    assert set(legacy.keys()) == {"items", "page", "total", "counts", "data_as_of", "scan_state"}
    assert isinstance(legacy["scan_state"], dict)
    assert legacy["scan_state"]["phase"] in ("idle", "scanning", "error")
    assert isinstance(legacy["counts"], dict) and isinstance(legacy["items"], list)
    playground = svc.board(view="all")
    assert {"page_size", "query", "playground", "counts"} <= set(playground.keys())
    assert SCHEMA_VERSION == 3
    src = open(os.path.join(BUILD_ROOT, "continuum", "service.py"), encoding="utf-8").read()
    for token in ("CREATE TABLE", "ALTER TABLE", "DROP TABLE", "CREATE INDEX",
                  "ADD COLUMN", "PRAGMA table_info"):
        assert token not in src, "service.py must not carry DDL: {!r}".format(token)
    with open(os.path.join(BUILD_ROOT, "continuum", "registry.py"), encoding="utf-8") as fh:
        registry_src = fh.read()
    assert "SCHEMA_VERSION = 3" in registry_src
