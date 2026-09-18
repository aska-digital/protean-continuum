"""F1 — Stable project identity regression test.

When a cluster gains a bridging session that changes its strong-key signature,
the project_id must remain stable and all declared decisions must be preserved.

Architecture §9 guarantee: "a full rescan after any sequence of these yields the
same declared end-state."
"""
from __future__ import annotations

import os
import sqlite3
import time

import pytest

from continuum.config import load_config
from continuum.service import Service
from fixtures.make_fixture import TJGC1, WS, build_fixture_tree

DAY = 86400.0


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


def _tjgc1_pid(svc):
    """Find the project_id for the tjgc1 cluster (the AV-3 git cluster)."""
    from continuum.model import project_id_for
    # The tjgc1 cluster has members from alpha (3 sessions + 1 child) and beta (1 session)
    # Find any of the known alpha sessions in the project_session table
    for sid in ("20260101_000001_aaa111", "20260102_000002_aaa222", "20260103_000003_aaa333"):
        row = svc.registry.conn.execute(
            "SELECT project_id FROM project_session WHERE profile_name='alpha' AND session_id=?",
            (sid,)).fetchone()
        if row:
            return row["project_id"]
    return None


def _add_bridging_session(home, now=None):
    """Insert a session that bridges tjgc1 and tywebsite workspaces.

    This session has cwd=WS (tywebsite) and git_repo_root=TJGC1, which means after
    the next scan the cluster will gain CS-2:WS as an additional strong key, changing
    its signature.
    """
    now = now or time.time()
    db = os.path.join(home, "profiles", "alpha", "state.db")
    conn = sqlite3.connect(db)
    conn.execute(
        """INSERT INTO sessions (id, source, title, title_source, cwd, git_repo_root,
           started_at, last_activity_at, message_count, tool_call_count, parent_session_id,
           chat_type, display_name, archived, pinned, hidden)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        ("20260119_000019_bridge1", "cli", "bridging session tjgc1 and tywebsite",
         "derived", WS, TJGC1, now - 1 * DAY, now - 0.5 * DAY, 8, 4,
         None, None, None, 0, 0, 0))
    conn.execute(
        """INSERT INTO messages (session_id, role, content, timestamp)
           VALUES (?,?,?,?)""",
        ("20260119_000019_bridge1", "user",
         "work on the typejoy landing page and the tywebsite hero", now - 1 * DAY))
    conn.commit()
    conn.close()


def test_accept_park_next_action_survives_bridging_session(svc, home):
    """F1 regression: declared decisions survive a cluster signature change.

    Steps:
    1. Scan → tjgc1 cluster identified
    2. accept + park + set_next_action on the cluster
    3. Insert a bridging session that changes the cluster's strong-key set
    4. Rescan → cluster signature changes
    5. Assert: one project, all declared fields preserved, not in Recovery Inbox
    """
    # Step 1: find the tjgc1 project
    pid = _tjgc1_pid(svc)
    assert pid is not None, "tjgc1 cluster not found"

    # Record initial state
    initial_count = len(svc.registry.projects())

    # Step 2: accept, park, set next action
    svc.review("accept", pid, {"name": "TJGC1 Project"})
    svc.review("park", pid, {})
    svc.review("set_next_action", pid, {"text": "ship the landing page"})

    # Verify declared state before bridging
    declared = svc.registry.effective_declared(pid)
    assert declared.get("accepted") == "1"
    assert declared.get("parked") == "1"
    assert declared.get("next_action_verified") == "ship the landing page"

    na = svc.registry.next_actions_for(pid)
    assert any(n["text"] == "ship the landing page" for n in na)

    # Step 3: add bridging session
    _add_bridging_session(home)

    # Step 4: rescan — the cluster now has a different strong-key signature
    svc.scan(full=True)

    # Step 5: assert stability
    # The project should still be accessible via the same project_id OR
    # the declared state should have been migrated to a new project
    # Check that exactly one project contains the tjgc1 sessions
    all_projects = svc.registry.projects()
    tjgc1_projects = []
    for p in all_projects:
        links = svc.registry.links_for(dict(p)["project_id"])
        has_tjgc1 = any(
            l["session_id"] in ("20260101_000001_aaa111", "20260102_000002_aaa222",
                                 "20260103_000003_aaa333", "20260119_000019_bridge1")
            for l in links
        )
        if has_tjgc1:
            tjgc1_projects.append(dict(p)["project_id"])

    assert len(tjgc1_projects) == 1, (
        "Expected exactly 1 project with tjgc1 sessions, got {}: {}. "
        "Declared state may have been orphaned.".format(len(tjgc1_projects), tjgc1_projects))

    surviving_pid = tjgc1_projects[0]

    # The declared state must be on the surviving project
    declared_after = svc.registry.effective_declared(surviving_pid)
    assert declared_after.get("accepted") == "1", (
        "accepted flag lost after signature change: {}".format(declared_after))
    assert declared_after.get("parked") == "1", (
        "parked flag lost after signature change: {}".format(declared_after))
    assert declared_after.get("next_action_verified") == "ship the landing page", (
        "next_action lost after signature change: {}".format(declared_after))

    # The project must NOT be in the Recovery Inbox (it was accepted)
    inbox_ids = {i["project_id"] for i in svc.inbox()["items"]}
    assert surviving_pid not in inbox_ids, (
        "Previously accepted project appeared in Recovery Inbox after signature change")

    # The bridging session must be linked to the same project
    bridge_link = svc.registry.conn.execute(
        "SELECT project_id FROM project_session WHERE profile_name='alpha' "
        "AND session_id='20260119_000019_bridge1'").fetchone()
    assert bridge_link is not None, "bridging session not linked"
    assert bridge_link["project_id"] == surviving_pid, (
        "bridging session linked to wrong project: {} vs {}".format(
            bridge_link["project_id"], surviving_pid))

    # Next action must still be present
    na_after = svc.registry.next_actions_for(surviving_pid)
    assert any(n["text"] == "ship the landing page" for n in na_after), (
        "next_action row lost after signature change")

    # The project should show as parked (LS-6)
    detail = svc.project_detail(surviving_pid)
    assert detail["project"]["lifecycle"] == "LS-6", (
        "parked lifecycle not preserved: {}".format(detail["project"]["lifecycle"]))


def test_no_duplicate_projects_after_rescan(svc, home):
    """After a rescan that changes cluster signatures, no orphaned project rows remain."""
    pid = _tjgc1_pid(svc)
    assert pid is not None
    svc.review("accept", pid, {})

    _add_bridging_session(home)
    svc.scan(full=True)

    # Count projects that contain tjgc1 sessions
    tjgc1_sids = {"20260101_000001_aaa111", "20260102_000002_aaa222",
                  "20260103_000003_aaa333", "20260114_000014_fff111",
                  "20260119_000019_bridge1", "20260201_000001_bbb901"}
    project_ids = set()
    for p in svc.registry.projects():
        p_dict = dict(p)
        links = svc.registry.links_for(p_dict["project_id"])
        if any(l["session_id"] in tjgc1_sids for l in links):
            project_ids.add(p_dict["project_id"])

    assert len(project_ids) == 1, (
        "Expected 1 project for tjgc1 sessions, got {}: {}".format(
            len(project_ids), project_ids))


def test_signature_table_is_populated(svc, home):
    """The project_signature table must contain entries after a scan."""
    svc.scan(full=True)
    rows = list(svc.registry.conn.execute("SELECT * FROM project_signature"))
    assert len(rows) >= 1, "project_signature table is empty after scan"
    for r in rows:
        assert r["signature"], "empty signature"
        assert r["project_id"], "empty project_id"


def _ws_pid(svc):
    """Find the project_id for the WS cluster (tywebsite)."""
    for sid in ("20260104_000004_bbb111", "20260105_000005_bbb222"):
        row = svc.registry.conn.execute(
            "SELECT project_id FROM project_session WHERE profile_name='alpha' AND session_id=?",
            (sid,)).fetchone()
        if row:
            return row["project_id"]
    return None


def test_absorbed_project_declared_state_migrates(svc, home):
    """R1 regression: when the ABSORBED project holds declared decisions, they
    must be migrated to the surviving project after a signature change.

    Variant A: declare on WS (absorbed), bridge, rescan.
    Expected: one project, declared fields on survivor, no ghost, no duplicate links.
    """
    # Find the WS project
    ws_pid = _ws_pid(svc)
    assert ws_pid is not None, "WS cluster not found"

    # Declare on WS (the project that will be absorbed)
    svc.review("accept", ws_pid, {"name": "WS Renamed"})
    svc.review("park", ws_pid, {})
    svc.review("set_next_action", ws_pid, {"text": "ws hero work"})

    # Verify declared state before bridging
    declared = svc.registry.effective_declared(ws_pid)
    assert declared.get("accepted") == "1"
    assert declared.get("parked") == "1"
    assert declared.get("next_action_verified") == "ws hero work"

    # Add bridging session
    _add_bridging_session(home)

    # Rescan — WS and tjgc1 merge into one cluster
    svc.scan(full=True)

    # Exactly one project should contain any of the original sessions
    all_projects = svc.registry.projects()
    all_sids = {"20260101_000001_aaa111", "20260102_000002_aaa222",
                "20260103_000003_aaa333", "20260104_000004_bbb111",
                "20260105_000005_bbb222", "20260119_000019_bridge1"}
    containing_pids = []
    for p in all_projects:
        links = svc.registry.links_for(dict(p)["project_id"])
        if any(l["session_id"] in all_sids for l in links):
            containing_pids.append(dict(p)["project_id"])

    assert len(containing_pids) == 1, (
        "Expected 1 project containing original sessions, got {}: {}. "
        "Ghost project may still exist.".format(len(containing_pids), containing_pids))

    surviving_pid = containing_pids[0]

    # Declared state must be on the surviving project
    declared_after = svc.registry.effective_declared(surviving_pid)
    assert declared_after.get("accepted") == "1", (
        "accepted flag lost after absorbed-project merge: {}".format(declared_after))
    assert declared_after.get("parked") == "1", (
        "parked flag lost after absorbed-project merge: {}".format(declared_after))
    assert declared_after.get("next_action_verified") == "ws hero work", (
        "next_action lost after absorbed-project merge: {}".format(declared_after))

    # No duplicate session membership
    link_counts = {}
    for p in all_projects:
        for l in svc.registry.links_for(dict(p)["project_id"]):
            key = (l["profile_name"], l["session_id"])
            link_counts[key] = link_counts.get(key, 0) + 1
    duplicates = {k: v for k, v in link_counts.items() if v > 1}
    assert not duplicates, (
        "Sessions linked to multiple projects: {}".format(duplicates))

    # Surviving project must NOT be in Recovery Inbox
    inbox_ids = {i["project_id"] for i in svc.inbox()["items"]}
    assert surviving_pid not in inbox_ids, (
        "Previously accepted project appeared in Recovery Inbox")

    # WS project must be pruned (not a ghost)
    assert ws_pid not in {dict(p)["project_id"] for p in svc.registry.projects()}, (
        "Absorbed WS project still exists as a ghost")

    # Next action must still be present
    na_after = svc.registry.next_actions_for(surviving_pid)
    assert any(n["text"] == "ws hero work" for n in na_after), (
        "next_action row lost after absorbed-project merge")

    # The project should show as parked (LS-6)
    detail = svc.project_detail(surviving_pid)
    assert detail["project"]["lifecycle"] == "LS-6", (
        "parked lifecycle not preserved: {}".format(detail["project"]["lifecycle"]))


def test_absorbed_declared_on_both_projects(svc, home):
    """R1 regression variant B: declare on BOTH projects, then bridge.

    Both sets of declared state must survive on the single merged project.
    """
    tjgc1_pid = _tjgc1_pid(svc)
    ws_pid = _ws_pid(svc)
    assert tjgc1_pid is not None and ws_pid is not None

    # Declare on both
    svc.review("accept", tjgc1_pid, {"name": "TJGC1 Renamed"})
    svc.review("set_next_action", tjgc1_pid, {"text": "tjgc1 next"})

    svc.review("accept", ws_pid, {"name": "WS Renamed"})
    svc.review("set_next_action", ws_pid, {"text": "ws next"})

    # Bridge and rescan
    _add_bridging_session(home)
    svc.scan(full=True)

    # Exactly one project
    all_projects = svc.registry.projects()
    all_sids = {"20260101_000001_aaa111", "20260102_000002_aaa222",
                "20260103_000003_aaa333", "20260104_000004_bbb111",
                "20260105_000005_bbb222", "20260119_000019_bridge1"}
    containing_pids = []
    for p in all_projects:
        links = svc.registry.links_for(dict(p)["project_id"])
        if any(l["session_id"] in all_sids for l in links):
            containing_pids.append(dict(p)["project_id"])

    assert len(containing_pids) == 1, (
        "Expected 1 project, got {}".format(containing_pids))

    surviving_pid = containing_pids[0]

    # Accepted flag must be on ALL original links (both tjgc1 and WS sessions)
    # The bridge session was added AFTER accepts, so it was never accepted
    original_sids = {"20260101_000001_aaa111", "20260102_000002_aaa222",
                     "20260103_000003_aaa333", "20260104_000004_bbb111",
                     "20260105_000005_bbb222"}
    links = svc.registry.links_for(surviving_pid)
    for l in links:
        if l["session_id"] in original_sids:
            assert l["accepted"] == 1, (
                "Session {} not accepted after merge: accepted={}".format(
                    l["session_id"], l["accepted"]))

    # No ghost projects
    assert tjgc1_pid not in {dict(p)["project_id"] for p in all_projects} or \
           ws_pid not in {dict(p)["project_id"] for p in all_projects}, \
        "At least one old project should be pruned"

    # Declared fields from both should be present
    declared = svc.registry.effective_declared(surviving_pid)
    assert declared.get("accepted") == "1"
