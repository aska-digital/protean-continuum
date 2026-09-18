"""AL-* acceptance tests for the action-log ARCHIVE surface, the audit sidecar and the wire.

Archive/unarchive/undo are file-level operations on the JSON ledger plus its append-only
``action_log_events.jsonl`` sidecar (AL-L2/AL-L5). The wire assertions (AL-A8/AL-A9/AL-A11/
AL-A14) drive the REAL FastAPI app through ``plugin_api``/``standalone`` exactly as a browser
would, over a throw-away fixture home — never the live profile tree.
"""
from __future__ import annotations

import hashlib
import json
import os

import pytest
from fastapi.testclient import TestClient

from continuum import action_log as al
from continuum.config import load_config
from continuum.service import Service
from dashboard import plugin_api, standalone
from fixtures.make_fixture import build_fixture_tree

BUILD_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIX = os.path.join(BUILD_ROOT, "fixtures", "action_log")
RECEIPTS = os.path.join(FIX, "receipts")
CLEAN_RECEIPTS = ("post-receipt.md", "merge-receipt.md", "close-receipt.md",
                  "retract-receipt.md", "edit-receipt.md")
ARCHIVE_ID = "al-inf-20260918-fixture-01"


def _sources():
    return [os.path.join(FIX, "INFLIGHT.md"), os.path.join(FIX, "DISPATCH-LEDGER.md")] + \
        [os.path.join(RECEIPTS, name) for name in CLEAN_RECEIPTS]


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Fixture home + temp registry + temp ledger, seeded from the FROZEN sources."""
    home = build_fixture_tree(str(tmp_path / "hermes_home"))
    cfg = load_config(hermes_home=home)
    cfg.bundle["registry_path"] = str(tmp_path / "registry.db")
    cfg.bundle["action_log_enabled"] = True
    cfg.bundle["action_log_ledger_path"] = str(tmp_path / "action_log.json")
    cfg.bundle["action_log_source_paths"] = _sources()
    report = al.sync(cfg, now=1000.0)
    assert report["row_count"] == 11, report["counts"]
    holder = {}

    def service():
        # Built lazily and only ever in the thread that first needs it: a SQLite connection
        # created in the test thread cannot be reused by TestClient's worker thread.
        if "svc" not in holder:
            holder["svc"] = Service(cfg)
        return holder["svc"]

    monkeypatch.setattr(plugin_api, "get_service", service)
    return {"cfg": cfg, "service": service, "report": report,
            "client": TestClient(standalone.create_app()), "tmp": tmp_path}


def _row(cfg, action_id):
    ledger = al.load_ledger(al.ledger_path_for(cfg))
    for row in ledger["items"]:
        if row["action_id"] == action_id:
            return row
    raise AssertionError("no such row: {}".format(action_id))


# --------------------------------------------------------------- AL-A5 archive

def test_al_a5_archive_sets_fields_and_appends_event(env):
    cfg = env["cfg"]
    result = al.archive(cfg, ARCHIVE_ID, actor="qa")
    assert result["ok"] is True
    audit_id = result["audit_id"]
    assert isinstance(audit_id, str) and len(audit_id) == 32
    int(audit_id, 16)  # hex, not a random label

    row = _row(cfg, ARCHIVE_ID)
    assert row["status"] == "archived"
    assert isinstance(row["archived_at"], float) and row["archived_at"] > 0
    assert row["archive_audit_id"] == audit_id

    events = al.read_events(al.events_path_for(cfg))
    assert len(events) == 1
    event = events[0]
    assert event["action"] == "archive_action_log"
    assert event["target_id"] == ARCHIVE_ID
    assert event["actor"] == "qa"
    assert isinstance(event["ts"], float)
    assert isinstance(event["rev"], int)
    before = json.loads(event["before_json"])
    after = json.loads(event["after_json"])
    assert before["status"] == "active" and before["archived_at"] is None
    assert after["status"] == "archived" and after["archive_audit_id"] == audit_id
    assert set(event.keys()) == {"event_id", "ts", "actor", "action", "target_id",
                                "before_json", "after_json", "rev"}


# --------------------------------------------------------------- AL-A6 unarchive

def test_al_a6_unarchive_clears_the_archive_fields(env):
    cfg = env["cfg"]
    al.archive(cfg, ARCHIVE_ID, actor="qa")
    result = al.unarchive(cfg, ARCHIVE_ID, actor="qa")
    assert result["ok"] is True
    row = _row(cfg, ARCHIVE_ID)
    assert row["status"] == "active"
    assert row["archived_at"] is None
    assert row["archive_audit_id"] is None
    events = al.read_events(al.events_path_for(cfg))
    assert [e["action"] for e in events] == ["archive_action_log", "unarchive_action_log"]


# --------------------------------------------------------------- AL-A7 undo

def test_al_a7_undo_restores_the_before_state_byte_identically(env):
    cfg = env["cfg"]
    original = json.dumps(_row(cfg, ARCHIVE_ID), sort_keys=True)
    archived = al.archive(cfg, ARCHIVE_ID, actor="qa")
    assert json.dumps(_row(cfg, ARCHIVE_ID), sort_keys=True) != original

    undo = al.undo(cfg, archived["audit_id"], actor="qa")
    assert undo["ok"] is True
    restored = json.dumps(_row(cfg, ARCHIVE_ID), sort_keys=True)
    event = al.read_events(al.events_path_for(cfg))[0]
    assert restored == json.dumps(json.loads(event["before_json"]), sort_keys=True)
    assert restored == original
    events = al.read_events(al.events_path_for(cfg))
    assert [e["action"] for e in events][-1] == "undo"
    assert events[-1]["target_id"] == ARCHIVE_ID


# --------------------------------------------------------------- AL-I2 double archive

def test_al_i2_double_archive_is_a_noop(env):
    cfg = env["cfg"]
    first = al.archive(cfg, ARCHIVE_ID, actor="qa")
    second = al.archive(cfg, ARCHIVE_ID, actor="qa")
    assert second["ok"] is True
    assert second["noop"] is True
    assert second["audit_id"] == first["audit_id"]
    assert len(al.read_events(al.events_path_for(cfg))) == 1
    # unarchiving an active row is equally a no-op
    al.unarchive(cfg, ARCHIVE_ID, actor="qa")
    third = al.unarchive(cfg, ARCHIVE_ID, actor="qa")
    assert third["noop"] is True
    assert len(al.read_events(al.events_path_for(cfg))) == 2


# --------------------------------------------------------------- AL-I3 append-only

def test_al_i3_events_grow_monotonically_and_are_never_rewritten(env):
    cfg = env["cfg"]
    path = al.events_path_for(cfg)
    snapshot = []
    operations = [("archive", ARCHIVE_ID), ("unarchive", ARCHIVE_ID),
                  ("archive", "al-fixture-dispatch-01"), ("unarchive", "al-fixture-dispatch-01"),
                  ("archive", "al-fixture-dispatch-02")]
    for index, (verb, action_id) in enumerate(operations, start=1):
        getattr(al, verb)(cfg, action_id, actor="qa")
        with open(path, "rb") as fh:
            raw = fh.read()
        lines = [ln for ln in raw.decode("utf-8").split("\n") if ln.strip()]
        assert len(lines) == index, "operation {} appended {} lines".format(index, len(lines))
        assert raw.startswith(b"".join(snapshot)), "an earlier event line was modified"
        snapshot = [ln.encode("utf-8") + b"\n" for ln in lines]
    events = al.read_events(path)
    assert [e["rev"] for e in events] == [1, 2, 3, 4, 5]
    assert len({e["event_id"] for e in events}) == 5


# --------------------------------------------------------------- AL-A8 wire disjointness

BOARD_VIEW_KEYS = {"committed_snapshot", "counts", "data_as_of", "items", "page", "page_size",
                   "playground", "query", "scan_state", "server_time", "snapshot_id",
                   "task_home", "total"}
BOARD_LEGACY_KEYS = {"counts", "data_as_of", "items", "page", "scan_state", "total"}
#: Fields the EXISTING board envelope has always carried as live clock readings. They move with
#: the wall clock between two calls of the same view; nothing else may move (AL-A11).
VOLATILE = ("data_as_of", "server_time", "age_seconds")


def _stable(payload):
    def scrub(value, key=None):
        if isinstance(value, dict):
            return {k: scrub(v, k) for k, v in value.items()}
        if isinstance(value, list):
            return [scrub(v) for v in value]
        if key in VOLATILE and isinstance(value, (int, float)):
            return 0
        return value
    return json.dumps(scrub(payload), sort_keys=True)


def test_al_a8_action_log_and_board_envelopes_are_disjoint(env):
    svc = env["service"]()
    actions = svc.board(view="action_log")
    today = svc.board(view="today")
    legacy = svc.board()
    assert set(actions.keys()) == {"view", "counts", "actions", "page", "page_size", "has_more"}
    assert "actions" in actions and "items" not in actions
    assert set(today.keys()) == BOARD_VIEW_KEYS
    assert set(legacy.keys()) == BOARD_LEGACY_KEYS
    assert "actions" not in today and "actions" not in legacy
    assert actions["view"] == "action_log"
    assert set(actions["counts"]["by_kind"].keys()) == set(al.KINDS)
    assert set(actions["counts"]["by_operator"].keys()) == set(al.OPERATOR_FLAGS)
    assert actions["counts"]["total"] == 11
    # The card key set is guarded by the existing frozen guards (CP-T13 / KB-N1); what this
    # test owns is that the two ENVELOPES never share an entity key: actions vs items.
    assert "kind" not in today and "kind" not in legacy


def test_al_a8_action_log_view_rejects_board_filters(env):
    svc = env["service"]()
    with pytest.raises(ValueError):
        svc.board(view="action_log", lane="inbox")
    with pytest.raises(ValueError):
        svc.board(view="action_log", q="text")
    with pytest.raises(ValueError):
        svc.board(view="today", kind="dispatch")


def test_al_a8_action_log_view_honours_the_enable_switch(env, tmp_path):
    cfg = env["cfg"]
    cfg.bundle["action_log_enabled"] = False
    try:
        with pytest.raises(ValueError):
            Service(cfg).board(view="action_log")
    finally:
        cfg.bundle["action_log_enabled"] = True


# --------------------------------------------------------------- AL-A9 / AL-A10 over HTTP

def test_al_a9_http_filters_and_400(env):
    client = env["client"]
    ok = client.get(standalone.API_PREFIX + "/projects",
                    params={"view": "action_log", "kind": "post"})
    assert ok.status_code == 200
    body = ok.json()
    assert [row["kind"] for row in body["actions"]] == ["post"]
    assert "projects" not in body
    bad = client.get(standalone.API_PREFIX + "/projects",
                     params={"view": "action_log", "kind": "nonsense"})
    assert bad.status_code == 400
    bad_page = client.get(standalone.API_PREFIX + "/projects",
                          params={"view": "action_log", "page": "0"})
    assert bad_page.status_code == 400
    for key, value in (("operator", "robot"), ("status", "gone"), ("date_from", "soon")):
        resp = client.get(standalone.API_PREFIX + "/projects",
                          params={"view": "action_log", key: value})
        assert resp.status_code == 400, key


def test_al_a10_archived_section(env):
    cfg = env["cfg"]
    svc = env["service"]()
    al.archive(cfg, ARCHIVE_ID, actor="qa")
    default = svc.board(view="action_log")
    assert all(row["status"] == "active" for row in default["actions"])
    assert default["counts"]["active"] == 10
    assert default["counts"]["archived"] == 1
    assert default["counts"]["total"] == 11
    # Orda 2026-09-17: the DEFAULT scope is external, and ARCHIVE_ID is a dispatch row —
    # the archived browse under the default scope honestly shows nothing of it. scope=all
    # preserves AL-L14's archived section behaviour exactly.
    assert svc.board(view="action_log", status=["archived"])["actions"] == []
    archived = svc.board(view="action_log", status=["archived"], scope="all")
    assert [row["action_id"] for row in archived["actions"]] == [ARCHIVE_ID]
    active = svc.board(view="action_log", status=["active"], scope="all")
    assert len(active["actions"]) == 10
    assert len(active["actions"]) + len(archived["actions"]) == default["counts"]["total"]


# --------------------------------------------------------------- AL-A11 no pollution

def test_al_a11_board_views_are_byte_identical_across_a_sync(env):
    svc, cfg = env["service"](), env["cfg"]
    before = {view: _stable(svc.board(view=view)) for view in ("today", "all")}
    before_legacy = _stable(svc.board())
    before_inbox = _stable(svc.inbox())
    before_items = {view: json.dumps(svc.board(view=view)["items"], sort_keys=True)
                    for view in ("today", "all")}
    before_total = svc.board(view="all")["counts"]["total"]

    report = al.sync(cfg, now=2000.0)
    assert report["exit_code"] in (al.EXIT_OK, al.EXIT_NO_CHANGES, al.EXIT_PARSE_DEGRADED)

    for view in ("today", "all"):
        assert _stable(svc.board(view=view)) == before[view]
        assert json.dumps(svc.board(view=view)["items"], sort_keys=True) == before_items[view]
        assert svc.board(view=view)["counts"]["total"] == before_total
    assert _stable(svc.board()) == before_legacy
    assert _stable(svc.inbox()) == before_inbox


def test_al_a14_legacy_board_envelope_has_no_action_key(env):
    client = env["client"]
    legacy = client.get(standalone.API_PREFIX + "/projects")
    assert legacy.status_code == 200
    body = legacy.json()
    assert set(body.keys()) == BOARD_LEGACY_KEYS
    assert "actions" not in body
    today = client.get(standalone.API_PREFIX + "/projects", params={"view": "today"}).json()
    assert set(today.keys()) == BOARD_VIEW_KEYS
    assert "actions" not in today


# --------------------------------------------------------------- routes (POST archive/unarchive)

def test_post_archive_and_unarchive_routes(env):
    client = env["client"]
    base = "{}/action-log/{}".format(standalone.API_PREFIX, ARCHIVE_ID)
    resp = client.post(base + "/archive", json={"actor": "dashboard"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True and body["action"] == "archive_action_log"
    assert body["target_id"] == ARCHIVE_ID and "audit_id" in body
    assert _row(env["cfg"], ARCHIVE_ID)["status"] == "archived"

    back = client.post(base + "/unarchive", json={"actor": "dashboard"})
    assert back.status_code == 200, back.text
    assert back.json()["action"] == "unarchive_action_log"
    assert _row(env["cfg"], ARCHIVE_ID)["status"] == "active"

    unknown = client.post("{}/action-log/nope/archive".format(standalone.API_PREFIX), json={})
    assert unknown.status_code == 400


def test_round_trip_leaves_the_row_active_with_two_events(env):
    cfg = env["cfg"]
    original = json.dumps(_row(cfg, ARCHIVE_ID), sort_keys=True)
    al.archive(cfg, ARCHIVE_ID, actor="qa")
    al.unarchive(cfg, ARCHIVE_ID, actor="qa")
    assert json.dumps(_row(cfg, ARCHIVE_ID), sort_keys=True) == original
    events = al.read_events(al.events_path_for(cfg))
    assert len(events) == 2
    assert all(row["status"] == "active" for row in al.load_ledger(
        al.ledger_path_for(cfg))["items"])
