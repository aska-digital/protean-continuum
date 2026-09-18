"""AL-* acceptance tests for the action-log SYNC pass.

Every source used here is a FROZEN copy under ``fixtures/action_log/`` on purpose: the live ops
files (``~/.hermes/team-skills/ops/*.md``) drift while concurrent lanes append to them, so
AL-A1/AL-A3/AL-A4 (completeness, determinism, idempotency) are only meaningful against frozen
input. AL-G1 (Shaka) re-measures the same ids against these fixtures.

Determinism is total: the sync engine takes an injected clock and injected source paths, no
network, no HTTP, and it never reads the wall clock for ids.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys

import pytest

from continuum import action_log as al

BUILD_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIX = os.path.join(BUILD_ROOT, "fixtures", "action_log")
RECEIPTS = os.path.join(FIX, "receipts")
CLEAN_RECEIPTS = ("post-receipt.md", "merge-receipt.md", "close-receipt.md",
                  "retract-receipt.md", "edit-receipt.md")


def cfg_for(tmp_path, *, inflight="INFLIGHT.md", dispatch="DISPATCH-LEDGER.md",
            receipts="clean", ledger="action_log.json", enabled=True):
    """A config bundle scoped to a temp ledger and to chosen FROZEN sources.

    ``receipts="clean"`` means the five keyword-carrying fixtures (the malformed one is
    adversarial and is requested explicitly by the AL-G2 tests).
    """
    sources = []
    if inflight:
        sources.append(os.path.join(FIX, inflight))
    if dispatch:
        sources.append(os.path.join(FIX, dispatch))
    if receipts == "clean":
        sources.extend(os.path.join(RECEIPTS, name) for name in CLEAN_RECEIPTS)
    elif receipts:
        sources.append(os.path.join(FIX, receipts))
    return {
        "action_log_enabled": enabled,
        "action_log_ledger_path": str(tmp_path / ledger),
        "action_log_source_paths": sources,
        "action_log_stale_after_seconds": 900,
    }


def sha256_file(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


# --------------------------------------------------------------- AL-A1 completeness

def test_al_a1_sync_completeness(tmp_path):
    cfg = cfg_for(tmp_path)
    report = al.sync(cfg, now=1000.0)
    ids = {row["action_id"] for row in report["items"]}

    inflight_rows = [r for r in report["items"] if r["source"] == "inflight"]
    dispatch_rows = [r for r in report["items"] if r["source"] == "dispatch-ledger"]
    receipt_rows = [r for r in report["items"] if r["source"] == "receipt"]

    # 3 claims in the frozen INFLIGHT fixture, 3 dispatches in the frozen ledger fixture.
    assert len(inflight_rows) == 3
    assert len(dispatch_rows) == 3
    assert len(receipt_rows) == 5
    # AL-A3: INFLIGHT claim ids map 1:1 onto action-ids.
    assert {r["action_id"] for r in inflight_rows} == {
        "al-inf-20260918-fixture-01", "al-inf-20260918-fixture-02",
        "al-inf-20260918-fixture-03"}
    assert {r["action_id"] for r in dispatch_rows} == {
        "al-fixture-dispatch-01", "al-fixture-dispatch-02", "al-fixture-dispatch-03"}
    assert ids == {r["action_id"] for r in report["items"]}
    counts = report["counts"]
    assert counts["total"] == len(report["items"]) == 11
    assert counts["active"] == 11 and counts["archived"] == 0
    assert counts["by_kind"]["dispatch"] == 6
    assert sum(counts["by_kind"].values()) == 11
    assert sum(counts["by_operator"].values()) == 11
    # measured totals, not estimates: every parsed row is projected (nothing silently dropped)
    for row in report["items"]:
        assert row["evidence_link"], row["action_id"]
        assert row["source"] in al.SOURCES
        assert row["kind"] in al.KINDS


# --------------------------------------------------------------- AL-A2 row schema

def test_al_a2_row_schema_types_and_enums(tmp_path):
    report = al.sync(cfg_for(tmp_path), now=1000.0)
    assert report["items"], "sync produced no rows"
    for row in report["items"]:
        assert sorted(row.keys()) == sorted(al.ROW_FIELDS), row["action_id"]
        assert len(row) == 13
        assert isinstance(row["action_id"], str) and row["action_id"].startswith("al-")
        assert len(row["action_id"]) <= al.ACTION_ID_MAX
        assert isinstance(row["timestamp"], float)
        assert row["kind"] in al.KINDS
        assert isinstance(row["target"], str) and row["target"]
        assert len(row["target"]) <= al.TARGET_MAX
        assert row["target_type"] in al.TARGET_TYPES
        assert row["evidence_type"] in al.EVIDENCE_TYPES
        assert isinstance(row["evidence_link"], str) and row["evidence_link"]
        assert len(row["evidence_link"]) <= al.EVIDENCE_MAX
        assert row["operator_flag"] in al.OPERATOR_FLAGS
        assert row["source"] in al.SOURCES
        assert row["source_row_id"] is None or isinstance(row["source_row_id"], str)
        assert row["status"] in al.STATUSES
        assert row["archived_at"] is None
        assert row["archive_audit_id"] is None
    # §3 operator-flag derivation: only the direct-records-action claim is operator-initiated.
    by_id = {row["action_id"]: row for row in report["items"]}
    assert by_id["al-inf-20260918-fixture-03"]["operator_flag"] == "operator"
    assert by_id["al-inf-20260918-fixture-01"]["operator_flag"] == "agent"
    assert by_id["al-fixture-dispatch-01"]["operator_flag"] == "agent"


# --------------------------------------------------------------- AL-A3 determinism

def test_al_a3_deterministic_ids_across_passes_and_clocks(tmp_path):
    cfg = cfg_for(tmp_path)
    first = al.sync(cfg, now=1000.0)
    ids_one = [row["action_id"] for row in first["items"]]
    # a different clock must not move a single id
    second = al.sync(cfg, now=9999.0)
    ids_two = [row["action_id"] for row in second["items"]]
    assert ids_one == ids_two
    assert len(set(ids_one)) == len(ids_one)
    for action_id in ids_one:
        assert action_id.startswith("al-")
        assert len(action_id) <= al.ACTION_ID_MAX
        assert all(ch.isalnum() or ch in "._-" for ch in action_id), action_id
    # receipt ids derive from (path, kind) only — the same receipt re-parsed gives the same id
    receipt = os.path.join(RECEIPTS, "post-receipt.md")
    row_one, _ = al.parse_receipt(receipt, open(receipt, encoding="utf-8").read(), 1.0)
    row_two, _ = al.parse_receipt(receipt, open(receipt, encoding="utf-8").read(), 2.0)
    assert row_one["action_id"] == row_two["action_id"]
    assert row_one["kind"] == "post"


# --------------------------------------------------------------- AL-A4 / AL-I1 idempotency

def test_al_a4_idempotent_second_pass_is_byte_identical(tmp_path):
    cfg = cfg_for(tmp_path, receipts=None)
    cfg["action_log_source_paths"] = [
        os.path.join(FIX, "INFLIGHT.md"),
        os.path.join(FIX, "DISPATCH-LEDGER.md"),
        os.path.join(RECEIPTS, "post-receipt.md"),
        os.path.join(RECEIPTS, "merge-receipt.md"),
        os.path.join(RECEIPTS, "close-receipt.md"),
        os.path.join(RECEIPTS, "retract-receipt.md"),
        os.path.join(RECEIPTS, "edit-receipt.md"),
    ]
    first = al.sync(cfg, now=1000.0)
    assert first["exit_code"] == al.EXIT_OK, first["degraded"]
    assert first["written"] is True
    before = al.ledger_sha256(cfg["action_log_ledger_path"])

    second = al.sync(cfg, now=2000.0)
    after = al.ledger_sha256(cfg["action_log_ledger_path"])
    assert second["changed"] == 0
    assert second["added"] == 0
    assert second["absent"] == 0
    assert second["written"] is False
    assert second["exit_code"] == al.EXIT_NO_CHANGES
    assert before == after, "an unchanged pass must not rewrite the ledger (AL-L9)"


# --------------------------------------------------------------- AL-I4 concurrency

def test_al_i4_concurrent_sync_exits_locked(tmp_path):
    """A second concurrent pass exits 3 (LOCKED) and writes nothing (AL-I4).

    The CLI is pointed at the REAL ledger on purpose — holding that lock is exactly the
    concurrent-pass condition, and a LOCKED pass by definition writes nothing.
    """
    live_ledger = al.ledger_path_for()
    before = al.ledger_sha256(live_ledger)
    lock_path = al.lock_path_for()
    holder = al._LedgerLock(lock_path)
    holder.__enter__()
    try:
        with pytest.raises(al.SyncLocked):
            al.sync(None, now=2000.0)
        proc = subprocess.run(
            [sys.executable, "-m", "continuum.cli", "action-log", "sync"],
            cwd=BUILD_ROOT, capture_output=True, text=True)
        assert proc.returncode == al.EXIT_LOCKED, proc.stdout + proc.stderr
    finally:
        holder.__exit__(None, None, None)
    assert al.ledger_sha256(live_ledger) == before


# --------------------------------------------------------------- AL-G2 adversarial inputs

def test_al_g2_empty_inflight_warns_and_does_not_crash(tmp_path):
    cfg = cfg_for(tmp_path, inflight="adversarial/INFLIGHT_empty.md", receipts=None)
    report = al.sync(cfg, now=1000.0)
    assert "no_claim_rows" in report["warnings"]
    assert [r for r in report["items"] if r["source"] == "inflight"] == []


def test_al_g2_malformed_receipt_yields_defined_warning(tmp_path):
    cfg = cfg_for(tmp_path, inflight=None, dispatch=None,
                  receipts="receipts/malformed-receipt.md")
    report = al.sync(cfg, now=1000.0)
    assert "receipt_kind_default" in report["degraded"]
    assert report["exit_code"] == al.EXIT_PARSE_DEGRADED
    assert len(report["items"]) == 1, "a malformed receipt must not drop its row"
    assert report["items"][0]["kind"] == "direct-edit"


def test_al_g2_receipt_without_columns(tmp_path):
    cfg = cfg_for(tmp_path, inflight=None, dispatch=None,
                  receipts="adversarial/receipt_no_columns.md")
    report = al.sync(cfg, now=1000.0)
    assert "receipt_no_columns" in report["warnings"]
    assert len(report["items"]) == 1
    assert report["items"][0]["kind"] == "direct-edit"


def test_al_g2_duplicate_claim_id_keeps_first(tmp_path):
    body = open(os.path.join(FIX, "INFLIGHT.md"), encoding="utf-8").read()
    row = [ln for ln in body.split("\n") if ln.startswith("| inf-20260918-fixture-01")][0]
    dup = tmp_path / "INFLIGHT_dup.md"
    dup.write_text(body + "\n" + row + "\n", encoding="utf-8")
    cfg = cfg_for(tmp_path, inflight=None, dispatch=None, receipts=None)
    cfg["action_log_source_paths"] = [str(dup)]
    report = al.sync(cfg, now=1000.0)
    assert "duplicate_source_row_id" in report["degraded"]
    ids = [r["action_id"] for r in report["items"]]
    assert ids.count("al-inf-20260918-fixture-01") == 1
    assert len(ids) == 3


def test_al_g2_crlf_source_is_parsed(tmp_path):
    body = open(os.path.join(FIX, "INFLIGHT.md"), encoding="utf-8").read()
    crlf = tmp_path / "INFLIGHT_crlf.md"
    crlf.write_bytes(body.replace("\n", "\r\n").encode("utf-8"))
    cfg = cfg_for(tmp_path, inflight=None, dispatch=None, receipts=None)
    cfg["action_log_source_paths"] = [str(crlf)]
    report = al.sync(cfg, now=1000.0)
    assert "crlf_normalized" in report["warnings"]
    assert len([r for r in report["items"] if r["source"] == "inflight"]) == 3


def test_al_g2_empty_delegation_directory(tmp_path):
    empty = tmp_path / "delegation"
    empty.mkdir()
    cfg = cfg_for(tmp_path, inflight=None, dispatch=None, receipts=None)
    cfg["action_log_source_paths"] = [str(empty / "**" / "*-receipt.md")]
    report = al.sync(cfg, now=1000.0)
    assert "no_receipt_files" in report["warnings"]
    assert report["items"] == []


def test_al_g2_missing_source_raises_source_missing(tmp_path):
    cfg = cfg_for(tmp_path, inflight=None, dispatch=None, receipts=None)
    cfg["action_log_source_paths"] = [str(tmp_path / "nope" / "INFLIGHT.md")]
    with pytest.raises(al.SourceMissing):
        al.sync(cfg, now=1000.0)


# --------------------------------------------------------------- AL-A8 envelope shape

def test_al_a8_action_log_envelope_key_set(tmp_path):
    cfg = cfg_for(tmp_path)
    al.sync(cfg, now=1000.0)
    query = al.parse_query({})
    envelope = al.view(cfg, query)
    assert set(envelope.keys()) == {"view", "counts", "actions", "page", "page_size", "has_more"}
    assert envelope["view"] == "action_log"
    assert "projects" not in envelope and "items" not in envelope
    assert set(envelope["counts"].keys()) == {"total", "active", "archived", "by_kind",
                                              "by_operator",
                                              # Orda 2026-09-17 additive scope keys
                                              "scope", "external_total", "internal_total"}
    assert envelope["counts"]["scope"] == "external"  # the default view is the external filter
    assert set(envelope["counts"]["by_kind"].keys()) == set(al.KINDS)
    assert set(envelope["counts"]["by_operator"].keys()) == set(al.OPERATOR_FLAGS)
    for row in envelope["actions"]:
        assert "kind" in row and "evidence_link" in row and "status" in row


# --------------------------------------------------------------- AL-A9 filters

def test_al_a9_filters_and_paging(tmp_path):
    cfg = cfg_for(tmp_path)
    al.sync(cfg, now=1000.0)
    # Orda 2026-09-17: dispatch rows are INTERNAL — the kind filter is exercised over the
    # full log (scope=all) so this test keeps its paging/filter semantics.
    dispatch = al.view(cfg, al.parse_query({"kind": ["dispatch"], "scope": "all"}))
    assert len(dispatch["actions"]) == 6
    assert all(row["kind"] == "dispatch" for row in dispatch["actions"])
    assert dispatch["counts"]["total"] == 11, "counts describe the ledger, not the page"
    posts = al.view(cfg, al.parse_query({"kind": "post"}))
    assert len(posts["actions"]) == 1 and posts["actions"][0]["kind"] == "post"
    agents = al.view(cfg, al.parse_query({"operator": "operator", "scope": "all"}))
    assert [row["action_id"] for row in agents["actions"]] == ["al-inf-20260918-fixture-03"]
    page = al.view(cfg, al.parse_query({"page": "2", "page_size": "5", "scope": "all"}))
    assert len(page["actions"]) == 5 and page["page"] == 2 and page["page_size"] == 5
    assert page["has_more"] is True
    window = al.view(cfg, al.parse_query({"date_from": "1000.5", "date_to": "1000.5"}))
    assert isinstance(window["actions"], list)


def test_al_a9_invalid_values_raise_value_error():
    for bad in ({"kind": "nonsense"}, {"operator": "robot"}, {"status": "gone"},
                {"page": "0"}, {"page_size": "9999"}, {"date_from": "soon"},
                {"sort": "nope"}, {"scope": "nope"}):
        with pytest.raises(ValueError):
            al.parse_query(bad)


def test_al_a10_default_view_is_active_only(tmp_path):
    cfg = cfg_for(tmp_path)
    al.sync(cfg, now=1000.0)
    al.archive(cfg, "al-inf-20260918-fixture-01", actor="test")
    default = al.view(cfg, al.parse_query({}))
    assert all(row["status"] == "active" for row in default["actions"])
    assert default["counts"]["active"] == 10 and default["counts"]["archived"] == 1
    assert default["counts"]["total"] == 11
    # The archived row is a DISPATCH (internal): the external default never shows it, even
    # when browsing archived; scope=all keeps AL-L14's archived browsing byte-for-byte.
    archived_external = al.view(cfg, al.parse_query({"status": "archived"}))
    assert [row["action_id"] for row in archived_external["actions"]] == []
    archived = al.view(cfg, al.parse_query({"status": "archived", "scope": "all"}))
    assert [row["action_id"] for row in archived["actions"]] == ["al-inf-20260918-fixture-01"]
    active = al.view(cfg, al.parse_query({"status": "active", "scope": "all"}))
    assert len(active["actions"]) == 10
    assert len(archived["actions"]) + len(active["actions"]) == default["counts"]["total"]


# --------------------------------------------------------------- AL-A12 write boundary

REGISTRY_TABLES = ("project", "project_session", "evidence", "next_action", "review_event",
                   "scan_run")


def test_al_a12_write_boundary(tmp_path):
    registry = os.path.join(BUILD_ROOT, "data", "registry.db")
    task_home = os.path.join(BUILD_ROOT, "data", "task_home_sync.json")
    # Hermetic: if no live registry is present (CI and fresh checkouts), build a
    # synthetic registry in tmp_path so the write-boundary assertion stays live
    # instead of being hidden behind a skip.
    if not os.path.exists(registry):
        synthetic = str(tmp_path / "synthetic-registry.db")
        import continuum.registry as _reg
        conn = sqlite3.connect(synthetic)
        try:
            conn.executescript(_reg._DDL)
            conn.execute("INSERT INTO schema_version (version) VALUES (?)", (_reg.SCHEMA_VERSION,))
            # one minimal row per table so hashing exercises real content
            now = 1000.0
            conn.execute(
                "INSERT INTO project (project_id, name, kind, phase, lifecycle, confidence, confidence_band, evidence_tier, owner_profile, drive_expected, stall_age_days, last_substantive_activity, session_count, derived_updated_at, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("syn-proj-1", "synthetic project", "derived", "active", "LS-2", 0.8, "high", 1, "", 0, 1.0, now, 1, now, now, now))
            conn.execute(
                "INSERT INTO project_session (link_id, project_id, profile_name, session_id, role_in_project, link_confidence, link_reason, evidence_ref, accepted, first_linked_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                ("link-syn-proj-1", "syn-proj-1", "syn", "20260101_000001_syn001", "supporting", 0.8, "synthetic", None, 0, now))
            conn.execute(
                "INSERT INTO evidence (evidence_id, project_id, cluster_id, profile_name, session_id, tier, kind, excerpt, locator, extracted_at, source_hash) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                ("ev-syn-1", "syn-proj-1", "cl-syn", "syn", "20260101_000001_syn001", 1, "session", "excerpt", '{"profile":"syn"}', now, "abc"))
            conn.execute(
                "INSERT INTO next_action (action_id, project_id, text, state, source, verified_at, expires_at, declared_rev) VALUES (?,?,?,?,?,?,?,?)",
                ("na-syn-1", "syn-proj-1", "do thing", "open", "derived", now, None, 0))
            conn.execute(
                "INSERT INTO review_event (event_id, ts, actor, action, target_type, target_id, before_json, after_json, rev) VALUES (?,?,?,?,?,?,?,?,?)",
                ("ev-1", now, "test", "create", "project", "syn-proj-1", "{}", "{}", 1))
            conn.execute(
                "INSERT INTO scan_run (run_id, started_at, ended_at, mode, profiles_scanned, sessions_read, messages_probed, status, error) VALUES (?,?,?,?,?,?,?,?,?)",
                ("run-syn-1", now, now, "incremental", 1, 1, 1, "ok", None))
            conn.commit()
        finally:
            conn.close()
        registry = synthetic
        # synthetic task-home lives beside the synthetic registry; action-log must not touch it
        task_home = str(tmp_path / "synthetic-task-home.json")
        with open(task_home, "w", encoding="utf-8") as fh:
            fh.write('{"synthetic": true}')

    def table_hashes():
        conn = sqlite3.connect("file:{}?mode=ro".format(registry), uri=True)
        try:
            out = {}
            for table in REGISTRY_TABLES:
                rows = conn.execute("SELECT * FROM {}".format(table)).fetchall()
                payload = json.dumps([list(r) for r in rows], sort_keys=True, default=str)
                out[table] = hashlib.sha256(payload.encode("utf-8")).hexdigest()
            return out
        finally:
            conn.close()

    before_tables = table_hashes()
    before_task_home = sha256_file(task_home) if os.path.exists(task_home) else None
    cfg = cfg_for(tmp_path)
    al.sync(cfg, now=1000.0)
    al.archive(cfg, "al-inf-20260918-fixture-02", actor="test")
    al.unarchive(cfg, "al-inf-20260918-fixture-02", actor="test")
    assert table_hashes() == before_tables
    after_task_home = sha256_file(task_home) if os.path.exists(task_home) else None
    assert after_task_home == before_task_home
    # only the action-log file set was written
    assert os.path.exists(cfg["action_log_ledger_path"])
    assert os.path.exists(al.events_path_for(cfg))
    assert not os.path.exists(os.path.join(BUILD_ROOT, "data", "action_log.json.tmp"))


def test_al_a13_schema_version_is_three():
    import continuum.registry as registry
    assert registry.SCHEMA_VERSION == 3
