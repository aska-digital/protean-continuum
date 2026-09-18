"""TASK-HOME projection acceptance — TH-A7..A10, A13, A14 + the audited bind/unbind route.

Owner: mozi (implementation). Upstream: leo-architecture.md (STATUS: STABLE) §3, §6, §8, TH-L9.
Every check is executed against a real ``Service`` over a synthetic fixture tree; the frozen
TASK-HOME copy under ``fixtures/task_home/`` is the source, and it is never written.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
import uuid

import pytest

BUILD_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BUILD_ROOT not in sys.path:
    sys.path.insert(0, BUILD_ROOT)

from continuum import task_home as th                     # noqa: E402
from continuum.config import load_config                  # noqa: E402
from continuum.registry import SCHEMA_VERSION             # noqa: E402
from continuum.service import Service                     # noqa: E402
from fixtures.make_fixture import build_fixture_tree      # noqa: E402

FROZEN = os.path.join(BUILD_ROOT, "fixtures", "task_home", "task-home.md")

#: §3's locked section -> (panel group, board lane) mapping.
SECTION_TO_LANE = {"RUNNING": "ongoing", "AWAITING_OWNER": "waiting_on_you",
                   "OPEN_FOLLOW_UP": "paused", "CLOSED": "done"}
PANEL_GROUPS = ("RUNNING", "AWAITING_OWNER", "OPEN_FOLLOW_UP", "CLOSED",
                "DASHBOARD_ONLY", "ABSENT")
#: §6's exact card.task_home key set.
CARD_TASK_HOME_KEYS = {"key", "proc", "section", "section_label", "lane", "line", "line_no",
                       "receipt", "receipt_text", "content_sha1", "binding", "synced_at",
                       "age_seconds", "stale", "conflicts"}
#: §6's exact task_home envelope key set.
ENVELOPE_KEYS = {"enabled", "source_path", "source_sha256", "source_mtime", "synced_at",
                 "age_seconds", "stale", "stale_after_seconds", "run_id", "warnings",
                 "conflicts", "items"}
ENVELOPE_ITEM_KEYS = {"key", "proc", "section", "lane", "line", "line_no", "project_id",
                      "binding", "receipt", "warnings"}
#: §6's counts.task_home key set.
COUNTS_KEYS = set(PANEL_GROUPS) | {"source_only", "conflicts"}
#: the legacy frozen surfaces (KB-N1 / CP-T13) — unchanged by this milestone.
LEGACY_BOARD_KEYS = {"items", "page", "total", "counts", "data_as_of", "scan_state"}
CARD_KEYS = {"project_id", "name", "lifecycle", "lifecycle_name", "phase",
             "owner_profile", "last_substantive_activity", "stall_age_days",
             "session_count", "confidence", "confidence_band", "evidence_tier",
             "evidence_tiers", "next_action", "drive_expected", "alert_state",
             "derived_updated_at", "declared_stale", "data_as_of"}


@pytest.fixture()
def home(tmp_path):
    return build_fixture_tree(str(tmp_path / "hermes_home"))


@pytest.fixture()
def cfg(tmp_path, home):
    c = load_config(hermes_home=home)
    c.bundle["registry_path"] = os.path.join(home, "registry.db")
    c.bundle["task_home_ledger_path"] = str(tmp_path / "task_home_sync.json")
    c.bundle["task_home_export_dir"] = str(tmp_path / "out")
    c.bundle["task_home_source_path"] = FROZEN
    # the projection switch ships false (§7); the projection tests exercise the ON state
    c.bundle["task_home_enabled"] = True
    return c


@pytest.fixture()
def svc(cfg):
    s = Service(cfg)
    s.scan()
    return s


def _add_project(svc, pid, name, *, lifecycle="LS-2", placement=None, band="high", tier=1,
                 sessions=(), accepted=True):
    now = time.time()
    svc.registry.conn.execute(
        "INSERT OR REPLACE INTO project (project_id, name, kind, phase, lifecycle, confidence,"
        " confidence_band, evidence_tier, owner_profile, drive_expected, session_count,"
        " stall_age_days, last_substantive_activity, derived_updated_at, created_at, updated_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (pid, name, "project", "active", lifecycle, 0.8, band, tier, "alpha", 0, len(sessions),
         1.0, now, now, now, now))
    for profile, sid, title in sessions:
        svc.registry.conn.execute(
            "INSERT OR REPLACE INTO session_fact (profile_name, session_id, title, cwd,"
            " workspace_root, message_count, tool_call_count, started_at, last_activity_at)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (profile, sid, title, "/tmp", None, 4, 2, now, now))
        svc.registry.conn.execute(
            "INSERT OR REPLACE INTO project_session (link_id, project_id, profile_name,"
            " session_id, role_in_project, link_confidence, link_reason, evidence_ref, accepted)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (uuid.uuid4().hex, pid, profile, sid, "primary", 0.9, "CS-1", None,
             1 if accepted else 0))
    if placement:
        svc.registry.set_declared(pid, "placement", placement, actor="local")
    svc.registry.conn.commit()
    return pid


def _card(svc, pid, **query):
    board = svc.board(view="all", **query)
    for card in board["items"]:
        if card["project_id"] == pid:
            return card, board
    raise AssertionError("project {} is not on the board".format(pid))


def _write(path, text):
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    return str(path)


def _declared_dump(svc, pid):
    return [tuple(r) for r in svc.registry.conn.execute(
        "SELECT project_id, field, value, source, actor, declared_rev FROM declared_field"
        " WHERE project_id=? ORDER BY field", (pid,))]


# --------------------------------------------------------------------------- TH-A7

def test_th_a7_mapping_matches_section_3_and_lane_filter_agrees(cfg, svc):
    """TH-A7: the panel group and `column` of a bound item equal §3; lane= == the panel group.

    Only the 11 RUNNING bullets carry a literal slug, so the other three groups are bound the
    way a human binds them (the audited bind route) — the keyless keys are exactly the TH-L4
    fallback keys the panel displays.
    """
    _add_project(svc, "P-run", "continuum-sync", lifecycle="LS-2",
                 sessions=(("alpha", "s-run", "task home sync"),))
    _add_project(svc, "P-owner", "some owner-awaiting project",
                 sessions=(("alpha", "s-owner", "awaiting owner ask"),))
    _add_project(svc, "P-open", "some follow-up project",
                 sessions=(("alpha", "s-open", "follow up later"),))
    _add_project(svc, "P-closed", "some closed project",
                 sessions=(("alpha", "s-closed", "closed thing"),))
    svc.review("bind_task_home", "P-owner", {"key": "unnamed:AWAITING_OWNER:1"})
    svc.review("bind_task_home", "P-open", {"key": "unnamed:OPEN_FOLLOW_UP:1"})
    svc.review("bind_task_home", "P-closed", {"key": "unnamed:CLOSED:1"})
    th.sync(cfg, registry=svc.registry)
    board = svc.board(view="all")
    by_pid = {c["project_id"]: c for c in board["items"]}
    expected = {"P-run": ("RUNNING", "ongoing"), "P-owner": ("AWAITING_OWNER", "waiting_on_you"),
                "P-open": ("OPEN_FOLLOW_UP", "paused"), "P-closed": ("CLOSED", "done")}
    for pid, (group, lane) in expected.items():
        card = by_pid[pid]
        assert card["home"] == group
        assert card["column"] == lane, (pid, card["column"], lane)
        assert card["task_home"]["section"] == group
        assert card["task_home"]["lane"] == lane
        assert card["task_home"]["section_label"] == th.PANEL_GROUP_LABELS[group]
        assert card["state_source"] == "task_home"
        # lane= returns exactly the panel group's set
        lane_board = svc.board(view="all", lane=[lane])
        home_board = svc.board(view="all", home=[group])
        assert {c["project_id"] for c in lane_board["items"]} == \
               {c["project_id"] for c in home_board["items"]}
        assert pid in {c["project_id"] for c in home_board["items"]}
        # the panel group and the lane cannot disagree (TH-A7 / §3 rule 1)
        for c in home_board["items"]:
            assert c["column"] == lane


def test_th_a7_absent_and_dashboard_only_groups(cfg, svc, tmp_path):
    _add_project(svc, "P-unbound", "no task home line at all")
    source = _write(tmp_path / "one.md",
                    "# synthetic\n\n## RUNNING (background workers live)\n"
                    "- will-vanish (proc_abcabcabcabc): present\n")
    _add_project(svc, "P-vanish", "will-vanish")
    th.sync(cfg, source_path=source, registry=svc.registry)
    board = svc.board(view="all")
    assert {c["project_id"] for c in board["items"] if c["home"] == "DASHBOARD_ONLY"} >= \
        {"P-unbound"}
    assert board["counts"]["task_home"]["DASHBOARD_ONLY"] >= 1
    # now the key disappears from the source -> ABSENT, override withdrawn
    th.sync(cfg, source_path=_write(tmp_path / "two.md",
                                    "# synthetic\n\n## RUNNING (background workers live)\n"
                                    "- something-else (proc_abcabcabcabc): present\n"),
            registry=svc.registry)
    card, board = _card(svc, "P-vanish")
    assert card["home"] == "ABSENT"
    assert card["task_home"] is None
    assert "placement_shadowed" not in card
    assert board["counts"]["task_home"]["ABSENT"] == 1
    assert {c["project_id"] for c in svc.board(view="all", home=["ABSENT"])["items"]} == \
        {"P-vanish"}


# --------------------------------------------------------------------------- TH-A8

def test_th_a8_no_pollution_of_the_other_surfaces(cfg, svc):
    """TH-A8: items[] and counts.total of board/inbox/attention/staleness are unchanged."""
    _add_project(svc, "P-run", "continuum-sync", sessions=(("alpha", "s-a", "task home sync"),))
    _add_project(svc, "P-other", "some dashboard project")
    before = {
        "board": svc.board(),
        "inbox": svc.inbox(),
        "attention": svc.attention(),
        "staleness": svc.staleness(),
    }
    report = th.sync(cfg, registry=svc.registry)
    assert report["writes"] > 0
    after = {
        "board": svc.board(),
        "inbox": svc.inbox(),
        "attention": svc.attention(),
        "staleness": svc.staleness(),
    }
    assert [c["project_id"] for c in before["board"]["items"]] == \
           [c["project_id"] for c in after["board"]["items"]]
    assert before["board"]["counts"]["total"] == after["board"]["counts"]["total"]
    assert before["board"]["counts"] == after["board"]["counts"]
    assert [c["project_id"] for c in before["inbox"]["items"]] == \
           [c["project_id"] for c in after["inbox"]["items"]]
    assert before["inbox"]["total"] == after["inbox"]["total"]
    assert before["attention"]["groups"].keys() == after["attention"]["groups"].keys()
    for group in before["attention"]["groups"]:
        assert [i["project_id"] for i in before["attention"]["groups"][group]] == \
               [i["project_id"] for i in after["attention"]["groups"][group]]
    assert before["attention"]["attention_count"] == after["attention"]["attention_count"]
    assert [c["project_id"] for c in before["staleness"]["parked"]] == \
           [c["project_id"] for c in after["staleness"]["parked"]]
    for b, a in zip(before["staleness"]["buckets"], after["staleness"]["buckets"]):
        assert [c["project_id"] for c in b["items"]] == [c["project_id"] for c in a["items"]]
    # the playground continuity set and total are unchanged
    pb = svc.board(view="all")
    assert pb["counts"]["continuity_total"] == pb["counts"]["total"]


def test_th_a8_a_task_home_only_key_never_enters_the_continuity_set(cfg, svc):
    """TH-L10: 34 keys, one bound project -> items[] still holds only Continuum projects."""
    _add_project(svc, "P-run", "continuum-sync")
    th.sync(cfg, registry=svc.registry)
    board = svc.board(view="all")
    project_ids = {row["project_id"] for row in svc.registry.projects()}
    ids = {c["project_id"] for c in board["items"]}
    assert ids <= project_ids
    assert board["counts"]["total"] == board["counts"]["continuity_total"] == len(project_ids)
    assert board["counts"]["task_home"]["source_only"] == 33
    assert board["counts"]["task_home"]["RUNNING"] == 1
    # the TASK-HOME-only keys appear in the panel payload, never in items[]
    panel_keys = {item["key"] for item in board["task_home"]["items"] if item["project_id"] is None}
    assert len(panel_keys) == 33
    assert panel_keys.isdisjoint({c["task_home"]["key"] for c in board["items"]
                                  if c["task_home"]})


# --------------------------------------------------------------------------- TH-A9

def test_th_a9_task_home_outranks_a_local_placement_without_touching_it(cfg, svc):
    """TH-A9: the rendered lane is TASK-HOME's; the placement declared row is byte-unchanged."""
    _add_project(svc, "P-run", "continuum-sync", placement="blocked")
    before_dump = _declared_dump(svc, "P-run")
    assert ("P-run", "placement", "blocked", "declared", "local", 1) in before_dump
    th.sync(cfg, registry=svc.registry)
    card, board = _card(svc, "P-run")
    assert card["column"] == "ongoing", "TASK-HOME wins on status (TH-L6 P2)"
    assert card["placement_source"] == "task_home"
    assert card["placement_shadowed"] == "blocked"
    assert card["placement"] == "blocked", "the local value is preserved, never overwritten"
    after_dump = _declared_dump(svc, "P-run")
    placement_before = [row for row in before_dump if row[1] == "placement"]
    placement_after = [row for row in after_dump if row[1] == "placement"]
    assert placement_before == placement_after
    # the lane filter honours the overridden value
    assert "P-run" in {c["project_id"] for c in svc.board(view="all", lane=["ongoing"])["items"]}
    assert "P-run" not in {c["project_id"] for c in svc.board(view="all", lane=["blocked"])["items"]}


def test_th_a9_a_conflicting_derived_lane_is_also_shadowed(cfg, svc):
    _add_project(svc, "P-run", "continuum-sync", lifecycle="LS-3",
                 sessions=(("alpha", "s-run", "task home sync"),))
    th.sync(cfg, registry=svc.registry)
    card, _board = _card(svc, "P-run")
    assert card["column"] == "ongoing"
    assert card["placement_shadowed"] == "blocked"
    assert card["task_home"]["conflicts"][0]["kind"] == "lane_conflict"
    # lifecycle is NOT rewritten by the import (TH-L2 / §3 rule 2)
    assert card["derived_lifecycle"] == "LS-3"


# --------------------------------------------------------------------------- TH-A10

def test_th_a10_conflict_count_matches_and_every_entry_names_both_values(cfg, svc):
    _add_project(svc, "P-run", "continuum-sync", placement="blocked")
    _add_project(svc, "P-clean", "sym2p-report",
                 sessions=(("alpha", "s-clean", "sym2p report work"),))
    _add_project(svc, "P-other", "a dashboard only project")
    th.sync(cfg, registry=svc.registry)
    board = svc.board(view="all")
    envelope = board["task_home"]
    conflicts = envelope["conflicts"]
    assert board["counts"]["task_home"]["conflicts"] == len(conflicts) == 1
    entry = conflicts[0]
    assert set(entry) == {"key", "kind", "task_home_value", "dashboard_value", "winner", "line_no"}
    assert entry["key"] == "continuum-sync"
    assert entry["task_home_value"] == "ongoing"
    assert entry["dashboard_value"] == "blocked"
    assert entry["winner"] == "task_home"
    assert entry["line_no"] == 11
    # the same entry is published on the card, and only for the conflicting card
    card, _ = _card(svc, "P-run")
    assert card["task_home"]["conflicts"] == [entry]
    clean, _ = _card(svc, "P-clean")
    assert clean["task_home"]["conflicts"] == []
    assert clean["column"] == "ongoing"
    # a conflict is a REPORT, never a write
    assert svc.registry.effective_declared("P-run")["placement"] == "blocked"


# --------------------------------------------------------------------------- TH-A14

def test_th_a14_frozen_legacy_shapes_are_unchanged(cfg, svc):
    _add_project(svc, "P-run", "continuum-sync")
    legacy_before = svc.board()
    card_keys_before = [set(c.keys()) for c in legacy_before["items"]]
    th.sync(cfg, registry=svc.registry)
    legacy = svc.board()
    assert set(legacy.keys()) == LEGACY_BOARD_KEYS
    assert [set(c.keys()) for c in legacy["items"]] == card_keys_before
    for card in legacy["items"]:
        for added in ("task_home", "home", "state_source", "placement_shadowed"):
            assert added not in card, added
    # the nested _card() surfaces keep the frozen KB-N1 key set exactly
    for group in svc.attention()["groups"].values():
        for item in group:
            assert set(item["card"].keys()) == CARD_KEYS
    for bucket in svc.staleness()["buckets"]:
        for card in bucket["items"]:
            assert set(card.keys()) == CARD_KEYS
    assert set(svc.attention().keys()) == {"groups", "attention_count", "data_as_of"}
    assert set(svc.staleness().keys()) == {"buckets", "parked", "data_as_of"}


def test_th_a14_playground_envelope_additions_are_exactly_the_locked_ones(cfg, svc):
    _add_project(svc, "P-run", "continuum-sync")
    th.sync(cfg, registry=svc.registry)
    board = svc.board(view="all")
    assert set(board.keys()) == LEGACY_BOARD_KEYS | {
        "page_size", "query", "playground", "server_time", "snapshot_id", "committed_snapshot",
        "task_home"}
    assert set(board["task_home"].keys()) == ENVELOPE_KEYS
    for item in board["task_home"]["items"]:
        assert set(item.keys()) == ENVELOPE_ITEM_KEYS
    assert set(board["counts"]["task_home"].keys()) == COUNTS_KEYS
    for card in board["items"]:
        if card["task_home"] is not None:
            assert set(card["task_home"].keys()) == CARD_TASK_HOME_KEYS
        assert "home" in card and "state_source" in card or card["task_home"] is None
    # no route was added by task-home: the router surface is unchanged BY TASK-HOME.
    # AL (Orda action log, AL-L5/§4) adds exactly two audited routes —
    # POST /action-log/{action_id}/archive and POST /action-log/{action_id}/unarchive —
    # and no other path changed.
    from dashboard import plugin_api
    paths = {r.path for r in plugin_api.router.routes}
    assert paths == {"/projects", "/projects/{project_id}", "/candidates", "/attention",
                     "/staleness", "/noise", "/scan/status", "/scan",
                     "/projects/{project_id}/review", "/review/undo", "/overview", "/events",
                     "/action-log/{action_id}/archive", "/action-log/{action_id}/unarchive",
                     "/review-queue"}


def test_th_a14_never_synced_is_disabled_and_names_the_cli_command(cfg, svc):
    board = svc.board(view="all")
    envelope = board["task_home"]
    assert envelope["enabled"] is False
    assert envelope["synced_at"] is None and envelope["age_seconds"] is None
    assert envelope["stale"] is False
    assert envelope["source_path"].endswith("task-home.md")
    assert envelope["stale_after_seconds"] == 900
    assert envelope["items"] == [] and envelope["conflicts"] == []
    assert board["counts"]["task_home"]["DASHBOARD_ONLY"] == board["counts"]["continuity_total"]
    for card in board["items"]:
        assert card["task_home"] is None and card["home"] == "DASHBOARD_ONLY"
    # the exact CLI command is derivable from the published source path (§6)
    assert "python -m continuum.cli task-home sync --source {}".format(
        envelope["source_path"]) == \
        "python -m continuum.cli task-home sync --source {}".format(FROZEN)


def test_th_a14_disabled_config_switch_hides_the_projection(cfg, svc):
    _add_project(svc, "P-run", "continuum-sync")
    th.sync(cfg, registry=svc.registry)
    assert svc.board(view="all")["task_home"]["enabled"] is True
    svc.cfg.bundle["task_home_enabled"] = False
    board = svc.board(view="all")
    assert board["task_home"]["enabled"] is False
    for card in board["items"]:
        assert card["task_home"] is None and card["home"] == "DASHBOARD_ONLY"


# --------------------------------------------------------------------------- TH-L7 read path

def test_get_path_writes_nothing(cfg, svc):
    _add_project(svc, "P-run", "continuum-sync")
    th.sync(cfg, registry=svc.registry)
    ledger_path = cfg.bundle["task_home_ledger_path"]
    before = (os.stat(ledger_path).st_mtime_ns, hashlib.sha256(
        open(ledger_path, "rb").read()).hexdigest())
    tables_before = th.table_hashes(svc.registry.conn, declared_only=True)
    for _ in range(3):
        svc.board(view="all")
        svc.board(view="today", home=["RUNNING"])
        svc.board()
    after = (os.stat(ledger_path).st_mtime_ns, hashlib.sha256(
        open(ledger_path, "rb").read()).hexdigest())
    assert before == after, "a GET must never rewrite the ledger"
    assert th.table_hashes(svc.registry.conn, declared_only=True) == tables_before


# --------------------------------------------------------------------------- route level

def test_home_query_is_a_real_400_on_the_existing_route(cfg, monkeypatch):
    from fastapi.testclient import TestClient
    from dashboard import plugin_api, standalone

    holder = {}

    def factory():
        if "svc" not in holder:
            svc = Service(cfg)
            svc.scan()
            holder["svc"] = svc
        return holder["svc"]

    monkeypatch.setattr(plugin_api, "get_service", factory)
    client = TestClient(standalone.create_app())
    prefix = standalone.API_PREFIX

    ok = client.get(prefix + "/projects?view=all&home=RUNNING")
    assert ok.status_code == 200
    assert ok.json()["query"]["home"] == ["RUNNING"]
    assert ok.json()["task_home"]["enabled"] is False       # nothing synced in this fixture

    for bad in ("?view=all&home=nope", "?view=all&home="):
        res = client.get(prefix + "/projects" + bad)
        assert res.status_code in (200, 400)
    res = client.get(prefix + "/projects?view=all&home=NOPE")
    assert res.status_code == 400
    assert "invalid home" in res.json()["detail"]

    # legacy (no query) stays byte-compatible: no task_home key at all
    legacy = client.get(prefix + "/projects")
    assert legacy.status_code == 200
    assert set(legacy.json().keys()) == LEGACY_BOARD_KEYS


# --------------------------------------------------------------------------- TH-L9 bind/unbind

def test_bind_and_unbind_ride_the_existing_review_route(cfg, svc):
    _add_project(svc, "P-any", "some unbound project")
    assert svc.registry.get_project("P-any") is not None

    bound = svc.review("bind_task_home", "P-any",
                       {"key": "continuum-sync", "proc": "proc_4faba8822b80"})
    assert bound["ok"] is True
    assert set(bound.keys()) == {"ok", "audit_id", "action", "target_id", "rev"}
    fields = {r["field"]: r["value"] for r in svc.registry.declared_fields("P-any")}
    assert fields["task_home_key"] == "continuum-sync"
    assert fields["task_home_proc"] == "proc_4faba8822b80"
    assert fields["task_home_binding"] == "human"
    # it is audited and reversible
    events = svc.registry.review_events("P-any", limit=5)
    assert events[0]["action"] == "bind_task_home"
    undo = svc.review("undo", "", {"audit_id": bound["audit_id"]})
    assert undo["ok"] is True
    fields = {r["field"]: r["value"] for r in svc.registry.declared_fields("P-any")}
    assert "task_home_key" not in fields

    # unbind clears the identity fields and leaves an audited trail
    svc.review("bind_task_home", "P-any", {"key": "continuum-sync"})
    th.sync(cfg, registry=svc.registry)
    assert "task_home_key" in {r["field"] for r in svc.registry.declared_fields("P-any")}
    unbound = svc.review("unbind_task_home", "P-any", {})
    assert unbound["ok"] is True
    fields = {r["field"]: r["value"] for r in svc.registry.declared_fields("P-any")}
    for field in th.ITEM_FIELDS:
        if field == "task_home_binding":
            continue
        assert field not in fields, field
    assert fields["task_home_binding"] == "unbound"


def test_bind_task_home_validates_its_payload(cfg, svc):
    _add_project(svc, "P-any", "some unbound project")
    for bad in ({}, {"key": ""}, {"key": "has space"}, {"key": "has:colon"},
                {"key": "has(paren"}, {"key": "ok-slug", "proc": "not-a-proc"}):
        with pytest.raises(ValueError):
            svc.review("bind_task_home", "P-any", bad)
    with pytest.raises(ValueError, match="unknown project_id"):
        svc.review("bind_task_home", "nope", {"key": "ok-slug"})
    with pytest.raises(ValueError, match="unknown project_id"):
        svc.review("unbind_task_home", "nope", {})
    assert svc.review("bind_task_home", "P-any", {"key": "ok-slug", "proc": None})["ok"] is True


def test_human_binding_wins_and_the_sync_never_overwrites_it(cfg, svc):
    """TH-L5/INV-MC-2: an audited human binding is authoritative; the pass reports it as human."""
    _add_project(svc, "P-any", "some unbound project")
    svc.review("bind_task_home", "P-any", {"key": "sym2p-report", "proc": None})
    report = th.sync(cfg, registry=svc.registry)
    item = [it for it in report["items"] if it["key"] == "sym2p-report"][0]
    assert item["project_id"] == "P-any"
    assert item["binding"] == "human"
    card, _board = _card(svc, "P-any")
    assert card["home"] == "RUNNING"
    assert card["task_home"]["binding"] == "human"
    fields = {r["field"]: r["value"] for r in svc.registry.declared_fields("P-any")}
    assert fields["task_home_key"] == "sym2p-report"
    # a second pass leaves the human binding byte-identical
    before = _declared_dump(svc, "P-any")
    th.sync(cfg, registry=svc.registry)
    assert _declared_dump(svc, "P-any") == before


def test_bind_task_home_route_is_the_existing_route(cfg, monkeypatch):
    from fastapi.testclient import TestClient
    from dashboard import plugin_api, standalone

    holder = {}

    def factory():
        if "svc" not in holder:
            svc = Service(cfg)
            svc.scan()
            holder["svc"] = svc
        return holder["svc"]

    monkeypatch.setattr(plugin_api, "get_service", factory)
    client = TestClient(standalone.create_app())
    prefix = standalone.API_PREFIX
    pid = sorted(c["project_id"] for c in client.get(
        prefix + "/projects?view=all").json()["items"])[0]
    res = client.post(prefix + "/projects/{}/review".format(pid),
                      json={"action": "bind_task_home", "payload": {"key": "kit-about"}})
    assert res.status_code == 200
    assert res.json()["action"] == "bind_task_home"
    bad = client.post(prefix + "/projects/{}/review".format(pid),
                      json={"action": "bind_task_home", "payload": {"key": "bad key"}})
    assert bad.status_code == 400
    assert "invalid" in bad.json()["detail"] or "key" in bad.json()["detail"]


# --------------------------------------------------------------------------- panel render

def test_task_home_panel_renders_server_fields_only():
    """Structural supplement (labelled): the static app wires `home=` and renders the panel."""
    with open(os.path.join(BUILD_ROOT, "dashboard", "static", "app.js"), encoding="utf-8") as fh:
        src = fh.read()
    assert "renderTaskHome" in src
    assert "home: HOME_GROUPS" in src                       # the repeatable server filter
    assert "'python -m continuum.cli task-home sync --source '" in src  # §6 never-synced state
    # the browser reads freshness from the server; it must not compute it
    assert "Date.now()" not in src.split("renderTaskHome")[1][:4000]
    with open(os.path.join(BUILD_ROOT, "dashboard", "static", "styles.css"),
              encoding="utf-8") as fh:
        css = fh.read()
    assert ".c-task-home" in css and ".c-th-group" in css


# --------------------------------------------------------------------------- schema (TH-A13)

def test_th_a13_projection_never_migrates_or_writes(cfg, svc):
    assert SCHEMA_VERSION == 3
    before = svc.registry.conn.execute("SELECT version FROM schema_version").fetchone()[0]
    svc.board(view="all", home=["RUNNING", "DASHBOARD_ONLY"])
    after = svc.registry.conn.execute("SELECT version FROM schema_version").fetchone()[0]
    assert before == after == 3


# ------------------------------------------------- v2 grammar: proc-less keys on the human route

def test_th_ga4_slug_only_key_is_bindable_via_human_route(cfg, svc):
    """v2 (continuum-grammar ruling): a bare slug key — the shape of every `- slug: text`
    bullet — is a full K1 identity: bindable through the audited TH-L9 route, proc-less."""
    _add_project(svc, "P-slugonly", "some unbound project")
    bound = svc.review("bind_task_home", "P-slugonly", {"key": "evopet-pointer-fix"})
    assert bound["ok"] is True
    fields = {r["field"]: r["value"] for r in svc.registry.declared_fields("P-slugonly")}
    assert fields["task_home_key"] == "evopet-pointer-fix"
    assert fields["task_home_binding"] == "human"
    assert th.valid_key("evopet-pointer-fix") is True
    # reversible through the same audited route (dissolution mechanics for the orphan lane)
    unbound = svc.review("unbind_task_home", "P-slugonly", {})
    assert unbound["ok"] is True
    fields = {r["field"]: r["value"] for r in svc.registry.declared_fields("P-slugonly")}
    assert fields["task_home_binding"] == "unbound"
    assert "task_home_key" not in fields
