"""G0 — the scanner against the REAL profile databases, in enforced read-only mode.

These are integration tests: they open live ``state.db`` files. They assert relationships and
invariants (never frozen counts), because the databases are live and drift between runs.
"""
from __future__ import annotations

import dataclasses
import os
import sqlite3
import sys
import time

import pytest

from conftest import ROSTER, real_hermes_home
from continuum import scanner
from continuum.config import load_config

HOME = real_hermes_home()


def _refs():
    cfg = load_config(HOME)
    return cfg, scanner.discover_profiles(HOME, cfg)


def _stat(path):
    st = os.stat(path)
    return (st.st_mtime, st.st_size, os.path.exists(path + "-wal"), os.path.exists(path + "-shm"))


def _independent_counts(db_path):
    uri = "file:{}?mode=ro".format(db_path)
    conn = sqlite3.connect(uri, uri=True)
    try:
        sessions = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
        messages = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        ids = {r[0] for r in conn.execute("SELECT id FROM sessions")}
        profiles = {r[0] for r in conn.execute("SELECT DISTINCT profile_name FROM sessions")}
    finally:
        conn.close()
    return sessions, messages, ids, profiles


def _snapshot_refs(refs, tmp_path):
    """Take one read-only SQLite backup per live source for a coherent test run."""
    out = []
    for ref in refs:
        target = os.path.join(str(tmp_path), ref.profile_name + ".db")
        source = sqlite3.connect("file:{}?mode=ro".format(ref.db_path), uri=True)
        dest = sqlite3.connect(target)
        try:
            source.backup(dest)
        finally:
            dest.close()
            source.close()
        out.append(dataclasses.replace(ref, db_path=target))
    return out


@pytest.mark.skipif(not os.path.isdir(os.path.join(HOME, "profiles")),
                    reason="no real Hermes profiles on this machine")
def test_discovers_the_current_profile_roster():
    _, refs = _refs()
    names = [r.profile_name for r in refs]
    assert names, "expected at least one profile database"
    assert names == sorted(names)
    assert len(names) == len(set(names))
    for r in refs:
        assert os.path.basename(os.path.dirname(r.db_path)) == r.profile_name


@pytest.mark.skipif(not os.path.isdir(os.path.join(HOME, "profiles")),
                    reason="no real Hermes profiles on this machine")
def test_every_profile_opens_readonly_and_counts_reconcile(tmp_path):
    cfg, live_refs = _refs()
    refs = _snapshot_refs(live_refs, tmp_path)
    before = {r.db_path: _stat(r.db_path) for r in live_refs}
    batch = scanner.scan(refs, cfg=cfg, probe=False)
    after = {r.db_path: _stat(r.db_path) for r in live_refs}

    assert batch.counts["profiles_ok"] == len(refs)
    for st in batch.status:
        assert st.status == "ok", "{} -> {}".format(st.profile_name, st.error)
        assert st.schema_version is not None

    # per-profile counts match an independent direct read-only count
    for st in batch.status:
        indep_sessions, indep_messages, _, _ = _independent_counts(st.path)
        assert st.session_count == indep_sessions, st.profile_name
        assert st.message_count == indep_messages, st.profile_name

    # aggregate == sum of members
    assert batch.counts["sessions"] == sum(s.session_count for s in batch.status)
    assert batch.counts["messages"] == sum(s.message_count for s in batch.status)

    # INV-1: sources are byte-unchanged and no new -wal/-shm appeared
    assert before == after


@pytest.mark.skipif(not os.path.isdir(os.path.join(HOME, "profiles")),
                    reason="no real Hermes profiles on this machine")
def test_write_attempt_on_a_source_handle_is_refused():
    _, refs = _refs()
    conn = scanner.open_readonly(refs[0].db_path)
    try:
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("CREATE TABLE continuum_should_not_exist (x INTEGER)")
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("UPDATE sessions SET title = 'mutated'")
    finally:
        conn.close()


def test_open_readonly_uri_forces_ro_mode():
    uri = scanner.build_ro_uri("/tmp/whatever.db")
    assert uri.startswith("file:")
    assert "mode=ro" in uri


@pytest.mark.skipif(not os.path.isdir(os.path.join(HOME, "profiles")),
                    reason="no real Hermes profiles on this machine")
def test_identifiers_are_preserved_verbatim():
    cfg, refs = _refs()
    batch = scanner.scan(refs, cfg=cfg, probe=False)
    by_profile = {}
    for f in batch.facts:
        by_profile.setdefault(f.profile_name, set()).add(f.session_id)

    for st in batch.status:
        _, _, ids, _ = _independent_counts(st.path)
        assert by_profile.get(st.profile_name, set()) == ids, st.profile_name


@pytest.mark.skipif(not os.path.isdir(os.path.join(HOME, "profiles")),
                    reason="no real Hermes profiles on this machine")
def test_generic_cwd_never_yields_a_workspace_root():
    cfg, refs = _refs()
    batch = scanner.scan(refs, cfg=cfg, probe=False)
    generics = [f for f in batch.facts if cfg.is_generic_path(f.cwd)]
    assert generics, "expected some sessions with a generic cwd"
    assert all(f.workspace_root is None for f in generics)


def test_workspace_root_derivation_rules():
    cfg = load_config()
    # generic roots -> None (AV-4)
    assert scanner.derive_workspace_root("/Users/kethuda", cfg) is None
    assert scanner.derive_workspace_root(".", cfg) is None
    assert scanner.derive_workspace_root(None, cfg) is None
    # repo-scoped path -> captured
    assert scanner.derive_workspace_root(
        "/Users/kethuda/Documents/ai work/Hermes/typejoy/wrk/tjgc1", cfg
    ) == "/Users/kethuda/Documents/ai work/Hermes/typejoy/wrk/tjgc1"


# ---------------------------------------------------------------------------
# T8 — the audience pass (brief index + async_delegations read) is read-only.
# Isolated fixture tree: no live database is touched.
# ---------------------------------------------------------------------------

AUDIENCE_FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 "fixtures", "audience")
if AUDIENCE_FIXTURES not in sys.path:
    sys.path.insert(0, AUDIENCE_FIXTURES)


def test_t8_audience_pass_leaves_source_dbs_byte_unchanged(tmp_path):
    from build_audience_home import build_audience_home

    home = build_audience_home(str(tmp_path / "hermes_home"))
    cfg = load_config(hermes_home=home)
    refs = scanner.discover_profiles(home, cfg)
    assert [r.profile_name for r in refs] == ["azaraki", "kodekoot", "lugia"]

    before = {r.db_path: _stat(r.db_path) for r in refs}
    batch = scanner.scan(refs, cfg=cfg)
    after = {r.db_path: _stat(r.db_path) for r in refs}
    assert before == after, "the audience pass changed a source database"
    assert batch.counts["profiles_ok"] == 3
    assert batch.first_user, "the untruncated first-user-message field must be populated"
    # the delegator index is populated from async_delegations, read-only
    assert any("20260911_163629_22fff0" in ids for ids in batch.delegators.values())

    conn = scanner.open_readonly(refs[0].db_path)
    try:
        assert conn.execute("PRAGMA query_only").fetchone()[0] == 1
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("UPDATE sessions SET title='mutated'")
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("UPDATE async_delegations SET state='mutated'")
    finally:
        conn.close()
    assert before == {r.db_path: _stat(r.db_path) for r in refs}


def test_t8_first_user_message_is_untruncated_and_bounded():
    """The C1 probe is the FULL first user message (bounded by audience_probe_chars)."""
    from build_audience_home import (AZARAKI_BRIEF, TARGET_PROFILE, TARGET_SESSION,
                                     brief_text, build_audience_home)
    import tempfile

    home = build_audience_home(os.path.join(tempfile.mkdtemp(), "hermes_home"))
    cfg = load_config(hermes_home=home)
    refs = scanner.discover_profiles(home, cfg)
    batch = scanner.scan(refs, cfg=cfg)
    ref = "{}/{}".format(TARGET_PROFILE, TARGET_SESSION)
    assert ref in batch.first_user
    expected = brief_text(AZARAKI_BRIEF)
    assert batch.first_user[ref] == expected
    # the full brief (5 389 chars / 5 415 bytes) survives the probe cap, unlike the 240-char excerpt
    assert len(batch.first_user[ref]) == len(expected) == 5389
    assert len(batch.first_user[ref]) > int(cfg.get("excerpt_chars", 240))


# ---------------------------------------------------------------------------
# M4 — incremental watermark behaviour and the lazy session-pane source read.
# Isolated fixture trees only; no live database is touched.
# ---------------------------------------------------------------------------

def test_m4_incremental_scan_skips_unchanged_profiles_and_reads_on_change(tmp_path):
    from fixtures.make_fixture import build_fixture_tree

    home = build_fixture_tree(str(tmp_path / "hermes_home"))
    cfg = load_config(hermes_home=home)
    refs = scanner.discover_profiles(home, cfg)
    assert [r.profile_name for r in refs] == ["alpha", "beta"]

    first = scanner.scan(refs, cfg=cfg, mode="full")
    assert first.counts["profiles_skipped"] == 0
    assert all(st.reconciliation == "full-first-run" for st in first.status)

    # Build the previous snapshot exactly as the registry would hold it.
    prior = {st.profile_name: {"mtime": st.mtime, "size": st.size,
                               "session_count": st.session_count,
                               "message_count": st.message_count,
                               "schema_version": st.schema_version, "status": "ok",
                               "last_scan_at": time.time()}
             for st in first.status}
    second = scanner.scan(refs, watermark=prior, cfg=cfg, mode="incremental")
    assert second.counts["profiles_skipped"] == len(refs)
    assert second.facts == [] and second.probes == [], "a skipped profile was re-read"

    # A changed stat forces a full profile reconciliation (never a partial read).
    changed = {k: dict(v) for k, v in prior.items()}
    changed["alpha"]["size"] = int(changed["alpha"]["size"]) + 4096
    third = scanner.scan(refs, watermark=changed, cfg=cfg, mode="incremental")
    statuses = {st.profile_name: st.reconciliation for st in third.status}
    assert statuses["alpha"] == "full", statuses
    assert statuses["beta"] == "skipped", statuses
    assert any(f.profile_name == "alpha" for f in third.facts)


def test_m4_pane_probe_is_read_only_and_bounded(tmp_path):
    from fixtures.make_fixture import build_fixture_tree
    from continuum.service import Service

    home = build_fixture_tree(str(tmp_path / "hermes_home"))
    cfg = load_config(hermes_home=home)
    cfg.bundle["registry_path"] = str(tmp_path / "registry.db")
    svc = Service(cfg)
    svc.scan()
    before = {r.db_path: _stat(r.db_path) for r in scanner.discover_profiles(home, cfg)}

    pid = sorted(c["project_id"] for c in svc.board(view="all")["items"])[0]
    ref = "{}/{}".format(*(svc.registry.links_for(pid)[0]["profile_name"],
                           svc.registry.links_for(pid)[0]["session_id"]))
    pane = svc.project_detail(pid, pane=ref)["session_pane"]
    assert pane["available"] is True
    assert pane["capture"] == {"probe_head": 1, "probe_tail": 6, "excerpt_chars": 240}
    assert len(pane["recent_messages"]) <= 7
    for row in pane["recent_messages"]:
        assert row["role"] in ("YOU", "AGENT")
        assert len(row["text"]) <= 240

    after = {r.db_path: _stat(r.db_path) for r in scanner.discover_profiles(home, cfg)}
    assert before == after, "a pane read wrote to a source database"


# ---------------------------------------------------------------------------
# M4 batch 2 — coarse-watermark reads stay read-only and consume fine/coarse marks.
# ---------------------------------------------------------------------------

def test_m4_coarse_read_consumes_watermarks_and_keeps_sources_read_only(tmp_path):
    """A changed profile with committed fine/coarse watermarks is read through the bounded
    coarse window; the source DB stays byte-unchanged with no WAL/SHM files."""
    from fixtures.make_fixture import build_fixture_tree

    home = build_fixture_tree(str(tmp_path / "hermes_home"))
    cfg = load_config(hermes_home=home)
    refs = scanner.discover_profiles(home, cfg)
    assert [r.profile_name for r in refs] == ["alpha", "beta"]

    first = scanner.scan(refs, cfg=cfg, mode="full")
    assert first.counts["profiles_skipped"] == 0
    # committed snapshot per profile, carrying both watermark columns (as the service writes)
    prior = {st.profile_name: {"mtime": st.mtime, "size": st.size,
                               "session_count": st.session_count,
                               "message_count": st.message_count,
                               "schema_version": st.schema_version, "status": "ok",
                               "last_scan_at": time.time(),
                               "fine_watermark": time.time() - 1000.0,
                               "coarse_watermark": time.time() - 1000.0}
             for st in first.status}

    # a NEW alpha session appears; beta stays unchanged -> beta is skipped, alpha coarse-reads
    db = os.path.join(home, "profiles", "alpha", "state.db")
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO sessions (id, source, title, cwd, git_repo_root, started_at, "
        "last_activity_at, message_count, tool_call_count) VALUES (?,?,?,?,?,?,?,?,?)",
        ("20260999_000099_coarse1", "cli", "coarse window new", "/Users/kethuda/work/cw",
         "/Users/kethuda/work/cw", time.time(), time.time(), 2, 1))
    conn.execute(
        "INSERT INTO messages (session_id, role, content, timestamp) VALUES (?,?,?,?)",
        ("20260999_000099_coarse1", "user", "coarse read work", time.time()))
    conn.commit()
    conn.close()

    before = {r.db_path: _stat(r.db_path) for r in refs}
    batch = scanner.scan(refs, watermark=prior, cfg=cfg, mode="incremental")
    after = {r.db_path: _stat(r.db_path) for r in refs}

    statuses = {st.profile_name: st for st in batch.status}
    assert statuses["beta"].skipped, "the unchanged beta profile must be skipped"
    assert statuses["alpha"].reconciliation == "coarse", statuses["alpha"].reconciliation
    assert statuses["alpha"].partial, "a coarse read is an intentional partial read"
    new_facts = [f for f in batch.facts
                 if f.profile_name == "alpha" and f.session_id == "20260999_000099_coarse1"]
    assert new_facts, "the newly observed session must be read inside the coarse window"

    # INV-1: sources are byte-unchanged and no -wal/-shm appeared
    assert before == after


def test_m4_missing_coarse_watermark_falls_back_to_full(tmp_path):
    """Without a committed coarse watermark a changed profile is FULLY reconciled — never
    read partially (ambiguous/missing watermark)."""
    from fixtures.make_fixture import build_fixture_tree

    home = build_fixture_tree(str(tmp_path / "hermes_home"))
    cfg = load_config(hermes_home=home)
    refs = scanner.discover_profiles(home, cfg)
    first = scanner.scan(refs, cfg=cfg, mode="full")

    prior = {st.profile_name: {"mtime": st.mtime, "size": st.size,
                               "session_count": st.session_count,
                               "message_count": st.message_count,
                               "schema_version": st.schema_version, "status": "ok",
                               "last_scan_at": time.time()}
             for st in first.status}  # no fine/coarse watermark keys
    prior["alpha"]["size"] = int(prior["alpha"]["size"]) + 4096  # force a change
    third = scanner.scan(refs, watermark=prior, cfg=cfg, mode="incremental")
    statuses = {st.profile_name: st for st in third.status}
    assert statuses["alpha"].reconciliation == "full", statuses["alpha"].reconciliation
    assert statuses["alpha"].partial is False
    assert statuses["beta"].skipped
