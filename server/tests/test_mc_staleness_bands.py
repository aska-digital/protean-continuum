"""MC-S4 — user-anchored staleness bands + band filter (MC-A16..MC-A21).

Bands are anchored on `last_user_worked_on` with config-driven edges. `stall_age_days` is a
DIFFERENT question and is never the anchor (MC-L3 / INV-MC-7). Everything here drives the real
service over an isolated fixture home with a temp registry.
"""
from __future__ import annotations

import os
import time

import pytest

from continuum.config import load_config
from continuum.service import (STALENESS_BAND_ANCHOR, STALENESS_BAND_FILTERS, Service)
from fixtures.make_fixture import build_fixture_tree

BUILD_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DAY = 86400.0
EXPLICIT_NULL_BAND = "unknown_anchor"
QUIET = "quiet"


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
                (prof, sid, "title " + sid, "/work/" + sid, msgs, 1, last, last,
                 audience, "U"))
    svc.registry.conn.commit()
    return pid


def _cards(svc, **kw):
    return {c["project_id"]: c for c in svc.board(view="all", page_size=200, **kw)["items"]}


def _attention_ids(svc):
    payload = svc.attention_queue()
    return {item["project_id"] for group in payload["groups"].values() for item in group}


def _write_config(tmp_path, edges):
    path = tmp_path / "mc-bands.yaml"
    path.write_text("staleness_band_edges: {}\n".format(edges), encoding="utf-8")
    return str(path)


# --------------------------------------------------------------------------- MC-A16

def test_mc_a16_bands_come_from_the_user_anchor_and_a_null_anchor_is_explicit(svc):
    now = time.time()
    recent = _add_project(
        svc, "mca16-recent", stall_age_days=40.0,
        sessions=[("zz", "mca16a", "USER_FACING", now - 1 * DAY, 4, True)])
    old = _add_project(
        svc, "mca16-old",
        sessions=[("zz", "mca16b", "USER_FACING", now - 20 * DAY, 4, True)])
    null_anchor = _add_project(
        svc, "mca16-null", stall_age_days=900.0,
        sessions=[("zz", "mca16c", "DELEGATED", now - 900 * DAY, 4, True)])
    cards = _cards(svc)
    # the anchor is the USER anchor, not the stall age
    assert cards["mca16-recent"]["last_user_worked_on"] is not None
    assert cards["mca16-recent"]["staleness_band"] == "0-3d"
    assert cards["mca16-old"]["staleness_band"] == "7-30d"
    # a NULL user anchor gets its OWN band and is NEVER presented as the oldest band
    assert cards["mca16-null"]["last_user_worked_on"] is None
    assert cards["mca16-null"]["staleness_band"] == EXPLICIT_NULL_BAND
    assert cards["mca16-null"]["staleness_band"] != "30d+"
    view = svc.staleness_view()
    assert view["band_anchor"] == STALENESS_BAND_ANCHOR == "last_user_worked_on"
    unknown_bucket = [b for b in view["buckets"] if b["band"] == EXPLICIT_NULL_BAND][0]
    assert null_anchor in {c["project_id"] for c in unknown_bucket["items"]}
    oldest = [b for b in view["buckets"] if b["band"] == "30d+"][0]
    assert null_anchor not in {c["project_id"] for c in oldest["items"]}


# --------------------------------------------------------------------------- MC-A17

def test_mc_a17_band_edges_come_from_config_and_an_invalid_value_fails_loudly(svc, tmp_path):
    now = time.time()
    pid = _add_project(svc, "mca17", sessions=[("zz", "mca17a", "USER_FACING",
                                                now - 3.6 * DAY, 4, True)])
    assert _cards(svc)[pid]["staleness_band"] == "3-7d"          # default edges [3, 7, 30]
    cfg = load_config(hermes_home=svc.cfg.hermes_home,
                      path=_write_config(tmp_path, "[5, 9, 40]"))
    cfg.bundle["registry_path"] = svc.cfg.registry_path_resolved()
    narrowed = Service(cfg)
    assert narrowed.band_edges() == [5.0, 9.0, 40.0]
    assert _cards(narrowed)[pid]["staleness_band"] == "0-3d"     # 3.6d is inside a 5d edge
    for bad in ("[7, 3, 30]", "[3, 7]", "[3, 7, 30, 60]", "[three, 7, 30]", "[-1, 7, 30]",
                "[0, 7, 30]"):
        with pytest.raises(ValueError):
            load_config(hermes_home=svc.cfg.hermes_home, path=_write_config(tmp_path, bad))


# --------------------------------------------------------------------------- MC-A18

def test_mc_a18_stall_age_is_never_the_anchor_and_is_never_rewritten(svc):
    now = time.time()
    pid = _add_project(svc, "mca18", stall_age_days=41.0,
                       sessions=[("zz", "mca18a", "USER_FACING", now - 1 * DAY, 4, True)])
    card = _cards(svc)[pid]
    # the two signals can disagree in the same payload, and neither is rewritten
    assert card["stall_age_days"] == 41.0
    assert card["staleness_band"] == "0-3d"
    assert card["staleness_band_anchor"] == "last_user_worked_on"
    assert card["recency_state"] == "recent"
    view = svc.staleness_view()
    assert view["band_anchor"] == "last_user_worked_on"
    bucket = [b for b in view["buckets"] if b["band"] == "0-3d"][0]
    ids = {c["project_id"] for c in bucket["items"]}
    assert pid in ids
    stored = svc.registry.get_project(pid)
    assert stored["stall_age_days"] == 41.0


# --------------------------------------------------------------------------- MC-A19

def test_mc_a19_parked_and_terminal_are_quiet_absent_from_alerts_but_on_the_board(svc):
    now = time.time()
    parked = _add_project(svc, "mca19-parked", lifecycle="LS-3",
                          sessions=[("zz", "mca19a", "USER_FACING", now - 3 * DAY, 4, True)])
    svc.review("accept", parked, {"placement": "paused"})
    shipped = _add_project(svc, "mca19-shipped", lifecycle="LS-4",
                           sessions=[("zz", "mca19b", "USER_FACING", now - 3 * DAY, 4, True)])
    svc.review("accept", shipped, {"placement": "shipped"})
    cards = _cards(svc)
    assert cards[parked]["staleness_band"] == QUIET
    assert cards[shipped]["staleness_band"] == QUIET
    view = svc.staleness_view()
    quiet_ids = {c["project_id"] for c in view["quiet"]["items"]}
    assert {parked, shipped} <= quiet_ids
    assert view["counts"][QUIET] == len(view["quiet"]["items"])
    alerted = _attention_ids(svc)
    assert parked not in alerted and shipped not in alerted
    board_ids = set(cards)
    assert parked in board_ids and shipped in board_ids
    for bucket in view["buckets"]:
        assert parked not in {c["project_id"] for c in bucket["items"]}


# --------------------------------------------------------------------------- MC-A20

def test_mc_a20_band_filter_reproduces_the_view_totals_and_round_trips(svc):
    now = time.time()
    for index, age in enumerate((0.4, 2.0, 4.0, 5.9, 8.0, 20.0, 45.0, 80.0)):
        _add_project(svc, "mca20-{}".format(index), lifecycle="LS-2",
                     sessions=[("zz", "mca20-{}-a".format(index), "USER_FACING",
                                now - age * DAY, 4, True)])
    _add_project(svc, "mca20-null", lifecycle="LS-2",
                 sessions=[("zz", "mca20-null-a", "DELEGATED", now - DAY, 4, True)])
    full = svc.board(view="all", page_size=200)
    band_counts = full["counts"]["staleness_bands"]
    assert set(STALENESS_BAND_FILTERS) <= set(band_counts)
    for key in STALENESS_BAND_FILTERS:
        filtered = svc.board(view="all", band=[key], page_size=200)
        assert filtered["total"] == band_counts[key], key
        assert len(filtered["items"]) == band_counts[key]
        for card in filtered["items"]:
            assert card["staleness_band"] == key
        # the canonical echo round-trips for a used filter
        assert filtered["query"]["bands"] == [key]
    # OR-within
    either = svc.board(view="all", band=["0-3d", "3-7d"], page_size=200)
    assert either["total"] == band_counts["0-3d"] + band_counts["3-7d"]
    assert {c["staleness_band"] for c in either["items"]} <= {"0-3d", "3-7d"}
    # AND-across
    combo = svc.board(view="all", lane=["inbox"], band=["0-3d"], page_size=200)
    assert all(c["column"] == "inbox" and c["staleness_band"] == "0-3d"
               for c in combo["items"])
    assert combo["total"] <= band_counts["0-3d"]
    # the filter vocabulary is closed and an unused filter adds no echo key
    assert "bands" not in svc.board(view="all", page_size=1)["query"]
    with pytest.raises(ValueError, match="invalid band"):
        svc.board(view="all", band=["nope"])


def test_mc_a20_the_band_filter_is_a_real_400_on_the_route(home, monkeypatch):
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
    ok = client.get(standalone.API_PREFIX + "/projects?view=all&band=0-3d")
    assert ok.status_code == 200
    assert ok.json()["query"]["bands"] == ["0-3d"]
    assert client.get(standalone.API_PREFIX + "/projects?view=all&band=nope").status_code == 400


# --------------------------------------------------------------------------- MC-A21

def test_mc_a21_no_age_only_alert_is_introduced(svc):
    now = time.time()
    quiet_old = _add_project(
        svc, "mca21-old", lifecycle="LS-5", stall_age_days=95.0,
        sessions=[("zz", "mca21a", "USER_FACING", now - 95 * DAY, 4, True)])
    driven = _add_project(
        svc, "mca21-driven", lifecycle="LS-5", stall_age_days=12.0, drive_expected=1,
        sessions=[("zz", "mca21b", "USER_FACING", now - 12 * DAY, 4, True)])
    cards = _cards(svc)
    assert cards["mca21-old"]["staleness_band"] == "30d+"
    alerted = _attention_ids(svc)
    # age alone never alerts; an explicit drive expectation does (INV-MC-6 preserved)
    assert "mca21-old" not in alerted
    assert "mca21-driven" in alerted
    # the banded view is where an old project is surfaced, not the attention queue
    view = svc.staleness_view()
    oldest = [b for b in view["buckets"] if b["band"] == "30d+"][0]
    assert "mca21-old" in {c["project_id"] for c in oldest["items"]}
