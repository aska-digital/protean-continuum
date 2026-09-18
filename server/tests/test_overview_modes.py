"""MVP review overview A/B acceptance gate (architecture decision
continuum-architecture-mvp-ab-001, section 6).

AB-C: contract/mode seam. AB-F: fresh registry. AB-A: variant A (immediate).
AB-B: variant B (accepted_only). AB-R: accept/undo round trip. AB-N: regression.

Fixture checks use the deterministic synthetic corpus (build_fixture_tree);
live-corpus checks run against a byte COPY of build/data/registry.db under
TMPDIR — the live file is never opened for write by these tests.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import socket
import sqlite3

import pytest

from continuum.config import load_config
from continuum.service import (
    DEFAULT_OVERVIEW_MODE,
    OverviewConfigError,
    OverviewModeError,
    Service,
)
from dashboard import plugin_api
from fixtures.make_fixture import build_fixture_tree

BUILD_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIVE_DB = os.path.join(BUILD_ROOT, "data", "registry.db")

LS9_PID = "48a43e05-ffd3-5a1f-9ac5-7962826e1517"
DBT6_BASIS_EVIDENCE = "6c08988d-8e99-5f0b-bf5c-bbbe7b8adc79"

ITEM_KEYS = {
    "project_id", "name", "name_source", "context_line",
    "summary_text", "summary_tier", "summary_basis", "summary_rule",
    "lifecycle", "lifecycle_name", "state_source", "phase",
    "quiet_days", "quiet_since", "quiet_band", "reported_stall_age_days",
    "confidence", "confidence_band", "evidence_tier", "evidence_tiers",
    "signals", "profiles", "primary_profile",
    "linked_session_count", "accepted_session_count", "reported_session_count",
    "review_status", "review_status_label", "accepted_at", "needs_review",
    "inclusion_basis", "sessions", "sessions_omitted",
    "drive_expected", "next_action", "alert_state", "data_as_of",
}

SESSION_KEYS = {
    "profile", "session_id", "title", "role", "accepted",
    "cli_resume", "cli_resume_profile_scoped",
    "copy_command", "copy_command_profile_scoped",
    "route", "link_reason",
}

RESPONSE_KEYS = {
    "items", "page", "page_size", "total", "counts", "sort",
    "mode", "mode_source", "include_candidates", "include_suppressed",
    "variant_label", "scan_state", "data_as_of", "limits",
}


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


@pytest.fixture()
def fresh_svc(tmp_path):
    cfg = load_config(hermes_home=str(tmp_path / "empty_home"))
    cfg.bundle["registry_path"] = os.path.join(str(tmp_path), "fresh.db")
    return Service(cfg)


@pytest.fixture()
def live_svc(tmp_path):
    """Live corpus via a byte copy; the live file is never the query target.

    Hermetic: when the live registry is absent (CI) or empty (no live data),
    the dependent tests are skipped with a clear reason rather than erroring.
    """
    if not os.path.exists(LIVE_DB):
        pytest.skip("no live registry at {} — live-corpus tests require a local scan".format(LIVE_DB))
    # Check if DB has any projects; empty DB means no live data to test against
    try:
        _check = sqlite3.connect("file:{}?mode=ro".format(LIVE_DB), uri=True)
        try:
            n = _check.execute("SELECT COUNT(*) FROM project").fetchone()[0]
        finally:
            _check.close()
        if n == 0:
            pytest.skip("live registry is empty (0 projects) — live-corpus tests require populated data")
    except sqlite3.OperationalError as exc:
        pytest.skip("live registry not readable ({}): {}".format(LIVE_DB, exc))
    copy = os.path.join(str(tmp_path), "registry-copy.db")
    shutil.copyfile(LIVE_DB, copy)
    for ext in ("-wal", "-shm", "-journal"):
        src = LIVE_DB + ext
        if os.path.exists(src):
            shutil.copyfile(src, copy + ext)
    cfg = load_config(hermes_home=str(tmp_path / "nope"))
    cfg.bundle["registry_path"] = copy
    return Service(cfg)


def _logical_hash(svc):
    c = svc.registry.conn
    parts = []
    for tbl in ("project_session", "declared_field", "next_action", "review_event"):
        rows = c.execute("SELECT * FROM {} ORDER BY rowid".format(tbl)).fetchall()
        parts.append(json.dumps([dict(r) for r in rows], sort_keys=True, default=str))
    prows = c.execute(
        "SELECT project_id, name, lifecycle FROM project ORDER BY project_id").fetchall()
    parts.append(json.dumps([dict(r) for r in prows], sort_keys=True, default=str))
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()


def _row_counts(svc):
    c = svc.registry.conn
    out = {}
    for tbl in ("project", "project_session", "declared_field",
                "next_action", "review_event"):
        out[tbl] = c.execute("SELECT COUNT(*) AS n FROM {}".format(tbl)).fetchone()["n"]
    return out


def _strip_volatile(payload):
    """Drop the three fields allowed to drift between consecutive GETs."""
    slim = []
    for it in payload["items"]:
        slim.append({k: v for k, v in it.items()
                     if k not in ("data_as_of", "quiet_days", "quiet_since")})
    return slim


# ---------------------------------------------------------------------------
# AB-C: contract checks (mode seam, both variants)
# ---------------------------------------------------------------------------

def test_ab_c1_single_overview_route(tmp_path):
    import re
    with open(os.path.join(BUILD_ROOT, "dashboard", "plugin_api.py")) as fh:
        src = fh.read()
    assert len(re.findall(r'@router\.get\("/overview"\)', src)) == 1
    # Both modes are served by that same route (no exception == served).
    cfg = load_config(hermes_home=str(tmp_path))
    cfg.bundle["registry_path"] = os.path.join(str(tmp_path), "c1.db")
    plugin_api._SERVICE = Service(cfg)
    try:
        assert plugin_api.overview(mode="immediate")["mode"] == "immediate"
        assert plugin_api.overview(mode="accepted_only")["mode"] == "accepted_only"
    finally:
        plugin_api._SERVICE = None


def test_ab_c2_mode_echo_and_identity(svc):
    for kwargs, mode, source, flag in (
            ({}, "immediate", "default", True),
            ({"mode": "immediate"}, "immediate", "request", True),
            ({"mode": "accepted_only"}, "accepted_only", "request", False),
            ({"include_candidates": True}, "immediate", "request", True),
            ({"include_candidates": False}, "accepted_only", "request", False),
            ({"mode": "immediate", "include_candidates": True},
             "immediate", "request", True)):
        out = svc.overview(**kwargs)
        assert out["mode"] in ("immediate", "accepted_only")
        assert out["mode_source"] in ("request", "config", "default")
        assert out["include_candidates"] == (out["mode"] == "immediate")
        assert out["mode"] == mode and out["mode_source"] == source
        assert out["include_candidates"] is flag
        assert set(out.keys()) == RESPONSE_KEYS


def test_ab_c3_same_shape_and_mode_independent_summary(svc):
    a = svc.overview(mode="immediate")
    b = svc.overview(mode="accepted_only")
    assert {i["project_id"] for i in a["items"]} >= \
        {i["project_id"] for i in b["items"]}
    keysets = {frozenset(i.keys()) for i in a["items"]} | \
        {frozenset(i.keys()) for i in b["items"]}
    assert keysets == {frozenset(ITEM_KEYS)}
    by_b = {i["project_id"]: i for i in b["items"]}
    shared = 0
    for item_a in a["items"]:
        if item_a["project_id"] in by_b:
            shared += 1
            item_b = by_b[item_a["project_id"]]
            assert set(item_a.keys()) == set(item_b.keys())
            for field in ("summary_text", "summary_tier", "summary_basis"):
                assert item_a[field] == item_b[field]
    # Fixture corpus shares nothing pre-accept (B is empty); R-tests pin sharing.


def test_ab_c4_unknown_and_contradictory_modes_refused(svc):
    with pytest.raises(OverviewModeError):
        svc.overview(mode="third_value")
    with pytest.raises(OverviewModeError):
        svc.overview(mode="accepted_only", include_candidates=True)
    with pytest.raises(OverviewModeError):
        svc.overview(mode="immediate", include_candidates=False)
    out = svc.overview(include_candidates=False)
    assert out["mode"] == "accepted_only" and out["mode_source"] == "request"
    # Route level maps to HTTP 400.
    plugin_api._SERVICE = svc
    try:
        for kwargs in ({"mode": "third_value"},
                       {"mode": "accepted_only", "include_candidates": True}):
            try:
                plugin_api.overview(**kwargs)
            except Exception as exc:  # HTTPException
                assert getattr(exc, "status_code", None) == 400, exc
                assert "third_value" in str(getattr(exc, "detail", "")) or \
                    "contradictory" in str(getattr(exc, "detail", ""))
            else:
                raise AssertionError("expected HTTP 400 for {!r}".format(kwargs))
    finally:
        plugin_api._SERVICE = None


def test_ab_c5_default_and_config_resolution(home):
    cfg = load_config(hermes_home=home)
    cfg.bundle["registry_path"] = os.path.join(home, "registry-c5.db")
    s = Service(cfg)
    s.scan()
    assert DEFAULT_OVERVIEW_MODE == "immediate"
    out = s.overview()
    assert out["mode"] == "immediate" and out["mode_source"] == "default"
    s.cfg.bundle["overview_mode"] = "accepted_only"
    out = s.overview()
    assert out["mode"] == "accepted_only" and out["mode_source"] == "config"
    s.cfg.bundle["overview_mode"] = "nonsense"
    with pytest.raises(OverviewConfigError) as ei:
        s.overview()
    assert "overview_mode" in str(ei.value)
    # Route level maps a bad configured value to HTTP 500 with empty items.
    plugin_api._SERVICE = s
    try:
        try:
            plugin_api.overview()
        except Exception as exc:
            assert getattr(exc, "status_code", None) == 500, exc
            assert "overview_mode" in str(getattr(exc, "detail", ""))
        else:
            raise AssertionError("expected HTTP 500 for invalid configured mode")
    finally:
        plugin_api._SERVICE = None


def test_ab_c6_filtering_never_reorders(svc):
    a = svc.overview(mode="immediate")
    b = svc.overview(mode="accepted_only")
    in_b = [i["project_id"] for i in b["items"]]
    in_a = [i["project_id"] for i in a["items"] if i["project_id"] in set(in_b)]
    assert in_a == in_b
    again = svc.overview(mode="immediate")
    assert [i["project_id"] for i in again["items"]] == \
        [i["project_id"] for i in a["items"]]
    p1 = svc.overview(mode="immediate", page=1, page_size=1)
    p2 = svc.overview(mode="immediate", page=2, page_size=1)
    assert p1["total"] == a["total"] == 2
    assert p1["items"][0]["project_id"] != p2["items"][0]["project_id"]
    named = svc.overview(mode="immediate", sort="name")
    assert [i["name"] for i in named["items"]] == \
        sorted(i["name"] for i in named["items"])


def test_ab_c7_reads_never_write(svc):
    before = _logical_hash(svc)
    for _ in range(20):
        svc.overview(mode="immediate")
        svc.overview(mode="accepted_only")
    assert _logical_hash(svc) == before


def test_ab_c8_no_network_on_the_overview_path(svc, monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("overview attempted network egress")

    monkeypatch.setattr(socket, "create_connection", _boom)
    monkeypatch.setattr(socket.socket, "connect", _boom)
    assert svc.overview(mode="immediate")["total"] == 2
    assert svc.overview(mode="accepted_only")["total"] == 0


# ---------------------------------------------------------------------------
# AB-F: fresh-registry checks
# ---------------------------------------------------------------------------

def test_ab_f1_empty_registry_both_modes(fresh_svc):
    for mode in ("immediate", "accepted_only"):
        out = fresh_svc.overview(mode=mode)
        assert out["total"] == 0
        assert out["items"] == []
        assert out["counts"] == {"total": 0, "candidate": 0, "accepted": 0,
                                 "suppressed_projects": 0, "suppressed_sessions": 0}
        assert out["data_as_of"] is not None


def test_ab_f2_gets_create_no_rows(fresh_svc):
    before = _row_counts(fresh_svc)
    for _ in range(5):
        fresh_svc.overview(mode="immediate")
        fresh_svc.overview(mode="accepted_only")
    assert _row_counts(fresh_svc) == before


def test_ab_f3_error_scan_state_reports_and_empties(svc):
    svc.registry.begin_scan_run("ab-f3-run", "full")
    svc.registry.end_scan_run("ab-f3-run", sessions_read=0, messages_probed=0,
                              profiles_scanned=0, status="error", error="disk boom")
    for mode in ("immediate", "accepted_only"):
        out = svc.overview(mode=mode)
        assert out["total"] == 0 and out["items"] == []
        assert out["scan_state"]["phase"] == "error"


def test_ab_f4_fixture_baseline_is_deterministic(svc):
    a = svc.overview(mode="immediate")
    b = svc.overview(mode="accepted_only")
    assert a["total"] == 2
    assert {i["name"] for i in a["items"]} == {"tjgc1", "tywebsite"}
    assert a["counts"]["candidate"] == 2 and a["counts"]["accepted"] == 0
    assert b["total"] == 0
    assert svc.inbox()["total"] == 2


# ---------------------------------------------------------------------------
# AB-A: variant A (mode=immediate) on the live copy
# ---------------------------------------------------------------------------

def test_ab_a1_live_a_shows_all_candidates(live_svc):
    out = live_svc.overview(mode="immediate")
    n_projects = live_svc.registry.conn.execute(
        "SELECT COUNT(*) AS n FROM project").fetchone()["n"]
    assert n_projects > 0
    assert out["total"] == n_projects
    assert out["counts"]["candidate"] == n_projects
    assert out["counts"]["accepted"] == 0
    assert out["total"] == out["counts"]["candidate"] + out["counts"]["accepted"]


def test_ab_a2_every_item_marked_derived_unreviewed(live_svc):
    out = live_svc.overview(mode="immediate")
    assert out["items"]
    for it in out["items"]:
        assert it["review_status"] == "candidate"
        assert it["review_status_label"] == "derived candidate \u2014 not reviewed"
        assert it["inclusion_basis"] == "derived candidate \u2014 no accepted link"
        assert it["accepted_at"] is None
        assert it["accepted_session_count"] == 0


def test_ab_a3_minimum_field_list(live_svc):
    out = live_svc.overview(mode="immediate")
    db_ids = {(r["profile_name"], r["session_id"]) for r in
              live_svc.registry.conn.execute(
                  "SELECT profile_name, session_id FROM project_session")}
    for it in out["items"]:
        assert set(it.keys()) == ITEM_KEYS
        assert it["name"] and it["context_line"]
        assert it["summary_text"] and len(it["summary_text"]) <= 320
        assert "\n" not in it["summary_text"]
        assert it["summary_tier"] in (0, 1)
        assert it["summary_rule"] == "mvp.summary.v1"
        assert it["lifecycle"].startswith("LS-")
        assert it["lifecycle_name"] and it["phase"]
        assert it["quiet_band"] in ("0-3d", "3-7d", "7-30d", "30d+", "unknown")
        assert it["confidence_band"] in ("high", "medium", "low", "unknown")
        assert set(it["sessions"][0].keys()) == SESSION_KEYS if it["sessions"] else True
        for s in it["sessions"]:
            assert set(s.keys()) == SESSION_KEYS
            assert (s["profile"], s["session_id"]) in db_ids
            assert s["route"] is None
            assert s["cli_resume"] == "hermes --resume " + s["session_id"]
            assert s["cli_resume_profile_scoped"] == \
                "hermes -p {} --resume {}".format(s["profile"], s["session_id"])
            assert "HERMES_PROFILE" not in s["cli_resume_profile_scoped"]
        assert it["linked_session_count"] == len(
            [r for r in db_ids if r in
             {(s["profile"], s["session_id"]) for s in it["sessions"]}]) + \
            it["sessions_omitted"]
        assert len(it["sessions"]) <= 20


def test_ab_a4_unknown_band_item_not_on_board_or_attention(live_svc):
    a = live_svc.overview(mode="immediate")
    ids = {i["project_id"] for i in a["items"]}
    assert LS9_PID in ids
    board = live_svc.board()
    # kanban: unknown-band candidate lives in Inbox lane, not in attention (architecture D-KB-14 §11.3)
    found = [c for c in board["items"] if c["project_id"] == LS9_PID]
    assert found and found[0]["column"] == "inbox"
    assert board["counts"]["inbox"] >= 1
    assert board["total"] == len(board["items"])
    alerted = {i["project_id"] for g in live_svc.attention()["groups"].values()
               for i in g}
    assert LS9_PID not in alerted


def test_ab_a5_link_count_not_stored_count(live_svc):
    a = live_svc.overview(mode="immediate")
    item = next(i for i in a["items"] if i["project_id"] == LS9_PID)
    assert len(item["sessions"]) <= 20
    assert item["linked_session_count"] >= len(item["sessions"])
    assert item["sessions_omitted"] == item["linked_session_count"] - len(item["sessions"])
    assert item["reported_session_count"] >= 0


def test_ab_a6_dbt6_summary_reproduces_worked_example(live_svc):
    a = live_svc.overview(mode="immediate")
    item = next(i for i in a["items"] if i["name"] == "dbt6")
    assert item["summary_text"].startswith(
        "Architecture and implementation-readiness audit for Dabba quality pass")
    assert item["summary_basis"][0]["evidence_id"] == DBT6_BASIS_EVIDENCE
    assert item["linked_session_count"] >= 1
    assert item["summary_tier"] == 1


def test_ab_a7_reject_filter_fires_on_live_corpus(live_svc):
    # The pinned mvp.summary.v1 reject filter must actually reject something.
    import re as _re
    from continuum.service import _mvp_rejected, _mvp_collapse, SUMMARY_MIN_EXCERPT_CHARS
    health = [_re.compile(p) for p in
              (live_svc.cfg.get("health_ack_content_patterns") or [])]
    rows = live_svc.registry.conn.execute("SELECT excerpt FROM evidence").fetchall()
    assert len(rows) > 0
    rejected = sum(1 for r in rows
                   if (r["excerpt"] is None
                       or _mvp_collapse(r["excerpt"]) == ""
                       or len(_mvp_collapse(r["excerpt"])) < SUMMARY_MIN_EXCERPT_CHARS
                       or _mvp_rejected(r["excerpt"], health)))
    assert rejected >= 1


# ---------------------------------------------------------------------------
# AB-B: variant B (mode=accepted_only)
# ---------------------------------------------------------------------------

def test_ab_b1_live_b_hides_nothing_silently(live_svc):
    out = live_svc.overview(mode="accepted_only")
    assert out["total"] == 0 and out["items"] == []
    assert out["counts"]["accepted"] == 0
    live_candidates = live_svc.registry.conn.execute(
        "SELECT COUNT(*) AS n FROM project").fetchone()["n"]
    assert out["counts"]["candidate"] == live_candidates


def test_ab_b2_fixture_b_empty_while_inbox_holds_candidates(svc):
    assert svc.overview(mode="accepted_only")["total"] == 0
    assert svc.inbox()["total"] == 2


def test_ab_b3_accept_gates_appearance_in_b(svc):
    pid = next(i["project_id"] for i in svc.overview(mode="immediate")["items"]
               if i["name"] == "tjgc1")
    res = svc.review("accept", pid, {})
    assert res["ok"]
    out = svc.overview(mode="accepted_only")
    assert out["total"] == 1
    item = out["items"][0]
    assert item["project_id"] == pid
    assert item["review_status"] == "accepted"
    assert item["accepted_at"] is not None
    assert item["inclusion_basis"] == "accepted link"
    assert pid not in {i["project_id"] for i in svc.inbox()["items"]}
    assert pid in {c["project_id"] for c in svc.board()["items"]}


def test_ab_b4_reads_never_accept(svc):
    for _ in range(20):
        svc.overview(mode="immediate")
        svc.overview(mode="accepted_only")
    c = svc.registry.conn
    assert c.execute(
        "SELECT COUNT(*) AS n FROM project_session WHERE accepted=1").fetchone()["n"] == 0
    assert c.execute(
        "SELECT COUNT(*) AS n FROM review_event").fetchone()["n"] == 0


def test_ab_b5_counts_agree_with_items(svc, live_svc):
    for s in (svc, live_svc):
        for mode in ("immediate", "accepted_only"):
            out = s.overview(mode=mode)
            assert out["counts"]["candidate"] + out["counts"]["accepted"] == \
                out["counts"]["total"]


# ---------------------------------------------------------------------------
# AB-R: round trip (the behavior the user will actually compare)
# ---------------------------------------------------------------------------

def test_ab_r1_accept_from_a_flips_badge_stays_on_a(svc):
    pid = next(i["project_id"] for i in svc.overview(mode="immediate")["items"]
               if i["name"] == "tjgc1")
    res = svc.review("accept", pid, {})
    a = svc.overview(mode="immediate")
    item = next(i for i in a["items"] if i["project_id"] == pid)
    assert item["review_status"] == "accepted"
    assert item["accepted_at"] is not None
    assert item["inclusion_basis"] == "accepted link"
    assert pid in {c["project_id"] for c in svc.board()["items"]}
    svc.review("undo", pid, {"audit_id": res["audit_id"]})


def test_ab_r2_undo_restores_candidate_and_empties_board(svc):
    pid = next(i["project_id"] for i in svc.overview(mode="immediate")["items"]
               if i["name"] == "tjgc1")
    res = svc.review("accept", pid, {})
    svc.review("undo", pid, {"audit_id": res["audit_id"]})
    a = svc.overview(mode="immediate")
    item = next(i for i in a["items"] if i["project_id"] == pid)
    assert item["review_status"] == "candidate"
    assert item["accepted_at"] is None
    # kanban: undone candidate returns to Inbox lane on the board (architecture §11.3)
    board = svc.board()
    card = next(c for c in board["items"] if c["project_id"] == pid)
    assert card["column"] == "inbox"
    assert card["placement_source"] == "candidate"
    assert pid in {i["project_id"] for i in svc.inbox()["items"]}
    assert svc.overview(mode="accepted_only")["total"] == 0


def test_ab_r3_mode_switching_is_stateless(svc):
    before = _logical_hash(svc)
    first = svc.overview(mode="immediate")
    svc.overview(mode="accepted_only")
    third = svc.overview(mode="immediate")
    assert _strip_volatile(first) == _strip_volatile(third)
    assert _logical_hash(svc) == before


def test_ab_r4_suppression_is_explicit(svc):
    pid = next(i["project_id"] for i in svc.overview(mode="immediate")["items"]
               if i["name"] == "tjgc1")
    svc.review("dismiss", pid, {})
    hidden = svc.overview(mode="immediate")
    assert pid not in {i["project_id"] for i in hidden["items"]}
    shown = svc.overview(mode="immediate", include_suppressed=True)
    assert pid in {i["project_id"] for i in shown["items"]}
    assert shown["counts"]["suppressed_projects"] >= 1


# ---------------------------------------------------------------------------
# AB-N: regression (existing behavior untouched)
# ---------------------------------------------------------------------------

def test_ab_n2_existing_surfaces_keep_their_shapes(svc):
    board = svc.board()
    assert set(board.keys()) == {"items", "page", "total", "counts", "data_as_of", "scan_state"}
    # kanban: board now populates Inbox with candidates (architecture D-KB-14 §11.3)
    assert board["total"] == len(board["items"])
    assert board["total"] == board["counts"]["total"]
    assert board["counts"]["inbox"] == 2
    assert all(c["column"] in ("inbox", "ongoing", "blocked", "waiting_on_you", "paused", "done", "shipped", "scrapped") for c in board["items"])
    inbox = svc.inbox()
    assert set(inbox.keys()) == {"items", "total", "data_as_of"}
    assert inbox["total"] == 2
    attention = svc.attention()
    assert set(attention.keys()) == {"groups", "attention_count", "data_as_of"}
    staleness = svc.staleness()
    assert set(staleness.keys()) == {"buckets", "parked", "data_as_of"}
    noise = svc.noise()
    assert set(noise.keys()) == {"suppressions", "rules", "counts", "data_as_of"}
    card_keys = {"project_id", "name", "lifecycle", "lifecycle_name", "phase",
                 "owner_profile", "last_substantive_activity", "stall_age_days",
                 "session_count", "confidence", "confidence_band", "evidence_tier",
                 "evidence_tiers", "next_action", "drive_expected", "alert_state",
                 "derived_updated_at", "declared_stale", "data_as_of"}
    for c in inbox["items"]:
        assert "cluster_id" in c and "member_refs" in c and "resume_links" in c


def test_ab_n4_resume_forms_and_readonly_proof(svc):
    for c in svc.inbox()["items"]:
        for link in c["resume_links"]:
            assert link["copy_command"] == "hermes --resume " + link["session_id"]
            assert link["copy_command_profile_scoped"] == \
                "hermes -p {} --resume {}".format(link["profile"], link["session_id"])
            assert link["route"] is None


def test_ab_n5_harness_has_details_and_inbox_artifacts():
    src_path = os.path.join(BUILD_ROOT, "review", "ab_snapshot.py")
    with open(src_path, "r", encoding="utf-8") as fh:
        src = fh.read()
    assert "<details" in src
    assert "<summary" in src
    assert "<script" not in src.lower()
    # must snapshot inbox for both corpora
    assert "inbox()" in src
    assert "inbox.json" in src
    assert "live-inbox.json" in src
    # manifest must hash both inbox files
    assert src.count("inbox.json") >= 2


def test_ab_r5_accept_returns_audit_id_and_undo_reverses(home):
    cfg = load_config(hermes_home=home)
    cfg.bundle["registry_path"] = os.path.join(home, "registry-r5.db")
    svc = Service(cfg)
    svc.scan()
    pid = next(i["project_id"] for i in svc.overview(mode="immediate")["items"] if i["name"] == "tjgc1")
    # drive the route functions directly (pattern at test_ab_c1)
    plugin_api._SERVICE = svc
    try:
        from dashboard.plugin_api import ReviewBody, UndoBody  # noqa
        res = plugin_api.review(pid, ReviewBody(action="accept", payload={}))
        assert res.get("ok") is True
        audit_id = res.get("audit_id")
        assert isinstance(audit_id, str) and len(audit_id) > 0
        # second accept on same pid would be idempotent but still returns audit_id; test undo path
        undo_res = plugin_api.undo(UndoBody(audit_id=audit_id))
        assert undo_res.get("undoes") == audit_id
        assert undo_res.get("ok") is True
        # verify round-trip state: after undo, B overview empty again
        assert svc.overview(mode="accepted_only")["total"] == 0
        item = next(i for i in svc.overview(mode="immediate")["items"] if i["project_id"] == pid)
        assert item["review_status"] == "candidate"
        assert item["accepted_at"] is None
    finally:
        plugin_api._SERVICE = None

# ---------------------------------------------------------------------------
# SDK-fix behavior proofs (D-1..D-5): mode switch, CopyButton text, host.notify singular action, audit_id->undo, Badge/ErrorState/Skeleton valid props
# These are behavior-focused, not source-only; they supplement the static guards in test_plugin_source.py.
# ---------------------------------------------------------------------------

def test_sdk_fix_mode_switch_selectable_and_accepted_only(home):
    """D-2 behavior: mode switch produces immediate vs accepted_only request/response. Variant B is selectable and renders accepted-only."""
    cfg = load_config(hermes_home=home)
    cfg.bundle["registry_path"] = os.path.join(home, "registry-sdk-fix.db")
    svc = Service(cfg)
    svc.scan()
    plugin_api._SERVICE = svc
    try:
        a = plugin_api.overview(mode="immediate")
        b = plugin_api.overview(mode="accepted_only")
        assert a["mode"] == "immediate" and a["include_candidates"] is True
        assert b["mode"] == "accepted_only" and b["include_candidates"] is False
        assert a["total"] == 2 and b["total"] == 0
        assert b["counts"]["candidate"] == 2
        pid = next(i["project_id"] for i in a["items"] if i["name"] == "tjgc1")
        res = plugin_api.review(pid, plugin_api.ReviewBody(action="accept", payload={}))
        assert res["ok"] and res["audit_id"]
        b2 = plugin_api.overview(mode="accepted_only")
        assert b2["total"] == 1 and b2["items"][0]["project_id"] == pid
        assert b2["items"][0]["review_status"] == "accepted"
        plugin_api.undo(plugin_api.UndoBody(audit_id=res["audit_id"]))
        assert plugin_api.overview(mode="accepted_only")["total"] == 0
    finally:
        plugin_api._SERVICE = None


def test_sdk_fix_copy_id_bare_session_id_and_text_prop(home):
    """D-3 behavior: Copy ID is the bare session_id; plugin wires CopyButton text= (not value=)."""
    cfg = load_config(hermes_home=home)
    cfg.bundle["registry_path"] = os.path.join(home, "registry-copyid.db")
    svc = Service(cfg)
    svc.scan()
    items = svc.overview(mode="immediate")["items"]
    for it in items:
        for s in it["sessions"]:
            assert " " not in s["session_id"] and "/" not in s["session_id"]
            assert s["cli_resume_profile_scoped"] != s["cli_resume"]
            assert s["session_id"] in s["copy_command"] and s["session_id"] in s["copy_command_profile_scoped"]
    import re
    with open(os.path.join(BUILD_ROOT, "desktop", "plugin.js")) as fh:
        src = fh.read()
    assert "text: primary.session_id" in src or "text: command" in src
    assert "value: primary.session_id" not in src
    assert "CopyButton" in src and "text:" in src


def test_sdk_fix_host_notify_singular_action_and_audit_id_to_undo(home):
    """D-1 behavior: host.notify with singular action, audit_id flows to POST /review/undo {audit_id}."""
    cfg = load_config(hermes_home=home)
    cfg.bundle["registry_path"] = os.path.join(home, "registry-notify.db")
    svc = Service(cfg)
    svc.scan()
    plugin_api._SERVICE = svc
    try:
        from dashboard.plugin_api import ReviewBody, UndoBody
        pid = next(i["project_id"] for i in svc.overview(mode="immediate")["items"] if i["name"] == "tjgc1")
        res = plugin_api.review(pid, ReviewBody(action="accept", payload={}))
        audit_id = res["audit_id"]
        assert audit_id
        import re
        with open(os.path.join(BUILD_ROOT, "desktop", "plugin.js")) as fh:
            src = fh.read()
        assert "host.notify" in src
        assert "ctx.notify" not in src
        assert re.search(r"host\.notify\(\{[^}]*action:", src, re.DOTALL)
        assert "actions:" not in src
        # kanban supersession S-4: retired "on the Overview" replaced by board destination "It's in {Column}" (file stores \u2019 escape)
        assert "Accepted" in src and ("s in" in src and "COLUMN_LABELS" in src)
        assert "on the Overview" not in src
        assert "Undo" in src
        # board destination wording present
        assert "COLUMN_LABELS" in src or "Inbox" in src
        undo_res = plugin_api.undo(UndoBody(audit_id=audit_id))
        assert undo_res["undoes"] == audit_id
        assert svc.overview(mode="accepted_only")["total"] == 0
    finally:
        plugin_api._SERVICE = None


def test_sdk_fix_badge_error_skeleton_valid_sdk_props():
    """D-4 + D-5 behavior: Badge variant, ErrorState title/children/description, Skeleton without rows."""
    import re
    with open(os.path.join(BUILD_ROOT, "desktop", "plugin.js")) as fh:
        src = fh.read()
    assert "variant:" in src
    badge_kinds = re.findall(r"Badge,\s*\{[^}]*kind:", src)
    assert not badge_kinds, f"Badge must use variant, not kind: {badge_kinds}"
    assert "variant: 'warn'" in src
    assert "variant: 'muted'" in src or "variant: 'default'" in src
    assert "ErrorState" in src
    assert re.search(r"ErrorState,\s*\{[^}]*title:", src)
    assert "children: jsx(Button" in src
    assert "onRetry" not in src
    assert "rows:" not in src
    assert src.count("Skeleton") >= 6
    assert "(c.signals" not in src and "signals.join" not in src
    assert "title: it.inclusion_basis" not in src
    assert src.count("Recovery Inbox") == 0
    # kanban supersession D-KB-6/S-3: retired Review candidates tab replaced by Inbox lane
    assert "Review candidates" not in src
    assert "on the Overview" not in src
    assert "Inbox" in src
    for label in ("Ongoing", "Blocked", "Waiting on you", "Paused", "Done", "Shipped", "Scrapped"):
        assert label in src
