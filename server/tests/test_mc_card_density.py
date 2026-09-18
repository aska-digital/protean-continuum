"""MC-S3 — card density + click-to-evidence (MC-A11..MC-A15).

Every assertion drives the real service over an isolated fixture home with a temp registry. The
dense grid renders the SAME `/projects` items the eight lanes do: one request, one envelope.
Where the contract is structural (the grid has no second transport) the test says so.
"""
from __future__ import annotations

import json
import os
import re
import time

import pytest

from continuum.config import load_config
from continuum.service import BOARD_COLUMNS, Service
from fixtures.make_fixture import build_fixture_tree

BUILD_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DAY = 86400.0

# MC-L6: the labels that must publish a resolvable claim_source.
DERIVED_LABELS = ("confidence", "attention_state", "derived_lifecycle", "next_action",
                  "stopping_point", "staleness_band", "owner")
# Nothing in a board payload may carry a message body under these names.
BODY_KEYS = {"messages", "message_body", "message", "body", "content", "raw_messages",
             "session_messages", "raw_text", "full_text"}
EXCERPT_CAP = 240


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
                 confidence=0.8, sessions=(), stall_age_days=1.0, primary_index=None,
                 drive_expected=0, workspace=None):
    now = time.time()
    svc.registry.conn.execute(
        "INSERT OR REPLACE INTO project (project_id, name, kind, phase, lifecycle, confidence,"
        " confidence_band, evidence_tier, owner_profile, drive_expected, stall_age_days,"
        " last_substantive_activity, session_count, derived_updated_at, created_at, updated_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (pid, name, "derived", "active", lifecycle, confidence, band, tier, "",
         drive_expected, stall_age_days, now, len(sessions), now, now, now))
    for idx, (prof, sid, audience, last, msgs, present) in enumerate(sessions):
        role = "primary" if primary_index is not None and idx == primary_index else "supporting"
        svc.registry.conn.execute(
            "INSERT OR REPLACE INTO project_session (link_id, project_id, profile_name,"
            " session_id, role_in_project, link_confidence, link_reason, evidence_ref, accepted,"
            " first_linked_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            ("link-{}-{}".format(pid, sid), pid, prof, sid, role, 0.8, "synthetic",
             None, 0, now))
        if present:
            svc.registry.conn.execute(
                "INSERT OR REPLACE INTO session_fact (profile_name, session_id, title, cwd,"
                " message_count, tool_call_count, started_at, last_activity_at, present,"
                " audience, audience_reason) VALUES (?,?,?,?,?,?,?,?,1,?,?)",
                (prof, sid, "title " + sid, workspace or ("/work/" + sid), msgs, 1, last, last,
                 audience, "U"))
    svc.registry.conn.commit()
    return pid


def _add_evidence(svc, pid, *, tier=1, kind="next_action", excerpt="Next step: ship it",
                  profile="zz", session_id="ev1", msg_id="m1"):
    import uuid
    eid = str(uuid.uuid4())
    svc.registry.conn.execute(
        "INSERT OR REPLACE INTO evidence (evidence_id, project_id, cluster_id, profile_name,"
        " session_id, tier, kind, excerpt, locator, extracted_at, source_hash)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (eid, pid, pid, profile, session_id, tier, kind, excerpt,
         json.dumps({"profile": profile, "session_id": session_id, "msg_id": msg_id}),
         time.time(), "hash-" + eid[:8]))
    svc.registry.conn.commit()
    return eid


def _cards(svc, **kw):
    return {c["project_id"]: c for c in svc.board(view="all", page_size=200, **kw)["items"]}


def _app_js() -> str:
    with open(os.path.join(BUILD_ROOT, "dashboard", "static", "app.js"), encoding="utf-8") as fh:
        return fh.read()


# --------------------------------------------------------------------------- MC-A12 resolver

def _resolve_entry(svc, pid, entry) -> str:
    """Return the row kind the claim resolves to, or raise AssertionError with the reason."""
    kind = entry.get("kind")
    conn = svc.registry.conn
    if kind == "evidence":
        row = conn.execute(
            "SELECT 1 FROM evidence WHERE project_id=? AND evidence_id=?",
            (pid, str(entry.get("evidence_id")))).fetchone()
        assert row is not None, "evidence row missing: {}".format(entry)
        return kind
    if kind == "declared_field":
        field = entry.get("field")
        row = conn.execute(
            "SELECT 1 FROM declared_field WHERE project_id=? AND field=?",
            (pid, field)).fetchone()
        if row is None and field in ("next_action", "next_action_verified"):
            row = conn.execute(
                "SELECT 1 FROM next_action WHERE project_id=?", (pid,)).fetchone()
        assert row is not None, "declared-field row missing: {}".format(entry)
        return kind
    if kind == "session_fact":
        row = conn.execute(
            "SELECT 1 FROM session_fact WHERE profile_name=? AND session_id=?",
            (entry.get("profile"), entry.get("session_id"))).fetchone()
        if row is None:
            row = conn.execute(
                "SELECT 1 FROM project_session WHERE project_id=? AND profile_name=?"
                " AND session_id=?",
                (pid, entry.get("profile"), entry.get("session_id"))).fetchone()
        if row is None:
            row = conn.execute("SELECT anchor_session FROM project WHERE project_id=?",
                               (pid,)).fetchone()
            assert row is not None and row["anchor_session"] == "{}/{}".format(
                entry.get("profile"), entry.get("session_id")), \
                "session row missing: {}".format(entry)
        return kind
    if kind == "project_row":
        row = conn.execute("SELECT 1 FROM project WHERE project_id=?", (pid,)).fetchone()
        assert row is not None, "project row missing: {}".format(entry)
        return kind
    raise AssertionError("unknown claim_source kind: {!r}".format(kind))


# --------------------------------------------------------------------------- MC-A11

def test_mc_a11_a_thirteen_project_fixture_is_self_sufficient_without_opening_detail(svc):
    now = time.time()
    for index in range(13):
        sessions = [("zz", "mca11-{}-1".format(index), "USER_FACING", now - (index + 1) * DAY,
                     4, True),
                    ("zz", "mca11-{}-2".format(index), "USER_FACING", now - index * DAY, 4, True)]
        _add_project(svc, "mca11-{}".format(index), name="dense {}".format(index),
                     lifecycle="LS-3" if index % 3 == 0 else "LS-2",
                     band="high" if index % 2 == 0 else "medium",
                     sessions=sessions, primary_index=0)
    board = svc.board(view="all", page_size=200)
    assert len(board["items"]) >= 13, "the fixture must exercise a dense grid"
    for card in board["items"]:
        assert card["name"], card
        assert card["column"] in BOARD_COLUMNS
        assert card["placement_source"], card["project_id"]
        assert card["derived_lifecycle"], card["project_id"]
        assert card["owner"] or card["owner_source"] == "no_owner", card["project_id"]
        assert card["staleness_band"], card["project_id"]
        assert card["confidence_band"], card["project_id"]
        assert card["evidence_tier"] is not None, card["project_id"]
        assert card["attention_state"] and card["attention_reason"], card["project_id"]
        assert card["next_action_state"], card["project_id"]
        assert "text" in card["next_action"], card["project_id"]
        assert card["source_sessions"], "a row needs at least one session ref"
        # the dense row is self-sufficient: no detail endpoint was opened for it
    # the owner column resolves declared -> primary profile -> no_owner
    pid = "mca11-1"
    assert _cards(svc)[pid]["owner"] == "zz"
    assert _cards(svc)[pid]["owner_source"] == "primary_profile"
    svc.registry.set_declared(pid, "owner", "ahraz", actor="test")
    assert _cards(svc)[pid]["owner"] == "ahraz"
    assert _cards(svc)[pid]["owner_source"] == "declared"
    plain = _add_project(svc, "mca11-noowner", sessions=[("zz", "mca11-noowner-1",
                                                          "DELEGATED", now - DAY, 3, True)])
    assert _cards(svc)[plain]["owner"] is None
    assert _cards(svc)[plain]["owner_source"] == "no_owner"


# --------------------------------------------------------------------------- MC-A12

def test_mc_a12_every_derived_label_resolves_through_claim_source(svc):
    now = time.time()
    pid = _add_project(svc, "mca12-declared", lifecycle="LS-3",
                       sessions=[("zz", "mca12a", "USER_FACING", now - 2 * DAY, 4, True),
                                 ("zz", "mca12b", "USER_FACING", now - 6 * DAY, 4, True)],
                       primary_index=0)
    _add_evidence(svc, pid, kind="next_action", excerpt="Next step: wire the gate")
    svc.registry.set_next_action(pid, "Declared next action text", source="declared", actor="test")
    svc.registry.set_declared(pid, "owner", "ahraz", actor="test")
    _add_evidence(svc, pid, kind="blocker", excerpt="blocked on credentials")
    bare = _add_project(svc, "mca12-bare", lifecycle="LS-9", band="low", tier=0,
                        sessions=[("zz", "mca12c", "DELEGATED", now - 90 * DAY, 2, True)])
    cards = _cards(svc)
    for card in cards.values():
        sources = card["claim_source"]
        assert isinstance(sources, dict) and sources, card["project_id"]
        assert set(DERIVED_LABELS) <= set(sources), (card["project_id"], sorted(sources))
        for label in DERIVED_LABELS:
            entry = sources.get(label)
            # MC-A12 kill site: a derived label with a null source fails the test
            assert entry is not None, "{} published no claim_source".format(label)
            assert entry.get("basis"), (card["project_id"], label)
            _resolve_entry(svc, card["project_id"], entry)
    assert cards[pid]["claim_source"]["owner"]["kind"] == "declared_field"
    assert cards[pid]["claim_source"]["owner"]["field"] == "owner"
    assert cards[pid]["claim_source"]["staleness_band"]["kind"] == "session_fact"
    assert cards[pid]["claim_source"]["staleness_band"]["profile"] == "zz"
    assert cards[bare]["claim_source"]["staleness_band"]["kind"] == "project_row"
    assert cards[bare]["owner_source"] == "no_owner"
    assert cards[bare]["claim_source"]["owner"]["kind"] == "project_row"
    for card in cards.values():
        for label in DERIVED_LABELS:
            entry = card["claim_source"][label]
            # every entry carries the identity of the row it resolves to
            if entry["kind"] in ("project_row", "declared_field"):
                assert entry["project_id"] == card["project_id"], label
            elif entry["kind"] == "evidence":
                assert entry["evidence_id"], label
            elif entry["kind"] == "session_fact":
                assert entry["profile"] and entry["session_id"], label


# --------------------------------------------------------------------------- MC-A13

def test_mc_a13_the_board_carries_no_message_body_and_no_tier_two_authority(svc):
    now = time.time()
    pid = _add_project(svc, "mca13", lifecycle="LS-3",
                       sessions=[("zz", "mca13a", "USER_FACING", now - DAY, 4, True)])
    _add_evidence(svc, pid, kind="next_action", excerpt="x" * EXCERPT_CAP)
    payload = svc.board(view="all", page_size=200)
    blob = json.dumps(payload)
    assert "transcript" not in blob.lower()
    for card in payload["items"]:
        assert not (set(card) & BODY_KEYS), sorted(set(card) & BODY_KEYS)
        for ref in card["evidence_refs"]:
            assert len(ref["excerpt"] or "") <= EXCERPT_CAP, "an excerpt must stay bounded"
            assert set(ref) <= {"evidence_id", "tier", "kind", "excerpt", "locator",
                                "source_hash", "extracted_at"}, sorted(ref)
            assert ref["locator"].get("profile") and ref["locator"].get("session_id")
        # Tier 2 is additive only: it is never the sole basis for a card
        assert card["evidence_tiers"] != [2], card["project_id"]
        assert 2 not in card["evidence_tiers"], "the model step is disabled in this build"


# --------------------------------------------------------------------------- MC-A14

def test_mc_a14_the_grid_renders_the_same_items_with_no_second_transport(svc):
    now = time.time()
    for index in range(4):
        _add_project(svc, "mca14-{}".format(index), lifecycle="LS-2" if index else "LS-3",
                     sessions=[("zz", "mca14-{}-a".format(index), "USER_FACING",
                                now - DAY, 4, True)])
    src = _app_js()
    start = src.index("function renderDenseGrid(data) {")
    end = src.index("function renderBands(payload) {")
    grid = src[start:end]
    assert "fetch(" not in grid, "the dense grid must not open a second request"
    assert "data.items" in grid, "the dense grid renders the same returned items"
    assert "c-lane" not in grid
    # one layout decision, one data source: the lanes and the grid share `data`
    assert "state.layout === 'grid' ? renderDenseGrid(data) : renderBoard(data)" in src
    assert "GRID_COLUMNS" in src and len(_GRID_COLUMNS(src)) == 7

    filtered = svc.board(view="all", lane=["inbox"], page_size=200)
    unfiltered = svc.board(view="all", page_size=200)
    assert all(c["column"] == "inbox" for c in filtered["items"])
    assert filtered["counts"]["total"] == len(filtered["items"])
    assert unfiltered["counts"]["continuity_total"] == unfiltered["counts"]["total"]
    assert {c["project_id"] for c in filtered["items"]} <= {
        c["project_id"] for c in unfiltered["items"]}
    # the header row and every row come from the same item set
    assert filtered["items"] and all("staleness_band" in c and "claim_source" in c
                                     for c in filtered["items"])


def _GRID_COLUMNS(src: str):
    match = re.search(r"const GRID_COLUMNS = \[([^\]]*)\]", src)
    assert match, "GRID_COLUMNS must be declared"
    return [part for part in re.findall(r"'([^']*)'", match.group(1)) if part]


# --------------------------------------------------------------------------- MC-A15

def test_mc_a15_the_eight_lanes_remain_present_and_unchanged(svc):
    assert BOARD_COLUMNS == ("inbox", "ongoing", "blocked", "waiting_on_you", "paused", "done",
                             "shipped", "scrapped")
    assert len(BOARD_COLUMNS) == 8
    src = _app_js()
    assert "BOARD_COLUMNS.map((col)" in src, "the lanes stay the lanes"
    counts = svc.board(view="all", page_size=200)["counts"]
    for column in BOARD_COLUMNS:
        assert column in counts, column
    assert counts["total"] == len(svc.board(view="all", page_size=200)["items"])
