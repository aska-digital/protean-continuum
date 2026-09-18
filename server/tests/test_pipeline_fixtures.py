"""Deterministic clustering / noise / lifecycle — isolated fixtures only.

Every assertion is an acceptance vector (AV-2..AV-8) or an invariant (INV-4/INV-6/CR-2).
No live database is touched.
"""
from __future__ import annotations

import os

import pytest

from continuum import cluster as cluster_mod
from continuum import scanner
from continuum.config import load_config
from continuum.service import Service
from fixtures.make_fixture import TJGC1, build_fixture_tree


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


def _all_links(svc):
    rows = svc.registry.conn.execute(
        "SELECT project_id, profile_name, session_id FROM project_session").fetchall()
    return [(r["project_id"], r["profile_name"], r["session_id"]) for r in rows]


def _project_for(svc, profile, session_id):
    for pid, p, sid in _all_links(svc):
        if p == profile and sid == session_id:
            return pid
    return None


def test_two_fixture_profiles_are_scanned(home):
    cfg = load_config(hermes_home=home)
    refs = scanner.discover_profiles(home, cfg)
    assert [r.profile_name for r in refs] == ["alpha", "beta"]


def test_av3_shared_git_repo_collapses_high_across_profiles(svc):
    pids = {_project_for(svc, "alpha", s) for s in
            ("20260101_000001_aaa111", "20260102_000002_aaa222", "20260103_000003_aaa333")}
    pids.add(_project_for(svc, "beta", "20260201_000001_bbb901"))
    assert len(pids) == 1, "the shared git repo must yield one cluster"
    pid = pids.pop()
    row = svc.registry.get_project(pid)
    assert row["confidence_band"] == "high"
    # 3 alpha sessions + 1 beta session + the folded subagent child (AV-7)
    assert row["session_count"] == 5
    assert _project_for(svc, "alpha", "20260114_000014_fff111") == pid


def test_av4_generic_cwd_sessions_are_never_clustered(svc):
    generic_sessions = ("20260106_000006_ccc111", "20260107_000007_ccc222",
                        "20260108_000008_ccc333", "20260109_000009_ccc444")
    dot_sessions = ("20260110_000010_ddd111", "20260111_000011_ddd222")
    for sid in generic_sessions + dot_sessions:
        assert _project_for(svc, "alpha", sid) is None, "{} must not be in a cluster".format(sid)


def test_av2_cron_sessions_are_noise_not_projects(svc):
    rows = dict(svc.registry.conn.execute(
        "SELECT session_id, noise_class FROM session_fact WHERE source='cron'"))
    assert rows == {"20260112_000012_eee111": "NS-1", "20260113_000013_eee222": "NS-1"}
    for sid in rows:
        assert _project_for(svc, "alpha", sid) is None


def test_av7_child_session_folds_into_parent(svc):
    child = _project_for(svc, "alpha", "20260114_000014_fff111")
    parent = _project_for(svc, "alpha", "20260101_000001_aaa111")
    assert child is not None and child == parent
    assert svc.registry.conn.execute(
        "SELECT noise_class FROM session_fact WHERE session_id='20260114_000014_fff111'"
    ).fetchone()["noise_class"] == "NS-2"


def test_av6_field_provenance_classes(svc):
    rows = {r["session_id"]: (r["title"], r["title_source"], r["is_noise"])
            for r in svc.registry.conn.execute(
                "SELECT session_id, title, title_source, is_noise FROM session_fact")}
    assert rows["20260116_000016_hhh111"][:2] == ("My Own Project Name", "user")
    assert rows["20260117_000017_iii111"][:2] == ("llm generated title", "llm")
    assert rows["20260115_000015_ggg111"][2] == 1  # UNTITLED -> NS-6 fragment


def test_untitled_fragment_is_ns6(svc):
    row = svc.registry.conn.execute(
        "SELECT noise_class FROM session_fact WHERE session_id='20260115_000015_ggg111'"
    ).fetchone()
    assert row["noise_class"] == "NS-6"


def test_test_handshake_is_ns4(svc):
    row = svc.registry.conn.execute(
        "SELECT noise_class FROM session_fact WHERE session_id='20260118_000018_jjj111'"
    ).fetchone()
    assert row["noise_class"] == "NS-4"


def test_av5_stale_only_project_emits_zero_alerts(svc):
    """CR-4: staleness alone is never an alert."""
    stale_pid = _project_for(svc, "alpha", "20260104_000004_bbb111")
    assert stale_pid is not None
    row = svc.registry.get_project(stale_pid)
    assert row["lifecycle"] in ("LS-5", "LS-8"), row["lifecycle"]
    assert row["drive_expected"] == 0
    alerted = {i["project_id"] for g in svc.attention()["groups"].values() for i in g}
    assert stale_pid not in alerted


def test_awaiting_user_alerts_regardless_of_age(svc):
    git_pid = _project_for(svc, "alpha", "20260101_000001_aaa111")
    alert = svc.attention()
    waiting = {i["project_id"] for i in alert["groups"]["awaiting_user"]}
    assert git_pid in waiting
    assert alert["attention_count"] >= 1
    # every attention row carries an explicit reason (AC1.2)
    for group in alert["groups"].values():
        for item in group:
            assert item["reason"]


def test_board_excludes_unknown_and_parked(svc):
    # kanban re-baseline D-KB-14 section 11.3 delta 4: Inbox holds every candidate including low/unknown; board has one lane per card
    board = svc.board()
    for card in board["items"]:
        assert card["column"] in ("inbox", "ongoing", "blocked", "waiting_on_you", "paused", "done", "shipped", "scrapped")
    # low/unknown candidates live in Inbox on the board, CR-3 gating is on attention
    attention_ids = {i["project_id"] for g in svc.attention()["groups"].values() for i in g}
    for card in board["items"]:
        if card["confidence_band"] in ("low", "unknown"):
            assert card["column"] == "inbox", "low/unknown board card must be in Inbox"
            assert card["project_id"] not in attention_ids, "low/unknown must not appear in attention (CR-3)"


def test_candidate_cards_expose_lifecycle_confidence_tier_stall_and_sources(svc):
    inbox = svc.inbox()
    assert inbox["total"] >= 1
    for c in inbox["items"]:
        assert c["lifecycle"].startswith("LS-")
        assert c["confidence_band"] in ("high", "medium", "low", "unknown")
        assert c["evidence_tier"] in (0, 1, 2)
        assert c["stall_age_days"] is not None
        assert len(c["member_refs"]) >= 2
        for ref in c["member_refs"]:
            assert ref["profile"] and ref["session_id"]
        for link in c["resume_links"]:
            assert link["session_id"] and link["profile"]


def test_av8_resume_links_preserve_ids_byte_identically(svc):
    expected = {r["session_id"] for r in svc.registry.conn.execute(
        "SELECT session_id FROM project_session")}
    seen = set()
    for c in svc.inbox()["items"]:
        for link in c["resume_links"]:
            seen.add(link["session_id"])
            assert link["copy_command"] == "hermes --resume " + link["session_id"]
            assert link["copy_command_profile_scoped"] == \
                "hermes -p {} --resume {}".format(link["profile"], link["session_id"])
            assert link["route"] is None  # OD3 not invented
    assert seen <= expected


def test_cr2_no_generic_merge(home):
    cfg = load_config(hermes_home=home)
    refs = scanner.discover_profiles(home, cfg)
    batch = scanner.scan(refs, cfg=cfg)
    from continuum import evidence as evidence_mod
    noise = cluster_mod.classify_noise(batch.facts, batch.probes, cfg)
    keys = evidence_mod.canonical_keys(batch.facts, [], batch.probes, cfg)
    clusters = cluster_mod.build_clusters(batch.facts, keys, cfg, noise)
    assert cluster_mod.assert_no_generic_merge(clusters, cfg) == []


def test_no_network_calls_during_a_scan(home, monkeypatch):
    """INV-4: nothing in the scan path opens a socket."""
    import socket

    def _boom(*a, **k):
        raise AssertionError("scan attempted network egress")

    monkeypatch.setattr(socket, "create_connection", _boom)
    monkeypatch.setattr(socket.socket, "connect", _boom)
    cfg = load_config(hermes_home=home)
    cfg.bundle["registry_path"] = os.path.join(home, "registry2.db")
    Service(cfg).scan()


# ---------------------------------------------------------------------------
# F7: CS-3/CR-2 token clustering — real coverage
# ---------------------------------------------------------------------------

def test_cs3_strong_token_unions_sessions(home):
    """CS-3: a strong token (repo, path, issue) actually unions sessions.

    The default fixture config has token_max_frequency=0.02 which makes CS-3
    impossible for 20 sessions (max freq = 0). Override to make it testable.
    """
    from continuum import evidence as evidence_mod
    cfg = load_config(hermes_home=home)
    # Override: allow tokens appearing in up to 50% of sessions, min 2
    cfg.bundle["token_max_frequency"] = 0.50
    cfg.bundle["token_min_sessions"] = 2
    refs = scanner.discover_profiles(home, cfg)
    batch = scanner.scan(refs, cfg=cfg)
    noise = cluster_mod.classify_noise(batch.facts, batch.probes, cfg)
    keys = evidence_mod.canonical_keys(batch.facts, [], batch.probes, cfg)

    # Find repo: tokens (these are strong)
    repo_keys = {k: v for k, v in keys.items() if k.startswith("repo:")}
    assert repo_keys, "expected repo tokens to form with overridden config"

    # A repo token must reference at least 2 sessions
    for tok, refs_list in repo_keys.items():
        assert len(refs_list) >= 2, "repo token {} has fewer than 2 sessions".format(tok)

    # The tjgc1 sessions must share a repo token
    tjgc1_ref = "alpha/20260101_000001_aaa111"
    tjgc1_tokens = [k for k, v in keys.items() if tjgc1_ref in v and k.startswith("repo:")]
    assert tjgc1_tokens, "tjgc1 session should have repo tokens"

    # The beta session sharing tjgc1 must also appear in the same token
    beta_ref = "beta/20260201_000001_bbb901"
    for tok in tjgc1_tokens:
        assert beta_ref in keys[tok], (
            "beta tjgc1 session missing from token {}: {}".format(tok, keys[tok]))


def test_cr2_bare_noun_never_unions(home):
    """CR-2: bare prose nouns never union two sessions (weak_token_clustering=false)."""
    from continuum import evidence as evidence_mod
    cfg = load_config(hermes_home=home)
    cfg.bundle["token_max_frequency"] = 0.50
    cfg.bundle["token_min_sessions"] = 2
    refs = scanner.discover_profiles(home, cfg)
    batch = scanner.scan(refs, cfg=cfg)
    noise = cluster_mod.classify_noise(batch.facts, batch.probes, cfg)
    keys = evidence_mod.canonical_keys(batch.facts, [], batch.probes, cfg)

    # Build clusters — with weak_token_clustering=false, bare nouns must not union
    clusters = cluster_mod.build_clusters(batch.facts, keys, cfg, noise)
    violations = cluster_mod.assert_no_generic_merge(clusters, cfg)
    assert violations == [], "CR-2 violations: {}".format(violations)


def test_cs3_token_edge_never_bridges_workspaces(home):
    """A token edge must never merge two different workspace groups."""
    from continuum import evidence as evidence_mod
    cfg = load_config(hermes_home=home)
    cfg.bundle["token_max_frequency"] = 0.50
    cfg.bundle["token_min_sessions"] = 2
    refs = scanner.discover_profiles(home, cfg)
    batch = scanner.scan(refs, cfg=cfg)
    noise = cluster_mod.classify_noise(batch.facts, batch.probes, cfg)
    keys = evidence_mod.canonical_keys(batch.facts, [], batch.probes, cfg)
    clusters = cluster_mod.build_clusters(batch.facts, keys, cfg, noise)

    # The tywebsite sessions (WS workspace) must NOT merge with tjgc1 sessions
    ws_refs = {"alpha/20260104_000004_bbb111", "alpha/20260105_000005_bbb222"}
    tjgc1_refs = {"alpha/20260101_000001_aaa111", "alpha/20260102_000002_aaa222",
                  "alpha/20260103_000003_aaa333", "beta/20260201_000001_bbb901"}

    for c in clusters:
        members = set(c.member_refs)
        has_ws = bool(members & ws_refs)
        has_tjgc1 = bool(members & tjgc1_refs)
        assert not (has_ws and has_tjgc1), (
            "token edge bridged workspaces: cluster {} has both ws and tjgc1 members: {}".format(
                c.cluster_id, members))


def test_hash_issue_requires_four_digits():
    """#10 is not a project key; #1042 is. The regex requires 4-6 digits."""
    from continuum.evidence import _HASH_ISSUE_RE
    # #10 should NOT match (too few digits)
    assert _HASH_ISSUE_RE.search("#10 is not a key") is None
    # #1042 SHOULD match
    m = _HASH_ISSUE_RE.search("fixed in #1042")
    assert m is not None
    assert m.group(1) == "1042"
    # #123456 should match (6 digits)
    m = _HASH_ISSUE_RE.search("see #123456")
    assert m is not None
    # #1234567 should NOT match (7 digits)
    assert _HASH_ISSUE_RE.search("#1234567 is too long") is None


# ---------------------------------------------------------------------------
# R7: Falsifiable CS-3/CR-2 tests — assert on partitions, not key existence
# ---------------------------------------------------------------------------

import time as _time
from continuum.scanner import SessionFact as _SF


def _fact(profile, sid, *, cwd=None, ws=None, git=None, title=None,
          source="cli", parent=None, msgs=4, tools=2):
    """Build a minimal SessionFact for clustering tests."""
    now = _time.time()
    return _SF(
        profile_name=profile, session_id=sid, source=source,
        title=title, title_source="derived" if title else None,
        display_name=None, cwd=cwd, workspace_root=ws,
        git_repo_root=git, git_branch=None,
        started_at=now - 86400, last_activity_at=now - 3600,
        message_count=msgs, tool_call_count=tools,
        parent_session_id=parent, model=None, tool_names=None,
        archived=0, pinned=0, hidden=0, content_hash="hash_" + sid,
    )


def _cluster_map(clusters):
    """Return {ref: cluster_id} for all members."""
    out = {}
    for c in clusters:
        for m in c.member_refs:
            out[m] = c.cluster_id
    return out


def test_ws_guard_prevents_cross_workspace_token_merging():
    """R7 state test (workspace guard): cross-workspace token holders stay separate.

    State assertion, NOT a single-mutation falsifier after T3. Two sessions in different
    workspaces share a strong token (repo:X); with the guard they stay in separate
    clusters. T3 removed the only union call in the CS-3 pass, so no CS-3 bucketing
    mutation (M3) can change membership: measured 0 failed / 55 passed under M3, and
    1 failed / 54 passed under M3+M4, this test passing in both. The nearest live kill
    site for the partition it asserts is removal of the CS-1/CS-2 grouping loop (H-2),
    which (measured) fails this test on its second assertion.
    """
    from continuum.config import Config
    cfg = Config()

    facts = [
        _fact("alpha", "a1", cwd="/work/projA", ws="/work/projA",
              title="work on project A"),
        _fact("alpha", "a2", cwd="/work/projA", ws="/work/projA",
              title="more work on project A"),
        _fact("alpha", "b1", cwd="/work/projB", ws="/work/projB",
              title="work on project B"),
    ]

    # All three share a strong token (repo:shared-repo)
    keys = {"repo:shared-repo": ["alpha/a1", "alpha/a2", "alpha/b1"]}

    clusters = cluster_mod.build_clusters(facts, keys, cfg, {})
    cmap = _cluster_map(clusters)

    # a1 and a2 should be together (same workspace via CS-2)
    assert cmap.get("alpha/a1") == cmap.get("alpha/a2"), \
        "a1 and a2 should be in the same cluster (same workspace)"

    # b1 must NOT join them (different workspace; token can't bridge)
    assert cmap.get("alpha/b1") != cmap.get("alpha/a1"), \
        "b1 must not merge with a1/a2 — different workspace, guard prevents it"


def test_bare_noun_never_unions_unclustered_sessions():
    """CR-2 corollary: a weak-token-only pair with NO workspace forms no cluster at all.

    Pure state corollary (INV-M4-C): no kill site after T3, and NOT the M4 falsifier.
    The M4 falsifier is test_weak_token_guard_never_registers_union_key. This test
    asserts a membership consequence that no post-T3 mutation can vary: measured, it
    passes under M3 (0 failed / 55 passed) and under M3+M4 (1 failed / 54 passed, that
    failure being the guard's own test). The earlier "Kill site: M3+M4 combined only"
    label, prescribed by decision §7 T1, is superseded by measurement.
    """
    from continuum.config import Config
    cfg = Config()

    facts = [
        _fact("alpha", "c1", cwd=None, ws=None,
              title="random note about deployment"),
        _fact("alpha", "c2", cwd=None, ws=None,
              title="another note about deployment"),
    ]

    # Preconditions: neither session has any workspace key; the only shared key is weak.
    assert [f.workspace_root for f in facts] == [None, None]
    assert [f.git_repo_root for f in facts] == [None, None]
    keys = {"noun:deployment": ["alpha/c1", "alpha/c2"]}
    assert list(keys) == ["noun:deployment"]
    assert cluster_mod.is_strong_token("noun:deployment") is False

    clusters = cluster_mod.build_clusters(facts, keys, cfg, {})
    assert clusters == [], (
        "weak-token-only pair must form no cluster; got {}".format(
            [c.member_refs for c in clusters]))


def test_weak_token_guard_never_registers_union_key():
    """R7 M4 falsifier: the bare-noun guard is observable on the union-key surface.

    Guard ON  -> the weak token registers no key: no CS-3w entry anywhere on the cluster.
    Guard OFF -> the same fixture DOES register CS-3w:<token> (positive control proving the
                 fixture reaches the guard, so the ON assertion cannot pass vacuously).
    Membership is deliberately NOT asserted: token edges never bridge components, so
    membership is identical under both settings by construction.

    Kill sites: M4 alone (ON assertions) and M5 (positive control).
    """
    from continuum.config import Config

    on = Config()                      # shipped default: weak_token_clustering=false
    off = Config()
    off.bundle = dict(off.bundle)
    off.bundle["weak_token_clustering"] = True
    assert on.get("weak_token_clustering") is False
    assert load_config().get("weak_token_clustering") is False   # shipped C1 default (CR-2)

    facts = [
        _fact("alpha", "e1", cwd="/work/projN", ws="/work/projN", title="projN one"),
        _fact("alpha", "e2", cwd="/work/projN", ws="/work/projN", title="projN two"),
    ]
    # one weak token shared by both sessions; the component itself forms by CS-2
    keys = {"noun:deployment": ["alpha/e1", "alpha/e2"]}
    assert cluster_mod.is_strong_token("noun:deployment") is False

    guarded = cluster_mod.build_clusters(facts, keys, on, {})
    unguarded = cluster_mod.build_clusters(facts, keys, off, {})

    # non-vacuity: the fixture really produces a cluster, formed by the CS-2 workspace key
    assert len(guarded) == 1, "fixture must yield exactly one component; got {}".format(
        [c.member_refs for c in guarded])
    assert guarded[0].member_refs == ["alpha/e1", "alpha/e2"]
    assert any(k.startswith("CS-2:") for k in guarded[0].strong_keys)
    assert cluster_mod.assert_no_generic_merge(guarded, on) == []

    # GUARD ON: the weak token is absent from the whole union-key surface
    assert [k for k in guarded[0].strong_keys if k.startswith("CS-3w:")] == []
    assert "CS-3w" not in guarded[0].strength_breakdown
    assert [r for r in guarded[0].build_reason if "CS-3w" in r] == []

    # POSITIVE CONTROL: with the guard off the very same fixture surfaces CS-3w, so the
    # assertions above do bite. Removing the guard (mutation M4) makes these two states
    # identical and fails the test.
    assert any(k.startswith("CS-3w:") for k in unguarded[0].strong_keys), (
        "fixture never reaches the weak-token branch; the ON assertions are vacuous")
    assert "CS-3w" in unguarded[0].strength_breakdown
    assert any("CS-3w" in r for r in unguarded[0].build_reason)


def test_cs3_token_appears_in_cluster_strong_keys():
    """R7 falsifiable (M5): removing CS-3 union path removes tokens from strong_keys.

    Two sessions in the same workspace share a strong token.
    The token must appear in the cluster's strong_keys as CS-3.
    If the CS-3 section is deleted (M5 mutation), strong_keys has no CS-3 entries → fails.
    """
    from continuum.config import Config
    cfg = Config()

    facts = [
        _fact("alpha", "d1", cwd="/work/projX", ws="/work/projX",
              title="work on typejoy"),
        _fact("alpha", "d2", cwd="/work/projX", ws="/work/projX",
              title="more work on typejoy"),
    ]

    # They share a strong token AND are in the same workspace
    keys = {"repo:typejoy": ["alpha/d1", "alpha/d2"]}

    clusters = cluster_mod.build_clusters(facts, keys, cfg, {})
    cmap = _cluster_map(clusters)

    # They should be in the same cluster
    assert cmap.get("alpha/d1") == cmap.get("alpha/d2"), \
        "Sessions in same workspace with shared token should cluster together"

    # The CS-3 token must appear in strong_keys
    for c in clusters:
        if "alpha/d1" in c.member_refs:
            cs3_keys = [k for k in c.strong_keys if k.startswith("CS-3:")]
            assert cs3_keys, (
                "CS-3 token must appear in strong_keys. "
                "If CS-3 union path is deleted, this fails. strong_keys={}".format(
                    c.strong_keys))
            assert "CS-3:repo:typejoy" in c.strong_keys
            break


def test_ws_guard_with_strong_token_and_different_workspaces_full_pipeline(home):
    """End-to-end no-bridge state test (workspace guard, full scan pipeline).

    Pure state corollary: no kill site after T3. The fixture shares no strong token
    across the two workspaces, so M3 alone cannot falsify it; measured, it passes under
    M3 alone (0 failed / 55 passed) and under M3+M4 (1 failed / 54 passed, that failure
    being the guard's own test). It documents that ws and tjgc1 stay in separate clusters
    even with token_max_frequency relaxed, and is deliberately NOT a single-mutation
    falsifier. The earlier "Kill site: M3+M4 combined only" label, prescribed by decision
    §7 T5, is superseded by measurement.
    """
    from continuum import evidence as evidence_mod
    from continuum.config import load_config
    # The existing fixture has ws sessions in /Users/kethuda/work/tywebsite and
    # tjgc1 sessions in /Users/kethuda/work/tjgc1. They don't share tokens.
    # This test verifies that even with token_max_frequency relaxed, the workspace
    # guard keeps them separate. The existing fixture's ws sessions mention "hero"
    # and tjgc1 sessions mention "typejoy" — no shared tokens.
    cfg = load_config(hermes_home=home)
    cfg.bundle["token_max_frequency"] = 0.50
    cfg.bundle["token_min_sessions"] = 2
    refs = scanner.discover_profiles(home, cfg)
    batch = scanner.scan(refs, cfg=cfg)
    noise = cluster_mod.classify_noise(batch.facts, batch.probes, cfg)
    keys = evidence_mod.canonical_keys(batch.facts, [], batch.probes, cfg)
    clusters = cluster_mod.build_clusters(batch.facts, keys, cfg, noise)

    ws_refs = {"alpha/20260104_000004_bbb111", "alpha/20260105_000005_bbb222"}
    tjgc1_refs = {"alpha/20260101_000001_aaa111", "alpha/20260102_000002_aaa222",
                  "alpha/20260103_000003_aaa333", "beta/20260201_000001_bbb901"}

    ws_cluster_id = None
    tjgc1_cluster_id = None
    for c in clusters:
        members = set(c.member_refs)
        if members & ws_refs:
            ws_cluster_id = c.cluster_id
        if members & tjgc1_refs:
            tjgc1_cluster_id = c.cluster_id

    assert ws_cluster_id is not None, "ws sessions should be in a cluster"
    assert tjgc1_cluster_id is not None, "tjgc1 sessions should be in a cluster"
    assert ws_cluster_id != tjgc1_cluster_id, \
        "ws and tjgc1 must be in different clusters (workspace guard)"


# ---------------------------------------------------------------------------
# T5 — audience invariant across the whole fixture corpus (D-US-6 step 4).
# ---------------------------------------------------------------------------

def test_t5_primary_and_resume_links_never_target_delegated_over_the_corpus(svc):
    from continuum import audience as audience_mod

    rows = svc.registry.conn.execute(
        "SELECT ps.project_id, ps.profile_name, ps.session_id, ps.role_in_project, sf.audience "
        "FROM project_session ps LEFT JOIN session_fact sf "
        "ON sf.profile_name=ps.profile_name AND sf.session_id=ps.session_id").fetchall()
    assert rows, "the fixture must produce project members"
    for r in rows:
        aud = r["audience"] or "UNKNOWN"
        if r["role_in_project"] == "primary":
            assert aud in ("USER_FACING", "UNKNOWN"), \
                "primary must never be {}".format(aud)

    # the AV-7 delegated child (subagent with a parent) is excluded from every resume surface
    assert svc.registry.session_audience("alpha", "20260114_000014_fff111")[0] == "AUTOMATED"
    for card in svc.board()["items"]:
        for s in card["sessions"]:
            aud = svc.registry.session_audience(s["profile"], s["session_id"])[0]
            assert aud not in ("DELEGATED", "AUTOMATED")
    for it in svc.inbox()["items"]:
        for link in it["resume_links"]:
            assert link["audience"] not in ("DELEGATED", "AUTOMATED")

    assert audience_mod.assert_no_resume_on_delegated(svc.board()["items"]) == []
    assert audience_mod.assert_no_resume_on_delegated(svc.inbox()["items"]) == []

    # every session_fact row is classified into the closed set (T6 on this corpus)
    values = {r["audience"] for r in svc.registry.conn.execute(
        "SELECT DISTINCT audience FROM session_fact")}
    assert values <= set(audience_mod.AUDIENCES)


# ---------------------------------------------------------------------------
# M4 — continuity invariants across the whole fixture corpus.
# ---------------------------------------------------------------------------

def test_m4_every_fixture_project_is_in_exactly_one_board_lane(svc):
    from continuum.service import BOARD_COLUMNS

    cards = svc.board(view="all", page_size=200)["items"]
    assert cards, "fixture must produce board cards"
    seen = set()
    for card in cards:
        assert card["column"] in BOARD_COLUMNS
        assert card["project_id"] not in seen, "a project appears twice on the board"
        seen.add(card["project_id"])
        assert card["session_count_total"] >= 1


def test_m4_a_scan_never_creates_a_terminal_or_suppressed_card(svc):
    svc.scan(full=True)
    svc.scan()
    cards = svc.board(view="all", page_size=200)["items"]
    assert [c for c in cards if c["column"] in ("done", "shipped", "scrapped")] == []
    for card in cards:
        assert card["placement_source"] != "human"
        declared = svc.registry.effective_declared(card["project_id"])
        assert declared.get("dismissed") != "1"
        assert not declared.get("merged_into")


def test_m4_no_fixture_card_offers_a_delegated_or_automated_resume(svc):
    for card in svc.board(view="all", page_size=200)["items"]:
        for target in card["resume_targets"]:
            assert target["audience"] == "USER_FACING"
            assert target["copy_command"] == "hermes -p {} --resume {}".format(
                target["profile"], target["session_id"])
        for session in card["sessions"]:
            audience = svc.registry.session_audience(session["profile"],
                                                     session["session_id"])[0]
            if audience == "USER_FACING":
                assert session["cli_resume"] == "hermes --resume " + session["session_id"]
            else:
                assert session["cli_resume"] is None
                assert session["copy_command"] is None
        if card["primary_session"] is not None:
            assert card["primary_session"]["audience"] == "USER_FACING"
            assert card["primary_session"]["selection"] in ("linked_primary", "anchor")


def test_m4_board_payload_carries_no_transcript(svc):
    blob = repr(svc.board(view="today"))
    for banned in ("recent_messages", "user_messages", "captured_window", "probe_head"):
        assert banned not in blob
    # the pane payload is detail-only
    pid = sorted(c["project_id"] for c in svc.board(view="all")["items"])[0]
    assert "session_pane" not in svc.project_detail(pid)

