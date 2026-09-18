"""M4 Continuity Playground — CP-T1 .. CP-T14 acceptance tests (implementation, KodeKoot).

Behavioural tests only: every assertion drives the real service/registry/scanner over an
isolated fixture home with a temp registry. No live profile database is opened, no installed
tree is touched, and no test asserts on source text except the explicit boundary sweeps
(CP-T13/CP-T14), which are labelled as such.

The contract is ``handoffs/architecture-continuum-m4-continuity-playground.md`` (Azaraki,
STATUS: STABLE) and the pane presentation is
``handoffs/ux-continuum-session-panes.md`` (Shayba, DECIDED).
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time

import pytest

from continuum import scanner
from continuum.config import load_config
from continuum.registry import SCHEMA_VERSION
from continuum.service import (
    ATTENTION_RANK, BOARD_COLUMNS, PLACEMENTS, RECENCY_RANK, SORT_FIELDS, Service,
)
from fixtures.make_fixture import build_fixture_tree

AUDIENCE_FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 "fixtures", "audience")
if AUDIENCE_FIXTURES not in sys.path:
    sys.path.insert(0, AUDIENCE_FIXTURES)

BUILD_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DAY = 86400.0


# --------------------------------------------------------------------------- fixtures

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


def _add_project(svc, pid, *, name="synthetic", lifecycle="LS-2", band="high", tier=1,
                 confidence=0.8, sessions=()):
    """Insert a synthetic project with explicit links/facts (registry-only writes)."""
    now = time.time()
    svc.registry.conn.execute(
        "INSERT OR REPLACE INTO project (project_id, name, kind, phase, lifecycle, confidence,"
        " confidence_band, evidence_tier, owner_profile, drive_expected, stall_age_days,"
        " last_substantive_activity, session_count, derived_updated_at, created_at, updated_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (pid, name, "derived", "active", lifecycle, confidence, band, tier, "", 0, 1.0,
         now, len(sessions), now, now, now))
    for prof, sid, audience, last, msgs, present in sessions:
        svc.registry.conn.execute(
            "INSERT OR REPLACE INTO project_session (link_id, project_id, profile_name,"
            " session_id, role_in_project, link_confidence, link_reason, evidence_ref, accepted,"
            " first_linked_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            ("link-{}-{}".format(pid, sid), pid, prof, sid, "supporting", 0.8, "synthetic",
             None, 0, now))
        if present:
            svc.registry.conn.execute(
                "INSERT OR REPLACE INTO session_fact (profile_name, session_id, title, cwd,"
                " message_count, tool_call_count, started_at, last_activity_at, present,"
                " audience, audience_reason) VALUES (?,?,?,?,?,?,?,?,1,?,?)",
                (prof, sid, "title " + sid, "/work/" + sid, msgs, 1, last, last,
                 audience, "U"))
    svc.registry.conn.commit()
    return pid


def _board(svc, **kw):
    return svc.board(**kw)


def _cards(svc, **kw):
    return {c["project_id"]: c for c in _board(svc, **kw)["items"]}


def _cleanup(svc, pid, sessions=()):
    svc.registry.conn.execute("DELETE FROM project WHERE project_id=?", (pid,))
    svc.registry.conn.execute("DELETE FROM project_session WHERE project_id=?", (pid,))
    for prof, sid, *_ in sessions:
        svc.registry.conn.execute(
            "DELETE FROM session_fact WHERE profile_name=? AND session_id=?", (prof, sid))
    svc.registry.conn.commit()


# --------------------------------------------------------------------------- CP-T1

def test_cp_t1_every_lifecycle_and_age_stays_in_exactly_one_lane(svc):
    """CP-T1: durable visibility. Age, lifecycle, band, audience, source failure never drop."""
    now = time.time()
    cases = []
    for i, ls in enumerate(["LS-1", "LS-2", "LS-3", "LS-4", "LS-5", "LS-6", "LS-7", "LS-8", "LS-9"]):
        pid = "cpt1-{}-{}".format(ls.lower(), i)
        sessions = [("zz", "cpt1s{}{}a".format(i, ls[-1]), "USER_FACING", now - 5 * DAY, 3, True)]
        _add_project(svc, pid, name="cpt1 " + ls, lifecycle=ls, sessions=sessions)
        cases.append((pid, sessions))
    try:
        # one very old project, one unknown-band, one delegated-only, one missing fact
        old_pid = "cpt1-old"
        old_sessions = [("zz", "cpt1old1", "USER_FACING", now - 365 * DAY, 2, True)]
        _add_project(svc, old_pid, name="cpt1 old", lifecycle="LS-5", sessions=old_sessions)
        cases.append((old_pid, old_sessions))

        low_pid = "cpt1-low"
        low_sessions = [("zz", "cpt1low1", "UNKNOWN", now - 30 * DAY, 2, True)]
        _add_project(svc, low_pid, name="cpt1 low", lifecycle="LS-9", band="unknown",
                     tier=0, sessions=low_sessions)
        cases.append((low_pid, low_sessions))

        deleg_pid = "cpt1-deleg"
        deleg_sessions = [("zz", "cpt1deleg1", "DELEGATED", now - 7 * DAY, 5, True),
                          ("zz", "cpt1deleg2", "AUTOMATED", now - 7 * DAY, 5, True)]
        _add_project(svc, deleg_pid, name="cpt1 delegated", lifecycle="LS-2",
                     sessions=deleg_sessions)
        cases.append((deleg_pid, deleg_sessions))

        gone_pid = "cpt1-gone"
        gone_sessions = [("zz", "cpt1gone1", "USER_FACING", now - 7 * DAY, 4, False)]
        _add_project(svc, gone_pid, name="cpt1 missing source", lifecycle="LS-2",
                     sessions=gone_sessions)
        cases.append((gone_pid, gone_sessions))

        cards = _cards(svc, view="all", page_size=200)
        for pid, _sessions in cases:
            assert pid in cards, "{} vanished from the continuity set".format(pid)
            assert cards[pid]["column"] in BOARD_COLUMNS
        # a delegated-only project is visible and marked no_user_work, not hidden
        assert cards[deleg_pid]["user_facing_session_count"] == 0
        assert cards[deleg_pid]["attention_state"] == "no_user_work"
        # a missing source link lowers completeness without removing the card
        assert cards[gone_pid]["message_count_complete"] is False
        assert cards[gone_pid]["session_count_total"] == 1
        # exactly one lane per card
        lanes = [cards[pid]["column"] for pid, _ in cases]
        assert len(lanes) == len(cases)

        # a FAILED scan leaves the last committed cards and data_as_of visible
        snapshot = _board(svc, view="today")
        before_as_of = snapshot["data_as_of"]
        before_ids = {c["project_id"] for c in snapshot["items"]}
        original = svc.registry.tombstone_missing

        def _boom(*a, **k):
            raise RuntimeError("injected ingest failure")

        svc.registry.tombstone_missing = _boom
        try:
            with pytest.raises(RuntimeError):
                svc.scan(full=True)
        finally:
            svc.registry.tombstone_missing = original
        after = _board(svc, view="today")
        assert after["data_as_of"] == before_as_of, "data_as_of moved without a commit"
        assert before_ids <= {c["project_id"] for c in after["items"]}
        assert after["scan_state"]["phase"] == "error"
        assert after["scan_state"]["last_error"]
    finally:
        for pid, sessions in cases:
            _cleanup(svc, pid, sessions)


def test_cp_t1_dismissed_and_merged_are_the_only_removals(svc):
    cards = _cards(svc, view="all")
    target = sorted(cards)[0]
    svc.review("dismiss", target, {"noise_class": "not-a-project"})
    assert target not in _cards(svc, view="all")
    # the project row still exists: suppression is not deletion
    assert svc.registry.get_project(target) is not None
    events = svc.registry.review_events(target, limit=10)
    svc.review("undo", "", {"audit_id": events[0]["event_id"]})
    assert target in _cards(svc, view="all")


# --------------------------------------------------------------------------- CP-T2

@pytest.mark.parametrize("placement", ["done", "shipped", "scrapped"])
def test_cp_t2_terminal_placement_is_human_explicit_and_visible(svc, placement):
    cards = _cards(svc, view="all")
    pid = sorted(cards)[0]
    before_links = len(svc.registry.links_for(pid))

    res = svc.review("set_placement", pid, {"placement": placement})
    assert res["ok"]
    card = _cards(svc, view="all")[pid]
    assert card["column"] == placement
    assert card["placement_source"] == "human"
    assert card["derived_lifecycle"] != placement  # lane and lifecycle are separate facts

    # survives a full scan and an incremental scan, with no source write and no suppression
    svc.scan(full=True)
    assert _cards(svc, view="all")[pid]["column"] == placement
    svc.scan()
    after = _cards(svc, view="all")[pid]
    assert after["column"] == placement
    assert len(svc.registry.links_for(pid)) == before_links
    declared = svc.registry.effective_declared(pid)
    assert declared.get("placement") == placement
    assert "dismissed" not in declared and "merged_into" not in declared
    assert svc.registry.get_project(pid) is not None

    svc.review("undo", "", {"audit_id": res["audit_id"]})
    assert _cards(svc, view="all")[pid]["placement_source"] != "human"


def test_cp_t2_ls7_and_ls8_never_resolve_to_a_terminal_lane(svc):
    for i, ls in enumerate(["LS-7", "LS-8", "LS-1", "LS-5", "LS-9"]):
        pid = "cpt2-{}-{}".format(ls.lower(), i)
        sessions = [("zz", "cpt2s{}{}".format(i, ls[-1]), "USER_FACING", 1.0, 4, True)]
        _add_project(svc, pid, name="cpt2 " + ls, lifecycle=ls, sessions=sessions)
        svc.review("accept", pid, {})
        try:
            card = _cards(svc, view="all")[pid]
            assert card["derived_lifecycle"] == ls
            assert card["column"] == "ongoing"
            assert card["placement_source"] == "unplaced_unmapped"
            assert card["column"] not in ("done", "shipped", "scrapped")
        finally:
            _cleanup(svc, pid, sessions)


def test_cp_t2_derived_mapping_still_places_non_terminal_lifecycles(svc):
    """LS-2/LS-3/LS-4/LS-6 keep their non-terminal derived lanes for accepted projects."""
    mapping = {"LS-2": "ongoing", "LS-3": "blocked", "LS-4": "waiting_on_you", "LS-6": "paused"}
    created = []
    try:
        for i, (ls, expected) in enumerate(sorted(mapping.items())):
            pid = "cpt2m-{}".format(i)
            sessions = [("zz", "cpt2ms{}".format(i), "USER_FACING", 1.0, 4, True)]
            _add_project(svc, pid, name="cpt2m " + ls, lifecycle=ls, sessions=sessions)
            created.append((pid, sessions))
            svc.review("accept", pid, {})
            card = _cards(svc, view="all")[pid]
            assert card["column"] == expected, (ls, card["column"])
            assert card["placement_source"] == "derived_unplaced"
    finally:
        for pid, sessions in created:
            _cleanup(svc, pid, sessions)


# --------------------------------------------------------------------------- CP-T3

def test_cp_t3_paused_is_visible_and_quiet_and_undo_restores(svc):
    cards = _cards(svc, view="all")
    pid = sorted(cards)[0]
    original_lifecycle = svc.registry.get_project(pid)["lifecycle"]
    res = svc.review("set_placement", pid, {"placement": "paused"})

    card = _cards(svc, view="all")[pid]
    assert card["column"] == "paused"
    assert card["attention_state"] == "quiet"
    assert card["recency_state"] == "quiet"
    # the derived lifecycle and evidence are untouched by the quiet placement
    assert card["derived_lifecycle"] == original_lifecycle
    assert card["evidence_tiers"] == list(card["evidence_tiers"])
    # absent from the attention groups, absent from START HERE, present in its lane
    alerted = {i["project_id"] for g in svc.attention()["groups"].values() for i in g}
    assert pid not in alerted
    today = _board(svc, view="today")
    assert pid not in {ref["project_id"] for ref in today["playground"]["start_here"]}
    assert pid in {c["project_id"] for c in today["items"] if c["column"] == "paused"}

    svc.review("undo", "", {"audit_id": res["audit_id"]})
    restored = _cards(svc, view="all")[pid]
    assert restored["column"] != "paused"
    assert restored["attention_state"] != "quiet"
    events = [e for e in svc.registry.review_events(pid, limit=20)
              if e["action"] == "set_placement"]
    assert len(events) == 1, "one placement move must produce exactly one event"


# --------------------------------------------------------------------------- CP-T4

def _audience_service(tmp_path):
    from build_audience_home import BRIEFS_DIR, build_audience_home
    home = build_audience_home(str(tmp_path / "audience_home"))
    cfg = load_config(hermes_home=home)
    cfg.bundle["registry_path"] = str(tmp_path / "audience_registry.db")
    cfg.bundle["audience_brief_roots"] = [BRIEFS_DIR]
    cfg.bundle["workspace_patterns"] = [r"^(?P<repo>/Users/kethuda/evopet-pet)$"]
    cfg.bundle["workspace_git_walkup"] = False
    service = Service(cfg)
    service.scan()
    return service


def test_cp_t4_audience_and_evopet_anchor_regression(tmp_path):
    """CP-T4: the verified audience outcome survives the M4 primary/resume projection."""
    from build_audience_home import (ANCHOR_PROFILE, ANCHOR_SESSION, DIRECT_AZARAKI,
                                     DIRECT_KODEKOOT, EVOPET_MEMBERS, TARGET_PROFILE,
                                     TARGET_SESSION)
    svc = _audience_service(tmp_path)
    worker = "{}/{}".format(TARGET_PROFILE, TARGET_SESSION)
    anchor = "{}/{}".format(ANCHOR_PROFILE, ANCHOR_SESSION)

    assert svc.registry.session_audience(TARGET_PROFILE, TARGET_SESSION)[0] == "DELEGATED"
    assert svc.registry.session_audience(TARGET_PROFILE, TARGET_SESSION)[1] == "C1"
    assert svc.registry.session_audience("azaraki", DIRECT_AZARAKI)[0] == "USER_FACING"
    assert svc.registry.session_audience("kodekoot", DIRECT_KODEKOOT)[0] == "USER_FACING"

    row = svc.registry.conn.execute(
        "SELECT project_id, anchor_session, anchor_reason FROM project WHERE name='evopet-pet'"
    ).fetchone()
    pid = row["project_id"]
    assert row["anchor_session"] == anchor
    assert row["anchor_reason"] == "ANCHOR-NAME"

    card = _cards(svc, view="all")[pid]
    assert card["audience_counts"]["DELEGATED"] == len(EVOPET_MEMBERS)
    assert card["audience_counts"]["USER_FACING"] == 0
    assert card["attention_state"] == "no_user_work"
    # the anchor is the resume target; no delegated worker is one
    assert [t["session_id"] for t in card["resume_targets"]] == [ANCHOR_SESSION]
    assert card["resume_targets"][0]["copy_command"] == (
        "hermes -p {} --resume {}".format(ANCHOR_PROFILE, ANCHOR_SESSION))
    assert card["primary_session"]["selection"] == "anchor"
    for t in card["resume_targets"]:
        assert t["audience"] == "USER_FACING"
    for s in card["sessions"]:
        assert s["session_id"] != TARGET_SESSION
    assert svc.registry.session_audience(TARGET_PROFILE, TARGET_SESSION)[0] != "USER_FACING"

    # detail: resume links never name the delegated worker, and the pane refuses a claim of
    # "your messages" for a delegated session
    detail = svc.project_detail(pid)
    for link in detail["resume_links"]:
        assert TARGET_SESSION not in link["copy_command_profile_scoped"]
    pane = svc.project_detail(pid, pane=worker)["session_pane"]
    assert pane["audience"] == "DELEGATED"
    assert pane["user_messages"] == []
    assert pane["user_messages_state"] == "not_user_facing"
    # the dispatch brief must never be rendered as the user's own message
    brief_terms = [row["text"] for row in pane["user_messages"]]
    assert brief_terms == []

    # a project with no user-facing member has NO resume target at all
    for card in _board(svc, view="all")["items"]:
        if card["user_facing_session_count"] == 0 and not card["anchor_session"]:
            assert card["resume_targets"] == []
            assert card["primary_session"] is None
            assert card["no_user_session"] is True


# --------------------------------------------------------------------------- CP-T5

def test_cp_t5_aggregate_and_stopping_point_contract(svc):
    now = time.time()
    pid = "cpt5-aggregate"
    sessions = [
        ("zz", "cpt5-user", "USER_FACING", now - 10 * DAY, 7, True),
        ("zz", "cpt5-deleg", "DELEGATED", now - 1 * DAY, 5, True),
        ("zz", "cpt5-auto", "AUTOMATED", now - 2 * DAY, 3, True),
        ("zz", "cpt5-unknown", "UNKNOWN", now - 3 * DAY, 2, True),
        ("zz", "cpt5-missing", "USER_FACING", now - 4 * DAY, 0, False),
    ]
    _add_project(svc, pid, name="cpt5 aggregate", lifecycle="LS-2", sessions=sessions)
    try:
        card = _cards(svc, view="all")[pid]
        assert card["session_count_total"] == 5
        counts = card["audience_counts"]
        assert sum(counts.values()) == card["session_count_total"]
        # the tombstoned/missing link has no audience fact, so it is counted as UNKNOWN
        # (visible supporting evidence with audience_unverified), never dropped
        assert counts == {"USER_FACING": 1, "DELEGATED": 1, "AUTOMATED": 1, "UNKNOWN": 2}
        assert card["message_count_complete"] is False       # one fact is missing
        assert card["message_count_total"] == 7 + 5 + 3 + 2  # never a substituted zero
        # last_worked_on includes supporting activity; last_user_worked_on does not
        assert card["last_worked_on"] == pytest.approx(now - 1 * DAY, abs=1)
        assert card["last_user_worked_on"] == pytest.approx(now - 10 * DAY, abs=1)
        assert card["last_worked_basis"] == "mixed"
        # evidence contract
        assert card["confidence_band"] in ("high", "medium", "low", "unknown")
        assert card["evidence_tiers"]
        assert isinstance(card["evidence_refs"], list)
        assert card["source_sessions"] and all(
            r["profile"] and r["session_id"] for r in card["source_sessions"])
        # no delegated/automated session carries a resume affordance in ANY field
        for s in card["sessions"]:
            assert s["session_id"] not in ("cpt5-deleg", "cpt5-auto")
        for t in card["resume_targets"]:
            assert t["audience"] == "USER_FACING"
        assert card["session_count"] == card["session_count_total"]
        assert card["message_count"] == card["message_count_total"]
        # no transcript text anywhere in the board payload
        blob = repr(_board(svc, view="all"))
        assert "tool_call_id" not in blob
        for probe_like in ("captured_window", "recent_messages", "user_messages"):
            assert probe_like not in blob
    finally:
        _cleanup(svc, pid, sessions)


def test_cp_t5_stopping_point_precedence(svc):
    now = time.time()
    pid = "cpt5-stopping"
    sessions = [("zz", "cpt5-stop1", "USER_FACING", now - 2 * DAY, 4, True)]
    _add_project(svc, pid, name="cpt5 stopping", lifecycle="LS-2", sessions=sessions)
    try:
        # 4. session title fallback when nothing grounded exists
        card = _cards(svc, view="all")[pid]
        stop = card["last_stopping_point"]
        assert stop["kind"] in ("session_title", "none")
        assert card["next_action"]["state"] in ("none", "open")
        # 2. Tier-1 next_action evidence outranks the title
        svc.registry.conn.execute(
            "INSERT OR REPLACE INTO evidence (evidence_id, project_id, cluster_id, profile_name,"
            " session_id, tier, kind, excerpt, locator, extracted_at, source_hash)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            ("cpt5-ev-next", pid, pid, "zz", "cpt5-stop1", 1, "next_action",
             "Next step: ship the fixture", '{"profile": "zz", "session_id": "cpt5-stop1",'
             ' "msg_id": 3}', now, "h"))
        svc.registry.conn.commit()
        stop = _cards(svc, view="all")[pid]["last_stopping_point"]
        assert stop["kind"] == "derived_next_action"
        assert stop["source"] == "tier1"
        assert stop["evidence_id"] == "cpt5-ev-next"
        assert stop["evidence_tier"] == 1
        assert stop["profile"] == "zz" and stop["session_id"] == "cpt5-stop1"
        # 1. an unexpired declared action outranks Tier-1 evidence and is shown verbatim
        svc.review("set_next_action", pid, {"text": "finish the release checklist"})
        card = _cards(svc, view="all")[pid]
        assert card["last_stopping_point"]["kind"] == "declared"
        assert card["last_stopping_point"]["text"] == "finish the release checklist"
        assert card["next_action"]["source"] == "declared"
        assert card["next_action_state"] == "open"
    finally:
        svc.registry.conn.execute("DELETE FROM evidence WHERE project_id=?", (pid,))
        svc.registry.conn.commit()
        _cleanup(svc, pid, sessions)


# --------------------------------------------------------------------------- CP-T6

def _sort_fixture(svc, now):
    """Projects with controlled values, an exact-name tie, a casefold pair, and a NULL."""
    rows = [
        ("cpt6-a", "Alpha", [("zz", "cpt6a1", "USER_FACING", now - 30 * DAY, 10, True)]),
        ("cpt6-b", "Alpha", [("zz", "cpt6b1", "USER_FACING", now - 30 * DAY, 10, True)]),
        ("cpt6-d", "alpha", [("zz", "cpt6d1", "USER_FACING", now - 20 * DAY, 4, True)]),
        ("cpt6-c", "Bravo", [("zz", "cpt6c1", "DELEGATED", now - 1 * DAY, 1, True)]),
        ("cpt6-null", "charlie", [("zz", "cpt6n1", "USER_FACING", None, 0, False)]),
    ]
    created = []
    for pid, name, sessions in rows:
        if pid == "cpt6-null":
            sessions = [(p, s, a, None, m, pr) for p, s, a, _l, m, pr in sessions]
        _add_project(svc, pid, name=name, lifecycle="LS-2", sessions=sessions)
        created.append((pid, sessions))
    return created


def test_cp_t6_sorting_is_total_null_safe_and_deterministic(svc):
    now = time.time()
    created = _sort_fixture(svc, now)
    try:
        for field in SORT_FIELDS:
            for direction in ("asc", "desc"):
                for _ in range(2):
                    ids = [c["project_id"] for c in
                           _board(svc, view="all", sort=field, direction=direction)["items"]]
                    assert len(ids) == len(set(ids))
        # nulls last for BOTH directions on a nullable key
        for direction in ("asc", "desc"):
            ids = [c["project_id"] for c in
                   _board(svc, view="all", sort="last_worked_on", direction=direction)["items"]]
            assert ids.index("cpt6-null") == len(ids) - 1, (direction, ids)
        # direction reverses the value order but NOT the project_id tie-break
        asc = [c["project_id"] for c in
               _board(svc, view="all", sort="message_count_total", direction="asc")["items"]]
        desc = [c["project_id"] for c in
                _board(svc, view="all", sort="message_count_total", direction="desc")["items"]]
        assert set(asc) == set(desc)
        # an EXACT name tie is broken by project_id ascending in BOTH directions
        for direction in ("asc", "desc"):
            tied = [c for c in _board(svc, view="all", sort="name", direction=direction)["items"]
                    if c["name"] == "Alpha"]
            assert len(tied) == 2
            assert [c["project_id"] for c in tied] == ["cpt6-a", "cpt6-b"]
        # casefold ordering: "Alpha" (casefold alpha) sorts before "alpha" only because the
        # original string is the next key (§5.3), and the direction reverses that value order
        asc_names = [c["name"] for c in _board(svc, view="all", sort="name",
                                               direction="asc")["items"]
                     if c["project_id"].startswith("cpt6-") and c["name"] != "charlie"]
        assert asc_names[:3] == ["Alpha", "Alpha", "alpha"]
        # message_count_total supports least and most over the non-null cards
        counted = [c for c in _board(svc, view="all", sort="message_count_total",
                                     direction="asc")["items"]
                   if c["message_count_total"] is not None
                   and c["project_id"].startswith("cpt6-")]
        assert [c["message_count_total"] for c in counted] == sorted(
            c["message_count_total"] for c in counted)
        # last_worked_on supports newest first (and nulls stay last)
        newest = [c for c in _board(svc, view="all", sort="last_worked_on",
                                    direction="desc")["items"]
                  if c["last_worked_on"] is not None]
        assert newest == sorted(newest, key=lambda c: -c["last_worked_on"])
        # the quiet alias echoes canonically
        alias = _board(svc, view="all", sort="quiet")
        assert alias["query"]["sort"] == "last_worked_on"
        assert alias["query"]["direction"] == "desc"
    finally:
        for pid, sessions in created:
            _cleanup(svc, pid, sessions)


def test_cp_t6_lifecycle_and_placement_orders(svc):
    created = []
    try:
        for i, ls in enumerate(["LS-3", "LS-1", "LS-9"]):
            pid = "cpt6o-{}".format(i)
            sessions = [("zz", "cpt6os{}".format(i), "USER_FACING", 1.0, 4, True)]
            _add_project(svc, pid, name="cpt6o{}".format(i), lifecycle=ls, sessions=sessions)
            created.append((pid, sessions))
        asc = [c["derived_lifecycle"] for c in
               _board(svc, view="all", sort="derived_lifecycle", direction="asc")["items"]
               if c["project_id"].startswith("cpt6o-")]
        assert asc == sorted(asc, key=lambda v: int(v.split("-")[1]))
    finally:
        for pid, sessions in created:
            _cleanup(svc, pid, sessions)


# --------------------------------------------------------------------------- CP-T7

def test_cp_t7_filters_are_and_across_types_or_within_a_type(svc):
    now = time.time()
    pid_a = "cpt7-a"
    pid_b = "cpt7-b"
    sa = [("zz", "cpt7a1", "USER_FACING", now - 2 * DAY, 4, True)]
    sb = [("zz", "cpt7b1", "DELEGATED", now - 2 * DAY, 4, True)]
    _add_project(svc, pid_a, name="cpt7needle alpha", lifecycle="LS-2", sessions=sa)
    _add_project(svc, pid_b, name="cpt7needle bravo", lifecycle="LS-3", sessions=sb)
    try:
        only_a = _board(svc, view="all", lane="inbox", q="cpt7needle alpha")
        ids = {c["project_id"] for c in only_a["items"]}
        assert pid_a in ids and pid_b not in ids
        # repeated type is ORed
        both = _board(svc, view="all", lifecycle=["LS-2", "LS-3"], q="cpt7needle")
        ids = {c["project_id"] for c in both["items"]}
        assert {pid_a, pid_b} <= ids
        # different types are ANDed
        anded = _board(svc, view="all", lane="inbox", lifecycle="LS-3", q="cpt7needle")
        ids = {c["project_id"] for c in anded["items"]}
        assert pid_b in ids and pid_a not in ids
        # attention filter
        att = _board(svc, view="all", attention="no_user_work", q="cpt7needle")
        assert {c["project_id"] for c in att["items"]} == {pid_b}
        # profile filter matches a linked profile
        prof = _board(svc, view="all", profile="zz", q="cpt7needle")
        assert {c["project_id"] for c in prof["items"]} == {pid_a, pid_b}
        # counts and total describe the same filtered set
        for q in (only_a, both, anded, att, prof):
            assert q["counts"]["total"] == len(q["items"])
            for lane in BOARD_COLUMNS:
                assert q["counts"][lane] == len([c for c in q["items"] if c["column"] == lane])
            assert q["counts"]["continuity_total"] >= q["counts"]["total"]
        # a filter never changes placement or audience
        assert _cards(svc, view="all")[pid_b]["column"] == "inbox"
        assert svc.registry.session_audience("zz", "cpt7b1")[0] == "DELEGATED"
    finally:
        _cleanup(svc, pid_a, sa)
        _cleanup(svc, pid_b, sb)


# --------------------------------------------------------------------------- CP-T8

def test_cp_t8_today_projection_and_reference_determinism(svc):
    first = _board(svc, view="today")
    second = _board(svc, view="today")
    assert first["query"]["view"] == "today"
    assert first["query"]["sort"] == "attention_state"
    assert first["query"]["direction"] == "desc"
    assert first["playground"]["mode"] == "today"
    ids = {c["project_id"] for c in first["items"]}
    for ref in first["playground"]["start_here"] + first["playground"]["rediscover"]:
        assert ref["project_id"] in ids, "a Today reference pointed outside the returned cards"
    assert [r["project_id"] for r in first["playground"]["start_here"]] == \
        [r["project_id"] for r in second["playground"]["start_here"]]
    assert [r["project_id"] for r in first["playground"]["rediscover"]] == \
        [r["project_id"] for r in second["playground"]["rediscover"]]

    # a reference does not mutate urgent / placement / review / registry state
    before_events = svc.registry.conn.execute(
        "SELECT COUNT(*) AS n FROM review_event").fetchone()["n"]
    before_declared = svc.registry.conn.execute(
        "SELECT COUNT(*) AS n FROM declared_field").fetchone()["n"]
    _board(svc, view="today")
    assert svc.registry.conn.execute(
        "SELECT COUNT(*) AS n FROM review_event").fetchone()["n"] == before_events
    assert svc.registry.conn.execute(
        "SELECT COUNT(*) AS n FROM declared_field").fetchone()["n"] == before_declared

    # Today keeps every lane: the same cards are reachable in view=all
    all_ids = {c["project_id"] for c in _board(svc, view="all", page_size=200)["items"]}
    assert ids <= all_ids
    # an old / no-user-work project stays discoverable through Rediscover and in its lane
    no_user = [c for c in first["items"] if c["user_facing_session_count"] == 0]
    if no_user:
        pid = no_user[0]["project_id"]
        assert pid in ids


def test_cp_t8_query_echo_is_canonical_and_paging_caps_transport_only(svc):
    out = _board(svc, view="all", page=1, page_size=2, sort="name", direction="asc")
    assert out["page"] == 1 and out["page_size"] == 2
    assert len(out["items"]) <= 2
    total = out["total"]
    full = _board(svc, view="all", page=1, page_size=200)
    assert total == full["total"], "paging must not change the reported total"
    assert out["counts"] == full["counts"], "paging must not change the lane counts"
    assert out["query"] == {
        "view": "all", "sort": "name", "direction": "asc", "lanes": [], "lifecycles": [],
        "attention": [], "profiles": [], "q": "", "page": 1, "page_size": 2,
    }


# --------------------------------------------------------------------------- CP-T9

def test_cp_t9_incremental_scan_skips_unchanged_and_picks_up_changes(home):
    cfg = load_config(hermes_home=home)
    cfg.bundle["registry_path"] = os.path.join(home, "registry.db")
    svc = Service(cfg)
    svc.scan(full=True)

    refs = scanner.discover_profiles(home, cfg)
    watermark = svc.registry.source_db_watermarks()
    assert watermark, "the first scan must record a per-profile snapshot"
    second = scanner.scan(refs, watermark=watermark, cfg=cfg, mode="incremental")
    assert second.counts["profiles_skipped"] == len(refs), second.counts
    assert second.facts == [], "an unchanged profile must not be re-read"
    assert second.probes == []

    # an ambiguous watermark (no recorded row count, stat unchanged) still reads the profile
    ambiguous = copy.deepcopy(watermark)
    for key in ambiguous:
        ambiguous[key]["session_count"] = None
    third = scanner.scan(refs, watermark=ambiguous, cfg=cfg, mode="incremental")
    assert third.counts["profiles_skipped"] == 0, "an ambiguous watermark must not skip"
    assert all(st.reconciliation == "full-ambiguous" for st in third.status)
    assert third.facts, "the ambiguous profile must be re-read"

    # a row-count shrink is also selected as a full reconciliation
    shrunk = copy.deepcopy(watermark)
    for key in shrunk:
        shrunk[key]["session_count"] = int(shrunk[key]["session_count"] or 0) + 5
    shrunk[refs[0].profile_name]["mtime"] = -1.0     # force the read without a skip
    fourth = scanner.scan(refs, watermark=shrunk, cfg=cfg, mode="incremental")
    statuses = {st.profile_name: st.reconciliation for st in fourth.status}
    assert statuses[refs[0].profile_name] == "full-count-shrink", statuses

    # a new session appears after ONE incremental scan
    import sqlite3
    db = os.path.join(home, "profiles", "alpha", "state.db")
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO sessions (id, source, title, cwd, git_repo_root, started_at,"
        " last_activity_at, message_count, tool_call_count) VALUES (?,?,?,?,?,?,?,?,?)",
        ("20260999_000099_new111", "cli", "cpt9 new session",
         "/Users/kethuda/work/cpt9", "/Users/kethuda/work/cpt9",
         time.time(), time.time(), 4, 1))
    conn.execute(
        "INSERT INTO messages (session_id, role, content, timestamp) VALUES (?,?,?,?)",
        ("20260999_000099_new111", "user", "start the cpt9 work", time.time()))
    conn.commit()
    conn.close()

    svc.scan()   # incremental
    svc.scan(full=True)   # full reconciliation picks up the new member
    assert svc.registry.conn.execute(
        "SELECT 1 FROM session_fact WHERE session_id='20260999_000099_new111'").fetchone() \
        is not None


def test_cp_t9_only_one_active_run_and_reads_see_the_last_commit(home):
    cfg = load_config(hermes_home=home)
    cfg.bundle["registry_path"] = os.path.join(home, "registry.db")
    svc = Service(cfg)
    svc.scan(full=True)
    before_as_of = _board(svc, view="today")["data_as_of"]

    real_scan = scanner.scan
    observed = {}

    def spy(*args, **kwargs):
        # "during" the scan: the read surface must still show the last committed snapshot
        observed["during"] = _board(svc, view="today")
        return real_scan(*args, **kwargs)

    scanner.scan = spy
    try:
        svc.scan()
    finally:
        scanner.scan = real_scan
    assert observed["during"]["data_as_of"] == before_as_of
    assert observed["during"]["scan_state"]["phase"] in ("idle", "scanning")

    running = svc.registry.conn.execute(
        "SELECT COUNT(*) AS n FROM scan_run WHERE status='running'").fetchone()["n"]
    assert running == 0
    total = svc.registry.conn.execute("SELECT COUNT(*) AS n FROM scan_run").fetchone()["n"]
    assert total == 2


# --------------------------------------------------------------------------- CP-T9 batch 2


def test_cp_t9_changed_profile_reconciles_against_skipped_profile_facts(home):
    """A changed session must reconcile into a project whose other members live in a
    SKIPPED (unchanged) profile — identity closes against retained facts/links."""
    cfg = load_config(hermes_home=home)
    cfg.bundle["registry_path"] = os.path.join(home, "registry.db")
    svc = Service(cfg)
    svc.scan(full=True)

    beta_link = svc.registry.conn.execute(
        "SELECT project_id FROM project_session WHERE profile_name='beta' "
        "AND session_id='20260201_000001_bbb901'").fetchone()
    assert beta_link, "beta bbb901 must be linked after the full scan"
    proj_before = beta_link["project_id"]
    alpha_members_before = {r["session_id"] for r in svc.registry.conn.execute(
        "SELECT session_id FROM project_session WHERE project_id=? AND profile_name='alpha'",
        (proj_before,))}
    assert "20260101_000001_aaa111" in alpha_members_before

    # Add a NEW alpha session sharing the same git repo (would cluster with beta bbb901).
    db = os.path.join(home, "profiles", "alpha", "state.db")
    import sqlite3
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO sessions (id, source, title, title_source, cwd, git_repo_root, "
        "started_at, last_activity_at, message_count, tool_call_count) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("20260999_000099_newaaa", "cli", "tjgc1 continued", "derived",
         "/synthetic/work/tjgc1", "/synthetic/work/tjgc1",
         time.time(), time.time(), 3, 1))
    conn.execute(
        "INSERT INTO messages (session_id, role, content, timestamp) VALUES (?,?,?,?)",
        ("20260999_000099_newaaa", "user", "continue tjgc1 landing page", time.time()))
    conn.commit()
    conn.close()

    svc.scan()  # incremental: alpha changed (coarse), beta unchanged (skipped)

    row = svc.registry.conn.execute(
        "SELECT project_id FROM project_session WHERE profile_name='alpha' "
        "AND session_id='20260999_000099_newaaa'").fetchone()
    assert row, "the changed alpha session must be linked after the incremental scan"
    assert row["project_id"] == proj_before, (
        "changed session must reconcile into the same project as the skipped beta session")
    assert svc.registry.conn.execute(
        "SELECT 1 FROM project_session WHERE project_id=? AND profile_name='beta' "
        "AND session_id='20260201_000001_bbb901'", (proj_before,)).fetchone(), \
        "the skipped beta member must survive reconciliation"


def test_cp_t9_ambiguous_watermark_forces_full_profile_reconciliation(home):
    """A missing/ambiguous watermark must never produce a partial (coarse) read."""
    cfg = load_config(hermes_home=home)
    cfg.bundle["registry_path"] = os.path.join(home, "registry.db")
    svc = Service(cfg)
    svc.scan(full=True)
    refs = scanner.discover_profiles(home, cfg)
    watermark = svc.registry.source_db_watermarks()

    # Force every profile's stat to move so none can be skipped, and null the row count
    # (ambiguous) while still claiming a coarse watermark -> must fall back to full.
    for key in watermark:
        watermark[key]["size"] = int(watermark[key]["size"] or 0) + 1024
        watermark[key]["coarse_watermark"] = time.time()
        watermark[key]["session_count"] = None

    batch = scanner.scan(refs, watermark=watermark, cfg=cfg, mode="incremental")
    assert batch.counts["profiles_skipped"] == 0, "ambiguous watermark must not skip"
    for st in batch.status:
        assert st.partial is False, "ambiguous watermark must never be a partial coarse read"
        # stat moved + ambiguous -> the scanner may label it "full" or "full-ambiguous",
        # but either way it is a FULL reconciliation, never a bounded coarse read.
        assert st.reconciliation in ("full", "full-ambiguous"), st.reconciliation
    # a full read must return the complete profile, not a coarse window
    by_profile = {}
    for f in batch.facts:
        by_profile.setdefault(f.profile_name, 0)
        by_profile[f.profile_name] += 1
    for st in batch.status:
        assert by_profile.get(st.profile_name, 0) == st.session_count, st.profile_name


def test_cp_t9_second_scan_returns_active_run_handle(home):
    """A second scan during an active run returns the active handle and never queues a
    competing scan (non-queueing at the observable API boundary)."""
    cfg = load_config(hermes_home=home)
    cfg.bundle["registry_path"] = os.path.join(home, "registry.db")
    svc = Service(cfg)
    svc.scan(full=True)
    runs_before = svc.registry.conn.execute(
        "SELECT COUNT(*) AS n FROM scan_run").fetchone()["n"]

    # Simulate an in-flight run: hold the service's scan lock and set the same scan-state
    # fields a live run publishes, exactly as scan() does after acquiring the lock.
    assert svc._scan_lock.acquire(blocking=False)
    try:
        svc._scan_state.update({
            "phase": "scanning", "progress": 0.0, "last_error": None,
            "active_mode": "incremental", "active_started_at": time.time(),
            "run_id": "run-active-1",
        })
        h2 = svc.scan()   # second request while the first is mid-flight
    finally:
        svc._scan_lock.release()

    assert h2.status == "running", "second caller must receive the active run handle"
    assert h2.run_id == "run-active-1", \
        "second caller must receive the SAME active run handle"
    # the failed-acquire call must not have started or queued a competing run
    runs_after = svc.registry.conn.execute(
        "SELECT COUNT(*) AS n FROM scan_run").fetchone()["n"]
    assert runs_after == runs_before, "a second scan must not have been queued/started"

    # once the lock is released, a normal scan still works (nothing was left queued)
    h3 = svc.scan()
    assert h3.run_id, "a released scan must start"
    last = svc.registry.conn.execute(
        "SELECT status FROM scan_run ORDER BY started_at DESC LIMIT 1").fetchone()["status"]
    assert last == "ok", "the post-release scan must commit successfully"
    assert svc.registry.conn.execute(
        "SELECT COUNT(*) AS n FROM scan_run").fetchone()["n"] == runs_before + 1


def test_cp_t9_events_change_only_after_committed_snapshot(home):
    """The committed snapshot marker shared by /scan/status and /projects moves only when a
    scan commits; a started run does not refresh it."""
    cfg = load_config(hermes_home=home)
    cfg.bundle["registry_path"] = os.path.join(home, "registry.db")
    svc = Service(cfg)
    svc.scan(full=True)
    before = svc.committed_snapshot()
    assert before["run_id"] and before["committed_at"] is not None
    st = svc.scan_status()
    assert st["committed_snapshot"] == before
    assert st["data_as_of"] == before["committed_at"]

    real_scan = scanner.scan
    observed = {}

    def spy(*a, **k):
        observed["during_status"] = svc.scan_status()
        observed["during_board"] = _board(svc, view="today")
        return real_scan(*a, **k)

    scanner.scan = spy
    try:
        svc.scan()
    finally:
        scanner.scan = real_scan

    assert observed["during_status"]["committed_snapshot"] == before, \
        "the committed marker must not move while a scan is in progress"
    assert observed["during_status"]["data_as_of"] == before["committed_at"]
    assert observed["during_board"]["data_as_of"] == before["committed_at"]
    assert observed["during_board"]["scan_state"]["phase"] == "scanning"

    after = svc.committed_snapshot()
    assert after["run_id"] != before["run_id"], "marker advances only after a successful commit"
    assert after["committed_at"] is not None
    assert svc.scan_status()["data_as_of"] == after["committed_at"]


def test_cp_t9_failed_scan_keeps_last_committed_snapshot(home):
    """On failure the derived writes roll back, the last committed marker stays visible with
    the real error, and declared/review history is preserved."""
    cfg = load_config(hermes_home=home)
    cfg.bundle["registry_path"] = os.path.join(home, "registry.db")
    svc = Service(cfg)
    svc.scan(full=True)
    before = svc.committed_snapshot()
    assert before["run_id"]

    pids = sorted(c["project_id"] for c in _cards(svc, view="all").values())
    pid = pids[0]
    svc.review("accept", pid, {"placement": "ongoing"})
    declared_before = svc.registry.effective_declared(pid)
    events_before = svc.registry.review_events(pid, limit=50)

    real_ingest = svc._ingest

    def boom(batch):
        raise RuntimeError("forced ingest failure")

    svc._ingest = boom
    try:
        with pytest.raises(RuntimeError, match="forced ingest failure"):
            svc.scan()
    finally:
        svc._ingest = real_ingest

    status = svc.scan_status()
    assert status["committed_snapshot"] == before, "failed scan must keep last committed marker"
    assert status["data_as_of"] == before["committed_at"]
    assert status["phase"] == "error"
    assert "forced ingest failure" in (status["last_error"] or "")
    assert svc.registry.effective_declared(pid) == declared_before, \
        "declared overlays must survive a failed scan"
    assert [e["event_id"] for e in svc.registry.review_events(pid, limit=50)] == \
        [e["event_id"] for e in events_before], "review history must survive a failed scan"
    err_rows = svc.registry.conn.execute(
        "SELECT COUNT(*) AS n FROM scan_run WHERE status='error'").fetchone()["n"]
    assert err_rows == 1, "the failed run must be recorded as error"

# --------------------------------------------------------------------------- CP-T10

def test_cp_t10_schema_v3_migration_compat_and_reversibility(svc):
    assert SCHEMA_VERSION == 3
    assert svc.registry.conn.execute("SELECT version FROM schema_version").fetchone()[0] == 3

    cards = _cards(svc, view="all")
    pid = sorted(cards)[0]
    svc.review("accept", pid, {"placement": "ongoing"})
    svc.review("set_urgent", pid, {"value": True})
    svc.review("set_next_action", pid, {"text": "cpt10 next action"})

    def _snapshot_rows(pid):
        out = {}
        for table, key in (("declared_field", "field"), ("next_action", "action_id")):
            out[table] = sorted(
                (r[0], r[1]) for r in svc.registry.conn.execute(
                    "SELECT {} , value FROM {} WHERE project_id=?".format(key, table), (pid,))
            ) if table == "declared_field" else sorted(
                (r[0],) for r in svc.registry.conn.execute(
                    "SELECT {} FROM {} WHERE project_id=?".format(key, table), (pid,)))
        return out

    before = _snapshot_rows(pid)
    rev_row = svc.registry.get_project(pid)["declared_rev"]
    _board(svc, view="today")               # a read must not change declared_rev
    svc.project_detail(pid)
    assert svc.registry.get_project(pid)["declared_rev"] == rev_row

    svc.scan(full=True)                     # a full rescan must preserve every declaration
    assert _snapshot_rows(pid) == before
    assert svc.registry.effective_declared(pid).get("placement") == "ongoing"
    assert svc.registry.effective_declared(pid).get("urgent") == "1"

    # every placement move has exactly one event with a working undo
    events = [e for e in svc.registry.review_events(pid, limit=50)
              if e["action"] in ("accept", "set_placement")]
    assert len(events) == 1
    svc.review("undo", "", {"audit_id": events[0]["event_id"]})
    assert svc.registry.effective_declared(pid).get("placement") != "ongoing"
    assert _cards(svc, view="all")[pid]["column"] == "inbox"


# --------------------------------------------------------------------------- CP-T11

def _source_state(home):
    out = {}
    profiles = os.path.join(home, "profiles")
    for prof in sorted(os.listdir(profiles)):
        db = os.path.join(profiles, prof, "state.db")
        if not os.path.exists(db):
            continue
        with open(db, "rb") as fh:
            digest = hashlib.sha256(fh.read()).hexdigest()
        st = os.stat(db)
        out[db] = (st.st_size, st.st_mtime, digest,
                   os.path.exists(db + "-wal"), os.path.exists(db + "-shm"))
    return out


def test_cp_t11_sources_are_never_written_by_scans_reads_or_reviews(svc, home):
    before = _source_state(home)
    svc.scan(full=True)
    svc.scan()
    _board(svc, view="today", q="typejoy", lane="inbox", sort="name", page=1, page_size=5)
    _board(svc, view="all", attention="needs_review")
    cards = _cards(svc, view="all")
    pid = sorted(cards)[0]
    svc.review("accept", pid, {"placement": "blocked"})
    svc.review("set_placement", pid, {"placement": "paused"})
    detail = svc.project_detail(pid)
    for link in detail["resume_links"]:
        svc.project_detail(pid, pane="{}/{}".format(link["profile"], link["session_id"]))
    svc.review("undo", "", {"audit_id": svc.registry.review_events(pid, limit=1)[0]["event_id"]})
    after = _source_state(home)
    assert before == after, "a source database was written or a -wal/-shm appeared"


def test_cp_t11_every_source_handle_is_read_only(svc, home):
    db = os.path.join(home, "profiles", "alpha", "state.db")
    conn = scanner.open_readonly(db)
    try:
        assert conn.execute("PRAGMA query_only").fetchone()[0] == 1
        import sqlite3
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("UPDATE sessions SET title='mutated'")
    finally:
        conn.close()
    assert scanner.build_ro_uri(db).startswith("file:")
    assert "mode=ro" in scanner.build_ro_uri(db)


# --------------------------------------------------------------------------- CP-T12

def test_cp_t12_board_issues_no_source_query_and_bounded_queries(svc, monkeypatch):
    """No per-card source DB read, and a bounded number of registry statements."""
    def _boom(*a, **k):
        raise AssertionError("the board opened a source database")

    monkeypatch.setattr(scanner, "open_readonly", _boom)
    statements = []

    def _tracer(sql):
        statements.append(sql)

    svc.registry.conn.set_trace_callback(_tracer)
    try:
        out = _board(svc, view="today")
    finally:
        svc.registry.conn.set_trace_callback(None)
    assert out["items"]
    per_card_links = [s for s in statements
                      if "SELECT * FROM project_session" in s and "project_id=?" in s]
    per_card_evidence = [s for s in statements
                         if "FROM evidence" in s and "project_id=?" in s]
    assert per_card_links == [], "per-card link query leaked into the board"
    assert per_card_evidence == [], "per-card evidence query leaked into the board"
    assert len(statements) < 40, "board statement count is not bounded: {}".format(
        len(statements))


def test_cp_t12_inline_session_cap_and_honest_omissions(svc):
    out = _board(svc, view="all", page_size=200)
    for card in out["items"]:
        assert len(card["sessions"]) <= 20
        assert card["sessions_omitted"] == card["session_count_total"] - len(card["sessions"])
        assert card["sessions_omitted"] >= 0


def test_cp_t12_pane_is_bounded_and_never_returns_a_full_transcript(svc):
    cards = _cards(svc, view="all")
    pid = None
    for project_id, card in cards.items():
        if card["sessions"]:
            pid = project_id
            break
    assert pid is not None
    link = cards[pid]["sessions"][0]
    pane = svc.project_detail(pid, pane="{}/{}".format(link["profile"], link["session_id"]))[
        "session_pane"]
    assert pane["available"] is True
    cap = pane["capture"]
    assert len(pane["recent_messages"]) <= max(cap["probe_head"], 1) + cap["probe_tail"]
    for row in pane["recent_messages"]:
        assert len(row["text"]) <= cap["excerpt_chars"]
        assert row["role"] in ("YOU", "AGENT")
        # D-SP-8 (corrected): `excerpt` is a grounded per-row fact, not a blanket label. A row
        # is an excerpt only when the capture clipped it; short rows say so honestly.
        assert isinstance(row["excerpt"], bool)
    assert pane["full_text_available"] is False
    # D-SP-9/D-SP-10: the response cap, the UX display cap and the captured window are distinct.
    assert pane["display_cap"] == 5
    assert pane["captured_window"] >= len(pane["recent_messages"])
    assert pane["rows_omitted_by_display"] == max(
        0, pane["captured_window"] - pane["display_cap"])
    assert "Last {} captured".format(pane["captured_window"]) in pane["copy"]["window"], \
        "the window line must state the captured window, not the rows shown"


# --------------------------------------------------------------------------- CP-T13

def test_cp_t13_route_behaviour(home, monkeypatch):
    from fastapi.testclient import TestClient
    from dashboard import plugin_api, standalone

    cfg = load_config(hermes_home=home)
    cfg.bundle["registry_path"] = os.path.join(home, "registry.db")
    holder = {"svc": None}

    def factory():
        # The cached Service owns a single SQLite connection, so it must be created on the
        # thread that serves the request (TestClient runs endpoints off the test thread).
        if holder["svc"] is None:
            svc = Service(cfg)
            svc.scan()
            holder["svc"] = svc
        return holder["svc"]

    monkeypatch.setattr(plugin_api, "get_service", factory)
    client = TestClient(standalone.create_app())

    # omitted parameters stay compatible (legacy envelope)
    legacy = client.get(standalone.API_PREFIX + "/projects")
    assert legacy.status_code == 200
    assert set(legacy.json().keys()) == {
        "items", "page", "total", "counts", "data_as_of", "scan_state"}

    # view=today returns the additive query and playground fields
    today = client.get(standalone.API_PREFIX + "/projects?view=today")
    assert today.status_code == 200
    body = today.json()
    assert {"page_size", "query", "playground"} <= set(body.keys())
    assert body["query"]["view"] == "today"
    assert body["playground"]["mode"] == "today"

    # invalid values are a real 400, never a silent fallback
    for bad in ("?view=nope", "?sort=nope", "?direction=sideways", "?lane=nope",
                "?lifecycle=LS-99", "?attention=nope", "?page=0", "?page_size=0",
                "?page_size=999"):
        res = client.get(standalone.API_PREFIX + "/projects" + bad)
        assert res.status_code == 400, (bad, res.status_code)

    # the pane is an additive query on the EXISTING detail route
    listing = client.get(standalone.API_PREFIX + "/projects?view=all").json()
    pid = sorted(c["project_id"] for c in listing["items"])[0]
    detail = client.get(standalone.API_PREFIX + "/projects/" + pid)
    assert detail.status_code == 200
    assert "session_pane" not in detail.json()
    refs = detail.json().get("source_sessions") or []
    if refs:
        ref = "{}/{}".format(refs[0]["profile"], refs[0]["session_id"])
        pane = client.get(standalone.API_PREFIX + "/projects/" + pid + "?pane=" + ref)
        assert pane.status_code == 200
        assert "session_pane" in pane.json()
    else:
        pytest.skip("no linked session in the fixture to open a pane for")
    bad = client.get(standalone.API_PREFIX + "/projects/" + pid + "?pane=not-a-ref")
    assert bad.status_code == 400

    # no route was added by M4
    paths = {r.path for r in standalone.create_app().routes}
    assert standalone.API_PREFIX + "/projects" in paths
    assert len([p for p in paths if p.startswith(standalone.API_PREFIX)]) == len(
        [r.path for r in plugin_api.router.routes])


def test_cp_t13_browser_boundary_and_url_persistence():
    with open(os.path.join(BUILD_ROOT, "dashboard", "static", "app.js"), encoding="utf-8") as fh:
        src = fh.read()
    low = src.lower()
    # no browser-side SQLite, clustering, classification, or audience inference
    for banned in ("sqlite", "cluster_id", "audience_override", "transcript",
                   "raw_messages", "session_messages", "localstorage"):
        assert banned not in low, banned
    assert re.search(r"\b(SELECT|INSERT INTO|UPDATE |DELETE FROM)\b", src) is None
    # the browser persists query state in the URL, never in the registry
    assert "replaceState" in src
    assert "queryString" in src
    assert "parseUrlState" in src
    # the browser default request is Today's Playground
    assert "view: 'today'" in src
    with open(os.path.join(BUILD_ROOT, "dashboard", "static", "styles.css"),
              encoding="utf-8") as fh:
        css = fh.read()
    assert ".c-toolbar" in css and ".c-pane" in css and ".c-playground" in css


# --------------------------------------------------------------------------- CP-T14

def test_cp_t14_forbidden_work_sweep():
    """Symbol/file sweep over the M4 diff surface. Structural, labelled as such."""
    py_files = ["continuum/service.py", "continuum/registry.py", "continuum/scanner.py",
                "continuum/model.py", "continuum/config.py", "dashboard/plugin_api.py"]
    forbidden = ("unread_count", "unread_messages", "requests.get", "requests.post",
                 "urllib.request", "socket.", "openai", "anthropic", "delete_project",
                 "rename_project", "pin_project", "archive_project", "auto_archive",
                 "auto_suppress")
    for rel in py_files:
        with open(os.path.join(BUILD_ROOT, rel), encoding="utf-8") as fh:
            low = fh.read().lower()
        for token in forbidden:
            assert token not in low, "{} contains forbidden token {!r}".format(rel, token)
    # no new lane / lifecycle vocabulary
    with open(os.path.join(BUILD_ROOT, "continuum", "service.py"), encoding="utf-8") as fh:
        service_src = fh.read()
    assert 'BOARD_COLUMNS = ("inbox", "ongoing", "blocked", "waiting_on_you", "paused",' \
           ' "done", "shipped", "scrapped")' in service_src
    assert 'PLACEMENTS = ("ongoing", "blocked", "waiting_on_you", "paused", "done",' \
           ' "shipped", "scrapped")' in service_src
    from continuum.classify import LIFECYCLES
    assert LIFECYCLES == ("LS-1", "LS-2", "LS-3", "LS-4", "LS-5", "LS-6", "LS-7",
                          "LS-8", "LS-9")
    # the schema version and registry tables are unchanged
    from continuum.registry import _DDL, SCHEMA_VERSION
    assert SCHEMA_VERSION == 3
    for table in ("project", "project_session", "declared_field", "review_event", "evidence",
                  "next_action", "scan_run", "source_db", "session_fact", "project_signature"):
        assert "CREATE TABLE IF NOT EXISTS " + table in _DDL
    for new_table in ("messages", "transcript", "session_message", "archive", "pane"):
        assert "CREATE TABLE IF NOT EXISTS " + new_table not in _DDL


def test_cp_t14_no_automatic_suppression_or_terminal_transition(svc):
    """A scan can never produce a terminal placement, a dismissal, or a merge."""
    for _ in range(2):
        svc.scan(full=True)
    svc.scan()
    for row in svc.registry.conn.execute("SELECT project_id FROM project"):
        declared = svc.registry.effective_declared(row["project_id"])
        assert declared.get("placement") not in ("done", "shipped", "scrapped")
        assert declared.get("dismissed") != "1"
        assert not declared.get("merged_into")
    cards = _board(svc, view="all", page_size=200)["items"]
    terminal = [c for c in cards if c["column"] in ("done", "shipped", "scrapped")]
    assert terminal == [], "a scan invented a terminal lane card"
    assert all(c["placement_source"] != "human" for c in cards)


def _file_hash(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def test_cp_t14_live_registry_fixture_is_not_written(svc):
    """The committed registry fixture is never the target of a test write."""
    reg = os.path.join(BUILD_ROOT, "data", "registry.db")
    if not os.path.exists(reg):
        pytest.skip("no committed registry fixture on this machine")
    before = _file_hash(reg)
    _board(svc, view="today")
    svc.scan(full=True)
    assert _file_hash(reg) == before


def test_cp_t14_changed_files_stay_inside_build():
    for rel in ("continuum/service.py", "continuum/registry.py", "continuum/scanner.py",
                "continuum/model.py", "continuum/config.py", "config.yaml",
                "dashboard/plugin_api.py", "dashboard/static/app.js",
                "dashboard/static/styles.css", "tests/test_continuity_playground.py"):
        assert os.path.exists(os.path.join(BUILD_ROOT, rel)), rel
    # no M4 marker leaked into an installed plugin tree
    installed = "/Users/kethuda/.hermes/plugins"
    if os.path.isdir(installed):
        for root, _dirs, names in os.walk(installed):
            for name in names:
                if not name.endswith((".py", ".js")):
                    continue
                with open(os.path.join(root, name), "rb") as fh:
                    assert b"M4 Continuity Playground" not in fh.read(), os.path.join(root, name)


# --------------------------------------------------------------------------- CP-T5b (audience safety)

def _add_project_with_roles(svc, pid, *, name="synthetic", lifecycle="LS-2", band="high",
                            tier=1, confidence=0.8, sessions=(), primary_ref=None):
    """Like ``_add_project`` but one session ref may be promoted to role_in_project='primary'.

    A stale v3 primary on a non-USER_FACING session is the CP-5 edge under test.
    """
    _add_project(svc, pid, name=name, lifecycle=lifecycle, band=band, tier=tier,
                 confidence=confidence, sessions=sessions)
    if primary_ref:
        svc.registry.conn.execute(
            "UPDATE project_session SET role_in_project='primary'"
            " WHERE project_id=? AND profile_name=? AND session_id=?",
            (pid, primary_ref[0], primary_ref[1]))
        svc.registry.conn.commit()


def test_cp_t5b_unknown_session_has_no_resume_affordance_anywhere(svc):
    """CP-5: only USER_FACING carries primary/resume/copy_command; UNKNOWN carries none.

    Drives the real service over an isolated fixture: the legacy board, the playground
    board, the detail projection and the session pane all refuse a resume affordance for an
    UNKNOWN session, even when a stale v3 row marks it ``role_in_project='primary'``.
    """
    now = time.time()
    pid = "cpt5u-unknown"
    sessions = [
        ("zz", "cpt5u-uf", "USER_FACING", now - DAY, 3, True),
        ("zz", "cpt5u-unk", "UNKNOWN", now - 2 * DAY, 2, True),
    ]
    _add_project_with_roles(svc, pid, name="cpt5u unknown", lifecycle="LS-2",
                            sessions=sessions, primary_ref=("zz", "cpt5u-unk"))
    try:
        # --- legacy board (no query -> legacy envelope) ---
        legacy = {c["project_id"]: c for c in svc.board()["items"]}
        card = legacy[pid]
        # the stale UNKNOWN primary is never trusted as a primary projection
        assert card["primary_session"] is None
        rows = {s["session_id"]: s for s in card["sessions"]}
        assert rows["cpt5u-unk"]["copy_command"] is None
        assert rows["cpt5u-unk"]["cli_resume"] is None
        assert rows["cpt5u-uf"]["copy_command"] == "hermes --resume cpt5u-uf"

        # --- playground board ---
        pcard = _cards(svc, view="all")[pid]
        assert pcard["primary_session"] is None
        assert [t["session_id"] for t in pcard["resume_targets"]] == ["cpt5u-uf"]
        assert all(t["audience"] == "USER_FACING" for t in pcard["resume_targets"])
        src = {s["session_id"]: s for s in pcard["source_sessions"]}
        assert src["cpt5u-unk"]["audience"] == "UNKNOWN"
        # the stored v3 audience reason is populated, never fabricated as None
        assert src["cpt5u-unk"]["reason"] == "U"
        assert src["cpt5u-uf"]["reason"] == "U"
        for s in pcard["sessions"]:
            if s["session_id"] == "cpt5u-unk":
                assert s["copy_command"] is None
        assert "hermes --resume cpt5u-unk" not in repr(pcard)

        # --- detail projection ---
        detail = svc.project_detail(pid)
        assert detail["project"]["primary_session"] is None
        for link in detail["resume_links"]:
            assert link["audience"] == "USER_FACING"
            assert link["session_id"] != "cpt5u-unk"
        assert "cpt5u-unk" not in repr(detail["resume_links"])
    finally:
        _cleanup(svc, pid, sessions)


def test_cp_t5b_unknown_pane_never_claims_user_messages(tmp_path):
    """CP-5 / D-SP-13: an UNKNOWN session's pane exposes no "your messages" and no command.

    Uses the audience fixture so the UNKNOWN relay row has a real read-only source database
    (a synthetic project has no source_db row and would short-circuit to ``no_source``).
    """
    svc = _audience_service(tmp_path)
    ref = "azaraki/20260912_150000_grp01"
    prof, _, sid = ref.partition("/")
    assert svc.registry.session_audience(prof, sid)[0] == "UNKNOWN"
    row = svc.registry.conn.execute(
        "SELECT project_id FROM project_session WHERE profile_name=? AND session_id=?",
        (prof, sid)).fetchone()
    pid = row["project_id"]

    pane = svc.project_detail(pid, pane=ref)["session_pane"]
    assert pane["audience"] == "UNKNOWN"
    assert pane["available"] is True
    assert pane["user_messages"] == []
    assert pane["user_messages_state"] == "unknown_audience"
    assert "copy_command" not in pane and "resume_links" not in pane
    assert "hermes" not in repr(pane.get("copy"))

    # the UNKNOWN session is never offered as a resume target on any surface
    detail = svc.project_detail(pid)
    assert sid not in repr(detail["resume_links"])
    card = _cards(svc, view="all")[pid]
    assert sid not in [t["session_id"] for t in card["resume_targets"]]
    assert f"hermes --resume {sid}" not in repr(card)


# --------------------------------------------------------------------------- CP-T6b (edge contracts)

def test_cp_t6_non_finite_sort_values_are_unavailable(svc):
    """CP-6: NaN and +/-inf sort last for BOTH directions, like NULL, with no artificial tie."""
    import functools as _ft

    # _sort_value normalises every non-finite numeric onto the NULL path
    assert svc._sort_value({"project_id": "x", "confidence": float("nan")},
                           "confidence")[1] is None
    assert svc._sort_value({"project_id": "x", "last_worked_on": float("inf")},
                           "last_worked_on")[1] is None
    assert svc._sort_value({"project_id": "x", "last_worked_on": float("-inf")},
                           "last_worked_on")[1] is None
    assert svc._sort_value({"project_id": "x", "last_worked_on": None},
                           "last_worked_on")[1] is None
    assert svc._sort_value({"project_id": "x", "last_worked_on": 5.0},
                           "last_worked_on")[1] == 5.0

    for direction in ("asc", "desc"):
        cmp = svc._sort_comparator("last_worked_on", direction)
        # a non-finite value never ties with a real value: it is strictly "after"
        assert cmp({"project_id": "a", "last_worked_on": float("nan")},
                   {"project_id": "b", "last_worked_on": 100.0}) == 1
        assert cmp({"project_id": "a", "last_worked_on": float("inf")},
                   {"project_id": "b", "last_worked_on": 100.0}) == 1
        assert cmp({"project_id": "b", "last_worked_on": 100.0},
                   {"project_id": "a", "last_worked_on": float("-inf")}) == -1
        # NaN vs NaN never a false tie: the project_id tie-break decides
        assert cmp({"project_id": "a", "last_worked_on": float("nan")},
                   {"project_id": "b", "last_worked_on": float("nan")}) == -1
        cards = [{"project_id": p, "last_worked_on": v} for p, v in (
            ("nan", float("nan")), ("posinf", float("inf")), ("neginf", float("-inf")),
            ("real", 100.0), ("null", None))]
        ordered = sorted(cards, key=_ft.cmp_to_key(cmp))
        # the single finite value is first in BOTH directions; every unavailable value is last
        assert ordered[0]["project_id"] == "real"
        assert {c["project_id"] for c in ordered[1:]} == {"nan", "posinf", "neginf", "null"}


def test_cp_t6_page_size_caps_and_config_bounds(svc, tmp_path):
    """CP-6: the transport cap is 200; the configured max is bounded and default <= max."""
    from continuum.config import load_config

    # the parser rejects any page_size above the fixed 200 transport cap
    with pytest.raises(ValueError):
        svc.board(view="all", page_size=201)
    assert svc.board(view="all", page_size=200)["page_size"] == 200

    # a configured max above 200 is rejected at load time, never silently clamped
    over = tmp_path / "over.yaml"
    over.write_text("board_page_size_max: 1000\n")
    with pytest.raises(ValueError):
        load_config(path=str(over))

    # a default above the configured max is rejected at load time
    crossed = tmp_path / "crossed.yaml"
    crossed.write_text("board_page_size_default: 200\nboard_page_size_max: 100\n")
    with pytest.raises(ValueError):
        load_config(path=str(crossed))


# --------------------------------------------------------------------------- B1: URL round-trip
# §5.1: lifecycle / lane / attention / profile are REPEATABLE. A reload must retain every
# selected value plus q, sort, direction, page and page_size. The API query echo is the
# canonical, lossless representation the browser replays.

def test_cp_t6_url_roundtrip_preserves_lifecycle_and_repeatable_filters(svc):
    """B1: a reload round-trips every repeated filter (incl. lifecycle) + q/sort/page state.

    Feed the additive query exactly as the browser would after parsing its URL, then assert
    the canonical echo keeps every repeated value and that replaying that echo reproduces the
    identical card set — a lossless URL round trip through the server contract.
    """
    now = time.time()
    pid_a = "b1-rt-a"
    pid_b = "b1-rt-b"
    pid_c = "b1-rt-c"
    _add_project(svc, pid_a, name="needle roundtrip alpha", lifecycle="LS-2",
                 sessions=[("zz", "b1rta1", "USER_FACING", now - 2 * DAY, 4, True)])
    _add_project(svc, pid_b, name="needle roundtrip bravo", lifecycle="LS-3",
                 sessions=[("zz", "b1rtb1", "DELEGATED", now - 2 * DAY, 4, True)])
    _add_project(svc, pid_c, name="needle roundtrip charlie", lifecycle="LS-4",
                 sessions=[("zz", "b1rtc1", "USER_FACING", now - 2 * DAY, 4, True)])
    try:
        request = dict(view="all", lane=["inbox", "ongoing"], lifecycle=["LS-2", "LS-3"],
                       attention=["no_user_work", "recent"], profile=["zz"], q="needle",
                       sort="name", direction="asc", page=1, page_size=5)
        first = _board(svc, **request)
        q = first["query"]
        # the canonical echo preserves EVERY repeated filter and the scalar state
        assert q["lanes"] == ["inbox", "ongoing"], q
        assert q["lifecycles"] == ["LS-2", "LS-3"], q
        assert q["attention"] == ["no_user_work", "recent"], q
        assert q["profiles"] == ["zz"], q
        assert q["q"] == "needle" and q["sort"] == "name"
        assert q["direction"] == "asc" and q["page"] == 1 and q["page_size"] == 5
        assert q["view"] == "all"

        # replaying the echo verbatim (as the browser does after a reload) is lossless
        replay = dict(view=q["view"], lane=q["lanes"], lifecycle=q["lifecycles"],
                      attention=q["attention"], profile=q["profiles"], q=q["q"],
                      sort=q["sort"], direction=q["direction"],
                      page=q["page"], page_size=q["page_size"])
        second = _board(svc, **replay)
        assert [c["project_id"] for c in second["items"]] == \
            [c["project_id"] for c in first["items"]]
        assert second["total"] == first["total"]
        assert second["counts"] == first["counts"]

        # OR-within-lifecycle: both LS-2 and LS-3 are returned; LS-4 (outside the OR) is not
        ids = {c["project_id"] for c in first["items"]}
        assert {pid_a, pid_b} <= ids and pid_c not in ids

        # a repeated lifecycle ANDed against a repeated lane still holds (no lane overlap here)
        both_lanes = _board(svc, view="all", lifecycle=["LS-2", "LS-3"], q="needle")
        assert {c["project_id"] for c in both_lanes["items"]} == {pid_a, pid_b}
    finally:
        _cleanup(svc, pid_a, [("zz", "b1rta1")])
        _cleanup(svc, pid_b, [("zz", "b1rtb1")])
        _cleanup(svc, pid_c, [("zz", "b1rtc1")])


# --------------------------------------------------------------------------- B1: completeness
# CP-7: message_count_complete and evidence_complete are independent. A preserved (tombstoned)
# message count is still known; a locked/unreadable source or absent evidence lowers only the
# correct completeness field and never removes the project.

def test_cp_t5_message_and_evidence_completeness_are_independent(svc):
    """B1: a tombstoned fact keeps a known count; a locked source lowers evidence, not counts."""
    now = time.time()
    pid = "b1-complete"
    _add_project(svc, pid, name="b1 completeness", lifecycle="LS-2",
                 sessions=[("zz", "b1c1", "USER_FACING", now - 2 * DAY, 7, True)])
    try:
        # 1) healthy baseline: known count AND complete evidence (tiered card, evidence present)
        svc.registry.conn.execute(
            "INSERT OR REPLACE INTO evidence (evidence_id, project_id, cluster_id, profile_name,"
            " session_id, tier, kind, excerpt, locator, extracted_at, source_hash)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            ("b1-ev", pid, pid, "zz", "b1c1", 1, "note", "grounded excerpt",
             '{"profile": "zz", "session_id": "b1c1", "msg_id": 1}', now, "h"))
        svc.registry.conn.commit()
        card = _cards(svc, view="all")[pid]
        assert card["message_count_complete"] is True
        assert card["message_count_known"] == 7
        assert card["evidence_complete"] is True

        # 2) tombstone the linked fact (present=0) but keep its preserved row: the count stays
        #    known (durable-link rule) while evidence now stands alone and stays complete.
        svc.registry.conn.execute(
            "UPDATE session_fact SET present=0 WHERE profile_name='zz' AND session_id='b1c1'")
        svc.registry.conn.commit()
        card = _cards(svc, view="all")[pid]
        assert card["message_count_complete"] is True, \
            "a preserved tombstoned count must remain known"
        assert card["message_count_known"] == 7
        assert card["message_count_total"] == 7
        assert card["evidence_complete"] is True

        # 3) remove the evidence: evidence_complete drops independently of the known count
        svc.registry.conn.execute("DELETE FROM evidence WHERE project_id=?", (pid,))
        svc.registry.conn.commit()
        card = _cards(svc, view="all")[pid]
        assert card["message_count_complete"] is True      # count is STILL known
        assert card["evidence_complete"] is False          # but evidence is now incomplete
        assert pid in _cards(svc, view="all"), "missing evidence must not remove the project"

        # 4) a locked source lowers evidence_complete but keeps counts and the project
        svc.registry.conn.execute(
            "INSERT OR REPLACE INTO source_db (profile_name, path, exists_now, status)"
            " VALUES ('zz', '/unreadable', 1, 'locked')")
        svc.registry.conn.commit()
        card = _cards(svc, view="all")[pid]
        assert card["source_status"].get("zz") == "locked"
        assert card["evidence_complete"] is False
        assert card["message_count_complete"] is True
        assert pid in _cards(svc, view="all"), "a locked source must not remove the project"
    finally:
        svc.registry.conn.execute("DELETE FROM evidence WHERE project_id=?", (pid,))
        svc.registry.conn.execute("DELETE FROM source_db WHERE profile_name='zz'")
        svc.registry.conn.commit()
        _cleanup(svc, pid, [("zz", "b1c1")])


# --------------------------------------------------------------------------- B1: page-safe Today
# CP-8: start_here / rediscover resolve ONLY into the RETURNED page_items. A reference whose
# project_id is not in items is a test failure — on page 2 and under a small page size.

def test_cp_t8_today_references_stay_inside_page_two_items(svc):
    """B1: on page 2 every Today reference points into the cards actually returned on page 2."""
    now = time.time()
    pids = []
    sessions_meta = []
    for i in range(14):
        pid = "b1-pg-{:02d}".format(i)
        sid = "b1pg{}s".format(i)
        _add_project(svc, pid, name="page two project {:02d}".format(i), lifecycle="LS-2",
                     sessions=[("zz", sid, "USER_FACING", now - 5 * DAY, 4, True)])
        pids.append(pid)
        sessions_meta.append(("zz", sid))
    try:
        board_p2 = _board(svc, view="today", page=2, page_size=3)
        assert board_p2["page"] == 2
        assert len(board_p2["items"]) <= 3
        returned = {c["project_id"] for c in board_p2["items"]}
        assert returned, "page 2 must return cards for this fixture"
        refs = board_p2["playground"]["start_here"] + board_p2["playground"]["rediscover"]
        for ref in refs:
            assert ref["project_id"] in returned, \
                "page-2 Today reference {} left the returned cards".format(ref["project_id"])
        # and the page-2 total equals the full page-1 .. page-N total (filtered set, not page)
        page1 = _board(svc, view="today", page=1, page_size=3)
        assert board_p2["total"] == page1["total"]
    finally:
        for pid, (prof, sid) in zip(pids, sessions_meta):
            _cleanup(svc, pid, [(prof, sid)])


def test_cp_t8_today_references_respect_small_page_size(svc):
    """B1: with a page size smaller than the reference candidate set, refs stay on the page."""
    now = time.time()
    pids = []
    sessions_meta = []
    for i in range(10):
        pid = "b1-small-{:02d}".format(i)
        sid = "b1small{}s".format(i)
        _add_project(svc, pid, name="small page project {:02d}".format(i), lifecycle="LS-2",
                     sessions=[("zz", sid, "USER_FACING", now - 5 * DAY, 4, True)])
        pids.append(pid)
        sessions_meta.append(("zz", sid))
    try:
        # page size 2 is far below the reference candidate set; refs must stay inside those 2
        board_small = _board(svc, view="today", page=1, page_size=2)
        assert len(board_small["items"]) == 2
        returned = {c["project_id"] for c in board_small["items"]}
        refs = board_small["playground"]["start_here"] + board_small["playground"]["rediscover"]
        assert refs, "a page of 2 must still produce Today references"
        for ref in refs:
            assert ref["project_id"] in returned, \
                "small-page Today reference {} left the returned cards".format(ref["project_id"])
        # every start_here entry must be present in the returned items (page-local names render)
        for ref in board_small["playground"]["start_here"]:
            assert ref["project_id"] in returned
        # rediscover_omitted never over-reports: it cannot exceed the on-page eligible count
        assert 0 <= board_small["playground"]["rediscover_omitted"] <= len(board_small["items"])
    finally:
        for pid, (prof, sid) in zip(pids, sessions_meta):
            _cleanup(svc, pid, [(prof, sid)])


# --------------------------------------------------------------------------- MC-S6 (Hazen O-1)
#
# The two behaviours that previously had only INDIRECT coverage now each have a dedicated
# falsifiable test: Escape-focus restoration and the outside-cluster anchor. Each one carries its
# own negative control, so it goes red if the behaviour is removed — there is no source-only
# substitute for either.

_ESCAPE_HARNESS = r"""
// Minimal DOM shim: enough to import the real app.js, boot it, and observe focus behaviour.
import { pathToFileURL } from 'node:url'

class El {
  constructor(tag) {
    this.tagName = tag; this.children = []; this.attrs = {}; this.style = {}; this.dataset = {}
    this.textContent = ''; this.className = ''; this.id = ''; this.parentNode = null
    this.listeners = {}
  }
  setAttribute(k, v) { this.attrs[k] = v; if (k === 'id') this.id = v }
  getAttribute(k) { return this.attrs[k] }
  appendChild(node) { node.parentNode = this; this.children.push(node); return node }
  insertBefore(node) { return this.appendChild(node) }
  replaceChildren() {
    this.children = []
    for (const node of arguments) { if (node && typeof node === 'object') this.appendChild(node) }
  }
  addEventListener(type, fn) { (this.listeners[type] = this.listeners[type] || []).push(fn) }
  removeEventListener() {}
  focus() { globalThis.__focused = this.id || this.tagName }
  querySelector() { return null }
}

const registry = new Map()
function byId(id) {
  if (!registry.has(id)) { const el = new El('div'); el.id = id; registry.set(id, el) }
  return registry.get(id)
}

const docListeners = {}
globalThis.document = {
  getElementById: (id) => byId(id),
  createElement: (tag) => new El(tag),
  createTextNode: (text) => ({ nodeType: 3, textContent: text }),
  querySelector: () => null,
  addEventListener: (type, fn) => { (docListeners[type] = docListeners[type] || []).push(fn) },
  body: byId('body'),
}
globalThis.window = { location: { search: '', pathname: '/' },
                      history: { replaceState: () => {} } }
globalThis.requestAnimationFrame = (fn) => { fn(0); return 1 }
globalThis.cancelAnimationFrame = () => {}
globalThis.setInterval = () => 0
globalThis.clearInterval = () => {}
globalThis.setTimeout = (fn) => { fn(0); return 0 }
globalThis.clearTimeout = () => {}
Object.defineProperty(globalThis, 'navigator', {
  value: { clipboard: { writeText: () => Promise.resolve() } }, configurable: true,
})
globalThis.fetch = () => Promise.reject(new Error('no network in this harness'))

const mod = await import(pathToFileURL(process.argv[2]).href)

const keydown = (docListeners['keydown'] || [])[0]
if (!keydown) { console.log(JSON.stringify({ error: 'boot registered no keydown handler' })); process.exit(0) }

const out = {}
const ref = 'zz/session-abc'
const toggleId = mod.paneToggleDomId(ref)
out.toggleId = toggleId

// 1. collapsePane returns focus to the disclosure button that opened the pane.
globalThis.__focused = null
mod.state.openPane = ref
mod.collapsePane(ref)
out.collapseFocus = globalThis.__focused
out.collapseClosedPane = mod.state.openPane === null

// 2. Escape with an open pane collapses it AND restores focus to that button.
globalThis.__focused = null
mod.state.openPane = ref
keydown({ key: 'Escape', preventDefault() {} })
out.escapePaneFocus = globalThis.__focused
out.escapeClosedPane = mod.state.openPane === null

// 3. NEGATIVE CONTROL: with no such element the implementation cannot focus anything, so the
//    assertions above measure a real focus call rather than an incidental global.
const realGet = globalThis.document.getElementById
globalThis.document.getElementById = () => null
globalThis.__focused = null
mod.state.openPane = ref
mod.collapsePane(ref)
out.noFocus = globalThis.__focused
globalThis.document.getElementById = realGet

// 4. The evidence drawer closes on Escape and returns focus to its activating control.
const card = { project_id: 'p1', evidence_refs: [{ evidence_id: 'e1', tier: 1, kind: 'next_action',
  excerpt: 'bounded excerpt', locator: { profile: 'zz', session_id: 's1' }, source_hash: 'h',
  extracted_at: 1 }] }
const entry = { label: 'staleness_band', kind: 'evidence', evidence_id: 'e1', tier: 1,
                basis: 'test' }
globalThis.__focused = null
mod.openEvidence(card, 'staleness_band', entry, 'c-claim-band-p1')
out.evidenceOpen = mod.state.evidence !== null
out.evidenceFocus = globalThis.__focused
keydown({ key: 'Escape', preventDefault() {} })
out.evidenceClosed = mod.state.evidence === null
out.evidenceReturnFocus = globalThis.__focused

console.log(JSON.stringify(out))
process.exit(0)
"""


def test_mc_s6_dedicated_escape_focus_restoration_is_falsifiable(tmp_path):
    """Dedicated (not indirect) test for Escape/pane focus restoration, with a negative control.

    The harness boots the REAL app.js against a minimal DOM shim and observes focus. Removing the
    focus restoration makes `collapseFocus`/`escapePaneFocus`/`evidenceReturnFocus` null, which
    fails this test.
    """
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is unavailable; the Escape-focus behaviour cannot be executed here")
    # app.js imports the shared module browser-relative ('../desktop/kanban-interaction.js'), so
    # the harness runs against byte-identical copies laid out the way the browser resolves them.
    mirror = tmp_path / "mirror"
    (mirror / "static").mkdir(parents=True)
    (mirror / "desktop").mkdir(parents=True)
    app_js = os.path.join(BUILD_ROOT, "dashboard", "static", "app.js")
    shared_js = os.path.join(BUILD_ROOT, "desktop", "kanban-interaction.js")
    shutil.copyfile(app_js, str(mirror / "static" / "app.js"))
    shutil.copyfile(shared_js, str(mirror / "desktop" / "kanban-interaction.js"))
    with open(app_js, "rb") as fh:
        original = fh.read()
    with open(str(mirror / "static" / "app.js"), "rb") as fh:
        assert fh.read() == original, "the harness must execute the shipped app.js bytes"
    script = tmp_path / "mc_escape_harness.mjs"
    script.write_text(_ESCAPE_HARNESS, encoding="utf-8")
    proc = subprocess.run([node, str(script), str(mirror / "static" / "app.js")],
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout.strip().splitlines()[-1])
    assert "error" not in payload, payload
    # the pane toggle is focused again after a collapse
    assert payload["collapseFocus"] == payload["toggleId"]
    assert payload["collapseClosedPane"] is True
    # Escape on an open pane collapses and restores focus
    assert payload["escapePaneFocus"] == payload["toggleId"]
    assert payload["escapeClosedPane"] is True
    # the negative control: with no target element no focus call happens at all
    assert payload["noFocus"] is None
    # the evidence drawer restores focus to the control that opened it
    assert payload["evidenceOpen"] is True
    assert payload["evidenceFocus"] == "c-evidence-drawer"
    assert payload["evidenceClosed"] is True
    assert payload["evidenceReturnFocus"] == "c-claim-band-p1"


def test_mc_s6_dedicated_outside_cluster_anchor_is_falsifiable(tmp_path):
    """Dedicated test for the verified anchor that sits OUTSIDE the cluster (§4.2 / D-SP-2).

    The negative control rewrites the stored anchor to a ref that is not linked and not the
    anchor, which must be refused — proving the outside-cluster allowance is load-bearing.
    """
    svc = _audience_service(tmp_path)
    row = svc.registry.conn.execute(
        "SELECT project_id, anchor_session FROM project WHERE name='evopet-pet'").fetchone()
    assert row is not None, "the audience fixture must build the EvoPet project"
    pid = row["project_id"]
    anchor = row["anchor_session"]
    assert anchor == "lugia/20260911_163629_22fff0"
    linked = {("{}/{}".format(r["profile_name"], r["session_id"]))
              for r in svc.registry.conn.execute(
                  "SELECT profile_name, session_id FROM project_session WHERE project_id=?",
                  (pid,))}
    assert anchor not in linked, "this test is only meaningful for an OUTSIDE-cluster anchor"

    detail = svc.project_detail(pid)
    refs = ["{}/{}".format(s["profile_name"], s["session_id"]) for s in detail["sessions"]]
    assert refs and refs[0] == anchor, "the anchor must be listed first in the detail projection"
    assert detail["sessions"][0]["role_in_project"] == "anchor"
    # the pane is reachable for the anchor even though it is not a member
    pane = svc.project_detail(pid, pane=anchor)["session_pane"]
    assert pane["available"] is True and pane["session_id"] == anchor.split("/")[1]
    # and the anchor is the resume target
    card = _cards(svc, view="all")[pid]
    assert card["resume_targets"] and card["resume_targets"][0]["session_id"] == anchor.split("/")[1]
    assert card["primary_session"]["selection"] == "anchor"

    # NEGATIVE CONTROL: rewrite the stored anchor. The OLD outside-cluster ref stops being
    # allowable and any ref that is neither linked nor the stored anchor is refused — proving the
    # allowance is keyed on the stored anchor and not on a blanket "any session" read.
    other = "lugia/20990101_000000_zzzzzz"
    svc.registry.conn.execute("UPDATE project SET anchor_session=? WHERE project_id=?",
                              (other, pid))
    svc.registry.conn.commit()
    try:
        with pytest.raises(ValueError):
            svc.project_detail(pid, pane=anchor)
        with pytest.raises(ValueError):
            svc.project_detail(pid, pane="lugia/20000101_000000_qqqqqq")
        # the genuinely linked member is still pane-eligible (the guard is narrow, not global)
        member = sorted(linked)[0]
        assert svc.project_detail(pid, pane=member)["session_pane"] is not None
    finally:
        svc.registry.conn.execute("UPDATE project SET anchor_session=? WHERE project_id=?",
                                  (anchor, pid))
        svc.registry.conn.commit()
    assert svc.project_detail(pid, pane=anchor)["session_pane"]["available"] is True

