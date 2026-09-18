"""User-facing session filter — audience classification and primary/resume policy.

Implements architecture decision ``continuum-azaraki-user-session-filter`` D-US-1 .. D-US-9:
T1..T8 plus the EvoPet anchor resolution. Every fixture is synthetic and self-contained
(``tests/fixtures/audience``); no test reads live state.

The reported defect: the EvoPet card offered ``hermes -p azaraki --resume 20260911_174725_516587``
— a delegated worker the user never typed into. After this change the card resolves the
user-facing anchor ``lugia/20260911_163629_22fff0``.
"""
from __future__ import annotations

import os
import sys

import pytest

from continuum import audience as audience_mod
from continuum.config import load_config
from continuum.service import Service

FIXTURES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "audience")
if FIXTURES_DIR not in sys.path:
    sys.path.insert(0, FIXTURES_DIR)

from build_audience_home import (  # noqa: E402
    ANCHOR_PROFILE,
    ANCHOR_SESSION,
    BRIEFS_DIR,
    DIRECT_AZARAKI,
    DIRECT_KODEKOOT,
    EVOPET_CWD,
    EVOPET_MEMBERS,
    TARGET_PROFILE,
    TARGET_SESSION,
    build_audience_home,
)

AZ_ID = "20260912_101010_f1f1f1"
F5_ID = "20260912_111111_f5f5f5"
CRON_ID = "20260912_120000_cr0n1"
SUB_ID = "20260912_130000_sub01"
KAN_ID = "20260912_140000_kan01"
GRP_ID = "20260912_150000_grp01"
NAN_USER_ID = "20260912_150200_nan01"
EVOPET_REF = "{}/{}".format(ANCHOR_PROFILE, ANCHOR_SESSION)
TARGET_REF = "{}/{}".format(TARGET_PROFILE, TARGET_SESSION)

BRIEF_ROOTS = [BRIEFS_DIR]
WORKSPACE_PATTERNS = [r"^(?P<repo>/Users/kethuda/evopet-pet)$"]


def _cfg(home, *, brief_roots=None, registry="registry.db"):
    cfg = load_config(hermes_home=home)
    cfg.bundle["registry_path"] = os.path.join(home, registry)
    cfg.bundle["audience_brief_roots"] = list(BRIEF_ROOTS if brief_roots is None else brief_roots)
    cfg.bundle["workspace_patterns"] = list(WORKSPACE_PATTERNS)
    cfg.bundle["workspace_git_walkup"] = False
    return cfg


@pytest.fixture()
def home(tmp_path):
    return build_audience_home(str(tmp_path / "hermes_home"))


@pytest.fixture()
def svc(home):
    service = Service(_cfg(home))
    service.scan()
    return service


def _aud(svc, profile, session_id):
    return svc.registry.session_audience(profile, session_id)


def _project_id(svc, name):
    row = svc.registry.conn.execute(
        "SELECT project_id FROM project WHERE name=?", (name,)).fetchone()
    return row["project_id"] if row else None


# --------------------------------------------------------------------------- T1

def test_t1_exact_target_session_is_delegated_c1(svc):
    """T1 — the exact reported session classifies DELEGATED with reason C1 (brief digest)."""
    value, reason, evidence_ref = _aud(svc, TARGET_PROFILE, TARGET_SESSION)
    assert value == "DELEGATED", (value, reason)
    assert reason == "C1"
    assert evidence_ref and evidence_ref.endswith("azaraki-brief.md"), evidence_ref


# --------------------------------------------------------------------------- T2

def test_t2_no_board_resume_link_points_at_the_target(svc):
    """T2 — no resume affordance anywhere on the board/inbox/detail targets the worker.

    Fails on the pre-change build: the shipped ``review/out/live-inbox.json`` contains
    ``hermes -p azaraki --resume 20260911_174725_516587``.
    """
    seen = []
    board = svc.board()
    for card in board["items"]:
        for s in card.get("sessions", []) or []:
            seen.extend([s.get("cli_resume"), s.get("cli_resume_profile_scoped"),
                         s.get("copy_command"), s.get("copy_command_profile_scoped")])
    for it in svc.inbox()["items"]:
        for link in it["resume_links"]:
            seen.extend([link["copy_command"], link["copy_command_profile_scoped"]])
            seen.extend([link.get("audience")])
    for pid, p in [(p["project_id"], p) for p in svc.registry.projects()]:
        detail = svc.project_detail(pid)
        for link in detail.get("resume_links", []):
            seen.extend([link["copy_command"], link["copy_command_profile_scoped"]])
    offenders = [s for s in seen if s and TARGET_SESSION in s]
    assert offenders == [], "board/inbox/detail still offer the delegated worker: {}".format(
        offenders)


# --------------------------------------------------------------------------- T3

def test_t3_direct_worker_profile_sessions_are_user_facing(svc):
    """T3 — the anti-over-exclusion guard: direct non-Lugia sessions stay USER_FACING."""
    assert _aud(svc, "azaraki", DIRECT_AZARAKI) == ("USER_FACING", "U", None)
    assert _aud(svc, "kodekoot", DIRECT_KODEKOOT) == ("USER_FACING", "U", None)


# --------------------------------------------------------------------------- T4

def test_t4_delegator_origin_session_is_user_facing(svc):
    """T4 — the positive U-DELEGATOR signal works without any profile allowlist."""
    value, reason, evidence_ref = _aud(svc, ANCHOR_PROFILE, ANCHOR_SESSION)
    assert value == "USER_FACING"
    assert reason == "U-DELEGATOR"
    assert evidence_ref == "async_delegations"


# --------------------------------------------------------------------------- T5

def _board_session_audiences(svc):
    out = []
    for card in svc.board()["items"]:
        for s in card.get("sessions", []) or []:
            a = _aud(svc, s["profile"], s["session_id"])
            out.append((card["project_id"], s["profile"], s["session_id"], a[0]))
    return out


def test_t5_primary_is_never_delegated_or_automated(svc):
    """T5 — the D-US-6 step-4 invariant, over every project in the fixture tree."""
    rows = svc.registry.conn.execute(
        "SELECT ps.project_id, ps.profile_name, ps.session_id, ps.role_in_project, sf.audience "
        "FROM project_session ps LEFT JOIN session_fact sf "
        "ON sf.profile_name=ps.profile_name AND sf.session_id=ps.session_id").fetchall()
    assert rows, "fixture must produce projects"
    for r in rows:
        aud = r["audience"] or "UNKNOWN"
        if r["role_in_project"] == "primary":
            assert aud in ("USER_FACING", "UNKNOWN"), (r["project_id"], r["session_id"], aud)
    for pid, profile, sid, aud in _board_session_audiences(svc):
        assert aud not in ("DELEGATED", "AUTOMATED"), (pid, profile, sid, aud)
    # the guard used by the service agrees
    assert audience_mod.assert_no_resume_on_delegated(svc.board()["items"]) == []
    assert audience_mod.assert_no_resume_on_delegated(svc.inbox()["items"]) == []


def test_t5b_no_resume_link_entry_is_delegated(svc):
    for it in svc.inbox()["items"]:
        for link in it["resume_links"]:
            assert link["audience"] not in ("DELEGATED", "AUTOMATED")
            assert link["copy_command"] == "hermes --resume " + link["session_id"]
            assert link["copy_command_profile_scoped"] == \
                "hermes -p {} --resume {}".format(link["profile"], link["session_id"])


# --------------------------------------------------------------------------- T6

def test_t6_audience_is_total_and_closed(svc):
    """T6 — every session_fact row carries one of the four closed values."""
    values = {r["audience"] for r in svc.registry.conn.execute(
        "SELECT DISTINCT audience FROM session_fact")}
    assert values, "audience must be written for every session"
    assert values <= set(audience_mod.AUDIENCES), values
    n = svc.registry.conn.execute("SELECT COUNT(*) AS n FROM session_fact").fetchone()["n"]
    n_aud = svc.registry.conn.execute(
        "SELECT COUNT(*) AS n FROM session_fact WHERE audience IS NOT NULL").fetchone()["n"]
    assert n == n_aud


# --------------------------------------------------------------------------- T7

def test_t7_disabling_c1_flips_t1_to_red(home):
    """T7 — mutation proof: with the brief index absent, C1 cannot fire, so T1 is RED."""
    on = Service(_cfg(home, registry="registry-c1-on.db"))
    on.scan()
    assert _aud(on, TARGET_PROFILE, TARGET_SESSION)[1] == "C1"

    off = Service(_cfg(home, registry="registry-c1-off.db", brief_roots=[]))
    off.scan()
    value, reason, _ref = _aud(off, TARGET_PROFILE, TARGET_SESSION)
    assert value == "DELEGATED"
    assert reason != "C1", "mutation did not disable C1"
    assert reason == "C2", reason
    # the anti-over-exclusion guard is unaffected by the mutation
    assert _aud(off, "azaraki", DIRECT_AZARAKI)[0] == "USER_FACING"


# --------------------------------------------------------------------------- rule coverage

def test_c1_fixture_session_matches_backing_brief_file(svc):
    assert _aud(svc, "azaraki", AZ_ID) == ("DELEGATED", "C1",
                                           os.path.join(BRIEFS_DIR, "synthetic_brief.md"))


def test_c2_envelope_without_backing_file(svc):
    assert _aud(svc, "azaraki", F5_ID)[:2] == ("DELEGATED", "C2")


def test_a1_a2_a3_automated_sources(svc):
    assert _aud(svc, "azaraki", CRON_ID)[:2] == ("AUTOMATED", "A1")
    assert _aud(svc, "azaraki", SUB_ID)[:2] == ("AUTOMATED", "A2")
    assert _aud(svc, "azaraki", KAN_ID)[:2] == ("AUTOMATED", "A3")


def test_f7_group_relay_is_not_user_facing_and_never_primary(svc):
    assert _aud(svc, "azaraki", GRP_ID)[0] == "UNKNOWN"
    pid = _project_id(svc, "Nanaveda recipe relay")
    assert pid is not None
    links = {r["session_id"]: r["role_in_project"] for r in svc.registry.links_for(pid)}
    assert links.get(GRP_ID) != "primary"
    assert links.get(NAN_USER_ID) == "primary"


def test_c5_role_declaration_never_stands_alone(svc):
    """C5 corroborates C2/C3/C4 only — a user turn that names an agent must not be excluded."""
    fact = {"source": "cli", "parent_session_id": None, "hidden": 0, "chat_type": None,
            "display_name": None, "title": "Review the pull request", "session_id": "s1"}
    head = "Review https://github.com/ahrazzle/team6-kit/pull/4 as Halakukhan.\n\nrole: reviewer\n"
    assert audience_mod.role_declaration(head, svc.cfg) is True
    value, reason, _ = audience_mod.classify_audience(fact, head, {}, set(), svc.cfg)
    assert value == "USER_FACING", (value, reason)

    # ... and when C2 also fires it is DELEGATED by C2 (C5 corroborates, never the reason)
    env = "---\ngoal: x\nteam6_agent: azaraki\nfrom: lugia\n---\n\nrole: writer\n"
    assert audience_mod.classify_audience(fact, env, {}, set(), svc.cfg)[:2] == ("DELEGATED", "C2")


# --------------------------------------------------------------------------- anchor + outcome

def test_evopet_anchor_resolves_to_the_user_conversation(svc):
    """The user outcome: the EvoPet card resumes the Lugia conversation, not the worker."""
    pid = _project_id(svc, "evopet-pet")
    assert pid is not None, "the EvoPet cluster must form in the fixture tree"

    for profile, sid, _title in EVOPET_MEMBERS:
        assert _aud(svc, profile, sid)[0] == "DELEGATED", (profile, sid)

    detail = svc.project_detail(pid)
    card = detail["project"]
    assert card["anchor_session"] == EVOPET_REF
    assert card["anchor_reason"] == "ANCHOR-NAME"
    assert card["anchor"]["copy_command_profile_scoped"] == \
        "hermes -p {} --resume {}".format(ANCHOR_PROFILE, ANCHOR_SESSION)
    assert card["audience_counts"]["DELEGATED"] == len(EVOPET_MEMBERS)
    assert card["audience_counts"]["USER_FACING"] == 0
    assert card["primary_session"] is None
    assert card["no_user_session"] is False  # the anchor IS the user conversation
    assert card["conversation_line"] == "Conversation: {} ({})".format(
        "Document evopet handoff package", ANCHOR_PROFILE)

    # the card's resume affordances never point at the Azaraki worker
    for link in detail["resume_links"]:
        assert TARGET_SESSION not in link["copy_command_profile_scoped"]
    for s in detail["sessions"]:
        assert not (s.get("copy_command") and TARGET_SESSION in s["copy_command"])


def test_no_anchor_uses_the_architecture_copy(svc, home):
    """anchor_reason NONE => the card carries the exact fallback copy and no anchor."""
    cfg = _cfg(home, registry="registry-noanchor.db")
    cfg.bundle["stopword_tokens"] = (cfg.get("stopword_tokens") or []) + ["evopet", "nanaveda"]
    service = Service(cfg)
    service.scan()
    row = service.registry.conn.execute(
        "SELECT project_id, anchor_session, anchor_reason FROM project WHERE name='evopet-pet'"
    ).fetchone()
    assert row["anchor_session"] is None
    assert row["anchor_reason"] == "NONE"
    card = service.project_detail(row["project_id"])["project"]
    assert card["anchor"] is None
    assert card["conversation_line"] == audience_mod.no_user_session_line()
    assert card["conversation_line"] == "No user-facing conversation found for this project."
