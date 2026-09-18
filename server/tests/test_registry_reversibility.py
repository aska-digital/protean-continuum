"""INV-2/INV-3 — review mutations are registry-only, audited, and reversible.

Uses isolated fixtures. A full rescan after a sequence of decisions must return the same
declared end-state, and the source databases must be byte-unchanged throughout.
"""
from __future__ import annotations

import os

import pytest

from continuum.config import load_config
from continuum.service import Service
from fixtures.make_fixture import build_fixture_tree


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


def _stat(path):
    st = os.stat(path)
    return (st.st_mtime, st.st_size, os.path.exists(path + "-wal"), os.path.exists(path + "-shm"))


def _source_state(home):
    out = {}
    for prof in ("alpha", "beta"):
        db = os.path.join(home, "profiles", prof, "state.db")
        out[db] = _stat(db)
    return out


def _inbox_ids(svc):
    return {i["project_id"] for i in svc.inbox()["items"]}


def _board_ids(svc):
    return {c["project_id"] for c in svc.board()["items"]}


def test_accept_then_undo_round_trip(svc, home):
    before_sources = _source_state(home)
    target = sorted(_inbox_ids(svc))[0]
    assert target in _inbox_ids(svc)

    result = svc.review("accept", target, {"name": "Accepted Project", "lifecycle": "LS-1"})
    assert result["ok"]
    assert target in _board_ids(svc)
    assert target not in _inbox_ids(svc)

    svc.review("undo", "", {"audit_id": result["audit_id"]})
    assert target in _inbox_ids(svc)
    # kanban: Inbox is a lane on the board, so undone candidate remains on board in Inbox (architecture §11.3)
    board_card = next(c for c in svc.board()["items"] if c["project_id"] == target)
    assert board_card["column"] == "inbox"
    assert board_card["placement_source"] == "candidate"
    assert _source_state(home) == before_sources


def test_park_then_undo_round_trip(svc):
    target = sorted(_inbox_ids(svc))[0]
    svc.review("accept", target, {})
    svc.review("park", target, {})
    view = svc.project_detail(target)["project"]
    assert view["lifecycle"] == "LS-6"
    alerted = {i["project_id"] for g in svc.attention()["groups"].values() for i in g}
    assert target not in alerted

    events = svc.registry.review_events(target)
    park_event = [e for e in events if e["action"] == "park"][0]
    svc.review("undo", "", {"audit_id": park_event["event_id"]})
    assert svc.project_detail(target)["project"]["lifecycle"] != "LS-6"


def test_dismiss_is_recorded_with_a_noise_class_and_reversible(svc):
    target = sorted(_inbox_ids(svc))[0]
    res = svc.review("dismiss", target, {"noise_class": "not-a-project"})
    assert res["ok"]
    noise = svc.noise()
    human = [s for s in noise["suppressions"] if s["source"] == "human"]
    assert human and human[0]["noise_class"] == "user-dismissed"
    assert target not in _inbox_ids(svc)

    svc.review("undo", "", {"audit_id": res["audit_id"]})
    assert target in _inbox_ids(svc)


def test_declared_next_action_and_drive_expected_survive_a_full_rescan(svc):
    target = sorted(_inbox_ids(svc))[0]
    svc.review("accept", target, {})
    svc.review("set_next_action", target, {"text": "finish the release checklist"})
    svc.review("drive_expected", target, {"value": True})

    svc.scan(full=True)  # rescan must not lose declared decisions

    row = svc.registry.get_project(target)
    assert row["drive_expected"] == 1
    na = svc.registry.next_actions_for(target)
    assert any(n["text"] == "finish the release checklist" and n["source"] == "declared"
               for n in na)
    declared = svc.registry.effective_declared(target)
    assert declared.get("next_action_verified") == "finish the release checklist"


def test_declared_field_expiry_flags_declared_stale(svc):
    target = sorted(_inbox_ids(svc))[0]
    svc.review("accept", target, {})
    svc.registry.set_declared(target, "lifecycle_override", "LS-1", ttl_days=-1)  # already expired
    assert svc.registry.is_declared_stale(target) is True
    svc.scan(full=True)
    detail = svc.project_detail(target)
    assert detail["project"]["declared_stale"] is True


def test_review_event_log_is_append_only(svc):
    target = sorted(_inbox_ids(svc))[0]
    n0 = len(svc.registry.review_events(target, limit=1000))
    svc.review("accept", target, {})
    svc.review("park", target, {})
    n1 = len(svc.registry.review_events(target, limit=1000))
    assert n1 == n0 + 2
    # events are never updated or deleted
    for e in svc.registry.review_events(target, limit=1000):
        assert e["before_json"] is not None and e["after_json"] is not None


def test_merge_and_split_are_reversible(svc):
    ids = sorted(_inbox_ids(svc))
    assert len(ids) >= 1
    src = ids[0]
    dst = ids[1] if len(ids) > 1 else ids[0]
    if src == dst:
        pytest.skip("needs two candidates")
    before_src_links = len(svc.registry.links_for(src))
    res = svc.review("merge", src, {"target_project_id": dst})
    assert svc.registry.links_for(src) == []
    svc.review("undo", "", {"audit_id": res["audit_id"]})
    assert len(svc.registry.links_for(src)) == before_src_links


def test_split_is_also_reversible(svc):
    """F6: split must be tested, not just merge. Exercise split + undo round-trip."""
    ids = sorted(_inbox_ids(svc))
    assert len(ids) >= 1
    target = ids[0]
    links = svc.registry.links_for(target)
    if len(links) < 2:
        pytest.skip("need at least 2 links to split")
    before_links = len(links)
    # Split the first session into a new project
    split_ref = "{}/{}".format(links[0]["profile_name"], links[0]["session_id"])
    res = svc.review("split", target, {
        "session_refs": [split_ref],
        "name": "split project",
    })
    assert res["ok"]
    # After split: target has one fewer link, new project has one
    after_target_links = svc.registry.links_for(target)
    assert len(after_target_links) == before_links - 1
    # Undo the split
    svc.review("undo", "", {"audit_id": res["audit_id"]})
    restored_links = svc.registry.links_for(target)
    assert len(restored_links) == before_links


def test_source_databases_are_untouched_by_reviews_and_rescans(svc, home):
    before = _source_state(home)
    for pid in sorted(_inbox_ids(svc))[:3]:
        svc.review("accept", pid, {})
        svc.review("set_lifecycle", pid, {"lifecycle": "LS-2"})
    svc.scan(full=True)
    assert _source_state(home) == before


# ---------------------------------------------------------------------------
# D-US-7.4 — the audited audience override, and its inverse.
# ---------------------------------------------------------------------------

def test_audience_override_is_audited_and_undone(svc):
    row = svc.registry.conn.execute(
        "SELECT project_id FROM project WHERE name='tjgc1'").fetchone()
    assert row is not None
    pid = row["project_id"]
    links = svc.registry.links_for(pid)
    primary = next(l for l in links if l["role_in_project"] == "primary")
    ref = "{}/{}".format(primary["profile_name"], primary["session_id"])
    assert svc.registry.session_audience(primary["profile_name"],
                                        primary["session_id"])[0] == "USER_FACING"

    res = svc.review("set_audience", pid, {"session_ref": ref, "audience": "DELEGATED"})
    assert res["ok"]
    # audited like every other declared field
    events = [e for e in svc.registry.review_events(pid, limit=100)
              if e["action"] == "set_audience"]
    assert events and events[0]["before_json"] and events[0]["after_json"]
    assert "audience_override:" + ref in svc.registry.effective_declared(pid)

    # the override is re-applied on the next reconcile (derived field semantics)
    svc.scan(full=True)
    assert svc.registry.session_audience(primary["profile_name"],
                                        primary["session_id"])[0] == "DELEGATED"
    after = {l["session_id"]: l["role_in_project"] for l in svc.registry.links_for(pid)}
    assert after[primary["session_id"]] == "supporting"
    assert not any(l["session_id"] == primary["session_id"] and l["role_in_project"] == "primary"
                   for l in svc.registry.links_for(pid))

    # undo replays the inverse; a rescan restores the prior audience and primary
    svc.review("undo", "", {"audit_id": res["audit_id"]})
    assert "audience_override:" + ref not in svc.registry.effective_declared(pid)
    svc.scan(full=True)
    assert svc.registry.session_audience(primary["profile_name"],
                                        primary["session_id"])[0] == "USER_FACING"
    restored = {l["session_id"]: l["role_in_project"] for l in svc.registry.links_for(pid)}
    assert restored[primary["session_id"]] == "primary"


# ---------------------------------------------------------------------------
# M4 — terminal placement and pause round-trips on the standalone board model.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("placement", ["done", "shipped", "scrapped"])
def test_m4_terminal_placement_survives_full_and_incremental_rescan(svc, home, placement):
    before_sources = _source_state(home)
    target = sorted(_inbox_ids(svc))[0]
    res = svc.review("set_placement", target, {"placement": placement})
    assert res["ok"]
    card = next(c for c in svc.board(view="all")["items"] if c["project_id"] == target)
    assert card["column"] == placement and card["placement_source"] == "human"

    svc.scan(full=True)
    svc.scan()
    card = next(c for c in svc.board(view="all")["items"] if c["project_id"] == target)
    assert card["column"] == placement
    declared = svc.registry.effective_declared(target)
    assert declared.get("placement") == placement
    assert declared.get("dismissed") != "1" and not declared.get("merged_into")
    assert svc.registry.get_project(target) is not None
    assert _source_state(home) == before_sources

    svc.review("undo", "", {"audit_id": res["audit_id"]})
    assert _cards_column(svc, target) != placement


def _cards_column(svc, project_id):
    return next(c["column"] for c in svc.board(view="all")["items"]
                if c["project_id"] == project_id)


def test_m4_paused_placement_is_quiet_and_reversible(svc):
    target = sorted(_inbox_ids(svc))[0]
    res = svc.review("set_placement", target, {"placement": "paused"})
    card = next(c for c in svc.board(view="all")["items"] if c["project_id"] == target)
    assert card["column"] == "paused"
    assert card["attention_state"] == "quiet"
    alerted = {i["project_id"] for g in svc.attention()["groups"].values() for i in g}
    assert target not in alerted
    svc.review("undo", "", {"audit_id": res["audit_id"]})
    restored = next(c for c in svc.board(view="all")["items"] if c["project_id"] == target)
    assert restored["column"] != "paused"
    assert restored["attention_state"] != "quiet"


def test_m4_a_failed_scan_keeps_the_last_committed_snapshot(svc):
    before = svc.board(view="today")
    ids_before = {c["project_id"] for c in before["items"]}
    original = svc.registry.upsert_evidence

    def _boom(*a, **k):
        raise RuntimeError("injected derived-write failure")

    svc.registry.upsert_evidence = _boom
    try:
        with pytest.raises(RuntimeError):
            svc.scan(full=True)
    finally:
        svc.registry.upsert_evidence = original
    after = svc.board(view="today")
    assert after["data_as_of"] == before["data_as_of"]
    assert ids_before <= {c["project_id"] for c in after["items"]}
    assert after["scan_state"]["phase"] == "error"

