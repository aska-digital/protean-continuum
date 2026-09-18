"""MC-S2 — global attention queue (MC-A6..MC-A10).

The rail's authority is the SERVER's global, unpaged answer. These tests drive the real service
and assert the closed vocabulary, page independence, quiet/terminal exclusion and the
no-fabricated-urgency rule. The rail's own source is checked only where the contract is
structural (labelled as such).
"""
from __future__ import annotations

import json
import os
import re
import time

import pytest

from continuum.config import load_config
from continuum.service import ATTENTION_GROUPS, ATTENTION_RANK, Service
from fixtures.make_fixture import build_fixture_tree

BUILD_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DAY = 86400.0

# §7.2: the CLOSED attention vocabulary. `unknown_quiet` is not a member.
LOCKED_ATTENTION_STATES = ("blocked", "waiting_on_user", "stale_with_commitment", "needs_review",
                          "no_user_work", "recent", "quiet", "unknown")
# "Urgent" is a human flag only; these words must never appear as fabricated urgency copy.
FABRICATED_URGENCY = ("overdue", "you're behind", "you are behind", "neglected", "should have",
                      "best choice", "countdown", "streak")


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
                 confidence=0.8, sessions=(), stall_age_days=1.0, drive_expected=0):
    """Insert a synthetic project with explicit links/facts (registry-only writes)."""
    now = time.time()
    svc.registry.conn.execute(
        "INSERT OR REPLACE INTO project (project_id, name, kind, phase, lifecycle, confidence,"
        " confidence_band, evidence_tier, owner_profile, drive_expected, stall_age_days,"
        " last_substantive_activity, session_count, derived_updated_at, created_at, updated_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (pid, name, "derived", "active", lifecycle, confidence, band, tier, "",
         drive_expected, stall_age_days, now, len(sessions), now, now, now))
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
                (prof, sid, "title " + sid, "/work/" + sid, msgs, 1, last, last, audience, "U"))
    svc.registry.conn.commit()
    return pid


def _attention_ids(svc):
    payload = svc.attention_queue()
    return {item["project_id"] for group in payload["groups"].values() for item in group}


def _app_js() -> str:
    with open(os.path.join(BUILD_ROOT, "dashboard", "static", "app.js"), encoding="utf-8") as fh:
        return fh.read()


def _app_literals() -> str:
    """Every QUOTED string in the renderer, joined — the copy a user could actually read.

    Comments are stripped first and a quote can never span a line, so a stray apostrophe in a
    comment cannot swallow real code into one fake "literal".
    """
    src = _app_js()
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    src = "\n".join(re.sub(r"//.*$", "", line) for line in src.splitlines())
    out = []
    for match in re.finditer(r"'([^'\\\n]*(?:\\.[^'\\\n]*)*)'|\"([^\"\\\n]*(?:\\.[^\"\\\n]*)*)\"",
                             src):
        single, double = match.group(1), match.group(2)
        out.append(single if single is not None else (double if double is not None else ''))
    return " ".join(out).lower()


# --------------------------------------------------------------------------- MC-A6

def test_mc_a6_group_totals_are_the_servers_and_are_page_independent(svc):
    now = time.time()
    awaiting = _add_project(svc, "mca6-await", lifecycle="LS-4",
                            sessions=[("zz", "mca6a", "USER_FACING", now - 2 * DAY, 3, True)])
    blocked = _add_project(svc, "mca6-block", lifecycle="LS-3",
                           sessions=[("zz", "mca6b", "USER_FACING", now - 1 * DAY, 3, True)])
    payload = svc.attention_queue()
    counts = payload["counts"]
    assert counts["awaiting_user"] == len(payload["groups"]["awaiting_user"])
    assert counts["blocked"] == len(payload["groups"]["blocked"])
    assert counts["stale_active"] == len(payload["groups"]["stale_active"])
    assert counts["total"] == payload["attention_count"]
    assert counts["total"] == sum(counts[g] for g in ("awaiting_user", "blocked",
                                                      "stale_active"))
    assert awaiting in _attention_ids(svc) and blocked in _attention_ids(svc)
    # the ordered reference list is bounded, ordered, and honest about what it omits
    ordered = payload["ordered"]
    assert ordered and ordered[0]["rank"] == 1
    assert [entry["rank"] for entry in ordered] == list(range(1, len(ordered) + 1))
    assert payload["ordered_omitted"] == max(0, counts["total"] - len(ordered))
    for entry in ordered:
        assert entry["group"] in ("awaiting_user", "blocked", "stale_active")
        assert entry["project_id"] in _attention_ids(svc)
    # page independence: the board page size never moves an attention total
    for size in (1, 200):
        board = svc.board(view="all", page_size=size)
        assert len(board["items"]) <= size
        again = svc.attention_queue()
        assert again["counts"] == counts
        assert again["attention_count"] == counts["total"]


def test_mc_a6_the_rail_reads_attention_and_never_counts_the_returned_page():
    """Structural: the rail's numbers come from /attention, not from `data.items`."""
    src = _app_js()
    assert "'/attention'" in src and "fetchAttention" in src
    assert "attentionRail(state.attention)" in src
    start = src.index("function attentionRail(payload) {")
    end = src.index("function healthLabel(")
    rail = src[start:end]
    assert "data.items" not in rail, "the rail must never count the returned board page"
    assert "payload.counts" in rail, "the rail must render the server's per-group counts"
    assert "Showing ' + String(shown.length) + ' of ' + String(n)" in rail


# --------------------------------------------------------------------------- MC-A7

def test_mc_a7_the_attention_vocabulary_is_closed_and_unknown_quiet_is_gone(svc):
    now = time.time()
    _add_project(svc, "mca7-old-user-work", lifecycle="LS-2", band="high", tier=1,
                 sessions=[("zz", "mca7a", "USER_FACING", now - 11 * DAY, 4, True)])
    _add_project(svc, "mca7-blocked", lifecycle="LS-3",
                 sessions=[("zz", "mca7b", "USER_FACING", now - 4 * DAY, 3, True)])
    _add_project(svc, "mca7-no-user", lifecycle="LS-2",
                 sessions=[("zz", "mca7c", "DELEGATED", now - 2 * DAY, 3, True)])
    cards = {c["project_id"]: c for c in
             svc.board(view="all", page_size=200)["items"]}
    states = {c["attention_state"] for c in cards.values()}
    assert states <= set(LOCKED_ATTENTION_STATES), states
    assert set(LOCKED_ATTENTION_STATES) == set(ATTENTION_RANK)
    for card in cards.values():
        assert card["attention_reason"], card["project_id"]
    # a card with real user work that is merely not recent is `quiet` — never a ninth value
    assert cards["mca7-old-user-work"]["attention_state"] == "quiet"
    assert cards["mca7-old-user-work"]["recency_state"] == "stale"
    # a project with no user-facing session carries no_user_work, a distinct honest state
    assert cards["mca7-no-user"]["attention_state"] == "no_user_work"
    blob = json.dumps({
        "board": svc.board(view="all", page_size=200),
        "attention": svc.attention_queue(), "inbox": svc.recovery_inbox(),
        "staleness": svc.staleness_view(), "legacy_attention": svc.attention(),
    })
    assert "unknown_quiet" not in blob, "a non-conforming attention value reached a payload"
    for rel in ("continuum/service.py", "dashboard/static/app.js", "dashboard/plugin_api.py"):
        with open(os.path.join(BUILD_ROOT, rel), encoding="utf-8") as fh:
            assert "unknown_quiet" not in fh.read(), rel


# --------------------------------------------------------------------------- MC-A8

def test_mc_a8_paused_and_terminal_cards_are_absent_from_attention_but_stay_on_the_board(svc):
    now = time.time()
    for placement in ("paused", "done", "shipped", "scrapped"):
        pid = _add_project(svc, "mca8-" + placement, lifecycle="LS-3",
                           sessions=[("zz", "mca8" + placement, "USER_FACING",
                                      now - 5 * DAY, 3, True)])
        svc.review("accept", pid, {"placement": placement})
    board_ids = {c["project_id"] for c in svc.board(view="all", page_size=200)["items"]}
    alerted = _attention_ids(svc)
    for placement in ("paused", "done", "shipped", "scrapped"):
        pid = "mca8-" + placement
        assert pid in board_ids, "an explicit placement must stay visible on the board"
        assert pid not in alerted, "{} must not raise attention".format(placement)


# --------------------------------------------------------------------------- MC-A9

def test_mc_a9_low_and_unknown_confidence_are_candidates_not_attention(svc):
    now = time.time()
    for band in ("low", "unknown"):
        pid = _add_project(svc, "mca9-" + band, lifecycle="LS-3", band=band, tier=0,
                           sessions=[("zz", "mca9" + band + "1", "USER_FACING",
                                      now - 3 * DAY, 3, True),
                                     ("zz", "mca9" + band + "2", "USER_FACING",
                                      now - 2 * DAY, 3, True)])
        candidates = {item["project_id"] for item in svc.recovery_inbox()["items"]}
        assert pid in candidates, band
        assert pid not in _attention_ids(svc), band


# --------------------------------------------------------------------------- MC-A10

def test_mc_a10_no_fabricated_urgency_and_urgent_is_a_human_flag_only(svc):
    now = time.time()
    pid = _add_project(svc, "mca10", lifecycle="LS-3",
                       sessions=[("zz", "mca10a", "USER_FACING", now - 2 * DAY, 3, True)])
    cards = {c["project_id"]: c for c in svc.board(view="all", page_size=200)["items"]}
    assert cards[pid]["urgent"] is False
    assert cards[pid]["urgent_since"] is None
    payload = svc.attention_queue()
    blob = json.dumps(payload)
    for word in FABRICATED_URGENCY:
        assert word not in blob.lower(), word
    assert "urgent" not in payload["counts"], "urgent must not become an attention total"
    assert "score" not in blob and "priority_score" not in blob
    # the ONLY way a card becomes urgent is the explicit audited human flag
    svc.review("set_urgent", pid, {"value": True})
    cards = {c["project_id"]: c for c in svc.board(view="all", page_size=200)["items"]}
    assert cards[pid]["urgent"] is True
    assert cards[pid]["urgent_since"] is not None
    others = [c for c in cards.values() if c["project_id"] != pid]
    assert all(c["urgent"] is False for c in others)
    src = _app_js().lower()
    assert "score" not in _app_literals()
    for word in FABRICATED_URGENCY:
        assert word not in _app_literals(), word
    # the rail's own group labels are the locked three, and "urgent" is not one of them
    assert "awaiting you" in src and "blocked" in src and "stale with commitment" in src
    original = _app_js()
    rail = original[original.index("function attentionRail(payload) {"):original.index(
        "function healthLabel(")]
    assert "urgent" not in rail, "urgent is a card flag, never an attention group"
    assert len(ATTENTION_GROUPS) == 3
