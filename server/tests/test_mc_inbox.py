"""MC-S5 — Recovery Inbox guidance + suppression honesty (MC-A22..MC-A24).

Candidate/accepted/suppressed counts and the triage order are SERVER values; the browser renders
them and computes none of them. Suppression stays the audited, reversible dismiss/merge surface
and is never deletion (INV-MC-2 / MC-L11).
"""
from __future__ import annotations

import os
import time

import pytest

from continuum.config import load_config
from continuum.service import Service
from fixtures.make_fixture import build_fixture_tree

BUILD_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DAY = 86400.0
# Frida UX-16 / UX-17 verbatim copy.
INBOX_ZERO_COPY = ("No candidates in this snapshot. Run a scan to check for new continuity "
                   "evidence.")
# The suppression sentence is one rendered string built from two adjacent literals, so it is
# asserted in its two parts (their concatenation IS the sentence).
SUPPRESSED_COPY_PARTS = (
    "items are suppressed evidence (dismissed or merged), not deleted.",
    "Undo remains available from the review history.",
)


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


def _seed_candidates(svc, count=4):
    now = time.time()
    created = []
    for index in range(count):
        confidence = 0.9 - index * 0.1
        pid = _add_project(svc, "mca22-{}".format(index), name="candidate {}".format(index),
                           confidence=confidence,
                           sessions=[("zz", "mca22-{}-a".format(index), "USER_FACING",
                                      now - (index + 1) * DAY, 4, True),
                                     ("zz", "mca22-{}-b".format(index), "USER_FACING",
                                      now - (index + 2) * DAY, 4, True)])
        created.append(pid)
    return created


def _app_js() -> str:
    with open(os.path.join(BUILD_ROOT, "dashboard", "static", "app.js"), encoding="utf-8") as fh:
        return fh.read()


# --------------------------------------------------------------------------- MC-A22

def test_mc_a22_inbox_exposes_counts_and_a_deterministic_server_triage_order(svc):
    baseline = svc.recovery_inbox()["counts"]
    created = _seed_candidates(svc)
    svc.review("accept", created[0], {})
    payload = svc.recovery_inbox()
    counts = payload["counts"]
    assert counts["candidates"] == payload["total"] == len(payload["items"])
    assert counts["candidates"] == baseline["candidates"] + len(created) - 1
    assert counts["accepted"] == baseline["accepted"] + 1
    assert counts["suppressed_total"] == baseline["suppressed_total"]
    order = payload["triage_order"]
    assert payload["triage_order_total"] == len(order) == len(payload["items"])
    assert [entry["rank"] for entry in order] == list(range(1, len(order) + 1))
    assert [entry["project_id"] for entry in order] == \
        [item["project_id"] for item in payload["items"]]
    # confidence desc, then project_id ASC — total, deterministic, and no score
    keys = [(-(item["confidence"] or 0), item["project_id"]) for item in payload["items"]]
    assert keys == sorted(keys)
    assert payload["triage_order"] == svc.recovery_inbox()["triage_order"]
    # the accepted project is counted, never re-offered
    offered = {entry["project_id"] for entry in order}
    assert created[0] not in offered
    assert "score" not in str(payload).lower()
    # the browser renders those values and computes none of them
    src = _app_js()
    start = src.index("function renderInbox(payload) {")
    end = src.index("// ── mission-control header")
    region = src[start:end]
    assert "payload.counts" in region and "payload.triage_order" in region
    assert "countReturned" not in region, "the inbox must not recompute a server count"
    assert "fetch(" not in region, "the inbox renders the payload it was given"
    assert "Review candidates in the server-provided order." in region


# --------------------------------------------------------------------------- MC-A23

def test_mc_a23_the_inbox_zero_state_is_reachable_and_names_the_next_action(svc):
    _seed_candidates(svc, count=3)
    # accept EVERY candidate (including the two the base fixture contributes): inbox zero is a
    # reachable state, not a theoretical one.
    for pid in [item["project_id"] for item in svc.recovery_inbox()["items"]]:
        svc.review("accept", pid, {})
    payload = svc.recovery_inbox()
    assert payload["counts"]["candidates"] == 0
    assert payload["items"] == [] and payload["triage_order"] == []
    assert payload["counts"]["accepted"] >= 3
    src = _app_js()
    assert INBOX_ZERO_COPY in src, "the zero state must name the next review action"
    zero_line = [line for line in src.splitlines() if "No candidates in this snapshot" in line]
    assert zero_line
    for line in zero_line:
        assert "deleted" not in line and "complete" not in line.lower()
    assert "counts.suppressed_total" in src
    for part in SUPPRESSED_COPY_PARTS:
        assert part in src, part
    # the rendered sentence is exactly the two parts, with no deletion language
    assert "deleted." in SUPPRESSED_COPY_PARTS[0]
    assert "Undo remains available" in SUPPRESSED_COPY_PARTS[1]
    # the suppressed line is omitted when the server publishes no suppressed count
    assert "if (counts.suppressed_total)" in src


# --------------------------------------------------------------------------- MC-A24

def test_mc_a24_suppression_is_audited_reversible_and_never_deletion(svc):
    created = _seed_candidates(svc, count=3)
    before = svc.recovery_inbox()
    target = created[1]
    result = svc.review("dismiss", target, {})
    assert result["ok"] and result["audit_id"]
    after = svc.recovery_inbox()
    assert after["counts"]["suppressed_total"] == before["counts"]["suppressed_total"] + 1
    assert after["counts"]["candidates"] == before["counts"]["candidates"] - 1
    assert target not in {item["project_id"] for item in after["items"]}
    events = svc.registry.review_events(target, limit=20)
    assert [e["event_id"] for e in events] == [result["audit_id"]], "one audited event"
    for event in events:
        assert event["action"] == "dismiss" and event["before_json"] and event["after_json"]
    # the suppressed surface stays visible in the noise/review projection with its count
    noise = svc.noise()
    assert any(entry.get("noise_class") == "user-dismissed" for entry in noise["suppressions"])
    assert noise["counts"]["suppressed_projects"] >= 1
    # undo restores the candidate exactly
    svc.review("undo", "", {"audit_id": result["audit_id"]})
    restored = svc.recovery_inbox()
    assert restored["counts"]["suppressed_total"] == before["counts"]["suppressed_total"]
    assert restored["counts"]["candidates"] == before["counts"]["candidates"]
    assert target in {item["project_id"] for item in restored["items"]}
    # merge remains the only other suppression path, and neither deletes a project row
    surviving = svc.registry.conn.execute(
        "SELECT COUNT(*) AS n FROM project WHERE project_id=?", (target,)).fetchone()["n"]
    assert surviving == 1, "suppression must never delete a project row"
    src = open(os.path.join(BUILD_ROOT, "continuum", "service.py"), encoding="utf-8").read()
    for token in ("delete_project", "DELETE FROM project ", "archive_project", "rename_project",
                  "pin_project", "auto_archive", "auto_suppress"):
        assert token not in src, token
    assert "unread_count" not in src and "unread_messages" not in src, \
        "no invented unread field"


def test_mc_a24_inbox_counts_agree_with_the_board_and_the_noise_surface(svc):
    created = _seed_candidates(svc, count=3)
    payload = svc.recovery_inbox()
    board = svc.board(view="all", page_size=200)
    accepted = [c for c in board["items"] if c["review_status"] == "accepted"]
    candidates = [c for c in board["items"] if c["review_status"] == "candidate"]
    assert payload["counts"]["accepted"] == len(accepted)
    assert payload["counts"]["candidates"] == len([c for c in candidates
                                                   if c["project_id"] in
                                                   {i["project_id"] for i in payload["items"]}])
    assert svc.noise()["counts"]["suppressed_projects"] == payload["counts"]["suppressed_total"]
